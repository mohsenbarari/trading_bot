"""Owned FD/pipe and explicit inert host-protocol tests; no actual host operations."""
from contextlib import ExitStack, redirect_stdout, redirect_stderr
from copy import deepcopy
from hashlib import sha256
import ast
import fcntl
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

with ExitStack() as import_fence:
    for target in ('subprocess.Popen','subprocess.run','os.system','os.kill','os.killpg',
                   'socket.socket','socket.create_connection','socket.getaddrinfo',
                   'urllib.request.urlopen','ctypes.CDLL','ctypes.PyDLL','signal.signal',
                   'signal.alarm','resource.setrlimit'):
        import_fence.enter_context(patch(target,side_effect=AssertionError('ambient_forbidden')))
    import tests.test_datastore_candidate_stage as BASE
C,W=BASE.C,BASE.W
ROOT=Path(__file__).resolve().parents[1]

class StageCompletionTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        for target in BASE.TARGETS:
            self.stack.enter_context(patch(target,side_effect=AssertionError('ambient_forbidden')))
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='owned-stage-protocol-')))
        self.root.chmod(0o700);old=os.umask(0o077);self.addCleanup(os.umask,old)
    def private(self,path,raw):
        path.write_bytes(raw);path.chmod(0o600);return path
    def operation(self,phase='stage-web'):return BASE.DatastoreCandidateTests.operation(self,phase)
    def context(self,site='web',phase='stage-web'):
        work=Path(tempfile.mkdtemp(prefix='work-',dir=self.root));work.chmod(0o700)
        dc=work/'docker-config';dc.mkdir(mode=0o700)
        out,err=os.memfd_create('owned-public-output'),os.memfd_create('owned-private-error')
        self.addCleanup(os.close,out);self.addCleanup(os.close,err)
        return SimpleNamespace(C=C,N=SimpleNamespace(bounds=Mock(),stop=Mock(return_value=True)),
            T=BASE.T,M=BASE.M,site=site,op=self.operation(phase),pin='c'*64,end=time.monotonic()+30,
            work=work,docker_config=dc,output_fd=out,error_fd=err,checks=[],on_rpc=Mock(),
            spec=deepcopy(C.PROFILES['redis']),data={'image':{'Config':{}}},baseline={'image_map':[]})
    def descriptor(self,path):
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW);self.addCleanup(os.close,fd);return fd
    def pipe(self,raw):
        r,w=os.pipe();os.write(w,raw);os.close(w)
        stream=os.fdopen(r,'rb',buffering=0);self.addCleanup(stream.close);return stream
    def write_all(self,fd,raw,end,pulse):
        pulse()
        while raw:raw=raw[os.write(fd,raw):]
    def reader(self,fd,count,end,pulse):
        raw=b''
        while len(raw)<count:
            pulse();part=os.read(fd,count-len(raw))
            if not part:raise RuntimeError('source_stream_short')
            raw+=part
        return raw
    def eof(self,fd,end,pulse):pulse();return os.read(fd,1)
    def row(self,identification='a'*64,health=None):
        return {'id':identification,'name':'/explicit-inert-fixture','image':'sha256:'+'b'*64,
            'running':True,'paused':False,'restarting':False,'restart_policy':{'Name':'always','MaximumRetryCount':0},
            'mounts':[],'ports':{},'networks':{},'pid_mode':'','privileged':False,'read_only':False,'health':health}
    def observation(self,ctx,row=None):
        row=self.row() if row is None else row
        data=self.root/'data';data.mkdir(mode=0o700,exist_ok=True)
        expected={'docker_store':{'root':'/explicit/inert-store','driver':'overlay2','server':'fixture'},
            'mount':{'filesystems':[{'target':'/explicit/inert-data','source':'fixture'}]},
            'data_roots':[{'path':str(data),'identity':list(C.identity(data.lstat())[:5])}],
            'containers':[deepcopy(row)]}
        ctx.baseline=expected
        def command(context,argv,**kwargs):
            if 'info' in argv:return C.encode(expected['docker_store'])
            if argv[0]=='/usr/bin/findmnt':return C.encode(expected['mount'])
            if argv[2:4]==['container','ls']:return (row['id']+'\n').encode()
            if argv[2:4]==['container','inspect']:return C.encode(row)
            raise AssertionError('unreviewed_inert_argv')
        return expected,command

    def test_owned_control_and_source_seals_read_same_buffer_and_refuse_replacement(self):
        tools=self.root/'tools';tools.mkdir(mode=0o700);controls=tools/'controls';controls.mkdir(mode=0o700)
        path=self.private(controls/('a'*32+'.json'),b'{"ordinary":true}\n');pin=sha256(path.read_bytes()).hexdigest()
        source=self.private(tools/'ordinary.py',b'VALUE = "ordinary retained source"\n')
        with patch.object(W,'HERE',tools),ExitStack() as held:
            raw,check=W.control_buffer(path,pin,held);self.assertEqual(C.document(raw),{'ordinary':True})
            module,source_check=W.seal(source,sha256(source.read_bytes()).hexdigest(),held)
            self.assertEqual(module.VALUE,'ordinary retained source');source_check();check()
            replacement=self.private(tools/'replacement',raw);os.replace(replacement,path)
            with self.assertRaisesRegex(RuntimeError,'operation_changed'):check()
        with ExitStack() as held:
            with self.assertRaisesRegex(RuntimeError,'source_sha256'):W.seal(source,'0'*64,held)
        public=self.private(tools/'public-unit',b'[Unit]\nDescription=ordinary\n');public.chmod(0o644)
        self.assertEqual(W.public_read(public,65536),public.read_bytes())
        public.chmod(0o666)
        with self.assertRaisesRegex(RuntimeError,'public_unit_metadata'):W.public_read(public,65536)

    def test_genuine_owned_flocks_are_required_without_source_payload_reads(self):
        ctx=self.context('bot');ctx.op['fences']={}
        marker=self.private(self.root/'marker',b'public permanent marker')
        source=self.private(self.root/'source',b'');reservation=self.private(self.root/'reservation',C.encode({
            'schema':'datastore_candidate_reservation/1','nonce':ctx.op['nonce'],
            'operation_sha256':ctx.pin,'pid':os.getpid()}))
        ctx.op['fences']['source_mutex']=list(C.identity(source.lstat()))
        lines=[]
        for n,path in enumerate((marker,source,reservation)):
            fd=self.descriptor(path);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);info=os.fstat(fd)
            lines.append(f'{n}: FLOCK ADVISORY WRITE {os.getpid()} {os.major(info.st_dev):02x}:{os.minor(info.st_dev):02x}:{info.st_ino} 0 EOF')
        read=C.read
        def only_reservation(path,*args,**kwargs):
            self.assertEqual(path,reservation);return read(path,*args,**kwargs)
        with patch.object(W,'MARKER',marker),patch.object(W,'SOURCE_LOCK',source),patch.object(W,'RESERVATION',reservation),\
             patch.object(C,'read',side_effect=only_reservation),patch.object(W,'command',return_value=('\n'.join(lines)+'\n').encode()):
            W.checkpoint(ctx,verify_locks=True)
            with patch.object(W,'command',return_value=('\n'.join(lines[:-1])+'\n').encode()):
                with self.assertRaisesRegex(RuntimeError,'native_fences'):W.checkpoint(ctx,verify_locks=True)
        self.assertEqual(source.stat().st_size,0)

    def test_scope_uses_owned_cgroup_files_and_refuses_missing_lifetime_policy(self):
        ctx=self.context();proc=self.private(self.root/'owned-cgroup',('0::/system.slice/security-datastore-stage-'+ctx.op['nonce']+'-web.scope\n').encode())
        sysroot=self.root/'owned-sys';scope=sysroot/'system.slice'/('security-datastore-stage-'+ctx.op['nonce']+'-web.scope');scope.mkdir(parents=True,mode=0o700)
        for name,raw in {'cpu.max':b'100000 100000','memory.max':str(2*1024**3).encode(),'memory.swap.max':b'0','pids.max':b'64'}.items():self.private(scope/name,raw)
        real=Path
        def paths(value):return proc if str(value)=='/proc/self/cgroup' else sysroot if str(value)=='/sys/fs/cgroup' else real(value)
        properties=b'RuntimeMaxUSec=15min\nTimeoutStopUSec=5s\nKillMode=control-group\nSendSIGKILL=yes\n'
        with patch.object(W,'Path',side_effect=paths),patch.object(W,'command',return_value=properties):
            W.scope(ctx)
            with patch.object(W,'command',return_value=properties.replace(b'15min',b'1s')):
                with self.assertRaisesRegex(RuntimeError,'scope_lifetime_required'):W.scope(ctx)
            (scope/'memory.max').write_bytes(b'999')
            with self.assertRaisesRegex(RuntimeError,'scope_required'):W.scope(ctx)
        self.assertEqual(W.duration_usec('1min 5s 12ms 3us'),65012003)
        with self.assertRaisesRegex(RuntimeError,'scope_duration'):W.duration_usec('infinity')

    def test_public_command_output_uses_owned_memfd_and_closes_on_failure_or_timeout(self):
        ctx=self.context();captured=[]
        def child(argv,**kwargs):
            captured.extend([kwargs['stdout'],kwargs['stderr']]);os.write(kwargs['stdout'],b'ordinary public output')
            self.assertIs(kwargs['preexec_fn'],ctx.N.bounds);return SimpleNamespace(wait=Mock(return_value=0))
        with patch.object(W.subprocess,'Popen',side_effect=child):self.assertEqual(W.command(ctx,['/inert/public']),b'ordinary public output')
        for fd in captured:
            with self.assertRaises(OSError):os.fstat(fd)
        timed=SimpleNamespace(wait=Mock(side_effect=subprocess.TimeoutExpired('inert',1)))
        with patch.object(W.subprocess,'Popen',return_value=timed):
            with self.assertRaisesRegex(RuntimeError,'public_command_timeout'):W.command(ctx,['/inert/public'])
        ctx.N.stop.assert_called_with(timed)
        with patch.object(W.subprocess,'Popen',return_value=SimpleNamespace(wait=Mock(return_value=7))):
            with self.assertRaisesRegex(RuntimeError,'public_command_failed'):W.command(ctx,['/inert/public'])

    def test_prospective_data_capacity_retains_eight_gib_floor(self):
        ctx=self.context();base=self.root/'owned-data';base.mkdir(mode=0o700)
        actual=Path.stat
        def metadata(path,*args,**kwargs):
            info=actual(path,*args,**kwargs)
            return SimpleNamespace(st_dev=2049) if path==base else info
        amount=W.RESERVE+W.MARGIN+4096
        fs=SimpleNamespace(f_frsize=4096,f_bavail=amount//4096)
        with patch.dict(W.ROOTS,{'web':base/'operation'}),patch.object(Path,'stat',metadata),\
             patch.object(W.os,'statvfs',return_value=fs),patch.object(W,'root_capacity',return_value=8*1024**3):
            self.assertEqual(W.capacity(ctx,4096)['data_free_bytes'],amount)
            with self.assertRaisesRegex(RuntimeError,'capacity_floor'):W.capacity(ctx,8192)

    def test_real_publication_and_full_public_baseline_validation(self):
        ctx=self.context();value={'schema':'explicit-owned-public-fixture/1','security_accepted':False}
        with patch.object(W,'capacity'):
            pin=W.publish(ctx,'ordinary.json',value)
        raw=(ctx.work/'ordinary.json').read_bytes();self.assertEqual(sha256(raw).hexdigest(),pin);self.assertEqual(C.document(raw),value)
        with patch.object(W,'capacity'):
            with self.assertRaises(FileExistsError):W.publish(ctx,'ordinary.json',value)
        rows=[{**self.row(format(i+1,'064x')),'running':i<11} for i in range(27)]
        profile={'schema':'datastore_candidate_public_baseline/1','site':'web','host':'65.109.220.59','port':37067,
            'machine_id':'a'*32,'kernel':os.uname().release,'docker_store':{},'data_roots':[],'mount':{},
            'containers':rows,'image_map':[],'app_consumers_completion_sha256':'b'*64,'legacy_parking_receipt_sha256':'c'*64}
        path=self.private(self.root/'baseline.json',C.encode(profile));ctx.op['baselines']['web']={'path':str(path),'sha256':sha256(path.read_bytes()).hexdigest()}
        with patch.object(W,'public_read',return_value=('a'*32+'\n').encode()):self.assertEqual(W.baseline(ctx),profile)
        dirty={**profile,'app_consumers_completion_sha256':'unverified'};path.write_bytes(C.encode(dirty));ctx.op['baselines']['web']['sha256']=sha256(path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(RuntimeError,'fresh_post_app_legacy_baseline_required'):W.baseline(ctx)

    def test_complete_public_observation_preserves_policy_and_reports_known_health_only(self):
        ctx=self.context();row=self.row();expected,command=self.observation(ctx,row)
        with patch.object(W,'command',side_effect=command):self.assertEqual(W.observe(ctx),expected)
        row['image']='sha256:'+'c'*64
        with patch.object(W,'command',side_effect=command):
            with self.assertRaisesRegex(RuntimeError,'container_public_policy_changed'):W.observe(ctx)
        row['image']=expected['containers'][0]['image'];row['health']='unhealthy'
        with patch.object(W,'command',side_effect=command):
            with self.assertRaisesRegex(RuntimeError,'new_health_failure'):W.observe(ctx)
        known=self.row(next(iter(W.OPEN_HEALTH)),'unhealthy');expected,command=self.observation(ctx,known)
        with patch.object(W,'command',side_effect=command):
            result=W.observe(ctx);self.assertEqual(result['containers'][0]['health'],'KNOWN_OPEN')

    def test_image_map_and_measurement_preserve_every_existing_tag(self):
        ctx=self.context();old={'id':'sha256:'+'a'*64,'repository':'ordinary','tag':'kept','digest':'<none>'}
        with patch.object(W,'command',return_value=C.encode(old)):self.assertEqual(W.image_map(ctx),[old])
        new='sha256:'+'b'*64;newrow={'id':new,'repository':'<none>','tag':'<none>','digest':'<none>'};proof={'source_identities':{new:{'kind':'Docker_config_identity'}}}
        with patch.object(W,'observe',return_value={'unchanged':True}),patch.object(W,'image_map',return_value=[old,newrow]),\
             patch.object(W,'observe_loaded',return_value={'image_loaded':True}),patch.object(W,'capacity'):
            leaf,after=W.measured(ctx,proof,{'unchanged':True},[old]);self.assertTrue(leaf['image_loaded'])
            with self.assertRaisesRegex(RuntimeError,'existing_image_or_tag_changed'):W.measured(ctx,proof,{'unchanged':True},[{**old,'tag':'different'}])

    def test_spawn_marks_native_rpc_before_client_and_keeps_normal_auth_explicit(self):
        ctx=self.context('bot');events=[];ctx.on_rpc=lambda:events.append('request')
        def client(*args,**kwargs):events.append('client');self.assertIsNone(kwargs['env']);return 'inert-client'
        with patch.object(W,'checkpoint') as checkpoint,patch.object(W.subprocess,'Popen',side_effect=client):
            self.assertEqual(W.spawn(ctx,['/inert/gh'],normal_auth=True),'inert-client')
        self.assertEqual(events,['request','client']);checkpoint.assert_called_once_with(ctx,verify_locks=True)
        ctx.site='web';ctx.on_rpc=Mock()
        with patch.object(W,'checkpoint'),patch.object(W.subprocess,'Popen',return_value='inert') as client:
            W.spawn(ctx,['/inert/docker'])
        self.assertEqual(client.call_args.kwargs['env']['DOCKER_CONFIG'],str(ctx.docker_config));ctx.on_rpc.assert_not_called()

    def test_web_receiver_authenticates_exact_pipe_and_preserves_incomplete_attempt(self):
        for kind in ('success','extra','drift'):
            with self.subTest(kind=kind):
                ctx=self.context();raw=b'explicit owned ZIP transport fixture';ctx.spec['image_zip_bytes']=len(raw)
                incoming=self.pipe(raw+(b'X' if kind=='extra' else b''));ctx.N.read_n=lambda fd,n,end,pulse:self.reader(incoming.fileno(),n,end,pulse)
                ctx.N.read_n_or_eof=lambda fd,end,pulse:self.eof(incoming.fileno(),end,pulse)
                proof={'archive_sha256':'d'*64,'physical_graph_closed':True,'security_accepted':False}
                def unpack(fd,size,*args):
                    self.assertEqual(os.pread(fd,size+1,0),raw);self.assertEqual(size,len(raw))
                    return proof,{'identity':[1,2,3]}
                state=Mock(side_effect=[{'fixture':'before'},{'fixture':'changed' if kind=='drift' else 'before'}])
                set_blocking=os.set_blocking
                with patch.object(W,'observe',state),patch.object(W,'image_map',return_value=[]),patch.object(W,'capacity'),\
                     patch.object(W.os,'set_blocking',side_effect=lambda fd,value:set_blocking(incoming.fileno() if fd==0 else fd,value)),\
                     patch.object(C,'unpack_image',side_effect=unpack) as admitted:
                    if kind=='success':
                        result=W.receive_web(ctx);self.assertEqual(result['status'],'archive_admitted_not_loaded')
                        receipt=C.document((ctx.work/'admission.json').read_bytes());self.assertFalse(receipt['docker_load_attempted'])
                    else:
                        with self.assertRaises(RuntimeError):W.receive_web(ctx)
                        self.assertFalse((ctx.work/'admission.json').exists())
                self.assertEqual((ctx.work/'artifact.zip').read_bytes(),raw)
                self.assertEqual(admitted.call_count,0 if kind=='extra' else 1)

    def source_fixture(self,ctx):
        root=self.root/('source-'+ctx.work.name);root.mkdir(mode=0o700)
        source=root/(ctx.op['profile']+'-'+ctx.op['source_nonce']);source.mkdir(mode=0o700)
        raw=b'explicit retained source TAR transport fixture'
        path=self.private(source/'candidate-image.tar',raw)
        ctx.spec['archive']['uncompressed_bytes']=len(raw)
        proof={'config_id':ctx.spec['config_id'],'rootfs_layers':ctx.spec['layers'],
            'physical_graph_closed':True,'repo_tags_empty':True,'daemon_identity_measured':False,
            'archive_bytes':len(raw),'archive_sha256':sha256(raw).hexdigest(),'expanded_layer_bytes':len(raw),
            'source_identities':{ctx.spec['config_id']:{'kind':'Docker_config_identity'}}}
        record={'schema':'datastore_candidate_web_admission/1','status':'archive_admitted_not_loaded',
            'profile':ctx.op['profile'],'nonce':ctx.op['source_nonce'],'image_zip_sha256':ctx.spec['image_zip_sha256'],
            'image_zip_bytes':ctx.spec['image_zip_bytes'],'docker_load_attempted':False,'security_accepted':False,
            'configuration_values_disclosed':False,'archive_binding':proof,'tar_binding':{'identity':list(C.identity(path.lstat()))}}
        admission=self.private(self.root/('admission-'+ctx.work.name),C.encode(record))
        ctx.op['source_admission']={'paths':{'bot':str(admission),'web':str(admission)},'sha256':sha256(admission.read_bytes()).hexdigest()}
        ctx.baseline.update(host='65.109.220.59',port=37067)
        return root,path,raw,proof

    def test_source_admission_and_stream_hold_real_fd_frame_hash_and_stable_public_policy(self):
        ctx=self.context('web','load-native');root,path,raw,proof=self.source_fixture(ctx)
        wire=os.memfd_create('owned-wire');self.addCleanup(os.close,wire)
        inspect=C.encode({'id':ctx.spec['config_id'],'os':'linux','architecture':'amd64',
            'rootfs':{'Type':'layers','Layers':ctx.spec['layers']},'tags':None,'config':{}})
        with patch.object(W,'command',return_value=inspect):leaf=W.observe_loaded(ctx,proof,ctx.spec['config_id'])
        loaded=self.private(self.root/'known-web-loaded',C.encode({'schema':'datastore_candidate_site_loaded/1',
            'status':'loaded_measured_not_accepted','profile':ctx.op['profile'],'security_accepted':False,'loaded_site':leaf}))
        ctx.op['web_loaded']={'paths':{'bot':str(loaded),'web':str(loaded)},'sha256':sha256(loaded.read_bytes()).hexdigest()}
        ctx.N.write_all=lambda fd,data,end,pulse:self.write_all(wire,data,end,pulse)
        set_blocking=os.set_blocking
        with patch.dict(W.ROOTS,{'web':root}),patch.object(W,'observe',return_value={'fixture':'unchanged'}),\
             patch.object(W,'image_map',return_value=[]),patch.object(W,'capacity'),patch.object(W,'command',return_value=inspect),\
             patch.object(W.os,'set_blocking',side_effect=lambda fd,value:set_blocking(wire if fd==1 else fd,value)):
            W.stream_web(ctx)
            # This previously raised EBADF after the phase had closed its TAR.
            W.checkpoint(ctx)
        data=os.pread(wire,os.fstat(wire).st_size,0);size=struct.unpack('!I',data[:4])[0]
        header=C.document(data[4:4+size]);position=4+size
        self.assertEqual(data[position:position+len(raw)],raw);position+=len(raw)
        size=struct.unpack('!I',data[position:position+4])[0];footer=C.document(data[position+4:position+4+size])
        self.assertEqual(footer['archive_sha256'],sha256(raw).hexdigest());self.assertEqual(footer['source_tar_identity'],list(C.identity(path.lstat())))
        self.assertEqual(header['web_loaded_site'],leaf);self.assertTrue(footer['source_fd_held_and_hash_verified'])
        self.assertEqual(position+4+size,len(data));self.assertTrue((ctx.work/'stream-started.json').exists())

    def test_web_load_keeps_tar_fd_through_operation_final_checkpoint_and_aba_refuses(self):
        for kind in ('success','aba'):
            with self.subTest(kind=kind),ExitStack() as operation:
                ctx=self.context('web','load-web');ctx.retained_source_stack=operation
                root,path,raw,proof=self.source_fixture(ctx);observed=[]
                def spawn(context,argv,**kwargs):
                    self.assertEqual(argv,[*W.DOCKER,'image','load','--quiet']);fd=kwargs['stdin'];observed.append(fd)
                    self.assertEqual(os.pread(fd,len(raw)+1,0),raw)
                    if kind=='aba':
                        replacement=self.private(path.with_name('replacement'),raw);os.replace(replacement,path)
                    return SimpleNamespace(wait=Mock(return_value=0))
                with patch.dict(W.ROOTS,{'web':root}),patch.object(W,'observe',return_value={'fixture':'unchanged'}),\
                     patch.object(W,'image_map',return_value=[]),patch.object(W,'capacity'),patch.object(W,'spawn',side_effect=spawn),\
                     patch.object(W,'measured',side_effect=lambda *args: W.checkpoint(ctx) or ({'image_loaded':True},{'fixture':'unchanged'})):
                    if kind=='success':
                        result=W.load_web(ctx);W.checkpoint(ctx)
                        self.assertEqual(result['status'],'loaded_measured_not_accepted')
                        self.assertEqual(os.pread(observed[0],len(raw)+1,0),raw)
                        self.assertEqual(os.fstat(observed[0]).st_ino,path.stat().st_ino)
                    else:
                        with self.assertRaises(C.Refusal):W.load_web(ctx)
                        with self.assertRaises(C.Refusal):operation.close()
                        self.assertFalse((ctx.work/'loaded-site.json').exists())

    def test_native_zip_transfer_and_remote_load_use_one_inert_request_without_retry(self):
        for phase in ('stage-web','load-web'):
            for kind in ('success','client_failure'):
                with self.subTest(phase=phase,kind=kind):
                    ctx=self.context('bot',phase);raw=b'explicit authenticated transport fixture'
                    ctx.spec.update(image_zip_bytes=len(raw),image_zip_sha256=sha256(raw).hexdigest())
                    source_stream=self.pipe(raw);source=SimpleNamespace(stdout=source_stream,stdin=None,wait=Mock(return_value=0))
                    read,write=os.pipe();self.addCleanup(os.close,read)
                    stdin=os.fdopen(write,'wb',buffering=0);self.addCleanup(stdin.close)
                    status='archive_admitted_not_loaded' if phase=='stage-web' else 'loaded_measured_not_accepted'
                    def wait(timeout):
                        if kind=='client_failure':return 9
                        os.write(ctx.output_fd,C.encode({'status':status,'receipt_sha256':'d'*64,'security_accepted':False}));return 0
                    target=SimpleNamespace(stdin=stdin,stdout=None,wait=Mock(side_effect=wait));calls=[]
                    def spawn(context,argv,**kwargs):
                        calls.append(argv)
                        if argv[0]=='/usr/bin/gh':self.assertTrue(kwargs['normal_auth']);return source
                        return target
                    ctx.N.read_n=self.reader;ctx.N.read_n_or_eof=self.eof;ctx.N.write_all=self.write_all
                    with patch.object(W,'observe',return_value={'fixture':'unchanged'}),patch.object(W,'image_map',return_value=[]),\
                         patch.object(W,'capacity'),patch.object(W,'spawn',side_effect=spawn):
                        function=W.transfer_web if phase=='stage-web' else W.request_web_load
                        if kind=='success':
                            result=function(ctx);self.assertEqual(result['status'],status)
                        else:
                            with self.assertRaisesRegex(RuntimeError,'client_outcome_unknown'):function(ctx)
                            self.assertFalse((ctx.work/('web-stage-result.json' if phase=='stage-web' else 'web-load-result.json')).exists())
                    self.assertEqual(len(calls),2 if phase=='stage-web' else 1)
                    if phase=='stage-web':self.assertEqual(os.read(read,len(raw)+1),raw)
                    request=C.document((ctx.work/('transfer-request.json' if phase=='stage-web' else 'web-load-request.json')).read_bytes())
                    self.assertFalse(request['security_accepted'])

    def test_native_artifact_hash_or_extra_bytes_never_publish_stage_success(self):
        for kind in ('hash','extra'):
            with self.subTest(kind=kind):
                ctx=self.context('bot','stage-web');body=b'EXPLICIT_OWNED_ARTIFACT_FIXTURE'
                ctx.spec.update(image_zip_bytes=len(body),image_zip_sha256='f'*64 if kind=='hash' else sha256(body).hexdigest())
                stream=self.pipe(body+(b'X' if kind=='extra' else b''))
                source=SimpleNamespace(stdout=stream,stdin=None,wait=Mock(return_value=0))
                read,write=os.pipe();self.addCleanup(os.close,read)
                stdin=os.fdopen(write,'wb',buffering=0);self.addCleanup(stdin.close)
                target=SimpleNamespace(stdin=stdin,stdout=None,wait=Mock(return_value=0))
                ctx.N.read_n=self.reader;ctx.N.read_n_or_eof=self.eof;ctx.N.write_all=self.write_all
                with patch.object(W,'observe',return_value={'fixture':'unchanged'}),patch.object(W,'image_map',return_value=[]),\
                     patch.object(W,'capacity'),patch.object(W,'spawn',side_effect=[source,target]) as invoked:
                    with self.assertRaisesRegex(RuntimeError,'download_size_or_sha'):W.transfer_web(ctx)
                self.assertEqual(invoked.call_count,2);source.wait.assert_not_called();target.wait.assert_not_called()
                self.assertFalse((ctx.work/'web-stage-result.json').exists())
                self.assertTrue((ctx.work/'transfer-request.json').exists());self.assertTrue(stdin.closed)

    def test_source_public_drift_after_tar_stream_emits_no_success_footer(self):
        ctx=self.context('web','load-native');root,path,raw,proof=self.source_fixture(ctx)
        wire=os.memfd_create('owned-incomplete-wire');self.addCleanup(os.close,wire)
        inspect=C.encode({'id':ctx.spec['config_id'],'os':'linux','architecture':'amd64',
            'rootfs':{'Type':'layers','Layers':ctx.spec['layers']},'tags':None,'config':{}})
        with patch.object(W,'command',return_value=inspect):leaf=W.observe_loaded(ctx,proof,ctx.spec['config_id'])
        loaded=self.private(self.root/'explicit-known-web-leaf',C.encode({'schema':'datastore_candidate_site_loaded/1',
            'status':'loaded_measured_not_accepted','profile':ctx.op['profile'],'security_accepted':False,'loaded_site':leaf}))
        ctx.op['web_loaded']={'paths':{'bot':str(loaded),'web':str(loaded)},'sha256':sha256(loaded.read_bytes()).hexdigest()}
        ctx.N.write_all=lambda fd,data,end,pulse:self.write_all(wire,data,end,pulse)
        set_blocking=os.set_blocking
        with patch.dict(W.ROOTS,{'web':root}),patch.object(W,'observe',side_effect=[{'fixture':'before'},{'fixture':'changed'}]),\
             patch.object(W,'image_map',return_value=[]),patch.object(W,'capacity'),patch.object(W,'command',return_value=inspect),\
             patch.object(W.os,'set_blocking',side_effect=lambda fd,value:set_blocking(wire if fd==1 else fd,value)):
            with self.assertRaisesRegex(RuntimeError,'stream_source_changed'):W.stream_web(ctx)
        data=os.pread(wire,os.fstat(wire).st_size,0);header_size=struct.unpack('!I',data[:4])[0]
        self.assertEqual(len(data),4+header_size+len(raw));self.assertEqual(data[4+header_size:],raw)
        self.assertTrue((ctx.work/'stream-started.json').exists());W.checkpoint(ctx)
