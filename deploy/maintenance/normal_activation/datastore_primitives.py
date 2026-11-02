"""Finite exact-store activation; private runtime config stays only in RAM.
Root executes only after genuine image/data qualification and Host maintenance.
"""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from urllib.parse import quote
import argparse,http.client,json,os,re,resource,socket,stat,subprocess,time,uuid

class UnixHTTP(http.client.HTTPConnection):
 def connect(self):
  self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.sock.settimeout(self.timeout)
  self.sock.connect('/var/run/docker.sock')

class Docker:
 """Bound request/response bodies before accumulation; no daemon text escapes."""
 def __init__(self,end):self.end=end
 def request(self,method,path,value=None,*,maximum=4*1024**2,allowed=(200,201,204),binary=False,timeout_seconds=30):
  if method not in ('GET','POST','DELETE') or not path.startswith('/') or any(c in path for c in '\r\n'):
   raise RuntimeError('normal_docker_request')
  body=None if value is None else json.dumps(value,separators=(',',':'),allow_nan=False).encode()
  if body is not None and len(body)>2*1024**2:raise RuntimeError('normal_docker_request')
  remaining=self.end-time.time()
  if remaining<=0:raise RuntimeError('deadline')
  if not 1<=timeout_seconds<=90:raise RuntimeError('normal_docker_request')
  client=UnixHTTP('localhost',timeout=min(timeout_seconds,remaining))
  try:
   client.request(method,path,body=body,headers={'Content-Type':'application/json','Connection':'close'})
   response=client.getresponse();length=response.getheader('Content-Length')
   if length is not None and (not length.isdigit() or int(length)>maximum):raise RuntimeError('normal_docker_body')
   chunks=[];count=0
   while True:
    if time.time()>=self.end:raise RuntimeError('deadline')
    part=response.read1(min(65536,maximum-count+1))
    if not part:break
    count+=len(part)
    if count>maximum:raise RuntimeError('normal_docker_body')
    chunks.append(part)
   raw=b''.join(chunks)
   if response.status not in allowed:raise RuntimeError('normal_docker_refused')
   if not raw:return None
   return raw if binary else json.loads(raw)
  except (OSError,http.client.HTTPException,ValueError,TypeError):raise RuntimeError('normal_docker_unknown') from None
  finally:client.close()
 def inspect(self,identifier):
  if not re.fullmatch('[a-f0-9]{64}',identifier):raise RuntimeError('normal_docker_request')
  return self.request('GET','/containers/'+identifier+'/json')
 def image(self,identifier):
  if not re.fullmatch('sha256:[a-f0-9]{64}',identifier):raise RuntimeError('normal_docker_request')
  return self.request('GET','/images/'+quote(identifier,safe='')+'/json')
 def execute(self,identifier,argv,*,environment=()):
  if not re.fullmatch('[a-f0-9]{64}',identifier) or type(argv) is not list or not argv or not all(type(v) is str for v in argv):
   raise RuntimeError('normal_docker_request')
  result=self.request('POST','/containers/'+identifier+'/exec',{'AttachStdout':True,'AttachStderr':True,
   'Tty':False,'Cmd':argv,'Env':list(environment)})
  if type(result) is not dict or not re.fullmatch('[a-f0-9]{64}',result.get('Id','')):raise RuntimeError('normal_docker_unknown')
  raw=self.request('POST','/exec/'+result['Id']+'/start',{'Detach':False,'Tty':False},binary=True)
  out=bytearray();offset=0
  while offset<len(raw or b''):
   if len(raw)-offset<8 or raw[offset] not in (1,2) or raw[offset+1:offset+4]!=b'\0'*3:raise RuntimeError('normal_docker_body')
   number=int.from_bytes(raw[offset+4:offset+8],'big');offset+=8
   if number>len(raw)-offset:raise RuntimeError('normal_docker_body')
   if raw[offset-8]==1:out.extend(raw[offset:offset+number])
   offset+=number
  status=self.request('GET','/exec/'+result['Id']+'/json')
  if status.get('Running') is not False or type(status.get('ExitCode')) is not int:raise RuntimeError('normal_docker_unknown')
  return status['ExitCode'],bytes(out)

LABEL='security.normal-datastore.activation'
SCOPE={'native':{'native-postgres','native-redis'},'web':{'web-postgres','web-market-postgres','web-redis'}}
NO_RESTART={'Name':'no','MaximumRetryCount':0}

def need(value,code):
 if not value:raise RuntimeError(code)
def encoded(value):return(json.dumps(value,sort_keys=True,allow_nan=False)+'\n').encode()
def read_json(path,maximum=160*1024):
 with path.open('rb')as f:raw=f.read(maximum+1)
 need(len(raw)<=maximum,'input_bound');return json.loads(raw)
def mounts(row):
 return sorted([{k:m.get(k)for k in('Type','Name','Source','Destination','RW','Propagation')}for m in row['Mounts']],key=lambda m:m['Destination'])
def endpoints(row):
 result={}
 for name,ep in row['NetworkSettings']['Networks'].items():
  need(re.fullmatch('[a-f0-9]{64}',ep.get('NetworkID',''))is not None,'network_identity')
  result[ep['NetworkID']]={k:deepcopy(ep[k])for k in('Aliases','IPAMConfig','Links','DriverOpts','MacAddress','GwPriority')if ep.get(k)is not None}
 return result
def effective_labels(old,previous_defaults,new_defaults,canonical,nonce):
 def provenance(key):return key.startswith(('org.opencontainers.image.','io.gold-trade.'))
 result={**new_defaults,**old}
 for key in set(previous_defaults)|set(new_defaults):
  if provenance(key):
   if key in new_defaults:result[key]=new_defaults[key]
   else:result.pop(key,None)
 result[LABEL]=nonce
 if 'com.docker.compose.image'in old:result['com.docker.compose.image']=canonical
 return result

def create_body(old,image,nonce,canonical,*,default_labels=None,previous_labels=None):
 body=deepcopy(old['Config']);body['Image']=image;body['Labels']=deepcopy(body.get('Labels')or{})
 body['Labels'][LABEL]=nonce
 if'com.docker.compose.image'in body['Labels']:body['Labels']['com.docker.compose.image']=canonical
 if default_labels is not None:body['Labels']=effective_labels(old['Config'].get('Labels')or{},previous_labels or{},default_labels,canonical,nonce)
 body['HostConfig']=deepcopy(old['HostConfig']);body['HostConfig']['RestartPolicy']=deepcopy(NO_RESTART)
 body['NetworkingConfig']={'EndpointsConfig':endpoints(old)}
 return body
def host_config_equal(actual,expected):
 # Docker29 materializes its unset OOM-killer-disable default as false.
 # Only this observed None->False representation is equivalent; true is not.
 normal=deepcopy(actual)
 if expected.get('OomKillDisable','ABSENT')is None and normal.get('OomKillDisable','ABSENT')is False:normal['OomKillDisable']=None
 return normal==expected

def never_started_unattached(row):
 state=row['State'];started=state.get('StartedAt','')
 return(state.get('Status')=='created'and state.get('Running')is False and state.get('Pid')==0 and type(started)is str and re.fullmatch(r'0001-01-01T00:00:00(?:[.]0+)?Z',started)is not None and all(all(ep.get(k)in('',None)for k in('NetworkID','EndpointID','IPAddress','GlobalIPv6Address'))for ep in row['NetworkSettings']['Networks'].values()))

def parity(row,old,image,nonce,canonical,*,final=False,created=False,body=None):
 expected=deepcopy(body)if body is not None else create_body(old,image,nonce,canonical)
 need(row['Config']=={k:v for k,v in expected.items()if k not in('HostConfig','NetworkingConfig')},'candidate_config_changed')
 host=deepcopy(old['HostConfig'])
 if not final:host['RestartPolicy']=deepcopy(NO_RESTART)
 need(row['Image']==image and host_config_equal(row['HostConfig'],host)and mounts(row)==mounts(old),'candidate_runtime_changed')
 network_parity(row,old,created=created)

def network_parity(row,old,*,created=False):
 wanted=endpoints(old);by_name={name:ep['NetworkID']for name,ep in old['NetworkSettings']['Networks'].items()};actual={}
 for name,ep in row['NetworkSettings']['Networks'].items():
  network=ep.get('NetworkID')or(name if name in wanted else by_name.get(name))
  need(network in wanted and network not in actual,'candidate_networks_changed');actual[network]=ep
 need(set(actual)==set(wanted),'candidate_networks_changed')
 for network,spec in wanted.items():
  ep=actual[network]
  for key in('IPAMConfig','Links','DriverOpts','GwPriority'):
   if key in spec:need(ep.get(key)==spec[key],'candidate_endpoint_changed')
  if 'Aliases'in spec:
   need(set(ep.get('Aliases')or[])>=set(spec['Aliases']),'candidate_alias_changed')
   need(set(ep.get('Aliases')or[])-set(spec['Aliases'])<={row['Id'],row['Id'][:12]},'candidate_alias_changed')
  if spec.get('MacAddress')and(not created or ep.get('MacAddress')):need(ep.get('MacAddress')==spec['MacAddress'],'candidate_MAC_changed')
  if not created:need(ep.get('NetworkID')==network,'candidate_network_unattached')

def configured_health_argv(config):
 health=config.get('Healthcheck')
 need(health is None or type(health)is dict,'configured_health_shape')
 test=(health or{}).get('Test')
 if test is None or test==[]or test==['NONE']:return None
 need(type(test)is list and 2<=len(test)<=64 and all(type(v)is str for v in test),'configured_health_shape')
 if test[0]=='CMD':return deepcopy(test[1:])
 need(test[0]=='CMD-SHELL'and len(test)==2,'configured_health_kind')
 shell=config.get('Shell')or['/bin/sh','-c'];need(type(shell)is list and 1<=len(shell)<=16 and all(type(v)is str for v in shell),'configured_health_shell')
 return [*deepcopy(shell),test[1]]

def client_probe_contract(row,item):
 declared=configured_health_argv(row['Config']);need(not item.get('require_configured_healthcheck')or declared is not None,'configured_client_health_required')
 probes=[]if declared is None else[declared];additional=item.get('probe_argv')
 if additional is not None:
  need(type(additional)is list and additional and all(type(v)is str for v in additional),'client_probe_shape')
  if additional not in probes:probes.append(deepcopy(additional))
 return probes,declared is not None

def unit_state(unit):
 def limits():
  hard=resource.getrlimit(resource.RLIMIT_FSIZE)[1];bound=8192 if hard==resource.RLIM_INFINITY else min(8192,hard);resource.setrlimit(resource.RLIMIT_FSIZE,(bound,bound));resource.setrlimit(resource.RLIMIT_CORE,(0,0))
 fd=os.memfd_create('datastore-unit-metadata',os.MFD_CLOEXEC)
 with os.fdopen(fd,'w+b')as output:
  result=subprocess.run(['/usr/bin/systemctl','show',unit,'--property=ActiveState,SubState,MainPID,DropInPaths,NeedDaemonReload'],stdout=output,stderr=subprocess.DEVNULL,timeout=10,preexec_fn=limits)
  output.seek(0);raw=output.read(8193)
 need(result.returncode==0 and len(raw)<=8192,'client_unit_unknown')
 return dict(line.split('=',1)for line in raw.decode().splitlines())

class Activation:
 def __init__(self,plan,state_dir,deadline,engine=None):
  self.plan=plan;self.state=state_dir;self.end=deadline;self.reserve=plan.get('rollback_seconds',600)
  self.engine=engine or Docker(deadline-self.reserve);self.nonce=plan.get('operation_id',uuid.uuid4().hex);self.pending=None;self.phase='preflight'
  self.stores=[];self.clients=[];self.events=[];self.journal=None;self.restoring=False;self.state_created=False;self.failure_phase=None
 def event(self,name,**facts):
  row={'event':name,'phase':self.phase,**facts};raw=encoded(row)
  need(sum(map(len,self.events))+len(raw)<=160*1024,'journal_bound');self.events.append(raw)
  if self.journal is not None:self.journal.write(raw);self.journal.flush();os.fsync(self.journal.fileno())
  print(raw.decode(),end='',flush=True)
 def fence(self):
  f=self.plan['fence'];p=Path(f['path']);s=p.lstat();now=time.time()
  need(p.is_absolute()and p.resolve(strict=True)==p and stat.S_ISREG(s.st_mode)and s.st_uid==0 and s.st_nlink==1 and stat.S_IMODE(s.st_mode)==0o600 and 0<s.st_size<=4096,'client_fence_identity')
  with p.open('rb')as incoming:raw=incoming.read(4097)
  need(len(raw)==s.st_size and sha256(raw).hexdigest()==f['sha256'],'client_fence_changed');value=json.loads(raw)
  need(value.get('schema')=='normal_datastore_client_fence/1'and value.get('operation_id')==self.nonce and type(value.get('issued_epoch'))is int and type(value.get('deadline_epoch'))is int and value['deadline_epoch']==self.end,'client_fence_scope')
  need(value['issued_epoch']<=now+5 and 0<value['deadline_epoch']-value['issued_epoch']<=5400 and now<value['deadline_epoch']and s.st_mtime>=value['issued_epoch']-1 and s.st_ctime>=value['issued_epoch']-1,'client_fence_freshness')
  units=f['units'];need(type(units)is list and len(units)<=64 and len(set(units))==len(units)and((bool(units)and f.get('no_Host_readers')is False)or(not units and f.get('no_Host_readers')is True)),'explicit_Host_reader_scope')
  need(re.fullmatch('[A-Za-z0-9_.-]+[.]conf',f['dropin_name'])is not None,'client_dropin_name')
  for unit in units:
   need(re.fullmatch('[A-Za-z0-9_.@:-]+[.](?:service|timer|socket)',unit)is not None,'client_unit')
   dropin=Path('/etc/systemd/system')/(unit+'.d')/f['dropin_name'];info=dropin.lstat();need(stat.S_ISREG(info.st_mode)and info.st_uid==0 and not info.st_mode&0o022 and 0<info.st_size<=4096,'client_dropin_identity')
   with dropin.open('rb')as incoming:condition=incoming.read(4097)
   need(b'ConditionPathExists=!'+str(p).encode()in condition.splitlines(),'client_dropin_binding')
   value=unit_state(unit)
   need(value.get('ActiveState')=='inactive'and value.get('MainPID','0')=='0'and value.get('NeedDaemonReload')=='no'and str(dropin)in value.get('DropInPaths','').split(),'host_client_not_quiesced')
 def guard(self):
  need(time.time()<self.end-(0 if self.restoring else self.reserve),'work_deadline');self.fence()
 def change(self,action,path,body=None,*,seconds=30):
  self.guard();self.pending=action;self.event('requested',action=action)
  result=self.engine.request('POST',path,body,timeout_seconds=seconds)
  self.pending=None;self.event('returned',action=action);return result
 def inspect(self,cid):
  row=self.engine.inspect(cid);need(row['Id']==cid,'container_identity');return row
 def restart(self,cid,policy):
  self.change('restart_policy:'+cid,'/containers/'+cid+'/update',{'RestartPolicy':policy})
  need(self.inspect(cid)['HostConfig']['RestartPolicy']==policy,'restart_policy_unknown')
 def stop(self,cid):
  row=self.inspect(cid)
  if row['State']['Running']:
   self.change('stop:'+cid,'/containers/'+cid+'/stop?t=45',seconds=60)
  after=self.inspect(cid);need(after['State']['Running']is False and after['State']['Pid']==0,'stop_outcome_unknown')
 def rename(self,cid,name):self.change('rename:'+cid,'/containers/'+cid+'/rename?name='+quote(name,safe=''))
 def disconnect(self,cid):
  row=self.inspect(cid)
  owned=next((s for s in self.stores if s['new']==cid),None)
  if owned is not None and (row['Config'].get('Labels')or{}).get(LABEL)==self.nonce and row['Image']==owned['plan']['new_image']and never_started_unattached(row):
   self.event('never_started_candidate_has_no_attached_endpoints',candidate=cid,pending_network_intent_retained=True);return
  for network in list(endpoints(row)):self.change('disconnect:'+cid+':'+network,'/networks/'+network+'/disconnect',{'Container':cid,'Force':False})
  need(not self.inspect(cid)['NetworkSettings']['Networks'],'network_detach_unknown')
 def connect(self,cid,saved):
  current=endpoints(self.inspect(cid));need(set(current)<=set(saved),'old_network_changed')
  for network,spec in saved.items():
   if network not in current:self.change('connect:'+cid+':'+network,'/networks/'+network+'/connect',{'Container':cid,'EndpointConfig':spec})
 def one_writer(self,store,allowed=None):
  rows=self.engine.request('GET','/containers/json?all=1');need(type(rows)is list,'writer_inventory')
  source=next(m['Source']for m in store['old']['Mounts']if m['Destination']==store['data_path'])
  for row in rows:
   if row.get('State')=='running'and any(m.get('Source')==source and m.get('RW')is True for m in row.get('Mounts',[])):
    need(row['Id']==allowed,'another_data_writer_running')
 def ready(self,cid,kind,version=None):
  end=min(self.engine.end,time.time()+90);actual=None
  while time.time()<end:
   self.guard();row=self.inspect(cid);need(row['State']['Running']is True,'candidate_exited')
   if kind=='postgres':
    script='u=${POSTGRES_USER:-postgres}; d=${POSTGRES_DB:-$u}; export PGPASSWORD=${POSTGRES_PASSWORD:-}; exec psql -h127.0.0.1 -X -A -t -v ON_ERROR_STOP=1 -U "$u" -d "$d" -c "SELECT current_setting(\x27server_version_num\x27)::int,1"'
    rc,raw=self.engine.execute(cid,['/bin/sh','-ec',script])
    if rc==0 and re.fullmatch(rb'15[0-9]{4}\|1\n?',raw):actual=int(raw.split(b'|')[0]);break
   else:
    command=row['Config'].get('Cmd')or[];need(command[:1]==['redis-server']and(len(command)-1)%2==0,'normal_redis_command')
    options=dict(zip(command[1::2],command[2::2]));auth=options.get('--requirepass','');env=['REDISCLI_AUTH='+auth]if auth else[]
    rc,raw=self.engine.execute(cid,['redis-cli','-e','--no-auth-warning','--raw','PING'],environment=env)
    if rc==0 and raw.strip()==b'PONG':
     rc,raw=self.engine.execute(cid,['redis-cli','-e','--no-auth-warning','--raw','INFO','server'],environment=env)
     if rc==0:
      values=dict(line.split(':',1)for line in raw.decode().splitlines()if':'in line);actual=values.get('redis_version');break
   time.sleep(.25)
  need(actual is not None,'datastore_not_ready')
  need(version is None or actual==version,'datastore_version');return actual
 def preflight(self):
  need(os.geteuid()==0 and self.plan['schema']=='normal_datastore_activation/1','Root_plan')
  need(re.fullmatch('[a-f0-9]{32}',self.nonce)is not None and self.plan.get('operation_id')==self.nonce,'operation_identity')
  need(self.plan.get('qualified_images_and_data_drills_accepted_by_Root')is True,'Root_qualification_required')
  need(self.plan['site']in SCOPE and{v['role']for v in self.plan['stores']}==SCOPE[self.plan['site']]and len(self.plan['stores'])==len(SCOPE[self.plan['site']]),'exact_store_scope')
  need(180<=self.reserve<=1200 and self.reserve+600<self.end-time.time()<=5400,'finite_deadline')
  self.fence();parent=self.state.parent;need(parent.is_absolute()and parent.resolve(strict=True)==parent and parent.stat().st_uid==0 and not parent.stat().st_mode&0o022,'state_parent')
  need(not os.path.lexists(self.state),'fresh_state_directory');self.state.mkdir(mode=0o700);self.state_created=True;self.journal=(self.state/'events.jsonl').open('xb');os.chmod(self.state/'events.jsonl',0o600)
  ids=set()
  for item in self.plan['stores']:
   for key in('old_cid','old_image','new_image','canonical_config'):need(re.fullmatch('[a-f0-9]{64}'if key=='old_cid'else'sha256:[a-f0-9]{64}',item[key])is not None,'image_or_CID')
   need(item['old_cid']not in ids,'duplicate_target');ids.add(item['old_cid']);old=self.inspect(item['old_cid']);labels=old['Config'].get('Labels')or{}
   need(old['Image']==item['old_image']and old['State']['Running']is True and labels.get('com.docker.compose.project')==item['project']and labels.get('com.docker.compose.service')==item['service'],'source_scope')
   previous_image=self.engine.image(item['old_image']);candidate_image=self.engine.image(item['new_image']);need(previous_image['Id']==item['old_image']and candidate_image['Id']==item['new_image'],'candidate_daemon_identity')
   previous_labels=(previous_image.get('Config')or{}).get('Labels')or{};candidate_labels=(candidate_image.get('Config')or{}).get('Labels')or{}
   need(type(previous_labels)is dict and type(candidate_labels)is dict and all(type(k)is str and type(v)is str for labels in(previous_labels,candidate_labels)for k,v in labels.items()),'candidate_image_labels')
   body=create_body(old,item['new_image'],self.nonce,item['canonical_config'],default_labels=candidate_labels,previous_labels=previous_labels)
   expected=item['mounts'];need(all(any(all(m.get(k)==v for k,v in wanted.items())for m in old['Mounts'])for wanted in expected),'source_data_mount')
   kind='redis'if item['role'].endswith('redis')else'postgres';data='/data'if kind=='redis'else'/var/lib/postgresql/data';need(sum(m['Destination']==data and m['RW']is True for m in old['Mounts'])==1,'data_mount')
   need(old['HostConfig']['NetworkMode']not in('none','host','default')and endpoints(old),'normal_project_network')
   store={'plan':item,'old':old,'kind':kind,'body':body,'data_path':data,'new':None,'changed':False,'renamed':False,'detached':False,'saved_endpoints':endpoints(old)}
   self.one_writer(store,old['Id']);store['old_version']=self.ready(old['Id'],kind);self.stores.append(store)
  for item in self.plan['clients']:
   cid=item['cid'];need(re.fullmatch('[a-f0-9]{64}',cid)is not None and cid not in ids,'client_scope');ids.add(cid);row=self.inspect(cid);need(row['Image']==item['image'],'client_image')
   probes,declared=client_probe_contract(row,item)
   if row['State']['Running']:
    for index,argv in enumerate(probes):
     rc,_=self.engine.execute(cid,argv)
     if declared and index==0:self.event('client_baseline_configured_health_observed',cid=cid,cached_Docker_health=row['State'].get('Health',{}).get('Status'),configured_actual_exit=rc)
     need(rc==0,'client_baseline_probe_failed')
   self.clients.append({'plan':item,'old':row,'changed':False,'probes':probes,'declared_health':declared})
  need(self.clients,'explicit_client_scope')
  metadata={'schema':'normal_datastore_public_runtime_metadata/1','site':self.plan['site'],'stores':[{'role':s['plan']['role'],'old_cid':s['old']['Id'],'old_image':s['old']['Image'],'original_name':s['old']['Name'],'original_restart_policy':s['old']['HostConfig']['RestartPolicy'],'mounts':mounts(s['old']),'endpoints':s['saved_endpoints']}for s in self.stores],'clients':[{'cid':c['old']['Id'],'image':c['old']['Image'],'original_running':c['old']['State']['Running'],'original_restart_policy':c['old']['HostConfig']['RestartPolicy']}for c in self.clients]}
  raw=encoded(metadata);need(len(raw)<=160*1024,'metadata_bound')
  with(self.state/'runtime-metadata.json').open('xb')as out:os.chmod(out.name,0o600);out.write(raw);out.flush();os.fsync(out.fileno())
  self.event('preflight_passed',site=self.plan['site'],stores=len(self.stores),clients=len(self.clients))
 def stop_clients(self):
  for client in self.clients:
   row=client['old'];client['changed']=True;self.restart(row['Id'],NO_RESTART);self.stop(row['Id'])
 def switch(self,store):
  item=store['plan'];old=store['old'];cid=old['Id'];self.phase=item['role'];store['changed']=True
  self.restart(cid,NO_RESTART);self.stop(cid);self.one_writer(store)
  name=old['Name'].lstrip('/');self.rename(cid,name+'.rollback-'+self.nonce);store['renamed']=True
  self.disconnect(cid);store['detached']=True
  self.pending='create:'+item['role'];self.event('requested',action=self.pending)
  result=self.engine.request('POST','/containers/create?name='+quote(name,safe=''),store['body'])
  need(type(result)is dict and re.fullmatch('[a-f0-9]{64}',result.get('Id',''))is not None,'create_outcome_unknown');store['new']=result['Id'];self.pending=None;self.event('returned',action='create:'+item['role'],candidate=store['new'])
  row=self.inspect(store['new']);need(not row['State']['Running']and row['State']['Pid']==0,'created_not_stopped');parity(row,old,item['new_image'],self.nonce,item['canonical_config'],created=True,body=store['body'])
  self.one_writer(store);self.change('start:'+store['new'],'/containers/'+store['new']+'/start');self.ready(store['new'],store['kind'],150019 if store['kind']=='postgres'else'7.4.12');self.one_writer(store,store['new'])
  parity(self.inspect(store['new']),old,item['new_image'],self.nonce,item['canonical_config'],body=store['body'])
  self.event('candidate_datastore_ready',role=item['role'],candidate=store['new'],canonical_config=item['canonical_config'])
 def clients_restore(self):
  for client in self.clients:
   old=client['old'];cid=old['Id'];current=self.inspect(cid);wanted=deepcopy(old['HostConfig']);wanted['RestartPolicy']=deepcopy(NO_RESTART)
   need(current['Image']==old['Image']and current['Config']==old['Config']and mounts(current)==mounts(old)and current['HostConfig']==wanted,'client_changed')
   if old['State']['Running']and not current['State']['Running']:self.change('start_client:'+cid,'/containers/'+cid+'/start')
   self.restart(cid,old['HostConfig']['RestartPolicy'])
  # Resume the full admitted cohort before waiting for dependent health.
  # A capture role may require the processor/fact-sync role later in the list.
  for client in self.clients:
   old=client['old'];cid=old['Id'];current=self.inspect(cid)
   need(current['Image']==old['Image']and current['Config']==old['Config']and current['HostConfig']==old['HostConfig']and mounts(current)==mounts(old),'client_changed')
   if old['State']['Running']:
    end=min(self.engine.end,time.time()+60)
    while time.time()<end:
     self.guard();current=self.inspect(cid);need(current['Image']==old['Image']and current['Config']==old['Config']and current['HostConfig']==old['HostConfig']and mounts(current)==mounts(old),'client_changed')
     okay=current['State']['Running']is True
     if client['declared_health']or old['State'].get('Health',{}).get('Status')=='healthy':okay=okay and current['State'].get('Health',{}).get('Status')=='healthy'
     if okay:
      for probe in client['probes']:
       rc,_=self.engine.execute(cid,probe)
       if rc!=0:okay=False;break
     if okay:break
     time.sleep(.25)
    else:raise RuntimeError('client_not_ready')
 def rollback(self):
  self.restoring=True;self.engine.end=self.end;self.phase='rollback';self.guard()
  for client in self.clients:
   if client['changed']:self.restart(client['old']['Id'],NO_RESTART);self.stop(client['old']['Id'])
  for store in reversed(self.stores):
   if not store['changed']:continue
   old=store['old'];new=store['new']
   if new is not None:
    row=self.inspect(new);need((row['Config'].get('Labels')or{}).get(LABEL)==self.nonce and row['Image']==store['plan']['new_image'],'candidate_ownership')
    self.restart(new,NO_RESTART);self.stop(new);self.disconnect(new);self.rename(new,old['Name'].lstrip('/')+'.failed-'+self.nonce)
   self.one_writer(store)
   if store['renamed']:self.rename(old['Id'],old['Name'].lstrip('/'))
   self.connect(old['Id'],store['saved_endpoints']);network_parity(self.inspect(old['Id']),old,created=True);self.one_writer(store)
   if not self.inspect(old['Id'])['State']['Running']:self.change('restart_old:'+old['Id'],'/containers/'+old['Id']+'/start')
   self.ready(old['Id'],store['kind'],store['old_version']);self.restart(old['Id'],old['HostConfig']['RestartPolicy']);self.one_writer(store,old['Id'])
   restored=self.inspect(old['Id']);need(restored['Image']==old['Image']and restored['Name']==old['Name']and restored['Config']==old['Config']and restored['HostConfig']==old['HostConfig']and mounts(restored)==mounts(old),'old_runtime_not_restored');network_parity(restored,old)
  self.clients_restore();self.event('known_failure_old_runtime_restored')
 def receipt(self,status,primary=None):
  value={'schema':'normal_datastore_activation_receipt/1','status':status,'site':self.plan['site'],'nonce':self.nonce,'phase':self.phase,'primary_error':primary,'primary_failure_phase':self.failure_phase,'pending_requested_action':self.pending,'roles':[{'role':s['plan']['role'],'old_container':s['old']['Id'],'old_image':s['old']['Image'],'candidate_container':s['new'],'candidate_daemon_image':s['plan']['new_image'],'canonical_config':s['plan']['canonical_config']}for s in self.stores],'old_containers_and_data_retained':True,'external_Host_fence_released':False,'data_restore_migration_or_seed':False,'security_accepted':False}
  path=self.state/'receipt.json';raw=encoded(value)
  with(self.state/'receipt.json.partial').open('xb')as out:os.chmod(out.name,0o600);out.write(raw);out.flush();os.fsync(out.fileno())
  need(not path.exists(),'receipt_exists');os.rename(self.state/'receipt.json.partial',path);return value
 def run(self):
  try:
   self.preflight();self.phase='stop_clients';self.stop_clients()
   for store in self.stores:self.switch(store)
   self.phase='restore_clients'
   for store in self.stores:self.restart(store['new'],store['old']['HostConfig']['RestartPolicy']);parity(self.inspect(store['new']),store['old'],store['plan']['new_image'],self.nonce,store['plan']['canonical_config'],final=True,body=store['body'])
   self.clients_restore()
   for store in self.stores:self.one_writer(store,store['new'])
   self.phase='completed';self.event('all_exact_datastores_active',stores=len(self.stores));return self.receipt('APPLIED_RUNTIME_ONLY')
  except Exception as error:
   self.failure_phase=self.phase
   code=str(error)if type(error)is RuntimeError and re.fullmatch('[A-Za-z0-9_]{1,80}',str(error))else'activation_unknown'
   unknown=self.pending is not None or code.startswith('normal_docker_')or code in{'activation_unknown','stop_outcome_unknown','network_detach_unknown','create_outcome_unknown','restart_policy_unknown'}
   self.event('incomplete',code=code,requested_action=self.pending,automatic_retry=False,unknown_outcome=unknown)
   status='NEEDS_RECONCILIATION'if unknown else'PREFLIGHT_REFUSED'
   changed=any(s['changed']for s in self.stores)or any(c['changed']for c in self.clients)
   if changed and not unknown:
    try:self.rollback();status='FAILED_RESTORED_OLD'
    except Exception:self.event('rollback_incomplete',requested_action=self.pending,automatic_retry=False);status='NEEDS_RECONCILIATION'
   if self.state_created:self.receipt(status,code)
   raise
  finally:
   if self.journal is not None:self.journal.close()

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--plan',type=Path,required=True);p.add_argument('--state-dir',type=Path,required=True);p.add_argument('--deadline-epoch',type=int,required=True);a=p.parse_args();os.umask(0o077)
 try:Activation(read_json(a.plan),a.state_dir,a.deadline_epoch).run();return 0
 except Exception:return 1
if __name__=='__main__':raise SystemExit(main())
