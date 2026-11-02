"""Parking-only fixtures: no real Docker, native operation or secret reads."""
from contextlib import ExitStack, nullcontext
from datetime import datetime, timezone, timedelta
from hashlib import sha256
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tests.test_web_qa_retirement import plan_fixture, M as Q

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('_web_qa_parking_test', ROOT / 'deploy/maintenance/web_qa_parking.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class QaParking(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for target in ('subprocess.run', 'subprocess.Popen', 'os.system', 'socket.socket.connect',
                       'socket.create_connection', 'socket.getaddrinfo', 'urllib.request.urlopen'):
            self.stack.enter_context(patch(target, side_effect=AssertionError('hermetic only')))
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.qa-parking-test-', dir=ROOT)))

    def test_reviewed_primitives_are_exact_buffers_without_old_main_execution(self):
        for filename, expected in (('web_qa_retirement.py', M.QA_SOURCE_SHA), ('web_market_hot_preflight.py', M.HOT_SOURCE_SHA)):
            raw = (ROOT / 'deploy/maintenance' / filename).read_text()
            self.assertEqual(sha256(raw.encode()).hexdigest(), expected)
            loaded = M.reviewed(raw, expected, 'fixture_' + filename[:-3])
            self.assertTrue(callable(loaded.main))
            with self.assertRaisesRegex(RuntimeError, 'reviewed_parking_source_changed'):
                M.reviewed(raw + '\n', expected, 'changed')

    def fixture(self, *, real_resources=False):
        marker, source, reservation = (self.root / name for name in ('marker', 'source', 'reservation'))
        for path in (marker, source, reservation):
            path.write_bytes(b'{"original_owner":2188434}\n'); path.chmod(0o600)
        for suffix in ('.journal.json', '.owned-restore.json', '.owned-intent.json'):
            path = self.root / ('old' + suffix)
            path.write_bytes(b'{"original_failed_evidence":"unchanged"}\n'); path.chmod(0o600)
        plan = plan_fixture()
        capture = next(row for row in plan['containers'] if row['id'] == 'production-6')
        capture.update(id=M.CAPTURE_ID, name='/market-private-pipeline-primary-market-capture-account2-1')
        events = []
        def write_json(path, value, **k):
            path.write_text(json.dumps(value)); path.chmod(0o600)
            events.append(('audit', value['phase']))
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        C = SimpleNamespace(ROOT=self.root, P=SimpleNamespace(MARKER=marker, RESERVATION=reservation, document=lambda *a: plan),
            B=SimpleNamespace(read=lambda p, **k: p.read_bytes(), identity=identity, command=Mock(return_value='not-found')),
            paths=lambda p: (self.root / 'old.journal.json', source), datetime=datetime, timezone=timezone,
            timedelta=timedelta, write_json=write_json, release_owned_fences=Mock(side_effect=AssertionError('parking never releases fences')))
        H = SimpleNamespace(OP='old', NONCE='oldnonce', PLAN_SHA='old-digest', OLD_ROOT=self.root)
        R = SimpleNamespace(exact_scope=Q.exact_scope, IDS=Q.IDS, KNOWN_MARKET_FLAPS=Q.KNOWN_MARKET_FLAPS,
            pinned=lambda c: nullcontext(), hot_finished=Mock(), no_backup_exec=Mock(), preserved=Mock(return_value={'kept': True}),
            retire=Mock(side_effect=lambda *a: events.append(('park-only-qa',))))
        owner = self.stack.enter_context(patch.object(M, 'owner_proof'))
        incident = [{'id': M.CAPTURE_ID, 'name': capture['name'], 'restarting': True, 'restart_count': 40, 'health': 'unhealthy'}]
        self.stack.enter_context(patch.object(M, 'inventory', return_value=incident))
        health = self.stack.enter_context(patch.object(M, 'web_datastores_native'))
        private = self.stack.enter_context(patch.object(M, 'private_configuration', return_value={
            'retained': {'Env': ['PRIVATE_FIXTURE=never_persist_or_hash_this_value']}}))
        if not real_resources:
            self.stack.enter_context(patch.object(M, 'preserved_resources', return_value={'kept': True}))
        # ss only; the old-unit admission call remains not-found.
        C.B.command.side_effect = lambda a: '' if a[0] == '/usr/bin/ss' else 'not-found'
        return C, H, R, owner, health, private, events, (marker, source, reservation)

    def bind_volume_fixture(self, C):
        parent = self.root / 'attached'; parent.mkdir()
        base = parent / 'three-site-dc32d90392b0/webapp_fi'; base.mkdir(parents=True)
        backings = [base / name for name in ('audit', 'postgres', 'redis', 'uploads')]
        points, records, actual = [], {}, []
        for index, backing in enumerate(backings):
            backing.mkdir()
            point = self.root / 'docker-volumes' / ('v' + str(index)) / '_data'; point.mkdir(parents=True)
            points.append(point)
            records['v' + str(index)] = {'name': 'v' + str(index), 'driver': 'local', 'mountpoint': str(point),
                'options': {'type': 'none', 'o': 'bind', 'device': str(backing)}, 'labels': {'public': 'qa'}}
            actual.append({'Type': 'volume', 'Name': 'v' + str(index), 'Source': str(point), 'Destination': '/data/' + str(index),
                           'Mode': 'rw', 'RW': True, 'Propagation': 'rprivate'})
        secret = backings[0] / 'server.key'; secret.write_bytes(b'synthetic secret is never read'); secret.chmod(0o600)
        actual.append({'Type': 'bind', 'Source': str(secret), 'Destination': '/run/tls/server.key',
                       'Mode': 'ro', 'RW': False, 'Propagation': 'rprivate'})
        plan = C.P.document(None)
        selected = [row for row in plan['containers'] if row['id'] in Q.IDS]
        selected[0]['mounts'] = [{key: value for key, value in row.items() if key != 'Name'} for row in actual]
        state = {'mounted': True, 'parent_present': True}
        def command(argv):
            if argv[0] == '/usr/bin/findmnt':
                if '-T' in argv:
                    return argv[-1] if state['mounted'] else '/'
                if not state['parent_present']: return ''
                return str(parent) if argv[3] == 'TARGET' else state.get('uuid', M.QA_PARENT_UUID)
            if argv[0] == '/usr/bin/systemctl': return 'not-found'
            if argv[0] == '/usr/bin/ss': return ''
            if argv[1] == 'inspect':
                return json.dumps(actual if argv[-1] == selected[0]['id'] else [])
            if argv[1] == 'volume': return json.dumps(records[argv[-1]])
            if argv[1] == 'image': return argv[-1]
            self.fail('unexpected volume fixture command')
        C.B.command = Mock(side_effect=command)
        C.B.private_parent = Mock()
        C.P.canonical_mounts = lambda rows: sorted(
            [{key: value for key, value in row.items() if key != 'Name'} for row in rows], key=lambda row: row['Destination'])
        self.stack.enter_context(patch.object(M, 'QA_PARENT_MOUNT', parent))
        self.stack.enter_context(patch.object(M, 'QA_BACKING_PATHS', frozenset(backings)))
        original_stat = Path.stat
        def simulated_bind_stat(path, *args, **kwargs):
            value = original_stat(path, *args, **kwargs)
            if path == parent or path in backings:
                fields = list(value)
                fields[2] = 12345  # The separately attached filesystem.
                return os.stat_result(fields)
            if path in points and state['mounted']:
                # Mounted _data is the same inode as Options.device; unmounted
                # _data below falls through to the underlying root directory.
                fields = list(original_stat(backings[points.index(path)], *args, **kwargs))
                fields[2] = 12345
                return os.stat_result(fields)
            return value
        self.stack.enter_context(patch.object(Path, 'stat', simulated_bind_stat))
        return selected, state, records, backings, points

    def test_default_accepts_open_capture_incident_but_has_no_mutation(self):
        C, H, R, owner, health, private, events, files = self.fixture()
        before = [path.read_bytes() for path in files]
        def proof(*a):
            for path in files:
                fd = os.open(path, os.O_RDONLY)
                try:
                    with self.assertRaises(BlockingIOError):
                        M.fcntl.flock(fd, M.fcntl.LOCK_EX | M.fcntl.LOCK_NB)
                finally:
                    os.close(fd)
        owner.side_effect = proof
        result = M.execute(C, H, R, 'check', datetime.now(timezone.utc) + timedelta(minutes=10))
        self.assertEqual(result, {'status': 'qa_parking_checked', 'runtime_changed': False, 'production_incident_count': 1})
        R.retire.assert_not_called()
        C.release_owned_fences.assert_not_called()
        self.assertFalse(events)
        self.assertEqual(before, [path.read_bytes() for path in files])

    def test_qa_parking_keeps_incident_open_and_every_original_fence_and_private_value_unrecorded(self):
        C, H, R, owner, health, private, events, files = self.fixture()
        before = [path.read_bytes() for path in files]
        original = (self.root / 'old.journal.json').read_bytes()
        result = M.execute(C, H, R, 'park', datetime.now(timezone.utc) + timedelta(minutes=10))
        R.retire.assert_called_once()
        self.assertEqual(result['status'], 'qa_parked_production_incident_open')
        self.assertTrue(result['qa_parking_verified'])
        self.assertTrue(result['old_fences_retained'])
        self.assertFalse(result['security_transition_completed'])
        self.assertFalse(result['production_health_complete'])
        self.assertTrue(result['completion_unverified'])
        C.release_owned_fences.assert_not_called()
        self.assertEqual(before, [path.read_bytes() for path in files])
        self.assertEqual((self.root / 'old.journal.json').read_bytes(), original)
        raw = (self.root / 'old.qa-parking.json').read_bytes()
        self.assertNotIn(b'never_persist_or_hash_this_value', raw)
        audit = json.loads(raw)
        self.assertEqual(audit['original_deadline_utc'], '2026-10-24T07:00:00Z')
        self.assertEqual(len(audit['parked_ids']), 10)
        self.assertNotIn('fences_release_status', audit)

    def test_post_parking_web_failure_is_truthful_partial_not_a_fence_release(self):
        C, H, R, owner, health, private, events, files = self.fixture()
        health.side_effect = [None, RuntimeError('parking_production_api_not_ready')]
        result = M.execute(C, H, R, 'park', datetime.now(timezone.utc) + timedelta(minutes=10))
        self.assertTrue(result['qa_parking_verified'])
        self.assertFalse(result['required_web_datastores_native_passed'])
        self.assertTrue(result['completion_unverified'])
        self.assertTrue(all(path.exists() for path in files))
        audit = json.loads((self.root / 'old.qa-parking.json').read_bytes())
        self.assertEqual(audit['required_web_datastores_native_code'], 'parking_production_api_not_ready')
        C.release_owned_fences.assert_not_called()

    def test_pre_parking_web_failure_blocks_without_stopping_anything(self):
        C, H, R, owner, health, private, events, files = self.fixture()
        health.side_effect = RuntimeError('parking_production_api_not_ready')
        with self.assertRaisesRegex(RuntimeError, 'parking_production_api_not_ready'):
            M.execute(C, H, R, 'park', datetime.now(timezone.utc) + timedelta(minutes=10))
        R.retire.assert_not_called()
        self.assertFalse(events)
        self.assertTrue(all(path.exists() for path in files))

    def test_changed_private_config_records_only_static_failure_and_keeps_fences(self):
        C, H, R, owner, health, private, events, files = self.fixture()
        private.side_effect = [{'private': 'old-secret'}, {'private': 'new-secret'}]
        with self.assertRaisesRegex(RuntimeError, 'retained_private_configuration_changed'):
            M.execute(C, H, R, 'park', datetime.now(timezone.utc) + timedelta(minutes=10))
        raw = (self.root / 'old.qa-parking.json').read_bytes()
        self.assertNotIn(b'old-secret', raw); self.assertNotIn(b'new-secret', raw)
        self.assertEqual(json.loads(raw)['phase'], 'parking_incomplete')
        self.assertTrue(all(path.exists() for path in files))
        C.release_owned_fences.assert_not_called()

    def test_existing_attempt_refuses_without_adoption(self):
        C, H, R, owner, health, private, events, files = self.fixture()
        attempt = self.root / 'old.qa-parking.json'; attempt.write_bytes(b'old incomplete evidence')
        with self.assertRaisesRegex(RuntimeError, 'parking_or_retirement_already_attempted'):
            M.execute(C, H, R, 'park', datetime.now(timezone.utc) + timedelta(minutes=10))
        self.assertEqual(attempt.read_bytes(), b'old incomplete evidence')
        R.retire.assert_not_called()

    def test_even_a_healthy_instant_cannot_close_production_incidents_or_release_fences(self):
        C, H, R, owner, health, private, events, files = self.fixture()
        with patch.object(M, 'inventory', return_value=[]):
            result = M.execute(C, H, R, 'park', datetime.now(timezone.utc) + timedelta(minutes=10))
        self.assertTrue(result['qa_parking_verified'])
        self.assertFalse(result['production_health_complete'])
        self.assertTrue(result['completion_unverified'])
        C.release_owned_fences.assert_not_called()
        self.assertTrue(all(path.exists() for path in files))

    def test_private_config_routes_only_through_memory_reader_not_old_disk_helper(self):
        ids = [f'{index:064x}' for index in range(17)]
        plan = {'containers': [{'id': ident} for ident in ids]}
        C = SimpleNamespace(B=SimpleNamespace(command=Mock(side_effect=AssertionError('private data cannot use disk helper'))))
        with patch.object(M, 'memory_private_inspect', return_value={'config': {}, 'host_config': {}}) as memory:
            self.assertEqual(len(M.private_configuration(C, SimpleNamespace(IDS={}), plan)), 17)
        self.assertEqual(memory.call_count, 17)
        C.B.command.assert_not_called()

    def test_actual_memory_pipe_never_uses_tempfile_hash_or_stdout_for_private_values(self):
        value = {'config': {'Env': ['PRIVATE_KEY=do-not-persist-or-hash']}, 'host_config': {'Memory': 1}}
        readfd, writefd = os.pipe()
        os.write(writefd, json.dumps(value).encode()); os.close(writefd)
        pipe = os.fdopen(readfd, 'rb')
        child = SimpleNamespace(pid=987654321, stdout=pipe, poll=lambda: 0, wait=lambda **k: 0)
        with patch.object(M, 'trusted_docker'), patch.object(M.subprocess, 'Popen', return_value=child), \
             patch('tempfile.TemporaryFile', side_effect=AssertionError('private data cannot touch disk')), \
             patch.object(M, 'sha256', side_effect=AssertionError('private data cannot be hashed')), \
             patch('sys.stdout', new_callable=io.StringIO) as output, patch.object(M.os, 'killpg') as kill:
            result = M.memory_private_inspect('a' * 64)
        self.assertEqual(result, value)
        self.assertEqual(output.getvalue(), '')
        kill.assert_not_called()
        self.assertTrue(pipe.closed)

    def test_memory_output_bound_kills_only_owned_inspect_client_before_unlimited_read(self):
        stream = io.BytesIO(b'x' * 2097153)
        child = SimpleNamespace(pid=987654321, stdout=stream, poll=lambda: None, wait=lambda **k: 0)
        class Selector:
            active = True
            def register(self, *a): pass
            def get_map(self): return {1: 1} if self.active else {}
            def select(self, **k): return [(SimpleNamespace(fileobj=stream), 1)]
            def unregister(self, *a): self.active = False
            def close(self): pass
        with patch.object(M, 'trusted_docker'), patch.object(M.subprocess, 'Popen', return_value=child), \
             patch.object(M.selectors, 'DefaultSelector', return_value=Selector()), patch.object(stream, 'fileno', return_value=123), \
             patch.object(M.os, 'read', side_effect=lambda fd, count: stream.read(count)), patch.object(M.os, 'killpg') as kill, \
             self.assertRaisesRegex(RuntimeError, 'private_inspect_output_bound'):
            M.memory_private_inspect('a' * 64)
        kill.assert_called_once_with(child.pid, M.signal.SIGKILL)
        self.assertTrue(stream.closed)

    def test_private_inspection_timeout_reaps_owned_client_without_waiting_or_disk(self):
        stream = io.BytesIO()
        child = SimpleNamespace(pid=987654321, stdout=stream, poll=lambda: None, wait=lambda **k: 0)
        selector = SimpleNamespace(register=Mock(), get_map=Mock(return_value={1: 1}), select=Mock(), close=Mock())
        with patch.object(M, 'trusted_docker'), patch.object(M.subprocess, 'Popen', return_value=child), \
             patch.object(M.selectors, 'DefaultSelector', return_value=selector), \
             patch.object(M.time, 'monotonic', side_effect=[0, 26]), patch.object(M.os, 'killpg') as kill, \
             self.assertRaisesRegex(RuntimeError, 'private_inspect_timeout'):
            M.memory_private_inspect('a' * 64)
        selector.select.assert_not_called()
        kill.assert_called_once_with(child.pid, M.signal.SIGKILL)
        self.assertTrue(stream.closed)

    def test_local_bind_data_survives_driver_unmount_without_false_parking_failure(self):
        C, H, R, owner, health, private, events, files = self.fixture(real_resources=True)
        selected, state, records, backings, points = self.bind_volume_fixture(C)
        before = M.preserved_resources(C, selected)
        legacy_before = Q.preserved(C, selected)
        mounted_identity = (points[0].stat().st_dev, points[0].stat().st_ino)
        R.retire.side_effect = lambda *a: state.update(mounted=False)
        result = M.execute(C, H, R, 'park', datetime.now(timezone.utc) + timedelta(minutes=10))
        unmounted_identity = (points[0].stat().st_dev, points[0].stat().st_ino)
        self.assertNotEqual(mounted_identity, unmounted_identity)
        self.assertNotEqual(Q.preserved(C, selected), legacy_before)
        self.assertEqual(M.preserved_resources(C, selected), before)
        self.assertTrue(result['qa_parking_verified'])
        self.assertTrue(result['old_fences_retained'])
        self.assertTrue(all(path.exists() for path in files))
        R.preserved.assert_not_called()

    def test_changed_actual_backing_inode_blocks_verification_and_retains_old_fences(self):
        C, H, R, owner, health, private, events, files = self.fixture(real_resources=True)
        selected, state, records, backings, points = self.bind_volume_fixture(C)
        def changed(*args):
            state['mounted'] = False
            backings[1].rename(backings[1].with_name('postgres-preserved-original'))
            backings[1].mkdir()
        R.retire.side_effect = changed
        with self.assertRaisesRegex(RuntimeError, 'parked_resource_changed'):
            M.execute(C, H, R, 'park', datetime.now(timezone.utc) + timedelta(minutes=10))
        self.assertTrue(all(path.exists() for path in files))
        C.release_owned_fences.assert_not_called()
        backings[1].rmdir()
        backings[1].symlink_to(backings[1].with_name('postgres-preserved-original'), target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, 'qa_backing_redirected'):
            M.preserved_resources(C, selected)

    def test_bind_options_cannot_be_silently_replaced_by_plain_local_options(self):
        C, H, R, owner, health, private, events, files = self.fixture(real_resources=True)
        selected, state, records, backings, points = self.bind_volume_fixture(C)
        def changed(*args):
            state['mounted'] = False
            records['v0']['options'] = None
        R.retire.side_effect = changed
        with self.assertRaisesRegex(RuntimeError, 'parked_resource_changed'):
            M.execute(C, H, R, 'park', datetime.now(timezone.utc) + timedelta(minutes=10))
        self.assertTrue(all(path.exists() for path in files))
        C.release_owned_fences.assert_not_called()

    def test_changed_options_unsupported_driver_or_outside_device_are_rejected(self):
        C, H, R, owner, health, private, events, files = self.fixture(real_resources=True)
        selected, state, records, backings, points = self.bind_volume_fixture(C)
        R.retire.side_effect = lambda *a: records['v0'].update(options={'type': 'none', 'o': 'rbind', 'device': str(backings[0])})
        with self.assertRaisesRegex(RuntimeError, 'qa_volume_options_unsupported'):
            M.execute(C, H, R, 'park', datetime.now(timezone.utc) + timedelta(minutes=10))
        self.assertTrue(all(path.exists() for path in files))
        records['v0']['options'] = {'type': 'none', 'o': 'bind', 'device': str(self.root / 'outside')}
        with self.assertRaisesRegex(RuntimeError, 'qa_bind_device_unapproved'):
            M.preserved_resources(C, selected)
        records['v0']['driver'] = 'unapproved-driver'
        with self.assertRaisesRegex(RuntimeError, 'qa_volume_driver_unsupported'):
            M.preserved_resources(C, selected)

    def test_parent_attachment_loss_refuses_and_plain_local_keeps_directory_identity(self):
        C, H, R, owner, health, private, events, files = self.fixture(real_resources=True)
        selected, state, records, backings, points = self.bind_volume_fixture(C)
        state['parent_present'] = False
        with self.assertRaisesRegex(RuntimeError, 'qa_parent_mount_missing'):
            M.preserved_resources(C, selected)
        state.update(parent_present=True, uuid='unapproved-attachment')
        with self.assertRaisesRegex(RuntimeError, 'qa_parent_mount_changed'):
            M.preserved_resources(C, selected)
        for record in records.values(): record['options'] = None
        state['mounted'] = False
        result = M.preserved_resources(C, selected)
        self.assertIsNone(result['parent_mount'])
        self.assertTrue(all(record['backing_kind'] == 'local_directory' for record in result['volumes'].values()))


if __name__ == '__main__':
    unittest.main()
