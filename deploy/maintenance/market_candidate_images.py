"""Fixed ten-image Market store admission only; never serving or data migration.

The same sealed source runs in a finite scope on either site. Native coordination
holds the existing empty SOURCE inode. Unknown RPCs retain durable ownership.
"""
from contextlib import ExitStack
from hashlib import sha256
from pathlib import Path
from types import ModuleType
import argparse
import errno
import fcntl
import gzip
import io
import json
import os
import re
import resource
import select
import signal
import stat
import struct
import subprocess
import sys
import time
import zipfile

OPERATION = 'security-market-candidate-images-20261025-04'
CATALOG_SHA = 'c354498c3659fdf99613753d950416f134d81e0fe61fbdc80a32e7eec29b8d12'
TOOLS = {'bot': Path('/root/trading-bot/trading_bot/.local')/OPERATION,
         'web': Path('/srv/trading-bot')/OPERATION}
MARKER = Path('/root/secure-envs/trading-bot/queue-cutover-artifacts/production-release.lock')
SOURCE = Path('/root/secure-envs/trading-bot/.production-runtime-source.lock')
RESERVATION = Path('/root/secure-envs/trading-bot/release-control/private-primary-controller.lock')
PENDING = Path('/var/lib/trading-bot/production-release/two-host-release-state.json')
DOCKER = ['/usr/bin/docker', '--host=unix:///var/run/docker.sock']
SSH = ['/usr/bin/ssh','-F','/dev/null','-p','37067','-oBatchMode=yes',
       '-oIdentitiesOnly=yes','-oStrictHostKeyChecking=yes',
       '-i','/root/.ssh/id_ed25519_iran','-oControlMaster=no','-S','none','-oUpdateHostKeys=no',
       '-oUserKnownHostsFile=/root/.ssh/known_hosts','-oForwardAgent=no',
       '-oClearAllForwardings=yes','-oConnectTimeout=15','root@65.109.220.59']
LIMIT = 2*1024**2
MARGIN = 64*1024**2
CONFIRM = 'MARKET-IMAGE-STORES-ONLY-20261025-04'
PHASES = ('receive-web','load-web','load-native')
CAPTURE = '6a16c8a1b4cdab3b4bcd2a0d4d4998ced7a455cc6c364255e5e0e3dacd9c2752'
SUBPHASE = 'control_admission'


def need(ok, code):
    if not ok: raise RuntimeError(code)


def encode(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)+'\n').encode()


def document(raw):
    need(type(raw) is bytes and 0 < len(raw) <= LIMIT, 'document_bound')
    def unique(rows):
        out = {}
        for key, value in rows:
            need(key not in out, 'duplicate_key'); out[key] = value
        return out
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: need(False, 'nonfinite_json'))


def identity(s):
    return [s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,
            s.st_size,s.st_mtime_ns,s.st_ctime_ns]


def bounds(maximum=LIMIT):
    resource.setrlimit(resource.RLIMIT_CORE, (0,0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (maximum+1,maximum+1))
    # Docker/SSH/gh are Go clients: no inherited RLIMIT_AS.


def public(value):
    if type(value) is dict:
        need(not {'Config','Env','environment','runtime_config','access_token'} & set(value), 'private_output')
        for child in value.values(): public(child)
    elif type(value) is list:
        for child in value: public(child)


def parents(path):
    rows=[]
    for parent in Path(path).parents:
        s=parent.lstat()
        protected=not s.st_mode&0o022 or parent==Path('/tmp') and stat.S_IMODE(s.st_mode)==0o1777
        need(parent.resolve(strict=True)==parent and stat.S_ISDIR(s.st_mode)
             and s.st_uid==s.st_gid==0 and protected,'protected_parent')
        rows.append((parent,identity(s)[:5]))
    return rows


def check_parents(rows):
    need(all(parent.resolve(strict=True)==parent and identity(parent.lstat())[:5]==before
             for parent,before in rows),'protected_parent_changed')


def seal(stack, path, pin, maximum=131072):
    path = Path(path)
    need(path.is_absolute() and path.resolve(strict=True)==path, 'source_path')
    ancestor=parents(path)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK); stack.callback(os.close,fd)
    before=identity(os.fstat(fd))
    need(before[2]==0o100600 and before[3:6]==[0,0,1] and 0<before[6]<=maximum, 'source_metadata')
    raw=os.pread(fd,before[6]+1,0)
    need(len(raw)==before[6] and sha256(raw).hexdigest()==pin, 'source_sha256')
    def check():
        check_parents(ancestor)
        need(before==identity(os.fstat(fd))==identity(path.lstat())
             and sha256(os.pread(fd,before[6]+1,0)).hexdigest()==pin, 'source_changed')
    check(); return raw,check


def scope(site, phase, nonce):
    name='market-images-'+site+'-'+phase+'-'+nonce+'.scope'
    with Path('/proc/self/cgroup').open('rb') as source:raw=source.read(65537)
    need(0<len(raw)<=65536 and raw.startswith(b'0::/')
         and raw.decode().strip().endswith('/'+name), 'finite_scope_required')
    folder=Path('/sys/fs/cgroup')/raw.decode().strip()[4:]
    values={}
    for n in ('memory.max','memory.swap.max','pids.max','cpu.max'):
        with (folder/n).open('rb') as source:value=source.read(1025)
        need(len(value)<=1024,'scope_limits');values[n]=value.decode().strip()
    quota,period=values['cpu.max'].split()
    need(values['memory.max']==str(2*1024**3) and values['memory.swap.max']=='0'
         and values['pids.max']=='64' and quota!='max' and 0<int(quota)<=int(period), 'scope_limits')


class Context:
    def __init__(self, args, stack):
        need(os.geteuid()==0, 'root_required')
        self.args,self.stack,self.site=args,stack,args.site
        self.root=TOOLS[self.site]; self.checks=[]; self.budget=0; self.requested=False
        info=self.root.lstat()
        need(self.root.resolve(strict=True)==self.root and stat.S_ISDIR(info.st_mode)
             and info.st_uid==info.st_gid==0 and stat.S_IMODE(info.st_mode)==0o700,'tools_directory')
        raw,check=seal(stack,self.root/'market_candidate_images.json',CATALOG_SHA)
        self.checks.append(check); self.catalog=document(raw)
        self.modules={}
        for path,pin in self.catalog['helpers'].items():
            name=Path(path).name; raw,check=seal(stack,self.root/'source'/name,pin['sha256'])
            self.checks.append(check); module=ModuleType('_market_images_'+Path(name).stem)
            module.__file__=str(self.root/'source'/name)
            exec(compile(raw,module.__file__,'exec'),module.__dict__); self.modules[name]=module
        self.D=self.modules['datastore_candidate_contract.py']; self.T=self.modules['runtime_image_archive_transport.py']
        self.L=self.modules['admit_security_component_image_reuse.py']; self.M=self.modules['compose_market_security_os_candidate.py']
        raw,check=seal(stack,args.control,args.control_sha256,LIMIT); self.checks.append(check)
        self.op=document(raw); self.pin=args.control_sha256
        need(set(self.op)=={'schema','operation','phase','profile','nonce','not_before','deadline','worker_sha256',
             'baselines','fences','admission','admission_sha256'}, 'control_fields')
        op=self.op
        need(op['schema']=='market_image_store_control/1' and op['operation']==OPERATION
             and op['phase'] in PHASES and re.fullmatch('[a-f0-9]{32}',op['nonce'])
             and args.control==self.root/'controls'/(op['nonce']+'.json'), 'control_scope')
        need(set(op['baselines'])=={'bot','web'} and set(op['fences'])=={'bot','web'},'baseline_sites')
        need(type(op['not_before']) is int and type(op['deadline']) is int
             and op['not_before']<=time.time()<op['deadline']<=op['not_before']+900, 'deadline')
        self.end=time.monotonic()+op['deadline']-time.time()
        raw,check=seal(stack,Path(__file__).resolve(),op['worker_sha256']);self.checks.append(check)
        chosen=[p for p in self.catalog['profiles'] if p['profile']==op['profile']]
        need(len(chosen)==1,'profile_scope');self.spec=chosen[0]
        need(op['phase']=='receive-web' or (op['phase']=='load-web')==(self.spec['site']=='web'), 'target_site')
        need(args.phase==op['phase'] or self.site=='web' and args.phase=='stream-web'
             and op['phase']=='load-native', 'phase_scope')
        self.work=self.root/'attempts'/(op['profile']+'-'+op['phase'])
        self.baseline=op['baselines'][self.site]
        need(type(self.baseline['observed_at']) is int and 0<=time.time()-self.baseline['observed_at']<=300,
             'baseline_freshness')
        scope(self.site,args.phase,op['nonce']);self.check()
        unit='market-images-'+self.site+'-'+args.phase+'-'+op['nonce']+'.scope'
        values=command(self,['/usr/bin/systemctl','show',unit,
            '--property=RuntimeMaxUSec,TimeoutStopUSec,KillMode,SendSIGKILL'],maximum=4096).decode()
        values=dict(line.split('=',1) for line in values.splitlines())
        need(set(values)=={'RuntimeMaxUSec','TimeoutStopUSec','KillMode','SendSIGKILL'}
             and values['RuntimeMaxUSec'] in {'15min','15min 0s','900s'} and values['TimeoutStopUSec']=='5s'
             and values['KillMode']=='control-group' and values['SendSIGKILL']=='yes',
             'scope_lifetime')

    def check(self):
        need(time.monotonic()<self.end,'deadline')
        for check in self.checks:check()

    def publish(self, name, value):
        public(value); raw=encode(value);need(len(raw)<=LIMIT and re.fullmatch(r'[a-z][a-z-]*\.json',name), 'output_bound')
        out=owned_output(self,self.work/name,LIMIT)
        for i in range(0,len(raw),65536):out.write(raw[i:i+65536])
        out.finish();return sha256(raw).hexdigest()

    def capacity(self, additional=0):
        self.check();budget=max(self.budget,additional)
        need(type(budget) is int and budget>=0,'capacity_budget')
        if 'identity' in self.baseline['store']:
            need(identity(Path(self.baseline['store']['root']).lstat())[:5]==self.baseline['store']['identity'],'store_root_changed')
        temporary=temp_metadata()
        need(temporary==self.baseline['temporary_storage'],'temporary_storage_changed')
        for path,floor in ((self.baseline['store']['root'],8*1024**3),('/',2*1024**3),('/tmp',2*1024**3)):
            fs=os.statvfs(path);need(fs.f_bavail*fs.f_frsize>=floor+MARGIN+LIMIT+budget,'capacity_floor')
        if temporary[0]['identity'][0]!=temporary[1]['identity'][0]:
            need(memory_available()>=2*1024**3+MARGIN+LIMIT+budget,'temporary_memory_floor')
        for row in self.baseline.get('daemon',{}).get('files',[]):
            need(identity(Path(row['path']).lstat())==row['identity'],'daemon_unit_metadata_changed')

    def begin(self):
        global SUBPHASE
        SUBPHASE='attempt_mkdir'
        need(not self.work.exists(),'previous_attempt_requires_reconciliation')
        self.work.mkdir(mode=0o700)
        SUBPHASE='attempt_intent'
        self.publish('intent.json',{'schema':'market_image_store_intent/1',
            'profile':self.spec['profile'],'phase':self.op['phase'],'operation_sha256':self.pin,
            'retry_allowed':False,'security_accepted':False})


def temp_metadata():
    rows=[]
    for p in (Path('/'),Path('/tmp')):
        s=p.lstat();need(p.resolve(strict=True)==p and stat.S_ISDIR(s.st_mode) and s.st_uid==s.st_gid==0,
                         'temporary_storage_metadata')
        need(stat.S_IMODE(s.st_mode)==0o1777 if p==Path('/tmp') else not s.st_mode&0o022,'temporary_storage_mode')
        rows.append({'path':str(p),'identity':identity(s)[:5]})
    return rows


def memory_available():
    """Conservative host RAM headroom when temporary storage is a distinct device."""
    with Path('/proc/meminfo').open('rb') as source:raw=source.read(65537)
    need(0<len(raw)<=65536,'temporary_memory_metadata')
    rows=[line for line in raw.splitlines() if line.startswith(b'MemAvailable:')]
    need(len(rows)==1,'temporary_memory_metadata')
    value=re.fullmatch(rb'MemAvailable:[ \t]+([0-9]{1,20})[ \t]+kB',rows[0])
    need(value is not None,'temporary_memory_metadata')
    return int(value[1])*1024


def stop(child):
    if child.poll() is None:child.kill()
    child.wait(timeout=10)


def command(ctx, argv, *, maximum=LIMIT, stdin=subprocess.DEVNULL):
    need(type(maximum) is int and 0<maximum<=LIMIT,'command_bound')
    ctx.capacity();fd=os.memfd_create('market-public-output',os.MFD_CLOEXEC)
    child=None
    try:
        child=subprocess.Popen(argv,stdin=stdin,stdout=fd,stderr=subprocess.DEVNULL,preexec_fn=lambda:bounds(maximum))
        while child.poll() is None:
            ctx.capacity();need(os.fstat(fd).st_size<=maximum,'command_output_bound');time.sleep(.05)
        need(child.wait(timeout=1)==0 and os.fstat(fd).st_size<=maximum,'command_outcome_unknown')
        return os.pread(fd,maximum+1,0)
    finally:
        try:
            if child is not None:stop(child)
        finally:os.close(fd)


def snapshot(ctx):
    global SUBPHASE
    SUBPHASE='snapshot_container_inventory'
    ids=command(ctx,[*DOCKER,'ps','-aq','--no-trunc'],maximum=65536).decode().splitlines()
    need(len(ids)==(15 if ctx.site=='bot' else 27) and len(set(ids))==len(ids)
         and all(re.fullmatch('[a-f0-9]{64}',i) for i in ids),'container_inventory')
    fmt='{"id":{{json .Id}},"image":{{json .Image}},"name":{{json .Name}},"host":{{json .HostConfig}},"mounts":{{json .Mounts}},"networks":{{json .NetworkSettings.Networks}},"running":{{json .State.Running}},"restarting":{{json .State.Restarting}},"pid":{{json .State.Pid}},"started":{{json .State.StartedAt}},"restarts":{{json .RestartCount}},"health":{{with index .State "Health"}}{{json .Status}}{{else}}null{{end}},"execution":{"entry":{{json .Config.Entrypoint}},"cmd":{{json .Config.Cmd}},"user":{{json .Config.User}},"workdir":{{json .Config.WorkingDir}},"labels":{{json .Config.Labels}},"healthcheck":{{with index .Config "Healthcheck"}}{{json .}}{{else}}null{{end}}}}'
    SUBPHASE='snapshot_container_contracts'
    rows=[document(r) for r in command(ctx,[*DOCKER,'inspect','--format',fmt,*ids]).splitlines()]
    for row in rows:row['host']=host_projection(row['host'])
    need(len(rows)==len(ids) and {r['id'] for r in rows}==set(ids),'container_inventory')
    if ctx.site=='web':
        capture=[r for r in rows if r['id']==CAPTURE]
        need(len(capture)==1 and capture[0]['running'] is True and capture[0]['restarting'] is False
             and capture[0]['health']=='healthy' and type(capture[0]['pid']) is int and capture[0]['pid']>0,'capture_singleton')
    SUBPHASE='snapshot_image_inventory'
    raw=command(ctx,[*DOCKER,'image','ls','--all','--no-trunc','--format','{"id":{{json .ID}},"repository":{{json .Repository}},"tag":{{json .Tag}}}'])
    image_map=sorted([document(r) for r in raw.splitlines()],key=lambda r:(r['id'],r['repository'],r['tag']))
    return {'containers':sorted(rows,key=lambda r:r['id']),'image_map':image_map}


def host_projection(value):
    """Keep typed logging policy; disambiguate its options from image Config."""
    need(type(value) is dict,'host_policy_shape')
    log=value.get('LogConfig')
    if log is None:return value
    need(type(log) is dict and 'Config' in log and 'options' not in log,'log_policy_shape')
    return {**value,'LogConfig':{**{k:v for k,v in log.items() if k!='Config'},'options':log['Config']}}


def stable(ctx):
    now=snapshot(ctx)
    need(ctx.D.same(now['containers'],ctx.baseline['containers']),'runtime_changed')
    return now


def daemon_policy(ctx):
    global SUBPHASE
    SUBPHASE='snapshot_daemon_unit_metadata'
    raw=command(ctx,['/usr/bin/systemctl','show','docker.service',
        '--property=FragmentPath,DropInPaths,NeedDaemonReload,PrivateTmp,RootDirectory,RootImage,TemporaryFileSystem'],maximum=8192)
    fields=dict(line.split('=',1) for line in raw.decode().splitlines())
    need(set(fields)=={'FragmentPath','DropInPaths','NeedDaemonReload','PrivateTmp','RootDirectory','RootImage','TemporaryFileSystem'}
         and fields['NeedDaemonReload']=='no' and fields['FragmentPath'] and not fields['RootDirectory']
         and not fields['RootImage'] and not fields['TemporaryFileSystem'],'daemon_temporary_storage_contract')
    files=[]
    for name in [fields['FragmentPath'],*fields['DropInPaths'].split()]:
        path=Path(name).resolve(strict=True);s=path.lstat()
        need(stat.S_ISREG(s.st_mode) and s.st_uid==s.st_gid==0 and s.st_nlink==1
             and not s.st_mode&0o022,'daemon_public_file_metadata')
        files.append({'path':str(path),'identity':identity(s)})
    return {'properties':fields,'files':files,'runtime_override_absence_proven':False}


def store_view(ctx):
    global SUBPHASE
    SUBPHASE='snapshot_store_info'
    store=document(command(ctx,[*DOCKER,'info','--format',
        '{"root":{{json .DockerRootDir}},"driver":{{json .Driver}},"version":{{json .ServerVersion}}}'],maximum=2048))
    path=Path(store['root']);need(path.is_absolute() and path.resolve(strict=True)==path,'store_root')
    store['identity']=identity(path.lstat())[:5]
    SUBPHASE='snapshot_store_mount'
    store['mount']=document(command(ctx,['/usr/bin/findmnt','--json','--target',str(path),
        '--output','SOURCE,UUID,FSTYPE,MAJ:MIN'],maximum=8192))
    return store


def collect(site,source_pin):
    """Read-only fresh observation; no locks, workspace, Config/Env or hashes of them."""
    global SUBPHASE
    SUBPHASE='snapshot_source_admission'
    need(os.geteuid()==0,'root_required')
    with ExitStack() as stack:
        _,check=seal(stack,Path(__file__).resolve(),source_pin)
        class Observer:
            def check(self):
                need(time.monotonic()<self.end,'deadline');check()
            capacity=check
        ctx=Observer();ctx.site=site;ctx.end=time.monotonic()+60
        store=store_view(ctx)
        machine=Path('/etc/machine-id')
        with machine.open('rb') as f:host=f.read(65).strip().decode()
        need(re.fullmatch('[a-f0-9]{32}',host),'host_identity')
        before=snapshot(ctx);daemon=daemon_policy(ctx);temporary=temp_metadata();after=snapshot(ctx)
        need(before==after,'snapshot_runtime_unstable')
        return {**after,'observed_at':int(time.time()),'site':site,'machine_id':host,
            'store':store,'daemon':daemon,'temporary_storage':temporary,
            'controller_sha256':source_pin,'security_accepted':False}


def owned_output(ctx,path,maximum):
    out=ctx.D.Output(path,maximum);ctx.stack.callback(out.close);ctx.checks.append(out.check)
    return out


def remote(ctx, phase):
    root=TOOLS['web'];unit='market-images-web-'+phase+'-'+ctx.op['nonce']
    argv=[*SSH,'/usr/bin/systemd-run','--scope','--quiet','--collect','--unit='+unit,
          '--property=CPUQuota=100%','--property=MemoryMax=2G','--property=MemorySwapMax=0',
          '--property=TasksMax=64','--property=RuntimeMaxSec=900','--property=TimeoutStopSec=5',
          '/usr/bin/python3','-I','-B',str(root/'market_candidate_images.py'),'--site','web',
          '--phase',phase,'--control',str(root/'controls'/(ctx.op['nonce']+'.json')),
          '--control-sha256',ctx.pin,'--apply',CONFIRM]
    return argv


def acquire_lock(ctx, fd, check):
    """Only genuine nonblocking contention may wait; never infer cancellation."""
    end=min(ctx.end,time.monotonic()+30)
    while True:
        need(time.monotonic()<end,'fence_lock_timeout')
        ctx.capacity();check()
        need(time.monotonic()<end,'fence_lock_timeout')
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as error:
            if error.errno not in {errno.EAGAIN,errno.EWOULDBLOCK}:raise
            remaining=end-time.monotonic()
            need(remaining>0,'fence_lock_timeout')
            time.sleep(min(.1,remaining))
        else:check();return


class Guard:
    def __init__(self,ctx):
        global SUBPHASE
        self.ctx=ctx;self.held=[];self.reservation=None;self.ancestors=[]
        need(not os.path.lexists(PENDING),'pending_release_requires_reconciliation')
        paths=[('marker',MARKER,ctx.op['fences'][ctx.site]['marker'])]
        if ctx.site=='bot':paths.append(('source',SOURCE,ctx.op['fences']['bot']['source']))
        for name,path,expected in paths:
            SUBPHASE='guard_'+name+'_admission'
            need(path.resolve(strict=True)==path,'permanent_fence_path')
            self.ancestors.extend(parents(path))
            need(type(expected) is list and len(expected)==9 and all(type(v) is int for v in expected),'permanent_fence_shape')
            SUBPHASE='guard_'+name+'_open'
            fd=os.open(path,os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK);ctx.stack.callback(os.close,fd)
            actual=identity(os.fstat(fd))
            need(actual==expected==identity(path.lstat()) and actual[2]==0o100600
                 and actual[3:6]==[0,0,1] and (path!=SOURCE or actual[6]==0),'permanent_fence_identity')
            def pending():
                self.check()
                need(identity(os.fstat(fd))==actual==identity(path.lstat()),'permanent_fence_changed')
            SUBPHASE='guard_'+name+'_flock'
            acquire_lock(ctx,fd,pending);self.held.append((path,fd,actual))
        self.path=RESERVATION if ctx.site=='bot' else ctx.root/'image-store.guard'
        SUBPHASE='guard_reservation_admission'
        self.ancestors.extend(parents(self.path))
        SUBPHASE='guard_reservation_create'
        out=ctx.D.Output(self.path,LIMIT);ctx.stack.callback(out.close)
        def pending_reservation():self.check();out.check()
        SUBPHASE='guard_reservation_flock'
        acquire_lock(ctx,out.fd,pending_reservation)
        self.payload=encode({'schema':'market_image_store_owner/1','nonce':ctx.op['nonce'],
            'profile':ctx.spec['profile'],'operation_sha256':ctx.pin,'pid':os.getpid()})
        SUBPHASE='guard_reservation_write'
        out.write(self.payload);out.finish();self.reservation=out
        SUBPHASE='guard_reservation_check'
        self.check();ctx.checks.append(self.check)

    def check(self):
        check_parents(self.ancestors)
        for path,fd,expected in self.held:
            need(identity(os.fstat(fd))==expected==identity(path.lstat()),'permanent_fence_changed')
        if self.reservation is not None:
            self.reservation.check()
            need(os.pread(self.reservation.fd,len(self.payload)+1,0)==self.payload,'owned_fence_changed')

    def finish(self):
        self.check();out=self.reservation;os.unlink(self.path)
        parent=os.open(self.path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(parent)
        finally:os.close(parent)
        need(os.fstat(out.fd).st_nlink==0 and not os.path.lexists(self.path),'fence_unlink_unproven')
        self.reservation=None;self.check()


def selected_evidence(ctx):
    spec=ctx.spec;row=spec['evidence_artifact'];path=ctx.root/'evidence'/(spec['profile']+'.zip')
    raw,check=seal(ctx.stack,path,row['digest'][7:],1048576);ctx.checks.append(check)
    need(len(raw)==row['size_in_bytes'],'evidence_size')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos=archive.infolist()
        need(len(infos)<=64 and len({r.filename for r in infos})==len(infos),'evidence_inventory')
        def item(name,size,pin):
            row=archive.getinfo(name);need(0<row.file_size==size<=LIMIT,'evidence_member_bound')
            value=archive.read(row);need(sha256(value).hexdigest()==pin,'evidence_member_pin');return document(value)
        exported=item('image-export.json',spec['public_export_bytes'],spec['public_export_sha256'])
        raw_report=archive.getinfo('reports/vulnerabilities.json')
        report=item(raw_report.filename,raw_report.file_size,spec['public_raw_report_sha256'])
    need(exported['image_id']==spec['candidate_config_id'] and exported['archive']==spec['archive']
         and exported['security_accepted'] is False and exported['deployment_eligible'] is False,'export_identity')
    image=report['Metadata']['ImageConfig']
    need(report['Metadata']['ImageID']==spec['candidate_config_id']
         and image['rootfs']['diff_ids']==spec['candidate_rootfs'],'evidence_config_identity')
    return {'spec':{'archive':spec['archive'],'config_id':spec['candidate_config_id'],
        'layers':spec['candidate_rootfs'],'profile':spec['profile']},
        'image':{'Config':image['config'],'Comment':ctx.D.expected_inspect_comment(image),
                 'Author':image.get('author',''),'DockerVersion':image.get('docker_version','')}}


def validate_zip(ctx,fd,size,tar):
    """Decode one exact public artifact; no general extraction or helper bypass."""
    spec=ctx.spec;expected=spec['image_artifact']
    need(size==expected['size_in_bytes'] and ctx.D.digest_fd(fd,size,ctx.end)==expected['digest'][7:],'image_zip_identity')
    stream=os.fdopen(os.dup(fd),'rb');ctx.stack.callback(stream.close);stream.seek(0)
    with zipfile.ZipFile(stream) as archive:
        infos=archive.infolist()
        need(len(infos)==2 and {i.filename for i in infos}=={'image-export.json','candidate-image.tar.gz'}
             and all(not i.is_dir() and not i.flag_bits&1 and stat.S_IFMT(i.external_attr>>16) in {0,stat.S_IFREG} for i in infos),'image_zip_inventory')
        row=archive.getinfo('image-export.json');need(row.file_size==spec['public_export_bytes']<=65536,'export_bound')
        need(sha256(archive.read(row)).hexdigest()==spec['public_export_sha256'],'export_pin')
        row=archive.getinfo('candidate-image.tar.gz')
        need(row.file_size==spec['archive']['size_bytes'],'gzip_bound')
        zipped=owned_output(ctx,ctx.work/'source.gz',row.file_size)
        digest=sha256()
        with archive.open(row) as source:
            while block:=source.read(min(65536,row.file_size+1-zipped.before[6])):
                ctx.capacity();digest.update(block);zipped.write(block)
        zipped.finish();need(zipped.before[6]==row.file_size and digest.hexdigest()==spec['archive']['sha256'],'gzip_identity')
    output=owned_output(ctx,tar,spec['archive']['uncompressed_bytes'])
    source=ctx.D.CompressedReader(zipped.fd,zipped.before[6],ctx.end)
    with gzip.GzipFile(fileobj=source,mode='rb') as decoded:
        while block:=decoded.read(min(65536,output.maximum+1-output.before[6])):
            ctx.capacity();output.write(block)
    output.finish();need(output.before[6]==output.maximum,'tar_size')
    return output


def source_archive(ctx):
    need(ctx.op['admission']==ctx.spec['profile']+'-admission.json','admission_path')
    raw,check=seal(ctx.stack,ctx.root/'controls'/ctx.op['admission'],ctx.op['admission_sha256'],LIMIT)
    ctx.checks.append(check);value=document(raw)
    need(value['schema']=='market_candidate_archive/1' and value['profile']==ctx.spec['profile']
         and value['candidate_config_id']==ctx.spec['candidate_config_id'] and value['security_accepted'] is False,'source_admission')
    need(value['archive_bytes']==ctx.spec['archive']['uncompressed_bytes']
         and ctx.D.same(value['rootfs'],ctx.spec['candidate_rootfs'])
         and type(value['expanded_layer_bytes']) is int and 0<value['expanded_layer_bytes']<=ctx.T.EXPANDED_BYTES
         and type(value['prospective_import_bytes']) is int
         and value['prospective_import_bytes']==2*value['archive_bytes']+2*value['expanded_layer_bytes']
         and re.fullmatch('[a-f0-9]{64}',value['archive_sha256'])
         and ctx.spec['candidate_config_id'] in value['source_identities'],'source_admission_budget')
    if ctx.spec.get('tar_sha256'):need(value['archive_sha256']==ctx.spec['tar_sha256'],'source_admission_sha256')
    return value


def admitted_tar(ctx):
    admitted=source_archive(ctx);path=ctx.root/'attempts'/(ctx.spec['profile']+'-receive-web')/'source.tar'
    fd,size,check=ctx.stack.enter_context(ctx.D.held(path,admitted['archive_bytes']))
    ctx.checks.append(check)
    need(identity(os.fstat(fd))==admitted['tar_identity'] and size==admitted['archive_bytes']
         and ctx.D.digest_fd(fd,size,ctx.end)==admitted['archive_sha256'],
         'admitted_source_changed')
    ctx.budget=admitted['prospective_import_bytes'];ctx.capacity()
    return fd,admitted


def receive_web(ctx):
    global SUBPHASE
    SUBPHASE='archive_receive'
    need(ctx.site=='web','site_scope')
    ctx.budget=ctx.spec['image_artifact']['size_in_bytes']+ctx.spec['archive']['size_bytes']+ctx.spec['archive']['uncompressed_bytes']
    ctx.capacity()
    output=owned_output(ctx,ctx.work/'source.zip',ctx.spec['image_artifact']['size_in_bytes'])
    digest=sha256()
    while output.before[6]<output.maximum:
        block=read_part(ctx,0,min(65536,output.maximum-output.before[6]));need(block,'source_short')
        output.write(block);digest.update(block)
    need(not read_part(ctx,0,1),'source_extra');output.finish()
    SUBPHASE='archive_unpack'
    tar=validate_zip(ctx,output.fd,output.before[6],ctx.work/'source.tar');data=selected_evidence(ctx)
    stream=os.fdopen(os.dup(tar.fd),'rb');ctx.stack.callback(stream.close);stream.seek(0)
    SUBPHASE='archive_graph_validation'
    proof=ctx.D.validate_archive(stream,tar.before[6],ctx.end,data,ctx.T,ctx.L)
    need(proof['physical_graph_closed'] is True and proof['archive_bytes']==ctx.spec['archive']['uncompressed_bytes']
         and proof['rootfs_layers']==ctx.spec['candidate_rootfs'],'archive_identity')
    if ctx.spec.get('tar_sha256'):need(proof['archive_sha256']==ctx.spec['tar_sha256'],'archive_sha256')
    budget=2*proof['archive_bytes']+2*proof['expanded_layer_bytes'];ctx.budget=budget;ctx.capacity()
    before=stable(ctx);need(before['image_map']==ctx.baseline['image_map'],'image_map_changed')
    receipt={'schema':'market_candidate_archive/1','profile':ctx.spec['profile'],
        'candidate_config_id':ctx.spec['candidate_config_id'],'archive_bytes':proof['archive_bytes'],
        'archive_sha256':proof['archive_sha256'],'tar_identity':list(tar.before),
        'rootfs':proof['rootfs_layers'],'source_identities':proof['source_identities'],
        'expanded_layer_bytes':proof['expanded_layer_bytes'],
        'prospective_import_bytes':budget,'operation_sha256':ctx.pin,
        'configuration_values_disclosed':False,'security_accepted':False}
    ctx.check();return {'status':'archive_admitted_not_loaded','receipt_sha256':ctx.publish('admission.json',receipt),'security_accepted':False}


def read_part(ctx,fd,count):
    need(type(count) is int and 0<count<=65536,'read_bound')
    while True:
        ctx.capacity()
        if select.select([fd],[],[],.1)[0]:return os.read(fd,count)


def write_all(ctx,fd,value):
    view=memoryview(value)
    while view:
        ctx.capacity()
        if select.select([],[fd],[],.1)[1]:
            try:count=os.write(fd,view[:65536])
            except BlockingIOError:continue
            need(count>0,'write_short');view=view[count:]


def frame(ctx,fd,value):
    raw=encode(value);need(len(raw)<=65536,'frame_bound');write_all(ctx,fd,struct.pack('!I',len(raw))+raw)


def read_frame(ctx,fd):
    def exact(count):
        value=bytearray()
        while len(value)<count:
            block=read_part(ctx,fd,min(65536,count-len(value)));need(block,'frame_short');value.extend(block)
        return bytes(value)
    length=struct.unpack('!I',exact(4))[0];need(0<length<=65536,'frame_bound')
    return document(exact(length))


def image(ctx,ident,admission):
    identities=admission['source_identities'];need(ident in identities,'unknown_daemon_identity')
    value=document(command(ctx,[*DOCKER,'image','inspect','--format',
        '{"id":{{json .Id}},"os":{{json .Os}},"architecture":{{json .Architecture}},"rootfs":{{json .RootFS}},"config":{{json .Config}}}',ident]))
    expected=selected_evidence(ctx)['image']['Config']
    need(value['id']==ident and value['os']=='linux' and value['architecture']=='amd64'
         and ctx.D.same(value['rootfs'],{'Type':'layers','Layers':ctx.spec['candidate_rootfs']})
         and ctx.D.same(ctx.D.container_config(value['config']),ctx.D.container_config(expected)),'daemon_config_or_rootfs')
    if ident!=ctx.spec['candidate_config_id']:
        descriptor=document(command(ctx,[*DOCKER,'image','inspect','--format',
            '{{with index . "Descriptor"}}{{json .}}{{else}}null{{end}}',ident]))
        expected=identities[ident]
        need(type(descriptor) is dict and descriptor.get('digest')==ident
             and descriptor.get('mediaType')==expected['media_type']
             and type(descriptor.get('size')) is int and descriptor['size']==expected['size'],'daemon_descriptor')
    return {'schema':'market_preloaded_image/1','site':'native' if ctx.site=='bot' else 'web',
        'host':'65.109.216.187' if ctx.site=='bot' else '65.109.220.59',
        'host_machine_id':ctx.baseline['machine_id'],'catalog_sha256':CATALOG_SHA,
        'ci_run_id':ctx.spec['ci_run_id'],'image_artifact_sha256':ctx.spec['image_artifact']['digest'][7:],
        'role':ctx.spec['role'],'profile':ctx.spec['profile'],'candidate_config_id':ctx.spec['candidate_config_id'],
        'candidate_local_image_id':ident,'rootfs':ctx.spec['candidate_rootfs'],
        'archive_and_config_association_verified':True,'whole_typed_config_compared_in_ram':True,
        'source_admission_sha256':ctx.op['admission_sha256'],'operation_sha256':ctx.pin,
        'source_archive_sha256':admission['archive_sha256'],'serving_roles_activated':[],
        'configuration_values_disclosed':False,'security_accepted':False}


def measured(ctx,admission):
    after=stable(ctx);before=ctx.baseline['image_map']
    old_ids={r['id'] for r in before};new_ids={r['id'] for r in after['image_map']}-old_ids
    need(len(new_ids)==1 and new_ids<=set(admission['source_identities'])
         and [r for r in after['image_map'] if r['id'] in old_ids]==before,'image_map_delta')
    return image(ctx,next(iter(new_ids)),admission)


def load_web(ctx):
    global SUBPHASE
    SUBPHASE='web_source_admission'
    need(ctx.site=='web' and ctx.spec['site']=='web','site_scope');fd,admitted=admitted_tar(ctx)
    before=stable(ctx);need(before['image_map']==ctx.baseline['image_map']
        and not {r['id'] for r in before['image_map']}&set(admitted['source_identities']),'candidate_preexisting')
    ctx.publish('load-request.json',{'daemon_outcome':'unknown_until_measured','retry_allowed':False,'security_accepted':False})
    SUBPHASE='web_load_request'
    ctx.requested=True;os.lseek(fd,0,os.SEEK_SET);command(ctx,[*DOCKER,'image','load','--quiet'],stdin=fd)
    need(ctx.D.digest_fd(fd,admitted['archive_bytes'],ctx.end)==admitted['archive_sha256'],'post_load_source_sha256')
    SUBPHASE='web_daemon_measurement'
    result=measured(ctx,admitted);ctx.check()
    return {'status':'loaded_measured_not_accepted','receipt_sha256':ctx.publish('loaded.json',result),'security_accepted':False}


def stream_web(ctx):
    global SUBPHASE
    SUBPHASE='web_stream_source'
    need(ctx.site=='web' and ctx.spec['site']=='native','site_scope');fd,admitted=admitted_tar(ctx)
    need(stable(ctx)['image_map']==ctx.baseline['image_map'],'image_map_changed')
    header={'schema':'market_image_stream/1','profile':ctx.spec['profile'],'operation_sha256':ctx.pin,
        'admission_sha256':ctx.op['admission_sha256'],'archive_bytes':admitted['archive_bytes'],
        'archive_sha256':admitted['archive_sha256'],'tar_identity':admitted['tar_identity']}
    frame(ctx,1,header);start=bytearray()
    while len(start)<6:
        block=read_part(ctx,0,6-len(start));need(block,'stream_start');start.extend(block)
    need(bytes(start)==b'START\n','stream_start')
    SUBPHASE='web_stream_body';digest=sha256();offset=0
    while offset<admitted['archive_bytes']:
        ctx.capacity();block=os.pread(fd,min(65536,admitted['archive_bytes']-offset),offset)
        need(block,'source_short');digest.update(block);write_all(ctx,1,block);offset+=len(block)
    need(digest.hexdigest()==admitted['archive_sha256']
         and ctx.D.digest_fd(fd,offset,ctx.end)==admitted['archive_sha256'],'source_stream_changed')
    SUBPHASE='web_stream_final'
    need(stable(ctx)['image_map']==ctx.baseline['image_map'],'image_map_changed');ctx.check()
    frame(ctx,1,{**header,'schema':'market_image_stream_complete/1','source_fd_held_and_verified':True})
    return None


def header(ctx,value,admitted,*,complete=False):
    expected={'schema':'market_image_stream_complete/1' if complete else 'market_image_stream/1',
        'profile':ctx.spec['profile'],'operation_sha256':ctx.pin,
        'admission_sha256':ctx.op['admission_sha256'],'archive_bytes':admitted['archive_bytes'],
        'archive_sha256':admitted['archive_sha256'],'tar_identity':admitted['tar_identity']}
    if complete:expected['source_fd_held_and_verified']=True
    need(ctx.D.same(value,expected),'stream_binding')


def clients_close(ctx,clients,streams):
    failures=[]
    for child in reversed(clients):
        try:stop(child)
        except BaseException:failures.append('client_reap_unproven')
    for stream in streams:
        try:stream.close() if hasattr(stream,'close') else os.close(stream)
        except BaseException:failures.append('client_descriptor_close_unproven')
    if failures:
        try:ctx.publish('client-cleanup-unproven.json',{'causes':sorted(set(failures)),
            'retry_allowed':False,'daemon_cancellation_proven':False,'security_accepted':False})
        except BaseException:pass  # Reporting must not replace an existing primary failure.
        return False
    return True


def load_native(ctx):
    global SUBPHASE
    SUBPHASE='native_source_admission'
    need(ctx.site=='bot' and ctx.spec['site']=='native','site_scope');admitted=source_archive(ctx)
    ctx.budget=admitted['prospective_import_bytes'];ctx.capacity()
    before=stable(ctx);need(before['image_map']==ctx.baseline['image_map']
        and not {r['id'] for r in before['image_map']}&set(admitted['source_identities']),'candidate_preexisting')
    ctx.publish('source-request.json',{'outcome':'unknown_until_measured','retry_allowed':False,'security_accepted':False})
    clients=[];streams=[];primary=False
    try:
        errors=os.memfd_create('market-client-errors',os.MFD_CLOEXEC);streams.append(errors)
        SUBPHASE='native_source_request'
        source=subprocess.Popen(remote(ctx,'stream-web'),stdin=subprocess.PIPE,stdout=subprocess.PIPE,
            stderr=errors,preexec_fn=bounds);clients.append(source);streams.extend([source.stdin,source.stdout])
        SUBPHASE='native_header';header(ctx,read_frame(ctx,source.stdout.fileno()),admitted)
        ctx.publish('load-request.json',{'daemon_outcome':'unknown_until_measured','retry_allowed':False,'security_accepted':False})
        SUBPHASE='native_load_request'
        ctx.requested=True;output=os.memfd_create('market-load-output',os.MFD_CLOEXEC);streams.append(output)
        loader=subprocess.Popen([*DOCKER,'image','load','--quiet'],stdin=subprocess.PIPE,stdout=output,
            stderr=errors,preexec_fn=bounds);clients.append(loader);streams.append(loader.stdin)
        os.set_blocking(loader.stdin.fileno(),False);write_all(ctx,source.stdin.fileno(),b'START\n');source.stdin.close()
        SUBPHASE='native_body';total=0;digest=sha256()
        while total<admitted['archive_bytes']:
            block=read_part(ctx,source.stdout.fileno(),min(65536,admitted['archive_bytes']-total));need(block,'source_short')
            total+=len(block);digest.update(block);write_all(ctx,loader.stdin.fileno(),block)
        SUBPHASE='native_footer_eof';loader.stdin.close();header(ctx,read_frame(ctx,source.stdout.fileno()),admitted,complete=True)
        need(not read_part(ctx,source.stdout.fileno(),1) and digest.hexdigest()==admitted['archive_sha256'],'stream_eof_or_sha256')
        SUBPHASE='native_client_reap'
        for child in clients:
            while child.poll() is None:ctx.capacity();time.sleep(.05)
            need(child.wait(timeout=1)==0,'load_client_unknown')
        need(os.fstat(output).st_size<=LIMIT and os.fstat(errors).st_size<=LIMIT,'client_output_bound')
        SUBPHASE='native_daemon_measurement';result=measured(ctx,admitted);ctx.check()
    except BaseException:primary=True;raise
    finally:
        clean=clients_close(ctx,clients,streams)
        if not clean and not primary:raise RuntimeError('client_cleanup_unproven')
    return {'status':'loaded_measured_not_accepted','receipt_sha256':ctx.publish('loaded.json',result),'security_accepted':False}


def coordinate_web(ctx):
    """Native owns permanent locks while one bounded remote phase runs."""
    global SUBPHASE
    SUBPHASE='web_phase_request'
    need(ctx.site=='bot' and ctx.op['phase'] in {'receive-web','load-web'},'site_scope')
    ctx.publish('web-request.json',{'outcome':'unknown_until_measured','retry_allowed':False,'security_accepted':False})
    ctx.requested=True;clients=[];streams=[];primary=False
    try:
        incoming=subprocess.DEVNULL
        if ctx.op['phase']=='receive-web':
            row=ctx.spec['image_artifact']
            fetch=subprocess.Popen(['/usr/bin/gh','api','--method','GET',f"repos/mohsenbarari/trading_bot/actions/artifacts/{row['id']}/zip"],
                stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,preexec_fn=bounds)
            clients.append(fetch);streams.append(fetch.stdout);incoming=fetch.stdout
        result=command(ctx,remote(ctx,ctx.op['phase']),stdin=incoming)
        if clients:
            while clients[0].poll() is None:ctx.capacity();time.sleep(.05)
            need(clients[0].wait(timeout=1)==0,'artifact_client_unknown')
        value=document(result);expected='archive_admitted_not_loaded' if ctx.op['phase']=='receive-web' else 'loaded_measured_not_accepted'
        need(value.get('status')==expected and value.get('security_accepted') is False
             and re.fullmatch('[a-f0-9]{64}',value.get('receipt_sha256','')),'remote_result')
        need(stable(ctx)['image_map']==ctx.baseline['image_map'],'native_runtime_changed')
    except BaseException:primary=True;raise
    finally:
        clean=clients_close(ctx,clients,streams)
        if not clean and not primary:raise RuntimeError('client_cleanup_unproven')
    return value


def run(ctx,apply=False):
    global SUBPHASE
    SUBPHASE='public_preflight'
    need(daemon_policy(ctx)==ctx.baseline['daemon'],'daemon_public_policy_changed')
    with Path('/etc/machine-id').open('rb') as source:host=source.read(65).strip().decode()
    need(host==ctx.baseline['machine_id'],'host_identity_changed')
    need(store_view(ctx)==ctx.baseline['store'],'docker_store_changed')
    before=stable(ctx);need(before['image_map']==ctx.baseline['image_map'],'image_map_changed');ctx.capacity()
    if not apply:return {'status':'public_preflight_passed_not_requested','security_accepted':False}
    SUBPHASE='owned_fences';guard=Guard(ctx);ctx.begin()
    if ctx.site=='bot' and ctx.op['phase']!='load-native':result=coordinate_web(ctx)
    else:result={'receive-web':receive_web,'load-web':load_web,'load-native':load_native,'stream-web':stream_web}[ctx.args.phase](ctx)
    ctx.check();need(daemon_policy(ctx)==ctx.baseline['daemon'],'daemon_public_policy_changed')
    need(store_view(ctx)==ctx.baseline['store'],'docker_store_changed')
    guard.finish();return result


def exception_metadata(error):
    """Closed builtin class/errno only; never exception text, args or paths."""
    allowed=(OSError,BlockingIOError,PermissionError,FileNotFoundError,FileExistsError,
        NotADirectoryError,IsADirectoryError,TimeoutError,InterruptedError,RuntimeError,
        ValueError,TypeError,KeyError,AttributeError,AssertionError)
    known=type(error) in allowed
    number=error.errno if known and isinstance(error,OSError) else None
    return {'exception_class':type(error).__name__ if known else 'other',
        'os_errno':number if type(number) is int and 0<number<=4095 else None}


def main(argv=None):
    global SUBPHASE
    SUBPHASE='control_admission'
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--site',choices=('bot','web'),required=True)
    parser.add_argument('--phase',choices=(*PHASES,'stream-web','snapshot'),required=True)
    parser.add_argument('--control',type=Path);parser.add_argument('--control-sha256')
    parser.add_argument('--source-sha256')
    parser.add_argument('--apply');args=parser.parse_args(argv)
    ctx=None
    try:
        need(args.apply in (None,CONFIRM),'confirmation')
        if args.phase=='snapshot':
            need(args.apply is None,'snapshot_readonly');result=collect(args.site,args.source_sha256)
            public(result);print(json.dumps(result,sort_keys=True));return 0
        with ExitStack() as stack:
            ctx=Context(args,stack);signal.alarm(max(1,int(ctx.end-time.monotonic())))
            result=run(ctx,args.apply is not None)
            if result is not None:public(result);print(json.dumps(result,sort_keys=True))
        return 0
    except Exception as error:
        code=error.args[0] if type(error) is RuntimeError and len(error.args)==1 and type(error.args[0]) is str and error.args[0] in PUBLIC_CODES else 'refused_or_unknown'
        if code=='refused_or_unknown' and ctx is not None:code=ctx.D.error_code(error)
        if args.phase!='stream-web':print(json.dumps({'status':'refused_or_unknown','phase':args.phase,
            'subphase':SUBPHASE,'code':code,'retry_allowed':False,'security_accepted':False,
            **exception_metadata(error)},sort_keys=True))
        return 1
    finally:signal.alarm(0)


PUBLIC_CODES = frozenset(["admission_path","admitted_source_changed","archive_identity","archive_sha256","artifact_client_unknown","baseline_freshness","baseline_sites","candidate_preexisting","capacity_budget","capacity_floor","capture_singleton","client_cleanup_unproven","client_output_bound","command_bound","command_outcome_unknown","command_output_bound","confirmation","container_inventory","control_fields","control_scope","daemon_config_or_rootfs","daemon_descriptor","daemon_public_file_metadata","daemon_public_policy_changed","daemon_temporary_storage_contract","daemon_unit_metadata_changed","deadline","docker_store_changed","document_bound","duplicate_key","evidence_config_identity","evidence_inventory","evidence_member_bound","evidence_member_pin","evidence_size","export_bound","export_identity","export_pin","fence_lock_timeout","fence_unlink_unproven","finite_scope_required","frame_bound","frame_short","gzip_bound","gzip_identity","host_identity","host_identity_changed","host_policy_shape","image_map_changed","image_map_delta","image_zip_identity","image_zip_inventory","load_client_unknown","log_policy_shape","native_runtime_changed","nonfinite_json","output_bound","owned_fence_changed","pending_release_requires_reconciliation","permanent_fence_changed","permanent_fence_identity","permanent_fence_path","permanent_fence_shape","phase_scope","post_load_source_sha256","previous_attempt_requires_reconciliation","private_output","profile_scope","protected_parent","protected_parent_changed","read_bound","remote_result","root_required","runtime_changed","scope_lifetime","scope_limits","site_scope","snapshot_readonly","snapshot_runtime_unstable","source_admission","source_admission_budget","source_admission_sha256","source_changed","source_extra","source_metadata","source_path","source_sha256","source_short","source_stream_changed","store_root","store_root_changed","stream_binding","stream_eof_or_sha256","stream_start","tar_size","target_site","temporary_storage_changed","temporary_memory_floor","temporary_memory_metadata","temporary_storage_metadata","temporary_storage_mode","tools_directory","unknown_daemon_identity","write_short"])


if __name__=='__main__':raise SystemExit(main())
