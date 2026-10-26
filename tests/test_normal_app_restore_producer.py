"""Owned public restore fixtures; ambient sockets/clients/production inputs denied."""
from contextlib import ExitStack
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import ModuleType,SimpleNamespace
from unittest.mock import patch
import ast,gzip,io,json,os,stat,tarfile,tempfile,time,unittest

ROOT=Path(__file__).resolve().parents[1]
DENIED=('subprocess.Popen','subprocess.run','subprocess.call','os.system','os.popen','os.fork','os.kill','os.killpg',
 'socket.socket','socket.create_connection','socket.getaddrinfo','urllib.request.urlopen','ctypes.CDLL','resource.setrlimit')
def module(name,path):
 value=ModuleType(name);value.__file__=str(path);exec(compile(path.read_bytes(),str(path),'exec'),value.__dict__);return value
with ExitStack() as stack:
 for key in DENIED:stack.enter_context(patch(key,side_effect=AssertionError('ambient denied')))
 M=module('_restore_producer_fixture',ROOT/'deploy/maintenance/normal_app_restore_producer.py')
 C=module('_restore_contract_fixture',ROOT/'deploy/maintenance/normal_app_restore_rehearsal.py')
 U=module('_restore_archive_fixture',ROOT/'deploy/maintenance/normal_backup_archive_restore.py')

class ProducerTests(unittest.TestCase):
 def setUp(self):
  self.assertEqual((os.geteuid(),os.getegid()),(0,0));self.stack=ExitStack();self.addCleanup(self.stack.close)
  for key in DENIED:self.stack.enter_context(patch(key,side_effect=AssertionError('ambient denied')))
  self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.restore-producer-',dir=ROOT)));self.root.chmod(0o700)
 def file(self,name,raw):
  path=self.root/name;path.write_bytes(raw);path.chmod(0o600);return path
 def runtime(self):
  value=M.Runtime.__new__(M.Runtime);value.C=C;value.stack=self.stack;value.end=time.time()+300;value.control={'nonce':'a'*32,'producer':{'path':'/public/normal_app_restore_producer.py','sha256':'1'*64},
   'limits':{k:{'memory':512*1024**2,'nano_cpus':1000000000,'pids':128,'log_bytes':1048576,'log_files':2} for k in ('api','db','redis')}}
  value.lease={'network_id':'b'*64,'bridge':'nar'+'a'*12,'db':{'ip':'172.29.1.2'},'redis':{'ip':'172.29.1.3'}}
  value.events=[];value.images={};value.container_ids=[];value.container_roles={};value.removed=set();value.started=[]
  value.check=lambda:None;value.check_sources=lambda:None;value.event=lambda step:value.events.append(step)
  return value
 def test_source_helpers_are_exact_public_byte_copies(self):
  for path,pin,size in ((ROOT/'deploy/maintenance/bounded_operational_capture.py',M.CAPTURE_SHA,5230),
   (ROOT/'deploy/maintenance/normal_backup_archive_restore.py',M.UPLOADS_SHA,8981)):
   raw=path.read_bytes();self.assertEqual((len(raw),sha256(raw).hexdigest()),(size,pin))
 def test_contract_loads_the_actual_complete_producer_and_default_is_unarmed(self):
  actual=C.producer(Path(M.__file__).read_bytes(),M.__file__)
  self.assertTrue(all(getattr(actual,name).__globals__ is actual.__dict__ for name in C.METHODS))
  with self.assertRaises(C.Refused):C.validate_control(C.unarmed_control(),time.time())
 def test_bootstrap_checks_owner_before_payload_and_holds_full9(self):
  path=self.file('public.py',b'PUBLIC');pin=sha256(b'PUBLIC').hexdigest()
  old=None
  try:
   with self.assertRaisesRegex(RuntimeError,'source_changed'),ExitStack() as held:
    raw,check=M.cold(path,pin,held);self.assertEqual(raw,b'PUBLIC')
    old=os.open(path,os.O_RDONLY);path.unlink();self.file('public.py',b'PUBLIC');check()
  finally:
   if old is not None:os.close(old)
 def test_foreign_owned_bootstrap_and_artifact_never_open_payload(self):
  path=self.file('wrong-owner',b'PUBLIC');os.chown(path,1,1)
  with patch.object(M.os,'open',side_effect=AssertionError('no open')):
   with self.assertRaisesRegex(RuntimeError,'source'):M.cold(path,sha256(b'PUBLIC').hexdigest(),self.stack)
   with self.assertRaisesRegex(RuntimeError,'normal_backup'):M.Artifact(path,6,sha256(b'PUBLIC').hexdigest(),self.stack)
 def test_private_receipt_metadata_rejects_0644_or_public_parent_before_any_payload(self):
  raw=b'OWNED PUBLIC RECEIPT';path=self.file('receipt.json',raw);path.chmod(0o644)
  with patch.object(M.os,'pread',side_effect=AssertionError('no pread')):
   with self.assertRaises(RuntimeError):M.Artifact(path,len(raw),sha256(raw).hexdigest(),self.stack,private_parent=True)
  path.chmod(0o600);self.root.chmod(0o755)
  try:
   with patch.object(M.os,'pread',side_effect=AssertionError('no pread')):
    with self.assertRaises(RuntimeError):M.Artifact(path,len(raw),sha256(raw).hexdigest(),self.stack,private_parent=True)
  finally:self.root.chmod(0o700)
 def readonly_reference(self,tag,target='/app/runtime/coin-inference'):
  value=self.runtime();value.ro=[];value.snapshots={};value.check=lambda:M.Runtime.check(value)
  # Use unchanged normal parent-metadata primitives, without App.Site or IO.
  S=module('_readonly_parent_'+tag,ROOT/'deploy/maintenance/canonical_app_activation_site.py');S.C=SimpleNamespace(require=C.need)
  value.S=S;path=self.root/tag;path.mkdir(mode=0o700)
  value.admission={'roles':{'app':{'actual':{'Mounts':[{'Type':'bind','Source':str(path),'Destination':target,'RW':False}]}}}}
  value.capture_readonly_references();return value,path,value.ro[0][0]
 def test_live_model_publish_changes_content_metadata_without_reading_or_repinning(self):
  for i,target in enumerate(sorted(C.LIVE_MODEL_TARGETS)):
   value,path,row=self.readonly_reference('model'+str(i),target);original=deepcopy(row['identity']);fd=value.ro[0][1]
   with patch.object(M.os,'pread',side_effect=AssertionError('model payload must not be read')):
    (path/'pending').write_bytes(b'PUBLIC SYNTHETIC MODEL');(path/'pending').rename(path/'active');(path/'new-generation').mkdir()
    value.check();C.opaque_check(row)
   self.assertEqual(row['identity'],original);self.assertEqual(M.identity(os.fstat(fd))[:5],original[:5])
   self.assertNotEqual(M.identity(os.fstat(fd)),original);self.assertGreater(os.fstat(fd).st_nlink,original[5])
 def test_live_directory_replacement_alias_unlink_owner_mode_and_parent_drift_refuse(self):
  for kind in ('replacement','alias','unlink','owner','mode','parent'):
   value,path,row=self.readonly_reference('drift-'+kind);restore=lambda:None
   try:
    if kind in ('replacement','alias'):
     path.rename(path.with_name(path.name+'-old'))
     if kind=='alias':path.symlink_to(path.with_name(path.name+'-old'),target_is_directory=True)
     else:path.mkdir(mode=0o700)
    elif kind=='unlink':path.rmdir()
    elif kind=='owner':os.chown(path,1,1);restore=lambda p=path:os.chown(p,0,0)
    elif kind=='mode':path.chmod(0o777);restore=lambda p=path:p.chmod(0o700)
    else:self.root.chmod(0o755);restore=lambda:self.root.chmod(0o700)
    with self.assertRaises((C.Refused,OSError)):value.check()
   finally:restore()
 def test_certificate_directory_keeps_immutable_full9_and_model_open_race_is_rejected(self):
  value,path,row=self.readonly_reference('cert','/app/certs');(path/'new-cert-entry').mkdir()
  with self.assertRaises(C.Refused):value.check()
  value=self.runtime();value.ro=[];S=module('_readonly_parent_race',ROOT/'deploy/maintenance/canonical_app_activation_site.py');S.C=SimpleNamespace(require=C.need);value.S=S
  path=self.root/'initial-model';path.mkdir(mode=0o700)
  value.admission={'roles':{'app':{'actual':{'Mounts':[{'Type':'bind','Source':str(path),'Destination':'/app/runtime/coin-inference','RW':False}]}}}}
  actual=os.open;changed=[]
  def opened(name,flags,*args,**kwargs):
   if Path(name)==path and flags&os.O_PATH and not changed:
    changed.append(True);(path/'publication-during-open').mkdir()
   return actual(name,flags,*args,**kwargs)
  with (patch.object(M.os,'open',side_effect=opened),patch.object(M.os,'pread',side_effect=AssertionError('no model payload')),
   self.assertRaises(C.Refused)):value.capture_readonly_references()
  self.assertEqual(changed,[True]);self.assertEqual(value.ro,[])
 def rdb_preopen_case(self,tag):
  runtime=self.runtime();runtime.U=U;runtime.prefix={'maximum_restore_bytes':1024**2};runtime.volume_paths={}
  runtime.lease['volumes']={};runtime.restore_tar=lambda kind:None;runtime.volume=lambda kind:'owned_stage'
  base=self.root/tag;base.mkdir()
  for kind in ('redis','redisstage'):
   path=base/kind;path.mkdir();fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY);self.addCleanup(os.close,fd)
   runtime.volume_paths[kind]=('owned_'+kind,path,fd,M.identity(os.fstat(fd))[:5])
  leaf=base/'redisstage'/'dump.rdb';leaf.write_bytes(b'REDIS0011PUBLIC_RDB_DATA');leaf.chmod(0o600)
  return runtime,leaf
 def test_RDB_FIFO_symlink_foreign_owner_and_hardlink_refuse_before_open(self):
  for kind in ('fifo','symlink','owner','hardlink'):
   runtime,leaf=self.rdb_preopen_case(kind)
   if kind=='fifo':leaf.unlink();os.mkfifo(leaf,0o600)
   elif kind=='symlink':leaf.rename(leaf.with_name('other'));leaf.symlink_to('other')
   elif kind=='owner':os.chown(leaf,1,1)
   else:os.link(leaf,leaf.with_name('hard'))
   actual=os.open;opened=[]
   def open_file(path,*a,**kw):
    if path=='dump.rdb':opened.append(path);raise AssertionError('RDB payload was opened')
    return actual(path,*a,**kw)
   with patch.object(M.os,'open',side_effect=open_file),self.assertRaises(C.Refused):runtime.restore_redis_rdb()
   self.assertEqual(opened,[])
 def test_RDB_same_byte_swap_at_open_refuses_before_any_pread(self):
  runtime,leaf=self.rdb_preopen_case('swap');old=os.open(leaf,os.O_RDONLY);self.addCleanup(os.close,old)
  actual=os.open;swapped=[]
  def open_file(path,*a,**kw):
   if path=='dump.rdb' and not swapped:
    swapped.append(True);leaf.rename(leaf.with_name('old'));leaf.write_bytes(b'REDIS0011PUBLIC_RDB_DATA');leaf.chmod(0o600)
   return actual(path,*a,**kw)
  with patch.object(M.os,'open',side_effect=open_file),patch.object(M.os,'pread',side_effect=AssertionError('no pread')),self.assertRaises(C.Refused):runtime.restore_redis_rdb()
  self.assertEqual(swapped,[True])
 def test_artifact_pread_stream_bound_hash_and_same_byte_swap(self):
  raw=b'OWNED PUBLIC PAYLOAD'*100;path=self.file('archive',raw)
  with ExitStack() as held:
   artifact=M.Artifact(path,len(raw),sha256(raw).hexdigest(),held);reader=artifact.reader()
   self.assertEqual(reader.read(7)+reader.read(len(raw)-7),raw)
   with self.assertRaises(RuntimeError):reader.read(1024**2+1)
  old=None
  try:
   with self.assertRaisesRegex(RuntimeError,'normal_backup'),ExitStack() as held:
    artifact=M.Artifact(path,len(raw),sha256(raw).hexdigest(),held);old=os.open(path,os.O_RDONLY)
    path.unlink();self.file('archive',raw);artifact.check()
  finally:
   if old is not None:os.close(old)
 def test_artifact_symlink_hardlink_size_and_hash_refusals(self):
  path=self.file('archive',b'PUBLIC');alias=self.root/'alias';alias.symlink_to(path)
  for p,count,pin in ((alias,6,sha256(b'PUBLIC').hexdigest()),(path,5,sha256(b'PUBLIC').hexdigest()),(path,6,'0'*64)):
   with self.assertRaises((RuntimeError,OSError)),ExitStack() as stack:M.Artifact(p,count,pin,stack)
  os.link(path,self.root/'hard')
  with self.assertRaises(RuntimeError):M.Artifact(path,6,sha256(b'PUBLIC').hexdigest(),self.stack)
 def test_actual_normal_backup_helpers_preserve_source_AST(self):
  b=ROOT/'scripts/run_production_backup.py';r=ROOT/'scripts/rehearse_production_migration.py'
  actual={};real_compile=__import__('builtins').compile
  def recorded(source,filename,*args,**kwargs):
   if isinstance(source,ast.Module):actual[str(filename)]=deepcopy(source)
   return real_compile(source,filename,*args,**kwargs)
  with patch('builtins.compile',side_effect=recorded):
   B=M.normal_api(b.read_bytes(),b,{},M.BACKUP_FUNCTIONS)
   R=M.normal_api(r.read_bytes(),r,{k:getattr(B,k) for k in ('DEFAULT_BACKUP_DIR','DEFAULT_BACKUP_RECEIPT_DIR','DEFAULT_IRAN_PULL_DIR',*M.BACKUP_FUNCTIONS)},M.REHEARSAL_FUNCTIONS)
  self.assertEqual(set(actual),{str(b),str(r)})
  for path,loaded,names in ((b,B,M.BACKUP_FUNCTIONS),(r,R,M.REHEARSAL_FUNCTIONS)):
   tree=ast.parse(path.read_bytes());chosen={n.name:n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names}
   compiled={n.name:n for n in actual[str(path)].body if isinstance(n,ast.FunctionDef)}
   self.assertEqual(set(chosen),names);self.assertEqual(set(compiled),names)
   for name,node in chosen.items():
    self.assertEqual(ast.dump(compiled[name],include_attributes=False),ast.dump(node,include_attributes=False))
    function=getattr(loaded,name);self.assertIs(function.__globals__,loaded.__dict__);self.assertEqual(function.__code__.co_name,name)
  self.assertFalse(hasattr(R,'run_guarded_alembic'));self.assertFalse(hasattr(R,'export_source_tree'))
 def test_redis_accepts_only_fixed_normal_authoritative_RDB_format(self):
  cfg={'Cmd':['redis-server','--appendonly','yes'],'Env':[]};self.assertEqual(M.redis_persistence(cfg),{'--appendonly':'yes'})
  for command in (['redis-server'],['redis-server','/private/redis.conf'],['redis-server','--appendonly','no'],
   ['redis-server','--appendonly','yes','--loadmodule','anything'],['redis-server','--appendonly','yes','--dir','/elsewhere'],
   ['redis-server','--appendonly','yes','--appendonly','yes']):
   with self.assertRaises(RuntimeError):M.redis_persistence({**cfg,'Cmd':command})
 def test_actual_public_compose_redis_options_are_admitted_without_command_rewrite(self):
  expected=['redis-server','--appendonly','yes','--appendfsync','everysec','--maxmemory','0','--maxmemory-policy','noeviction']
  for name in ('docker-compose.yml','docker-compose.iran.yml'):
   text=(ROOT/name).read_text();block=text.split('\n  redis:\n',1)[1]
   match=__import__('re').search(r'^    command:\n((?:      - .*\n)+)',block,__import__('re').MULTILINE);self.assertIsNotNone(match)
   command=[]
   for line in match[1].splitlines():
    item=line[len('      - '):];default=__import__('re').fullmatch(r'\$\{[A-Z_]+:-(.*)\}',item)
    command.append(default[1] if default else item)
   self.assertEqual(command,expected);config={'Cmd':command,'Env':['TZ=UTC']};before=deepcopy(config)
   self.assertEqual(M.redis_persistence(config),{'--appendonly':'yes','--appendfsync':'everysec','--maxmemory':'0','--maxmemory-policy':'noeviction'})
   self.assertEqual(config,before)
 def test_redis_memory_policy_types_units_and_unknown_loader_options_are_closed(self):
  base=['redis-server','--appendonly','yes','--appendfsync','everysec','--maxmemory','0','--maxmemory-policy','noeviction']
  for memory in ('0','1024','1k','1KB','8m','512mb','1g','2GB','9223372036854775807'):
   value=base[:];value[6]=memory;before=value[:];self.assertEqual(M.redis_persistence({'Cmd':value})['--maxmemory'],memory);self.assertEqual(value,before)
  for policy in ('noeviction','volatile-lru','allkeys-lru','volatile-lfu','allkeys-lfu','volatile-random','allkeys-random','volatile-ttl','NOEVICTION'):
   value=base[:];value[8]=policy;self.assertEqual(M.redis_persistence({'Cmd':value})['--maxmemory-policy'],policy)
  for index,bad in ((6,True),(6,0),(6,None),(6,[]),(6,'-1'),(6,'1.5gb'),(6,'1KiB'),(6,'1K'),(6,'1tb'),(6,'1 gb'),(6,' 1gb'),
   (6,'9223372036854775808'),(6,'9223372036854775807gb'),(8,False),(8,None),(8,'unknown'),(8,'allKeys-lru'),(8,'allkeys-lru extra'),(4,'sometimes')):
   value=base[:];value[index]=bad
   with self.subTest(index=index,bad=bad),self.assertRaisesRegex(RuntimeError,'normal_redis_persistence'):M.redis_persistence({'Cmd':value})
  for extra in (['--maxmemory','1gb'],['--maxmemory-policy','noeviction'],['--loadmodule','/private/plugin.so'],['--include','/private/redis.conf'],['--dir','/production'],['--dbfilename','../dump.rdb']):
   with self.assertRaisesRegex(RuntimeError,'normal_redis_persistence'):M.redis_persistence({'Cmd':base+extra})
 def test_fixed_archive_roots_only_no_path_relaxation(self):
  audit=M.archive_components(U.components,'audit');redis=M.archive_components(U.components,'redis')
  self.assertEqual(audit('audit_trail/2026/public.json',False),('2026','public.json'));self.assertEqual(redis('./dump.rdb',False),('dump.rdb',))
  self.assertEqual(redis('./',True),())
  for name in ('../private','/etc/private','redirect/../private','./../private'):
   with self.assertRaises(U.Refused):redis(name,False)
 def tar(self,rows):
  buf=io.BytesIO()
  with tarfile.open(fileobj=buf,mode='w:gz') as tar:
   for name,typ,raw in rows:
    info=tarfile.TarInfo(name);info.type=typ;info.mode=0o700 if typ==tarfile.DIRTYPE else 0o600;info.size=len(raw);tar.addfile(info,io.BytesIO(raw) if raw else None)
  return buf.getvalue()
 def test_real_unchanged_archive_restore_handles_audit_bytes_and_refuses_links(self):
  runtime=self.runtime();runtime.U=U;runtime.prefix={'maximum_restore_bytes':1024**2};runtime.volume_paths={};runtime.artifacts={}
  destination=self.root/'volume';destination.mkdir();fd=os.open(destination,os.O_RDONLY|os.O_DIRECTORY);self.addCleanup(os.close,fd)
  raw=self.tar([('audit_trail',tarfile.DIRTYPE,b''),('audit_trail/public',tarfile.REGTYPE,b'OWNED PUBLIC')]);path=self.file('audit.tar.gz',raw)
  runtime.volume_paths['audit']=('fixture',destination,fd,M.identity(os.fstat(fd))[:5]);runtime.artifacts['audit']=M.Artifact(path,len(raw),sha256(raw).hexdigest(),self.stack)
  result=runtime.restore_tar('audit');self.assertTrue(result['complete_stream_verified']);self.assertEqual((destination/'public').read_bytes(),b'OWNED PUBLIC')
  empty=self.root/'empty';empty.mkdir();fd2=os.open(empty,os.O_RDONLY|os.O_DIRECTORY);self.addCleanup(os.close,fd2)
  raw=self.tar([('audit_trail',tarfile.DIRTYPE,b''),('audit_trail/link',tarfile.SYMTYPE,b'')]);path=self.file('bad.tar.gz',raw)
  runtime.volume_paths['audit']=('fixture',empty,fd2,M.identity(os.fstat(fd2))[:5]);runtime.artifacts['audit']=M.Artifact(path,len(raw),sha256(raw).hexdigest(),self.stack)
  with self.assertRaises(U.Refused):runtime.restore_tar('audit')
 def test_Docker_response_bound_before_accumulation_and_private_error_closed(self):
  class Response:
   status=200
   def __init__(self,chunks,length=None):self.chunks=list(chunks);self.length=length;self.reads=[]
   def getheader(self,name):return self.length
   def read1(self,n):
    self.reads.append(n)
    if not self.chunks:return b''
    raw=self.chunks.pop(0);self.assert_bound=n;return raw
  class Client:
   def __init__(self,*a,**k):pass
   def request(self,*a,**k):pass
   def getresponse(self):return response
   def close(self):pass
  response=Response([b'{"ok":',b'true}'])
  with patch.object(M,'UnixHTTP',Client):self.assertEqual(M.Docker(time.time()+10).request('GET','/public',maximum=20),{'ok':True})
  response=Response([b'PRIVATE_SENTINEL'],length='9999999')
  with patch.object(M,'UnixHTTP',Client),self.assertRaisesRegex(RuntimeError,'normal_docker_body'):M.Docker(time.time()+10).request('GET','/public',maximum=10)
  self.assertEqual(response.reads,[])
  response=Response([b'1'*10,b'x'])
  with patch.object(M,'UnixHTTP',Client),self.assertRaisesRegex(RuntimeError,'normal_docker_body'):M.Docker(time.time()+10).request('GET','/public',maximum=10)
  self.assertEqual(response.reads,[11,1])
  response=Response([b'PRIVATE_SENTINEL']);response.status=500
  with patch.object(M,'UnixHTTP',Client),self.assertRaisesRegex(RuntimeError,'^normal_docker_refused$'):M.Docker(time.time()+10).request('GET','/public')
 def test_Docker_exec_demultiplexes_only_bounded_stdout(self):
  engine=M.Docker(time.time()+10);calls=[]
  frame=lambda kind,raw:bytes([kind])+b'\0'*3+len(raw).to_bytes(4,'big')+raw
  def request(method,path,value=None,**kw):
   calls.append((method,path,value,kw))
   if path.endswith('/exec'):return {'Id':'e'*64}
   if path.endswith('/start'):return frame(2,b'PRIVATE_STDERR')+frame(1,b'PUBLIC')
   return {'Running':False,'ExitCode':0}
  engine.request=request;self.assertEqual(engine.execute('a'*64,['normal']), (0,b'PUBLIC'))
  engine.request=lambda method,path,*a,**kw: {'Id':'e'*64} if path.endswith('/exec') else b'\1\0\0\0\0\0\0\xff'
  with self.assertRaisesRegex(RuntimeError,'normal_docker_body'):engine.execute('a'*64,['normal'])
 def test_create_namespaces_labels_retains_exact_private_env_and_starts_nothing(self):
  runtime=self.runtime();seen=[];runtime.engine=SimpleNamespace()
  cfg={'Env':['NORMAL_PRIVATE=PRIVATE_SENTINEL'],'Cmd':['normal'],'Entrypoint':None,'User':'10001',
   'Labels':{'com.docker.compose.project':'production','com.docker.compose.image':'sha256:'+'c'*64}}
  normal=deepcopy(cfg);image='sha256:'+'d'*64
  def request(method,path,value=None):seen.append((method,path,value));return {'Id':'e'*64}
  runtime.engine.request=request
  runtime.engine.inspect=lambda cid:{'Id':cid,'Image':image,'Config':seen[0][2], 'State':{'Running':False,'Pid':0},
   'NetworkSettings':{'Networks':{'owned_network':{'NetworkID':'b'*64}}}}
  identifier=runtime.create('app_candidate',image,cfg,M.cap_host(runtime.control['limits']['api']))
  self.assertEqual(identifier,'e'*64);self.assertEqual(cfg,normal);actual=seen[0][2]
  self.assertEqual(actual['Env'],normal['Env']);self.assertEqual(actual['User'],'10001');self.assertIsNone(actual['Entrypoint'])
  self.assertEqual(actual['Labels']['com.docker.compose.project'],'nar_'+'a'*32)
  self.assertEqual([x for x in runtime.events if 'start' in x],[]);self.assertEqual(len(seen),1)
 def test_store_create_refuses_any_production_volume_before_start(self):
  runtime=self.runtime();runtime.lease['volumes']={'db':'owned_db'};runtime.volume_paths={'db':('owned_db',self.root,0,[])}
  image='sha256:'+'d'*64;config={'Env':['POSTGRES_PASSWORD=PRIVATE_SENTINEL'],'Cmd':['postgres'],'Entrypoint':['normal'],'User':'','WorkingDir':'','Labels':{}}
  host={'NetworkMode':'b'*64,'Binds':None,'Mounts':[{'Type':'volume','Source':'owned_db','Target':'/var/lib/postgresql/data','ReadOnly':False}]}
  sent=[];row={}
  runtime.engine=SimpleNamespace(request=lambda *args:sent.append(args) or {'Id':'e'*64},inspect=lambda cid:row)
  row.update(Id='e'*64,Image=image,Config={**deepcopy(config),'Labels':M.labels('a'*32,'db')},HostConfig=deepcopy(host),State={'Running':False,'Pid':0},
   NetworkSettings={'Networks':{'owned':{'NetworkID':'b'*64}}},Mounts=[{'Type':'volume','Name':'production_db','Source':'/NOT_READ_OR_MOUNTED','Destination':'/var/lib/postgresql/data','RW':True}])
  with self.assertRaises(C.Refused):runtime.create('db',image,config,host)
  self.assertEqual(len(sent),1);self.assertEqual(runtime.started,[]);self.assertNotIn('create_db_complete',runtime.events)
 def merged_store(self,config,change=None):
  runtime=self.runtime();runtime.lease['volumes']={'db':'owned_db'};runtime.volume_paths={'db':('owned_db',self.root,0,[])}
  image='sha256:'+'d'*64;sent=[];row={}
  host={'NetworkMode':'b'*64,'Binds':None,'Mounts':[{'Type':'volume','Source':'owned_db','Target':'/var/lib/postgresql/data','ReadOnly':False}]}
  def request(method,path,body):
   sent.append(deepcopy(body));actual=deepcopy(body)
   # Real Engine API container.Config has Go string User/WorkingDir even
   # when image DockerOCIImageConfig omitted their empty default fields.
   for key in ('User','WorkingDir'):
    if key not in actual or actual[key] is None:actual[key]=''
   if change:change(actual)
   row.update(Id='e'*64,Image=image,Config=actual,HostConfig=deepcopy(host),State={'Running':False,'Pid':0},
    NetworkSettings={'Networks':{'owned':{'NetworkID':'b'*64}}},Mounts=[{'Type':'volume','Name':'owned_db',
     'Source':str(self.root),'Destination':'/var/lib/postgresql/data','RW':True}])
   return {'Id':'e'*64}
  runtime.engine=SimpleNamespace(request=request,inspect=lambda cid:row)
  return runtime,image,host,sent
 def test_real_engine_string_defaults_admit_omitted_image_User_without_command_or_Env_rewrite(self):
  config={'Env':['POSTGRES_PASSWORD=PRIVATE_SENTINEL','PATH=/usr/local/bin:/usr/bin'],
   'Cmd':['postgres'],'Entrypoint':['docker-entrypoint.sh'],'WorkingDir':'/','Labels':{}}
  for wanted in (config,{k:v for k,v in config.items() if k!='WorkingDir'},{**config,'User':'70'}):
   before=deepcopy(wanted);runtime,image,host,sent=self.merged_store(wanted)
   self.assertEqual(runtime.create('db',image,wanted,host),'e'*64);self.assertEqual(wanted,before)
   self.assertEqual(sent[0]['Env'],wanted['Env']);self.assertEqual(sent[0]['Cmd'],wanted['Cmd'])
   self.assertEqual(sent[0]['Entrypoint'],wanted['Entrypoint']);self.assertEqual(runtime.started,[])
   self.assertIn('create_db_complete',runtime.events)
 def test_string_defaults_refuse_explicit_null_types_nonempty_privilege_or_other_Config_drift(self):
  base={'Env':['POSTGRES_PASSWORD=PRIVATE_SENTINEL'],'Cmd':['postgres'],'Entrypoint':['docker-entrypoint.sh'],'Labels':{}}
  for key in ('User','WorkingDir'):
   for malformed in (None,False,0,[],{}):
    config={**base,key:malformed};runtime,image,host,_=self.merged_store(config)
    with self.subTest(body_key=key,bad=malformed),self.assertRaisesRegex(C.Refused,'normal_runtime'):
     runtime.create('db',image,config,host)
    self.assertNotIn('create_db_complete',runtime.events);self.assertEqual(runtime.started,[])
   for actual in (None,False,0,'root' if key=='User' else '/'):
    runtime,image,host,_=self.merged_store(base,lambda row,k=key,v=actual:row.update({k:v}))
    with self.subTest(actual_key=key,bad=actual),self.assertRaisesRegex(C.Refused,'normal_runtime'):
     runtime.create('db',image,base,host)
    self.assertEqual(runtime.started,[])
  for key,actual in (('Env',base['Env']+['UNBOUND=VALUE']),('Cmd',['postgres','UNBOUND']),('Entrypoint',['UNBOUND'])):
   runtime,image,host,_=self.merged_store(base,lambda row,k=key,v=actual:row.update({k:v}))
   with self.subTest(field=key),self.assertRaisesRegex(C.Refused,'normal_runtime'):runtime.create('db',image,base,host)
   self.assertEqual(runtime.started,[])
 def test_network_key_is_not_confused_with_actual_network_ID(self):
  runtime=self.runtime();self.assertEqual(runtime.endpoint({'NetworkSettings':{'Networks':{'owned_normal_name':{'NetworkID':'b'*64}}}})['NetworkID'],'b'*64)
  for networks in ({},{'name':{'NetworkID':'c'*64}},{'one':{'NetworkID':'b'*64},'two':{'NetworkID':'b'*64}}):
   with self.assertRaises(C.Refused):runtime.endpoint({'NetworkSettings':{'Networks':networks}})
 def test_real_shaped_never_started_endpoint_keeps_raw_generated_fields_empty(self):
  runtime=self.runtime();endpoint={'NetworkID':'','EndpointID':'','MacAddress':'','Gateway':'','IPAddress':'','IPPrefixLen':0,
   'IPv6Gateway':'','GlobalIPv6Address':'','GlobalIPv6PrefixLen':0,'DNSNames':None,'Aliases':[],
   'IPAMConfig':{'IPv4Address':'172.29.1.4'}}
  row={'NetworkSettings':{'Networks':{'nar_'+'a'*32+'_network':endpoint}},'HostConfig':{'NetworkMode':'b'*64},
   'State':{'Running':False,'Pid':0,'StartedAt':'0001-01-01T00:00:00Z'},'RestartCount':0}
  before=deepcopy(row);self.assertEqual(runtime.endpoint(row,stopped=True)['NetworkID'],'');self.assertEqual(row,before)
  with self.assertRaises(C.Refused):runtime.endpoint(row)
  for field,bad in (('NetworkID','c'*64),('EndpointID','d'*64),('IPAddress','192.0.2.7'),('IPPrefixLen',True)):
   changed=deepcopy(row);next(iter(changed['NetworkSettings']['Networks'].values()))[field]=bad
   with self.assertRaises(C.Refused):runtime.endpoint(changed,stopped=True)
  for state in ({'Running':True,'Pid':123,'StartedAt':'0001-01-01T00:00:00Z'},
   {'Running':False,'Pid':0,'StartedAt':'2026-10-25T00:00:00Z'}):
   changed=deepcopy(row);changed['State']=state
   with self.assertRaises(C.Refused):runtime.endpoint(changed,stopped=True)
 def ready_runtime(self,health='absent',status='absent',execute=None,metadata=None):
  from tests.test_normal_app_restore_rehearsal import NormalRestoreTests
  operands=NormalRestoreTests();operands.root=self.root
  control,lease,private,_,_=operands.case();normal=private['normal']['candidate']
  if health=='absent':normal['Config'].pop('Healthcheck')
  else:normal['Config']['Healthcheck']=deepcopy(health)
  normal['Config']['Labels'].update(M.labels(control['nonce'],'app_candidate'))
  measured=operands.measured(normal,lease,control['candidate_image_id']);measured.update(running=True,pid=1234)
  state={'Running':True,'Restarting':False,'Paused':False,'Dead':False,'Pid':1234,'StartedAt':'2026-10-26T00:00:00Z'}
  if status!='absent':state['Health']={'Status':status}
  row={'Id':measured['id'],'Image':measured['image'],'Config':deepcopy(measured['Config']),'HostConfig':deepcopy(measured['HostConfig']),
   'Mounts':deepcopy(measured['Mounts']),'State':state,'RestartCount':0,'NetworkSettings':{'Networks':{'owned':{'NetworkID':lease['network_id']}}}}
  runtime=self.runtime();runtime.control=control;runtime.lease=lease;runtime.normal=private['normal'];runtime.current=('candidate',measured['id'])
  runtime.images={'app_candidate':measured['image']};runtime.end=3;calls=[]
  runtime.support={'docker_git_commit':M.DNS_ENGINE_GIT[:7]}
  runtime.isolation=lambda:calls.append('isolation')
  def executed(cid,argv):
   calls.append(('HTTP',cid,argv));return execute(row) if execute else (0,b'')
  def request(method,path):
   self.assertEqual(method,'GET');calls.append('metadata:'+path)
   value=({'Version':'29.9.2','GitCommit':M.DNS_ENGINE_GIT[:7],'Os':'linux'} if path=='/version' else
    {'ServerVersion':'29.9.2','OSType':'linux','KernelVersion':M.os.uname().release,'CgroupVersion':'2','OomKillDisable':False})
   return metadata(path,deepcopy(value)) if metadata else value
  runtime.engine=SimpleNamespace(inspect=lambda cid:deepcopy(row),execute=executed,request=request)
  clock=[0]
  def now():clock[0]+=1;return clock[0]
  self.stack.enter_context(patch.object(M.time,'time',side_effect=now));self.stack.enter_context(patch.object(M.time,'sleep'))
  return runtime,row,calls
 def test_readiness_absent_null_or_disabled_normal_health_requires_HTTP_and_keeps_raw_Config(self):
  for health in ('absent',None,{'Test':['NONE']}):
   directory=self.root/str(len(list(self.root.iterdir())));directory.mkdir(mode=0o700);original=self.root;self.root=directory
   try:
    runtime,row,calls=self.ready_runtime(health);before=deepcopy(runtime.normal)
    ready=runtime.ready_api(row['Id']);self.assertEqual(ready['normal_api_config_returncode'],0);self.assertEqual(ready['pid'],1234)
    self.assertEqual(runtime.normal,before);self.assertTrue(any(type(x) is tuple and x[0]=='HTTP' for x in calls));self.assertEqual(runtime.started,[])
   finally:self.root=original
 def test_readiness_enabled_normal_check_waits_starting_then_requires_real_healthy(self):
  def ready(row):row['State']['Health']['Status']='healthy';return 0,b''
  runtime,row,calls=self.ready_runtime({'Test':['CMD-SHELL','normal public API check']},'starting',ready)
  self.assertEqual(runtime.ready_api(row['Id'])['pid'],1234);self.assertEqual(sum(type(x) is tuple for x in calls),2)
 def test_readiness_mode_does_not_waive_HTTP_health_type_status_or_lifecycle(self):
  cases=[('absent','healthy',None),('absent',None,None),('absent','absent',lambda row:(1,b'')),
   ({'Test':['CMD','normal']},'absent',None),({'Test':['CMD','normal']},'unhealthy',None),
   ({'Test':['CMD','normal']},'unknown',None),({'Test':['CMD','normal']},True,None)]
  for i,(health,status,execute) in enumerate(cases):
   directory=self.root/str(i);directory.mkdir(mode=0o700);original=self.root;self.root=directory
   try:
    runtime,row,_=self.ready_runtime(health,status,execute)
    with self.subTest(health=health,status=status),self.assertRaisesRegex(C.Refused,'normal_runtime'):runtime.ready_api(row['Id'])
    self.assertEqual(runtime.started,[])
   finally:self.root=original
 def test_readiness_bad_admitted_check_or_late_Config_and_PID_drift_refuse(self):
  malformed=({},False,[],{'Test':None},{'Test':[]},{'Test':['NONE','extra']},{'Test':['CMD']},
   {'Test':['OTHER','normal']},{'Test':['CMD',False]},{'Test':['CMD','normal'],'Interval':True})
  cases=[(health,None) for health in malformed]
  cases += [('absent',lambda row:(row['Config']['Env'].append('UNBOUND=PUBLIC'),(0,b''))[1]),
   ('absent',lambda row:(row['State'].update(Pid=2345),(0,b''))[1]),
   ({'Test':['CMD','normal']},lambda row:(row['State'].update(Pid=2345),(0,b''))[1])]
  for i,(health,execute) in enumerate(cases):
   directory=self.root/str(i);directory.mkdir(mode=0o700);original=self.root;self.root=directory
   try:
    runtime,row,_=self.ready_runtime(health,'starting' if type(health) is dict and execute else 'absent',execute)
    with self.subTest(health=health),self.assertRaisesRegex(C.Refused,'normal_runtime'):runtime.ready_api(row['Id'])
   finally:self.root=original
 def test_real_shaped_created_false_started_null_then_stopped_null_keeps_raw_HostConfig(self):
  runtime,row,calls=self.ready_runtime();normal=runtime.normal['candidate'];normal['HostConfig']['OomKillDisable']=None
  row['HostConfig']['OomKillDisable']=None;before=deepcopy(normal)
  result=runtime.ready_api(row['Id']);self.assertEqual(result['normal_api_config_returncode'],0)
  self.assertEqual(runtime.normal['candidate'],before);self.assertIsNone(row['HostConfig']['OomKillDisable'])
  self.assertEqual(calls.count('metadata:/version'),2);self.assertEqual(calls.count('metadata:/info'),2)
  self.assertNotIn('OomKillDisable',result);self.assertNotIn('engine_support',result)
  runtime.container_roles[row['Id']]='app_candidate';closed=[]
  runtime.S=SimpleNamespace(Site=SimpleNamespace(cgroup=lambda owner,value:lambda:closed.append(True)),row=lambda value:value)
  request=runtime.engine.request
  def stop(method,path):
   if method=='POST':
    self.assertEqual(path,'/containers/'+row['Id']+'/stop?t=30');row['State'].update(Running=False,Pid=0);return None
   return request(method,path)
  runtime.engine.request=stop;stopped=runtime.stop(row['Id'])
  self.assertEqual(closed,[True]);self.assertFalse(stopped['State']['Running']);self.assertEqual(stopped['State']['Pid'],0)
  self.assertIsNone(stopped['HostConfig']['OomKillDisable']);self.assertEqual(runtime.normal['candidate'],before)
 def test_running_null_requires_fresh_real_metadata_and_rejects_explicit_boolean_drift(self):
  mutations=[lambda path,v:(v.pop('CgroupVersion'),v)[1] if path=='/info' else v,
   lambda path,v:{**v,'CgroupVersion':2} if path=='/info' else v,
   lambda path,v:{**v,'CgroupVersion':'1','OomKillDisable':True} if path=='/info' else v,
   lambda path,v:{**v,'OomKillDisable':0} if path=='/info' else v,
   lambda path,v:{**v,'OomKillDisable':True} if path=='/info' else v,
   lambda path,v:{**v,'Version':'29.9.1'} if path=='/version' else v,
   lambda path,v:{**v,'GitCommit':'0'*40} if path=='/version' else v,
   lambda path,v:{**v,'KernelVersion':'6.1.0-different'} if path=='/info' else v]
  for i,mutate in enumerate(mutations):
   directory=self.root/str(i);directory.mkdir(mode=0o700);original=self.root;self.root=directory
   try:
    runtime,row,_=self.ready_runtime(metadata=mutate);runtime.normal['candidate']['HostConfig']['OomKillDisable']=None
    row['HostConfig']['OomKillDisable']=None
    with self.subTest(case=i),self.assertRaises(C.Refused):runtime.ready_api(row['Id'])
   finally:self.root=original
  for i,expected in enumerate((False,True)):
   directory=self.root/('boolean-'+str(i));directory.mkdir(mode=0o700);original=self.root;self.root=directory
   try:
    runtime,row,_=self.ready_runtime();runtime.normal['candidate']['HostConfig']['OomKillDisable']=expected
    row['HostConfig']['OomKillDisable']=None
    with self.subTest(expected=expected),self.assertRaises(C.Refused):runtime.ready_api(row['Id'])
   finally:self.root=original
 def test_started_backend_is_rechecked_after_final_parity_and_never_taken_from_control(self):
  count=[0]
  def metadata(path,value):
   if path=='/info':
    count[0]+=1
    if count[0]==2:value['CgroupVersion']='1'
   return value
  runtime,row,calls=self.ready_runtime(metadata=metadata)
  runtime.control['engine_support']={'CgroupVersion':'2','OomKillDisable':False}
  runtime.normal['candidate']['HostConfig']['OomKillDisable']=None;row['HostConfig']['OomKillDisable']=None
  with self.assertRaises(C.Refused):runtime.ready_api(row['Id'])
  self.assertEqual(count,[2]);self.assertEqual(runtime.started,[])
 def test_runtime_container_caps_include_real_swap_pids_cpu_log_bounds(self):
  cap=M.cap_host(self.runtime().control['limits']['api']);self.assertEqual(cap['Memory'],cap['MemorySwap']);self.assertGreater(cap['PidsLimit'],0)
  self.assertEqual(cap['NanoCpus'],1000000000);self.assertEqual(cap['LogConfig'],{'Type':'json-file','Config':{'max-size':'1048576','max-file':'2'}})
 def test_actual_Go_mount_omitted_false_default_is_typed_and_keeps_raw_fields(self):
  # api/types/mount/mount.go31 uses json omitempty for false ReadOnly.
  plain=[{'Type':'volume','Source':'owned','Target':'/data'}];before=deepcopy(plain)
  self.assertEqual(C.host_mounts(plain),C.host_mounts([{**plain[0],'ReadOnly':False}]));self.assertEqual(plain,before)
  for extra in ({'ReadOnly':0},{'ReadOnly':None},{'ReadOnly':'false'},{'VolumeOptions':{}},{'Consistency':'cached'}):
   with self.assertRaises(C.Refused):C.host_mounts([{**plain[0],**extra}])
  with self.assertRaises(C.Refused):C.host_mounts(plain+plain)
 def test_expanded_stream_limit_counts_before_restore_without_extracting(self):
  raw=gzip.compress(b'PUBLIC'*10000);path=self.file('compressed.gz',raw)
  artifact=M.Artifact(path,len(raw),sha256(raw).hexdigest(),self.stack)
  self.assertEqual(M.expanded_count(artifact,60000,lambda:None),60000)
  with self.assertRaisesRegex(RuntimeError,'normal_restore_size'):M.expanded_count(artifact,59999,lambda:None)
  self.assertEqual(sorted(p.name for p in self.root.iterdir()),['compressed.gz'])
 def test_actual_RDB_selection_never_copies_live_AOF_to_active_volume(self):
  runtime=self.runtime();runtime.U=U;runtime.prefix={'maximum_restore_bytes':1024**2};runtime.volume_paths={};runtime.artifacts={};runtime.redis_auth=''
  runtime.redis_old={'Id':'sha256:'+'d'*64};runtime.admission={'datastores':{'redis':{'Config':{'Cmd':['redis-server','--appendonly','yes'],'Env':[]}}}}
  runtime.lease['volumes']={'redis':'owned_redis'}
  for name in ('redis','redisstage'):
   path=self.root/name;path.mkdir();fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY);self.addCleanup(os.close,fd)
   runtime.volume_paths[name]=('owned_'+name,path,fd,M.identity(os.fstat(fd))[:5])
  raw=self.tar([('.',tarfile.DIRTYPE,b''),('./dump.rdb',tarfile.REGTYPE,b'REDIS0011PUBLIC_RDB_CHECKSUM'),
   ('./appendonlydir',tarfile.DIRTYPE,b''),('./appendonlydir/live.aof',tarfile.REGTYPE,b'INCONSISTENT_LIVE_AOF')])
  path=self.file('redis.tar.gz',raw);runtime.artifacts['redis']=M.Artifact(path,len(raw),sha256(raw).hexdigest(),self.stack)
  runtime.volume=lambda role:runtime.volume_paths[role][0];runtime.store_host=lambda role:{}
  calls=[];runtime.create=lambda *a,**kw:calls.append(a) or 'e'*64;runtime.start=lambda cid:None;runtime.wait_redis=lambda cid:None
  def redis(cid,args):
   if args==['DBSIZE']:return '3'
   if args[:2]==['CONFIG','SET']:return 'OK'
   return 'aof_enabled:1\naof_rewrite_in_progress:0\naof_last_bgrewrite_status:ok'
  runtime.redis_exec=redis;runtime.stop=lambda cid:calls.append('stopped');runtime.remove=lambda cid:calls.append('removed')
  runtime.restore_redis_rdb()
  self.assertEqual((self.root/'redis'/'dump.rdb').read_bytes(),b'REDIS0011PUBLIC_RDB_CHECKSUM')
  self.assertEqual([p.name for p in (self.root/'redis').iterdir()],['dump.rdb'])
  self.assertTrue((self.root/'redisstage'/'appendonlydir'/'live.aof').is_file())
  self.assertEqual(calls[0][2]['Cmd'],['redis-server','--appendonly','no']);self.assertEqual(calls[-2:],['stopped','removed'])
 def test_actual_fixed_PG_guard_argv_has_one_local_socket_binding(self):
  runtime=self.runtime();seen=[];runtime.command=lambda argv,timeout:(seen.append(argv) or (0,b'PUBLIC\n'))
  argv=['/usr/bin/docker','--host=unix:///var/run/docker.sock','image','inspect','PUBLIC']
  self.assertEqual(runtime.public_query(argv),'PUBLIC');self.assertEqual(seen,[argv])
  with self.assertRaises(C.Refused):runtime.public_query(['/usr/bin/docker','--host=tcp://foreign','image','inspect'])
 def test_actual_timer_uses_absolute_deadline_before_any_container_start(self):
  runtime=self.runtime();runtime.control.update(issued_at=int(time.time()),deadline=int(time.time())+300,machine_id='b'*32)
  runtime.control['limits']['lifetime_seconds']=120;runtime.end=runtime.control['deadline'];runtime.run_root=self.root
  runtime.pg_candidate=SimpleNamespace(image='sha256:'+'d'*64);runtime.redis_old={'Id':'sha256:'+'e'*64}
  runtime.control.update(candidate_image_id='sha256:'+'f'*64,old_image_id='sha256:'+'c'*64)
  calls=[];runtime.require_command=lambda argv:calls.append(argv) or b'';runtime.expiry_view=lambda:None
  runtime.arm_expiry();self.assertEqual(len(calls),1);self.assertIn('--on-calendar=@'+str(runtime.control['issued_at']+120),calls[0])
  expected=['/usr/bin/python3','-I','-B',runtime.control['producer']['path'],'--expire',str(self.root/'expiry.json'),
   '--expiry-sha256',runtime.expiry_pin,'--source-sha256',runtime.control['producer']['sha256']]
  self.assertEqual(runtime.expiry_argv,expected);self.assertEqual(calls[0][-len(expected):],expected)
  for forbidden in ('--control','--contract','--apply','--confirm'):self.assertNotIn(forbidden,runtime.expiry_argv)
  self.assertEqual(runtime.started,[])
  expiry=json.loads((self.root/'expiry.json').read_bytes());self.assertEqual(expiry['producer'],runtime.control['producer'])
  self.assertEqual(expiry['images']['app_candidate'],runtime.control['candidate_image_id'])
 def bridge_json(self,runtime):
  table='nar_'+runtime.control['nonce'];rows=[{'metainfo':{}},{'table':{'family':'bridge','name':table}}]
  for name,hook in (('host_input','input'),('isolated_forward','forward'),('host_output','output')):
   rows.append({'chain':{'family':'bridge','table':table,'name':name,'type':'filter','hook':hook,'prio':0,'policy':'accept'}})
  for rule in C.bridge_rules(runtime.lease['bridge'],runtime.lease['db']['ip'],runtime.lease['redis']['ip']):
   rows.append({'rule':{'family':'bridge','table':table,**deepcopy(rule)}})
  return rows
 def test_actual_nft_readback_requires_exact_drop_allow_and_reply_rules(self):
  runtime=self.runtime();nft=self.bridge_json(runtime);before=deepcopy(nft);seen=[]
  runtime.require_command=lambda argv:(seen.append(argv),json.dumps({'nftables':nft}).encode())[1]
  runtime.firewall_view();self.assertEqual(nft,before);self.assertEqual(seen[0][-2:],['bridge','nar_'+runtime.control['nonce']])
  for bad in ('accept','wrong_bridge','foreign','priority','family','drop_arp','allow_ipv6','duplicate_chain','wrong_port','wrong_states','stale_set','stale_ether'):
   nft[:]=deepcopy(before)
   if bad=='accept':nft[-1]['rule']['expr'][-1]={'accept':None}
   elif bad=='wrong_bridge':nft[-1]['rule']['expr'][0]['match']['right']='production_bridge'
   elif bad=='foreign':nft.append({'rule':{'family':'bridge','table':'unowned','chain':'isolated_forward','expr':[{'accept':None}]}})
   elif bad=='priority':nft[2]['chain']['prio']=-10
   elif bad=='family':nft[1]['table']['family']='inet'
   elif bad=='drop_arp':nft[5]['rule']['expr'][-1]={'drop':None}
   elif bad=='allow_ipv6':nft[8]['rule']['expr'][2]['match']['right']='ip6'
   elif bad=='duplicate_chain':nft[3]=deepcopy(nft[2])
   elif bad=='wrong_port':nft[8]['rule']['expr'][-2]['match']['right']=80
   elif bad=='wrong_states':nft[10]['rule']['expr'][-2]['match']['right']=['established','related','new']
   elif bad=='stale_set':nft[10]['rule']['expr'][-2]['match']['right']={'set':['established','related']}
   else:nft[8]['rule']['expr'].insert(2,{'match':{'op':'==','left':{'payload':{'protocol':'ether','field':'type'}},'right':'ip'}})
   with self.assertRaises(C.Refused):runtime.firewall_view()
 def test_native_bridge_exclusive_creation_real_owned_rule_file_and_foreign_rules_preserved(self):
  runtime=self.runtime();runtime.run_root=self.root;seen=[];nft=self.bridge_json(runtime)
  foreign={'nftables':[{'metainfo':{}},{'table':{'family':'bridge','name':'foreign-public-fixture'}}]};changed=False
  def command(argv):
   seen.append(argv)
   if '--file' in argv:return b''
   if argv[-2:]==['list','ruleset']:return json.dumps(foreign).encode()
   self.assertEqual(argv[-3:],['table','bridge','nar_'+runtime.control['nonce']]);return json.dumps({'nftables':nft}).encode()
  runtime.require_command=command;runtime.prepare_firewall();raw=(self.root/'firewall.nft').read_text();lines=raw.splitlines()
  self.assertEqual(lines[0],'create table bridge nar_'+runtime.control['nonce']);self.assertEqual(sum(x.startswith('add chain') for x in lines),3)
  self.assertEqual(sum(x.startswith('add rule') for x in lines),10);self.assertIn('meta ibrname',raw);self.assertIn('meta obrname',raw)
  self.assertNotIn('iifname',raw);self.assertNotIn('ip6',raw);self.assertNotIn('flush',raw);self.assertNotIn('sysctl',raw);self.assertNotIn('modprobe',raw)
  self.assertEqual((self.root/'firewall.nft').stat().st_mode,0o100600);self.assertEqual(sum('--file' in v for v in seen),1)
  with self.assertRaises(FileExistsError):runtime.prepare_firewall()
  self.assertEqual(sum('--file' in v for v in seen),1)
 def test_foreign_rule_drift_after_creation_is_closed_without_retry(self):
  runtime=self.runtime();runtime.run_root=self.root;seen=[];nft=self.bridge_json(runtime);global_reads=[]
  def command(argv):
   seen.append(argv)
   if '--file' in argv:return b''
   if argv[-2:]==['list','ruleset']:
    global_reads.append(1);return json.dumps({'nftables':[{'table':{'family':'bridge','name':'sentinel','comment':'before' if len(global_reads)==1 else 'changed'}}]}).encode()
   return json.dumps({'nftables':nft}).encode()
  runtime.require_command=command
  with self.assertRaisesRegex(C.Refused,'isolation'):runtime.prepare_firewall()
  self.assertEqual(sum('--file' in v for v in seen),1);self.assertNotIn('firewall_complete',runtime.events)
 def test_cleanup_only_exact_bridge_table_once_unknown_delete_retained(self):
  runtime=self.runtime();nft=self.bridge_json(runtime);runtime.rules_before={'chains':{},'rules':[]}
  calls=[];runtime.firewall_view=lambda:runtime.rules_before;runtime.foreign_rules=lambda:[{'foreign':'unchanged-public-fixture'}]
  runtime.require_command=lambda argv:calls.append(argv) or b'';runtime.delete_firewall()
  self.assertEqual(calls,[['/usr/sbin/nft','delete','table','bridge','nar_'+runtime.control['nonce']]])
  calls.clear();runtime.events.clear()
  def unknown(argv):calls.append(argv);raise OSError('PRIVATE_UNKNOWN_NOT_EMITTED')
  runtime.require_command=unknown
  with self.assertRaises(OSError):runtime.delete_firewall()
  self.assertEqual(len(calls),1);self.assertNotIn('firewall_delete_complete',runtime.events)
 def test_approved72_sensitive_bodies_remain_exact_with_no_ignored_fixture_dependency(self):
  expected={'normal_app_restore_rehearsal.py:held_source': 'c633f611e9561436b7030375f4ff3e3df2ec878cd227cc1efea94ef8696cdb43', 'normal_app_restore_rehearsal.py:candidate_association': '53212173c4ea995e5910a35dba010811ae1bc053e20ba32bbc960c5257c37c7c', 'normal_app_restore_rehearsal.py:clone_targets': 'e70dc5b67d55ef417df117adf5a6f6c03ab3fe73872dc0691808df3699c84ded', 'normal_app_restore_rehearsal.py:normal_role_scope': '3a11822f75c83f4816661aaff564ccef6de7532dfa570c68389abf355052537f', 'normal_app_restore_rehearsal.py:opaque_check': 'e6936976fa60ade14f2266daac5caef3ea262ca7a09b8eadef66ad6dea93721e', 'normal_app_restore_rehearsal.py:host_mounts': '6154d84a04b4b82256cb68639b097555e71273c0e5f02087e5020fd522a908fe', 'normal_app_restore_producer.py:Docker.request': '18dc6505a9f1eed042e578a63b3ce1874a5d9221f5bd1cd749067110f4dd38b4', 'normal_app_restore_producer.py:Docker.execute': 'a8b7873968fb1fbee1695f65e59a0ce59e1ba27a08264eedcdb77cae0598ba9e', 'normal_app_restore_producer.py:normal_api': 'c5e6c7a268201125b1d5fea5958a021c302af1a91c99cf55bf75cf1a32369c81', 'normal_app_restore_producer.py:archive_components': '948c04e2942b5ba2db65176b4f18b70b377804f479b796d6e4433e8b5179157e', 'normal_app_restore_producer.py:Runtime.start': 'a149298baa7e2e0327ca77075270b8c98fb7ae424eec1fd073a6dc2a077938bf', 'normal_app_restore_producer.py:Runtime.stop': '21650e01d383250719cb0d295a28f4d5c67d9d720c1413e26a64cc858582ec58', 'normal_app_restore_producer.py:Runtime.remove': 'aa3856c0fe00656f40b7723d8574e3823a039fe710db98760aebda7bb414c462', 'normal_app_restore_producer.py:Runtime.restore_tar': '4b6ac9053d5c1861abc109f0a6bce6fb736749e540b1d634482637f1ca58ac27', 'normal_app_restore_producer.py:Runtime.restore_redis_rdb': '474fbaee7aac0b7461d5cd46a3c5731c51312c4f6e302d601be4946720f50458', 'normal_app_restore_producer.py:Runtime.namespace_probe': 'aba4f4c30615d581600e0a7e7744c42e267e261810eca0787a2e2bd1559a712b', 'normal_app_restore_producer.py:Runtime.measure_data': '4d904d93c678a76c0265a1e6d1000f9868a9756798ff4ee1c50e4ab9647d7e38', 'normal_app_restore_producer.py:Runtime.restart_stores': '599c788b8fb83d89d721bcbb9540a0173ca889368715a0b8f2049e2ad5765509'}
  for name in ('normal_app_restore_rehearsal.py','normal_app_restore_producer.py'):
   source=(ROOT/'deploy/maintenance'/name).read_text();tree=ast.parse(source);found={}
   def visit(nodes,prefix=''):
    for node in nodes:
     if isinstance(node,(ast.FunctionDef,ast.ClassDef)):
      key=name+':'+prefix+node.name
      if key in expected:found[key]=sha256(ast.get_source_segment(source,node).encode()).hexdigest()
      if isinstance(node,ast.ClassDef):visit(node.body,prefix+node.name+'.')
   visit(tree.body)
   self.assertEqual(found,{key:pin for key,pin in expected.items() if key.startswith(name+':')})
 def test_closed_cli_refusal_never_exposes_unknown_exception_values(self):
  output=io.StringIO()
  with patch.object(M,'cold',side_effect=RuntimeError('PRIVATE_CONFIG_OR_DAEMON_TEXT')),patch.object(M.sys,'stdout',output):
   self.assertEqual(M.main(['--source-sha256','a'*64]),3)
  result=json.loads(output.getvalue());self.assertEqual(result['code'],'unknown_outcome');self.assertNotIn('PRIVATE',output.getvalue())
 def test_ambiguous_mutation_result_is_not_retried_or_started(self):
  runtime=self.runtime();calls=[]
  def request(*args,**kw):calls.append(args);raise OSError('PRIVATE_SENTINEL')
  runtime.engine=SimpleNamespace(request=request)
  with self.assertRaises(OSError):runtime.create('app_candidate','sha256:'+'c'*64,{'Labels':{}},{})
  self.assertEqual(len(calls),1);self.assertEqual(runtime.started,[]);self.assertEqual(runtime.container_ids,[])
 def test_expiry_is_noop_before_deadline_and_never_uses_production_names(self):
  value={'schema':'normal_restore_expiry/1','operation':M.OP,'nonce':'a'*32,'machine_id':'b'*32,'deadline':int(time.time())+60,
   'producer':{'sha256':'c'*64},'images':{}}
  path=self.file('expiry.json',M.json_bytes(value));pin=sha256(path.read_bytes()).hexdigest()
  with self.assertRaisesRegex(RuntimeError,'expiry_refused'):M.expire(path,pin,'c'*64,self.stack)
 def test_source_has_no_custom_env_or_source_overlay_or_go_address_space_limit(self):
  tree=ast.parse(Path(M.__file__).read_bytes());literals=[n.value for n in ast.walk(tree) if isinstance(n,ast.Constant) and isinstance(n.value,str)]
  self.assertFalse(any('/source' in x or 'fresh-profile' in x or 'RLIMIT_AS' in x for x in literals))
  node=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Runtime')
  create=next(n for n in node.body if isinstance(n,ast.FunctionDef) and n.name=='create_api')
  self.assertFalse(any(isinstance(n,ast.Subscript) and isinstance(n.slice,ast.Constant) and n.slice.value=='Env' for n in ast.walk(create)))

if __name__=='__main__':unittest.main()
