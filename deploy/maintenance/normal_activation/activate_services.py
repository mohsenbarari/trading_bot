"""Finite normal service replacement using the reviewed datastore primitives.
No deployment, migration, seeding, old controller or private config persistence.
"""
from pathlib import Path
from types import ModuleType
from copy import deepcopy
from hashlib import sha256
import argparse,json,os,re,time

BASE_PIN=(29611, 'c64baef99ca5b59a15f368090daedf5bc659d1197e9904d7084e292a5e04098e')
def base():
 path=Path(__file__).resolve().with_name('datastore_primitives.py')
 with path.open('rb')as source:raw=source.read(65537)
 if(len(raw),sha256(raw).hexdigest())!=BASE_PIN:raise RuntimeError('service_primitives_pin')
 module=ModuleType('_reviewed_normal_datastore_primitives');module.__file__=str(path);exec(compile(raw,str(path),'exec'),module.__dict__);return module
B=base();need=B.need
AUTHORITY_PIN=(9945, '6863586a18ef82b508b0aa0d5655424a7f431b2695defef07bc06854a7a8f7d0')

def authority_source():
 path=Path(__file__).resolve().with_name('capture_authority.py')
 with path.open('rb')as source:raw=source.read(65537)
 need((len(raw),sha256(raw).hexdigest())==AUTHORITY_PIN,'capture_authority_source_pin')
 module=ModuleType('_reviewed_capture_authority_handover');module.__file__=str(path);exec(compile(raw,str(path),'exec'),module.__dict__);return module

MARKET={
 'native':{'coin-estimator','estimator-snapshot-sender','market-fact-receiver','market-store-adapter'},
 'web':{'market-capture-account2','estimator-snapshot-receiver','market-capture-account1','market-capture-external','market-fact-sync-worker','market-processor'}}
STAMPS={'MARKET_PIPELINE_RELEASE_SHA','MARKET_PIPELINE_IMAGE_REVISION'}
PAUSE={'MARKET_CAPTURE_BACKFILL_NOT_BEFORE_UTC','MARKET_CAPTURE_BACKFILL_SOURCE_CODES'}
FOUNDATION='core.market_intelligence.private_pipeline_foundation'
PUBLIC_COMPOSE={'com.docker.compose.project','com.docker.compose.service','com.docker.compose.project.config_files','com.docker.compose.project.working_dir','com.docker.compose.project.environment_file'}

def env_list(old,defaults,revision,kind,added_keys=()):
 need(type(old)is list and all(type(v)is str and'='in v for v in old),'normal_service_environment')
 result=[];seen=set();pause={}
 for entry in old:
  key,value=entry.split('=',1);seen.add(key)
  if kind=='market'and key in STAMPS:result.append(key+'='+revision)
  else:result.append(entry)
  if kind=='market'and key in PAUSE:need(value=='','replay_pause_must_remain_empty');pause[key]=value
 if kind=='market':need(STAMPS<=seen and(not pause or set(pause)==PAUSE),'market_public_stamp_or_pause_pair')
 extra=[]
 for entry in defaults or[]:
  need(type(entry)is str and'='in entry,'image_environment');key=entry.split('=',1)[0]
  if key not in seen:
   need(not(kind=='market'and key in PAUSE),'new_replay_environment_not_allowed');extra.append(entry);seen.add(key)
 need({v.split('=',1)[0]for v in extra}==set(added_keys),'new_image_environment_keys_not_reviewed')
 return result+extra

def labels(old,old_defaults,new_defaults,canonical,nonce):
 def provenance(key):return key.startswith(('org.opencontainers.image.','io.gold-trade.'))
 inherited={key for key in set(old_defaults)|set(new_defaults)if provenance(key)}
 result={**new_defaults,**old}
 for key in inherited:
  if key in new_defaults:result[key]=new_defaults[key]
  else:result.pop(key,None)
 if'com.docker.compose.image'in old:result['com.docker.compose.image']=canonical
 result[B.LABEL]=nonce
 return result

def service_body(old,old_image,new_image,item,nonce):
 body=B.create_body(old,item['new_image'],nonce,item['canonical_config']);body['Env']=env_list(old['Config'].get('Env')or[],new_image['Config'].get('Env')or[],item['release'],item['kind'],item.get('image_added_env_keys',[]))
 body['Labels']=labels(old['Config'].get('Labels')or{},old_image['Config'].get('Labels')or{},new_image['Config'].get('Labels')or{},item['canonical_config'],nonce)
 return body

def parity(row,old,body,*,created=False,final=False):
 expected={k:v for k,v in body.items()if k not in('HostConfig','NetworkingConfig')}
 need(row['Config']==expected and row['Image']==body['Image'],'service_config_changed')
 host=deepcopy(body['HostConfig'])
 if final:host['RestartPolicy']=old['HostConfig']['RestartPolicy']
 need(B.host_config_equal(row['HostConfig'],host)and B.mounts(row)==B.mounts(old),'service_host_or_mount_changed');B.network_parity(row,old,created=created)

class Services(B.Activation):
 def prepare_capture_binding(self,store):
  if self.plan['site']=='web'and store['plan']['service']in('market-capture-account1','market-capture-account2'):return authority_source().Binding(self,store)
  return None
 def change(self,action,path,body=None,*,seconds=30):
  if action.startswith('restart_old:'):
   cid=action.split(':',1)[1];store=next(s for s in self.stores if s['old']['Id']==cid);binding=store.get('capture_authority')
   if binding is not None:binding.restore()
  return super().change(action,path,body,seconds=seconds)
 def preflight(self):
  need(os.geteuid()==0 and self.plan['schema']=='normal_service_activation/1','Root_service_plan')
  need(self.plan.get('qualified_images_and_business_accepted_by_Root')is True,'Root_service_qualification_required')
  site=self.plan['site'];need(site in MARKET and re.fullmatch('[a-f0-9]{32}',self.nonce)is not None and self.plan.get('operation_id')==self.nonce,'service_scope')
  need(180<=self.reserve<=1200 and self.reserve+600<self.end-time.time()<=5400,'finite_deadline');self.fence()
  parent=self.state.parent;need(parent.is_absolute()and parent.resolve(strict=True)==parent and parent.stat().st_uid==0 and not parent.stat().st_mode&0o022,'state_parent');need(not os.path.lexists(self.state),'fresh_state_directory');self.state.mkdir(mode=0o700);self.state_created=True;self.journal=(self.state/'events.jsonl').open('xb');os.chmod(self.state/'events.jsonl',0o600)
  items=self.plan['services'];need(type(items)is list and 1<=len(items)<=5,'finite_service_scope');seen=set()
  for item in items:
   need(item['old_cid']not in seen and re.fullmatch('[a-f0-9]{64}',item['old_cid'])is not None,'service_CID');seen.add(item['old_cid'])
   need(all(re.fullmatch('sha256:[a-f0-9]{64}',item[k])is not None for k in('old_image','new_image','canonical_config'))and re.fullmatch('[a-f0-9]{40}',item['release'])is not None,'service_image_identity')
   old=self.inspect(item['old_cid']);need(old['Image']==item['old_image']and old['State']['Running']is True,'old_service_identity');meta=old['Config'].get('Labels')or{}
   need(meta.get('com.docker.compose.project')==item['project']and meta.get('com.docker.compose.service')==item['service'],'normal_compose_service')
   before_image=self.engine.image(item['old_image']);new_image=self.engine.image(item['new_image']);need(new_image['Id']==item['new_image']and new_image['Config'].get('Labels',{}).get('org.opencontainers.image.revision')==item['release'],'qualified_image_provenance')
   need(item['kind']in('market','app','bot','sync'),'service_kind')
   if item['kind']=='market':need(item['role']==site+'/'+item['service']and item['service']in MARKET[site],'market_role_site')
   else:
    profile=item['business_profile'];need(profile.get('Root_accepted')is True and profile.get('site')==site and profile.get('old_image')==old['Image']and profile.get('new_canonical_config')==item['canonical_config']and profile.get('original_business_commit')==profile.get('retained_business_commit')and re.fullmatch('[a-f0-9]{40}',profile.get('retained_business_commit',''))is not None,'closed_site_business_profile_required')
    need(type(item.get('health_argv'))is list and item['health_argv'],'real_App_Bot_Sync_probe_required')
   body=service_body(old,before_image,new_image,item,self.nonce);old_env=old['Config'].get('Env')or[];previous=next((v.split('=',1)[1]for v in old_env if v.split('=',1)[0]=='MARKET_PIPELINE_RELEASE_SHA'),None)
   store={'plan':item,'old':old,'kind':item['kind'],'body':body,'new':None,'changed':False,'renamed':False,'detached':False,'saved_endpoints':B.endpoints(old),'old_version':previous}
   if item['kind']=='market':
    declared=B.configured_health_argv(old['Config']);store['declared_health']=declared is not None
    if declared is None:
     need(item.get('foundation_fallback_baseline_accepted_by_Root')is True,'configured_health_or_explicit_baselined_fallback_required');declared=['python','-m',FOUNDATION,'healthcheck','--role',item['service']]
    store['health_argv']=declared
   else:store['health_argv']=item['health_argv'];store['declared_health']=False
   store['capture_authority']=self.prepare_capture_binding(store)
   rc,_=self.engine.execute(old['Id'],store['health_argv'])
   self.event('service_baseline_probe_observed',role=item['role'],cid=old['Id'],cached_Docker_health=old['State'].get('Health',{}).get('Status'),actual_probe_exit=rc,configured_health=store['declared_health'])
   need(rc==0,'service_baseline_probe_failed')
   self.one_writer(store,old['Id']);self.stores.append(store)
  need(len({(s['plan']['project'],s['plan']['service'])for s in self.stores})==len(self.stores),'duplicate_service_owner')
  metadata={'schema':'normal_service_public_runtime_metadata/1','site':site,'services':[{'role':s['plan']['role'],'cid':s['old']['Id'],'image':s['old']['Image'],'name':s['old']['Name'],'image_reference':s['old']['Config'].get('Image'),'compose_metadata':{k:v for k,v in(s['old']['Config'].get('Labels')or{}).items()if k in PUBLIC_COMPOSE},'mounts':B.mounts(s['old']),'endpoints':s['saved_endpoints'],'restart_policy':s['old']['HostConfig']['RestartPolicy']}for s in self.stores]}
  raw=B.encoded(metadata);need(len(raw)<=160*1024,'service_public_metadata_bound')
  with(self.state/'runtime-metadata.json').open('xb')as out:os.chmod(out.name,0o600);out.write(raw);out.flush();os.fsync(out.fileno())
  self.event('service_preflight_passed',site=site,services=len(self.stores))
 def one_writer(self,store,allowed=None):
  rows=self.engine.request('GET','/containers/json?all=1');need(type(rows)is list,'service_owner_inventory');item=store['plan']
  for row in rows:
   meta=row.get('Labels')or{}
   if row.get('State')=='running'and meta.get('com.docker.compose.project')==item['project']and meta.get('com.docker.compose.service')==item['service']:need(row['Id']==allowed,'another_service_owner_running')
 def ready(self,cid,kind,version=None):
  store=next(s for s in self.stores if cid in(s['old']['Id'],s['new']));item=store['plan'];end=min(self.engine.end,time.time()+90)
  while time.time()<end:
   self.guard();row=self.inspect(cid);need(row['State']['Running']is True,'service_exited')
   okay=row['State'].get('Health',{}).get('Status')=='healthy'if store['declared_health']else row['State'].get('Health',{}).get('Status')in(None,'healthy')
   if kind=='market':
    stamps=[v.split('=',1)[1]for v in row['Config'].get('Env',[])if v.split('=',1)[0]in STAMPS];need(stamps and all(v==version for v in stamps),'runtime_public_release_mismatch')
    if store['declared_health']:need(B.configured_health_argv(row['Config'])==store['health_argv'],'copied_health_contract_changed')
   argv=store['health_argv']
   if okay:
    rc,_=self.engine.execute(cid,argv)
    if rc==0:return True
   time.sleep(.25)
  raise RuntimeError('normal_service_not_ready')
 def switch(self,store):
  item=store['plan'];old=store['old'];cid=old['Id'];self.phase=item['role'];store['changed']=True;self.restart(cid,B.NO_RESTART);self.stop(cid);self.one_writer(store);name=old['Name'].lstrip('/')
  self.rename(cid,name+'.rollback-'+self.nonce);store['renamed']=True;self.disconnect(cid);store['detached']=True;self.pending='create:'+item['role'];self.event('requested',action=self.pending)
  result=self.engine.request('POST','/containers/create?name='+B.quote(name,safe=''),store['body']);need(type(result)is dict and re.fullmatch('[a-f0-9]{64}',result.get('Id',''))is not None,'create_outcome_unknown');store['new']=result['Id'];self.pending=None;self.event('returned',action='create:'+item['role'],candidate=store['new'])
  row=self.inspect(store['new']);need(row['State']['Running']is False and row['State']['Pid']==0,'created_not_stopped');parity(row,old,store['body'],created=True)
  self.one_writer(store)
  if store['capture_authority']is not None:store['capture_authority'].apply()
  self.change('start:'+store['new'],'/containers/'+store['new']+'/start');self.ready(store['new'],store['kind'],item['release']);parity(self.inspect(store['new']),old,store['body']);self.one_writer(store,store['new']);self.event('normal_service_ready',role=item['role'],candidate=store['new'],release=item['release'])
 def receipt(self,status,primary=None):
  value={'schema':'normal_service_activation_receipt/1','status':status,'site':self.plan['site'],'operation_id':self.nonce,'phase':self.phase,'primary_error':primary,'primary_failure_phase':self.failure_phase,'pending_requested_action':self.pending,'services':[{'role':s['plan']['role'],'old_container':s['old']['Id'],'candidate_container':s['new'],'candidate_daemon_image':s['plan']['new_image'],'canonical_config':s['plan']['canonical_config'],'release':s['plan']['release']}for s in self.stores],'old_containers_and_data_retained':True,'Host_watchdog_fence_released':False,'public_image_reference_consistency_still_requires_Root':True,'postactivation_publication_freshness_assessed':False,'private_runtime_configuration_persisted':False,'security_accepted':False}
  with(self.state/'receipt.json.partial').open('xb')as out:os.chmod(out.name,0o600);out.write(B.encoded(value));out.flush();os.fsync(out.fileno())
  need(not(self.state/'receipt.json').exists(),'receipt_exists');os.rename(self.state/'receipt.json.partial',self.state/'receipt.json');return value
 def run(self):
  try:
   self.preflight()
   for store in self.stores:self.switch(store)
   for store in self.stores:self.restart(store['new'],store['old']['HostConfig']['RestartPolicy']);parity(self.inspect(store['new']),store['old'],store['body'],final=True);self.one_writer(store,store['new'])
   self.phase='completed';self.event('normal_services_active',services=len(self.stores));return self.receipt('APPLIED_RUNTIME_ONLY')
  except Exception as error:
   self.failure_phase=self.phase;code=str(error)if type(error)is RuntimeError and re.fullmatch('[A-Za-z0-9_]{1,80}',str(error))else'activation_unknown';unknown=self.pending is not None or code.startswith('normal_docker_')or code in{'activation_unknown','stop_outcome_unknown','network_detach_unknown','create_outcome_unknown','restart_policy_unknown'}
   self.event('incomplete',code=code,requested_action=self.pending,automatic_retry=False,unknown_outcome=unknown);status='NEEDS_RECONCILIATION'if unknown else'PREFLIGHT_REFUSED'
   if any(s['changed']for s in self.stores)and not unknown:
    try:self.rollback();status='FAILED_RESTORED_OLD'
    except Exception:self.event('rollback_incomplete',requested_action=self.pending,automatic_retry=False);status='NEEDS_RECONCILIATION'
   if self.state_created:self.receipt(status,code)
   raise
  finally:
   if self.journal is not None:self.journal.close()

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--plan',type=Path,required=True);p.add_argument('--state-dir',type=Path,required=True);p.add_argument('--deadline-epoch',type=int,required=True);a=p.parse_args();os.umask(0o077)
 try:Services(B.read_json(a.plan),a.state_dir,a.deadline_epoch).run();return 0
 except Exception:return 1
if __name__=='__main__':raise SystemExit(main())
