"""UNARMED hosted entry for four public current-candidate provider cases.

Reuses the exact existing gold2 source I/O and canonical image admission.
Historical gold2/default/App32/inventory entrypoints are never called.
"""
from hashlib import sha256
from pathlib import Path
from types import ModuleType
import argparse
import errno
import fcntl
import json
import os
import re
import stat
import time

TRACKED=Path('deploy/maintenance/coreutils_provider_smoke')
PREFIX='security-app-ci-smoke-provider-'
CONFIRM='RUN-ONLY-2389-COREUTILS-PROVIDER-OWNED-FIXTURES-20261029-01'
OUTER_PATH=Path('deploy/maintenance/app_gold2_smoke/seal_and_run.py')
OUTER_PIN=(32933, '06e59f02741c14c854c9d32391fdbe03ff69128c295c62286d6aca2c85ae18d8')
MANIFEST_PIN=(5266, '1eff96510d60ab6ac4a0e1fea30c1a11fcb8366666644e7a93cc3793d1e93a4e')
PUBLIC_CODES=frozenset(('provider_unarmed','provider_host','provider_manifest','provider_source','provider_source_changed',
    'provider_source_finality','provider_git','provider_output','provider_result','provider_outer_finality'))
LAST_FAILURE=None


def need(v,c):
    if not v: raise RuntimeError(c)


def frame(s):return(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def flags(fd):return(fcntl.fcntl(fd,fcntl.F_GETFL),fcntl.fcntl(fd,fcntl.F_GETFD))


class Bootstrap:
    """Only the original known outer-I/O module source prefix, held to finality."""
    def __init__(self,path,pin,clock):
        self.path=path;self.pin=pin;self.clock=clock;self.fd=self.original_fd=None
        self.before=self.fl=self.origin=None;self.parents=();self.raw=None;self.attempted=False;self.errors=[]
    def acquire(self):
        try:return self._acquire()
        except BaseException as e:
            if all(e is not x for x in self.errors):self.errors.append(e)
            raise
    def _acquire(self):
        self.clock();need(self.path.resolve(strict=True)==self.path,'provider_source')
        self.parents=tuple((p,frame(p.lstat())[:5])for p in self.path.parents)
        need(all(stat.S_ISDIR(b[2])and b[3]in(0,1001)and not b[2]&0o022 for p,b in self.parents),'provider_source')
        self.fd=self.original_fd=os.open(self.path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
        self.before=frame(os.fstat(self.original_fd));self.fl=flags(self.original_fd)
        self.origin=(self.path,self.original_fd,self.before,self.fl,self.parents)
        need(self.before[2:7]==(stat.S_IFREG|0o644,1001,1001,1,self.pin[0])and 0<self.pin[0]<=131072
             and self.fl[0]&os.O_ACCMODE==os.O_RDONLY and self.fl[1]==fcntl.FD_CLOEXEC,'provider_source')
        out=[];count=0
        while count<self.pin[0]:
            self.check();b=os.pread(self.original_fd,min(65536,self.pin[0]-count),count)
            need(b and count+len(b)<=self.pin[0],'provider_source');out.append(b);count+=len(b);self.check()
        need(not os.pread(self.original_fd,1,count),'provider_source')
        self.raw=b''.join(out);need((len(self.raw),sha256(self.raw).hexdigest())==self.pin,'provider_source');self.check()
    def named(self,observation=None):
        self.clock();need(self.path.resolve(strict=True)==self.path and frame(self.path.lstat())==self.before,'provider_source_changed')
        need(all(p.resolve(strict=True)==p and frame(p.lstat())[:5]==b for p,b in self.parents),'provider_source_changed')
    def check(self,observation=None):
        try:return self._check(observation)
        except BaseException as e:
            if all(e is not x for x in self.errors):self.errors.append(e)
            raise
    def _check(self,observation=None):
        self.named();need((self.path,self.original_fd,self.before,self.fl,self.parents)==self.origin,'provider_source_changed')
        if self.fd is not None:
            need(self.fd==self.original_fd and frame(os.fstat(self.original_fd))==self.before and flags(self.original_fd)==self.fl
                 and os.lseek(self.original_fd,0,os.SEEK_CUR)==0,'provider_source_changed')
    def close(self):
        need(not self.attempted,'provider_source_finality');self.attempted=True
        if self.original_fd is None:return
        # A failed admission/check is not healed by silently closing another FD.
        self.check();need(self.raw is not None,'provider_source_finality');original=self.original_fd
        os.close(original)
        try:os.fstat(original)
        except OSError as e:need(e.errno==errno.EBADF,'provider_source_finality')
        else:raise RuntimeError('provider_source_finality')
        self.fd=None;self.named()


def host(root):
    need(os.getuid()==os.geteuid()==os.getgid()==os.getegid()==1001 and os.environ.get('GITHUB_ACTIONS')=='true'
         and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted' and os.environ.get('GITHUB_REPOSITORY')=='mohsenbarari/trading_bot'
         and os.environ.get('GITHUB_REF')=='refs/heads/main','provider_host')
    commit=os.environ.get('GITHUB_SHA','');run=os.environ.get('GITHUB_RUN_ID','');attempt=os.environ.get('GITHUB_RUN_ATTEMPT','')
    need(re.fullmatch('[a-f0-9]{40}',commit) and re.fullmatch('[1-9][0-9]{0,19}',run) and re.fullmatch('[1-9][0-9]{0,5}',attempt),'provider_host')
    parent=Path(os.environ['RUNNER_TEMP']).resolve(strict=True);repo=Path(os.environ['GITHUB_WORKSPACE']).resolve(strict=True)
    need(root.is_absolute()and root.parent==parent and root.name==PREFIX+run+'-'+attempt and not os.path.lexists(root)
         and not parent.stat().st_mode&0o022 and Path(__file__).absolute()==repo/TRACKED/'seal_and_run.py','provider_host')
    return repo,commit


def publish_root(root):
    p=Path(os.environ['GITHUB_OUTPUT']);need(p.is_absolute()and p.resolve(strict=True)==p,'provider_output')
    fd=os.open(p,os.O_WRONLY|os.O_APPEND|os.O_NOFOLLOW|os.O_CLOEXEC)
    primary=None;final=[]
    try:
        b=frame(os.fstat(fd));fl=flags(fd)
        need(stat.S_ISREG(b[2])and b[3:6]==(1001,1001,1)and b[6]<=65536,'provider_output')
        raw=('root='+str(root)+'\n').encode();need(len(raw)<=4096,'provider_output')
        need(os.write(fd,raw)==len(raw),'provider_output');a=frame(os.fstat(fd))
        need(a[:6]==b[:6]and a[6]==b[6]+len(raw)and flags(fd)==fl and frame(p.lstat())==a,'provider_output')
    except BaseException as e:primary=e
    try:
        os.close(fd)
        try:os.fstat(fd)
        except OSError as e:need(e.errno==errno.EBADF,'provider_output')
        else:raise RuntimeError('provider_output')
    except BaseException as e:final.append(e)
    if final:
        final[0].provider_output_primary=primary;raise final[0]from primary
    if primary is not None:raise primary


def failure(error,O=None):
    code=error.args[0]if type(error)is RuntimeError and len(error.args)==1 else None
    if type(code)is not str:code=None
    allowed=PUBLIC_CODES|(frozenset()if O is None else O.CODES)
    return {'code':code if code in allowed else'provider_failure','exception_class':type(error).__name__
            if type(error).__name__ in {'RuntimeError','OSError','ValueError','TypeError','KeyError','FileNotFoundError','PermissionError','TimeoutError','FinalizationFailure','IOFailure'} else 'unclassified_exception',
            'os_errno':error.errno if isinstance(error,OSError)and type(error.errno)is int and 0<error.errno<=4095 else None}


def run(args):
    need(args.execute_reviewed_provider_smoke is True and re.fullmatch('[a-f0-9]{64}',args.source_sha256 or''),'provider_unarmed')
    started=time.monotonic();end=started+1800;actor=os.getpid();root=Path(args.root);repo,commit=host(root)
    def clocks():need(os.getpid()==actor and time.monotonic()<end,'provider_outer_finality')
    held=[];saved=[];completion=[];keeper=None;O=None;finished=False;final_attempted=False;controller_failure=None
    boot=Bootstrap(repo/OUTER_PATH,OUTER_PIN,clocks);held.append(boot)
    try:
        boot.acquire();O=ModuleType('_provider_existing_outer_io');O.__file__=str(repo/OUTER_PATH);exec(compile(boot.raw,O.__file__,'exec'),O.__dict__)
        def check():O.metadata_check(held,saved,keeper,clocks)
        def capture(path,pin,mode):
            item=O.Held.__new__(O.Held);held.append(item);item.__init__(path,pin,owner=1001,mode=mode,scope=check);return item
        own=capture(Path(__file__).absolute(),(Path(__file__).lstat().st_size,args.source_sha256),0o644)
        manifest=capture(repo/TRACKED/'source-manifest.json',MANIFEST_PIN,0o644)
        data=json.loads(manifest.raw);rows=data.get('files')
        need(data.get('schema')=='current_provider_source_cohort/1'and type(rows)is list and 0<len(rows)<=32
             and len({r['name']for r in rows})==len(rows),'provider_manifest')
        originals={}
        for row in rows:
            name=row['name'];path=Path(row['path']);pin=(row['bytes'],row['sha256'])
            need(type(name)is str and re.fullmatch(r'[a-zA-Z0-9_-]+\.(?:py|json)',name)and not path.is_absolute()
                 and '..'not in path.parts and pin[0]<=131072 and re.fullmatch('[a-f0-9]{64}',pin[1]),'provider_manifest')
            originals[name]=capture(repo/path,pin,0o644)
        root.mkdir(mode=0o700)
        for leaf in('source','reports','docker-config'):(root/leaf).mkdir(mode=0o700)
        publish_root(root)
        backend=ModuleType('_provider_existing_archive_backend');backend.__file__=str(repo/next(r['path']for r in rows if r['name']=='compose_security_os_candidate.py'))
        exec(compile(originals['compose_security_os_candidate.py'].raw,backend.__file__,'exec'),backend.__dict__)
        keeper=backend.ProofKeeper(root)
        H=ModuleType('_provider_existing_host_commands');H.__file__=str(repo/next(r['path']for r in rows if r['name']=='build_hosted.py'))
        exec(compile(originals['build_hosted.py'].raw,H.__file__,'exec'),H.__dict__)
        env={'PATH':'/usr/bin:/bin','LC_ALL':'C.UTF-8','GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null',
             'GIT_OPTIONAL_LOCKS':'0','GIT_TERMINAL_PROMPT':'0','GIT_ALLOW_PROTOCOL':''}
        def git(*argv,maximum=131072):
            return H.run(['/usr/bin/git','--no-optional-locks','-C',str(repo),'-c','core.fsmonitor=false',*argv],
                         env=env,deadline=min(end,time.monotonic()+15),maximum=maximum,guard=check)
        need(git('rev-parse','HEAD',maximum=64)==commit.encode()+b'\n','provider_git')
        need(git('cat-file','blob',commit+':'+str(TRACKED/'seal_and_run.py'),maximum=len(own.raw)+1)==own.raw,'provider_git')
        need(git('cat-file','blob',commit+':'+str(TRACKED/'source-manifest.json'),maximum=len(manifest.raw)+1)==manifest.raw,'provider_git')
        copies={};records={}
        for row in rows:
            name=row['name'];item=originals[name];pin=(row['bytes'],row['sha256']);ref=commit+':'+row['path']
            need(git('cat-file','-t',ref,maximum=16)==b'blob\n'and git('cat-file','blob',ref,maximum=pin[0]+1)==item.raw,'provider_git')
            O.save(root/'source'/name,item.raw,owned=saved,check=check);copies[name]=capture(root/'source'/name,pin,0o600)
            records[name]=row
        def raw(name):check();need(name in copies,'provider_source');return copies[name].raw
        def load(name):
            script=raw(name);m=ModuleType('_provider_'+name.replace('.','_'));m.__file__=str(root/'source'/name)
            exec(compile(script,m.__file__,'exec'),m.__dict__);check();return m
        keeper.write(root/'reports/provider-source-cohort.json',O.encoded({'schema':'provider_hosted_source/1','controller_commit':commit,
            'image_id':'sha256:2389c900dbbf520bd7d7f723e8c48a5c258f1612c43e80d2b66c6d046f9f8434','source_files':records,
            'historical_profiles_modified':False,'app32_or_inventory_entrypoints_called':False,'security_accepted':False}),0o600)
        controller=load('controller.py')
        result=controller.main(['--root',str(root),'--execute-reviewed-provider-smoke','--confirm',CONFIRM],
                               outer_keeper=keeper,outer_check=check,load=load,raw=raw)
        need(result.get('schema')=='provider_hosted_inner_completed/1'and result.get('cases_passed')==4
             and result.get('owned_cleanup_complete')is True,'provider_result')
        bindings=keeper.bindings();final_attempted=True;O.finalize(held,keeper,clocks,saved)
        O.completion_save(root/'reports/provider-completion.json',O.encoded({'schema':'current_provider_hosted_completion/1',
            'controller_commit':commit,'controller_source_sha256':args.source_sha256,'cases_passed':4,
            'source_provider_and_owned_container_finalizers_completed':True,'retained_public_inputs_and_proofs':bindings,
            'completion_finality':'conditional_on_successful_final_exit','candidate_CI_compatibility_only':True,
            'production_activation_performed':False,'security_accepted':False,'provider_accepted':False}),held,keeper,saved,clocks,completion)
        finished=True;return {'status':'PROVIDER_CAPSULE_COMPLETED_PENDING_ROOT_ACCEPTANCE','cases_passed':4,
            'outer_physical_finalizers_completed':True,'security_accepted':False,'provider_accepted':False}
    except BaseException as primary:
        errors=[]
        if not final_attempted:
            final_attempted=True
            if O is not None:
                try:O.finalize(held,keeper,clocks,saved)
                except BaseException as e:errors.append(e)
            else:
                try:boot.close()
                except BaseException as e:errors.append(e)
        selected=errors[0]if errors else primary
        record={'status':'REFUSED_OR_FAILED','primary':failure(primary,O),'finalizers':[failure(e,O)for e in errors],
            'finalizer_details':[e.failures for e in errors if O is not None and type(e)is O.FinalizationFailure],
            'controller':getattr(primary,'provider_failure',None),'outer_physical_finalizers_completed':finished,
            'security_accepted':False,'provider_accepted':False}
        record['source_prefixes']=[{'original_FD_acquired':getattr(r,'original_fd',None)is not None,
            'reader_retired':getattr(r,'fd',None)is None,'close_attempted':bool(getattr(r,'attempted',False)or getattr(r,'close_attempted',False)),
            'initial_closed_errors':[failure(e,O)for e in getattr(r,'errors',())]}for r in held]
        selected.provider_bootstrap_prefix=boot
        selected.provider_outer_failure=record
        if selected is not primary:raise selected from primary
        raise


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root');p.add_argument('--source-sha256')
    p.add_argument('--execute-reviewed-provider-smoke',action='store_true',default=False);args=p.parse_args(argv)
    if not args.execute_reviewed_provider_smoke:return {'status':'UNARMED','hosted_operation_performed':False,'security_accepted':False}
    return run(args)


if __name__=='__main__':
    try:print(json.dumps(main(),sort_keys=True))
    except BaseException as e:print(json.dumps(getattr(e,'provider_outer_failure',{'status':'REFUSED_OR_FAILED','failure':failure(e),'security_accepted':False}),sort_keys=True));raise SystemExit(1)from None
