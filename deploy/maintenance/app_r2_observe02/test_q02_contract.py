"""Pure/owned Q02 source checks; no actual container, image, cache or HOST proof."""
from pathlib import Path
from types import ModuleType
from hashlib import sha256
from unittest.mock import patch
import ast,json,os,struct,types,unittest,tempfile,stat,marshal,tarfile,time,base64
from io import BytesIO
HERE=Path(__file__).absolute().parent
def load(path):
 m=ModuleType(path.stem);m.__file__=str(path);exec(compile(path.read_bytes(),str(path),'exec'),m.__dict__);return m
A=load(HERE/'archive_inventory.py');H=load(HERE/'cache_probe.py');I=load(HERE/'inventory.py');E=load(HERE/'export_controller.py');OLD=load(HERE/'run_app_image_ci_smoke.py');Q=load(HERE/'controller.py');S=load(HERE/'seal_and_run.py')
N=ModuleType('owned_structure_checker');exec(compile(H.STRUCTURE,'<trusted-owned-structure-checker>','exec'),N.__dict__)
# Byte-exact historical Q parser fixture makes the new-field regression portable.
BASE=HERE/'test_data/baseline_inventory.json'
with BASE.open('rb')as fixture_stream:fixture_raw=fixture_stream.read(40001)
assert 0<len(fixture_raw)<=40000 and fixture_raw.endswith(b'\n')and not fixture_raw.endswith(b'\n\n');fixture=json.loads(fixture_raw);assert(json.dumps(fixture,sort_keys=True,separators=(',',':'))+'\n').encode()==fixture_raw;assert set(fixture)=={'schema','bytes','sha256','base64'}and fixture['schema']=='authenticated_original_Q_ELF_parser_source_DATA/1'and type(fixture['bytes'])is int and(fixture['bytes'],fixture['sha256'])==(27258,'8b4b0091a1e8d20786e33c97a6bb4b63c28b263180527b313883815c67cfe5dc');assert type(fixture['base64'])is str and len(fixture['base64'])==((27258+2)//3)*4;raw=base64.b64decode(fixture['base64'],validate=True);assert(len(raw),sha256(raw).hexdigest())==(27258,'8b4b0091a1e8d20786e33c97a6bb4b63c28b263180527b313883815c67cfe5dc');B=ModuleType('authenticated_original_Q_parser');B.__file__=str(BASE);exec(compile(raw,'<authenticated-original-Q-parser-bytes>','exec'),B.__dict__)
def elf(*,defined_helper=False,irrelevant=False):
 names=['','libacl.so.1','acl_get_file','perm_copy_fd','attr_copy_fd','perm_copy_fd_not_the_helper'];strings=b'\0';offsets={'':0}
 for n in names[1:]:offsets[n]=len(strings);strings+=n.encode()+b'\0'
 shnum=4;str_off=128;dyn_off=(str_off+len(strings)+7)//8*8;dynamic=b''.join(struct.pack('<qQ',k,v)for k,v in[(1,offsets['libacl.so.1']),(5,0x5000),(10,len(strings)),(6,0x6000),(11,24),(0,0)]);sym_off=(dyn_off+len(dynamic)+7)//8*8;symbols=bytes(24)
 for n in('acl_get_file','perm_copy_fd','attr_copy_fd','perm_copy_fd_not_the_helper'):
  symbols+=struct.pack('<IBBHQQ',offsets[n],0x12,0,1 if defined_helper and n in HN else 0,0,0)
 sh_off=(sym_off+len(symbols)+7)//8*8;data=bytearray(sh_off+4*64);data[:16]=b'\x7fELF\x02\x01\x01'+bytes(9);struct.pack_into('<HHIQQQIHHHHHH',data,16,3,62,1,0,64,sh_off,0,64,56,1,64,4,0);struct.pack_into('<IIQQQQQQ',data,64,2,4,dyn_off,0x7000,0,len(dynamic),len(dynamic),8);data[str_off:str_off+len(strings)]=strings;data[dyn_off:dyn_off+len(dynamic)]=dynamic;data[sym_off:sym_off+len(symbols)]=symbols
 for index,row in enumerate([(0,0,0,0,0,0,0,0,0,0),(0,3,0,0x5000,str_off,len(strings),0,0,1,0),(0,6,0,0x7000,dyn_off,len(dynamic),1,0,8,16),(0,11,0,0x6000,sym_off,len(symbols),1,0,8,24)]):struct.pack_into('<IIQQQQIIQQ',data,sh_off+index*64,*row)
 return bytes(data)
HN={'perm_copy_fd','attr_copy_fd'}
def actor(*,after=False):
 oldowner=OLD.owner();cid='c'*64;name='OWNED_PROTOCOL_ONLY_jmespath';image='sha256:'+'a'*64;v={'id':cid,'name':'/'+name,'image':image,'config_image':image,'owner':oldowner,'user':'0:0','entrypoint':['/usr/local/bin/python3.11'],'cmd':['-I','-S','-B','-c',H.PROBE],'readonly':True,'privileged':False,'network':'none','caps_drop':['ALL'],'security':['no-new-privileges'],'mounts':[],'restart_policy':'no','auto_remove':False,'running':False,'paused':False,'restarting':False,'dead':False,'oom_killed':False,'status':'exited'if after else'created','memory':512*1024**2,'memory_swap':512*1024**2,'nano_cpus':10**9,'pids':64,'pid':0,'exit_code':0,'restart_count':0,'networks':{},'caps_add':None,'volumes':None,'tmpfs':None,'binds':None,'host_mounts':None,'started_at':'2026-10-28T00:00:00Z'if after else E.ZERO_TIME,'finished_at':'2026-10-28T00:00:01Z'if after else E.ZERO_TIME};return v,name,image,cid
class Q02(unittest.TestCase):
 def test_unfiltered_helper_imports_preserve_every_old_field(self):
  raw=elf();old=B.elf_metadata(raw);new=I.elf_metadata(raw);self.assertEqual(new.pop('undefined_acl_attr_helper_imports'),['attr_copy_fd','perm_copy_fd']);self.assertEqual(new,old);self.assertEqual(I.classify_elf(raw)['callsite_safety_status'],'UNASSESSED')
 def test_exact_consumer_metadata_digest_chain(self):
  actual=sha256((HERE/'inventory.py').read_bytes()).hexdigest();self.assertEqual(A.PURE_INVENTORY_SHA256,actual);self.assertEqual(E.PURE_INVENTORY_SHA256,actual);self.assertEqual(Q.INVENTORY_PIN[1],actual);self.assertEqual(S.SOURCES['inventory.py'][1],actual)
  for name,pin in(('archive_inventory.py',Q.ARCHIVE_PIN),('export_controller.py',Q.EXPORT_PIN)):
   self.assertEqual(S.SOURCES[name],pin);raw=(HERE/name).read_bytes();self.assertEqual(pin,(len(raw),sha256(raw).hexdigest()))
 def test_actual_owned_archive_collect_emits_current_pure_parser_digest(self):
  # Real owned TAR and real A.Source FD lifecycle; protocol IDs are not image authority.
  with tempfile.TemporaryDirectory(prefix='owned-metadata-pipeline-',dir=HERE)as td:
   path=Path(td)/'owned.tar';data={'var/lib/dpkg/status':b'Package: owned-protocol-elf\nStatus: install ok installed\nVersion: 1.0\nArchitecture: amd64\n\n','var/lib/dpkg/info/owned-protocol-elf.list':b'/usr/bin/owned-protocol-elf\n','usr/bin/owned-protocol-elf':elf()}
   with tarfile.open(path,'w',format=tarfile.USTAR_FORMAT)as archive:
    for name,raw in data.items():
     item=tarfile.TarInfo(name);item.size=len(raw);item.mode=0o644;item.uid=item.gid=0;archive.addfile(item,BytesIO(raw))
   path.chmod(0o600);raw=path.read_bytes();digest=sha256(raw).hexdigest();sbom=b'{"components":[]}';image='sha256:'+'1'*64;commit='2'*40
   with path.open('rb')as stream:
    original_fd=stream.fileno();receipt={'schema':'same_image_never_started_export/1','container_id':'3'*64,'image_id':image,'source_commit':commit,'before':{'OWNED_PROTOCOL_ONLY':True},'after':{'OWNED_PROTOCOL_ONLY':True},'archive':{'bytes':len(raw),'sha256':digest,'full9':list(A.identity(os.fstat(original_fd)))},'actual_export_client_returncode':0,'container_started':False,'private_mounts_used':False,'security_accepted':False};report=A.collect(I,None,stream,len(raw),image_id=image,source_commit=commit,sbom_raw=sbom,sbom_sha256=sha256(sbom).hexdigest(),verifier_sha256=Q.ARCHIVE_PIN[1],export_sha256=digest,container_receipt=receipt,check=lambda:None,end=time.monotonic()+10);self.assertFalse(stream.closed);os.fstat(original_fd)
   self.assertEqual(report['pure_inventory_source_sha256'],sha256((HERE/'inventory.py').read_bytes()).hexdigest());self.assertEqual(report['inventory']['elf_files'][0]['undefined_acl_attr_helper_imports'],['attr_copy_fd','perm_copy_fd']);self.assertFalse(report['security_accepted']);self.assertFalse(report['whole_image_acl_closed'])
 def test_defined_and_lookalike_names_never_become_helper_imports(self):
  self.assertEqual(I.elf_metadata(elf(defined_helper=True))['undefined_acl_attr_helper_imports'],[])
 def test_all_CodeType_fields_and_recursive_nested_values(self):
  raw='def f(x):\n try:\n  return x+1\n except ValueError:\n  return None\n';a=compile(raw,'/OWNED/source.py','exec');b=compile(raw,'/OWNED/source.py','exec');v=N.compare_code(a,b);self.assertTrue(v['all_public_CodeType_value_fields_recursively_equal']);self.assertEqual(set(v['field_inventory']),{n for n in dir(types.CodeType)if n.startswith('co_')and not callable(getattr(a,n))});self.assertGreater(v['reference_walk']['code_objects'],1);self.assertFalse(v['marshal_dumps_byte_identity_required'])
 def test_filename_linetable_exceptiontable_and_nested_const_drift_refuse_equality(self):
  a=compile('def f(x):\n try:\n  return x+1\n except ValueError:\n  return None\n','/OWNED/source.py','exec');nested=next(v for v in a.co_consts if isinstance(v,types.CodeType))
  cases=[a.replace(co_filename='/OWNED/other.py'),a.replace(co_firstlineno=2),a.replace(co_linetable=b''),a.replace(co_consts=tuple(v.replace(co_exceptiontable=b'')if v is nested else v for v in a.co_consts))]
  for b in cases:
   with self.subTest():self.assertFalse(N.compare_code(a,b)['all_public_CodeType_value_fields_recursively_equal'])
 def test_genuine_owned_source0755_and_cache0644_modes(self):
  with tempfile.TemporaryDirectory(prefix='owned-input-mode-',dir=HERE)as td:
   paths=[Path(td)/'source.py',Path(td)/'cache.pyc']
   for index,(path,mode)in enumerate(zip(paths,(0o755,0o644))):
    path.write_bytes(b'OWNED_PUBLIC_TEST_DATA');path.chmod(mode);fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
     before=tuple(getattr(os.fstat(fd),k)for k in('st_dev','st_ino','st_mode','st_uid','st_gid','st_nlink','st_size','st_mtime_ns','st_ctime_ns'));N.admit_input_mode(before,index);path.chmod(0o644 if index==0 else 0o755);changed=tuple(getattr(os.fstat(fd),k)for k in('st_dev','st_ino','st_mode','st_uid','st_gid','st_nlink','st_size','st_mtime_ns','st_ctime_ns'))
     with self.assertRaises(RuntimeError):N.admit_input_mode(changed,index)
    finally:os.close(fd)
 def test_exact_marshal_EOF_refuses_trailing_bytes_without_execution(self):
  # Exercise exact decoder on owned inert marshal data, not the real3.11cache.
  good=marshal.dumps(b'A'*3432);self.assertEqual(len(good),3437);decoded,observed=N.decode_cache_code(good);self.assertEqual(decoded,b'A'*3432);self.assertEqual(observed,{'payload_bytes':3437,'cursor_bytes':3437,'EOF':True});raw=marshal.dumps(None);padding=3437-len(raw);self.assertGreater(padding,0)
  with self.assertRaises(RuntimeError):N.decode_cache_code(raw+b'X'*padding)
  with self.assertRaises(RuntimeError):N.decode_cache_code(raw)
 def test_walk_and_derived_rows_are_bounded(self):
  a=compile('x=1\n','/OWNED/source.py','exec');deep=None
  for _ in range(40):deep=(deep,)
  with self.assertRaises(RuntimeError):N.snapshot(a.replace(co_consts=(deep,)))
  with self.assertRaises(RuntimeError):N.snapshot(compile('x=1\n'*4100,'/OWNED/source.py','exec'))
 def test_wire_JSON_has_real_newline_and_no_code_exec_import(self):
  self.assertEqual(N.encode({'x':1}),b'{"x":1}\n');tree=ast.parse(H.PROBE);calls={n.func.id for n in ast.walk(tree)if isinstance(n,ast.Call)and isinstance(n.func,ast.Name)};self.assertFalse(calls&{'exec','eval','__import__'});imports={n.name for node in ast.walk(tree)if isinstance(node,ast.Import)for n in node.names};self.assertNotIn('jmespath',imports);self.assertIn('compile',calls);self.assertIn('marshal.load(reader)',H.PROBE);self.assertNotIn('marshal.dumps(',H.PROBE)
 def test_exact_version_isolation_and_header_inputs_are_literal(self):
  self.assertIn("sys.version_info[:3]==(3,11,18)",H.PROBE);self.assertIn("timestamp==1793122203",H.PROBE);self.assertIn("held[0]['before'][7]//10**9",H.PROBE);self.assertIn("os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC",H.PROBE);self.assertEqual(H.SOURCE_PIN[0],1704);self.assertEqual(H.CACHE_PIN[0],3453);self.assertEqual(H.PROBE_SHA256,sha256(H.PROBE.encode()).hexdigest())
 def test_actor_exact_command_isolation_and_lifecycle_negative_matrix(self):
  for after in(False,True):
   v,name,image,cid=actor(after=after);H.admit(v,OLD,name,image,cid,after=after)
   for key,value in(('image','sha256:'+'b'*64),('cmd',['-c','unheld']),('network','host'),('mounts',[{}]),('running',True),('pid',1),('exit_code',1),('restart_count',1),('oom_killed',True)):
    changed={**v,key:value}
    with self.subTest(after=after,key=key),self.assertRaises(RuntimeError):H.admit(changed,OLD,name,image,cid,after=after)
 def test_real_generic_cleanup_includes_new_actor_and_rejects_identity_drift(self):
  class Wire:
   def __init__(self,drift):self.created=[('OWNED_PROTOCOL_ONLY_jmespath','sha256:'+'a'*64)];self.network=None;self.calls=[];self.drift=drift
   def docker(self,args,**kw):
    self.calls.append(args)
    if args[:2]==['container','ls']:return('c'*64+'\n').encode()
    if args[:2]==['container','inspect']:return json.dumps({'name':'/OWNED_PROTOCOL_ONLY_jmespath','image':'sha256:'+('b'if self.drift else'a')*64,'owner':OLD.owner()}).encode()
    if args[:2]==['container','rm']:return b''
    raise AssertionError('unexpected owned wire command')
  for drift in(False,True):
   wire=Wire(drift);result=OLD.cleanup(wire);removed=any(c[:2]==['container','rm']for c in wire.calls);self.assertEqual(removed,not drift);self.assertEqual(bool(result),drift)
 def test_register_precedes_create_all_CAS_and_held_source_checks_precede_start(self):
  text=(HERE/'cache_probe.py').read_text();fn=next(n for n in ast.parse(text).body if isinstance(n,ast.FunctionDef)and n.name=='run');body=ast.get_source_segment(text,fn);self.assertLess(body.index('ctx.created.append'),body.index('ctx.docker(create_args'));self.assertLess(body.index('before=inspect(False)'),body.index("['container','start','--attach'"));self.assertLess(body.index('jmespath-probe-prestart.json'),body.index("['container','start','--attach'"));self.assertLess(body.index('after=inspect(True)'),body.index('jmespath-compile-cache-structure.json'))
 def test_lifecycle_and_P04_bodies_are_unchanged(self):
  # The new fixed actor is covered by the authenticated generic cleanup body;
  # no acceptance function is introduced or called.
  self.assertFalse(load(HERE/'producer02_controller.py').ARMED);self.assertIn('old.cleanup(ctx)',(HERE/'controller.py').read_text());self.assertEqual(S.SOURCES['cache_probe.py'],Q.CACHE_PROBE_PIN);self.assertEqual(S.SOURCES['inventory.py'],Q.INVENTORY_PIN)
if __name__=='__main__':unittest.main(verbosity=2)
