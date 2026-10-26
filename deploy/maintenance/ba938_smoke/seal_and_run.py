"""Seal this public Git source cohort, then run ONLY reviewed hosted ba938 proof.

Default unarmed. Genuine hosted UID1001 only; no host/runtime identity override.
Image release and controller commits are separate immutable identities.
"""
from hashlib import sha256
from pathlib import Path
from types import ModuleType
import argparse,json,os,re,stat,sys

ROOT_PREFIX='security-app-ci-smoke-ba938-'
TRACKED=Path('deploy/maintenance/ba938_smoke')
REPOSITORY='mohsenbarari/trading_bot'
CONFIRM='RUN-ONLY-BA938-ISOLATED-SMOKE-AND-PUBLIC-INVENTORY-20261026-03'
# Exact public source hashes are generated once before this source cohort freezes.
SOURCES={'admit_security_component_image_reuse.py': (27518, '9adddae3d6966443f33040f54a57ce597a1816af918e8c606557801d27bf4025'), 'compose_app_mountpoint_image.py': (49042, '14849bd8c616b27ade1c8488b8e6f276b369480456d0acc1a8c8eb4284f40226'), 'compose_market_security_os_candidate.py': (52231, 'c53cfd7ae4570314ef7f13d43ecd73de26436cf56407676ac2fe24d19dc1caa9'), 'compose_security_os_candidate.py': (60894, '4156ad042885bc7104c5a9ce537193adf2211ade8447e77676ad29d520e7633d'), 'controller.py': (45766, 'b24eea974189eeb2e83bec500085c7559a4ad31b78dd16177ec771dfcdf1af8e'), 'datastore_candidate_contract.py': (38382, 'd210020e24fedd4986803a3fe2618a5016ecd99f9e3ca9588af02d77a967e17d'), 'inventory.py': (24518, '1ec1e29c2a673eb14f1e8734014ea49a2b04a78ffce928b60aa447b5bd8c5469'), 'native-expectation.json': (34993, '9a2f97514d33b62a6c787ee94776f2856e5cba861570e4a6c28603664e9969b3'), 'native-expectation.source19.UNARMED.json': (34994, '2cf0e5410381cd0e0303611e72949daed67e4fd12d7913de2912775f53df51cb'), 'prepare_canonical_app_image.py': (29359, '9916037a7d2756c3996d04a43ad59655d30d708ae6f5b35cb20789ea190cc555'), 'probe_native_payload.py': (11427, '078e412950039b0e2055597022f89eab001e63e69eb1b7137e56c12e44d66eae'), 'run_app_image_ci_smoke.py': (68163, '0555ffd794a6db2cb13c7119145bd8f1b5e09348576b6cb9d514239837c27a58'), 'runtime_image_archive_transport.py': (35148, '20c4b90f1c719d4cdee69df8ad1c31c2bcd663b99619b1d58dcbafa1eafecad2'), 'test_security_form_parser.py': (3910, 'de0090351837742ba6b0be747f0b586d4943590831215a4b4ea2619ebd5149fb'), 'test_stage6_otp_delivery_redis.py': (25543, 'b1e59ad7285a3c1f034aa269e024e091fe331cbdb446ac5e82f85de64530bd50'), 'test_telegram_otp_ephemeral_queue_redis.py': (8024, 'cbe02df0618239e4affa1f8e51b2e69cdfe13f332a44d46e8b4626b21d80e22e'), 'verify_acl_consumer_presence.py': (13746, '001e51d270c079153cc38db1be53c723fbbc8a8bb9e981e718e7b657127bc778'), 'verify_image_component_presence.py': (12257, '3cd10d7bc591615723f7b85a87011d0d7029120324f09ef2f099d49137bcfd94')}
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


def finalize(held,keeper=None):
    error=False
    for item in reversed(held):
        try:item.close()
        except BaseException:error=True
    if keeper is not None:
        try:keeper.close()
        except BaseException:error=True
    for item in held:
        try:item.named()
        except BaseException:error=True
    if keeper is not None:
        try:keeper.named()
        except BaseException:error=True
    need(not error,'sealed_finalization')


def save(path,raw):
    need(type(raw)is bytes and len(raw)<=131072,'sealed_bound');fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    try:
        view=memoryview(raw)
        while view:n=os.write(fd,view);need(n>0,'sealed_write');view=view[n:]
        os.fsync(fd)
    finally:os.close(fd)


def host(root):
    need(os.geteuid()==os.getuid()==1001 and os.environ.get('GITHUB_ACTIONS')=='true'
        and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted' and os.environ.get('GITHUB_REPOSITORY')==REPOSITORY
        and os.environ.get('GITHUB_REF')=='refs/heads/security/remediation-20261023','hosted_ci_only')
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
    global PHASE
    root=Path(args.root);repo,commit=host(root);held=[];finished=False;code=None;keeper=None
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
            item=Held(repo/TRACKED/name,pin,owner=1001,mode=0o644);held.append(item)
        backend=ModuleType('_reviewed_ba938_executor')
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
            save(root/'source'/name,item.raw);held.append(Held(root/'source'/name,pin,owner=1001,mode=0o600))
            records[name]={'bytes':pin[0],'sha256':pin[1],'git_path':str(TRACKED/name)}
        keeper.write(root/'reports/hosted-controller-source.json',encoded({'schema':'ba938_hosted_controller_source/1','controller_commit':commit,
            'image_source_commit':'6d257bf4a92ce196e90fdbe138d59f48eddd95c0','run_id':os.environ['GITHUB_RUN_ID'],
            'run_attempt':os.environ['GITHUB_RUN_ATTEMPT'],'files':records,'security_accepted':False}),0o600)
        PHASE='isolated_smoke_and_inventory'
        controller=ModuleType('_new_ba938_controller');controller.__file__=str(root/'source/controller.py')
        script=next(item.raw for item in held if item.path==root/'source/controller.py')
        exec(compile(script,controller.__file__,'exec'),controller.__dict__)
        code=controller.main(['--root',str(root),'--execute-reviewed-ba938-proof','--confirm',CONFIRM],outer_keeper=keeper)
        need(type(code)is int and code==0,'controller_failed')
        # Archives and immutable reports were admitted at FIRST use/creation,
        # not captured after the controller's inner ExitStack already closed.
        report_pins=keeper.bindings()
        PHASE='outer_source_finalization';finalize(held,keeper);finished=True
        save(root/'reports/hosted-completion.json',encoded({'schema':'ba938_hosted_controller_completion/1','controller_commit':commit,
            'controller_source_sha256':args.source_sha256,'image_id':controller.CONTROL['image_id'],
            'controller_exit_code':code,'outer_source_physical_finalizers_complete':True,'runtime_assertions':32,'retained_public_inputs_and_proofs':report_pins,
            'security_accepted':False,'full_matrix_accepted':False}))
        for item in held:item.named()
        keeper.named()  # Also catch drift during the final completion-file close.
        return 0
    finally:
        if not finished:
            for item in reversed(held):
                if item.fd is not None:
                    try:item.close()
                    except BaseException:pass
            if keeper is not None:
                try:keeper.close()
                except BaseException:pass


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root');parser.add_argument('--source-sha256');parser.add_argument('--execute-reviewed-ba938-proof',action='store_true')
    args=parser.parse_args(argv)
    if not args.execute_reviewed_ba938_proof:
        print(json.dumps({'status':'UNARMED','hosted_operation_performed':False,'security_accepted':False}));return 0
    return run(args)


CODES=frozenset(('sealed_parent','sealed_metadata','sealed_bound','sealed_pin','sealed_changed','sealed_finalization','sealed_write',
    'hosted_ci_only','hosted_root','hosted_source_path','controller_git_identity','controller_failed',
    'proof_parent','proof_parent_changed','proof_bound','proof_write','proof_metadata','proof_changed','proof_root','proof_scope','proof_closed','proof_finalization'))
if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as error:
        code=error.args[0] if type(error)is RuntimeError and len(error.args)==1 and type(error.args[0])is str and error.args[0]in CODES else'hosted_proof_unproven'
        print(json.dumps({'status':'REFUSED_OR_FAILED','phase':PHASE,'code':code,'security_accepted':False},sort_keys=True));raise SystemExit(1)from None
