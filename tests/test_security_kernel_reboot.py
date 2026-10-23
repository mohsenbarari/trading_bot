"""Hermetic reboot contracts: no live service, Docker, SSH, network or private IO."""
import ast
from contextlib import ExitStack
from datetime import datetime, timezone
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.request

import yaml
from jinja2 import Environment

PROJECT = Path(__file__).resolve().parents[1]
HERE = PROJECT / 'deploy/maintenance'


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


G = module('_security_reboot_guard_test', 'reboot_guard.py')
C = module('_security_reboot_controller_test', 'reboot_controller.py')
W = module('_security_reboot_fetch_test', 'reboot_fetch_wheel.py')


def known_monitor_cron():
    return ('PYTHONPATH=' + str(G.INTEGRATION) + '\n' +
            '*/15 * * * * /usr/bin/tmux set-environment -g PYTHONPATH ' + str(G.INTEGRATION) +
            ' >/dev/null 2>&1 ; /usr/bin/bash ' + str(G.MONITOR) + ' --quota-wait-enabled >/dev/null 2>&1\n')


class FencedCase(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.temp.chmod(0o700)
        def forbidden(*args, **kwargs):
            raise AssertionError('unexpected real command or network operation')
        for target in ('subprocess.run', 'subprocess.Popen', 'os.system', 'socket.socket.connect',
                       'socket.create_connection', 'socket.getaddrinfo', 'urllib.request.urlopen'):
            self.stack.enter_context(patch(target, side_effect=forbidden))
        original_open = os.open
        def safe_open(path, *args, **kwargs):
            if isinstance(path, int) or not Path(path).absolute().is_relative_to(self.temp):
                raise AssertionError('unexpected non-fixture file open')
            return original_open(path, *args, **kwargs)
        self.stack.enter_context(patch('os.open', side_effect=safe_open))

    def write(self, name, value=b''):
        path = self.temp / name
        path.write_bytes(value)
        path.chmod(0o600)
        return path


class NativeRebootTests(FencedCase):
    def setUp(self):
        super().setUp()
        for name in ('STATE', 'RESULT', 'TOKEN', 'MANIFEST', 'GOLDEN_INHIBITOR'):
            self.stack.enter_context(patch.object(G, name, self.temp / (name + '.json')))
        for name in ('RESERVATION', 'MARKER', 'SOURCE', 'PENDING'):
            self.stack.enter_context(patch.object(G.B, name, self.temp / (name + '.json')))
        self.stack.enter_context(patch.object(G.B, 'private_parent', side_effect=lambda path:
            G.require(Path(path).is_relative_to(self.temp), 'non_fixture_parent')))
        self.stack.enter_context(patch.object(G.B, 'command', side_effect=AssertionError('unexpected native command')))
        self.stack.enter_context(patch.object(G, 'boot_id', return_value='11111111-1111-4111-8111-111111111111'))
        self.stack.enter_context(patch.object(G, 'now', return_value=datetime(2026, 10, 23, 20, tzinfo=timezone.utc)))
        self.plan = {'nonce': '22222222-2222-4222-8222-222222222222',
                     'before_boot_id': G.boot_id(), 'units': []}
        self.digest = 'a' * 64
        G.B.MARKER.write_bytes(b'historic transferred authority unchanged\n')
        G.B.SOURCE.write_bytes(b'')
        G.B.MARKER.chmod(0o600)
        G.B.SOURCE.chmod(0o600)

    def assert_history_unchanged(self):
        self.assertEqual(G.B.MARKER.read_bytes(), b'historic transferred authority unchanged\n')
        self.assertEqual(G.B.SOURCE.read_bytes(), b'')

    def test_owned_reservation_is_durable_distinct_and_does_not_adopt_authority(self):
        with ExitStack() as locks:
            G.lock_current(locks)
            G.reserve(self.plan, self.digest, locks)
            self.assertEqual(G.own_reservation(self.plan, self.digest)['schema'], 'security_kernel_reboot_reservation/1')
            self.assertEqual(G.state(self.plan, self.digest)['phase'], 'reserved')
            self.assertEqual(G.B.document(G.TOKEN), G.token_payload(self.plan, self.digest))
            G.check_owners(self.plan, self.digest)
            self.assert_history_unchanged()

    def test_foreign_reservation_refused_without_changes(self):
        G.B.RESERVATION.write_bytes(b'foreign controller')
        with ExitStack() as locks, self.assertRaises(G.B.Refusal):
            G.reserve(self.plan, self.digest, locks)
        self.assertEqual(G.B.RESERVATION.read_bytes(), b'foreign controller')
        self.assertFalse(G.STATE.exists())
        self.assert_history_unchanged()

    def test_short_reservation_write_never_publishes_partial_owner(self):
        def failed(fd, raw):
            os.write(fd, raw[:3])
            raise OSError('synthetic short write')
        with ExitStack() as locks, patch.object(G.B, 'write_all', side_effect=failed), self.assertRaises(OSError):
            G.reserve(self.plan, self.digest, locks)
        self.assertFalse(G.B.RESERVATION.exists())
        self.assertFalse(G.STATE.exists())
        self.assert_history_unchanged()

    def test_reservation_publish_race_never_overwrites_foreign_winner(self):
        real_atomic = G.atomic
        def atomic(path, value, exclusive=False):
            real_atomic(path, value, exclusive)
            if path == G.STATE:
                G.B.RESERVATION.write_bytes(b'race winner')
        with ExitStack() as locks, patch.object(G, 'atomic', side_effect=atomic), self.assertRaises(G.B.Refusal):
            G.reserve(self.plan, self.digest, locks)
        self.assertEqual(G.B.RESERVATION.read_bytes(), b'race winner')
        self.assert_history_unchanged()

    def test_crash_before_token_is_fail_closed_owned_intent_not_fake_success(self):
        real_atomic = G.atomic
        def atomic(path, value, exclusive=False):
            if path == G.TOKEN:
                raise OSError('synthetic crash')
            return real_atomic(path, value, exclusive)
        with ExitStack() as locks, patch.object(G, 'atomic', side_effect=atomic), self.assertRaises(OSError):
            G.reserve(self.plan, self.digest, locks)
        self.assertTrue(G.B.RESERVATION.exists())
        self.assertEqual(G.state(self.plan, self.digest)['phase'], 'intent_durable')
        self.assertFalse(G.TOKEN.exists())
        self.assertFalse(G.RESULT.exists())
        self.assert_history_unchanged()

    def test_stale_boot_pid_cannot_establish_fence_ownership(self):
        with ExitStack() as locks:
            G.lock_current(locks)
            G.reserve(self.plan, self.digest, locks)
            with patch.object(G, 'boot_id', return_value='33333333-3333-4333-8333-333333333333'):
                with self.assertRaisesRegex(G.B.Refusal, 'stale_native_owner_boot'):
                    G.check_owners(self.plan, self.digest)

    def test_unheld_current_inode_is_not_accepted_by_pid(self):
        with ExitStack() as locks:
            G.reserve(self.plan, self.digest, locks)
            with self.assertRaisesRegex(G.B.Refusal, 'native_fence_lost'):
                G.check_owners(self.plan, self.digest)

    def test_crossboot_restore_refuses_non_reboot_requested_journal(self):
        with ExitStack() as locks:
            G.reserve(self.plan, self.digest, locks)
        with patch.object(G, 'invariants'), patch.object(G, 'lock_current', return_value=[]):
            with self.assertRaisesRegex(G.B.Refusal, 'postboot_phase_not_reboot_requested'):
                G.execute(self.plan, self.digest, before=False)
        self.assertFalse(G.RESULT.exists())
        self.assert_history_unchanged()

    def test_changed_reservation_nonce_or_inode_is_not_adopted(self):
        with ExitStack() as locks:
            G.reserve(self.plan, self.digest, locks)
        value = G.B.document(G.B.RESERVATION)
        value['nonce'] = 'f' * 36
        G.atomic(G.B.RESERVATION, value)
        with self.assertRaisesRegex(G.B.Refusal, 'foreign_or_changed_reservation'):
            G.own_reservation(self.plan, self.digest)

    def test_exclusive_journal_publish_does_not_replace_existing_file(self):
        G.atomic(G.STATE, {'old': True}, exclusive=True)
        with self.assertRaises(G.B.Refusal):
            G.atomic(G.STATE, {'old': False}, exclusive=True)
        self.assertEqual(G.B.document(G.STATE), {'old': True})

    def test_mount_proof_ignores_device_alias_but_binds_uuid_root_inode(self):
        fixture = self.temp / 'mount-path'
        fixture.mkdir(mode=0o700)
        value = {'filesystems': [{'target': str(fixture), 'source': '/dev/sdb[/retained/root]',
                                  'fstype': 'ext4', 'uuid': 'fixed-uuid'}]}
        with patch.object(G.B, 'command', return_value=json.dumps(value)):
            initial = G.mount_binding(str(fixture))
            value['filesystems'][0]['source'] = '/dev/sdc[/retained/root]'
            with patch.object(G.B, 'command', return_value=json.dumps(value)):
                self.assertEqual(G.mount_binding(str(fixture)), initial)
            value['filesystems'][0]['uuid'] = 'different'
            with patch.object(G.B, 'command', return_value=json.dumps(value)):
                self.assertNotEqual(G.mount_binding(str(fixture)), initial)
        self.assertNotIn('mount_id', initial)

    def test_scope_exactly_twenty_one_original_units(self):
        self.assertEqual(len(G.UNITS), 21)
        self.assertNotIn('ssh.service', G.UNITS)
        self.assertNotIn('codex-turkey-relay.service', G.UNITS)

    def test_golden_inhibitor_requires_real_mount_and_only_its_owned_token(self):
        text = G.GOLDEN_INHIBITOR_BYTES.decode()
        self.assertIn('ConditionPathExists=!' + str(G.ROOT / 'release-control' / (G.OPERATION + '.quiescence.json')), text)
        self.assertIn('AssertPathIsMountPoint=' + str(G.ROOT), text)
        self.assertNotIn('ExecStart', text)

    def test_known_cron_is_classified_without_storing_commands(self):
        raw = '@reboot /usr/bin/systemctl restart golden_bot\n' + known_monitor_cron()
        result = G.launcher_inventory(raw)
        self.assertEqual([item['kind'] for item in result], ['golden', 'monitor', 'monitor_pythonpath'])
        self.assertNotIn('tmux', json.dumps(result))
        self.assertNotIn('systemctl', json.dumps(result))

    def test_only_exact_project_pythonpath_assignment_is_admitted(self):
        prefix = '@reboot /usr/bin/systemctl restart golden_bot\n'
        monitor = known_monitor_cron().splitlines()[-1] + '\n'
        env = 'PYTHONPATH=' + str(G.INTEGRATION)
        for assignment in (env + '; true', env + ' extra', env + ':untrusted',
                           'PYTHONPATH=other-two-site-refactor', env + '\n' + env,
                           env + '\nPYTHONPATH=/tmp', env + '\nPYTHONPATH = /tmp'):
            with self.subTest(assignment=assignment), self.assertRaises(G.B.Refusal):
                G.launcher_inventory(prefix + assignment + '\n' + monitor)

    def test_unknown_or_chained_golden_cron_refused(self):
        for suffix in ('; /usr/bin/true', ' && /usr/bin/true', ' --now', '.service'):
            with self.subTest(suffix=suffix), self.assertRaises(G.B.Refusal):
                G.launcher_inventory('@reboot /usr/bin/systemctl restart golden_bot' + suffix)

    def test_missing_durable_stop_refuses_before_any_command(self):
        with patch.object(G, 'STOP', self.temp / 'absent'), self.assertRaises(FileNotFoundError):
            G.admission()

    def test_public_status_0644_allowed_but_group_write_refused(self):
        stop = self.write('STOP', b'STOP\n')
        status = self.write('status.json', b'{"state":"BLOCKED","cycle":8}')
        monitor = self.write('two-site-refactor-monitor.sh', b'public reviewed monitor')
        status.chmod(0o644)
        raw = ('@reboot /usr/bin/systemctl restart golden_bot\n'
               '*/15 * * * * /usr/bin/tmux new-session /usr/bin/bash "' + str(monitor) + '"\n')
        with patch.object(G, 'STOP', stop), patch.object(G, 'AUTOPILOT_STATUS', status), \
             patch.object(G, 'MONITOR', monitor), patch.object(G, 'MONITOR_SHA', sha256(monitor.read_bytes()).hexdigest()), \
             patch.object(G.B, 'command', return_value=raw):
            raw = '@reboot /usr/bin/systemctl restart golden_bot\n' + known_monitor_cron()
            with patch.object(G.B, 'command', return_value=raw):
                self.assertEqual(len(G.admission()), 3)
            status.chmod(0o664)
            with self.assertRaisesRegex(G.B.Refusal, 'file_mode_or_bound'):
                G.admission()

    def test_new_boot_complete_path_reacquires_flocks_and_preserves_history(self):
        with ExitStack() as locks:
            G.reserve(self.plan, self.digest, locks)
            G.advance(self.plan, self.digest, 'reboot_requested')
        def restore(*args):
            G.TOKEN.unlink()
            return 0
        new_boot = '33333333-3333-4333-8333-333333333333'
        with patch.object(G, 'boot_id', return_value=new_boot), patch.object(G, 'invariants') as invariant, \
             patch.object(G, 'phase_check'), patch.object(G, 'invoke_local', side_effect=restore), \
             patch.object(G, 'golden_health'), patch.object(G.B, 'runtime'):
            G.execute(self.plan, self.digest, before=False)
        self.assertTrue(all(call.args[1] is False if len(call.args) > 1 else call.kwargs['before'] is False
                            for call in invariant.call_args_list))
        result = G.B.document(G.RESULT)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['after_boot_id'], new_boot)
        self.assertEqual(result['active_kernel'], G.NEW_KERNEL)
        self.assertTrue(result['marker_journal_bytes_unchanged'])
        self.assertFalse(result['authority_changed'])
        self.assertFalse(G.B.RESERVATION.exists())
        self.assert_history_unchanged()

    def test_failed_restore_retains_owned_reservation_no_false_completed_receipt(self):
        with ExitStack() as locks:
            G.reserve(self.plan, self.digest, locks)
            G.advance(self.plan, self.digest, 'reboot_requested')
        with patch.object(G, 'boot_id', return_value='33333333-3333-4333-8333-333333333333'), \
             patch.object(G, 'invariants'), patch.object(G, 'phase_check'), \
             patch.object(G, 'invoke_local', return_value=1), self.assertRaisesRegex(G.B.Refusal, 'restore_failed'):
            G.execute(self.plan, self.digest, before=False)
        self.assertTrue(G.B.RESERVATION.exists())
        self.assertTrue(G.TOKEN.exists())
        self.assertFalse(G.RESULT.exists())
        self.assert_history_unchanged()

    def test_real_invariants_require_new_boot_and_active_target_not_installed_only(self):
        plan = dict(self.plan, source_revision='f' * 40, machine_id='native-machine', files=[], mounts=[], boot_artifacts=None)
        def command(argv):
            return G.OLD_KERNEL if argv[0] == '/usr/bin/uname' else 'f' * 40
        with patch.object(G, 'native'), patch.object(G, 'admission'), patch.object(G, 'grub_gate', return_value=None), patch.object(G, 'validate_scope'), \
             patch.object(Path, 'read_text', return_value='native-machine'), \
             patch.object(G.B, 'command', side_effect=command):
            G.invariants(plan, before=True)
            with self.assertRaisesRegex(G.B.Refusal, 'boot_identity_mismatch'):
                G.invariants(plan, before=False)
            with patch.object(G, 'boot_id', return_value='33333333-3333-4333-8333-333333333333'):
                with self.assertRaisesRegex(G.B.Refusal, 'active_kernel_not_expected'):
                    G.invariants(plan, before=False)
                with patch.object(G.B, 'command', side_effect=lambda a: G.NEW_KERNEL if a[0] == '/usr/bin/uname' else 'f' * 40):
                    G.invariants(plan, before=False)

    def test_real_invariants_refuse_git_or_bound_evidence_drift(self):
        plan = dict(self.plan, source_revision='f' * 40, machine_id='native-machine', files=[], mounts=[], boot_artifacts=None)
        with patch.object(G, 'native'), patch.object(G, 'admission'), patch.object(G, 'grub_gate', return_value=None), patch.object(G, 'validate_scope'), \
             patch.object(Path, 'read_text', return_value='native-machine'), patch.object(G.B, 'command', return_value='e' * 40):
            with self.assertRaisesRegex(G.B.Refusal, 'source_head_changed'):
                G.invariants(plan, before=True)
        fixture = self.write('evidence', b'old')
        plan['files'] = [G.file_binding(fixture)]
        fixture.write_bytes(b'new')
        with patch.object(G, 'native'), patch.object(G, 'admission'), patch.object(G, 'grub_gate', return_value=None), patch.object(G, 'validate_scope'), \
             patch.object(Path, 'read_text', return_value='native-machine'), patch.object(G.B, 'command', return_value='f' * 40):
            with self.assertRaisesRegex(G.B.Refusal, 'bound_file_changed'):
                G.invariants(plan, before=True)

    def test_grub_target_default_zero_no_override_passes_without_writes(self):
        def read(path, **kwargs):
            return b'GRUB_DEFAULT="0"\n' if path == '/etc/default/grub' else b'menuentry latest {\n linux /boot/vmlinuz-6.8.0-147-generic root=UUID=synthetic\ninitrd /boot/initrd.img-6.8.0-147-generic\n}\n'
        with patch.object(G, 'kernel_artifacts', return_value={'four': 'artifacts'}), patch.object(G.B, 'read', side_effect=read), patch.object(G.B, 'command', return_value='saved_entry=old-ignored'):
            G.grub_gate()

    def test_grub_saved_default_old_first_entry_and_next_override_refused(self):
        for defaults, config, environment in (
            (b'GRUB_DEFAULT=saved', b'linux /boot/vmlinuz-6.8.0-147-generic', ''),
            (b'GRUB_DEFAULT=0\nGRUB_SAVEDEFAULT=true', b'linux /boot/vmlinuz-6.8.0-147-generic', ''),
            (b'GRUB_DEFAULT=0', b'linux /boot/vmlinuz-6.8.0-138-generic', ''),
            (b'GRUB_DEFAULT=0', b'linux /boot/vmlinuz-6.8.0-147-generic\ninitrd /boot/initrd.img-6.8.0-147-generic', 'next_entry=old'),
            (b'GRUB_DEFAULT=0', b'linux /boot/vmlinuz-6.8.0-147-generic\ninitrd /boot/initrd.img-6.8.0-138-generic', ''),
        ):
            with self.subTest(defaults=defaults, environment=environment), \
                 patch.object(G, 'kernel_artifacts', return_value={'four': 'artifacts'}), \
                 patch.object(G.B, 'read', side_effect=lambda p, **kw: defaults if p == '/etc/default/grub' else config), \
                 patch.object(G.B, 'command', return_value=environment), self.assertRaises(G.B.Refusal):
                G.grub_gate()

    def test_mount_destination_symlink_and_non_directory_refused_before_findmnt(self):
        actual = self.temp / 'actual'
        actual.mkdir(mode=0o700)
        link = self.temp / 'link'
        link.symlink_to(actual)
        for path in (link, self.write('not-directory')):
            with self.subTest(path=path.name), self.assertRaisesRegex(G.B.Refusal, 'unsafe_mount_destination'):
                G.mount_binding(str(path))

    def test_target_and_fallback_kernel_initrd_must_be_owned_regular_nonempty(self):
        boot = self.temp / 'boot'
        boot.mkdir(mode=0o700)
        for kernel in (G.OLD_KERNEL, G.NEW_KERNEL):
            for prefix in ('vmlinuz-', 'initrd.img-'):
                artifact = boot / (prefix + kernel)
                artifact.write_bytes(b'nonempty boot artifact')
                artifact.chmod(0o600)
        with patch.object(G, 'BOOT_ROOT', boot):
            self.assertEqual(len(G.kernel_artifacts()), 4)
            target = boot / ('initrd.img-' + G.NEW_KERNEL)
            target.write_bytes(b'')
            with self.assertRaisesRegex(G.B.Refusal, 'boot_artifact_missing_or_unsafe'):
                G.kernel_artifacts()
            target.unlink()
            with patch.object(G, 'native'), patch.object(G.B, 'command', return_value=G.OLD_KERNEL), self.assertRaises(FileNotFoundError):
                G.make_manifest()
            self.assertFalse(G.MANIFEST.exists())
            self.assertFalse(G.STATE.exists())
            self.assertFalse(G.B.RESERVATION.exists())
            self.assert_history_unchanged()

    def test_monitor_extra_or_chained_commands_are_never_normalized_away(self):
        original = '@reboot /usr/bin/systemctl restart golden_bot\n' + known_monitor_cron().rstrip()
        for addition in (' ; /usr/bin/systemctl start golden_bot', ' && /usr/bin/true',
                         ' | /usr/bin/bash', ' &', ' --new-option'):
            with self.subTest(addition=addition), self.assertRaises(G.B.Refusal):
                G.launcher_inventory(original + addition)

    def test_helper_executes_verified_buffer_not_a_second_loader_read(self):
        content = b'VERIFIED_BUFFER = True\n'
        helper = self.write('reviewed.py', content)
        expected = sha256(content).hexdigest()
        class Digest:
            def hexdigest(self):
                helper.write_bytes(b'raise AssertionError("unverified reopened bytes executed")\n')
                return expected
        with patch.object(G, 'HELPER', helper), patch.object(G, 'HELPER_SHA', expected), \
             patch.object(G, 'sha256', return_value=Digest()):
            loaded = G.base_module()
        self.assertTrue(loaded.VERIFIED_BUFFER)
        self.assertEqual(loaded.__file__, str(helper))

    def test_empty_missing_or_duplicate_mount_scope_refused_before_native_reads(self):
        paths = [str(G.PROJECT), str(G.ROOT), '/srv/trading-bot/production-data',
                 '/srv/trading-bot/production-data/docker-data', '/srv/trading-bot/backups',
                 '/root/golden_bot', '/srv/trading-bot/staging-data']
        for selected in ([], paths[:-1], paths[:-1] + [paths[0]]):
            with self.subTest(paths=selected), self.assertRaisesRegex(G.B.Refusal, 'required_mount_binding_missing'):
                G.validate_scope({'mounts': [{'path': p} for p in selected]})

    def test_required_file_binding_cannot_be_omitted_or_private_flag_weakened(self):
        paths = [str(G.PROJECT), str(G.ROOT), '/srv/trading-bot/production-data',
                 '/srv/trading-bot/production-data/docker-data', '/srv/trading-bot/backups',
                 '/root/golden_bot', '/srv/trading-bot/staging-data']
        files = [{'path': path, 'private': flag} for path, flag in G.expected_files().items()]
        for selected in ([], files[:-1], [dict(item, private=False) for item in files]):
            with self.subTest(count=len(selected)), self.assertRaisesRegex(G.B.Refusal, 'required_file_binding_missing'):
                G.validate_scope({'mounts': [{'path': p} for p in paths], 'files': selected})


class WheelFetchTests(FencedCase):
    def setUp(self):
        super().setUp()
        self.stack.enter_context(patch.object(W, 'STAGE', self.temp))

    def test_verify_existing_exact_public_file_streams_and_rejects_size_digest(self):
        path = self.write('fixture.whl', b'verified bytes')
        digest = sha256(path.read_bytes()).hexdigest()
        W.verify(path, 14, digest)
        for size, expected in ((13, digest), (14, '0' * 64)):
            with self.subTest(size=size), self.assertRaises(RuntimeError): W.verify(path, size, expected)

    def test_unapproved_url_or_unbounded_size_refused_before_network(self):
        for url, size in (('http://files.pythonhosted.org/packages/x/a.whl', 1),
                          ('https://evil.invalid/packages/x/a.whl', 1),
                          ('https://files.pythonhosted.org/packages/x/a.whl', 9 * 1024 * 1024)):
            with self.subTest(url=url, size=size), patch.object(W.os, 'geteuid', return_value=0), self.assertRaises(RuntimeError):
                W.fetch(url, 'a.whl', size, 'a' * 64)

    def test_mocked_curl_has_early_cap_and_publishes_only_verified_exact_bytes(self):
        content = b'verified bounded wheel'
        def curl(argv, **kwargs):
            self.assertEqual(argv[argv.index('--max-filesize') + 1], str(len(content)))
            self.assertEqual(kwargs['timeout'], 65)
            self.assertNotIn('capture_output', kwargs)
            self.assertEqual(argv[argv.index('--proto') + 1], '=https')
            target = Path(argv[argv.index('--output') + 1])
            self.assertTrue(target.is_relative_to(self.temp))
            target.write_bytes(content)
        with patch.object(W, 'native_root'), patch.object(W, 'stage_guard'), patch.object(W.subprocess, 'run', side_effect=curl):
            W.fetch('https://files.pythonhosted.org/packages/fixed/a.whl', 'a.whl', len(content), sha256(content).hexdigest())
        self.assertEqual((self.temp / 'a.whl').read_bytes(), content)
        self.assertEqual((self.temp / 'a.whl').stat().st_nlink, 1)

    def test_mocked_oversize_or_corrupt_response_never_publishes(self):
        for body in (b'oversized', b'bad'):
            def curl(argv, **kwargs):
                target = Path(argv[argv.index('--output') + 1])
                self.assertTrue(target.is_relative_to(self.temp))
                target.write_bytes(body)
            with self.subTest(body=body), patch.object(W, 'native_root'), \
                 patch.object(W, 'stage_guard'), \
                 patch.object(W.subprocess, 'run', side_effect=curl), self.assertRaises(RuntimeError):
                W.fetch('https://files.pythonhosted.org/packages/fixed/a.whl', 'a.whl', 3, sha256(b'yes').hexdigest())
            self.assertFalse((self.temp / 'a.whl').exists())
            self.assertEqual(list(self.temp.iterdir()), [])

    def test_mocked_publish_race_does_not_overwrite_existing_wheel(self):
        def curl(argv, **kwargs):
            target = Path(argv[argv.index('--output') + 1])
            self.assertTrue(target.is_relative_to(self.temp))
            target.write_bytes(b'yes')
            self.write('a.whl', b'foreign')
        with patch.object(W, 'native_root'), patch.object(W, 'stage_guard'), patch.object(W.subprocess, 'run', side_effect=curl), self.assertRaises(OSError):
            W.fetch('https://files.pythonhosted.org/packages/fixed/a.whl', 'a.whl', 3, sha256(b'yes').hexdigest())
        self.assertEqual((self.temp / 'a.whl').read_bytes(), b'foreign')


class ControllerFileTests(FencedCase):
    def setUp(self):
        super().setUp()
        self.stack.enter_context(patch.object(C, 'ROOT', self.temp))

    def test_bounded_owned_public_control_read(self):
        self.assertEqual(C.read(self.write('receipt', b'{}')), b'{}')

    def test_foreign_path_is_rejected_before_open(self):
        with self.assertRaisesRegex(RuntimeError, 'foreign_controller_file'):
            C.read(Path(C.KEY))

    def test_oversize_mode_symlink_hardlink_are_refused(self):
        original = self.write('original', b'12345')
        with self.assertRaises(RuntimeError): C.read(original, maximum=4)
        original.chmod(0o644)
        with self.assertRaises(RuntimeError): C.read(original)
        original.chmod(0o600)
        linked = self.temp / 'hardlink'
        os.link(original, linked)
        with self.assertRaises(RuntimeError): C.read(original)
        linked.unlink()
        linked.symlink_to(original)
        with self.assertRaises(OSError): C.read(linked)

    def test_controller_intent_is_single_attempt_exclusive(self):
        C.publish('controller-intent.json', {'attempt': 1})
        with self.assertRaises(FileExistsError): C.publish('controller-intent.json', {'attempt': 2})
        self.assertEqual(json.loads(C.read(self.temp / 'controller-intent.json')), {'attempt': 1})


class PublicSourceContracts(FencedCase):
    @classmethod
    def setUpClass(cls):
        cls.bootstrap = yaml.safe_load((HERE / 'reboot_bootstrap.yml').read_text())[0]
        cls.native = yaml.safe_load((HERE / 'reboot_native.yml').read_text())[0]
        cls.external = yaml.safe_load((HERE / 'reboot_external.yml').read_text())[0]

    def test_public_wheels_exactly_ten_hashlocked_official_artifacts(self):
        wheels = yaml.safe_load((HERE / 'reboot_wheels.yml').read_text())['reboot_wheels']
        requirements = (HERE / 'reboot_requirements.txt').read_text()
        self.assertEqual(len(wheels), 10)
        self.assertEqual(len({w['filename'] for w in wheels}), 10)
        for item in wheels:
            self.assertRegex(item['sha256'], r'^[a-f0-9]{64}$')
            self.assertTrue(0 < item['size'] <= 8 * 1024 * 1024)
            self.assertIn('--hash=sha256:' + item['sha256'], requirements)
            if 'url' in item:
                self.assertTrue(item['url'].startswith('https://files.pythonhosted.org/packages/'))
                self.assertTrue(item['url'].endswith('/' + item['filename']))

    def test_default_bootstrap_is_readonly_before_all_mutations(self):
        self.assertEqual(self.bootstrap['vars']['reboot_bootstrap_action'], 'check')
        tasks = self.bootstrap['tasks']
        boundary = next(i for i, t in enumerate(tasks) if t.get('ansible.builtin.meta') == 'end_play')
        self.assertTrue(all(not any(key in task for key in ('ansible.builtin.copy', 'ansible.builtin.file', 'ansible.builtin.get_url')) for task in tasks[:boundary]))

    def staged_wheel_validator(self):
        task = next(t for t in self.bootstrap['tasks'] if t['name'] == 'Refuse redirected or unsafe native task-wheel stage before any file action')
        argv = task['ansible.builtin.command']['argv']
        wheels = yaml.safe_load((HERE / 'reboot_wheels.yml').read_text())['reboot_wheels']
        environment = Environment(autoescape=False)
        environment.filters['to_json'] = json.dumps
        rendered = environment.from_string(argv[-1]).render(reboot_wheels=wheels)
        # Execute the actual inline validator with a closed metadata-only
        # filesystem fixture. Imports alone are replaced; its checks are intact.
        tree = ast.parse(argv[-2])
        tree.body = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
        program = compile(tree, 'reviewed_stage_validator_fixture', 'exec')
        return wheels, rendered, program

    def execute_staged_wheel_validator(self, program, rendered, entries):
        directory = SimpleNamespace(st_mode=0o040700, st_uid=0)
        parent = SimpleNamespace(lstat=lambda: directory)
        stage = SimpleNamespace(parent=parent, parents=(parent,), lstat=lambda: directory, iterdir=lambda: iter(entries))
        namespace = {'json': json, 'stat': __import__('stat'), 'sys': SimpleNamespace(argv=['validator', rendered]),
                     'os': SimpleNamespace(path=SimpleNamespace(lexists=lambda p: p is stage)),
                     'pathlib': SimpleNamespace(Path=lambda path: stage)}
        exec(program, namespace)

    def test_real_jinja_json_argv_roundtrip_accepts_existing_ten_wheel_stage(self):
        wheels, rendered, program = self.staged_wheel_validator()
        expected = [wheel['filename'] for wheel in wheels]
        self.assertEqual(json.loads(rendered), expected)
        self.assertEqual(len(expected), 10)
        entries = [SimpleNamespace(name=name, lstat=lambda: SimpleNamespace(st_mode=0o100600, st_uid=0, st_nlink=1)) for name in expected]
        self.execute_staged_wheel_validator(program, rendered, entries)

    def test_rendered_stage_validator_still_refuses_unknown_linked_or_unsafe_files(self):
        wheels, rendered, program = self.staged_wheel_validator()
        for name, mode, uid, links in [('../outside.whl', 0o100600, 0, 1),
                                       ('unknown.whl', 0o100600, 0, 1),
                                       (wheels[0]['filename'], 0o120600, 0, 1),
                                       (wheels[0]['filename'], 0o100600, 0, 2),
                                       (wheels[0]['filename'], 0o100644, 0, 1),
                                       (wheels[0]['filename'], 0o100600, 12345, 1)]:
            value = SimpleNamespace(st_mode=mode, st_uid=uid, st_nlink=links)
            entry = SimpleNamespace(name=name, lstat=lambda: value)
            with self.subTest(name=name, mode=mode, uid=uid, links=links), self.assertRaises(AssertionError):
                self.execute_staged_wheel_validator(program, rendered, [entry])

    def test_without_ensurepip_offline_hashlocked_install_and_actual_versions(self):
        raw = (HERE / 'reboot_bootstrap.yml').read_text()
        self.assertIn('--without-pip', raw)
        self.assertNotIn('import datetime,ensurepip', raw)
        for token in ('--no-index', '--no-deps', '--require-hashes', '--no-cache-dir', "m.version('ansible-core')=='2.21.5'"):
            self.assertIn(token, raw)
        self.assertNotIn('get-pip', raw)

    def test_durable_external_controller_no_reboot_restart_loop(self):
        raw = (HERE / 'reboot_bootstrap.yml').read_text()
        self.assertIn('/usr/bin/systemd-run', raw)
        self.assertIn('--property=Restart=no', raw)
        self.assertIn('--property=RuntimeMaxSec=3600', raw)
        self.assertEqual(sum('ansible.builtin.reboot' in t for t in self.external['tasks']), 1)
        reboot = next(t['ansible.builtin.reboot'] for t in self.external['tasks'] if 'ansible.builtin.reboot' in t)
        self.assertIn('reboot_guard.py prepare', reboot['reboot_command'])

    def test_no_env_or_private_key_transfer_or_credential_hash(self):
        raw = '\n'.join((HERE / name).read_text() for name in ('reboot_bootstrap.yml', 'reboot_native.yml', 'reboot_external.yml'))
        self.assertNotIn('lookup(', raw)
        self.assertNotIn('.env', raw)
        self.assertNotIn('fetch:\n        src: /root/.ssh', raw)
        source = (HERE / 'reboot_controller.py').read_text()
        self.assertIn('os.lstat(path)  # Metadata only', source)
        self.assertNotIn('read(KEY', source)
        self.assertNotIn('read_bytes(KEY', source)

    def test_native_only_exact_stop_start_not_compose_recreate_or_enable_static(self):
        raw = (HERE / 'reboot_native.yml').read_text()
        for forbidden in ('docker compose', 'state: restarted', 'state: reloaded', 'pull:', 'build:'):
            self.assertNotIn(forbidden, raw)
        self.assertIn('rejectattr("kind", "equalto", "oneshot")', raw)
        self.assertLess(raw.index('Start exact prior-running datastore'), raw.index('Start remaining exact prior-running containers'))
        self.assertLess(raw.index('release-golden'), raw.index('Start Golden immediately'))

    def test_source_fence_and_real_kernel_proof_not_historic_authority_adoption(self):
        raw = (HERE / 'reboot_guard.py').read_text()
        self.assertIn("for path in (B.MARKER, B.SOURCE)", raw)
        self.assertIn("'active_kernel_not_expected'", raw)
        self.assertIn("'boot_identity_mismatch'", raw)
        self.assertNotIn('os.replace(temporary, B.MARKER)', raw)
        self.assertNotIn('B.MARKER.unlink', raw)

    def test_public_wheel_download_is_bounded_before_first_write_not_posthoc_only(self):
        raw = (HERE / 'reboot_fetch_wheel.py').read_text()
        for token in ('--max-filesize', '--max-time', 'RLIMIT_FSIZE', 'timeout=65', 'renameat2', 'verify(Path(temporary)'):
            self.assertIn(token, raw)
        self.assertNotIn('ansible.builtin.get_url', (HERE / 'reboot_bootstrap.yml').read_text())
        self.assertIn('timeout: 90', (HERE / 'reboot_bootstrap.yml').read_text())


if __name__ == '__main__':
    unittest.main()
