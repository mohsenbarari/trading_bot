"""Root read-only source observation, bounded SSH and no private diagnostics."""
from pathlib import Path
from types import ModuleType
from hashlib import sha256
import json, os, selectors, shlex, subprocess, time

MAIN=Path('/root/trading-bot/trading_bot')
OUT=MAIN/'.local/security-market-current-source93-20261026-01'
RUNNER=MAIN/'.local/security-sqlite-ubuntu-package-build-runner-20261026-04/build_package.py'
READER=MAIN/'.local/security-market-source93-reader-20261026-01/read_source93.py'
CID='bfa02ea6a77329d8bb0c0efd069846220b1843fa3362ba1372c59fe7dfa4c6ff'
IMAGE='sha256:f79cdda21c192c52536f4ccfecadc7f724453a9ea4abfae96b1efe540ebbc61b'
SSH=['/usr/bin/ssh','-F','/dev/null','-i','/root/.ssh/id_ed25519_iran','-p','37067','-oBatchMode=yes','-oIdentitiesOnly=yes','-oStrictHostKeyChecking=yes','-oForwardAgent=no','-oUpdateHostKeys=no','-oControlMaster=no','-S','none','-oClearAllForwardings=yes','-oConnectTimeout=15','root@65.109.220.59']
ENV={'PATH':'/usr/bin:/bin','HOME':'/root','LANG':'C.UTF-8','LC_ALL':'C.UTF-8'}
FMT='{"id":{{json .Id}},"image":{{json .Image}},"name":{{json .Name}},"running":{{json .State.Running}},"paused":{{json .State.Paused}},"pid":{{json .State.Pid}},"started_at":{{json .State.StartedAt}},"restarts":{{json .RestartCount}},"health":{{with index .State "Health"}}{{json .Status}}{{else}}null{{end}},"mount_destinations":[{{range $i,$m := .Mounts}}{{if $i}},{{end}}{{json $m.Destination}}{{end}}]}'
SCOPES=('/app/core/__init__.py','/app/core/market_intelligence','/app/config/market_data_sources.v1.json','/app/contracts/market_data','/app/deploy/market-data/migrations')

def need(ok):
    if not ok:raise RuntimeError('root_source_observation_refused')

def ident(s):
    return (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)

def loader():
    before=ident(RUNNER.lstat());need(RUNNER.resolve(strict=True)==RUNNER and before[2:7]==(0o100600,0,0,1,23949))
    parents=[(p,ident(p.lstat())[:5]) for p in RUNNER.parents]
    need(all(p.resolve(strict=True)==p and v[2]&0o170000==0o040000 and v[3:]==(0,0) and not v[2]&0o022 for p,v in parents))
    fd=os.open(RUNNER,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        need(ident(os.fstat(fd))==before);raw=os.pread(fd,23950,0)
        need(len(raw)==23949 and sha256(raw).hexdigest()=='0e528dfeda2c2e6e14a2030cdbc43a7c52db5dc3d4cde613750e8fb7d146984e' and ident(os.fstat(fd))==ident(RUNNER.lstat())==before)
    finally:os.close(fd)
    need(ident(RUNNER.lstat())==before and all(ident(p.lstat())[:5]==v for p,v in parents))
    B=ModuleType('_root_public_runner');B.__file__=str(RUNNER);exec(compile(raw,str(RUNNER),'exec'),B.__dict__);return B

def read_probe(B,held):
    held.check();os.lseek(held.fd,0,os.SEEK_SET)
    remote=shlex.join(['/usr/bin/docker','--host','unix:///var/run/docker.sock','exec','-i','--user','10001:10001','--workdir','/',CID,'/usr/local/bin/python3','-I','-S','-B','-'])
    p=subprocess.Popen(SSH+[remote],stdin=held.fd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,env=ENV,close_fds=True,start_new_session=True)
    end=time.monotonic()+60;count=0;parts=[]
    try:
        with selectors.DefaultSelector() as s:
            os.set_blocking(p.stdout.fileno(),False);s.register(p.stdout,selectors.EVENT_READ)
            while True:
                held.check();need(time.monotonic()<end)
                if not s.select(.25):continue
                raw=os.read(p.stdout.fileno(),min(4096,16384-count+1))
                if not raw:break
                count+=len(raw);need(count<=16384);parts.append(raw)
        code=p.wait(timeout=max(.001,end-time.monotonic()));held.check()
        return code,b''.join(parts)
    finally:
        if p.poll() is None:B.cleanup_child(p)
        p.stdout.close();held.check()

def main():
    need(not (OUT/'actual.json').exists())
    B=loader();held=B.Held(READER,65536,(30085,'da8455903a8194b3b321331ed6fa5eb70b5c4e09a227004be3519e3a5e8d8d66'))
    def metadata():
        command=SSH+[shlex.join(['/usr/bin/docker','--host','unix:///var/run/docker.sock','inspect','--type','container','--format',FMT,CID])]
        raw=B.run(command,env=ENV,deadline=time.monotonic()+30,maximum=8192,guard=held.check)
        v=json.loads(raw)
        need(v['id']==CID and v['image']==IMAGE and v['name']=='/market-private-pipeline-primary-market-capture-account2-1'
             and v['running'] is True and v['paused'] is False and v['pid']==1707556 and v['started_at']=='2026-10-26T16:42:44.957036046Z'
             and v['restarts']==0 and v['health']=='healthy' and type(v['mount_destinations'])is list
             and all(type(d)is str and d.startswith('/') for d in v['mount_destinations'])
             and not any(s==d or s.startswith(d.rstrip('/')+'/') for d in v['mount_destinations'] for s in SCOPES))
        return v
    try:
        before=metadata();code,raw=read_probe(B,held);after=metadata();need(after==before)
        B.save(OUT/'actual-reader.stdout',raw)
        observation=json.loads(raw)
        need(code==0 and observation['status']=='match' and observation['source_files']==93
             and observation['expected_manifest_sha256']=='fef6305b1d7e6db851d1d0b77f3aa9910aad535e83f64f012790165a3d62c81b'
             and observation['expected_rows_sha256']=='75d7d2cfb2c20fd036501e7aac198cb5567fb82c53301fb3972f8c81b935e57f'
             and observation['all_source_rows_match'] is True and observation['project_cache_bodies_read'] is False
             and observation['project_imported'] is False and observation['security_accepted'] is False)
        result={'schema':'root_current_market_source_observation/1','status':'PASS','container_before':before,'container_after':after,
                'reader':held.binding,'actual_peer_exit':code,'actual_reader_stdout':{'bytes':len(raw),'sha256':sha256(raw).hexdigest()},
                'observation':observation,'native_production_data_read':False,'source_only_nonatomic_observation':True,
                'activation_authority':False,'security_accepted':False}
        held.check()
    finally:
        held.close();held.named()
    B.save(OUT/'actual.json',B.encoded(result));print(json.dumps(result,sort_keys=True))

if __name__=='__main__':main()
