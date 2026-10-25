"""Owned public Root fixtures: no daemon, operational data or process clients."""
from contextlib import ExitStack,redirect_stdout
from copy import deepcopy
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
from types import ModuleType,SimpleNamespace
from unittest.mock import patch
import ast,io,json,os,struct,tempfile,time,unittest

ROOT=Path(__file__).resolve().parents[1]
DENIED=('subprocess.run','subprocess.Popen','os.system','os.popen','os.fork','os.kill','os.killpg',
    'socket.socket','socket.create_connection','socket.getaddrinfo','urllib.request.urlopen',
    'ctypes.CDLL','ctypes.PyDLL','resource.setrlimit','signal.signal','signal.alarm')

def load(path,name):
    value=ModuleType(name);value.__file__=str(path)
    exec(compile(path.read_bytes(),str(path),'exec'),value.__dict__);return value

with ExitStack() as importing:
    for name in DENIED:importing.enter_context(patch(name,side_effect=AssertionError('ambient denied')))
    V=load(ROOT/'deploy/maintenance/market_candidate_images_v3.py','_health_version_fixture')
    P=load(ROOT/'deploy/maintenance/runtime_health_observation_policy.py','_health_policy_fixture')
    D=load(ROOT/'deploy/maintenance/datastore_candidate_contract.py','_health_contract_fixture')

class MarketHealthV3Tests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        for name in DENIED:self.stack.enter_context(patch(name,side_effect=AssertionError('ambient denied')))
        self.stack.enter_context(patch.dict(os.environ,{'APP_ENV_FILE':'config/unit-test.env.example'},clear=True))
        self.temp=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.health-v3-',dir=ROOT)))
        self.roots={s:self.temp/s for s in ('bot','web')}
        for root in self.roots.values():
            root.mkdir(mode=0o700);(root/'source').mkdir(mode=0o700);(root/'controls').mkdir(mode=0o700);(root/'attempts').mkdir(mode=0o700)
            for src,dst in ((ROOT/'deploy/maintenance/market_candidate_images_v2.py',root/'market_candidate_images.py'),
                    (ROOT/'deploy/maintenance/runtime_health_observation_policy.py',root/'source'/V.POLICY)):
                dst.write_bytes(src.read_bytes());dst.chmod(0o600)
        self.stack.enter_context(patch.object(V,'ROOTS',self.roots))
        self.M=V.load_engine(self.stack,'bot');self.M.TOOLS=self.roots
        self.now=int(time.time());self.stack.enter_context(patch.object(V.time,'time',return_value=self.now))

    def stamp(self,value):return datetime.fromtimestamp(value,timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    def rows(self,site):
        M=self.M;rows=[]
        for i in range(27 if site=='web' else 15):
            cid=M.CAPTURE if site=='web' and i==0 else M.PROCESSOR if site=='web' and i==1 else f'{i+1:064x}'
            row={'id':cid,'image':'sha256:'+'b'*64,'name':'/public-role-'+str(i),'running':True,
                'restarting':False,'paused':False,'dead':False,'health':'healthy','pid':1000+i,
                'started':self.stamp(self.now-120),'restarts':7,'host':{'Memory':1024,'CapDrop':['ALL']},
                'execution':{'cmd':['public'],'environment':['PUBLIC=FIXTURE']},
                'mounts':[{'Source':'/public/source','Destination':'/public/code','RW':False}],
                'networks':{'public':{'NetworkID':'a'*64,'EndpointID':'b'*64,'MacAddress':'02:00:00:00:00:02',
                    'IPAddress':'192.0.2.7','Gateway':'192.0.2.1','Aliases':['public'],'IPAMConfig':None}}}
            if cid==M.CAPTURE:
                row['image']=M.F79_IMAGE;row['networks']={n:deepcopy(row['networks']['public']) for n in M.F79_NETWORKS}
            if cid==M.PROCESSOR:row['image']=M.PROCESSOR_IMAGE
            rows.append(row)
        return sorted(rows,key=lambda r:r['id'])
    def ctx(self,site='bot'):
        baselines={s:{'containers':self.rows(s),'image_map':[{'id':'sha256:'+'c'*64,'tag':'public'}]} for s in ('bot','web')}
        op={'operation':V.OPERATION,'profile':'e31aed440f25','phase':'load-native','nonce':'a'*32,'not_before':self.now-10,'deadline':self.now+120,
            'admission_sha256':'e'*64,'health_policy_sha256':V.POLICY_SHA,'baselines':baselines}
        work=self.roots[site]/'attempts'/'public';work.mkdir(mode=0o700)
        return SimpleNamespace(site=site,D=D,baseline=baselines[site],op=op,pin='d'*64,
            spec={'profile':'e31aed440f25'},root=self.roots[site],stack=self.stack,work=work,checks=[],end=time.monotonic()+120,
            requested=False,check=lambda:None,capacity=lambda:None)
    def changed(self,ctx,cid=None):
        value=deepcopy(ctx.baseline)
        row=next(r for r in value['containers'] if r['id']==cid) if cid else next(r for r in value['containers'] if r['id'] not in {self.M.CAPTURE,self.M.PROCESSOR})
        row['health']='unhealthy';return value
    def restart(self,value):
        value=deepcopy(value);row=next(r for r in value['containers'] if r['id']==self.M.CAPTURE)
        row.update(pid=row['pid']+1,restarts=row['restarts']+1,started=self.stamp(self.now-60))
        for i,net in enumerate(row['networks'].values()):net.update(EndpointID=str(i+3)*64,MacAddress='02:00:00:00:00:0'+str(i+3))
        return value
    def observe(self,ctx,value):
        with patch.object(self.M,'snapshot',return_value=value):return self.M.stable(ctx)

    def test_exact_sealed1041_and_pure_policy_cold_pins_and_unchanged_core_functions(self):
        self.assertEqual(sha256((ROOT/'deploy/maintenance/market_candidate_images_v2.py').read_bytes()).hexdigest(),V.BASE_SHA)
        self.assertEqual(sha256((ROOT/'deploy/maintenance/runtime_health_observation_policy.py').read_bytes()).hexdigest(),V.POLICY_SHA)
        base=load(ROOT/'deploy/maintenance/market_candidate_images_v2.py','_independent_base')
        for name in ('Guard','seal','parents','check_parents','command_result','clients_close','source_archive','admitted_tar',
                'legacy_a37','run','image','load_web','load_native','stream_web','receive_web','read_frame','read_part','write_all','snapshot','image_store_equivalence'):
            a,b=getattr(base,name),getattr(self.M,name)
            if isinstance(a,type):a,b=a.__init__,b.__init__
            with self.subTest(name=name):self.assertEqual(a.__code__.co_code,b.__code__.co_code);self.assertEqual(a.__code__.co_consts,b.__code__.co_consts)
        self.assertNotEqual(base.header.__code__.co_code,self.M.header.__code__.co_code)

    def test_both_sites_raw_health_only_flaps_pass_and_first_closed_records_persist(self):
        for site in ('bot','web'):
            ctx=self.ctx(site);value=self.changed(ctx);saved=deepcopy((ctx.baseline,value))
            self.assertEqual(self.observe(ctx,value),value);first=deepcopy(self.M.health_details(ctx))
            self.observe(ctx,ctx.baseline)
            self.assertEqual(self.M.health_details(ctx),first);self.assertEqual((ctx.baseline,value),saved)
            self.assertIs(first['runtime_health_issues_open'],True);self.assertEqual(len(first['runtime_health_observations']),1)
            self.assertIs(first['runtime_health_observations'][0]['security_accepted'],False)

    def test_nonhealth_or_unknown_health_drift_refuses_before_observation(self):
        ctx=self.ctx()
        for field,value in (('pid',2000),('running',False),('paused',True),('dead',True),('restarting',True),('restarts',8),
                ('started',self.stamp(self.now-60)),('image','sha256:'+'f'*64),('health','starting'),('health',None)):
            changed=self.changed(ctx);next(r for r in changed['containers'] if r['health']=='unhealthy')[field]=value
            with self.subTest(field=field),self.assertRaises(RuntimeError):self.observe(ctx,changed)
        for field in ('host','execution','mounts','networks'):
            changed=self.changed(ctx);row=next(r for r in changed['containers'] if r['health']=='unhealthy')
            if field=='mounts':row[field][0]['RW']=True
            else:row[field]['unmodeled']='PUBLIC'
            with self.subTest(field=field),self.assertRaises(RuntimeError):self.observe(ctx,changed)
        self.assertFalse(hasattr(ctx,'runtime_health_observations'))

    def test_f79_original_one_cached_healthy_successor_remains_exact_and_independent(self):
        ctx=self.ctx('web');value=self.restart(self.changed(ctx));self.observe(ctx,value)
        self.assertEqual(ctx.f79_successor,next(r for r in value['containers'] if r['id']==self.M.CAPTURE))
        self.observe(ctx,value)
        self.assertEqual(len(ctx.runtime_health_observations),1)
        second=self.restart(value);next(r for r in second['containers'] if r['id']==self.M.CAPTURE)['started']=self.stamp(self.now-30)
        for bad in (second,ctx.baseline):
            with self.assertRaises(RuntimeError):self.observe(ctx,bad)
        unhealthy=deepcopy(value);next(r for r in unhealthy['containers'] if r['id']==self.M.CAPTURE)['health']='unhealthy'
        with self.assertRaises(RuntimeError):self.observe(ctx,unhealthy)

    def test_warmup_timeout_keeps_actual_accepted_flap_and_restores_deadline(self):
        ctx=self.ctx('web');value=self.changed(ctx);next(r for r in value['containers'] if r['id']==self.M.CAPTURE)['health']='starting'
        original=ctx.end
        with patch.object(self.M,'_snapshot_once',return_value=value),patch.object(self.M.time,'sleep',return_value=None),patch.object(self.M.time,'monotonic',side_effect=[10,10,10,60]):
            with self.assertRaisesRegex(RuntimeError,'capture_readiness_timeout'):self.M.snapshot(ctx)
        self.assertEqual(ctx.end,original);self.assertEqual(len(ctx.runtime_health_observations),1)
        self.assertEqual(self.M.failure_details(ctx)['runtime_health_observations'],ctx.runtime_health_observations)

    def test_warmup_does_not_record_flap_when_static_or_image_map_guard_refuses(self):
        ctx=self.ctx('web');value=self.changed(ctx);next(r for r in value['containers'] if r['id']==self.M.CAPTURE)['health']='starting'
        value['image_map'].append({'id':'sha256:'+'f'*64,'tag':'other'})
        with self.assertRaisesRegex(RuntimeError,'image_map_changed'):self.M._readiness_guard(ctx,value,None)
        self.assertFalse(hasattr(ctx,'runtime_health_observations'))

    def test_actual_length_framing_headers_footers_strict_bindings_and_monotonic_observations(self):
        web=self.ctx('web');self.observe(web,self.changed(web));native=self.ctx()
        admitted={'archive_bytes':100,'archive_sha256':'f'*64,'tar_identity':[1,2,3,4,5,1,100,6,7]}
        first={'schema':'market_image_stream/1','profile':web.spec['profile'],'operation_sha256':web.pin,
            'admission_sha256':web.op['admission_sha256'],**admitted}
        fd=os.memfd_create('public-health-frame',os.MFD_CLOEXEC);self.addCleanup(os.close,fd)
        self.M.frame(web,fd,first);raw=os.pread(fd,65537,0);length=struct.unpack('!I',raw[:4])[0]
        self.assertEqual(length,len(raw)-4);value=json.loads(raw[4:]);self.M.header(native,value,admitted)
        self.assertEqual(native.peer_runtime_health_observations,web.runtime_health_observations)
        for key,bad in (('nonce','f'*32),('operation_sha256','f'*64),('health_policy_sha256','f'*64),('site','bot'),('pid',True)):
            wrong=deepcopy(value);wrong['runtime_health_observations'][0][key]=bad
            with self.subTest(key=key),self.assertRaises(RuntimeError):self.M.header(native,wrong,admitted)
        complete={**value,'schema':'market_image_stream_complete/1','source_fd_held_and_verified':True}
        self.M.header(native,complete,admitted,complete=True)
        for bad in ({**complete,'runtime_health_observations':[]},{**complete,'unmodeled':True},
                {k:v for k,v in complete.items() if k!='health_policy_sha256'}):
            with self.assertRaises(RuntimeError):self.M.header(native,bad,admitted,complete=True)

    def test_closed_failure_peer_result_and_actual_receipt_include_same_bound_observations(self):
        web=self.ctx('web');self.observe(web,self.changed(web));native=self.ctx()
        failure={'status':'refused_or_unknown','phase':'load-native','subphase':'control_admission','code':'runtime_changed',
            'retry_allowed':False,'security_accepted':False,'exception_class':'RuntimeError','os_errno':None,**self.M.health_details(web)}
        result=self.M.peer_failure(native,failure,1);self.assertIsNotNone(result)
        self.assertEqual(result['runtime_health_observations'],web.runtime_health_observations)
        self.assertIsNone(self.M.peer_failure(native,{**failure,'unmodeled':'PUBLIC'},1))
        self.assertIsNone(self.M.peer_failure(native,{**failure,'health_policy_sha256':'f'*64},1))
        with patch.object(self.M,'coordinate_web',wraps=self.M.coordinate_web):
            self.M.admit_peer_observations(native,self.M.health_details(web))
        self.M.Context.publish(web,'public-receipt.json',{'status':'not_accepted','security_accepted':False})
        receipt=json.loads((web.work/'public-receipt.json').read_bytes())
        self.assertEqual(receipt['runtime_health_observations'],web.runtime_health_observations)
        with self.assertRaises(RuntimeError):self.M.Context.publish(web,'wrong-receipt.json',{'health_policy_sha256':'f'*64})

    def test_cold_sources_refuse_hash_hardlink_mode_and_late_drift(self):
        path=self.temp/'public.py';path.write_bytes(b'PUBLIC=1\n');path.chmod(0o600);pin=sha256(path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(RuntimeError,'source_changed'):
            with ExitStack() as stack:
                _,check=V.cold(stack,path,pin);path.chmod(0o644)
                with self.assertRaises(RuntimeError):check()
                path.chmod(0o600)
                # The retained full9 intentionally refuses even restored metadata.
                with self.assertRaises(RuntimeError):check()
        for change in ('hash','hardlink','mode'):
            p=self.temp/('public-'+change+'.py');p.write_bytes(b'PUBLIC=1\n');p.chmod(0o600)
            if change=='hardlink':os.link(p,self.temp/'public-link.py')
            if change=='mode':p.chmod(0o644)
            with ExitStack() as stack,self.assertRaises(RuntimeError):V.cold(stack,p,'f'*64 if change=='hash' else pin)

    def test_context_explicit_policy_control_and_fresh_load_work_no_old_attempt_adoption(self):
        M=self.M;root=self.roots['bot'];catalog=(ROOT/'deploy/maintenance/market_candidate_images.json').read_bytes()
        (root/'market_candidate_images.json').write_bytes(catalog);(root/'market_candidate_images.json').chmod(0o600)
        for name,pin in json.loads(catalog)['helpers'].items():
            path=root/'source'/Path(name).name;path.write_bytes((ROOT/name).read_bytes());path.chmod(0o600)
        worker=root/V.WORKER;worker.write_bytes(Path(V.__file__).read_bytes());worker.chmod(0o600)
        fixtures=self.ctx();op={**fixtures.op,'schema':'market_image_store_control/2','profile':fixtures.spec['profile'],
            'worker_sha256':sha256(worker.read_bytes()).hexdigest(),'fences':{'bot':{},'web':{}},'admission':None,'admission_sha256':None}
        for site,baseline in op['baselines'].items():baseline.update(health_policy_sha256=V.POLICY_SHA,observed_at=self.now,initial_observed_at=self.now,
            site=site,controller_sha256=op['worker_sha256'],runtime_health_issues_open=True,initial_containers=deepcopy(baseline['containers']))
        control=root/'controls'/(op['nonce']+'.json');args=SimpleNamespace(site='bot',phase='load-native',control=control,control_sha256=None)
        def write(value):
            raw=json.dumps(value).encode();control.write_bytes(raw);control.chmod(0o600);args.control_sha256=sha256(raw).hexdigest()
        scope=b'RuntimeMaxUSec=15min\nTimeoutStopUSec=5s\nKillMode=control-group\nSendSIGKILL=yes\n'
        old=root/'attempts'/(op['profile']+'-load-native');old.mkdir(mode=0o700)
        with patch.object(M,'scope',return_value=None),patch.object(M,'command',return_value=scope):
            write({**op,'health_policy_sha256':'f'*64})
            with ExitStack() as stack,self.assertRaisesRegex(RuntimeError,'health_policy_control'):V.context(args,stack,M)
            write(op)
            with ExitStack() as stack,self.assertRaisesRegex(RuntimeError,'attempt_inventory'):V.context(args,stack,M)
            old.rmdir()
            with ExitStack() as stack:
                ctx=V.context(args,stack,M)
                self.assertEqual(ctx.work.name,op['profile']+'-load-native-health-v1-'+op['nonce'])
        for site in ('bot','web'):
            ctx=SimpleNamespace(op=op,pin='a'*64)
            argv=M.remote(ctx,'stream-web');self.assertEqual(argv.count(str(self.roots['web']/V.WORKER)),1)
            self.assertNotIn(str(self.roots['web']/'market_candidate_images.py'),argv)

    def test_final_held_source_check_precedes_any_success_output(self):
        def failing_load(stack,site):
            stack.callback(lambda:(_ for _ in ()).throw(RuntimeError('source_changed')))
            return self.M
        with patch.object(V,'load_engine',side_effect=failing_load),patch.object(V,'context',return_value=SimpleNamespace(end=time.monotonic()+5,check=lambda:None)),\
                patch.object(self.M,'run',return_value={'status':'success'}),patch.object(self.M,'observation_details',return_value={}),\
                patch.object(self.M,'failure_details',return_value={}),patch.object(V.signal,'alarm',return_value=0),redirect_stdout(io.StringIO()) as output:
            code=V.main(['--site','bot','--phase','load-native'])
        self.assertEqual(code,1);lines=output.getvalue().splitlines();self.assertEqual(len(lines),1)
        self.assertEqual(json.loads(lines[0])['status'],'refused_or_unknown')

    def test_primary_failure_survives_actual_malformed_observation_diagnostic_dual_fault(self):
        ctx=self.ctx();ctx.op['baselines']['web']['containers']=[]
        with self.assertRaisesRegex(RuntimeError,'runtime_health_observation'):self.M.failure_details(ctx)
        with patch.object(V,'load_engine',return_value=self.M),patch.object(V,'context',return_value=ctx),\
                patch.object(self.M,'run',side_effect=RuntimeError('runtime_changed')),patch.object(V.signal,'alarm',return_value=0),\
                redirect_stdout(io.StringIO()) as output:
            code=V.main(['--site','bot','--phase','load-native'])
        self.assertEqual(code,1);lines=output.getvalue().splitlines();self.assertEqual(len(lines),1)
        value=json.loads(lines[0]);self.assertEqual(value['code'],'runtime_changed');self.assertEqual(value['exception_class'],'RuntimeError')
        self.assertIs(value['runtime_health_diagnostic_unavailable'],True)
        self.assertNotIn('runtime_health_observation',output.getvalue().replace('runtime_health_observations',''))
        self.assertNotIn('PUBLIC=FIXTURE',output.getvalue())

    def test_snapshot_success_uses_original_secret_output_guard_before_any_print(self):
        with patch.object(V,'load_engine',return_value=self.M),patch.object(V,'collect',return_value={'Config':{'Env':['PRIVATE-SENTINEL']}}),\
                patch.object(V.signal,'alarm',return_value=0),redirect_stdout(io.StringIO()) as output:
            code=V.main(['--site','bot','--phase','snapshot','--source-sha256','a'*64])
        self.assertEqual(code,1);lines=output.getvalue().splitlines();self.assertEqual(len(lines),1)
        self.assertEqual(json.loads(lines[0])['code'],'private_output');self.assertNotIn('PRIVATE-SENTINEL',output.getvalue())

    def test_baseline_collection_keeps_raw_health_and_strict_image_map_and_f79_identity(self):
        M=self.M;worker=self.roots['bot']/V.WORKER;raw=Path(V.__file__).read_bytes();worker.write_bytes(raw);worker.chmod(0o600)
        ctx=self.ctx();before=deepcopy(ctx.baseline);after=self.changed(ctx);source_open=Path.open
        def public_machine(path,*args,**kwargs):
            return io.BytesIO(b'1'*32+b'\n') if path==Path('/etc/machine-id') else source_open(path,*args,**kwargs)
        for value,allowed in ((after,True),({**after,'image_map':[]},False)):
            with patch.object(M,'snapshot',side_effect=[before,value]),patch.object(M,'store_view',return_value={'public':True}),\
                    patch.object(M,'daemon_policy',return_value={'public':True}),patch.object(M,'temp_metadata',return_value={}),patch.object(Path,'open',public_machine):
                if allowed:
                    result=V.collect(M,'bot',sha256(raw).hexdigest());self.assertEqual(result['containers'],after['containers'])
                    self.assertEqual(result['initial_containers'],before['containers'])
                    self.assertEqual(result['initial_observed_at'],self.now)
                    self.assertIs(result['runtime_health_issues_open'],True);self.assertIs(result['security_accepted'],False)
                else:
                    with self.assertRaisesRegex(RuntimeError,'snapshot_runtime_unstable'):V.collect(M,'bot',sha256(raw).hexdigest())
        web=self.ctx('web');before=deepcopy(web.baseline);after=self.restart(before)
        worker=self.roots['web']/V.WORKER;worker.write_bytes(raw);worker.chmod(0o600)
        with patch.object(M,'snapshot',side_effect=[before,after]),patch.object(M,'store_view',return_value={}),\
                patch.object(M,'daemon_policy',return_value={}),patch.object(M,'temp_metadata',return_value={}),patch.object(Path,'open',public_machine):
            with self.assertRaisesRegex(RuntimeError,'snapshot_runtime_unstable'):V.collect(M,'web',sha256(raw).hexdigest())

    def test_under_guard_begin_refuses_all_old_new_same_phase_names_then_persists_disposition(self):
        ctx=self.ctx();prefix=ctx.spec['profile']+'-'+ctx.op['phase'];ctx.work=ctx.root/'attempts'/(prefix+'-health-v1-'+ctx.op['nonce'])
        ctx.publish=lambda name,value:self.M.Context.publish(ctx,name,value)
        for suffix in ('','-health-v1-'+'b'*32,'-other-version-'+'c'*32):
            old=ctx.root/'attempts'/(prefix+suffix);old.mkdir(mode=0o700)
            with self.subTest(suffix=suffix),self.assertRaisesRegex(RuntimeError,'attempt_inventory'):self.M.Context.begin(ctx)
            self.assertFalse(ctx.work.exists());old.rmdir()
        self.M.Context.begin(ctx)
        value=json.loads((ctx.work/'health-policy-disposition.json').read_bytes())
        self.assertIs(value['all_same_phase_attempts_absent'],True)
        self.assertEqual(value['operation_sha256'],ctx.pin);self.assertEqual(value['health_policy_sha256'],V.POLICY_SHA)
        with self.assertRaisesRegex(RuntimeError,'attempt_inventory'):self.M.Context.begin(ctx)

    def test_late_owned_worker_drift_in_collect_and_phase_final_control_check_refuse_green(self):
        M=self.M;worker=self.roots['bot']/V.WORKER;raw=Path(V.__file__).read_bytes();worker.write_bytes(raw);worker.chmod(0o600)
        before=self.ctx().baseline;calls=[];source_open=Path.open
        def public_snapshot(ctx):
            calls.append(True)
            if len(calls)==2:worker.write_bytes(raw+b'\n')
            return deepcopy(before)
        def public_machine(path,*a,**kw):return io.BytesIO(b'1'*32+b'\n') if path==Path('/etc/machine-id') else source_open(path,*a,**kw)
        with patch.object(M,'snapshot',side_effect=public_snapshot),patch.object(M,'store_view',return_value={}),\
                patch.object(M,'daemon_policy',return_value={}),patch.object(M,'temp_metadata',return_value={}),patch.object(Path,'open',public_machine):
            with self.assertRaises(RuntimeError):V.collect(M,'bot',sha256(raw).hexdigest())
        ctx=SimpleNamespace(end=time.monotonic()+5,check=lambda:(_ for _ in ()).throw(RuntimeError('source_metadata_changed')))
        with patch.object(V,'load_engine',return_value=M),patch.object(V,'context',return_value=ctx),patch.object(M,'run',return_value={'status':'success'}),\
                patch.object(M,'observation_details',return_value={}),patch.object(M,'failure_details',return_value={}),\
                patch.object(V.signal,'alarm',return_value=0),redirect_stdout(io.StringIO()) as output:
            self.assertEqual(V.main(['--site','bot','--phase','load-native']),1)
        self.assertEqual(len(output.getvalue().splitlines()),1);self.assertEqual(json.loads(output.getvalue())['status'],'refused_or_unknown')

if __name__=='__main__':unittest.main()
