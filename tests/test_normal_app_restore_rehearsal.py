"""Genuine-root public contract fixtures; no backup, socket or daemon is read."""
from contextlib import ExitStack
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
import ast,json,os,tempfile,time,unittest,zipfile

ROOT=Path(__file__).resolve().parents[1]
DENIED=('subprocess.Popen','subprocess.run','os.system','os.popen','os.fork','os.kill','os.killpg',
 'socket.socket','socket.create_connection','socket.getaddrinfo','urllib.request.urlopen','ctypes.CDLL','resource.setrlimit')
def module():
 path=ROOT/'deploy/maintenance/normal_app_restore_rehearsal.py';m=ModuleType('_normal_restore_public_fixture');m.__file__=str(path)
 exec(compile(path.read_bytes(),str(path),'exec'),m.__dict__);return m
with ExitStack() as importing:
 for name in DENIED:importing.enter_context(patch(name,side_effect=AssertionError('ambient denied')))
 M=module()

class NormalRestoreTests(unittest.TestCase):
 def setUp(self):
  self.assertEqual((os.geteuid(),os.getegid()),(0,0),'genuine root fixture lane required')
  self.stack=ExitStack();self.addCleanup(self.stack.close)
  for name in DENIED:self.stack.enter_context(patch(name,side_effect=AssertionError('ambient denied')))
  self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.normal-restore-public-',dir=ROOT)));self.root.chmod(0o700)
 def binding(self,name,raw=b'public fixture'):
  path=self.root/name;path.write_bytes(raw);path.chmod(0o600);return {'path':str(path),'bytes':len(raw),'sha256':sha256(raw).hexdigest()}
 def capability(self,c):
  now=c['issued_at']
  return {'schema':'normal_restore_native_bridge_capability/1','machine_id':c['machine_id'],'nonce':'e'*32,'observed_at':now,
   'valid_until':now+3600,'scope':'owned_empty_network_namespace','kernel_release':'6.1.0-public-fixture','nft_version':'1.0.6',
   'policy_sha256':M.BRIDGE_POLICY_SHA,'namespace_identity':{'host':[1,2],'owned':[1,3]},
   **{k:True for k in ('namespace_owned','exclusive_table_created','native_conntrack_rule_supported','three_hooks_supported',
    'exact_json_readback_passed','owned_table_deleted','owned_process_reaped','owned_namespace_handles_closed','foreign_rules_unchanged')},
   **{k:False for k in ('explicit_global_module_or_sysctl_commands_used','app_or_private_data_used','actual_clone_isolation_claimed','security_accepted')}}
 def case(self):
  n='a'*32;now=int(time.time());c=M.unarmed_control();c.update(nonce=n,site='web',machine_id='b'*32,issued_at=now,deadline=now+120,
   candidate_image_id=M.CANDIDATE_CONFIG,old_image_id='sha256:'+'c'*64,producer=self.binding(M.PRODUCER),
   backup_receipt=self.binding('normal-backup.json'),normal_prefix=self.binding('normal-prefix.json'),isolation_support=self.binding('isolation-support.json'),
   normal_api_sources={k:{'path':str(self.root/Path(k).name),'bytes':1,'sha256':v} for k,v in M.NORMAL_APIS.items()},
   limits={**{role:{'memory':1024**3,'nano_cpus':1000000000,'pids':256,'log_bytes':1048576,'log_files':2} for role in ('api','db','redis')},'lifetime_seconds':120})
  c['bridge_capability']=self.binding('bridge-capability.json',json.dumps(self.capability(c)).encode())
  volumes={k:'nar_'+n+'_'+k for k in ('db','redis','uploads','audit')}
  lease={'nonce':n,'network_id':'d'*64,'bridge':'nar'+'a'*12,'subnet':'172.29.240.0/28',
   'db':{'id':'1'*64,'ip':'172.29.240.2','aliases':['db','trading_bot_db'],'artifact_sha256':'1'*64},
   'redis':{'id':'2'*64,'ip':'172.29.240.3','aliases':['redis','trading_bot_redis'],'artifact_sha256':'2'*64},'volumes':volumes,
   'api_mounts':[{'Type':'volume','Destination':'/app/uploads','RW':True,'Name':volumes['uploads'],'artifact_sha256':'3'*64},
    {'Type':'volume','Destination':'/app/audit_trail','RW':True,'Name':volumes['audit'],'artifact_sha256':'4'*64}]}
  host={'NetworkMode':'normal_trading_net','PortBindings':{'8000/tcp':[{'HostIp':'127.0.0.1','HostPort':'8000'}]},
   'PublishAllPorts':False,'Dns':[],'DnsSearch':[],'ExtraHosts':['production.example:192.0.2.9'],'Binds':[],
   'RestartPolicy':{'Name':'always','MaximumRetryCount':0},'Mounts':[], 'Privileged':False,'NetworkModePolicy':'normal_fixture',
   'CapAdd':None,'CapDrop':None,'ReadonlyRootfs':False,'Memory':0,'PidsLimit':None}
  config={'Env':['DATABASE_URL=postgresql+asyncpg://private:PRIVATE_PAYLOAD@db:5432/business',
   'SYNC_DATABASE_URL=postgresql+psycopg2://private:PRIVATE_PAYLOAD@db:5432/business',
   'REDIS_URL=redis://redis:6379/0','REDIS_HOST=redis','REDIS_PORT=6379','BACKGROUND_JOBS_ENABLED=true','TRADING_BOT_SERVICE=api'],
   'Cmd':['sh','-c','exec uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4'],'Entrypoint':None,
   'User':'','WorkingDir':'/app','Healthcheck':{'Test':['CMD-SHELL','normal public API check']},'Labels':{'public':'normal'},'Hostname':'normal-fixture'}
  normal={'Config':config,'HostConfig':host,'Mounts':[{'Type':'volume','Destination':r['Destination'],'RW':r['RW']} for r in lease['api_mounts']]}
  private={'normal':{'candidate':deepcopy(normal),'old':deepcopy(normal)},'head':'ff6c7d8e9f01',
   'backup':{'receipt_sha256':c['backup_receipt']['sha256'],'durable':True,'prior_database_restore_passed':True,
    'uploads_artifact_authenticated':True,'redis_restore_contract':'normal_persistence_format_authenticated',
    'artifacts':{role:{'sha256':str(i)*64,'bytes':100+i} for i,role in enumerate(('db','redis','uploads','audit'),1)}},
   'mount_artifacts':{r['Destination']:r['artifact_sha256'] for r in lease['api_mounts']},
   'role_scope':[{'role':role,'normal_command_valid':True,'postgres_drivers_fixed':True,'loader_env_controlled':True,'source_mounts_absent':True} for role in ('app','sync_worker')]}
  support={'schema':'normal_restore_isolation_support/2','producer_sha256':c['producer']['sha256'],'docker_source_sha256':'9'*64,
   'docker_git_commit':'0367cbe','machine_id':c['machine_id'],'dns_backend':'container_loopback_forwarding_verified',
   'kernel_backend':'owned_native_bridge_input_forward_output','bridge_capability_sha256':c['bridge_capability']['sha256'],'bridge_policy_sha256':M.BRIDGE_POLICY_SHA}
  network={'schema':'normal_restore_network_isolation/2','nonce':n,'network_id':lease['network_id'],'internal':True,
   'other_networks':[],'published_ports':[],'host_network':False,'host_pid':False,'host_ipc':False,'privileged':False,'devices':[],
   'dns':['127.0.0.1'],'dns_search':['.'],'dns_forwarding':'verified_container_loopback_only','docker_git_commit':'0367cbe',
   'support_sha256':c['isolation_support']['sha256'],'bridge_capability_sha256':c['bridge_capability']['sha256'],'bridge_policy_sha256':M.BRIDGE_POLICY_SHA,'aliases':{role:{alias:lease[role]['ip'] for alias in lease[role]['aliases']} for role in ('db','redis')},
   'rules':M.bridge_summary(n,lease)}
  network['stores']={role:{'id':lease[role]['id'],'operation':M.OPERATION,'nonce':n,'role':role,'network_id':lease['network_id'],
   'volume':volumes[role]} for role in ('db','redis')}
  network['volume_owners']={name:{'operation':M.OPERATION,'nonce':n,'role':role} for role,name in volumes.items()}
  network['expiry']={'timer':'nar-expire-'+n+'.timer','active':True,'producer_sha256':c['producer']['sha256'],'deadline':c['deadline']}
  return c,lease,private,support,network
 def measured(self,normal,lease,image_id,cid='e'*64):
  h=deepcopy(normal['HostConfig']);h.update(NetworkMode=lease['network_id'],PortBindings={},PublishAllPorts=False,Dns=['127.0.0.1'],DnsSearch=['.'],
   ExtraHosts=[],Binds=[],RestartPolicy={'Name':'no','MaximumRetryCount':0},
   Mounts=[{'Type':'volume','Source':r['Name'],'Target':r['Destination'],'ReadOnly':not r['RW']} for r in lease['api_mounts']])
  h.update(Memory=1024**3,MemorySwap=1024**3,NanoCpus=1000000000,PidsLimit=256,
   LogConfig={'Type':'json-file','Config':{'max-size':'1048576','max-file':'2'}})
  return {'id':cid,'image':image_id,'Config':deepcopy(normal['Config']),'HostConfig':h,'network_id':lease['network_id'],'running':False,'pid':0,
   'Mounts':[{'Type':'volume','Name':r['Name'],'Destination':r['Destination'],'RW':r['RW']} for r in lease['api_mounts']]}
 def provider(self,c,lease,private,network,fault=None):
  state={'calls':[],'private':private,'lease':lease,'network':network,'candidate_written':False,'persisted':False,'current':None,'fault':fault}
  code='''
def check():
 STATE['calls'].append('check')
def admit_normal_inputs(control,apis,helpers):
 STATE['calls'].append('admit');return STATE['private']
def restore_owned(control,private):
 STATE['calls'].append('restore');return STATE['lease']
def isolation(control,lease):
 STATE['calls'].append('isolation');return STATE['network']
def create_api(control,private,lease,label,image):
 STATE['calls'].append(label+'_create');STATE['current']=(label,image)
 if STATE['fault']=='expected_env_drift':private['normal'][label]['Config']['Env'].append('CUSTOM=PRIVATE_PAYLOAD')
 if STATE['fault']=='lease_swap':lease['volumes']['uploads']='nar_'+control['nonce']+'_foreign'
 return ('e' if label=='candidate' else 'f')*64
def inspect_api(control,lease,cid):
 label,image=STATE['current'];return MEASURE(STATE['private']['normal'][label],lease,image,cid)
def start_api(control,lease,cid):
 label,image=STATE['current'];STATE['calls'].append(label+'_start')
 if STATE['fault']=='start_unknown':raise RuntimeError('PRIVATE_PAYLOAD')
 if label=='candidate':STATE['candidate_written']=True
def ready_api(control,lease,cid):
 label,image=STATE['current'];STATE['calls'].append(label+'_ready')
 return {'id':cid,'image':image,'running':True,'pid':True if STATE['fault']=='ready_pid_bool' else 1234,'started':'2026-10-25T01:00:00Z','restarts':0,'normal_api_config_returncode':0}
def measure_data(control,lease,cid):
 label,image=STATE['current'];STATE['calls'].append(label+'_data')
 assert label!='old' or STATE['candidate_written'] and STATE['persisted']
 return {'head':'000000000000' if STATE['fault']=='head_drift' else STATE['private']['head'],
  'integrity_passed':True,'normal_startup_completed':True,'clone_writes_retained':True,'persistence_verified':label=='old'}
def stop_api(control,lease,cid):
 label,image=STATE['current'];STATE['calls'].append(label+'_stop')
 return {'id':cid,'running':False,'pid':0,'cgroup_populated':1 if STATE['fault']=='residual_tasks' else 0,'descendants':0}
def restart_owned_stores(control,lease):
 STATE['calls'].append('persist');STATE['persisted']=True
def cleanup_owned(control,lease):
 STATE['calls'].append('cleanup')
 return {'nonce':control['nonce'],'containers_absent':True,'volumes_absent':STATE['fault']!='cleanup_unknown',
  'network_absent':True,'firewall_absent':True,'production_unchanged':True}
'''
  p=M.producer(code.encode(),self.root/M.PRODUCER);p.STATE=state;p.MEASURE=self.measured;return p,state

 def public_load_receipt(self,site='web'):
     identities = {self.H.IMAGE['image_id']:{'kind':'Docker_config_identity'},
         self.H.IMAGE['archive_observation']['manifest_id']:{'kind':'OCI_manifest_to_config',
             'media_type':'application/vnd.oci.image.manifest.v1+json','size':5254},
         'sha256:'+self.H.IMAGE['archive_observation']['index_sha256']:{'kind':'OCI_index_to_manifest_to_config',
             'media_type':'application/vnd.oci.image.index.v1+json','size':241}}
     return {'schema':'canonical_app_preloaded_image/2','operation':'security-canonical-app-candidate-images-20261025-01',
         'site':'native' if site == 'bot' else 'web','host_machine_id':'a'*32,
         'host':'65.109.216.187' if site == 'bot' else '65.109.220.59',
         'catalog_sha256':self.H.LOAD_CATALOG,'controller_sha256':self.H.LOAD_CONTROLLER,'engine_sha256':self.H.LOAD_ENGINE,
         'health_policy_sha256':self.H.LOAD_HEALTH_POLICY,'health_adapter_sha256':self.H.LOAD_HEALTH_ADAPTER,
         'ci_run_id':self.H.IMAGE['run_id'],'image_artifact_sha256':self.H.IMAGE['image_zip_sha256'],
         'actual_ci_run_id':self.H.SMOKE['run_id'],'actual_ci_artifact_sha256':self.H.SMOKE['zip_sha256'],
         'role':'app','profile':'canonical_app','candidate_config_id':self.H.IMAGE['image_id'],
         'candidate_local_image_id':self.H.IMAGE['image_id'],'rootfs':self.H.IMAGE['rootfs']['Layers'],
         'source_archive_bytes':self.H.IMAGE['archive_observation']['archive_bytes'],
         'source_archive_sha256':self.H.IMAGE['archive_observation']['archive_sha256'],
         'source_identities':identities,'source_admission_sha256':'b'*64,'operation_sha256':'c'*64,'nonce':'d'*32,
         'archive_and_config_association_verified':True,'whole_typed_config_compared_in_ram':True,
         'physical_graph_closed':True,'runtime_policy_preserved':True,'serving_app_roles_unchanged':True,
         'serving_roles_activated':[],'serving_validation':'NOT_TESTED','configuration_values_disclosed':False,
         'runtime_health_issues_open':True,'capture_issue_open':True,'security_accepted':False,'runtime_health_observations':[]}
 def resource_load_receipt(self,site):
     value = self.public_load_receipt(site)
     value.update(schema='canonical_app_preloaded_image/3',controller_sha256=self.H.LOAD_V3_CONTROLLER,
         resource_contract_sha256=self.H.LOAD_RESOURCE_CONTRACT)
     if site == 'bot':value['native_resource_policy_unchanged'] = True
     else:value.update(resource_ram_floor_bytes=3021996032,aggregate_client_memory_max_bytes=536870912,
         metadata_stream_allowance_bytes=268435456,actual_tmpfs_payload_write_bytes=0,
         daemon_heap_quota_claimed=False,resource_contract_accepted=False)
     return value
 def mapped_case(self,site,local=None):
  self.H=__import__('types').ModuleType('_mapping_contract')
  source=ROOT/'deploy/maintenance/canonical_app_activation_contract.py'
  exec(compile(source.read_bytes(),str(source),'exec'),self.H.__dict__)
  receipt=self.resource_load_receipt(site);local=local or M.CANDIDATE_CONFIG
  receipt['candidate_local_image_id']=local
  self.assertEqual(self.H.host_load(receipt,site,{'machine_id':'a'*32}),local)
  actual={'Id':local,'Os':'linux','Architecture':'amd64','RootFS':{'Type':'layers','Layers':deepcopy(receipt['rootfs'])},'Config':deepcopy(M.CANONICAL_CONFIG)}
  if local!=M.CANDIDATE_CONFIG:
   association=receipt['source_identities'][local];actual['Descriptor']={'digest':local,'mediaType':association['media_type'],'size':association['size']}
  return receipt,actual,local
 def test_authenticated_manifest_mapping_and_classic_config_are_distinct(self):
  receipt,actual,local=self.mapped_case('bot');actual['Descriptor']=object()
  self.assertIs(M.candidate_association(actual,local,receipt),actual)
  manifest=self.H.IMAGE['archive_observation']['manifest_id']
  receipt,actual,local=self.mapped_case('web',manifest)
  self.assertIs(M.candidate_association(actual,local,receipt),actual)
  control,*_=self.case();control['candidate_image_id']=local;M.validate_control(control,time.time())
  # The classic branch must never consult Descriptor, even for bad unsupported data.
  tree=ast.parse((ROOT/'deploy/maintenance/normal_app_restore_rehearsal.py').read_bytes())
  f=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='candidate_association')
  reads=[n for n in ast.walk(f) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.args and isinstance(n.args[0],ast.Constant) and n.args[0].value=='Descriptor']
  self.assertEqual(len(reads),1)
  branch=next(n for n in f.body if isinstance(n,ast.If));self.assertIn(reads[0],list(ast.walk(branch)))
 def test_mapped_image_tampering_refuses_full_config_rootfs_and_descriptor(self):
  self.mapped_case('web');manifest=self.H.IMAGE['archive_observation']['manifest_id']
  receipt,actual,local=self.mapped_case('web',manifest)
  edits=[lambda v:v.update(Id=M.CANDIDATE_CONFIG),lambda v:v.update(Os='windows'),lambda v:v.update(Architecture='arm64'),
   lambda v:v['RootFS']['Layers'].reverse(),lambda v:v['Config'].update(User='nobody'),
   lambda v:v['Config'].update(Env=[*v['Config']['Env'],'UNADMITTED=true']),lambda v:v['Config'].update(Unknown=False),
   lambda v:v['Descriptor'].update(digest=M.CANDIDATE_CONFIG),lambda v:v['Descriptor'].update(size=True),
   lambda v:v['Descriptor'].update(mediaType='application/unknown'),lambda v:v.pop('Descriptor')]
  for edit in edits:
   changed=deepcopy(actual);edit(changed)
   with self.assertRaisesRegex(M.Refused,'normal_runtime'):M.candidate_association(changed,local,receipt)
  changed=deepcopy(receipt);changed['candidate_local_image_id']='sha256:'+'0'*64
  with self.assertRaisesRegex(RuntimeError,'host_load_gate'):self.H.host_load(changed,'web',{'machine_id':'a'*32})
 def test_full_public_config_literal_is_authenticated_canonical_build(self):
  path=ROOT/'tests/fixtures/canonical_app/canonical-build-665220a2-run40191605131.zip';raw=path.read_bytes()
  self.assertEqual(sha256(raw).hexdigest(),'eac66d11595ae9bd7a4478f5b126ac77e4d36b81dab25613f0d951c833d7b653')
  with zipfile.ZipFile(path) as archive:expected=json.loads(archive.read('reports/vulnerabilities.json'))['Metadata']['ImageConfig']['config']
  self.assertEqual(M.CANONICAL_CONFIG,expected)
 def test_unarmed_factory_performs_no_callbacks_or_mutations(self):
  with self.assertRaises(M.Refused):M.validate_control(M.unarmed_control(),time.time())
  with self.assertRaises(M.Refused):M.execute(M.unarmed_control(),self.stack)
 def test_public_control_is_typed_and_keeps_security_false(self):
  c,*_=self.case();M.validate_control(c,time.time())
  for key,bad in (('issued_at',True),('deadline',int(time.time())+3601),('source_commit','a'*40),('candidate_config_id','sha256:'+'0'*64),
   ('candidate_image_id',c['old_image_id']),('security_accepted',True),('nonce','../bad')):
   with self.subTest(key=key),self.assertRaises(M.Refused):M.validate_control({**c,key:bad},time.time())
 def test_actual_source_fd_seals_reject_replacement_mode_parent_and_hardlink_drift(self):
  b=self.binding('producer.py',b'PUBLIC SOURCE')
  with self.assertRaises(M.Refused):
   with M.held_source(b):
    p=Path(b['path']);p.rename(self.root/'old');p.write_bytes(b'PUBLIC SOURCE');p.chmod(0o600)
  p=Path(b['path']);p.chmod(0o666)
  with self.assertRaises(M.Refused):
   with M.held_source(b):pass
  p.chmod(0o644);b['sha256']=sha256(p.read_bytes()).hexdigest()
  os.link(p,self.root/'hardlink')
  with self.assertRaises(M.Refused):
   with M.held_source(b):pass
 def test_parent_drift_is_a_real_root_metadata_failure(self):
  folder=self.root/'owned';folder.mkdir(mode=0o700);b=self.binding('unused');b={'path':str(folder/'source.py'),'bytes':6,'sha256':sha256(b'public').hexdigest()}
  Path(b['path']).write_bytes(b'public');Path(b['path']).chmod(0o600)
  try:
   with self.assertRaises(M.Refused):
    with M.held_source(b):folder.chmod(0o777)
  finally:folder.chmod(0o700)
 def test_genuine_foreign_owner_is_rejected_before_any_payload_open(self):
  b=self.binding('wrong-owner.py',b'public source');path=Path(b['path']);os.chown(path,1,1);opened=[];real=os.open
  def opening(p,*args,**kwargs):opened.append(Path(p));return real(p,*args,**kwargs)
  try:
   self.assertEqual((path.stat().st_uid,path.stat().st_gid),(1,1))
   with patch.object(os,'open',side_effect=opening),self.assertRaises(M.Refused):
    with M.held_source(b):pass
   self.assertNotIn(path,opened)
  finally:os.chown(path,0,0)
 def test_provider_requires_functions_defined_in_its_admitted_namespace(self):
  with self.assertRaises(M.Refused):M.producer(b'from json import loads as check',self.root/M.PRODUCER)
  with self.assertRaises(M.Refused):M.producer(b'',self.root/M.PRODUCER)
 def test_unchanged_env_routes_only_normal_hostnames_to_clones_and_is_not_disclosed(self):
  c,l,p,s,n=self.case();values=p['normal']['candidate']['Config']['Env'];before=deepcopy(values)
  aliases=M.isolation(n,l,c,s);out=M.clone_targets(values,aliases);self.assertEqual(values,before)
  self.assertEqual(set(out),{'database_destinations_are_clones','redis_destinations_are_clones'});self.assertNotIn('PRIVATE_PAYLOAD',json.dumps(out))
 def test_literal_external_unix_alternate_host_and_proxy_destinations_refuse(self):
  c,l,p,s,n=self.case();values=p['normal']['candidate']['Config']['Env'];aliases=M.isolation(n,l,c,s)
  for replacement in ('DATABASE_URL=postgresql+asyncpg://u:p@192.0.2.9:5432/db','DATABASE_URL=postgresql+asyncpg:///db?host=/var/run/postgresql',
   'DATABASE_URL=postgresql+asyncpg://u:p@db:5432/db?hostaddr=192.0.2.9','DATABASE_URL=postgresql+asyncpg://u:p@production:5432/db',
   'DATABASE_URL=postgresql+asyncpg://u:p@db:9999/db'):
   with self.subTest(replacement=replacement),self.assertRaises(M.Refused):M.clone_targets([replacement,*values[1:]],aliases)
  for extra in ('HTTPS_PROXY=PRIVATE_PAYLOAD','PGHOSTADDR=192.0.2.9','REDIS_PORT=9999'):
   filtered=[v for v in values if not v.startswith(extra.split('=')[0]+'=')]
   with self.assertRaises(M.Refused):M.clone_targets([*filtered,extra],aliases)
 def test_internal_network_is_insufficient_without_host_dns_ipv6_and_forwarding_proofs(self):
  c,l,p,s,n=self.case();M.isolation(n,l,c,s)
  for key,bad in (('dns_forwarding','unproved'),('dns',['8.8.8.8']),('dns_search',['production']),('other_networks',['production']),
   ('published_ports',[8000]),('host_network',True),('privileged',True),('docker_git_commit','bad'),('support_sha256','0'*64)):
   with self.subTest(key=key),self.assertRaises(M.Refused):M.isolation({**n,key:bad},l,c,s)
  for key,bad in (('host_input','accept'),('external_forward','accept'),('ipv6','accept'),('foreign_rules_in_owned_table',True),('input_priority',-10)):
   changed=deepcopy(n);changed['rules'][key]=bad
   with self.assertRaises(M.Refused):M.isolation(changed,l,c,s)
 def test_native_bridge_capability_requires_actual_scoped_complete_fresh_evidence(self):
  c,*_=self.case();v=self.capability(c);M.bridge_capability(v,c,c['issued_at'])
  edits=[lambda x:x.update(schema='normal_restore_native_bridge_capability/0'),lambda x:x.update(machine_id='c'*32),
   lambda x:x.update(policy_sha256='0'*64),lambda x:x.update(scope='host_network'),lambda x:x.update(native_conntrack_rule_supported=False),
   lambda x:x.update(exclusive_table_created=1),lambda x:x.update(actual_clone_isolation_claimed=True),
   lambda x:x.update(explicit_global_module_or_sysctl_commands_used=True),lambda x:x['namespace_identity'].update(owned=[1,2]),
   lambda x:x['namespace_identity'].update(host=[True,2]),lambda x:x.update(observed_at=c['issued_at']+1),
   lambda x:x.update(valid_until=c['deadline']-1),lambda x:x.update(valid_until=c['issued_at']+3601)]
  for edit in edits:
   changed=deepcopy(v);edit(changed)
   with self.assertRaisesRegex(M.Refused,'isolation_unsupported'):M.bridge_capability(changed,c,c['issued_at'])
  with self.assertRaisesRegex(M.Refused,'isolation_unsupported'):M.bridge_capability(v,c,v['valid_until'])
 def test_bridge_rules_ten_closed_rules_cover_three_hooks_without_sysctls(self):
  c,l,p,s,n=self.case();rules=M.bridge_rules(l['bridge'],l['db']['ip'],l['redis']['ip'])
  self.assertEqual(len(rules),10);self.assertEqual([r['chain'] for r in rules],['host_input']*2+['isolated_forward']*6+['host_output']*2)
  self.assertEqual(rules[0]['expr'][-2]['match']['right'],'arp');self.assertEqual(rules[8]['expr'][-2]['match']['right'],'arp')
  self.assertEqual(rules[3]['expr'][2]['match']['left'],{'payload':{'protocol':'ip','field':'daddr'}});self.assertEqual(rules[4]['expr'][2]['match']['left'],{'payload':{'protocol':'ip','field':'daddr'}})
  for index in (1,6,7,9):self.assertEqual(rules[index]['expr'][-1],{'drop':None})
  for index in (5,):self.assertIn({'match':{'op':'==','left':{'payload':{'protocol':'ether','field':'type'}},'right':'ip'}},rules[index]['expr'])
  self.assertEqual(rules[3]['expr'][-2]['match']['right'],5432);self.assertEqual(rules[4]['expr'][-2]['match']['right'],6379)
  M.isolation(n,l,c,s)
  for key,bad in (('family','inet'),('output_priority',-10),('host_output','accept'),('ipv6_vlan_other','accept')):
   changed=deepcopy(n);changed['rules'][key]=bad
   with self.assertRaises(M.Refused):M.isolation(changed,l,c,s)
 def test_isolation_support_is_source_host_backend_bound_or_unsupported(self):
  c,l,p,s,n=self.case();M.isolation_support(s,c)
  for key,bad in (('producer_sha256','0'*64),('machine_id','0'*32),('docker_git_commit',None),('dns_backend','internal_network_only')):
   with self.assertRaises(M.Refused):M.isolation_support({**s,key:bad},c)
 def test_whole_normal_env_command_entrypoint_and_other_execution_fields_are_exact(self):
  c,l,p,s,n=self.case();normal=p['normal']['candidate'];row=self.measured(normal,l,c['candidate_image_id']);M.runtime_parity(normal,row,c['candidate_image_id'],l,c)
  for key,bad in (('Env',['CUSTOM=PRIVATE_PAYLOAD']),('Cmd',['python','special_probe.py']),('Entrypoint',['sh']),('User','10001'),('WorkingDir','/source')):
   changed=deepcopy(row);changed['Config'][key]=bad
   with self.assertRaises(M.Refused):M.runtime_parity(normal,changed,c['candidate_image_id'],l,c)
 def test_expected_null_OomKillDisable_matches_only_actual_false_with_every_other_field_exact(self):
  c,l,p,s,n=self.case();normal=p['normal']['candidate'];normal['HostConfig']['OomKillDisable']=None
  row=self.measured(normal,l,c['candidate_image_id']);row['HostConfig']['OomKillDisable']=False;anchor=deepcopy((normal,row))
  M.runtime_parity(normal,row,c['candidate_image_id'],l,c);self.assertEqual((normal,row),anchor)
  for value in (None,True,0,1,'false',[],{}):
   changed=deepcopy(row);changed['HostConfig']['OomKillDisable']=value
   with self.subTest(actual=value),self.assertRaises(M.Refused):M.runtime_parity(normal,changed,c['candidate_image_id'],l,c)
  for value in (0,1,'false',[],{}):
   before=deepcopy(normal);before['HostConfig']['OomKillDisable']=value;changed=deepcopy(row);changed['HostConfig']['OomKillDisable']=value
   with self.subTest(expected=value),self.assertRaises(M.Refused):M.runtime_parity(before,changed,c['candidate_image_id'],l,c)
  for old,new in ((True,False),(False,True)):
   before=deepcopy(normal);before['HostConfig']['OomKillDisable']=old;changed=deepcopy(row);changed['HostConfig']['OomKillDisable']=new
   with self.assertRaises(M.Refused):M.runtime_parity(before,changed,c['candidate_image_id'],l,c)
  before=deepcopy(normal);before['HostConfig']['OomKillDisable']=True;changed=deepcopy(row);changed['HostConfig']['OomKillDisable']=True
  M.runtime_parity(before,changed,c['candidate_image_id'],l,c)
  for edit in (lambda h:h.pop('OomKillDisable'),lambda h:h.update(Privileged=True),lambda h:h.update(Memory=1)):
   changed=deepcopy(row);edit(changed['HostConfig'])
   with self.assertRaises(M.Refused):M.runtime_parity(normal,changed,c['candidate_image_id'],l,c)
  before=deepcopy(normal);before['HostConfig'].pop('OomKillDisable')
  with self.assertRaises(M.Refused):M.runtime_parity(before,row,c['candidate_image_id'],l,c)
 def started_backend(self,c):
  return {'schema':'normal_restore_started_oom_backend/1','engine_version':'29.9.2',
   'source_git_commit':'0367cbeb49b9d98cb0b4f51e95f7b54b4803e76c','engine_git_commit':'0367cbe',
   'support_docker_git_commit':'0367cbe','cgroup_version':'2','oom_kill_disable_supported':False,
   'kernel_release':'6.1.0-public-fixture','machine_id':c['machine_id']}
 def test_started_cgroup2_null_has_only_the_typed_running_lifecycle_exception(self):
  c,l,p,s,n=self.case();normal=p['normal']['candidate'];normal['HostConfig']['OomKillDisable']=None
  row=self.measured(normal,l,c['candidate_image_id']);row['HostConfig']['OomKillDisable']=False;support=self.started_backend(c)
  M.runtime_parity(normal,row,c['candidate_image_id'],l,c,engine_support=support)
  row.update(running=True,pid=1234);row['HostConfig']['OomKillDisable']=None;anchor=deepcopy((normal,row,support))
  self.assertEqual(M.runtime_parity(normal,row,c['candidate_image_id'],l,c,stopped_required=False,engine_support=support),row['id'])
  self.assertEqual((normal,row,support),anchor)
  row.update(running=False,pid=0)
  M.stopped({'id':row['id'],'running':False,'pid':0,'cgroup_populated':0,'descendants':0},row['id'])
  with self.assertRaises(M.Refused):M.runtime_parity(normal,row,c['candidate_image_id'],l,c,engine_support=support)
 def test_missing_wrong_backend_version_or_boolean_normal_never_grants_started_null(self):
  c,l,p,s,n=self.case();normal=p['normal']['candidate'];normal['HostConfig']['OomKillDisable']=None
  row=self.measured(normal,l,c['candidate_image_id']);row.update(running=True,pid=1234);support=self.started_backend(c)
  bad=[None,{},False,{**support,'extra':'PRIVATE_SENTINEL'},{**support,'cgroup_version':'1'},
   {**support,'cgroup_version':2},{**support,'oom_kill_disable_supported':True},
   {**support,'oom_kill_disable_supported':0},{**support,'engine_version':'29.9.1'},
   {**support,'source_git_commit':'0'*40},{**support,'engine_git_commit':'0367'},
   {**support,'support_docker_git_commit':'0'*7},{**support,'kernel_release':False},
   {**support,'machine_id':'0'*32}]
  for witness in bad:
   with self.subTest(witness=witness),self.assertRaises(M.Refused):
    M.runtime_parity(normal,row,c['candidate_image_id'],l,c,stopped_required=False,engine_support=witness)
  for value in (False,True):
   before=deepcopy(normal);before['HostConfig']['OomKillDisable']=value
   with self.subTest(normal=value),self.assertRaises(M.Refused):
    M.runtime_parity(before,row,c['candidate_image_id'],l,c,stopped_required=False,engine_support=support)
  for key,value in (('Env',['PRIVATE_SENTINEL=DRIFT']),('Cmd',['wrong']),('Entrypoint',['wrong'])):
   changed=deepcopy(row);changed['Config'][key]=value
   with self.subTest(field=key),self.assertRaises(M.Refused):
    M.runtime_parity(normal,changed,c['candidate_image_id'],l,c,stopped_required=False,engine_support=support)
 def test_source_or_production_mounts_and_dropped_normal_mounts_refuse(self):
  c,l,p,s,n=self.case();normal=p['normal']['candidate'];row=self.measured(normal,l,c['candidate_image_id'])
  changed=deepcopy(row);changed['Mounts'][0].update(Type='bind',Name='/srv/production/uploads')
  with self.assertRaises(M.Refused):M.runtime_parity(normal,changed,c['candidate_image_id'],l,c)
  changed=deepcopy(row);changed['HostConfig']['Mounts'][0]['Source']='production_volume'
  with self.assertRaises(M.Refused):M.runtime_parity(normal,changed,c['candidate_image_id'],l,c)
  expected=deepcopy(normal);expected['Mounts'].append({'Destination':'/app/certs','RW':False})
  with self.assertRaises(M.Refused):M.runtime_parity(expected,row,c['candidate_image_id'],l,c)
  l['api_mounts'][0]['Destination']='/app/main.py'
  with self.assertRaises(M.Refused):M.lease_identity(l,c)
 def test_genuine_owned_store_and_volume_metadata_are_required_even_with_good_network_flags(self):
  c,l,p,s,n=self.case();changed=deepcopy(n);changed['stores']['db']['nonce']='f'*32
  with self.assertRaises(M.Refused):M.isolation(changed,l,c,s)
  changed=deepcopy(n);changed['volume_owners'][l['volumes']['uploads']]['operation']='production'
  with self.assertRaises(M.Refused):M.isolation(changed,l,c,s)
 def test_success_is_same_candidate_written_clone_then_old_image_with_persistence_and_cleanup(self):
  c,l,p,s,n=self.case();P,state=self.provider(c,l,p,n);result=M.run_sequence(c,P,s)
  self.assertEqual(result['schema'],'normal_app_restore_compatibility/2');self.assertFalse(result['security_accepted'])
  calls=[v for v in state['calls'] if v not in ('check','isolation')]
  self.assertEqual(calls,['admit','restore','candidate_create','candidate_start','candidate_ready','candidate_data','candidate_stop','persist',
   'old_create','old_start','old_ready','old_data','old_stop','cleanup'])
  self.assertNotIn('PRIVATE_PAYLOAD',json.dumps(result));self.assertEqual(result['provider_sync_validation'],'NOT_TESTED')
 def test_unsupported_network_prerequisite_stops_before_even_backup_admission(self):
  c,l,p,s,n=self.case();P,state=self.provider(c,l,p,n);s['dns_backend']='unsupported';result=M.run_sequence(c,P,s)
  self.assertEqual(state['calls'],[]);self.assertEqual(result['code'],'isolation_unsupported');self.assertFalse(result['owned_resources_retained'])
 def test_bad_isolation_stops_before_create_and_never_opens_or_starts_api(self):
  c,l,p,s,n=self.case();n['rules']['host_input']='accept';P,state=self.provider(c,l,p,n);result=M.run_sequence(c,P,s)
  self.assertEqual(result['code'],'isolation');self.assertFalse(any('create' in v or 'start' in v for v in state['calls']))
  self.assertTrue(result['owned_resources_retained']);self.assertNotIn('cleanup',state['calls'])
 def test_clone_destination_failure_stops_before_api_create(self):
  c,l,p,s,n=self.case();p['normal']['candidate']['Config']['Env'][0]='DATABASE_URL=postgresql://u:p@production/db'
  P,state=self.provider(c,l,p,n);result=M.run_sequence(c,P,s)
  self.assertEqual(result['code'],'clone_target');self.assertNotIn('candidate_create',state['calls'])
 def test_changed_expected_env_or_owned_volume_is_detected_before_start(self):
  for fault in ('expected_env_drift','lease_swap'):
   c,l,p,s,n=self.case();P,state=self.provider(c,l,p,n,fault);result=M.run_sequence(c,P,s)
   self.assertEqual(result['schema'],'normal_app_restore_failure/2');self.assertNotIn('candidate_start',state['calls'])
 def test_unknown_start_keeps_resources_and_primary_closed_without_retry_or_cleanup(self):
  c,l,p,s,n=self.case();P,state=self.provider(c,l,p,n,'start_unknown');result=M.run_sequence(c,P,s)
  self.assertEqual(result['code'],'unknown_outcome');self.assertTrue(result['start_outcome_may_be_unknown'])
  self.assertEqual(state['calls'].count('candidate_start'),1);self.assertNotIn('old_create',state['calls']);self.assertNotIn('cleanup',state['calls'])
  self.assertNotIn('PRIVATE_PAYLOAD',json.dumps(result))
 def test_head_or_residual_cgroup_failure_prevents_old_image_start(self):
  for fault,code in (('head_drift','schema'),('residual_tasks','not_stopped')):
   c,l,p,s,n=self.case();P,state=self.provider(c,l,p,n,fault);result=M.run_sequence(c,P,s)
   self.assertEqual(result['code'],code);self.assertNotIn('old_create',state['calls']);self.assertNotIn('cleanup',state['calls'])
   self.assertEqual(result['stage'],'candidate_data' if fault=='head_drift' else 'candidate_stop')
 def test_cleanup_unproved_never_claims_compatibility(self):
  c,l,p,s,n=self.case();P,state=self.provider(c,l,p,n,'cleanup_unknown');result=M.run_sequence(c,P,s)
  self.assertEqual(result['schema'],'normal_app_restore_failure/2');self.assertEqual(result['code'],'cleanup');self.assertEqual(state['calls'].count('cleanup'),1)
 def test_untyped_readiness_prevents_data_or_old_image_claim(self):
  c,l,p,s,n=self.case();P,state=self.provider(c,l,p,n,'ready_pid_bool');result=M.run_sequence(c,P,s)
  self.assertEqual(result['code'],'normal_runtime');self.assertNotIn('candidate_data',state['calls']);self.assertNotIn('old_create',state['calls'])
  self.assertEqual(result['stage'],'candidate_ready')
 def test_late_source_check_after_cleanup_prevents_green_without_any_retry(self):
  c,l,p,s,n=self.case();P,state=self.provider(c,l,p,n)
  def check():
   if 'cleanup' in state['calls']:raise M.Refused('source_changed')
  result=M.run_sequence(c,P,s,check)
  self.assertEqual(result['code'],'source_changed');self.assertEqual(state['calls'].count('cleanup'),1);self.assertNotIn('candidate_on_restored_database_passed',result)
 def test_no_normal_api_source_drift_or_arbitrary_rehearsal_import_is_accepted(self):
  for path,pin in M.NORMAL_APIS.items():self.assertEqual(sha256((ROOT/path).read_bytes()).hexdigest(),pin)
  raw=(ROOT/'deploy/maintenance/normal_app_restore_rehearsal.py').read_bytes()
  for denied in (b'canonical_app_activation_',b'run_guarded_scratch_alembic',b'os.environ',b'docker run',b'/source:ro',b'--env'):
   self.assertNotIn(denied,raw)
 def test_fixed_all_site_role_commands_and_loaders_are_checked_without_claiming_execution(self):
  c,l,p,s,n=self.case();aliases=M.isolation(n,l,c,s)
  for role,command in (('app',['sh','-c','exec uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4']),
   ('bot',['python','run_bot.py']),('sync_worker',['python','-m','core.sync_worker'])):
   cfg=deepcopy(p['normal']['candidate']['Config']);cfg['Cmd']=command
   self.assertTrue(M.normal_role_scope(role,cfg,p['normal']['candidate']['Mounts'],aliases)['loader_env_controlled'])
   cfg['Env'].append('LD_PRELOAD=/app/runtime/unknown.so')
   with self.assertRaises(M.Refused):M.normal_role_scope(role,cfg,p['normal']['candidate']['Mounts'],aliases)

if __name__=='__main__':unittest.main()
