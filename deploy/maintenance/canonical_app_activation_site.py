"""The five existing normal Compose services only. No historical controller reuse.

Private renders/Config/Env never leave RAM. This module has no standalone apply
entrypoint: the coordinator supplies its held, source-admitted contract.
"""
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
from hashlib import sha256
import fcntl
import json
import os
import re
import resource
import signal
import stat
import subprocess
import time

C = None
ROOTS = {'bot':Path('/root/trading-bot/trading_bot/.local/security-canonical-app-activation-20261025-01'),
         'web':Path('/srv/trading-bot/security-canonical-app-activation-20261025-01')}
MARKER = Path('/root/secure-envs/trading-bot/queue-cutover-artifacts/production-release.lock')
SOURCE = Path('/root/secure-envs/trading-bot/.production-runtime-source.lock')
RESERVATION = Path('/root/secure-envs/trading-bot/release-control/private-primary-controller.lock')
COMMAND_ENV = {'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8',
    'HOME':'/root','DOCKER_HOST':'unix:///var/run/docker.sock','DOCKER_CONFIG':'/root/.docker'}
OWNER_READ = '''import asyncio,json,sys
sys.path.insert(0,'/app')
from core.telegram_central_poller_owner import telegram_central_poller_owner_is_held
from core.telegram_delivery_queue_owner import telegram_delivery_queue_owner_is_held
from core.db import engine
async def read():
 try:
  return {'central':await telegram_central_poller_owner_is_held(),'queue':await telegram_delivery_queue_owner_is_held()}
 finally:await engine.dispose()
print(json.dumps(asyncio.run(read()),sort_keys=True))
'''


class Unknown(RuntimeError):
    """No cancellation/rollback authority follows an unproved write outcome."""


class KnownFailure(RuntimeError):
    """The write completed and exact owned runtime was measured independently."""


def identity(s):
    return [s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns]


def parents(path):
    rows = []
    for p in path.parents:
        s = p.lstat()
        C.require(p.resolve(strict=True) == p and stat.S_ISDIR(s.st_mode) and s.st_uid == s.st_gid == 0
            and not s.st_mode & 0o022,'parent_admission')
        rows.append((p,identity(s)[:5]))
    return rows


def check_parents(rows):
    C.require(all(p.resolve(strict=True) == p and identity(p.lstat())[:5] == before for p,before in rows),'parent_changed')


class Held:
    def __init__(self,path,stack,*,expected=None,pin=None,maximum=131072,opaque=False,lock=False,public_source=False):
        self.path = Path(path); self.parents = parents(self.path)
        C.require(self.path.resolve(strict=True) == self.path,'source_alias')
        self.fd = os.open(self.path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
        stack.callback(os.close,self.fd); self.before = identity(os.fstat(self.fd))
        if public_source:
            C.require(self.before[2] in {0o100600,0o100644} and self.before[3:6] == [0,0,1],'public_source_identity')
        else:C.metadata(self.before)
        if expected is not None:C.require(C.same(self.before,expected),'held_identity')
        self.raw = None
        if not opaque:
            C.require(0 < self.before[6] <= maximum,'held_bound')
            self.raw = os.pread(self.fd,self.before[6]+1,0)
            C.require(len(self.raw) == self.before[6] and (pin is None or sha256(self.raw).hexdigest() == pin),'held_content')
        if lock:
            try:fcntl.flock(self.fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError:raise RuntimeError('permanent_lock_busy') from None
        self.check(); stack.callback(self.final_check)

    def check(self):
        check_parents(self.parents)
        C.require(C.same(identity(os.fstat(self.fd)),self.before)
            and C.same(identity(self.path.lstat()),self.before),'held_changed')
        if self.raw is not None:C.require(os.pread(self.fd,len(self.raw)+1,0) == self.raw,'held_changed')

    def final_check(self):
        # ExitStack may already be unwinding a primary error. Never mask it.
        import sys
        primary = sys.exc_info()[0] is not None
        try:self.check()
        except BaseException:
            if not primary:raise


def sync_directory(path):
    fd = os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:os.fsync(fd)
    finally:os.close(fd)


def publish(path,value):
    raw = C.encoded(value); proof = parents(path)
    fd = os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    try:
        os.fchmod(fd,0o600); view = memoryview(raw)
        while view:
            n = os.write(fd,view); C.require(n > 0,'write_incomplete'); view = view[n:]
        os.fsync(fd)
    finally:os.close(fd)
    check_parents(proof); sync_directory(path.parent)
    return sha256(raw).hexdigest()


class Guard:
    def __init__(self,path,control,stack):
        self.path = Path(path)
        self.raw = {'schema':'canonical_app_activation_guard/1','operation':C.OPERATION,'nonce':control['nonce'],
            'controller_commit':control['controller_commit'],'pid':os.getpid(),'security_accepted':False}
        publish(self.path,self.raw); self.held = Held(self.path,stack); self.closed = False
    def check(self):
        if self.closed:
            C.require(os.fstat(self.held.fd).st_nlink == 0 and not os.path.lexists(self.path),'guard_cleanup'); return
        C.require(C.same(C.document(self.held.raw),self.raw),'guard_binding'); self.held.check()
    def close(self):
        self.check(); os.unlink(self.path); sync_directory(self.path.parent)
        C.require(os.fstat(self.held.fd).st_nlink == 0 and not os.path.lexists(self.path),'guard_cleanup')
        # Do not perform a path check on an intentionally unlinked owned guard.
        self.closed = True
        self.held.check = self.check


class Journal:
    def __init__(self,directory,control):
        self.path = Path(directory); self.control = control; self.index = 0
        C.require(not os.path.lexists(self.path),'attempt_spent'); parents(self.path)
        self.path.mkdir(mode=0o700); sync_directory(self.path.parent)
    def record(self,state,service=None,container_id=None):
        C.require(state in {'stop_intent','stopped','create_intent','created','start_intent','active',
            'rollback_intent','rolled_back','unknown','final_verified','terminal_prepared','terminal_closed'},'journal_state')
        C.require(service is None or service in {'app','bot','sync_worker'},'journal_service')
        C.require(container_id is None or C.hex64(container_id),'journal_container')
        value = {'schema':'canonical_app_activation_step/1','operation':C.OPERATION,'nonce':self.control['nonce'],
            'index':self.index,'state':state,'service':service,'container_id':container_id,'security_accepted':False}
        publish(self.path/('%03d.json'%self.index),value); self.index += 1


def command(argv,deadline,*,stdin=None,maximum=None,cwd=None,mutation=False):
    """Hard file bounds before accumulation; private output never in an exception."""
    maximum = C.MAX_JSON if maximum is None else maximum
    C.require(type(argv) is list and argv and all(type(x) is str and '\x00' not in x for x in argv),'command_shape')
    C.require(type(maximum) is int and 0 < maximum <= C.MAX_JSON,'command_bound')
    remaining = deadline-time.monotonic(); C.require(remaining > 0,'deadline')
    output = os.memfd_create('normal-compose-private-output',os.MFD_CLOEXEC)
    errors = os.memfd_create('normal-compose-private-error',os.MFD_CLOEXEC)
    inputfd = None; child = None
    try:
        if stdin is not None:
            C.require(type(stdin) is bytes and len(stdin) <= C.MAX_JSON,'stdin_bound')
            inputfd = os.memfd_create('normal-compose-private-input',os.MFD_CLOEXEC)
            view = memoryview(stdin)
            while view:n=os.write(inputfd,view); C.require(n > 0,'stdin_write'); view=view[n:]
            os.lseek(inputfd,0,os.SEEK_SET)
        def limits():
            resource.setrlimit(resource.RLIMIT_FSIZE,(maximum+1,maximum+1))
            resource.setrlimit(resource.RLIMIT_CORE,(0,0))
        child = subprocess.Popen(argv,stdin=inputfd if inputfd is not None else subprocess.DEVNULL,
            stdout=output,stderr=errors,env=COMMAND_ENV,cwd=cwd,start_new_session=True,preexec_fn=limits,close_fds=True)
        try:rc = child.wait(timeout=min(remaining,120))
        except subprocess.TimeoutExpired:
            try:os.killpg(child.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            child.wait(timeout=5); raise Unknown('command_timeout') from None
        size,esize = os.fstat(output).st_size,os.fstat(errors).st_size
        if rc != 0 or size > maximum or esize > maximum:
            raise (Unknown if mutation else RuntimeError)('command_unproven')
        raw = os.pread(output,size+1,0); C.require(len(raw) == size,'command_output_changed'); return raw
    except (Unknown,RuntimeError):raise
    except BaseException:raise (Unknown if mutation else RuntimeError)('command_unproven') from None
    finally:
        import sys
        primary = sys.exc_info()[0] is not None; cleanup_failed = False
        try:
            if child is not None and child.poll() is None:
                try:os.killpg(child.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                child.wait(timeout=5)
        except BaseException:cleanup_failed = True
        for fd in (inputfd,output,errors):
            if fd is not None:
                try:os.close(fd)
                except BaseException:cleanup_failed = True
        if cleanup_failed and not primary:raise Unknown('command_cleanup_unproven')


def row(value):
    state = value['State']; C.require(type(state) is dict,'container_shape')
    return {'id':value['Id'],'name':value['Name'],'image':value['Image'],'config':value['Config'],
        'host':value['HostConfig'],'mounts':value['Mounts'],'networks':value['NetworkSettings']['Networks'],
        'running':state['Running'],'pid':state['Pid'],'started':state['StartedAt'],
        'restarts':value['RestartCount'],'paused':state['Paused'],'restarting':state['Restarting'],
        'dead':state['Dead'],'health':state.get('Health',{}).get('Status')}


def stable_running(value):
    C.require(value['running'] is True and value['paused'] is value['restarting'] is value['dead'] is False
        and type(value['pid']) is int and value['pid'] > 0 and type(value['restarts']) is int
        and value['restarts'] >= 0 and type(value['started']) is str and value['started'],'role_not_running')


def comparable_inventory(rows):
    # Keep the complete private Config (including Env) only in these RAM maps.
    # Dynamic health is observed separately; it is never a new all-container gate.
    return {key:{k:v for k,v in item.items() if k != 'health'} for key,item in rows.items()}


class Site:
    def __init__(self,site,control,stack,*,apply=False):
        C.require(site in C.SITES and os.geteuid() == os.getegid() == 0,'root_site_required')
        C.validate_control(control,int(time.time()))
        self.site,self.control,self.scope,self.stack = site,control,control['sites'][site],stack
        self.root = ROOTS[site]; self.apply = apply; self.guard = None; self.journal = None; self.recovering = False
        self.deadline = time.monotonic()+control['forward_deadline']-time.time(); self.checks = []
        parents(self.root/'placeholder')
        C.require((self.root.stat().st_mode & 0o7777) == 0o700,'root_mode')
        C.require(Path('/proc/self/ns/mnt').stat().st_ino == Path('/proc/1/ns/mnt').stat().st_ino,'host_namespace')
        machine = HeldPublic('/etc/machine-id',stack,64).raw.strip().decode('ascii')
        C.require(machine == self.scope['machine_id'],'machine_id')
        if apply:self.scope_check()
        for name,path in [('marker',MARKER)]+([('source',SOURCE)] if site == 'bot' else []):
            held = Held(path,stack,expected=self.scope['fences'][name],opaque=True,lock=True); self.checks.append(held.check)
        C.require(not os.path.lexists(RESERVATION if site == 'bot' else self.root/'activation.guard'),'guard_busy')
        self.paths = [r['path'] for r in self.scope['compose_files']]
        for proof in self.scope['compose_files']:
            held = Held(proof['path'],stack,pin=proof['sha256'],maximum=C.MAX_CONTROL,public_source=True)
            C.require(len(held.raw) == proof['bytes'],'compose_size'); self.checks.append(held.check)
        for proof in self.scope['env_files']:
            reference = OpaqueReference(proof,stack); self.checks.append(reference.check)
        local = C.evidence(control,self.evidence,int(time.time())); self.candidate = local[site]
        self.version_check(); self.models = {}; self.original = self.inventory(); self.current = deepcopy(self.original)
        self.images = {}; self.role_ids = {}; self.completed = []; self.transactions = {}; self.terminal = None
        self.old_model = self.render()
        references = self.render(no_env_resolution=True)
        allowed_refs = {r['path'] for r in self.scope['env_files']} | {r['target'] for r in self.scope['env_files']}
        for service in C.SITES[site]['roles']:
            for reference in references['services'][service].get('env_file',[]):
                path = reference['path'] if type(reference) is dict else reference
                C.require(path in allowed_refs,'unbound_env_reference')
            for mount in self.old_model['services'][service].get('volumes',[]):
                if mount.get('target') == '/app/.env':
                    C.require(mount.get('type') == 'bind' and mount.get('source') in allowed_refs,'unbound_env_reference')
        for service,binding in self.scope['roles'].items():
            cid = binding['container_id']; C.require(cid in self.original,'role_missing'); actual = self.original[cid]
            stable_running(actual)
            C.require(all(C.same(actual[k],binding[key]) for k,key in
                (('image','image_id'),('started','started'),('pid','pid'),('restarts','restarts'))),'role_baseline')
            self.role_ids[service] = cid; self.images[actual['image']] = self.image(actual['image'])
            C.normal_baseline(self.old_model,service,actual,self.images[actual['image']],self.scope,self.paths)
            self.functional(service,actual)
        self.new_image = self.image(self.candidate)
        C.require(C.same(self.new_image['RootFS'],C.IMAGE['rootfs']),'candidate_rootfs')
        C.require(self.new_image['Id'] == self.candidate,'candidate_identity')
        for value in self.images.values():C.image_defaults(value['Config'],self.new_image['Config'],self.scope['public_image_env_delta'])
        self.override = self.root/'attempts'/control['nonce']/'image-only.yml'
        self.rollback_override = self.root/'attempts'/control['nonce']/'old-images.yml'
        self.new_model = self.render(override=self.override_bytes(),virtual=True)
        C.rendered_delta(self.old_model,self.new_model,C.SITES[site]['roles'],self.candidate,control['runtime_env_delta'])
        self.rollback_model = self.render(override=self.override_bytes(candidate=False),virtual=True)
        C.rendered_delta(self.old_model,self.rollback_model,C.SITES[site]['roles'],
            {s:self.original[cid]['image'] for s,cid in self.role_ids.items()},{})
        self.units_check(); self.check()

    def evidence(self,binding):
        held = Held(self.root/'evidence'/binding['name'],self.stack,pin=binding['sha256'],maximum=C.MAX_JSON)
        C.require(len(held.raw) == binding['bytes'],'evidence_size'); self.checks.append(held.check); return held.raw

    def check(self):
        C.require(time.monotonic() < self.deadline,'deadline')
        for check in self.checks:check()
        if self.guard:self.guard.check()

    def cmd(self,args,**kw):
        self.check(); result = command(args,self.deadline,**kw); self.check(); return result

    def docker(self,*args,**kw):return self.cmd(['/usr/bin/docker',*args],**kw)

    def compose(self,extra=False,rollback=False):
        args = ['/usr/bin/docker','compose','--project-directory',self.scope['workdir'],'--project-name',self.scope['project']]
        for proof in self.scope['env_files']:
            if proof['cli']:args += ['--env-file',proof['path']]
        for path in self.paths:args += ['--file',path]
        if extra:args += ['--file',str(self.override)]
        if rollback:args += ['--file',str(self.rollback_override)]
        return args

    def render(self,override=None,virtual=False,no_env_resolution=False):
        argv = self.compose()
        if virtual:argv += ['--file','-']
        elif override is not None:argv += ['--file',str(override)]
        value = C.document(self.cmd(argv+['config','--format','json']+(['--no-env-resolution'] if no_env_resolution else []),
            stdin=override if virtual else None,cwd=self.scope['workdir']))
        C.require(type(value) is dict and value.get('name') == self.scope['project'],'normal_render'); return value

    def override_bytes(self,candidate=True):
        return (json.dumps({'services':{s:{'image':self.candidate if candidate else self.original[self.scope['roles'][s]['container_id']]['image'],
            **({'environment':self.control['runtime_env_delta']} if candidate and self.control['runtime_env_delta'] else {})}
            for s in C.SITES[self.site]['roles']}},sort_keys=True)+'\n').encode()

    def inventory(self):
        ids = self.docker('ps','--all','--no-trunc','--format','{{.ID}}',maximum=131072).decode().splitlines()
        C.require(0 < len(ids) <= 128 and len(set(ids)) == len(ids) and all(C.hex64(cid) for cid in ids),'inventory_shape')
        rows = C.document(self.docker('inspect','--type','container',*ids)); C.require(type(rows) is list and len(rows) == len(ids),'inventory_shape')
        values = {value['Id']:row(value) for value in rows}; C.require(set(values) == set(ids),'inventory_shape'); return values

    def inspect(self,cid):
        C.require(C.hex64(cid),'container_id'); values = C.document(self.docker('inspect','--type','container',cid))
        C.require(type(values) is list and len(values) == 1 and values[0]['Id'] == cid,'container_identity'); return row(values[0])

    def image(self,iid):
        C.require(C.image_id(iid),'image_id'); values = C.document(self.docker('image','inspect',iid))
        C.require(type(values) is list and len(values) == 1 and values[0]['Id'] == iid,'image_identity'); return values[0]

    def version_check(self):
        C.require(self.docker('compose','version','--short',maximum=1024).decode().strip() == self.scope['compose_version'],'compose_version')
        C.require(self.docker('version','--format','{{.Server.Version}}',maximum=1024).decode().strip() == self.scope['docker_version'],'docker_version')

    def scope_check(self):
        with open('/proc/self/cgroup','rb') as source:raw = source.read(8193)
        C.require(len(raw) <= 8192,'scope_bound'); paths = [s[3:] for s in raw.decode().splitlines() if s.startswith('0::')]
        unit = 'security-canonical-app-activation-'+self.control['nonce']+'.scope'
        C.require(len(paths) == 1 and paths[0].endswith('/'+unit) and '..' not in paths[0].split('/'),'finite_scope')
        directory = Path('/sys/fs/cgroup'+paths[0])
        for name,maximum in (('memory.max',768*1024**2),('pids.max',64)):
            with (directory/name).open('rb') as source:value = source.read(64)
            C.require(value.strip().isdigit() and 0 < int(value) <= maximum,'finite_scope')
        with (directory/'cpu.max').open('rb') as source:parts = source.read(64).split()
        C.require(len(parts) == 2 and all(v.isdigit() for v in parts) and 0 < int(parts[0]) <= int(parts[1]),'finite_scope')
        duration = command(['/usr/bin/systemctl','show',unit,'--value','--property=RuntimeMaxUSec'],self.deadline,maximum=1024).decode().strip()
        C.scope_seconds(duration)

    def units_check(self):
        for unit,expected in {**self.scope['reader_units'],**self.scope['legacy_units']}.items():
            raw = self.cmd(['/usr/bin/systemctl','show',unit,'--no-pager',
                '--property='+','.join(sorted(expected))],maximum=32768).decode()
            actual = dict(line.split('=',1) for line in raw.splitlines() if '=' in line)
            C.require(C.same(actual,expected),'unit_policy_changed')

    def unchanged(self,old=None,new=None):
        now = self.inventory(); expected = comparable_inventory(self.current)
        if old is None:C.require(C.same(expected,comparable_inventory(now)),'runtime_changed')
        else:C.other_containers(expected,comparable_inventory(now),old,new)
        self.units_check(); self.check(); return now

    def begin(self):
        C.require(self.apply and self.guard is None,'apply_not_authorized'); self.unchanged()
        self.guard = Guard(RESERVATION if self.site == 'bot' else self.root/'activation.guard',self.control,self.stack)
        self.journal = Journal(self.root/'attempts'/self.control['nonce'],self.control)
        for path,candidate in ((self.override,True),(self.rollback_override,False)):
            raw = self.override_bytes(candidate=candidate); fd = os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
            try:
                C.require(os.write(fd,raw) == len(raw),'override_write'); os.fsync(fd)
            finally:os.close(fd)
            sync_directory(path.parent)
            held = Held(path,self.stack,pin=sha256(raw).hexdigest()); self.checks.append(held.check)
        actual = self.render(override=self.override); C.require(C.same(actual,self.new_model),'normal_render_changed')
        C.require(C.same(self.render(override=self.rollback_override),self.rollback_model),'normal_render_changed')

    def cgroup(self,value):
        stable_running(value); pid = value['pid']
        with open('/proc/'+str(pid)+'/cgroup','rb') as source:raw = source.read(8193)
        C.require(len(raw) <= 8192,'cgroup_bound')
        lines = raw.decode().splitlines(); paths = [line[3:] for line in lines if line.startswith('0::')]
        C.require(len(paths) == 1 and value['id'] in paths[0] and '..' not in paths[0].split('/'),'cgroup_identity')
        path = Path('/sys/fs/cgroup'+paths[0]); fd = os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
        self.stack.callback(os.close,fd); before = identity(os.fstat(fd))[:5]
        def empty():
            C.require(identity(os.fstat(fd))[:5] == before,'cgroup_changed')
            try:events = os.open('cgroup.events',os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
            except FileNotFoundError:
                C.require(not os.path.lexists(path) and os.fstat(fd).st_nlink == 0,'cgroup_unproven'); return
            try:raw = os.read(events,4097)
            finally:os.close(events)
            C.require(len(raw) <= 4096 and b'populated 0\n' in raw,'cgroup_not_empty')
        return empty

    def singleton(self,service,excluding=None):
        rows = self.inventory(); expected = self.old_model['services'][service]
        for cid,value in rows.items():
            if cid == excluding or not value['running']:continue
            labels = value['config'].get('Labels') or {}; env = C.environment(value['config'].get('Env',[]))
            C.require(not (labels.get('com.docker.compose.project') == self.scope['project']
                and labels.get('com.docker.compose.service') == service),'duplicate_role')
            if service in {'bot','sync_worker'}:
                C.require(env.get('TRADING_BOT_SERVICE') != service,'duplicate_role')
        self.units_check()

    def stop(self,service,cid):
        before = self.inspect(cid); empty = self.cgroup(before)
        self.journal.record('stop_intent',service,cid); self.docker('stop','--time','45',cid,mutation=True,maximum=1024)
        after = self.inspect(cid)
        if after['running'] is not False or after['pid'] != 0:raise Unknown('stop_unproven')
        empty(); self.singleton(service,excluding=cid); self.unchanged(cid)
        self.journal.record('stopped',service,cid); return empty

    def create(self,service,*,candidate=True):
        self.journal.record('create_intent',service)
        self.cmd(self.compose(extra=candidate,rollback=not candidate)+['up','--no-start','--force-recreate','--no-deps','--no-build','--pull','never',service],
            mutation=True,cwd=self.scope['workdir'],maximum=131072)
        values = self.cmd(self.compose(extra=candidate,rollback=not candidate)+['ps','--all','--quiet',service],maximum=1024).decode().splitlines()
        if len(values) != 1 or not C.hex64(values[0]):raise Unknown('created_identity_unproven')
        value = self.inspect(values[0])
        if (value['running'] is not False or value['pid'] != 0 or value['restarts'] != 0
            or value['started'] != '0001-01-01T00:00:00Z' or value['paused'] is not False
            or value['restarting'] is not False or value['dead'] is not False):raise Unknown('created_state_unproven')
        self.journal.record('created',service,value['id']); return value

    def start(self,service,value):
        self.singleton(service,excluding=value['id']); self.journal.record('start_intent',service,value['id'])
        self.docker('start',value['id'],mutation=True,maximum=1024)
        after = self.inspect(value['id']); stable_running(after); return after

    def functional(self,service,value):
        """Use the existing normal execution, never a diagnostic profile/process."""
        import urllib.request
        limits = C.normalize_health(value['config'].get('Healthcheck'))
        configured = 0 if limits is None else (limits['StartPeriod']+limits['Interval']*limits['Retries'])/1000000000
        end = min(self.deadline,time.monotonic()+max(45,min(configured,180)))
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        while True:
            measured = self.inspect(value['id']); stable_running(measured)
            C.require(all(C.same(value[k],measured[k]) for k in ('id','image','pid','started','restarts')),'readiness_runtime_changed')
            try:
                if measured['health'] is not None:C.require(measured['health'] == 'healthy','normal_health_unready')
                if service in {'app','sync_worker'}:
                    path = '/api/config' if service == 'app' else '/api/sync/health'
                    headers = {}
                    if service == 'sync_worker':
                        key = C.environment(value['config']['Env']).get('OBSERVABILITY_API_KEY')
                        if key:headers['X-Observability-Api-Key'] = key
                    request = urllib.request.Request('http://127.0.0.1:8000'+path,headers=headers)
                    with opener.open(request,timeout=min(5,max(.1,end-time.monotonic()))) as response:
                        raw = response.read(65537); C.require(response.status == 200 and len(raw) <= 65536,'normal_api_unready')
                    if service == 'sync_worker':
                        health = C.document(raw,65536)
                        C.require(health.get('status') == 'ok' and health.get('redis_ok') is True,'normal_sync_unready')
                else:
                    owners = C.document(self.docker('exec','--workdir','/app',value['id'],
                        '/usr/local/bin/python3','-I','-B','-c',OWNER_READ,maximum=1024),1024)
                    C.require(type(owners) is dict and set(owners) == {'central','queue'}
                        and all(type(v) is bool for v in owners.values()),'normal_bot_owner')
                    env = C.environment(value['config']['Env']); role = env.get('TELEGRAM_BOT_RUNTIME_ROLE','all')
                    C.require(role in {'all','primary','executor'},'normal_bot_owner')
                    if role in {'all','primary'}:C.require(owners['central'],'normal_bot_owner')
                    if env.get('TELEGRAM_DELIVERY_EXECUTION_OWNER') == 'queue_v1' and role in {'all','executor'}:
                        C.require(owners['queue'],'normal_bot_owner')
                break
            except (RuntimeError,OSError,ValueError):
                if time.monotonic()+.25 >= end:raise RuntimeError('normal_role_unready') from None
                time.sleep(.25)
        self.singleton(service,excluding=value['id']); self.units_check()

    def replace(self,service):
        C.require(not self.recovering,'forward_after_recovery'); return replace(self,service)

    def rollback(self,service):
        C.require(self.recovering,'recovery_not_admitted'); return rollback(self,service)

    def recovery(self):
        C.validate_control(self.control,int(time.time()),recovery=True)
        C.require(self.guard is not None,'recovery_without_guard')
        self.recovering = True
        self.deadline = time.monotonic()+self.control['recovery_deadline']-time.time(); self.check()

    def final(self):
        self.unchanged()
        for service in self.completed:
            value = self.inspect(self.role_ids[service]); stable_running(value); self.functional(service,value)
        self.journal.record('final_verified'); return {'site':self.site,'roles':list(self.completed),'security_accepted':False}

    def close(self):
        C.require(self.terminal is not None,'terminal_not_prepared')
        self.final(); self.journal.record('terminal_closed'); self.guard.close()

    def prepare_terminal(self,status=None):
        self.final()
        local = 'activated' if self.completed == [s for site,s in C.ROLE_ORDER if site == self.site] else 'rolled_back'
        C.require((local == 'activated' or not self.completed) and (status is None or status == local),'terminal_state')
        value = {'schema':'canonical_app_activation_site_outcome/1','operation':C.OPERATION,'nonce':self.control['nonce'],
            'site':self.site,'status':local,'image_source_commit':C.IMAGE['source_commit'],
            'roles':{s:{'container_id':cid,'image_id':self.current[cid]['image']} for s,cid in self.role_ids.items()},
            'configuration_values_disclosed':False,'security_accepted':False,'full_matrix_accepted':False}
        self.journal.record('terminal_prepared'); self.terminal = publish(self.journal.path/'terminal.json',value)
        self.check(); return self.terminal


class HeldPublic:
    """Fixed public host identity only; never used for configuration or Env."""
    def __init__(self,path,stack,maximum):
        self.fd = os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC); stack.callback(os.close,self.fd)
        value = os.fstat(self.fd); C.require(stat.S_ISREG(value.st_mode) and value.st_uid == 0 and value.st_size <= maximum,'public_host_identity')
        self.raw = os.pread(self.fd,maximum+1,0); C.require(len(self.raw) <= maximum,'public_host_identity')


class OpaqueReference:
    """Hold the normal reference's explicit alias metadata; never read its data."""
    def __init__(self,proof,stack):
        self.proof = proof; self.links = []
        for alias in proof['aliases']:
            path = Path(alias['path']); chain = parents(path)
            C.require(C.same(identity(path.lstat()),alias['identity']),'env_alias_changed')
            self.links.append((path,alias['identity'],chain))
        self.held = Held(proof['target'],stack,expected=proof['identity'],opaque=True)
        self.check(); stack.callback(self.final_check)
    def alias_chain(self):
        declared = {str(path):before for path,before,_ in self.links}
        C.require(len(declared) == len(self.links),'env_alias_inventory')
        current = Path(self.proof['path']); visited = set()
        for _ in range(9):
            # Parent aliases are deliberately unsupported; they must never be
            # silently resolved outside this explicit metadata-only contract.
            parents(current); actual = identity(current.lstat())
            if not stat.S_ISLNK(actual[2]):
                C.require(str(current) == self.proof['target'] and visited == set(declared),'env_alias_inventory'); return
            C.require(str(current) in declared and str(current) not in visited
                and C.same(actual,declared[str(current)]),'env_alias_inventory')
            visited.add(str(current)); reference = os.readlink(current)
            C.require(type(reference) is str and 0 < len(reference) <= 4096 and '\x00' not in reference,'env_alias_inventory')
            current = Path(os.path.normpath(reference if reference.startswith('/') else str(current.parent/reference)))
            C.absolute(str(current))
        raise RuntimeError('env_alias_inventory')
    def check(self):
        for path,before,chain in self.links:
            check_parents(chain); C.require(C.same(identity(path.lstat()),before),'env_alias_changed')
        self.alias_chain()
        self.held.check()
    def final_check(self):
        import sys
        primary = sys.exc_info()[0] is not None
        try:self.check()
        except BaseException:
            if not primary:raise


def replace(backend,service):
    """A small stopped-first state machine, exercised with real owned fixtures."""
    order = [role for site,role in C.ROLE_ORDER if site == backend.site]
    C.require(service in order and backend.completed == order[:order.index(service)],'role_order')
    old = backend.current[backend.role_ids[service]]
    backend.unchanged(); backend.singleton(service,excluding=old['id'])
    tx = {'old':old,'empty':None,'created':None,'start_requested':False}; backend.transactions[service] = tx
    try:
        tx['empty'] = backend.stop(service,old['id'])
        candidate = backend.create(service); tx['created'] = candidate
        C.require(candidate['id'] != old['id'] and candidate['id'] not in backend.current
            and candidate['running'] is False and candidate['pid'] == 0 and candidate['restarts'] == 0
            and candidate['started'] == '0001-01-01T00:00:00Z','created_state_unproven')
        backend.unchanged(old['id'],candidate['id'])
        C.configuration_parity(backend.old_model,backend.new_model,service,old,candidate,
            backend.images[old['image']],backend.new_image,backend.scope,backend.paths,[*backend.paths,str(backend.override)],
            backend.control['runtime_env_delta'],stopped=True)
        backend.singleton(service,excluding=candidate['id']); tx['start_requested'] = True
        active = backend.start(service,candidate)
        C.require(active['restarts'] == 0,'candidate_restarted')
        backend.unchanged(old['id'],active['id'])
        C.configuration_parity(backend.old_model,backend.new_model,service,old,active,
            backend.images[old['image']],backend.new_image,backend.scope,backend.paths,[*backend.paths,str(backend.override)],
            backend.control['runtime_env_delta'])
        backend.functional(service,active)
    except Unknown:raise
    except BaseException:
        # Rollback requires an independently measured exact owned state. An
        # exception before create/after an uncertain request grants no authority.
        if tx['empty'] is None:raise Unknown('stop_state_unknown') from None
        try:
            if tx['created'] is not None:
                measured = backend.inspect(tx['created']['id'])
                C.require(measured['id'] == tx['created']['id'],'owned_state_unproven')
                if measured['pid'] == 0:
                    C.require(not tx['start_requested'] and measured['running'] is False,'owned_state_unproven'); tx['empty']()
                else:stable_running(measured)
                backend.unchanged(old['id'],measured['id'])
            else:
                measured = backend.inspect(old['id']); C.require(measured['pid'] == 0 and measured['running'] is False,'owned_state_unproven')
                tx['empty'](); backend.unchanged(old['id'])
        except BaseException:raise Unknown('owned_state_unproven') from None
        raise KnownFailure('measured_role_rejected') from None
    backend.current.pop(old['id']); backend.current[active['id']] = active; backend.role_ids[service] = active['id']
    backend.completed.append(service); backend.journal.record('active',service,active['id'])
    return {'site':backend.site,'service':service,'container_id':active['id'],'status':'active','security_accepted':False}


def rollback(backend,service):
    tx = backend.transactions[service]; old = tx['old']; current = tx['created']
    backend.journal.record('rollback_intent',service,current['id'] if current else old['id'])
    try:
        baseline_id = current['id'] if current and current['id'] in backend.current else old['id']
        backend.current = backend.unchanged(baseline_id,current['id']) if current else backend.unchanged(old['id'])
        if current is not None:
            value = backend.inspect(current['id'])
            if value['running'] is True:backend.stop(service,current['id'])
            else:
                C.require(value['pid'] == 0 and not tx['start_requested'] and callable(tx['empty']),'rollback_state_unproven')
                tx['empty'](); backend.unchanged(current['id'])
        else:tx['empty'](); backend.unchanged(old['id'])
        restored = backend.create(service,candidate=False)
        backend.unchanged(current['id'] if current else old['id'],restored['id'])
        rollback_paths = [*backend.paths,str(backend.rollback_override)]
        C.normal_baseline(backend.rollback_model,service,restored,backend.images[old['image']],backend.scope,rollback_paths,replacement=True)
        # Same old image, full business Config/Host/mounts; generated hostname,
        # Compose metadata and endpoint allocation are accounted explicitly.
        check = deepcopy(restored); check['config']['Hostname'] = old['config']['Hostname']
        check['config']['Labels'] = old['config']['Labels']; check['config']['Image'] = old['config']['Image']
        before_config = deepcopy(old['config'])
        C.require(C.same(C.environment(check['config'].pop('Env')),C.environment(before_config.pop('Env'))),'rollback_environment')
        C.require(C.same(check['config'],before_config)
            and C.same(C.unordered_host(restored['host']),C.unordered_host(old['host']))
            and C.same(sorted(restored['mounts'],key=lambda r:r['Destination']),sorted(old['mounts'],key=lambda r:r['Destination'])),'rollback_configuration')
        C.network_parity(backend.old_model,service,old,restored,stopped=True)
        active = backend.start(service,restored); C.normal_baseline(backend.rollback_model,service,active,backend.images[old['image']],backend.scope,rollback_paths,replacement=True)
        C.require(active['restarts'] == 0 and C.same(restored['config'],active['config'])
            and C.same(restored['host'],active['host']) and C.same(restored['mounts'],active['mounts']),'rollback_configuration')
        backend.unchanged(current['id'] if current else old['id'],active['id'])
        C.network_parity(backend.old_model,service,old,active,stopped=False); backend.functional(service,active)
    except BaseException:raise Unknown('rollback_unproven') from None
    backend.current = backend.inventory(); backend.role_ids[service] = active['id']
    if service in backend.completed:backend.completed.remove(service)
    backend.journal.record('rolled_back',service,active['id'])
    return {'site':backend.site,'service':service,'container_id':active['id'],'status':'rolled_back','security_accepted':False}
