"""One exact reserved operation, simulated only; unmocked host calls denied."""
from contextlib import contextmanager, ExitStack, redirect_stdout
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import importlib.util
import io
import json
import fcntl
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

REPO = Path(__file__).resolve().parents[1]
PATH = REPO / 'deploy/maintenance/reboot_owned_resume.py'
SPEC = importlib.util.spec_from_file_location('_owned_resume_test', PATH)
R = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(R)


class OwnedResumeTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        def denied(*args, **kwargs): raise AssertionError('unmocked host command or network')
        for name in ('subprocess.run', 'subprocess.Popen', 'subprocess.call', 'subprocess.check_call',
                     'subprocess.check_output', 'os.system', 'socket.socket.connect', 'socket.getaddrinfo',
                     'socket.create_connection', 'urllib.request.urlopen'):
            self.stack.enter_context(patch(name, side_effect=denied))
        self.stack.enter_context(patch.object(R.signal, 'alarm', side_effect=denied))
        self.stack.enter_context(patch.object(R.signal, 'pause', side_effect=denied))
        self.temp = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.owned-resume-', dir=REPO)))
        reservation = self.temp / 'reservation'; reservation.write_bytes(b'owned reservation'); reservation.chmod(0o600)
        marker, source = self.temp / 'marker', self.temp / 'source'
        for path in (marker,source): path.touch(mode=0o600)
        self.old = {'schema': 'security_kernel_reboot_state/1', 'phase': 'reserved',
                    'owner_pid': 987654321, 'current_boot_id': 'oldboot', 'recorded_at_utc': 'unchanged'}
        self.live_state = deepcopy(self.old)
        self.plan = {'nonce': R.NONCE, 'source_revision': R.SOURCE, 'operation_id': 'security-kernel-reboot-20261023-02',
                     'before_boot_id': 'oldboot', 'units': [{'name': 'example.service', 'enabled': 'enabled'}]}
        self.calls = []
        self.documents = {}
        def document(path):
            if path == self.g.TOKEN: return {'owned': True}
            if path == self.g.RESULT: return {'status': 'completed', 'nonce': R.NONCE, 'manifest_sha256': R.DIGEST}
            return self.documents[path]
        def command(argv):
            self.assertEqual(argv, ['/usr/bin/systemctl', 'reboot'])
            self.calls.append('reboot')
            raise RuntimeError('SIMULATED_SHUTDOWN')
        def identity(info): return (info.st_dev, info.st_ino, info.st_mode, info.st_size)
        b = SimpleNamespace(RESERVATION=reservation, MARKER=marker, SOURCE=source, PENDING=self.temp / 'pending',
             document=Mock(side_effect=document), read=Mock(return_value=b'owned gate'),
             runtime=Mock(side_effect=lambda *args: self.calls.append('quiesced-runtime')),
             identity=identity, command=Mock(side_effect=command))
        def atomic(path, value, exclusive=False):
            self.assertTrue(path.is_relative_to(self.temp), 'non-fixture mutation')
            self.calls.append(('atomic', path.name, deepcopy(value), exclusive))
            if path == self.g.STATE: self.live_state = deepcopy(value)
            else:
                self.assertNotIn(path, self.documents)
                self.documents[path] = deepcopy(value)
        self.g = SimpleNamespace(B=b, UNITS={'example.service', 'example.timer'}, OPERATION=self.plan['operation_id'],
              STATE=self.temp / 'state', TOKEN=self.temp / 'token', RESULT=self.temp / 'result',
              GOLDEN_INHIBITOR=self.temp / 'golden', GOLDEN_INHIBITOR_BYTES=b'owned gate',
              MANIFEST=self.temp / 'manifest', CUTOFF='2026-10-24T00:30:00Z',
              load=Mock(return_value=self.plan), invariants=Mock(),
              state=Mock(side_effect=lambda *args: deepcopy(self.live_state)), boot_id=Mock(return_value='oldboot'),
              own_reservation=Mock(return_value={'inode': reservation.lstat().st_ino}),
              token_payload=Mock(return_value={'owned': True}),
              properties=Mock(return_value={'UnitFileState': 'disabled', 'ActiveState': 'inactive'}),
              lock_current=Mock(), atomic=Mock(side_effect=atomic),
              phase_check=Mock(side_effect=lambda *args: self.calls.append('original-phase-check')),
              advance=Mock(side_effect=lambda *args: self.calls.append(('advance', args[-1]))),
              execute=Mock(), main=Mock(return_value=0), now=lambda: datetime(2026,10,23,22,tzinfo=timezone.utc),
              deadline=lambda value: datetime.fromisoformat(value.replace('Z','+00:00')))
        @contextmanager
        def refs(g):
            self.calls.append('refs-enter')
            try: yield
            finally: self.calls.append('refs-exit')
        self.stack.enter_context(patch.object(R, 'pinned_units', side_effect=refs))
        original_exists = os.path.lexists
        self.stack.enter_context(patch.object(R.os.path, 'lexists', side_effect=lambda path:
            False if str(path).startswith('/proc/') else path in self.documents or original_exists(path)))

    def intent(self):
        return {'schema': 'security_kernel_reboot_owned_continuation/1', 'operation_id': self.g.OPERATION,
                'nonce': R.NONCE, 'manifest_sha256': R.DIGEST, 'source_revision': R.SOURCE,
                'guard_sha256': R.GUARD_SHA, 'before_boot_id': 'oldboot',
                'reservation_inode': self.g.B.RESERVATION.lstat().st_ino}

    def test_default_check_is_readonly_full_double_proof_with_same_inode_flock(self):
        before = self.g.B.RESERVATION.read_bytes()
        R.run(self.g, 'check')
        self.assertEqual(self.g.invariants.call_count, 2)
        self.assertEqual(self.g.B.runtime.call_count, 2)
        self.g.atomic.assert_not_called(); self.g.advance.assert_not_called(); self.g.B.command.assert_not_called()
        self.assertEqual(self.g.B.RESERVATION.read_bytes(), before)
        self.assertEqual(self.calls[0], 'refs-enter'); self.assertEqual(self.calls[-1], 'refs-exit')

    def test_check_holds_all_three_real_fixture_flocks_without_any_reboot_or_write(self):
        def locks(stack):
            for path in (self.g.B.MARKER,self.g.B.SOURCE):
                fd=os.open(path,os.O_RDWR); stack.callback(os.close,fd)
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        count=0
        def runtime(*args):
            nonlocal count
            count+=1
            if count==2:
                for path in (self.g.B.MARKER,self.g.B.SOURCE,self.g.B.RESERVATION):
                    fd=os.open(path,os.O_RDWR)
                    try:
                        with self.assertRaises(BlockingIOError): fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    finally: os.close(fd)
        self.g.lock_current.side_effect=locks; self.g.B.runtime.side_effect=runtime
        R.run(self.g,'check')
        self.assertEqual(count,2)
        self.g.atomic.assert_not_called(); self.g.B.command.assert_not_called()

    def test_resume_proves_then_publishes_intent_changes_only_state_pid_checks_and_reboots_once(self):
        with self.assertRaisesRegex(RuntimeError, 'SIMULATED_SHUTDOWN'): R.run(self.g, 'resume', R.CONFIRM)
        publications = [call for call in self.calls if isinstance(call, tuple) and call[0] == 'atomic']
        self.assertTrue(publications[0][-1]); self.assertEqual(publications[0][2]['nonce'], R.NONCE)
        expected = deepcopy(self.old); expected['owner_pid'] = os.getpid()
        self.assertEqual(publications[1][2], expected)
        self.assertLess(self.calls.index('original-phase-check'), self.calls.index(('advance','reboot_requested')))
        self.assertEqual(self.calls.count('reboot'), 1)
        self.g.B.command.assert_called_once_with(['/usr/bin/systemctl','reboot'])

    def test_wrong_confirmation_never_publishes_or_reboots(self):
        with self.assertRaisesRegex(RuntimeError, 'explicit_apply_or_cutoff_invalid'): R.run(self.g,'resume','wrong')
        self.g.atomic.assert_not_called(); self.g.B.command.assert_not_called()

    def test_live_previous_pid_wrong_boot_phase_nonce_or_result_refuses(self):
        for key, value in (('phase','reboot_requested'), ('current_boot_id','otherboot')):
            previous = self.live_state[key]; self.live_state[key] = value
            with self.subTest(key=key), self.assertRaises(RuntimeError): R.run(self.g,'check')
            self.live_state[key] = previous
        with patch.object(R.os.path,'lexists',return_value=True), self.assertRaises(RuntimeError): R.run(self.g,'check')
        self.g.atomic.assert_not_called(); self.g.B.command.assert_not_called()
        self.plan['nonce'] = 'foreign'
        with self.assertRaisesRegex(RuntimeError,'wrong_fixed_manifest'): R.run(self.g,'check')

    def test_invariant_runtime_enablement_and_token_failures_cannot_transfer_owner(self):
        for member in ('invariants','properties'):
            with self.subTest(member=member), patch.object(self.g,member,side_effect=RuntimeError('original refusal')):
                with self.assertRaises(RuntimeError): R.run(self.g,'resume',R.CONFIRM)
        with patch.object(self.g.B,'runtime',side_effect=RuntimeError('original runtime refusal')):
            with self.assertRaises(RuntimeError): R.run(self.g,'resume',R.CONFIRM)
        with patch.object(self.g.B,'document',return_value={'foreign':True}):
            with self.assertRaisesRegex(RuntimeError,'owned_quiescence_gate_changed'): R.run(self.g,'resume',R.CONFIRM)
        with patch.object(self.g,'properties',return_value={'UnitFileState':'disabled','ActiveState':'active'}):
            with self.assertRaisesRegex(RuntimeError,'unit_not_quiescent'): R.run(self.g,'resume',R.CONFIRM)
        self.g.atomic.assert_not_called(); self.g.B.command.assert_not_called()

    def test_actual_result_or_reservation_identity_drift_refuses_without_reboot(self):
        self.g.RESULT.touch(mode=0o600)
        with self.assertRaisesRegex(RuntimeError,'result_or_other_release_exists'): R.run(self.g,'check')
        self.g.RESULT.unlink()
        self.g.own_reservation.return_value={'inode':self.g.B.RESERVATION.lstat().st_ino+1}
        with self.assertRaisesRegex(RuntimeError,'reservation_inode_changed'): R.run(self.g,'resume',R.CONFIRM)
        self.g.atomic.assert_not_called(); self.g.B.command.assert_not_called()

    def test_failed_original_phase_check_retains_intent_and_never_requests_reboot_or_retry(self):
        self.g.phase_check.side_effect=RuntimeError('unchanged original check refused')
        with self.assertRaisesRegex(RuntimeError,'unchanged original check refused'): R.run(self.g,'resume',R.CONFIRM)
        self.assertIn(R.continuation_path(self.g),self.documents)
        self.g.B.command.assert_not_called(); self.g.advance.assert_not_called()
        with self.assertRaises(RuntimeError): R.run(self.g,'resume',R.CONFIRM)
        self.g.B.command.assert_not_called()

    def test_guard_executes_exact_verified_buffer_never_reopens_after_hash(self):
        path=self.temp/'guard.py'; good=b"PROOF = 'verified'\n"; path.write_bytes(good); path.chmod(0o600)
        digest=sha256(good).hexdigest()
        def hash_and_swap(raw):
            result=sha256(raw)
            path.write_bytes(b"raise AssertionError('unverified replacement executed')\n")
            return result
        with patch.object(R,'GUARD',path),patch.object(R,'GUARD_SHA',digest),patch.object(R,'sha256',side_effect=hash_and_swap):
            self.assertEqual(R.guard_module().PROOF,'verified')
        with patch.object(R,'GUARD',path),patch.object(R,'GUARD_SHA',digest):
            with self.assertRaisesRegex(RuntimeError,'canonical_guard_changed'): R.guard_module()

    def test_existing_continuation_is_not_an_automatic_retry(self):
        self.documents[R.continuation_path(self.g)] = self.intent()
        with self.assertRaisesRegex(RuntimeError,'continuation_already_attempted'): R.run(self.g,'resume',R.CONFIRM)
        self.g.atomic.assert_not_called(); self.g.B.command.assert_not_called()

    def test_restore_wraps_exact_original_execute_only_after_new_boot_and_phase(self):
        self.documents[R.continuation_path(self.g)] = self.intent()
        self.g.boot_id.return_value = 'newboot'; self.live_state['phase']='reboot_requested'
        R.run(self.g,'restore',R.CONFIRM)
        self.g.execute.assert_called_once_with(self.plan,R.DIGEST,before=False)
        self.assertEqual(self.calls, ['refs-enter','refs-exit'])
        self.g.B.command.assert_not_called()

    def test_restore_cannot_reboot_again_or_accept_same_boot_wrong_inode_or_missing_intent(self):
        with self.assertRaises(KeyError): R.run(self.g,'restore',R.CONFIRM)
        self.documents[R.continuation_path(self.g)] = self.intent()
        with self.assertRaises(RuntimeError): R.run(self.g,'restore',R.CONFIRM)
        self.g.boot_id.return_value='newboot'; self.live_state['phase']='reboot_requested'
        self.documents[R.continuation_path(self.g)]['reservation_inode'] += 1
        with self.assertRaises(RuntimeError): R.run(self.g,'restore',R.CONFIRM)
        self.g.execute.assert_not_called(); self.g.B.command.assert_not_called()

    def test_cleanup_calls_unchanged_original_main_under_refs_and_restores_argv(self):
        self.documents[R.continuation_path(self.g)] = self.intent()
        self.g.boot_id.return_value='newboot'; self.g.B.RESERVATION.unlink()
        old = sys.argv
        def cleanup():
            self.assertEqual(sys.argv[1:3], ['cleanup-controller','--manifest'])
            self.assertIn(R.DIGEST,sys.argv); self.assertIn('--apply',sys.argv)
            self.assertEqual(self.calls[-1],'refs-enter')
            return 0
        self.g.main.side_effect=cleanup
        R.run(self.g,'cleanup',R.CONFIRM)
        self.assertIs(sys.argv,old)
        self.assertEqual(self.calls[-1],'refs-exit')


class PersistentUnitReferenceTests(unittest.TestCase):
    def setUp(self):
        stack=ExitStack(); self.addCleanup(stack.close)
        def denied(*args,**kwargs): raise AssertionError('unmocked host command or network')
        for name in ('subprocess.run','subprocess.Popen','subprocess.call','subprocess.check_call',
                     'subprocess.check_output','os.system','socket.socket.connect','socket.getaddrinfo',
                     'socket.create_connection','urllib.request.urlopen'):
            stack.enter_context(patch(name,side_effect=denied))

    def test_exact_private_connection_refs_load_without_start_and_close_on_exception(self):
        calls=[]
        class Manager:
            def RefUnit(self,unit,timeout): calls.append(('ref',unit,timeout))
            def UnrefUnit(self,unit,timeout): calls.append(('unref',unit,timeout))
        manager=Manager()
        bus=Mock(); bus.get_object.return_value=object()
        dbus=SimpleNamespace(SystemBus=Mock(return_value=bus),Interface=Mock(return_value=manager))
        with patch.dict(sys.modules,{'dbus':dbus}):
            with self.assertRaisesRegex(RuntimeError,'synthetic'):
                with R.pinned_units(SimpleNamespace(UNITS={'z.service','a.timer'})):
                    self.assertEqual([row[1] for row in calls],['a.timer','docker.service','z.service'])
                    raise RuntimeError('synthetic')
        dbus.SystemBus.assert_called_once_with(private=True)
        self.assertEqual([row[0] for row in calls],['ref']*3+['unref']*3)
        self.assertTrue(all(row[2]==5 for row in calls))
        bus.close.assert_called_once()

    def test_partial_reference_failure_unrefs_only_successful_units_and_closes_private_connection(self):
        manager=Mock(); manager.RefUnit.side_effect=[None,RuntimeError('synthetic RefUnit refusal')]
        bus=Mock(); dbus=SimpleNamespace(SystemBus=Mock(return_value=bus),Interface=Mock(return_value=manager))
        with patch.dict(sys.modules,{'dbus':dbus}),self.assertRaisesRegex(RuntimeError,'synthetic RefUnit refusal'):
            with R.pinned_units(SimpleNamespace(UNITS={'a.service','z.service'})): self.fail('cannot enter')
        manager.UnrefUnit.assert_called_once_with('a.service',timeout=5)
        bus.close.assert_called_once()


if __name__=='__main__': unittest.main()
