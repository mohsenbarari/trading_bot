"""UNARMED never-started public-image export proposal; no action on import.

The authenticated outer controller supplies its admitted image/control, frozen
pure inventory module/adapter, and private C.ProofKeeper instance. No capability,
image mode, native probe, original smoke assertion or raw finding is changed.
"""
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
import errno,fcntl,json,os,re,selectors,stat,subprocess,sys,time

EXECUTION_ARMED = True
MAX_CHUNK=1024**2
MAX_EXPORT=2*1024**3
MAX_STDERR=8192
MAX_REPORT=4*1024**2
MAX_INVENTORY_SECONDS=600
STAGES=frozenset(('create','preinspect','export','postinspect'))
ZERO_TIME='0001-01-01T00:00:00Z'
PURE_INVENTORY_SHA256='e8171fb6d28a5b3e938e0136dab230c13a9e864f66aad4c2727878e22a3ea73f'
PUBLIC_FAILURE_CODES=frozenset(('flat_export_unarmed','flat_export_contract','flat_export_metadata','flat_export_changed',
 'flat_export_bound','flat_export_write','flat_export_readback','flat_export_not_frozen','flat_export_close',
 'flat_export_stage','flat_export_timeout','flat_export_stdout_bound','flat_export_stderr_bound',
 'flat_export_client','flat_export_pipe_close','flat_export_receipt','flat_export_container','flat_export_scope',
 'flat_export_io_finality','flat_export_unproven','flat_export_reader_changed','flat_export_reader_missing','flat_export_close_unknown','flat_export_pipe_changed','flat_export_borrow_changed','flat_export_selector_close_unknown','flat_export_borrow_held'))

def need(ok,code):
 if not ok:raise RuntimeError(code)
def identity(s):return(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def encode(value):return(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
def hex64(value):return type(value)is str and re.fullmatch('[a-f0-9]{64}',value)is not None
def image_id(value):return type(value)is str and re.fullmatch('sha256:[a-f0-9]{64}',value)is not None
def document(raw):
 need(type(raw)is bytes and 0<len(raw)<=16384,'flat_export_receipt')
 def pairs(rows):
  value={}
  for key,item in rows:need(key not in value,'flat_export_receipt');value[key]=item
  return value
 return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:need(False,'flat_export_receipt'))

class ExportIOFailure(RuntimeError):
 def __init__(self,errors):super().__init__('flat_export_io_finality');self.errors=tuple(errors)
def causes(error):return error.errors if type(error)is ExportIOFailure else(error,)
def fail(errors):
 if len(errors)==1:raise errors[0]
 raise ExportIOFailure(errors)from errors[0]
def public_error(error):
 code=error.args[0]if isinstance(error,RuntimeError)and len(error.args)==1 and type(error.args[0])is str else None
 return{'code':code if code in PUBLIC_FAILURE_CODES else'flat_export_unproven','exception_class':type(error).__name__ if type(error).__name__ in ('RuntimeError','OSError','TimeoutError','PermissionError','FileNotFoundError','ExportIOFailure')else'unclassified_exception','os_errno':error.errno if isinstance(error,OSError)else None}
def guarded(check,function,*args):
 errors=[];value=None
 try:check();value=function(*args)
 except BaseException as error:errors.extend(causes(error))
 finally:
  try:check()
  except BaseException as error:errors.extend(causes(error))
 if errors:fail(errors)
 return value

def failed_forward(function):
 def call(self,*args,**kwargs):
  if self.errors:fail(self.errors)
  try:return function(self,*args,**kwargs)
  except BaseException as error:self.note(error);raise
 return call

class Borrowed:
 """Published acquisition prefix and one original duplicate/stream owner."""
 def __init__(self,output):
  self.output=output;self.fd=None;self.original=None;self.before=output.before;self.flags=None
  self.stream=None;self.original_stream=None;self.closer=None;self.close_attempted=False;self.closed=False;self.admitted=False;self.held=False;self.fdopen_attempted=False
 def acquire(self):
  self.fd=os.dup(self.output.original_reader);self.original=self.fd
  self.flags=(fcntl.fcntl(self.original,fcntl.F_GETFL),fcntl.fcntl(self.original,fcntl.F_GETFD))
  need(self.flags[0]&os.O_ACCMODE==os.O_RDONLY and self.flags[1]&fcntl.FD_CLOEXEC,'flat_export_borrow_changed');self.check()
  self.fdopen_attempted=True;self.stream=os.fdopen(self.original,'rb');self.original_stream=self.stream;self.closer=self.stream.close;self.check();self.admitted=True
 def check(self):
  need(not self.closed and self.original is not None and self.fd==self.original and identity(os.fstat(self.original))==self.before==self.output.before and(fcntl.fcntl(self.original,fcntl.F_GETFL),fcntl.fcntl(self.original,fcntl.F_GETFD))==self.flags,'flat_export_borrow_changed')
  need(self.original_stream is None or self.stream is self.original_stream and self.original_stream.fileno()==self.original,'flat_export_borrow_changed')
 def close(self):
  need(not self.close_attempted,'flat_export_close_unknown');self.close_attempted=True;errors=[]
  if self.original is None:self.closed=True;return
  metadata_ok=False
  try:self.check();metadata_ok=True
  except BaseException as error:errors.extend(causes(error))
  # An FD number can be reused by a failing fdopen or caller. Metadata alone
  # never creates original open-description ownership. Only an admitted bound
  # stream with fresh exact metadata/flags has the successful close authority.
  already_closed=False
  try:os.fstat(self.original)
  except OSError as error:
   if error.errno==errno.EBADF:already_closed=True;self.closed=True
   else:errors.append(error)
  if not already_closed:
   if not metadata_ok or not self.admitted or self.original_stream is None or self.closer is None:
    self.held=True;errors.append(RuntimeError('flat_export_borrow_held'))
   else:
    try:self.closer()
    except BaseException as error:errors.extend(causes(error))
    finally:
     try:os.fstat(self.original)
     except OSError as error:
      if error.errno==errno.EBADF:self.closed=True
      else:errors.append(error)
     else:self.held=True;errors.append(RuntimeError('flat_export_close_unknown'))
  if errors:fail(errors)

class ExportFile:
 """One source-owned EXCL streaming output; original writer and RO loan remain.

 Only successful writes update full9/count/hash. No source/FD fact survives a
 payload boundary without fresh validation. The independent reader is acquired
 before writer close, retained through writer retirement and closed last.
 """
 def __init__(self,C,keeper,root):
  self.C=C;self.path=Path(root)/'whole-image-export.tar';self.parents=None
  self.fd=None;self.original_writer=None;self.writer_flags=None;self.writer_attempted=False;self.writer_closed=False
  self.reader=None;self.original_reader=None;self.reader_flags=None;self.reader_attempted=False;self.reader_close_attempted=False;self.reader_closed=False
  self.before=None;self.created=None;self.expected_sha256=None;self.count=0;self.digest=sha256();self.frozen=False;self.errors=[];self.loans=[];self.original_loans=self.loans
  need(C.PROOF_KEEPER is keeper and keeper.root==Path(root)and not keeper.closed,'flat_export_contract');keeper._admit(self.path)
  self.parents=C._ProofParents(self.path);keeper.records.append(self)
  try:
   self.fd=os.open(self.path.name,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=self.parents.fd);self.original_writer=self.fd
   self.before=identity(os.fstat(self.original_writer));self.created=self.before[:6]
   self.writer_flags=(fcntl.fcntl(self.original_writer,fcntl.F_GETFL),fcntl.fcntl(self.original_writer,fcntl.F_GETFD))
   need(self.before[2:7]==(0o100600,os.geteuid(),os.getegid(),1,0)and self.writer_flags[0]&os.O_ACCMODE==os.O_RDWR and self.writer_flags[1]&fcntl.FD_CLOEXEC,'flat_export_metadata');self.check()
  except BaseException as error:self.note(error);raise
 def note(self,error):
  for cause in causes(error):
   if all(cause is not old for old in self.errors):self.errors.append(cause)
 def named(self):
  self.parents.named()
  need(self.before is not None and self.path.resolve(strict=True)==self.path and identity(self.path.lstat())==self.before,'flat_export_changed')
 def check(self):
  self.parents.check();self.named()
  if self.original_writer is not None and not self.writer_closed:
   need(self.fd==self.original_writer and identity(os.fstat(self.original_writer))==self.before and(fcntl.fcntl(self.original_writer,fcntl.F_GETFL),fcntl.fcntl(self.original_writer,fcntl.F_GETFD))==self.writer_flags and os.lseek(self.original_writer,0,os.SEEK_CUR)==self.count,'flat_export_changed')
  if self.original_reader is not None and not self.reader_closed:
   need(self.reader==self.original_reader and identity(os.fstat(self.original_reader))==self.before and(fcntl.fcntl(self.original_reader,fcntl.F_GETFL),fcntl.fcntl(self.original_reader,fcntl.F_GETFD))==self.reader_flags,'flat_export_reader_changed')
  if self.writer_closed:need(self.original_reader is not None,'flat_export_reader_missing')
  need(self.loans is self.original_loans,'flat_export_borrow_changed')
  for loan in self.original_loans:
   if not loan.closed and loan.original is not None:loan.check()
 def guard(self,check):
  errors=[]
  for function in(check,self.check):
   try:function()
   except BaseException as error:self.note(error);errors.extend(causes(error))
  if errors:fail(errors)
 def call(self,check,function,*args):
  if self.errors:fail(self.errors)
  errors=[];value=None
  try:self.guard(check);value=function(*args)
  except BaseException as error:self.note(error);errors.extend(causes(error))
  finally:
   try:self.guard(check)
   except BaseException as error:self.note(error);errors.extend(causes(error))
  if errors:fail(errors)
  return value
 @failed_forward
 def write(self,raw,check):
  need(type(raw)is bytes and 0<len(raw)<=MAX_CHUNK and not self.frozen,'flat_export_write')
  need(self.count+len(raw)<=MAX_EXPORT,'flat_export_bound');view=memoryview(raw)
  while view:
   def actual():
    n=os.write(self.original_writer,view);need(type(n)is int and 0<n<=len(view),'flat_export_write')
    self.count+=n;self.digest.update(view[:n]);after=identity(os.fstat(self.original_writer))
    need(after[:6]==self.created and after[6]==self.count and identity(self.path.lstat())==after,'flat_export_changed');self.before=after;return n
   n=self.call(check,actual);view=view[n:]
 def retain_reader(self,check):
  need(not self.reader_attempted,'flat_export_reader_missing');self.reader_attempted=True
  self.reader=os.open(self.path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,dir_fd=self.parents.fd);self.original_reader=self.reader
  self.reader_flags=(fcntl.fcntl(self.original_reader,fcntl.F_GETFL),fcntl.fcntl(self.original_reader,fcntl.F_GETFD))
  need(self.reader_flags[0]&os.O_ACCMODE==os.O_RDONLY and self.reader_flags[1]&fcntl.FD_CLOEXEC,'flat_export_reader_changed');self.guard(check)
 @failed_forward
 def freeze(self,check,end):
  need(not self.frozen and 0<self.count<=MAX_EXPORT and self.count%512==0,'flat_export_bound')
  def scope():check();need(time.monotonic()<end,'flat_export_timeout')
  self.call(scope,os.fsync,self.original_writer);self.call(scope,self.retain_reader,scope);snapshot=self.before;digest=sha256();offset=0
  while offset<self.count:
   raw=self.call(scope,os.pread,self.original_reader,min(MAX_CHUNK,self.count-offset),offset);need(raw,'flat_export_readback');offset+=len(raw);digest.update(raw)
  need(self.call(scope,os.pread,self.original_reader,1,self.count)==b''and digest.hexdigest()==self.digest.hexdigest()and self.before==snapshot,'flat_export_readback')
  self.expected_sha256=self.digest.hexdigest();self.frozen=True;self.guard(scope);return self.binding()
 @failed_forward
 def binding(self):
  need(self.frozen,'flat_export_not_frozen');return{'bytes':self.count,'sha256':self.expected_sha256,'full9':list(self.before)}
 @contextmanager
 def borrow(self,check):
  if self.errors:fail(self.errors)
  loan=Borrowed(self);self.original_loans.append(loan);errors=[]
  try:
   need(self.frozen and self.original_reader is not None,'flat_export_not_frozen');self.guard(check)
   loan.acquire();self.guard(check);loan.check();loan.original_stream.seek(0);self.guard(check);loan.check();yield loan.original_stream
  except BaseException as error:self.note(error);errors.extend(causes(error))
  finally:
   try:self.guard(check)
   except BaseException as error:self.note(error);errors.extend(causes(error))
   try:loan.close()
   except BaseException as error:self.note(error);errors.extend(causes(error))
   # Closed loan callbacks are retired. Original RO/writer/name/source checks
   # still run after the duplicate's actual one-shot physical finalizer.
   try:self.guard(check)
   except BaseException as error:self.note(error);errors.extend(causes(error))
   if errors:fail(errors)
 def close(self):
  errors=list(self.errors)
  def attempt(function):
   try:function()
   except BaseException as error:self.note(error);errors.extend(causes(error))
  if self.original_writer is not None and not self.writer_closed:
   need(not self.writer_attempted,'flat_export_close_unknown');self.writer_attempted=True;attempt(self.check)
   if not self.reader_attempted:attempt(lambda:self.retain_reader(lambda:None))
   def writer_close():
    os.close(self.original_writer)
    try:os.fstat(self.original_writer)
    except OSError as error:need(error.errno==errno.EBADF,'flat_export_close_unknown')
    else:raise RuntimeError('flat_export_close_unknown')
    self.fd=None;self.writer_closed=True
   attempt(writer_close);attempt(self.check)
  if self.original_reader is not None and not self.reader_closed:
   need(not self.reader_close_attempted,'flat_export_close_unknown');self.reader_close_attempted=True;attempt(self.check)
   def reader_close():
    os.close(self.original_reader)
    try:os.fstat(self.original_reader)
    except OSError as error:need(error.errno==errno.EBADF,'flat_export_close_unknown')
    else:raise RuntimeError('flat_export_close_unknown')
    self.reader=None;self.reader_closed=True
   attempt(reader_close)
  attempt(self.parents.close);attempt(self.named)
  if errors:fail(errors)

class Pipe:
 """Original pipe description, fixed7 FIFO facts/flags/capacity until close."""
 def __init__(self,stream,capacity):
  self.stream=stream;self.fd=stream.fileno();os.set_blocking(self.fd,False)
  self.before=identity(os.fstat(self.fd))[:7];self.flags=(fcntl.fcntl(self.fd,fcntl.F_GETFL),fcntl.fcntl(self.fd,fcntl.F_GETFD));self.capacity=fcntl.fcntl(self.fd,fcntl.F_GETPIPE_SZ)
  need(stat.S_ISFIFO(self.before[2])and self.before[3:6]==(os.geteuid(),os.getegid(),1)and self.flags[0]&os.O_ACCMODE==os.O_RDONLY and self.flags[1]&fcntl.FD_CLOEXEC and self.capacity==capacity,'flat_export_pipe_changed');self.closed=False;self.close_attempted=False;self.check()
 def check(self):
  need(not self.closed and self.stream.fileno()==self.fd and identity(os.fstat(self.fd))[:7]==self.before and(fcntl.fcntl(self.fd,fcntl.F_GETFL),fcntl.fcntl(self.fd,fcntl.F_GETFD))==self.flags and fcntl.fcntl(self.fd,fcntl.F_GETPIPE_SZ)==self.capacity,'flat_export_pipe_changed')
 def close(self):
  need(not self.close_attempted,'flat_export_close_unknown');self.close_attempted=True;errors=[]
  try:self.check()
  except BaseException as error:errors.extend(causes(error))
  try:
   self.stream.close()
   try:os.fstat(self.fd)
   except OSError as error:need(error.errno==errno.EBADF,'flat_export_close_unknown')
   else:raise RuntimeError('flat_export_close_unknown')
   self.closed=True
  except BaseException as error:errors.extend(causes(error))
  if errors:fail(errors)

def create_args(old,name,image):
 return['create','--name',name,'--label',old.LABEL+'='+old.owner(),'--pull=never',
  '--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges',
  '--user=0:0','--memory=512m','--memory-swap=512m','--cpus=1','--pids-limit=64',
  '--restart=no','--ulimit','core=0','--entrypoint','/bin/true',image,'--inventory-never-started']

def inspect_template(old):
 return '{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Image}},"config_image":{{json .Config.Image}},"owner":{{json (index .Config.Labels "'+old.LABEL+'")}},"user":{{json .Config.User}},"entrypoint":{{json .Config.Entrypoint}},"cmd":{{json .Config.Cmd}},"volumes":{{json (index .Config "Volumes")}},"readonly":{{json .HostConfig.ReadonlyRootfs}},"privileged":{{json .HostConfig.Privileged}},"network":{{json .HostConfig.NetworkMode}},"networks":{{json .NetworkSettings.Networks}},"caps_add":{{json .HostConfig.CapAdd}},"caps_drop":{{json .HostConfig.CapDrop}},"security":{{json .HostConfig.SecurityOpt}},"memory":{{json .HostConfig.Memory}},"memory_swap":{{json .HostConfig.MemorySwap}},"nano_cpus":{{json .HostConfig.NanoCpus}},"pids":{{json .HostConfig.PidsLimit}},"tmpfs":{{json (index .HostConfig "Tmpfs")}},"mounts":{{json .Mounts}},"binds":{{json (index .HostConfig "Binds")}},"host_mounts":{{json (index .HostConfig "Mounts")}},"restart_policy":{{json .HostConfig.RestartPolicy.Name}},"auto_remove":{{json .HostConfig.AutoRemove}},"status":{{json .State.Status}},"running":{{json .State.Running}},"paused":{{json .State.Paused}},"restarting":{{json .State.Restarting}},"dead":{{json .State.Dead}},"pid":{{json .State.Pid}},"exit_code":{{json .State.ExitCode}},"oom_killed":{{json .State.OOMKilled}},"started_at":{{json .State.StartedAt}},"finished_at":{{json .State.FinishedAt}},"restart_count":{{json .RestartCount}}}'

def admit_container(value,old,name,image,cid):
 expected={'id':cid,'name':'/'+name,'image':image,'config_image':image,'owner':old.owner(),
  'user':'0:0','entrypoint':['/bin/true'],'cmd':['--inventory-never-started'],'readonly':True,
  'privileged':False,'network':'none','caps_drop':['ALL'],'security':['no-new-privileges'],
  'mounts':[],'restart_policy':'no','auto_remove':False,'status':'created','running':False,
  'paused':False,'restarting':False,'dead':False,'oom_killed':False,'started_at':ZERO_TIME,'finished_at':ZERO_TIME}
 numbers=(('memory',512*1024**2),('memory_swap',512*1024**2),('nano_cpus',10**9),('pids',64),('pid',0),('exit_code',0),('restart_count',0))
 need(type(value)is dict and set(value)==set(expected)|{key for key,_ in numbers}|{'networks','caps_add','volumes','tmpfs','binds','host_mounts'}
  and all(type(value.get(k))is type(v)and value[k]==v for k,v in expected.items()),'flat_export_container')
 for key,number in numbers:
  need(type(value.get(key))is int and value[key]==number,'flat_export_container')
 need(type(value.get('networks'))is dict and set(value['networks'])<={'none'}
  and value.get('caps_add')in(None,[])and value.get('volumes')in(None,{})and value.get('tmpfs')in(None,{})
  and value.get('binds')in(None,[])and value.get('host_mounts')in(None,[]),'flat_export_container')
 return value

def cli(ctx,old,stage,*,image,cid=None,output=None):
 """Source-owned gold2 successor; same limits, larger bounded I/O operations."""
 need(stage in STAGES and old.DOCKER==['/usr/bin/docker','--host=unix:///var/run/docker.sock']and image_id(image),'flat_export_stage');name='app-ci-flat-acl-'+old.owner()
 if stage=='create':
  need(cid is None and output is None,'flat_export_stage');args=create_args(old,name,image);limit,seconds=256,30
 else:
  need(hex64(cid),'flat_export_stage')
  if stage in('preinspect','postinspect'):
   need(output is None,'flat_export_stage');args=['container','inspect','--format',inspect_template(old),cid];limit,seconds=16384,30
  else:
   need(type(output)is ExportFile and output.path==ctx.root/'whole-image-export.tar','flat_export_stage');args=['container','export',cid];limit,seconds=MAX_EXPORT,300
 ctx.check();end=min(ctx.end,time.monotonic()+seconds);child=None;pipes=[];buffers={'stdout':bytearray(),'stderr':bytearray()};counts={'stdout':0,'stderr':0};readcalls={'stdout':0,'stderr':0};low=None;high=0;binding=None;errors=[];result=None
 environment={'PATH':'/usr/bin:/bin','LC_ALL':'C.UTF-8','DOCKER_CONFIG':str(ctx.root/'docker-config')}
 def scope():
  ctx.check();need(time.monotonic()<end,'flat_export_timeout')
  for item in pipes:item.check()
 try:
  scope()
  try:child=subprocess.Popen([*old.DOCKER,*args],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=environment,start_new_session=True,close_fds=True,pipesize=MAX_CHUNK if stage=='export'else-1)
  finally:scope()
  for pipe in(child.stdout,child.stderr):
   capacity=MAX_CHUNK if stage=='export'else fcntl.fcntl(pipe.fileno(),fcntl.F_GETPIPE_SZ)
   need(type(capacity)is int and 4096<=capacity<=MAX_CHUNK,'flat_export_pipe_changed');pipes.append(Pipe(pipe,capacity))
  selector=None;selector_fd=None
  try:
   selector=selectors.DefaultSelector();selector_fd=selector.fileno()
   for key,pipe in zip(('stdout','stderr'),pipes):selector.register(pipe.fd,selectors.EVENT_READ,key)
   while selector.get_map():
    scope();left=end-time.monotonic();need(left>0,'flat_export_timeout')
    for event,_ in selector.select(min(.1,left)):
     key=event.data;maximum=limit if key=='stdout'else MAX_STDERR
     raw=guarded(scope,os.read,event.fd,min(MAX_CHUNK,maximum-counts[key]+1));readcalls[key]+=1
     if not raw:selector.unregister(event.fd);continue
     counts[key]+=len(raw);need(counts[key]<=maximum,'flat_export_stdout_bound'if key=='stdout'else'flat_export_stderr_bound')
     if key=='stdout':low=len(raw)if low is None else min(low,len(raw));high=max(high,len(raw))
     if key=='stdout'and output is not None:output.write(raw,scope)
     else:buffers[key].extend(raw)
   try:code=child.wait(timeout=max(.01,end-time.monotonic()))
   except subprocess.TimeoutExpired:raise RuntimeError('flat_export_timeout')from None
   scope();need(type(code)is int and code==0,'flat_export_client')
   if output is not None:binding=output.freeze(ctx.check,min(ctx.end,time.monotonic()+120))
  except BaseException as error:errors.extend(causes(error))
  finally:
   if selector is not None:
    try:ctx.check()
    except BaseException as error:errors.extend(causes(error))
    try:selector.close()
    except BaseException as error:errors.extend(causes(error))
    finally:
     try:os.fstat(selector_fd)
     except OSError as error:
      if error.errno!=errno.EBADF:errors.append(error)
     else:errors.append(RuntimeError('flat_export_selector_close_unknown'))
     try:ctx.check()
     except BaseException as error:errors.extend(causes(error))
  if not errors:ctx.check();result=binding if output is not None else bytes(buffers['stdout'])
 except BaseException as error:errors.extend(causes(error))
 finally:
  if child is not None:
   try:ctx.check()
   except BaseException as error:errors.extend(causes(error))
   try:ctx.C.cleanup_owned_child(child)
   except BaseException as error:errors.extend(causes(error))
   finally:
    try:ctx.check()
    except BaseException as error:errors.extend(causes(error))
   # A retained RO reader must exist before ANY collector stream is closed,
   # including failed exports. Unknown/failed acquisition stays failed.
   if output is not None and output.original_writer is not None and not output.reader_attempted:
    try:output.retain_reader(ctx.check)
    except BaseException as error:output.note(error);errors.extend(causes(error))
   for stream in(child.stdout,child.stderr):
    item=next((p for p in pipes if p.stream is stream),None)
    try:ctx.check()
    except BaseException as error:errors.extend(causes(error))
    try:
     if item is not None:item.close()
     else:
      fd=stream.fileno();stream.close()
      try:os.fstat(fd)
      except OSError as error:need(error.errno==errno.EBADF,'flat_export_close_unknown')
      else:raise RuntimeError('flat_export_close_unknown')
    except BaseException as error:errors.extend(causes(error))
    finally:
     try:ctx.check()
     except BaseException as error:errors.extend(causes(error))
  report={'schema':'public_flat_image_export_cli/2','stage':stage,'actual_client_returncode':None if child is None else child.returncode,'stdout_bytes_observed':counts['stdout'],'stderr_bytes_observed':counts['stderr'],'stderr_text':bytes(buffers['stderr']).decode('utf-8',errors='replace'),'stderr_truncated':counts['stderr']>MAX_STDERR,'stdout_read_calls_including_EOF':readcalls['stdout'],'stderr_read_calls_including_EOF':readcalls['stderr'],'stdout_min_chunk':low,'stdout_max_chunk':high,'maximum_payload_operation':MAX_CHUNK,'original_stage_seconds':seconds,'pipe_capacities':[p.capacity for p in pipes],'pipe_original_descriptions_physically_closed':child is not None and len(pipes)==2 and all(p.closed for p in pipes),'io_errors':[public_error(e)for e in errors],'security_accepted':False}
  try:ctx.C.write(ctx.root/'reports'/('flat-export-cli-'+stage+'.json'),encode(report))
  except BaseException as error:errors.extend(causes(error))
  try:ctx.check()
  except BaseException as error:errors.extend(causes(error))
 if errors:
  if output is not None:
   for error in errors:output.note(error)
  fail(errors)
 return result

def whole_image_inventory(ctx,old,image,control,sbom,*,I,T,A,adapter_sha256):
 """New cohort only after genuine image/source/SBOM admission by outer caller."""
 need(EXECUTION_ARMED is True,'flat_export_unarmed')
 need(type(control)is dict and control.get('image_id')==image and image_id(image)
  and type(control.get('source_commit'))is str and re.fullmatch('[a-f0-9]{40}',control['source_commit'])
  and hex64(control.get('sbom_sha256'))and type(sbom)is bytes and 0<len(sbom)<=4*1024**2
  and sha256(sbom).hexdigest()==control['sbom_sha256']and hex64(adapter_sha256),'flat_export_scope')
 keeper=ctx.C.PROOF_KEEPER
 need(keeper is not None and keeper.root==ctx.root and not keeper.closed,'flat_export_contract')
 name='app-ci-flat-acl-'+old.owner();ctx.created.append((name,image))
 cid=cli(ctx,old,'create',image=image).decode('ascii').strip();need(hex64(cid),'flat_export_receipt')
 ctx.C.write(ctx.root/'reports/flat-export-created.json',encode({'schema':'public_flat_image_export_created/1',
  'cid':cid,'name':name,'image_id':image,'owner':old.owner(),'started':False,'security_accepted':False}))
 before=admit_container(document(cli(ctx,old,'preinspect',image=image,cid=cid)),old,name,image,cid)
 output=ExportFile(ctx.C,keeper,ctx.root)
 binding=cli(ctx,old,'export',image=image,cid=cid,output=output)
 after=admit_container(document(cli(ctx,old,'postinspect',image=image,cid=cid)),old,name,image,cid)
 need(before==after,'flat_export_container');ctx.check()
 receipt={'schema':'same_image_never_started_export/1','container_id':cid,'image_id':image,
  'source_commit':control['source_commit'],'before':before,'after':after,'archive':binding,
  'actual_export_client_returncode':0,'container_started':False,'private_mounts_used':False,'security_accepted':False}
 ctx.C.write(ctx.root/'reports/flat-export-receipt.json',encode(receipt));ctx.check()
 # The independently frozen pure adapter's final signature is joined here by a
 # successor after its peer review; this proposal cannot execute while unarmed.
 with output.borrow(ctx.check)as stream:
  value=A.collect(I,T,stream,binding['bytes'],image_id=image,source_commit=control['source_commit'],
   sbom_raw=sbom,sbom_sha256=control['sbom_sha256'],verifier_sha256=adapter_sha256,
   export_sha256=binding['sha256'],container_receipt=receipt,check=ctx.check,end=min(ctx.end,time.monotonic()+MAX_INVENTORY_SECONDS))
 need(type(value)is dict and value.get('schema')=='image_acl_archive_inventory/1'
  and value.get('image_id')==image and value.get('source_commit')==control['source_commit']
  and value.get('security_accepted')is False,'flat_export_receipt')
 raw=encode(value);need(len(raw)<=MAX_REPORT,'flat_export_bound')
 ctx.C.write(ctx.root/'reports/fresh-whole-image-acl-inventory.json',raw);ctx.check();return value

if __name__=='__main__':print(json.dumps({'status':'UNARMED','export_performed':False,'security_accepted':False}))
