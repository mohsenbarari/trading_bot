"""Unarmed ordinary App restore rehearsal; no serving or production-data API.

Normal backup/restore IO and the supported isolation backend are supplied by
the cold-admitted sibling producer. Actual prerequisite facts are never inferred.
Private normal Config/Env and restored data remain in that trusted runtime.
"""
from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import ModuleType
from urllib.parse import urlsplit,parse_qs
import ipaddress,json,os,re,stat,time

OPERATION='normal-app-isolated-restore-20261026-02'
SOURCE_COMMIT='665220a2b5086597daa4565eef6450a37aaf47f4'
CANDIDATE_CONFIG='sha256:e1a727de49c45befbcef82f2b93721b780c06b64643d749290ade390215cc9f1'
DATA_TARGETS=frozenset(('/app/uploads','/app/audit_trail','/app/certs','/app/runtime/coin-inference','/app/runtime/product-estimator'))
LIVE_MODEL_TARGETS=frozenset(('/app/runtime/coin-inference','/app/runtime/product-estimator'))
PRODUCER='normal_app_restore_producer.py'
NORMAL_APIS={'scripts/run_production_backup.py':'268ec5178aecbc7b2389721cc64e6562ce02744197968ed6c8df812d46e620d6',
 'scripts/rehearse_production_migration.py':'b234f3bb0a2ad3f598bce06cb96f2f880e1741362b763d925cd7c56bf0e5e77f'}
BACKUP_HELPERS=('verify_backup_receipt','extract_owner_roles','create_owner_roles','restore_plain_dump','cleanup_owned_resources')
METHODS=('admit_normal_inputs','restore_owned','isolation','create_api','inspect_api','start_api',
 'ready_api','stop_api','restart_owned_stores','measure_data','cleanup_owned','check')
CODES=frozenset(('unarmed','control','source','source_changed','producer','normal_backup','normal_runtime',
 'clone_target','isolation_unsupported','isolation','mounts','owned_identity','not_stopped','schema',
 'persistence','cleanup','deadline','unknown_outcome'))

class Refused(RuntimeError):pass
def need(ok,code):
 if not ok:raise Refused(code)
def same(a,b):
 if type(a) is not type(b):return False
 if type(a) is dict:return set(a)==set(b) and all(same(a[k],b[k]) for k in a)
 if type(a) in (list,tuple):return len(a)==len(b) and all(same(x,y) for x,y in zip(a,b))
 return a==b
def hex64(v):return type(v) is str and re.fullmatch('[a-f0-9]{64}',v) is not None
def image(v):return type(v) is str and re.fullmatch('sha256:[a-f0-9]{64}',v) is not None
def cid(v):return hex64(v)
def identity(s):return [s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns]
def public(v):
 if type(v) is dict:
  need(not {'Config','Env','HostConfig','environment','runtime','password','url','endpoint'}&set(v),'control')
  for child in v.values():public(child)
 elif type(v) in (list,tuple):
  for child in v:public(child)
def binding(v):
 need(type(v) is dict and set(v)=={'path','bytes','sha256'} and type(v['path']) is str
  and Path(v['path']).is_absolute() and '..' not in Path(v['path']).parts
  and type(v['bytes']) is int and 0<v['bytes']<=131072 and hex64(v['sha256']),'source')
def unarmed_control():
 return {'schema':'normal_app_restore_rehearsal/2','operation':OPERATION,'nonce':None,'site':None,'machine_id':None,
  'issued_at':None,'deadline':None,'source_commit':SOURCE_COMMIT,'candidate_config_id':CANDIDATE_CONFIG,
  'candidate_image_id':None,'old_image_id':None,'producer':None,'normal_api_sources':None,'backup_receipt':None,
  'normal_prefix':None,'isolation_support':None,'bridge_capability':None,'limits':None,'security_accepted':False,'full_matrix_accepted':False}
def validate_control(v,now):
 need(type(v) is dict and set(v)==set(unarmed_control()) and v['schema']=='normal_app_restore_rehearsal/2'
  and v['operation']==OPERATION and v['source_commit']==SOURCE_COMMIT and v['candidate_config_id']==CANDIDATE_CONFIG,'control')
 need(type(v['nonce']) is str and re.fullmatch('[a-f0-9]{32}',v['nonce']) and v['site'] in ('bot','web')
  and type(v['machine_id']) is str and re.fullmatch('[a-f0-9]{32}',v['machine_id']),'unarmed')
 need(type(v['issued_at']) is type(v['deadline']) is int and v['issued_at']<=now<v['deadline']<=v['issued_at']+3600,'deadline')
 need(image(v['candidate_image_id']) and image(v['old_image_id']) and v['candidate_image_id']!=v['old_image_id'],'unarmed')
 for name in ('producer','backup_receipt','normal_prefix','isolation_support','bridge_capability'):binding(v[name])
 need(Path(v['producer']['path']).name==PRODUCER,'producer')
 need(type(v['normal_api_sources']) is dict and set(v['normal_api_sources'])==set(NORMAL_APIS),'normal_backup')
 for name,pin in NORMAL_APIS.items():binding(v['normal_api_sources'][name]);need(v['normal_api_sources'][name]['sha256']==pin,'normal_backup')
 limits=v['limits'];need(type(limits) is dict and set(limits)=={'api','db','redis','lifetime_seconds'},'control')
 need(type(limits['lifetime_seconds']) is int and 60<=limits['lifetime_seconds']<=1800,'control')
 for role in ('api','db','redis'):
  row=limits[role];need(type(row) is dict and set(row)=={'memory','nano_cpus','pids','log_bytes','log_files'}
   and all(type(n) is int for n in row.values()) and 256*1024**2<=row['memory']<=4*1024**3
   and 100000000<=row['nano_cpus']<=2000000000 and 32<=row['pids']<=512
   and 65536<=row['log_bytes']<=10*1024**2 and 1<=row['log_files']<=3,'control')
 need(v['security_accepted'] is v['full_matrix_accepted'] is False,'control');public(v)

@contextmanager
def held_source(v):
 binding(v);path=Path(v['path']);parents=[]
 for p in path.parents:
  s=p.lstat();need(p.resolve(strict=True)==p and stat.S_ISDIR(s.st_mode) and s.st_uid==s.st_gid==0
   and not s.st_mode&0o022,'source');parents.append((p,identity(s)[:5]))
 before=identity(path.lstat());need(path.resolve(strict=True)==path and before[2] in (0o100600,0o100644)
  and before[3:7]==[0,0,1,v['bytes']],'source')
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
 try:
  need(before==identity(os.fstat(fd)),'source');raw=os.pread(fd,v['bytes']+1,0)
  need(len(raw)==v['bytes'] and sha256(raw).hexdigest()==v['sha256'],'source')
  def check():
   need(all(p.resolve(strict=True)==p and identity(p.lstat())[:5]==old for p,old in parents),'source_changed')
   need(before==identity(os.fstat(fd))==identity(path.lstat()) and os.pread(fd,len(raw)+1,0)==raw,'source_changed')
  check();primary=False
  try:yield raw,check
  except BaseException:primary=True;raise
  finally:
   try:check()
   except BaseException:
    if not primary:raise
 finally:os.close(fd)

def producer(raw,path):
 need(type(raw) is bytes and 0<len(raw)<=131072 and Path(path).name==PRODUCER,'producer')
 m=ModuleType('_admitted_normal_app_restore');m.__file__=str(path);exec(compile(raw,str(path),'exec'),m.__dict__)
 need(all(callable(getattr(m,k,None)) and getattr(getattr(m,k),'__globals__',None) is m.__dict__ for k in METHODS),'producer')
 return m

# Public canonical Config from authenticated eac66d11 build evidence. No normal Env.
CANONICAL_CONFIG={'Cmd': ['python3'], 'Env': ['PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', 'LANG=C.UTF-8', 'GPG_KEY=A035C8C19219BA821ECEA86B64E628F8D684696D', 'PYTHON_VERSION=3.11.17', 'PYTHON_SHA256=bfb74ad39efae27cda510f134ab408e00f9992c56851cfc0b1cdb5646da11599'], 'Labels': {'io.gold-trade.release.input-signature': 'a63abda5a1b5fd889575be09d0cdff0412ad0c0cd062f85336fedf0237cf87a2', 'io.gold-trade.release.tree': 'f522d42f754599bb9ebd0ea0e284a338ec1dcfbf', 'org.opencontainers.image.revision': '665220a2b5086597daa4565eef6450a37aaf47f4'}, 'WorkingDir': '/app'}

def candidate_association(actual,local,receipt):
 need(type(actual) is dict and type(receipt) is dict and image(local)
  and receipt.get('candidate_config_id')==CANDIDATE_CONFIG and receipt.get('candidate_local_image_id')==local
  and actual.get('Id')==local and actual.get('Os')=='linux' and actual.get('Architecture')=='amd64'
  and same(actual.get('RootFS'),{'Type':'layers','Layers':receipt.get('rootfs')}),'normal_runtime')
 # These are the same Moby Config defaults used by the admitted LOAD validator.
 defaults={'Hostname':'','Domainname':'','User':'','AttachStdin':False,'AttachStdout':False,'AttachStderr':False,
  'ExposedPorts':None,'Tty':False,'OpenStdin':False,'StdinOnce':False,'Env':None,'Cmd':None,'Healthcheck':None,
  'ArgsEscaped':False,'Image':'','Volumes':None,'WorkingDir':'','Entrypoint':None,'NetworkDisabled':False,
  'MacAddress':'','OnBuild':None,'Labels':None,'StopSignal':'','StopTimeout':None,'Shell':None}
 config=actual.get('Config');need(type(config) is dict and set(config)<=set(defaults)
  and same({**defaults,**config},{**defaults,**CANONICAL_CONFIG}),'normal_runtime')
 if local!=CANDIDATE_CONFIG:
  association=receipt.get('source_identities',{}).get(local);descriptor=actual.get('Descriptor')
  need(type(association) is dict and association.get('kind') in ('OCI_manifest_to_config','OCI_index_to_manifest_to_config')
   and type(descriptor) is dict and descriptor.get('digest')==local
   and descriptor.get('mediaType')==association.get('media_type')
   and type(descriptor.get('size')) is int and descriptor['size']==association.get('size'),'normal_runtime')
 return actual

def env_map(values):
 need(type(values) is list and len(values)<=1024,'normal_runtime');result={}
 for value in values:
  need(type(value) is str and '=' in value and len(value)<=65536,'normal_runtime');key,content=value.split('=',1)
  need(re.fullmatch('[A-Za-z_][A-Za-z0-9_]*',key) and key not in result,'normal_runtime');result[key]=content
 return result
def clone_targets(values,aliases):
 """Only RAM values are inspected; no URL, user, password or digest is returned."""
 env=env_map(values)
 need(type(aliases) is dict and set(aliases)=={'db','redis'} and all(type(v) is dict and v for v in aliases.values()),'clone_target')
 for key,role,schemes,port in (('DATABASE_URL','db',{'postgres','postgresql','postgresql+asyncpg'},5432),
  ('SYNC_DATABASE_URL','db',{'postgres','postgresql','postgresql+psycopg2'},5432),('REDIS_URL','redis',{'redis','rediss'},6379)):
  need(key in env,'clone_target')
  try:
   value=urlsplit(env[key]);host=value.hostname;number=value.port or port
   need(type(host) is str and host in aliases[role] and value.scheme in schemes and number==port,'clone_target')
   try:ipaddress.ip_address(host)
   except ValueError:pass
   else:need(False,'clone_target')
   query=parse_qs(value.query,keep_blank_values=True)
   need(set(query)<={'sslmode','application_name','connect_timeout','command_timeout'},'clone_target')
  except (ValueError,TypeError):raise Refused('clone_target') from None
 if 'REDIS_HOST' in env:need(env['REDIS_HOST'] in aliases['redis'],'clone_target')
 if 'REDIS_PORT' in env:need(env['REDIS_PORT']=='6379','clone_target')
 need(not any(env.get(k) for k in ('PGHOSTADDR','PGSERVICE','PGSERVICEFILE','PGPASSFILE','PGSYSCONFDIR',
  'LD_PRELOAD','LD_LIBRARY_PATH','PYTHONPATH','PYTHONHOME','PYTHONSTARTUP','PYTHONUSERBASE')),'clone_target')
 need(not any(k.upper() in {'HTTP_PROXY','HTTPS_PROXY','ALL_PROXY'} and env[k] for k in env),'clone_target')
 return {'database_destinations_are_clones':True,'redis_destinations_are_clones':True}

def normal_role_scope(role,config,mounts,aliases):
 """Static normal-role eligibility; starting API does not execute these roles."""
 need(role in ('app','bot','sync_worker') and type(config) is dict and type(mounts) is list,'normal_runtime')
 command=config.get('Cmd')
 if role=='app':
  need(type(command) is list and len(command)==3 and command[:2]==['sh','-c'] and type(command[2]) is str
   and re.fullmatch(r'exec uvicorn main:app --host 0\.0\.0\.0 --port 8000 --workers [1-9][0-9]*',command[2]),'normal_runtime')
 else:need(same(command,['python','run_bot.py'] if role=='bot' else ['python','-m','core.sync_worker']),'normal_runtime')
 need(config.get('Entrypoint') in (None,[]) and all(type(r) is dict and r.get('Destination') in DATA_TARGETS for r in mounts),'mounts')
 clone_targets(config['Env'],aliases)
 return {'role':role,'normal_command_valid':True,'postgres_drivers_fixed':True,'loader_env_controlled':True,'source_mounts_absent':True}

def lease_identity(lease,control):
 need(type(lease) is dict and set(lease)=={'nonce','network_id','bridge','subnet','db','redis','volumes','api_mounts'},'owned_identity')
 need(lease['nonce']==control['nonce'] and cid(lease['network_id']) and re.fullmatch('nar[0-9a-f]{12}',lease['bridge']),'owned_identity')
 try:network=ipaddress.ip_network(lease['subnet'],strict=True)
 except (ValueError,TypeError):raise Refused('owned_identity') from None
 need(network.version==4 and network.is_private and 24<=network.prefixlen<=28,'owned_identity')
 for role in ('db','redis'):
  row=lease[role];need(type(row) is dict and set(row)=={'id','ip','aliases','artifact_sha256'} and cid(row['id']) and hex64(row['artifact_sha256'])
   and type(row['aliases']) is list and row['aliases'] and len(row['aliases'])<=8
   and all(type(v) is str and re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}',v) for v in row['aliases']),'owned_identity')
  try:address=ipaddress.ip_address(row['ip'])
  except (ValueError,TypeError):raise Refused('owned_identity') from None
  need(address in network and address not in (network.network_address,network.broadcast_address),'owned_identity')
 need(lease['db']['id']!=lease['redis']['id'] and lease['db']['ip']!=lease['redis']['ip'],'owned_identity')
 need(type(lease['volumes']) is dict and {'db','redis','uploads','audit'}<=set(lease['volumes']) and len(lease['volumes'])<=16
  and len(set(lease['volumes'].values()))==len(lease['volumes'])
  and all(type(v) is str and re.fullmatch('nar_'+control['nonce']+'_[a-z]+',v) for v in lease['volumes'].values()),'owned_identity')
 need(type(lease['api_mounts']) is list and 2<=len(lease['api_mounts'])<=14,'mounts')
 for row in lease['api_mounts']:
  need(type(row) is dict and row.get('Destination') in DATA_TARGETS and type(row.get('RW')) is bool,'mounts')
  if row.get('Type')=='bind':
   need(set(row)=={'Type','Destination','RW','Source','identity'} and row['Destination'] not in {'/app/uploads','/app/audit_trail'}
    and row['RW'] is False,'mounts');opaque_check(row)
  else:
   need(set(row)=={'Type','Destination','RW','Name','artifact_sha256'} and row['Type']=='volume'
    and row['Name'] in set(lease['volumes'].values())-{lease['volumes']['db'],lease['volumes']['redis']}
    and hex64(row['artifact_sha256']),'mounts')
 need(len({r['Destination'] for r in lease['api_mounts']})==len(lease['api_mounts'])
  and len({r['Name'] for r in lease['api_mounts'] if r['Type']=='volume'})==sum(r['Type']=='volume' for r in lease['api_mounts']),'mounts')
 return deepcopy(lease)

def opaque_check(row,*,initial=False):
 """Check only an authorized RO source reference; never open/read/hash payload."""
 path=Path(row['Source']);need(path.is_absolute() and path.resolve(strict=True)==path,'mounts')
 need(type(row['identity']) is list and len(row['identity'])==9 and all(type(v) is int for v in row['identity'])
  and stat.S_ISDIR(row['identity'][2]),'mounts')
 current=identity(path.lstat())
 if initial or row['Destination'] not in LIVE_MODEL_TARGETS:need(current==row['identity'],'mounts')
 else:need(current[:5]==row['identity'][:5] and current[5]>0,'mounts')
 for parent in path.parents:
  s=parent.lstat();need(parent.resolve(strict=True)==parent and stat.S_ISDIR(s.st_mode) and s.st_uid==s.st_gid==0 and not s.st_mode&0o022,'mounts')

BRIDGE_RECIPE={'family':'bridge','hooks':['input','forward','output'],'priority':0,'policy':'accept',
 'meta':['ibrname','obrname'],'input':['owned_arp_accept','owned_drop'],
 'forward':['owned_pair_arp_accept','owned_pair_ipv4_db_tcp5432_accept','owned_pair_ipv4_redis_tcp6379_accept',
 'owned_pair_ipv4_ct_established_related_accept','owned_input_drop','owned_output_drop'],
 'output':['owned_arp_accept','owned_drop'],'exclusive_create':True}
BRIDGE_POLICY_SHA=sha256(json.dumps(BRIDGE_RECIPE,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def bridge_capability(value,control,now=None):
 now=time.time() if now is None else now
 yes={'namespace_owned','exclusive_table_created','native_conntrack_rule_supported','three_hooks_supported',
  'exact_json_readback_passed','owned_table_deleted','owned_process_reaped','owned_namespace_handles_closed','foreign_rules_unchanged'}
 no={'explicit_global_module_or_sysctl_commands_used','app_or_private_data_used','actual_clone_isolation_claimed','security_accepted'}
 fields={'schema','machine_id','nonce','observed_at','valid_until','scope','kernel_release','nft_version','policy_sha256','namespace_identity'}|yes|no
 need(type(value) is dict and set(value)==fields and value['schema']=='normal_restore_native_bridge_capability/1'
  and value['machine_id']==control['machine_id'] and type(value['nonce']) is str and re.fullmatch('[a-f0-9]{32}',value['nonce'])
  and value['scope']=='owned_empty_network_namespace' and value['policy_sha256']==BRIDGE_POLICY_SHA,'isolation_unsupported')
 need(type(value['observed_at']) is type(value['valid_until']) is int
  and value['observed_at']<=now<value['valid_until']<=value['observed_at']+3600
  and value['observed_at']<=control['issued_at'] and control['deadline']<=value['valid_until'],'isolation_unsupported')
 need(type(value['kernel_release']) is str and re.fullmatch('[0-9][A-Za-z0-9_.+-]{2,99}',value['kernel_release'])
  and type(value['nft_version']) is str and re.fullmatch(r'1\.[0-9]+\.[0-9]+',value['nft_version'])
  and all(value[k] is True for k in yes) and all(value[k] is False for k in no),'isolation_unsupported')
 namespaces=value['namespace_identity'];need(type(namespaces) is dict and set(namespaces)=={'host','owned'}
  and all(type(v) is list and len(v)==2 and all(type(n) is int and n>0 for n in v) for v in namespaces.values())
  and namespaces['host']!=namespaces['owned'],'isolation_unsupported')
 return value

def bridge_rules(bridge,db,redis):
 need(type(bridge) is str and re.fullmatch('nar[0-9a-f]{12}',bridge),'isolation')
 for address in (db,redis):
  try:valid=type(address) is str and ipaddress.ip_address(address).version==4
  except ValueError:valid=False
  need(valid,'isolation')
 def match(left,right,op='=='):return {'match':{'op':op,'left':left,'right':right}}
 i=match({'meta':{'key':'ibrname'}},bridge);o=match({'meta':{'key':'obrname'}},bridge)
 arp=match({'payload':{'protocol':'ether','field':'type'}},'arp');ipv4=match({'payload':{'protocol':'ether','field':'type'}},'ip')
 rows=[{'chain':'host_input','expr':[i,arp,{'accept':None}]},{'chain':'host_input','expr':[i,{'drop':None}]},
  {'chain':'isolated_forward','expr':[i,o,arp,{'accept':None}]}]
 # Installed nft1.0.9 JSON omits redundant EtherIP beside IPv4 daddr; commands still include it.
 for address,port in ((db,5432),(redis,6379)):
  rows.append({'chain':'isolated_forward','expr':[i,o,match({'payload':{'protocol':'ip','field':'daddr'}},address),
   match({'payload':{'protocol':'tcp','field':'dport'}},port),{'accept':None}]})
 rows.extend([{'chain':'isolated_forward','expr':[i,o,ipv4,match({'ct':{'key':'state'}},['established','related'],'in'),{'accept':None}]},
  {'chain':'isolated_forward','expr':[i,{'drop':None}]},{'chain':'isolated_forward','expr':[o,{'drop':None}]},
  {'chain':'host_output','expr':[o,arp,{'accept':None}]},{'chain':'host_output','expr':[o,{'drop':None}]}])
 return rows

def bridge_summary(nonce,lease):
 return {'family':'bridge','table':'nar_'+nonce,'owner_nonce':nonce,'input_priority':0,'forward_priority':0,'output_priority':0,
  'bridge':lease['bridge'],'host_input':'drop_except_arp','host_output':'drop_except_arp','external_forward':'drop',
  'inter_container':'arp_ipv4_tcp_clone_ports_and_ct_returns_only','allowed':[{'destination':lease['db']['ip'],'port':5432},
  {'destination':lease['redis']['ip'],'port':6379}],'ipv6_vlan_other':'drop','foreign_rules_in_owned_table':0,'policy_sha256':BRIDGE_POLICY_SHA}

def isolation(v,lease,control,support):
 """A source-admitted producer must read actual engine/kernel state for this view.

The support receipt must authenticate installed Docker loopback-forwarding
semantics and the nft parser/measurement. There is no supported fallback here.
"""
 fields={'schema','nonce','network_id','internal','other_networks','published_ports','host_network','host_pid','host_ipc',
  'privileged','devices','dns','dns_search','dns_forwarding','docker_git_commit','support_sha256','bridge_capability_sha256','bridge_policy_sha256','rules','aliases','stores','volume_owners','expiry'}
 need(type(v) is dict and set(v)==fields and v['schema']=='normal_restore_network_isolation/2'
  and v['nonce']==control['nonce'] and v['network_id']==lease['network_id'],'isolation')
 need(v['internal'] is True and all(v[k] is False for k in ('host_network','host_pid','host_ipc','privileged'))
  and same(v['other_networks'],[]) and same(v['published_ports'],[]) and same(v['devices'],[]),'isolation')
 need(same(v['dns'],['127.0.0.1']) and same(v['dns_search'],['.'])
  and v['dns_forwarding']=='verified_container_loopback_only'
  and v['support_sha256']==control['isolation_support']['sha256']
  and v['docker_git_commit']==support['docker_git_commit']
  and v['bridge_capability_sha256']==control['bridge_capability']['sha256'] and v['bridge_policy_sha256']==BRIDGE_POLICY_SHA,'isolation_unsupported')
 aliases={role:{name:lease[role]['ip'] for name in lease[role]['aliases']} for role in ('db','redis')}
 need(same(v['aliases'],aliases),'isolation')
 stores={role:{'id':lease[role]['id'],'operation':OPERATION,'nonce':control['nonce'],'role':role,
  'network_id':lease['network_id'],'volume':lease['volumes'][role]} for role in ('db','redis')}
 need(same(v['stores'],stores) and same(v['volume_owners'],{name:{'operation':OPERATION,'nonce':control['nonce'],'role':role}
  for role,name in lease['volumes'].items()}),'owned_identity')
 need(same(v['expiry'],{'timer':'nar-expire-'+control['nonce']+'.timer','active':True,'producer_sha256':control['producer']['sha256'],
  'deadline':control['issued_at']+control['limits']['lifetime_seconds']})
  and time.time()<v['expiry']['deadline']<=control['deadline'],'deadline')
 # These are the exact parsed kernel semantics, never an arbitrary ready flag.
 expected=bridge_summary(control['nonce'],lease)
 need(same(v['rules'],expected),'isolation');return aliases

def host_mounts(rows):
 """Docker's Mount.ReadOnly omits false; preserve raw operands, model that default only."""
 need(type(rows) is list,'mounts');result=[]
 for row in rows:
  need(type(row) is dict and {'Type','Source','Target'}<=set(row)<= {'Type','Source','Target','ReadOnly'}
   and all(type(row[k]) is str for k in ('Type','Source','Target'))
   and ('ReadOnly' not in row or type(row['ReadOnly']) is bool),'mounts')
  result.append({**row,'ReadOnly':row.get('ReadOnly',False)})
 need(len({r['Target'] for r in result})==len(result),'mounts');return sorted(result,key=lambda r:r['Target'])
def runtime_parity(normal,measured,image_id,lease,control,*,stopped_required=True,engine_support=None):
 """Whole normal Config remains private; isolation changes only infrastructure."""
 need(type(normal) is dict and set(normal)=={'Config','HostConfig','Mounts'} and type(measured) is dict
  and set(measured)=={'id','image','Config','HostConfig','Mounts','network_id','running','pid'},'normal_runtime')
 need(cid(measured['id']) and measured['image']==image_id and same(normal['Config'],measured['Config']),'normal_runtime')
 host=measured['HostConfig'];expected=deepcopy(normal['HostConfig'])
 need(type(host) is dict and type(expected) is dict,'normal_runtime')
 if engine_support is not None:
  # Only the trusted producer's fresh /version + /info measurements supply
  # this private in-process operand; no control flag grants the exception.
  fields={'schema','engine_version','source_git_commit','engine_git_commit','support_docker_git_commit',
   'cgroup_version','oom_kill_disable_supported','kernel_release','machine_id'}
  need(type(engine_support) is dict and set(engine_support)==fields
   and engine_support['schema']=='normal_restore_started_oom_backend/1'
   and engine_support['engine_version']=='29.9.2'
   and engine_support['source_git_commit']=='0367cbeb49b9d98cb0b4f51e95f7b54b4803e76c'
   and type(engine_support['engine_git_commit']) is str and 7<=len(engine_support['engine_git_commit'])<=40
   and engine_support['source_git_commit'].startswith(engine_support['engine_git_commit'])
   and engine_support['support_docker_git_commit']==engine_support['engine_git_commit']
   and type(engine_support['cgroup_version']) is str and engine_support['cgroup_version'] in ('1','2')
   and type(engine_support['oom_kill_disable_supported']) is bool
   and type(engine_support['kernel_release']) is str
   and re.fullmatch('[0-9][A-Za-z0-9_.+-]{2,99}',engine_support['kernel_release'])
   and engine_support['machine_id']==control['machine_id'],'normal_runtime')
 if 'OomKillDisable' in expected:
  need(expected['OomKillDisable'] is None or type(expected['OomKillDisable']) is bool,'normal_runtime')
  if expected['OomKillDisable'] is None:
   expected['OomKillDisable']=False
   # Moby0367 adapts nil->false at CREATE, then START revalidates the
   # actual stored Resources and clears this unsupported pointer on v2.
   # Keep the created/stopped check strict; only a running clone with the
   # measured backend may retain the original normal null after START.
   if (stopped_required is False and measured['running'] is True
    and type(measured['pid']) is int and measured['pid']>0 and engine_support is not None
    and engine_support['cgroup_version']=='2' and engine_support['oom_kill_disable_supported'] is False
    and 'OomKillDisable' in host and host['OomKillDisable'] is None):expected['OomKillDisable']=None
 isolation_fields={'NetworkMode','PortBindings','PublishAllPorts','Dns','DnsSearch','ExtraHosts','Binds','Mounts','RestartPolicy','Memory','MemorySwap','NanoCpus','PidsLimit','LogConfig'}
 need(same({k:v for k,v in expected.items() if k not in isolation_fields},{k:v for k,v in host.items() if k not in isolation_fields}),'normal_runtime')
 need(host.get('NetworkMode')==lease['network_id'] and host.get('PortBindings') in ({},None)
  and host.get('PublishAllPorts') is False and same(host.get('Dns'),['127.0.0.1']) and same(host.get('DnsSearch'),['.'])
  and host.get('ExtraHosts') in ([],None) and same(host.get('RestartPolicy'),{'Name':'no','MaximumRetryCount':0})
  and host.get('Binds') in ([],None),'isolation')
 cap=control['limits']['api'];need(same({k:host.get(k) for k in ('Memory','MemorySwap','NanoCpus','PidsLimit','LogConfig')},
  {'Memory':cap['memory'],'MemorySwap':cap['memory'],'NanoCpus':cap['nano_cpus'],'PidsLimit':cap['pids'],
   'LogConfig':{'Type':'json-file','Config':{'max-size':str(cap['log_bytes']),'max-file':str(cap['log_files'])}}}),'normal_runtime')
 need(measured['network_id'] in ('',lease['network_id']) if stopped_required else measured['network_id']==lease['network_id'],'isolation')
 mounts=measured['Mounts'];need(type(mounts) is list,'mounts')
 expected_mounts=[{k:r[k] for k in ('Type','Destination','RW',('Name' if r['Type']=='volume' else 'Source'))} for r in lease['api_mounts']]
 need(same(sorted(mounts,key=lambda r:r['Destination']),sorted(expected_mounts,key=lambda r:r['Destination'])),'mounts')
 need(type(normal['Mounts']) is list and same(sorted([{'Destination':r['Destination'],'RW':False if r['Destination']=='/app/certs' else r['RW']} for r in normal['Mounts']],key=lambda r:r['Destination']),
  sorted([{'Destination':r['Destination'],'RW':r['RW']} for r in lease['api_mounts']],key=lambda r:r['Destination'])),'mounts')
 expected_host_mounts=[{'Type':r['Type'],'Source':r.get('Name',r.get('Source')),'Target':r['Destination'],'ReadOnly':not r['RW']} for r in lease['api_mounts']]
 need(same(host_mounts(host.get('Mounts',[])),host_mounts(expected_host_mounts)),'mounts')
 if stopped_required:need(measured['running'] is False and type(measured['pid']) is int and measured['pid']==0,'not_stopped')
 else:need(measured['running'] is True and type(measured['pid']) is int and measured['pid']>0,'normal_runtime')
 return measured['id']

def stopped(value,expected):
 need(type(value) is dict and same(value,{'id':expected,'running':False,'pid':0,'cgroup_populated':0,'descendants':0}),'not_stopped')
def ready(value,expected,image_id):
 need(type(value) is dict and set(value)=={'id','image','running','pid','started','restarts','normal_api_config_returncode'}
  and value['id']==expected and value['image']==image_id and value['running'] is True and type(value['pid']) is int and value['pid']>0
  and type(value['started']) is str and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z',value['started'])
  and type(value['restarts']) is int and value['restarts']==0
  and type(value['normal_api_config_returncode']) is int and value['normal_api_config_returncode']==0,'normal_runtime')
def data_state(value,head,*,persisted):
 need(type(value) is dict and set(value)=={'head','integrity_passed','normal_startup_completed','clone_writes_retained','persistence_verified'}
  and value['head']==head and all(value[k] is True for k in set(value)-{'head','persistence_verified'})
  and value['persistence_verified'] is persisted,'schema')

def isolation_support(value,control):
 need(type(value) is dict and set(value)=={'schema','producer_sha256','docker_source_sha256','docker_git_commit','machine_id','dns_backend','kernel_backend','bridge_capability_sha256','bridge_policy_sha256'}
  and value['schema']=='normal_restore_isolation_support/2' and value['producer_sha256']==control['producer']['sha256']
  and hex64(value['docker_source_sha256']) and type(value['docker_git_commit']) is str and re.fullmatch('[a-f0-9]{7,40}',value['docker_git_commit'])
  and value['machine_id']==control['machine_id'] and value['dns_backend']=='container_loopback_forwarding_verified'
  and value['kernel_backend']=='owned_native_bridge_input_forward_output'
  and value['bridge_capability_sha256']==control['bridge_capability']['sha256']
  and value['bridge_policy_sha256']==BRIDGE_POLICY_SHA,'isolation_unsupported')
 return value

def run_sequence(control,P,support,check=lambda:None):
 """No CLI. All actual IO belongs to the admitted normal producer, never tests."""
 need(type(P) is ModuleType and all(getattr(getattr(P,k,None),'__globals__',None) is P.__dict__ for k in METHODS),'producer')
 lease=None;requested=False;stage='input_admission'
 try:
  validate_control(control,int(time.time()));isolation_support(support,control);authority=deepcopy(control);check();P.check()
  # This authenticates artifacts through pinned normal verify_backup_receipt;
  # no protected artifact is opened by this contract or by its source fixtures.
  private=P.admit_normal_inputs(control,NORMAL_APIS,BACKUP_HELPERS)
  need(same(authority,control) and type(private) is dict and set(private)=={'normal','backup','head','mount_artifacts','role_scope'},'normal_backup')
  backup=private['backup'];need(type(backup) is dict and set(backup)=={'receipt_sha256','durable','prior_database_restore_passed','uploads_artifact_authenticated','redis_restore_contract','artifacts'}
   and backup['receipt_sha256']==control['backup_receipt']['sha256'] and all(backup[k] is True for k in ('durable','prior_database_restore_passed','uploads_artifact_authenticated'))
   and backup['redis_restore_contract']=='normal_persistence_format_authenticated','normal_backup')
  need(type(private['head']) is str and re.fullmatch('[a-f0-9]{12,40}',private['head']),'schema')
  need(type(private['normal']) is dict and set(private['normal'])=={'candidate','old'},'normal_runtime')
  scope=private['role_scope'];roles={'app','bot','sync_worker'} if control['site']=='bot' else {'app','sync_worker'}
  need(type(scope) is list and len(scope)==len(roles) and {r.get('role') for r in scope}==roles
   and all(same(r,{'role':r['role'],'normal_command_valid':True,'postgres_drivers_fixed':True,'loader_env_controlled':True,'source_mounts_absent':True}) for r in scope),'normal_runtime')
  artifacts=backup['artifacts'];need(type(artifacts) is dict and {'db','redis','uploads','audit'}<=set(artifacts) and len(artifacts)<=16,'normal_backup')
  for row in artifacts.values():need(type(row) is dict and set(row)=={'sha256','bytes'} and hex64(row['sha256']) and type(row['bytes']) is int and 0<row['bytes']<=1024**4,'normal_backup')
  mounts=private['mount_artifacts'];need(type(mounts) is dict and all(type(k) is str and hex64(v) and v in {r['sha256'] for r in artifacts.values()} for k,v in mounts.items()),'normal_backup')
  private_anchor=deepcopy(private)
  stage='restore'
  check();P.check();need(same(authority,control) and same(private,private_anchor),'normal_runtime')
  lease=P.restore_owned(control,private);anchor=lease_identity(lease,control)
  need(all(lease[role]['artifact_sha256']==artifacts[role]['sha256'] for role in ('db','redis'))
   and same({r['Destination']:r['artifact_sha256'] for r in lease['api_mounts'] if r['Type']=='volume'},mounts),'normal_backup')
  def checkpoints():
   need(time.time()<authority['deadline'],'deadline');check();P.check()
   need(same(authority,control) and same(private,private_anchor),'normal_runtime')
   need(same(anchor,lease_identity(lease,control)),'owned_identity')
  for label,image_id in (('candidate',control['candidate_image_id']),('old',control['old_image_id'])):
   checkpoints();stage=label+'_isolation';aliases=isolation(P.isolation(control,lease),lease,control,support)
   need(label in private['normal'],'normal_runtime');clone_targets(private['normal'][label]['Config']['Env'],aliases)
   stage=label+'_create';created=P.create_api(control,private,lease,label,image_id);need(cid(created),'owned_identity')
   measured=P.inspect_api(control,lease,created);need(runtime_parity(private['normal'][label],measured,image_id,lease,control)==created,'normal_runtime')
   # Re-measure containment after Docker creation and immediately before start.
   isolation(P.isolation(control,lease),lease,control,support);checkpoints();stage=label+'_start';requested=True
   P.start_api(control,lease,created);stage=label+'_ready';ready(P.ready_api(control,lease,created),created,image_id)
   stage=label+'_data';data_state(P.measure_data(control,lease,created),private['head'],persisted=label=='old');stage=label+'_stop'
   stopped(P.stop_api(control,lease,created),created);requested=False;checkpoints()
   if label=='candidate':
    stage='persistence';P.restart_owned_stores(control,lease);checkpoints()
  stage='cleanup';closed=P.cleanup_owned(control,lease);need(same(closed,{'nonce':control['nonce'],'containers_absent':True,'volumes_absent':True,
   'network_absent':True,'firewall_absent':True,'production_unchanged':True}),'cleanup');checkpoints()
  return {'schema':'normal_app_restore_compatibility/2','operation':OPERATION,'nonce':control['nonce'],'site':control['site'],
   'machine_id':control['machine_id'],'source_commit':SOURCE_COMMIT,'image_config_id':CANDIDATE_CONFIG,
   'candidate_local_image_id':control['candidate_image_id'],'bridge_capability_sha256':control['bridge_capability']['sha256'],'bridge_policy_sha256':BRIDGE_POLICY_SHA,'completed_at':int(time.time()),
   'candidate_on_restored_database_passed':True,'old_image_on_same_restored_database_passed':True,'database_head':private['head'],
   'normal_env_entrypoint_command_preserved':True,'normal_role_eligibility':deepcopy(private['role_scope']),
   'live_model_data_references':sorted(r['Destination'] for r in lease['api_mounts'] if r['Type']=='bind' and r['Destination'] in LIVE_MODEL_TARGETS),
   'live_model_contents_frozen':False,'live_model_data_equivalence':'NOT_TESTED','market_output_acceptance_claimed':False,
   'roles_executed':['app'],'isolated_persistence_passed':True,'cleanup_complete':True,
   'implicit_migration_permitted':False,'serving_validation':'NOT_TESTED','provider_sync_validation':'NOT_TESTED',
   'security_accepted':False,'full_matrix_accepted':False}
 except Exception as error:
  code=error.args[0] if type(error) is Refused and len(error.args)==1 and type(error.args[0]) is str and error.args[0] in CODES else 'unknown_outcome'
  # A failed/unknown mutation never grants cancellation or a cleanup retry.
  return {'schema':'normal_app_restore_failure/2','operation':OPERATION,
   'nonce':control.get('nonce') if type(control) is dict and type(control.get('nonce')) is str and re.fullmatch('[a-f0-9]{32}',control['nonce']) else None,
   'site':control.get('site') if type(control) is dict and control.get('site') in ('bot','web') else None,
   'stage':stage,'code':code,'start_outcome_may_be_unknown':requested,'owned_resources_retained':lease is not None or stage!='input_admission',
   'retry_allowed':False,'security_accepted':False,'full_matrix_accepted':False}

def execute(control,stack):
 """Root normal-workflow integration point. Missing support stays UNARMED."""
 need(os.geteuid()==os.getegid()==0,'unarmed');validate_control(control,int(time.time()))
 raw,capcheck=stack.enter_context(held_source(control['bridge_capability']));bridge_capability(json.loads(raw),control)
 raw,pcheck=stack.enter_context(held_source(control['producer']));P=producer(raw,control['producer']['path'])
 P._restore_stack=stack
 api=ModuleType('_normal_restore_contract_api');api.__dict__.update(globals());P._restore_contract=api
 checks=[pcheck,capcheck]
 for value in control['normal_api_sources'].values():_,check=stack.enter_context(held_source(value));checks.append(check)
 # This receipt is public prerequisite evidence, not a declaration that a
 # Docker flag or synthetic ready value proves actual network containment.
 raw,scheck=stack.enter_context(held_source(control['isolation_support']));checks.append(scheck)
 support=isolation_support(json.loads(raw),control)
 def check():
  for fn in checks:fn()
 return run_sequence(control,P,support,check)
