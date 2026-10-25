#!/usr/bin/env python3
"""Two fixed datastore images, image stores only; no serving or data transition.

The isolated, byte-pinned Market06 engine supplies the guarded transfer/load
protocol. This adapter supplies fresh operation03 intake and datastore evidence.
"""
from contextlib import ExitStack,contextmanager
from hashlib import sha256
from pathlib import Path
from types import ModuleType
import argparse,json,os,re,stat,time

OPERATION='security-datastore-candidate-images-20261025-03'
CONFIRM='DATASTORE-IMAGE-STORES-ONLY-20261025-03'
WORKER='datastore_candidate_images_v3.py'
MACHINE=Path('/etc/machine-id')
ROOTS={'bot':Path('/root/trading-bot/trading_bot/.local')/OPERATION,'web':Path('/srv/trading-bot')/OPERATION}
ENGINE_SHA='1041cc2d04100e2f6aebb95dca30c2339de41342b350ef44522c05277e9c146e'
HEALTH_POLICY_SHA='2e520dcb1ac3ab9b0da96959caad3035170d792284bf3df531f128f43a3f9c86'
HEALTH_POLICY_NAME='runtime_health_observation_policy.py'
HEALTH_ADAPTER_NAME='market_candidate_images_v3.py'
HEALTH_ADAPTER_SHA='5017b7546a3f117e74fdd6314582e19e39395ae1ac183149be924ba720d68aac'
CATALOG_SHA='fc89cd6e75ee1b2b973b9436e55d05f41879e7f52be9cfe583acdecc5fe42263'
HELPERS={
 'deploy/maintenance/datastore_candidate_contract.py':{'bytes':38382,'sha256':'d210020e24fedd4986803a3fe2618a5016ecd99f9e3ca9588af02d77a967e17d'},
 'deploy/maintenance/runtime_image_archive_transport.py':{'bytes':35148,'sha256':'20c4b90f1c719d4cdee69df8ad1c31c2bcd663b99619b1d58dcbafa1eafecad2'},
 'scripts/admit_security_component_image_reuse.py':{'bytes':27518,'sha256':'9adddae3d6966443f33040f54a57ce597a1816af918e8c606557801d27bf4025'},
 'scripts/compose_market_security_os_candidate.py':{'bytes':52231,'sha256':'c53cfd7ae4570314ef7f13d43ecd73de26436cf56407676ac2fe24d19dc1caa9'}}
IMAGE_PINS={
 'postgres':(40142076769,12578982553,114543515,'7d22f13f6cb225efe325c297e777536973f7de1082da12629ca2df44a1f601da','sha256:49a811b28ed9289d4e981c8f6cd36e1cf05259653a858e40df0cb6d6413c9aa1'),
 'redis':(40142214416,12578882888,18444160,'2faa867c2f3863740e823f0e20f8dc867d9ce04f33367083580fdbfd4392b97d','sha256:98c4c0abd245b4b65178494e4727c69c63a28ed604cfb7dbf74205203a987015')}
SOURCE_ID=[2064,1835555,33152,0,0,1,0,1787336928392178187,1788695466612272194]

def need(ok,code):
    if not ok:raise RuntimeError(code)
def derive_catalog(D):
    need(set(D.PROFILES)==set(IMAGE_PINS),'catalog_binding');profiles=[]
    for name in sorted(IMAGE_PINS):
        s=D.PROFILES[name]
        need((s['run_id'],s['image_artifact_id'],s['image_zip_bytes'],s['image_zip_sha256'],s['config_id'])==IMAGE_PINS[name]
            and s['profile']==name,'catalog_binding')
        profiles.append({'profile':name,'role':name,'ci_run_id':s['run_id'],
            'candidate_config_id':s['config_id'],'candidate_rootfs':list(s['layers']),'archive':dict(s['archive']),
            'image_artifact':{'id':s['image_artifact_id'],'size_in_bytes':s['image_zip_bytes'],'digest':'sha256:'+s['image_zip_sha256']},
            'evidence_artifact':{'size_in_bytes':s['evidence_bytes'],'digest':'sha256:'+s['evidence_sha256']},
            'public_export_bytes':s['export_bytes'],'public_export_sha256':s['export_sha256'],'security_accepted':False})
    return {'schema':'datastore_image_store_catalog/2','operation':OPERATION,'engine_sha256':ENGINE_SHA,'helpers':{k:dict(v) for k,v in HELPERS.items()},'profiles':profiles}

@contextmanager
def engine_source(path):
    """Bootstrap only the fixed public engine; retain its FD and parent proof."""
    ancestor=[]
    for p in path.parents:
        s=p.lstat();need(p.resolve(strict=True)==p and stat.S_ISDIR(s.st_mode) and s.st_uid==s.st_gid==0
            and (not s.st_mode&0o022 or p==Path('/tmp') and stat.S_IMODE(s.st_mode)==0o1777),'protected_parent')
        ancestor.append((p,(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid)))
    need(path.resolve(strict=True)==path,'source_path');fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    identity=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
    try:
        before=identity(os.fstat(fd));need(before[2:6]==(0o100600,0,0,1) and 0<before[6]<=131072,'source_metadata')
        raw=os.pread(fd,before[6]+1,0);need(len(raw)==before[6] and sha256(raw).hexdigest()==ENGINE_SHA,'source_sha256')
        def check():
            need(all(p.resolve(strict=True)==p and identity(p.lstat())[:5]==old for p,old in ancestor),'protected_parent_changed')
            need(before==identity(os.fstat(fd))==identity(path.lstat()) and sha256(os.pread(fd,before[6]+1,0)).hexdigest()==ENGINE_SHA,'source_changed')
        check();primary=False
        try:yield raw,check
        except BaseException:primary=True;raise
        finally:
            try:check()
            except Exception:
                if not primary:raise
    finally:os.close(fd)

def inventory(M,site,rows):
    count,running=(15,15) if site=='bot' else (27,16)
    M.need(type(rows) is list and len(rows)==count and len({r['id'] for r in rows})==count
        and all(type(r['running']) is bool for r in rows) and sum(r['running'] for r in rows)==running,'container_inventory')

def selected_evidence(ctx):
    M=ctx.engine;s=ctx.D.PROFILES[ctx.spec['profile']]
    raw,check=M.seal(ctx.stack,ctx.root/'evidence'/(s['profile']+'.zip'),s['evidence_sha256'],1048576)
    ctx.checks.append(check);M.need(len(raw)==s['evidence_bytes'],'evidence_size')
    return ctx.D.evidence(raw,s)

def configure(M,contract,checks):
    """Change globals only in this private ModuleType, never an imported engine."""
    M.OPERATION=OPERATION;M.CONFIRM=CONFIRM;M.TOOLS=dict(ROOTS);M.CATALOG_SHA=CATALOG_SHA
    M.runtime_contract=contract;M.bootstrap_checks=list(checks)
    M.PUBLIC_CODES=M.PUBLIC_CODES|{'catalog_binding','baseline_scope','health_policy_binding','attempt_inventory'}
    snapshot,remote,image,source_archive=M.snapshot,M.remote,M.image,M.source_archive
    def observe(ctx,admission=None):
        value=snapshot(ctx,admission);inventory(M,ctx.site,value['containers']);return value
    def peer(ctx,phase):
        argv=remote(ctx,phase);old=str(ROOTS['web']/'market_candidate_images.py')
        M.need(argv.count(old)==1,'source_path');return [str(ROOTS['web']/WORKER) if x==old else x for x in argv]
    def no_legacy(ctx,admitted):
        M.need(ctx.op['admission_sha256']!=M.LEGACY_A37_RECEIPT,'source_admission');return False
    def measured(ctx,ident,admission):
        return {**image(ctx,ident,admission),'schema':'datastore_preloaded_image/3','operation':OPERATION,
            'nonce':ctx.op['nonce'],'controller_sha256':ctx.op['worker_sha256'],'engine_sha256':ENGINE_SHA,
            'health_policy_sha256':HEALTH_POLICY_SHA,'health_adapter_sha256':HEALTH_ADAPTER_SHA,
            'runtime_health_issues_open':True,'capture_issue_open':True,'serving_validation':'NOT_TESTED',**M.health_details(ctx)}
    def archive(ctx):
        value=source_archive(ctx);historical_health(ctx,value);return value
    M.snapshot=observe;M.remote=peer;M.selected_evidence=selected_evidence;M.legacy_a37=no_legacy;M.image=measured;M.source_archive=archive
    return M

def load_engine(stack,site):
    root=ROOTS[site];path=root/'source/market_candidate_images.py'
    raw,check=stack.enter_context(engine_source(path));M=ModuleType('_datastore_images_engine03');M.__file__=str(path)
    exec(compile(raw,str(path),'exec'),M.__dict__)
    name='datastore_candidate_contract.py';pin=HELPERS['deploy/maintenance/'+name]['sha256']
    raw,contract_check=M.seal(stack,root/'source'/name,pin);D=ModuleType('_datastore_images_contract03');D.__file__=str(root/'source'/name)
    exec(compile(raw,D.__file__,'exec'),D.__dict__)
    configure(M,D,(check,contract_check));return install_policy(stack,root,M)


def install_policy(stack,root,M):
    modules={};checks=[]
    for name,pin in ((HEALTH_POLICY_NAME,HEALTH_POLICY_SHA),(HEALTH_ADAPTER_NAME,HEALTH_ADAPTER_SHA)):
        M.need(type(pin) is str and re.fullmatch('[a-f0-9]{64}',pin),'health_policy_binding')
        path=root/'source'/name;raw,check=M.seal(stack,path,pin,131072);checks.append(check)
        def final(check=check):
            primary=__import__('sys').exc_info()[0] is not None
            try:check()
            except BaseException:
                if not primary:raise
        stack.callback(final);module=ModuleType('_private_'+Path(name).stem);module.__file__=str(path)
        exec(compile(raw,str(path),'exec'),module.__dict__);modules[name]=module
    M.runtime_health_policy=modules[HEALTH_POLICY_NAME];M.runtime_health_adapter=modules[HEALTH_ADAPTER_NAME]
    return M.runtime_health_adapter.install_health_policy(M,M.runtime_health_policy,policy_sha256=HEALTH_POLICY_SHA,checks=checks)

def prior_attempts(ctx):
    """No new nonce may escape ANY retained same-profile/same-phase attempt."""
    M=ctx.engine;parent=ctx.root/'attempts';proof=M.parents(parent);fd=os.open(parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        before=M.identity(os.fstat(fd));M.need(before[2:5]==[0o40700,0,0],'attempt_inventory')
        prefix=ctx.op['profile']+'-'+ctx.op['phase'];count=0
        with os.scandir(fd) as entries:
            for entry in entries:
                count+=1;M.need(count<=1024,'attempt_inventory')
                M.need(entry.name!=prefix and not entry.name.startswith(prefix+'-'),'previous_attempt_requires_reconciliation')
        M.check_parents(proof);M.need(M.identity(os.fstat(fd))==M.identity(parent.lstat())==before,'attempt_inventory')
        return {'schema':'image_store_prior_attempt_disposition/1','operation':OPERATION,'profile':ctx.op['profile'],
            'phase':ctx.op['phase'],'nonce':ctx.op['nonce'],'same_profile_phase_attempts':0,
            'prior_attempts_absent':True,'directory_identity':before[:5],'security_accepted':False}
    finally:os.close(fd)

def historical_health(ctx,value):
    """Authenticate producer observations as history, never current LOAD health."""
    M=ctx.engine;keys={'health_policy_sha256','runtime_health_observations','peer_runtime_health_observations'}
    if not keys&set(value):return
    M.need(value.get('health_policy_sha256')==HEALTH_POLICY_SHA and value.get('runtime_health_issues_open') is True,
        'health_policy_binding')
    rows=value.get('runtime_health_observations',[]);peer=value.get('peer_runtime_health_observations',[])
    M.need(type(rows) is list and len(rows)<=128 and type(peer) is list and not peer,'runtime_health_observation')
    if not rows:return
    M.need(all(type(row) is dict and type(row.get('nonce')) is str and re.fullmatch('[a-f0-9]{32}',row['nonce']) for row in rows),
        'runtime_health_observation')
    nonces={row['nonce'] for row in rows};M.need(len(nonces)==1,'runtime_health_observation')
    producer_pin=value['operation_sha256'];M.need(type(producer_pin) is str and re.fullmatch('[a-f0-9]{64}',producer_pin),'runtime_health_observation')
    path=ctx.root/'controls'/(next(iter(nonces))+'.json');raw,check=M.seal(ctx.stack,path,producer_pin,M.LIMIT);ctx.checks.append(check)
    producer=M.document(raw)
    M.need(producer['schema']=='datastore_image_store_control/3' and producer['operation']==OPERATION and producer['profile']==ctx.spec['profile']
        and producer['phase']=='receive-web' and producer['health_policy_sha256']==HEALTH_POLICY_SHA
        and producer['worker_sha256']==ctx.op['worker_sha256'],'runtime_health_observation')
    baseline=producer['baselines']['web'];M.need(baseline['schema']=='datastore_image_store_baseline/3' and baseline['operation']==OPERATION and baseline['site']=='web'
        and baseline['health_policy_sha256']==HEALTH_POLICY_SHA
        and baseline['controller_sha256']==producer['worker_sha256'],'runtime_health_observation')
    scope={'operation':OPERATION,'operation_sha256':producer_pin,'health_policy_sha256':HEALTH_POLICY_SHA,'nonce':producer['nonce'],
        'site':'web','not_before':producer['not_before'],'deadline':producer['deadline']}
    M.need(M.runtime_health_policy.admit_observations(scope,rows,baseline['containers']) is not None,'runtime_health_observation')

def safe_failure_details(M,ctx):
    try:
        value=M.failure_details(ctx) if M is not None else {}
        if type(value) is not dict:raise ValueError('diagnostic_shape')
        if M is not None:M.public(value)
        return value
    except Exception:return {'runtime_health_diagnostic_unavailable':True}

def context(args,stack,M):
    class Context(M.Context):
        def __init__(self):
            M.need(os.geteuid()==os.getegid()==0,'root_required')
            self.engine=M;self.args,self.stack,self.site=args,stack,args.site
            self.root=ROOTS[self.site];self.checks=list(M.bootstrap_checks);self.budget=0;self.requested=False
            info=self.root.lstat();M.need(self.root.resolve(strict=True)==self.root and stat.S_ISDIR(info.st_mode)
                and info.st_uid==info.st_gid==0 and stat.S_IMODE(info.st_mode)==0o700,'tools_directory')
            raw,check=M.seal(stack,self.root/'datastore_candidate_images.json',CATALOG_SHA);self.checks.append(check)
            self.catalog=M.document(raw);M.need(M.runtime_contract.same(self.catalog,derive_catalog(M.runtime_contract)),'catalog_binding')
            self.modules={}
            for path,pin in self.catalog['helpers'].items():
                name=Path(path).name;raw,check=M.seal(stack,self.root/'source'/name,pin['sha256']);self.checks.append(check)
                M.need(len(raw)==pin['bytes'],'source_metadata');module=ModuleType('_datastore_images_'+Path(name).stem)
                module.__file__=str(self.root/'source'/name);exec(compile(raw,module.__file__,'exec'),module.__dict__);self.modules[name]=module
            self.D=self.modules['datastore_candidate_contract.py'];self.T=self.modules['runtime_image_archive_transport.py']
            self.L=self.modules['admit_security_component_image_reuse.py'];self.M=self.modules['compose_market_security_os_candidate.py']
            raw,check=M.seal(stack,args.control,args.control_sha256,M.LIMIT);self.checks.append(check);self.op=M.document(raw);self.pin=args.control_sha256
            op=self.op;M.need(set(op)=={'schema','operation','phase','profile','nonce','not_before','deadline','worker_sha256',
                'baselines','fences','admission','admission_sha256','health_policy_sha256'},'control_fields')
            M.need(op['schema']=='datastore_image_store_control/3' and op['operation']==OPERATION and op['phase'] in M.PHASES
                and type(op['nonce']) is str and re.fullmatch('[a-f0-9]{32}',op['nonce'])
                and args.control==self.root/'controls'/(op['nonce']+'.json'),'control_scope')
            M.need(type(op['health_policy_sha256']) is str and op['health_policy_sha256']==HEALTH_POLICY_SHA,'health_policy_binding')
            M.need(set(op['baselines'])==set(op['fences'])=={'bot','web'},'baseline_sites')
            M.need(self.D.same(op['fences']['bot'].get('source'),SOURCE_ID),'permanent_fence_identity')
            M.need(type(op['not_before']) is int and type(op['deadline']) is int
                and op['not_before']<=time.time()<op['deadline']<=op['not_before']+900,'deadline')
            self.end=time.monotonic()+op['deadline']-time.time()
            raw,check=M.seal(stack,self.root/WORKER,op['worker_sha256']);self.checks.append(check)
            chosen=[s for s in self.catalog['profiles'] if s['profile']==op['profile']];M.need(len(chosen)==1,'profile_scope')
            self.spec={**chosen[0],'site':'native' if op['phase']=='load-native' else 'web'}
            M.need(args.phase==op['phase'] or self.site=='web' and args.phase=='stream-web' and op['phase']=='load-native','phase_scope')
            self.work=self.root/'attempts'/(op['profile']+'-'+op['phase']+('' if op['phase']=='receive-web' else '-'+op['nonce']));self.baseline=op['baselines'][self.site]
            self.prior_attempt_disposition=prior_attempts(self)
            for site,b in op['baselines'].items():
                M.need(b['schema']=='datastore_image_store_baseline/3' and b['operation']==OPERATION and b['site']==site
                    and b['controller_sha256']==op['worker_sha256'] and b['health_policy_sha256']==HEALTH_POLICY_SHA and b['runtime_health_issues_open'] is True
                    and b['security_accepted'] is False,'baseline_scope');inventory(M,site,b['containers'])
                initial=b.get('initial_containers');cloned=M.runtime_health_policy.comparison_clone(site,initial,b['containers'])
                M.need(cloned is not False and self.D.same(initial,cloned),'baseline_scope')
                M.need(type(b.get('initial_observed_at')) is int and type(b['observed_at']) is int
                    and 0<=b['initial_observed_at']<=b['observed_at'] and b['observed_at']-b['initial_observed_at']<=60,'baseline_scope')
                M.need(type(b['observed_at']) is int and 0<=time.time()-b['observed_at']<=300,'baseline_freshness')
            M.scope(self.site,args.phase,op['nonce']);self.check()
            unit='market-images-'+self.site+'-'+args.phase+'-'+op['nonce']+'.scope'
            values=M.command(self,['/usr/bin/systemctl','show',unit,'--property=RuntimeMaxUSec,TimeoutStopUSec,KillMode,SendSIGKILL'],maximum=4096).decode()
            values=dict(line.split('=',1) for line in values.splitlines())
            M.need(set(values)=={'RuntimeMaxUSec','TimeoutStopUSec','KillMode','SendSIGKILL'}
                and values['RuntimeMaxUSec'] in {'15min','15min 0s','900s'} and values['TimeoutStopUSec']=='5s'
                and values['KillMode']=='control-group' and values['SendSIGKILL']=='yes','scope_lifetime')
        def begin(self):
            self.prior_attempt_disposition=prior_attempts(self)
            super().begin();self.publish('prior-attempt-disposition.json',self.prior_attempt_disposition)
    return Context()

def collect(M,site,source_pin):
    M.SUBPHASE='snapshot_source_admission';M.need(os.geteuid()==os.getegid()==0,'root_required')
    with ExitStack() as stack:
        _,check=M.seal(stack,ROOTS[site]/WORKER,source_pin)
        class Observer:
            def check(self):
                M.need(time.monotonic()<self.end,'deadline');check()
                for held in M.bootstrap_checks:held()
            capacity=check
        ctx=Observer();ctx.site=site;ctx.end=time.monotonic()+60
        store=M.store_view(ctx)
        with MACHINE.open('rb') as f:host=f.read(65).strip().decode()
        M.need(re.fullmatch('[a-f0-9]{32}',host),'host_identity')
        before=M.snapshot(ctx);initial_at=int(time.time());daemon=M.daemon_policy(ctx);temporary=M.temp_metadata();after=M.snapshot(ctx)
        clone=M.runtime_health_policy.comparison_clone(site,before['containers'],after['containers'])
        M.need(clone is not False and M.runtime_contract.same(before,{**after,'containers':clone}),'snapshot_runtime_unstable');ctx.check()
        return {**after,'schema':'datastore_image_store_baseline/3','operation':OPERATION,'observed_at':int(time.time()),
            'site':site,'machine_id':host,'store':store,'daemon':daemon,'temporary_storage':temporary,
            'controller_sha256':source_pin,'health_policy_sha256':HEALTH_POLICY_SHA,'initial_containers':before['containers'],'initial_observed_at':initial_at,
            'runtime_health_issues_open':True,'security_accepted':False}

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--site',choices=('bot','web'),required=True)
    parser.add_argument('--phase',choices=('receive-web','load-web','load-native','stream-web','snapshot'),required=True)
    parser.add_argument('--control',type=Path);parser.add_argument('--control-sha256');parser.add_argument('--source-sha256');parser.add_argument('--apply')
    args=parser.parse_args(argv);M=None;ctx=None
    try:
        with ExitStack() as stack:
            M=load_engine(stack,args.site);M.need(args.apply in (None,CONFIRM),'confirmation')
            if args.phase=='snapshot':
                M.need(args.apply is None,'snapshot_readonly');result=collect(M,args.site,args.source_sha256)
            else:
                ctx=context(args,stack,M);M.signal.alarm(max(1,int(ctx.end-time.monotonic())))
                result=M.run(ctx,args.apply is not None)
                if result is not None:result={**result,**M.observation_details(ctx),'health_policy_sha256':HEALTH_POLICY_SHA,
                    'runtime_health_issues_open':True,'prior_attempt_disposition':ctx.prior_attempt_disposition}
                ctx.check()
        if result is not None:M.public(result);print(json.dumps(result,sort_keys=True))
        return 0
    except Exception as error:
        code=error.args[0] if M is not None and type(error) is RuntimeError and len(error.args)==1 and type(error.args[0]) is str and error.args[0] in M.PUBLIC_CODES else 'refused_or_unknown'
        if code=='refused_or_unknown' and ctx is not None:code=ctx.D.error_code(error)
        if args.phase!='stream-web':
            details=M.exception_metadata(error) if M is not None else {'exception_class':'other','os_errno':None}
            print(json.dumps({'status':'refused_or_unknown','phase':args.phase,'subphase':getattr(M,'SUBPHASE','control_admission'),
                'code':code,'retry_allowed':False,'security_accepted':False,'health_policy_sha256':HEALTH_POLICY_SHA,
                'runtime_health_issues_open':True,'runtime_health_observations':[],**details,**safe_failure_details(M,ctx)},sort_keys=True))
        return 1
    finally:
        if M is not None:M.signal.alarm(0)

if __name__=='__main__':raise SystemExit(main())
