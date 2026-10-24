"""Hermetic owned continuation tests; no systemd, Docker, SSH or network."""
from contextlib import ExitStack, nullcontext
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('_owned_intent_test', ROOT / 'deploy/maintenance/web_engine_owned_intent.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class Refusal(RuntimeError):
    pass


class OwnedIntent(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for target in ('subprocess.run', 'subprocess.Popen', 'os.system', 'socket.socket.connect',
                       'socket.create_connection', 'socket.getaddrinfo'):
            self.stack.enter_context(patch(target, side_effect=AssertionError('hermetic only')))
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.owned-intent-test-', dir=ROOT)))

    def test_prior_buffer_is_exact_and_compiled_once_without_path_reopen(self):
        raw = (ROOT / 'deploy/maintenance/web_engine_owned_restore.py').read_text()
        self.assertEqual(sha256(raw.encode()).hexdigest(), M.PRIOR_SHA)
        module = M.prior_source(raw)
        self.assertEqual(module.NONCE, '304b2ec68d304738a43794f5a666b6c8')
        with self.assertRaisesRegex(RuntimeError, 'prior_source_changed'):
            M.prior_source(raw + '\n')

    def test_db_api_checks_do_not_demand_still_paused_application_health(self):
        events = []
        plan = {'health_probes': [{'id': 'app', 'url': 'http://127.0.0.1:8000/api/config'}]}
        C = SimpleNamespace(old_datastores_and_api_ready=lambda p: events.append(('db', p['health_probes'])),
            B=SimpleNamespace(CONTAINER_HTTP_PROBE='PUBLIC_PROBE', command=lambda a: events.append(('api', a)) or '200'))
        M.db_api(C, plan)
        self.assertEqual(events[0], ('db', []))
        self.assertEqual(events[1][1][-1], plan['health_probes'][0]['url'])
        C.B.command = Mock(return_value='500')
        with self.assertRaisesRegex(RuntimeError, 'original_api_not_ready'):
            M.db_api(C, plan)

    def test_original_policies_and_only_four_timers_precede_one_blob_start(self):
        commands = []
        plan = {'containers': [{'id': str(i), 'restart_policy': {'Name': 'unless-stopped', 'MaximumRetryCount': 0}} for i in range(27)]}
        def command(a):
            commands.append(a)
            return 'false' if a[1] == 'inspect' else ''
        C = SimpleNamespace(B=SimpleNamespace(command=command))
        with patch.object(M, 'db_api') as db:
            M.restore_intent(C, plan)
        db.assert_called_once_with(C, plan)
        self.assertEqual(sum(a[1] == 'update' for a in commands), 27)
        self.assertEqual({a[-1] for a in commands if a[0] == '/usr/bin/systemctl'}, M.TIMERS)
        self.assertEqual(commands[-1], ['/usr/bin/docker', 'start', M.BLOB_ID])
        self.assertTrue(all(a[1] == 'update' for a in commands[:27]))
        self.assertFalse(any('coin-capture.service' in a or 'market-channel-capture.service' in a for a in commands))

    def test_prior_owner_must_be_failed_dead_and_have_no_descendants(self):
        C = SimpleNamespace(B=SimpleNamespace(command=Mock(return_value='ActiveState=failed\nMainPID=0\nExecMainStatus=1')),
                            scope_empty=Mock(return_value=True))
        with patch.object(M.os.path, 'lexists', return_value=False):
            M.prior_dead(C, SimpleNamespace(NONCE='publicnonce'))
        C.scope_empty.return_value = False
        with patch.object(M.os.path, 'lexists', return_value=False), self.assertRaisesRegex(RuntimeError, 'previous_restore_descendants_present'):
            M.prior_dead(C, SimpleNamespace(NONCE='publicnonce'))

    def test_wrong_prior_audit_and_fence_or_original_inode_refuse_before_host_actions(self):
        import copy
        marker, source, reservation = (self.root / name for name in ('marker', 'source', 'reservation'))
        for path in (marker, source, reservation):
            path.write_bytes(b'public fixture')
        fds = {path: os.open(path, os.O_RDWR) for path in (marker, source, reservation)}
        for fd in fds.values():
            self.addCleanup(os.close, fd)
        prior = SimpleNamespace(OP='op', NONCE='nonce', DIGEST='digest', SOURCE_SHA='source', ROOT=self.root)
        plan = {'operation_id': 'op', 'nonce': 'nonce', 'source_root': str(self.root / 'source')}
        fence = {'schema': 'web_engine_transaction/1', 'operation_id': 'op', 'nonce': 'nonce', 'manifest_sha256': 'digest', 'pid': M.PRIOR_PID}
        originals = [{'path': str(path), 'device': path.stat().st_dev, 'inode': path.stat().st_ino,
                      'payload': {**fence, 'pid': M.FIRST_PID}} for path in (source, reservation)]
        old = {'schema': 'web_engine_owned_restore/1', 'operation_id': 'op', 'nonce': 'nonce', 'manifest_sha256': 'digest',
               'source_sha256': 'source', 'previous_owner_pid': M.FIRST_PID, 'restore_owner_pid': M.PRIOR_PID,
               'status': 'restoring', 'original_fences': originals}
        C = SimpleNamespace(native_contract=Mock(), control_parent_contract=Mock(), remaining_contract=Mock(),
            journal=Mock(return_value={'phase': 'writers_quiesced', 'pid': M.FIRST_PID}),
            paths=lambda p: (self.root / 'journal', source), P=SimpleNamespace(RESERVATION=reservation))
        wrong = {**old, 'nonce': 'foreign'}
        with self.assertRaisesRegex(RuntimeError, 'previous_restore_audit_changed'):
            M.metadata_proof(C, prior, plan, object(), fence, fence, wrong, fds)
        with self.assertRaisesRegex(RuntimeError, 'previous_restore_fence_changed'):
            M.metadata_proof(C, prior, plan, object(), {**fence, 'pid': 1}, fence, old, fds)
        changed = copy.deepcopy(old)
        changed['original_fences'][0]['inode'] += 1
        with self.assertRaisesRegex(RuntimeError, 'original_fence_binding_changed'):
            M.metadata_proof(C, prior, plan, object(), fence, fence, changed, fds)

    def test_final_health_never_waives_original_health_or_baseline(self):
        from datetime import datetime, timezone, timedelta
        events = []
        C = SimpleNamespace(P=SimpleNamespace(Refusal=Refusal), B=SimpleNamespace(Refusal=Refusal),
            datetime=datetime, timezone=timezone, remaining_contract=lambda *a: datetime.now(timezone.utc) + timedelta(minutes=10),
            old_datastores_and_api_ready=lambda p: events.append('allhealth'),
            invariant_contract=lambda *a, **k: events.append('invariant'), runtime_contract=lambda *a: events.append('baseline'))
        with patch.object(M, 'stable_snapshot', return_value={'original': ('start', 0)}), \
             patch.object(M.time, 'monotonic', side_effect=[0, 0, 0, 0, 31]), patch.object(M.time, 'sleep'):
            self.assertTrue(M.final_health(C, SimpleNamespace(DIGEST='public'), {}))
        self.assertEqual(events, ['allhealth', 'invariant', 'baseline'] * 2)
        C.old_datastores_and_api_ready = Mock(side_effect=Refusal('old_container_not_healthy'))
        with patch.object(M.time, 'monotonic', side_effect=[0, 0, 301]), patch.object(M.time, 'sleep') as sleep:
            self.assertFalse(M.final_health(C, SimpleNamespace(DIGEST='public'), {}))
        sleep.assert_not_called()

    def test_restart_after_apparent_health_resets_stability_and_cannot_release_fences(self):
        from datetime import datetime, timezone, timedelta
        C = SimpleNamespace(P=SimpleNamespace(Refusal=Refusal), B=SimpleNamespace(Refusal=Refusal),
            datetime=datetime, timezone=timezone, remaining_contract=lambda *a: datetime.now(timezone.utc) + timedelta(minutes=10),
            old_datastores_and_api_ready=Mock(), invariant_contract=Mock(), runtime_contract=Mock())
        snapshots = [{'blob': ('first-start', 0)}, {'blob': ('new-start', 1)}, Refusal('original_application_restarting')]
        # 31 seconds since first success is insufficient: the restart at 10
        # resets that interval; the later exit/restart then fails closed.
        with patch.object(M, 'stable_snapshot', side_effect=snapshots), \
             patch.object(M.time, 'monotonic', side_effect=[0, 0, 0, 0, 10, 10, 301]), patch.object(M.time, 'sleep'):
            self.assertFalse(M.final_health(C, SimpleNamespace(DIGEST='public'), {}))

    def test_stability_snapshot_refuses_a_restarting_original_application(self):
        rows = [{'id': str(i), 'running': i < 26} for i in range(27)]
        C = SimpleNamespace(P=SimpleNamespace(Refusal=Refusal), B=SimpleNamespace(command=Mock(return_value=
            '{"started":"public-time","restarts":0,"running":true,"restarting":false}')))
        self.assertEqual(len(M.stable_snapshot(C, {'containers': rows})), 26)
        C.B.command = Mock(return_value='{"started":"public-time","restarts":1,"running":true,"restarting":true}')
        with self.assertRaisesRegex(Refusal, 'original_application_restarting'):
            M.stable_snapshot(C, {'containers': rows})

    def test_transitional_app_health_does_not_waive_identities_policies_or_database_readiness(self):
        import copy
        rows = []
        for index in range(27):
            ident = M.BLOB_ID if index == 25 else str(index)
            rows.append({'id': ident, 'name': '/three-site-dc32d90392b0-webapp-fi-webapp_fi_blobs-1' if index == 25 else '/synthetic-' + str(index),
                'image': 'public-image', 'project': 'public-project', 'mounts': [], 'network_mode': 'public-network',
                'port_bindings': {}, 'network_names': {'public-network': {}}, 'running': index < 26,
                'health': None if index < 8 else 'unhealthy', 'restart_policy': {'Name': 'no', 'MaximumRetryCount': 0}})
        baseline = copy.deepcopy(rows)
        for row in baseline:
            row['restart_policy'] = {'Name': 'unless-stopped', 'MaximumRetryCount': 0}
        rows[25]['running'] = False
        plan = {'containers': baseline, 'datastore_ids': [str(index) for index in range(8)]}
        current = {row['id']: row for row in rows}
        def command(argv):
            if argv[:3] == ['/usr/bin/docker', 'ps', '-aq']:
                return '\n'.join(current)
            if argv[:2] == ['/usr/bin/docker', 'info']:
                return '{}' if '"root"' in argv[-1] else '{"driver":"systemd","version":"2"}'
            if argv[:2] == ['/usr/bin/docker', 'inspect']:
                return json.dumps(current[argv[-1]])
            if 'namespaces' in argv:
                return 'moby\nmoby_history'
            if 'tasks' in argv:
                return '\n'.join(row['id'] for row in current.values() if row['running']) if 'moby' in argv else ''
            raise AssertionError('unexpected synthetic command')
        C = SimpleNamespace(B=SimpleNamespace(command=command), INSPECT='PUBLIC_INSPECT',
            P=SimpleNamespace(store_contract=Mock(), canonical_mounts=lambda rows: rows))
        with patch.object(M, 'db_api') as ready:
            M.transitional_runtime(C, plan)
        ready.assert_called_once_with(C, plan)
        for key, value, code in [('running', False, 'unexpected_stopped_application'),
                                 ('network_mode', 'foreign-network', 'container_identity_changed'),
                                 ('restart_policy', {'Name': 'always', 'MaximumRetryCount': 0}, 'original_restore_policy_changed')]:
            original = current['8'][key]
            current['8'][key] = value
            with self.subTest(key=key), patch.object(M, 'db_api') as ready, self.assertRaisesRegex(RuntimeError, code):
                M.transitional_runtime(C, plan)
            ready.assert_not_called()
            current['8'][key] = original
        current['0']['health'] = 'unhealthy'
        with self.assertRaisesRegex(RuntimeError, 'datastore_unhealthy'):
            M.transitional_runtime(C, plan)

    def test_intent_proof_requires_original_policy_and_timer_state(self):
        policy = {'Name': 'unless-stopped', 'MaximumRetryCount': 0}
        plan = {'containers': [{'id': 'fixture', 'restart_policy': policy}], 'units': [
            {'name': 'public.timer', 'enabled': 'enabled', 'kind': 'timer', 'active': True}], 'engine_intent': []}
        responses = [json.dumps(policy), 'enabled', 'active']
        C = SimpleNamespace(B=SimpleNamespace(command=Mock(side_effect=responses)))
        M.intent_proof(C, plan)
        C.B.command = Mock(return_value='{"Name":"no","MaximumRetryCount":0}')
        with self.assertRaisesRegex(RuntimeError, 'original_policy_not_restored'):
            M.intent_proof(C, plan)
        C.B.command = Mock(side_effect=[json.dumps(policy), 'enabled', 'inactive'])
        with self.assertRaisesRegex(RuntimeError, 'original_native_intent_not_restored'):
            M.intent_proof(C, plan)

    def fixture(self):
        marker, source, reservation = (self.root / name for name in ('marker', 'source', 'reservation'))
        owner = {'pid': M.PRIOR_PID}
        marker.write_bytes(b'original M unchanged')
        for path in (source, reservation):
            path.write_text(json.dumps(owner))
        for path in (marker, source, reservation):
            path.chmod(0o600)
        audit = self.root / 'public-operation.owned-restore.json'
        audit.write_bytes(b'{"original":"evidence remains"}\n')
        audit.chmod(0o600)
        events = []
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        def write_json(p, value, **k):
            events.append(('write', p.name, dict(value)))
            p.write_text(json.dumps(value))
        C = SimpleNamespace(ROOT=self.root, P=SimpleNamespace(MARKER=marker, RESERVATION=reservation,
            document=lambda *a: {}), B=SimpleNamespace(identity=identity, read=lambda p, **k: p.read_bytes(),
            document=lambda p: json.loads(p.read_bytes())), paths=lambda p: (self.root / 'journal', source),
            write_json=write_json, invariant_contract=Mock(),
            release_owned_fences=lambda rows: events.append(('release', [str(p) for p, _, _ in rows])))
        prior = SimpleNamespace(ROOT=self.root, OP='public-operation', NONCE='public', DIGEST='digest',
            pinned=lambda c: nullcontext(object()), rewrite_owner=Mock())
        return C, prior, events, (marker, source, reservation, audit)

    def test_default_check_writes_nothing_holds_three_real_flocks(self):
        C, prior, events, files = self.fixture()
        before = [path.read_bytes() for path in files]
        def proof(*args):
            for path in files[:3]:
                fd = os.open(path, os.O_RDONLY)
                try:
                    with self.assertRaises(BlockingIOError):
                        M.fcntl.flock(fd, M.fcntl.LOCK_EX | M.fcntl.LOCK_NB)
                finally:
                    os.close(fd)
            return {'phase': 'writers_quiesced'}
        with patch.object(M, 'metadata_proof', side_effect=proof):
            self.assertEqual(M.execute(C, prior, 'check'), {'status': 'owned_intent_checked', 'runtime_changed': False})
        self.assertFalse(events)
        self.assertEqual(before, [path.read_bytes() for path in files])
        prior.rewrite_owner.assert_not_called()

    def test_incomplete_health_retains_fences_and_original_evidence(self):
        C, prior, events, files = self.fixture()
        original = files[-1].read_bytes()
        journal = {'phase': 'writers_quiesced'}
        C.journal = Mock(return_value=dict(journal))
        with patch.object(M, 'metadata_proof', return_value=journal), patch.object(M, 'restore_intent') as intent, patch.object(M, 'intent_proof'), \
             patch.object(M, 'final_health', return_value=False):
            result = M.execute(C, prior, 'restore_intent')
        intent.assert_called_once()
        self.assertEqual(result['status'], 'intent_restored_health_incomplete')
        self.assertTrue(result['completion_unverified'])
        self.assertFalse(any(row[0] == 'release' for row in events))
        self.assertTrue(all(path.exists() for path in files))
        self.assertEqual(files[-1].read_bytes(), original)
        self.assertEqual(C.journal.return_value, {'phase': 'writers_quiesced'})
        self.assertEqual(prior.rewrite_owner.call_count, 2)

    def test_complete_health_is_required_before_terminal_journal_and_source_first_release(self):
        C, prior, events, files = self.fixture()
        journal = {'phase': 'writers_quiesced'}
        C.journal = Mock(return_value=dict(journal))
        with patch.object(M, 'metadata_proof', return_value=journal), patch.object(M, 'restore_intent'), patch.object(M, 'intent_proof'), \
             patch.object(M, 'final_health', return_value=True):
            result = M.execute(C, prior, 'restore_intent')
        self.assertEqual(result['status'], 'aborted_restored')
        self.assertFalse(result['security_transition_completed'])
        releases = [row for row in events if row[0] == 'release']
        self.assertEqual(releases[0][1], [str(files[2]), str(files[1])])  # C reverses: source, reservation.
        writes = [row for row in events if row[0] == 'write' and row[1] == 'journal']
        self.assertEqual(writes[0][2]['phase'], 'aborted_restored')


if __name__ == '__main__':
    unittest.main()
