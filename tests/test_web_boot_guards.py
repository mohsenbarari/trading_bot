"""Synthetic controls only. Never contact systemd, Docker, D-Bus or the network."""
from contextlib import contextmanager, ExitStack
from hashlib import sha256
import importlib.util
import errno
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from jinja2 import Environment, StrictUndefined
import yaml

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('_finite_web_boot_test', REPO / 'deploy/maintenance/web_boot_guard.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
PLAY = yaml.safe_load((REPO / 'deploy/maintenance/web_boot_guards.yml').read_text())[0]


class WebBootGuards(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for target in ('subprocess.Popen', 'subprocess.run', 'socket.socket.connect',
                       'socket.create_connection', 'socket.getaddrinfo', 'ctypes.CDLL'):
            self.stack.enter_context(patch(target, side_effect=AssertionError('no native/network calls')))
        self.stack.enter_context(patch.object(M, 'run', side_effect=AssertionError('unmocked host command')))
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.web-boot-fixture-', dir=REPO)))
        self.stack.enter_context(patch.object(M, 'OWNER', os.geteuid()))

    def fixture_entries(self, *, installed):
        systemd, runtime = self.root / 'systemd', self.root / 'runtime'
        systemd.mkdir(mode=0o700)
        runtime.mkdir(mode=0o700)
        self.stack.enter_context(patch.object(M, 'SYSTEMD', systemd))
        self.stack.enter_context(patch.object(M, 'RUNTIME', runtime))
        self.stack.enter_context(patch.object(M, 'TOKEN', self.root / 'authority-absent'))
        rows = {}
        properties = {}
        for unit, text in M.desired().items():
            target = systemd / (unit + '.d') / M.NAME
            target.parent.mkdir(mode=0o700)
            loaded = []
            if installed:
                target.write_text(text)
                target.chmod(0o644)
                loaded.append(str(target))
            if unit in M.COLLECTORS:
                guard = runtime / (unit + '.d') / 'market-container-authority.conf'
                guard.parent.mkdir(mode=0o700)
                guard.write_text(M.BODY)
                guard.chmod(0o644)
                loaded.append(str(guard))
            rows[unit] = {'LoadState': 'loaded', 'NeedDaemonReload': 'no',
                          'UnitFileState': 'disabled' if unit in M.COLLECTORS else 'enabled',
                          'ActiveState': 'inactive' if unit in M.COLLECTORS else 'active',
                          'What': '/dev/disk/by-uuid/' + M.UUID, 'RequiresMountsFor': M.PRIMARY}
            properties[unit] = {'DropInPaths': loaded, 'RefuseManualStart': 1,
                                'Conditions': [('ConditionPathExists', False, False, str(M.TOKEN), 0)],
                                'Asserts': [('AssertPathIsMountPoint', False, False, M.PRIMARY, 1)]}
        self.stack.enter_context(patch.object(M, 'props', side_effect=lambda unit: rows[unit]))
        self.stack.enter_context(patch.object(M, 'property_value', side_effect=lambda bus, unit, name: properties[unit][name]))
        return rows, properties

    def test_four_literal_guards_preserve_same_inhibition_and_only_real_primary_mount(self):
        desired = M.desired()
        self.assertEqual(set(desired), {*M.COLLECTORS, 'docker.service', M.MOUNT})
        self.assertEqual(M.MOUNT, r'srv-trading\x2dbot\x2dthree\x2dsite\x2dstaging\x2ddata.mount')
        self.assertEqual(desired[M.COLLECTORS[0]], M.HEADER + M.BODY)
        self.assertEqual(desired[M.COLLECTORS[1]], M.HEADER + M.BODY)
        self.assertIn('What=/dev/disk/by-uuid/' + M.UUID + '\n', desired[M.MOUNT])
        self.assertIn('RequiresMountsFor=' + M.PRIMARY + '\n', desired['docker.service'])
        self.assertNotIn('/var/lib/docker/volumes', desired['docker.service'])
        self.assertNotIn('Restart=', ''.join(desired.values()))

    def test_entries_refuse_false_dbus_boolean_and_missing_conditions(self):
        rows, properties = self.fixture_entries(installed=True)
        self.assertEqual(len(M.entries('setup', True, object())), 4)
        properties[M.COLLECTORS[0]]['RefuseManualStart'] = 0
        with self.assertRaisesRegex(RuntimeError, 'manual_start_unfenced'):
            M.entries('setup', True, object())
        properties[M.COLLECTORS[0]]['RefuseManualStart'] = 1
        properties[M.COLLECTORS[0]]['Conditions'] = []
        with self.assertRaisesRegex(RuntimeError, 'condition_not_loaded'):
            M.entries('setup', True, object())

    def test_foreign_owned_filename_and_authority_token_refuse(self):
        self.fixture_entries(installed=False)
        target = M.SYSTEMD / (M.COLLECTORS[0] + '.d') / M.NAME
        target.write_text('foreign content')
        target.chmod(0o644)
        with self.assertRaisesRegex(RuntimeError, 'foreign_owned_filename'):
            M.entries('setup', False, object())
        target.unlink()
        M.TOKEN.write_text('synthetic authority')
        with self.assertRaisesRegex(RuntimeError, 'legacy_authority_token_present'):
            M.entries('setup', False, object())

    def test_cleanup_cannot_remove_only_remaining_persistent_inhibitor_after_reboot(self):
        self.fixture_entries(installed=True)
        runtime = M.RUNTIME / (M.COLLECTORS[0] + '.d') / 'market-container-authority.conf'
        runtime.unlink()
        with self.assertRaisesRegex(RuntimeError, 'runtime_authority_missing'):
            M.entries('cleanup', False, object())

    def test_uuid_override_must_really_be_loaded(self):
        rows, _ = self.fixture_entries(installed=True)
        rows[M.MOUNT]['What'] = '/dev/wrong'
        with self.assertRaisesRegex(RuntimeError, 'uuid_override_not_loaded'):
            M.entries('setup', True, object())

    def test_primary_uses_unescaped_dbus_mount_dropins_and_same_verified_uuid_device(self):
        mounted = {'target': M.PRIMARY, 'uuid': M.UUID, 'fstype': 'ext4', 'options': 'rw,relatime', 'source': '/dev/sdb'}
        unit = {'LoadState': 'loaded', 'UnitFileState': 'enabled', 'ActiveState': 'active',
                'Where': M.PRIMARY, 'Type': 'ext4', 'FragmentPath': str(M.SYSTEMD / M.MOUNT),
                'DropInPaths': 'must-not-parse-escaped-systemctl-text', 'What': '/dev/disk/by-uuid/' + M.UUID}
        dropin = str(M.SYSTEMD / (M.MOUNT + '.d') / M.NAME)
        public = ('[Mount]\nWhat=/dev/sdb\nWhere=' + M.PRIMARY + '\nType=ext4\n').encode()
        with patch.object(M, 'metadata', return_value=SimpleNamespace(st_dev=2064, st_ino=2)), \
             patch.object(M, 'source_mount', return_value=mounted), patch.object(M, 'props', return_value=unit), \
             patch.object(M, 'property_value', return_value=[dropin]), patch.object(M, 'read', return_value=public), \
             patch.object(M.Path, 'resolve', return_value=Path('/dev/sdb')):
            self.assertEqual(M.primary(object())['inode'], 2)
            mounted['uuid'] = M.ROOT_UUID
            with self.assertRaisesRegex(RuntimeError, 'wrong_primary'):
                M.primary(object())

    def test_current_marker_flock_is_held_and_bytes_remain_unchanged(self):
        marker = self.root / 'marker'
        marker.write_bytes(b'unchanged original authority')
        marker.chmod(0o600)
        with patch.object(M, 'MARKER', marker):
            with M.locked_marker():
                fd = os.open(marker, os.O_RDONLY)
                try:
                    with self.assertRaises(BlockingIOError):
                        M.fcntl.flock(fd, M.fcntl.LOCK_EX | M.fcntl.LOCK_NB)
                finally:
                    os.close(fd)
        self.assertEqual(marker.read_bytes(), b'unchanged original authority')

    def test_exact_publish_never_overwrites_foreign_or_prior_content(self):
        def rename(_at1, source, _at2, target, flags):
            self.assertEqual(flags, 1)
            source, target = Path(os.fsdecode(source)), Path(os.fsdecode(target))
            self.assertTrue(source.is_relative_to(self.root))
            self.assertTrue(target.is_relative_to(self.root))
            try:
                os.link(source, target)
            except FileExistsError:
                M.ctypes.set_errno(errno.EEXIST)
                return -1
            source.unlink()
            return 0
        with patch.object(M.ctypes, 'CDLL', return_value=SimpleNamespace(renameat2=rename)):
            path = self.root / 'owned.d' / M.NAME
            M.publish(path, 'owned first\n')
            self.assertEqual(path.read_bytes(), b'owned first\n')
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)
            with self.assertRaises(OSError):
                M.publish(path, 'never overwrite\n')
            self.assertEqual(path.read_bytes(), b'owned first\n')
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_real_baseline_comparison_refuses_changed_image_mount_or_running_identity(self):
        mounts = [{'Destination': '/app/data', 'Mode': 'rw', 'Propagation': 'rprivate',
                   'RW': True, 'Source': M.PRIMARY + '/data', 'Type': 'bind'}]
        rows = [{'id': f'{index:064x}', 'name': '/fixture-' + str(index), 'image': 'sha256:' + 'a' * 64,
                 'running': index < 26, 'restart_policy': {'Name': 'unless-stopped', 'MaximumRetryCount': 0},
                 'mounts': mounts, 'network_names': ['bridge'], 'network_mode': 'bridge', 'port_bindings': {}, 'health': None} for index in range(27)]
        unit_bytes = b'public unchanged unit'
        units = [{'name': unit, 'enabled': 'disabled' if unit in M.COLLECTORS else 'enabled',
                  'active': unit.endswith('.timer'), 'kind': 'timer' if unit.endswith('.timer') else 'oneshot' if unit not in M.COLLECTORS else 'daemon',
                  'fragment_path': '/public/' + unit, 'fragment_sha256': sha256(unit_bytes).hexdigest(), 'dropins': []}
                 for unit in M.CONSUMERS]
        plan = {'containers': rows, 'units': units}
        mutation = {}
        def command(argv):
            if argv[0] == '/usr/bin/ctr': return 'Server:\n  Version: v2.4.2'
            if argv[0] == '/usr/bin/runc': return 'runc version 1.5.2'
            self.assertEqual(argv[:2], M.DOCKER)
            if argv[2] == 'version': return '29.9.2'
            if argv[2] == 'ps': return '\n'.join(row['id'] for row in rows)
            self.assertEqual(argv[2], 'inspect')
            row = dict(next(row for row in rows if row['id'] == argv[-1]))
            row['network_names'] = {'bridge': {}}
            if row['id'] == rows[0]['id']: row.update(mutation)
            return json.dumps(row)
        def properties(unit):
            row = next(row for row in units if row['name'] == unit)
            return {'LoadState': 'loaded', 'UnitFileState': row['enabled'], 'FragmentPath': row['fragment_path'],
                    'ActiveState': 'active' if row['active'] else 'inactive', 'WorkingDirectory': '/',
                    'ReadOnlyPaths': '', 'ReadWritePaths': ''}
        def mounted(path):
            return {'target': '/' if path == '/' else M.PRIMARY, 'uuid': M.ROOT_UUID if path == '/' else M.UUID, 'fstype': 'ext4'}
        with patch.object(M, 'run', side_effect=command), patch.object(M, 'props', side_effect=properties), \
             patch.object(M, 'read', return_value=unit_bytes), patch.object(M, 'source_mount', side_effect=mounted), \
             patch.object(M, 'property_value', return_value=[]), patch.object(M.os.path, 'lexists', return_value=False):
            self.assertEqual(M.runtime(plan, object())['containers'], 27)
            for changed in ({'image': 'sha256:' + 'b' * 64}, {'running': False}, {'mounts': [{**mounts[0], 'RW': False}]},
                            {'port_bindings': {'80/tcp': []}}, {'network_mode': 'host'}):
                mutation.clear()
                mutation.update(changed)
                with self.subTest(changed=changed), self.assertRaisesRegex(RuntimeError, 'container_baseline_changed'):
                    M.runtime(plan, object())

    def orchestration(self, action, unchanged=False):
        held = set()
        events = []
        @contextmanager
        def hold(name, value):
            held.add(name)
            try:
                yield value
            finally:
                held.remove(name)
        def assert_held(*args, **kwargs):
            self.assertEqual(held, {'marker', 'bus'})
        targets = [{'destination': str(self.root / ('fixed-' + str(i))), 'content': 'owned\n',
                    'exists': action == 'setup' if unchanged else action == 'cleanup'} for i in range(4)]
        def completed(*args):
            assert_held()
            return {'synthetic': 'plan'}
        def publish(path, text):
            assert_held()
            events.append(('publish', str(path)))
        def command(argv):
            assert_held()
            self.assertEqual(argv, ['/usr/bin/systemctl', 'daemon-reload'])
            events.append(('reload',))
            return ''
        with patch.object(M, 'native'), patch.object(M, 'locked_marker', side_effect=lambda: hold('marker', 1)), \
             patch.object(M, 'pinned', side_effect=lambda: hold('bus', object())), patch.object(M, 'completed', side_effect=completed), \
             patch.object(M, 'primary', return_value={'inode': 1}), patch.object(M, 'runtime', return_value={'containers': 27}), \
             patch.object(M, 'entries', return_value=targets) as entries, patch.object(M, 'publish', side_effect=publish), \
             patch.object(M, 'run', side_effect=command), patch.object(M, 'read', return_value=b'owned\n'), \
             patch.object(M.Path, 'unlink', side_effect=lambda: events.append(('unlink',))), patch.object(M, 'sync_parent'):
            result = M.execute(action, 'a' * 64)
            if action == 'check':
                self.assertEqual(events, [])
                self.assertEqual(entries.call_count, 1)
            else:
                if unchanged:
                    self.assertEqual(events, [])
                    self.assertFalse(result['configuration_changed'])
                else:
                    self.assertEqual(events[-1], ('reload',))
                    self.assertEqual(len(events), 5)
                self.assertEqual(entries.call_args.args[:2], (action, True))
        self.assertEqual(held, set())
        self.assertFalse(result['runtime_changed'])
        return events

    def test_default_never_writes_reloads_or_restarts(self):
        self.orchestration('check')

    def test_already_installed_setup_verifies_without_rewrite_or_reload(self):
        self.orchestration('setup', unchanged=True)

    def test_setup_holds_original_flock_and_dbus_through_all_four_writes_and_postcheck(self):
        events = self.orchestration('setup')
        self.assertEqual([event[0] for event in events], ['publish'] * 4 + ['reload'])

    def test_cleanup_unlinks_only_validated_owned_exact_targets_under_same_lock(self):
        events = self.orchestration('cleanup')
        self.assertEqual([event[0] for event in events], ['unlink'] * 4 + ['reload'])

    def test_pending_maintenance_refuses_before_reading_completed_plan(self):
        with patch.object(M.os.path, 'lexists', return_value=True), patch.object(M, 'read') as read:
            with self.assertRaisesRegex(RuntimeError, 'maintenance_active'):
                M.completed('a' * 64)
            read.assert_not_called()

    def test_exact_completed07_phase_nonce_plan_and_controller_are_required(self):
        marker = b'unchanged public authority'
        controller = b'reviewed public controller'
        plan = {'schema': 'web_engine_transaction_plan/1', 'operation_id': M.OP, 'nonce': M.NONCE,
                'files': [{'path': str(M.WORK / 'source/deploy/maintenance/web_engine_controller.py'), 'sha256': sha256(controller).hexdigest()}],
                'marker': {'path': str(M.MARKER), 'device': 1, 'inode': 2,
                           'size': len(marker), 'sha256': sha256(marker).hexdigest()}}
        raw = json.dumps(plan).encode()
        digest = sha256(raw).hexdigest()
        receipt = {'schema': 'web_engine_transaction/1', 'operation_id': M.OP, 'nonce': M.NONCE,
                   'manifest_sha256': digest, 'phase': 'complete'}
        allowed = {M.WORK / 'plan.json': raw, M.MARKER: marker,
                   M.WORK / 'source/deploy/maintenance/web_engine_controller.py': controller}
        def fetch(path, *args):
            if path == M.CONTROL / (M.OP + '.journal.json'):
                return json.dumps(receipt).encode()
            return allowed[path]
        with patch.object(M.os.path, 'lexists', return_value=False), patch.object(M, 'read', side_effect=fetch), \
             patch.object(M, 'metadata', return_value=SimpleNamespace(st_dev=1, st_ino=2)), patch.object(M, 'C_SHA', sha256(controller).hexdigest()):
            self.assertEqual(M.completed(digest), plan)
            for key, wrong in (('phase', 'aborted_restored'), ('nonce', '0' * 32), ('manifest_sha256', '0' * 64)):
                saved = receipt[key]
                receipt[key] = wrong
                with self.subTest(field=key), self.assertRaisesRegex(RuntimeError, 'ce_not_complete'):
                    M.completed(digest)
                receipt[key] = saved

    def test_check_mode_cannot_mutate_and_helper_has_no_sealed_controller_run(self):
        self.assertEqual(PLAY['vars']['web_boot_action'], 'check')
        task = PLAY['tasks'][1]
        env = Environment(undefined=StrictUndefined)
        action = task['ansible.builtin.command']['argv'][6]
        for check_mode, requested, expected in ((True, 'setup', 'check'), (True, 'cleanup', 'check'), (False, 'setup', 'setup')):
            self.assertEqual(env.from_string(action).render(ansible_check_mode=check_mode, web_boot_action=requested), expected)
        source = (REPO / 'deploy/maintenance/web_boot_guard.py').read_text()
        for forbidden in ('C.run(', 'owned=True', 'StartUnit(', 'reboot(', "'enable'", "'restart'", "'remount'"):
            self.assertNotIn(forbidden, source)
        self.assertIn('manager.RefUnit(unit', source)
        self.assertIn('manager.UnrefUnit(unit', source)
        self.assertIn('manager.GetUnit(unit', source)


if __name__ == '__main__':
    unittest.main()
