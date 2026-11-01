"""Explicit hosted-only managed age build; default inert, never install/backup."""
from pathlib import Path,PurePosixPath
from types import ModuleType
from hashlib import sha256
from uuid import uuid4
import argparse,errno,fcntl,io,json,os,re,shutil,stat,sys,tarfile,time,urllib.request
HERE=Path(__file__).absolute().parent
IO_PIN=(8831,'b2e6159d62ded26c81c5a72ad9dbda73f6029719451ff2b27d7edeefeae7b568')
PINS={'source_io.py': (8831, 'b2e6159d62ded26c81c5a72ad9dbda73f6029719451ff2b27d7edeefeae7b568'), 'host_io_original.py': (53351, 'fc9fc8e4ae7fe1af75fd0b4c63fedb0ebfa5820148bb311c5eaafcaaa5bbbb82'), 'contracts.py': (11551, 'bac0d626957c40659d5dfd79b6d5f5bf05773ba6e8ceef526e0926d84f9dd8f5'), 'inputs.json': (14758, '9cfa5c3dd9e3faa6c85201c1337933aa77a2115e6b44823daf4f36d4488ad642'), 'age.go.mod': (619, '53e9fdd9fd328e14aab3eebe759c5ab048c1a7a8bbacbb8a853556b26efedc9c'), 'age.go.sum': (2823, '1f504fde4a3f8cbc4c159ac4e75f17cee7015b1d74d555feb79e06a34365eb75'), 'scanner.go.mod': (624, '5c3d2dd42ed9e7fa9d0025334c7edcf8bcf5fff788b0a836cbd746f444690b2c'), 'scanner.go.sum': (2625, '11a4fa1753df1bb15e5bc4fba3192eeab54764274f213c77f034c916217d7bdb')}
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
def publish_result(M,path,result,source_rows,deadline):
 """Post-Source-scope public receipt; retain reader across original writer close."""
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
  body=json.dumps(result,sort_keys=True).encode()+b'\n';need(0<len(body)<=65536,'result_bound');parents=tuple(M.parent_frame(path));need(not path.exists(),'result_exists')
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
def execute(source_sha):
 hosted()
 need(set(PINS)=={'source_io.py','host_io_original.py','contracts.py','inputs.json','age.go.mod','age.go.sum','scanner.go.mod','scanner.go.sum'},'source_inventory')
 IO=bootstrap();scope=IO.Scope(3720);primary=None;final=[];phase='source';exports=None;result=None;products=[];failure_fact=None
 started=time.monotonic();end=started+3600;payload_end=end-90
 try:
  scope.open(Path(__file__).absolute(),(Path(__file__).stat().st_size,source_sha),131072);held={name:scope.open(HERE/name,pin,262144)for name,pin in PINS.items()}
  def raw(name):
   r=held[name];return b''.join(r.read(min(65536,r.before[6]-i),i)for i in range(0,r.before[6],65536))
  C=module('contracts',raw('contracts.py'),HERE/'contracts.py');M=module('transport',raw('host_io_original.py'),HERE/'host_io_original.py');lock=C.validate_lock(C.document(raw('inputs.json')))
  def guard():
   need(time.monotonic()<payload_end,'build_deadline');scope.check()
   for item in products:item.check()
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
  def command(argv,name,maximum=8*1024**2,extra=None):
   nonlocal failure_fact
   guard()
   try:body=M.run(argv,env={**env,**(extra or{})},deadline=payload_end,maximum=maximum,guard=guard)
   except BaseException as e:
    fact=getattr(e,'public_command_failure',None)
    if type(fact)is dict:
     failure_fact={k:fact[k]for k in('actual_returncode','observed_output_bytes','actual_pipe_closed','stdout_eof_observed','maximum_output_bytes')};failure_fact['command_name']=name
     if type(fact.get('captured_output'))is bytes and fact['captured_output']:save(exports/(name+'.failed-public-output.bin'),fact['captured_output'])
    raise
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
   pin=lock[kind];download=C.download_record(command([str(go),'mod','download','-json',pin['path']+'@'+pin['version']],kind+'-source-download',65536),pin);directory=Path(download['Dir']);need(directory.resolve(strict=True)==directory and directory.is_relative_to(root/'modcache'),'module_directory')
   target=root/(kind+'-source');clone_public_module(directory,target);need(target.resolve(strict=True)==target,'source_copy');projects[kind]=target;module_downloads[kind]=download;source_files[kind]=[]
   for path in sorted(target.rglob('*.go')):
    item=held_product(path,4*1024**2,allow_empty=True);source_files[kind].append({'path':str(path.relative_to(target)),'bytes':item.pin[0],'sha256':item.pin[1],'original_full9':list(item.before)})
   for name in('go.mod','go.sum'):
    path=target/name
    if path.exists():save(exports/(kind+'-original-'+name),path.read_bytes());path.unlink()
    save(path,raw(kind+'.'+name))
   os.chdir(target);downloads=C.documents(command([str(go),'mod','download','-json','all'],kind+'-dependency-download',2*1024**2));expected={r['path']:r for r in lock[kind+'_modules']};seen=set()
   for row in downloads:
    need(row.get('Path')in expected and row['Path']not in seen,'dependency_download_inventory');C.download_record(C.encoded(row),expected[row['Path']]);seen.add(row['Path'])
   need(seen==set(expected),'dependency_download_inventory');command([str(go),'mod','verify'],kind+'-module-verify',65536);C.graph(command([str(go),'list','-m','-json','all'],kind+'-module-graph',2*1024**2),pin['path'],lock[kind+'_modules'])
  env['GOPROXY']='off';phase='scanner_build';os.chdir(projects['scanner']);scanner=root/'scanner-output/govulncheck';command([str(go),'build','-mod=readonly','-trimpath','-buildvcs=false','-o',str(scanner),'./cmd/govulncheck'],'scanner-build',2*1024**2);scanner_reader=held_product(scanner,32*1024**2);scanner_info=command([str(go),'version','-m',str(scanner)],'scanner-build-info',65536);C.build_info(scanner_info,'golang.org/x/vuln',lock['scanner_modules'])
  phase='complete_upstream_tests';os.chdir(projects['age']);tests={}
  for name,cgo,args in[('upstream-tests-static','0',[]),('upstream-tests-race','1',['-race'])]:tests[name]=C.test_result(command([str(go),'test','-mod=readonly','-json',*args,'./...'],name,16*1024**2,{'CGO_ENABLED':cgo}))
  command([str(go),'mod','verify'],'age-final-module-verify',65536)
  phase='all_module_SCA';source_sca=C.sca(command([str(scanner),'-C',str(projects['age']),'-scan=module','-json'],'age-module-SCA',16*1024**2),'source');scanner_sca=C.sca(command([str(scanner),'-C',str(projects['scanner']),'-scan=module','-json'],'scanner-module-SCA',16*1024**2),'source');published_age_sca=C.query_age(command([str(scanner),'-mode=query','-json','filippo.io/age@v1.3.2'],'published-age-module-SCA',16*1024**2));C.sca_subject(source_sca,{r['path']:r['version']for r in lock['age_modules']},'filippo.io/age');C.sca_subject(scanner_sca,{r['path']:r['version']for r in lock['scanner_modules']},'golang.org/x/vuln')
  phase='two_static_CLI_builds';binaries={};graphs={};symbols={};binary_sca={};elfs={}
  for name in('age','age-keygen'):
   path=root/'age-output'/name;command([str(go),'build','-mod=readonly','-trimpath','-buildvcs=false','-ldflags=-X main.Version=v1.3.2+gold1','-o',str(path),'./cmd/'+name],name+'-build',2*1024**2)
   need(command([str(path),'--version'],name+'-version',4096).strip()==b'v1.3.2+gold1','CLI_version');info=command([str(go),'version','-m',str(path)],name+'-build-info',65536);compiled_modules=C.build_info(info,'filippo.io/age',lock['age_modules'])
   r=held_product(path,32*1024**2);elfs[name]=C.static_amd64(r.body());binaries[name]=r;graphs[name]=C.package_graph(command([str(go),'list','-mod=readonly','-deps','-f','{{.ImportPath}}','./cmd/'+name],name+'-packages',1024**2),'filippo.io/age/cmd/'+name);symbols[name]=command([str(go),'tool','nm',str(path)],name+'-linked-symbols',16*1024**2);binary_sca[name]=C.sca(command([str(scanner),'-mode=binary','-scan=module','-json',str(path)],name+'-binary-SCA',16*1024**2),'binary');C.sca_subject(binary_sca[name],compiled_modules,'filippo.io/age')
  phase='benign_native_format';payload=b'Trading Bot public native format compatibility fixture\n';save(root/'PUBLIC-PLAINTEXT.txt',payload);cipher=root/'PUBLIC-NATIVE-FORMAT.age';command([str(root/'age-output/age'),'--encrypt','--recipient','age1ql3z7hjy54pw3hyww5ayyfg7zqgvc7w3j2elw8zmrj2kg5sfn9aqmcac8p','--output',str(cipher),str(root/'PUBLIC-PLAINTEXT.txt')],'public-native-encrypt',4096);cipher_reader=held_product(cipher,65536);native=C.native_format(cipher_reader.body());save(exports/'public-native-format.age',cipher_reader.body())
  phase='preserved_vendor_source';command([str(go),'mod','vendor'],'vendor-source',1024**2);vendor_tar=exports/'age132-vendor-source.tar.gz';command(['/usr/bin/tar','-czf',str(vendor_tar),'-C',str(projects['age']),'.'],'vendor-source-archive',65536)
  all_sca={'source':source_sca,'scanner':scanner_sca,'published_age':published_age_sca,**binary_sca};unresolved=sorted({v for s in all_sca.values()for v in s['unresolved_ids']});proposals=[]
  for id in unresolved:
   osv=next(s['all_OSV'][id]for s in all_sca.values()if id in s['all_OSV']);paths=sorted({i['path']for a in osv.get('affected',[])for i in a.get('ecosystem_specific',{}).get('imports',[])})
   proposals.append({'id':id,'affected_packages':paths,'both_CLI_package_and_symbol_observations':{name:{p:{'package_in_actual_build_graph':p in graphs[name],'linked_symbol_prefix_observed':p.encode()+b'.'in symbols[name]}for p in paths}for name in binaries},'Root_assessment_required':True,'component_not_present_accepted':False})
  provenance={'schema':'age132_managed_tool_build_provenance/1','upstream_commit':lock['age']['origin']['Hash'],'upstream_module_checksum':lock['age']['module_sum'],'go':lock['go'],'module_versions':{r['path']:r['version']for r in lock['age_modules']},'all_SCA_unresolved':unresolved,'Root_component_absence_proposals':proposals,'tests':tests,'native_format':native,'SDK_archive':archive_truth,'ELF':elfs,'CLI_files':{n:{'bytes':r.before[6],'sha256':r.pin[1],'original_full9':list(r.before)}for n,r in binaries.items()},'held_original_go_source_files':source_files,'no_operational_key_generation_or_private_data_access':True,'runtime_install_allowed':False,'backup_eligibility':False,'security_accepted':False};save(exports/'BUILD-PROVENANCE.json',C.encoded(provenance))
  phase='managed_deb_no_hooks';stage=root/'package';owned_directory(stage,0o755,guard)
  for path in('DEBIAN','usr','usr/bin','usr/share','usr/share/doc','usr/share/doc/age'):owned_directory(stage/path,0o755,guard)
  save(stage/'DEBIAN/control',b'Package: age\nVersion: 1.3.2+gold1\nArchitecture: amd64\nMaintainer: Trading Bot Operations <operations@localhost>\nSection: utils\nPriority: optional\nDescription: Managed standalone age security tool; Root qualification required\n',0o644);expected_files={}
  licenses=[];license_bytes=0
  for path in [projects['age']/'LICENSE',root/'sdk/go/LICENSE',*sorted((projects['age']/'vendor').rglob('LICENSE*')),*sorted((projects['age']/'vendor').rglob('COPYING*'))]:
   if not path.is_file():continue
   item=held_product(path,65536);body=item.body();license_bytes+=len(body);need(license_bytes<=2*1024**2,'license_bound');licenses.append(body)
  docs={'usr/share/doc/age/copyright':b'\n\n'.join(licenses),'usr/share/doc/age/README.Debian':b'Upstream age1.3.2, fixed Go/dependency build. No daemons, maintainer scripts, hooks or dependencies. Runtime/backup qualification is separate.\n','usr/share/doc/age/build-provenance.json':C.encoded(provenance)}
  for name,r in binaries.items():docs['usr/bin/'+name]=b''.join(r.read(min(65536,r.before[6]-i),i)for i in range(0,r.before[6],65536))
  for name,body in docs.items():save(stage/name,body,C.PACKAGE_FILES[name]);expected_files[name]=(len(body),sha256(body).hexdigest())
  package=exports/'age_1.3.2+gold1_amd64.deb';command(['/usr/bin/dpkg-deb','--build','--root-owner-group',str(stage),str(package)],'deb-build',65536);control=C.deb_control(command(['/usr/bin/dpkg-deb','--ctrl-tarfile',str(package)],'deb-control-tar',65536));payload_files=C.deb_data(command(['/usr/bin/dpkg-deb','--fsys-tarfile',str(package)],'deb-data-tar',64*1024**2),expected_files)
  package_reader=held_product(package,64*1024**2)
  result={'schema':'age132_managed_tool_SOURCE_build_result/1','status':'BUILT_QUARANTINED_PENDING_ROOT_REVIEW'if unresolved or any(x['skips']for x in tests.values())else'BUILT_TESTED_REQUIRES_ROOT_QUALIFICATION','phase':phase,'package':{'path':package.name,'bytes':package_reader.pin[0],'sha256':package_reader.pin[1],'control':control,'exact_payload_files':payload_files},'unresolved_module_findings':unresolved,'upstream_skip_events':sum(len(x['skips'])for x in tests.values()),'runtime_install_allowed':False,'backup_eligibility':False,'security_accepted':False}
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
  value=str(primary);result={'schema':'age132_managed_tool_SOURCE_build_result/1','status':'FAILED','phase':phase,'code':value if re.fullmatch('[A-Za-z0-9_]{1,100}',value)else'build_refused','failed_command_truth':failure_fact,'finalizer_codes':final,'runtime_install_allowed':False,'backup_eligibility':False,'security_accepted':False}
 result.update(original_Source_FDs_physically_closed=len(scope.rows)if scope.finished and not final else None,original_product_FDs_physically_closed=sum(getattr(x,'physical_EBADF',False)and getattr(x,'closed',False)and not getattr(x,'unknown',True)for x in products),source_finality_proven=scope.finished and not final,actual_install_or_backup_performed=False)
 if exports is not None:
  try:publish_result(M,exports/'RESULT.json',result,scope.rows,scope.end)
  except BaseException as error:
   code=str(error);result={'schema':'age132_managed_tool_SOURCE_build_result/1','status':'FAILED','phase':'final_RESULT_publication','code':code if re.fullmatch('[A-Za-z0-9_]{1,100}',code)else'result_publication_refused','prior_build_status':result['status'],'final_publication_truth':getattr(error,'age_result_publication_truth',None),'runtime_install_allowed':False,'backup_eligibility':False,'security_accepted':False,'actual_install_or_backup_performed':False};print(json.dumps(result,sort_keys=True));return 1
 print(json.dumps(result,sort_keys=True));return 1 if primary is not None else(2 if result['status']=='BUILT_QUARANTINED_PENDING_ROOT_REVIEW'else 0)
def main():
 if len(sys.argv)!=3 or sys.argv[1]!='--build-managed-age132' or re.fullmatch('[a-f0-9]{64}',sys.argv[2])is None:
  print('{"status":"UNARMED","actual_actions":false,"runtime_install_allowed":false,"backup_eligibility":false}');return 0
 try:return execute(sys.argv[2])
 except BaseException:
  print('{"status":"REFUSED_BEFORE_CONTEXT","runtime_install_allowed":false,"backup_eligibility":false,"security_accepted":false}');return 1
if __name__=='__main__':raise SystemExit(main())
