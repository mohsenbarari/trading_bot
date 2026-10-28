"""Two fixed public image files, trusted compile/marshal structure only on CI.
No import/exec/eval of inspected source or bytecode; no application assertions.
"""
from hashlib import sha256
import json,re
SOURCE_PATH='/usr/local/bin/jp.py'
CACHE_PATH='/usr/local/bin/__pycache__/jp.cpython-311.pyc'
SOURCE_PIN=(1704,'75f9cbd2f38e045e583d2cd8c4d65ba1f21f9bc4474bd5a503d4460587621336')
CACHE_PIN=(3453,'e381ec10784c6811fcf931a10e84c24d3414b362c2aa383be8eca7e12f45d912')
STRUCTURE = r'''
import types,struct,json,hashlib,stat
VALUE_FIELDS=('co_argcount','co_cellvars','co_code','co_consts','co_exceptiontable','co_filename','co_firstlineno','co_flags','co_freevars','co_kwonlyargcount','co_linetable','co_lnotab','co_name','co_names','co_nlocals','co_posonlyargcount','co_qualname','co_stacksize','co_varnames')
def need(ok,code):
 if not ok:raise RuntimeError(code)
def encode(value):return(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
def admit_input_mode(before,index):
 need(type(index)is int and index in(0,1)and len(before)==9 and before[2]==(stat.S_IFREG|(0o755 if index==0 else 0o644)),'cache_input_metadata')

def decode_cache_code(payload):
 import io,marshal
 need(type(payload)is bytes and len(payload)==3437,'cache_header');reader=io.BytesIO(payload)
 try:
  value=marshal.load(reader);cursor=reader.tell();tail=reader.read(1);need(cursor==len(payload)and tail==b'','cache_marshal_EOF');return value,{'payload_bytes':len(payload),'cursor_bytes':cursor,'EOF':tail==b''}
 finally:reader.close()

def snapshot(code):
 state={'nodes':0,'rows':0,'code_objects':0,'scalar_bytes':0}
 def freeze(value,depth=0):
  state['nodes']+=1;need(state['nodes']<=4096 and depth<=32,'cache_structure_bound');kind=type(value)
  if kind is types.CodeType:
   state['code_objects']+=1;need(state['code_objects']<=128,'cache_structure_bound');actual=tuple(sorted(n for n in dir(types.CodeType)if n.startswith('co_')and not callable(getattr(value,n))));need(actual==VALUE_FIELDS,'cache_CodeType_field_inventory');fields={n:freeze(getattr(value,n),depth+1)for n in VALUE_FIELDS};derived={}
   for name,iterator,length in(('co_lines',value.co_lines(),3),('co_positions',value.co_positions(),4)):
    rows=[]
    for row in iterator:
     state['rows']+=1;need(state['rows']<=4096 and type(row)is tuple and len(row)==length and all(v is None or type(v)is int and v.bit_length()<=1024 for v in row),'cache_derived_row_bound');rows.append(row)
    derived[name]=rows
   return{'type':'CodeType','fields':fields,'derived':derived}
  if value is None:return['None']
  if value is Ellipsis:return['Ellipsis']
  if kind is bool:return['bool',value]
  if kind is int:need(value.bit_length()<=65536,'cache_scalar_bound');return['int',str(value)]
  if kind in(str,bytes):
   n=len(value.encode('utf-8','surrogatepass'))if kind is str else len(value);state['scalar_bytes']+=n;need(n<=65536 and state['scalar_bytes']<=262144,'cache_scalar_bound');return['str',value]if kind is str else['bytes',value.hex()]
  if kind is float:return['float_bits',struct.pack('>d',value).hex()]
  if kind is complex:return['complex_bits',struct.pack('>dd',value.real,value.imag).hex()]
  if kind in(tuple,frozenset):
   need(len(value)<=4096,'cache_const_bound');items=[freeze(item,depth+1)for item in value]
   if kind is frozenset:items.sort(key=encode)
   return['tuple'if kind is tuple else'frozenset',items]
  raise RuntimeError('cache_const_type')
 tree=freeze(code);return tree,state

def compare_code(reference,observed):
 need(type(reference)is types.CodeType and type(observed)is types.CodeType,'cache_code_type');a,am=snapshot(reference);b,bm=snapshot(observed);fields=[]
 for name in VALUE_FIELDS:
  av,bv=a['fields'][name],b['fields'][name];fields.append({'field':name,'equal':av==bv,'reference_structural_sha256':hashlib.sha256(encode(av)).hexdigest(),'cache_structural_sha256':hashlib.sha256(encode(bv)).hexdigest()})
 return{'all_public_CodeType_value_fields_recursively_equal':a==b,'field_inventory':list(VALUE_FIELDS),'root_field_comparisons':fields,'derived_line_and_position_tables_equal':a['derived']==b['derived'],'reference_tree_sha256':hashlib.sha256(encode(a)).hexdigest(),'cache_tree_sha256':hashlib.sha256(encode(b)).hexdigest(),'reference_walk':am,'cache_walk':bm,'marshal_dumps_byte_identity_required':False,'bounds':{'nodes_per_tree':4096,'derived_rows_per_tree':4096,'depth':32,'code_objects':128,'scalar_bytes_per_tree':262144}}
'''
PROBE = STRUCTURE + r'''
import os,sys,stat,marshal,importlib.util,time
SOURCE='/usr/local/bin/jp.py';CACHE='/usr/local/bin/__pycache__/jp.cpython-311.pyc'
PINS=((1704,'75f9cbd2f38e045e583d2cd8c4d65ba1f21f9bc4474bd5a503d4460587621336'),(3453,'e381ec10784c6811fcf931a10e84c24d3414b362c2aa383be8eca7e12f45d912'))
CLOSED=frozenset(('cache_version','cache_isolation','cache_input_metadata','cache_input_pin','cache_input_changed','cache_header','cache_CodeType_field_inventory','cache_structure_bound','cache_derived_row_bound','cache_scalar_bound','cache_const_bound','cache_const_type','cache_code_type','cache_probe_bound','cache_marshal_EOF'))
def frame(s):return(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def error_info(error):return{'class':type(error).__name__ if type(error).__name__ in('RuntimeError','OSError','FileNotFoundError','PermissionError','ValueError','TypeError','EOFError','MemoryError','RecursionError')else'OtherException','code':error.args[0]if isinstance(error,RuntimeError)and len(error.args)==1 and type(error.args[0])is str and error.args[0]in CLOSED else'closed_refusal','errno':error.errno if isinstance(error,OSError)and type(error.errno)is int else None}
held=[];primary=None;finalizers=[];result=None;started=time.monotonic()
def check(row):need(time.monotonic()-started<30 and frame(os.fstat(row['fd']))==row['before']==frame(os.lstat(row['path'])),'cache_input_changed')
try:
 need(sys.implementation.name=='cpython'and sys.version_info[:3]==(3,11,18)and importlib.util.MAGIC_NUMBER==bytes.fromhex('a70d0d0a'),'cache_version');need(sys.flags.isolated==1 and sys.flags.ignore_environment==1 and sys.flags.no_site==1 and sys.dont_write_bytecode is True,'cache_isolation');raws=[]
 for index,(path,pin) in enumerate(zip((SOURCE,CACHE),PINS)):
  before=frame(os.lstat(path));admit_input_mode(before,index);need(before[3:7]==(0,0,1,pin[0]),'cache_input_metadata');fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC);row={'fd':fd,'path':path,'before':before};held.append(row);check(row);raw=os.pread(fd,pin[0]+1,0);need((len(raw),hashlib.sha256(raw).hexdigest())==pin,'cache_input_pin');check(row);raws.append(raw)
 source,cache=raws;flags,timestamp,source_bytes=struct.unpack('<III',cache[4:16]);need(cache[:4]==importlib.util.MAGIC_NUMBER and flags==0 and timestamp==1793122203 and timestamp==((held[0]['before'][7]//10**9)&0xffffffff) and source_bytes==len(source)==held[0]['before'][6]==1704,'cache_header')
 for row in held:check(row)
 reference=compile(source,SOURCE,'exec',dont_inherit=True,optimize=0)
 observed,marshal_decode=decode_cache_code(cache[16:]);need(type(observed)is types.CodeType,'cache_code_type');comparison=compare_code(reference,observed)
 for row in held:check(row)
 result={'schema':'App_R2_single_jmespath_trusted_compile_cache_structure/1','interpreter':{'implementation':sys.implementation.name,'version':list(sys.version_info[:3]),'magic_hex':importlib.util.MAGIC_NUMBER.hex(),'isolated':True,'site_disabled':True,'bytecode_writes_disabled':True},'inputs':[{'path':r['path'],'full9':list(r['before']),'bytes':p[0],'sha256':p[1]}for r,p in zip(held,PINS)],'PYC_header':{'magic_hex':cache[:4].hex(),'flags':flags,'timestamp':timestamp,'source_bytes':source_bytes,'source_fstat_integer_mtime':held[0]['before'][7]//10**9},'marshal_payload_bytes':len(cache)-16,'marshal_decode':marshal_decode,'marshal_exact_cursor_and_EOF_verified':True,'marshal_payload_sha256':hashlib.sha256(cache[16:]).hexdigest(),'comparison':comparison,'inspected_source_imported_or_executed':False,'inspected_PYC_executed':False,'business_assertions_executed':0,'security_accepted':False}
except BaseException as error:primary=error
finally:
 for index,row in enumerate(held):
  try:check(row)
  except BaseException as error:finalizers.append({'stage':'probe_input_preclose','index':index,**error_info(error)})
  try:os.close(row['fd']);row['fd']=None
  except BaseException as error:finalizers.append({'stage':'probe_input_physical_close','index':index,**error_info(error)})
  for j,other in enumerate(held):
   try:
    need(frame(os.lstat(other['path']))==other['before'],'cache_input_changed')
    if other['fd']is not None:check(other)
   except BaseException as error:finalizers.append({'stage':'probe_input_postclose_cross','index':j,**error_info(error)})
if primary is not None or finalizers:
 print(encode({'schema':'App_R2_single_jmespath_compile_probe_failure/1','primary':None if primary is None else error_info(primary),'physical_finalizer_failures':finalizers,'all_input_physical_finalizers_completed':False,'inspected_source_or_cache_executed':False,'security_accepted':False}).decode(),end='');raise SystemExit(1)
result['all_input_physical_finalizers_completed']=True;raw=encode(result);need(len(raw)<=32768,'cache_probe_bound');print(raw.decode(),end='');raise SystemExit(0)
'''
PROBE_SHA256=sha256(PROBE.encode()).hexdigest()
CODES=frozenset(('cache_actor_identity','cache_actor_isolation','cache_actor_lifecycle','cache_probe_output','cache_probe_structure_mismatch','cache_probe_source_scope'))
def need(ok,code):
 if not ok:raise RuntimeError(code)
def create_args(old,name,image):
 return['create','--name',name,'--label',old.LABEL+'='+old.owner(),'--pull=never','--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges','--user=0:0','--memory=512m','--memory-swap=512m','--cpus=1','--pids-limit=64','--restart=no','--ulimit','core=0','--entrypoint','/usr/local/bin/python3.11',image,'-I','-S','-B','-c',PROBE]
def admit(value,old,name,image,cid,*,after):
 fixed={'id':cid,'name':'/'+name,'image':image,'config_image':image,'owner':old.owner(),'user':'0:0','entrypoint':['/usr/local/bin/python3.11'],'cmd':['-I','-S','-B','-c',PROBE],'readonly':True,'privileged':False,'network':'none','caps_drop':['ALL'],'security':['no-new-privileges'],'mounts':[],'restart_policy':'no','auto_remove':False,'running':False,'paused':False,'restarting':False,'dead':False,'oom_killed':False,'status':'exited'if after else'created'}
 numbers={'memory':512*1024**2,'memory_swap':512*1024**2,'nano_cpus':10**9,'pids':64,'pid':0,'exit_code':0,'restart_count':0};extra={'networks','caps_add','volumes','tmpfs','binds','host_mounts','started_at','finished_at'}
 need(type(value)is dict and set(value)==set(fixed)|set(numbers)|extra and all(type(value.get(k))is type(v)and value[k]==v for k,v in fixed.items()),'cache_actor_identity');need(all(type(value.get(k))is int and value[k]==v for k,v in numbers.items()),'cache_actor_isolation');need(type(value['networks'])is dict and set(value['networks'])<={'none'}and all(value[k]in(None,[])for k in('caps_add','binds','host_mounts'))and all(value[k]in(None,{})for k in('volumes','tmpfs')),'cache_actor_isolation')
 zero='0001-01-01T00:00:00Z'
 if after:need(all(type(value[k])is str and value[k]!=zero and re.fullmatch('[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+Z',value[k])for k in('started_at','finished_at')),'cache_actor_lifecycle')
 else:need(value['started_at']==value['finished_at']==zero,'cache_actor_lifecycle')
 return value

def admit_probe_output(value):
 keys={'schema','interpreter','inputs','PYC_header','marshal_payload_bytes','marshal_decode','marshal_exact_cursor_and_EOF_verified','marshal_payload_sha256','comparison','inspected_source_imported_or_executed','inspected_PYC_executed','business_assertions_executed','security_accepted','all_input_physical_finalizers_completed'}
 need(type(value)is dict and set(value)==keys and value['schema']=='App_R2_single_jmespath_trusted_compile_cache_structure/1','cache_probe_output')
 need(value['interpreter']=={'implementation':'cpython','version':[3,11,18],'magic_hex':'a70d0d0a','isolated':True,'site_disabled':True,'bytecode_writes_disabled':True}and all(type(v)is int for v in value['interpreter']['version'])and all(value['interpreter'][k]is True for k in('isolated','site_disabled','bytecode_writes_disabled')),'cache_probe_output')
 need(value['PYC_header']=={'magic_hex':'a70d0d0a','flags':0,'timestamp':1793122203,'source_bytes':1704,'source_fstat_integer_mtime':1793122203}and all(type(value['PYC_header'][k])is int for k in('flags','timestamp','source_bytes','source_fstat_integer_mtime')),'cache_probe_output')
 need(value['marshal_decode']=={'payload_bytes':3437,'cursor_bytes':3437,'EOF':True}and type(value['marshal_decode']['payload_bytes'])is int and type(value['marshal_decode']['cursor_bytes'])is int and value['marshal_decode']['EOF']is True,'cache_probe_output')
 need(type(value['marshal_payload_bytes'])is int and value['marshal_payload_bytes']==3437 and value['marshal_exact_cursor_and_EOF_verified']is True and type(value['marshal_payload_sha256'])is str and re.fullmatch('[a-f0-9]{64}',value['marshal_payload_sha256'])is not None,'cache_probe_output')
 need(type(value['inputs'])is list and len(value['inputs'])==2,'cache_probe_output')
 for row,path,pin,mode in zip(value['inputs'],(SOURCE_PATH,CACHE_PATH),(SOURCE_PIN,CACHE_PIN),(0o755,0o644)):
  need(type(row)is dict and set(row)=={'path','full9','bytes','sha256'}and row['path']==path and type(row['bytes'])is int and(row['bytes'],row['sha256'])==pin,'cache_probe_output');f=row['full9'];need(type(f)is list and len(f)==9 and all(type(v)is int for v in f)and f[0]>0 and f[1]>0 and f[2:7]==[0o100000|mode,0,0,1,pin[0]],'cache_probe_output')
 need(value['all_input_physical_finalizers_completed']is True and value['inspected_source_imported_or_executed']is False and value['inspected_PYC_executed']is False and type(value['business_assertions_executed'])is int and value['business_assertions_executed']==0 and value['security_accepted']is False,'cache_probe_output')
 comparison=value['comparison'];need(type(comparison)is dict and set(comparison)=={'all_public_CodeType_value_fields_recursively_equal','field_inventory','root_field_comparisons','derived_line_and_position_tables_equal','reference_tree_sha256','cache_tree_sha256','reference_walk','cache_walk','marshal_dumps_byte_identity_required','bounds'}and type(comparison['all_public_CodeType_value_fields_recursively_equal'])is bool and type(comparison['derived_line_and_position_tables_equal'])is bool and comparison['marshal_dumps_byte_identity_required']is False,'cache_probe_output')
 fields=('co_argcount','co_cellvars','co_code','co_consts','co_exceptiontable','co_filename','co_firstlineno','co_flags','co_freevars','co_kwonlyargcount','co_linetable','co_lnotab','co_name','co_names','co_nlocals','co_posonlyargcount','co_qualname','co_stacksize','co_varnames');need(comparison['field_inventory']==list(fields)and type(comparison['root_field_comparisons'])is list and len(comparison['root_field_comparisons'])==len(fields),'cache_probe_output')
 for row,name in zip(comparison['root_field_comparisons'],fields):
  need(type(row)is dict and set(row)=={'field','equal','reference_structural_sha256','cache_structural_sha256'}and row['field']==name and type(row['equal'])is bool and all(type(row[k])is str and re.fullmatch('[a-f0-9]{64}',row[k])is not None for k in('reference_structural_sha256','cache_structural_sha256'))and row['equal']==(row['reference_structural_sha256']==row['cache_structural_sha256']),'cache_probe_output')
 for key in('reference_tree_sha256','cache_tree_sha256'):need(type(comparison[key])is str and re.fullmatch('[a-f0-9]{64}',comparison[key])is not None,'cache_probe_output')
 for key in('reference_walk','cache_walk'):
  metrics=comparison[key];need(type(metrics)is dict and set(metrics)=={'nodes','rows','code_objects','scalar_bytes'}and all(type(v)is int and v>=0 for v in metrics.values())and 0<metrics['nodes']<=4096 and metrics['rows']<=4096 and 0<metrics['code_objects']<=128 and metrics['scalar_bytes']<=262144,'cache_probe_output')
 need(comparison['bounds']=={'nodes_per_tree':4096,'derived_rows_per_tree':4096,'depth':32,'code_objects':128,'scalar_bytes_per_tree':262144},'cache_probe_output')
 if comparison['all_public_CodeType_value_fields_recursively_equal']:need(all(row['equal']for row in comparison['root_field_comparisons'])and comparison['derived_line_and_position_tables_equal']and comparison['reference_tree_sha256']==comparison['cache_tree_sha256'],'cache_probe_output')
 return value

def run(ctx,old,export,image):
 need(image=='sha256:f685744c572f14025fe81ab8f2b1d5371a23d7e19fd9e572e3c6ab83d53effc7'and old.DOCKER==['/usr/bin/docker','--host=unix:///var/run/docker.sock'],'cache_probe_source_scope');name='app-ci-jmespath-cache-'+old.owner();ctx.created.append((name,image));ctx.check();cid=ctx.docker(create_args(old,name,image),maximum=256).decode('ascii').strip();need(re.fullmatch('[a-f0-9]{64}',cid)is not None,'cache_actor_identity');ctx.write(ctx.root/'reports/jmespath-probe-created.json',{'schema':'Q02_fixed_jmespath_probe_actor/1','container_id':cid,'name':name,'image_id':image,'trusted_probe_sha256':PROBE_SHA256,'private_mounts':False,'security_accepted':False})
 def inspect(after):
  value=export.document(ctx.docker(['container','inspect','--format',export.inspect_template(old),cid],maximum=32768));return admit(value,old,name,image,cid,after=after)
 before=inspect(False);ctx.check();ctx.write(ctx.root/'reports/jmespath-probe-prestart.json',{'schema':'Q02_probe_first_START_admission/1','before':before,'trusted_probe_sha256':PROBE_SHA256,'source_checks_completed_before_first_START':True,'image_id':image,'security_accepted':False});ctx.check();raw=ctx.docker(['container','start','--attach',cid],maximum=32768,seconds=60);ctx.check();after=inspect(True);ctx.check();value=admit_probe_output(export.document(raw));ctx.write(ctx.root/'reports/jmespath-compile-cache-structure.json',{'schema':'Q02_actual_single_jmespath_compile_cache_observation/1','image_id':image,'container_id':cid,'trusted_probe_sha256':PROBE_SHA256,'before':before,'after':after,'probe':value,'business_assertions_executed':0,'full_P_created':False,'security_accepted':False});ctx.check();need(value['comparison']['all_public_CodeType_value_fields_recursively_equal']is True,'cache_probe_structure_mismatch');return value
