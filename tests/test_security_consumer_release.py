"""Finite coordinator choreography, with unconditional host/network denial."""
from contextlib import ExitStack
from datetime import datetime, timezone
from hashlib import sha256
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
PATH = REPO / 'deploy/maintenance/security_consumer_release.py'
SPEC = importlib.util.spec_from_file_location('_security_consumer_release_test', PATH)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class ConsumerCoordinator(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        def denied(*args, **kwargs): raise AssertionError('unexpected host command/network')
        for name in ('subprocess.run', 'subprocess.Popen', 'subprocess.call', 'subprocess.check_call',
                     'subprocess.check_output', 'os.system', 'socket.socket.connect', 'socket.getaddrinfo',
                     'socket.create_connection', 'urllib.request.urlopen'):
            self.stack.enter_context(patch(name, side_effect=denied))
        self.temp = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.consumers-', dir=REPO)))
        marker, source, reservation = (self.temp / name for name in ('marker', 'source', 'reservation'))
        marker.write_bytes(b'old authority remains unchanged\n'); marker.chmod(0o600)
        source.touch(mode=0o600)
        self.before = marker.read_bytes()
        def identity(info): return (info.st_dev, info.st_ino, info.st_mode, info.st_uid,
                                   info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        def public_document(path):
            self.assertTrue(path.is_relative_to(self.temp))
            return json.loads(path.read_bytes())
        def publish(temporary, target):
            self.assertTrue(Path(temporary).is_relative_to(self.temp) and target.is_relative_to(self.temp))
            self.assertFalse(target.exists())
            os.rename(temporary, target)  # Fixture only; production uses reviewed renameat2.
        def write(fd, raw):
            while raw: raw = raw[os.write(fd, raw):]
        self.b = SimpleNamespace(MARKER=marker, SOURCE=source, RESERVATION=reservation,
                                 identity=identity, document=public_document,
                                 write_all=write, publish_noreplace=publish,
                                 private_parent=lambda path: self.assertTrue(path.is_relative_to(self.temp)))
        self.record = {'operation_id': 'synthetic-operation', 'nonce': 'synthetic-nonce', 'source_commit': 'a' * 40}
        self.worker = SimpleNamespace(native=lambda *args: None, CONFIRMATION='EXPLICIT',
                                      CUTOFF=datetime(2099, 1, 1, tzinfo=timezone.utc))
        self.source = self.stack.enter_context(patch.object(M, 'source_binding'))
        self.controls = self.stack.enter_context(patch.object(M, 'control_binding'))
        original_stat = Path.lstat
        def lstat(path, *args, **kwargs):
            if path == Path('/root/.ssh/id_ed25519_iran'):
                return SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=0, st_nlink=1)
            self.assertTrue(path.is_relative_to(self.temp), 'non-fixture lstat')
            return original_stat(path, *args, **kwargs)
        self.stack.enter_context(patch.object(Path, 'lstat', lstat))
        original_open = os.open
        def open_fixture(path, *args, **kwargs):
            self.assertTrue(Path(path).is_relative_to(self.temp), 'non-fixture control open')
            return original_open(path, *args, **kwargs)
        self.stack.enter_context(patch.object(M.os, 'open', side_effect=open_fixture))
        self.calls = []
        self.fail = None
        def invoke(action, sites, *args):
            self.calls.append((action, sites))
            return 1 if self.fail == (action, sites) else 0
        self.stack.enter_context(patch.object(M, 'invoke', side_effect=invoke))

    def run_action(self, action='apply', confirmation='EXPLICIT'):
        return M.run(action, self.record, self.temp / 'acceptance', 'b' * 64,
                     self.worker, None, self.b, confirmation)

    def test_default_check_never_creates_reservation_or_mutates_marker(self):
        self.assertEqual(self.run_action('check', None), 'checked')
        self.assertEqual(self.calls, [('check', ('web', 'bot'))])
        self.assertFalse(self.b.RESERVATION.exists())
        self.assertEqual(self.b.MARKER.read_bytes(), self.before)

    def test_web_then_native_apply_and_both_postchecks_under_same_owned_fences(self):
        self.assertEqual(self.run_action(), 'completed')
        self.assertEqual(self.calls, [('check', ('web', 'bot')), ('snapshot', ('web', 'bot')),
                                      ('apply', ('web',)), ('apply', ('bot',)), ('verify', ('web', 'bot'))])
        self.assertFalse(self.b.RESERVATION.exists())
        self.assertEqual(self.b.MARKER.read_bytes(), self.before)

    def test_native_failed_cut_rolls_back_only_attempted_hosts_in_reverse_order(self):
        self.fail = ('apply', ('bot',))
        self.assertEqual(self.run_action(), 'rolled_back')
        self.assertEqual(self.calls[-2:], [('rollback', ('bot',)), ('rollback', ('web',))])
        self.assertFalse(self.b.RESERVATION.exists())

    def test_web_failed_cut_never_attempts_native_activation(self):
        self.fail = ('apply', ('web',))
        self.assertEqual(self.run_action(), 'rolled_back')
        self.assertNotIn(('apply', ('bot',)), self.calls)
        self.assertEqual(self.calls[-1], ('rollback', ('web',)))

    def test_initial_preflight_failure_has_no_cut_and_retains_owned_reservation(self):
        self.fail = ('check', ('web', 'bot'))
        with self.assertRaisesRegex(RuntimeError, 'consumer_preflight_failed'): self.run_action()
        self.assertEqual(self.calls, [('check', ('web', 'bot'))])
        self.assertTrue(self.b.RESERVATION.exists())
        self.assertEqual(self.b.MARKER.read_bytes(), self.before)

    def test_foreign_or_crashed_reservation_is_never_adopted(self):
        self.b.RESERVATION.write_bytes(b'foreign ownership\n')
        with self.assertRaisesRegex(RuntimeError, 'existing_private_primary_operation'): self.run_action()
        self.assertEqual(self.b.RESERVATION.read_bytes(), b'foreign ownership\n')
        self.assertEqual(self.calls, [])

    def test_implicit_apply_or_wrong_confirmation_never_reserves(self):
        with self.assertRaisesRegex(RuntimeError, 'explicit_scoped_apply_required'): self.run_action(confirmation=None)
        self.assertFalse(self.b.RESERVATION.exists())
        self.assertEqual(self.calls, [])

    def test_unsafe_or_redirected_reservation_parent_refuses_before_temp_creation(self):
        spec = importlib.util.spec_from_file_location('_consumer_parent_primitives', REPO / 'scripts/validate_docker_daemon_maintenance.py')
        primitives = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(primitives)
        parent = self.temp / 'real-control'; parent.mkdir(mode=0o700)
        alias = self.temp / 'redirected-control'; alias.symlink_to(parent, target_is_directory=True)
        for destination, code in ((alias / 'reservation', 'redirected_path'), (parent / 'reservation', 'unsafe_parent')):
            if destination.parent == parent: parent.chmod(0o777)
            with self.subTest(code=code), patch.object(self.b, 'RESERVATION', destination), \
                    patch.object(self.b, 'private_parent', side_effect=primitives.private_parent), \
                    patch.object(M.tempfile, 'mkstemp', side_effect=AssertionError('must not create')):
                with self.assertRaisesRegex(primitives.Refusal, code): self.run_action()
            self.assertFalse(destination.exists())
        self.assertEqual(self.calls, [])

    def test_stage_copies_public_assets_only_without_application_activation(self):
        self.assertEqual(self.run_action('stage'), 'public_assets_staged')
        self.assertEqual(self.calls, [('stage', ('web', 'bot'))])
        self.assertFalse(self.b.RESERVATION.exists())

    def test_cli_pinned_acceptance_is_verified_before_loading_any_derived_module(self):
        from unittest.mock import Mock
        raw = b'{"control_sources":{"security_consumer_contract.py":"untrusted","security_consumer_site.py":"untrusted"}}'
        bootstrap = SimpleNamespace(read=Mock(return_value=raw), decode=json.loads)
        with patch.object(M, 'module', return_value=bootstrap) as modules, \
                patch.object(sys, 'argv', ['release', '--acceptance', str(self.temp / 'acceptance'),
                                          '--acceptance-sha256', '0' * 64]):
            with self.assertRaisesRegex(RuntimeError, 'acceptance_bytes_changed'): M.main()
        self.assertEqual(modules.call_count, 1)  # Only the independently pinned helper.

    def test_second_acceptance_read_cannot_change_executable_authority_or_validated_record(self):
        from contextlib import redirect_stdout
        from unittest.mock import Mock
        raw = json.dumps({'control_sources': {'security_consumer_contract.py': 'a' * 64,
                                              'security_consumer_site.py': 'b' * 64}}).encode()
        bootstrap = SimpleNamespace(read=Mock(side_effect=[raw, b'foreign replacement']), decode=json.loads)
        record = {'operation_id': 'synthetic', 'nonce': 'synthetic-nonce'}
        worker = SimpleNamespace(artifact=Mock(return_value=record))
        with patch.object(M, 'module', side_effect=[bootstrap, object(), worker]) as modules, \
                patch.object(M, 'run', return_value='checked'), \
                patch.object(sys, 'argv', ['release', '--acceptance', str(self.temp / 'acceptance'),
                                          '--acceptance-sha256', sha256(raw).hexdigest()]), redirect_stdout(io.StringIO()):
            self.assertEqual(M.main(), 0)
        bootstrap.read.assert_called_once()
        self.assertEqual(worker.artifact.call_args.kwargs, {'raw': raw})
        self.assertEqual(modules.call_args_list[1].args[1], 'a' * 64)
        self.assertEqual(modules.call_args_list[2].args[1], 'b' * 64)


if __name__ == '__main__': unittest.main()
