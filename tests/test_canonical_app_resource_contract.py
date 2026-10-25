"""Genuine-root public fixtures; no image, daemon, service or process operations."""
from contextlib import ExitStack
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,Mock
import ast,importlib.util,json,os,tempfile,time,unittest

ROOT=Path(__file__).resolve().parents[1]
DENIED=('subprocess.Popen','subprocess.run','os.system','os.popen','os.fork','os.kill','os.killpg',
    'socket.socket','socket.create_connection','socket.getaddrinfo','urllib.request.urlopen','ctypes.CDLL',
    'resource.setrlimit','signal.signal','signal.alarm')
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
with ExitStack() as importing:
    for name in DENIED:importing.enter_context(patch(name,side_effect=AssertionError('ambient denied')))
    R=module('_app_resource_public',ROOT/'deploy/maintenance/canonical_app_resource_contract.py')
    A=module('_app_resource_v3_public',ROOT/'deploy/maintenance/canonical_app_candidate_images_v3.py')
    H=module('_app_resource_existing_fixtures',ROOT/'tests/test_image_store_health_entries.py')

class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual((os.geteuid(),os.getegid()),(0,0))
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        for name in DENIED:self.stack.enter_context(patch(name,side_effect=AssertionError('ambient denied')))
        self.f=H.HealthEntryTests('test_old_sources_catalogs_and_algorithms_remain_immutable_and_engines_private')
        self.f.setUp();self.addCleanup(self.f.doCleanups);self.stack.enter_context(patch.object(H,'APP',A))
    def workspace(self):
        original=A.load_engine
        def load(stack,site):
            root=A.ROOTS[site];self.f.private(root/'source/canonical_app_resource_contract.py',(ROOT/'deploy/maintenance/canonical_app_resource_contract.py').read_bytes())
            for name in R.EVIDENCE:self.f.private(root/'evidence'/name,(ROOT/'tests/fixtures/canonical_app_resources'/name).read_bytes())
            return original(stack,site)
        with patch.object(A,'load_engine',side_effect=load):return self.f.workspace(A)
    def op(self,M,root,phase='load-native'):
        value=self.f.op(A,M,root,phase);value['schema']='canonical_app_image_store_control/3'
        value['resource_contract_sha256']=M.app_resource.CONTRACT_SHA
        for b in value['baselines'].values():
            b['schema']='canonical_app_image_store_baseline/3';b['resource_contract_sha256']=M.app_resource.CONTRACT_SHA
        return value
    def capacity_case(self,free=None,memory=None):
        root=self.f.root;before=H.module('_resource_base',ROOT/'deploy/maintenance/market_candidate_images_v2.py')
        temporary=[{'identity':before.identity(root.stat())},{'identity':before.identity(root.stat())}]
        M=SimpleNamespace(identity=before.identity,MARGIN=before.MARGIN,LIMIT=before.LIMIT,
            temp_metadata=lambda:deepcopy(temporary),memory_available=lambda:R.RAM_FLOOR if memory is None else memory)
        ctx=SimpleNamespace(engine=M,budget=R.DISK_BUDGET,tmpfs_payload_write_bytes=0,check=lambda:None,
            baseline={'store':{'root':str(root),'identity':before.identity(root.stat())[:5]},'temporary_storage':temporary,'daemon':{'files':[]}})
        resource=object.__new__(R.WebResource);resource.ctx=ctx
        available=8*1024**3+M.MARGIN+M.LIMIT+R.DISK_BUDGET if free is None else free
        return resource,ctx,SimpleNamespace(f_bavail=available,f_frsize=1)
    def test_exact_resource_arithmetic_and_false_claims(self):
        self.assertEqual(R.RAM_FLOOR,3021996032);self.assertEqual(R.BASE_RAM,2216689664)
        self.assertEqual(R.DISK_BUDGET,1810708480);self.assertEqual(R.CLIENT_RAM,536870912)
        self.assertEqual(R.METADATA_RAM,268435456);self.assertFalse(R.CONTRACT['daemon_heap_quota_claimed'])
        self.assertFalse(R.CONTRACT['native_policy_changed'])
    def test_actual_public_inventory_and_metadata_inputs_are_pinned_in_repository(self):
        for name,(size,pin) in R.EVIDENCE.items():
            raw=(ROOT/'tests/fixtures/canonical_app_resources'/name).read_bytes()
            self.assertEqual((len(raw),sha256(raw).hexdigest()),(size,pin))
        record=json.loads((ROOT/'tests/fixtures/canonical_app_resources/resource-inner-inventory.json').read_bytes())['result']
        self.assertEqual(sum(v['entries'] for v in record['layers']),21644)
        self.assertEqual(sum(v['directories'] for v in record['layers']),2933)
        self.assertEqual(max(v['entries'] for v in record['layers']),10949)
    def test_old_workers_and_base_algorithms_remain_immutable(self):
        for name,pin in (('canonical_app_candidate_images_v2.py','d7ca623be49facc51a58a58dd38bfa06760287a46b2b30e9db0b73d53e7f19e6'),
                ('market_candidate_images_v2.py','1041cc2d04100e2f6aebb95dca30c2339de41342b350ef44522c05277e9c146e')):
            self.assertEqual(sha256((ROOT/'deploy/maintenance'/name).read_bytes()).hexdigest(),pin)
        root,M=self.workspace();base=module('_untouched_resource_engine',ROOT/'deploy/maintenance/market_candidate_images_v2.py')
        for name in ('load_native','load_web','stream_web','measured','read_part','write_all','read_frame','clients_close','run','Guard'):
            actual=M.__dict__[name];expected=base.__dict__[name]
            if isinstance(actual,type):self.assertEqual(actual.__init__.__code__.co_code,expected.__init__.__code__.co_code)
            else:self.assertEqual(actual.__code__.co_code,expected.__code__.co_code)
        self.assertEqual(sha256((root/'source/canonical_app_resource_contract.py').read_bytes()).hexdigest(),A.RESOURCE_SOURCE_SHA)
    def test_every_web_producer_phase_has_exact_aggregate_scope_flags(self):
        for phase in ('snapshot','admit-existing-web','load-web','stream-web'):
            flags=R.scope_arguments(phase,'a'*32)
            self.assertIn('--property=MemoryMax=512M',flags);self.assertIn('--property=MemorySwapMax=0',flags)
            self.assertIn('--property=CPUQuota=100%',flags);self.assertIn('--property=TasksMax=64',flags)
            self.assertEqual(flags[0],'--unit=market-images-web-'+phase+'-'+'a'*32)
        for phase,nonce in (('load-native','a'*32),('snapshot',True),('stream-web','../bad')):
            with self.assertRaises(RuntimeError):R.scope_arguments(phase,nonce)
    def test_remote_native_load_stream_and_all_web_commands_use_new_worker_and_512_scope(self):
        root,M=self.workspace();ctx=self.f.basic(A,M,root);ctx.op=self.op(M,root)
        for phase in ('admit-existing-web','load-web','stream-web'):
            argv=M.remote(ctx,phase)
            self.assertIn('--property=MemoryMax=512M',argv);self.assertNotIn('--property=MemoryMax=2G',argv)
            self.assertIn(str(root/A.WORKER),argv);self.assertNotIn(str(root/'canonical_app_candidate_images_v2.py'),argv)
    def test_ram_exact_boundary_and_additive_tmpfs_writes_without_disk_reduction(self):
        resource,ctx,fs=self.capacity_case()
        with patch.object(R.os,'statvfs',return_value=fs):resource.capacity()
        ctx.engine.memory_available=lambda:R.RAM_FLOOR-1
        with patch.object(R.os,'statvfs',return_value=fs),self.assertRaises(RuntimeError):resource.capacity()
        ctx.tmpfs_payload_write_bytes=100;ctx.engine.memory_available=lambda:R.RAM_FLOOR+99
        with patch.object(R.os,'statvfs',return_value=fs),self.assertRaises(RuntimeError):resource.capacity()
        ctx.engine.memory_available=lambda:R.RAM_FLOOR+100
        with patch.object(R.os,'statvfs',return_value=fs):resource.capacity()
    def test_all_three_disk_floors_keep_full_prospective_budget_at_every_pulse(self):
        resource,ctx,fs=self.capacity_case();calls=[]
        def statvfs(path):calls.append(str(path));return fs
        with patch.object(R.os,'statvfs',side_effect=statvfs):resource.capacity()
        self.assertEqual(calls,[ctx.baseline['store']['root'],'/','/tmp'])
        for index,floor in ((0,8*1024**3),(1,2*1024**3),(2,2*1024**3)):
            calls=[]
            def low(path):
                calls.append(str(path));free=floor+ctx.engine.MARGIN+ctx.engine.LIMIT+R.DISK_BUDGET-1 if len(calls)-1==index else fs.f_bavail
                return SimpleNamespace(f_bavail=free,f_frsize=1)
            with patch.object(R.os,'statvfs',side_effect=low),self.assertRaises(RuntimeError):resource.capacity()
    def test_boolean_negative_or_omitted_tmpfs_and_budget_never_lower_gate(self):
        for field,value in (('budget',True),('budget',-1),('tmpfs_payload_write_bytes',False),('tmpfs_payload_write_bytes',-1)):
            resource,ctx,fs=self.capacity_case();setattr(ctx,field,value)
            with patch.object(R.os,'statvfs',return_value=fs),self.assertRaises(RuntimeError):resource.capacity()
        resource,ctx,fs=self.capacity_case();ctx.budget=0
        with patch.object(R.os,'statvfs',return_value=SimpleNamespace(f_bavail=8*1024**3+ctx.engine.MARGIN+ctx.engine.LIMIT+R.DISK_BUDGET-1,f_frsize=1)),self.assertRaises(RuntimeError):resource.capacity()
    def test_resource_receipt_fields_are_complete_typed_and_no_daemon_quota(self):
        resource,ctx,_=self.capacity_case();value=resource.details();R.admit_details(value)
        for key,bad in (('resource_ram_floor_bytes',True),('aggregate_client_memory_max_bytes',2*1024**3),
                ('metadata_stream_allowance_bytes',0),('actual_tmpfs_payload_write_bytes',False),('daemon_heap_quota_claimed',True),
                ('resource_contract_accepted',True),('resource_contract_sha256','0'*64)):
            with self.subTest(key=key),self.assertRaises(RuntimeError):R.admit_details({**value,key:bad})
    def test_configured_plugin_root_projection_refuses_unknown_or_tmpfs_destinations(self):
        raw=('Type:\t io.containerd.content.v1\nID:\t content\nExports:\n root\t '+R.ROOTS['content']+'\n'
            'Type:\t io.containerd.snapshotter.v1\nID:\t overlayfs\nPlatforms: linux/amd64\nExports:\n root\t '+R.ROOTS['snapshot']+'\n').encode()
        R.project_roots(raw)
        for bad in (raw.replace(R.ROOTS['content'].encode(),b'/tmp/unproven'),raw+raw,raw+b'Error: PRIVATE_ERROR\n'):
            with self.assertRaises(RuntimeError):R.project_roots(bad)
    def test_fresh_v3_control_refuses_old_or_missing_contract_and_native_capacity_is_unchanged(self):
        root,M=self.workspace();value=self.op(M,root)
        ctx=self.f.context(A,M,root,value);self.assertEqual(ctx.op['schema'],'canonical_app_image_store_control/3')
        with patch.object(M.Context,'capacity',return_value='native_original') as original:self.assertEqual(ctx.capacity(),'native_original')
        original.assert_called_once()
        for schema,pin in (('canonical_app_image_store_control/2',R.CONTRACT_SHA),('canonical_app_image_store_control/3','0'*64)):
            changed=deepcopy(value);changed['schema']=schema;changed['resource_contract_sha256']=pin
            with self.assertRaises(RuntimeError):self.f.context(A,M,root,changed)
    def test_snapshot_producer_requires_fresh_scope_nonce_before_commands(self):
        root,M=self.workspace()
        with patch.object(M,'store_view',side_effect=AssertionError('no commands before nonce')):
            for nonce in (None,True,'../bad'):
                with self.assertRaises(RuntimeError):A.collect(M,'web',sha256((root/A.WORKER).read_bytes()).hexdigest(),nonce)
    def test_source_never_sets_go_address_space_or_changes_base_loader_and_unknown_policy(self):
        raw=(ROOT/'deploy/maintenance/canonical_app_resource_contract.py').read_bytes()
        for word in (b'setrlimit',b'RLIMIT_AS',b'os.kill',b'docker image load',b'drop_caches'):self.assertNotIn(word,raw)
        self.assertNotIn(b'retry_allowed\x27:True',(ROOT/'deploy/maintenance/canonical_app_candidate_images_v3.py').read_bytes())
        self.assertEqual(A.ENGINE_SHA,'1041cc2d04100e2f6aebb95dca30c2339de41342b350ef44522c05277e9c146e')
    def test_owned_scope_files_accept_only_exact_hard512_and_original_cpu_swap_pid_limits(self):
        nonce='a'*32;unit='market-images-web-stream-web-'+nonce+'.scope';proc=self.f.private(self.f.root/'proc-cgroup',('0::/'+unit+'\n').encode())
        sysroot=self.f.root/'sys-cgroup';group=sysroot/unit;group.mkdir(parents=True)
        values={'memory.max':str(R.CLIENT_RAM),'memory.swap.max':'0','pids.max':'64','cpu.max':'100000 100000'}
        for name,value in values.items():self.f.private(group/name,(value+'\n').encode())
        real=Path
        def mapped(value):return proc if str(value)=='/proc/self/cgroup' else sysroot if str(value)=='/sys/fs/cgroup' else real(value)
        with patch.object(R,'Path',side_effect=mapped):
            R.check_scope('stream-web',nonce)
            for name,bad in (('memory.max',str(2*1024**3)),('memory.max','max'),('memory.swap.max','1'),('pids.max','65'),('cpu.max','200000 100000')):
                self.f.private(group/name,(bad+'\n').encode())
                with self.subTest(name=name,bad=bad),self.assertRaises(RuntimeError):R.check_scope('stream-web',nonce)
                self.f.private(group/name,(values[name]+'\n').encode())
    def test_live_public_directory_mode_or_inode_drift_is_measured_without_stat_fakes(self):
        path=self.f.root/'public-storage';path.mkdir(mode=0o700);s=path.stat();fs=os.statvfs(path)
        resource=object.__new__(R.WebResource);resource.nonce='a'*32;resource.phase='load-web'
        resource.paths=[{'path':str(path),'device':s.st_dev,'inode':s.st_ino,'mode':s.st_mode,'uid':s.st_uid,'gid':s.st_gid,'filesystem_id':fs.f_fsid}]
        with patch.object(R,'check_scope'):
            resource.check();path.chmod(0o755)
            with self.assertRaises(RuntimeError):resource.check()
            path.chmod(0o700);resource.check();path.rename(self.f.root/'old-storage');path.mkdir(mode=0o700)
            with self.assertRaises(RuntimeError):resource.check()
    def test_exact_resource_evidence_is_cold_sealed_and_late_content_change_refuses(self):
        root,M=self.workspace();ctx=SimpleNamespace(engine=M,root=root,stack=self.stack,checks=[])
        with patch.object(R.WebResource,'check'):
            resource=R.WebResource(ctx,'a'*32,'load-web')
        for check in ctx.checks:check()
        self.assertEqual(ctx.tmpfs_payload_write_bytes,0);self.assertEqual(len(ctx.checks),4)
        path=root/'evidence/resource-plugin-roots.json';path.write_bytes(path.read_bytes()+b'\n')
        with self.assertRaises(RuntimeError):
            for check in ctx.checks:check()
    def test_actual_backend_version_and_configured_roots_are_verified_before_requests(self):
        raw=('Type: io.containerd.content.v1\nID: content\nExports:\n root '+R.ROOTS['content']+'\n'
            'Type: io.containerd.snapshotter.v1\nID: overlayfs\nExports:\n root '+R.ROOTS['snapshot']+'\n').encode()
        expected={'version':'29.9.2','commit':R.ENGINE_COMMIT,'containerd':{'version':'v2.4.2','commit':R.CONTAINERD_COMMIT}}
        calls=[]
        def command(ctx,argv,**kw):calls.append(argv);return json.dumps(expected).encode() if argv[1]=='version' else raw
        M=SimpleNamespace(DOCKER=['/usr/bin/docker'],document=json.loads,command=command,
            store_view=lambda ctx:{'root':'/var/lib/docker','driver':'overlayfs','version':'29.9.2'})
        resource=object.__new__(R.WebResource);resource.ctx=SimpleNamespace(engine=M);resource.check=Mock()
        resource.verify_backend();self.assertEqual(len(calls),2);self.assertNotIn('load',str(calls));self.assertNotIn('Env',str(calls))
        expected['containerd']['commit']='0'*40
        with self.assertRaises(RuntimeError):resource.verify_backend()
    def test_snapshot_scope_actual_lifetime_is_admitted_not_only_generated_flags(self):
        resource=object.__new__(R.WebResource);resource.phase='snapshot';resource.nonce='a'*32
        valid=b'RuntimeMaxUSec=15min\nTimeoutStopUSec=5s\nKillMode=control-group\nSendSIGKILL=yes\n'
        command=Mock(return_value=valid);resource.ctx=SimpleNamespace(engine=SimpleNamespace(command=command))
        resource.verify_lifetime();self.assertIn('market-images-web-snapshot-'+'a'*32+'.scope',command.call_args.args[1])
        for raw in (valid.replace(b'15min',b'infinity'),valid.replace(b'5s',b'infinity'),valid.replace(b'control-group',b'process'),
                    valid.replace(b'yes',b'no'),valid+b'RuntimeMaxUSec=15min\n'):
            command.return_value=raw
            with self.assertRaises(RuntimeError):resource.verify_lifetime()
        tree=ast.parse((ROOT/'deploy/maintenance/canonical_app_candidate_images_v3.py').read_bytes())
        collect=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='collect')
        text=ast.unparse(collect);self.assertLess(text.index('verify_lifetime()'),text.index('verify_backend()'))
    def test_web_memory_failure_precedes_guard_and_unknown_loader_failure_never_closes_guard(self):
        root,M=self.workspace();ctx=self.f.basic(A,M,root,'web');ctx.op=self.op(M,root,'admit-existing-web');ctx.baseline=ctx.op['baselines']['web']
        ctx.baseline.update(daemon={},store={});ctx.begin=Mock();machine=self.f.private(root/'public-machine-id',b'e'*32)
        with patch.object(A,'MACHINE',machine),patch.object(M,'daemon_policy',return_value={}),patch.object(M,'store_view',return_value={}), \
                patch.object(M,'stable',return_value={'image_map':[]}),patch.object(M,'Guard') as guard:
            ctx.capacity=Mock(side_effect=RuntimeError('app_resource_contract'))
            with self.assertRaisesRegex(RuntimeError,'app_resource_contract'):A.run(ctx,True)
            guard.assert_not_called();ctx.begin.assert_not_called();self.assertFalse(ctx.requested)
        ctx.op['phase']='load-web';ctx.args.phase='load-web';ctx.capacity=lambda:None;ctx.baseline['machine_id']='e'*32
        real_path=Path
        with patch.object(M,'Path',side_effect=lambda value:machine if str(value)=='/etc/machine-id' else real_path(value)), \
                patch.object(M,'daemon_policy',return_value={}),patch.object(M,'store_view',return_value={}),patch.object(M,'stable',return_value={'image_map':[]}), \
                patch.object(M,'Guard') as guard,patch.object(M,'load_web',side_effect=RuntimeError('command_outcome_unknown')):
            with self.assertRaisesRegex(RuntimeError,'command_outcome_unknown'):A.run(ctx,True)
            guard.assert_called_once();guard.return_value.finish.assert_not_called()

if __name__=='__main__':unittest.main()
