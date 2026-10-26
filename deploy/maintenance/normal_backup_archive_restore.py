"""Bounded normal-backup uploads restore into one exclusive owned volume.

Private archive bytes, names and checksums stay in operational RAM. This module
has no CLI, extraction convenience API, command, network or production path.
"""
from hashlib import sha256
from pathlib import PurePosixPath
import gzip,os,re,stat,tarfile

class Refused(RuntimeError):pass
def need(value,code):
 if not value:raise Refused(code)

def identity(s):return (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)

class Compressed:
 def __init__(self,source,count,pin,check):
  need(type(count) is int and 0<count<=256*1024**3 and type(pin) is str and re.fullmatch('[a-f0-9]{64}',pin),'archive_binding')
  self.source,self.expected,self.pin,self.check=source,count,pin,check;self.count=0;self.digest=sha256()
 def read(self,n):
  self.check();need(type(n) is int and 0<=n<=1024**2,'compressed_read_bound')
  value=self.source.read(min(n,self.expected-self.count+1));need(type(value) is bytes,'compressed_read_type')
  self.count+=len(value);need(self.count<=self.expected,'compressed_size');self.digest.update(value);self.check();return value
 def finish(self):
  need(self.source.read(1)==b'','compressed_eof');need(self.count==self.expected and self.digest.hexdigest()==self.pin,'compressed_identity');self.check()

class Expanded:
 def __init__(self,source,limit,check):
  need(type(limit) is int and 1024<=limit<=1024**4,'expanded_binding');self.source,self.limit,self.check=source,limit,check;self.count=0
 def read(self,n):
  need(type(n) is int and 0<n<=1024**2,'expanded_read_bound');self.check();value=self.source.read(n)
  self.count+=len(value);need(self.count<=self.limit,'expanded_limit');self.check();return value
 def exact(self,n):
  need(type(n) is int and 0<=n<=1024**2,'exact_read_bound');parts=[];remaining=n
  while remaining:
   value=self.read(remaining);need(bool(value),'archive_short');parts.append(value);remaining-=len(value)
  return b''.join(parts)

def number(raw,code):
 need(type(raw) is str and re.fullmatch('[0-9]{1,12}',raw),code);return int(raw)
def pax(raw):
 need(0<len(raw)<=65536,'pax_bound');offset=0;out={}
 while offset<len(raw):
  space=raw.find(b' ',offset);need(offset<space<=offset+8,'pax_length')
  count=number(raw[offset:space].decode('ascii','strict'),'pax_length');end=offset+count
  need(space+3<end<=len(raw) and raw[end-1:end]==b'\n','pax_record')
  pair=raw[space+1:end-1].split(b'=',1);need(len(pair)==2,'pax_record')
  key,value=(x.decode('utf-8','strict') for x in pair);need(key in {'path','size','uid','gid','mtime','atime','ctime'} and key not in out,'pax_field')
  if key in {'size','uid','gid'}:number(value,'pax_number')
  elif key!='path':need(re.fullmatch('-?[0-9]{1,12}(?:\.[0-9]{1,9})?',value),'pax_time')
  out[key]=value;offset=end
 return out

def components(name,directory):
 need(type(name) is str and '\0' not in name and 0<len(name.encode('utf-8','strict'))<=4096,'member_name')
 need(not any(ord(x)<32 or ord(x)==127 for x in name),'member_name')
 if name.startswith('./'):name=name[2:]
 if directory and name.endswith('/'):name=name[:-1]
 if directory and name in ('','.'):return ()
 pieces=name.split('/');need(pieces[0]=='uploads' and all(x not in ('','.','..') for x in pieces),'member_path')
 need(str(PurePosixPath(name))==name,'member_path');return tuple(pieces[1:])

def directory_fd(rootfd,pieces):
 fd=os.dup(rootfd)
 try:
  for name in pieces:
   child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd);os.close(fd);fd=child
  return fd
 except BaseException:os.close(fd);raise

def restore(source,rootfd,*,compressed_bytes,compressed_sha256,maximum_uncompressed,maximum_members,check=lambda:None):
 """Only regular files/directories. Preserve their numeric owner/mode, never links.

 The caller proves rootfd belongs to its newly created labeled volume. No other
 container may mount it until this function completes. No checksum is returned.
 """
 need(type(maximum_members) is int and 1<=maximum_members<=200000,'member_limit')
 root_before=identity(os.fstat(rootfd));need(stat.S_ISDIR(root_before[2]),'restore_root')
 with os.scandir(rootfd) as entries:need(next(entries,None) is None,'restore_root_not_empty')
 compressed=Compressed(source,compressed_bytes,compressed_sha256,check);raw=gzip.GzipFile(fileobj=compressed,mode='rb');expanded=Expanded(raw,maximum_uncompressed,check)
 entries={};pending=None;longname=None;members=files=directories=bytes_written=0;ended=False
 try:
  while True:
   header=expanded.exact(512)
   if header==b'\0'*512:
    need(expanded.exact(512)==b'\0'*512 and pending is None and longname is None,'tar_eof');ended=True;break
   try:info=tarfile.TarInfo.frombuf(header,'utf-8','strict')
   except Exception:raise Refused('tar_header') from None
   members+=1;need(members<=maximum_members and type(info.size) is int and 0<=info.size<=maximum_uncompressed,'member_bound')
   if info.type in (tarfile.GNUTYPE_LONGNAME,tarfile.XHDTYPE):
    need(info.size<=65536 and pending is None and longname is None,'metadata_bound')
    value=expanded.exact(info.size);padding=(-info.size)%512
    if padding:need(expanded.exact(padding)==b'\0'*padding,'member_padding')
    if info.type==tarfile.GNUTYPE_LONGNAME:
     need(value.endswith(b'\0') and b'\0' not in value[:-1] and len(value)<=4097,'longname');longname=value[:-1].decode('utf-8','strict')
    else:pending=pax(value)
    continue
   if pending:
    for key,value in pending.items():
     if key in {'path','size','uid','gid'}:setattr(info,'name' if key=='path' else key,value if key=='path' else number(value,'pax_number'))
    pending=None
   if longname is not None:info.name=longname;longname=None
   directory=info.type==tarfile.DIRTYPE;need(directory or info.type in (tarfile.REGTYPE,tarfile.AREGTYPE),'member_type')
   need(type(info.mode) is int and 0<=info.mode<=0o777 and type(info.uid) is int and 0<=info.uid<2**31 and type(info.gid) is int and 0<=info.gid<2**31,'member_metadata')
   path=components(info.name,directory);need(path not in entries,'duplicate_member')
   need(not directory or info.size==0,'directory_payload');need(not path or path[:-1] in entries and entries[path[:-1]]['directory'],'parent_order')
   if not path:need(directory,'root_type')
   parent=None;fd=None
   try:
    if directory:
     if path:
      parent=directory_fd(rootfd,path[:-1]);os.mkdir(path[-1],0o700,dir_fd=parent)
      fd=os.open(path[-1],os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=parent)
     else:fd=os.dup(rootfd)
     directories+=1
    else:
     parent=directory_fd(rootfd,path[:-1]);fd=os.open(path[-1],os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=parent)
     remaining=info.size;digest=sha256()
     while remaining:
      value=expanded.exact(min(remaining,1024**2));digest.update(value);view=memoryview(value)
      while view:count=os.write(fd,view);need(count>0,'write_short');view=view[count:]
      remaining-=len(value);bytes_written+=len(value);check()
     padding=(-info.size)%512
     if padding:need(expanded.exact(padding)==b'\0'*padding,'member_padding')
     os.fsync(fd);verify=sha256();offset=0
     while offset<info.size:
      value=os.pread(fd,min(info.size-offset,1024**2),offset);need(bool(value),'restore_short');verify.update(value);offset+=len(value);check()
     need(verify.digest()==digest.digest() and os.fstat(fd).st_size==info.size,'restored_bytes')
     os.fchmod(fd,info.mode);os.fchown(fd,info.uid,info.gid);os.fsync(fd);files+=1
    entries[path]={'directory':directory,'uid':info.uid,'gid':info.gid,'mode':info.mode,'size':info.size}
   finally:
    if fd is not None:os.close(fd)
    if parent is not None:os.fsync(parent);os.close(parent)
  # Consume all compressed input/trailers. Only zero tar record padding may remain.
  while True:
   value=expanded.read(1024**2)
   if not value:break
   need(not any(value),'trailing_archive_data')
  compressed.finish();need(ended and () in entries,'archive_root_missing')
  # Child files are complete before owner/modes can remove traversal access.
  for path,row in sorted(entries.items(),key=lambda item:len(item[0]),reverse=True):
   fd=directory_fd(rootfd,path) if row['directory'] else None
   try:
    if fd is not None:os.fchmod(fd,row['mode']);os.fchown(fd,row['uid'],row['gid']);os.fsync(fd)
   finally:
    if fd is not None:os.close(fd)
  # The tree was built through retained directory descriptors; no link is admitted.
  need(identity(os.fstat(rootfd))[:2]==root_before[:2],'restore_root_changed');check()
  return {'regular_files':files,'directories':directories,'file_bytes':bytes_written,'archive_members':members,
   'expanded_bytes':expanded.count,'complete_stream_verified':True,'all_file_bytes_verified_in_ram':True}
 except Refused:raise
 except (OSError,EOFError,gzip.BadGzipFile,UnicodeError,ValueError,tarfile.TarError):raise Refused('uploads_restore_failed') from None
 finally:raw.close()
