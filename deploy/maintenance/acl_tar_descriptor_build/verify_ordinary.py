"""Owned normal Tar/ACL interoperability in the disposable compiler only.
Default/import is unarmed. No production/archive/host operands are accepted.
"""
from pathlib import Path
from hashlib import sha256
import argparse,ctypes,json,os,re,selectors,signal,stat,subprocess,sys,time

VERSION='1.35+dfsg-5~trixie+gold2';OLD_SHA='1d9d884a06465ad5c0551858ceaaaa4dc60eda57ad5f12b710ab52d63e817892'
CANDIDATE=Path('/usr/bin/tar');BASELINE=Path('/build/old-tar/usr/bin/tar');ROOT=Path('/work/ordinary-tar-compat')
EMPTY=0x1000;ACCESS=0x8000;DEFAULT=0x4000;PHASE='bootstrap'
ENV={'PATH':'/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/work/home','LANG':'C.UTF-8','LC_ALL':'C.UTF-8'}
def require(value,code):
 if not value:raise RuntimeError(code)
def ident(s):return (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def file_binding(path,expected=None):
 before=ident(path.lstat());require(path.resolve(strict=True)==path and stat.S_ISREG(before[2])and before[3:6]==(0,0,1)
  and not before[2]&0o022 and 0<before[6]<=32*1024**2,'public_binary')
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
 try:
  require(ident(os.fstat(fd))==before,'binary_changed');h=sha256();count=0
  while True:
   part=os.read(fd,min(65536,before[6]-count+1))
   if not part:break
   count+=len(part);require(count<=before[6],'binary_bound');h.update(part)
  require(count==before[6]and ident(os.fstat(fd))==ident(path.lstat())==before,'binary_changed')
 finally:os.close(fd)
 require(ident(path.lstat())==before and(expected is None or h.hexdigest()==expected),'binary_changed')
 return {'path':str(path),'bytes':count,'sha256':h.hexdigest()},before

def cleanup(child):
 if child.returncode is not None:return
 try:os.waitid(os.P_PID,child.pid,os.WEXITED|os.WNOHANG|os.WNOWAIT)
 except ChildProcessError:return
 try:os.killpg(child.pid,signal.SIGKILL)
 except ProcessLookupError:pass
 child.wait(timeout=5)
def command(argv,*,cwd=None,timeout=30):
 child=subprocess.Popen(argv,cwd=cwd,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
  env=ENV,close_fds=True,start_new_session=True);end=time.monotonic()+timeout;total=0;parts=[]
 try:
  with selectors.DefaultSelector()as selector:
   fd=child.stdout.fileno();os.set_blocking(fd,False);selector.register(fd,selectors.EVENT_READ)
   while True:
    left=end-time.monotonic();require(left>0,'ordinary_command_timeout')
    if not selector.select(min(1,left)):continue
    raw=os.read(fd,min(65536,1024**2-total+1))
    if not raw:break
    total+=len(raw);require(total<=1024**2,'ordinary_output_bound');parts.append(raw)
  require(child.wait(timeout=max(.1,end-time.monotonic()))==0,'ordinary_command_failed');return b''.join(parts)
 except BaseException:cleanup(child);raise
 finally:child.stdout.close()

def acl_api(library):
 lib=ctypes.CDLL(str(library),use_errno=True);pointer=ctypes.c_void_p;integer=ctypes.c_int;unsigned=ctypes.c_uint;string=ctypes.c_char_p
 for name,args,result in [('acl_from_text',[string],pointer),('acl_valid',[pointer],integer),('acl_free',[pointer],integer),
  ('acl_get_file_at',[integer,string,integer,unsigned],pointer),('acl_set_file_at',[integer,string,integer,unsigned,pointer],integer),
  ('acl_cmp',[pointer,pointer],integer),('acl_entries',[pointer],integer)]:
  function=getattr(lib,name);function.argtypes=args;function.restype=result
 return lib

def acl_set(lib,path,kind,value):
 fd=os.open(path,os.O_PATH|os.O_NOFOLLOW|os.O_CLOEXEC);acl=lib.acl_from_text(value)
 try:require(acl and lib.acl_valid(acl)==0 and lib.acl_set_file_at(fd,b'',EMPTY,kind,acl)==0,'owned_acl_set')
 finally:
  if acl:require(lib.acl_free(acl)==0,'owned_acl_free')
  os.close(fd)
def acl_matches(lib,path,kind,value=None):
 fd=os.open(path,os.O_PATH|os.O_NOFOLLOW|os.O_CLOEXEC);got=lib.acl_get_file_at(fd,b'',EMPTY,kind);expected=lib.acl_from_text(value)if value is not None else None
 try:
  require(bool(got),'owned_acl_get')
  if value is None:require(lib.acl_entries(got)==0,'default_cleanup')
  else:require(expected and lib.acl_cmp(got,expected)==0,'owned_acl_mismatch')
 finally:
  if got:require(lib.acl_free(got)==0,'owned_acl_free')
  if expected:require(lib.acl_free(expected)==0,'owned_acl_free')
  os.close(fd)

def library_mapping(library,frame):
 fd=os.open('/proc/self/maps',os.O_RDONLY|os.O_CLOEXEC);parts=[];total=0
 try:
  while True:
   raw=os.read(fd,min(65536,2*1024**2-total+1))
   if not raw:break
   total+=len(raw);require(total<=2*1024**2,'map_bound');parts.append(raw)
 finally:os.close(fd)
 matches=[]
 for line in b''.join(parts).splitlines():
  fields=line.split(maxsplit=5)
  if len(fields)==6 and fields[5].decode('utf-8',errors='strict')==str(library):
   dev=fields[3].split(b':');require(len(dev)==2 and(int(dev[0],16),int(dev[1],16))==(os.major(frame[0]),os.minor(frame[0]))and int(fields[4])==frame[1],'library_mapping');matches.append(True)
 require(matches,'library_mapping');return True

ACCESS_TEXT=b'user::rw-,user:12345:r--,group::---,mask::r--,other::---'
DEFAULT_TEXT=b'user::rwx,user:12345:r-x,group::---,mask::r-x,other::---'

def make_tree(lib,path):
 path.mkdir(mode=0o700);(path/'plain.txt').write_bytes(b'OWNED PUBLIC CONTENT\n');(path/'plain.txt').chmod(0o600)
 (path/'سکه.txt').write_bytes('PUBLIC PERSIAN NAME'.encode());(path/'سکه.txt').chmod(0o640);acl_set(lib,path/'سکه.txt',ACCESS,ACCESS_TEXT)
 (path/'empty.txt').write_bytes(b'');(path/'empty.txt').chmod(0o644)
 os.link(path/'plain.txt',path/'hard.txt');os.symlink('plain.txt',path/'link.txt');os.mkfifo(path/'ordinary.fifo',0o600)
 (path/'default-only').mkdir(mode=0o700);acl_set(lib,path/'default-only',DEFAULT,DEFAULT_TEXT)
 # This access ACL remains the ordinary three entries; only the default is extended.
 fd=os.open(path/'default-only',os.O_PATH|os.O_DIRECTORY|os.O_CLOEXEC);acl=lib.acl_get_file_at(fd,b'',EMPTY,ACCESS)
 try:require(acl and lib.acl_entries(acl)==3,'default_only_fixture')
 finally:
  if acl:lib.acl_free(acl)
  os.close(fd)
 (path/'clear-default').mkdir(mode=0o700);os.symlink('default-only',path/'default-dir-link')

def bytes_equal(path,expected):
 require(path.is_file()and not path.is_symlink()and path.stat().st_size==len(expected),'ordinary_file_identity')
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
 try:require(os.read(fd,len(expected)+1)==expected,'ordinary_file_content')
 finally:os.close(fd)
def check_tree(lib,path,*,dereference=False):
 bytes_equal(path/'plain.txt',b'OWNED PUBLIC CONTENT\n');bytes_equal(path/'سکه.txt',b'PUBLIC PERSIAN NAME');bytes_equal(path/'empty.txt',b'')
 require((path/'plain.txt').stat().st_ino==(path/'hard.txt').stat().st_ino,'ordinary_hardlink')
 if dereference:bytes_equal(path/'link.txt',b'OWNED PUBLIC CONTENT\n')
 else:require((path/'link.txt').is_symlink()and os.readlink(path/'link.txt')=='plain.txt','ordinary_symlink')
 if dereference:
  require((path/'default-dir-link').is_dir()and not(path/'default-dir-link').is_symlink(),'ordinary_directory_symlink');acl_matches(lib,path/'default-dir-link',DEFAULT,DEFAULT_TEXT)
 else:require((path/'default-dir-link').is_symlink()and os.readlink(path/'default-dir-link')=='default-only','ordinary_directory_symlink')
 require(stat.S_ISFIFO((path/'ordinary.fifo').lstat().st_mode),'ordinary_fifo')
 acl_matches(lib,path/'سکه.txt',ACCESS,ACCESS_TEXT);acl_matches(lib,path/'default-only',DEFAULT,DEFAULT_TEXT);acl_matches(lib,path/'clear-default',DEFAULT)
 fd=os.open(path/'default-only',os.O_PATH|os.O_DIRECTORY|os.O_CLOEXEC);acl=lib.acl_get_file_at(fd,b'',EMPTY,ACCESS)
 try:require(acl and lib.acl_entries(acl)==3,'default_only_preserved')
 finally:
  if acl:lib.acl_free(acl)
  os.close(fd)

def verify(args):
 global PHASE
 require(os.getuid()==os.geteuid()==os.getgid()==os.getegid()==1001 and Path('/.dockerenv').is_file()
  and sys.flags.isolated==sys.flags.no_site==sys.flags.ignore_environment==1,'owned_disposable_required')
 require(not any(n.startswith('LD_')for n in os.environ)and not Path('/etc/ld.so.preload').exists(),'loader_override')
 require(re.fullmatch('[a-f0-9]{64}',args.candidate_sha256)and re.fullmatch('[a-f0-9]{64}',args.libacl_sha256),'public_pin')
 candidate,cframe=file_binding(CANDIDATE,args.candidate_sha256);baseline,bframe=file_binding(BASELINE,OLD_SHA)
 require(command(['/usr/bin/dpkg-query','-W','-f=${Version}','tar']).decode()==VERSION,'installed_candidate_version')
 library=Path('/usr/lib/x86_64-linux-gnu/libacl.so.1').resolve(strict=True);require(library.parent==Path('/usr/lib/x86_64-linux-gnu'),'library_path');library_bound,lframe=file_binding(library,args.libacl_sha256);lib=acl_api(library);library_mapping(library,lframe)
 require(not ROOT.exists(),'fresh_owned_tree');ROOT.mkdir(mode=0o700);source=ROOT/'input';make_tree(lib,source)
 records=[];index=0
 for dereference in (False,True):
  for creator in (BASELINE,CANDIDATE):
   archive=ROOT/('ordinary-'+str(index)+'.tar');index+=1;PHASE='ordinary_create'
   flags=['--sort=name','--format=pax','--pax-option=exthdr.name=%d/PaxHeaders/%f,atime:=0,ctime:=0','--mtime=@0','--owner=0','--group=0','--numeric-owner','--acls']
   if dereference:flags+=['--dereference']
   command([str(creator),*flags,'-cf',str(archive),'-C',str(source),'.'])
   require(0<archive.stat().st_size<=1024**2,'owned_archive_bound');command([str(CANDIDATE),'-tf',str(archive)])
   for consumer in (BASELINE,CANDIDATE):
    output=ROOT/('output-'+str(len(records)));output.mkdir(mode=0o700);acl_set(lib,output,DEFAULT,DEFAULT_TEXT);PHASE='ordinary_extract'
    command([str(consumer),'--acls','--numeric-owner','--no-same-owner','--same-permissions','-xf',str(archive),'-C',str(output)])
    check_tree(lib,output,dereference=dereference);records.append({'creator':'candidate'if creator==CANDIDATE else'baseline','extractor':'candidate'if consumer==CANDIDATE else'baseline','dereference':dereference,'default_only_directory_preserved':True,'default_inheritance_cleanup':True,'content_acl_links_fifo':True})
 PHASE='ordinary_bounded_fds';deep=ROOT/'deep-input';deep.mkdir(mode=0o700);leaf=deep
 for number in range(160):leaf=leaf/('d'+str(number).zfill(3));leaf.mkdir(mode=0o700)
 (leaf/'leaf.txt').write_bytes(b'OWNED DEEP TREE\n');archive=ROOT/'deep.tar';out=ROOT/'deep-output';out.mkdir(mode=0o700)
 command(['/usr/bin/prlimit','--nofile=64:64','--',str(CANDIDATE),'--format=pax','--acls','-cf',str(archive),'-C',str(deep),'.'],timeout=60)
 command(['/usr/bin/prlimit','--nofile=64:64','--',str(CANDIDATE),'--acls','--no-same-owner','--same-permissions','-xf',str(archive),'-C',str(out)],timeout=60)
 bytes_equal(out/leaf.relative_to(deep)/'leaf.txt',b'OWNED DEEP TREE\n')
 PHASE='physical_final';require(ident(CANDIDATE.lstat())==cframe and ident(BASELINE.lstat())==bframe and ident(library.lstat())==lframe,'final_binary_drift')
 file_binding(CANDIDATE,args.candidate_sha256);file_binding(BASELINE,OLD_SHA);file_binding(library,args.libacl_sha256);library_mapping(library,lframe)
 return {'schema':'tar_ordinary_owned_compatibility/1','status':'PASS','candidate':candidate,'baseline':baseline,'approved_libacl':library_bound,
  'roundtrips':records,'actual_roundtrip_count':8,'default_only_directory_tested':True,'default_cleanup_tested':True,'ordinary_fifo_no_payload_open':True,
  'bounded_directory_fds_tested':True,'normal_deep_directory_count':160,'production_data_accessed':False,'exploit_or_race_reproduction_performed':False,'whole_image_acl_closed':False,'security_accepted':False}

def main(argv=None):
 p=argparse.ArgumentParser();p.add_argument('--run-owned-compatibility',action='store_true');p.add_argument('--candidate-sha256');p.add_argument('--libacl-sha256');args=p.parse_args(argv)
 if not args.run_owned_compatibility:print(json.dumps({'schema':'tar_ordinary_owned_compatibility/1','status':'UNARMED','security_accepted':False}));return 0
 signal.signal(signal.SIGALRM,lambda *_:(_ for _ in ()).throw(RuntimeError('ordinary_deadline')));signal.alarm(120)
 try:value=verify(args);status=0
 except BaseException as e:
  codes={'owned_disposable_required','loader_override','public_pin','public_binary','binary_changed','binary_bound','installed_candidate_version','library_path','library_mapping','map_bound','fresh_owned_tree','default_only_fixture','default_only_preserved','owned_acl_get','owned_acl_set','owned_acl_free','owned_acl_mismatch','default_cleanup','ordinary_file_identity','ordinary_file_content','ordinary_hardlink','ordinary_symlink','ordinary_fifo','ordinary_directory_symlink','ordinary_create','ordinary_extract','owned_archive_bound','ordinary_command_failed','ordinary_output_bound','ordinary_command_timeout','ordinary_deadline','final_binary_drift'}
  args=BaseException.args.__get__(e);code=args[0]if type(e)is RuntimeError and type(args)is tuple and len(args)==1 and type(args[0])is str and args[0]in codes else'unclassified'
  value={'schema':'tar_ordinary_owned_compatibility/1','status':'FAIL','phase':PHASE,'closed_reason':code,'security_accepted':False};status=1
 finally:signal.alarm(0)
 print(json.dumps(value,sort_keys=True));return status
if __name__=='__main__':raise SystemExit(main())
