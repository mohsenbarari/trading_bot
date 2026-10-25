"""One capture2 public source-bind replacement; CHECK is the default.

No image build, schema change, session read, writer/DNS change, or acceptance.
Only a new Root-reviewed public control can authorize the stopped-first path.
Private Compose/container configuration is compared only in bounded RAM.
"""
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime,timezone
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
import argparse
import errno
import fcntl
import json
import os
import re
import resource
import shlex
import signal
import stat
import subprocess
import sys
import time

OP='capture-conflict-diagnostic-20261025-01'
CONFIRM='REPLACE-ONLY-CAPTURE2-PUBLIC-DIAGNOSTIC-SOURCE-20261025-01'
ROOTS={'native':Path('/root/trading-bot/trading_bot/.local')/OP,'web':Path('/root/.local')/OP}
MARKER=Path('/root/secure-envs/trading-bot/queue-cutover-artifacts/production-release.lock')
SOURCE=Path('/root/secure-envs/trading-bot/.production-runtime-source.lock')
RESERVATION=Path('/root/secure-envs/trading-bot/release-control/private-primary-controller.lock')
WEB_OWNER=ROOTS['web']/'capture-conflict.guard'
PROJECT='market-private-pipeline-primary';SERVICE='market-capture-account2'
OLD_CID='6a16c8a1b4cdab3b4bcd2a0d4d4998ced7a455cc6c364255e5e0e3dacd9c2752'
IMAGE='sha256:f79cdda21c192c52536f4ccfecadc7f724453a9ea4abfae96b1efe540ebbc61b'
DEST='/app/core/market_intelligence/private_capture.py'
FOUNDATION_DEST='/app/core/market_intelligence/private_pipeline_foundation.py'
SESSION='/srv/trading-bot/market-data-staging-shadow/sessions/account2'
LEGACY_TOKEN=Path('/run/trading-bot/market-capture-legacy-authority-enabled')
LEGACY_UNITS=('coin-capture.service','market-channel-capture.service')
LEGACY_BODY=b'[Unit]\nConditionPathExists=/run/trading-bot/market-capture-legacy-authority-enabled\nRefuseManualStart=yes\n'
LEGACY_HEADER=b'# Owned Web boot guards 2026-10-24; exact-content cleanup only.\n'
OLD_SHA='1b9c611692978edec4c37de217e77abf00f5a0e456006060c936d40c53978929'
FOUNDATION_SHA='f9d2b55921431e069b06e7de463d8b1d36ecbc01a94d5d0d98cd59f1dc9260da'
# Set only to an actually generated/reviewed diagnostic packet, never a guess.
NEW_SHA='2227443411d2d2b9ffcf56259b6750b5517025387f83dee024125c2abeb912c8'
NEW_BYTES=132537
LIMIT=2*1024**2
DOCKER=['/usr/bin/docker','--host=unix:///var/run/docker.sock']
SSH=['/usr/bin/ssh','-F','/dev/null','-p','37067','-oBatchMode=yes','-oIdentitiesOnly=yes',
    '-oStrictHostKeyChecking=yes','-i','/root/.ssh/id_ed25519_iran','-oControlMaster=no','-S','none',
    '-oUpdateHostKeys=no','-oUserKnownHostsFile=/root/.ssh/known_hosts','-oForwardAgent=no',
    '-oClearAllForwardings=yes','-oConnectTimeout=15','root@65.109.220.59']
INSPECT='{"id":{{json .Id}},"image":{{json .Image}},"name":{{json .Name}},"config":{{json .Config}},"host":{{json .HostConfig}},"mounts":{{json .Mounts}},"networks":{{json .NetworkSettings.Networks}},"running":{{json .State.Running}},"restarting":{{json .State.Restarting}},"pid":{{json .State.Pid}}}'
CODES=frozenset(('control','window','root','path','metadata','source_changed','source_pin','fence_changed','fence_busy',
    'owner_exists','owner_changed','command','command_timeout','command_bound','cleanup','inventory','target','profile',
    'environment','compose','config','host','mounts','networks','source_delta','session_owner','cgroup','old_owner',
    'candidate_started','unknown','peer','journal','diagnostic','rollback_failed'))
PHASE='entry'

def need(ok,code):
    if not ok:raise RuntimeError(code)

class Unknown(RuntimeError):
    def __init__(self):super().__init__('unknown')

def same(a,b):
    if type(a) is not type(b):return False
    if type(a) is dict:return set(a)==set(b) and all(same(a[k],b[k]) for k in a)
    if type(a) is list:return len(a)==len(b) and all(same(x,y) for x,y in zip(a,b))
    return a==b

def encode(value):return (json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()

def document(raw,maximum=LIMIT):
    need(type(raw) is bytes and 0<len(raw)<=maximum,'control')
    def unique(rows):
        value={}
        for key,item in rows:need(key not in value,'control');value[key]=item
        return value
    return json.loads(raw,object_pairs_hook=unique,parse_constant=lambda _:need(False,'control'))

def ident(s):return [s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns]

def canonical(path):
    path=Path(path);need(path.is_absolute() and path.resolve(strict=True)==path,'path');return path

def protected_parents(path):
    rows=[]
    for parent in path.parents:
        s=parent.lstat();need(parent.resolve(strict=True)==parent and stat.S_ISDIR(s.st_mode)
            and s.st_uid==0 and not s.st_mode&0o022,'path');rows.append((parent,ident(s)[:5]))
    return rows

def public_file(stack,path,pin,maximum=LIMIT,mode=None):
    path=canonical(path);parents=protected_parents(path)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC);stack.callback(os.close,fd)
    before=ident(os.fstat(fd));need(stat.S_ISREG(before[2]) and before[3:6]==[0,0,1] and not before[2]&0o022
        and 0<before[6]<=maximum and (mode is None or stat.S_IMODE(before[2])==mode),'metadata')
    raw=os.pread(fd,before[6]+1,0);need(len(raw)==before[6] and sha256(raw).hexdigest()==pin,'source_pin')
    def check():
        need(all(p.resolve(strict=True)==p and ident(p.lstat())[:5]==v for p,v in parents),'source_changed')
        need(before==ident(os.fstat(fd))==ident(path.lstat()) and sha256(os.pread(fd,before[6]+1,0)).hexdigest()==pin,'source_changed')
    check();stack.callback(check);return raw,check

def private_metadata(stack,path):
    path=canonical(path);parents=protected_parents(path)
    fd=os.open(path,os.O_PATH|os.O_NOFOLLOW|os.O_CLOEXEC);stack.callback(os.close,fd);before=ident(os.fstat(fd))
    need(before[2]==0o100600 and before[3:6]==[0,0,1],'metadata')
    def check():
        need(before==ident(os.fstat(fd))==ident(path.lstat()) and all(ident(p.lstat())[:5]==v for p,v in parents),'source_changed')
    check();stack.callback(check);return check

def validate_control(value,now):
    fields={'schema','operation','nonce','source_commit','worker_sha256','issued_at','deadline','fences',
        'native_machine_id','web_machine_id','compose','old_source','new_source','foundation_source','public_profile_sha256'}
    need(type(value) is dict and set(value)==fields and value['schema']=='capture_conflict_control/1'
        and value['operation']==OP and type(value['nonce']) is str and re.fullmatch('[a-f0-9]{32}',value['nonce'])
        and type(value['source_commit']) is str and re.fullmatch('[a-f0-9]{40}',value['source_commit'])
        and all(type(value[k]) is str and re.fullmatch('[a-f0-9]{64}',value[k]) for k in ('worker_sha256','public_profile_sha256')),'control')
    need(type(value['issued_at']) is int and type(value['deadline']) is int and value['issued_at']<=now<value['deadline']<=value['issued_at']+900,'window')
    need(all(type(value[k]) is str and re.fullmatch('[a-f0-9]{32}',value[k]) for k in ('native_machine_id','web_machine_id')),'control')
    need(type(value['fences']) is dict and set(value['fences'])=={'native','web'},'control')
    for site,names in (('native',{'marker','source'}),('web',{'marker'})):
        rows=value['fences'][site];need(type(rows) is dict and set(rows)==names,'control')
        for name,row in rows.items():
            need(type(row) is list and len(row)==9 and all(type(n) is int for n in row) and row[0]>0 and row[1]>0
                and row[2:6]==[0o100600,0,0,1] and row[6]>=0 and (name!='source' or row[6]==0),'control')
    compose=value['compose'];need(type(compose) is dict and set(compose)=={'directory','files','env_reference'}
        and type(compose['directory']) is str and compose['directory']=='/root/.local/security-capture2-compose-parity-20261024-04'
        and type(compose['files']) is list and 2<=len(compose['files'])<=8
        and type(compose['env_reference']) is str and re.fullmatch('/srv/trading-bot/market-pipeline-releases/[a-f0-9]{40}/web.release.env',compose['env_reference']),'control')
    need(len({r['path'] for r in compose['files']})==len(compose['files']),'control')
    for row in compose['files']:
        need(type(row) is dict and set(row)=={'path','sha256'} and type(row['path']) is str
            and row['path'].startswith('/root/.local/') and row['path'].endswith('.yml')
            and type(row['sha256']) is str and re.fullmatch('[a-f0-9]{64}',row['sha256']),'control')
    for name,pin,size in (('old_source',OLD_SHA,111954),('new_source',NEW_SHA,NEW_BYTES),('foundation_source',FOUNDATION_SHA,37187)):
        row=value[name];need(type(row) is dict and set(row)=={'path','sha256','bytes'} and type(row['path']) is str
            and row['path'].startswith('/root/.local/') and row['sha256']==pin and type(row['bytes']) is int and row['bytes']==size,'control')
    need(re.fullmatch('[a-f0-9]{64}',NEW_SHA) and NEW_BYTES>0
        and value['new_source']['path']==str(ROOTS['web']/value['nonce']/'private_capture.py')
        and value['old_source']['path']!=value['new_source']['path'],'control')
    return value

class MetadataFence:
    """Permanent M/SOURCE payloads are NEVER read, copied, hashed or rewritten."""
    def __init__(self,path,expected,stack):
        self.path=canonical(path);self.parents=protected_parents(self.path);self.expected=expected
        self.fd=os.open(self.path,os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC);stack.callback(os.close,self.fd)
        self.check()
        try:fcntl.flock(self.fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('fence_busy') from None
        self.check()
    def check(self):
        need(ident(os.fstat(self.fd))==ident(self.path.lstat())==self.expected
            and all(ident(p.lstat())[:5]==v for p,v in self.parents),'fence_changed')

def write_new(path,value):
    raw=encode(value) if type(value) is not bytes else value
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    try:
        view=memoryview(raw)
        while view:n=os.write(fd,view);need(n>0,'journal');view=view[n:]
        os.fchmod(fd,0o600);os.fsync(fd)
    finally:os.close(fd)
    directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC)
    try:os.fsync(directory)
    finally:os.close(directory)
    return sha256(raw).hexdigest()

class OwnedGuard:
    def __init__(self,path,nonce,pin,stack):
        self.path=path;self.parents=protected_parents(path);need(not os.path.lexists(path),'owner_exists')
        self.fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600);stack.callback(os.close,self.fd)
        raw=encode({'schema':'capture_conflict_owner/1','nonce':nonce,'control_sha256':pin,'retry_allowed':False})
        need(os.write(self.fd,raw)==len(raw),'journal');os.fchmod(self.fd,0o600);os.fsync(self.fd)
        fcntl.flock(self.fd,fcntl.LOCK_EX|fcntl.LOCK_NB);self.before=ident(os.fstat(self.fd));self.check();self.closed=False
        self.sync_parent()
    def sync_parent(self):
        fd=os.open(self.path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC)
        try:os.fsync(fd)
        finally:os.close(fd)
    def check(self):
        need(self.before==ident(os.fstat(self.fd))==ident(self.path.lstat())
            and all(p.resolve(strict=True)==p and ident(p.lstat())[:5]==v for p,v in self.parents),'owner_changed')
    def close_known(self):
        self.check();self.path.unlink();self.sync_parent();after=ident(os.fstat(self.fd))
        need(after[:5]==self.before[:5] and after[5]==0 and after[6:8]==self.before[6:8]
            and not os.path.lexists(self.path),'owner_changed');self.closed=True

class Journal:
    STATES=frozenset(('prepared','stop_requested','old_stopped','create_requested','candidate_stopped_verified',
        'start_requested','diagnostic_active_incident_open','rollback_requested','rolled_back_incident_open',
        'refused_before_stop','outcome_unknown','terminal_durable','guard_closed','peer_requested'))
    def __init__(self,path,nonce,pin):self.path=path;self.nonce=nonce;self.pin=pin;self.index=0
    def add(self,phase,**public):
        need(phase in self.STATES and set(public)<={'container_id','full_config_parity','source_bind_parity','diagnostic'},'journal')
        for k,v in public.items():
            if k=='container_id':need(type(v) is str and re.fullmatch('[a-f0-9]{64}',v),'journal')
            elif k=='diagnostic':need(v is None or diagnostic(v) is not None,'journal')
            else:need(type(v) is bool,'journal')
        self.index+=1;return write_new(self.path/('%02d-%s.json'%(self.index,phase)),{'schema':'capture_conflict_journal/1',
            'nonce':self.nonce,'control_sha256':self.pin,'phase':phase,'security_accepted':False,'capture_issue_open':True,**public})

def bounded(argv,clock,*,maximum=LIMIT,stdin=subprocess.DEVNULL,mutating=False,allowed_codes=(0,),combine_stderr=False,seconds=120):
    fd=os.memfd_create('capture-private-command',os.MFD_CLOEXEC);child=None;primary=False
    def limits():
        resource.setrlimit(resource.RLIMIT_CORE,(0,0));resource.setrlimit(resource.RLIMIT_FSIZE,(maximum+1,maximum+1))
    try:
        child=subprocess.Popen(argv,stdin=stdin,stdout=fd,stderr=fd if combine_stderr else subprocess.DEVNULL,start_new_session=True,close_fds=True,preexec_fn=limits,
            env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LC_ALL':'C.UTF-8','DOCKER_HOST':'unix:///var/run/docker.sock'})
        end=min(clock.end,time.monotonic()+seconds)
        while child.poll() is None:
            need(os.fstat(fd).st_size<=maximum,'command_bound');need(time.monotonic()<end,'command_timeout');time.sleep(.02)
        need(os.fstat(fd).st_size<=maximum,'command_bound');code=child.wait(timeout=1)
        if code not in allowed_codes:raise Unknown() if mutating else RuntimeError('command')
        size=os.fstat(fd).st_size;raw=os.pread(fd,size+1,0);need(len(raw)==size,'command_bound');return raw
    except BaseException:
        primary=True
        if mutating:raise Unknown() from None
        raise
    finally:
        clean=True
        if child is not None:
            try:os.killpg(child.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            except BaseException:clean=False
            try:child.wait(timeout=2)
            except BaseException:clean=False
        os.close(fd)
        if not clean and not primary:raise Unknown() if mutating else RuntimeError('cleanup')

class Clock:
    def __init__(self,control):self.end=time.monotonic()+control['deadline']-time.time()
    def check(self):need(time.monotonic()<self.end,'window')

def environment(rows):
    need(type(rows) is list and all(type(v) is str and '=' in v for v in rows),'environment');value={}
    for row in rows:
        key,item=row.split('=',1);need(key not in value,'environment');value[key]=item
    return value

def mounts(rows):
    need(type(rows) is list and all(type(r) is dict and type(r.get('Destination')) is str for r in rows),'mounts')
    need(len({r['Destination'] for r in rows})==len(rows),'mounts');return sorted(rows,key=lambda r:r['Destination'])

def canonical_host(value):
    value=deepcopy(value)
    if value.get('OomKillDisable') is None:value['OomKillDisable']=False
    for key,target in (('Binds',None),('Mounts','Target')):
        rows=value.get(key) or [];need(type(rows) is list,'host')
        if target:need(all(type(r) is dict and type(r.get(target)) is str for r in rows),'host');paths=[r[target] for r in rows]
        else:need(all(type(r) is str and len(r.rsplit(':',2))==3 for r in rows),'host');paths=[r.rsplit(':',2)[1] for r in rows]
        need(len(set(paths))==len(paths),'host')
        if not any(Path(a) in Path(b).parents or Path(b) in Path(a).parents for i,a in enumerate(paths) for b in paths[i+1:]):
            value[key]=sorted(rows,key=(lambda r:r[target]) if target else lambda r:r.rsplit(':',2)[1])
    return value

def static_networks(value):
    need(type(value) is dict,'networks');out={}
    for name,row in value.items():
        need(type(row) is dict and {'NetworkID','IPAMConfig','DriverOpts','Links','Aliases'}<=set(row),'networks')
        aliases=row.get('Aliases') or [];need(type(aliases) is list and all(type(v) is str for v in aliases) and len(set(aliases))==len(aliases),'networks')
        out[name]={k:deepcopy(v) for k,v in row.items() if k not in {'EndpointID','IPAddress','MacAddress','GlobalIPv6Address','DNSNames','Gateway','IPPrefixLen','IPv6Gateway','GlobalIPv6PrefixLen'}}
        out[name]['Aliases']=sorted(aliases)
        if same(out[name]['IPAMConfig'],{'IPv4Address':'','IPv6Address':''}):out[name]['IPAMConfig']=None
    return out

def source_row(rows,destination):
    selected=[r for r in rows if r.get('Destination')==destination];need(len(selected)==1 and selected[0].get('Type')=='bind'
        and selected[0].get('RW') is False,'source_delta');return selected[0]

def replace_mount_source(row,old,new):
    value=deepcopy(row);bound=source_row(value['mounts'],DEST);need(bound['Source']==old,'source_delta');bound['Source']=new
    host=value['host'];count=0
    for i,bind in enumerate(host.get('Binds') or []):
        source,destination,mode=bind.rsplit(':',2)
        if destination==DEST:need(source==old and mode=='ro','source_delta');host['Binds'][i]=new+':'+destination+':'+mode;count+=1
    for mount in host.get('Mounts') or []:
        if mount.get('Target')==DEST:need(mount['Source']==old and mount.get('ReadOnly') is True,'source_delta');mount['Source']=new;count+=1
    need(count==1,'source_delta');return value

def rendered_delta(old,new,oldpath,newpath):
    expected=deepcopy(old);service=expected['services'][SERVICE];rows=[r for r in service.get('volumes',[]) if r.get('target')==DEST]
    need(len(rows)==1 and rows[0].get('type')=='bind' and rows[0].get('read_only') is True and rows[0].get('source')==oldpath,'source_delta')
    rows[0]['source']=newpath;need(same(expected,new),'source_delta')

def expected_source_change(old,new,oldpath,newpath,*,stopped=False):
    expected=replace_mount_source(old,oldpath,newpath)
    need(old['image']==new['image']==IMAGE and new['id']!=old['id'],'target')
    a,b=expected['config'],new['config'];need(set(a)==set(b),'config')
    need(environment(a['Env'])==environment(b['Env']),'environment')
    omitted={'Env','Hostname','Labels'};need(same({k:v for k,v in a.items() if k not in omitted},{k:v for k,v in b.items() if k not in omitted}),'config')
    need(b['Hostname']==new['id'][:12],'config')
    changing={'com.docker.compose.config-hash','com.docker.compose.project.config_files','com.docker.compose.replace'}
    need(same({k:v for k,v in a['Labels'].items() if k not in changing},{k:v for k,v in b['Labels'].items() if k not in changing}),'config')
    need(type(b['Labels'].get('com.docker.compose.config-hash')) is str
        and re.fullmatch('[a-f0-9]{64}',b['Labels']['com.docker.compose.config-hash'])
        and b['Labels'].get('com.docker.compose.replace') in {None,SERVICE+'-1'},'config')
    need(same(canonical_host(expected['host']),canonical_host(new['host'])) and same(mounts(expected['mounts']),mounts(new['mounts'])),'host')
    left,right=static_networks(old['networks']),static_networks(new['networks']);need(set(left)==set(right),'networks')
    for name in left:
        if stopped and right[name].get('NetworkID')=='':right[name]['NetworkID']=left[name]['NetworkID']
    need(same(left,right),'networks')

def other_static(before,after,old,newid=None):
    keys=set(before)-{old};need(set(after)-({newid} if newid else {old})==keys,'inventory')
    for key in keys:
        def static(row):return {'id':row['id'],'image':row['image'],'name':row['name'],'config':row['config'],
            'host':canonical_host(row['host']),'mounts':mounts(row['mounts']),'networks':static_networks(row['networks'])}
        need(same(static(before[key]),static(after[key])),'profile')

def session_owners(rows,allowed=None):
    wanted=Path(SESSION)
    for key,row in rows.items():
        if key==allowed or not (row['running'] or row['restarting']):continue
        need((row['config'].get('Labels') or {}).get('com.docker.compose.service')!=SERVICE,'session_owner')
        for mount in row['mounts']:
            source=Path(mount['Source']);need(source.is_absolute() and mount['Source']==os.path.normpath(mount['Source']),'mounts')
            need(source!=wanted and source not in wanted.parents and wanted not in source.parents,'session_owner')

def public_profile(rows):
    # Only IDs, immutable images and public container names; never Config/Env.
    return sha256(encode(sorted([{'id':r['id'],'image':r['image'],'name':r['name']} for r in rows.values()],key=lambda r:r['id']))).hexdigest()

def duration(value):
    match=re.fullmatch(r'(\d+)(ns|us|ms|s|m|h)',value);need(match is not None,'compose')
    return int(match[1])*{'ns':1,'us':1000,'ms':1000000,'s':1000000000,'m':60000000000,'h':3600000000000}[match[2]]

def full_parity(rendered,row,image):
    """Full normal-Compose model vs actual Config/HostConfig, only in RAM."""
    service=rendered['services'][SERVICE];cfg=row['config'];host=row['host'];defaults=image['Config'];mounts(row['mounts'])
    need(type(cfg.get('Image')) is str and row['image']==image['Id']==service['image']==cfg['Image']==IMAGE,'target')
    expected_env=environment(defaults.get('Env') or []);expected_env.update(service.get('environment',{}))
    need(all(type(v) is str for v in expected_env.values()) and same(expected_env,environment(cfg['Env'])),'environment')
    for field,key,default in (('Cmd','command',defaults.get('Cmd')),('Entrypoint','entrypoint',defaults.get('Entrypoint')),
            ('User','user',defaults.get('User')),('WorkingDir','working_dir',defaults.get('WorkingDir'))):
        desired=service.get(key,default)
        if key=='entrypoint' and desired is None:desired=default
        need(same(cfg.get(field),desired),'config')
    health=service['healthcheck'];expected_health={'Test':health['test'],'Interval':duration(health['interval']),
        'Timeout':duration(health['timeout']),'StartPeriod':duration(health['start_period']),'Retries':health['retries']}
    need(same(cfg['Healthcheck'],expected_health),'config')
    fixed={'AttachStdin':False,'AttachStdout':True,'AttachStderr':True,'OpenStdin':False,'StdinOnce':False,'Tty':False,'Domainname':'','Hostname':row['id'][:12]}
    need(set(cfg)==set(fixed)|{'Cmd','Entrypoint','Env','Healthcheck','Image','Labels','User','Volumes','WorkingDir'}
        and all(same(cfg[k],v) for k,v in fixed.items()) and same(cfg['Volumes'],defaults.get('Volumes')),'config')
    labels={**(defaults.get('Labels') or {}),**service.get('labels',{})}
    need(same({k:v for k,v in cfg['Labels'].items() if not k.startswith('com.docker.compose.')},labels),'config')
    volumes=service.get('volumes',[]);binds=sorted(r['source']+':'+r['target']+(':ro' if r.get('read_only') else ':rw') for r in volumes)
    native=[];expected_mounts={(r['type'],r['source'],r['target'],not r.get('read_only',False)) for r in volumes}
    for secret in service.get('secrets',[]):
        need(type(secret) is dict and not set(secret)-{'source','target'},'compose');target=secret.get('target',secret['source'])
        target=target if target.startswith('/') else '/run/secrets/'+target;source=rendered['secrets'][secret['source']]['file']
        native.append({'Type':'bind','Source':source,'Target':target,'ReadOnly':True,'BindOptions':{}});expected_mounts.add(('bind',source,target,False))
    need(expected_mounts=={(r['Type'],r['Source'],r['Destination'],r['RW']) for r in row['mounts']}
        and all(r.get('Propagation')=='rprivate' for r in row['mounts']),'mounts')
    memory=int(service['mem_limit']);policy=service.get('restart','no').split(':')
    expected={'ReadonlyRootfs':service.get('read_only',False),'Init':service.get('init',False),'CapDrop':service.get('cap_drop'),
        'SecurityOpt':service.get('security_opt'),'PidsLimit':service.get('pids_limit'),'Memory':memory,
        'NanoCpus':int(Decimal(str(service.get('cpus',0)))*1000000000),
        'RestartPolicy':{'Name':policy[0],'MaximumRetryCount':int(policy[1]) if len(policy)==2 else 0},
        'LogConfig':{'Type':service['logging']['driver'],'Config':service['logging'].get('options',{})},
        'Tmpfs':dict(r.split(':',1) for r in service.get('tmpfs',[])),'Binds':binds,'Mounts':sorted(native,key=lambda r:r['Target'])}
    for key,item in expected.items():
        actual=host.get(key)
        if key=='Binds':actual=sorted(actual or [])
        if key=='Mounts':actual=sorted(actual or [],key=lambda r:r['Target'])
        need(same(actual,item),'host')
    empty={'BlkioDeviceReadBps','BlkioDeviceReadIOps','BlkioDeviceWriteBps','BlkioDeviceWriteIOps','BlkioWeightDevice','CapAdd',
        'DeviceCgroupRules','DeviceRequests','Devices','Dns','DnsOptions','DnsSearch','ExtraHosts','GroupAdd','Links','PortBindings','Ulimits','VolumesFrom'}
    zero={'BlkioWeight','CpuCount','CpuPercent','CpuPeriod','CpuQuota','CpuRealtimePeriod','CpuRealtimeRuntime','CpuShares','IOMaximumBandwidth','IOMaximumIOps','MemoryReservation','OomScoreAdj'}
    text={'Cgroup','CgroupParent','ContainerIDFile','CpusetCpus','CpusetMems','Isolation','PidMode','UTSMode','UsernsMode','VolumeDriver'}
    fixed_host={'AutoRemove':False,'CgroupnsMode':'private','ConsoleSize':[0,0],'IpcMode':'private','MemorySwappiness':None,
        'Privileged':False,'PublishAllPorts':False,'Runtime':'runc','ShmSize':67108864,'MemorySwap':memory*2,
        'NetworkMode':rendered['networks']['market-backend']['name']}
    need(set(host)==set(expected)|empty|zero|text|set(fixed_host)|{'OomKillDisable','MaskedPaths','ReadonlyPaths'},'host')
    need(all(host[k] is None or same(host[k],[]) or k=='PortBindings' and same(host[k],{}) for k in empty)
        and all(type(host[k]) is int and host[k]==0 for k in zero) and all(host[k]=='' for k in text)
        and all(same(host[k],v) for k,v in fixed_host.items()) and (host['OomKillDisable'] is None or host['OomKillDisable'] is False),'host')
    masked={'/proc/acpi','/proc/asound','/proc/interrupts','/proc/kcore','/proc/keys','/proc/latency_stats','/proc/sched_debug',
        '/proc/scsi','/proc/timer_list','/proc/timer_stats','/sys/devices/virtual/powercap','/sys/firmware'}
    need(set(host['MaskedPaths'] or [])==masked and set(host['ReadonlyPaths'] or [])=={'/proc/bus','/proc/fs','/proc/irq','/proc/sys','/proc/sysrq-trigger'},'host')
    names={rendered['networks'][n]['name'] for n in service['networks']};need(names==set(row['networks']),'networks')
    need(all(set(r.get('Aliases') or [])=={PROJECT+'-'+SERVICE+'-1',SERVICE} for r in row['networks'].values()),'networks')
    allowed={'image','profiles','user','read_only','init','restart','cap_drop','security_opt','pids_limit','mem_limit','cpus',
        'tmpfs','logging','environment','command','entrypoint','secrets','volumes','depends_on','healthcheck','networks'}
    need(not set(service)-allowed,'compose');return True

def diagnostic(value):
    fields={'schema','account','source_code','event_schema','schema_version','event_type','incoming_origin','previous_origin',
        'branch','rollback_confirmed','comparison_status','changed_fields','security_accepted'}
    labels=set('schema event_type occurred_time source_policy message_text message_revision message_publication message_edit message_content_type sender_peer sender_kind sender_telegram_id sender_display_name reply_status reply_parent reply_topic topic_status topic_id entities media_type action_type grouped_id forward_flags producer_name producer_version other_semantic_fields'.split())
    if type(value) is not dict or set(value)!=fields:return None
    choices={'schema':{'capture_identity_conflict_diagnostic/1'},'account':{'account2'},'source_code':{'GROUP_1','GROUP_2'},
        'event_schema':{'coin_group_event'},'schema_version':{'2.0','2.1'},'event_type':{'message_created','message_snapshot','message_edited','message_deleted'},
        'incoming_origin':{'live','reconcile','explicit_backfill','unclassified'},'previous_origin':{'live','reconcile','explicit_backfill','unclassified'},
        'branch':{'seen'},'comparison_status':{'compared','not_found','read_bound','timeout','unavailable','old_identity_mismatch','invalid_record','context_unavailable'}}
    if any(type(value[k]) is not str or value[k] not in allowed for k,allowed in choices.items()):return None
    rows=value['changed_fields']
    if value['rollback_confirmed'] is not True or value['security_accepted'] is not False or type(rows) is not list:return None
    if any(type(v) is not str or v not in labels for v in rows) or rows!=sorted(set(rows)) or len(encode(value))>4096:return None
    if value['comparison_status']!='compared' and rows:return None
    return value

class Web:
    def __init__(self,control,pin,root,stack,checks):
        self.op=control;self.pin=pin;self.root=root;self.stack=stack;self.checks=checks;self.clock=Clock(control);self.requested=False
        self.journal=None;self.override=root/'diagnostic-source.yml'
    def check(self):
        self.clock.check()
        for check in self.checks:check()
    def command(self,argv,*,mutating=False,stdin=subprocess.DEVNULL,combine_stderr=False):
        self.check()
        if mutating:self.requested=True
        return bounded(argv,self.clock,mutating=mutating,stdin=stdin,combine_stderr=combine_stderr)
    def snapshot(self,absent=False):
        ids=self.command([*DOCKER,'ps','-aq','--no-trunc']).decode().splitlines()
        need(len(ids)==len(set(ids)) and len(ids) in ({26,27} if absent else {27}) and all(re.fullmatch('[a-f0-9]{64}',v) for v in ids),'inventory')
        return {key:document(self.command([*DOCKER,'inspect','--format',INSPECT,key])) for key in ids}
    def target(self,rows):
        chosen=[r for r in rows.values() if r['name']=='/'+PROJECT+'-'+SERVICE+'-1'];need(len(chosen)==1,'target');row=chosen[0]
        need(row['image']==IMAGE and row['config']['Labels'].get('com.docker.compose.project')==PROJECT
            and row['config']['Labels'].get('com.docker.compose.service')==SERVICE,'target')
        need(any(m.get('Type')=='bind' and m.get('Source')==SESSION and m.get('Destination')=='/var/lib/market-data/session'
            and m.get('RW') is True for m in row['mounts']),'session_owner');return row
    def compose(self,*tail,new=False,override=None):
        spec=self.op['compose'];argv=[*DOCKER,'compose','--project-name',PROJECT,'--project-directory',spec['directory'],'--env-file',spec['env_reference']]
        for row in spec['files']:argv+=['-f',row['path']]
        if new:argv+=['-f',str(override if override is not None else self.override)]
        return [*argv,'--profile','web',*tail]
    def render(self,new=False,public_override=None):
        if public_override is None:return document(self.command(self.compose('config','--format','json',new=new)))
        fd=os.memfd_create('capture-public-compose-override',os.MFD_CLOEXEC)
        try:
            raw=encode(public_override);need(len(raw)<=65536 and os.write(fd,raw)==len(raw),'compose');os.lseek(fd,0,os.SEEK_SET)
            return document(self.command(self.compose('config','--format','json',new=True,override='-'),stdin=fd))
        finally:os.close(fd)
    def bind_cgroup(self,row):
        pid=row['pid'];need(type(pid) is int and pid>0 and row['running'] is True and row['restarting'] is False,'old_owner')
        path=Path('/proc')/str(pid)/'cgroup'
        with path.open('rb') as source:raw=source.read(65537)
        need(0<len(raw)<=65536 and raw.startswith(b'0::/') and raw.count(b'\n')==1,'cgroup')
        relative=raw.decode().strip()[4:];folder=canonical(Path('/sys/fs/cgroup')/relative)
        need(row['id'] in relative,'cgroup');fd=os.open(folder,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC);self.stack.callback(os.close,fd)
        before=ident(os.fstat(fd))[:2]
        current=self.target(self.snapshot());need(current['id']==row['id'] and current['pid']==pid,'old_owner')
        def empty():
            self.check();need(ident(os.fstat(fd))[:2]==before,'cgroup')
            if os.path.lexists(folder):need(ident(folder.lstat())[:2]==before,'cgroup')
            try:events=os.open('cgroup.events',os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
            except FileNotFoundError:
                need(not os.path.lexists(folder),'cgroup');return
            try:value=os.read(events,4097)
            finally:os.close(events)
            need(len(value)<=4096 and dict(line.split() for line in value.splitlines()).get(b'populated')==b'0','cgroup')
        return empty
    def legacy_owners(self):
        """Read-only existing host inhibitors; no session/process inspection."""
        import dbus
        need(not os.path.lexists(LEGACY_TOKEN),'session_owner');self.check()
        bus=dbus.SystemBus(private=True);held=[]
        try:
            manager=dbus.Interface(bus.get_object('org.freedesktop.systemd1','/org/freedesktop/systemd1'),'org.freedesktop.systemd1.Manager')
            for name in LEGACY_UNITS:
                manager.RefUnit(name,timeout=5);held.append(name);obj=manager.GetUnit(name,timeout=5)
                props=dbus.Interface(bus.get_object('org.freedesktop.systemd1',obj),'org.freedesktop.DBus.Properties')
                unit=lambda key:props.Get('org.freedesktop.systemd1.Unit',key,timeout=5)
                service=lambda key:props.Get('org.freedesktop.systemd1.Service',key,timeout=5)
                need(str(unit('LoadState'))=='loaded' and str(unit('UnitFileState'))=='disabled'
                    and str(unit('ActiveState')) in {'inactive','failed'} and bool(unit('RefuseManualStart'))
                    and not bool(unit('NeedDaemonReload')) and int(service('MainPID'))==int(service('ControlPID'))==0,'session_owner')
                need(any(list(row[:4])==['ConditionPathExists',False,False,str(LEGACY_TOKEN)] for row in unit('Conditions')),'session_owner')
                runtime=Path('/run/systemd/system')/(name+'.d')/'market-container-authority.conf'
                persistent=Path('/etc/systemd/system')/(name+'.d')/'90-security-20261024-web-boot.conf'
                files={str(runtime)}
                _,check=public_file(self.stack,runtime,sha256(LEGACY_BODY).hexdigest(),8192);self.checks.append(check)
                if os.path.lexists(persistent):
                    _,check=public_file(self.stack,persistent,sha256(LEGACY_HEADER+LEGACY_BODY).hexdigest(),8192);self.checks.append(check);files.add(str(persistent))
                need(set(map(str,unit('DropInPaths')))==files,'session_owner')
                group=str(unit('ControlGroup'))
                if group:
                    need(group.startswith('/') and '..' not in Path(group).parts,'cgroup')
                    folder=Path('/sys/fs/cgroup')/group.lstrip('/')
                    if folder.exists():
                        with (canonical(folder)/'cgroup.events').open('rb') as source:raw=source.read(4097)
                        need(len(raw)<=4096 and dict(line.split() for line in raw.splitlines()).get(b'populated')==b'0','session_owner')
        finally:
            for name in reversed(held):
                try:manager.UnrefUnit(name,timeout=5)
                except Exception:pass
            bus.close()
        need(not os.path.lexists(LEGACY_TOKEN),'session_owner');self.check()
    def stop(self,new=False):self.command([*self.compose('stop','--timeout','30',new=new),SERVICE],mutating=True)
    def create(self,new=False):self.command([*self.compose('up','--no-start','--no-deps','--no-build','--pull','never','--force-recreate',new=new),SERVICE],mutating=True)
    def start(self,cid):self.command([*DOCKER,'start',cid],mutating=True)
    def observe(self,cid):
        records=[];end=min(self.clock.end-30,time.monotonic()+120)
        while time.monotonic()<end:
            raw=self.command([*DOCKER,'logs','--tail','64',cid],combine_stderr=True)
            for line in raw.splitlines():
                if not 0<len(line)<=4096:continue
                try:value=diagnostic(document(line,4096))
                except Exception:value=None
                if value is not None and value not in records:records.append(value)
            if records:return records[-1]
            time.sleep(min(2,max(0,end-time.monotonic())))
        return None

def provenance(row,files,env_reference):
    labels=row['config']['Labels'];need(labels.get('com.docker.compose.project.working_dir')=='/root/.local/security-capture2-compose-parity-20261024-04'
        and labels.get('com.docker.compose.project.config_files')==','.join(map(str,files))
        and labels.get('com.docker.compose.project.environment_file')==env_reference,'compose')

def replacement(backend,baseline,old_render,new_render):
    old=backend.target(baseline);oldpath=backend.op['old_source']['path'];newpath=backend.op['new_source']['path']
    backend.legacy_owners();empty=backend.bind_cgroup(old);backend.old_empty_proof=empty;backend.journal.add('stop_requested');backend.stop()
    stopped=backend.snapshot();target=backend.target(stopped)
    need(target['id']==old['id'] and target['pid']==0 and target['running'] is False and target['restarting'] is False,'old_owner')
    empty();session_owners(stopped);other_static(baseline,stopped,old['id']);backend.journal.add('old_stopped')
    backend.journal.add('create_requested');backend.create(new=True)
    created=backend.snapshot();new=backend.target(created);need(new['pid']==0 and new['running'] is False and new['restarting'] is False,'candidate_started')
    backend.created_stopped_id=new['id']
    other_static(baseline,created,old['id'],new['id']);session_owners(created);empty();backend.legacy_owners()
    expected_source_change(old,new,oldpath,newpath,stopped=True);backend.full_parity(new_render,new)
    provenance(new,[r['path'] for r in backend.op['compose']['files']]+[str(backend.override)],backend.op['compose']['env_reference'])
    backend.journal.add('candidate_stopped_verified',container_id=new['id'],full_config_parity=True,source_bind_parity=True)
    backend.journal.add('start_requested',container_id=new['id']);backend.start_requested_id=new['id'];backend.start(new['id'])
    after=backend.snapshot();started=backend.target(after);need(started['id']==new['id'],'target')
    other_static(baseline,after,old['id'],new['id']);session_owners(after,new['id']);expected_source_change(old,started,oldpath,newpath)
    backend.full_parity(new_render,started);backend.check();return started

def rollback(backend,baseline,old_render):
    before=backend.target(baseline);current=backend.snapshot(absent=True)
    targets=[r for r in current.values() if r['name']==before['name']]
    if targets:
        current_target=backend.target(current)
        if current_target['pid']==0 and current_target['running'] is False and current_target['restarting'] is False:
            if (getattr(backend,'created_stopped_id',None)==current_target['id']
                    and getattr(backend,'start_requested_id',None)!=current_target['id']):
                need(callable(getattr(backend,'old_empty_proof',None)),'old_owner')
                empty=backend.old_empty_proof  # Exact never-started candidate; retain the original stop proof.
            elif current_target['id']==before['id'] and callable(getattr(backend,'old_empty_proof',None)):
                empty=backend.old_empty_proof
            else:raise Unknown()  # Exited/possibly-started tasks need their own cgroup proof.
        else:empty=backend.bind_cgroup(current_target)
        backend.stop(new=True);stopped=backend.snapshot();target=backend.target(stopped)
        need(target['pid']==0 and target['running'] is False and target['restarting'] is False,'old_owner');empty();session_owners(stopped)
        other_static(baseline,stopped,before['id'],target['id'])
    else:need(set(current)==set(baseline)-{before['id']},'inventory');session_owners(current)
    backend.legacy_owners();backend.journal.add('rollback_requested');backend.create()
    created=backend.snapshot();restored=backend.target(created);need(restored['pid']==0 and restored['running'] is False and restored['restarting'] is False,'candidate_started')
    # Swap the old/new arguments to compare the exact original bind contract.
    expected_source_change(replace_mount_source(before,backend.op['old_source']['path'],backend.op['new_source']['path']),
        restored,backend.op['new_source']['path'],backend.op['old_source']['path'],stopped=True)
    backend.full_parity(old_render,restored);other_static(baseline,created,before['id'],restored['id']);session_owners(created);backend.legacy_owners()
    backend.start(restored['id']);after=backend.snapshot();final=backend.target(after)
    need(final['id']==restored['id'],'target');backend.full_parity(old_render,final)
    other_static(baseline,after,before['id'],final['id']);session_owners(after,final['id']);backend.check();return final

def result(control,pin,status,*,cid=None,record=None):
    need(record is None or diagnostic(record) is not None,'diagnostic')
    value={'schema':'capture_conflict_outcome/1','nonce':control['nonce'],'control_sha256':pin,'status':status,
        'capture_issue_open':True,'security_accepted':False,'functional_fix_claimed':False,'retry_allowed':False}
    if cid is not None:value['container_id']=cid
    if record is not None:value['diagnostic']=record
    return value

def web_run(control,pin,root,stack,checks,apply=False):
    need(not os.path.lexists(WEB_OWNER),'owner_exists')
    backend=Web(control,pin,root,stack,checks);baseline=backend.snapshot();old=backend.target(baseline)
    need(old['id']==OLD_CID and public_profile(baseline)==control['public_profile_sha256'],'profile')
    source=source_row(old['mounts'],DEST);foundation=source_row(old['mounts'],FOUNDATION_DEST)
    need(source['Source']==control['old_source']['path'] and foundation['Source']==control['foundation_source']['path'],'source_delta')
    provenance(old,[r['path'] for r in control['compose']['files']],control['compose']['env_reference']);session_owners(baseline,old['id'])
    backend.legacy_owners()
    version=backend.command([*DOCKER,'compose','version','--short']).decode().strip()
    need(version in {'2.40.3','2.40.3+ds1-0ubuntu1'} and old['config']['Labels'].get('com.docker.compose.version')=='2.40.3','compose')
    image=document(backend.command([*DOCKER,'image','inspect','--format','{{json .}}',IMAGE]))
    backend.full_parity=lambda render,row:full_parity(render,row,image)
    old_render=backend.render();backend.full_parity(old_render,old)
    volume=next(r for r in old_render['services'][SERVICE]['volumes'] if r.get('target')==DEST)
    new_volume={**volume,'source':control['new_source']['path']}
    # Public code path/mount declaration only, never rendered environment.
    override={'services':{SERVICE:{'volumes':[new_volume]}}}
    new_render=backend.render(new=True,public_override=override)
    rendered_delta(old_render,new_render,control['old_source']['path'],control['new_source']['path']);backend.check()
    if not apply:return result(control,pin,'preflight_passed_not_applied')
    need(backend.clock.end-time.monotonic()>360,'window')
    guard=OwnedGuard(WEB_OWNER,control['nonce'],pin,stack);backend.checks.append(guard.check)
    backend.journal=Journal(root,control['nonce'],pin);backend.journal.add('prepared',full_config_parity=True)
    write_new(backend.override,override)
    need(same(backend.render(new=True),new_render),'compose');backend.check()
    try:
        new=replacement(backend,baseline,old_render,new_render)
        try:record=backend.observe(new['id'])
        except BaseException:record=None  # A closed observation can remain pending; it is not a functional-fix gate.
        value=result(control,pin,'diagnostic_active_incident_open',cid=new['id'],record=record)
        backend.journal.add('diagnostic_active_incident_open',container_id=new['id'],diagnostic=record)
    except Unknown:
        backend.journal.add('outcome_unknown');raise
    except BaseException:
        if not backend.requested:
            value=result(control,pin,'refused_before_stop');backend.journal.add('refused_before_stop')
        else:
            try:
                restored=rollback(backend,baseline,old_render);value=result(control,pin,'rolled_back_incident_open',cid=restored['id'])
                backend.journal.add('rolled_back_incident_open',container_id=restored['id'])
            except BaseException:
                backend.journal.add('outcome_unknown');raise Unknown() from None
    backend.check();terminal=write_new(root/'terminal.json',value);backend.journal.add('terminal_durable')
    guard.close_known();backend.journal.add('guard_closed')
    ack={**value,'terminal_sha256':terminal,'web_guard_closed':True};write_new(root/'ack.json',ack);return ack

def load(args,stack):
    need(os.geteuid()==0,'root')
    need(type(args.nonce) is str and re.fullmatch('[a-f0-9]{32}',args.nonce)
        and type(args.control_sha256) is str and re.fullmatch('[a-f0-9]{64}',args.control_sha256),'control')
    root=ROOTS[args.site]/args.nonce;canonical(root)
    need(root.lstat().st_uid==0 and stat.S_IMODE(root.lstat().st_mode)==0o700 and re.fullmatch('[a-f0-9]{32}',args.nonce),'root')
    need(not any(os.path.lexists(root/name) for name in ('01-prepared.json','terminal.json','ack.json')),'owner_exists')
    raw,check=public_file(stack,root/'control.json',args.control_sha256,mode=0o600);control=validate_control(document(raw),time.time())
    need(control['nonce']==args.nonce,'control');checks=[check]
    _,check=public_file(stack,Path(__file__).resolve(),control['worker_sha256']);checks.append(check)
    with Path('/etc/machine-id').open('rb') as source:machine=source.read(65).strip().decode()
    need(machine==control[args.site+'_machine_id'],'root')
    if args.site=='web':
        unit='capture-conflict-web-'+control['nonce']+'.scope'
        with Path('/proc/self/cgroup').open('rb') as source:group=source.read(65537)
        need(0<len(group)<=65536 and group.startswith(b'0::/') and group.decode().strip().endswith('/'+unit),'root')
        raw=bounded(['/usr/bin/systemctl','show',unit,'--property=RuntimeMaxUSec,TimeoutStopUSec,KillMode,SendSIGKILL'],Clock(control),maximum=4096)
        fields=dict(line.split('=',1) for line in raw.decode().splitlines())
        need(set(fields)=={'RuntimeMaxUSec','TimeoutStopUSec','KillMode','SendSIGKILL'}
            and fields['RuntimeMaxUSec'] in {'15min','15min 0s','900s'} and fields['TimeoutStopUSec']=='5s'
            and fields['KillMode']=='control-group' and fields['SendSIGKILL']=='yes','root')
    for name,path in (('marker',MARKER),*([('source',SOURCE)] if args.site=='native' else [])):
        fence=MetadataFence(path,control['fences'][args.site][name],stack);checks.append(fence.check)
    if args.site=='web':
        for row in [*control['compose']['files'],control['old_source'],control['new_source'],control['foundation_source']]:
            _,check=public_file(stack,Path(row['path']),row['sha256'],maximum=256*1024,mode=0o644 if row in [control['old_source'],control['new_source'],control['foundation_source']] else None);checks.append(check)
        checks.append(private_metadata(stack,Path(control['compose']['env_reference'])))
    return root,control,checks

def peer_result(value,control,pin):
    base={'schema','nonce','control_sha256','status','capture_issue_open','security_accepted','functional_fix_claimed','retry_allowed'}
    extra={'container_id','diagnostic','terminal_sha256','web_guard_closed'}
    need(type(value) is dict and base<=set(value) and not set(value)-base-extra and value['schema']=='capture_conflict_outcome/1'
        and value['nonce']==control['nonce'] and value['control_sha256']==pin and value['capture_issue_open'] is True
        and value['security_accepted'] is False and value['functional_fix_claimed'] is False and value['retry_allowed'] is False
        and type(value['status']) is str and value['status'] in {'preflight_passed_not_applied','diagnostic_active_incident_open','rolled_back_incident_open','refused_before_stop'},'peer')
    status=value['status'];expected=set(base)
    if status!='preflight_passed_not_applied':expected|={'terminal_sha256','web_guard_closed'}
    if status in {'diagnostic_active_incident_open','rolled_back_incident_open'}:expected.add('container_id')
    if 'diagnostic' in value:
        need(status=='diagnostic_active_incident_open','peer');expected.add('diagnostic')
    need(set(value)==expected,'peer')
    if 'container_id' in value:need(type(value['container_id']) is str and re.fullmatch('[a-f0-9]{64}',value['container_id']),'peer')
    if 'diagnostic' in value and value['diagnostic'] is not None:need(diagnostic(value['diagnostic']) is not None,'peer')
    if status!='preflight_passed_not_applied':
        need(value.get('web_guard_closed') is True and type(value.get('terminal_sha256')) is str
            and re.fullmatch('[a-f0-9]{64}',value['terminal_sha256']),'peer')
        terminal={k:v for k,v in value.items() if k not in {'terminal_sha256','web_guard_closed'}}
        need(sha256(encode(terminal)).hexdigest()==value['terminal_sha256'],'peer')
    return value

def native_run(args,root,control,checks,stack):
    apply=args.apply==CONFIRM;guard=None;journal=None
    need(not os.path.lexists(RESERVATION),'owner_exists')
    clock=Clock(control)
    if apply:need(clock.end-time.monotonic()>360,'window')
    if apply:
        guard=OwnedGuard(RESERVATION,control['nonce'],args.control_sha256,stack);checks.append(guard.check)
        journal=Journal(root,control['nonce'],args.control_sha256);journal.add('prepared')
    for check in checks:check()
    remote=ROOTS['web']/args.nonce/'capture_conflict_diagnostic.py'
    bootstrap='''from hashlib import sha256
import os,pathlib,stat,sys
p=pathlib.Path(sys.argv[1]);pin=sys.argv[2]
identity=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
assert p.is_absolute() and p.resolve(strict=True)==p
fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
try:
 s=os.fstat(fd);assert stat.S_ISREG(s.st_mode) and s.st_uid==0 and s.st_nlink==1 and not s.st_mode&0o022 and 0<s.st_size<=131072
 before=identity(s);b=os.pread(fd,s.st_size+1,0);assert len(b)==s.st_size and sha256(b).hexdigest()==pin
 assert before==identity(os.fstat(fd))==identity(p.lstat())
 sys.argv=sys.argv[1:2]+sys.argv[3:]
 try:exec(compile(b,str(p),'exec'),{'__name__':'__main__','__file__':str(p)})
 finally:assert before==identity(os.fstat(fd))==identity(p.lstat()) and os.pread(fd,len(b)+1,0)==b
finally:os.close(fd)
'''
    argv=['/usr/bin/python3','-I','-B','-c',bootstrap,str(remote),control['worker_sha256'],'--site','web','--nonce',args.nonce,
        '--control-sha256',args.control_sha256,'--coordinator-request',args.control_sha256]
    if apply:argv+=['--apply',CONFIRM]
    if journal:journal.add('peer_requested')
    scope=['/usr/bin/systemd-run','--scope','--quiet','--collect','--unit=capture-conflict-web-'+args.nonce,
        '--property=RuntimeMaxSec=900','--property=TimeoutStopSec=5','--property=KillMode=control-group',
        '--property=SendSIGKILL=yes','--property=MemoryMax=1G','--property=MemorySwapMax=0','--property=TasksMax=64','--property=CPUQuota=100%']
    try:
        value=peer_result(document(bounded([*SSH,shlex.join([*scope,*argv])],clock,allowed_codes=(0,3),seconds=900)),control,args.control_sha256)
        need(apply==(value['status']!='preflight_passed_not_applied'),'peer')
        for check in checks:check()
        if journal:
            write_new(root/'terminal.json',value);journal.add('terminal_durable');guard.close_known();journal.add('guard_closed')
        return value
    except BaseException:
        if journal:journal.add('outcome_unknown')
        raise Unknown() from None

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--site',choices=('native','web'),required=True)
    parser.add_argument('--nonce',required=True);parser.add_argument('--control-sha256',required=True);parser.add_argument('--apply')
    parser.add_argument('--coordinator-request');parser.error=lambda _:need(False,'control')
    try:
        args=parser.parse_args(argv);need(args.apply in {None,CONFIRM},'control')
        need(args.site!='web' or args.coordinator_request==args.control_sha256,'peer')
        with ExitStack() as stack:
            root,control,checks=load(args,stack)
            value=native_run(args,root,control,checks,stack) if args.site=='native' else web_run(control,args.control_sha256,root,stack,checks,args.apply==CONFIRM)
        print(encode(value).decode(),end='');return 3 if args.apply else 0
    except BaseException as error:
        code=error.args[0] if isinstance(error,RuntimeError) and len(error.args)==1 and type(error.args[0]) is str and error.args[0] in CODES else 'unknown'
        print(encode({'schema':'capture_conflict_refusal/1','status':'unknown_or_refused','code':code,'retry_allowed':False,
            'capture_issue_open':True,'security_accepted':False,'functional_fix_claimed':False}).decode(),end='');return 1

if __name__=='__main__':raise SystemExit(main())
