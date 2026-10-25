"""Finite retained-container choreography with synthetic public runtime only."""
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from hashlib import sha256
import base64,fcntl,importlib.util,json,os
from pathlib import Path
from types import SimpleNamespace
import tempfile,unittest
from unittest.mock import Mock,patch
import yaml

REPO=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('_legacy_parking_test',REPO/'deploy/maintenance/security_legacy_parking.py')
M=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(M)
def encode(v):return(json.dumps(v,sort_keys=True,separators=(',',':'))+'\n').encode()
def reader_bindings():
    """Synthetic public controls only; never claimed as actual host observations."""
    result={}
    for name in M.READER_UNITS:
        base,suffix=name.rsplit('.',1);service=suffix=='service';peer=base+('.timer' if service else '.service')
        body=b'[Unit]\nDescription=Explicit inert production-reader test fixture\n'
        path=str(M.READER_SYSTEMD/name)
        proof={'schema':'security_production_reader_source_classification/1','unit':name,
            'role':'production_model_projection','source_commit':'a'*40,
            'versioned_sources':{p:'b'*64 for p in set(M.READER_FAMILIES[base])|M.READER_COMMON_SOURCES},
            'root_public_source_equivalence_reviewed':True,'legacy_container_driver':False,'credential_values_present':False}
        result[name]={'classification':proof,'policy':{'Id':name,'LoadState':'loaded','UnitFileState':'static' if service else 'enabled',
            'Type':'oneshot' if service else '', 'NeedDaemonReload':'no','RefuseManualStart':'no',
            'TriggeredBy':peer if service else '', 'Triggers':'' if service else peer,
            'FragmentPath':path,'DropInPaths':'','Transient':'no'},'public_files':{path:{
                'identity':[1,2,0o100644,0,0,1,len(body),4,5],'sha256':sha256(body).hexdigest(),
                'public_bytes_b64':base64.b64encode(body).decode(),'versioned_source':M.READER_FAMILIES[base][0]}}}
    return result
def context():
    now=datetime(2026,10,25,2,tzinfo=timezone.utc);sites={};counter=1
    for site,count in (('bot',15),('web',27)):
        rows=[]
        ids=[i for i,v in M.TARGETS.items() if v[0]==site]
        for i in ids:
            rows.append({'id':i,'name':M.TARGETS[i][1],'project':M.TARGETS[i][2]})
        while len(rows)<count:
            rows.append({'id':format(counter,'064x'),'name':'/future-active-'+str(counter),'project':'future-production'});counter+=1
        for n,row in enumerate(rows):
            stopped=site=='web' and (len(ids)<=n<len(ids)+10 or n==count-1)
            row.update(image='sha256:'+format(n+100,'064x'),service='fixture',running=not stopped,paused=False,restarting=False,restart_policy=deepcopy(M.NO) if stopped else {'Name':'unless-stopped','MaximumRetryCount':0},mounts=[{'Type':'bind','Source':'/ordinary/staging-data/active-market','Destination':'/runtime','RW':True}],network_mode='ordinary',networks={'ordinary':{'IPAMConfig':None,'Aliases':['ordinary'],'Links':None,'DriverOpts':None}},ports={},pid_mode='',privileged=False,read_only=False,health=None,exit_code=0,oom_killed=False,error_empty=True)
        serving=[r for r in rows if r['id'] not in M.TARGETS and r['running']]
        for row,service in zip(serving,('app','sync_worker','bot') if site=='bot' else ('app','sync_worker')):
            row.update(name='/trading_bot_'+service,service=service,project='trading_bot' if site=='bot' else 'current',image='sha256:8da3e404d01cc239606d1c79a30c4701c5a2ef2d103787b68b9ec221ae723672' if site=='bot' else 'sha256:c6106a78b1ef830103fddf351635028773eb0efa59281449a3a9f062ec946e7c')
        roots=['/srv/trading-bot/production-data/docker-data'] if site=='bot' else ['/var/lib/docker','/var/lib/containerd']
        sites[site]={'machine_id':'a'*32,'runtime':rows,'marker_identity':[1,2,0o100600,0,0,1,32,4,5],'source_mutex_identity':[1,3,0o100600,0,0,1,0,4,5] if site=='bot' else None,'drivers':{},'production_readers':reader_bindings() if site=='bot' else {},'qa_ids':[r['id'] for r in rows[len(ids):len(ids)+10]] if site=='web' else [],'store_roots':[{'path':p,'identity':[1,5,0o40700,0,0],'uuid':'0'*8+'-'+('0'*4+'-')*3+'0'*12} for p in roots]}
    profile={'schema':'security_legacy_parking_profile/1','created_at_utc':(now-timedelta(seconds=30)).isoformat(),'app_controller_nonce':'a'*32,'app_controller_source_commit':'c'*40,'sites':sites,'legacypark_FINAL':False,'security_accepted':False}
    window={'schema':'security_legacy_parking_window/1','operation_id':M.OP,'action':'park','nonce':'b'*32,'not_before_utc':now.isoformat(),'deadline_utc':(now+timedelta(seconds=900)).isoformat(),'controls':{'script':'1'*64,'playbook':'2'*64,'helper':M.HELPER_SHA,'inventory':M.INVENTORY_SHA,'config':M.CONFIG_SHA},'permissions':{'restart_policy_only':True,'stop_or_owned_start_only':True,'remove':False,'volumes':False,'images':False,'qa':False,'active_market':False},'legacypark_FINAL':False,'security_accepted':False,'data_deletion_authorized':False}
    app={'schema':'security_consumer_controller_result/2','operation_id':'security-consumers-20261024-02','nonce':'a'*32,'source_commit':'c'*40,'status':'five_targets_verified_global_gates_pending','action':'apply','fence_cleanup_confirmed':True,'source_mutex_preserved':True,'reservation_removed':True,'security_accepted':False,'global_security_eligible':False,'raw_gate_passed':False,'raw_gate_waived':False}
    return now,window,profile,app
def primitives():
    def publish(src,dst):os.link(src,dst);os.unlink(src)
    def write(fd,raw):
        while raw:raw=raw[os.write(fd,raw):]
    return SimpleNamespace(private_parent=lambda p:None,write_all=write,publish_noreplace=publish,decode=json.loads,document=lambda p:json.loads(Path(p).read_bytes()),read=lambda p,**k:Path(p).read_bytes())

class ParkingTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        for target in ('subprocess.Popen','subprocess.run','subprocess.call','subprocess.check_output','os.system','socket.socket.connect','socket.create_connection','socket.getaddrinfo','urllib.request.urlopen','ctypes.CDLL','ctypes.PyDLL','signal.signal','signal.alarm','resource.setrlimit'):
            self.stack.enter_context(patch(target,side_effect=AssertionError('ambient host/network denied')))
        self.now,self.window,self.profile,self.app=context();now=self.now
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None):return now
        self.stack.enter_context(patch.object(M,'datetime',Clock))
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.legacy-parking-',dir=REPO)));self.root.chmod(0o700)
        # This module belongs to the required genuine-root fixture CI lane.
        # Never disguise a nonroot fixture's filesystem UID as root.
    def test_exact_eleven_scope_writers_first_and_datastores_first_restore(self):
        self.assertEqual(sum(v[0]=='bot' for v in M.TARGETS.values()),6);self.assertEqual(sum(v[0]=='web' for v in M.TARGETS.values()),5)
        self.assertEqual(len(M.ORDER),len(set(M.ORDER)));self.assertEqual(set(M.ORDER),set(M.TARGETS));self.assertEqual(M.target_order({**self.window,'action':'restore'}),tuple(reversed(M.ORDER)))
        self.assertEqual(M.checked_context(self.window,self.profile,self.app,self.now),self.now+timedelta(seconds=900))
    def test_future_active_app_ids_are_data_and_are_never_parking_targets(self):
        before={r['id'] for d in self.profile['sites'].values() for r in d['runtime'] if r['id'] not in M.TARGETS}
        self.assertTrue(before.isdisjoint(M.TARGETS));self.assertTrue(all('future-active' not in v[1] for v in M.TARGETS.values()))
        M.checked_context(self.window,self.profile,self.app,self.now)
    def test_qa_id_duplicates_native_qa_and_false_as_int_projection_refuse(self):
        for site,qa in (('web',[self.profile['sites']['web']['qa_ids'][0]]*10),('bot',[M.ORDER[0]])):
            p=deepcopy(self.profile);p['sites'][site]['qa_ids']=qa
            with self.assertRaisesRegex(M.Refusal,'qa_'):M.checked_context(self.window,p,self.app,self.now)
        projection={M.ORDER[0]:{'running':False,'restart_policy':deepcopy(M.NO)}}
        self.assertEqual(M.checked_projection(self.window,projection,None),projection)
        for value in ({'running':0,'restart_policy':M.NO},{'running':False,'restart_policy':{'Name':'always','MaximumRetryCount':0}}):
            with self.assertRaisesRegex(M.Refusal,'projection_intent'):M.checked_projection(self.window,{M.ORDER[0]:value},None)
        with self.assertRaisesRegex(M.Refusal,'target_order'):M.checked_projection(self.window,{M.ORDER[1]:projection[M.ORDER[0]]},None)
    def test_false_as_int_qa_resurrection_extra_target_or_stale_profile_refuses(self):
        cases=[]
        w=deepcopy(self.window);w['permissions']['remove']=0;cases.append((w,self.profile,self.app))
        p=deepcopy(self.profile);p['sites']['web']['runtime'][-15]['running']=True;cases.append((self.window,p,self.app))
        p=deepcopy(self.profile);p['sites']['bot']['runtime'][0]['id']='f'*64;cases.append((self.window,p,self.app))
        p=deepcopy(self.profile);p['created_at_utc']=(self.now-timedelta(hours=1)).isoformat();cases.append((self.window,p,self.app))
        a=deepcopy(self.app);a['source_mutex_preserved']=1;cases.append((self.window,self.profile,a))
        for w,p,a in cases:
            with self.subTest(value=(w['permissions'],p['created_at_utc'])):
                with self.assertRaises(M.Refusal):M.checked_context(w,p,a,self.now)
    def test_known_open_peer_health_can_flap_without_waiver_but_other_drift_refuses(self):
        old=deepcopy(self.profile['sites']['web']['runtime']);peer=next(r for r in old if r['id'] not in M.TARGETS and r['running']);peer['id']=next(iter(M.KNOWN_HEALTH));current=deepcopy(old);next(r for r in current if r['id']==peer['id'])['health']='unhealthy'
        self.assertEqual(M.matches_runtime(current,old),[peer['id']]);current[-1]['image']='sha256:'+'f'*64
        with self.assertRaisesRegex(M.Refusal,'runtime_changed'):M.matches_runtime(current,old)
    def target(self,unknown=None,restore=False):
        root=self.root/('case-'+str(len(list(self.root.iterdir()))));root.mkdir(mode=0o700)
        identification=M.ORDER[-1] if restore else M.ORDER[0];site=M.TARGETS[identification][0];profile=deepcopy(self.profile);window=deepcopy(self.window);parent=None
        if restore:
            window['action']='restore';parent={r['id']:deepcopy(r) for d in profile['sites'].values() for r in d['runtime'] if r['id'] in M.TARGETS}
            for d in profile['sites'].values():
                for row in d['runtime']:
                    if row['id'] in M.TARGETS:row.update(running=False,restart_policy=deepcopy(M.NO))
        current=deepcopy(profile['sites'][site]['runtime']);calls=[];started=[];b=primitives()
        def snapshot(s,p,projection,*args):M.matches_runtime(current,M.phase_expected(p,s,projection));return deepcopy(current),[]
        def mutate(b,argv,deadline,fd):
            calls.append(argv);row=next(r for r in current if r['id']==identification)
            if argv[1]=='update':row['restart_policy']=deepcopy(M.NO if not restore else parent[identification]['restart_policy'])
            else:row['running']=restore
            if unknown==argv[1]:raise M.Refusal('parking_rpc_outcome_unknown')
        with patch.object(M,'snapshot',side_effect=snapshot),patch.object(M,'mutation',side_effect=mutate),patch.object(M,'datastore_ready',return_value=True):
            if unknown:
                with self.assertRaisesRegex(M.Refusal,'rpc_outcome_unknown'):M.target_step(site,identification,window,profile,self.app,{},b,root,5,[],parent=parent,before_rpc=lambda:started.append(True))
            else:result=M.target_step(site,identification,window,profile,self.app,{},b,root,5,[],parent=parent,before_rpc=lambda:started.append(True))
        self.assertEqual(len(calls),1 if unknown=='update' else 2);self.assertTrue(started)
        self.assertEqual(calls[0],['/usr/bin/docker','update','--restart='+('unless-stopped' if restore else 'no'),identification])
        if unknown:self.assertFalse((root/('result-'+identification+'.json')).exists());return
        self.assertEqual(calls[1],['/usr/bin/docker',*(['start',identification] if restore else ['stop','--time','60',identification])]);self.assertFalse(result['legacypark_FINAL']);self.assertFalse(result['data_backup_or_restore_proven'])
        self.assertEqual(next(r for r in current if r['id']==identification)['id'],identification)
    def test_known_park_changes_only_exact_policy_and_stopped_intent(self):self.target()
    def test_known_owned_restore_starts_the_retained_datastore_identity(self):self.target(restore=True)
    def test_unknown_update_or_stop_never_retries_or_publishes_success(self):
        for step in ('update','stop'):
            with self.subTest(step=step):self.target(unknown=step)
    def test_permanent_empty_source_flock_is_retained_and_metadata_unchanged(self):
        p=self.root/'source';p.touch(mode=0o600);before=list(M.ident(p.lstat()))
        with ExitStack() as stack:
            fd,check=M.mutex(stack,p,before);other=os.open(p,os.O_RDONLY)
            try:
                with self.assertRaises(BlockingIOError):fcntl.flock(other,fcntl.LOCK_EX|fcntl.LOCK_NB)
            finally:os.close(other)
            check();self.assertEqual(p.read_bytes(),b'')
        self.assertEqual(list(M.ident(p.lstat())),before)
    def test_fresh_guard_never_adopts_foreign_or_identical_replacement(self):
        b=primitives();p=self.root/'guard'
        with ExitStack() as stack:
            _,check=M.guard(stack,b,p,{'nonce':'a'*32});raw=p.read_bytes();q=self.root/'replacement';q.write_bytes(raw);q.chmod(0o600);os.replace(q,p)
            with self.assertRaisesRegex(M.Refusal,'guard_changed'):M.remove_guard(p,check)
            self.assertEqual(p.read_bytes(),raw)
            with self.assertRaisesRegex(M.Refusal,'existing_guard'):M.guard(stack,b,p,{'nonce':'a'*32})
    def test_guard_initial_publication_sync_failure_releases_only_its_owned_file(self):
        b=primitives();p=self.root/'guard';original=os.fsync;failed=False
        def fsync(fd):
            nonlocal failed
            if p.exists() and not failed and stat.S_ISDIR(os.fstat(fd).st_mode):failed=True;raise OSError('ordinary fixture directory sync failure')
            return original(fd)
        import stat
        with ExitStack() as stack,patch.object(os,'fsync',side_effect=fsync):
            with self.assertRaises(OSError):M.guard(stack,b,p,{'nonce':'a'*32})
        self.assertTrue(failed);self.assertFalse(p.exists())
    def coordinator(self,kind='good',mode='apply'):
        base=self.root/('coord-'+kind+'-'+mode);base.mkdir(mode=0o700);main=base/'main';here=main/'deploy/maintenance';here.mkdir(parents=True,mode=0o700);(main/'.local').mkdir(mode=0o700)
        b=primitives();b.MARKER=base/'marker';b.SOURCE=base/'source';b.RESERVATION=base/'reservation';b.PENDING=base/'pending';stop=base/'STOP'
        b.MARKER.write_bytes(b'permanent original authority\n');b.MARKER.chmod(0o600);b.SOURCE.touch(mode=0o600);stop.write_bytes(b'STOP\n');stop.chmod(0o600);marker_before=b.MARKER.read_bytes();source_before=M.ident(b.SOURCE.lstat())
        profile=deepcopy(self.profile);profile['sites']['bot']['marker_identity']=list(M.ident(b.MARKER.lstat()));profile['sites']['bot']['source_mutex_identity']=list(source_before)
        root=main/'.local'/(M.OP+'-'+self.window['nonce']);root.mkdir(mode=0o700);(root/'results').mkdir(mode=0o700)
        staged={'schema':'security_legacy_parking_result/1','nonce':self.window['nonce'],'status':'public_assets_staged','profile_sha256':'b'*64,'window_sha256':'a'*64,'canonical_reservation_removed':True};(root/'controller-stage-result.json').write_bytes(encode(staged))
        args=SimpleNamespace(mode=mode,confirm=M.CONFIRM,window_sha256='a'*64,profile_sha256='b'*64,app_sha256='c'*64)
        calls=[]
        def invoke(args,action,sites,window,projection,b,locks,target=None):
            calls.append((action,sites,target));self.assertEqual(b.MARKER.read_bytes(),marker_before);self.assertEqual(M.ident(b.SOURCE.lstat()),source_before)
            probe=os.open(b.SOURCE,os.O_RDONLY)
            try:
                with self.assertRaises(BlockingIOError):fcntl.flock(probe,fcntl.LOCK_EX|fcntl.LOCK_NB)
            finally:os.close(probe)
            if kind=='stage_fail':return 1
            if action=='target':
                if kind=='unknown':return 1
                value={'schema':'security_legacy_parking_target_result/1','nonce':window['nonce'],'id':target,'action':'park','status':'known_target_verified','projection':{'running':False,'restart_policy':M.NO},'legacypark_FINAL':False,'security_accepted':False}
                (root/'results'/(sites[0]+'-'+target+'.json')).write_bytes(encode(value))
            return 0
        actual_held=M.held
        def held(stack,path,maximum,pin=None):return actual_held(stack,path,maximum,pin) if path==root/'controller-journal.json' else (b'public',lambda:None)
        with patch.object(M,'MAIN',main),patch.object(M,'HERE',here),patch.object(M,'STOP',stop),patch.object(M.os,'geteuid',return_value=0),patch.object(M,'inputs',return_value=(self.window,profile,self.app,b,[],None)),patch.object(M,'held',side_effect=held),patch.object(M,'invoke',side_effect=invoke):
            if kind in {'unknown','stage_fail'}:
                with self.assertRaises(M.Refusal):M.coordinate(args)
            else:result=M.coordinate(args)
        self.assertEqual(M.ident(b.SOURCE.lstat()),source_before);self.assertEqual(b.SOURCE.read_bytes(),b'');self.assertEqual(b.MARKER.read_bytes(),marker_before)
        if kind=='unknown':self.assertTrue(b.RESERVATION.exists());self.assertEqual(len([c for c in calls if c[0]=='target']),1);self.assertFalse((root/'controller-result.json').exists());return
        if kind=='stage_fail':self.assertFalse(b.RESERVATION.exists());return
        self.assertFalse(b.RESERVATION.exists());self.assertEqual(result['status'],'reversibly_parked');self.assertFalse(result['legacypark_FINAL']);self.assertFalse(result['clean_stopped_backup_restore_coverage_proven']);self.assertEqual([c[2] for c in calls if c[0]=='target'],list(M.ORDER))
    def test_coordinator_exact_eleven_order_and_source_preservation(self):self.coordinator()
    def test_coordinator_unknown_rpc_retains_reservation_without_retry(self):self.coordinator('unknown')
    def test_coordinator_failed_public_stage_releases_only_new_reservation(self):self.coordinator('stage_fail','stage')
    def test_same_byte_regular_source_aba_fails_before_next_action(self):
        p=self.root/'public';p.write_bytes(b'public fixture');p.chmod(0o600)
        with ExitStack() as stack:
            _,check=M.held(stack,p,1024,sha256(b'public fixture').hexdigest());q=self.root/'new';q.write_bytes(b'public fixture');q.chmod(0o600);os.replace(q,p)
            with self.assertRaisesRegex(M.Refusal,'public_changed'):check()
    def test_mutable_journal_requires_its_previous_identity_and_never_adopts_replacement(self):
        b=primitives();p=self.root/'journal';value={'schema':'ordinary_public/1','nonce':'a'*32,'phase':'first'};binding=M.write(b,p,value)
        binding=M.write(b,p,{**value,'phase':'second'},exclusive=False,expected=binding);before=p.read_bytes();q=self.root/'replacement';q.write_bytes(before);q.chmod(0o600);os.replace(q,p)
        with self.assertRaisesRegex(M.Refusal,'mutable_control_changed'):M.write(b,p,{**value,'phase':'third'},exclusive=False,expected=binding)
        self.assertEqual(p.read_bytes(),before)
    def test_private_values_and_final_acceptance_cannot_be_persisted(self):
        for value in ({'Env':['private']},{'nested':{'Config':{}}},{'legacypark_FINAL':True},{'security_accepted':1}):
            with self.subTest(value=value):
                with self.assertRaises(M.Refusal):M.write(primitives(),self.root/'never',value)
        self.assertFalse((self.root/'never').exists())
    def test_worker_partial_stage_refuses_and_guard_survives_only_requested_rpc(self):
        for outcome in ('partial_stage','before_rpc','unknown','known'):
            with self.subTest(outcome=outcome):
                root=self.root/outcome;root.mkdir(mode=0o700);b=primitives()
                seal={'schema':'security_legacy_parking_stage/1','nonce':self.window['nonce'],'source_sha256':self.window['controls']['script'],'window_sha256':'a'*64,'profile_sha256':'b'*64,'app_sha256':'c'*64}
                (root/'public-stage.json').write_bytes(encode({} if outcome=='partial_stage' else seal))
                args=SimpleNamespace(site='bot',directory=root,projection='{}',mode='target',target=M.ORDER[0],confirm=M.CONFIRM,window_sha256='a'*64,profile_sha256='b'*64,app_sha256='c'*64)
                calls=[]
                def target(*args,**kwargs):
                    calls.append(True)
                    if outcome in {'unknown','known'}:kwargs['before_rpc']()
                    if outcome!='known':raise M.Refusal('parking_fixture_closed_failure')
                    return {'status':'known_target_verified'}
                with patch.object(M,'HERE',root),patch.object(M,'workspace',return_value=root),patch.object(M.os,'geteuid',return_value=0),patch.object(M,'inputs',return_value=(self.window,self.profile,self.app,b,[],None)),patch.object(M,'coordinator_owner'),patch.object(M,'target_step',side_effect=target):
                    if outcome=='known':self.assertEqual(M.worker(args)['status'],'known_target_verified')
                    else:
                        with self.assertRaises(M.Refusal):M.worker(args)
                self.assertEqual((root/'site-operation.lock').exists(),outcome=='unknown')
                self.assertEqual(bool(calls),outcome!='partial_stage')
                if outcome in {'before_rpc','unknown'}:
                    failure=json.loads((root/('failure-'+M.ORDER[0]+'.json')).read_bytes());self.assertIs(failure['rpc_requested'],outcome=='unknown');self.assertFalse(failure['rpc_retry_allowed'])
    def test_separate_piped_configuration_is_pinned_and_used_by_normal_ansible(self):
        import configparser
        cfg=REPO/'deploy/maintenance/security_legacy_parking_ansible.cfg';raw=cfg.read_bytes();self.assertEqual(sha256(raw).hexdigest(),M.CONFIG_SHA)
        parsed=configparser.ConfigParser();parsed.read_string(raw.decode());self.assertEqual(parsed['ssh_connection']['transfer_method'],'piped');self.assertEqual(parsed['defaults']['host_key_checking'],'True')
        args=SimpleNamespace(primitives=REPO/'scripts/validate_docker_daemon_maintenance.py',window=self.root/'legacy-window.json',window_sha256='a'*64,profile=self.root/'legacy-profile.json',profile_sha256='b'*64,app_result=self.root/'controller-result.json',app_sha256='c'*64,parent=None,parent_sha256=None)
        b=SimpleNamespace(execute_ansible=Mock(return_value=0));self.assertEqual(M.invoke(args,'check',('bot','web'),self.window,{},b,[19,20]),0)
        argv,env,deadline=b.execute_ansible.call_args.args;self.assertEqual(env['ANSIBLE_CONFIG'],str(REPO/'deploy/maintenance/security_legacy_parking_ansible.cfg'));self.assertIn('security_bot:security_web',argv);self.assertEqual(deadline,self.window['deadline_utc']);self.assertEqual(b.execute_ansible.call_args.kwargs['lock_fds'],[19,20])
    def test_diagnostics_accept_only_trusted_closed_codes(self):
        self.assertEqual(M.reason(M.Refusal('parking_fixture_closed_failure')),'parking_fixture_closed_failure')
        for error in (ValueError('private reflection'),M.Refusal('private /path'),M.Refusal('x'*81),M.Refusal('first','second')):self.assertEqual(M.reason(error),'parking_incomplete_or_unknown')
    def test_deploy_driver_enabled_or_foreign_trigger_refuses(self):
        base='LoadState=loaded\nActiveState=inactive\nUnitFileState=disabled\nMainPID=0\nJob=0\nNeedDaemonReload=no\nRefuseManualStart=no\nTriggeredBy=\n'
        for kind in ('good','enabled','trigger'):
            value=base.replace('UnitFileState=disabled','UnitFileState=enabled') if kind=='enabled' else base.replace('TriggeredBy=','TriggeredBy=unreviewed.timer') if kind=='trigger' else base
            def command(argv):return 'stage-staging.service disabled' if argv[1].startswith('list-') else value
            b=SimpleNamespace(command=command)
            if kind=='good':self.assertIn('stage-staging.service',M.drivers(b,'a'*32))
            else:
                with self.assertRaisesRegex(M.Refusal,'driver_not_fenced'):M.drivers(b,'a'*32)
    def test_yaml_embedded_sources_parse_and_stage_never_invokes_worker(self):
        import ast
        tasks=yaml.safe_load((REPO/'deploy/maintenance/security_legacy_parking.yml').read_bytes())[0]['tasks']
        for task in tasks:
            argv=task.get('ansible.builtin.command',{}).get('argv',[])
            if '-c' in argv:ast.parse(argv[argv.index('-c')+1])
        entry=next(t for t in tasks if t['name'].startswith('Execute the first'))
        self.assertEqual(entry['when'],"legacy_action != 'stage'");self.assertEqual(entry['timeout'],900)

    def readers(self, mutation=None, extra_driver=None):
        """Only actual owned files plus explicitly inert systemd responses."""
        persistent=self.root/'reader-systemd';persistent.mkdir(mode=0o700)
        runtime=self.root/'reader-run';runtime.mkdir(mode=0o700)
        self.stack.enter_context(patch.object(M,'READER_SYSTEMD',persistent))
        self.stack.enter_context(patch.object(M,'READER_RUNTIME',runtime))
        bindings=reader_bindings()
        for name,binding in bindings.items():
            for path,bound in binding['public_files'].items():
                file=Path(path);file.write_bytes(base64.b64decode(bound['public_bytes_b64']));file.chmod(0o644)
                bound['identity']=list(M.ident(file.lstat()))
        name=next(n for n in sorted(bindings) if n.endswith('.service'))
        drop=persistent/(name+'.d')/'ordinary-public.conf';drop.parent.mkdir(mode=0o700)
        body=b'[Service]\nEnvironment=PYTHONDONTWRITEBYTECODE=1\n'
        drop.write_bytes(body);drop.chmod(0o644)
        binding=bindings[name];binding['policy']['DropInPaths']=str(drop)
        binding['public_files'][str(drop)]={'identity':list(M.ident(drop.lstat())),
            'sha256':sha256(body).hexdigest(),'public_bytes_b64':base64.b64encode(body).decode(),
            'versioned_source':'deploy/maintenance/boot_mount_gates.yml'}
        calls=[];counts={}
        def command(argv):
            calls.append(argv)
            if argv[1].startswith('list-'):
                return '\n'.join(n+' enabled' for n in sorted(bindings))+('\n'+extra_driver+' enabled' if extra_driver else '')
            unit=argv[2];counts[unit]=counts.get(unit,0)+1
            if unit==extra_driver:
                return 'LoadState=loaded\nActiveState=inactive\nUnitFileState=enabled\nMainPID=0\nJob=0\nNeedDaemonReload=no\nRefuseManualStart=no\nTriggeredBy=\n'
            value=deepcopy(bindings[unit]['policy'])
            if unit.endswith('.service'):
                value.update(ActiveState='activating' if counts[unit]%2 else 'inactive',MainPID='42' if counts[unit]%2 else '0',Job='51' if counts[unit]%2 else '')
            else:
                value.pop('Type');value.update(ActiveState='active',Job='')
            if mutation:mutation(unit,counts[unit],value,bindings)
            return '\n'.join(k+'='+v for k,v in value.items())+'\n'
        return bindings,SimpleNamespace(command=command),calls,name,drop

    def test_exact_eight_source_bound_readers_remain_active_across_natural_oneshot_transitions(self):
        bindings,b,calls,_,_=self.readers()
        result=M.drivers(b,'a'*32,'bot',bindings)
        self.assertEqual(set(result),M.READER_UNITS);self.assertEqual(len(result),8)
        self.assertTrue(all(value['classification']['legacy_container_driver'] is False for value in result.values()))
        for name,row in result.items():
            self.assertEqual(row['ActiveState'],'NATURAL_ONESHOT_TRANSITION' if name.endswith('.service') else 'active')
            self.assertTrue(row['verified_public_files'])
        self.assertTrue(all(argv[1] in {'list-unit-files','list-units','show'} for argv in calls))

    def test_reader_name_without_closed_bindings_or_versioned_source_proof_never_exempts(self):
        original=reader_bindings()
        cases=[]
        missing=deepcopy(original);missing.pop(next(iter(missing)));cases.append(missing)
        foreign=deepcopy(original);foreign['forged-staging-reader.service']=deepcopy(next(iter(foreign.values())));cases.append(foreign)
        forged=deepcopy(original);key=next(iter(forged));forged[key]['classification']['unit']='forged.service';cases.append(forged)
        unbound=deepcopy(original);key=next(iter(unbound));unbound[key]['classification']['versioned_sources']['unreviewed/deploy.py']='f'*64;cases.append(unbound)
        false_as_int=deepcopy(original);key=next(iter(false_as_int));false_as_int[key]['classification']['root_public_source_equivalence_reviewed']=1;cases.append(false_as_int)
        for bindings in cases:
            with self.subTest(bindings=list(bindings)),self.assertRaises(M.Refusal):M.checked_readers(bindings,'bot')
        with self.assertRaises(M.Refusal):M.checked_readers(original,'web')

    def test_real_deploy_driver_enabled_still_refuses_with_all_readers_bound(self):
        bindings,b,_,_,_=self.readers(extra_driver='ordinary-staging-deploy.service')
        with self.assertRaisesRegex(M.Refusal,'legacy_deploy_driver_not_fenced'):M.drivers(b,'a'*32,'bot',bindings)

    def test_reader_policy_reload_type_enablement_trigger_fragment_and_dropin_drift_refuse(self):
        bindings,b,_,name,drop=self.readers()
        good=b.command
        for field,bad in (('NeedDaemonReload','yes'),('Type','simple'),('UnitFileState','enabled'),
                          ('TriggeredBy','unreviewed.timer'),('FragmentPath','/unreviewed/unit.service'),
                          ('DropInPaths',str(drop)+' /unreviewed/extra.conf'),('Transient','yes')):
            def command(argv,field=field,bad=bad):
                raw=good(argv)
                if argv[1]=='show' and argv[2]==name:
                    value=dict(line.split('=',1) for line in raw.splitlines());value[field]=bad
                    return '\n'.join(k+'='+v for k,v in value.items())
                return raw
            with self.subTest(field=field),self.assertRaises(M.Refusal):M.drivers(SimpleNamespace(command=command),'a'*32,'bot',bindings)

    def test_failed_service_or_inactive_timer_is_not_a_natural_transition(self):
        bindings,b,_,_,_=self.readers();good=b.command
        for suffix,state in (('.service','failed'),('.timer','inactive')):
            def command(argv,suffix=suffix,state=state):
                raw=good(argv)
                if argv[1]=='show' and argv[2].endswith(suffix):
                    value=dict(line.split('=',1) for line in raw.splitlines());value['ActiveState']=state
                    return '\n'.join(k+'='+v for k,v in value.items())
                return raw
            with self.subTest(suffix=suffix),self.assertRaises(M.Refusal):M.drivers(SimpleNamespace(command=command),'a'*32,'bot',bindings)

    def test_unknown_changed_unit_environment_is_compared_before_any_actual_hash(self):
        bindings,b,_,name,_=self.readers();binding=bindings[name];path=Path(binding['policy']['FragmentPath'])
        expected=base64.b64decode(binding['public_files'][str(path)]['public_bytes_b64'])
        prefix=b'[Service]\nEnvironment=EXPLICIT_INERT_PRIVATE_FIXTURE\n'
        unknown=prefix+b'#'*(len(expected)-len(prefix));self.assertEqual(len(unknown),len(expected))
        path.write_bytes(unknown);binding['public_files'][str(path)]['identity']=list(M.ident(path.lstat()))
        def public_only(raw):
            self.assertNotEqual(raw,unknown,'unknown unit bytes must never be hashed')
            return sha256(raw)
        with patch.object(M,'sha256',side_effect=public_only):
            with self.assertRaisesRegex(M.Refusal,'production_reader_public_bytes_changed'):M.drivers(b,'a'*32,'bot',bindings)
        self.assertEqual(path.read_bytes(),unknown)

    def test_bound_public_dropin_metadata_change_refuses_without_touching_file(self):
        bindings,b,_,_,drop=self.readers();drop.chmod(0o600)
        with self.assertRaisesRegex(M.Refusal,'production_reader_public_metadata_changed'):M.drivers(b,'a'*32,'bot',bindings)
        self.assertEqual(drop.stat().st_mode&0o777,0o600)

    def test_reader_source_aba_during_second_show_refuses_retained_descriptor(self):
        replacement=None
        def mutate(unit,count,value,bindings):
            nonlocal replacement
            if count==2 and replacement is None:
                path=Path(bindings[unit]['policy']['FragmentPath']);replacement=path.with_name(path.name+'.foreign')
                replacement.write_bytes(path.read_bytes());replacement.chmod(0o644);os.replace(replacement,path)
        bindings,b,_,_,_=self.readers(mutation=mutate)
        with self.assertRaisesRegex(M.Refusal,'public_changed'):M.drivers(b,'a'*32,'bot',bindings)
        self.assertIsNotNone(replacement)

    def test_reader_second_show_policy_drift_refuses(self):
        def mutate(unit,count,value,bindings):
            if count==2:value['NeedDaemonReload']='yes'
        bindings,b,_,_,_=self.readers(mutation=mutate)
        with self.assertRaisesRegex(M.Refusal,'production_reader_loaded_policy_changed'):M.drivers(b,'a'*32,'bot',bindings)


if __name__=='__main__':unittest.main()
