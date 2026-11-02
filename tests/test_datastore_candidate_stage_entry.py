"""Real owned entry buffers/final checkpoints with explicitly inert host seams.

These fixtures never admit real helper/image bytes or claim a production LOAD.
No source guard is waived: default source/control readers and final checkpoints
run on the actual retained owned descriptors. Only named ancillary/host inputs
and phase side effects are inert test seams.
"""
from contextlib import ExitStack, redirect_stdout, redirect_stderr
from hashlib import sha256
import fcntl
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

with ExitStack() as importing:
    for name in ('subprocess.Popen','subprocess.run','os.system','os.kill','os.killpg',
                 'socket.socket','socket.create_connection','socket.getaddrinfo',
                 'urllib.request.urlopen','ctypes.CDLL','ctypes.PyDLL','signal.signal',
                 'signal.alarm','resource.setrlimit'):
        importing.enter_context(patch(name,side_effect=AssertionError('ambient operation denied')))
    from tests import test_datastore_candidate_stage_completion as fixtures

C,W=fixtures.C,fixtures.W
ROOT=Path(__file__).resolve().parents[1]


class StageEntryTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        for name in fixtures.BASE.TARGETS:
            self.stack.enter_context(patch(name,side_effect=AssertionError('ambient operation denied')))
        self.stack.enter_context(patch.object(W,'CONTRACT',W.CONTRACT))
        self.stack.enter_context(patch.object(W,'PHASE',W.PHASE))
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='owned-stage-entry-')))
        self.root.chmod(0o700);old=os.umask(0o077);self.addCleanup(os.umask,old)

    def private(self,path,raw):
        path.write_bytes(raw);path.chmod(0o600);return path

    def entry(self,site,phase,handler,*,owned=True,drift_argument=False):
        folder=self.root/(site+'-'+phase);folder.mkdir(mode=0o700)
        tools=folder/'tools';tools.mkdir(mode=0o700);controls=tools/'controls';controls.mkdir(mode=0o700)
        data=folder/'data';data.mkdir(mode=0o700)
        logical={'receive-web':'stage-web','stream-web':'load-native'}.get(phase,phase)
        op=fixtures.BASE.DatastoreCandidateTests.operation(self,logical)
        actual_sources={}
        for name in ('datastore_candidate_contract.py','datastore_candidate_stage.py'):
            source=ROOT/'deploy/maintenance'/name;body=source.read_bytes()
            self.private(tools/name,body);actual_sources[name]=sha256(body).hexdigest()
        op['source_sha256'].update(actual_sources)
        helper_names=(*C.HELPERS,'load_native8da.py')
        helper_bodies={name:('EXPLICIT_INERT_ANCILLARY_MODULE = '+repr(name)+'\n').encode() for name in helper_names}
        for name,body in helper_bodies.items():self.private(tools/name,body)
        evidence_body=b'EXPLICIT_OWNED_PUBLIC_EVIDENCE_SEAM\n';evidence=self.private(folder/'public-evidence.zip',evidence_body)
        op['evidence_path']=str(evidence)
        marker=self.private(folder/'marker',b'permanent owned public marker');source=self.private(folder/'source',b'')
        reservation=folder/'reservation';op['fences']={'marker':list(C.identity(marker.lstat())),'source_mutex':list(C.identity(source.lstat()))}
        raw=C.encode(op);pin=sha256(raw).hexdigest();control=self.private(controls/(op['nonce']+'.json'),raw)
        self.private(reservation,C.encode({'schema':'datastore_candidate_reservation/1','nonce':op['nonce'],
            'operation_sha256':pin,'pid':os.getpid()}))
        args=SimpleNamespace(site=site,phase=phase,control=str(control),control_sha256=pin,apply=W.CONFIRM)
        state=SimpleNamespace(op=op,pin=pin,args=args,work=data/(op['profile']+'-'+op['nonce']),
            data=data,tools=tools,phase_returned=False,fd=None,checkpoint_after_phase=0,helper_calls=[],source_identity=C.identity(source.lstat()))
        real_seal=W.seal;real_control=W.control;real_checkpoint=W.checkpoint
        class EvidenceSeam:
            # The actual source module remains unchanged. Only the explicitly
            # named public-evidence seam substitutes an independent fixture.
            def __init__(self,base):self.base=base
            def __getattr__(self,name):return getattr(self.base,name)
            def read(inner,path,maximum=C.MAX_JSON,pin=None):
                if Path(path)==evidence:
                    self.assertEqual(pin,C.PROFILES[op['profile']]['evidence_sha256'])
                    return inner.base.read(evidence,maximum,pin=sha256(evidence_body).hexdigest())
                self.assertNotIn(Path(path),{marker,source})
                return inner.base.read(path,maximum,pin=pin)
            def evidence(inner,body,spec):
                self.assertEqual(body,evidence_body);self.assertEqual(spec,C.PROFILES[op['profile']])
                return {'image':{'Config':{}},'synthetic_owned_evidence_seam':True}
        def seal(path,pin,stack):
            if path.name in actual_sources:
                self.assertEqual(pin,actual_sources[path.name])
                module,check=real_seal(path,pin,stack)
                return (EvidenceSeam(module) if path.name=='datastore_candidate_contract.py' else module),check
            self.assertIn(path.name,helper_names)
            self.assertEqual(pin,op['source_sha256'][path.name]);state.helper_calls.append(path.name)
            # A named ancillary loader seam: these inert fixture bytes are NOT
            # represented as matching the production helper pin above.
            own=helper_bodies[path.name];fd,_,check=stack.enter_context(C.held(path,len(own),pin=sha256(own).hexdigest()))
            self.assertEqual(os.pread(fd,len(own)+1,0),own)
            return SimpleNamespace(explicit_inert_ancillary_module=path.name),check
        def fence():
            self.assertEqual(C.identity(source.lstat()),state.source_identity)
            self.assertEqual(source.lstat().st_size,0)
        def checkpoint(ctx,*,verify_locks=False):
            if state.phase_returned:
                state.checkpoint_after_phase+=1
                if state.fd is not None:self.assertEqual(os.fstat(state.fd).st_ino,state.source_tar_inode)
            return real_checkpoint(ctx,verify_locks=verify_locks)
        def control_admission(*argv):
            value=real_control(*argv)
            if drift_argument:args.phase='unreviewed-invocation-drift'
            return value
        handlers={name:Mock(side_effect=lambda ctx,name=name:handler(name,ctx,state)) for name in
            ('transfer_web','request_web_load','load_native','receive_web','load_web','stream_web')}
        lock_rows=[]
        with ExitStack() as stack:
            for index,path in enumerate((marker,source,reservation)):
                fd=os.open(path,os.O_RDONLY);stack.callback(os.close,fd)
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                info=os.fstat(fd);lock_rows.append(f'{index}: FLOCK ADVISORY WRITE {os.getpid()} {os.major(info.st_dev):x}:{os.minor(info.st_dev):x}:{info.st_ino} 0 EOF')
            def public_command(ctx,argv,**kw):
                self.assertEqual(argv,['/usr/bin/cat','/proc/locks'])
                return ('\n'.join(lock_rows)+'\n').encode()
            stack.enter_context(patch.object(W,'HERE',tools));stack.enter_context(patch.dict(W.ROOTS,{site:data}))
            stack.enter_context(patch.object(W,'MARKER',marker));stack.enter_context(patch.object(W,'SOURCE_LOCK',source));stack.enter_context(patch.object(W,'RESERVATION',reservation))
            stack.enter_context(patch.object(W,'seal',side_effect=seal));stack.enter_context(patch.object(W,'control',side_effect=control_admission))
            stack.enter_context(patch.object(W,'scope'));stack.enter_context(patch.object(W,'baseline',return_value={'synthetic_owned_host_baseline':True}))
            stack.enter_context(patch.object(W,'capacity'));stack.enter_context(patch.object(W,'command',side_effect=public_command))
            stack.enter_context(patch.object(W,'checkpoint',side_effect=checkpoint));stack.enter_context(patch.object(W.signal,'alarm'))
            for name,method in handlers.items():stack.enter_context(patch.object(W,name,method))
            try:state.result=W.execute(args,reservation_owned=owned,fence_check=fence if owned else None,on_rpc=Mock() if owned else None)
            except Exception:
                state.calls={name:method.call_count for name,method in handlers.items()}
                self.last_state=state
                raise
        state.calls={name:method.call_count for name,method in handlers.items()}
        self.assertEqual(C.identity(source.lstat()),state.source_identity)
        self.assertEqual(set(state.helper_calls),set(helper_names))
        self.last_state=state
        return state

    def test_six_phase_routes_use_real_owned_buffers_and_preserve_native_callbacks(self):
        routes=(('bot','stage-web','transfer_web'),('bot','load-web','request_web_load'),
                ('bot','load-native','load_native'),('web','receive-web','receive_web'),
                ('web','load-web','load_web'),('web','stream-web','stream_web'))
        for site,phase,expected in routes:
            def handler(name,ctx,state):
                self.assertIsInstance(ctx.retained_source_stack,ExitStack)
                self.assertTrue((ctx.work/'docker-config').is_dir())
                self.assertEqual(ctx.op['nonce'],state.op['nonce']);self.assertEqual(ctx.pin,state.pin)
                self.assertTrue(ctx.data['synthetic_owned_evidence_seam'])
                state.phase_returned=True
                return {'status':'explicit_owned_phase_fixture','security_accepted':False}
            with self.subTest(site=site,phase=phase):
                state=self.entry(site,phase,handler)
                self.assertEqual(state.calls[expected],1);self.assertEqual(sum(state.calls.values()),1)
                self.assertEqual(state.result,None if phase=='stream-web' else {'status':'explicit_owned_phase_fixture','security_accepted':False})
                self.assertEqual(state.checkpoint_after_phase,0 if phase=='stream-web' else 1)

    def test_native_entry_rejects_missing_owned_callbacks_before_work_or_phase(self):
        with self.assertRaisesRegex(RuntimeError,'native_launcher_required'):
            self.entry('bot','stage-web',lambda *args:self.fail('phase must not run'),owned=False)
        self.assertFalse(self.last_state.work.exists());self.assertEqual(sum(self.last_state.calls.values()),0)

    def test_post_admission_invocation_drift_refuses_instead_of_routing_a_phase(self):
        with self.assertRaisesRegex(RuntimeError,'phase_scope'):
            self.entry('web','receive-web',lambda *args:self.fail('unreviewed phase must not run'),drift_argument=True)
        self.assertEqual(sum(self.last_state.calls.values()),0)

    def retained_tar(self,replace=False):
        raw=b'EXPLICIT_OWNED_PUBLIC_TAR_FD_FIXTURE';captured={}
        def handler(name,ctx,state):
            self.assertEqual(name,'load_web')
            source_root=state.data/(ctx.op['profile']+'-'+ctx.op['source_nonce']);source_root.mkdir(mode=0o700)
            path=self.private(source_root/'candidate-image.tar',raw)
            proof={'archive_bytes':len(raw),'archive_sha256':sha256(raw).hexdigest()}
            admission={'archive_binding':proof,'tar_binding':{'identity':list(C.identity(path.lstat()))}}
            with patch.object(W,'read_admission',return_value=admission),ExitStack() as phase:
                fd,size,_=W.source_tar(ctx,phase)
                self.assertEqual(os.pread(fd,size+1,0),raw)
                state.fd=fd;state.source_tar_inode=path.lstat().st_ino;captured['fd']=fd
            # The phase's local stack closed. The REAL execute() stack must
            # retain this descriptor through its REAL final checkpoint.
            self.assertEqual(os.fstat(fd).st_ino,state.source_tar_inode)
            if replace:
                other=self.private(source_root/'replacement.tar',raw);os.replace(other,path)
            state.phase_returned=True
            return {'status':'explicit_owned_loaded_phase_fixture','security_accepted':False}
        if replace:
            with self.assertRaisesRegex(RuntimeError,'input_changed'):self.entry('web','load-web',handler)
            state=self.last_state
        else:
            state=self.entry('web','load-web',handler)
            self.assertEqual(state.result['status'],'explicit_owned_loaded_phase_fixture')
        self.assertEqual(state.checkpoint_after_phase,1)
        with self.assertRaises(OSError):os.fstat(captured['fd'])

    def test_execute_retains_real_tar_fd_after_phase_return_through_final_checkpoint(self):self.retained_tar()

    def test_execute_rejects_tar_path_aba_at_real_final_checkpoint_and_closes_fd(self):self.retained_tar(True)

    def test_oci_index_daemon_identity_keeps_index_manifest_config_distinct(self):
        spec=C.PROFILES['postgres'];index='sha256:'+'d'*64;manifest='sha256:'+'e'*64
        proof={'source_identities':{index:{'kind':'OCI_index_to_manifest_to_config','media_type':C.OCI_INDEX,'size':123}},
            'manifest_id':manifest,'archive_sha256':'f'*64,'archive_bytes':4096}
        ctx=SimpleNamespace(C=C,spec=spec,data={'image':{'Config':{}}},baseline={'host':'65.109.220.59','port':37067})
        def command(ctx,argv):
            if 'Descriptor' in argv[-2]:return C.encode({'digest':index,'mediaType':C.OCI_INDEX,'size':123})
            return C.encode({'id':index,'os':'linux','architecture':'amd64','rootfs':{'Type':'layers','Layers':spec['layers']},'tags':None,'config':{}})
        with patch.object(W,'command',side_effect=command):leaf=W.observe_loaded(ctx,proof,index)
        binding=leaf['binding'];self.assertEqual(binding['docker_image_id'],index)
        self.assertEqual(binding['scanner_config_id'],spec['config_id'])
        self.assertEqual(binding['index_binding']['root_index_sha256'],index[7:])
        self.assertEqual(binding['index_binding']['selected_manifest_sha256'],manifest[7:])
        self.assertFalse(binding['index_binding']['signatures_verified']);self.assertFalse(binding['security_accepted'])

    def test_entry_cli_outputs_only_public_result_and_no_stdout_for_stream(self):
        argv=['datastore_candidate_stage.py','--site','web','--phase','stream-web','--control',str(self.root/'public.json'),
              '--control-sha256','a'*64,'--apply',W.CONFIRM]
        for result in (None,{'status':'explicit_owned_fixture','security_accepted':False}):
            out,err=io.StringIO(),io.StringIO()
            with patch.object(sys,'argv',argv),patch.object(W,'execute',return_value=result),redirect_stdout(out),redirect_stderr(err):
                self.assertEqual(W.main(),0)
            self.assertEqual(err.getvalue(),'')
            self.assertEqual(out.getvalue(),'' if result is None else json.dumps(result,sort_keys=True)+'\n')

    def test_entry_cli_errors_never_reflect_private_tokens_or_allow_replay(self):
        argv=['datastore_candidate_stage.py','--site','web','--phase','receive-web','--control',str(self.root/'public.json'),
              '--control-sha256','a'*64,'--apply',W.CONFIRM]
        for contract,error,code in ((C,RuntimeError('capacity_floor'),'capacity_floor'),
                                    (C,ValueError('EXPLICIT_INERT_PRIVATE_ERROR'),'unexpected_failure'),
                                    (None,ValueError('EXPLICIT_INERT_PRIVATE_ERROR'),'entry_not_verified')):
            out,err=io.StringIO(),io.StringIO()
            with patch.object(sys,'argv',argv),patch.object(W,'CONTRACT',contract),patch.object(W,'execute',side_effect=error),redirect_stdout(out),redirect_stderr(err):
                self.assertEqual(W.main(),1)
            value=json.loads(err.getvalue());self.assertEqual(value['code'],code)
            self.assertFalse(value['retry_load_allowed']);self.assertFalse(value['daemon_cancellation_proven'])
            self.assertFalse(value['security_accepted']);self.assertEqual(out.getvalue(),'')
            self.assertNotIn('EXPLICIT_INERT_PRIVATE_ERROR',err.getvalue())


if __name__=='__main__':unittest.main()
