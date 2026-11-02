"""Finite synthetic Engine tests; never contacts Docker, systemd or real data."""
from pathlib import Path
from contextlib import redirect_stdout
from copy import deepcopy
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import urlsplit,parse_qs
from types import SimpleNamespace
import importlib.util,io,json,time,unittest
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('_activation',ROOT/'activate_stores.py');M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)
CID=lambda n:format(n,'064x')
IID=lambda n:'sha256:'+CID(n)
NET=CID(999)
PRIVATE='PRIVATE_AUTH_NEVER_PRINT'
def fixture(site):
 stores=[];rows={};roles=sorted(M.SCOPE[site]);nextid=1
 for role in roles:
  cid=CID(nextid);nextid+=1;kind='redis'if role.endswith('redis')else'postgres';data='/data'if kind=='redis'else'/var/lib/postgresql/data'
  image=IID(nextid+100);candidate=IID(501 if kind=='redis'else 502);project=role;name=role;address='172.20.0.'+str(nextid)
  mounts=[{'Type':'volume','Name':role+'-data','Source':'/PUBLIC/'+role,'Destination':data,'RW':True,'Propagation':''}]
  config={'Image':image,'Env':['POSTGRES_PASSWORD='+PRIVATE],'Cmd':['redis-server','--appendonly','yes','--requirepass',PRIVATE]if kind=='redis'else['postgres'],'Entrypoint':['docker-entrypoint.sh'],'User':'','WorkingDir':data,'Volumes':{data:{}},'Labels':{'com.docker.compose.project':project,'com.docker.compose.service':'store','com.docker.compose.image':image}}
  host={'OomKillDisable':None,'NetworkMode':project+'_default','RestartPolicy':{'Name':'unless-stopped','MaximumRetryCount':0},'Mounts':[{'Type':'volume','Source':role+'-data','Target':data}]}
  ep={'NetworkID':NET,'Aliases':[name,'store'],'IPAMConfig':{'IPv4Address':address},'MacAddress':'02:42:ac:14:00:'+format(nextid,'02x'),'GwPriority':0}
  rows[cid]={'Id':cid,'Image':image,'Name':'/'+name,'Config':config,'HostConfig':host,'Mounts':mounts,'NetworkSettings':{'Networks':{project+'_default':ep}},'State':{'Running':True,'Pid':nextid+1000}}
  stores.append({'role':role,'old_cid':cid,'old_image':image,'new_image':candidate,'canonical_config':IID(502 if kind=='postgres'else 501),'project':project,'service':'store','mounts':[{'Source':mounts[0]['Source'],'Destination':data,'RW':True}]})
 clients=[]
 for n in range(2):
  cid=CID(100+n);row={'Id':cid,'Image':IID(900+n),'Name':'/client'+str(n),'Config':{'Env':['PRIVATE='+PRIVATE]},'HostConfig':{'RestartPolicy':{'Name':'always','MaximumRetryCount':0}},'Mounts':[],'NetworkSettings':{'Networks':{}},'State':{'Running':True,'Pid':2000+n,'Health':{'Status':'healthy'}}};rows[cid]=row;clients.append({'cid':cid,'image':row['Image'],'probe_argv':['PUBLIC_PROBE']})
 return {'schema':'normal_datastore_activation/1','operation_id':'a'*32,'site':site,'stores':stores,'clients':clients,'qualified_images_and_data_drills_accepted_by_Root':True,'fence':{'path':'/PUBLIC/FENCE','sha256':'a'*64,'units':[],'dropin_name':'owned.conf','no_Host_readers':True}},rows
class Engine:
 def __init__(self,rows):self.rows=deepcopy(rows);self.end=time.time()+1800;self.calls=[];self.nextid=10000;self.failure=None;self.bad_new_version=False;self.bad_role=None;self.requested={}
 def inspect(self,cid):return deepcopy(self.rows[cid])
 def image(self,image):
  config={'Labels':{}}
  if self.failure=='image_default_labels':config['Labels']={'org.opencontainers.image.revision':'b'*40,'io.gold-trade.security.recipe':'redis7412-corrected'}
  return {'Id':image,'Config':config}
 def request(self,method,path,value=None,**kw):
  self.calls.append((method,path,deepcopy(value)));u=urlsplit(path);parts=u.path.strip('/').split('/')
  if method=='GET'and u.path=='/containers/json':return[{'Id':r['Id'],'State':'running'if r['State']['Running']else'exited','Mounts':r['Mounts']}for r in self.rows.values()]
  if method=='POST'and parts[:2]==['containers','create']:
   if self.failure=='create_before':raise RuntimeError('normal_docker_unknown')
   cid=CID(self.nextid);self.nextid+=1;config={k:deepcopy(v)for k,v in value.items()if k not in('HostConfig','NetworkingConfig')};host=deepcopy(value['HostConfig']);mounts=[]
   for old in self.rows.values():
    if old['HostConfig'].get('Mounts')==host.get('Mounts'):mounts=deepcopy(old['Mounts']);break
   eps={net:{**deepcopy(ep),'NetworkID':'','MacAddress':''}for net,ep in value['NetworkingConfig']['EndpointsConfig'].items()}
   self.requested[cid]=deepcopy(value['NetworkingConfig']['EndpointsConfig'])
   self.rows[cid]={'Id':cid,'Image':config['Image'],'Name':'/'+parse_qs(u.query)['name'][0],'Config':config,'HostConfig':host,'Mounts':mounts,'NetworkSettings':{'Networks':eps},'State':{'Running':False,'Pid':0,'Status':'created','StartedAt':'0001-01-01T00:00:00Z'}}
   if self.failure=='image_default_labels':self.rows[cid]['Config']['Labels']={**self.image(config['Image'])['Config']['Labels'],**config['Labels']}
   if self.failure=='materialize_oom_default':self.rows[cid]['HostConfig']['OomKillDisable']=False
   if self.failure=='created_material_host_drift':self.rows[cid]['HostConfig']['OomKillDisable']=True
   if self.failure=='create_after':raise RuntimeError('normal_docker_unknown')
   return {'Id':cid}
  if parts[0]=='networks':
   cid=value['Container']
   if parts[2]=='disconnect':
    if self.failure=='disconnect':raise RuntimeError('normal_docker_unknown')
    self.rows[cid]['NetworkSettings']['Networks']={k:ep for k,ep in self.rows[cid]['NetworkSettings']['Networks'].items()if ep.get('NetworkID')!=parts[1]and k!=parts[1]}
   else:
    if self.failure=='connect_noop':return None
    self.rows[cid]['NetworkSettings']['Networks'][parts[1]]={**deepcopy(value['EndpointConfig']),'NetworkID':parts[1]}
   return None
  cid=parts[1];row=self.rows[cid];action=parts[2]
  if action=='update':row['HostConfig']['RestartPolicy']=deepcopy(value['RestartPolicy']);return {}
  if action=='stop':row['State'].update(Running=False,Pid=0);return None
  if action=='rename':row['Name']='/'+parse_qs(u.query)['name'][0];return None
  if action=='start':
   row['State'].update(Running=True,Pid=3000,Status='running',StartedAt='2026-11-02T00:00:00Z');row['State']['Health']={'Status':'healthy'}
   if self.failure=='client_cached_unhealthy_after_start'and cid==CID(100):row['State']['Health']['Status']='unhealthy'
   for net,ep in row['NetworkSettings']['Networks'].items():
    if not ep.get('NetworkID'):
     ep['NetworkID']=net;ep['MacAddress']=self.requested[cid][net].get('MacAddress','')
   return None
  raise AssertionError((method,path))
 def execute(self,cid,argv,**kw):
  row=self.rows[cid];new=M.LABEL in row['Config'].get('Labels',{});bad=self.bad_new_version and(self.bad_role is None or row['Config'].get('Labels',{}).get('com.docker.compose.project')==self.bad_role)
  if argv==['ACTUAL_CAPTURE_HEALTH']:return 0,b'OK'
  if argv==['WRONG_GENERIC_PROBE']:return 1,b''
  if argv==['PUBLIC_PROBE']:
   if cid==CID(100)and self.failure=='first_client_needs_second':
    if not self.rows[CID(101)]['State']['Running']:raise RuntimeError('dependent_client_not_started')
   if cid==CID(100)and self.failure=='first_client_health_not_ready'and any(M.LABEL in r['Config'].get('Labels',{})for r in self.rows.values()):return 1,b''
   return 0,b'OK'
  if argv[0]=='/bin/sh':
   assert '-h127.0.0.1'in argv[-1];return 0,((b'150020'if bad else b'150019')if new else b'150015')+b'|1\n'
  if argv[-1]=='PING':return 0,b'PONG\n'
  if argv[-2:]==['INFO','server']:return 0,('redis_version:'+('7.4.99'if bad and new else'7.4.12'if new else'7.4.11')+'\r\n').encode()
  raise AssertionError(argv)
class Tests(unittest.TestCase):
 def run_case(self,site='native',failure=None,bad=False,bad_role=None):
  folder=TemporaryDirectory(dir=ROOT);self.addCleanup(folder.cleanup);plan,rows=fixture(site);engine=Engine(rows);engine.failure=failure;engine.bad_new_version=bad;engine.bad_role=bad_role;state=Path(folder.name)/'fresh';op=M.Activation(plan,state,time.time()+1800,engine);out=io.StringIO()
  with redirect_stdout(out),patch.object(op,'fence',return_value=None):
   try:result=op.run();error=None
   except Exception as e:result=json.loads((state/'receipt.json').read_bytes());error=e
  self.assertNotIn(PRIVATE,out.getvalue());self.assertNotIn(PRIVATE,(state/'receipt.json').read_text());self.assertNotIn(PRIVATE,(state/'runtime-metadata.json').read_text());return plan,rows,engine,op,result,error
 def test_full_two_and_three_store_paths_preserve_data_config_and_only_one_serving_owner(self):
  for site in('native','web'):
   with self.subTest(site=site):
    plan,rows,e,op,result,error=self.run_case(site);self.assertIsNone(error);self.assertEqual(result['status'],'APPLIED_RUNTIME_ONLY')
    for s in op.stores:
     old=e.rows[s['old']['Id']];new=e.rows[s['new']];self.assertFalse(old['State']['Running']);self.assertEqual(old['HostConfig']['RestartPolicy'],M.NO_RESTART);self.assertFalse(old['NetworkSettings']['Networks']);self.assertTrue(new['State']['Running']);self.assertEqual(M.mounts(old),M.mounts(new));self.assertEqual(new['Config']['Env'],s['old']['Config']['Env'])
    self.assertTrue(all(e.rows[c['cid']]['State']['Running']for c in plan['clients']))
 def test_unknown_detach_or_create_never_retries_or_starts_retained_old(self):
  for failure in('disconnect','create_before','create_after'):
   with self.subTest(failure=failure):
    plan,rows,e,op,result,error=self.run_case(failure=failure);self.assertIsNotNone(error);self.assertEqual(result['status'],'NEEDS_RECONCILIATION');self.assertIsNotNone(result['pending_requested_action'])
    creates=[c for c in e.calls if c[1].startswith('/containers/create')];self.assertLessEqual(len(creates),1);self.assertFalse(any(c[1].endswith('/start')for c in e.calls))
 def test_known_wrong_candidate_runtime_restores_exact_old_and_preserves_failure(self):
  plan,rows,e,op,result,error=self.run_case(bad=True);self.assertIsNotNone(error);self.assertEqual(result['status'],'FAILED_RESTORED_OLD');self.assertEqual(result['primary_error'],'datastore_version');self.assertIn(result['primary_failure_phase'],M.SCOPE['native'])
  for s in op.stores:
   if s['changed']:self.assertEqual(e.rows[s['old']['Id']]['Name'],s['old']['Name']);self.assertTrue(e.rows[s['old']['Id']]['State']['Running'])
  self.assertTrue(all(e.rows[c['cid']]['State']['Running']for c in plan['clients']))
 def test_late_third_store_failure_restores_both_previously_healthy_store_candidates(self):
  plan,rows,e,op,result,error=self.run_case('web',bad=True,bad_role='web-redis');self.assertIsNotNone(error);self.assertEqual(result['status'],'FAILED_RESTORED_OLD');self.assertEqual(len([s for s in op.stores if s['changed']]),3)
  for s in op.stores:
   self.assertTrue(e.rows[s['old']['Id']]['State']['Running']);self.assertEqual(e.rows[s['old']['Id']]['Name'],s['old']['Name']);self.assertFalse(e.rows[s['new']]['State']['Running']);self.assertFalse(e.rows[s['new']]['NetworkSettings']['Networks'])
 def test_foreign_running_same_data_writer_refuses_before_any_mutating_call(self):
  plan,rows=fixture('native');extra=deepcopy(rows[plan['stores'][0]['old_cid']]);extra['Id']=CID(777);rows[extra['Id']]=extra;e=Engine(rows)
  with TemporaryDirectory(dir=ROOT)as d:
   op=M.Activation(plan,Path(d)/'fresh',time.time()+1800,e)
   with redirect_stdout(io.StringIO()),patch.object(op,'fence'),self.assertRaisesRegex(RuntimeError,'another_data_writer_running'):op.run()
   self.assertFalse(any(m=='POST'for m,_,_ in e.calls))
 def test_explicit_fence_binds_fresh_scope_loaded_dropin_and_inactive_unit(self):
  with TemporaryDirectory(dir=ROOT)as d:
   base=Path(d);deadline=int(time.time())+1800;token=base/'FENCE';body={'schema':'normal_datastore_client_fence/1','operation_id':'a'*32,'issued_epoch':int(time.time()),'deadline_epoch':deadline};raw=json.dumps(body).encode();token.write_bytes(raw);token.chmod(0o600);unit='reader.service';drop=base/'units'/(unit+'.d')/'owned.conf';drop.parent.mkdir(parents=True);drop.write_text('[Unit]\nConditionPathExists=!'+str(token)+'\n');drop.chmod(0o644)
   plan,rows=fixture('native');plan['fence']={'path':str(token),'sha256':M.sha256(raw).hexdigest(),'units':[unit],'dropin_name':'owned.conf','no_Host_readers':False};op=M.Activation(plan,base/'fresh',deadline,Engine(rows));actual_path=M.Path
   def mapped(path):return base/'units'if str(path)=='/etc/systemd/system'else actual_path(path)
   value={'ActiveState':'inactive','SubState':'dead','MainPID':'0','DropInPaths':str(drop),'NeedDaemonReload':'no'}
   with patch.object(M,'Path',side_effect=mapped),patch.object(M,'unit_state',return_value=value):op.fence()
   for change in({'ActiveState':'active'},{'NeedDaemonReload':'yes'}):
    with patch.object(M,'Path',side_effect=mapped),patch.object(M,'unit_state',return_value={**value,**change}),self.assertRaisesRegex(RuntimeError,'host_client_not_quiesced'):op.fence()
   wrong={**body,'operation_id':'b'*32};raw=json.dumps(wrong).encode();token.write_bytes(raw);plan['fence']['sha256']=M.sha256(raw).hexdigest()
   with self.assertRaisesRegex(RuntimeError,'client_fence_scope'):op.fence()
 def test_unknown_no_op_network_connect_never_claims_old_runtime_restored(self):
  plan,rows,e,op,result,error=self.run_case(failure='connect_noop',bad=True);self.assertIsNotNone(error);self.assertEqual(result['status'],'NEEDS_RECONCILIATION');self.assertEqual(result['primary_error'],'datastore_version')
  changed=next(s for s in op.stores if s['changed']);self.assertFalse(e.rows[changed['old']['Id']]['State']['Running']);self.assertFalse(e.rows[changed['old']['Id']]['NetworkSettings']['Networks'])
 def test_unit_metadata_uses_bounded_file_not_unbounded_pipe(self):
  def output(argv,**kw):
   self.assertIsNot(kw['stdout'],M.subprocess.PIPE);self.assertIn('preexec_fn',kw);kw['stdout'].write(b'ActiveState=inactive\nMainPID=0\n');return SimpleNamespace(returncode=0)
  with patch.object(M.subprocess,'run',side_effect=output):self.assertEqual(M.unit_state('reader.service')['MainPID'],'0')
  def oversized(argv,**kw):kw['stdout'].write(b'x'*9000);return SimpleNamespace(returncode=0)
  with patch.object(M.subprocess,'run',side_effect=oversized),self.assertRaisesRegex(RuntimeError,'client_unit_unknown'):M.unit_state('reader.service')
 def test_insufficient_fresh_clock_refuses_before_any_client_or_store_mutation(self):
  plan,rows=fixture('native');e=Engine(rows)
  with TemporaryDirectory(dir=ROOT)as d:
   state=Path(d)/'fresh';op=M.Activation(plan,state,time.time()+500,e)
   with redirect_stdout(io.StringIO()),patch.object(op,'fence'),self.assertRaisesRegex(RuntimeError,'finite_deadline'):op.run()
   self.assertFalse(e.calls);self.assertFalse(state.exists())
 def test_client_dependency_starts_full_original_cohort_before_first_health_probe(self):
  plan,rows,e,op,result,error=self.run_case('web',failure='first_client_needs_second');self.assertIsNone(error);self.assertEqual(result['status'],'APPLIED_RUNTIME_ONLY');self.assertTrue(all(e.rows[c['cid']]['State']['Running']for c in plan['clients']))
  for client in plan['clients']:self.assertEqual(e.rows[client['cid']]['Config'],rows[client['cid']]['Config']);self.assertEqual(e.rows[client['cid']]['HostConfig'],rows[client['cid']]['HostConfig'])
 def test_persistent_first_health_failure_does_not_leave_later_original_clients_stopped(self):
  clock=[time.time()]
  with patch.object(M.time,'time',side_effect=lambda:clock[0]),patch.object(M.time,'sleep',side_effect=lambda sec:clock.__setitem__(0,clock[0]+sec)):
   plan,rows,e,op,result,error=self.run_case('web',failure='first_client_health_not_ready')
  self.assertIsNotNone(error);self.assertEqual(result['status'],'NEEDS_RECONCILIATION');self.assertEqual(result['primary_error'],'client_not_ready');self.assertIsNone(result['pending_requested_action']);self.assertTrue(all(e.rows[c['cid']]['State']['Running']for c in plan['clients']))
  for client in plan['clients']:self.assertEqual(e.rows[client['cid']]['Config'],rows[client['cid']]['Config']);self.assertEqual(e.rows[client['cid']]['HostConfig'],rows[client['cid']]['HostConfig'])
  self.assertTrue(all(e.rows[store['old_cid']]['State']['Running']for store in plan['stores']))
 def test_actual_declared_health_contract_CMD_and_CMD_SHELL_preserved_exactly(self):
  self.assertEqual(M.configured_health_argv({'Healthcheck':{'Test':['CMD','tool','arg with spaces']}}),['tool','arg with spaces'])
  self.assertEqual(M.configured_health_argv({'Healthcheck':{'Test':['CMD-SHELL','echo $PRIVATE']}}),['/bin/sh','-c','echo $PRIVATE'])
  self.assertEqual(M.configured_health_argv({'Shell':['/bin/bash','-o','pipefail','-c'],'Healthcheck':{'Test':['CMD-SHELL','echo $PRIVATE']}}),['/bin/bash','-o','pipefail','-c','echo $PRIVATE'])
  self.assertIsNone(M.configured_health_argv({'Healthcheck':{'Test':['NONE']}}))
  with self.assertRaisesRegex(RuntimeError,'configured_health_kind'):M.configured_health_argv({'Healthcheck':{'Test':['INVENTED','command']}})
 def test_wrong_added_probe_refuses_before_any_client_outage_while_configured_contract_passes(self):
  plan,rows=fixture('web');cid=plan['clients'][0]['cid'];rows[cid]['Config']['Healthcheck']={'Test':['CMD','ACTUAL_CAPTURE_HEALTH']};plan['clients'][0]['probe_argv']=['WRONG_GENERIC_PROBE'];plan['clients'][0]['require_configured_healthcheck']=True;e=Engine(rows)
  with TemporaryDirectory(dir=ROOT)as folder:
   op=M.Activation(plan,Path(folder)/'fresh',time.time()+1800,e)
   with redirect_stdout(io.StringIO()),patch.object(op,'fence'),self.assertRaisesRegex(RuntimeError,'client_baseline_probe_failed'):op.run()
  self.assertFalse(any(method=='POST'for method,_,_ in e.calls));self.assertTrue(all(r['State']['Running']for r in e.rows.values()))
 def test_actual_configured_capture_health_replaces_invented_extra_probe_and_baselines_before_outage(self):
  plan,rows=fixture('web');cid=plan['clients'][0]['cid'];rows[cid]['Config']['Healthcheck']={'Test':['CMD','ACTUAL_CAPTURE_HEALTH']};plan['clients'][0].pop('probe_argv');plan['clients'][0]['require_configured_healthcheck']=True;e=Engine(rows);original=e.execute;observed=[]
  def execute(target,argv,**kwargs):
   if argv==['ACTUAL_CAPTURE_HEALTH']:observed.append(('POST_restore'if any(M.LABEL in r['Config'].get('Labels',{})for r in e.rows.values())else'PRE_outage',argv))
   return original(target,argv,**kwargs)
  e.execute=execute
  with TemporaryDirectory(dir=ROOT)as folder:
   op=M.Activation(plan,Path(folder)/'fresh',time.time()+1800,e)
   with redirect_stdout(io.StringIO()),patch.object(op,'fence'):result=op.run()
  self.assertEqual(result['status'],'APPLIED_RUNTIME_ONLY');self.assertEqual(observed,[('PRE_outage',['ACTUAL_CAPTURE_HEALTH']),('POST_restore',['ACTUAL_CAPTURE_HEALTH'])]);self.assertEqual(e.rows[cid]['Config']['Healthcheck'],rows[cid]['Config']['Healthcheck'])
 def test_preflight_cached_unhealthy_cannot_override_fresh_configured_test_but_real_failure_blocks(self):
  for good in(True,False):
   plan,rows=fixture('web');cid=plan['clients'][0]['cid'];rows[cid]['State']['Health']['Status']='unhealthy';rows[cid]['Config']['Healthcheck']={'Test':['CMD','ACTUAL_CAPTURE_HEALTH'if good else'WRONG_GENERIC_PROBE']};plan['clients'][0].pop('probe_argv');plan['clients'][0]['require_configured_healthcheck']=True;e=Engine(rows)
   with self.subTest(actual_configured_pass=good),TemporaryDirectory(dir=ROOT)as folder:
    op=M.Activation(plan,Path(folder)/'fresh',time.time()+1800,e)
    with redirect_stdout(io.StringIO()),patch.object(op,'fence'):
     if good:op.preflight();self.assertEqual(op.clients[0]['old']['State']['Health']['Status'],'unhealthy');op.journal.close()
     else:
      with self.assertRaisesRegex(RuntimeError,'client_baseline_probe_failed'):op.run()
   self.assertFalse(any(method=='POST'for method,_,_ in e.calls));self.assertTrue(all(r['State']['Running']for r in e.rows.values()))
 def test_postrestore_declared_Docker_healthy_gate_is_not_waived_by_actual_CMD_pass(self):
  plan,rows=fixture('web');cid=plan['clients'][0]['cid'];rows[cid]['Config']['Healthcheck']={'Test':['CMD','ACTUAL_CAPTURE_HEALTH']};plan['clients'][0].pop('probe_argv');plan['clients'][0]['require_configured_healthcheck']=True;e=Engine(rows);e.failure='client_cached_unhealthy_after_start';clock=[time.time()]
  with TemporaryDirectory(dir=ROOT)as folder:
   op=M.Activation(plan,Path(folder)/'fresh',clock[0]+1800,e)
   with redirect_stdout(io.StringIO()),patch.object(op,'fence'),patch.object(M.time,'time',side_effect=lambda:clock[0]),patch.object(M.time,'sleep',side_effect=lambda sec:clock.__setitem__(0,clock[0]+sec)):
    with self.assertRaisesRegex(RuntimeError,'client_not_ready'):op.run()
   result=json.loads((Path(folder)/'fresh/receipt.json').read_text());self.assertEqual(result['status'],'NEEDS_RECONCILIATION');self.assertTrue(all(e.rows[c['cid']]['State']['Running']for c in plan['clients']))
 def test_actual_docker_image_default_label_merge_predicted_before_cut_and_strict_after_create(self):
  plan,rows,e,op,result,error=self.run_case(failure='image_default_labels');self.assertIsNone(error);self.assertEqual(result['status'],'APPLIED_RUNTIME_ONLY')
  for store in op.stores:
   actual=e.rows[store['new']]['Config'];self.assertEqual(actual['Labels']['org.opencontainers.image.revision'],'b'*40);self.assertEqual(actual['Labels']['io.gold-trade.security.recipe'],'redis7412-corrected');self.assertEqual(actual['Env'],store['old']['Config']['Env']);self.assertEqual(actual['Cmd'],store['old']['Config']['Cmd']);self.assertEqual(actual['Labels']['com.docker.compose.project'],store['old']['Config']['Labels']['com.docker.compose.project']);self.assertEqual(store['body']['Labels'],actual['Labels'])
 def test_only_authenticated_inherited_provenance_replaces_old_labels_business_and_private_exact(self):
  old={'private_business':'PRIVATE_AUTH_NEVER_PRINT','org.opencontainers.image.revision':'old','io.gold-trade.retired':'old','com.docker.compose.project':'keep','com.docker.compose.image':'old'};previous={'org.opencontainers.image.revision':'old','io.gold-trade.retired':'old'};new={'org.opencontainers.image.revision':'new','io.gold-trade.security.recipe':'qualified','private_business':'IMAGE_DEFAULT_MUST_NOT_OVERRIDE'};actual=M.effective_labels(old,previous,new,'sha256:'+'e'*64,'a'*32)
  self.assertEqual(actual['private_business'],old['private_business']);self.assertEqual(actual['com.docker.compose.project'],'keep');self.assertEqual(actual['org.opencontainers.image.revision'],'new');self.assertNotIn('io.gold-trade.retired',actual);self.assertEqual(actual['com.docker.compose.image'],'sha256:'+'e'*64);self.assertEqual(actual[M.LABEL],'a'*32)
 def test_real_docker29_created_oom_default_is_semantically_preserved(self):
  plan,rows,e,op,result,error=self.run_case(failure='materialize_oom_default');self.assertIsNone(error);self.assertEqual(result['status'],'APPLIED_RUNTIME_ONLY')
  self.assertFalse(M.host_config_equal({'OomKillDisable':True},{'OomKillDisable':None}));self.assertFalse(M.host_config_equal({'Other':1,'OomKillDisable':False},{'Other':2,'OomKillDisable':None}))
 def test_prestart_material_host_drift_rolls_back_without_disconnect_of_unattached_intent(self):
  plan,rows,e,op,result,error=self.run_case(failure='created_material_host_drift');self.assertIsNotNone(error);self.assertEqual(result['status'],'FAILED_RESTORED_OLD');candidate=next(s['new']for s in op.stores if s['new']);self.assertFalse(e.rows[candidate]['State']['Running']);self.assertTrue(e.rows[candidate]['NetworkSettings']['Networks']);self.assertFalse(any(path.startswith('/networks/')and value and value.get('Container')==candidate for _,path,value in e.calls));self.assertTrue(all(e.rows[c['cid']]['State']['Running']for c in plan['clients']))
 def test_unaccepted_source_plan_refuses_without_serving_change(self):
  plan,rows=fixture('native');plan['qualified_images_and_data_drills_accepted_by_Root']=False;e=Engine(rows)
  with TemporaryDirectory(dir=ROOT)as d:
   op=M.Activation(plan,Path(d)/'fresh',time.time()+1800,e)
   with redirect_stdout(io.StringIO()),self.assertRaisesRegex(RuntimeError,'Root_qualification_required'):op.run()
   self.assertFalse(e.calls)
if __name__=='__main__':unittest.main()
