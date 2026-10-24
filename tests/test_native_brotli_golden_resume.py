"""Golden-only continuation fixtures; all native/network commands denied."""
from contextlib import ExitStack
from datetime import datetime, timezone
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('golden_resume_fixture',ROOT/'deploy/maintenance/brotli_native_golden_resume.py')
R=importlib.util.module_from_spec(spec);spec.loader.exec_module(R)

class GoldenResume(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        for name in ('subprocess.Popen','subprocess.run','os.system','socket.socket','socket.create_connection','ctypes.CDLL'):
            self.stack.enter_context(patch(name,side_effect=AssertionError('native_forbidden')))
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.golden-resume-',dir=ROOT)))
        self.parent=self.root/'parent.json';self.target=self.root/'next'
        self.first,self.second=('coin-public-market-telegram.service','golden_bot.service')
        self.units={self.first:{'MainPID':'8','ActiveState':'active'},self.second:{'MainPID':'9','ActiveState':'active'}}
        self.prior={'schema':'native_brotli_consumer_refresh/1','phase':'partial_refresh_incident_open',
            'completed_units':{},'current_unit':self.first,'native_brotli_runtime_verified':False,
            'security_transition_completed':False,'before_units':{self.first:{'MainPID':'7'},self.second:self.units[self.second]},
            'before_processes':{'7':{'pid':7,'start_ticks':70}},'requested_at_utc':'2026-10-24T11:05:41+00:00',
            'requested_monotonic_us':100,'window_end_utc':'2026-10-24T11:15:14+00:00'}
        self.parent.write_text(json.dumps(self.prior));self.parent.chmod(0o600)
        self.stack.enter_context(patch.object(R,'PARENT',self.parent))
        self.stack.enter_context(patch.object(R,'PARENT_SHA',sha256(self.parent.read_bytes()).hexdigest()))
        self.stack.enter_context(patch.object(R,'ROOT',self.target))
        self.N=SimpleNamespace(read=lambda p:p.read_bytes(),identity=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns),
                               command=Mock(return_value=''),write=Mock())
        self.M=SimpleNamespace(UNITS=(self.first,self.second),ROOT=self.root/'old',
            before_state=Mock(return_value=(None,None,None,self.units,None,{8:{'patched':True},9:{'patched':False}})),
            start_ticks=Mock(side_effect=FileNotFoundError),module_is_patched=lambda p:p['patched'],
            journal_ready=Mock(return_value=True),heartbeat_ready=Mock(return_value=True),
            utc=lambda s:datetime.fromisoformat(s.replace('Z','+00:00')),action=Mock())
        self.window=SimpleNamespace(start=datetime(2026,10,24,12,tzinfo=timezone.utc))

    def test_check_has_no_action_journal_or_restart(self):
        before=self.parent.read_bytes();result=R.execute(self.M,self.N,'check')
        self.assertEqual(result['status'],'golden_resume_checked');self.assertFalse(result['runtime_changed'])
        self.M.action.assert_not_called();self.N.write.assert_not_called()
        self.assertEqual(self.parent.read_bytes(),before);self.assertFalse(self.target.exists())

    def test_parent_drift_refuses_before_any_native_observation(self):
        self.parent.write_text('{}')
        with self.assertRaisesRegex(RuntimeError,'resume_parent_changed'):R.preflight(self.M,self.N)
        self.M.before_state.assert_not_called()

    def test_collector_requires_both_real_marker_and_fresh_heartbeat(self):
        for name in ('journal_ready','heartbeat_ready'):
            probe=getattr(self.M,name);probe.return_value=False
            with self.assertRaisesRegex(RuntimeError,'resume_collector_not_ready'):R.execute(self.M,self.N,'refresh',self.window)
            probe.return_value=True
        self.M.action.assert_not_called();self.N.write.assert_not_called()

    def test_old_collector_still_alive_blocks_continuation(self):
        self.M.start_ticks=Mock(return_value=70)
        with self.assertRaisesRegex(RuntimeError,'resume_old_collector_alive'):R.preflight(self.M,self.N)

    def test_changed_golden_pid_is_not_adopted(self):
        self.units[self.second]={'MainPID':'10','ActiveState':'active'}
        with self.assertRaisesRegex(RuntimeError,'resume_golden_identity_changed'):R.preflight(self.M,self.N)

    def test_already_patched_golden_cannot_receive_a_redundant_restart(self):
        self.M.module_is_patched=lambda p:True
        with self.assertRaisesRegex(RuntimeError,'resume_golden_identity_changed'):R.preflight(self.M,self.N)

    def test_pending_systemd_job_refuses(self):
        self.N.command.return_value='123 /pending'
        with self.assertRaisesRegex(RuntimeError,'resume_job_pending'):R.preflight(self.M,self.N)

    def test_actual_empty_job_newline_is_not_mistaken_for_a_job(self):
        self.N.command.return_value='\n'
        self.assertEqual(R.execute(self.M,self.N,'check')['status'],'golden_resume_checked')
        self.M.action.assert_not_called()

    def test_missing_or_overlapping_new_window_refuses_before_action(self):
        for window in (None,SimpleNamespace(start=datetime(2026,10,24,11,tzinfo=timezone.utc))):
            with self.assertRaisesRegex(RuntimeError,'resume_new_window_required'):R.execute(self.M,self.N,'refresh',window)
        self.M.action.assert_not_called()

    def test_golden_only_request_has_durable_parent_binding_and_keeps_original_receipt(self):
        before=self.parent.read_bytes();command=self.N.command;write=self.N.write;oldroot=self.M.ROOT
        def action(N,mode,window):
            self.assertEqual(mode,'refresh');self.assertEqual(self.M.ROOT,self.target)
            N.write(self.target/'receipt.json',json.dumps({'before_units':self.units,'phase':'restart_requested'}).encode(),exclusive=True)
            N.command(['/usr/bin/systemctl','--no-block','restart',self.second],seconds=10)
            return {'status':'native_brotli_consumers_verified','security_transition_completed':False}
        self.M.action.side_effect=action
        result=R.execute(self.M,self.N,'refresh',self.window)
        value=json.loads(write.call_args.args[1]);self.assertEqual(value['parent_partial_receipt_sha256'],R.PARENT_SHA)
        self.assertFalse(value['collector_restart_requested']);self.assertFalse(result['collector_restart_requested'])
        self.assertEqual(self.parent.read_bytes(),before);self.assertIs(self.N.command,command);self.assertIs(self.N.write,write)
        self.assertEqual(self.M.ROOT,oldroot)

    def test_collector_restart_is_denied_even_if_underlying_action_requests_it(self):
        def action(N,*a):N.command(['/usr/bin/systemctl','--no-block','restart',self.first])
        self.M.action.side_effect=action
        with self.assertRaisesRegex(RuntimeError,'resume_only_one_golden_restart_allowed'):R.execute(self.M,self.N,'refresh',self.window)
        self.assertFalse(any('--no-block' in call.args[0] for call in self.N.command.call_args_list))

    def test_second_golden_request_is_denied(self):
        def action(N,*a):
            for _ in range(2):N.command(['/usr/bin/systemctl','--no-block','restart',self.second])
        self.M.action.side_effect=action
        with self.assertRaisesRegex(RuntimeError,'resume_only_one_golden_restart_allowed'):R.execute(self.M,self.N,'refresh',self.window)
        self.assertEqual(sum('--no-block' in c.args[0] for c in self.N.command.call_args_list),1)

    def test_write_to_parent_or_other_control_path_is_denied(self):
        self.M.action.side_effect=lambda N,*a:N.write(self.parent,b'{}',exclusive=False)
        with self.assertRaisesRegex(RuntimeError,'resume_write_outside_journal'):R.execute(self.M,self.N,'refresh',self.window)
        self.N.write.assert_not_called()

if __name__=='__main__':unittest.main()
