"""Unbound actual Python18+native-library sidecar; original32 stay unchanged."""
from hashlib import sha256
from pathlib import Path
import argparse,json,os,re,stat,sys
EXECUTION_ARMED=False
EXPECTED_IMAGE_ID=None
EXPECTED_SOURCE_COMMIT=None
MAX_BINARY=32*1024**2
MAX_MAPS=2*1024**2
def need(ok,code):
 if not ok:raise RuntimeError(code)
def ident(s):return(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def validate_versions(version,expat):
 need(type(version)is tuple and version==(3,11,18),'actual_python18_required')
 need(type(expat)is tuple and expat==(2,9,0),'actual_python18_expat_required')
def maps():
 fd=os.open('/proc/self/maps',os.O_RDONLY|os.O_CLOEXEC);raw=bytearray()
 try:
  while True:
   part=os.read(fd,min(65536,MAX_MAPS-len(raw)+1))
   if not part:break
   need(len(raw)+len(part)<=MAX_MAPS,'actual_python_maps_bound');raw.extend(part)
 finally:os.close(fd)
 result={}
 for line in bytes(raw).splitlines():
  f=line.split(maxsplit=5)
  if len(f)==6 and f[5].startswith(b'/'):
   dev=f[3].split(b':');need(len(dev)==2,'actual_python_mapping');name=f[5].decode('utf-8');result.setdefault(name,[]).append((int(dev[0],16),int(dev[1],16),int(f[4])))
 return result
class Binary:
 def __init__(self,path,held):
  self.path=path;self.fd=None;self.before=ident(path.lstat());self.parents=[(p,ident(p.lstat())[:5])for p in path.parents];self.record=None
  need(path.resolve(strict=True)==path and stat.S_ISREG(self.before[2])and self.before[3:6]==(0,0,1)and not self.before[2]&0o022 and 0<self.before[6]<=MAX_BINARY,'actual_python_binary')
  need(all(p.resolve(strict=True)==p and stat.S_ISDIR(s[2])and s[3:5]==(0,0)and not s[2]&0o022 for p,s in self.parents),'actual_python_parents')
  self.fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC);held.append(self);self.check()
  h=sha256();count=0
  while True:
   part=os.read(self.fd,min(65536,self.before[6]-count+1))
   if not part:break
   count+=len(part);need(count<=self.before[6],'actual_python_bound');h.update(part)
  need(count==self.before[6],'actual_python_bound');self.check()
  self.record={'path':str(path),'bytes':count,'sha256':h.hexdigest(),'actual_mapped_device_inode_bound':True}
 def named(self):need(self.path.resolve(strict=True)==self.path and ident(self.path.lstat())==self.before and all(p.resolve(strict=True)==p and ident(p.lstat())[:5]==s for p,s in self.parents),'actual_python_changed')
 def check(self):self.named();need(ident(os.fstat(self.fd))==self.before,'actual_python_changed')
 def mapped(self,value):
  found=value.get(str(self.path));need(found and all(v==(os.major(self.before[0]),os.minor(self.before[0]),self.before[1])for v in found),'actual_python_mapping');self.check()
 def close(self):
  if self.fd is None:return
  try:self.check()
  finally:os.close(self.fd);self.fd=None
  self.named()
def collect(image,commit,verifier):
 need(EXECUTION_ARMED is True and type(EXPECTED_IMAGE_ID)is str and re.fullmatch('sha256:[a-f0-9]{64}',EXPECTED_IMAGE_ID)and image==EXPECTED_IMAGE_ID and type(EXPECTED_SOURCE_COMMIT)is str and re.fullmatch('[a-f0-9]{40}',EXPECTED_SOURCE_COMMIT)and commit==EXPECTED_SOURCE_COMMIT and type(verifier)is str and re.fullmatch('[a-f0-9]{64}',verifier),'future_python_scope_unbound')
 need(os.getuid()==os.geteuid()==os.getgid()==os.getegid()==0 and sys.flags.isolated==sys.flags.ignore_environment==1,'isolated_actual_root')
 import platform,struct,pyexpat,sqlite3,_sqlite3
 validate_versions(tuple(sys.version_info[:3]),tuple(pyexpat.version_info))
 need(platform.python_implementation()=='CPython'and platform.machine()=='x86_64'and struct.calcsize('P')==8 and sys.prefix==sys.base_prefix=='/usr/local','actual_python_abi')
 binary=Path(sys.executable).resolve(strict=True);need(binary==Path('/usr/local/bin/python3.11'),'actual_python_path')
 current=maps();libpython=[Path(p)for p in current if p=='/usr/local/lib/libpython3.11.so.1.0'];sqlite=[Path(p)for p in current if re.fullmatch('/usr/lib/x86_64-linux-gnu/libsqlite3[.]so[.]0[.][0-9.]+',p)]
 need(len(libpython)==len(sqlite)==1,'actual_native_library_path')
 extensions=[Path(pyexpat.__file__).resolve(strict=True),Path(_sqlite3.__file__).resolve(strict=True)]
 need(all(p.parent==Path('/usr/local/lib/python3.11/lib-dynload')for p in extensions)and extensions[0].name=='pyexpat.cpython-311-x86_64-linux-gnu.so'and extensions[1].name=='_sqlite3.cpython-311-x86_64-linux-gnu.so','actual_native_library_path')
 paths=[binary,*libpython,*extensions,*sqlite];need(len(set(paths))==5,'actual_native_library_path');held=[];closed=False
 try:
  for path in paths:Binary(path,held)
  for item in held:item.mapped(current)
  seen=[];parser=pyexpat.ParserCreate();parser.StartElementHandler=lambda name,attrs:seen.append(name);parser.Parse(b'<owned><fixture/></owned>',True);need(seen==['owned','fixture'],'actual_expat_literal_parse')
  need(sqlite3.sqlite_version_info==(3,46,1),'actual_sqlite_capsule_version');connection=sqlite3.connect(':memory:')
  try:need(connection.execute("SELECT json_extract(?, '$.owned')",('{"owned":1}',)).fetchone()==(1,),'actual_sqlite_literal_query')
  finally:connection.close()
  for item in held:item.mapped(maps())
  records=[dict(item.record)for item in held];errors=[]
  for item in reversed(held):
   try:item.close()
   except BaseException as e:errors.append(e)
  for item in held:
   try:item.named()
   except BaseException as e:errors.append(e)
  need(not errors,'actual_python_finalization');closed=True
  return{'schema':'future_app_python18_presence/1','image_id':image,'source_commit':commit,'verifier_sha256':verifier,'actual_python_version':'.'.join(map(str,sys.version_info[:3])),'actual_expat_version':'.'.join(map(str,pyexpat.version_info)),'actual_binary':records[0],'actual_native_libraries':records[1:],'synthetic_XML_and_in_memory_SQLite_literals_passed':True,'original_8_runtime_24_component_assertions_replaced':False,'actual_sqlite_runtime':sqlite3.sqlite_version,'production_mounts_or_data_used':False,'scanner_findings_modified':False,'security_accepted':False}
 finally:
  if not closed:
   for item in reversed(held):
    if item.fd is not None:
     try:item.close()
     except BaseException:pass
PUBLIC_CODES=frozenset(('actual_python18_required','actual_python18_expat_required','future_python_scope_unbound','isolated_actual_root','actual_python_abi','actual_python_path','actual_python_binary','actual_python_parents','actual_python_changed','actual_python_bound','actual_python_maps_bound','actual_python_mapping','actual_native_library_path','actual_python_finalization','actual_expat_literal_parse','actual_sqlite_capsule_version','actual_sqlite_literal_query','python_report_bound'))
def main(argv=None):
 p=argparse.ArgumentParser();p.add_argument('--image-id');p.add_argument('--source-commit');p.add_argument('--verifier-sha256');a=p.parse_args(argv)
 if not EXECUTION_ARMED:print(json.dumps({'status':'UNARMED','collection_performed':False,'security_accepted':False}));return 0
 try:value=collect(a.image_id,a.source_commit,a.verifier_sha256)
 except (RuntimeError,OSError)as error:
  code=error.args[0]if type(error)is RuntimeError and len(error.args)==1 and error.args[0]in PUBLIC_CODES else'unclassified_python_probe_refusal';print(json.dumps({'status':'REFUSED','closed_code':code,'security_accepted':False},sort_keys=True));return 1
 raw=json.dumps(value,sort_keys=True);need(len(raw)<=16384,'python_report_bound');print(raw);return 0
if __name__=='__main__':raise SystemExit(main())
