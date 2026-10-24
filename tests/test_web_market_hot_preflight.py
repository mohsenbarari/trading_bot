"""No actual Docker, SSH, systemd, env/key access or network."""
from contextlib import ExitStack
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

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('_market_hot_preflight', ROOT / 'deploy/maintenance/web_market_hot_preflight.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class HotPreflight(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for target in ('subprocess.run', 'subprocess.Popen', 'os.system', 'socket.socket.connect',
                       'socket.create_connection', 'socket.getaddrinfo', 'urllib.request.urlopen'):
            self.stack.enter_context(patch(target, side_effect=AssertionError('hermetic only')))
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.hot-preflight-test-', dir=ROOT)))

    def test_only_two_public_sources_match_exact_published_recipe_hashes(self):
        self.assertEqual(M.SOURCE_COMMIT, '43ae1eae96b9bb54b0aaffae53aafcd473c89670')
        self.assertEqual(M.SCRIPTS, {
            'backup_market_pipeline_archive.py': 'c7695e2e9321aed8d39a008cb76fdf229010021e3b74132ac6dc2f82b7dec527',
            'prepare_market_pipeline_release.py': '8952ae8997101960baa3ae598c57d434dab69f86a7ae12ab72a5480a600b3953'})

    def test_stage_is_exact_idempotent_and_does_not_read_any_private_input(self):
        public = {name: (ROOT / 'scripts' / name).read_bytes() for name in M.SCRIPTS}
        destination = self.root / 'new-private-source'
        with patch.object(M, 'ROOT', destination), patch.dict(M.SCRIPTS, {name: sha256(raw).hexdigest() for name, raw in public.items()}):
            M.stage(public)
            before = {p: p.stat().st_ino for p in (destination / 'source/scripts').iterdir()}
            M.stage(public)
            self.assertEqual(before, {p: p.stat().st_ino for p in (destination / 'source/scripts').iterdir()})
            self.assertEqual({p.name for p in destination.iterdir()}, {'source'})
            for path in before:
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            changed = {**public, 'backup_market_pipeline_archive.py': b'changed'}
            with self.assertRaisesRegex(RuntimeError, 'published_public_source_changed'):
                M.stage(changed)

    def test_existing_partial_stage_symlink_or_attempt_is_never_overwritten(self):
        public = {name: (ROOT / 'scripts' / name).read_bytes() for name in M.SCRIPTS}
        destination = self.root / 'new-private-source'
        with patch.object(M, 'ROOT', destination), patch.dict(M.SCRIPTS, {name: sha256(raw).hexdigest() for name, raw in public.items()}):
            M.stage(public)
            attempt = destination / 'attempt.json'
            attempt.write_bytes(b'original failed attempt evidence')
            with self.assertRaisesRegex(RuntimeError, 'existing_diagnostic_attempt'):
                M.stage(public)
            self.assertEqual(attempt.read_bytes(), b'original failed attempt evidence')
        alias = self.root / 'alias'
        alias.symlink_to(destination, target_is_directory=True)
        with patch.object(M, 'ROOT', alias), patch.dict(M.SCRIPTS, {name: sha256(raw).hexdigest() for name, raw in public.items()}), self.assertRaisesRegex(RuntimeError, 'path_redirected'):
            M.stage(public)

    def test_real_create_uses_hot_confirmation_bluegreen_and_original_anchor(self):
        options = {'env_file': '/private/metadata-only.env', 'release_sha': 'a' * 40, 'release_tree': 'b' * 40,
                   'image_id': 'sha256:' + 'c' * 64, 'image_input_signature': 'd' * 64}
        command = M.command({'market_backup': options})
        self.assertEqual(command[:4], ['/usr/bin/python3', '-I', '-B', str(M.ROOT / 'source/scripts/backup_market_pipeline_archive.py')])
        self.assertIn('--allow-running-writers', command)
        self.assertIn('--bluegreen-source-env', command)
        self.assertEqual(command[command.index('--confirm') + 1], 'create-production-market-pipeline-archive-hot-backup')
        self.assertEqual(command[command.index('--env-file') + 1], options['env_file'])
        self.assertEqual(command[4], 'create')
        self.assertNotIn('verify', command)
        self.assertNotIn('--refresh-complete', command)

    def child(self, raw, returncode=0):
        stdout = io.BytesIO(raw)
        child = SimpleNamespace(pid=987654321, stdout=stdout, wait=lambda **k: returncode)
        class Selector:
            active = True
            def register(self, *a):
                pass
            def get_map(self):
                return {1: 1} if self.active else {}
            def select(self, **k):
                return [(SimpleNamespace(fileobj=stdout), 1)]
            def unregister(self, *a):
                self.active = False
            def close(self):
                pass
        return child, Selector()

    def run_output(self, raw, returncode=0):
        child, selector = self.child(raw, returncode)
        with patch.object(M.subprocess, 'Popen', return_value=child) as launch, \
             patch.object(M.selectors, 'DefaultSelector', return_value=selector), \
             patch.object(M.os, 'read', side_effect=lambda fd, n: child.stdout.read(n)), \
             patch.object(child.stdout, 'fileno', return_value=123), patch.object(M.os, 'killpg') as kill:
            value = M.run_cli(['public-cli'], M.time.monotonic() + 10)
        return value, launch, kill, child

    def test_success_output_is_filtered_and_child_group_reaped(self):
        raw = json.dumps({'status': 'pass', 'backup_status': 'PASS', 'database_mutated': False,
                          'services_started': False, 'secrets_disclosed': False, 'arbitrary': 'never print'}).encode() + b'\n'
        value, launch, kill, child = self.run_output(raw)
        self.assertNotIn('arbitrary', value)
        self.assertEqual(value['status'], 'pass')
        self.assertTrue(child.stdout.closed)
        kill.assert_called_once_with(child.pid, M.signal.SIGKILL)
        self.assertEqual(launch.call_args.kwargs['env'], {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C.UTF-8',
            'DOCKER_HOST': 'unix:///var/run/docker.sock'})
        with patch.object(M.resource, 'setrlimit') as limit:
            launch.call_args.kwargs['preexec_fn']()
        limit.assert_called_once_with(M.resource.RLIMIT_FSIZE, (16 * 1024**3, 16 * 1024**3))

    def test_real_cli_static_refusal_survives_without_private_exception_text(self):
        with self.assertRaisesRegex(RuntimeError, '^market_release_source_invalid$'):
            self.run_output(b'{"status":"fail","reason_code":"market_release_source_invalid"}\n', 1)
        with self.assertRaisesRegex(RuntimeError, '^real_backup_cli_failed$'):
            self.run_output(b'{"status":"fail","reason_code":"private path or secret must not print"}\n', 1)

    def test_output_bound_kills_only_owned_child_before_large_output_accumulation(self):
        with self.assertRaisesRegex(RuntimeError, 'hot_backup_diagnostics_exceeded'):
            self.run_output(b'x' * 2097153)

    def test_static_errors_never_render_arbitrary_values(self):
        self.assertEqual(M.static_code(RuntimeError('root_free_below_44gib')), 'root_free_below_44gib')
        for error in (ValueError('private'), OSError('/private/value'), RuntimeError('private value'), RuntimeError('x' * 97)):
            self.assertEqual(M.static_code(error), 'hot_backup_preflight_refused')

    def test_default_holds_three_original_flocks_without_pid_changes_or_backup(self):
        marker, source, reservation = (self.root / name for name in ('marker', 'source', 'reservation'))
        for path in (marker, source, reservation):
            path.write_text(json.dumps({'pid': M.OWNER}))
            path.chmod(0o600)
        before = [p.read_bytes() for p in (marker, source, reservation)]
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        C = SimpleNamespace(P=SimpleNamespace(MARKER=marker, RESERVATION=reservation, document=lambda *a: {}),
            paths=lambda p: (self.root / 'journal', source),
            B=SimpleNamespace(read=lambda p, **k: p.read_bytes(), identity=identity))
        def proof(c, p, fds):
            for path in (marker, source, reservation):
                fd = os.open(path, os.O_RDONLY)
                try:
                    with self.assertRaises(BlockingIOError):
                        M.fcntl.flock(fd, M.fcntl.LOCK_EX | M.fcntl.LOCK_NB)
                finally:
                    os.close(fd)
        with patch.object(M, 'controller', return_value=C), patch.object(M, 'owner_proof', side_effect=proof), \
             patch.object(M, 'stage'), patch.object(M, 'TARGET', self.root / 'absent-backup'), patch.object(M, 'run_cli') as backup:
            self.assertEqual(M.execute('stage', {}), {'status': 'public_scripts_staged_checked', 'backup_performed': False})
        self.assertEqual(backup.call_count, 1)
        self.assertEqual(backup.call_args.args[0][-1], '--help')
        self.assertTrue(backup.call_args.kwargs['expected_help'])
        self.assertEqual(before, [p.read_bytes() for p in (marker, source, reservation)])

    def test_hot_refuses_low_root_capacity_before_any_real_create(self):
        marker, source, reservation = (self.root / name for name in ('marker', 'source', 'reservation'))
        for path in (marker, source, reservation):
            path.write_text(json.dumps({'pid': M.OWNER}))
            path.chmod(0o600)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        C = SimpleNamespace(P=SimpleNamespace(MARKER=marker, RESERVATION=reservation, document=lambda *a: {}),
            paths=lambda p: (self.root / 'journal', source),
            B=SimpleNamespace(read=lambda p, **k: p.read_bytes(), identity=identity))
        with patch.object(M, 'controller', return_value=C), patch.object(M, 'owner_proof'), patch.object(M, 'stage'), \
             patch.object(M, 'TARGET', self.root / 'absent-backup'), patch.object(M, 'run_cli') as backup, \
             patch.object(M.os, 'statvfs', return_value=SimpleNamespace(f_bavail=44 * 1024**3 - 1, f_frsize=1)), \
             self.assertRaisesRegex(RuntimeError, 'root_free_below_44gib'):
            M.execute('hot', {})
        self.assertEqual(backup.call_count, 1)
        self.assertTrue(backup.call_args.kwargs['expected_help'])

    def test_launcher_log_is_outside_closed_staging_inventory_and_check_mode_ends_early(self):
        import yaml
        play = yaml.safe_load((ROOT / 'deploy/maintenance/web_market_hot_preflight.yml').read_text())[0]
        self.assertEqual(play['vars']['market_hot_action'], 'stage')
        self.assertEqual(play['tasks'][1]['when'], 'ansible_check_mode')
        argv = play['tasks'][-1]['ansible.builtin.command']['argv']
        output = next(value for value in argv if value.startswith('--property=StandardOutput=append:'))
        path = Path(output.split('append:', 1)[1])
        self.assertNotIn(M.ROOT, path.parents)
        self.assertIn('--expand-environment=no', argv)
        self.assertTrue(any('rstrip=false' in arg for arg in argv))

    def hot_fixture(self):
        marker, source, reservation = (self.root / name for name in ('marker', 'source', 'reservation'))
        for path in (marker, source, reservation):
            path.write_text(json.dumps({'pid': M.OWNER}))
            path.chmod(0o600)
        plan = {'containers': [{'id': 'db', 'name': '/public-market-database-1'}],
                'datastore_probes': [{'id': 'db', 'user': 'synthetic', 'database': 'synthetic'}]}
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        writes = []
        C = SimpleNamespace(P=SimpleNamespace(MARKER=marker, RESERVATION=reservation, document=lambda *a: plan),
            paths=lambda p: (self.root / 'journal', source),
            B=SimpleNamespace(read=lambda p, **k: p.read_bytes(), identity=identity, command=Mock(return_value='1024')),
            write_json=lambda path, value, **k: writes.append(dict(value)))
        self.stack.enter_context(patch.object(M, 'controller', return_value=C))
        self.stack.enter_context(patch.object(M, 'owner_proof'))
        self.stack.enter_context(patch.object(M, 'stage'))
        self.stack.enter_context(patch.object(M, 'TARGET', self.root / 'absent-backup'))
        self.stack.enter_context(patch.object(M, 'ROOT', self.root))
        self.stack.enter_context(patch.object(M, 'command', return_value=['public-cli', 'create']))
        self.stack.enter_context(patch.object(M.os, 'statvfs', return_value=SimpleNamespace(f_bavail=44 * 1024**3, f_frsize=1)))
        return writes, (marker, source, reservation)

    def test_real_hot_success_is_create_only_and_never_quiesced_upgrade_acceptance(self):
        writes, paths = self.hot_fixture()
        before = [p.read_bytes() for p in paths]
        with patch.object(M, 'run_cli', return_value={'status': 'pass'}) as cli:
            result = M.execute('hot', {})
        self.assertEqual(cli.call_count, 2)  # Import-only help, then one real CREATE; no VERIFY.
        self.assertEqual(cli.call_args.args[0], ['public-cli', 'create'])
        self.assertEqual(result['status'], 'hot_create_restore_smoke_passed')
        self.assertFalse(result['quiesced_upgrade_backup_accepted'])
        self.assertFalse(result['completion_unverified'])
        self.assertTrue(writes[-1]['real_create_restore_cleanup'])
        self.assertEqual(before, [p.read_bytes() for p in paths])

    def test_actual_cli_failure_preserves_all_fences_and_requires_cleanup_proof(self):
        writes, paths = self.hot_fixture()
        before = [p.read_bytes() for p in paths]
        with patch.object(M, 'run_cli', side_effect=[{'status': 'import_passed'}, RuntimeError('market_release_source_invalid')]), \
             self.assertRaisesRegex(RuntimeError, '^market_release_source_invalid$'):
            M.execute('hot', {})
        self.assertEqual(writes[-1]['status'], 'failed')
        self.assertTrue(writes[-1]['completion_unverified'])
        self.assertTrue(writes[-1]['restore_cleanup_must_be_proven'])
        self.assertEqual(before, [p.read_bytes() for p in paths])


if __name__ == '__main__':
    unittest.main()
