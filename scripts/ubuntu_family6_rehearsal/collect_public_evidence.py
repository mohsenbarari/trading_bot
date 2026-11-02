"""Finite public evidence collection; observational, never an acceptance gate."""
from pathlib import Path
from hashlib import sha256
import argparse,errno,json,os,re,stat,sys
LIMITS={'original-producer-evidence.zip':16*1024**2,'original-api-admission.json':65536,'inherited-policy-public.json':16384,'policy-removal.json':4096,'bootstrap-public.log':4*1024**2,'prepare.json':8*1024**2,'exercise.json':8*1024**2,'failed-public-phase-output.bin':8*1024**2}
def need(value,code):
    if not value:raise RuntimeError(code)
def frame(s):return(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def collect(temp,profile,uid):
    need(profile in {'ubuntu239','ubuntu243'}and temp.resolve(strict=True)==temp and temp.is_dir(),'temporary_scope')
    destination=temp/'family6-public-evidence';destination.mkdir(mode=0o700);rows=[];total=0
    work=[p for p in temp.iterdir()if re.fullmatch(r'family6-[a-f0-9]{32}-[a-z0-9_]{8}',p.name)]
    need(len(work)<=1,'multiple_owned_workdirs')
    sources=[(temp/('family6-result-'+profile+'.json'),4*1024**2)]
    if work:
        d=work[0];s=d.lstat();need(stat.S_ISDIR(s.st_mode)and s.st_uid==uid and s.st_mode&0o777==0o700 and d.resolve(strict=True)==d,'owned_workdir')
        sources.extend((d/name,limit)for name,limit in LIMITS.items())
    for path,limit in sources:
        try:s=path.lstat()
        except FileNotFoundError as error:need(error.errno==errno.ENOENT,'evidence_unknown');continue
        need(stat.S_ISREG(s.st_mode)and s.st_uid==uid and s.st_nlink==1 and not s.st_mode&0o077 and 0<=s.st_size<=limit,'evidence_file_scope')
        need(total+s.st_size<=48*1024**2,'aggregate_evidence_bound')
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:
            raw=os.pread(fd,limit+1,0);need(len(raw)==s.st_size and frame(s)==frame(os.fstat(fd))==frame(path.lstat()),'evidence_changed')
        finally:os.close(fd)
        try:os.fstat(fd)
        except OSError as error:need(error.errno==errno.EBADF,'source_close_unknown')
        else:need(False,'source_close_unknown')
        need(path.resolve(strict=True)==path and frame(path.lstat())==frame(s),'source_postclose_named_changed')
        target=destination/path.name;out=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
        try:
            view=memoryview(raw)
            while view:n=os.write(out,view);need(n>0,'evidence_write');view=view[n:]
            os.fsync(out)
        finally:os.close(out)
        try:os.fstat(out)
        except OSError as error:need(error.errno==errno.EBADF,'destination_close_unknown')
        else:need(False,'destination_close_unknown')
        rows.append({'name':path.name,'bytes':len(raw),'sha256':sha256(raw).hexdigest(),'source_full9':frame(s),'both_original_FDs_physically_closed':True});total+=len(raw)
    value={'schema':'family6_finite_public_evidence_collection/1','profile':profile,'files':rows,'total_bytes':total,'limit_bytes':48*1024**2,'observational_only':True,'rehearsal_accepted':False,'security_accepted':False}
    raw=json.dumps(value,sort_keys=True,indent=2).encode()+b'\n';need(len(raw)<=65536,'manifest_bound')
    path=destination/'COLLECTION.json';fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    try:need(os.write(fd,raw)==len(raw),'manifest_write');os.fsync(fd)
    finally:os.close(fd)
    return value
def main(argv=None):
    argv=list(sys.argv[1:]if argv is None else argv)
    if '--collect-public-evidence'not in argv:print('{"status":"UNARMED","Host_action":false}');return 0
    parser=argparse.ArgumentParser();parser.add_argument('--collect-public-evidence',action='store_true',required=True);parser.add_argument('--profile',choices=('ubuntu239','ubuntu243'),required=True);args=parser.parse_args(argv)
    try:
        need(os.getuid()==os.geteuid()==1001 and all(os.environ.get(k)==v for k,v in {'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'github-hosted','RUNNER_OS':'Linux','RUNNER_ARCH':'X64','GITHUB_REPOSITORY':'mohsenbarari/trading_bot','GITHUB_REPOSITORY_ID':'1065680631','GITHUB_EVENT_NAME':'workflow_dispatch','GITHUB_REF':'refs/heads/main'}.items()),'hosted_owned_repo_only')
        result=collect(Path(os.environ['RUNNER_TEMP']),args.profile,os.geteuid());print(json.dumps({'status':'COLLECTED_PUBLIC_OBSERVATIONS_ONLY','files':len(result['files']),'security_accepted':False}));return 0
    except BaseException:print('{"status":"EVIDENCE_COLLECTION_REFUSED","security_accepted":false}');return 1
if __name__=='__main__':raise SystemExit(main())
