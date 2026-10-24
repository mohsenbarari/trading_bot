"""Synthetic controls only. Never contact systemd, Docker, D-Bus or the network."""
from contextlib import contextmanager, ExitStack
from hashlib import sha256
import ast
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

AMBIENT = ExitStack()
for target in ('subprocess.Popen', 'subprocess.run', 'subprocess.check_output', 'os.system',
               'socket.socket.connect', 'socket.create_connection', 'socket.getaddrinfo',
               'ctypes.CDLL', 'os.kill', 'os.killpg'):
    AMBIENT.enter_context(patch(target, side_effect=AssertionError('ambient_host_or_network_forbidden')))
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
                 'project': 'current' if index < 17 else 'three-site-dc32d90392b0-webapp-fi',
                 'restarting': False, 'running': index < 16,
                 'restart_policy': {'Name': 'unless-stopped' if index < 17 else 'no', 'MaximumRetryCount': 0},
                 'mounts': mounts, 'network_names': ['bridge'], 'network_mode': 'bridge', 'port_bindings': {}, 'health': None} for index in range(27)]
        unit_bytes = b'public unchanged unit'
        units = [{'name': unit, 'enabled': 'disabled' if unit in M.COLLECTORS else 'enabled',
                  'active': unit.endswith('.timer'), 'kind': 'timer' if unit.endswith('.timer') else 'oneshot' if unit not in M.COLLECTORS else 'daemon',
                  'fragment_path': '/public/' + unit, 'fragment_sha256': sha256(unit_bytes).hexdigest(), 'dropins': []}
                 for unit in M.CONSUMERS]
        plan = {'containers': rows, 'units': units,
                'current_profile': {'containers': rows, 'parked_ids': [r['id'] for r in rows[17:]],
                    'known_health_incidents': {'ids': [r['id'] for r in rows[:2]],
                                               'status': 'open', 'acceptance_waived': False}}}
        mutation = {}
        mutated_id = [rows[0]['id']]
        def command(argv):
            if argv[0] == '/usr/bin/ctr': return 'Server:\n  Version: v2.4.2'
            if argv[0] == '/usr/bin/runc': return 'runc version 1.5.2'
            self.assertEqual(argv[:2], M.DOCKER)
            if argv[2] == 'version': return '29.9.2'
            if argv[2] == 'ps': return '\n'.join(row['id'] for row in rows)
            self.assertEqual(argv[2], 'inspect')
            row = dict(next(row for row in rows if row['id'] == argv[-1]))
            row['network_names'] = {'bridge': {}}
            if row['id'] == mutated_id[0]: row.update(mutation)
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
             patch.object(M, 'property_value', return_value=[]), patch.object(M.os.path, 'lexists', return_value=False), \
             patch.object(M, 'parked_storage'):
            self.assertEqual(M.runtime(plan, object())['containers'], 27)
            for changed in ({'image': 'sha256:' + 'b' * 64}, {'running': False}, {'running': 1},
                            {'restarting': True}, {'project': 'wrong-owner'}, {'mounts': [{**mounts[0], 'RW': False}]},
                            {'port_bindings': {'80/tcp': []}}, {'network_mode': 'host'}, {'network_names': {'changed-dns-network': {}}}):
                mutation.clear()
                mutation.update(changed)
                with self.subTest(changed=changed), self.assertRaisesRegex(RuntimeError, 'container_baseline_changed'):
                    M.runtime(plan, object())
            mutation.clear(); mutated_id[0] = rows[-1]['id']
            for changed in ({'running': True}, {'restart_policy': {'Name': 'always', 'MaximumRetryCount': 0}}):
                mutation.clear(); mutation.update(changed)
                with self.subTest(qa_changed=changed), self.assertRaisesRegex(RuntimeError, 'container_baseline_changed'):
                    M.runtime(plan, object())
            mutation.clear(); mutated_id[0] = rows[0]['id']; mutation['health'] = 'unhealthy'
            closure = M.runtime(plan, object())
            self.assertEqual(closure['observed_known_health_incidents'], [rows[0]['id']])
            self.assertEqual(closure['known_health_incidents_status'], 'open')
            self.assertFalse(closure['health_incident_acceptance_waived'])
            mutated_id[0] = rows[2]['id']
            with self.assertRaisesRegex(RuntimeError, 'container_unhealthy'):
                M.runtime(plan, object())

    def test_parked_qa_backing_directories_and_all_four_volume_records_remain_exact(self):
        primary = self.root/'attached'; primary.mkdir(mode=0o700)
        def snapshot(path):
            info = path.stat()
            return {'path': str(path), 'device': info.st_dev, 'inode': info.st_ino, 'uid': info.st_uid,
                    'gid': info.st_gid, 'mode': info.st_mode & 0o7777}
        volumes = {}
        for name in ('audit', 'postgres', 'redis', 'uploads'):
            path = primary/name; path.mkdir(mode=0o700)
            record = {'name': name, 'driver': 'local', 'mountpoint': '/var/lib/docker/volumes/'+name+'/_data',
                      'options': {'device': str(path), 'o': 'bind', 'type': 'none'}, 'labels': {}}
            volumes[name] = {'backing_identity': snapshot(path), 'record': record}
        public_metadata_file = primary/'ordinary-metadata-only.fixture'
        public_metadata_file.write_bytes(b'public fixture contents are never read by the guard')
        info = public_metadata_file.lstat()
        file_metadata = [info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
                         info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns]
        resources = {'parent_mount': {**snapshot(primary), 'uuid': M.UUID}, 'volumes': volumes,
                     'secret_metadata': {str(public_metadata_file): file_metadata}}
        plan = {'current_profile': {'parked_resources': resources}}
        changed_volume = {}
        mounted = {'target': str(primary), 'uuid': M.UUID, 'fstype': 'ext4'}
        def command(argv):
            self.assertEqual(argv[:4], M.DOCKER+['volume', 'inspect'])
            record = dict(volumes[argv[-1]]['record'])
            if argv[-1] == 'postgres': record.update(changed_volume)
            return json.dumps(record)
        with patch.object(M, 'PRIMARY', str(primary)), patch.object(M, 'run', side_effect=command), \
             patch.object(M, 'source_mount', return_value=mounted), \
             patch.object(M, 'read', side_effect=AssertionError('no private file contents')):
            M.parked_storage(plan)
            changed_volume['options'] = {'device': '/wrong/backing', 'o': 'bind', 'type': 'none'}
            with self.assertRaisesRegex(RuntimeError, 'parked_volume_changed'):
                M.parked_storage(plan)
            changed_volume.clear(); mounted['uuid'] = M.ROOT_UUID
            with self.assertRaisesRegex(RuntimeError, 'parked_backing_mount_changed'):
                M.parked_storage(plan)
            mounted['uuid'] = M.UUID
            public_metadata_file.chmod((public_metadata_file.stat().st_mode & 0o777) ^ 0o040)
            with self.assertRaisesRegex(RuntimeError, 'parked_file_metadata_changed'):
                M.parked_storage(plan)
            # Restore the exact recorded metadata snapshot for the independent
            # backing-directory identity negative case below.
            info = public_metadata_file.lstat()
            resources['secret_metadata'][str(public_metadata_file)] = [info.st_dev, info.st_ino, info.st_mode,
                info.st_uid, info.st_gid, info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns]
            path = primary/'uploads'; path.rmdir(); path.mkdir(mode=0o755)
            with self.assertRaisesRegex(RuntimeError, 'parked_backing_changed'):
                M.parked_storage(plan)

    def orchestration(self, action, unchanged=False, runtime_observations=None):
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
             patch.object(M, 'primary', return_value={'inode': 1}), \
             patch.object(M, 'runtime', return_value={'containers': 27}, side_effect=runtime_observations), \
             patch.object(M, 'entries', return_value=targets) as entries, patch.object(M, 'publish', side_effect=publish), \
             patch.object(M, 'run', side_effect=command), patch.object(M, 'read', return_value=b'owned\n'), \
             patch.object(M.Path, 'unlink', side_effect=lambda: events.append(('unlink',))), patch.object(M, 'sync_parent'):
            result = M.execute(action, 'a' * 64, 'b' * 64, 'c' * 64, profile='current08')
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

    def test_prepost_known_health_observations_may_change_but_inventory_cannot(self):
        events = self.orchestration('setup', runtime_observations=[
            {'containers': 27, 'observed_known_health_incidents': []},
            {'containers': 27, 'observed_known_health_incidents': ['already-sealed-known-id']}])
        self.assertEqual([event[0] for event in events], ['publish'] * 4 + ['reload'])
        with self.assertRaisesRegex(RuntimeError, 'runtime_closure_changed'):
            self.orchestration('setup', runtime_observations=[
                {'containers': 27, 'observed_known_health_incidents': []},
                {'containers': 28, 'observed_known_health_incidents': []}])

    def test_pending_maintenance_refuses_before_reading_completed_plan(self):
        with patch.object(M.os.path, 'lexists', return_value=True), patch.object(M, 'read') as read:
            with self.assertRaisesRegex(RuntimeError, 'maintenance_active'):
                M.completed('a' * 64, 'b' * 64, 'c' * 64)
            read.assert_not_called()

    def completed_fixture(self):
        marker = b'unchanged public authority'
        controller = b'reviewed public controller'
        profile = {'configuration_source': {'release_sha': 'f'*40, 'release_tree': 'd'*40},
                   'source_image': {'image_id': 'sha256:'+'e'*64, 'image_input_signature': '6'*64}}
        window = {'schema': 'web_engine_current_window/1', 'operation_id': M.OP, 'nonce': M.NONCE,
                  'reboot_allowed': False, 'deadline_utc': '2026-10-24T22:29:08Z',
                  'approved_sources_sha256': {'deploy/maintenance/web_engine_current_controller.py': sha256(controller).hexdigest(),
                                              'deploy/maintenance/web_engine_current_profile.py': M.PROFILE_SHA}}
        window_raw = json.dumps(window).encode()
        plan = {'schema': 'web_engine_current_plan/1', 'operation_id': M.OP, 'nonce': M.NONCE,
                'source_root': str(M.WORK/'source'), 'window_sha256': sha256(window_raw).hexdigest(),
                'marker': {'path': str(M.MARKER), 'device': 1, 'inode': 2,
                           'size': len(marker), 'sha256': sha256(marker).hexdigest()}}
        raw = json.dumps(plan).encode()
        digest = sha256(raw).hexdigest()
        fresh = {'schema': 'web_engine_fresh_quiesced_operand/1', 'operation_id': M.OP, 'nonce': M.NONCE,
                 'backup_mode': 'quiesced', 'restore_status': 'PASS', 'cleanup_status': 'PASS',
                 **profile['configuration_source'], **profile['source_image']}
        receipt = {'schema': 'web_engine_current_transaction/1', 'operation_id': M.OP, 'nonce': M.NONCE,
                   'manifest_sha256': digest, 'phase': 'complete', 'backup_requests_started': True,
                   'backup_requests_complete': True, 'cold_archive_compared': True, 'fresh_dump_in_cold_archive': True,
                   'security_accepted': False, 'deployment_eligible': False, 'reboot_performed': False,
                   'fresh_quiesced_backup': fresh,
                   'offhost': {'status': 'verified', 'operation_id': M.OP, 'nonce': M.NONCE,
                               'manifest_sha256': digest, 'fsync_completed': True, 'plaintext_materialized_offhost': False}}
        cleanup = {'schema': 'web_engine_current_owned_control/1', 'control_name': 'watch-cleanup',
                   'operation_id': M.OP, 'nonce': M.NONCE, 'manifest_sha256': digest,
                   'all_owned_resources_absent': True, 'worker_succeeded': True, 'marker_retained': True}
        allowed = {M.WORK/'plan.json': raw, M.MARKER: marker, M.WORK/'window.json': window_raw,
                   M.WORK/'source/deploy/maintenance/web_engine_current_controller.py': controller}
        cleanup_path = Path('/srv/trading-bot')/(M.OP+'-'+M.NONCE)/'watch-cleanup.json'
        def fetch(path, *args):
            if path == M.CONTROL / (M.OP + '.journal.json'):
                return json.dumps(receipt).encode()
            if path == cleanup_path:
                return json.dumps(cleanup).encode()
            return allowed[path]
        self.stack.enter_context(patch.object(M.os.path, 'lexists', return_value=False))
        self.stack.enter_context(patch.object(M, 'read', side_effect=fetch))
        self.stack.enter_context(patch.object(M, 'metadata', return_value=SimpleNamespace(st_dev=1, st_ino=2)))
        self.stack.enter_context(patch.object(M, 'C_SHA', sha256(controller).hexdigest()))
        self.stack.enter_context(patch.object(M, 'current_profile', return_value=profile))
        def verify():
            return M.completed(digest, sha256(json.dumps(receipt).encode()).hexdigest(),
                               sha256(json.dumps(cleanup).encode()).hexdigest())
        return plan, receipt, cleanup, allowed, verify

    def test_exact_completed08_phase_nonce_plan_and_controller_are_required(self):
        plan, receipt, cleanup, allowed, verify = self.completed_fixture()
        self.assertEqual(verify(), plan)
        for key, wrong in (('phase', 'aborted_restored'), ('nonce', '0' * 32), ('manifest_sha256', '0' * 64),
                           ('schema', 'web_engine_transaction/1'), ('backup_requests_complete', 1), ('reboot_performed', 0)):
            saved = receipt[key]
            receipt[key] = wrong
            with self.subTest(field=key), self.assertRaisesRegex(RuntimeError, 'ce_not_complete'):
                verify()
            receipt[key] = saved

    def test_final_journal_and_watcher_hashes_are_immutable_even_with_same_complete_phase(self):
        plan, receipt, cleanup, allowed, verify = self.completed_fixture()
        digest = sha256(allowed[M.WORK/'plan.json']).hexdigest()
        journal_pin = sha256(json.dumps(receipt).encode()).hexdigest()
        cleanup_pin = sha256(json.dumps(cleanup).encode()).hexdigest()
        receipt['new_unrelated_field'] = 'same_phase_but_changed_bytes'
        with self.assertRaisesRegex(RuntimeError, 'completed_control_changed'):
            M.completed(digest, journal_pin, cleanup_pin)
        receipt.pop('new_unrelated_field')
        cleanup['worker_succeeded'] = False
        with self.assertRaisesRegex(RuntimeError, 'completed_control_changed'):
            M.completed(digest, journal_pin, cleanup_pin)
        with self.assertRaisesRegex(RuntimeError, 'completed_cleanup_missing'):
            verify()

    def test_old_c07_plan_or_wrong_current_controller_cannot_be_adopted(self):
        plan, receipt, cleanup, allowed, verify = self.completed_fixture()
        allowed[M.WORK/'source/deploy/maintenance/web_engine_current_controller.py'] += b' changed'
        with self.assertRaisesRegex(RuntimeError, 'completed_controller_changed'):
            verify()
        allowed[M.WORK/'source/deploy/maintenance/web_engine_current_controller.py'] = b'reviewed public controller'
        plan.update(schema='web_engine_transaction_plan/1', operation_id='security-web-engine-20261023-07')
        raw = json.dumps(plan).encode(); allowed[M.WORK/'plan.json'] = raw
        with self.assertRaisesRegex(RuntimeError, 'wrong_completed_plan'):
            M.completed(sha256(raw).hexdigest(), 'b'*64, 'c'*64)

    def test_current_profile_is_sealed_before_any_public_source_compilation(self):
        with patch.object(M, 'read', return_value=b'unsealed public source'), patch.object(M, 'ModuleType') as module:
            with self.assertRaisesRegex(RuntimeError, 'completed_profile_source_changed'):
                M.current_profile({})
            module.assert_not_called()

    def test_real_profile_derivation_binds_all14_controls_and_exact_current_intent(self):
        fixture_spec = importlib.util.spec_from_file_location('_boot_profile_data', REPO/'tests/test_web_engine_current_profile.py')
        fixture_module = importlib.util.module_from_spec(fixture_spec); fixture_spec.loader.exec_module(fixture_module)
        docs = fixture_module.CurrentProfileTests().fixture()
        # The public fixture omits unrelated unit/marker metadata; add its
        # sanitized fields and recompute the same original-plan digest links.
        old = docs['c07_plan']
        old.update(marker={'path': '/public/permanent.lock'}, units=[], engine_intent=[])
        old_digest = sha256(fixture_module.raw(old)).hexdigest()
        for name in ('qa_parking', 'capture06', 'closure'):
            docs[name]['original_plan_sha256'] = old_digest
        parking_digest = sha256(fixture_module.raw(docs['qa_parking'])).hexdigest()
        docs['capture06']['parking_audit_sha256'] = parking_digest
        docs['closure']['qa_parking_audit_sha256'] = parking_digest
        docs['closure']['accepted_capture06_audit_sha256'] = sha256(fixture_module.raw(docs['capture06'])).hexdigest()
        buffers = {name: fixture_module.raw(value) for name, value in docs.items()}
        pins = {name: sha256(value).hexdigest() for name, value in buffers.items()}
        with patch.object(fixture_module.M, 'CONTROL_SHA256', pins):
            profile = fixture_module.M.derive_current_profile(buffers)
        source_path = REPO/'deploy/maintenance/web_engine_current_profile.py'
        original_source = source_path.read_text()
        # Only test fixture pins change: execute the reviewed real derivation,
        # including all closure/parking/capture/HOT validation, in this test.
        tree = ast.parse(original_source)
        assignment = next(n for n in tree.body if isinstance(n, ast.Assign)
                          and any(isinstance(t, ast.Name) and t.id == 'CONTROL_SHA256' for t in n.targets))
        lines = original_source.splitlines(keepends=True)
        lines[assignment.lineno-1:assignment.end_lineno] = ['CONTROL_SHA256 = '+repr(pins)+'\n']
        sealed = ''.join(lines).encode()
        plan = {'current_profile': profile, 'containers': profile['containers'],
                **{key: old[key] for key in ('marker', 'units', 'engine_intent', 'store_roots')}}
        reads = []
        def fetch(path, *args):
            reads.append(path)
            if path == M.WORK/'source/deploy/maintenance/web_engine_current_profile.py':
                return sealed
            return buffers[path.stem]
        with patch.object(M, 'PROFILE_SHA', sha256(sealed).hexdigest()), patch.object(M, 'read', side_effect=fetch):
            self.assertEqual(M.current_profile(plan), profile)
            self.assertEqual({p.stem for p in reads[1:]}, set(pins))
            plan['containers'] = [dict(row) for row in profile['containers']]
            parked = next(r for r in plan['containers'] if r['id'] in profile['parked_ids'])
            parked['running'] = True
            with self.assertRaisesRegex(RuntimeError, 'completed_current_profile_changed'):
                M.current_profile(plan)
            plan['containers'] = profile['containers']
            buffers['closure'] += b' '
            with self.assertRaisesRegex(RuntimeError, 'profile_control_digest'):
                M.current_profile(plan)

    def test_explicit_current08_profile_rejects_historical_or_native_scope_before_host_calls(self):
        with patch.object(M, 'native') as native:
            for profile in ('c07', 'native15', '', None):
                with self.subTest(profile=profile), self.assertRaisesRegex(RuntimeError, 'explicit_current_profile_required'):
                    M.execute('setup', 'a'*64, 'b'*64, 'c'*64, profile=profile)
            native.assert_not_called()

    def test_readonly_preflight_has_no_engine_window_or_reboot_authority(self):
        events = self.orchestration('check')
        self.assertEqual(events, [])
        self.assertNotEqual(M.CONFIRMATION, 'WEB-BOOT-GUARDS-20261024')
        self.assertEqual(M.OP, 'security-web-engine-20261024-08')
        self.assertEqual(M.NONCE, 'a872a41985d8f931cd856f50068c75f2')

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
