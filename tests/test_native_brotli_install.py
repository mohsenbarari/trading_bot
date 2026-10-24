from contextlib import ExitStack
from hashlib import sha256
import importlib.util
import io
import json
import os
import fcntl
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('native_brotli_install', ROOT / 'deploy/maintenance/brotli_native_install.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class NativeBrotliInstallTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name in ('subprocess.Popen', 'subprocess.run', 'os.system', 'socket.socket', 'socket.create_connection', 'ctypes.CDLL'):
            self.stack.enter_context(patch(name, side_effect=AssertionError('native_action_forbidden')))
        self.folder = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.brotli-install-test-', dir=ROOT)))

    def candidate(self, *, unsafe=False):
        payload = io.BytesIO()
        selected = {name: ('synthetic-' + name).encode() for name in M.PACKAGES}
        with zipfile.ZipFile(payload, 'w') as archive:
            for name, value in selected.items():
                archive.writestr(name + '_' + M.NEW + '_amd64.deb', value)
            archive.writestr('build-receipt.json', json.dumps({'schema': 'noble_brotli_source_build/1', 'status': 'passed',
                'version': M.NEW, 'tests': {'status': 'passed', 'python_embedded_decoder_rebuilt': True}}))
            for number in range(6):
                archive.writestr('../bad' if unsafe and number == 0 else 'public-' + str(number), b'public-proof')
        raw = payload.getvalue()
        path = self.folder / 'candidate.zip'
        path.write_bytes(raw)
        os.chmod(path, 0o600)
        pins = {name: (len(value), sha256(value).hexdigest()) for name, value in selected.items()}
        self.stack.enter_context(patch.object(M, 'BUNDLE_SHA', sha256(raw).hexdigest()))
        self.stack.enter_context(patch.object(M, 'BUNDLE_BYTES', len(raw)))
        self.stack.enter_context(patch.object(M, 'INPUTS', pins))
        return path, selected

    def test_candidate_bundle_selects_only_the_two_already_installed_packages(self):
        path, selected = self.candidate()
        actual = M.bundle(path)
        self.assertEqual(set(actual), {name + '_' + M.NEW + '_amd64.deb' for name in M.PACKAGES})
        self.assertEqual(set(actual.values()), set(selected.values()))

    def test_bundle_tampering_and_traversal_are_refused(self):
        path, _ = self.candidate(unsafe=True)
        with self.assertRaisesRegex(RuntimeError, 'candidate_archive_inventory'):
            M.bundle(path)
        path.write_bytes(path.read_bytes() + b'changed')
        with self.assertRaisesRegex(RuntimeError, 'candidate_bundle_changed'):
            M.bundle(path)

    def test_simulated_package_closure_rejects_removal_extra_package_and_wrong_version(self):
        good = '\n'.join('Inst ' + name + ' [' + M.OLD + '] (' + M.NEW + ' local [amd64])' for name in M.PACKAGES)
        with patch.object(M, 'command', return_value=good):
            M.simulate(self.folder, M.NEW)
        for bad in (good + '\nRemv protected', good + '\nInst unrelated (1.0 local)', good.replace(M.NEW, 'wrong-version'), good.splitlines()[0]):
            with self.subTest(bad=bad), patch.object(M, 'command', return_value=bad), self.assertRaises(RuntimeError):
                M.simulate(self.folder, M.NEW)

    def test_dpkg_pending_triggers_are_dirty_and_not_treated_as_installed(self):
        raw = 'safe\tinstalled\nremoved\tconfig-files\nlibbrotli1\ttriggers-pending\nforeign\thalf-configured\n'
        with patch.object(M, 'command', return_value=raw):
            self.assertEqual(M.dirty_packages(), {'libbrotli1', 'foreign'})

    def test_service_inhibitor_is_exactly_owned_and_removed_on_failure(self):
        target = self.folder / 'policy-rc.d'
        with patch.object(M, 'POLICY', target):
            with self.assertRaisesRegex(ValueError, 'synthetic_failure'):
                with M.no_restarts():
                    self.assertEqual(target.read_bytes(), M.POLICY_BODY)
                    self.assertEqual(target.stat().st_mode & 0o777, 0o755)
                    raise ValueError('synthetic_failure')
        self.assertFalse(target.exists())

    def test_foreign_policy_is_neither_overwritten_nor_deleted(self):
        target = self.folder / 'policy-rc.d'
        target.write_bytes(b'foreign policy')
        with patch.object(M, 'POLICY', target), self.assertRaisesRegex(RuntimeError, 'preexisting_service_policy_refused'):
            with M.no_restarts():
                self.fail('must not enter')
        self.assertEqual(target.read_bytes(), b'foreign policy')

    def test_replaced_owned_policy_is_not_deleted(self):
        target = self.folder / 'policy-rc.d'
        with patch.object(M, 'POLICY', target), self.assertRaisesRegex(RuntimeError, 'owned_service_policy_changed'):
            with M.no_restarts():
                target.unlink()
                target.write_bytes(b'foreign replacement')
        self.assertEqual(target.read_bytes(), b'foreign replacement')

    def test_hardlinked_output_is_refused_before_truncation(self):
        original, alias = self.folder / 'original', self.folder / 'alias'
        original.write_bytes(b'preserve-public-proof')
        os.chmod(original, 0o600)
        os.link(original, alias)
        with self.assertRaisesRegex(RuntimeError, 'unsafe_output'):
            M.write(alias, b'new', exclusive=False)
        self.assertEqual(original.read_bytes(), b'preserve-public-proof')

    def test_existing_idle_zero_byte_source_flock_is_valid_and_unchanged(self):
        target = self.folder / 'source.lock'
        target.write_bytes(b'')
        target.chmod(0o600)
        before = M.identity(target.lstat())
        with patch.object(M, 'LEGACY_SOURCE', target):
            with M.legacy_source_guard() as fd:
                self.assertEqual(M.identity(os.fstat(fd)), before)
        self.assertEqual(M.identity(target.lstat()), before)
        self.assertEqual(target.read_bytes(), b'')

    def test_busy_existing_source_flock_is_refused_without_taking_it_over(self):
        target = self.folder / 'source.lock'
        target.write_bytes(b'')
        target.chmod(0o600)
        fd = os.open(target, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(M, 'LEGACY_SOURCE', target), self.assertRaisesRegex(RuntimeError, 'native_source_lock_busy'):
                with M.legacy_source_guard():
                    self.fail('busy lock admitted')
        finally:
            os.close(fd)
        self.assertEqual(target.read_bytes(), b'')

    def test_replaced_source_flock_inode_is_refused_and_never_deleted(self):
        target = self.folder / 'source.lock'
        target.write_bytes(b'')
        target.chmod(0o600)
        with patch.object(M, 'LEGACY_SOURCE', target), self.assertRaisesRegex(RuntimeError, 'native_source_lock_changed'):
            with M.legacy_source_guard():
                target.unlink()
                target.write_bytes(b'')
                target.chmod(0o600)
        self.assertTrue(target.exists())

    def test_default_check_never_opens_marker_or_creates_workspace(self):
        with patch.object(M, 'native'), patch.object(M, 'versions', return_value={n: M.OLD for n in M.PACKAGES}), \
             patch.object(M, 'bundle', return_value={}), patch.object(M, 'runtime', return_value=[]), \
             patch.object(M, 'units', return_value={}), patch.object(M.os, 'open', side_effect=AssertionError('no_writes')):
            result = M.action('check', self.folder / 'unused')
        self.assertFalse(result['host_installed'])
        self.assertEqual(result['consumer_refresh_required'], list(M.UNITS))

    def test_installed_api_uses_fresh_isolated_interpreter_and_never_live_service(self):
        with patch.object(M, 'command', return_value='installed_brotli_api_passed\n') as command:
            M.installed_api(M.NEW)
        argv = command.call_args.args[0]
        self.assertEqual(argv[:5], ['/usr/bin/python3', '-I', '-B', '-S', '-c'])
        self.assertEqual(argv[-1], 'patched')
        self.assertIn('_ERROR_FORMAT_BLOCK_SWITCH', argv[-2])

    def test_installation_recipe_has_no_daemon_or_container_mutation(self):
        source = (ROOT / 'deploy/maintenance/brotli_native_install.py').read_text()
        self.assertIn("['/usr/bin/dpkg', '--install'", source)
        for forbidden in ("'restart'", "'stop'", "'start'", "'kill'", "'--force-depends'", "'--force-remove-essential'"):
            self.assertNotIn(forbidden, source)
        self.assertEqual(set(M.INPUTS), {'libbrotli1', 'python3-brotli'})
        self.assertEqual(M.ENV['NEEDRESTART_MODE'], 'l')

    def transaction_fixture(self, *, fail_first_install=False):
        folder = self.folder / 'transaction'
        marker, policy, source_lock = self.folder / 'marker', self.folder / 'policy', self.folder / 'source.lock'
        marker.write_bytes(b'original-production-authority')
        os.chmod(marker, 0o600)
        source_lock.write_bytes(b'')
        source_lock.chmod(0o600)
        old = {name: ('old-' + name).encode() for name in M.PACKAGES}
        new = {name: ('new-' + name).encode() for name in M.PACKAGES}
        states = {name: M.OLD for name in M.PACKAGES}
        calls, failures = [], [fail_first_install]
        for name, value in [('ROOT', folder), ('MARKER', marker), ('POLICY', policy), ('LEGACY_SOURCE', source_lock),
                            ('BASELINE', {n: (len(b), sha256(b).hexdigest()) for n, b in old.items()}),
                            ('INPUTS', {n: (len(b), sha256(b).hexdigest()) for n, b in new.items()})]:
            self.stack.enter_context(patch.object(M, name, value))
        for name, value in [('native', None), ('runtime', [{'id': 'a' * 64, 'mounts': []}]),
                            ('units', {n: {'MainPID': '10'} for n in M.UNITS}), ('boot_id', 'fixed-boot'), ('installed_api', None)]:
            self.stack.enter_context(patch.object(M, name, return_value=value))
        self.stack.enter_context(patch.object(M, 'versions', side_effect=lambda: dict(states)))
        self.stack.enter_context(patch.object(M, 'bundle', return_value={n + '_' + M.NEW + '_amd64.deb': b for n, b in new.items()}))

        def command(argv, **kwargs):
            calls.append(argv)
            if argv[:2] == ['/usr/bin/apt-get', 'download']:
                for name, value in old.items():
                    M.write(folder / 'rollback' / (name + '_' + M.OLD + '_amd64.deb'), value, exclusive=True)
                return ''
            if argv[:2] == ['/usr/bin/dpkg-deb', '-f']:
                package, version, _ = Path(argv[2]).name.split('_')
                return 'Package: ' + package + '\nVersion: ' + version + '\nArchitecture: amd64\n'
            if '--simulate' in argv:
                version = Path(argv[-1]).name.split('_')[1]
                return '\n'.join('Inst ' + n + ' [' + states[n] + '] (' + version + ' local [amd64])' for n in M.PACKAGES)
            if argv[:2] == ['/usr/bin/dpkg', '--install']:
                version = Path(argv[-1]).name.split('_')[1]
                self.assertTrue(policy.exists())
                if failures[0]:
                    failures[0] = False
                    states['libbrotli1'] = M.NEW
                    raise RuntimeError('native_command_failed_dpkg_1')
                states.update({name: version for name in M.PACKAGES})
                return ''
            if argv[0] == '/usr/bin/dpkg-query':
                return '\n'.join(name + '\tinstalled' for name in M.PACKAGES)
            if argv[:2] in (['/usr/bin/dpkg', '--audit'], ['/usr/bin/dpkg', '--verify']):
                return ''
            raise AssertionError('unexpected native call: ' + repr(argv))
        self.stack.enter_context(patch.object(M, 'command', side_effect=command))
        return folder, marker, policy, states, calls

    def test_stage_precedes_install_and_success_stays_partial_without_service_changes(self):
        folder, marker, policy, states, calls = self.transaction_fixture()
        result = M.action('stage', self.folder / 'public.zip')
        self.assertEqual(result['status'], 'staged_not_installed')
        self.assertFalse(any(row[:2] == ['/usr/bin/dpkg', '--install'] for row in calls))
        self.assertEqual(len(list((folder / 'rollback').iterdir())), 2)
        result = M.action('install', self.folder / 'public.zip')
        self.assertEqual(states, {name: M.NEW for name in M.PACKAGES})
        self.assertEqual(result['status'], 'on_disk_patched_consumers_pending')
        self.assertFalse(result['security_transition_completed'])
        self.assertFalse(result['services_restarted'])
        self.assertFalse(policy.exists())
        self.assertEqual(marker.read_bytes(), b'original-production-authority')

    def test_owned_partial_failure_can_restore_only_the_exact_old_packages(self):
        folder, marker, policy, states, calls = self.transaction_fixture(fail_first_install=True)
        M.action('stage', self.folder / 'public.zip')
        with self.assertRaisesRegex(RuntimeError, 'native_command_failed_dpkg_1'):
            M.action('install', self.folder / 'public.zip')
        self.assertEqual(json.loads((folder / 'receipt.json').read_bytes())['phase'], 'install_started')
        self.assertFalse(policy.exists())
        self.assertNotEqual(len(set(states.values())), 1)
        result = M.action('rollback', self.folder / 'public.zip')
        self.assertEqual(states, {name: M.OLD for name in M.PACKAGES})
        self.assertEqual(result['status'], 'rolled_back_security_open')
        for call in [row for row in calls if row[:2] == ['/usr/bin/dpkg', '--install']]:
            self.assertEqual(len(call), 4)
        self.assertEqual(marker.read_bytes(), b'original-production-authority')


if __name__ == '__main__':
    unittest.main()
