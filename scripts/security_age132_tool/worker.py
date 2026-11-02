"""Explicit hosted-only managed age build; default inert, never install/backup."""
from pathlib import Path,PurePosixPath
from types import ModuleType
from hashlib import sha256
from uuid import uuid4
import argparse,base64,errno,fcntl,io,json,os,re,shutil,stat,sys,tarfile,time,urllib.request
HERE=Path(__file__).absolute().parent
IO_PIN=(8831,'b2e6159d62ded26c81c5a72ad9dbda73f6029719451ff2b27d7edeefeae7b568')
PINS={'source_io.py': (8831, 'b2e6159d62ded26c81c5a72ad9dbda73f6029719451ff2b27d7edeefeae7b568'), 'host_io_original.py': (53351, 'fc9fc8e4ae7fe1af75fd0b4c63fedb0ebfa5820148bb311c5eaafcaaa5bbbb82'), 'contracts.py': (22660, 'a5ae855750c67e83b7349919b0450a5094212797cab1bee904f191475846dd41'), 'inputs.json': (14758, '9cfa5c3dd9e3faa6c85201c1337933aa77a2115e6b44823daf4f36d4488ad642'), 'age.go.mod': (619, '53e9fdd9fd328e14aab3eebe759c5ab048c1a7a8bbacbb8a853556b26efedc9c'), 'age.go.sum': (2823, '1f504fde4a3f8cbc4c159ac4e75f17cee7015b1d74d555feb79e06a34365eb75'), 'scanner.go.mod': (624, '5c3d2dd42ed9e7fa9d0025334c7edcf8bcf5fff788b0a836cbd746f444690b2c'), 'scanner.go.sum': (2625, '11a4fa1753df1bb15e5bc4fba3192eeab54764274f213c77f034c916217d7bdb'), 'public-x25519-test-fixture.txt': (823, '3048a68cb0a94f2fd0d33bef64288364ccc16adcc2321018f3a2036aa92923d9')}
REPOSITORY='mohsenbarari/trading_bot'
def need(v,c):
 if v is not True:raise RuntimeError(c)
def frame(s):return(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def closed(fd):
 try:os.fstat(fd)
 except OSError as e:need(e.errno==errno.EBADF,'physical_close_unknown')
 else:need(False,'physical_close_unknown')
def module(name,raw,path):
 m=ModuleType('_fixed_age_'+name);m.__file__=str(path);exec(compile(raw,str(path),'exec'),m.__dict__);return m
def bootstrap():
 p=HERE/'source_io.py';s=frame(p.lstat());need(s[2]in{0o100600,0o100644}and s[3]==os.geteuid()and s[5]==1 and s[6]==IO_PIN[0],'bootstrap_source')
 fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
 try:b=os.pread(fd,IO_PIN[0]+1,0);need((len(b),sha256(b).hexdigest())==IO_PIN and frame(os.fstat(fd))==frame(p.lstat())==s,'bootstrap_source_pin')
 finally:os.close(fd)
 closed(fd);need(frame(p.lstat())==s,'bootstrap_source_postclose');return module('io',b,p)
def owned_directory(path,mode,guard):
 guard();path.mkdir(mode=0o700);s=frame(path.lstat());fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
 try:
  need(s[3]==os.geteuid()and frame(os.fstat(fd))==s,'owned_directory');os.fchmod(fd,mode);after=frame(os.fstat(fd));need(after[:2]==s[:2]and after[2]==stat.S_IFDIR|mode and after[3:8]==s[3:8]and frame(path.lstat())==after,'owned_directory')
 finally:os.close(fd)
 closed(fd);need(frame(path.lstat())==after,'owned_directory_postclose');guard()
class ReaderStream:
 def __init__(self,reader):self.reader=reader;self.offset=0
 def read(self,n):
  need(type(n)is int and 0<=n<=65536,'archive_read_bound')
  if not n or self.offset==self.reader.before[6]:return b''
  b=self.reader.read(min(n,self.reader.before[6]-self.offset),self.offset);self.offset+=len(b);return b
class ProductHeld:
 """Owned public-product FD; legacy Held is a read-only metadata witness only."""
 def __init__(self,M,path,maximum,pin=None,allow_empty=False):
  self.item=self.original=self.origin=None;self.flags=self.offset=self.pin=None;self.path=path;self.parents=();self.before=None
  self.closed=self.unknown=self.attempted=self.physical_EBADF=self.no_fd_prefix=self.held=self.admitted=False
  try:
   need(type(maximum)is int and 0<maximum<=64*1024**2 and type(allow_empty)is bool and isinstance(path,Path),'product_role')
   self.parents=tuple(M.parent_frame(path));self.before=frame(path.lstat())
   need(path.resolve(strict=True)==path and stat.S_ISREG(self.before[2])and self.before[3]in{0,M.HOST_UID}and self.before[5]==1 and not self.before[2]&0o022 and(0<=self.before[6]<=maximum if allow_empty else 0<self.before[6]<=maximum),'public_file')
   self.item=M.Held.__new__(M.Held);self.item.path=path;self.item.parents=self.parents;self.item.before=self.before;self.item.fd=None;self.item.binding=None
   # Publish the original descriptor before fstat/flags/offset or any callback.
   self.original=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC);self.item.fd=self.original
   need(frame(os.fstat(self.original))==self.before,'product_acquisition')
   self.flags=(fcntl.fcntl(self.original,fcntl.F_GETFL),fcntl.fcntl(self.original,fcntl.F_GETFD));self.offset=os.lseek(self.original,0,os.SEEK_CUR)
   need(self.flags[0]&os.O_ACCMODE==os.O_RDONLY and self.flags[1]==fcntl.FD_CLOEXEC and self.offset==0,'product_acquisition')
   self.origin=(self.item,path,self.original,self.before,self.flags,self.offset,self.parents,self.pin);self.check();h=sha256();parts=[]
   for offset in range(0,self.before[6],65536):
    body=self._read(min(65536,self.before[6]-offset),offset);h.update(body)
    if maximum<=65536:parts.append(body)
   value=(self.before[6],h.hexdigest());need(pin is None or value==pin,'public_pin')
   self.pin=value;self.item.binding={'bytes':value[0],'sha256':value[1]};self.item.raw=b''.join(parts)
   self.origin=(*self.origin[:7],value);self.check()
   # Admit this actor's one deliberate offset transition; normal reads use pread.
   need(os.lseek(self.original,self.before[6],os.SEEK_SET)==self.before[6],'product_own_offset')
   self.offset=self.before[6];self.origin=(*self.origin[:5],self.offset,*self.origin[6:]);self.check();self.admitted=True
  except BaseException:
   if self.original is not None:self.unknown=True;self.held=True
   raise
 def _facts(self,origin,opened=True):
  item,path,fd,before,flags,offset,parents,pin=origin
  need(self.origin is origin and self.item is item and self.original==fd and self.path==path and self.before==before and self.flags==flags and self.offset==offset and self.parents is parents and self.pin==pin,'product_original_alias')
  need(item.fd==(fd if opened else None)and item.path==path and item.before==before and item.parents is parents and item.binding==(None if pin is None else{'bytes':pin[0],'sha256':pin[1]}),'product_original_alias')
  need(path.resolve(strict=True)==path and frame(path.lstat())==before and all(p.resolve(strict=True)==p and frame(p.lstat())[:5]==old for p,old in parents),'product_original_named')
  if opened:need(frame(os.fstat(fd))==before and(fcntl.fcntl(fd,fcntl.F_GETFL),fcntl.fcntl(fd,fcntl.F_GETFD))==flags and os.lseek(fd,0,os.SEEK_CUR)==offset,'product_original_OFD')
 def check(self):
  try:
   need(not self.closed and not self.unknown and self.origin is not None,'product_original_FD');origin=self.origin;self._facts(origin)
   # This witness may fail or mutate aliases; it never owns close authority.
   item=origin[0];item.check();self._facts(origin)
  except BaseException:self.unknown=True;self.held=self.original is not None and not self.physical_EBADF;raise
 def _read(self,n,offset):
  try:
   need(type(n)is int and 0<n<=65536 and type(offset)is int and 0<=offset<=self.before[6],'product_read_bound');self.check();origin=self.origin;fd=origin[2]
   body=os.pread(fd,n,offset);need(type(body)is bytes and len(body)==min(n,self.before[6]-offset),'product_read_size');self.check();self._facts(origin);return body
  except BaseException:self.unknown=True;self.held=self.original is not None and not self.physical_EBADF;raise
 def read(self,n,offset):
  need(self.admitted,'product_not_admitted');return self._read(n,offset)
 def body(self):
  self.check();need(self.admitted,'product_not_admitted');body=b''.join(self.read(min(65536,self.before[6]-i),i)for i in range(0,self.before[6],65536));self.check();return body
 def named(self):
  try:need(self.closed and self.physical_EBADF and not self.unknown,'product_named_finality');self._facts(self.origin,False)
  except BaseException:self.unknown=True;self.held=self.original is not None and not self.physical_EBADF;raise
 def close(self):
  if self.attempted or self.closed or self.unknown:
   self.unknown=True;self.held=self.original is not None and not self.physical_EBADF;raise RuntimeError('product_close_unknown')
  self.attempted=True
  if self.original is None and self.origin is None:
   try:need(self.item is None or getattr(self.item,'fd',None)is None,'product_no_fd_prefix')
   except BaseException:self.unknown=True;raise
   self.closed=True;self.no_fd_prefix=True;return
  try:
   origin=self.origin;need(origin is not None,'product_close_unknown');fd=origin[2];item=origin[0]
   self.check();self._facts(origin);self.check();self._facts(origin)
   os.close(fd);closed(fd);self.physical_EBADF=True;self.closed=True;item.fd=None;self.named()
  except BaseException:self.unknown=True;self.held=self.original is not None and not self.physical_EBADF;raise
def clone_public_module(source,target):
 need(not target.exists(),'module_copy_exists');count=0;total=0
 for p in source.rglob('*'):
  s=p.lstat();need(stat.S_ISDIR(s.st_mode)or stat.S_ISREG(s.st_mode),'module_source_link');count+=1;total+=s.st_size if stat.S_ISREG(s.st_mode)else 0;need(count<=20000 and total<=128*1024**2,'module_copy_bound')
 shutil.copytree(source,target,symlinks=False)
 # These are newly created private copies, never borrowed module-cache paths.
 for p in [target,*target.rglob('*')]:
  s=p.lstat();need(s.st_uid==os.geteuid()and(stat.S_ISDIR(s.st_mode)or stat.S_ISREG(s.st_mode)),'module_copy_owner')
  fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|(os.O_DIRECTORY if stat.S_ISDIR(s.st_mode)else 0))
  try:need(frame(os.fstat(fd))==frame(p.lstat()),'module_copy_identity');os.fchmod(fd,0o700 if stat.S_ISDIR(s.st_mode)or s.st_mode&0o111 else 0o600)
  finally:os.close(fd)
  closed(fd)
def sdk_inventory(reader):
 rows=[];total=0;go=None
 with tarfile.open(mode='r|gz',fileobj=ReaderStream(reader))as archive:
  for item in archive:
   path=PurePosixPath(item.name);need(not path.is_absolute()and '..'not in path.parts and path.parts[0]=='go'and str(path)==item.name.rstrip('/'),'sdk_archive_path');need(item.isdir()or item.isfile(),'sdk_archive_link')
   need(len(rows)<40000 and 0<=item.size<=64*1024**2 and total+item.size<=512*1024**2,'sdk_archive_bound');rows.append(item.name);total+=item.size
   if item.name=='go/bin/go':
    need(go is None and item.isfile(),'sdk_go_inventory');h=sha256();count=0;stream=archive.extractfile(item)
    while chunk:=stream.read(65536):h.update(chunk);count+=len(chunk)
    need(count==item.size,'sdk_go_bound');go=(count,h.hexdigest())
 need(go is not None and len(set(rows))==len(rows),'sdk_inventory');return go,{'members':len(rows),'uncompressed_bytes':total}
def hosted():
 expected={'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'github-hosted','RUNNER_OS':'Linux','RUNNER_ARCH':'X64','GITHUB_REPOSITORY':REPOSITORY,'GITHUB_REPOSITORY_ID':'1065680631','GITHUB_REF':'refs/heads/main','GITHUB_EVENT_NAME':'workflow_dispatch'}
 need(os.getuid()==os.geteuid()==1001 and all(os.environ.get(k)==v for k,v in expected.items()),'hosted_main_UID1001_only')
def workspace_manifests(mod,sums,main,expected):
 """Validate final managed manifests, never infer a future selected graph."""
 need(type(mod)is bytes and type(sums)is bytes and 0<len(mod)<=65536 and 0<len(sums)<=65536,'workspace_manifest_bound')
 directives={};requirements={};block=False
 for raw in mod.decode('ascii').splitlines():
  line=raw.partition('//')[0].strip()
  if not line:continue
  if line=='require (':need(not block,'workspace_module_syntax');block=True;continue
  if line==')':need(block,'workspace_module_syntax');block=False;continue
  fields=line.split()
  if block:need(len(fields)==2,'workspace_module_syntax');path,version=fields;need(path not in requirements,'workspace_module_duplicate');requirements[path]=version
  elif fields[0]=='require':need(len(fields)==3 and fields[1]not in requirements,'workspace_module_syntax');requirements[fields[1]]=fields[2]
  else:need(len(fields)==2 and fields[0]in{'module','go','toolchain'}and fields[0]not in directives,'workspace_root_directive');directives[fields[0]]=fields[1]
 need(not block and directives=={'module':main,'go':'1.26.0','toolchain':'go1.27.3'},'workspace_root_directive')
 versions={r['path']:r['version']for r in expected};need(requirements==versions,'workspace_requirements_exact')
 approved={(r['path'],r['version']):r['module_sum']for r in expected};approved.update({(r['path'],r['version']+'/go.mod'):r['gomod_sum']for r in expected});observed={};metadata=[]
 for line in sums.decode('ascii').splitlines():
  need(0<len(line)<=512,'workspace_sum_syntax');fields=line.split();need(len(fields)==3,'workspace_sum_syntax');path,version,value=fields;key=(path,version)
  need(key not in observed and path in versions and re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+(?:-[a-zA-Z0-9.-]+)?(?:/go.mod)?',version)is not None and re.fullmatch(r'h1:[A-Za-z0-9+/]{43}=',value)is not None,'workspace_sum_inventory')
  if key in approved:need(value==approved[key],'workspace_selected_checksum')
  else:
   # MVS may retain hashes of other go.mod files. They grant no selection,
   # source, origin, build or backup authority; unknown paths/full zips refuse.
   need(version.endswith('/go.mod')and version!=versions[path]+'/go.mod','workspace_unapproved_sum');metadata.append({'path':path,'version':version,'sum':value,'selected_or_qualified':False})
  observed[key]=value
 need(all(observed.get(k)==v for k,v in approved.items()),'workspace_selected_checksum')
 return{'root_directives':directives,'exact_requirements':versions,'nonselected_go_mod_checksum_metadata':metadata,'selected_graph_requires_separate_actual_validation':True}
def public_code(error,fallback):
 value=str(error);return value if re.fullmatch('[A-Za-z0-9_]{1,100}',value)else fallback
def publication_failure(result,error):
 """Keep the original build failure and command/finalizer truth intact."""
 out=dict(result);prior=result['status'];out['prior_build_status']=prior
 failure={'phase':'final_RESULT_publication','code':public_code(error,'result_publication_refused'),'truth':getattr(error,'age_result_publication_truth',None)}
 out['final_publication_failure']=failure
 if prior!='FAILED':out.update(phase=failure['phase'],code=failure['code'])
 out.update(status='FAILED',runtime_install_allowed=False,backup_eligibility=False,security_accepted=False,actual_install_or_backup_performed=False);return out
def publish_result(M,path,result,source_rows,deadline):
 """Finite public JSON receipt or pinned template bytes; no Source registration.

 Retain the reader across original writer close. Template roles retire before
 their names enter the explicit Go-managed mutable workspace phase.
 """
 fd=None;reader=None;writer_before=None;writer_flags=None;writer_offset=0;parents=();writer_attempted=False;writer_closed=False;writer_held=False;primary=None;errors=[]
 def writer_check():
  need(time.monotonic()<deadline and fd is not None and writer_before is not None,'result_writer_prefix')
  need(frame(os.fstat(fd))==frame(path.lstat())==writer_before and(fcntl.fcntl(fd,fcntl.F_GETFL),fcntl.fcntl(fd,fcntl.F_GETFD))==writer_flags and os.lseek(fd,0,os.SEEK_CUR)==writer_offset,'result_original_writer')
  need(path.resolve(strict=True)==path and all(p.resolve(strict=True)==p and frame(p.lstat())[:5]==old for p,old in parents),'result_original_writer')
 def writer_close():
  nonlocal writer_attempted,writer_closed,writer_held
  need(not writer_attempted,'result_writer_close_repeated');writer_attempted=True
  try:writer_check();os.close(fd);closed(fd);writer_closed=True;need(frame(path.lstat())==writer_before,'result_writer_postclose_named')
  except BaseException:writer_held=not writer_closed;raise
 try:
  body=result if type(result)is bytes else json.dumps(result,sort_keys=True).encode()+b'\n';need(0<len(body)<=65536,'result_bound');parents=tuple(M.parent_frame(path));need(not path.exists(),'result_exists')
  fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,0o600)
  writer_before=frame(os.fstat(fd));writer_flags=(fcntl.fcntl(fd,fcntl.F_GETFL),fcntl.fcntl(fd,fcntl.F_GETFD));need(writer_before[2]==0o100600 and writer_before[3:7]==(os.geteuid(),os.getegid(),1,0)and writer_flags[0]&os.O_ACCMODE==os.O_WRONLY and writer_flags[1]==fcntl.FD_CLOEXEC,'result_writer_metadata');writer_check()
  view=memoryview(body)
  while view:
   writer_check();n=os.write(fd,view);need(type(n)is int and 0<n<=len(view),'result_write');writer_offset+=n;view=view[n:];after=frame(os.fstat(fd));need(after[:6]==writer_before[:6]and after[6]==writer_offset and frame(path.lstat())==after,'result_own_write');writer_before=after;writer_check()
  os.fsync(fd);writer_check();reader=ProductHeld.__new__(ProductHeld);ProductHeld.__init__(reader,M,path,65536,(len(body),sha256(body).hexdigest()))
  need(reader.before==writer_before,'result_producer_reader');reader.check();writer_check();writer_close();reader.check();reader.close();reader.named()
 except BaseException as error:primary=error
 finally:
  if fd is not None and not writer_attempted:
   try:writer_close()
   except BaseException as error:
    errors.append('result_original_writer_finality_unknown')
    if primary is None:primary=error
  if reader is not None and not getattr(reader,'attempted',False):
   try:reader.close()
   except BaseException as error:
    errors.append('result_original_reader_finality_unknown')
    if primary is None:primary=error
  for row in source_rows:
   try:need(time.monotonic()<deadline,'result_final_deadline');row.named()
   except BaseException as error:
    errors.append('source_postfinal_named_unknown')
    if primary is None:primary=error
 truth={'writer_acquired':fd is not None,'writer_original_physical_EBADF':writer_closed,'writer_HELD':writer_held,'reader_acquired':reader is not None and getattr(reader,'original',None)is not None,'reader_original_physical_EBADF':reader is not None and getattr(reader,'physical_EBADF',False),'reader_unknown':reader is not None and getattr(reader,'unknown',True),'finalizer_codes':errors,'provisional_file_never_acceptance':True}
 if primary is not None:primary.age_result_publication_truth=truth;raise primary
 need(writer_closed and reader is not None and reader.closed and reader.physical_EBADF and not reader.unknown and not errors,'result_finality');return truth
def public_packaged_roundtrip(C,root,staged,binaries,fixture,command,save,held_product,guard,deadline):
 """Only exact staged payload bytes and the pinned public upstream test key.

 The caller later binds these same hashes into the actual DEB. No existing
 cipher, backup, key, plugin, installed command or stdout identity is used.
 Successful process returns inherit the original finite command's zero/EOF/
 physical-pipe-close contract; outer product/Source finalizers remain required.
 """
 def check():guard();need(time.monotonic()<deadline,'public_CLI_deadline')
 check();need(set(staged)==set(binaries)=={'age','age-keygen'},'public_CLI_binary_inventory')
 for name in('age','age-keygen'):
  need(type(staged[name])is ProductHeld and type(binaries[name])is ProductHeld
   and staged[name].path==root/'package/usr/bin'/name and binaries[name].path==root/'age-output'/name,'public_CLI_binary_owner')
  staged[name].check();binaries[name].check();need(staged[name].pin==binaries[name].pin,'public_CLI_delivered_binary_pin')
 identity,recipient=C.public_cli_fixture(fixture)
 identity_path=root/'PUBLIC-UPSTREAM-TEST-IDENTITY.txt';plain=root/'PUBLIC-PACKAGED-PLAINTEXT.txt'
 derived=root/'PUBLIC-PACKAGED-RECIPIENT.txt';cipher=root/'PUBLIC-PACKAGED-CIPHERTEXT.age';restored=root/'PUBLIC-PACKAGED-RESTORED.txt'
 # lstat catches broken aliases too. No named file from an earlier run is used.
 for path in(identity_path,plain,derived,cipher,restored):
  check()
  try:path.lstat()
  except FileNotFoundError:pass
  else:raise RuntimeError('public_CLI_path_preexists')
 save(identity_path,identity);save(plain,C.PUBLIC_CLI_PAYLOAD);check();facts=[]
 def run(argv,name):
  check();out=command(argv,name,4096);need(type(out)is bytes and not out,'public_CLI_unexpected_stdout');check()
  facts.append({'name':name,'actual_returncode':0,'stdout_eof_observed':True,'actual_pipe_closed':True,'stdout_bytes':0})
 run([str(staged['age-keygen'].path),'-y','-o',str(derived),str(identity_path)],'packaged-public-keygen-derive')
 recipient_reader=held_product(derived,256);need(recipient_reader.body()==recipient,'public_CLI_recipient_equality');check()
 run([str(staged['age'].path),'--encrypt','--recipient',recipient.strip().decode('ascii'),'--output',str(cipher),str(plain)],'packaged-public-encrypt')
 cipher_reader=held_product(cipher,65536);C.native_format(cipher_reader.body());check()
 run([str(staged['age'].path),'--decrypt','--identity',str(identity_path),'--output',str(restored),str(cipher)],'packaged-public-decrypt')
 restored_reader=held_product(restored,65536);need(restored_reader.body()==C.PUBLIC_CLI_PAYLOAD,'public_CLI_plaintext_equality');check()
 def pin(row):return{'bytes':row.pin[0],'sha256':row.pin[1]}
 files={name:pin(row)for name,row in staged.items()}
 value={'schema':'age132_public_packaged_CLI_roundtrip/1','public_fixture':{'member':C.PUBLIC_CLI_FIXTURE_MEMBER,'bytes':C.PUBLIC_CLI_FIXTURE_PIN[0],'sha256':C.PUBLIC_CLI_FIXTURE_PIN[1]},
  'CLI_files':files,'plaintext':{'bytes':len(C.PUBLIC_CLI_PAYLOAD),'sha256':sha256(C.PUBLIC_CLI_PAYLOAD).hexdigest()},
  'ciphertext':pin(cipher_reader),'restored':pin(restored_reader),'derived_recipient':pin(recipient_reader),'commands':facts,
  'CLI_keygen_public_recipient_proven':True,'CLI_decrypt_roundtrip_proven':True,'owned_current_run_ciphertext_only':True,
  'operational_key_generated_or_read':False,'existing_backup_or_data_used':False,'raw_identity_exported':False,
  'provisional_until_outer_Source_and_product_finality':True,'runtime_install_allowed':False,'backup_eligibility':False,'security_accepted':False}
 check();return C.public_packaged_cli_roundtrip(value,files)

def execute(source_sha):
 hosted()
 need(set(PINS)=={'source_io.py','host_io_original.py','contracts.py','inputs.json','age.go.mod','age.go.sum','scanner.go.mod','scanner.go.sum','public-x25519-test-fixture.txt'},'source_inventory')
 IO=bootstrap();scope=IO.Scope(3720);primary=None;final=[];phase='source';exports=None;result=None;products=[];failure_fact=None;failure_output=None;workspaces={};mutable_kind=None;workspace_truth={}
 started=time.monotonic();end=started+3600;payload_end=end-90
 try:
  scope.open(Path(__file__).absolute(),(Path(__file__).stat().st_size,source_sha),131072);held={name:scope.open(HERE/name,pin,262144)for name,pin in PINS.items()}
  def raw(name):
   r=held[name];return b''.join(r.read(min(65536,r.before[6]-i),i)for i in range(0,r.before[6],65536))
  C=module('contracts',raw('contracts.py'),HERE/'contracts.py');M=module('transport',raw('host_io_original.py'),HERE/'host_io_original.py');lock=C.validate_lock(C.document(raw('inputs.json')))
  def guard():
   need(time.monotonic()<payload_end,'build_deadline');scope.check()
   for item in products:item.check()
   for kind,rows in workspaces.items():
    for row in rows:
     path,before,parents=row;now=frame(path.lstat())
     need(path.resolve(strict=True)==path and now[2]==0o100600 and now[3:6]==(os.geteuid(),os.getegid(),1)and 0<=now[6]<=65536 and all(p.resolve(strict=True)==p and frame(p.lstat())[:5]==old for p,old in parents),'workspace_mutable_metadata')
     if mutable_kind!=kind:need(now==before and now[6]>0,'workspace_outside_producer_changed')
  temp=Path(os.environ['RUNNER_TEMP']);workspace=Path(os.environ['GITHUB_WORKSPACE']);need(temp.resolve(strict=True)==temp and workspace.resolve(strict=True)==workspace and HERE.is_relative_to(workspace),'hosted_paths')
  need(shutil.disk_usage(temp).free>=4*1024**3,'disk_floor');root=temp/('age132-build-'+uuid4().hex);owned_directory(root,0o700,guard);exports=temp/'security-age132-public';owned_directory(exports,0o700,guard)
  for name in('home','gopath','gocache','modcache','tmp','sdk','age-output','scanner-output'):owned_directory(root/name,0o700,guard)
  env={'PATH':'/usr/bin:/bin','HOME':str(root/'home'),'TMPDIR':str(root/'tmp'),'LC_ALL':'C.UTF-8','LANG':'C.UTF-8','APP_ENV_FILE':'config/unit-test.env.example','GOTOOLCHAIN':'local','GOENV':'off','GOWORK':'off','GO111MODULE':'on','CGO_ENABLED':'0','GOOS':'linux','GOARCH':'amd64','GOPATH':str(root/'gopath'),'GOCACHE':str(root/'gocache'),'GOMODCACHE':str(root/'modcache'),'GOPROXY':'https://proxy.golang.org','GOSUMDB':'sum.golang.org','GOPRIVATE':'','GONOSUMDB':'','GONOPROXY':'','GOFLAGS':''}
  def held_product(path,maximum,pin=None,allow_empty=False):
   item=ProductHeld.__new__(ProductHeld);products.append(item);ProductHeld.__init__(item,M,path,maximum,pin,allow_empty);return item
  def save(path,body,mode=0o600):
   need(type(body)is bytes and 0<len(body)<=64*1024**2,'output_bound');w=scope.create(path,64*1024**2)
   for i in range(0,len(body),65536):w.append(body[i:i+65536])
   w.check();before=w.before;os.fchmod(w.original_fd,mode);after=IO.frame(os.fstat(w.original_fd));need(after[:2]==before[:2]and after[2]==stat.S_IFREG|mode and after[3:8]==before[3:8]and after[8]>=before[8],'output_chmod_identity');w.before=after;pin=w.seal((len(body),sha256(body).hexdigest()));r=held_product(path,64*1024**2,(pin['bytes'],pin['sha256']))if mode==0o755 else scope.open(path,(pin['bytes'],pin['sha256']),64*1024**2);need(r.before==w.before,'output_reader_writer_identity');w.close();r.check();return r
  def command(argv,name,maximum=8*1024**2,extra=None,mutable=None):
   nonlocal failure_fact,failure_output,mutable_kind
   guard();need(mutable_kind is None,'workspace_parallel_producer');body=None;caught=None
   if mutable is not None:
    need(mutable in workspaces and Path.cwd()==projects[mutable]and name in{mutable+'-dependency-download',mutable+'-module-verify',mutable+'-module-graph',mutable+'-vendor-source'},'workspace_mutation_command');mutable_kind=mutable
   try:body=M.run(argv,env={**env,**(extra or{})},deadline=payload_end,maximum=maximum,guard=guard)
   except BaseException as e:
    caught=e
    fact=getattr(e,'public_command_failure',None)
    if type(fact)is dict:
     failure_fact={k:fact[k]for k in('actual_returncode','observed_output_bytes','actual_pipe_closed','stdout_eof_observed','maximum_output_bytes')};failure_fact['command_name']=name
     captured=fact.get('captured_output')
     if type(captured)is bytes:
      prefix=captured[:16384];failure_output={'encoding':'base64','prefix_bytes':len(prefix),'captured_bytes':len(captured),'prefix_truncated':len(prefix)<len(captured),'prefix_base64':base64.b64encode(prefix).decode('ascii'),'public_tool_output_only':True,'diagnostic_never_acceptance':True}
   finally:
    if mutable is not None:
     try:
      guard()
      # The finite transport has reaped its producer and closed its pipe before
      # returning. Admit metadata changes only at this command boundary.
      for row in workspaces[mutable]:row[1]=frame(row[0].lstat())
     except BaseException as e:
      if caught is None:caught=e
     finally:mutable_kind=None
   if caught is not None:raise caught
   if body:save(exports/(name+'.log'),body)
   need(shutil.disk_usage(root).free>=1024**3,'disk_floor');guard();return body
  phase='authenticated_Go_SDK';sdk=root/'go-sdk.tar.gz';w=scope.create(sdk,lock['go']['bytes'])
  class Redirect(urllib.request.HTTPRedirectHandler):
   def redirect_request(self,request,fp,code,msg,headers,newurl):
    need(newurl==lock['go']['redirect'],'sdk_redirect');return super().redirect_request(request,fp,code,msg,headers,newurl)
  opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),Redirect())
  with opener.open(lock['go']['url'],timeout=30)as response:
   need(response.status==200 and response.url in{lock['go']['url'],lock['go']['redirect']},'sdk_endpoint')
   while chunk:=response.read(min(65536,lock['go']['bytes']-w.count+1)):
    need(w.count+len(chunk)<=lock['go']['bytes'],'sdk_download_bound');w.append(chunk);guard()
  ref=w.seal((lock['go']['bytes'],lock['go']['sha256']));archive=scope.open(sdk,(ref['bytes'],ref['sha256']),lock['go']['bytes']);need(archive.before==w.before,'sdk_producer_reader');w.close();go_pin,archive_truth=sdk_inventory(archive)
  command(['/usr/bin/tar','-xzf',str(sdk),'--no-same-owner','--no-same-permissions','-C',str(root/'sdk')],'sdk-unpack',65536)
  go=root/'sdk/go/bin/go';go_reader=held_product(go,64*1024**2,go_pin);env['PATH']=str(go.parent)+':/usr/bin:/bin';need(command([str(go),'version'],'go-version',4096).strip()==b'go version go1.27.3 linux/amd64','go_version')
  phase='authenticated_module_sources';os.chdir(root);module_downloads={}
  projects={};source_files={}
  for kind in('age','scanner'):
   # Version-query downloads run outside either managed main module, so the
   # scanner acquisition cannot rewrite the already sealed age manifests.
   os.chdir(root);pin=lock[kind];download=C.download_record(command([str(go),'mod','download','-json',pin['path']+'@'+pin['version']],kind+'-source-download',65536),pin);directory=Path(download['Dir']);need(directory.resolve(strict=True)==directory and directory.is_relative_to(root/'modcache'),'module_directory')
   target=root/(kind+'-source');clone_public_module(directory,target);need(target.resolve(strict=True)==target,'source_copy');projects[kind]=target;module_downloads[kind]=download;source_files[kind]=[]
   for path in sorted(target.rglob('*.go')):
    item=held_product(path,4*1024**2,allow_empty=True);source_files[kind].append({'path':str(path.relative_to(target)),'bytes':item.pin[0],'sha256':item.pin[1],'original_full9':list(item.before)})
   rows=[];template_truth=[]
   for name in('go.mod','go.sum'):
    path=target/name
    if path.exists():save(exports/(kind+'-original-'+name),path.read_bytes());path.unlink()
    guard();template_truth.append(publish_result(M,path,raw(kind+'.'+name),(),scope.end));guard();rows.append([path,frame(path.lstat()),tuple(M.parent_frame(path))])
   workspaces[kind]=rows
   os.chdir(target);downloads=C.documents(command([str(go),'mod','download','-json','all'],kind+'-dependency-download',2*1024**2,mutable=kind));expected={r['path']:r for r in lock[kind+'_modules']};seen=set();download_evidence=[]
   for row in downloads:
    need(row.get('Path')in expected and row['Path']not in seen,'dependency_download_inventory');reference=expected[row['Path']];C.download_record(C.encoded(row),reference,origin_required=False);seen.add(row['Path'])
    download_evidence.append({'path':row['Path'],'version':row['Version'],'locked_module_h1':reference['module_sum'],'observed_module_h1':row['Sum'],'locked_go_mod_h1':reference['gomod_sum'],'observed_go_mod_h1':row['GoModSum'],'content_checksums_match_lock':True,'locked_origin_reference':reference['origin'],'observed_origin':row.get('Origin'),'vcs_origin_observed_and_exactly_matched':'Origin'in row})
   need(seen==set(expected),'dependency_download_inventory');command([str(go),'mod','verify'],kind+'-module-verify',65536,mutable=kind)
   if kind=='age':command([str(go),'mod','vendor'],kind+'-vendor-source',1024**2,mutable=kind)
   graph=C.graph(command([str(go),'list','-mod=readonly','-m','-json','all'],kind+'-module-graph',2*1024**2,mutable=kind),pin['path'],lock[kind+'_modules'])
   guard();sealed={name:held_product(target/name,65536)for name in('go.mod','go.sum')};semantics=workspace_manifests(sealed['go.mod'].body(),sealed['go.sum'].body(),pin['path'],lock[kind+'_modules']);guard()
   for name,item in sealed.items():save(exports/(kind+'-final-'+name),item.body())
   workspace_truth[kind]={'initial_template_original_writer_and_reader_finality':template_truth,'resolved_actual_manifests':{name:{'bytes':item.pin[0],'sha256':item.pin[1],'original_full9':list(item.before)}for name,item in sealed.items()},'manifest_semantics':semantics,'actual_selected_graph':graph,'selected_download_content_checksums_match_lock':True,'source_root_origin_required_and_exactly_matched':True,'dependency_download_evidence':download_evidence,'Go_module_download_and_verify_completed':True,'checksum_database_configuration':'sum.golang.org','fresh_SumDB_lookup_for_every_selected_module_proven':False,'sealed_before_any_build_or_test':True};save(exports/(kind+'-resolved-manifests.json'),C.encoded(workspace_truth[kind]))
  env['GOPROXY']='off';phase='authenticated_package_coverage';package_profiles={}
  profiles=(('age-whole','age','./...',('filippo.io/age','filippo.io/age/cmd/age','filippo.io/age/cmd/age-keygen')),('scanner-whole','scanner','./...',('golang.org/x/vuln','golang.org/x/vuln/cmd/govulncheck')),('age-cli','age','./cmd/age',('filippo.io/age/cmd/age',)),('age-keygen-cli','age','./cmd/age-keygen',('filippo.io/age/cmd/age-keygen',)),('scanner-cli','scanner','./cmd/govulncheck',('golang.org/x/vuln/cmd/govulncheck',)))
  for profile,kind,pattern,required in profiles:
   os.chdir(projects[kind]);package_profiles[profile]=C.package_projection(command([str(go),'list','-mod=readonly','-test=false','-deps','-json='+C.GO_PACKAGE_FIELDS,pattern],profile+'-package-projection',8*1024**2),lock[kind]['path'],lock[kind+'_modules'],pattern,required)
  def projected_dependencies(profile,kind):return{p:v for p,v in package_profiles[profile]['modules'].items()if p not in{lock[kind]['path'],'stdlib'}}
  phase='scanner_build';os.chdir(projects['scanner']);scanner=root/'scanner-output/govulncheck';command([str(go),'build','-mod=readonly','-trimpath','-buildvcs=false','-o',str(scanner),'./cmd/govulncheck'],'scanner-build',2*1024**2);scanner_reader=held_product(scanner,32*1024**2);scanner_info=command([str(go),'version','-m',str(scanner)],'scanner-build-info',65536);C.build_info(scanner_info,'golang.org/x/vuln',lock['scanner_modules'],entry='golang.org/x/vuln/cmd/govulncheck',expected_modules=projected_dependencies('scanner-cli','scanner'))
  phase='complete_upstream_tests';os.chdir(projects['age']);tests={}
  for name,cgo,args in[('upstream-tests-static','0',[]),('upstream-tests-race','1',['-race'])]:tests[name]=C.test_result(command([str(go),'test','-mod=readonly','-json',*args,'./...'],name,16*1024**2,{'CGO_ENABLED':cgo}))
  command([str(go),'mod','verify'],'age-final-module-verify',65536)
  phase='all_module_SCA';source_scans={};module_queries={}
  for profile,kind,pattern,required in profiles:
   source_scans[profile]=C.sca(command([str(scanner),*C.source_arguments(str(projects[kind]),pattern)],profile+'-package-SCA',16*1024**2),'source','package');C.sca_subject(source_scans[profile],projected_dependencies(profile,kind),lock[kind]['path'],roots=package_profiles[profile]['roots'])
  for kind in('age','scanner'):
   subjects={lock[kind]['path']:lock[kind]['version'],**{r['path']:r['version']for r in lock[kind+'_modules']},'stdlib':'v1.27.3'};need(len(subjects)==(19 if kind=='age'else 17),'selected_query_inventory');module_queries[kind+'-selected-module-query']=C.query_modules(command([str(scanner),'-mode=query','-json',*[p+'@'+v for p,v in sorted(subjects.items())]],kind+'-selected-module-advisories',16*1024**2),subjects)
  phase='two_static_CLI_builds';binaries={};graphs={};symbols={};binary_sca={};elfs={}
  for name in('age','age-keygen'):
   path=root/'age-output'/name;command([str(go),'build','-mod=readonly','-trimpath','-buildvcs=false','-ldflags=-X main.Version=v1.3.2+gold1','-o',str(path),'./cmd/'+name],name+'-build',2*1024**2)
   need(command([str(path),'--version'],name+'-version',4096).strip()==b'v1.3.2+gold1','CLI_version');info=command([str(go),'version','-m',str(path)],name+'-build-info',65536);compiled_modules=C.build_info(info,'filippo.io/age',lock['age_modules'],entry='filippo.io/age/cmd/'+name,expected_modules=projected_dependencies(name+'-cli','age'))
   r=held_product(path,32*1024**2);elfs[name]=C.static_amd64(r.body());binaries[name]=r;graphs[name]=C.package_graph(command([str(go),'list','-mod=readonly','-deps','-f','{{.ImportPath}}','./cmd/'+name],name+'-packages',1024**2),'filippo.io/age/cmd/'+name);need(sorted(graphs[name])==package_profiles[name+'-cli']['package_paths'],'binary_package_graph_projection');symbols[name]=command([str(go),'tool','nm',str(path)],name+'-linked-symbols',16*1024**2);binary_sca[name]=C.sca(command([str(scanner),'-mode=binary','-scan=module','-json',str(path)],name+'-binary-SCA',16*1024**2),'binary');C.sca_subject(binary_sca[name],compiled_modules,'filippo.io/age',roots=['filippo.io/age'])
  phase='benign_native_format';payload=b'Trading Bot public native format compatibility fixture\n';save(root/'PUBLIC-PLAINTEXT.txt',payload);cipher=root/'PUBLIC-NATIVE-FORMAT.age';command([str(root/'age-output/age'),'--encrypt','--recipient','age1ql3z7hjy54pw3hyww5ayyfg7zqgvc7w3j2elw8zmrj2kg5sfn9aqmcac8p','--output',str(cipher),str(root/'PUBLIC-PLAINTEXT.txt')],'public-native-encrypt',4096);cipher_reader=held_product(cipher,65536);native=C.native_format(cipher_reader.body());save(exports/'public-native-format.age',cipher_reader.body())
  phase='preserved_vendor_source';vendor_tar=exports/'age132-vendor-source.tar.gz';command(['/usr/bin/tar','-czf',str(vendor_tar),'-C',str(projects['age']),'.'],'vendor-source-archive',65536)
  all_sca={**source_scans,**module_queries,**binary_sca};unresolved=sorted({v for s in all_sca.values()for v in s['unresolved_ids']});proposals=[]
  for id in unresolved:
   osv=next(s['all_OSV'][id]for s in all_sca.values()if id in s['all_OSV']);paths=sorted({i['path']for a in osv.get('affected',[])for i in a.get('ecosystem_specific',{}).get('imports',[])})
   proposals.append({'id':id,'affected_packages':paths,'both_CLI_package_and_symbol_observations':{name:{p:{'package_in_actual_build_graph':p in graphs[name],'linked_symbol_prefix_observed':p.encode()+b'.'in symbols[name]}for p in paths}for name in binaries},'Root_assessment_required':True,'component_not_present_accepted':False})
  provenance={'schema':'age132_managed_tool_build_provenance/1','upstream_commit':lock['age']['origin']['Hash'],'upstream_module_checksum':lock['age']['module_sum'],'go':lock['go'],'module_versions':{r['path']:r['version']for r in lock['age_modules']},'resolved_managed_workspaces':workspace_truth,'authenticated_package_profiles':package_profiles,'complete_selected_module_advisory_queries':{name:{'requested_subjects':value['requested_subjects'],'observed_subjects':value['observed_subjects'],'unresolved_ids':value['unresolved_ids'],'coverage_kind':value['coverage_kind']}for name,value in module_queries.items()},'source_package_tests_included':False,'all_SCA_unresolved':unresolved,'Root_component_absence_proposals':proposals,'tests':tests,'native_format':native,'SDK_archive':archive_truth,'ELF':elfs,'CLI_files':{n:{'bytes':r.before[6],'sha256':r.pin[1],'original_full9':list(r.before)}for n,r in binaries.items()},'held_original_go_source_files':source_files,'no_operational_key_generation_or_private_data_access':True,'runtime_install_allowed':False,'backup_eligibility':False,'security_accepted':False}
  phase='managed_deb_no_hooks';stage=root/'package';owned_directory(stage,0o755,guard)
  for path in('DEBIAN','usr','usr/bin','usr/share','usr/share/doc','usr/share/doc/age'):owned_directory(stage/path,0o755,guard)
  save(stage/'DEBIAN/control',b'Package: age\nVersion: 1.3.2+gold1\nArchitecture: amd64\nMaintainer: Trading Bot Operations <operations@localhost>\nSection: utils\nPriority: optional\nDescription: Managed standalone age security tool; Root qualification required\n',0o644);expected_files={}
  licenses=[];license_bytes=0
  for path in [projects['age']/'LICENSE',root/'sdk/go/LICENSE',*sorted((projects['age']/'vendor').rglob('LICENSE*')),*sorted((projects['age']/'vendor').rglob('COPYING*'))]:
   if not path.is_file():continue
   item=held_product(path,65536);body=item.body();license_bytes+=len(body);need(license_bytes<=2*1024**2,'license_bound');licenses.append(body)
  staged={}
  for name,r in binaries.items():
   body=b''.join(r.read(min(65536,r.before[6]-i),i)for i in range(0,r.before[6],65536));staged[name]=save(stage/'usr/bin'/name,body,C.PACKAGE_FILES['usr/bin/'+name]);expected_files['usr/bin/'+name]=(len(body),sha256(body).hexdigest())
  phase='public_packaged_CLI_roundtrip'
  public_roundtrip=public_packaged_roundtrip(C,root,staged,binaries,raw('public-x25519-test-fixture.txt'),command,save,held_product,guard,payload_end)
  provenance['initial_encrypt_only_native_format_observation']=native
  provenance['native_format']={**native,'CLI_decrypt_roundtrip_proven':public_roundtrip['CLI_decrypt_roundtrip_proven'],'public_packaged_CLI_roundtrip':public_roundtrip}
  save(exports/'BUILD-PROVENANCE.json',C.encoded(provenance))
  docs={'usr/share/doc/age/copyright':b'\n\n'.join(licenses),'usr/share/doc/age/README.Debian':b'Upstream age1.3.2, fixed Go/dependency build. No daemons, maintainer scripts, hooks or dependencies. Runtime/backup qualification is separate.\n','usr/share/doc/age/build-provenance.json':C.encoded(provenance)}
  for name,body in docs.items():save(stage/name,body,C.PACKAGE_FILES[name]);expected_files[name]=(len(body),sha256(body).hexdigest())
  phase='managed_deb_no_hooks'
  package=exports/'age_1.3.2+gold1_amd64.deb';command(['/usr/bin/dpkg-deb','--build','--root-owner-group',str(stage),str(package)],'deb-build',65536);control=C.deb_control(command(['/usr/bin/dpkg-deb','--ctrl-tarfile',str(package)],'deb-control-tar',65536));payload_files=C.deb_data(command(['/usr/bin/dpkg-deb','--fsys-tarfile',str(package)],'deb-data-tar',64*1024**2),expected_files)
  package_reader=held_product(package,64*1024**2)
  C.public_packaged_cli_roundtrip(public_roundtrip,{name:{'bytes':expected_files['usr/bin/'+name][0],'sha256':expected_files['usr/bin/'+name][1]}for name in('age','age-keygen')})
  qualification={'schema':'age132_actual_DEB_public_CLI_roundtrip_binding/1','package':{'bytes':package_reader.pin[0],'sha256':package_reader.pin[1]},'actual_DEB_payload_verified':True,'public_packaged_CLI_roundtrip':public_roundtrip,'provisional_until_outer_Source_and_product_finality':True,'runtime_install_allowed':False,'backup_eligibility':False,'security_accepted':False}
  qualification_reader=save(exports/'PACKAGED-CLI-QUALIFICATION.json',C.encoded(qualification))
  result={'schema':'age132_managed_tool_SOURCE_build_result/1','status':'BUILT_QUARANTINED_PENDING_ROOT_REVIEW'if unresolved or any(x['skips']for x in tests.values())else'BUILT_TESTED_REQUIRES_ROOT_QUALIFICATION','phase':phase,'public_packaged_CLI_qualification':{'path':'PACKAGED-CLI-QUALIFICATION.json','bytes':qualification_reader.pin[0],'sha256':qualification_reader.pin[1]},'packaged_CLI_decrypt_roundtrip_proven':True,'package':{'path':package.name,'bytes':package_reader.pin[0],'sha256':package_reader.pin[1],'control':control,'exact_payload_files':payload_files},'unresolved_module_findings':unresolved,'upstream_skip_events':sum(len(x['skips'])for x in tests.values()),'runtime_install_allowed':False,'backup_eligibility':False,'security_accepted':False}
 except BaseException as e:primary=e
 finally:
  for item in reversed(products):
   try:item.close()
   except BaseException as e:
    final.append('product_original_finality_unknown')
    if primary is None:primary=e
  try:scope.finish()
  except BaseException as e:
   final.append('source_finality_unknown')
   if primary is None:primary=e
 if primary is not None:
  result={'schema':'age132_managed_tool_SOURCE_build_result/1','status':'FAILED','phase':phase,'code':public_code(primary,'build_refused'),'failed_command_truth':failure_fact,'failed_public_output_prefix':failure_output,'finalizer_codes':final,'runtime_install_allowed':False,'backup_eligibility':False,'security_accepted':False}
 result.update(original_Source_FDs_physically_closed=len(scope.rows)if scope.finished and not final else None,original_product_FDs_physically_closed=sum(getattr(x,'physical_EBADF',False)and getattr(x,'closed',False)and not getattr(x,'unknown',True)for x in products),source_finality_proven=scope.finished and not final,actual_install_or_backup_performed=False)
 if exports is not None:
  try:publish_result(M,exports/'RESULT.json',result,scope.rows,scope.end)
  except BaseException as error:
   result=publication_failure(result,error);print(json.dumps(result,sort_keys=True));return 1
 print(json.dumps(result,sort_keys=True));return 1 if primary is not None else(2 if result['status']=='BUILT_QUARANTINED_PENDING_ROOT_REVIEW'else 0)
def main():
 if len(sys.argv)!=3 or sys.argv[1]!='--build-managed-age132' or re.fullmatch('[a-f0-9]{64}',sys.argv[2])is None:
  print('{"status":"UNARMED","actual_actions":false,"runtime_install_allowed":false,"backup_eligibility":false}');return 0
 try:return execute(sys.argv[2])
 except BaseException:
  print('{"status":"REFUSED_BEFORE_CONTEXT","runtime_install_allowed":false,"backup_eligibility":false,"security_accepted":false}');return 1
if __name__=='__main__':raise SystemExit(main())
