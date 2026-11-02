"""Synthetic Docker Engine regression tests. No Docker, systemd or real data access."""
from pathlib import Path
from contextlib import redirect_stdout
from copy import deepcopy
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import urlsplit,parse_qs
import importlib.util,io,json,time,unittest
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('_service_activation',ROOT/'activate_services.py');M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)
CID=lambda n:format(n,'064x')
IID=lambda n:'sha256:'+CID(n)
REV='b'*40;OLDREV='a'*40;SECRET='PRIVATE_AUTH_NEVER_PRINT'
def fixture(site='web',kind='market'):
 service='estimator-snapshot-receiver'if kind=='market'else'app';cid=CID(1);image=IID(100);candidate=IID(500);name='current_receiver';project='current_market';net=CID(900)
 labels={'com.docker.compose.project':project,'com.docker.compose.service':service,'com.docker.compose.image':image,'com.docker.compose.project.config_files':'/PUBLIC/compose.yml','org.opencontainers.image.revision':OLDREV,'io.gold-trade.old-security':'old','business_label':'keep'}
 env=['PRIVATE='+SECRET,'MARKET_PIPELINE_RELEASE_SHA='+OLDREV,'PRIVATE_DUP=one','MARKET_PIPELINE_IMAGE_REVISION='+OLDREV,'PRIVATE_DUP=two','MARKET_PIPELINE_RELEASE_SHA='+OLDREV,'MARKET_CAPTURE_BACKFILL_NOT_BEFORE_UTC=','MARKET_CAPTURE_BACKFILL_SOURCE_CODES=']
 config={'Image':image,'Env':env,'Cmd':['python','-m',M.FOUNDATION,'run','--role',service],'Entrypoint':None,'User':'1000','WorkingDir':'/app','Healthcheck':{'Test':['CMD','PUBLIC_HEALTH']},'Labels':labels}
 mounts=[{'Type':'bind','Name':None,'Source':'/PUBLIC/market-data','Destination':'/var/lib/market-data','RW':True,'Propagation':'rprivate'}]
 host={'OomKillDisable':None,'NetworkMode':'current_default','RestartPolicy':{'Name':'unless-stopped','MaximumRetryCount':0},'Binds':['/PUBLIC/market-data:/var/lib/market-data:rw']}
 ep={'NetworkID':net,'Aliases':[name,service],'IPAMConfig':{'IPv4Address':'172.20.0.2'},'MacAddress':'02:42:ac:14:00:02','GwPriority':0}
 old={'Id':cid,'Image':image,'Name':'/'+name,'Config':config,'HostConfig':host,'Mounts':mounts,'NetworkSettings':{'Networks':{'current_default':ep}},'State':{'Running':True,'Pid':1000,'Health':{'Status':'healthy'}}}
 images={image:{'Id':image,'Config':{'Env':[],'Labels':{'org.opencontainers.image.revision':OLDREV,'io.gold-trade.old-security':'old'}}},candidate:{'Id':candidate,'Config':{'Env':[],'Labels':{'org.opencontainers.image.revision':REV,'io.gold-trade.new-security':'new'}}}}
 item={'role':site+'/'+service,'service':service,'kind':kind,'project':project,'old_cid':cid,'old_image':image,'new_image':candidate,'canonical_config':IID(501),'release':REV}
 if kind!='market':item.update(health_argv=['PUBLIC_PROBE'],business_profile={'Root_accepted':True,'site':site,'old_image':image,'new_canonical_config':IID(501),'original_business_commit':'c'*40,'retained_business_commit':'c'*40})
 plan={'schema':'normal_service_activation/1','operation_id':'f'*32,'site':site,'qualified_images_and_business_accepted_by_Root':True,'services':[item],'fence':{'path':'/PUBLIC/FENCE','sha256':'f'*64,'units':[],'dropin_name':'owned.conf','no_Host_readers':True}}
 return plan,{cid:old},images
class Engine:
 def __init__(self,rows,images):self.rows=deepcopy(rows);self.images=images;self.calls=[];self.end=time.time()+1800;self.nextid=10000;self.failure=None;self.requested={}
 def inspect(self,cid):return deepcopy(self.rows[cid])
 def image(self,image):return deepcopy(self.images[image])
 def request(self,method,path,value=None,**kw):
  self.calls.append((method,path,deepcopy(value)));u=urlsplit(path);parts=u.path.strip('/').split('/')
  if method=='GET'and u.path=='/containers/json':return[{'Id':r['Id'],'State':'running'if r['State']['Running']else'exited','Labels':r['Config']['Labels']}for r in self.rows.values()]
  if method=='POST'and parts[:2]==['containers','create']:
   if self.failure=='create_before':raise RuntimeError('normal_docker_unknown')
   cid=CID(self.nextid);self.nextid+=1;config={k:deepcopy(v)for k,v in value.items()if k not in('HostConfig','NetworkingConfig')};host=deepcopy(value['HostConfig']);mounts=deepcopy(next(iter(self.rows.values()))['Mounts']);eps={n:{**deepcopy(e),'NetworkID':'','MacAddress':''}for n,e in value['NetworkingConfig']['EndpointsConfig'].items()};self.requested[cid]=deepcopy(value['NetworkingConfig']['EndpointsConfig']);self.rows[cid]={'Id':cid,'Image':config['Image'],'Name':'/'+parse_qs(u.query)['name'][0],'Config':config,'HostConfig':host,'Mounts':mounts,'NetworkSettings':{'Networks':eps},'State':{'Running':False,'Pid':0,'Status':'created','StartedAt':'0001-01-01T00:00:00Z'}}
   if self.failure=='materialize_oom_default':self.rows[cid]['HostConfig']['OomKillDisable']=False
   if self.failure=='created_material_host_drift':self.rows[cid]['HostConfig']['OomKillDisable']=True
   if self.failure=='create_after':raise RuntimeError('normal_docker_unknown')
   return{'Id':cid}
  if parts[0]=='networks':
   cid=value['Container']
   if parts[2]=='disconnect':
    if self.failure=='disconnect':raise RuntimeError('normal_docker_unknown')
    self.rows[cid]['NetworkSettings']['Networks']={k:e for k,e in self.rows[cid]['NetworkSettings']['Networks'].items()if e.get('NetworkID')!=parts[1]and k!=parts[1]}
   elif self.failure!='connect_noop':self.rows[cid]['NetworkSettings']['Networks'][parts[1]]={**deepcopy(value['EndpointConfig']),'NetworkID':parts[1]}
   return None
  cid=parts[1];row=self.rows[cid];action=parts[2]
  if action=='update':row['HostConfig']['RestartPolicy']=deepcopy(value['RestartPolicy']);return{}
  if action=='stop':row['State'].update(Running=False,Pid=0);return None
  if action=='rename':row['Name']='/'+parse_qs(u.query)['name'][0];return None
  if action=='start':
   row['State'].update(Running=True,Pid=3000,Status='running',StartedAt='2026-11-02T00:00:00Z');row['State']['Health']={'Status':'healthy'}
   for net,ep in row['NetworkSettings']['Networks'].items():
    if not ep.get('NetworkID'):ep['NetworkID']=net;ep['MacAddress']=self.requested[cid][net].get('MacAddress','')
   return None
  raise AssertionError((method,path))
 def execute(self,cid,argv,**kw):
  assert argv in(['PUBLIC_PROBE'],['PUBLIC_HEALTH'],['ACTUAL_CAPTURE_HEALTH'])or argv[:4]==['python','-m',M.FOUNDATION,'healthcheck'];return 0,b'PUBLIC_OK'
class Tests(unittest.TestCase):
 def case(self,failure=None,bad=False,kind='market',mutate=None):
  p,rows,images=fixture(kind=kind)
  if mutate:mutate(p,rows,images)
  e=Engine(rows,images);e.failure=failure;folder=TemporaryDirectory(dir=ROOT);self.addCleanup(folder.cleanup);state=Path(folder.name)/'fresh';op=M.Services(p,state,time.time()+1800,e);out=io.StringIO();original=op.ready
  def probe(cid,kind,version=None):
   if bad and cid!=p['services'][0]['old_cid']:raise RuntimeError('normal_service_not_ready')
   return original(cid,kind,version)
  with redirect_stdout(out),patch.object(op,'fence'),patch.object(op,'prepare_capture_binding',return_value=None),patch.object(op,'ready',side_effect=probe):
   try:result=op.run();error=None
   except Exception as ex:error=ex;result=json.loads((state/'receipt.json').read_bytes())if(state/'receipt.json').exists()else None
  for text in(out.getvalue(),*(f.read_text()for f in state.glob('*.json'))):self.assertNotIn(SECRET,text)
  return p,rows,e,op,result,error
 def test_receiver_full_path_preserves_private_order_duplicates_replay_data_and_network(self):
  p,rows,e,op,result,error=self.case();self.assertIsNone(error);self.assertEqual(result['status'],'APPLIED_RUNTIME_ONLY');s=op.stores[0];old=e.rows[s['old']['Id']];new=e.rows[s['new']];self.assertFalse(old['State']['Running']);self.assertFalse(old['NetworkSettings']['Networks']);self.assertEqual(old['HostConfig']['RestartPolicy'],M.B.NO_RESTART);self.assertTrue(new['State']['Running']);self.assertEqual(M.B.mounts(new),M.B.mounts(old));M.B.network_parity(new,s['old']);self.assertEqual(new['Config']['Env'],[v.split('=',1)[0]+'='+REV if v.split('=',1)[0]in M.STAMPS else v for v in s['old']['Config']['Env']]);self.assertEqual(new['Config']['Cmd'],old['Config']['Cmd']);self.assertEqual(new['Config']['Healthcheck'],old['Config']['Healthcheck']);self.assertEqual(new['Config']['Labels']['business_label'],'keep');self.assertNotIn('io.gold-trade.old-security',new['Config']['Labels']);self.assertEqual(new['Config']['Labels']['org.opencontainers.image.revision'],REV);self.assertEqual(new['Config']['Labels']['io.gold-trade.new-security'],'new');self.assertTrue(result['public_image_reference_consistency_still_requires_Root']);self.assertFalse(result['postactivation_publication_freshness_assessed'])
 def test_unknown_detach_and_create_preserve_pending_without_retry_or_old_restart(self):
  for failure in('disconnect','create_before','create_after'):
   with self.subTest(failure=failure):
    p,rows,e,op,result,error=self.case(failure=failure);self.assertIsNotNone(error);self.assertEqual(result['status'],'NEEDS_RECONCILIATION');self.assertIsNotNone(result['pending_requested_action']);self.assertLessEqual(sum('/containers/create'in path for _,path,_ in e.calls),1);self.assertFalse(any(path.endswith('/start')for _,path,_ in e.calls))
 def test_known_failed_candidate_restores_exact_original_container_private_config_and_network(self):
  p,rows,e,op,result,error=self.case(bad=True);self.assertIsNotNone(error);self.assertEqual(result['status'],'FAILED_RESTORED_OLD');self.assertEqual(result['primary_error'],'normal_service_not_ready');old=rows[p['services'][0]['old_cid']];actual=e.rows[old['Id']];self.assertEqual(actual['Config'],old['Config']);self.assertEqual(actual['HostConfig'],old['HostConfig']);self.assertEqual(actual['Name'],old['Name']);self.assertTrue(actual['State']['Running']);M.B.network_parity(actual,old);self.assertFalse(e.rows[op.stores[0]['new']]['State']['Running'])
 def test_noop_network_connect_never_claims_old_restored(self):
  p,rows,e,op,result,error=self.case(failure='connect_noop',bad=True);self.assertEqual(result['status'],'NEEDS_RECONCILIATION');self.assertFalse(e.rows[p['services'][0]['old_cid']]['State']['Running'])
 def test_nonempty_pause_or_missing_pair_refuses_before_mutation(self):
  for replacement in(['MARKET_CAPTURE_BACKFILL_NOT_BEFORE_UTC=2020','MARKET_CAPTURE_BACKFILL_SOURCE_CODES='],['MARKET_CAPTURE_BACKFILL_NOT_BEFORE_UTC=']):
   def mutate(p,rows,images):row=next(iter(rows.values()));row['Config']['Env']=[v for v in row['Config']['Env']if v.split('=',1)[0]not in M.PAUSE]+replacement
   with self.subTest(replacement=replacement):p,rows,e,op,result,error=self.case(mutate=mutate);self.assertIsNotNone(error);self.assertFalse(any(method=='POST'for method,_,_ in e.calls))
 def test_image_default_environment_requires_explicit_review_and_never_replaces_old_private_value(self):
  old=['PRIVATE=x','DUP=a','DUP=b'];self.assertEqual(M.env_list(old,['PRIVATE=new','PUBLIC_NEW=1'],REV,'app',['PUBLIC_NEW']),old+['PUBLIC_NEW=1'])
  with self.assertRaisesRegex(RuntimeError,'new_image_environment_keys_not_reviewed'):M.env_list(old,['PUBLIC_NEW=1'],REV,'app')
 def test_other_running_same_compose_role_refuses_before_mutation(self):
  def mutate(p,rows,images):row=deepcopy(next(iter(rows.values())));row['Id']=CID(777);rows[row['Id']]=row
  p,rows,e,op,result,error=self.case(mutate=mutate);self.assertIsNotNone(error);self.assertEqual(str(error),'another_service_owner_running');self.assertFalse(any(method=='POST'for method,_,_ in e.calls))
 def test_actual_four_native_and_six_web_market_roles_admit_only_their_site(self):
  actual={'native':{'coin-estimator','estimator-snapshot-sender','market-fact-receiver','market-store-adapter'},'web':{'market-capture-account2','estimator-snapshot-receiver','market-capture-account1','market-capture-external','market-fact-sync-worker','market-processor'}}
  self.assertEqual(M.MARKET,actual)
  for correct_site,services in actual.items():
   for service in services:
    for chosen_site in ('native','web'):
     def mutate(p,rows,images):
      p['site']=chosen_site;item=p['services'][0];item['service']=service;item['role']=chosen_site+'/'+service;row=next(iter(rows.values()));row['Config']['Labels']['com.docker.compose.service']=service
     with self.subTest(service=service,site=chosen_site):
      p,rows,e,op,result,error=self.case(mutate=mutate)
      if chosen_site==correct_site:self.assertIsNone(error);self.assertEqual(result['status'],'APPLIED_RUNTIME_ONLY')
      else:self.assertEqual(str(error),'market_role_site');self.assertFalse(any(method=='POST'for method,_,_ in e.calls))
 def test_no_replay_fields_in_old_config_cannot_be_added_nonempty_by_image_defaults(self):
  old=['MARKET_PIPELINE_RELEASE_SHA='+OLDREV,'MARKET_PIPELINE_IMAGE_REVISION='+OLDREV]
  # An admitted image default must not introduce replay authority that was absent.
  for value in ('','2020-01-01T00:00:00Z'):
   with self.subTest(value=value),self.assertRaisesRegex(RuntimeError,'new_replay_environment_not_allowed'):
    M.env_list(old,['MARKET_CAPTURE_BACKFILL_NOT_BEFORE_UTC='+value],REV,'market',['MARKET_CAPTURE_BACKFILL_NOT_BEFORE_UTC'])
 def test_real_created_oom_default_only_is_allowed(self):
  p,rows,e,op,result,error=self.case(failure='materialize_oom_default');self.assertIsNone(error);self.assertEqual(result['status'],'APPLIED_RUNTIME_ONLY')
 def test_prestart_host_drift_restores_original_without_requesting_disconnect_of_pending_intent(self):
  p,rows,e,op,result,error=self.case(failure='created_material_host_drift');self.assertIsNotNone(error);self.assertEqual(result['status'],'FAILED_RESTORED_OLD');candidate=op.stores[0]['new'];self.assertFalse(e.rows[candidate]['State']['Running']);self.assertTrue(e.rows[candidate]['NetworkSettings']['Networks']);self.assertFalse(any(path.startswith('/networks/')and value and value.get('Container')==candidate for _,path,value in e.calls));self.assertTrue(e.rows[p['services'][0]['old_cid']]['State']['Running'])
 def test_actual_custom_capture_health_not_invented_generic_is_baselined_and_repeated(self):
  p,rows,images=fixture();old=next(iter(rows.values()));old['Config']['Healthcheck']={'Test':['CMD','ACTUAL_CAPTURE_HEALTH']};e=Engine(rows,images);original=e.execute;called=[]
  def execute(cid,argv,**kwargs):
   called.append((cid,argv))
   if argv[:4]==['python','-m',M.FOUNDATION,'healthcheck']:return 1,b''
   return original(cid,argv,**kwargs)
  e.execute=execute
  with TemporaryDirectory(dir=ROOT)as folder:
   op=M.Services(p,Path(folder)/'fresh',time.time()+1800,e)
   with redirect_stdout(io.StringIO()),patch.object(op,'fence'):result=op.run()
  self.assertEqual(result['status'],'APPLIED_RUNTIME_ONLY');self.assertTrue(called);self.assertTrue(all(argv==['ACTUAL_CAPTURE_HEALTH']for _,argv in called));self.assertEqual(called[0][0],old['Id'])
 def test_missing_declared_health_requires_explicit_Root_fallback_and_actual_old_baseline(self):
  def missing(p,rows,images):next(iter(rows.values()))['Config'].pop('Healthcheck')
  p,rows,e,op,result,error=self.case(mutate=missing);self.assertEqual(str(error),'configured_health_or_explicit_baselined_fallback_required');self.assertFalse(any(method=='POST'for method,_,_ in e.calls))
  def approved(p,rows,images):missing(p,rows,images);p['services'][0]['foundation_fallback_baseline_accepted_by_Root']=True
  p,rows,e,op,result,error=self.case(mutate=approved);self.assertIsNone(error);self.assertEqual(result['status'],'APPLIED_RUNTIME_ONLY')
 def test_preflight_cached_unhealthy_accepts_only_actual_declared_exit_zero(self):
  for good in(True,False):
   p,rows,images=fixture();old=next(iter(rows.values()));old['State']['Health']['Status']='unhealthy';e=Engine(rows,images);original=e.execute
   def execute(cid,argv,**kwargs):return original(cid,argv,**kwargs)if good else(1,b'')
   e.execute=execute
   with self.subTest(actual_configured_pass=good),TemporaryDirectory(dir=ROOT)as folder:
    op=M.Services(p,Path(folder)/'fresh',time.time()+1800,e)
    with redirect_stdout(io.StringIO()),patch.object(op,'fence'):
     if good:op.preflight();self.assertTrue(op.stores[0]['declared_health']);op.journal.close()
     else:
      with self.assertRaisesRegex(RuntimeError,'service_baseline_probe_failed'):op.run()
   self.assertFalse(any(method=='POST'for method,_,_ in e.calls));self.assertTrue(e.rows[old['Id']]['State']['Running'])
 def test_postactivation_declared_Docker_healthy_gate_remains_even_when_current_CMD_passes(self):
  p,rows,images=fixture();e=Engine(rows,images);clock=[time.time()]
  with TemporaryDirectory(dir=ROOT)as folder:
   op=M.Services(p,Path(folder)/'fresh',clock[0]+1800,e)
   with redirect_stdout(io.StringIO()),patch.object(op,'fence'),patch.object(M.time,'time',side_effect=lambda:clock[0]),patch.object(M.time,'sleep',side_effect=lambda sec:clock.__setitem__(0,clock[0]+sec)):
    op.preflight();old=p['services'][0]['old_cid'];e.rows[old]['State']['Health']['Status']='unhealthy'
    with self.assertRaisesRegex(RuntimeError,'normal_service_not_ready'):op.ready(old,'market',OLDREV)
    op.journal.close()
 def test_site_bound_app_profile_prevents_web_profile_on_native_and_valid_profile_works(self):
  p,rows,e,op,result,error=self.case(kind='app');self.assertIsNone(error);self.assertEqual(result['status'],'APPLIED_RUNTIME_ONLY');self.assertEqual(e.rows[op.stores[0]['new']]['Config']['Env'],next(iter(rows.values()))['Config']['Env'])
  def mutate(p,rows,images):p['site']='native'
  p,rows,e,op,result,error=self.case(kind='app',mutate=mutate);self.assertEqual(str(error),'closed_site_business_profile_required');self.assertFalse(any(method=='POST'for method,_,_ in e.calls))
if __name__=='__main__':unittest.main()
