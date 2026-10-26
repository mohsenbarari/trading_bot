"""One normal Datastore role, image only; source library with no apply entrypoint.

Actual per-role prefixes, LOAD/backup/stateful format compatibility evidence and
the normal functional/owner producer are mandatory. The factory is UNARMED.
No App.Site, historical rollout/controller, COW or diagnostic profile is used.
No production data restore, migration, retry or guard-close API is provided.
"""
from contextlib import ExitStack
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import ModuleType,SimpleNamespace
import json,os,re,stat,time

OPERATION='security-normal-datastore-activation-20261025-01'
BASE_CONTRACT_SHA='c07784fd6364b39db1b25f4e0e73dc95f9be6b7128b10f37f12c49dd681de140'
BASE_UTILITIES_SHA='d6c0f59c63fe8932e9c3a378eced3dbf7a993fd4caef78dd0d1c19b49d506a29'
CATALOG_SHA='fc89cd6e75ee1b2b973b9436e55d05f41879e7f52be9cfe583acdecc5fe42263'
CONTRACT_SHA='d210020e24fedd4986803a3fe2618a5016ecd99f9e3ca9588af02d77a967e17d'
LOAD_WORKER_SHA='ff07311e32371832b6c1fef8abf35c22559cca4baaaa0c195a9dcacb82fb4221'
LEGACY_LOAD_SHA='743169b3d9aa5c1941013493b868f9867a640071ffc8405310f9023da3d51e8e'
HEALTH_POLICY='2e520dcb1ac3ab9b0da96959caad3035170d792284bf3df531f128f43a3f9c86'
HEALTH_ADAPTER='5017b7546a3f117e74fdd6314582e19e39395ae1ac183149be924ba720d68aac'
PRODUCER='normal_datastore_functional_producer.py'
ROLES={'native-postgres':('bot','postgres'),'native-redis':('bot','redis'),
    'web-postgres':('web','postgres'),'web-redis':('web','redis'),'web-market-postgres':('web','postgres')}
SERVICE={'native-postgres':'db','native-redis':'redis','web-postgres':'db','web-redis':'redis','web-market-postgres':'market-database'}
VERSIONS={'postgres':'15.19','redis':'7.4.11'}
ROOTS={'bot':Path('/root/trading-bot/trading_bot/.local')/OPERATION,'web':Path('/srv/trading-bot')/OPERATION}
MARKER=Path('/root/secure-envs/trading-bot/queue-cutover-artifacts/production-release.lock')
SOURCE=Path('/root/secure-envs/trading-bot/.production-runtime-source.lock')
SOURCE_ID=[2064,1835555,33152,0,0,1,0,1787336928392178187,1788695466612272194]
RESERVATION=Path('/root/secure-envs/trading-bot/release-control/private-primary-controller.lock')

def require(ok,code):
    if not ok:raise RuntimeError(code)

def unarmed_control():
    return {'schema':'normal_datastore_activation/1','operation':OPERATION,'nonce':None,'controller_commit':None,
        'role':None,'issued_at':None,'forward_deadline':None,'recovery_deadline':None,'scope':None,'adapter_source':None,
        'sites':{'bot':None,'web':None},'gates':{'load_receipt':None,'load_control':None,'backup_restore':None,
            'archive_admission':None,'stateful_compatibility':None,'normal_prefix':None,'functional_producer':None},
        'runtime_env_delta':{},'writer_change_permitted':False,'dns_change_permitted':False,
        'security_accepted':False,'full_matrix_accepted':False}

def file_binding(C,value,name=None):
    C.file_binding(value)
    if name is not None:require(value['name']==name,'producer_source')

def validate_control(C,value,now):
    require(type(value) is dict and set(value)==set(unarmed_control()) and value['schema']=='normal_datastore_activation/1'
        and value['operation']==OPERATION and value['role'] in ROLES,'unarmed')
    require(type(value['nonce']) is str and re.fullmatch('[a-f0-9]{32}',value['nonce'])
        and type(value['controller_commit']) is str and re.fullmatch('[a-f0-9]{40}',value['controller_commit']),'unarmed')
    begin,end,recovery=(value[k] for k in ('issued_at','forward_deadline','recovery_deadline'))
    require(all(type(v) is int for v in (begin,end,recovery)) and begin<=now<end<=begin+3600 and end<recovery<=end+1800,'authority_window')
    require(C.same(value['runtime_env_delta'],{}) and all(value[k] is False for k in
        ('writer_change_permitted','dns_change_permitted','security_accepted','full_matrix_accepted')),'authority_scope')
    require(type(value['gates']) is dict and set(value['gates'])==set(unarmed_control()['gates']),'unarmed')
    file_binding(C,value['adapter_source'],'normal_datastore_activation.py')
    for gate in value['gates'].values():file_binding(C,gate)
    file_binding(C,value['gates']['functional_producer'],PRODUCER)
    require(type(value['sites']) is dict and set(value['sites'])=={'bot','web'},'site_scope')
    for site,binding in value['sites'].items():
        require(type(binding) is dict and set(binding)=={'machine_id','fences','reader_units','legacy_units'}
            and type(binding['machine_id']) is str and re.fullmatch('[a-f0-9]{32}',binding['machine_id']),'unarmed_site')
        require(set(binding['fences'])==({'marker','source'} if site=='bot' else {'marker'}),'fence_binding')
        for name,identity in binding['fences'].items():C.metadata(identity,empty=name=='source')
        if site=='bot':require(C.same(binding['fences']['source'],SOURCE_ID),'original_source_identity')
        require(type(binding['reader_units']) is dict and set(binding['reader_units'])==(C.READER_UNITS if site=='bot' else set()),'reader_units')
        require(type(binding['legacy_units']) is dict and binding['legacy_units'],'legacy_units')
        for name,policy in {**binding['reader_units'],**binding['legacy_units']}.items():
            require(type(name) is str and re.fullmatch(r'[A-Za-z0-9_.@-]+\.(?:service|timer)',name)
                and type(policy) is dict and policy and set(policy)<=C.UNIT_PUBLIC_FIELDS
                and all(type(v) is str for v in policy.values()),'public_unit_policy')
        for policy in binding['reader_units'].values():
            require(set(policy)<=C.READER_POLICY_FIELDS and policy.get('LoadState')=='loaded'
                and policy.get('NeedDaemonReload')=='no' and policy.get('UnitFileState'),'reader_units')
        for policy in binding['legacy_units'].values():
            require(policy.get('ActiveState') in {'inactive','failed'} and policy.get('MainPID')==policy.get('ControlPID')=='0'
                and policy.get('UnitFileState') in {'disabled','masked','not-found'} and policy.get('NeedDaemonReload')=='no','legacy_owner')
    scope=value['scope'];require(type(scope) is dict and set(scope)=={'project','workdir','container_name','compose_files','profiles','env_files',
        'secret_files','public_sources','compose_version','docker_version','environment_file_label','release','data_bindings','image_env_delta','baseline'},'unarmed_prefix')
    C.absolute(scope['workdir']);require(type(scope['project']) is str and re.fullmatch('[a-z0-9][a-z0-9_-]{0,99}',scope['project']),'normal_project')
    if value['role']!='web-market-postgres':
        normal=C.SITES[ROLES[value['role']][0]]
        require(scope['project']==normal['project'] and scope['workdir']==normal['workdir'],'normal_app_datastore_scope')
    require(type(scope['container_name']) is str and re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{1,127}',scope['container_name']),'normal_container_name')
    require(C.same(scope['profiles'],['foundation-template','web'] if value['role']=='web-market-postgres' else []),'normal_profiles')
    files=scope['compose_files'];require(type(files) is list and 1<=len(files)<=24 and len({r['path'] for r in files})==len(files),'normal_prefix')
    for binding in files:
        require(type(binding) is dict and set(binding)=={'path','bytes','sha256'} and type(binding['bytes']) is int
            and 0<binding['bytes']<=C.MAX_CONTROL and C.hex64(binding['sha256']),'normal_prefix')
        C.absolute(binding['path']);require(binding['path'].endswith(('.yml','.yaml')),'normal_prefix')
    for name in ('env_files','secret_files'):
        rows=scope[name];require(type(rows) is list and (rows or name=='secret_files') and len(rows)<=64
            and len({r['path'] for r in rows})==len(rows),'opaque_references')
        for row in rows:
            require(type(row) is dict and set(row)=={'path','target','identity','aliases','cli'} and type(row['cli']) is bool
                and (name=='env_files' or row['cli'] is False),'opaque_references')
            C.absolute(row['path']);C.absolute(row['target'])
            if name=='env_files':C.metadata(row['identity'])
            else:secret_metadata(row['identity'])
            require(type(row['aliases']) is list and len(row['aliases'])<=8,'opaque_references')
            for alias in row['aliases']:
                require(set(alias)=={'path','identity'},'opaque_references');C.absolute(alias['path'])
                ident=alias['identity'];require(type(ident) is list and len(ident)==9 and all(type(v) is int for v in ident)
                    and ident[2:6]==[0o120777,0,0,1] and 0<ident[6]<=4096,'opaque_references')
    require(type(scope['public_sources']) is list and len(scope['public_sources'])<=32,'public_source_scope')
    require(len({r['target'] for r in scope['public_sources']})==len(scope['public_sources'])
        and len({r['path'] for r in scope['public_sources']})==len(scope['public_sources']),'public_source_scope')
    for row in scope['public_sources']:
        require(type(row) is dict and set(row)=={'path','target','bytes','sha256'} and C.hex64(row['sha256'])
            and type(row['bytes']) is int and 0<row['bytes']<=131072,'public_source_scope');C.absolute(row['path']);C.absolute(row['target'])
    require(not scope['public_sources'],'public_source_scope')
    require(type(scope['image_env_delta']) is dict,'datastore_image_defaults')
    data=scope['data_bindings'];require(type(data) is list and len(data)==1,'data_binding')
    require(len({r['target'] for r in data})==len(data),'data_binding')
    for row in data:
        require(type(row) is dict and set(row)=={'path','target','identity'},'data_binding')
        C.absolute(row['path']);C.absolute(row['target']);ident=row['identity']
        require(type(ident) is list and len(ident)==5 and all(type(x) is int for x in ident)
            and ident[0]>0 and ident[1]>0 and stat.S_IFMT(ident[2])==stat.S_IFDIR
            and not stat.S_IMODE(ident[2])&0o007 and ident[3]>=0 and ident[4]>=0,'data_binding')
        require(row['target']==('/var/lib/postgresql/data' if ROLES[value['role']][1]=='postgres' else '/data'),'data_binding')
    require(type(scope['compose_version']) is str and re.fullmatch(r'2\.[0-9]+\.[0-9]+(?:[+a-zA-Z0-9.-]*)?',scope['compose_version'])
        and type(scope['docker_version']) is str and re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',scope['docker_version']),'normal_version')
    require(scope['environment_file_label'] is None or type(scope['environment_file_label']) is str,'normal_prefix')
    require(type(scope['release']) is str and re.fullmatch('[a-f0-9]{40}',scope['release']),'normal_release')
    row=scope['baseline'];require(type(row) is dict and set(row)=={'container_id','image_id','pid','started','restarts'}
        and C.hex64(row['container_id']) and C.image_id(row['image_id']) and type(row['pid']) is int and row['pid']>0
        and type(row['started']) is str and row['started'] and type(row['restarts']) is int and row['restarts']>=0,'unarmed_baseline')
    C.public(value);return value

def size_bytes(value):
    if type(value) is int:require(value>=0,'normal_size');return value
    require(type(value) is str and re.fullmatch(r'[0-9]+(?:\.[0-9]+)?[bkmg]?',value.lower()),'normal_size')
    from decimal import Decimal
    match=re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)([bkmg]?)',value.lower());result=Decimal(match[1])*{'':1,'b':1,'k':1024,'m':1024**2,'g':1024**3}[match[2]]
    require(result==int(result),'normal_size');return int(result)

def normal_memory_spec(spec):
    """Parse normal Compose byte strings on a validation-only service copy."""
    value=deepcopy(spec)
    for key in ('mem_limit','memswap_limit'):
        if key not in value:continue
        item=value[key]
        require(type(item) is int or type(item) is str and len(item)<=64,'normal_resource')
        value[key]=size_bytes(item);require(value[key]<=2**63-1,'normal_resource')
    require(value.get('memswap_limit',2*value.get('mem_limit',0))<=2**63-1,'normal_resource')
    return value

def secret_metadata(value,*,empty=False):
    # Normal file-backed Compose secrets may be root:10001 group-readable.
    # A measured full9 reference is still mandatory and held without a read.
    require(type(value) is list and len(value)==9 and all(type(x) is int for x in value)
        and value[0]>0 and value[1]>0 and stat.S_IFMT(value[2])==stat.S_IFREG
        and stat.S_IMODE(value[2]) in {0o400,0o440,0o600,0o640} and value[3]==0
        and value[4] in {0,10001} and value[5]==1 and value[6]>=0 and (not empty or value[6]==0),'secret_metadata')

class SecretReference:
    """Separate file-backed secret references; Env/M/S parent rules stay exact."""
    def __init__(self,proof,S,stack):
        self.proof=proof;self.S=S;self.parents=[]
        for path in [proof['target'],proof['path'],*[r['path'] for r in proof['aliases']]]:
            for parent in Path(path).parents:
                s=parent.lstat();require(parent.resolve(strict=True)==parent and stat.S_ISDIR(s.st_mode)
                    and s.st_uid==0 and s.st_gid in {0,10001} and not s.st_mode&0o022,'secret_parent')
                self.parents.append((parent,S.identity(s)[:5]))
        self.fd=os.open(proof['target'],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC);stack.callback(os.close,self.fd)
        self.before=S.identity(os.fstat(self.fd));secret_metadata(self.before)
        require(self.before==proof['identity'],'secret_metadata');self.collecting=True;self.check();self.collecting=False;stack.callback(self.final_check)
    def raw_parents(self,current,reference):
        # Validate raw components BEFORE collapsing '..'. In particular an
        # undeclared directory symlink cannot redirect OS path resolution.
        cursor=Path('/') if reference.startswith('/') else current.parent
        for part in reference.split('/')[:-1]:
            if part in ('','.'):continue
            cursor=cursor.parent if part=='..' else cursor/part
            s=cursor.lstat();require(cursor.resolve(strict=True)==cursor and stat.S_ISDIR(s.st_mode)
                and s.st_uid==0 and s.st_gid in {0,10001} and not s.st_mode&0o022,'secret_parent')
            identity=self.S.identity(s)[:5];known={str(p):before for p,before in self.parents}
            if str(cursor) not in known:
                require(self.collecting,'secret_parent_changed');self.parents.append((cursor,identity))
            else:require(identity==known[str(cursor)],'secret_parent_changed')
    def check(self):
        S=self.S;require(all(p.resolve(strict=True)==p and S.identity(p.lstat())[:5]==before for p,before in self.parents),'secret_parent_changed')
        require(self.before==S.identity(os.fstat(self.fd))==S.identity(Path(self.proof['target']).lstat()),'secret_changed')
        declared={row['path']:row['identity'] for row in self.proof['aliases']};require(len(declared)==len(self.proof['aliases']),'secret_alias')
        require(Path(self.proof['path']).resolve(strict=True)==Path(self.proof['target']),'secret_alias')
        current=Path(self.proof['path']);seen=set()
        for _ in range(9):
            actual=S.identity(current.lstat())
            if not stat.S_ISLNK(actual[2]):
                require(str(current)==self.proof['target'] and seen==set(declared)
                    and Path(self.proof['path']).resolve(strict=True)==Path(self.proof['target'])
                    and self.before==S.identity(os.fstat(self.fd))==S.identity(current.lstat()),'secret_alias');return
            require(str(current) in declared and str(current) not in seen and actual==declared[str(current)],'secret_alias')
            seen.add(str(current));target=os.readlink(current);require(type(target) is str and 0<len(target)<=4096 and '\x00' not in target,'secret_alias')
            require(actual==S.identity(current.lstat()),'secret_alias');self.raw_parents(current,target)
            current=Path(os.path.normpath(target if target.startswith('/') else str(current.parent/target)))
        raise RuntimeError('secret_alias')
    def final_check(self):
        primary=__import__('sys').exc_info()[0] is not None
        try:self.check()
        except BaseException:
            if not primary:raise

def tmpfs(spec):
    value=spec.get('tmpfs',[]);require(type(value) is list and len(value)<=16,'normal_tmpfs');result={}
    for row in value:
        require(type(row) is str,'normal_tmpfs');target,separator,options=row.partition(':')
        require(target.startswith('/') and target not in result and target not in {'/','/app','/usr','/lib','/lib64','/bin','/sbin'},'normal_tmpfs')
        require(separator and options and len(options)<=512,'normal_tmpfs');result[target]=options
    return result

class DataReference:
    """Hold an active data directory without reading entries or freezing writes."""
    def __init__(self,proof,C,S,stack):
        self.C,self.S,self.proof=C,S,deepcopy(proof);self.path=Path(proof['path'])
        self.parents=S.parents(self.path);require(self.path.resolve(strict=True)==self.path,'data_binding')
        self.fd=os.open(self.path,os.O_PATH|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC);stack.callback(os.close,self.fd)
        self.check();stack.callback(self.final_check)
    def check(self):
        self.S.check_parents(self.parents)
        require(self.C.same(self.S.identity(os.fstat(self.fd))[:5],self.proof['identity'])
            and self.C.same(self.S.identity(self.path.lstat())[:5],self.proof['identity']),'data_directory_changed')
    def final_check(self):
        primary=__import__('sys').exc_info()[0] is not None
        try:self.check()
        except BaseException:
            if not primary:raise

def expected_mounts(C,model,role,scope):
    spec=model['services'][SERVICE[role]];rows={};refs={p for r in scope['secret_files'] for p in (r['path'],r['target'])}
    sources={r['target']:r for r in scope['public_sources']}
    for item in spec.get('volumes',[]):
        require(type(item) is dict and item.get('type') in {'bind','volume'} and type(item.get('target')) is str,'normal_mount')
        target=C.absolute(item['target']);require(target not in rows,'normal_mount')
        require(target!='/' and not any(target==r or r.startswith(target+'/') or target.startswith(r+'/') for r in ('/usr','/lib','/lib64','/bin','/sbin')),'source_mount')
        if target=='/app' or target.startswith('/app/'):
            require(target in sources and item.get('source')==sources[target]['path'] and item.get('read_only') is True,'source_mount')
        source=C.absolute(item['source']) if item['type']=='bind' else model['volumes'][item['source']]['name']
        options=item.get('bind') or {};require(type(options) is dict and set(options)<={'propagation','create_host_path','selinux','recursive'},'normal_mount')
        require(type(item.get('read_only',False)) is bool,'normal_mount')
        rows[target]={'type':item['type'],'source':source,'readonly':item.get('read_only',False),
            'propagation':options.get('propagation','rprivate') if item['type']=='bind' else None}
    for item in spec.get('secrets',[]):
        require(type(item) is dict and set(item)<={'source','target','uid','gid','mode'} and type(item.get('source')) is str,'normal_secret')
        definition=model['secrets'][item['source']];require(type(definition) is dict and set(definition)<={'name','file'} and definition.get('file') in refs,'unbound_secret')
        target=item.get('target',item['source']);target=target if target.startswith('/') else '/run/secrets/'+target
        require(target not in rows and target.startswith('/run/secrets/'),'normal_secret')
        rows[target]={'type':'bind','source':definition['file'],'readonly':True,'propagation':'rprivate'}
    require(set(sources)=={target for target in rows if target=='/app' or target.startswith('/app/')},'public_source_scope')
    return rows

def baseline(C,model,role,row,image,scope,files,*,replacement=False,creation_no_dependencies=None):
    require(role in ROLES and type(model) is dict and type(model.get('services')) is dict and SERVICE[role] in model['services'],'normal_role')
    spec=model['services'][SERVICE[role]];cfg=row['config'];host=row['host'];defaults=image['Config']
    profiles=spec.get('profiles',[]);declared=['foundation-template','web'] if role=='web-market-postgres' else []
    require(set(spec)<=C.SERVICE_FIELDS|{'profiles','secrets','tmpfs','shm_size'} and C.same(scope['profiles'],declared)
        and type(profiles) is list and all(type(v) is str for v in profiles) and len(profiles)==len(set(profiles))==len(declared)
        and set(profiles)==set(declared),'normal_features')
    container_name=spec.get('container_name',scope['project']+'-'+SERVICE[role]+'-1')
    require(container_name==scope['container_name'] and row['image']==image['Id'] and cfg['Image']==spec['image'] and row['name']=='/'+container_name
        and set(cfg)<=C.CONFIG_FIELDS,'normal_identity')
    explicit=spec.get('environment',{});require(type(explicit) is dict and all(type(k) is str and type(v) is str for k,v in explicit.items()),'normal_environment')
    env=C.environment(defaults.get('Env',[]));env.update(explicit)
    require(C.same(C.environment(cfg['Env']),env),'normal_environment')
    require(C.same(cfg.get('Cmd'),C.image_command(spec,defaults,'command'))
        and C.same(cfg.get('Entrypoint'),C.image_command(spec,defaults,'entrypoint'))
        and cfg['User']==spec.get('user',defaults.get('User') or '') and cfg['WorkingDir']==spec.get('working_dir',defaults.get('WorkingDir') or '')
        and cfg['Hostname']==spec.get('hostname',row['id'][:12]),'normal_execution')
    require(C.same(C.normalize_health(cfg.get('Healthcheck')),C.normalize_health(C.healthcheck(spec['healthcheck']) if 'healthcheck' in spec else defaults.get('Healthcheck'))),'normal_healthcheck')
    for key,default in {'Domainname':'','AttachStdin':False,'AttachStdout':True,'AttachStderr':True,'Tty':False,'OpenStdin':False,
            'StdinOnce':False,'NetworkDisabled':False,'MacAddress':'','OnBuild':None,'Shell':None,'ArgsEscaped':defaults.get('ArgsEscaped',False)}.items():
        require(C.same(cfg.get(key,default),default),'normal_execution')
    require(C.same(cfg.get('Volumes'),defaults.get('Volumes')) and cfg.get('StopSignal','')==spec.get('stop_signal',defaults.get('StopSignal',''))
        and C.same(cfg.get('StopTimeout'),defaults.get('StopTimeout')),'normal_execution')
    require(defaults.get('StopTimeout') is None or type(defaults['StopTimeout']) is int and defaults['StopTimeout']>=0,'normal_execution')
    require(C.same({k:v for k,v in cfg['Labels'].items() if not k.startswith('com.docker.compose.')},
        {**(defaults.get('Labels') or {}),**spec.get('labels',{})}),'normal_labels')
    # Compose replacement metadata omits the project for an implicit name;
    # keep the actual container identity/network checks above unchanged.
    label_model=deepcopy(model);label_model['services'][SERVICE[role]]['container_name']=spec.get('container_name',SERVICE[role]+'-1')
    C.normal_labels(cfg['Labels'],label_model,SERVICE[role],scope,files,image['Id'],replacement=replacement,
        creation_no_dependencies=C.no_dependencies_label(cfg['Labels']) if creation_no_dependencies is None else creation_no_dependencies)
    expected=expected_mounts(C,model,role,scope);actual=row['mounts'];require(type(actual) is list and len(actual)==len(expected),'normal_mount')
    seen=set()
    for mount in actual:
        target=mount['Destination'];require(target in expected and target not in seen,'normal_mount');seen.add(target);item=expected[target]
        require(mount.get('Type')==item['type'] and mount.get('RW') is (not item['readonly']),'normal_mount')
        if item['type']=='bind':require(mount.get('Source')==item['source'] and mount.get('Propagation')==item['propagation'],'normal_mount')
        else:require(mount.get('Name')==item['source'] and mount.get('Driver')=='local','normal_mount')
    require(C.same(host.get('Tmpfs') or {},tmpfs(spec)) and host.get('ShmSize')==size_bytes(spec.get('shm_size',67108864)),'normal_tmpfs')
    # New features are accounted explicitly above, then existing complete host
    # validation applies to a validation-only clone. Actual operands stay raw.
    host_check=deepcopy(host);host_check.pop('Tmpfs',None);host_check['ShmSize']=67108864;C.normal_host(normal_memory_spec(spec),host_check)
    require(host.get('Privileged') is spec.get('privileged',False) and host.get('ReadonlyRootfs') is spec.get('read_only',False)
        and host.get('AutoRemove') is False and (host.get('PidMode') or '')==spec.get('pid','')
        and (host.get('UsernsMode') or '')==spec.get('userns_mode',''),'normal_host')
    for key,name in (('CapAdd','cap_add'),('CapDrop','cap_drop'),('SecurityOpt','security_opt'),('GroupAdd','group_add')):
        require(C.same(host.get(key) or [],spec.get(name) or []),'normal_host')
    restart=spec.get('restart','no').split(':');require(C.same(host.get('RestartPolicy'),{'Name':restart[0],'MaximumRetryCount':int(restart[1]) if len(restart)==2 else 0}),'normal_host')
    logging=spec.get('logging',{});require(C.same(host.get('LogConfig'),{'Type':logging.get('driver','json-file'),'Config':logging.get('options',{})}),'normal_host')
    ports={}
    for port in spec.get('ports',[]):
        require(type(port) is dict and set(port)<={'mode','host_ip','target','published','protocol','name','app_protocol'}
            and type(port.get('target')) is int and 0<port['target']<=65535 and port.get('protocol','tcp') in {'tcp','udp'}
            and port.get('mode','ingress')=='ingress' and type(port.get('host_ip','')) is str,'normal_ports')
        published=port.get('published');require(type(published) in (str,int) and str(published).isdigit() and 0<int(published)<=65535,'normal_ports')
        key=str(port['target'])+'/'+port.get('protocol','tcp');binding={'HostIp':port.get('host_ip',''),'HostPort':str(published)}
        require(binding not in ports.get(key,[]),'normal_ports');ports.setdefault(key,[]).append(binding)
    require(C.same(host.get('PortBindings') or {},ports),'normal_ports')
    exposed={**(defaults.get('ExposedPorts') or {}),**{key:{} for key in ports}}
    for name in spec.get('expose',[]):
        require(type(name) is str and re.fullmatch('[0-9]+(?:/(?:tcp|udp))?',name),'normal_ports')
        exposed[name if '/' in name else name+'/tcp']={}
    require(C.same(cfg.get('ExposedPorts') or {},exposed),'normal_ports')
    names={model['networks'][n]['name'] for n in spec['networks']}
    require(set(row['networks'])==names and host.get('NetworkMode') in names,'normal_network')
    for key,options in spec['networks'].items():
        aliases=row['networks'][model['networks'][key]['name']].get('Aliases');extra=(options or {}).get('aliases',[])
        require(type(aliases) is list and all(type(v) is str for v in aliases) and len(aliases)==len(set(aliases))
            and type(extra) is list and all(type(v) is str for v in extra) and set(aliases)=={container_name,SERVICE[role],*extra},'normal_network_aliases')
    return row

def image_defaults(C,old,new,allowed,profile):
    """Measured vendor-version metadata only; business Env never enters this delta."""
    keys={'postgres':{'PG_VERSION','PG_SHA256','GOSU_VERSION'},
        'redis':{'REDIS_VERSION','REDIS_DOWNLOAD_URL','REDIS_DOWNLOAD_SHA'}}[profile]
    require(type(allowed) is dict and set(allowed)<=keys,'datastore_image_defaults')
    before,after=C.environment(old.get('Env') or []),C.environment(new.get('Env') or [])
    require(set(before)==set(after),'datastore_image_defaults')
    observed={key:{'old':before[key],'new':after[key]} for key in before if before[key]!=after[key]}
    require(C.same(observed,allowed),'datastore_image_defaults')
    for pair in allowed.values():
        require(type(pair) is dict and set(pair)=={'old','new'} and all(type(v) is str and 0<len(v)<=1024 for v in pair.values()),'datastore_image_defaults')
    version='PG_VERSION' if profile=='postgres' else 'REDIS_VERSION'
    require(after.get(version)==VERSIONS[profile] and before.get(version,'').split('.')[0]==VERSIONS[profile].split('.')[0],'datastore_major')
    a,b=deepcopy(old),deepcopy(new);a['Env']=b['Env']=[]
    require(C.same(a,b),'datastore_image_defaults')

def parity(C,old_model,new_model,role,before,after,old_image,new_image,scope,old_files,new_files,delta,*,stopped=False):
    require(C.same(delta,{}),'datastore_image_defaults')
    image_defaults(C,old_image['Config'],new_image['Config'],scope['image_env_delta'],ROLES[role][1])
    baseline(C,old_model,role,before,old_image,scope,old_files,replacement=True)
    baseline(C,new_model,role,after,new_image,scope,new_files,replacement=True)
    expected=deepcopy(old_model);expected['services'][SERVICE[role]]['image']=new_model['services'][SERVICE[role]]['image']
    require(C.same(expected,new_model),'normal_render_delta')
    a,b=deepcopy(before['config']),deepcopy(after['config']);require(set(a)==set(b),'complete_config')
    a['Image']=b['Image'];a['Hostname']=b['Hostname'];a.pop('Labels');b.pop('Labels')
    expected_env=C.environment(a.pop('Env'));new_env=C.environment(b.pop('Env'))
    for key,pair in scope['image_env_delta'].items():
        if key not in old_model['services'][SERVICE[role]].get('environment',{}):
            require(expected_env.get(key)==pair['old'],'datastore_image_defaults');expected_env[key]=pair['new']
    require(C.same(expected_env,new_env) and C.same(a,b),'complete_config')
    require(C.same(C.unordered_host(before['host']),C.unordered_host(after['host']))
        and C.same(sorted(before['mounts'],key=lambda r:r['Destination']),sorted(after['mounts'],key=lambda r:r['Destination'])),'complete_host_mounts')
    C.network_parity(old_model,SERVICE[role],before,after,stopped=stopped);return True

def primitives(stack):
    """Compile exact public libraries into a private namespace, never App.Site."""
    directory=Path(__file__).resolve().parent
    def cold(path,pin):
        proof=[]
        for parent in path.parents:
            s=parent.lstat();require(parent.resolve(strict=True)==parent and stat.S_ISDIR(s.st_mode)
                and s.st_uid==s.st_gid==0 and not s.st_mode&0o022,'source_parent')
            proof.append((parent,(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid)))
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC);stack.callback(os.close,fd)
        ident=lambda s:[s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns]
        before=ident(os.fstat(fd));require(before[2] in {0o100600,0o100644} and before[3:6]==[0,0,1] and 0<before[6]<=131072,'source_metadata')
        raw=os.pread(fd,before[6]+1,0);require(len(raw)==before[6] and sha256(raw).hexdigest()==pin,'source_sha256')
        def check():
            require(before==ident(os.fstat(fd))==ident(path.lstat()) and os.pread(fd,len(raw)+1,0)==raw,'source_changed')
            require(all(parent.resolve(strict=True)==parent and ident(parent.lstat())[:5]==list(value) for parent,value in proof),'source_parent')
        def final():
            primary=__import__('sys').exc_info()[0] is not None
            try:check()
            except BaseException:
                if not primary:raise
        check();stack.callback(final);module=ModuleType('_sealed_market_'+path.stem);module.__file__=str(path)
        exec(compile(raw,str(path),'exec'),module.__dict__);return module,check
    C,cc=cold(directory/'canonical_app_activation_contract.py',BASE_CONTRACT_SHA)
    S,sc=cold(directory/'canonical_app_activation_site.py',BASE_UTILITIES_SHA)
    return C,S,[cc,sc]

def bind_cgroup(backend,value):
    """One shared retained-kernfs proof for every normal lifecycle."""
    return backend.S.bind_cgroup(backend.stack,value)

def normal_producer(raw,path):
    """Required normal producer code, not boolean readiness or callback inputs.

    verify_gate authenticates actual underlying backup/restore, source-only
    security delta and measured normal-prefix receipts. owners and functional
    run normal role contracts; fences retains the authenticated live Native
    SOURCE and both-host markers for every check. No producer is shipped here.
    Its absence leaves the adapter UNARMED; static JSON cannot replace it.
    """
    require(type(raw) is bytes and 0<len(raw)<=131072 and Path(path).name==PRODUCER,'normal_producer_required')
    module=ModuleType('_source_admitted_normal_datastore_producer');module.__file__=str(path)
    exec(compile(raw,str(path),'exec'),module.__dict__)
    require(all(callable(getattr(module,key,None)) and getattr(getattr(module,key),'__globals__',None) is module.__dict__
        for key in ('verify_gate','owners','functional','fences')),'normal_producer_required')
    return module

class DatastoreBackend:
    """One role; normal producer source is required, never an asserted ready flag."""
    def __init__(self,control,stack,*,control_sha256=None):
        require(os.geteuid()==os.getegid()==0,'root_required')
        self.C,self.S,self.checks=primitives(stack);C,S=self.C,self.S
        validate_control(C,control,int(time.time()));self.control=control;self.role=control['role'];self.site=ROLES[self.role][0]
        self.stack=stack;self.root=ROOTS[self.site];self.scope=control['scope'];self.deadline=time.monotonic()+control['forward_deadline']-time.time()
        self.guard=None;self.journal=None;self.completed=[];self.transactions={};self.terminal=None
        self.rollback_authorized=False
        # Generic utilities receive a Datastore-only private contract binding.
        domain=SimpleNamespace(**{k:getattr(C,k) for k in ('MAX_JSON','require','same','document','public','encoded','absolute','hex64','environment','unordered_host','network_parity','other_containers')})
        domain.OPERATION=OPERATION;domain.metadata=C.metadata;domain.ROLE_ORDER=((self.site,self.role),)
        domain.normal_baseline=lambda *a,**kw:baseline(C,*a,**kw)
        domain.network_parity=lambda model,role,*a,**kw:C.network_parity(model,SERVICE[role],*a,**kw)
        domain.configuration_parity=lambda *a,**kw:parity(C,*a,**kw);S.C=domain
        info=self.root.lstat();require(self.root.resolve(strict=True)==self.root and info.st_mode==0o40700 and info.st_uid==info.st_gid==0,'root_directory')
        require(C.hex64(control_sha256),'authority_source')
        authority=S.Held(self.root/'controls'/(control['nonce']+'.json'),stack,pin=control_sha256,maximum=C.MAX_CONTROL)
        snapshot=C.document(authority.raw);require(C.same(control,snapshot),'authority_source')
        self.control=C.document(authority.raw);control=self.control;self.scope=control['scope'];self.control_sha256=control_sha256
        self.checks.extend((authority.check,lambda:require(C.same(self.control,snapshot),'authority_changed')))
        own=S.Held(Path(__file__).resolve(),stack,pin=control['adapter_source']['sha256'],public_source=True)
        require(len(own.raw)==control['adapter_source']['bytes'],'adapter_source');self.checks.append(own.check)
        require(not os.path.lexists(self.root/'attempts'/control['nonce']),'attempt_spent')
        require(Path('/proc/self/ns/mnt').stat().st_ino==Path('/proc/1/ns/mnt').stat().st_ino,'host_namespace')
        machine=S.HeldPublic('/etc/machine-id',stack,64).raw.strip().decode('ascii')
        require(machine==control['sites'][self.site]['machine_id'],'host_identity');self.scope_check()
        for name,path in [('marker',MARKER)]+([('source',SOURCE)] if self.site=='bot' else []):
            held=S.Held(path,stack,expected=control['sites'][self.site]['fences'][name],opaque=True,lock=True);self.checks.append(held.check)
        require(not os.path.lexists(RESERVATION if self.site=='bot' else self.root/'activation.guard'),'guard_busy')
        self.paths=[r['path'] for r in self.scope['compose_files']]
        for proof in self.scope['compose_files']+self.scope['public_sources']:
            held=S.Held(proof['path'],stack,pin=proof['sha256'],maximum=C.MAX_CONTROL,public_source=True)
            require(len(held.raw)==proof['bytes'],'public_source_size');self.checks.append(held.check)
        for proof in self.scope['env_files']:
            reference=S.OpaqueReference(proof,stack);self.checks.append(reference.check)
        for proof in self.scope['secret_files']:
            reference=SecretReference(proof,S,stack);self.checks.append(reference.check)
        for proof in self.scope['data_bindings']:
            reference=DataReference(proof,C,S,stack);self.checks.append(reference.check)
        producer_binding=control['gates']['functional_producer'];raw=self.evidence(producer_binding)
        self.candidate,self.spec=self.load_evidence()
        self.producer=normal_producer(raw,self.root/'evidence'/PRODUCER)
        self.version_check();self.original=self.inventory();self.current=deepcopy(self.original);self.role_ids={self.role:self.scope['baseline']['container_id']}
        require(self.role_ids[self.role] in self.original,'role_missing');old=self.original[self.role_ids[self.role]];S.stable_running(old)
        require(all(C.same(old[k],self.scope['baseline'][v]) for k,v in (('image','image_id'),('pid','pid'),('started','started'),('restarts','restarts'))),'role_baseline')
        require(old['image']!=self.candidate,'already_active_requires_review')
        self.images={old['image']:self.image(old['image'])};self.new_image=self.image(self.candidate)
        require(self.new_image['Id']==self.candidate and C.same(self.new_image['RootFS'],{'Type':'layers','Layers':self.spec['candidate_rootfs']})
            and self.new_image.get('Os')=='linux' and self.new_image.get('Architecture')=='amd64'
            ,'datastore_image_defaults')
        image_defaults(C,self.images[old['image']]['Config'],self.new_image['Config'],self.scope['image_env_delta'],ROLES[self.role][1])
        self.old_model=self.render();references=self.render(no_env_resolution=True)
        env_refs={p for r in self.scope['env_files'] for p in (r['path'],r['target'])}
        require(all((r['path'] if type(r) is dict else r) in env_refs for r in references['services'][SERVICE[self.role]].get('env_file',[])),'unbound_env_reference')
        baseline(C,self.old_model,self.role,old,self.images[old['image']],self.scope,self.paths)
        for proof in self.scope['data_bindings']:
            mounted=[r for r in old['mounts'] if r['Destination']==proof['target']]
            require(len(mounted)==1 and mounted[0]['Source']==proof['path'] and mounted[0]['RW'] is True,'data_binding')
        self.override=self.root/'attempts'/control['nonce']/'image-only.yml';self.rollback_override=self.override.with_name('old-image.yml')
        self.new_model=self.render(override=self.override_bytes(),virtual=True);expected=deepcopy(self.old_model);expected['services'][SERVICE[self.role]]['image']=self.candidate
        require(C.same(expected,self.new_model),'normal_render_delta');self.rollback_model=self.render(override=self.override_bytes(False),virtual=True)
        expected['services'][SERVICE[self.role]]['image']=old['image'];require(C.same(expected,self.rollback_model),'normal_render_delta')
        require(self.producer.verify_gate(self.role,self,self.evidence) is True,'normal_producer_gate')
        self.functional(self.role,old);self.singleton(self.role,excluding=old['id']);self.unchanged()

    def check(self):
        require(time.monotonic()<self.deadline,'deadline')
        for check in self.checks:check()
        if hasattr(self,'normal_proofs'):
            now=int(time.time())
            require(all(0<=now-p['completed_at']<=14400 for p in self.normal_proofs.values()),'stateful_evidence_expired')
        if hasattr(self,'producer'):
            # A future normal producer must retain/authenticate BOTH hosts'
            # actual held fences. A static JSON assertion is not an input.
            proof=self.producer.fences(self.role,self)
            expected={'schema':'normal_datastore_live_peer_fences/1','operation':OPERATION,'control_sha256':self.control_sha256,
                'nonce':self.control['nonce'],'role':self.role,'site':self.site,'native_source_held':True,
                'both_markers_held':True,'pending_release_absent':True,'guard_owner_exact':True,
                'reader_units_unchanged':True,'other_host_runtime_unchanged':True,'legacy_owners_absent':True,'security_accepted':False}
            require(self.C.same(proof,expected),'normal_live_fences_unproved')
        if self.guard:self.guard.check()
    def evidence(self,binding):
        held=self.S.Held(self.root/'evidence'/binding['name'],self.stack,pin=binding['sha256'],maximum=self.C.MAX_JSON)
        require(len(held.raw)==binding['bytes'],'evidence_size');self.checks.append(held.check);return held.raw
    def cmd(self,args,**kw):
        self.check();result=self.S.command(args,self.deadline,**kw);self.check();return result
    def docker(self,*args,**kw):return self.cmd(['/usr/bin/docker',*args],**kw)
    def compose(self,extra=False,rollback=False):
        args=['/usr/bin/docker','compose','--project-directory',self.scope['workdir'],'--project-name',self.scope['project']]
        if self.role=='web-market-postgres':args+=['--profile','web']
        for proof in self.scope['env_files']:
            if proof['cli']:args+=['--env-file',proof['path']]
        for path in self.paths:args+=['--file',path]
        if extra:args+=['--file',str(self.override)]
        if rollback:args+=['--file',str(self.rollback_override)]
        return args
    def render(self,override=None,virtual=False,no_env_resolution=False):
        argv=self.compose()
        if virtual:argv+=['--file','-']
        elif override is not None:argv+=['--file',str(override)]
        value=self.C.document(self.cmd(argv+['config','--format','json']+(['--no-env-resolution'] if no_env_resolution else []),
            stdin=override if virtual else None,cwd=self.scope['workdir']))
        require(type(value) is dict and value.get('name')==self.scope['project'],'normal_render');return value
    def override_bytes(self,candidate=True):
        image=self.candidate if candidate else self.original[self.role_ids[self.role]]['image']
        return (json.dumps({'services':{SERVICE[self.role]:{'image':image}}},sort_keys=True)+'\n').encode()
    def inventory(self):
        ids=self.docker('ps','--all','--no-trunc','--format','{{.ID}}',maximum=131072).decode().splitlines()
        require(0<len(ids)<=128 and len(set(ids))==len(ids) and all(self.C.hex64(k) for k in ids),'inventory')
        rows=self.C.document(self.docker('inspect','--type','container',*ids));require(type(rows) is list and len(rows)==len(ids),'inventory')
        result={r['Id']:self.S.row(r) for r in rows};require(set(result)==set(ids),'inventory');return result
    def inspect(self,cid):
        require(self.C.hex64(cid),'container_id');rows=self.C.document(self.docker('inspect','--type','container',cid))
        require(type(rows) is list and len(rows)==1 and rows[0]['Id']==cid,'container_identity');return self.S.row(rows[0])
    def image(self,iid):
        require(self.C.image_id(iid),'image_id');rows=self.C.document(self.docker('image','inspect',iid))
        require(type(rows) is list and len(rows)==1 and rows[0]['Id']==iid,'image_identity');return rows[0]
    def version_check(self):
        require(self.docker('compose','version','--short',maximum=1024).decode().strip()==self.scope['compose_version'],'compose_version')
        require(self.docker('version','--format','{{.Server.Version}}',maximum=1024).decode().strip()==self.scope['docker_version'],'docker_version')
    def scope_check(self):
        with open('/proc/self/cgroup','rb') as source:raw=source.read(8193)
        require(len(raw)<=8192,'scope_bound');paths=[s[3:] for s in raw.decode().splitlines() if s.startswith('0::')]
        unit=OPERATION+'-'+self.control['nonce']+'.scope'
        require(len(paths)==1 and paths[0].endswith('/'+unit) and '..' not in paths[0].split('/'),'finite_scope')
        directory=Path('/sys/fs/cgroup'+paths[0])
        for name,maximum in (('memory.max',768*1024**2),('pids.max',64)):
            with (directory/name).open('rb') as source:value=source.read(64)
            require(value.strip().isdigit() and 0<int(value)<=maximum,'finite_scope')
        with (directory/'cpu.max').open('rb') as source:parts=source.read(64).split()
        require(len(parts)==2 and all(v.isdigit() for v in parts) and 0<int(parts[0])<=int(parts[1]),'finite_scope')
        self.C.scope_seconds(self.cmd(['/usr/bin/systemctl','show',unit,'--value','--property=RuntimeMaxUSec'],maximum=1024).decode().strip())
    def load_evidence(self):
        C,S=self.C,self.S
        helper=S.Held(Path(__file__).parent/'datastore_candidate_contract.py',self.stack,pin=CONTRACT_SHA,public_source=True)
        self.checks.append(helper.check);D=ModuleType('_sealed_datastore_vendor_contract')
        exec(compile(helper.raw,str(Path(__file__).parent/'datastore_candidate_contract.py'),'exec'),D.__dict__)
        profile=ROLES[self.role][1];vendor=D.PROFILES[profile]
        raw=self.evidence({'name':'datastore_candidate_images.json','bytes':3868,'sha256':CATALOG_SHA})
        catalog=C.document(raw);spec=next(r for r in catalog['profiles'] if r['profile']==profile)
        require(spec['candidate_config_id']==vendor['config_id'] and C.same(spec['candidate_rootfs'],vendor['layers']),'vendor_catalog')
        receipt=C.document(self.evidence(self.control['gates']['load_receipt']))
        control_raw=self.evidence(self.control['gates']['load_control']);operation=C.document(control_raw)
        admitted_raw=self.evidence(self.control['gates']['archive_admission']);admitted=C.document(admitted_raw)
        for value in (receipt,operation,admitted):C.public(value)
        versions={('datastore_image_store_control/2',LEGACY_LOAD_SHA):'datastore_preloaded_image/2',
            ('datastore_image_store_control/3',LOAD_WORKER_SHA):'datastore_preloaded_image/3'}
        pair=(operation['schema'],operation['worker_sha256']);require(pair in versions,'actual_load_control')
        require(operation['operation']=='security-datastore-candidate-images-20261025-03'
            and operation['profile']==profile and operation['phase']==('load-native' if self.site=='bot' else 'load-web')
            and type(operation['nonce']) is str and re.fullmatch('[a-f0-9]{32}',operation['nonce']),'actual_load_control')
        require(receipt['schema']==versions[pair] and receipt['operation']==operation['operation']
            and receipt['controller_sha256']==operation['worker_sha256'] and receipt['engine_sha256']=='1041cc2d04100e2f6aebb95dca30c2339de41342b350ef44522c05277e9c146e'
            and receipt['nonce']==operation['nonce'] and receipt['profile']==profile and receipt['role']==profile
            and receipt['catalog_sha256']==CATALOG_SHA and receipt['site']==('native' if self.site=='bot' else 'web')
            and receipt['host_machine_id']==self.control['sites'][self.site]['machine_id'] and receipt['ci_run_id']==spec['ci_run_id']
            and receipt['image_artifact_sha256']==spec['image_artifact']['digest'][7:]
            and receipt['candidate_config_id']==vendor['config_id'] and C.same(receipt['rootfs'],vendor['layers'])
            and receipt['operation_sha256']==sha256(control_raw).hexdigest() and receipt['source_admission_sha256']==sha256(admitted_raw).hexdigest()
            and operation['admission_sha256']==sha256(admitted_raw).hexdigest()
            and receipt['archive_and_config_association_verified'] is True and receipt['whole_typed_config_compared_in_ram'] is True
            and receipt['configuration_values_disclosed'] is False and receipt['security_accepted'] is False
            and receipt['serving_roles_activated']==[] and receipt['serving_validation']=='NOT_TESTED','actual_load_receipt')
        if receipt['schema']=='datastore_preloaded_image/3':
            require(operation['health_policy_sha256']==receipt['health_policy_sha256']==HEALTH_POLICY
                and receipt['health_adapter_sha256']==HEALTH_ADAPTER and receipt['runtime_health_issues_open'] is True,'actual_load_receipt')
            C.load_observation_history(receipt,self.site)
        require(admitted['schema']=='market_candidate_archive/1' and admitted['profile']==profile
            and admitted['candidate_config_id']==vendor['config_id'] and C.same(admitted['rootfs'],vendor['layers'])
            and admitted['archive_sha256']==receipt['source_archive_sha256']
            and receipt['candidate_local_image_id'] in admitted['source_identities'] and admitted['security_accepted'] is False,'actual_archive_admission')
        require(type(admitted.get('archive_bytes')) is int and admitted['archive_bytes']==spec['archive']['uncompressed_bytes']
            and type(admitted.get('expanded_layer_bytes')) is int and admitted['expanded_layer_bytes']>0
            and type(admitted.get('prospective_import_bytes')) is int
            and admitted['prospective_import_bytes']==2*admitted['archive_bytes']+2*admitted['expanded_layer_bytes']
            and admitted.get('configuration_values_disclosed') is False,'actual_archive_admission')
        C.metadata(admitted.get('tar_identity'));require(admitted['tar_identity'][6]==admitted['archive_bytes'],'actual_archive_admission')
        identities=admitted['source_identities'];require(type(identities) is dict and 1<=len(identities)<=16
            and C.same(identities.get(vendor['config_id']),{'kind':'Docker_config_identity'}),'actual_archive_admission')
        media={'OCI_manifest_to_config':'application/vnd.oci.image.manifest.v1+json',
            'OCI_index_to_manifest_to_config':'application/vnd.oci.image.index.v1+json'}
        for ident,row in identities.items():
            require(C.image_id(ident) and type(row) is dict,'actual_archive_admission')
            if ident==vendor['config_id']:continue
            require(set(row)=={'kind','media_type','size'} and row['kind'] in media and row['media_type']==media[row['kind']]
                and type(row['size']) is int and 0<row['size']<=1048576,'actual_archive_admission')
        proofs={}
        for name,schema in (('backup_restore','normal_datastore_backup_restore/1'),
                ('stateful_compatibility','normal_datastore_stateful_compatibility/1'),('normal_prefix','normal_datastore_prefix/1')):
            proof=C.document(self.evidence(self.control['gates'][name]));now=int(time.time())
            C.public(proof)
            require(proof.get('schema')==schema and proof.get('role')==self.role and proof.get('profile')==profile
                and proof.get('machine_id')==self.control['sites'][self.site]['machine_id'] and proof.get('candidate_config_id')==vendor['config_id']
                and proof.get('old_image_id')==self.scope['baseline']['image_id'] and C.same(proof.get('data_bindings'),self.scope['data_bindings'])
                and type(proof.get('completed_at')) is int and 0<=now-proof['completed_at']<=14400
                and proof.get('security_accepted') is False,'normal_producer_evidence')
            proofs[name]=proof
        require(all(proofs['backup_restore'].get(k) is True for k in
            ('backup_complete','isolated_restore_passed','durable_receipt_verified')),'stateful_backup_restore')
        self.compatibility=proofs['stateful_compatibility']
        require(all(self.compatibility.get(k) is True for k in
            ('candidate_on_fresh_restore_passed','old_on_candidate_written_clone_passed','persistence_restart_passed','rollback_data_compatible'))
            and self.compatibility.get('implicit_migration_permitted') is False
            and self.compatibility.get('candidate_server_version')==VERSIONS[profile]
            and type(self.compatibility.get('old_server_version')) is str
            and re.fullmatch(r'[0-9]+(?:\.[0-9]+){1,2}',self.compatibility['old_server_version'])
            and self.compatibility['old_server_version'].split('.')[0]==VERSIONS[profile].split('.')[0],'stateful_compatibility')
        self.normal_proofs=proofs;snapshot=deepcopy(proofs)
        self.checks.append(lambda:require(C.same(self.normal_proofs,snapshot)
            and C.same(self.compatibility,snapshot['stateful_compatibility']),'stateful_evidence_changed'))
        # verify_gate must authenticate the underlying normal restore/version/
        # prefix producer receipts. No default producer or asserted-ready fallback.
        return receipt['candidate_local_image_id'],spec
    def units_check(self):
        binding=self.control['sites'][self.site]
        for unit,expected in {**binding['reader_units'],**binding['legacy_units']}.items():
            raw=self.cmd(['/usr/bin/systemctl','show',unit,'--no-pager','--property='+','.join(sorted(expected))],maximum=32768).decode()
            actual=dict(row.split('=',1) for row in raw.splitlines() if '=' in row)
            require(self.C.same(actual,expected),'unit_policy_changed')
    def unchanged(self,old=None,new=None):
        now=self.inventory();expected=self.S.comparable_inventory(self.current);actual=self.S.comparable_inventory(now)
        if old is None:require(self.C.same(expected,actual),'runtime_changed')
        else:self.C.other_containers(expected,actual,old,new)
        self.units_check();self.check();return now
    def singleton(self,service,excluding=None):
        require(service==self.role,'role_scope')
        for cid,row in self.inventory().items():
            if cid==excluding or not row['running']:continue
            labels=row['config'].get('Labels') or {};cmd=row['config'].get('Cmd')
            require(not (labels.get('com.docker.compose.project')==self.scope['project'] and labels.get('com.docker.compose.service')==SERVICE[service])
                ,'duplicate_role')
            require(not any(m.get('RW') is True and m.get('Source') in {r['path'] for r in self.scope['data_bindings']}
                for m in row['mounts']),'duplicate_data_writer')
        value=self.producer.owners(service,self,excluding)
        expected={'schema':'normal_datastore_owner/1','role':service,'excluded_container_id':excluding,
            'single_owner':True,'legacy_owners_absent':True,'data_mount_singleton':True,'security_accepted':False}
        require(self.C.same(value,expected),'normal_owner_unproved');self.units_check()
    def functional(self,service,value):
        require(service==self.role,'role_scope');self.check();measured=self.inspect(value['id']);self.S.stable_running(measured)
        require(all(self.C.same(measured[k],value[k]) for k in ('id','image','pid','started','restarts')),'normal_functional_identity')
        proof=self.producer.functional(service,self,measured)
        self.check();measured=self.inspect(value['id']);self.S.stable_running(measured)
        require(all(self.C.same(measured[k],value[k]) for k in ('id','image','pid','started','restarts')),'normal_functional_identity')
        health=self.C.normalize_health(value['config'].get('Healthcheck'))
        if health is not None and health.get('Test') not in (None,[],['NONE']):
            require(measured['health']=='healthy','normal_role_unready')
        profile=ROLES[service][1]
        version=VERSIONS[profile] if measured['image']==self.candidate else self.compatibility['old_server_version']
        expected={'schema':'normal_datastore_functional/1','operation':OPERATION,'control_sha256':self.control_sha256,
            'nonce':self.control['nonce'],'role':service,'profile':profile,'site':self.site,'container_id':measured['id'],'image_id':measured['image'],
            'pid':measured['pid'],'started':measured['started'],'restarts':measured['restarts'],'server_version':version,
            'status':'ready','authenticated':True,'persistence_ready':True,'stateful_restore_gate_bound':True,'security_accepted':False}
        require(self.C.same(proof,expected),'normal_functional_unproved');self.singleton(service,excluding=value['id']);self.check()
    def begin(self):
        require(self.guard is None and self.journal is None,'attempt_spent');self.unchanged()
        S,C=self.S,self.C;role=self.role
        class Guard(S.Guard):
            def __init__(guard,path,control,stack):
                guard.path=Path(path);guard.raw={'schema':'normal_datastore_activation_guard/1','operation':OPERATION,'nonce':control['nonce'],
                    'controller_commit':control['controller_commit'],'pid':os.getpid(),'security_accepted':False}
                S.publish(guard.path,guard.raw);guard.held=S.Held(guard.path,stack);guard.closed=False
        class Journal(S.Journal):
            def record(journal,state,service=None,container_id=None):
                require(state in {'stop_intent','stopped','create_intent','created','start_intent','active','rollback_intent','rolled_back','unknown',
                    'final_verified','terminal_prepared','terminal_closed'} and service in (None,role) and (container_id is None or C.hex64(container_id)),'journal_scope')
                S.publish(journal.path/('%03d.json'%journal.index),{'schema':'normal_datastore_activation_step/1','operation':OPERATION,'nonce':self.control['nonce'],
                    'index':journal.index,'state':state,'service':service,'container_id':container_id,'security_accepted':False});journal.index+=1
        self.guard=Guard(RESERVATION if self.site=='bot' else self.root/'activation.guard',self.control,self.stack)
        self.journal=Journal(self.root/'attempts'/self.control['nonce'],self.control)
        for path,candidate in ((self.override,True),(self.rollback_override,False)):
            raw=self.override_bytes(candidate);fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
            try:require(os.write(fd,raw)==len(raw),'override_write');os.fsync(fd)
            finally:os.close(fd)
            S.sync_directory(path.parent);held=S.Held(path,self.stack,pin=sha256(raw).hexdigest());self.checks.append(held.check)
        require(C.same(self.render(override=self.override),self.new_model) and C.same(self.render(override=self.rollback_override),self.rollback_model),'normal_render_changed')
    def cgroup(self,value):return bind_cgroup(self,value)
    def stop(self,service,cid):
        before=self.inspect(cid);empty=self.cgroup(before);self.journal.record('stop_intent',service,cid)
        self.docker('stop','--time','45',cid,mutation=True,maximum=1024);after=self.inspect(cid)
        if after['running'] is not False or after['pid']!=0:raise self.S.Unknown('stop_unproven')
        empty();self.singleton(service,excluding=cid);self.unchanged(cid);self.journal.record('stopped',service,cid);return empty
    def create(self,service,*,candidate=True):
        require(service==self.role,'role_scope');self.journal.record('create_intent',service)
        self.cmd(self.compose(extra=candidate,rollback=not candidate)+['up','--no-start','--force-recreate','--no-deps','--no-build','--pull','never',SERVICE[service]],mutation=True,cwd=self.scope['workdir'],maximum=131072)
        ids=self.cmd(self.compose(extra=candidate,rollback=not candidate)+['ps','--all','--quiet',SERVICE[service]],maximum=1024).decode().splitlines()
        if len(ids)!=1 or not self.C.hex64(ids[0]):raise self.S.Unknown('created_identity_unproven')
        value=self.inspect(ids[0])
        if (value['running'] is not False or value['pid']!=0 or value['restarts']!=0 or value['started']!='0001-01-01T00:00:00Z'
            or any(value[k] is not False for k in ('paused','restarting','dead'))):raise self.S.Unknown('created_state_unproven')
        self.journal.record('created',service,value['id']);return value
    def start(self,service,value):
        self.singleton(service,excluding=value['id']);self.journal.record('start_intent',service,value['id'])
        self.docker('start',value['id'],mutation=True,maximum=1024);after=self.inspect(value['id']);self.S.stable_running(after);return after
    def replace(self):
        require(self.guard is not None and self.journal is not None,'begin_required');self.rollback_authorized=False
        try:return self.S.replace(self,self.role)
        except self.S.KnownFailure:
            self.rollback_authorized=True;raise
    def rollback(self):
        require(self.role in self.transactions and self.rollback_authorized,'rollback_unproved')
        require(self.compatibility.get('rollback_data_compatible') is True and self.compatibility.get('implicit_migration_permitted') is False,'stateful_compatibility')
        require(int(time.time())<self.control['recovery_deadline'],'recovery_expired')
        self.deadline=time.monotonic()+self.control['recovery_deadline']-time.time()
        require(self.producer.verify_gate(self.role,self,self.evidence) is True,'normal_producer_gate')
        self.rollback_authorized=False;return self.S.rollback(self,self.role)
    def prepare_terminal(self):
        require(self.completed==[self.role] and self.guard is not None,'role_incomplete');self.unchanged()
        active=self.current[self.role_ids[self.role]];self.functional(self.role,active)
        result={'schema':'normal_datastore_activation_terminal/1','operation':OPERATION,'nonce':self.control['nonce'],
            'control_sha256':self.control_sha256,'role':self.role,
            'site':self.site,'container_id':active['id'],'image_id':active['image'],'status':'normal_role_verified',
            'writer_changed':False,'dns_changed':False,'full_matrix_accepted':False,'security_accepted':False}
        pin=self.S.publish(self.journal.path/'terminal.json',result);self.journal.record('terminal_prepared',self.role,active['id'])
        self.check();self.terminal=(pin,result)
        return {**result,'terminal_sha256':pin,'guard_retained':True,'cross_site_closure_proven':False}
