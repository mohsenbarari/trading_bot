"""Seal this public Git source cohort, then run ONLY fixed hosted AppR2 public observations.

Default unarmed. Genuine hosted UID1001 only; no host/runtime identity override. No runtime32 claim.
Image release and controller commits are separate immutable identities.
"""
from hashlib import sha256
from pathlib import Path
from types import ModuleType
import argparse,json,os,re,stat,sys

ROOT_PREFIX = 'security-app-ci-smoke-r2-observe-'
TRACKED=Path('deploy/maintenance/app_r2_observe')
REPOSITORY='mohsenbarari/trading_bot'
CONFIRM = 'OBSERVE-ONLY-APP-R2-ELF-AND-PUBLIC-DOC-POLICY-20261027-01'
# Exact public source hashes are generated once before this source cohort freezes.
SOURCES = {'admit_security_component_image_reuse.py': (27518, '9adddae3d6966443f33040f54a57ce597a1816af918e8c606557801d27bf4025'), 'archive_inventory.py': (16581, 'cf5e665286429b02e64963620538c339f29e0839b60f97db58347a89764022c3'), 'compose_app_mountpoint_image.py': (49042, '14849bd8c616b27ade1c8488b8e6f276b369480456d0acc1a8c8eb4284f40226'), 'compose_market_security_os_candidate.py': (52231, 'c53cfd7ae4570314ef7f13d43ecd73de26436cf56407676ac2fe24d19dc1caa9'), 'compose_security_os_candidate.py': (60894, '4156ad042885bc7104c5a9ce537193adf2211ade8447e77676ad29d520e7633d'), 'controller.py': (21210, '6eb4b8705064f97c46094241f993e8696f03b9ad53217329a002f189f322b90f'), 'datastore_candidate_contract.py': (38383, '253f234f2ba68dea6929e16128d8dcb48021cda7bf71ffd004ecb8e709c575ee'), 'export_controller.py': (15009, '8648ebb75c7ff8b9c289e1224bf6431047d7e4004c0026573de68bf21b301b10'), 'image-controller-sources.json': (656, 'f88d042d8a4dbe05f6e636635f60d7aa0abdc947bcfd84b4c430a9014cec8b30'), 'inventory.py': (27258, '8b4b0091a1e8d20786e33c97a6bb4b63c28b263180527b313883815c67cfe5dc'), 'original-R-workflow.yml': (11885, '9d68c472faf4aa4dcb1c5770d0eb4a69bb2048658b21f23fe52ff1069e3d0144'), 'prepare_canonical_app_image.py': (33828, '86eac6a326237e7a8758c5ca50357db2d8717484ba983cfcda7b72b8bc9053c0'), 'producer02_controller.py': (57553, '9de2b86dedbb773351b042bcd3177ebb41a88272cfd76e13f24de5ece6d49a25'), 'public_policy_facts.py': (3264, '358de5ab355c73d5cc803d4b64da5b17b3b1460940303065f3893a692481a7aa'), 'run_app_image_ci_smoke.py': (68163, 'b2c958060d176c0098b86a44d0dc225d4494023120b3253239bef9e2f3f2acb2'), 'runtime_image_archive_transport.py': (35148, '20c4b90f1c719d4cdee69df8ad1c31c2bcd663b99619b1d58dcbafa1eafecad2')}
EXECUTION_ARMED=True
IMAGE_SOURCE_COMMIT='6cefc83c5c2da812d99c549e383ada2e43cd722d'
IMAGE_ID='sha256:f685744c572f14025fe81ab8f2b1d5371a23d7e19fd9e572e3c6ab83d53effc7'
PHASE='unarmed'


def need(ok,code):
    if not ok: raise RuntimeError(code)


def ident(s):
    return(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)


def encoded(value):
    return(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()


def parents(path,owner):
    result=[]
    for p in path.parents:
        frame=ident(p.lstat())
        need(p.resolve(strict=True)==p and stat.S_ISDIR(frame[2]) and frame[3] in {0,owner} and not frame[2]&0o022,'sealed_parent')
        result.append((p,frame[:5]))
    return result


class Held:
    def __init__(self,path,pin,*,owner,mode,maximum=131072):
        self.path=path;self.parents=parents(path,owner);self.before=ident(path.lstat());self.fd=None
        need(path.resolve(strict=True)==path and self.before[2:6]==(stat.S_IFREG|mode,owner,os.getegid(),1)
            and type(maximum) is int and 0<self.before[6]<=maximum<=4*1024**2,'sealed_metadata')
        size=self.before[6]
        need(pin is None or type(pin)is tuple and len(pin)==2 and pin[0]==size,'sealed_pin')
        self.fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
        try:
            self.check();parts=[];count=0
            while True:
                part=os.read(self.fd,min(65536,size-count+1))
                if not part:break
                count+=len(part);need(count<=size,'sealed_bound');parts.append(part)
            self.raw=b''.join(parts);self.binding=(count,sha256(self.raw).hexdigest())
            need(count==size and (pin is None or self.binding==pin),'sealed_pin');self.check()
        except BaseException:
            os.close(self.fd);self.fd=None;raise
    def named(self):
        need(self.path.resolve(strict=True)==self.path and ident(self.path.lstat())==self.before
            and all(p.resolve(strict=True)==p and ident(p.lstat())[:5]==old for p,old in self.parents),'sealed_changed')
    def check(self):
        self.named();need(ident(os.fstat(self.fd))==self.before,'sealed_changed')
    def close(self):
        if self.fd is None:return
        try:self.check()
        finally:os.close(self.fd);self.fd=None
        self.named()


PUBLIC_CLASSES=frozenset(('RuntimeError','Refusal','OSError','FileNotFoundError','PermissionError','TimeoutError','ValueError','TypeError','KeyError','FinalizationFailure'))
LAST_FAILURE=None

def closed_error(error):
    code=error.args[0]if isinstance(error,RuntimeError)and len(error.args)==1 and type(error.args[0])is str else None
    return {'code':code if code in CODES else'hosted_proof_unproven','exception_class':type(error).__name__ if type(error).__name__ in PUBLIC_CLASSES else'unclassified_exception','os_errno':error.errno if isinstance(error,OSError)and type(error.errno)is int and 0<error.errno<=4095 else None}

class FinalizationFailure(RuntimeError):
    def __init__(self,failures):
        super().__init__('sealed_finalization');self.failures=failures

def finalize(held,keeper=None):
    failures=[]
    def attempt(stage,index,function):
        try:function()
        except BaseException as error:failures.append({'stage':stage,'index':index,**closed_error(error)})
    for index in reversed(range(len(held))):attempt('outer_source_close',index,held[index].close)
    if keeper is not None:attempt('outer_keeper_close',0,keeper.close)
    for index,item in enumerate(held):attempt('outer_source_named',index,item.named)
    if keeper is not None:attempt('outer_keeper_named',0,keeper.named)
    if failures:raise FinalizationFailure(failures)

def closed_failure(error):
    if LAST_FAILURE is not None and LAST_FAILURE[0]is error:return LAST_FAILURE[1]
    return {'status':'REFUSED_OR_FAILED','schema':'App_R2_observation_outer_failure/2','phase':PHASE,**closed_error(error),'primary_failure':closed_error(error),'controller_failure':None,'physical_finalizer_failures':[],'outer_physical_finalizers_completed':False,'security_accepted':False}


def source_limit(name):
    return 2*1024**2 if name=='native-expectation.json' else 131072


def save(path,raw,maximum=131072):
    need(type(maximum)is int and 0<maximum<=2*1024**2 and type(raw)is bytes and len(raw)<=maximum,'sealed_bound');fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    try:
        view=memoryview(raw)
        while view:n=os.write(fd,view);need(n>0,'sealed_write');view=view[n:]
        os.fsync(fd)
    finally:os.close(fd)


def host(root):
    need(os.geteuid()==os.getuid()==1001 and os.environ.get('GITHUB_ACTIONS')=='true'
        and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted' and os.environ.get('GITHUB_REPOSITORY')==REPOSITORY
        and os.environ.get('GITHUB_REF')=='refs/heads/main','hosted_ci_only')
    commit=os.environ.get('GITHUB_SHA','');need(re.fullmatch('[a-f0-9]{40}',commit),'hosted_ci_only')
    run=os.environ.get('GITHUB_RUN_ID','');attempt=os.environ.get('GITHUB_RUN_ATTEMPT','')
    need(re.fullmatch('[1-9][0-9]{0,19}',run) and re.fullmatch('[1-9][0-9]{0,5}',attempt),'hosted_ci_only')
    parent=Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    need(root.is_absolute() and root.parent==parent and root.name==ROOT_PREFIX+run+'-'+attempt
        and not os.path.lexists(root) and not parent.stat().st_mode&0o022,'hosted_root')
    repo=Path(os.environ['GITHUB_WORKSPACE']).resolve(strict=True)
    need(Path(__file__).absolute()==repo/TRACKED/'seal_and_run.py','hosted_source_path')
    return repo,commit


def run(args):
    global PHASE,LAST_FAILURE
    PHASE='unarmed';LAST_FAILURE=None
    need(EXECUTION_ARMED is True and type(IMAGE_ID)is str and re.fullmatch('sha256:[a-f0-9]{64}',IMAGE_ID) and type(IMAGE_SOURCE_COMMIT)is str and re.fullmatch('[a-f0-9]{40}',IMAGE_SOURCE_COMMIT),'future_intake_unbound')
    root=Path(args.root);repo,commit=host(root);held=[];finished=False;finalization_attempted=False;code=None;keeper=None;controller_failure=None
    need(type(args.source_sha256)is str and re.fullmatch('[a-f0-9]{64}',args.source_sha256),'sealed_pin')
    PHASE='source_admission'
    try:
        own=Held(Path(__file__).absolute(),(Path(__file__).lstat().st_size,args.source_sha256),owner=1001,mode=0o644);held.append(own)
        # Source files must be genuinely tracked in THIS controller commit;
        # neither the image release commit nor a moving blob ref is substituted.
        root.mkdir(mode=0o700)
        for leaf in('source','reports','docker-config'):(root/leaf).mkdir(mode=0o700)
        output=Path(os.environ['GITHUB_OUTPUT'])
        with output.open('a') as stream:stream.write('root='+str(root)+'\n')
        for name,pin in SOURCES.items():
            item=Held(repo/TRACKED/name,pin,owner=1001,mode=0o644,maximum=source_limit(name));held.append(item)
        backend=ModuleType('_reviewed_6de_executor')
        backend.__file__=str(repo/TRACKED/'compose_security_os_candidate.py')
        raw=next(item.raw for item in held if item.path==repo/TRACKED/'compose_security_os_candidate.py')
        exec(compile(raw,backend.__file__,'exec'),backend.__dict__)
        keeper=backend.ProofKeeper(root)
        env={'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null','GIT_OPTIONAL_LOCKS':'0','GIT_TERMINAL_PROMPT':'0','GIT_ALLOW_PROTOCOL':''}
        def git(*argv,maximum=131072):
            return backend.command(['/usr/bin/git','--no-optional-locks','-C',str(repo),'-c','core.fsmonitor=false',*argv],seconds=15,maximum=maximum,environment=env)
        need(git('rev-parse','HEAD',maximum=64)==commit.encode()+b'\n','controller_git_identity')
        own_ref=commit+':'+str(TRACKED/'seal_and_run.py')
        need(git('cat-file','blob',own_ref,maximum=len(own.raw)+1)==own.raw,'controller_git_identity')
        records={}
        for name,pin in SOURCES.items():
            item=next(item for item in held if item.path==repo/TRACKED/name);ref=commit+':'+str(TRACKED/name)
            need(git('cat-file','-t',ref,maximum=16)==b'blob\n'
                and git('cat-file','-s',ref,maximum=32)==str(pin[0]).encode()+b'\n'
                and git('cat-file','blob',ref,maximum=pin[0]+1)==item.raw,'controller_git_identity')
            save(root/'source'/name,item.raw,maximum=source_limit(name));held.append(Held(root/'source'/name,pin,owner=1001,mode=0o600,maximum=source_limit(name)))
            records[name]={'bytes':pin[0],'sha256':pin[1],'git_path':str(TRACKED/name)}
        keeper.write(root/'reports/hosted-controller-source.json',encoded({'schema':'App_R2_observation_controller_source/1','controller_commit':commit,
            'image_source_commit':IMAGE_SOURCE_COMMIT,'image_id':IMAGE_ID,'run_id':os.environ['GITHUB_RUN_ID'],
            'run_attempt':os.environ['GITHUB_RUN_ATTEMPT'],'files':records,'security_accepted':False}),0o600)
        PHASE='canonical_archive_and_public_inventory'
        controller=ModuleType('_new_6de_controller');controller.__file__=str(root/'source/controller.py')
        script=next(item.raw for item in held if item.path==root/'source/controller.py')
        exec(compile(script,controller.__file__,'exec'),controller.__dict__)
        need(controller.CONTROL['source_commit']==IMAGE_SOURCE_COMMIT and controller.CONTROL['image_id']==IMAGE_ID,'future_intake_unbound')
        code=controller.main(['--root',str(root),'--execute-fixed-R2-observation','--confirm',CONFIRM],outer_keeper=keeper)
        controller_failure=controller.LAST_FAILURE_RECORD if type(code)is int and code!=0 else None
        need(type(code)is int and code==0,'controller_failed')
        # Archives and immutable reports were admitted at FIRST use/creation,
        # not captured after the controller's inner ExitStack already closed.
        report_pins=keeper.bindings()
        PHASE='outer_source_finalization';finalization_attempted=True;finalize(held,keeper);finished=True
        PHASE='completion_publication'
        save(root/'reports/hosted-completion.json',encoded({'schema':'App_R2_observation_controller_completion/1','controller_commit':commit,
            'controller_source_sha256':args.source_sha256,'image_id':controller.CONTROL['image_id'],'image_scope':controller.image_scope(controller.CONTROL),
            'controller_exit_code':code,'outer_source_physical_finalizers_complete':True,'runtime_assertions_executed':0,'full_P_proof_created':False,'source_flags_review_created':False,'policy_origin_layer_collected':False,'retained_public_inputs_and_proofs':report_pins,
            'security_accepted':False,'full_matrix_accepted':False}))
        PHASE='completion_named_finalization';late=[]
        for index,item in enumerate(held):
            try:item.named()
            except BaseException as error:late.append({'stage':'completion_source_named','index':index,**closed_error(error)})
        try:keeper.named()
        except BaseException as error:late.append({'stage':'completion_keeper_named','index':0,**closed_error(error)})
        if late:raise FinalizationFailure(late)
        return 0
    except BaseException as primary:
        failures=primary.failures if type(primary)is FinalizationFailure else[];failure=primary
        # Exactly one all-attempted finalization, even on an earlier primary.
        # A failed close is never blindly retried against a possibly reused FD.
        if not finalization_attempted:
            finalization_attempted=True
            try:finalize(held,keeper);finished=True
            except FinalizationFailure as closing:failures=closing.failures;failure=closing
        record={'status':'REFUSED_OR_FAILED','schema':'App_R2_observation_outer_failure/2','phase':PHASE,**closed_error(failure),'primary_failure':None if type(primary)is FinalizationFailure else closed_error(primary),'controller_failure':controller_failure,'physical_finalizer_failures':failures,'outer_physical_finalizers_completed':finished and not failures,'security_accepted':False}
        LAST_FAILURE=(failure,record)
        if failure is not primary:raise failure from primary
        raise


def main(argv=None):
    global LAST_FAILURE,PHASE
    LAST_FAILURE=None;PHASE='unarmed'
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root');parser.add_argument('--source-sha256');parser.add_argument('--execute-fixed-R2-observation',action='store_true')
    args=parser.parse_args(argv)
    if not args.execute_fixed_R2_observation:
        print(json.dumps({'status':'UNARMED','hosted_operation_performed':False,'security_accepted':False}));return 0
    return run(args)


CODES=frozenset(('future_intake_unbound','sealed_parent','sealed_metadata','sealed_bound','sealed_pin','sealed_changed','sealed_finalization','sealed_write',
    'hosted_ci_only','hosted_root','hosted_source_path','controller_git_identity','controller_failed',
    'proof_parent','proof_parent_changed','proof_bound','proof_write','proof_metadata','proof_changed','proof_root','proof_scope','proof_closed','proof_finalization'))
if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as error:
        print(json.dumps(closed_failure(error),sort_keys=True));raise SystemExit(1)from None
