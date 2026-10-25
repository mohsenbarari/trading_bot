"""Finite C07 closure fixtures: no host/network/native or private-input access."""
import copy
from contextlib import ExitStack, nullcontext
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

from tests.test_web_capture2_observability import rows, settings, M as A

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('c07_closure', ROOT/'deploy/maintenance/web_c07_terminal_closure.py')
M = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(M)

class HistoricalClosureClock(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
        return value if tz is None else value.astimezone(tz)

class C07ClosureTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(M, 'datetime', HistoricalClosureClock))
        for name in ('subprocess.Popen','subprocess.run','os.system','socket.socket','socket.create_connection','ctypes.CDLL'):
            self.stack.enter_context(patch(name, side_effect=AssertionError('native_forbidden')))
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.closure-test-',dir=ROOT)))

    def fixture(self):
        V = settings()
        plan = {'operation_id':'old','nonce':'nonce','deadline_utc':'expired-original-deadline',
                'containers':[{'id':V.CID,'image':V.BASE,'name':'/'+V.PROJECT+'-'+V.SERVICE+'-1'}],
                'absent_control_paths':[]}
        marker, source, reservation = [self.root/name for name in ('marker','source','reservation')]
        for path in (marker,source,reservation):
            path.write_bytes(b'{"old_owner":2188434}\n');path.chmod(0o600)
        identity = lambda s: (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        value=marker.stat()
        plan['marker']={'sha256':sha256(marker.read_bytes()).hexdigest(),'device':value.st_dev,'inode':value.st_ino}
        events=[]
        def write(path,value,*,exclusive):
            if exclusive and path.exists():raise AssertionError('audit_overwrite')
            path.write_text(json.dumps(value));path.chmod(0o600)
            events.append(('audit',value['phase']))
        C=SimpleNamespace(ROOT=self.root,paths=lambda p:(self.root/'old.journal.json',source),
            P=SimpleNamespace(MARKER=marker,RESERVATION=reservation,PENDING=self.root/'pending',document=Mock(return_value=plan)),
            B=SimpleNamespace(read=lambda p,**k:p.read_bytes(),identity=identity,document=lambda p:json.loads(p.read_bytes()),
                              command=Mock(side_effect=lambda a:'healthy' if a[0]=='/usr/bin/docker' else '')),write_json=write)
        H=SimpleNamespace(controller=lambda:C,OLD_ROOT=self.root,OP='old',NONCE='nonce',OWNER=2188434,PLAN_SHA='a'*64)
        resources={'preserved_backings':['synthetic']}
        originals=[]
        for suffix in ('.journal.json','.owned-restore.json','.owned-intent.json'):
            path=self.root/('old'+suffix);raw=b'{"original_evidence":"immutable"}\n';path.write_bytes(raw);path.chmod(0o600)
            originals.append({'path':str(path),'raw_json':raw.decode(),'sha256':sha256(raw).hexdigest()})
        parking={'phase':'qa_parked_production_incident_open','qa_parking_verified':True,'parked_count':10,
            'retained_count':17,'old_fences_retained':True,'security_transition_completed':False,
            'parked_ids':['qa'],'preserved_resources':resources,'preserved_evidence':originals}
        parking_raw=json.dumps(parking).encode();(self.root/'old.qa-parking.json').write_bytes(parking_raw)
        capture={'schema':'capture2_observability_activation/1','operation_id':A.OP,
            'phase':'diagnostic_image_active_incident_open','new_capture_id':M.CAPTURE_ID,
            'candidate_image':V.FUNCTIONAL_IMAGE,'candidate_configuration_accepted':True,
            'all_other_26_configuration_unchanged':True,'original_operation_id':'old','original_plan_sha256':H.PLAN_SHA,
            'security_transition_completed':False}
        capture_raw=json.dumps(capture).encode();(self.root/(A.OP+'.activation.json')).write_bytes(capture_raw)
        known='f'*64
        R=SimpleNamespace(IDS={'qa'},KNOWN_MARKET_FLAPS={known:'/known'},exact_scope=Mock(return_value=([],set())),
                          pinned=lambda c:nullcontext(),hot_finished=Mock())
        P=SimpleNamespace(owner_proof=Mock(),inventory=Mock(return_value=[{'id':known,'running':True,'restarting':False,'health':'unhealthy'}]),
                          preserved_resources=Mock(return_value=resources),web_datastores_native=Mock())
        snapshot=rows(M.CAPTURE_ID,V.FUNCTIONAL_IMAGE)
        snapshot[known]=snapshot.pop(next(k for k in snapshot if k!=M.CAPTURE_ID))
        snapshot[known]['id']=known
        self.stack.enter_context(patch.object(M,'CAPTURE_SHA',sha256(capture_raw).hexdigest()))
        self.stack.enter_context(patch.object(A,'PARK_AUDIT_SHA',sha256(parking_raw).hexdigest()))
        self.stack.enter_context(patch.object(A,'snapshot',side_effect=lambda *a:copy.deepcopy(snapshot)))
        self.stack.enter_context(patch.object(A,'legacy',return_value={'persistent_boot_guard_verified':False}))
        self.stack.enter_context(patch.object(M,'terminal_owners'))
        self.stack.enter_context(patch.object(M.time,'sleep'))
        return C,P,R,H,V,plan,events,(marker,source,reservation),snapshot

    def run_it(self,fixture,action='check'):
        C,P,R,H,V,*_=fixture
        return M.execute(P,R,H,A,V,action)

    def test_historical_fixture_does_not_extend_production_day_authority(self):
        class ExpiredClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 10, 25, 0, 0, tzinfo=timezone.utc)
        fixture = self.fixture(); original = {p: p.read_bytes() for p in fixture[7]}
        with patch.object(M, 'datetime', ExpiredClock), self.assertRaisesRegex(RuntimeError, 'closure_scope_day_expired'):
            self.run_it(fixture, 'close')
        self.assertEqual({p: p.read_bytes() for p in fixture[7]}, original)
        self.assertEqual(fixture[6], [])

    def test_exact_reviewed_sources_load_without_running_old_main(self):
        files={'capture':'web_capture2_observability.py','parking':'web_qa_parking.py','qa':'web_qa_retirement.py',
               'hot':'web_market_hot_preflight.py','parity':'web_capture2_compose_parity.py'}
        for key,name in files.items():
            raw=(ROOT/'deploy/maintenance'/name).read_text()
            self.assertEqual(sha256(raw.encode()).hexdigest(),M.PINS[key])
            self.assertTrue(callable(M.reviewed(raw,key).require))
            with self.assertRaisesRegex(RuntimeError,'closure_reviewed_source_changed'):M.reviewed(raw+'\n',key)

    def test_default_has_three_real_locks_no_writes_and_keeps_known_health_incident_open(self):
        f=self.fixture(); C,P,R,H,V,plan,events,files,snapshot=f
        before={path:path.read_bytes() for path in files}
        def prove(*a):
            for path in files:
                fd=os.open(path,os.O_RDONLY)
                try:
                    with self.assertRaises(BlockingIOError):M.fcntl.flock(fd,M.fcntl.LOCK_EX|M.fcntl.LOCK_NB)
                finally:os.close(fd)
        P.owner_proof.side_effect=prove
        result=self.run_it(f)
        self.assertEqual(result['status'],'aborted_c07_closure_checked');self.assertFalse(result['security_transition_completed'])
        self.assertEqual(before,{path:path.read_bytes() for path in files});self.assertEqual(events,[])
        self.assertEqual(P.inventory.call_count,3);self.assertEqual(P.web_datastores_native.call_count,3)
        self.assertEqual(M.time.sleep.call_args_list,[((15,),),((15,),)])
        self.assertEqual(plan['containers'][0]['id'],V.CID)

    def test_close_writes_pending_before_source_then_reservation_preserves_marker_and_history(self):
        f=self.fixture();C,P,R,H,V,plan,events,files,snapshot=f
        history={p:p.read_bytes() for p in self.root.glob('old.*')}
        real_unlink=os.unlink
        def unlink(path,*a,**k):
            if Path(path) in files:events.append(('unlink',Path(path).name))
            return real_unlink(path,*a,**k)
        with patch.object(M.os,'unlink',side_effect=unlink):result=self.run_it(f,'close')
        self.assertTrue(result['owned_fences_released']);self.assertTrue(files[0].exists())
        self.assertFalse(files[1].exists());self.assertFalse(files[2].exists())
        self.assertEqual(events,[('audit','aborted_before_install_fence_release_pending'),('unlink','source'),('unlink','reservation'),
                                 ('audit','aborted_before_install_fences_released')])
        self.assertEqual(history,{p:p.read_bytes() for p in history})
        receipt=json.loads((self.root/(M.OP+'.json')).read_bytes())
        self.assertFalse(receipt['security_transition_completed']);self.assertFalse(receipt['all_prior_runtime_healthy'])
        self.assertEqual(receipt['known_market_incidents_status'],'open');self.assertNotIn('Env',json.dumps(receipt))
        checked=self.run_it(f)
        self.assertTrue(checked['historical_closure_verified']);self.assertFalse(checked['current_runtime_rechecked'])

    def test_current_capture_wrong_image_or_stopped_or_second_owner_refuses(self):
        V=settings()
        for change in ('image','stopped','owner'):
            data=rows(M.CAPTURE_ID,V.FUNCTIONAL_IMAGE)
            if change=='image':data[M.CAPTURE_ID]['image']=V.BASE
            elif change=='stopped':data[M.CAPTURE_ID]['running']=False
            else:
                other=next(row for key,row in data.items() if key!=M.CAPTURE_ID)
                other.update(running=True,mounts=copy.deepcopy(data[M.CAPTURE_ID]['mounts']))
            with self.subTest(change=change),self.assertRaises(RuntimeError):M.capture_current(A,V,data)

    def test_new_health_failure_or_retained_stopped_process_refuses_before_write(self):
        for record in ({'id':'foreign','running':True,'restarting':False},
                       {'id':'f'*64,'running':False,'restarting':False},
                       {'id':'f'*64,'running':True,'restarting':True}):
            f=self.fixture();f[1].inventory.return_value=[record]
            with self.subTest(record=record),self.assertRaises(RuntimeError):self.run_it(f,'close')
            self.assertFalse((self.root/(M.OP+'.json')).exists())
            for path in f[7]:path.unlink()

    def test_changed_qa_backing_or_open_8443_refuses(self):
        f=self.fixture();f[1].preserved_resources.return_value={'changed':True}
        with self.assertRaisesRegex(RuntimeError,'closure_qa_resource_changed'):self.run_it(f,'close')
        self.assertEqual(f[6],[])

    def test_capture_missing_or_unhealthy_state_never_counts_as_ready(self):
        f=self.fixture()
        for state in ('missing','starting','unhealthy'):
            f[0].B.command.side_effect=lambda argv:state if argv[0]=='/usr/bin/docker' else ''
            with self.subTest(state=state),self.assertRaisesRegex(RuntimeError,'closure_capture_health_not_ready'):
                self.run_it(f,'close')
        self.assertEqual(f[6],[])

    def test_foreign_or_pending_closure_receipt_never_adopted(self):
        f=self.fixture();path=self.root/(M.OP+'.json')
        path.write_text(json.dumps({'schema':'web_c07_terminal_closure/1','operation_id':M.OP,
            'original_plan_sha256':f[3].PLAN_SHA,'phase':'aborted_before_install_fence_release_pending'}))
        with self.assertRaisesRegex(RuntimeError,'closure_partial_or_previous_attempt_requires_review'):self.run_it(f)
        self.assertTrue(all(p.exists() for p in f[7]));self.assertEqual(f[6],[])

    def test_full_private_configuration_change_refuses_even_when_known_state_can_change(self):
        f=self.fixture();original=copy.deepcopy(f[-1]);changed=copy.deepcopy(original)
        changed['f'*64]['config']['Env']=['PRIVATE=different']
        with patch.object(A,'snapshot',side_effect=[original,changed]):
            with self.assertRaisesRegex(RuntimeError,'other_container_runtime_changed'):self.run_it(f,'close')
        self.assertEqual(f[6],[])

    def test_capture_acceptance_audit_change_refuses_without_writes(self):
        f=self.fixture();path=self.root/(A.OP+'.activation.json')
        changed=json.loads(path.read_bytes());changed['candidate_configuration_accepted']=False
        path.write_text(json.dumps(changed))
        with self.assertRaisesRegex(RuntimeError,'closure_capture_acceptance_changed'):self.run_it(f,'close')
        self.assertEqual(f[6],[]);self.assertTrue(all(p.exists() for p in f[7]))

    def test_partial_release_failure_keeps_pending_receipt_and_does_not_adopt_on_check(self):
        f=self.fixture();source,reservation=f[7][1:];real_unlink=os.unlink
        def failure(path,*a,**k):
            if Path(path)==reservation:raise OSError('synthetic-unlink-failure')
            return real_unlink(path,*a,**k)
        with patch.object(M.os,'unlink',side_effect=failure):
            with self.assertRaises(OSError):self.run_it(f,'close')
        self.assertFalse(source.exists());self.assertTrue(reservation.exists())
        receipt=json.loads((self.root/(M.OP+'.json')).read_bytes())
        self.assertEqual(receipt['phase'],'aborted_before_install_fence_release_pending')
        with self.assertRaisesRegex(RuntimeError,'closure_partial_or_previous_attempt_requires_review'):self.run_it(f)

    def test_replaced_source_inode_never_removed(self):
        f=self.fixture();C,P,R,H,V,plan,events,files,snapshot=f
        def replace(*args):
            old=files[1].read_bytes();files[1].unlink();files[1].write_bytes(old)
        P.owner_proof.side_effect=replace
        with self.assertRaisesRegex(RuntimeError,'closure_owned_fence_changed'):self.run_it(f,'close')
        self.assertTrue(files[1].exists());self.assertTrue(files[2].exists());self.assertEqual(events,[])

if __name__=='__main__':unittest.main()
