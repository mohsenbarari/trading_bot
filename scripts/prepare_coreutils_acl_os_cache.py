"""Prepare one authentic Coreutils caller cache; install only Coreutils offline.

The tracked lock binds actual reviewed package, CI and artifact evidence.
No placeholder is accepted as an artifact identity.
"""
from contextlib import ExitStack
from hashlib import sha256
from pathlib import Path
from types import ModuleType
from zipfile import ZipFile,ZIP_STORED,ZIP_DEFLATED
import argparse,ctypes,errno,fcntl,io,json,math,os,platform,re,resource,selectors,shutil,signal,stat,subprocess,sys,tempfile,time

ROOT=Path(__file__).resolve().parent
LOCK_NAME='coreutils_acl_package.lock.json'
LOCK_SHA='987cb4cea875bcf103c5f85a53b5e990bf646fd55cf3f26b2f832c7d217765b6'
BASE_VERSION='9.7-3';VERSION='9.7-3+gold2'
PACKAGE='coreutils_'+VERSION+'_amd64.deb'
CAPSULE=Path('/coreutils-capsule')
DOC_INCLUDES=tuple(flag for root in('/usr/share/doc/coreutils','/usr/share/locale','/usr/share/man','/usr/share/info')for flag in('--path-include='+root,'--path-include='+root+'/*'))+('--path-include=/usr/share/lintian/overrides/coreutils',)
OUTPUT_LIMITS={'coreutils_9.7-3+gold2_amd64.deb': 67108864, 'coreutils_9.7-3+gold2_amd64.changes': 4194304, 'coreutils_9.7-3+gold2_amd64.buildinfo': 4194304, 'acl_2.4.0-1~trixie+gold1_amd64.changes': 4194304, 'acl_2.4.0-1~trixie+gold1_amd64.buildinfo': 4194304, 'source-proof.json': 4194304, 'sdk-proof.json': 4194304, 'abi-contract.json': 4194304, 'ordinary-compatibility.json': 4194304, 'provenance.json': 4194304, 'runtime-payload.json': 4194304, 'caller-proof.json': 4194304, 'vendor-suite.json': 4194304, 'generated-build-proof.json': 4194304}
PARENT_PATCH={'bytes':7024,'sha256':'b2d25168d84d8bd85fbf7af852e09634b446ff640570bb8009d6a7c09886a005'}
PARENT_CP={'bytes':46515,'sha256':'480b19ee4e3c73e1d3e65420520e064843167095b4abe2742a5e46dd0ce11c82'}
CLI_CASES=('regular_acl_mode_xattr','cp_P_command_line_symlink','cp_H_command_line_dereference','recursive_P_nested_symlink','recursive_H_nested_symlink','recursive_L_nested_symlink','cp_s_intended_symlink_destination','directory_default_acl_general_xattr','directory_no_preserve_mode','fifo_no_payload_open','mv_same_filesystem_rename','mv_cross_device_default_acl','install_regular_mode','install_directory_mode','parents_new_acl_mode','parents_existing_metadata','parents_existing_fast_path','parents_source_alias_P','parents_source_alias_H','parents_source_alias_L','parents_destination_alias','parents_search_without_read','parents_no_preserve_mode')
MAX_ZIP=64*1024**2;MAX_OUTPUT=2*1024**2
INVENTORY=('/usr/bin/dpkg-query','-W','-f=${binary:Package}\t${Version}\t${db:Status-Status}\n')
CODES=frozenset(('lock_unarmed','lock_contract','path','parent','parent_changed','file_metadata','file_bound','file_pin',
 'file_changed','file_closed_changed','directory','cache_inventory','cache_changed','cache_busy','cache_lock','publication',
 'archive_contract','archive_bound','archive_pin','archive_member','source_contract','image_boundary','loader_override',
 'installer_command','installer_output','installer_deadline','installer_failed','installer_descendants','installer_cleanup',
 'package_control','package_baseline','package_inventory','package_payload','installed_library','write','unexpected_refusal',
 'artifact_token','artifact_timeout','artifact_failed','artifact_bound','artifact_write'))

def need(ok,code):
 if not ok:raise RuntimeError(code)
def exact_int(value,expected):return type(value)is int and value==expected
def identity(s):return (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def encoded(value):return (json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
def document(raw):
 def unique(rows):
  out={}
  for key,value in rows:need(key not in out,'lock_contract');out[key]=value
  return out
 def finite(raw):
  value=float(raw);need(math.isfinite(value),'lock_contract');return value
 return json.loads(raw,object_pairs_hook=unique,parse_float=finite,parse_constant=lambda _:need(False,'lock_contract'))
def binding(value,maximum):
 need(type(value) is dict and set(value)=={'bytes','sha256'} and type(value['bytes']) is int
  and 0<value['bytes']<=maximum and type(value['sha256']) is str and re.fullmatch('[a-f0-9]{64}',value['sha256']),'lock_contract')
 return value
def validate_lock(value):
 need(type(value)is dict and value.get('schema')=='coreutils_acl_image_package_lock/1','lock_contract')
 need(value.get('armed')is True,'lock_unarmed')
 need(set(value)=={'schema','armed','package','outputs','artifact','source','runtime_payload','installed_callers','approved_libacl','security_accepted','whole_image_other_callers_assessed'},'lock_contract')
 need(value['package']=={'name':'coreutils','baseline_version':BASE_VERSION,'version':VERSION,'architecture':'amd64','multi_arch':'foreign','essential':'yes','path':PACKAGE}
  and value['security_accepted']is False and value['whole_image_other_callers_assessed']is False,'lock_contract')
 outputs=value['outputs'];need(type(outputs)is dict and set(outputs)==set(OUTPUT_LIMITS),'lock_contract')
 for name,row in outputs.items():binding(row,OUTPUT_LIMITS[name])
 artifact=value['artifact'];need(type(artifact)is dict and set(artifact)=={'id','run_id','bytes','sha256','members'}
  and all(type(artifact[k])is int and artifact[k]>0 for k in('id','run_id','members'))and artifact['members']==len(outputs),'lock_contract')
 binding({k:artifact[k]for k in('bytes','sha256')},MAX_ZIP)
 source=value['source'];need(type(source)is dict and set(source)=={'commit','recipe_sha256','production_patch_sha256','parent_patch_sha256','parent_cp','controller_sha256','root_artifact_admission_sha256','root_static_verification_sha256','ci_result_sha256','ci_source_identity_sha256'},'lock_contract')
 need(type(source['commit'])is str and re.fullmatch('[a-f0-9]{40}',source['commit'])and all(type(source[k])is str and re.fullmatch('[a-f0-9]{64}',source[k])for k in set(source)-{'commit','parent_cp'})
  and source['production_patch_sha256']=='3c91b4386c7608b4620422daaf88ec561d3bc709140ae29dc904ed54a9833d1d'
  and source['parent_patch_sha256']==PARENT_PATCH['sha256']and source['parent_cp']==PARENT_CP,'lock_contract')
 library=value['approved_libacl'];need(library=={'version':'2.4.0-1~trixie+gold1','path':'/usr/lib/x86_64-linux-gnu/libacl.so.1.2.2400','bytes':42928,'sha256':'32d112de5fb4ec3124c7a4b859b738b45e4c4a9db3b3115f167625f082909dd1'},'lock_contract')
 payload=value['runtime_payload'];need(type(payload)is dict and set(payload)=={'schema','coreutils','approved_libacl','security_accepted'}and payload['schema']=='coreutils_acl_runtime_payload/1'and payload['security_accepted']is False,'lock_contract')
 need({'bytes':len(encoded(payload)),'sha256':sha256(encoded(payload)).hexdigest()}==outputs['runtime-payload.json'],'lock_contract')
 names=set()
 for key,package,version,deb in [('coreutils','coreutils',VERSION,outputs[PACKAGE]['sha256']),('approved_libacl','libacl1',library['version'],'bbb12e190bfe19b876f73a669848ebd8d04293d95000babf51058c88879966b1')]:
  group=payload[key];need(type(group)is dict and set(group)=={'package','installed_version','deb_sha256','regular_files','symlinks','regular_and_link_inventory_complete'}
   and group['package']==package and group['installed_version']==version and group['deb_sha256']==deb and group['regular_and_link_inventory_complete']is True,'lock_contract')
  files=group['regular_files'];links=group['symlinks'];need(type(files)is list and 0<len(files)<=4096 and type(links)is list and len(links)<=256,'lock_contract')
  total=0
  for row in files:
   need(type(row)is dict and set(row)=={'path','canonical_path','bytes','sha256','mode','uid','gid'}and type(row['path'])is str and row['path'].startswith('/usr/')and row['canonical_path']==row['path']
    and str(Path(row['path']))==row['path']and '..'not in Path(row['path']).parts and not any(c in row['path']for c in('\0','\n','\\'))and row['path']not in names
    and type(row['mode'])is int and row['mode']in(0o100644,0o100755)and type(row['uid'])is type(row['gid'])is int and row['uid']==row['gid']==0,'lock_contract')
   binding({k:row[k]for k in('bytes','sha256')},16*1024**2);total+=row['bytes'];need(total<=128*1024**2,'lock_contract');names.add(row['path'])
  for row in links:
   need(type(row)is dict and set(row)=={'path','target'}and type(row['path'])is str and row['path'].startswith('/usr/')and str(Path(row['path']))==row['path']and '..'not in Path(row['path']).parts
    and row['path']not in names and type(row['target'])is str and 0<len(row['target'])<=4096 and not any(c in row['path']+row['target']for c in('\0','\n','\\')),'lock_contract');names.add(row['path'])
 callers=value['installed_callers'];need(type(callers)is dict and set(callers)=={'cp','mv','install'},'lock_contract')
 for name,row in callers.items():
  need(type(row)is dict and set(row)=={'path','bytes','sha256'}and row['path']=='/usr/bin/'+name,'lock_contract');binding({k:row[k]for k in('bytes','sha256')},16*1024**2)
  need(next(({k:r[k]for k in('path','bytes','sha256')}for r in payload['coreutils']['regular_files']if r['path']==row['path']),None)==row,'lock_contract')
 need(next(({k:r[k]for k in('path','bytes','sha256')}for r in payload['approved_libacl']['regular_files']if r['path']==library['path']),None)=={k:library[k]for k in('path','bytes','sha256')},'lock_contract')
 return value
def parents(path,cache_owner=None):
 rows=[]
 for parent in path.parents:
  s=parent.lstat();sticky=parent==Path('/tmp') and s.st_uid==0 and stat.S_IMODE(s.st_mode)==0o1777
  allowed={0,os.geteuid()};allowed.update(() if cache_owner is None else (cache_owner,))
  need(parent.resolve(strict=True)==parent and stat.S_ISDIR(s.st_mode) and s.st_uid in allowed
   and (not s.st_mode&0o022 or sticky),'parent');rows.append((parent,identity(s)[:5]))
 return rows
def check_parents(rows):need(all(p.resolve(strict=True)==p and identity(p.lstat())[:5]==old for p,old in rows),'parent_changed')
class PublicFile:
 def __init__(self,path,maximum,pin=None,owner=None,cache_owner=None):
  self.path=Path(path).absolute();self.parents=parents(self.path,cache_owner);self.before=identity(self.path.lstat());self.fd=None
  need(self.path.resolve(strict=True)==self.path and stat.S_ISREG(self.before[2]) and self.before[5]==1
   and not self.before[2]&0o022 and 0<self.before[6]<=maximum
   and (owner is None or self.before[3:5]==owner),'file_metadata')
  self.fd=os.open(self.path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
  try:
   self.check();h=sha256();parts=[];count=0
   while True:
    block=os.read(self.fd,min(65536,self.before[6]-count+1))
    if not block:break
    count+=len(block);need(count<=self.before[6],'file_bound');h.update(block);parts.append(block)
   need(count==self.before[6],'file_bound');self.raw=b''.join(parts);self.binding={'bytes':count,'sha256':h.hexdigest()}
   need(pin is None or self.binding==pin,'file_pin');self.check()
  except BaseException:os.close(self.fd);self.fd=None;raise
 def named(self):check_parents(self.parents);need(identity(self.path.lstat())==self.before,'file_closed_changed')
 def check(self):self.named();need(identity(os.fstat(self.fd))==self.before,'file_changed')
 def close(self):
  try:self.check()
  finally:os.close(self.fd);self.fd=None
  self.named()
class Held:
 def __init__(self):self.files=[];self.final_checks=[]
 def __enter__(self):return self
 def read(self,*args,**kwargs):
  row=PublicFile(*args,**kwargs);self.files.append(row);return row
 def check(self):
  for row in self.files:row.check()
  for check in self.final_checks:check()
 def __exit__(self,*_):
  failed=None
  for row in reversed(self.files):
   try:row.close()
   except BaseException as error:failed=error
  for row in self.files:
   try:row.named()
   except BaseException as error:failed=error
  for check in self.final_checks:
   try:check()
   except BaseException as error:failed=error
  if failed is not None:raise failed
class PublicLink:
 def __init__(self,path,target):
  self.path=Path(path);self.parents=parents(self.path);self.before=identity(self.path.lstat());self.fd=None
  need(stat.S_ISLNK(self.before[2])and self.before[3:6]==(0,0,1),'package_payload')
  self.fd=os.open(self.path,os.O_PATH|os.O_NOFOLLOW|os.O_CLOEXEC)
  try:
   self.check();need(os.readlink('',dir_fd=self.fd)==target,'package_payload');self.check()
  except BaseException:os.close(self.fd);self.fd=None;raise
 def named(self):check_parents(self.parents);need(identity(self.path.lstat())==self.before,'file_closed_changed')
 def check(self):self.named();need(identity(os.fstat(self.fd))==self.before,'file_changed')
 def close(self):
  try:self.check()
  finally:os.close(self.fd);self.fd=None
  self.named()
def load_lock(held):
 row=held.read(ROOT/LOCK_NAME,1024**2,owner=(os.geteuid(),os.getegid()))
 need(row.binding['sha256']==LOCK_SHA,'lock_contract');return validate_lock(document(row.raw))
def inventory(path,expected):
 rows=[]
 with os.scandir(path) as stream:
  for row in stream:rows.append(row.name);need(len(rows)<=len(expected),'cache_inventory')
 need(set(rows)==set(expected) and len(rows)==len(expected),'cache_inventory')
def cache_manifest(lock):return encoded({'schema':'coreutils_acl_image_package_cache/1','package':lock['package'],'binding':lock['outputs'][PACKAGE],
 'source':{key:lock['source'][key]for key in('recipe_sha256','production_patch_sha256','parent_patch_sha256','parent_cp')},'runtime_payload':lock['outputs']['runtime-payload.json']})
def verify_cache(cache,lock,held,owner=None):
 cache=Path(cache).absolute();ancestors=parents(cache);before=identity(cache.lstat())
 need(cache.resolve(strict=True)==cache and stat.S_ISDIR(before[2]) and stat.S_IMODE(before[2])==0o755
  and (owner is None or before[3:5]==owner),'directory');inventory(cache,{PACKAGE,'manifest.json'})
 cache_owner=before[3];pair=before[3:5]
 meta=held.read(cache/'manifest.json',65536,owner=pair,cache_owner=cache_owner)
 need(meta.raw==cache_manifest(lock)and stat.S_IMODE(meta.before[2])==0o644,'cache_inventory')
 package=held.read(cache/PACKAGE,OUTPUT_LIMITS[PACKAGE],lock['outputs'][PACKAGE],owner=pair,cache_owner=cache_owner)
 need(stat.S_IMODE(package.before[2])==0o644,'cache_inventory')
 def check():check_parents(ancestors);need(identity(cache.lstat())==before,'cache_changed');inventory(cache,{PACKAGE,'manifest.json'})
 check();held.final_checks.append(check);return package
def write_new(path,raw,mode=0o644,*,held):
 need(type(raw) is bytes and len(raw)<=MAX_ZIP,'write')
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,mode)
 try:
  view=memoryview(raw)
  while view:n=os.write(fd,view);need(n>0,'write');view=view[n:]
  os.fchmod(fd,mode);os.fsync(fd)
  created=identity(os.fstat(fd));need(identity(Path(path).lstat())==created,'file_changed')
  row=held.read(path,MAX_ZIP,{'bytes':len(raw),'sha256':sha256(raw).hexdigest()},owner=(os.geteuid(),os.getegid()))
  need(row.before==created,'file_changed')
 finally:os.close(fd)
 held.check();return row
def rebind_published(rows,stage,cache,stage_id):
 need(not os.path.lexists(stage)and identity(cache.lstat())[:2]==stage_id and{row.path.name for row in rows}=={PACKAGE,'manifest.json'},'publication')
 for row in rows:
  need(row.path.parent==stage and identity(os.fstat(row.fd))==row.before,'publication')
  destination=cache/row.path.name;need(identity(destination.lstat())==row.before,'publication')
  row.path=destination;row.parents=parents(destination);row.check()
class CacheLock:
 def __init__(self,cache):
  self.path=cache.parent/('.'+cache.name+'.lock');self.parents=parents(self.path)
  self.fd=os.open(self.path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,0o600);self.before=identity(os.fstat(self.fd))
  try:
   need(stat.S_ISREG(self.before[2]) and self.before[3:5]==(os.geteuid(),os.getegid()) and self.before[5:7]==(1,0) and stat.S_IMODE(self.before[2])==0o600,'cache_lock');self.check()
   try:fcntl.flock(self.fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
   except OSError as error:
    if error.errno in {errno.EAGAIN,errno.EWOULDBLOCK}:raise RuntimeError('cache_busy') from None
    raise
  except BaseException:os.close(self.fd);raise
 def check(self):check_parents(self.parents);need(identity(os.fstat(self.fd))==identity(self.path.lstat())==self.before,'cache_changed')
 def __enter__(self):return self
 def __exit__(self,*_):
  try:self.check()
  finally:os.close(self.fd)
  check_parents(self.parents);need(identity(self.path.lstat())==self.before,'cache_changed')
def rename_noreplace(source,target):
 libc=ctypes.CDLL(None,use_errno=True);fn=libc.renameat2
 fn.argtypes=(ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint);fn.restype=ctypes.c_int
 if fn(-100,os.fsencode(source),-100,os.fsencode(target),1):raise OSError(ctypes.get_errno(),'cache publication refused')
def admit_archive(raw,lock):
 need(type(raw)is bytes and len(raw)==lock['artifact']['bytes']and sha256(raw).hexdigest()==lock['artifact']['sha256'],'archive_pin')
 package=None;proofs={}
 with ZipFile(io.BytesIO(raw))as archive:
  rows=archive.infolist();need(len(rows)==len(lock['outputs'])and len({r.filename for r in rows})==len(rows)and{r.filename for r in rows}==set(lock['outputs']),'archive_contract')
  for row in rows:
   expected=lock['outputs'][row.filename];kind=stat.S_IFMT(row.external_attr>>16)
   need(not row.is_dir()and not row.flag_bits&1 and row.compress_type in(ZIP_STORED,ZIP_DEFLATED)and kind in(0,stat.S_IFREG)and row.file_size==expected['bytes']and 0<row.compress_size<=MAX_ZIP,'archive_member')
   parts=[];count=0;digest=sha256()
   with archive.open(row)as source:
    while True:
     block=source.read(min(65536,expected['bytes']-count+1))
     if not block:break
     count+=len(block);need(count<=expected['bytes'],'archive_bound');digest.update(block)
     if row.filename==PACKAGE or row.filename.endswith('.json'):parts.append(block)
   need(count==expected['bytes']and digest.hexdigest()==expected['sha256'],'archive_pin')
   if row.filename==PACKAGE:package=b''.join(parts)
   elif row.filename.endswith('.json'):proofs[row.filename]=document(b''.join(parts))
 need(package is not None and proofs['runtime-payload.json']==lock['runtime_payload'],'archive_contract')
 caller=proofs['caller-proof.json'];ordinary=proofs['ordinary-compatibility.json'];abi=proofs['abi-contract.json'];suite=proofs['vendor-suite.json'];provenance=proofs['provenance.json'];source=proofs['source-proof.json'];sdk=proofs['sdk-proof.json'];generated=proofs['generated-build-proof.json']
 need(caller.get('schema')=='coreutils_descriptor_caller_build/1'and caller.get('source_patch_sha256')==lock['source']['production_patch_sha256']and caller.get('ordinary_coreutils_compatibility_passed')is True
  and all(caller.get(k)is False for k in('whole_image_other_callers_assessed','all_cves_attested','security_accepted')),'archive_contract')
 native=ordinary.get('required_owned_c_fixtures');cli=ordinary.get('actual_cli')
 need(ordinary.get('schema')=='coreutils_acl_ordinary_compatibility/1'and exact_int(ordinary.get('all_required_owned_cases_skip_count'),0)and ordinary.get('security_accepted')is False
  and type(native)is dict and set(native)=={'private_fd','gnu_fd0','parent_frontend'}and caller.get('required_owned_fixtures')==native and type(cli)is dict
  and cli.get('schema')=='coreutils_owned_cli_compatibility/1'and cli.get('status')=='PASS'and cli.get('actual_uid')==cli.get('actual_gid')==10001 and exact_int(cli.get('skip_count'),0)and cli.get('security_accepted')is False,'archive_contract')
 need(all(exact_int(native['private_fd'].get(k),v)for k,v in{'actual_uid':10001,'actual_gid':10001,'actual_returncode':0,'skip_count':0}.items())and native['private_fd'].get('NFSv4_real_filesystem_exercised')is False
  and type(native['private_fd'].get('actual_assertions'))is int and native['private_fd']['actual_assertions']>=20
  and native['gnu_fd0'].get('schema')=='ordinary_gnulib_regular_fd0/1'and native['gnu_fd0'].get('status')=='PASS'
  and native['gnu_fd0'].get('actual_uid')==native['gnu_fd0'].get('actual_gid')==10001 and native['gnu_fd0'].get('actual_gnulib_qcopy_acl_called')is True and native['gnu_fd0'].get('source_fd_zero')is True,'archive_contract')
 parent=native['parent_frontend']
 need(parent.get('schema')=='ordinary_cp_parent_frontend/1'and parent.get('status')=='PASS'
  and all(exact_int(parent.get(k),v)for k,v in{'actual_uid':10001,'actual_gid':10001,'actual_case_count':4,'actual_EMFILE_partial_prefixes':2,'actual_assertions':223,'skip_count':0}.items())
  and all(parent.get(k)is True for k in('actual_frontend_and_copy_called','actual_OPATH_pairs','source_fd_zero','physical_pair_cleanup_verified','owned_cleanup_complete'))and parent.get('security_accepted')is False,'archive_contract')
 for fixture in native.values():
  binding(fixture.get('actual_fixture_binary'),16*1024**2)
 need(all(exact_int(cli.get(k),v)for k,v in{'actual_case_count':23,'legacy_case_count':14,'parent_case_count':9}.items())
  and cli.get('cases')==list(CLI_CASES)and all(cli.get(k)is True for k in('all_commands_actual_zero_and_pipes_closed','owned_cleanup_complete','parent_ownership_and_timestamp_path_semantics_retained'))
  and cli.get('production_data_accessed')is False and cli.get('exploit_or_race_reproduction_performed')is False
  and caller.get('parent_source_patch_sha256')==lock['source']['parent_patch_sha256'],'archive_contract')
 need(abi.get('schema')=='coreutils_dynamic_approved_acl_abi/1'and abi.get('security_accepted')is False and type(abi.get('callers'))is dict and set(abi['callers'])=={'cp','mv','install'}and caller.get('compiled_caller_contract')==abi['callers'],'archive_contract')
 library={k:lock['approved_libacl'][k]for k in('path','bytes','sha256')}
 for name,row in abi['callers'].items():
  need(row.get('path')=='/usr/bin/'+name and row.get('candidate_binary')=={k:lock['installed_callers'][name][k]for k in('bytes','sha256')}
   and row.get('libacl')==library and row.get('dynamic_approved_libacl_not_static_sdk')is True and row.get('actual_ldd_acl_target')==library['path']and row.get('security_accepted')is False
   and row.get('required_private_at_imports')==['acl_delete_def_file_at','acl_set_file_at']and set(row.get('actual_versioned_acl_bindings',{}))==set(row['required_private_at_imports'])
   and type(cli.get('actual_installed_candidate_binaries'))is dict and set(cli['actual_installed_candidate_binaries'])=={'cp','mv','install'}
   and type(cli['actual_installed_candidate_binaries'][name])is dict
   and cli['actual_installed_candidate_binaries'][name]=={'path':row['path'],'sha256':row['candidate_binary']['sha256']},'archive_contract')
 need(suite.get('schema')=='coreutils_unfiltered_vendor_command/1'and suite.get('actual_command')==['/usr/bin/dh_auto_test']and exact_int(suite.get('actual_returncode'),0)and suite.get('actual_uid')==suite.get('actual_gid')==10001
  and suite.get('actual_pipe_closed')is True and suite.get('configured_default_selections_captured_before_test')is True and suite.get('test_filter_override_used')is False and suite.get('test_bodies_modified')is False and suite.get('security_accepted')is False,'archive_contract')
 observation=suite.get('observation');need(type(observation)is dict and set(observation.get('scopes',{}))=={'coreutils','gnulib'}and exact_int(observation.get('failed_unexpected_or_error_results'),0)and observation.get('security_accepted')is False,'archive_contract')
 for scope in observation['scopes'].values():
  counts=scope.get('actual_summary');need(type(counts)is dict and all(type(counts.get(k))is int and counts[k]>=0 for k in('TOTAL','PASS','SKIP','XFAIL','FAIL','XPASS','ERROR'))
   and counts['FAIL']==counts['XPASS']==counts['ERROR']==0 and counts['TOTAL']==counts['PASS']+counts['SKIP']+counts['XFAIL']and exact_int(scope.get('actual_configured_test_count'),counts['TOTAL']),'archive_contract')
 need(source.get('schema')=='coreutils_acl_source_build/1'and source.get('maintainer_dsc_signatures_authenticated')is True and source.get('patch',{}).get('sha256')==lock['source']['production_patch_sha256']and source.get('security_accepted')is False
  and sdk.get('schema')=='coreutils_acl_compiler_sdk/1'and sdk.get('compiler_only')is True and sdk.get('runtime_replacement_exported')is False and sdk.get('actual_approved_runtime_after_reinstall')==library and sdk.get('security_accepted')is False
  and generated.get('schema')=='coreutils_actual_generated_build_provenance/1'and generated.get('configured_descriptor_acl_feature')is True and generated.get('debian_dh_autoreconf_noop_removed')is True and generated.get('security_accepted')is False,'archive_contract')
 contract=source.get('actual_source_contract');manifest=source.get('source_manifest')
 need(type(contract)is dict and contract.get('parent_acl_patch')==PARENT_PATCH
  and contract.get('parent_cp_actual')==lock['source']['parent_cp']and type(manifest)is dict
  and manifest.get('package_version')==VERSION and manifest.get('parent_acl_patch')==PARENT_PATCH
  and manifest.get('files',{}).get('src/cp.c',{}).get('after')==lock['source']['parent_cp'],'archive_contract')
 need(provenance.get('schema')=='coreutils_acl_package_provenance/1'and provenance.get('normal_package_version')==VERSION and exact_int(provenance.get('full_unfiltered_gnu_test_hook_actual_returncode'),0)
  and provenance.get('vendor_test_selection')=='configured_default_unchanged'and provenance.get('vendor_zero_skip_coverage_claimed')is False and provenance.get('runtime_loader_override_used')is False
  and provenance.get('vendor_build_only_loader_mechanics_not_disabled')is True and provenance.get('uid10001_children_gone')is True and provenance.get('only_new_coreutils_runtime_exported')is True and provenance.get('production_installed')is False and provenance.get('security_accepted')is False,'archive_contract')
 return package
def download(lock,target,held):
 token=os.environ.get('GH_TOKEN');need(type(token)is str and bool(token),'artifact_token');spec=lock['artifact']
 fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600);child=None
 try:
  child=subprocess.Popen(['/usr/bin/gh','api','--method','GET','repos/mohsenbarari/trading_bot/actions/artifacts/'+str(spec['id'])+'/zip'],
   stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,close_fds=True,start_new_session=True,
   env={'PATH':'/usr/bin:/bin','GH_TOKEN':token});end=time.monotonic()+300;count=0;digest=sha256()
  with selectors.DefaultSelector()as selector:
   os.set_blocking(child.stdout.fileno(),False);selector.register(child.stdout.fileno(),selectors.EVENT_READ)
   while True:
    held.check();left=end-time.monotonic();need(left>0,'artifact_timeout')
    if not selector.select(min(1,left)):continue
    block=os.read(child.stdout.fileno(),min(65536,spec['bytes']-count+1))
    if not block:break
    count+=len(block);need(count<=spec['bytes'],'artifact_bound');digest.update(block);view=memoryview(block)
    while view:n=os.write(fd,view);need(n>0,'artifact_write');view=view[n:]
   need(child.wait(timeout=max(.1,end-time.monotonic()))==0,'artifact_failed')
   need(count==spec['bytes']and digest.hexdigest()==spec['sha256'],'archive_pin');os.fsync(fd)
   created=identity(os.fstat(fd));need(identity(Path(target).lstat())==created,'file_changed')
   row=held.read(target,MAX_ZIP,{k:spec[k]for k in('bytes','sha256')},owner=(os.geteuid(),os.getegid()))
   need(row.before==created,'file_changed');held.check()
  # Original frame/known bytes were retained BEFORE selector closure.
  held.check()
 finally:
  try:
   if child is not None:cleanup_child(child)
  finally:
   if child is not None:child.stdout.close()
   os.close(fd)
 held.check()
def prepare(cache,artifact_zip=None,check_only=False):
 cache=Path(cache).absolute();need(cache.name=='coreutils-acl-capsule' and cache.parent.resolve(strict=True)==cache.parent,'path');parents(cache)
 with tempfile.TemporaryDirectory(prefix='.coreutils-acl-download-',dir=cache.parent) as temporary:
  with Held() as held:
   lock=load_lock(held)
   # Close/unlock this owned lock BEFORE finalizing the retained data/source
   # FDs. A physical lock close can never hide a late data mutation.
   with CacheLock(cache) as busy:
    if cache.exists():verify_cache(cache,lock,held,(os.geteuid(),os.getegid()));held.check();status='cache_verified'
    else:
     need(not check_only,'cache_inventory');stage=Path(tempfile.mkdtemp(prefix='.coreutils-acl-capsule-',dir=cache.parent));stage_id=identity(stage.lstat())[:2];stage.chmod(0o755);published=False
     try:
      target=Path(artifact_zip).absolute() if artifact_zip else Path(temporary)/'artifact.zip'
      if artifact_zip is None:download(lock,target,held)
      archive=held.read(target,MAX_ZIP,{k:lock['artifact'][k] for k in ('bytes','sha256')},owner=(os.geteuid(),os.getegid()))
      package=admit_archive(archive.raw,lock)
      created=[write_new(stage/PACKAGE,package,held=held),write_new(stage/'manifest.json',cache_manifest(lock),held=held)]
      # Verify the new namespace completely before its no-clobber publication;
      # open fresh held output FDs immediately after that exact owned rename.
      with Held() as staged:verify_cache(stage,lock,staged,(os.geteuid(),os.getegid()));staged.check()
      held.check();busy.check();need(identity(stage.lstat())[:2]==stage_id,'cache_changed')
      rename_noreplace(stage,cache);published=True;need(identity(cache.lstat())[:2]==stage_id,'publication')
      rebind_published(created,stage,cache,stage_id)
      verify_cache(cache,lock,held,(os.geteuid(),os.getegid()));held.check();status='cache_prepared'
     finally:
      if not published and stage.exists():need(identity(stage.lstat())[:2]==stage_id,'cache_changed');shutil.rmtree(stage)
    busy.check()
   survivors=[row for row in held.files if not row.path.is_relative_to(Path(temporary))]
   final_checks=list(held.final_checks)
 # Every temporary cleanup has now finished. Re-admit the exact approved cache
 # and lock through a final complete physical-close/named pass before success.
 for row in survivors:row.named()
 for check in final_checks:check()
 with Held()as final:
  lock=load_lock(final);verify_cache(cache,lock,final,(os.geteuid(),os.getegid()));final.check()
 for row in survivors:row.named()
 for check in final_checks:check()
 return {'status':status,'packages':1,'coreutils_package':VERSION,'security_accepted':False,'whole_image_other_callers_assessed':False}

def owned(pid):
 try:return os.waitid(os.P_PID,pid,os.WEXITED|os.WNOHANG|os.WNOWAIT)
 except ChildProcessError:raise RuntimeError('installer_cleanup') from None
def children():
 fd=os.open('/proc/self/task/'+str(os.getpid())+'/children',os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
 try:raw=os.read(fd,4097)
 finally:os.close(fd)
 need(len(raw)<=4096,'installer_cleanup');values=raw.split();need(len(values)<=64 and all(v.isdigit() for v in values),'installer_cleanup');return {int(v) for v in values}
def reap_extras(except_pid=None):
 end=time.monotonic()+3;unexpected=False
 while True:
  extra=children()-({except_pid} if except_pid is not None else set())
  if not extra:break
  unexpected=True;need(time.monotonic()<end,'installer_cleanup')
  for pid in extra:
   owned(pid)
   try:os.kill(pid,signal.SIGKILL)
   except ProcessLookupError:pass
   os.waitpid(pid,0)
 return unexpected
def cleanup_child(child):
 if child.returncode is None:
  try:owned(child.pid)
  except RuntimeError:return
  try:os.killpg(child.pid,signal.SIGKILL)
  except ProcessLookupError:pass
  child.wait(timeout=3)
def bounded(argv,allowed,*,guard=lambda:None,seconds=120):
 need(tuple(argv) in allowed,'installer_command');guard()
 def limits():
  for kind,value in ((resource.RLIMIT_CORE,0),(resource.RLIMIT_FSIZE,64*1024**2),(resource.RLIMIT_CPU,240),(resource.RLIMIT_AS,512*1024**2)):
   hard=resource.getrlimit(kind)[1];value=value if hard==resource.RLIM_INFINITY else min(value,hard);resource.setrlimit(kind,(value,value))
 child=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,close_fds=True,start_new_session=True,
  env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LC_ALL':'C.UTF-8','DEBIAN_FRONTEND':'noninteractive'},preexec_fn=limits)
 parts=[];count=0;end=time.monotonic()+seconds
 try:
  with selectors.DefaultSelector() as select:
   fd=child.stdout.fileno();os.set_blocking(fd,False);select.register(fd,selectors.EVENT_READ)
   while True:
    guard();left=end-time.monotonic();need(left>0,'installer_deadline')
    if not select.select(min(1,left)):continue
    block=os.read(fd,min(65536,MAX_OUTPUT-count+1))
    if not block:break
    count+=len(block);need(count<=MAX_OUTPUT,'installer_output');parts.append(block)
  while owned(child.pid) is None:need(time.monotonic()<end,'installer_deadline');time.sleep(.01)
  need(not (children()-{child.pid}),'installer_descendants')
  need(child.wait(timeout=3)==0,'installer_failed');guard();return b''.join(parts)
 finally:
  active=sys.exc_info()[0] is not None;bad=False
  try:cleanup_child(child)
  except BaseException:bad=True
  try:bad=reap_extras() or bad
  except BaseException:bad=True
  child.stdout.close()
  if bad and not active:raise RuntimeError('installer_cleanup')
def commands(path):
 need(Path(path)==CAPSULE/PACKAGE,'installer_command')
 return {INVENTORY,('/usr/bin/dpkg','--audit'),('/usr/bin/dpkg','--verify','coreutils'),
  ('/usr/bin/dpkg',*DOC_INCLUDES,'--install',str(path)),
  *(('/usr/bin/dpkg-deb','-f',str(path),field) for field in ('Package','Version','Architecture','Multi-Arch','Essential'))}
def package_inventory(raw):
 need(len(raw)<=MAX_OUTPUT,'package_inventory');out={}
 for line in raw.decode('ascii').splitlines():
  parts=line.split('\t');need(len(parts)==3 and parts[0] not in out and len(parts[0])<=256 and len(parts[1])<=100
   and re.fullmatch('[a-z0-9][a-z0-9+.-]*(?::[a-z0-9]+)?',parts[0]) and parts[1] and parts[2] in {'installed','config-files'},'package_inventory')
  out[parts[0]]=(parts[1],parts[2]);need(len(out)<=4096,'package_inventory')
 need(out,'package_inventory');return out
def image_boundary(cache):
 need(os.geteuid()==os.getegid()==0 and platform.machine() in {'x86_64','amd64'} and cache==CAPSULE
  and os.statvfs(cache).f_flag&os.ST_RDONLY,'image_boundary')
 with Held() as held:
  release=held.read(Path('/etc/os-release').resolve(strict=True),65536,owner=(0,0))
  fields=dict(line.split('=',1) for line in release.raw.decode('utf-8').splitlines() if '=' in line)
  need(fields.get('ID','').strip('"')=='debian' and fields.get('VERSION_ID','').strip('"')=='13'
   and fields.get('VERSION_CODENAME','').strip('"')=='trixie','image_boundary')
 need(sys.flags.isolated==sys.flags.ignore_environment==sys.flags.no_user_site==1 and not any(name.startswith('LD_') for name in os.environ),'loader_override')
 need(set(os.listdir('/sys/class/net'))=={'lo'},'image_boundary')
 # The normal Dockerfile's actual setpriv mask: CHOWN,FOWNER,FSETID,SETGID,SETUID.
 fd=os.open('/proc/self/status',os.O_RDONLY|os.O_CLOEXEC)
 try:raw=os.read(fd,65537)
 finally:os.close(fd)
 need(len(raw)<=65536,'image_boundary');rows=dict(line.split(':',1) for line in raw.decode('ascii').splitlines() if ':' in line)
 capability_contract(rows)
def capability_contract(rows):
 mask=sum(1<<bit for bit in (0,3,4,6,7))
 need(rows.get('NoNewPrivs','').strip()=='1' and int(rows['CapBnd'],16)==mask
  and int(rows['CapEff'],16)&~mask==0 and int(rows['CapPrm'],16)&~mask==0
  and int(rows['CapInh'],16)==int(rows['CapAmb'],16)==0,'image_boundary')
def verify_coreutils_payload(lock,held):
 payload=lock['runtime_payload']['coreutils']
 for spec in payload['regular_files']:
  row=held.read(Path(spec['path']),16*1024**2,{k:spec[k]for k in('bytes','sha256')},owner=(0,0))
  need(row.before[2]==spec['mode'],'package_payload')
 for spec in payload['symlinks']:held.files.append(PublicLink(spec['path'],spec['target']))
 held.check()
def verify_approved_libacl_payload(lock,held):
 payload=lock['runtime_payload']['approved_libacl']
 for spec in payload['regular_files']:
  row=held.read(Path(spec['path']),16*1024**2,{k:spec[k]for k in('bytes','sha256')},owner=(0,0));need(row.before[2]==spec['mode'],'package_payload')
 for spec in payload['symlinks']:held.files.append(PublicLink(spec['path'],spec['target']))
 held.check()
def baseline_inventory(before,lock):
 need(before.get('coreutils')in{(BASE_VERSION,'installed'),(VERSION,'installed')}
  and before.get('libacl1:amd64')==(lock['approved_libacl']['version'],'installed'),'package_baseline')
def verify_transition(before,after):
 expected=dict(before);expected['coreutils']=(VERSION,'installed');need(after==expected,'package_inventory')
def install_admitted(cache):
 with Held() as held:
  lock=load_lock(held);package=verify_cache(cache,lock,held);allowed=commands(package.path)
  need(ctypes.CDLL(None,use_errno=True).prctl(36,1,0,0,0)==0,'installer_cleanup')
  before=package_inventory(bounded(list(INVENTORY),allowed,guard=held.check))
  baseline_inventory(before,lock)
  verify_approved_libacl_payload(lock,held)
  library=lock['approved_libacl'];held.read(Path(library['path']),16*1024**2,{k:library[k]for k in('bytes','sha256')},owner=(0,0))
  need(bounded(['/usr/bin/dpkg','--audit'],allowed,guard=held.check).strip()==b'','package_inventory')
  for field,expected in [('Package','coreutils'),('Version',VERSION),('Architecture','amd64'),('Multi-Arch','foreign'),('Essential','yes')]:
   need(bounded(['/usr/bin/dpkg-deb','-f',str(package.path),field],allowed,guard=held.check).decode('ascii').strip()==expected,'package_control')
  bounded(['/usr/bin/dpkg',*DOC_INCLUDES,'--install',str(package.path)],allowed,guard=held.check)
  after=package_inventory(bounded(list(INVENTORY),allowed,guard=held.check));verify_transition(before,after)
  need(bounded(['/usr/bin/dpkg','--audit'],allowed,guard=held.check).strip()==b'' and bounded(['/usr/bin/dpkg','--verify','coreutils'],allowed,guard=held.check).strip()==b'','package_payload')
  verify_coreutils_payload(lock,held)
  verify_approved_libacl_payload(lock,held)
  held.read(Path(library['path']),16*1024**2,{k:library[k] for k in ('bytes','sha256')},owner=(0,0))
  held.check()
 return {'status':'image_package_installed','packages':1,'coreutils_package':VERSION,'security_accepted':False,'whole_image_other_callers_assessed':False}
def install(cache):
 cache=Path(cache);image_boundary(cache)
 def expired(*_):raise RuntimeError('installer_deadline')
 previous=signal.signal(signal.SIGALRM,expired);signal.alarm(600)
 try:return install_admitted(cache)
 finally:signal.alarm(0);signal.signal(signal.SIGALRM,previous)
def failure(error):
 code=error.args[0] if type(error) is RuntimeError and len(error.args)==1 and type(error.args[0]) is str and error.args[0] in CODES else 'unexpected_refusal'
 kinds=(RuntimeError,OSError,FileNotFoundError,PermissionError,FileExistsError,NotADirectoryError,IsADirectoryError,ValueError,TypeError,KeyError,TimeoutError)
 kind=type(error).__name__ if type(error)in kinds else 'unclassified'
 number=error.errno if type(error)in(OSError,FileNotFoundError,PermissionError,FileExistsError,NotADirectoryError,IsADirectoryError,TimeoutError)else None
 number=number if type(number)is int and 0<number<=4095 else None
 frames=[];trace=error.__traceback__;public=str(Path(__file__).absolute())
 while trace is not None and len(frames)<24:
  code_object=trace.tb_frame.f_code
  if code_object.co_filename==public and re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,99}',code_object.co_name)and 0<trace.tb_lineno<=10000:
   frames.append({'function':code_object.co_name,'line':trace.tb_lineno})
  trace=trace.tb_next
 return {'status':'refused','code':code,'exception_class':kind,'errno':number,'public_source_frames':frames,'security_accepted':False}

def main(argv=None):
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--cache-dir',type=Path,required=True);parser.add_argument('--artifact-zip',type=Path)
 mode=parser.add_mutually_exclusive_group();mode.add_argument('--check',action='store_true');mode.add_argument('--install-image-cache',action='store_true')
 try:
  args=parser.parse_args(argv);need(not args.artifact_zip or not (args.check or args.install_image_cache),'path')
  # The authentic lock must be armed before cache or image consumption starts.
  with Held()as gate:load_lock(gate)
  result=install(args.cache_dir) if args.install_image_cache else prepare(args.cache_dir,args.artifact_zip,args.check)
 except Exception as error:
  result=failure(error);print(json.dumps(result,sort_keys=True));return 1
 print(json.dumps(result,sort_keys=True));return 0
if __name__=='__main__':raise SystemExit(main())
