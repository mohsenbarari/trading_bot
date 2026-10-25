"""Finite five-target choreography using real temporary files/flocks only."""
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import fcntl
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
from unittest.mock import Mock, patch

REPO = Path(__file__).resolve().parents[1]
def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result
M = module('_release_v2_fixture', REPO/'deploy/maintenance/security_consumer_release.py')
C = module('_release_v2_contract', REPO/'deploy/maintenance/security_consumer_contract.py')
W = module('_release_v2_writer', REPO/'deploy/maintenance/security_consumer_site.py')


class ConsumerCoordinator(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        for name in ('subprocess.run','subprocess.Popen','subprocess.call','subprocess.check_call',
                     'subprocess.check_output','os.system','socket.create_connection','socket.getaddrinfo',
                     'urllib.request.urlopen','ctypes.CDLL','ctypes.PyDLL','resource.setrlimit','signal.signal','signal.alarm'):
            self.stack.enter_context(patch(name,side_effect=AssertionError('ambient command/network/native denied')))
        self.temp=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.consumer-v2-',dir=REPO)))
        self.temp.chmod(0o700);self.workspace=self.temp/'workspace';self.workspace.mkdir(mode=0o700)
        (self.workspace/'results').mkdir(mode=0o700)
        marker,history,stop=(self.temp/name for name in ('marker','history','stop'))
        for path,raw in ((marker,b'original authority immutable\n'),(history,b'old terminal history\n'),(stop,b'STOP\n')):
            path.write_bytes(raw);path.chmod(0o600)
        self.before=marker.read_bytes()
        def identity(s):return (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        def read(path,**kwargs):
            self.assertTrue(Path(path).is_relative_to(self.temp));return Path(path).read_bytes()
        def publish(src,dst):os.link(src,dst);os.unlink(src)
        def write(fd,raw):
            while raw:raw=raw[os.write(fd,raw):]
        self.b=SimpleNamespace(MARKER=marker,BOTJ=history,SOURCE=self.temp/'source',RESERVATION=self.temp/'reservation',
            PENDING=self.temp/'absent-pending',identity=identity,read=read,decode=json.loads,
            document=lambda p:json.loads(read(p)),write_all=write,publish_noreplace=publish,
            private_parent=lambda p:self.assertTrue(Path(p).is_relative_to(self.temp)))
        self.b.SOURCE.touch(mode=0o600);self.source_before=identity(self.b.SOURCE.stat())
        self.stack.enter_context(patch.object(M,'STOP',stop))
        self.now=datetime(2026,10,25,0,10,tzinfo=timezone.utc);case=self
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None):return case.now
        self.stack.enter_context(patch.object(M,'datetime',Clock))
        self.record={'schema':C.SUCCESSOR_SCHEMA,'operation_id':C.SUCCESSOR_OPERATION,
            'status':'approved_for_finite_conditional_activation','nonce':'a'*32,
            'source_commit':'1'*40,'source_tree':'2'*40,'candidate_source_image_id':C.CANDIDATE,
            'not_before_utc':'2026-10-25T00:00:00Z','cutoff_utc':'2026-10-25T00:50:00Z',
            'deadline_utc':'2026-10-25T01:20:00Z',
            'recovery':{'purpose':'owned_rollback_only','not_before_utc':'2026-10-25T00:00:00Z',
                'deadline_utc':'2026-10-25T01:50:00Z','owned_targets_only':True,
                'new_forward_actions_allowed':False,'unknown_rpc_observation_only':True},
            'images':{'bot':C.CANDIDATE,'web':C.CANDIDATE},'baseline_images':dict(C.OLD_IMAGES),
            'permissions':{'five_consumer_images':True,'migrations':False,'capture':False,
                'writer_dns':False,'engine_packages':False,'reboot':False},
            'runtime_env_delta':dict(C.RUNTIME_ENV_DELTA),'checkout_exception':dict(C.USER_DOCUMENT_EXCEPTION),
            'payload':{'baseline_commit':C.BASELINE,'baseline_tree':C.BASELINE_TREE,'payload_tree':C.PAYLOAD_TREE,
                       'manifest_sha256':C.PAYLOAD_SHA256},
            'global_security_eligible':False,'raw_gate_passed':False,'raw_gate_waived':False,'acl_global_library_fixed':False,
            'evidence':{n:{'path':str(self.temp/(n+'.json')),'sha256':'3'*64} for n in C.EVIDENCE_NAMES},
            'control_sources':{n:'4'*64 for n in C.CONTROL_SOURCES},
            'control_files':{str(p):sha256(p.read_bytes()).hexdigest() for p in (marker,history,stop)},
            'control_identities':{str(p):list(identity(p.stat())) for p in (marker,history,stop)}}
        self.record['source_mutex']=list(self.source_before)
        for name,pin in [('finding_dispositions',C.DISPOSITION_SHA256),('source_closures',C.SOURCE_CLOSURE_SHA256),
            ('payload_manifest',C.PAYLOAD_SHA256),('web_current_profile',C.WEB_COMPLETED_PLAN_SHA256),
            ('web_engine_completion',C.WEB_ENGINE_COMPLETION_SHA256),('web_kernel_postboot',C.WEB_KERNEL_POSTBOOT_SHA256),
            ('web_host_maintenance',C.WEB_KERNEL_COMPLETION_SHA256),('native_public_baseline',C.NATIVE_PUBLIC_BASELINE_SHA256),
            ('web_artifact_admission',C.WEB_ARTIFACT_ADMISSION_SHA256)]:self.record['evidence'][name]['sha256']=pin
        self.worker=SimpleNamespace(CONFIRMATION='EXPLICIT',native=Mock(),workspace=lambda *_:self.workspace,
            read=lambda p,**kw:Path(p).read_bytes(),write=W.write)
        # Authority typing/expiry is tested against the real pure contract.
        # Choreography fixtures retain their actual local UID (no fake stat).
        original_window=C.assert_window
        def window(record,now,**kwargs):
            value=deepcopy(record)
            for identity in value['control_identities'].values():identity[3]=0
            value['source_mutex'][3:5]=[0,0]
            return original_window(value,now,**kwargs)
        self.stack.enter_context(patch.object(C,'assert_window',side_effect=window))
        self.stack.enter_context(patch.object(M,'source_binding',return_value={'preserved_user_document':{'verified':True}}))
        self.stack.enter_context(patch.object(M,'sealed_controls',return_value=self.workspace))
        old_lstat=Path.lstat
        def lstat(path,*a,**kw):
            if path==Path('/root/.ssh/id_ed25519_iran'):return SimpleNamespace(st_mode=stat.S_IFREG|0o600,st_uid=0,st_nlink=1)
            return old_lstat(path,*a,**kw)
        self.stack.enter_context(patch.object(Path,'lstat',lstat))
        self.calls=[];self.behavior={};self.expire_after=None
        def invoke(action,sites,record,path,digest,b,locks,**kw):
            service=kw.get('service');self.calls.append((action,sites,service))
            self.assertTrue(locks);self.assertEqual(marker.read_bytes(),self.before)
            self.assertEqual(b.SOURCE.read_bytes(),b'');self.assertEqual(identity(b.SOURCE.stat()),self.source_before)
            probe=os.open(b.SOURCE,os.O_RDWR|os.O_NOFOLLOW)
            try:
                with self.assertRaises(BlockingIOError):fcntl.flock(probe,fcntl.LOCK_EX|fcntl.LOCK_NB)
            finally:os.close(probe)
            if b.RESERVATION.exists():
                self.assertEqual(json.loads(b.RESERVATION.read_bytes())['window_sha256'],'b'*64)
            behavior=self.behavior.get((action,sites[0],service))
            if behavior=='command_failure':return 1
            if action in {'target','rollback'}:
                site=sites[0];index=C.ROLE_ORDER.index((site,service));status='rolled_back' if action=='rollback' else 'verified'
                if behavior=='known_failure':status='rolled_back_after_postcheck_failure'
                if behavior=='unknown':status='write_outcome_unknown'
                image=C.OLD_IMAGES[site] if status.startswith('rolled_back') else record['images'][site]
                result={'schema':'security_consumer_target_result/2','operation_id':C.SUCCESSOR_OPERATION,
                    'nonce':record['nonce'],'source_commit':record['source_commit'],'site':site,'service':service,
                    'source_image_id':C.CANDIDATE,'status':status,'image':image,
                    'id':None if status=='write_outcome_unknown' else format(10+index+(100 if action=='rollback' else 0),'064x'),
                    'first_failure_stage':'target_postcheck' if behavior=='known_failure' else None,
                    'first_failure_code':'successor_api_not_ready' if behavior=='known_failure' else None,
                    'global_security_eligible':False,'security_accepted':False,'raw_gate_passed':False,'raw_gate_waived':False}
                if behavior=='bad_result':result['source_commit']='9'*40
                (self.workspace/'results'/(site+'-'+action+'-'+service+'.json')).write_text(json.dumps(result))
                if self.expire_after==(site,service):self.now=datetime(2026,10,25,1,21,tzinfo=timezone.utc)
            return 0
        self.stack.enter_context(patch.object(M,'invoke',side_effect=invoke))

    def run_action(self,action='apply',confirmation='EXPLICIT'):
        return M.run(action,self.record,self.temp/'window.json','b'*64,self.worker,C,self.b,confirmation)

    def assert_fences_removed(self):
        self.assertTrue(self.b.SOURCE.exists());self.assertFalse(self.b.RESERVATION.exists())
        self.assertEqual(self.b.identity(self.b.SOURCE.stat()),self.source_before)
        self.assertEqual(self.b.SOURCE.read_bytes(),b'')
        self.assertEqual(self.b.MARKER.read_bytes(),self.before)

    def test_readonly_check_has_no_new_fences_or_mutations(self):
        self.assertEqual(self.run_action('check',None)['status'],'checked')
        self.assertEqual(self.calls,[('check',('web','bot'),None)]);self.assert_fences_removed()

    def test_exact_five_single_service_order_then_global_postcheck(self):
        result=self.run_action();self.assertEqual(result['status'],'five_targets_verified_global_gates_pending')
        self.assertFalse(result['global_security_eligible'])
        self.assertEqual([(s[0],n) for a,s,n in self.calls if a=='target'],list(C.ROLE_ORDER))
        self.assertEqual(self.calls[-1],('verify',('web','bot'),None));self.assert_fences_removed()

    def test_final_receipt_follows_exact_cleanup_with_marker_flock_still_held(self):
        original_write=self.worker.write;observed=[]
        def write(b,path,value,exclusive=False):
            if path.name=='controller-result.json':
                self.assert_fences_removed();self.assertTrue(exclusive)
                for mutex in (self.b.MARKER,self.b.SOURCE):
                    fd=os.open(mutex,os.O_RDONLY|os.O_NOFOLLOW)
                    try:
                        with self.assertRaises(BlockingIOError):fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    finally:os.close(fd)
                observed.append(value.copy())
            return original_write(b,path,value,exclusive=exclusive)
        self.worker.write=write
        result=self.run_action();receipt=json.loads((self.workspace/'controller-result.json').read_bytes())
        self.assertEqual(observed,[receipt]);self.assertTrue(result['controller_result_written'])
        self.assertEqual({k:receipt[k] for k in ('nonce','source_commit','source_tree','window_sha256')},
            {'nonce':'a'*32,'source_commit':'1'*40,'source_tree':'2'*40,'window_sha256':'b'*64})
        self.assertTrue(receipt['fence_cleanup_confirmed']);self.assertIsNone(receipt['first_failure'])
        self.assertTrue(receipt['source_mutex_preserved']);self.assertTrue(receipt['reservation_removed'])
        self.assertEqual(receipt['status'],'five_targets_verified_global_gates_pending')
        self.assertTrue(all(receipt[k] is False for k in
            ('global_security_eligible','security_accepted','raw_gate_passed','raw_gate_waived')))
        calls=self.calls.copy()
        with self.assertRaisesRegex(RuntimeError,'existing_controller_attempt_not_adopted'):self.run_action()
        self.assertEqual(self.calls,calls);self.assert_fences_removed()

    def test_final_receipt_failure_is_non_success_without_any_post_cleanup_mutation(self):
        original_write=self.worker.write
        def write(b,path,value,exclusive=False):
            if path.name=='controller-result.json':
                self.assert_fences_removed();raise OSError('ordinary fixture publication failure')
            return original_write(b,path,value,exclusive=exclusive)
        self.worker.write=write
        result=self.run_action();calls=self.calls.copy()
        self.assertEqual(result['status'],'known_outcome_final_receipt_pending')
        self.assertEqual(result['verified_outcome'],'five_targets_verified_global_gates_pending')
        self.assertFalse(result['controller_result_written']);self.assertTrue(result['fence_cleanup_confirmed'])
        self.assertFalse(any(a=='rollback' for a,_,_ in self.calls))
        journal=json.loads((self.workspace/'controller-journal.json').read_bytes())
        self.assertEqual(journal['phase'],'verified_pending_fence_cleanup')
        with self.assertRaisesRegex(RuntimeError,'existing_controller_attempt_not_adopted'):self.run_action()
        self.assertEqual(self.calls,calls);self.assert_fences_removed()

    def test_receipt_publication_then_sync_failure_is_still_pending_and_never_overwritten(self):
        original_write=self.worker.write
        def write(b,path,value,exclusive=False):
            original_write(b,path,value,exclusive=exclusive)
            if path.name=='controller-result.json':raise OSError('ordinary fixture final sync uncertainty')
        self.worker.write=write;result=self.run_action()
        self.assertEqual(result['status'],'known_outcome_final_receipt_pending')
        self.assertFalse(result['controller_result_written']);self.assert_fences_removed()
        original=(self.workspace/'controller-result.json').read_bytes();calls=self.calls.copy()
        with self.assertRaisesRegex(RuntimeError,'existing_controller_attempt_not_adopted'):self.run_action()
        self.assertEqual((self.workspace/'controller-result.json').read_bytes(),original);self.assertEqual(self.calls,calls)

    def test_rollback_first_failure_survives_final_receipt_failure(self):
        self.behavior[('target','bot','app')]='known_failure';original_write=self.worker.write
        def write(b,path,value,exclusive=False):
            if path.name=='controller-result.json':raise OSError('ordinary fixture publication failure')
            return original_write(b,path,value,exclusive=exclusive)
        self.worker.write=write;result=self.run_action()
        self.assertEqual(result['status'],'known_outcome_final_receipt_pending')
        self.assertEqual(result['verified_outcome'],'rolled_back')
        self.assertEqual(result['first_failure'],{'site':'bot','service':'app','stage':'target_postcheck',
                                                'code':'successor_api_not_ready'})
        self.assertEqual(self.calls[-1][0],'recovered_verify');self.assert_fences_removed()

    def test_partial_fence_cleanup_cannot_publish_a_final_result(self):
        original_unlink=Path.unlink
        def unlink(path,*a,**kw):
            if path==self.b.RESERVATION:raise OSError('ordinary fixture unlink failure')
            return original_unlink(path,*a,**kw)
        with patch.object(Path,'unlink',unlink):
            with self.assertRaisesRegex(OSError,'ordinary fixture unlink failure'):self.run_action()
        self.assertEqual(self.b.identity(self.b.SOURCE.stat()),self.source_before);self.assertTrue(self.b.RESERVATION.exists())
        self.assertFalse((self.workspace/'controller-result.json').exists())
        self.assertEqual(self.b.MARKER.read_bytes(),self.before)

    def test_known_failure_restores_only_prior_completed_targets_reverse_order(self):
        self.behavior[('target','bot','app')]='known_failure'
        self.assertEqual(self.run_action()['status'],'rolled_back')
        self.assertEqual([(s[0],n) for a,s,n in self.calls if a=='rollback'],[('web','sync_worker'),('web','app')])
        self.assertNotIn(('target',('bot',),'bot'),self.calls);self.assert_fences_removed()

    def test_unknown_rpc_stops_without_any_rollback_or_later_target(self):
        self.behavior[('target','web','sync_worker')]='unknown'
        with self.assertRaisesRegex(RuntimeError,'target_daemon_outcome_unknown'):self.run_action()
        self.assertFalse(any(a=='rollback' for a,_,_ in self.calls))
        self.assertEqual(len([a for a,_,_ in self.calls if a=='target']),2)
        self.assertTrue(self.b.SOURCE.exists());self.assertTrue(self.b.RESERVATION.exists())
        with self.assertRaisesRegex(RuntimeError,'existing_private_primary_operation'):self.run_action()

    def test_changed_or_missing_result_is_unknown_even_after_worker_returned_zero(self):
        self.behavior[('target','web','app')]='bad_result'
        with self.assertRaisesRegex(RuntimeError,'target_result_binding'):self.run_action()
        self.assertFalse(any(a=='rollback' for a,_,_ in self.calls));self.assertTrue(self.b.SOURCE.exists())

    def test_forward_expiry_cannot_strand_known_writes_and_never_extends_forward_authority(self):
        self.expire_after=('web','sync_worker')
        result=self.run_action();self.assertEqual(result['status'],'rolled_back')
        self.assertEqual([(s[0],n) for a,s,n in self.calls if a=='target'],[('web','app'),('web','sync_worker')])
        self.assertEqual([(s[0],n) for a,s,n in self.calls if a=='rollback'],[('web','sync_worker'),('web','app')])
        self.assert_fences_removed()

    def test_wrong_confirmation_never_creates_source_or_reservation(self):
        with self.assertRaisesRegex(RuntimeError,'explicit_scoped_apply_required'):self.run_action(confirmation=None)
        self.assertEqual(self.calls,[]);self.assert_fences_removed()

    def test_nonempty_persistent_source_is_not_adopted_or_overwritten(self):
        self.b.SOURCE.write_bytes(b'foreign');self.b.SOURCE.chmod(0o600)
        with self.assertRaisesRegex(RuntimeError,'persistent_source_mutex_changed'):self.run_action()
        self.assertEqual(self.b.SOURCE.read_bytes(),b'foreign');self.assertEqual(self.calls,[])

    def test_busy_existing_source_refuses_before_reservation_or_private_validation(self):
        fd=os.open(self.b.SOURCE,os.O_RDWR|os.O_NOFOLLOW)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):self.run_action('check',None)
        finally:os.close(fd)
        self.assertEqual(self.calls,[]);self.assert_fences_removed()

    def test_source_aba_is_rejected_and_never_unlinked_by_stage_apply_or_check(self):
        old=self.b.SOURCE.with_name('old-source');self.b.SOURCE.rename(old);self.b.SOURCE.touch(mode=0o600)
        with self.assertRaisesRegex(RuntimeError,'persistent_source_mutex_changed'):self.run_action('check',None)
        self.b.SOURCE.unlink();old.rename(self.b.SOURCE)
        # Renames change ctime; bind the complete fresh public metadata explicitly.
        self.source_before=self.b.identity(self.b.SOURCE.stat());self.record['source_mutex']=list(self.source_before)
        original_unlink=Path.unlink
        def unlink(path,*a,**kw):
            self.assertNotEqual(path,self.b.SOURCE)
            return original_unlink(path,*a,**kw)
        with patch.object(Path,'unlink',unlink):
            self.assertEqual(self.run_action('check',None)['status'],'checked')
            self.assertEqual(self.run_action('stage')['status'],'public_assets_staged')
            self.assertEqual(self.run_action()['status'],'five_targets_verified_global_gates_pending')
        self.assert_fences_removed()

    def test_source_path_replacement_during_acquisition_rejects_retained_fd_mismatch(self):
        original_flock=fcntl.flock;old=self.b.SOURCE.with_name('retained-source')
        def flock(fd,operation):
            original_flock(fd,operation)
            if self.b.identity(os.fstat(fd))==self.source_before:
                self.b.SOURCE.rename(old);self.b.SOURCE.touch(mode=0o600)
        with patch.object(M.fcntl,'flock',side_effect=flock):
            with self.assertRaisesRegex(RuntimeError,'persistent_source_mutex_changed'):self.run_action('check',None)
        self.assertTrue(old.exists());self.assertEqual(old.read_bytes(),b'');self.assertEqual(self.calls,[])
        self.assertFalse(self.b.RESERVATION.exists())

    def test_foreign_reservation_refuses_without_writing_either_persistent_mutex(self):
        self.b.RESERVATION.write_bytes(b'foreign operation context')
        before=self.b.RESERVATION.stat();content=self.b.RESERVATION.read_bytes()
        with self.assertRaisesRegex(RuntimeError,'existing_private_primary_operation'):self.run_action()
        self.assertEqual(self.b.RESERVATION.stat(),before);self.assertEqual(self.b.RESERVATION.read_bytes(),content)
        self.assertEqual(self.b.identity(self.b.SOURCE.stat()),self.source_before);self.assertEqual(self.calls,[])

    def test_stage_only_copies_public_assets_and_releases_its_exact_new_fences(self):
        self.assertEqual(self.run_action('stage')['status'],'public_assets_staged')
        self.assertEqual(self.calls,[('stage',('web','bot'),None)]);self.assert_fences_removed()
        receipt=json.loads((self.workspace/'controller-stage-result.json').read_bytes())
        self.assertTrue(receipt['fence_cleanup_confirmed']);self.assertEqual(receipt['action'],'stage')
        self.assertEqual(self.run_action()['status'],'five_targets_verified_global_gates_pending')

    def test_failed_finalcheck_recovers_all_five_and_verifies_original_images(self):
        self.behavior[('verify','web',None)]='command_failure'
        self.assertEqual(self.run_action()['status'],'rolled_back')
        self.assertEqual([(s[0],n) for a,s,n in self.calls if a=='rollback'],list(reversed(C.ROLE_ORDER)))
        self.assertEqual(self.calls[-1][0],'recovered_verify');self.assert_fences_removed()

    def test_cli_acceptance_pin_precedes_loading_any_derived_module(self):
        raw=b'{"control_sources":{"security_consumer_contract.py":"bad","security_consumer_site.py":"bad"}}'
        bootstrap=SimpleNamespace(read=Mock(return_value=raw),decode=json.loads)
        with patch.object(M.resource,'setrlimit'),patch.object(M,'module',return_value=bootstrap) as modules, \
             patch.object(sys,'argv',['release','--acceptance',str(self.temp/'window'),'--acceptance-sha256','0'*64]):
            with self.assertRaisesRegex(RuntimeError,'acceptance_bytes_changed'):M.main()
        self.assertEqual(modules.call_count,1)

    def test_approved_buffer_is_not_reopened_before_artifact_validation(self):
        raw=json.dumps({'control_sources':{'security_consumer_contract.py':'a'*64,'security_consumer_site.py':'b'*64}}).encode()
        bootstrap=SimpleNamespace(read=Mock(side_effect=[raw,b'foreign']),decode=json.loads)
        worker=SimpleNamespace(artifact=Mock(return_value={'operation_id':'new','nonce':'a'*32}))
        with patch.object(M.resource,'setrlimit'),patch.object(M,'module',side_effect=[bootstrap,object(),worker]), \
             patch.object(M,'run',return_value={'status':'checked'}), \
             patch.object(sys,'argv',['release','--acceptance',str(self.temp/'window'),'--acceptance-sha256',sha256(raw).hexdigest()]), \
             redirect_stdout(io.StringIO()):self.assertEqual(M.main(),0)
        bootstrap.read.assert_called_once();self.assertEqual(worker.artifact.call_args.kwargs,{'raw':raw})

    def test_cli_final_receipt_pending_returns_failure(self):
        raw=json.dumps({'control_sources':{'security_consumer_contract.py':'a'*64,'security_consumer_site.py':'b'*64}}).encode()
        bootstrap=SimpleNamespace(read=Mock(return_value=raw),decode=json.loads)
        worker=SimpleNamespace(artifact=Mock(return_value={'operation_id':'new','nonce':'a'*32}))
        with patch.object(M.resource,'setrlimit'),patch.object(M,'module',side_effect=[bootstrap,object(),worker]), \
             patch.object(M,'run',return_value={'status':'known_outcome_final_receipt_pending'}), \
             patch.object(sys,'argv',['release','--acceptance',str(self.temp/'window'),'--acceptance-sha256',sha256(raw).hexdigest()]), \
             redirect_stdout(io.StringIO()):self.assertEqual(M.main(),1)


class PublishedBuffers(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.root=Path('/root/trading-bot/trading_bot')
        self.stack.enter_context(patch.object(M,'REPO',self.root))
        self.stack.enter_context(patch.object(M,'HERE',self.root/'deploy/maintenance'))
        self.user=b'ordinary user-owned public document fixture\n'
        self.exception={**C.USER_DOCUMENT_EXCEPTION,'sha256':sha256(self.user).hexdigest()}
        self.c=SimpleNamespace(CONTROL_SOURCES=C.CONTROL_SOURCES,USER_DOCUMENT_EXCEPTION=self.exception)
        names=[*('deploy/maintenance/'+n for n in C.CONTROL_SOURCES),'docker-compose.yml','docker-compose.iran.yml']
        self.actual={name:('ordinary committed '+name+'\n').encode() for name in names}
        self.committed=deepcopy(self.actual);self.actual[self.exception['path']]=self.user
        self.record={'source_commit':'a'*40,'source_tree':'b'*40,'checkout_exception':dict(self.exception),
            'control_sources':{name:sha256(self.actual['deploy/maintenance/'+name]).hexdigest() for name in C.CONTROL_SOURCES}}
        self.status=(' M '+self.exception['path']+'\0').encode();self.remote='https://github.com/mohsenbarari/trading_bot'
        def command(argv):
            suffix=argv[4:]
            values={('symbolic-ref','--short','HEAD'):'main',('rev-parse','HEAD'):'a'*40,
                ('rev-parse','HEAD^{tree}'):'b'*40,('remote','get-url','origin'):self.remote,
                ('ls-remote','origin','refs/heads/main'):'a'*40+'\trefs/heads/main'}
            return values[tuple(suffix)]
        self.b=SimpleNamespace(command=command,read=lambda p,**_:self.actual[str(Path(p).relative_to(self.root))])
        def public(argv,**kwargs):
            suffix=argv[4:]
            if suffix[0]=='status':return self.status
            self.assertEqual(suffix[:2],['cat-file','blob']);return self.committed[suffix[2].split(':',1)[1]]
        self.worker=SimpleNamespace(private_json=public)

    def verify(self):return M.source_binding(self.record,self.c,self.b,self.worker)

    def test_only_exact_unchanged_user_document_is_declared_without_touching_it(self):
        result=self.verify();self.assertEqual(self.actual[self.exception['path']],self.user)
        self.assertTrue(result['preserved_user_document']['verified']);self.assertFalse(result['user_document_modified'])

    def test_staged_status_extra_file_or_changed_user_document_is_refused(self):
        for status in [('M  '+self.exception['path']+'\0').encode(), self.status+b'?? extra.py\0', b'']:
            with self.subTest(status=status),patch.object(self,'status',status),self.assertRaisesRegex(RuntimeError,'unapproved_checkout_change'):
                self.verify()
        self.actual[self.exception['path']]=b'changed public document'
        with self.assertRaisesRegex(RuntimeError,'preserved_user_document_changed'):self.verify()

    def test_matching_worktree_hash_cannot_substitute_for_committed_control_or_compose_bytes(self):
        for name in ['deploy/maintenance/security_consumer_site.py','docker-compose.yml','docker-compose.iran.yml']:
            old=self.actual[name];self.actual[name]=b'ordinary unpublished change\n'
            if name.startswith('deploy/'):
                self.record['control_sources'][Path(name).name]=sha256(self.actual[name]).hexdigest()
            with self.subTest(name=name),self.assertRaisesRegex(RuntimeError,'consumed_public_source_not_committed'):self.verify()
            self.actual[name]=old
            if name.startswith('deploy/'):self.record['control_sources'][Path(name).name]=sha256(old).hexdigest()

    def test_local_origin_cannot_masquerade_as_published_main(self):
        self.remote='/ordinary/local/mirror'
        with self.assertRaisesRegex(RuntimeError,'real_public_origin_required'):self.verify()


if __name__=='__main__':unittest.main()
