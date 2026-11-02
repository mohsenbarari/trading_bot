#!/usr/bin/env python3
"""One fresh Web engine transaction; defaults to check, never reboots or recreates."""
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import re
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import time
from types import ModuleType, SimpleNamespace

OP = 'security-web-engine-20261024-08'
ROOT = Path('/root/.local/security-web-engine-20261024-task08')
SOURCE = ROOT/'source'
CONTROL = Path('/root/secure-envs/trading-bot/release-control')
MARKER = CONTROL.parent/'queue-cutover-artifacts/production-release.lock'
RESERVATION = CONTROL/'private-primary-controller.lock'
SOURCE_LOCK = CONTROL/(OP+'.source.lock')
JOURNAL = CONTROL/(OP+'.journal.json')
MANAGED_ENV = Path('/srv/trading-bot/market-pipeline-releases/security-backup-managed-20261024-04/web.release.env')
RESERVE, WAL_TEMP, CLEANUP = 8*1024**3, 4*1024**3, 120
RESTORE_RESERVE, PREINSTALL_MARGIN, MINIMUM_START = 1800, 2100, 3900
PHASES = ('reserved','quiescing','writers_quiesced','market_backed_up','quiesced','backing_up',
    'backup_ready','installing','installed','engines_verified','datastores','healthy','complete')
PINS = {
    'deploy/maintenance/web_engine_preflight.py':'cbadbc1085dd5e351510ed719a9e6c779523c4d644f53198d4f9c0c1d7750679',
    'deploy/maintenance/web_engine_controller.py':'81b8983cb0f790c95d0e51c3f3225bd60aa5eb8ef5037dd9c20f8dbc549e64b9',
    'deploy/maintenance/web_engine_current_profile.py':'e28f258a86da83b435392abc6b1d9d997137a54f8dd600ead61b36455da0ec6a',
    'deploy/maintenance/web_engine_cold_archive_plan.py':'3eba8e76147465ec23c1d5ee9fdca277e5678d0c3464ea11263169ec610fc93b',
    'deploy/maintenance/web_market_hot3.py':'00742f4d9370fc64883305ab3b6c5525cab8012d5a858c489b153ecc11ca4f76',
    'deploy/maintenance/web_engine_local_inventory.yml':'5099b628d981b7599c415e03b5194949764f04a318fa695a8c6adfc7efdfec82',
    'deploy/maintenance/ansible.cfg':'aac369e737bb5c125177785411b3389b988de9f56d97d76dbeaa2df06ef8806f',
    'scripts/validate_docker_daemon_maintenance.py':'fa2bf85cd50378bb999a7d8aabbd965a174722605c943c59ce6da56f68a74901',
    'scripts/prepare_market_pipeline_release.py':'6355aafa369b3abd652ff251a19fa9e329a8104bf9781ba197b1dc5e113677d5',
    'scripts/backup_market_pipeline_archive.py':'29b54303461aabfb1f7063aa59bb0347e37448db2712a673d5c54bf5ba6c4399',
    'scripts/crypt_market_pipeline_backup.py':'5a6f237eea9b042b1e51e0de6ea5dba827f66bb3707f667ce50f7955ee03a300',
}
OWN_SOURCES = ('deploy/maintenance/web_engine_current_controller.py','deploy/maintenance/web_engine_current_transaction.yml')
OWNED_CONTROLS=frozenset(('watch-ready','watch-errors','watch-cleanup','failure-requested','cleanup-unresolved'))
INSPECT = ('{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Image}},"running":{{json .State.Running}},'
    '"restarting":{{json .State.Restarting}},"restart_policy":{{json .HostConfig.RestartPolicy}},'
    '"project":{{json (index .Config.Labels "com.docker.compose.project")}},"mounts":{{json .Mounts}},'
    '"network_mode":{{json .HostConfig.NetworkMode}},"port_bindings":{{json .HostConfig.PortBindings}},'
    '"network_names":{{json .NetworkSettings.Networks}},"health":{{with index .State "Health"}}{{json .Status}}{{else}}null{{end}}}')


class Refusal(RuntimeError): pass
TRUSTED_ERRORS={Refusal}


def require(ok, code):
    if not ok: raise Refusal(code)


def stamp(text):
    require(type(text) is str and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z',text), 'current_deadline_shape')
    return datetime.fromisoformat(text.replace('Z','+00:00'))


def identity(info):
    return (info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_gid,info.st_nlink,info.st_size,info.st_mtime_ns,info.st_ctime_ns)


def read(path, maximum=2*1024**2, digest=None):
    require(path.is_absolute() and path.resolve(strict=True) == path, 'current_public_path')
    for parent in path.parents:
        info=parent.lstat();require(stat.S_ISDIR(info.st_mode) and info.st_uid==0 and not info.st_mode&0o022,'current_public_parent')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    try:
        before=os.fstat(fd)
        require(before.st_nlink!=0,'current_public_changed')
        require(stat.S_ISREG(before.st_mode) and before.st_uid==0 and before.st_nlink==1
            and stat.S_IMODE(before.st_mode)==0o600 and 0<before.st_size<=maximum,'current_public_metadata')
        raw=os.pread(fd,maximum+1,0)
        require(len(raw)==before.st_size and identity(before)==identity(os.fstat(fd))==identity(path.lstat()),'current_public_changed')
        require(digest is None or sha256(raw).hexdigest()==digest,'current_public_digest');return raw
    finally:os.close(fd)


def document(path,digest=None):
    def unique(pairs):
        value={}
        for key,item in pairs:require(key not in value,'current_duplicate_key');value[key]=item
        return value
    value=json.loads(read(path,digest=digest),object_pairs_hook=unique)
    require(type(value) is dict,'current_document_shape');return value


def load_recipe(project=SOURCE):
    raw={name:read(project/name,131072,digest) for name,digest in PINS.items()}
    for package in ('scripts','deploy','deploy.maintenance'):
        require(package not in sys.modules,'current_namespace_occupied')
        module=ModuleType(package);module.__path__=[];sys.modules[package]=module
    modules={}
    for name,fullname in (
        ('scripts/prepare_market_pipeline_release.py','scripts.prepare_market_pipeline_release'),
        ('scripts/backup_market_pipeline_archive.py','scripts.backup_market_pipeline_archive'),
        ('deploy/maintenance/web_engine_current_profile.py','deploy.maintenance.web_engine_current_profile'),
        ('deploy/maintenance/web_engine_cold_archive_plan.py','deploy.maintenance.web_engine_cold_archive_plan'),
        ('deploy/maintenance/web_engine_controller.py','_current_legacy_helpers'),
        ('deploy/maintenance/web_market_hot3.py','_current_backup_ownership')):
        module=ModuleType(fullname);module.__file__=str(project/name);module.__package__=fullname.rpartition('.')[0]
        sys.modules[fullname]=module;exec(compile(raw[name],str(project/name),'exec'),module.__dict__);modules[name]=module
    recipe=SimpleNamespace(C=modules['deploy/maintenance/web_engine_controller.py'],B=modules['scripts/backup_market_pipeline_archive.py'],
        R=modules['deploy/maintenance/web_engine_current_profile.py'],A=modules['deploy/maintenance/web_engine_cold_archive_plan.py'],
        H=modules['deploy/maintenance/web_market_hot3.py'])
    TRUSTED_ERRORS.update((recipe.C.P.Refusal,recipe.C.B.Refusal,recipe.C.StageLivenessError,recipe.B.BackupError,
        recipe.R.ProfileError,recipe.A.ColdPlanError,recipe.H.Refusal))
    return recipe


def checked_window(value, recipe, *, apply=False, now=None):
    keys={'schema','operation_id','nonce','not_before_utc','deadline_utc','approved_sources_sha256','profile_control_sha256',
        'engine_packages_allowed','reboot_allowed','kernel_package_changes_allowed','container_recreation_allowed',
        'writer_dns_changes_allowed','capture_owner_changes_allowed','automatic_downgrade_allowed'}
    require(type(value) is dict and set(value)==keys and value['schema']=='web_engine_current_window/1'
        and value['operation_id']==OP and type(value['nonce']) is str and re.fullmatch('[a-f0-9]{32}',value['nonce']), 'current_window_scope')
    require(value['engine_packages_allowed'] is True and all(value[k] is False for k in
        ('reboot_allowed','kernel_package_changes_allowed','container_recreation_allowed','writer_dns_changes_allowed',
         'capture_owner_changes_allowed','automatic_downgrade_allowed')), 'current_window_permissions')
    before,deadline=stamp(value['not_before_utc']),stamp(value['deadline_utc']);current=now or datetime.now(timezone.utc)
    require(0<(deadline-before).total_seconds()<=5400 and current<deadline,'current_window_expired_or_expanded')
    if apply:require(before<=current and (deadline-current).total_seconds()>=MINIMUM_START,'current_window_start_reserve')
    require(recipe.R.equal(value['profile_control_sha256'],recipe.R.CONTROL_SHA256),'current_profile_pins')
    pins=value['approved_sources_sha256'];require(type(pins) is dict and set(pins)==set(PINS)|set(OWN_SOURCES),'current_source_inventory')
    require(all(type(v) is str and re.fullmatch('[a-f0-9]{64}',v) for v in pins.values())
        and all(pins[k]==v for k,v in PINS.items()),'current_source_pins')
    return deadline


def work_root(nonce): return Path('/srv/trading-bot')/(OP+'-'+nonce)


def free(path):
    value=os.statvfs(path);return value.f_bavail*value.f_frsize


class Runtime:
    def __init__(self, recipe, plan, digest):
        self.recipe,self.plan,self.digest=recipe,plan,digest
        self.C,self.B,self.R,self.A,self.H=recipe.C,recipe.B,recipe.R,recipe.A,recipe.H
        self.profile=plan['current_profile'];self.deadline=stamp(plan['deadline_utc'])
        self.cutoff=self.deadline-timedelta(seconds=PREINSTALL_MARGIN)
        self.work=work_root(plan['nonce'])
        db=next(r for r in self.profile['containers'] if r['project']=='market-private-pipeline-primary' and r['name'].endswith('-market-database-1'))
        probe=next(p for p in self.profile['datastore_probes'] if p['id']==db['id'])
        self.intent={'nonce':plan['nonce'],'created_at_utc':plan['created_at_utc'],'deadline_utc':plan['deadline_utc'],
            'database':{'container_id':db['id'],'image':db['image'],'database':probe['database'],'user':probe['user']},
            'pg_application_name':'engine8-'+plan['nonce'],'restores':[self.restore_binding(plan['nonce'][:16])]}

    @staticmethod
    def restore_binding(nonce):
        return {'container':'market_pipeline_restore_'+nonce,'volume':'market_pipeline_restore_'+nonce+'_data','label':'market-pipeline-backup-'+nonce}

    def remaining(self,reserve=0):
        seconds=(self.deadline-datetime.now(timezone.utc)).total_seconds()
        require(seconds>reserve,'current_deadline_reserve');return seconds

    def command(self,argv,*,seconds=20,maximum=2*1024**2,stdin=None,**options):
        remaining=(self.deadline+timedelta(seconds=CLEANUP if getattr(self,'cleanup_actor',False) else 0)-datetime.now(timezone.utc)).total_seconds()
        require(remaining>0,'current_command_deadline')
        return self.C.bounded(argv,timeout=min(seconds,max(.01,remaining)),maximum=maximum,stdin=stdin,**options).encode()

    def state(self):
        def validate(value):
            require(value.get('schema')=='web_engine_current_transaction/1' and value.get('operation_id')==OP
                and value.get('nonce')==self.plan['nonce'] and value.get('manifest_sha256')==self.digest
                and type(value.get('phase')) is str and value['phase'] in {*PHASES,'aborted_restored'}
                and type(value.get('backup_requests_started')) is type(value.get('backup_requests_complete')) is bool,
                'current_journal_binding')
        return self.mutable_control(JOURNAL,validate)

    def mutable_control(self,path,validate):
        # C.write_json atomically replaces these owned mutable files. A retained
        # old FD can legitimately disagree with the new pathname once; retry
        # only that closed race, never a stable wrong owner or malformed body.
        require(path==JOURNAL or path in {self.work/(name+'.json') for name in OWNED_CONTROLS},'current_mutable_control_scope')
        until=time.monotonic()+.75
        for attempt in range(3):
            try:
                value=document(path);validate(value);return value
            except Refusal as error:
                if str(error)!='current_public_changed' or attempt==2 or time.monotonic()>=until:raise
                time.sleep(.025)

    def save(self,value,*,exclusive=False):self.C.write_json(JOURNAL,value,exclusive=exclusive)

    def advance(self,name,**evidence):
        value=self.state();require(name in PHASES and PHASES.index(name)==PHASES.index(value['phase'])+1,'current_phase_order')
        value.update(phase=name,**evidence);self.save(value);return value

    def controls(self):
        buffers={k:read(ROOT/'evidence'/(k+'.json'),digest=v) for k,v in self.R.CONTROL_SHA256.items()}
        require(self.R.equal(self.profile,self.R.derive_current_profile(buffers)),'current_profile_changed')
        return buffers

    def marker(self):
        marker=self.plan['marker'];info=MARKER.lstat()
        require((info.st_dev,info.st_ino)==(marker['device'],marker['inode'])
            and self.C.P.file_digest(MARKER,maximum=8192,private=True)==(marker['size'],marker['sha256']),'current_marker_changed')

    def owners(self):
        value=document(RESERVATION);source=document(SOURCE_LOCK)
        require(self.R.equal(value,source) and value.get('schema')=='web_engine_current_owner/1'
            and value.get('operation_id')==OP and value.get('nonce')==self.plan['nonce']
            and value.get('manifest_sha256')==self.digest and type(value.get('pid')) is int,'current_owner_binding')
        with Path('/proc/locks').open('rb') as stream:records=stream.read(2*1024**2+1)
        require(len(records)<=2*1024**2,'current_locks_bound')
        for path in (MARKER,SOURCE_LOCK,RESERVATION):
            info=path.lstat();key=f'{os.major(info.st_dev):02x}:{os.minor(info.st_dev):02x}:{info.st_ino}'
            require(any(len(f)>=8 and f[1:4]==['FLOCK','ADVISORY','WRITE'] and f[4]==str(value['pid']) and f[5]==key
                for line in records.decode('ascii').splitlines() for f in [line.split()]),'current_owner_flock_lost')

    def invariant(self,*,owned):
        self.remaining();self.C.native_contract(self.plan);buffers=self.controls();old=json.loads(buffers['c07_plan'])
        window=document(ROOT/'window.json',self.plan['window_sha256']);checked_window(window,self.recipe)
        require(window['nonce']==self.plan['nonce'] and window['deadline_utc']==self.plan['deadline_utc'],'current_window_binding')
        for name,digest in window['approved_sources_sha256'].items():read(SOURCE/name,131072,digest)
        self.marker()
        require(self.R.equal(self.plan['marker'],old['marker']) and self.R.equal(self.plan['units'],old['units'])
            and self.R.equal(self.plan['store_roots'],old['store_roots']) and self.R.equal(self.plan['engine_intent'],old['engine_intent'])
            and self.plan['backup_key_file']==old['backup_key_file'],'current_historical_metadata_binding')
        ansible=next(v for v in old['files'] if v['path']==old['ansible_binary'])
        require(self.plan['ansible_binary']==old['ansible_binary'] and self.R.equal(self.plan['ansible_binding'],ansible)
            and self.C.P.file_digest(ansible['path'],maximum=2*1024**2,private=False)==(ansible['size'],ansible['sha256']),
            'current_ansible_binding')
        require(not any(os.path.lexists(p) for p in self.plan['absent_control_paths']),'current_foreign_control')
        for entry in self.plan['store_roots']:self.C.store_root_contract(entry)
        self.parked_storage()
        for key,pins in (('old_packages',self.C.P.OLD_PACKAGES),('new_packages',self.C.P.NEW_PACKAGES)):
            require(self.R.equal(self.plan[key],old[key]),'current_package_path_binding')
            self.C.P.package_contract(self.plan[key],pins)
            for entry in self.plan[key]:
                require(self.C.P.file_digest(entry['path'],maximum=64*1024**2,private=False)==(entry['size'],entry['sha256']),'current_package_bytes')
                require(self.C.B.command(['/usr/bin/dpkg-deb','-f',entry['path'],'Package','Version','Architecture']).splitlines()
                    ==['Package: '+entry['name'],'Version: '+entry['version'],'Architecture: amd64'],'current_package_identity')
        require(self.plan['app_backup_receipt']==str(ROOT/'evidence/app-backup.json')
            and self.plan['app_offhost_receipt']==str(ROOT/'evidence/app-offhost.json')
            and {v['path'] for v in self.plan['files']}=={self.plan['app_backup_receipt'],self.plan['app_offhost_receipt']}
            and len(self.plan['files'])==2,'current_application_evidence_paths')
        for entry in self.plan['files']:
            require(self.C.P.file_digest(entry['path'],maximum=2*1024**2,private=True)==(entry['size'],entry['sha256']),'current_evidence_changed')
        self.C.backup_contract(self.plan)
        permitted=self.state().get('vendor_configs',{}) if owned and PHASES.index(self.state()['phase'])>=PHASES.index('installing') else {}
        for path in ('/etc/docker/daemon.json','/etc/containerd/config.toml'):
            if path in permitted:
                item=permitted[path];require(self.C.P.file_digest(path,maximum=65536,private=False)==(item['size'],item['sha256']),'current_vendor_config')
            else:require(not os.path.lexists(path),'current_foreign_runtime_config')
        for unit in self.plan['units']:
            actual=self.C.B.command(['/usr/bin/systemctl','show',unit['name'],'--property=FragmentPath,DropInPaths,UnitFileState'])
            values=dict(line.split('=',1) for line in actual.splitlines())
            require(values['FragmentPath']==unit['fragment_path'] and values['UnitFileState']==unit['enabled']
                and values['DropInPaths'].split()==[v['path'] for v in unit['dropins']],'current_native_definition')
            require(sha256(self.C.B.read(unit['fragment_path'],private=False)).hexdigest()==unit['fragment_sha256']
                and all(sha256(self.C.B.read(v['path'],private=False)).hexdigest()==v['sha256'] for v in unit['dropins']),'current_native_source')
        if owned:self.owners()
        else:require(not any(os.path.lexists(p) for p in (SOURCE_LOCK,RESERVATION,JOURNAL)),'current_previous_operation')

    def parked_storage(self,*,daemon=False):
        resources=self.profile['parked_resources']
        for item in [resources['parent_mount'],*[v['backing_identity'] for v in resources['volumes'].values()]]:
            path=Path(item['path']);require(path.is_absolute() and path.resolve(strict=True)==path,'current_parked_backing_redirected')
            info=path.lstat();observed={'path':str(path),'device':info.st_dev,'inode':info.st_ino,
                'uid':info.st_uid,'gid':info.st_gid,'mode':stat.S_IMODE(info.st_mode)}
            require(stat.S_ISDIR(info.st_mode) and self.R.equal(observed,{k:v for k,v in item.items() if k!='uuid'}),'current_parked_backing_changed')
        mount=resources['parent_mount']
        require(self.C.B.command(['/usr/bin/findmnt','-n','-o','TARGET','--mountpoint',mount['path']])==mount['path']
            and self.C.B.command(['/usr/bin/findmnt','-n','-o','UUID','--mountpoint',mount['path']])==mount['uuid'],'current_parked_mount_changed')
        for path,before in resources['secret_metadata'].items():
            require(list(identity(Path(path).lstat()))==before and not Path(path).is_symlink(),'current_parked_file_metadata_changed')
        if daemon:
            template='{"name":{{json .Name}},"driver":{{json .Driver}},"mountpoint":{{json .Mountpoint}},"options":{{json .Options}},"labels":{{json .Labels}}}'
            for name,before in resources['volumes'].items():
                require(self.R.equal(json.loads(self.command(['/usr/bin/docker','volume','inspect','--format',template,name])),before['record']),
                    'current_parked_volume_changed')

    def runtime(self,phase):
        require(phase in {'baseline','backup_datastores','quiesced','datastores','healthy','complete'},'current_runtime_phase')
        self.parked_storage(daemon=True)
        expected={r['id']:r for r in self.profile['containers']};stores=set(self.plan['datastore_ids']);incidents=[]
        require(set(self.C.B.command(['/usr/bin/docker','ps','-aq','--no-trunc']).splitlines())==set(expected),'current_container_inventory')
        self.C.P.store_contract(json.loads(self.C.B.command(['/usr/bin/docker','info','--format',
            '{"root":{{json .DockerRootDir}},"driver":{{json .Driver}},"driver_status":{{json .DriverStatus}},"live_restore":{{json .LiveRestoreEnabled}}}'])))
        require(json.loads(self.C.B.command(['/usr/bin/docker','info','--format',
            '{"driver":{{json .CgroupDriver}},"version":{{json .CgroupVersion}}}']))=={'driver':'systemd','version':'2'},'current_cgroup_runtime')
        for ident,before in expected.items():
            row=json.loads(self.C.B.command(['/usr/bin/docker','inspect','--format',INSPECT,ident]))
            require(row['restarting'] is False,'current_container_restarting')
            actual=self.R.canonical(row);wanted=dict(before)
            wanted['running']=before['running'] if phase in {'baseline','healthy','complete'} else ident in stores if phase in {'datastores','backup_datastores'} else False
            wanted['restart_policy']=before['restart_policy'] if phase in {'baseline','complete'} else {'Name':'no','MaximumRetryCount':0}
            require(self.R.equal(actual,wanted),'current_container_intent')
            if wanted['running'] and row['health'] not in {None,'healthy'}:
                require(ident in self.R.KNOWN_HEALTH,'current_unexpected_health_incident');incidents.append(ident)
        self.C.containerd_contract(self.plan,phase)
        for unit in self.plan['units']:
            state=self.C.B.command(['/usr/bin/systemctl','show',unit['name'],'-p','ActiveState','--value'])
            if phase in {'baseline','complete'}:
                if unit['kind']!='oneshot':require((state=='active')==unit['active'],'current_native_intent')
            else:require(state in {'inactive','failed'},'current_native_writer_running')
        if phase in {'datastores','backup_datastores','healthy','complete'}:
            for probe in self.plan['datastore_probes']:
                argv=['redis-cli','ping'] if probe['kind']=='redis' else ['pg_isready','-U',probe['user'],'-d',probe['database']]
                result=self.C.B.command(['/usr/bin/docker','exec',probe['id'],*argv])
                require(probe['kind']!='redis' or result=='PONG','current_datastore_not_ready')
        if phase in {'healthy','complete'}:
            self.C.patched_runtime()
            for probe in self.plan['health_probes']:
                require(self.C.B.command(['/usr/bin/docker','exec',probe['id'],'/usr/local/bin/python','-I','-B','-c',
                    self.C.B.CONTAINER_HTTP_PROBE,probe['url']])=='200','current_api_smoke')
        if phase in {'baseline','complete'}:
            for unit in self.plan['engine_intent']:
                require(self.C.B.command(['/usr/bin/systemctl','show',unit['name'],'-p','UnitFileState','--value'])==unit['enabled']
                    and (self.C.B.command(['/usr/bin/systemctl','show',unit['name'],'-p','ActiveState','--value'])=='active')==unit['active'],
                    'current_engine_enablement_intent')
        return incidents

    def offhost(self,mode,*,size=None,digest=None,stream=None):
        remote=document(ROOT/'transport.json',self.plan['transport_sha256'])
        require(set(remote)=={'schema','operation_id','nonce','source_host','destination_host','port','user','identity_file','identity_metadata',
            'known_hosts_file','known_hosts_sha256','expires_at_utc','receiver_source_sha256','guard_control_sha256','destination_receipt_sha256'},
            'current_transport_shape')
        require(remote['schema']=='web_engine_cold_transport/1' and remote['operation_id']==OP and remote['nonce']==self.plan['nonce']
            and remote['source_host']=='65.109.220.59' and remote['destination_host']=='65.109.214.203'
            and type(remote['port']) is int and remote['port']==22 and remote['user']=='root'
            and stamp(remote['expires_at_utc'])>=self.deadline
            and remote['receiver_source_sha256']=='9ecf85e2b321cab1de2dba06e121dd452825d8abdb479aa318b5b01a7237e9de'
            and remote['destination_receipt_sha256']==self.plan['destination_sha256'],'current_transport_binding')
        key=Path(remote['identity_file']);self.C.P.safe_parent(key);info=key.lstat()
        require(key==Path('/root/.ssh/security-web-engine-cold-20261024-08/id_ed25519')
            and remote['known_hosts_file']==str(key.parent/'known_hosts')
            and all(type(remote[k]) is str and re.fullmatch('[a-f0-9]{64}',remote[k]) for k in ('known_hosts_sha256','guard_control_sha256'))
            and key.resolve(strict=True)==key
            and stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode)==0o600 and info.st_uid==0 and info.st_nlink==1
            and list(identity(info))==remote['identity_metadata'],'current_transport_key_metadata')
        require(self.C.P.file_digest(remote['known_hosts_file'],maximum=16384,private=True)[1]==remote['known_hosts_sha256'],'current_transport_host_pin')
        args=['engine-cold',mode,'--operation',OP,'--nonce',self.plan['nonce'],'--manifest-sha256',self.digest,'--deadline-utc',self.plan['deadline_utc']]
        if mode!='capacity':args+=['--cipher-size',str(size),'--cipher-sha256',digest]
        argv=['/usr/bin/ssh','-F','/dev/null','-i',str(key),'-p','22','-o','IdentitiesOnly=yes','-o','BatchMode=yes',
            '-o','StrictHostKeyChecking=yes','-o','UpdateHostKeys=no','-o','ForwardAgent=no','-o','ConnectTimeout=10',
            '-o','ControlMaster=no','-S','none','-o','UserKnownHostsFile='+remote['known_hosts_file'],'root@65.109.214.203',shlex.join(args)]
        result=json.loads(self.command(argv,seconds=900,stdin=stream))
        require(identity(key.lstat())==identity(info),'current_transport_key_metadata')
        document(ROOT/'transport.json',self.plan['transport_sha256'])
        require(result['status']=={'capacity':'capacity_verified','receive':'received','verify':'verified'}[mode]
            and result['operation_id']==OP and result['nonce']==self.plan['nonce'] and result['manifest_sha256']==self.digest
            and result['deadline_utc']==self.plan['deadline_utc'],'current_offhost_binding')
        return result

    def apt_argv(self,*,simulation):
        return ['/usr/bin/apt-get',*(['--simulate','--no-download'] if simulation else ['--yes']),
            '-o','Dir::Cache::pkgcache=','-o','Dir::Cache::srcpkgcache=',
            '-o','Dir::Etc::sourcelist=/dev/null','-o','Dir::Etc::sourceparts=-',
            '-o','Dir::State::lists='+str(self.work/'apt/lists'),'-o','Dir::Cache::archives='+str(self.work/'apt/archives'),
            '--no-install-recommends','install',*[p['path'] for p in self.plan['new_packages']],*[name+'-' for name in self.C.P.OLD_PACKAGES]]

    def pg_rows(self):return self.H.Runtime.pg_rows(self)
    def stop_pg(self,rows):return self.H.Runtime.stop_pg(self,rows)
    def restore_resources(self,*,remove=False):return self.H.Runtime.restore_resources(self,remove=remove)

    def limits(self,dump_cap):
        require(datetime.now(timezone.utc)<self.cutoff,'current_backup_cutoff')
        last=[-1.]
        def checkpoint():
            self.remaining(PREINSTALL_MARGIN);require(free(self.work)>=RESERVE,'current_disk_reserve')
            if time.monotonic()-last[0]>=1:
                self.marker();self.owners();self.assert_watchdog();last[0]=time.monotonic()
        return self.B.BackupExecutionLimits(metadata_timeout_seconds=60,dump_timeout_seconds=1800,restore_timeout_seconds=1800,
            dump_max_bytes=dump_cap,minimum_free_bytes=RESERVE,pg_application_name=self.intent['pg_application_name'],
            restore_cpus=2,restore_memory_bytes=2*1024**3,restore_pids_limit=256,
            daemon_deadline_epoch_seconds=int(self.cutoff.timestamp()),checkpoint=checkpoint)

    def common_backup(self):
        return dict(env_file=MANAGED_ENV,release_sha=self.profile['configuration_source']['release_sha'],
            release_tree=self.profile['configuration_source']['release_tree'],image_id=self.profile['source_image']['image_id'],
            image_input_signature=self.profile['source_image']['image_input_signature'])

    def market_backup(self):
        self.invariant(owned=True);require(self.state()['phase']=='writers_quiesced','current_backup_phase')
        self.assert_watchdog()
        self.runtime('backup_datastores');db=self.intent['database']
        sql="SET statement_timeout='30000ms'; SELECT pg_database_size(current_database())"
        size=int(self.command(['/usr/bin/docker','exec',db['container_id'],'psql','-X','-q','-v','ON_ERROR_STOP=1',
            '-At','-U',db['user'],'-d',db['database'],'-c',sql],seconds=60).decode().strip())
        cap=min(10*1024**3,free(self.work)-2*size-WAL_TEMP-RESERVE)
        require(cap>0,'current_backup_capacity_insufficient')
        state=self.state();state.update(backup_requests_started=True,backup_requests_complete=False,
            backup_daemon_deadline=int(self.cutoff.timestamp()),backup_source_bytes=size,dump_max_bytes=cap);self.save(state)
        folder=self.work/'market';receipt=folder/'market-pipeline-backup-receipt.json';limits=self.limits(cap)
        created=self.B.create_quiesced_primary_backup(**self.common_backup(),backup_dir=folder,receipt=receipt,
            execution_limits=limits,restore_resource_binding=self.intent['restores'][0])
        receipt_sha=self.B.file_digest(receipt,execution_limits=limits)
        journal_sha=self.B.file_digest(self.B._journal_path(folder),execution_limits=limits)
        verified=self.B.verify_quiesced_primary_receipt(**self.common_backup(),receipt=receipt,
            receipt_sha256=receipt_sha,journal_sha256=journal_sha,maximum_age_seconds=7200,execution_limits=limits)
        require(self.R.equal(created,verified) and created['status']=='PASS' and created['backup_mode']=='quiesced'
            and created['restore_smoke']['cleanup_status']=='PASS' and self.restore_resources() and not self.pg_rows(),
            'current_backup_cleanup_or_parity')
        artifact=created['backup']
        fresh={'schema':'web_engine_fresh_quiesced_operand/1','operation_id':OP,'nonce':self.plan['nonce'],'backup_mode':'quiesced',
            **{k:created[k] for k in ('release_sha','release_tree','image_id','image_input_signature')},
            **{k:artifact[k] for k in ('path','size_bytes','sha256')},'receipt_sha256':receipt_sha,'journal_sha256':journal_sha,
            'restore_status':created['restore_smoke']['status'],'cleanup_status':created['restore_smoke']['cleanup_status']}
        self.A.fresh_operand(fresh,self.profile)
        self.runtime('backup_datastores')
        self.advance('market_backed_up',fresh_quiesced_backup=fresh,backup_requests_complete=True)

    def verified_fresh_dump(self):
        state=self.state();fresh=self.A.fresh_operand(state['fresh_quiesced_backup'],self.profile)
        require(fresh['nonce']==self.plan['nonce'],'current_fresh_backup_nonce')
        receipt=self.work/'market/market-pipeline-backup-receipt.json'
        # After the engines stop, verify the already completed quiesced controls
        # and retained artifact; do not attempt a query against a stopped DB.
        payload=document(receipt,fresh['receipt_sha256'])
        document(self.B._journal_path(receipt.parent),fresh['journal_sha256'])
        require(payload['backup_mode']=='quiesced' and payload['status']=='PASS'
            and self.R.equal(payload['backup'],{'format':'postgres_custom',**{k:fresh[k] for k in ('path','size_bytes','sha256')}})
            and payload['restore_smoke']['cleanup_status']=='PASS','current_fresh_backup_controls')
        require(self.B.file_digest(Path(fresh['path']))==fresh['sha256'],'current_fresh_backup_bytes')
        age=(datetime.now(timezone.utc)-stamp(payload['created_at_utc'])).total_seconds()
        require(0<=age<=7200,'current_fresh_backup_stale');return fresh

    def archive_plan(self,fresh=None):
        destination=read(ROOT/'destination.json',65536,self.plan['destination_sha256'])
        inputs=list(self.A.COLD_INPUTS)+[p for p in self.A.OPTIONAL_INPUTS if os.path.lexists(p)]
        if fresh is not None:inputs.append(fresh['path'])
        lines=self.C.B.command(['/usr/bin/du','-sx','--block-size=1',*inputs]).splitlines()
        require(len(lines)==len(inputs),'current_cold_size_inventory');sizes={}
        for line,path in zip(lines,inputs):
            value,name=line.split(None,1);require(name==path and value.isdigit(),'current_cold_size_shape');sizes[path]=int(value)
        capacity=self.offhost('capacity')
        require(type(capacity['free_bytes']) is int and capacity['reserve_bytes']==RESERVE
            and capacity['control_allowance_bytes']==self.A.CONTROL_ALLOWANCE,'current_cold_capacity_shape')
        return self.A.build_cold_archive_plan(self.profile,self.controls(),input_sizes=sizes,web_free_bytes=free(self.work.parent),
            offhost_free_bytes=capacity['free_bytes'],destination_receipt=destination,
            destination_receipt_sha256=self.plan['destination_sha256'],fresh_quiesced_backup=fresh)

    def cold_backup(self):
        self.invariant(owned=True);require(self.state()['phase']=='quiesced','current_cold_phase')
        self.assert_watchdog()
        self.C.engine_stopped();self.no_native_jobs()
        fresh=self.verified_fresh_dump();budget=self.archive_plan(fresh)
        require(budget['fresh_quiesced_dump_included'] is True,'current_fresh_dump_required')
        self.advance('backing_up',cold_plan=budget)
        folder=self.work/'archive';os.mkdir(folder,0o700)
        plain,cipher,receipt=folder/'engine.tar.gz',folder/'engine.enc',folder/'encryption.json'
        cap=budget['capacity']['archive_max_bytes'];inputs=budget['cold_archive_inputs']
        fd=os.open(plain,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        try:
            with self.B._pinned_backup_artifact(Path(fresh['path']),fresh):
                self.C.bounded(['/usr/bin/tar','--acls','--xattrs','--numeric-owner','--sparse','--one-file-system','--gzip',
                    '--create','--file=-','--directory=/',*[p.lstrip('/') for p in inputs]],
                    timeout=min(900,int(self.remaining(PREINSTALL_MARGIN))),stdout=fd,maximum=cap)
                os.fsync(fd)
        finally:os.close(fd)
        self.invariant(owned=True);self.C.engine_stopped();self.no_native_jobs()
        compared={'size_bytes':plain.stat().st_size,'sha256':self.B.file_digest(plain)}
        with self.B._pinned_backup_artifact(plain,compared):
            self.command(['/usr/bin/tar','--acls','--xattrs','--numeric-owner','--gzip','--compare','--file='+str(plain),
                '--directory=/'],seconds=900)
            self.command(['/usr/bin/python3','-I','-B',str(SOURCE/'scripts/crypt_market_pipeline_backup.py'),'encrypt',
                '--source',str(plain),'--destination',str(cipher),'--key-file',self.plan['backup_key_file'],
                '--receipt',str(receipt),'--confirm','encrypt-production-market-pipeline-offhost-backup'],seconds=900,
                artifact_maximum=budget['capacity']['ciphertext_max_bytes'])
            crypt=document(receipt);require(crypt['schema']=='market_pipeline_backup_encryption/1.1'
                and crypt['plaintext_materialized_offhost'] is False and crypt['secrets_disclosed'] is False,
                'current_crypt_contract')
            require(crypt['plaintext_size_bytes']==compared['size_bytes'] and crypt['plaintext_sha256']==compared['sha256'],
                'current_crypt_plaintext_reconciliation')
        size,digest=crypt['ciphertext_size_bytes'],crypt['ciphertext_sha256']
        capacity=self.offhost('capacity')
        require(type(size) is int and 0<size<=budget['capacity']['ciphertext_max_bytes']
            and capacity['max_cipher_bytes']>=size and free(self.work)>=RESERVE,'current_actual_cipher_capacity')
        with self.B._pinned_backup_artifact(cipher,{'size_bytes':size,'sha256':digest}) as retained:
            with os.fdopen(os.dup(retained),'rb') as stream:
                stream.seek(0);received=self.offhost('receive',size=size,digest=digest,stream=stream)
            remote=self.offhost('verify',size=size,digest=digest)
        for proof in (received,remote):
            require(proof['fsync_completed'] is True and proof['plaintext_materialized_offhost'] is False
                and proof['ciphertext_size_bytes']==size and proof['ciphertext_sha256']==digest,'current_offhost_durability')
        self.invariant(owned=True);self.C.engine_stopped();self.remaining(PREINSTALL_MARGIN)
        self.advance('backup_ready',encryption=crypt,offhost=remote,cold_archive_compared=True,
            fresh_dump_in_cold_archive=True,redundant_plaintext_retained=True)

    def install(self):
        self.invariant(owned=True);self.C.engine_stopped();require(self.state()['phase']=='backup_ready','current_install_phase')
        self.assert_watchdog()
        state=self.state();self.verified_fresh_dump();crypt=state['encryption'];cipher=self.work/'archive/engine.enc'
        require(self.B.file_digest(cipher)==crypt['ciphertext_sha256'],'current_cold_cipher_changed')
        remote=self.offhost('verify',size=crypt['ciphertext_size_bytes'],digest=crypt['ciphertext_sha256'])
        require(remote['fsync_completed'] is True and remote['ciphertext_sha256']==crypt['ciphertext_sha256'],'current_offhost_reverify')
        self.C.apt_scope(self.C.B.command(self.apt_argv(simulation=True)))
        require(not os.path.lexists('/usr/sbin/policy-rc.d'),'current_foreign_service_policy')
        configs=self.C.vendor_configs(self.plan);self.remaining(RESTORE_RESERVE)
        self.advance('installing',vendor_configs=configs)
        policy=Path('/usr/sbin/policy-rc.d');fd=os.open(policy,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o755)
        try:os.fchmod(fd,0o755);self.C.B.write_all(fd,b'#!/bin/sh\nexit 101\n');os.fsync(fd);owned=identity(os.fstat(fd))
        finally:os.close(fd)
        try:self.command(self.apt_argv(simulation=False),seconds=240,artifact_maximum=512*1024**2,
            extra_env={'NEEDRESTART_MODE':'l','DEBIAN_FRONTEND':'noninteractive'})
        finally:
            require(identity(policy.lstat())==owned and self.C.B.read(policy,private=False)==b'#!/bin/sh\nexit 101\n','current_service_policy_changed')
            policy.unlink();parent=os.open(policy.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:os.fsync(parent)
            finally:os.close(parent)
        require(self.C.package_versions(self.C.P.NEW_PACKAGES)=={k:v[0] for k,v in self.C.P.NEW_PACKAGES.items()},'current_installed_versions')
        self.invariant(owned=True);self.C.engine_stopped();self.advance('installed')

    def checkpoint(self,name):
        self.invariant(owned=True)
        phase={'quiescing':'baseline','writers_quiesced':'backup_datastores','quiesced':'quiesced','engines_verified':'quiesced',
            'datastores':'datastores','healthy':'healthy','complete':'complete'}[name]
        if name=='engines_verified':self.C.patched_runtime()
        incidents=self.runtime(phase)
        if self.state()['phase']!=name:
            self.advance(name,known_health_incidents=incidents,known_health_incidents_status='open',security_accepted=False,
                deployment_eligible=False,reboot_performed=False)

    def no_native_jobs(self):
        for unit in sorted(self.C.UNITS|set(self.C.ENGINES)):
            raw=self.C.B.command(['/usr/bin/systemctl','show',unit,'-p','LoadState','-p','Job'])
            require(set(raw.splitlines()) in ({'LoadState=loaded','Job='},{'LoadState=loaded','Job=0'}),'current_native_job_pending')

    def scope(self,stage,*,stop=False):
        unit='security-web-engine-'+self.plan['nonce']+'-'+stage+'.scope';group=Path('/sys/fs/cgroup/system.slice')/unit
        values=dict(line.split('=',1) for line in self.command(['/usr/bin/systemctl','show',unit,
            '--property=LoadState,Description,Transient,ActiveState,KillMode,ControlGroup,Slice']).decode().splitlines())
        if values.get('LoadState')=='not-found':require(not os.path.lexists(group),'current_stage_descendants');return True
        require(values.get('Description')==':'.join(('web-engine',OP,self.plan['nonce'],self.digest,stage))
            and values.get('Transient')=='yes' and values.get('KillMode')=='control-group' and values.get('Slice')=='system.slice'
            and values.get('ControlGroup') in {'','/system.slice/'+unit},'current_foreign_stage')
        if stop and values.get('ActiveState') not in {'inactive','failed'}:self.command(['/usr/bin/systemctl','stop',unit])
        return self.C.scope_empty(group)

    def watch_alive(self):
        unit='security-web-engine-'+self.plan['nonce']+'-backup-watch.scope'
        value=dict(line.split('=',1) for line in self.C.B.command(['/usr/bin/systemctl','show',unit,
            '--property=Description,ActiveState,Transient']).splitlines())
        return value.get('Description')=='current-engine-watch:'+OP+':'+self.plan['nonce']+':'+self.digest \
            and value.get('ActiveState')=='active' and value.get('Transient')=='yes'

    def assert_watchdog(self):
        require(self.watch_alive() and self.proof('watch-ready') is not None and self.proof('watch-errors') is None
            and self.proof('watch-cleanup') is None,'current_watchdog_not_healthy')

    def proof(self,name):
        require(name in OWNED_CONTROLS,'current_mutable_control_scope')
        path=self.work/(name+'.json')
        if not os.path.lexists(path):return None
        def validate(value):
            require(value.get('schema')=='web_engine_current_owned_control/1' and value.get('operation_id')==OP
                and value.get('control_name')==name and value.get('manifest_sha256')==self.digest
                and value.get('nonce')==self.plan['nonce'],'current_owned_control_changed')
        return self.mutable_control(path,validate)

    def record(self,name,**values):
        require(name in OWNED_CONTROLS,'current_mutable_control_scope')
        path=self.work/(name+'.json')
        exists=os.path.lexists(path)
        if exists:self.proof(name)
        self.C.write_json(path,{'schema':'web_engine_current_owned_control/1','operation_id':OP,'control_name':name,
            'manifest_sha256':self.digest,'nonce':self.plan['nonce'],**values},exclusive=not exists)

    def source_provenance(self):
        labels='{'+','.join(json.dumps(k)+':{{json (index .Config.Labels '+json.dumps(k)+')}}' for k in self.R.FUNCTIONAL_LABELS)+'}'
        template='{"id":{{json .Id}},"layers":{{json .RootFS.Layers}},"release_sha":{{json (index .Config.Labels "org.opencontainers.image.revision")}},"release_tree":{{json (index .Config.Labels "io.gold-trade.release.tree")}},"image_input_signature":{{json (index .Config.Labels "io.gold-trade.release.input-signature")}},"functional":'+labels+'}'
        images={key:json.loads(self.command(['/usr/bin/docker','image','inspect','--format',template,key]))
            for key in (self.R.IMAGE['image_id'],self.R.BASE,self.R.FUNCTIONAL)}
        require(all(v['id']==k for k,v in images.items()),'current_image_identity')
        require(self.R.equal({k:images[self.R.IMAGE['image_id']][k] for k in self.R.IMAGE if k!='image_id'},
            {k:v for k,v in self.R.IMAGE.items() if k!='image_id'}),'current_oci_provenance')
        base,derived=images[self.R.BASE]['layers'],images[self.R.FUNCTIONAL]['layers']
        require(type(base) is list and base and type(derived) is list and len(derived)==len(base)+2 and derived[:len(base)]==base
            and self.R.equal(images[self.R.FUNCTIONAL]['functional'],self.R.FUNCTIONAL_LABELS),'current_functional_lineage')

    def preflight(self,*,owned=False):
        self.invariant(owned=owned);self.runtime('baseline');self.source_provenance()
        require(self.C.package_versions(self.C.P.OLD_PACKAGES)=={k:v[0] for k,v in self.C.P.OLD_PACKAGES.items()},'current_old_packages')
        require(not os.path.lexists('/usr/sbin/policy-rc.d'),'current_foreign_service_policy')
        values=self.B.validate_release_env(MANAGED_ENV,release_sha=self.R.CONFIGURATION['release_sha'],
            image_id=self.R.IMAGE['image_id'],allow_target_identity_mismatch=False,allow_private_primary_source=True)
        require(values.get('MARKET_POSTGRES_USER','market_data')==self.intent['database']['user']
            and values.get('MARKET_POSTGRES_DB','market_archive')==self.intent['database']['database'],'current_managed_database_binding')
        del values
        document(MANAGED_ENV.parent/'web-managed-receipt.json',self.R.MANAGED_RECEIPT)
        require(re.fullmatch('sha256:[a-f0-9]{64}',self.C.B.command(['/usr/bin/docker','image','inspect','--format','{{.Id}}',self.B.POSTGRES_IMAGE])),
            'current_restore_image_unavailable')
        self.C.apt_scope(self.C.B.command(self.apt_argv(simulation=True)))
        self.archive_plan()

    def recover_before_install(self):
        self.invariant(owned=True);state=self.state();self.remaining(60)
        require(state['phase'] in {'reserved','backing_up','backup_ready'} and state.get('backup_requests_complete') is True
            and self.proof('watch-cleanup') is not None,'current_recovery_requires_review')
        require(self.C.package_versions(self.C.P.OLD_PACKAGES)=={k:v[0] for k,v in self.C.P.OLD_PACKAGES.items()},'current_recovery_package_change')
        require(all(self.scope(s) for s in ('pre_install','install_restore')),'current_recovery_descendants')
        self.no_native_jobs()
        if state['phase']=='reserved':
            self.runtime('baseline');state.update(phase='aborted_restored',aborted_from='reserved',security_transition_completed=False)
            self.save(state);return
        for name in ('containerd.service','docker.socket','docker.service'):
            before=next(v for v in self.plan['engine_intent'] if v['name']==name)
            if before['active']:self.command(['/usr/bin/systemctl','start',name])
        # Revalidate every immutable ID/mount/image before restoring writers.
        expected={r['id']:r for r in self.profile['containers']}
        require(set(self.C.B.command(['/usr/bin/docker','ps','-aq','--no-trunc']).splitlines())==set(expected),'current_recovery_inventory')
        for ident,before in expected.items():
            row=json.loads(self.command(['/usr/bin/docker','inspect','--format',INSPECT,ident]));actual=self.R.canonical(row)
            wanted=dict(before);wanted.update(running=actual['running'],restart_policy=actual['restart_policy'])
            require(self.R.equal(actual,wanted) and (not actual['running'] or before['running']) and row['restarting'] is False
                and actual['restart_policy'] in [before['restart_policy'],{'Name':'no','MaximumRetryCount':0}],'current_recovery_foreign_runtime')
        self.source_provenance()
        for ident in self.plan['datastore_ids']:self.command(['/usr/bin/docker','start',ident])
        for probe in self.plan['datastore_probes']:
            for attempt in range(30):
                try:
                    argv=['redis-cli','ping'] if probe['kind']=='redis' else ['pg_isready','-U',probe['user'],'-d',probe['database']]
                    result=self.command(['/usr/bin/docker','exec',probe['id'],*argv]).decode().strip()
                    require(probe['kind']!='redis' or result=='PONG','current_recovery_database');break
                except Exception:
                    if attempt==29:raise
                    self.remaining(30);time.sleep(2)
        for row in self.profile['containers']:
            if row['running'] and row['id'] not in self.plan['datastore_ids']:self.command(['/usr/bin/docker','start',row['id']])
        for row in self.profile['containers']:
            policy=row['restart_policy'];label=policy['Name']+(':'+str(policy['MaximumRetryCount']) if policy['Name']=='on-failure' and policy['MaximumRetryCount']>0 else '')
            self.command(['/usr/bin/docker','update','--restart='+label,row['id']])
        for timer in (False,True):
            for unit in self.plan['units']:
                if unit['active'] and unit['kind']!='oneshot' and (unit['kind']=='timer')==timer:self.command(['/usr/bin/systemctl','start',unit['name']])
        self.invariant(owned=True);self.runtime('baseline')
        state.update(phase='aborted_restored',aborted_from=state['phase'],security_transition_completed=False);self.save(state)


def parent_start(pid):
    raw=Path('/proc')/str(pid)/'stat'
    with raw.open('rb') as stream:data=stream.read(8193)
    require(len(data)<=8192 and b') ' in data,'current_parent_identity')
    return data.rsplit(b') ',1)[1].split()[19].decode('ascii')


def check_descriptors(rt,descriptors):
    require(len(descriptors)==3 and len(set(descriptors))==3,'current_descriptor_scope')
    for fd,path in zip(descriptors,(MARKER,SOURCE_LOCK,RESERVATION)):
        before=os.fstat(fd);require(identity(before)==identity(path.lstat()) and stat.S_ISREG(before.st_mode)
            and before.st_uid==0 and before.st_nlink==1 and stat.S_IMODE(before.st_mode)==0o600,'current_descriptor_changed')
        raw=os.pread(fd,8193,0);require(len(raw)==before.st_size<=8192,'current_descriptor_bound')
        if path==MARKER:require(sha256(raw).hexdigest()==rt.plan['marker']['sha256'],'current_marker_changed')
        else:
            value=json.loads(raw);require(value.get('manifest_sha256')==rt.digest and value.get('nonce')==rt.plan['nonce'],
                'current_descriptor_owner')


def watch(rt,descriptors,*,pause=time.sleep):
    """Survive parent/client death; unknown creation outcomes never release fences."""
    rt.cleanup_actor=True;check_descriptors(rt,descriptors);rt.record('watch-ready',marker_retained=True)
    cancelled=False;unknown=False;foreign=False;errors=0
    end=rt.deadline.timestamp()+CLEANUP
    monotonic_end=time.monotonic()+max(0,end-time.time())
    def attempt(callback):
        nonlocal cancelled,foreign,errors
        try:return callback()
        except Exception as error:
            cancelled=True;errors+=1
            code=safe_reason(error)
            if code.endswith(('owner','changed','foreign_stage','descriptor_scope')) or code in {
                'current_journal_binding','current_descriptor_bound','hot3_restore_inventory','hot3_pg_inventory'}:foreign=True
            rt.record('watch-errors',errors=errors,reason_code=safe_reason(error),ownership_refused=foreign)
            return None
    while time.time()<end and time.monotonic()<monotonic_end:
        attempt(lambda:check_descriptors(rt,descriptors));state=attempt(rt.state)
        failure=attempt(lambda:rt.proof('failure-requested'))
        try:alive=parent_start(rt.plan['parent_pid'])==rt.plan['parent_start_ticks']
        except (OSError,Refusal):alive=False
        capacity=attempt(lambda:free(rt.work))
        if failure is not None or not alive or time.time()>=rt.deadline.timestamp() or capacity is None or capacity<RESERVE:cancelled=True
        if state is None:
            # Missing metadata cannot prove that no daemon request started.
            # Stop only our scopes, retain M and retry; never infer an empty
            # creation phase or successful cleanup from a failed journal read.
            for stage in ('pre_install','install_restore'):attempt(lambda s=stage:rt.scope(s,stop=True))
            pause(.5);continue
        active=state.get('backup_requests_started') is True and state.get('backup_requests_complete') is not True
        rows=attempt(rt.pg_rows) if active else []
        resources=attempt(rt.restore_resources) if active else True
        if cancelled:
            if active:unknown=True
            scopes=[attempt(lambda s=s:rt.scope(s,stop=True)) for s in ('pre_install','install_restore')]
            if active and rows is not None:attempt(lambda:rt.stop_pg(rows))
            if active:attempt(lambda:rt.restore_resources(remove=True))
            if alive and not unknown and not foreign and rows==[] and resources is True and all(v is True for v in scopes):
                rt.record('watch-cleanup',all_owned_resources_absent=True,worker_succeeded=False,marker_retained=True);return False
        elif state.get('phase') in {'complete','aborted_restored'} and all(attempt(lambda s=s:rt.scope(s)) is True for s in ('pre_install','install_restore')):
            rt.record('watch-cleanup',all_owned_resources_absent=True,worker_succeeded=state['phase']=='complete',marker_retained=True);return True
        pause(.5)
    rt.record('cleanup-unresolved',daemon_request_completion_proven=False,manual_recovery_required=True,
        automatic_retry_or_adoption_permitted=False,reason_code='current_daemon_creation_unknown' if unknown else 'current_cleanup_unproven')
    raise Refusal('current_cleanup_unproven')


def safe_reason(error):
    text=str(error)
    if type(error) in TRUSTED_ERRORS and text=='native_command_failed_apt-get':return 'native_command_failed_apt_get'
    if type(error) in TRUSTED_ERRORS and re.fullmatch('[a-z][a-z0-9_]{0,79}',text):return text
    known={OSError:'oserror',ValueError:'valueerror',KeyError:'keyerror',TypeError:'typeerror',TimeoutError:'timeouterror',
        subprocess.TimeoutExpired:'timeoutexpired',KeyboardInterrupt:'interrupted'}
    return 'current_'+known[type(error)]+'_unknown' if type(error) in known else 'current_unexpected_failure'


def build_plan(recipe,window_raw,*,transport_sha256,destination_sha256,app_sha256,app_offhost_sha256,now=None):
    window=json.loads(window_raw);checked_window(window,recipe,now=now)
    buffers={k:read(ROOT/'evidence'/(k+'.json'),digest=v) for k,v in recipe.R.CONTROL_SHA256.items()}
    old=json.loads(buffers['c07_plan']);profile=recipe.R.derive_current_profile(buffers)
    require(all(type(v) is str and re.fullmatch('[a-f0-9]{64}',v) for v in
        (transport_sha256,destination_sha256,app_sha256,app_offhost_sha256)),'current_input_digest_shape')
    plan={k:old[k] for k in ('machine_id','marker','units','store_roots','engine_intent','backup_key_file','old_packages','new_packages','ansible_binary')}
    plan['ansible_binding']=next(v for v in old['files'] if v['path']==old['ansible_binary'])
    plan.update(schema='web_engine_current_plan/1',operation_id=OP,nonce=window['nonce'],deadline_utc=window['deadline_utc'],
        created_at_utc=(now or datetime.now(timezone.utc)).isoformat().replace('+00:00','Z'),window_sha256=sha256(window_raw).hexdigest(),
        source_root=str(SOURCE),backup_root=str(work_root(window['nonce'])/'archive'),current_profile=profile,containers=profile['containers'],
        datastore_probes=profile['datastore_probes'],datastore_ids=[p['id'] for p in profile['datastore_probes']],
        health_probes=old['health_probes'],transport_sha256=transport_sha256,destination_sha256=destination_sha256,
        absent_control_paths=[*old['absent_control_paths'],str(recipe.C.P.PENDING),str(CONTROL/'security-web-engine-20261023-07.source.lock')],
        app_backup_receipt=str(ROOT/'evidence/app-backup.json'),app_offhost_receipt=str(ROOT/'evidence/app-offhost.json'),files=[])
    for name,digest in (('app-backup.json',app_sha256),('app-offhost.json',app_offhost_sha256)):
        path=ROOT/'evidence'/name;raw=read(path,digest=digest);plan['files'].append({'path':str(path),'size':len(raw),'sha256':digest})
    return plan


def checked_plan(recipe,path,digest):
    require(path==ROOT/'plan.json' and type(digest) is str and re.fullmatch('[a-f0-9]{64}',digest),'current_manifest_scope')
    value=document(path,digest);window_raw=read(ROOT/'window.json',digest=value['window_sha256'])
    expected=build_plan(recipe,window_raw,transport_sha256=value['transport_sha256'],destination_sha256=value['destination_sha256'],
        app_sha256=value['files'][0]['sha256'],app_offhost_sha256=value['files'][1]['sha256'],now=stamp(value['created_at_utc']))
    require(recipe.R.equal(value,expected),'current_manifest_contract');checked_window(json.loads(window_raw),recipe)
    return value


def make_work(rt):
    rt.C.B.private_parent(rt.work)
    os.mkdir(rt.work,0o700)
    for suffix in ('market','apt','apt/lists','apt/archives'):
        os.mkdir(rt.work/suffix,0o700)
    parent=os.open(rt.work.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(parent)
    finally:os.close(parent)


def launch_watch(rt,descriptors,path):
    unit='security-web-engine-'+rt.plan['nonce']+'-backup-watch.scope'
    require(rt.C.B.command(['/usr/bin/systemctl','show',unit,'-p','LoadState','--value'])=='not-found','current_previous_watchdog')
    seconds=max(1,int(rt.remaining()))+CLEANUP+10
    argv=['/usr/bin/systemd-run','--scope','--system','--quiet','--no-ask-password','--expand-environment=no','--slice=system.slice',
        '--unit='+unit,'--description=current-engine-watch:'+OP+':'+rt.plan['nonce']+':'+rt.digest,
        '--property=KillMode=control-group','--property=SendSIGKILL=yes','--property=TimeoutStopSec=10s','--property=RuntimeMaxSec='+str(seconds),
        '--','/usr/bin/python3','-I','-B',str(SOURCE/OWN_SOURCES[0]),'watch','--manifest',str(path),'--manifest-sha256',rt.digest,
        '--lock-fds',','.join(map(str,descriptors)),'--parent-pid',str(rt.plan['parent_pid']),'--parent-start-ticks',rt.plan['parent_start_ticks']]
    return subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
        pass_fds=tuple(descriptors),start_new_session=True,env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LC_ALL':'C.UTF-8'})


def await_watch(rt,watcher,*,ready=False,seconds=20):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        proof=rt.proof('watch-ready' if ready else 'watch-cleanup')
        if proof is not None:return proof
        require(watcher.poll() is None,'current_watchdog_died');time.sleep(.1)
    raise Refusal('current_watchdog_wait_timeout')


def rollback_initial_fences(rt,attempted,owner_raw):
    """Only before journal admission/worker launch; never adopt a foreign file."""
    for target,fd in reversed(attempted):
        if not os.path.lexists(target):continue
        info=os.fstat(fd);current=target.lstat()
        if (current.st_dev,current.st_ino)!=(info.st_dev,info.st_ino):continue
        require(identity(current)==identity(info) and stat.S_ISREG(info.st_mode) and info.st_nlink==1
            and info.st_size==len(owner_raw) and os.pread(fd,len(owner_raw)+1,0)==owner_raw,'current_initialization_cleanup_unproven')
        target.unlink();directory=os.open(target.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(directory)
        finally:os.close(directory)


def run(rt,path,*,apply=False):
    rt.preflight()
    if not apply:return {'status':'preflight_passed','runtime_changed':False,'maintenance_started':False}
    checked_window(document(ROOT/'window.json',rt.plan['window_sha256']),rt.recipe,apply=True)
    with ExitStack() as stack:
        fd=os.open(MARKER,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC);stack.callback(os.close,fd)
        require(identity(os.fstat(fd))==identity(MARKER.lstat()),'current_marker_changed');fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        rt.preflight();make_work(rt);rt.C.ansible_preflight(rt.plan)
        descriptors=[fd];owned=[]
        rt.plan={**rt.plan,'parent_pid':os.getpid(),'parent_start_ticks':parent_start(os.getpid())}
        owner={'schema':'web_engine_current_owner/1','operation_id':OP,'nonce':rt.plan['nonce'],'manifest_sha256':rt.digest,'pid':os.getpid()}
        attempted=[];owner_raw=(json.dumps(owner,sort_keys=True)+'\n').encode()
        try:
            for target in (SOURCE_LOCK,RESERVATION):
                ownfd,temporary=tempfile.mkstemp(prefix='.current-engine-',dir=target.parent);stack.callback(os.close,ownfd)
                stack.callback(lambda p=temporary:Path(p).unlink(missing_ok=True));fcntl.flock(ownfd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                rt.C.B.write_all(ownfd,owner_raw);os.fsync(ownfd);attempted.append((target,ownfd))
                rt.C.B.publish_noreplace(temporary,target);descriptors.append(ownfd);owned.append((target,os.fstat(ownfd).st_dev,os.fstat(ownfd).st_ino))
                directory=os.open(target.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
                try:os.fsync(directory)
                finally:os.close(directory)
            rt.save({'schema':'web_engine_current_transaction/1','operation_id':OP,'nonce':rt.plan['nonce'],'manifest_sha256':rt.digest,
                'phase':'reserved','backup_requests_started':False,'backup_requests_complete':True,'security_accepted':False,'deployment_eligible':False},exclusive=True)
        except BaseException:
            rollback_initial_fences(rt,attempted,owner_raw)
            raise
        watcher=None
        try:
            watcher=launch_watch(rt,descriptors,path);await_watch(rt,watcher,ready=True)
            env=rt.C.ansible_environment(rt.plan)
            argv=[rt.plan['ansible_binary'],'-i',str(SOURCE/'deploy/maintenance/web_engine_local_inventory.yml'),
                str(SOURCE/OWN_SOURCES[1]),'-e',json.dumps({'web_engine_manifest':str(path),'web_engine_manifest_sha256':rt.digest})]
            cutoff=rt.cutoff.isoformat().replace('+00:00','Z')
            require(rt.C.execute_stage(rt.plan,rt.digest,argv,env,cutoff,descriptors,'pre_install')==0,'current_preinstall_failed')
            require(rt.state()['phase']=='backup_ready','current_preinstall_evidence');rt.remaining(PREINSTALL_MARGIN);rt.assert_watchdog()
            require(rt.C.execute_stage(rt.plan,rt.digest,argv,env,rt.plan['deadline_utc'],descriptors,'install_restore')==0,'current_install_restore_failed')
            rt.invariant(owned=True);rt.runtime('complete');require(rt.state()['phase']=='complete','current_completion_missing')
            cleanup=await_watch(rt,watcher);require(cleanup['worker_succeeded'] is True and cleanup['all_owned_resources_absent'] is True,'current_cleanup_unproven')
            rt.C.release_owned_fences(owned)
            return {'status':'completed','runtime_changed':True,'reboot_performed':False,'security_accepted':False,'deployment_eligible':False}
        except BaseException as failure:
            # Publish BEFORE any potentially timed out scope-stop RPC. The
            # independent watcher also detects parent SIGKILL via PID starttime.
            try:rt.record('failure-requested',reason_code=safe_reason(failure),daemon_request_completion_proven=False)
            except Exception:pass  # Independent watcher also detects death/deadline/capacity.
            for stage in ('pre_install','install_restore'):
                try:rt.scope(stage,stop=True)
                except Exception:pass
            if watcher is not None and watcher.poll() is not None:
                try:watch(rt,descriptors)
                except Exception:pass
            if not isinstance(failure,rt.C.StageLivenessError):
                try:
                    if watcher is not None:await_watch(rt,watcher)
                    rt.recover_before_install();rt.C.release_owned_fences(owned)
                    return {'status':'aborted_restored','security_transition_completed':False,'reboot_performed':False}
                except Exception:pass
            raise


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('prepare','check','run','inspect','checkpoint','market-backup','cold-backup','install','watch'))
    parser.add_argument('--manifest',type=Path,default=ROOT/'plan.json');parser.add_argument('--manifest-sha256')
    parser.add_argument('--apply',action='store_true');parser.add_argument('--phase',choices=PHASES)
    for name in ('transport-sha256','destination-sha256','app-sha256','app-offhost-sha256'):parser.add_argument('--'+name)
    parser.add_argument('--lock-fds');parser.add_argument('--parent-pid',type=int);parser.add_argument('--parent-start-ticks')
    args=parser.parse_args(argv)
    try:
        require(args.mode=='run' or not args.apply,'current_apply_scope');recipe=load_recipe()
        if args.mode=='prepare':
            plan=build_plan(recipe,read(ROOT/'window.json'),transport_sha256=args.transport_sha256,destination_sha256=args.destination_sha256,
                app_sha256=args.app_sha256,app_offhost_sha256=args.app_offhost_sha256)
            rt=Runtime(recipe,plan,'0'*64);rt.preflight();recipe.C.write_json(ROOT/'plan.json',plan,exclusive=True)
            result={'status':'prepared_not_applied','manifest_sha256':sha256(read(ROOT/'plan.json')).hexdigest(),'runtime_changed':False}
        else:
            plan=checked_plan(recipe,args.manifest,args.manifest_sha256);rt=Runtime(recipe,plan,args.manifest_sha256)
            if args.mode in {'check','run'}:result=run(rt,args.manifest,apply=args.apply)
            elif args.mode=='watch':
                require(type(args.parent_pid) is int and args.parent_pid>0 and type(args.parent_start_ticks) is str and args.parent_start_ticks.isdigit(),'current_watch_parent')
                rt.plan={**rt.plan,'parent_pid':args.parent_pid,'parent_start_ticks':args.parent_start_ticks}
                require(args.lock_fds is not None and re.fullmatch(r'[0-9]+,[0-9]+,[0-9]+',args.lock_fds),'current_descriptor_scope')
                watch(rt,list(map(int,args.lock_fds.split(','))));result={'status':'watch_cleanup_completed'}
            elif args.mode=='inspect':
                rt.invariant(owned=True);result={k:plan[k] for k in ('units','containers','datastore_ids')}
            else:
                require(args.phase is None or args.mode=='checkpoint','current_phase_scope')
                if args.mode=='checkpoint':rt.checkpoint(args.phase)
                else:getattr(rt,args.mode.replace('-','_'))()
                result={'status':'phase_completed'}
        print(json.dumps(result,sort_keys=True));return 0
    except BaseException as error:
        print(json.dumps({'status':'refused','code':safe_reason(error),'runtime_acceptance_claimed':False}),file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
