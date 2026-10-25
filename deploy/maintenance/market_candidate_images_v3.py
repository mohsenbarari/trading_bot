"""Versioned OP06 IMAGE-STORE entry over exact1041; old worker/history stay intact."""
from contextlib import ExitStack
from pathlib import Path
from types import ModuleType
from hashlib import sha256
import argparse,json,os,re,signal,stat,sys,time

BASE_SHA='1041cc2d04100e2f6aebb95dca30c2339de41342b350ef44522c05277e9c146e'
POLICY_SHA='2e520dcb1ac3ab9b0da96959caad3035170d792284bf3df531f128f43a3f9c86'
POLICY='runtime_health_observation_policy.py'
WORKER='market_candidate_images_v3.py'
OPERATION='security-market-candidate-images-20261025-06'
ROOTS={'bot':Path('/root/trading-bot/trading_bot/.local')/OPERATION,'web':Path('/srv/trading-bot')/OPERATION}
EXTRAS=frozenset(('health_policy_sha256','runtime_health_issues_open','runtime_health_observations'))

def no_prior_attempt(M,ctx):
    """A fresh nonce never adopts a same-profile/phase old or versioned attempt."""
    path=ctx.root/'attempts';proof=M.parents(path/'child');fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        before=M.identity(os.fstat(fd));M.need(before==M.identity(path.lstat()) and before[3:5]==[0,0],'attempt_inventory')
        prefix=ctx.op['profile']+'-'+ctx.op['phase'];count=0
        with os.scandir(fd) as rows:
            for row in rows:
                count+=1;M.need(count<=4096 and not (row.name==prefix or row.name.startswith(prefix+'-')),'attempt_inventory')
        M.need(before==M.identity(os.fstat(fd))==M.identity(path.lstat()),'attempt_inventory');M.check_parents(proof)
    finally:os.close(fd)

def health_scope(ctx,policy_sha256,site=None):
    return {'operation':ctx.op['operation'],'operation_sha256':ctx.pin,'health_policy_sha256':policy_sha256,
        'nonce':ctx.op['nonce'],'site':site or ctx.site,'not_before':ctx.op['not_before'],'deadline':ctx.op['deadline']}

def install_health_policy(M,P,*,policy_sha256=POLICY_SHA,checks=()):
    """Wrap only a private sealed engine namespace; no shared/imported globals."""
    M.need(P.hex64(policy_sha256),'source_sha256')
    M.bootstrap_checks=[*getattr(M,'bootstrap_checks',()),*checks]
    M.PUBLIC_CODES=M.PUBLIC_CODES|{'runtime_health_policy','runtime_health_observation','health_policy_control'}
    M.health_policy_sha256=policy_sha256
    stable,readiness,details,frame,header,peer_failure,coordinate,publish=(M.stable,M._readiness_guard,
        M.observation_details,M.frame,M.header,M.peer_failure,M.coordinate_web,M.Context.publish)
    def comparison(site,before,after,same):return P.comparison_clone(site,before,after)
    def record(ctx,rows):
        scope=health_scope(ctx,policy_sha256)
        try:values=P.observations(scope,ctx.baseline['containers'],rows,
            min(ctx.op['deadline'],int(time.time())),getattr(ctx,'runtime_health_observations',None))
        except (ValueError,TypeError,KeyError):M.need(False,'runtime_health_policy')
        ctx.runtime_health_observations=values
    def observe(ctx,admission=None):
        value=stable(ctx,admission);record(ctx,value['containers']);return value
    def warming(ctx,value,admission):
        readiness(ctx,value,admission)
        if hasattr(ctx,'baseline'):record(ctx,value['containers'])
    def metadata(ctx):
        out={'health_policy_sha256':policy_sha256,'runtime_health_issues_open':True,'runtime_health_observations':[]}
        if ctx is None:return out
        for name,site in (('runtime_health_observations',ctx.site),('peer_runtime_health_observations','web')):
            values=P.admit_observations(health_scope(ctx,policy_sha256,site),getattr(ctx,name,[]),ctx.op['baselines'][site]['containers'])
            M.need(values is not None,'runtime_health_observation')
            if name=='runtime_health_observations' or values:out[name]=values
        return out
    def peer_values(ctx,value,*,commit=True):
        M.need(type(value) is dict and EXTRAS<=set(value) and value.get('health_policy_sha256')==policy_sha256
            and value.get('runtime_health_issues_open') is True,'runtime_health_observation')
        values=P.admit_observations(health_scope(ctx,policy_sha256,'web'),value.get('runtime_health_observations',[]),
            ctx.op['baselines']['web']['containers'],getattr(ctx,'peer_runtime_health_observations',None))
        M.need(values is not None,'runtime_health_observation')
        if commit:ctx.peer_runtime_health_observations=values
        return values
    def observations(ctx):return {**details(ctx),**metadata(ctx)}
    def send_frame(ctx,fd,value):
        if type(value) is dict and value.get('schema') in {'market_image_stream/1','market_image_stream_complete/1'}:
            extra=metadata(ctx);extra={k:extra[k] for k in EXTRAS}
            M.need(not set(value)&EXTRAS,'stream_binding');value={**value,**extra}
        return frame(ctx,fd,value)
    def admit_header(ctx,value,admitted,*,complete=False):
        values=peer_values(ctx,value,commit=False)
        header(ctx,{k:v for k,v in value.items() if k not in EXTRAS},admitted,complete=complete)
        ctx.peer_runtime_health_observations=values
    def failure(ctx,value,returncode):
        if type(value) is not dict or ('runtime_health_diagnostic_unavailable' in value and value['runtime_health_diagnostic_unavailable'] is not True):return None
        try:values=peer_values(ctx,value,commit=False)
        except (RuntimeError,ValueError,TypeError,KeyError):return None
        result=peer_failure(ctx,{k:v for k,v in value.items() if k not in EXTRAS and k!='runtime_health_diagnostic_unavailable'},returncode)
        if result is None:return None
        ctx.peer_runtime_health_observations=values
        result={**result,'health_policy_sha256':policy_sha256,'runtime_health_issues_open':True,'runtime_health_observations':values}
        if value.get('runtime_health_diagnostic_unavailable') is True:result['runtime_health_diagnostic_unavailable']=True
        return result
    def web_result(ctx):
        value=coordinate(ctx);peer_values(ctx,value);return value
    def receipt(ctx,name,value):
        extra=metadata(ctx)
        M.need(all(k not in value or P.same(value[k],v) for k,v in extra.items()),'runtime_health_observation')
        return publish(ctx,name,{**value,**extra})
    M.processor_health_clone=comparison;M.stable=observe;M._readiness_guard=warming;M.observation_details=observations
    M.frame=send_frame;M.header=admit_header;M.peer_failure=failure;M.coordinate_web=web_result;M.Context.publish=receipt
    M.health_details=metadata;M.admit_peer_observations=peer_values
    return M

def cold(stack,path,pin,maximum=131072):
    identity=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
    proof=[]
    for p in path.parents:
        s=p.lstat()
        if p.resolve(strict=True)!=p or not stat.S_ISDIR(s.st_mode) or s.st_uid!=0 or s.st_gid!=0 or s.st_mode&0o022:raise RuntimeError('protected_parent')
        proof.append((p,identity(s)[:5]))
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC);stack.callback(os.close,fd);before=identity(os.fstat(fd))
    if before[2:6]!=(0o100600,0,0,1) or not 0<before[6]<=maximum:raise RuntimeError('source_metadata')
    raw=os.pread(fd,before[6]+1,0)
    if len(raw)!=before[6] or sha256(raw).hexdigest()!=pin:raise RuntimeError('source_sha256')
    def check():
        if before!=identity(os.fstat(fd)) or before!=identity(path.lstat()) or os.pread(fd,len(raw)+1,0)!=raw:raise RuntimeError('source_changed')
        if any(p.resolve(strict=True)!=p or identity(p.lstat())[:5]!=s for p,s in proof):raise RuntimeError('protected_parent_changed')
    def final():
        primary=sys.exc_info()[0] is not None
        try:check()
        except BaseException:
            if not primary:raise
    check();stack.callback(final);return raw,check

def load_engine(stack,site):
    root=ROOTS[site];raw,check=cold(stack,root/'market_candidate_images.py',BASE_SHA)
    M=ModuleType('_sealed_market1041_health_version');M.__file__=str(root/WORKER)
    exec(compile(raw,str(root/'market_candidate_images.py'),'exec'),M.__dict__)
    raw,policy_check=cold(stack,root/'source'/POLICY,POLICY_SHA)
    P=ModuleType('_sealed_runtime_health_policy');P.__file__=str(root/'source'/POLICY)
    exec(compile(raw,P.__file__,'exec'),P.__dict__)
    M.runtime_health_policy=P
    install_health_policy(M,P,checks=(check,policy_check))
    begin=M.Context.begin
    def fresh_begin(ctx):
        no_prior_attempt(M,ctx);begin(ctx)
        ctx.publish('health-policy-disposition.json',{'schema':'market_image_store_health_disposition/1',
            'operation':ctx.op['operation'],'operation_sha256':ctx.pin,'nonce':ctx.op['nonce'],
            'profile':ctx.spec['profile'],'phase':ctx.op['phase'],'all_same_phase_attempts_absent':True,
            'retry_allowed':False,'security_accepted':False})
        ctx.check()
    M.Context.begin=fresh_begin;M.PUBLIC_CODES=M.PUBLIC_CODES|{'attempt_inventory'}
    remote=M.remote
    def peer(ctx,phase):
        argv=remote(ctx,phase);old=str(ROOTS['web']/'market_candidate_images.py');M.need(argv.count(old)==1,'source_path')
        return [str(ROOTS['web']/WORKER) if value==old else value for value in argv]
    M.remote=peer;return M

def context(args,stack,M):
    """1041 admission, with one explicit policy binding and fresh versioned LOAD work."""
    class Context(M.Context):
        def __init__(self):
            M.need(os.geteuid()==0,'root_required');self.args,self.stack,self.site=args,stack,args.site
            self.root=M.TOOLS[self.site];self.checks=list(M.bootstrap_checks);self.budget=0;self.requested=False
            info=self.root.lstat();M.need(self.root.resolve(strict=True)==self.root and stat.S_ISDIR(info.st_mode)
                and info.st_uid==info.st_gid==0 and stat.S_IMODE(info.st_mode)==0o700,'tools_directory')
            raw,check=M.seal(stack,self.root/'market_candidate_images.json',M.CATALOG_SHA);self.checks.append(check);self.catalog=M.document(raw)
            self.modules={}
            for path,pin in self.catalog['helpers'].items():
                name=Path(path).name;raw,check=M.seal(stack,self.root/'source'/name,pin['sha256']);self.checks.append(check)
                module=ModuleType('_market_health_'+Path(name).stem);module.__file__=str(self.root/'source'/name)
                exec(compile(raw,module.__file__,'exec'),module.__dict__);self.modules[name]=module
            self.D=self.modules['datastore_candidate_contract.py'];self.T=self.modules['runtime_image_archive_transport.py']
            self.L=self.modules['admit_security_component_image_reuse.py'];self.M=self.modules['compose_market_security_os_candidate.py']
            raw,check=M.seal(stack,args.control,args.control_sha256,M.LIMIT);self.checks.append(check);self.op=M.document(raw);self.pin=args.control_sha256;op=self.op
            M.need(set(op)=={'schema','operation','phase','profile','nonce','not_before','deadline','worker_sha256','health_policy_sha256',
                'baselines','fences','admission','admission_sha256'} and op['health_policy_sha256']==POLICY_SHA,'health_policy_control')
            M.need(op['schema']=='market_image_store_control/2' and op['operation']==OPERATION and op['phase'] in M.PHASES
                and type(op['nonce']) is str and re.fullmatch('[a-f0-9]{32}',op['nonce']) and args.control==self.root/'controls'/(op['nonce']+'.json'),'control_scope')
            M.need(set(op['baselines'])==set(op['fences'])=={'bot','web'} and all(op['baselines'][s].get('health_policy_sha256')==POLICY_SHA for s in ('bot','web')),'baseline_sites')
            for site,baseline in op['baselines'].items():
                initial=baseline.get('initial_containers');clone=M.runtime_health_policy.comparison_clone(site,initial,baseline['containers'])
                M.need(baseline.get('site')==site and baseline.get('controller_sha256')==op['worker_sha256']
                    and baseline.get('runtime_health_issues_open') is True and clone is not False
                    and M.runtime_health_policy.same(initial,clone) and type(baseline.get('initial_observed_at')) is int
                    and type(baseline.get('observed_at')) is int and baseline['initial_observed_at']<=baseline['observed_at'],'baseline_sites')
            M.need(type(op['not_before']) is int and type(op['deadline']) is int and op['not_before']<=time.time()<op['deadline']<=op['not_before']+900,'deadline')
            self.end=time.monotonic()+op['deadline']-time.time();raw,check=M.seal(stack,self.root/WORKER,op['worker_sha256']);self.checks.append(check)
            chosen=[p for p in self.catalog['profiles'] if p['profile']==op['profile']];M.need(len(chosen)==1,'profile_scope');self.spec=chosen[0]
            M.need(op['phase']=='receive-web' or (op['phase']=='load-web')==(self.spec['site']=='web'),'target_site')
            M.need(args.phase==op['phase'] or self.site=='web' and args.phase=='stream-web' and op['phase']=='load-native','phase_scope')
            # New LOAD work never adopts a prior attempt. Canonical receive TAR
            # paths remain unchanged and the old begin() still refuses reuse.
            name=op['profile']+'-'+op['phase']
            if op['phase']!='receive-web':name+='-health-v1-'+op['nonce']
            self.work=self.root/'attempts'/name;self.baseline=op['baselines'][self.site]
            no_prior_attempt(M,self)
            M.need(type(self.baseline['observed_at']) is int and 0<=time.time()-self.baseline['observed_at']<=300,'baseline_freshness')
            M.scope(self.site,args.phase,op['nonce']);self.check()
            unit='market-images-'+self.site+'-'+args.phase+'-'+op['nonce']+'.scope'
            values=M.command(self,['/usr/bin/systemctl','show',unit,'--property=RuntimeMaxUSec,TimeoutStopUSec,KillMode,SendSIGKILL'],maximum=4096).decode()
            values=dict(line.split('=',1) for line in values.splitlines())
            M.need(set(values)=={'RuntimeMaxUSec','TimeoutStopUSec','KillMode','SendSIGKILL'} and values['RuntimeMaxUSec'] in {'15min','15min 0s','900s'}
                and values['TimeoutStopUSec']=='5s' and values['KillMode']=='control-group' and values['SendSIGKILL']=='yes','scope_lifetime')
    return Context()

def collect(M,site,source_pin):
    M.SUBPHASE='snapshot_source_admission';M.need(os.geteuid()==0,'root_required')
    with ExitStack() as stack:
        _,check=M.seal(stack,ROOTS[site]/WORKER,source_pin)
        class Observer:
            def check(self):
                M.need(time.monotonic()<self.end,'deadline');check()
                for verify in M.bootstrap_checks:verify()
            capacity=check
        ctx=Observer();ctx.site=site;ctx.end=time.monotonic()+60;store=M.store_view(ctx)
        with Path('/etc/machine-id').open('rb') as source:host=source.read(65).strip().decode()
        M.need(re.fullmatch('[a-f0-9]{32}',host),'host_identity')
        before=M.snapshot(ctx);initial_observed_at=int(time.time());daemon=M.daemon_policy(ctx);temporary=M.temp_metadata();after=M.snapshot(ctx)
        P=M.runtime_health_policy;clone=P.comparison_clone(site,before['containers'],after['containers'])
        M.need(clone is not False and P.same(before['containers'],clone) and P.same(before['image_map'],after['image_map']),'snapshot_runtime_unstable')
        ctx.check()
        return {**after,'initial_containers':before['containers'],'initial_observed_at':initial_observed_at,'observed_at':int(time.time()),'site':site,'machine_id':host,'store':store,'daemon':daemon,
            'temporary_storage':temporary,'controller_sha256':source_pin,'health_policy_sha256':POLICY_SHA,
            'runtime_health_issues_open':True,'security_accepted':False}

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--site',choices=('bot','web'),required=True)
    parser.add_argument('--phase',choices=('receive-web','load-web','load-native','stream-web','snapshot'),required=True)
    parser.add_argument('--control',type=Path);parser.add_argument('--control-sha256');parser.add_argument('--source-sha256');parser.add_argument('--apply')
    args=parser.parse_args(argv);M=None;ctx=None;result=None
    try:
        with ExitStack() as stack:
            M=load_engine(stack,args.site);M.need(args.apply in (None,M.CONFIRM),'confirmation')
            if args.phase=='snapshot':
                M.need(args.apply is None,'snapshot_readonly');result=collect(M,args.site,args.source_sha256)
            else:
                ctx=context(args,stack,M);signal.alarm(max(1,int(ctx.end-time.monotonic())))
                result=M.run(ctx,args.apply is not None)
                if result is not None:result={**{k:v for k,v in result.items() if k not in EXTRAS},**M.observation_details(ctx)};M.public(result)
                ctx.check()
        if result is not None:M.public(result);print(json.dumps(result,sort_keys=True))
        return 0
    except Exception as error:
        if args.phase!='stream-web':
            code=error.args[0] if type(error) is RuntimeError and len(error.args)==1 and type(error.args[0]) is str else 'refused_or_unknown'
            if M is None or code not in M.PUBLIC_CODES:code='refused_or_unknown'
            value={'status':'refused_or_unknown','phase':args.phase,'subphase':M.SUBPHASE if M is not None else 'control_admission',
                'code':code,'retry_allowed':False,'security_accepted':False,'health_policy_sha256':POLICY_SHA,'runtime_health_issues_open':True,
                'runtime_health_observations':[]}
            if M is not None:
                value.update(M.exception_metadata(error))
                try:value.update(M.failure_details(ctx))
                except Exception:value['runtime_health_diagnostic_unavailable']=True
            else:value.update(exception_class='other',os_errno=None)
            print(json.dumps(value,sort_keys=True))
        return 1
    finally:signal.alarm(0)

if __name__=='__main__':raise SystemExit(main())
