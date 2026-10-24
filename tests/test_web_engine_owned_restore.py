"""Owned fixtures only. No actual Docker, systemd, SSH, DB or network."""
from contextlib import ExitStack, nullcontext
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('_owned_web_restore_test', ROOT / 'deploy/maintenance/web_engine_owned_restore.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class OwnedRestore(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name in ('subprocess.run', 'subprocess.Popen', 'socket.socket.connect',
                     'socket.create_connection', 'socket.getaddrinfo'):
            self.stack.enter_context(patch(name, side_effect=AssertionError('no host/network')))
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.owned-restore-fixture-', dir=ROOT)))

    def test_restore_starts_only_eighteen_original_writers_then_policies_and_timers_last(self):
        events = []
        rows = [{'id': str(i), 'running': i < 26, 'restart_policy': {'Name': 'unless-stopped', 'MaximumRetryCount': 0}}
                for i in range(27)]
        units = [{'name': 'original.service', 'kind': 'daemon', 'active': True},
                 {'name': 'original.timer', 'kind': 'timer', 'active': True},
                 {'name': 'never-manual.service', 'kind': 'oneshot', 'active': True},
                 {'name': 'disabled.service', 'kind': 'daemon', 'active': False}]
        plan = {'containers': rows, 'datastore_ids': [str(i) for i in range(8)], 'units': units,
                'health_probes': [{'id': 'app'}]}
        status = {'phase': 'writers_quiesced', 'pid': 99999999}
        def command(argv): events.append(('command', argv)); return ''
        C = SimpleNamespace(B=SimpleNamespace(command=command),
            old_datastores_and_api_ready=lambda p: events.append(('ready', p['health_probes'])),
            invariant_contract=lambda *a, **k: events.append(('invariant',)),
            runtime_contract=lambda *a: events.append(('baseline',)),
            journal=lambda *a: dict(status), paths=lambda p: (self.root / 'journal', self.root / 'source'),
            write_json=lambda p, v, **k: events.append(('journal', v)))
        M.restore(C, plan, status)
        self.assertEqual(events[0], ('ready', []))
        starts = [event[1][-1] for event in events if event[0] == 'command' and event[1][:2] == ['/usr/bin/docker', 'start']]
        self.assertEqual(starts, [str(i) for i in range(8, 26)])
        self.assertNotIn('26', starts)
        self.assertEqual(sum(event[0] == 'command' and event[1][1] == 'update' for event in events), 27)
        native = [event[1][-1] for event in events if event[0] == 'command' and event[1][0] == '/usr/bin/systemctl']
        self.assertEqual(native, ['original.service', 'original.timer'])
        terminal = events[-1][1]
        self.assertEqual((terminal['phase'], terminal['aborted_from'], terminal['security_transition_completed']),
                         ('aborted_restored', 'writers_quiesced', False))

    def test_active_exec_or_dump_backend_refuses_without_private_env_reads(self):
        plan = {'datastore_ids': ['db0', 'db1'], 'datastore_probes': [
            {'id': 'db0', 'kind': 'postgres', 'user': 'synthetic', 'database': 'synthetic'},
            {'id': 'db1', 'kind': 'redis'}]}
        commands = []
        def command(argv):
            commands.append(argv)
            return '[]' if argv[1] == 'inspect' else '0'
        C = SimpleNamespace(B=SimpleNamespace(command=command))
        M.no_dump_or_restore(C, plan)
        self.assertEqual(len(commands), 3)
        self.assertIn('application_name', commands[-1][-1])
        C.B.command = Mock(return_value='["active-exec"]')
        with self.assertRaisesRegex(RuntimeError, 'datastore_exec_active'):
            M.no_dump_or_restore(C, plan)
        C.B.command = lambda argv: '[]' if argv[1] == 'inspect' else '1'
        with self.assertRaisesRegex(RuntimeError, 'dump_or_restore_backend_active'):
            M.no_dump_or_restore(C, plan)

    def test_owner_pid_changes_in_place_only_without_replacing_original_inode(self):
        path = self.root / 'source'
        original = {'schema': 'web_engine_transaction/1', 'operation_id': M.OP, 'nonce': M.NONCE,
                    'manifest_sha256': M.DIGEST, 'pid': 99999999, 'phase': 'reserved'}
        path.write_text(json.dumps(original))
        path.chmod(0o600)
        before = path.stat()
        C = SimpleNamespace(B=SimpleNamespace(document=lambda p: json.loads(p.read_bytes()),
                                               write_all=lambda fd, raw: os.write(fd, raw)))
        fd = os.open(path, os.O_RDWR)
        try:
            M.fcntl.flock(fd, M.fcntl.LOCK_EX | M.fcntl.LOCK_NB)
            M.rewrite_owner(C, fd, path, original)
        finally:
            os.close(fd)
        self.assertEqual((path.stat().st_dev, path.stat().st_ino), (before.st_dev, before.st_ino))
        self.assertEqual(json.loads(path.read_bytes()), {**original, 'pid': os.getpid()})

    def test_wrong_phase_refuses_before_any_restore_or_ownership_update(self):
        C = SimpleNamespace(native_contract=Mock(), control_parent_contract=Mock(),
                            journal=Mock(return_value={'phase': 'installing', 'pid': 99999999}))
        with self.assertRaisesRegex(RuntimeError, 'not_original_failed_phase'):
            M.proof(C, {'operation_id': M.OP, 'nonce': M.NONCE}, object(), {'pid': 99999999})

    def test_original_runner_and_scope_must_be_dead_and_empty(self):
        C = SimpleNamespace(B=SimpleNamespace(command=Mock(side_effect=[
            'ActiveState=failed\nMainPID=0\nExecMainStatus=1', 'not-found'])),
            scope_empty=Mock(return_value=True))
        M.descendants_gone(C, {})
        C.B.command = Mock(return_value='ActiveState=active\nMainPID=100\nExecMainStatus=0')
        with self.assertRaisesRegex(RuntimeError, 'original_runner_not_failed_exited'):
            M.descendants_gone(C, {})
        C.B.command = Mock(side_effect=['ActiveState=failed\nMainPID=0\nExecMainStatus=1', 'not-found'])
        C.scope_empty.return_value = False
        with self.assertRaisesRegex(RuntimeError, 'old_descendants_present'):
            M.descendants_gone(C, {})

    def test_default_check_holds_all_three_original_flocks_and_writes_nothing(self):
        marker, source, reservation = (self.root / name for name in ('marker', 'source', 'reservation'))
        owner = {'schema': 'web_engine_transaction/1', 'operation_id': M.OP, 'nonce': M.NONCE,
                 'manifest_sha256': M.DIGEST, 'pid': 99999999, 'phase': 'reserved'}
        marker.write_bytes(b'unchanged original authority')
        for path in (source, reservation): path.write_text(json.dumps(owner))
        for path in (marker, source, reservation): path.chmod(0o600)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        C = SimpleNamespace(ROOT=self.root, P=SimpleNamespace(MARKER=marker, RESERVATION=reservation,
            document=lambda *a: {'source_root': str(M.ROOT / 'source')}),
            B=SimpleNamespace(read=lambda p, **k: p.read_bytes(), identity=identity,
                              document=lambda p: json.loads(p.read_bytes())),
            paths=lambda p: (self.root / 'journal', source), write_json=Mock())
        def proof(*args):
            for path in (marker, source, reservation):
                fd = os.open(path, os.O_RDONLY)
                try:
                    with self.assertRaises(BlockingIOError):
                        M.fcntl.flock(fd, M.fcntl.LOCK_EX | M.fcntl.LOCK_NB)
                finally: os.close(fd)
            return {'phase': 'writers_quiesced', 'pid': owner['pid']}
        with patch.object(M, 'pinned', return_value=nullcontext(object())), patch.object(M, 'proof', side_effect=proof):
            self.assertEqual(M.execute(C, 'check'), {'status': 'owned_restore_checked', 'runtime_changed': False})
        C.write_json.assert_not_called()
        self.assertEqual(json.loads(source.read_bytes()), owner)
        self.assertEqual(json.loads(reservation.read_bytes()), owner)
        self.assertEqual(marker.read_bytes(), b'unchanged original authority')


if __name__ == '__main__':
    unittest.main()
