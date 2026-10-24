"""Public disposable filesystem fixtures only; all real commands denied."""
from contextlib import ExitStack
import errno
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('component_probe_fixture', REPO / 'scripts/verify_image_component_presence.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
IMAGE, COMMIT, DIGEST = 'sha256:' + '1' * 64, '2' * 40, '3' * 64


class ComponentPresence(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for target in ('subprocess.Popen', 'subprocess.run', 'os.system', 'socket.socket.connect',
                       'socket.create_connection', 'socket.getaddrinfo', 'urllib.request.urlopen'):
            self.stack.enter_context(patch(target, side_effect=AssertionError('fixture only')))
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.component-fixture-', dir=REPO)))
        path = self.root / 'var/lib/dpkg/status'
        path.parent.mkdir(parents=True)
        path.write_bytes(self.status())
        (self.root / 'usr/share/perl/5.40.1').mkdir(parents=True)
        self.stack.enter_context(patch.object(M, 'perl_paths', return_value=['/usr/share/perl/5.40.1']))

    @staticmethod
    def status():
        return b''.join(('Package: '+name+'\nStatus: install ok installed\nVersion: '+version+'\n\n').encode()
                        for name, version in [('libsystemd0', '257.13-1~deb13u1'),
                                              ('libudev1', '257.13-1~deb13u1'), ('perl-base', '5.40.1-6+deb13u1')])

    def probe(self):
        return M.probe(IMAGE, COMMIT, DIGEST, root=self.root)

    def test_actual_files_not_source_package_label_determine_absence(self):
        result = self.probe()
        self.assertTrue(all(row['component_absent'] for row in result['components'].values()))
        self.assertIn('libsystemd0', result['packages'])
        self.assertIn('perl-base', result['packages'])
        self.assertFalse(result['scanner_findings_modified'])
        self.assertFalse(result['security_accepted'])
        self.assertFalse(result['renamed_or_obfuscated_embedded_code_analyzed'])

    def test_homed_binary_or_unit_or_activation_or_symlink_prevents_absence(self):
        for name in ('usr/lib/systemd/systemd-homed', 'usr/lib/systemd/systemd-homework',
                     'usr/lib/systemd/system/systemd-homed.service',
                     'usr/share/dbus-1/system-services/org.freedesktop.home1.service', 'usr/bin/homectl'):
            with self.subTest(name=name):
                path = self.root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.symlink_to('/intentionally-absent-public-fixture')
                self.assertFalse(self.probe()['components']['systemd_homed']['component_absent'])
                path.unlink()

    def test_archive_tar_in_standard_or_app_vendor_tree_prevents_absence(self):
        for name in ('usr/share/perl/5.40.1/Archive/Tar.pm', 'app/vendor/Archive/Tar.pm',
                     'opt/module/Archive/Tar/Constant.pm'):
            with self.subTest(name=name):
                path = self.root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'public fixture')
                self.assertFalse(self.probe()['components']['archive_tar']['component_absent'])
                path.unlink()

    def test_actual_inc_path_outside_inventory_roots_is_checked(self):
        path = self.root / 'custom-perl/Archive/Tar.pm'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'public fixture')
        with patch.object(M, 'perl_paths', return_value=['/custom-perl']):
            result = self.probe()
        self.assertFalse(result['components']['archive_tar']['component_absent'])
        self.assertEqual(result['perl_inc_archive_tar_matches'], ['/custom-perl'])

    def test_missing_actual_inc_module_is_absent_only_after_enoent(self):
        path = self.root / 'custom-perl/Archive/Tar.pm'
        with self.assertRaises(FileNotFoundError) as missing:
            path.lstat()
        self.assertEqual(missing.exception.errno, errno.ENOENT)
        with patch.object(M, 'perl_paths', return_value=['/custom-perl']):
            result = self.probe()
        self.assertTrue(result['components']['archive_tar']['component_absent'])
        self.assertEqual(result['perl_inc_archive_tar_matches'], [])

    def test_permission_denied_actual_inc_lookup_refuses_absence_evidence(self):
        path = self.root / 'custom-perl/Archive/Tar.pm'
        original_lstat = Path.lstat
        denied = PermissionError(errno.EACCES, 'public fixture access denied', str(path))
        # A targeted permission fixture works even when the test runner is root.
        def lstat(candidate, *args, **kwargs):
            if candidate == path:
                raise denied
            return original_lstat(candidate, *args, **kwargs)
        with patch.object(M, 'perl_paths', return_value=['/custom-perl']), \
                patch.object(Path, 'lstat', new=lstat), self.assertRaises(PermissionError) as refused:
            self.probe()
        self.assertIs(refused.exception, denied)
        self.assertEqual(refused.exception.errno, errno.EACCES)

    def test_loop_in_actual_inc_directory_refuses_absence_evidence(self):
        # This real loop is outside inventory roots, so only the @INC lookup
        # detects that module presence cannot be determined.
        (self.root / 'custom-perl').symlink_to('custom-perl', target_is_directory=True)
        with patch.object(M, 'perl_paths', return_value=['/custom-perl']), \
                self.assertRaises(OSError) as refused:
            self.probe()
        self.assertEqual(refused.exception.errno, errno.ELOOP)

    def test_directory_symlink_loop_is_not_followed_and_coverage_is_explicit(self):
        path = self.root / 'usr/loop'
        path.symlink_to(path, target_is_directory=True)
        result = self.probe()
        self.assertLess(result['inventory']['entries_examined'], 10)
        self.assertEqual(result['scope'], 'known_vendor_component_paths_and_names_plus_actual_perl_inc')

    def test_usr_merge_aliases_are_not_rescanned(self):
        (self.root / 'usr/lib').mkdir()
        (self.root / 'lib').symlink_to('usr/lib', target_is_directory=True)
        scan = M.inventory([self.root / 'usr', self.root / 'lib'])
        self.assertEqual(scan['roots'], [str(self.root / 'usr')])

    def test_inventory_file_and_time_bounds_refuse_incomplete_evidence(self):
        with self.assertRaisesRegex(RuntimeError, 'inventory_count_bound'):
            M.inventory([self.root / 'usr'], maximum=1)
        with self.assertRaisesRegex(RuntimeError, 'inventory_timeout'):
            M.inventory([self.root / 'usr'], deadline=0)

    def test_unreadable_tree_refuses_instead_of_silently_proving_absence(self):
        def walk(*args, **kwargs):
            kwargs['onerror'](PermissionError())
            return iter(())
        with patch.object(M.os, 'walk', side_effect=walk), self.assertRaisesRegex(RuntimeError, 'inventory_unreadable'):
            self.probe()

    def test_unreadable_inventory_root_refuses_before_false_absence(self):
        root = self.root / 'opt'
        original = Path.lstat
        def lstat(candidate, *args, **kwargs):
            if candidate == root:
                raise PermissionError(errno.EACCES, 'public fixture denied', str(root))
            return original(candidate, *args, **kwargs)
        with patch.object(Path, 'lstat', new=lstat), self.assertRaises(PermissionError):
            M.inventory([root])

    def test_inventory_root_symlink_loop_refuses_before_false_absence(self):
        root = self.root / 'inventory-loop'
        root.symlink_to('inventory-loop', target_is_directory=True)
        with self.assertRaises((OSError, RuntimeError)):
            M.inventory([root])

    def test_dpkg_status_duplicate_and_missing_packages_refuse(self):
        with self.assertRaisesRegex(RuntimeError, 'invalid_package_record'):
            M.packages(self.status() + self.status())
        with self.assertRaisesRegex(RuntimeError, 'expected_runtime_packages_missing'):
            M.packages(b'Package: unrelated\nVersion: 1\nStatus: install ok installed\n')

    def test_unsafe_and_nonregular_status_not_read(self):
        path = self.root / 'public'
        path.write_bytes(b'public fixture')
        with self.assertRaisesRegex(RuntimeError, 'input_metadata'):
            M.read_regular(path, 2)
        link = self.root / 'link'
        link.symlink_to(path)
        with self.assertRaises(OSError):
            M.read_regular(link, 100)

    def test_changed_status_during_read_refuses(self):
        path = self.root / 'public'
        path.write_bytes(b'fixture')
        real_read = M.os.read
        changed = False
        def read(*args):
            nonlocal changed
            raw = real_read(*args)
            if not changed:
                path.write_bytes(b'changed-longer')
                changed = True
            return raw
        with patch.object(M.os, 'read', side_effect=read), self.assertRaisesRegex(RuntimeError, 'input_changed'):
            M.read_regular(path, 100)

    def test_perl_inc_is_actual_fixed_command_with_sanitized_output_validation(self):
        source = (REPO / 'scripts/verify_image_component_presence.py').read_text()
        import ast
        function = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == 'perl_paths')
        namespace = {'bounded_command': lambda argv: b'/usr/share/perl/5.40.1\n', 'require': M.require, 'Path': Path}
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<perl-inc-fixture>', 'exec'), namespace)
        self.assertEqual(namespace['perl_paths'](), ['/usr/share/perl/5.40.1'])
        for raw in (b'./relative\n', b'/safe\n/safe\n', b'/safe/../unsafe\n', b''):
            namespace['bounded_command'] = lambda argv, raw=raw: raw
            with self.assertRaises(RuntimeError):
                namespace['perl_paths']()

    def test_container_receipt_is_exact_image_and_buffer_without_mounts_or_network(self):
        raw = b'public verifier fixture'
        digest = sha256(raw).hexdigest()
        receipt = {'schema': 'security_image_component_presence/1', 'image_id': IMAGE,
                   'source_commit': COMMIT, 'verifier_sha256': digest,
                   'scanner_findings_modified': False, 'security_accepted': False}
        with patch.object(M, 'bounded_command', side_effect=[json.dumps(receipt).encode(), b'']) as command:
            result = M.in_container(IMAGE, COMMIT, raw, digest)
        self.assertEqual(json.loads(result), receipt)
        argv = command.call_args_list[0].args[0]
        self.assertEqual(argv[argv.index('--network') + 1], 'none')
        self.assertIn('--read-only', argv)
        self.assertEqual(argv[argv.index('--cap-drop') + 1], 'ALL')
        self.assertNotIn('--mount', argv)
        self.assertNotIn('--env', argv)
        self.assertIn('--pull=never', argv)
        self.assertIn(raw.hex(), argv)
        self.assertEqual(command.call_args_list[0].kwargs, {'maximum': 65536, 'timeout': 75})

    def test_changed_verifier_or_wrong_receipt_never_produces_evidence(self):
        with self.assertRaisesRegex(RuntimeError, 'verifier_mismatch'):
            M.in_container(IMAGE, COMMIT, b'changed', DIGEST)
        raw = b'fixture'
        with patch.object(M, 'bounded_command', side_effect=[b'{"image_id":"wrong"}', b'']), \
                self.assertRaisesRegex(RuntimeError, 'receipt_identity_mismatch'):
            M.in_container(IMAGE, COMMIT, raw, sha256(raw).hexdigest())

    def test_timed_out_client_cleans_only_actual_cid_with_exact_unique_owner_label(self):
        raw, owner, cid = b'public fixture', 'a' * 32, 'b' * 64
        name = 'security-component-probe-' + owner
        record = json.dumps({'id': cid, 'name': '/' + name, 'owner': owner}).encode()
        with patch.object(M.uuid, 'uuid4') as token, patch.object(M, 'bounded_command', side_effect=[
                RuntimeError('command_timeout'), (cid + '\n').encode(), record, (cid + '\n').encode(), b'']) as command:
            token.return_value.hex = owner
            with self.assertRaisesRegex(RuntimeError, 'command_timeout'):
                M.in_container(IMAGE, COMMIT, raw, sha256(raw).hexdigest())
        self.assertEqual(command.call_args_list[2].args[0][-1], cid)
        self.assertEqual(command.call_args_list[3].args[0][-3:], ['rm', '--force', cid])
        self.assertEqual(command.call_args_list[-1].kwargs['timeout'], 5)

    def test_foreign_container_name_collision_is_never_removed(self):
        owner, cid = 'a' * 32, 'b' * 64
        name = 'security-component-probe-' + owner
        row = json.dumps({'id': cid, 'name': '/' + name, 'owner': 'foreign'}).encode()
        with patch.object(M, 'bounded_command', side_effect=[cid.encode(), row]) as command, \
                self.assertRaisesRegex(RuntimeError, 'cleanup_owner_mismatch'):
            M.cleanup_container(['docker'], name, owner)
        self.assertFalse(any('rm' in call.args[0] for call in command.call_args_list))

    def test_unverified_cleanup_never_returns_successful_probe_receipt(self):
        raw = b'public fixture'
        with patch.object(M, 'bounded_command', side_effect=[b'{}', RuntimeError('command_timeout')]), \
                self.assertRaisesRegex(RuntimeError, 'command_timeout'):
            M.in_container(IMAGE, COMMIT, raw, sha256(raw).hexdigest())

    def test_invalid_image_identity_refuses_before_reading_any_files(self):
        with patch.object(M, 'read_regular') as read, self.assertRaisesRegex(RuntimeError, 'identity_invalid'):
            M.probe('mutable:latest', COMMIT, DIGEST, root=self.root)
        read.assert_not_called()

    def test_real_pipe_output_is_capped_before_unbounded_accumulation_and_child_is_reaped(self):
        reader, writer = os.pipe()
        os.write(writer, b'x' * 6000)
        os.close(writer)
        class Child:
            pid = 999999
            stdout = os.fdopen(reader, 'rb', buffering=0)
            status = None
            def poll(self): return self.status
            def wait(self, **kwargs): self.status = 0; return 0
        child = Child()
        reads = []
        original = M.os.read
        def read(fd, maximum):
            result = original(fd, maximum)
            reads.append(len(result))
            return result
        with patch.object(M.subprocess, 'Popen', return_value=child) as launch, \
                patch.object(M.os, 'killpg') as kill, patch.object(M.os, 'read', side_effect=read), \
                self.assertRaisesRegex(RuntimeError, 'command_output_bound'):
            M.bounded_command(['/usr/bin/perl', '-T', '-e', 'public fixture'], maximum=4096)
        self.assertEqual(sum(reads), 4097)
        kill.assert_called_once_with(child.pid, M.signal.SIGKILL)
        self.assertTrue(child.stdout.closed)
        self.assertEqual(child.status, 0)
        self.assertEqual(launch.call_args.kwargs['env'], {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C.UTF-8'})

    def test_ci_seals_before_npm_probes_exact_built_image_and_preserves_raw_gate(self):
        import yaml
        job = yaml.safe_load((REPO / '.github/workflows/pre-release-gate.yml').read_text())['jobs']['security-consumer-payload']
        steps = job['steps']
        seal = next(s for s in steps if s.get('id') == 'payload_seal')
        check = next(s for s in steps if s['name'].startswith('Record actual immutable-image'))
        scan = next(s for s in steps if s['name'].startswith('Scan the actual candidate'))
        self.assertIn("maximum=32768", seal['run'])
        self.assertIn("module.git(Path.cwd(),'show',os.environ['PAYLOAD_SOURCE_COMMIT']+':scripts/verify_image_component_presence.py'", seal['run'])
        self.assertEqual(check['env']['PAYLOAD_IMAGE'], '${{ steps.payload-build.outputs.image_id }}')
        self.assertEqual(check['env']['PAYLOAD_COMPONENT_PROBE_SHA256'], '${{ steps.payload_seal.outputs.component_probe_sha256 }}')
        self.assertIn('module.in_container', check['run'])
        self.assertEqual(scan['with']['ignore-unfixed'], 'false')
        self.assertEqual(scan['with']['exit-code'], '1')
        self.assertNotIn('vex', scan['with'])
        self.assertTrue(any('component-presence.json' in str(s.get('with', {}).get('path', '')) for s in steps))


if __name__ == '__main__':
    unittest.main()
