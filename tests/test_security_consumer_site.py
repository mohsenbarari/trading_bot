"""Synthetic-only worker safety tests: unmocked host commands always fail."""
from contextlib import ExitStack
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'deploy/maintenance/security_consumer_site.py'
SPEC = importlib.util.spec_from_file_location('_security_consumer_site_test', SOURCE)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class OwnedRollback(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        def denied(*args, **kwargs): raise AssertionError('unexpected command/network/private file')
        for name in ('subprocess.run', 'subprocess.Popen', 'os.system', 'socket.socket.connect',
                     'socket.getaddrinfo', 'socket.create_connection', 'urllib.request.urlopen'):
            self.stack.enter_context(patch(name, side_effect=denied))
        self.record = {'nonce': 'synthetic-nonce', 'images': {'bot': 'sha256:' + '2' * 64}}
        self.original = {'config_binding': {'synthetic': True}, 'targets': {
            'app': {'id': 'a' * 64, 'image': 'sha256:' + '1' * 64,
                    'name': '/trading_bot_app', 'mounts': [], 'ports': [], 'networks': [],
                    'restart_policy': {'Name': 'always', 'MaximumRetryCount': 0}}},
            'all_containers': [{'id': 'a' * 64, 'name': '/trading_bot_app', 'image': 'sha256:' + '1' * 64},
                               {'id': 'b' * 64, 'name': '/protected-market', 'image': 'sha256:' + '3' * 64}]}
        self.current = deepcopy(self.original['all_containers'])
        self.item = {'Id': 'a' * 64, 'Image': 'sha256:' + '1' * 64,
                     'Config': {'Labels': {'com.docker.compose.service': 'app'}},
                     'Mounts': [], 'HostConfig': {'RestartPolicy': {'Name': 'always', 'MaximumRetryCount': 0}},
                     'NetworkSettings': {'Networks': {}}}
        self.rows = [self.item]
        self.b = SimpleNamespace(canonical_mounts=lambda rows: rows)
        self.parity = []
        self.c = SimpleNamespace(ports_from_inspect=lambda _: [],
                                 assert_config_parity=lambda *args, **kwargs: self.parity.append(kwargs))
        self.stack.enter_context(patch.object(M, 'config_metadata', return_value={'synthetic': True}))
        self.stack.enter_context(patch.object(M, 'all_public', side_effect=lambda _: deepcopy(self.current)))
        def read(path, *args, **kwargs):
            self.assertEqual(path, M.HERE / 'activation-intent.json')
            return json.dumps({'nonce': 'synthetic-nonce', 'site': 'bot', 'phase': 'applying'}).encode()
        self.stack.enter_context(patch.object(M, 'read', side_effect=read))
        self.stack.enter_context(patch.object(M.os.path, 'lexists', side_effect=lambda path:
            path == M.HERE / 'activation-intent.json'))
        self.stack.enter_context(patch.object(M, 'compose_prefix', return_value=['SYNTHETIC-COMPOSE']))
        self.commands = []
        def metadata(argv, **kwargs):
            self.commands.append(argv)
            if argv[:3] == ['/usr/bin/docker', 'image', 'inspect']:
                return [{'Id': value} for value in argv[3:]]
            if argv == ['SYNTHETIC-COMPOSE', 'config', '--format', 'json']:
                return {'services': {'app': {}}}
            self.assertEqual(argv[:3], ['/usr/bin/docker', 'inspect', self.current[0]['id']])
            return self.rows
        self.stack.enter_context(patch.object(M, 'private_json', side_effect=metadata))

    def check(self):
        return M.rollback_preflight('bot', self.record, self.original, self.c, self.b)

    def test_unchanged_old_and_owned_stopped_new_consumers_recognized(self):
        self.check()
        self.assertEqual(self.parity[-1], {'managed_override': None, 'running': False})
        self.item.update(Id='c' * 64, Image=self.record['images']['bot'])
        self.current[0].update(id='c' * 64, image=self.record['images']['bot'])
        self.check()
        self.assertEqual(self.parity[-1], {'managed_override': M.HERE / 'security.override.json', 'running': False})

    def test_missing_consumer_only_allowed_under_exact_owned_cut_intent(self):
        self.current.pop(0)
        self.rows = []
        self.check()
        self.assertFalse(any(argv[:2] == ['/usr/bin/docker', 'inspect'] for argv in self.commands))
        with patch.object(M, 'read', return_value=b'{"nonce":"foreign","site":"bot","phase":"applying"}'):
            with self.assertRaisesRegex(RuntimeError, 'rollback_not_owned_activation'): self.check()

    def test_changed_protected_market_container_refuses_before_any_inspect(self):
        self.current[1]['id'] = 'd' * 64
        with self.assertRaisesRegex(RuntimeError, 'rollback_nonconsumer_drift'): self.check()
        self.assertEqual(self.commands, [])

    def test_third_party_image_or_recreated_old_identity_refuses(self):
        for image in ('sha256:' + '4' * 64, 'sha256:' + '1' * 64):
            with self.subTest(image=image):
                self.item.update(Id='c' * 64, Image=image)
                self.current[0].update(id='c' * 64, image=image)
                with self.assertRaisesRegex(RuntimeError, 'rollback_foreign_image_or_identity'): self.check()

    def test_runtime_policy_or_snapshot_drift_refuses(self):
        self.item['HostConfig']['RestartPolicy']['Name'] = 'no'
        with self.assertRaisesRegex(RuntimeError, 'rollback_consumer_runtime_drift'): self.check()
        self.assertEqual(self.parity, [])

    def test_no_mutating_compose_or_docker_commands_are_ever_used(self):
        self.check()
        self.assertTrue(all(argv[1] in {'inspect', 'image'} if argv[0] == '/usr/bin/docker' else
                            argv == ['SYNTHETIC-COMPOSE', 'config', '--format', 'json'] for argv in self.commands))

    def test_failure_before_owned_cut_is_a_verified_noop_not_a_recreation(self):
        with patch.object(M.os.path, 'lexists', return_value=False), patch.object(M, 'baseline', return_value=self.original):
            self.assertFalse(self.check())
        self.assertEqual(self.commands, [])
        with patch.object(M.os.path, 'lexists', return_value=False), patch.object(M, 'baseline', return_value={}):
            with self.assertRaisesRegex(RuntimeError, 'precut_failure_not_unchanged'): self.check()


class SiteControlFiles(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        def denied(*args, **kwargs): raise AssertionError('unexpected command/network')
        for name in ('subprocess.run', 'subprocess.Popen', 'subprocess.call', 'subprocess.check_call',
                     'subprocess.check_output', 'os.system', 'socket.socket.connect', 'socket.getaddrinfo',
                     'socket.create_connection', 'urllib.request.urlopen'):
            self.stack.enter_context(patch(name, side_effect=denied))
        self.temp = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.consumer-io-', dir=SOURCE.parents[2])))
        self.stack.enter_context(patch.object(M, 'HERE', self.temp))
        helper = SOURCE.parent.parent.parent / 'scripts/validate_docker_daemon_maintenance.py'
        spec = importlib.util.spec_from_file_location('_consumer_io_primitives', helper)
        self.b = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.b)

    def test_atomic_lock_is_complete_held_and_only_exact_owned_rollback_can_reacquire(self):
        import fcntl
        fd = M.site_lock(self.b, 'synthetic-nonce', False)
        try:
            lock = self.temp / 'site-operation.lock'
            value = json.loads(lock.read_bytes())
            self.assertEqual(value['nonce'], 'synthetic-nonce')
            self.assertEqual(value['inode'], os.fstat(fd).st_ino)
            with self.assertRaisesRegex(RuntimeError, 'site_operation_requires_owned_recovery'):
                M.site_lock(self.b, 'synthetic-nonce', False)
            with self.assertRaises(BlockingIOError): M.site_lock(self.b, 'synthetic-nonce', True)
            with self.assertRaisesRegex(RuntimeError, 'foreign_site_operation'):
                M.site_lock(self.b, 'foreign', True)
        finally: os.close(fd)
        recovered = M.site_lock(self.b, 'synthetic-nonce', True)
        try: M.release_site_lock(recovered, 'synthetic-nonce')
        finally: os.close(recovered)
        self.assertFalse(lock.exists())

    def test_interrupt_before_full_lock_publication_leaves_no_canonical_blocker(self):
        with patch.object(self.b, 'write_all', side_effect=RuntimeError('synthetic short-write interruption')):
            with self.assertRaises(RuntimeError): M.site_lock(self.b, 'nonce', False)
        self.assertEqual(list(self.temp.iterdir()), [])

    def test_json_publication_never_clobbers_foreign_existing_bytes(self):
        target = self.temp / 'result.json'
        M.write(self.b, target, {'complete': True}, exclusive=True)
        with self.assertRaises(self.b.Refusal): M.write(self.b, target, {'complete': False}, exclusive=True)
        self.assertEqual(json.loads(target.read_bytes()), {'complete': True})

    def test_read_rejects_symlink_hardlink_and_unsafe_mode_without_content_diagnostic(self):
        target = self.temp / 'private.json'
        target.write_bytes(b'never-emit-synthetic-value'); target.chmod(0o600)
        alias = self.temp / 'alias'; alias.symlink_to(target)
        with self.assertRaises(RuntimeError): M.read(alias)
        alias.unlink(); os.link(target, alias)
        with self.assertRaises(RuntimeError): M.read(target)
        alias.unlink(); target.chmod(0o644)
        with self.assertRaises(RuntimeError) as caught: M.read(target)
        self.assertNotIn('never-emit', str(caught.exception))
        self.assertEqual(M.read(target, public=True), b'never-emit-synthetic-value')

    def test_native_worker_requires_same_real_pid_flock_on_all_three_current_inodes(self):
        marker, source, reservation = (self.temp / name for name in ('marker', 'source', 'reservation'))
        for path in (marker, source, reservation): path.touch(mode=0o600)
        record = {'nonce': 'synthetic-nonce', 'source_commit': 'a' * 40}
        owner = {'schema': 'security_consumer_release_guard/1', 'operation_id': M.OPERATION,
                 'nonce': record['nonce'], 'source_commit': record['source_commit'], 'pid': 123,
                 'inode': reservation.lstat().st_ino}
        reservation.write_text(json.dumps(owner))
        def row(path, pid=123):
            info = path.lstat()
            return f'1: FLOCK ADVISORY WRITE {pid} {os.major(info.st_dev):02x}:{os.minor(info.st_dev):02x}:{info.st_ino} 0 EOF'
        with patch.object(self.b, 'MARKER', marker), patch.object(self.b, 'SOURCE', source), \
                patch.object(self.b, 'RESERVATION', reservation):
            def command(argv):
                self.assertEqual(argv, ['/usr/bin/cat', '/proc/locks'])
                return '\n'.join(row(path) for path in (marker, source, reservation))
            with patch.object(self.b, 'command', side_effect=command): M.native_coordinator_owner(record, self.b)
            with patch.object(self.b, 'command', return_value='\n'.join(row(path) for path in (marker, reservation))):
                with self.assertRaisesRegex(RuntimeError, 'native_coordinator_fences_not_held'):
                    M.native_coordinator_owner(record, self.b)
            with patch.object(self.b, 'command', return_value='\n'.join(row(path, 124) for path in (marker, source, reservation))):
                with self.assertRaisesRegex(RuntimeError, 'native_coordinator_fences_not_held'):
                    M.native_coordinator_owner(record, self.b)


if __name__ == '__main__': unittest.main()
