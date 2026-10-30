"""Finite CI SOURCE-file custody. Large maxima belong to exact named profiles."""
from pathlib import Path
from hashlib import sha256
import errno,fcntl,os,stat,time,threading
def need(v,c):
 if not v:raise RuntimeError(c)
def frame(s):return(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def flags(fd):return(fcntl.fcntl(fd,fcntl.F_GETFL),fcntl.fcntl(fd,fcntl.F_GETFD))
def parents(path,owner):
 rows=[]
 for p in path.parents:
  b=frame(p.lstat());need(p.resolve(strict=True)==p and stat.S_ISDIR(b[2])and b[3]in{0,owner}and not b[2]&0o022,'source_parent');rows.append((p,b[:5]))
 return tuple(rows)
class Scope:
 def __init__(self,seconds):self.started=time.monotonic();self.end=self.started+seconds;self.original_clock=(self.started,self.end);self.rows=[];self.active=[];self.errors=[];self.owner=os.geteuid();self.gid=os.getegid();self.actor=(os.getpid(),threading.get_native_id());self.finished=False
 def check(self):
  need(not self.finished and(self.started,self.end)==self.original_clock and time.monotonic()<self.end and not self.errors and self.actor==(os.getpid(),threading.get_native_id()),'source_scope')
  # Active original descriptors are freshly guarded on every payload call.
  # Closed publications are never reopened/used as authority by this actor;
  # their full9/names/parents are freshly checked together at final commit.
  facts={}
  def observe(path,expected):
   if path not in facts:facts[path]=[path.resolve(strict=True),frame(path.lstat()),len(expected)]
   got=facts[path];got[2]=max(got[2],len(expected));need(got[0]==path and got[1][:len(expected)]==expected,'source_named_changed')
  # Deduplicate names/parents inside this one guard pass only. Original FD
  # metadata/flags/offset are always fresh; no facts survive a payload call.
  for row in self.active:row.check(observe)
  for path,(resolved,before,count)in facts.items():need(path.resolve(strict=True)==resolved==path and frame(path.lstat())[:count]==before[:count],'source_guard_retirement')
  need(time.monotonic()<self.end,'source_scope')
 def note(self,error):self.errors.append({'class':type(error).__name__,'code':str(error)[:120]})
 def open(self,path,pin,maximum):
  try:self.check()
  except BaseException as e:self.note(e);raise
  r=File.__new__(File);self.rows.append(r);self.active.append(r);File.__init__(r,self,Path(path),pin,maximum,False);return r
 def create(self,path,maximum):
  try:self.check()
  except BaseException as e:self.note(e);raise
  r=File.__new__(File);self.rows.append(r);self.active.append(r);File.__init__(r,self,Path(path),None,maximum,True);return r
 def finish(self):
  need(not self.finished,'source_finalize_repeated');self.check()
  for row in reversed(self.rows):
   if not row.attempted:row.close()
  self.check()
  for row in self.rows:row.named()
  self.check()
  need(all(r.closed and r.physical_EBADF for r in self.rows),'source_finality');self.finished=True
  return[{'path':str(r.path),'bytes':r.before[6],'sha256':r.pin[1]if r.pin else None,'original_fd':r.original_fd,'physical_EBADF':r.physical_EBADF,'full9_flags_offset_and_named':True}for r in self.rows]
class File:
 def __init__(self,scope,path,pin,maximum,write):
  # Prefix is published in Scope.rows before this constructor. Capture the
  # original descriptor immediately after open, before fstat/flags/read.
  self.scope=scope;self.path=path;self.fd=self.original_fd=None;self.before=self.origin=self.flags=None;self.parents=();self.pin=pin;self.maximum=maximum;self.write_mode=write;self.count=0;self.sealed=False;self.admitted=False;self.uncertain=False;self.attempted=False;self.closed=False;self.physical_EBADF=False;self.held=False
  try:
   need(type(maximum)is int and maximum>0 and path.is_absolute(),'source_role_bound');self.parents=parents(path,scope.owner)
   if not write:
    b=frame(path.lstat());need(path.resolve(strict=True)==path and b[2]in{stat.S_IFREG|0o600,stat.S_IFREG|0o644}and b[3:6]==(scope.owner,scope.gid,1)and 0<b[6]<=maximum,'source_input_metadata')
   access=(os.O_RDWR|os.O_CREAT|os.O_EXCL)if write else os.O_RDONLY
   self.fd=self.original_fd=os.open(path,access|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,0o600)
   self.before=frame(os.fstat(self.original_fd));self.flags=flags(self.original_fd);self.origin=(self.path,self.original_fd,self.flags)
   need(self.before[3:6]==(scope.owner,scope.gid,1)and self.flags[1]==fcntl.FD_CLOEXEC and self.flags[0]&os.O_ACCMODE==(os.O_RDWR if write else os.O_RDONLY),'source_fd_metadata')
   need((write and self.before[6]==0)or(not write and self.before==b),'source_acquisition_identity');self.admitted=True;self.check()
   if not write:need(pin is not None and pin[0]==self.before[6]and self.hash()==pin[1],'source_input_pin')
  except BaseException as e:self.uncertain=True;scope.note(e);raise
 def named(self,observe=None):
  if observe is None:need(self.path.resolve(strict=True)==self.path and frame(self.path.lstat())==self.before and all(p.resolve(strict=True)==p and frame(p.lstat())[:5]==old for p,old in self.parents),'source_named_changed')
  else:
   observe(self.path,self.before)
   for p,b in self.parents:observe(p,b)
 def check(self,observe=None):
  if self.before is None:need(self.original_fd is None,'source_unknown_prefix');return
  need((self.path,self.original_fd,self.flags)==self.origin and not self.uncertain,'source_origin');self.named(observe)
  if not self.closed:need(self.fd==self.original_fd and frame(os.fstat(self.original_fd))==self.before and flags(self.original_fd)==self.flags and os.lseek(self.original_fd,0,os.SEEK_CUR)==0,'source_original_changed')
 def read(self,n,offset):
  need(self.admitted and not self.attempted and not self.closed and type(n)is int and 0<n<=65536 and type(offset)is int and 0<=offset<=self.before[6],'source_read_bound');b=None;errors=[]
  try:self.scope.check();b=os.pread(self.original_fd,n,offset)
  except BaseException as e:errors.append(e)
  finally:
   try:self.scope.check()
   except BaseException as e:errors.append(e)
  if errors:
   self.uncertain=True
   for e in errors:self.scope.note(e)
   raise errors[0]
  return b
 def hash(self):
  h=sha256();offset=0
  while offset<self.before[6]:
   b=self.read(min(65536,self.before[6]-offset),offset);need(b,'source_hash_short');h.update(b);offset+=len(b)
  return h.hexdigest()
 def append(self,b):
  need(self.write_mode and self.admitted and not self.attempted and not self.sealed and type(b)is bytes and 0<len(b)<=65536 and self.count+len(b)<=self.maximum,'source_write_bound')
  try:
   self.scope.check();view=memoryview(b)
   while view:
    # pwrite keeps the original FD offset0; each metadata change is admitted
    # only as the exact result of this actor's own bounded output operation.
    n=os.pwrite(self.original_fd,view,self.count);need(type(n)is int and 0<n<=len(view),'source_write');self.count+=n;view=view[n:];after=frame(os.fstat(self.original_fd));need(after[:6]==self.before[:6]and after[6]==self.count and frame(self.path.lstat())==after,'source_own_write');self.before=after
   self.scope.check()
  except BaseException as e:self.uncertain=True;self.scope.note(e);raise
 def seal(self,pin=None):
  try:
   need(self.write_mode and self.count>0 and not self.attempted and not self.sealed,'source_empty_output_or_reseal');self.scope.check();os.fsync(self.original_fd);self.scope.check();h=self.hash();need(pin is None or pin==(self.count,h),'source_output_pin');self.pin=(self.count,h);self.sealed=True;return{'path':str(self.path),'bytes':self.count,'sha256':h}
  except BaseException as e:self.uncertain=True;self.scope.note(e);raise
 def close(self):
  if self.attempted:
   e=RuntimeError('source_close_repeated');self.scope.note(e);raise e
  self.attempted=True
  if self.original_fd is None:
   need(self.admitted and not self.uncertain,'source_unadmitted_prefix');self.closed=True;return
  try:self.scope.check();self.check();need(self.admitted and not self.uncertain,'source_close_unknown')
  except BaseException as e:self.held=True;self.uncertain=True;self.scope.note(e);raise
  try:
   os.close(self.original_fd)
   try:os.fstat(self.original_fd)
   except OSError as e:need(e.errno==errno.EBADF,'source_close_unknown');self.physical_EBADF=True
   else:raise RuntimeError('source_close_noop')
   self.closed=True;self.fd=None;self.named();self.scope.check();self.scope.active.remove(self)
  except BaseException as e:self.held=True;self.uncertain=True;self.scope.note(e);raise
def publish(scope,path,b,maximum):
 need(type(b)is bytes and 0<len(b)<=maximum,'source_publication_bound');w=scope.create(path,maximum)
 for off in range(0,len(b),65536):w.append(b[off:off+65536])
 ref=w.seal();r=scope.open(path,(ref['bytes'],ref['sha256']),maximum);w.close();need(r.hash()==ref['sha256'],'source_postwriter_readback');r.close();scope.check();return ref
