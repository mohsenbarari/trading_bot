"""Fixed Ubuntu worker entry; unbound Source pins grant no action."""
from pathlib import Path
from hashlib import sha256
from types import ModuleType
import errno,fcntl,json,os,re,stat,sys
HERE=Path(__file__).absolute().parent
SHARED_NAME='sourcebuild.py'
SHARED_PIN=(36239, '625922e422080cd91e113fae531bcbfd420005299cd5a9d7b3a5d4bbe7aa575b')
PROFILE_NAME='profile239.py'
PROFILE_PIN=(5262, '420c3b55567fd93d200386b4572dfc143da1df8b5bc2114409babf2209c9f767')
PROFILE_KEY='WORKER'
ACTION_FLAGS=('--prepare', '--build')

def need(ok,code):
 if ok is not True:raise RuntimeError(code)
def frame(s):return(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def code(error):
 text=str(error)
 return text if type(error)is RuntimeError and re.fullmatch('[a-zA-Z0-9_]{1,80}',text)else'closed_source_failure'
def main(argv=None):
 argv=list(sys.argv[1:]if argv is None else argv)
 if not any(flag in argv for flag in ACTION_FLAGS):
  print('{"status":"UNARMED","build":false}');return 0
 if SHARED_PIN is None:
  print('{"status":"UNBOUND_SOURCE_PINS","build":false,"security_accepted":false}');return 1
 rows=[];primary=None;final=[];rc=1
 def take(name,pin):
  path=HERE/name;fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
  row={'fd':fd,'path':path,'frame':None};rows.append(row)
  value=frame(os.fstat(fd));row['frame']=value
  need(stat.S_ISREG(value[2])and value[3]in{0,1001}and not value[2]&0o022 and value[5]==1 and 0<value[6]<=131072,'source_metadata')
  row['parents']=[(p,frame(p.lstat())[:5])for p in path.parents]
  row['flags']=(fcntl.fcntl(fd,fcntl.F_GETFL),fcntl.fcntl(fd,fcntl.F_GETFD))
  raw=os.pread(fd,value[6]+1,0)
  need(len(raw)==value[6]and(pin is None or(len(raw),sha256(raw).hexdigest())==pin),'source_pin')
  check(row);return raw
 def check(row):
  fd=row['fd'];path=row['path'];value=row['frame']
  need(value is not None and path.resolve(strict=True)==path and frame(path.lstat())==value,'source_named')
  need(all(p.resolve(strict=True)==p and frame(p.lstat())[:5]==v and stat.S_ISDIR(v[2])and v[3]in{0,1001}and not v[2]&0o022 for p,v in row['parents']),'source_parent')
  need(frame(os.fstat(fd))==value and(fcntl.fcntl(fd,fcntl.F_GETFL),fcntl.fcntl(fd,fcntl.F_GETFD))==row['flags']and row['flags'][1]==fcntl.FD_CLOEXEC and os.lseek(fd,0,os.SEEK_CUR)==0,'source_OFD')
 def module(name,raw):
  value=ModuleType('_fixed_'+name.replace('.','_'));value.__file__=str(HERE/name);exec(compile(raw,str(HERE/name),'exec'),value.__dict__);return value
 try:
  take(Path(__file__).name,None)
  profile=module(PROFILE_NAME,take(PROFILE_NAME,PROFILE_PIN))
  shared=module(SHARED_NAME,take(SHARED_NAME,SHARED_PIN))
  shared.configure_profile(Path(__file__),getattr(profile,PROFILE_KEY))
  for row in rows:check(row)
  rc=shared.main(argv);need(type(rc)is int and rc in{0,1},'source_return')
 except BaseException as error:primary=code(error)
 finally:
  for row in reversed(rows):
   try:
    if row['frame']is not None:check(row)
   except BaseException as error:final.append(code(error))
   try:
    # Metadata failures before acquisition completed still own this opened FD.
    current=frame(os.fstat(row['fd']))
    need(row['frame']is None or current[:6]==row['frame'][:6],'source_identity_changed')
    os.close(row['fd'])
    try:os.fstat(row['fd'])
    except OSError as error:need(error.errno==errno.EBADF,'source_close')
    else:need(False,'source_close')
   except BaseException as error:final.append(code(error))
 if primary is not None or final:
  print(json.dumps({'status':'FAILED','code':primary,'finalizer_codes':final,'retry_allowed':False,'security_accepted':False},sort_keys=True));return 1
 return rc
if __name__=='__main__':raise SystemExit(main())
