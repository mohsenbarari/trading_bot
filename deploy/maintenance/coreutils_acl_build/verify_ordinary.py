"""Finite public Coreutils CLI fixtures; only the disposable vendor CI may arm.
No imports perform I/O. Genuine UID10001; no mocks, tests skipped, or production
paths. Kernel/libc behavior is exercised by real installed candidate programs.
"""
from hashlib import sha256
from pathlib import Path
import argparse,json,os,re,selectors,signal,stat,subprocess,tempfile,time

ROOT=Path('/work');TOOLS={n:'/usr/bin/'+n for n in ('cp','mv','install','setfacl','getfacl')}
ENV={'PATH':'/usr/bin:/bin','LC_ALL':'C.UTF-8','LANG':'C.UTF-8','HOME':'/work/home'}
def need(ok,code):
 if not ok:raise RuntimeError(code)
def ident(s):return(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def run(argv,cwd,timeout=20):
 need(type(argv)is list and argv and argv[0]in TOOLS.values(),'owned_command_scope')
 child=subprocess.Popen(argv,cwd=cwd,env=ENV,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,close_fds=True,start_new_session=True)
 total=0;parts=[];end=time.monotonic()+timeout
 try:
  with selectors.DefaultSelector()as selector:
   fd=child.stdout.fileno();os.set_blocking(fd,False);selector.register(fd,selectors.EVENT_READ)
   while True:
    need(time.monotonic()<end,'owned_command_timeout')
    if not selector.select(.1):continue
    raw=os.read(fd,min(65536,65536-total+1))
    if not raw:break
    need(total+len(raw)<=65536,'owned_command_output_bound');total+=len(raw);parts.append(raw)
  need(child.wait(timeout=max(.1,end-time.monotonic()))==0,'owned_command_failed')
 except BaseException:
  try:os.waitid(os.P_PID,child.pid,os.WEXITED|os.WNOHANG|os.WNOWAIT)
  except ChildProcessError:pass
  else:
   try:os.killpg(child.pid,signal.SIGKILL)
   except ProcessLookupError:pass
   child.wait(timeout=5)
  raise
 finally:child.stdout.close()
 need(child.stdout.closed and child.returncode==0,'owned_command_closed');return b''.join(parts)
def payload(p):
 s=p.lstat();need(stat.S_ISREG(s.st_mode)and s.st_uid==s.st_gid==10001 and s.st_size<=65536,'owned_payload')
 with p.open('rb')as f:raw=f.read(65537)
 need(len(raw)==s.st_size and ident(p.lstat())==ident(s),'owned_payload_changed');return raw
def binary_hash(path):
 path=path.resolve(strict=True);before=ident(path.lstat());need(stat.S_ISREG(before[2])and before[3]==before[4]==0 and before[5]==1 and 0<before[6]<=4*1024**2,'candidate_binary_metadata')
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC);digest=sha256();count=0
 try:
  need(ident(os.fstat(fd))==before,'candidate_binary_identity')
  while True:
   raw=os.read(fd,min(65536,before[6]-count+1))
   if not raw:break
   need(count+len(raw)<=before[6],'candidate_binary_bound');count+=len(raw);digest.update(raw)
  need(count==before[6]and ident(os.fstat(fd))==ident(path.lstat())==before,'candidate_binary_changed')
 finally:os.close(fd)
 need(ident(path.lstat())==before,'candidate_binary_closed_changed');return digest.hexdigest()
def acl(p,cwd,default=False):
 flags=['--numeric','--absolute-names','--omit-header']
 if default:flags+=['--default']
 return run([TOOLS['getfacl'],*flags,str(p)],cwd)
def clean(root,held):
 need(root.parent in (ROOT,Path('/dev/shm'))and root.name.startswith('coreutils-ordinary-')and root.resolve(strict=True)==root
  and ident(root.lstat())[:5]==held,'owned_cleanup_identity')
 count=0
 for directory,dirs,files in os.walk(root,topdown=False,followlinks=False):
  for name in files+dirs:
   count+=1;need(count<=512,'owned_cleanup_bound');p=Path(directory)/name;s=p.lstat()
   need(s.st_uid==s.st_gid==10001,'owned_cleanup_owner')
   if stat.S_ISDIR(s.st_mode):p.chmod(0o700);p.rmdir()
   else:need(stat.S_ISREG(s.st_mode)or stat.S_ISLNK(s.st_mode)or stat.S_ISFIFO(s.st_mode),'owned_cleanup_type');p.unlink()
 root.rmdir();need(not os.path.lexists(root),'owned_cleanup_absence')
def run_owned():
 need(os.getuid()==os.geteuid()==os.getgid()==os.getegid()==10001 and Path.cwd()==ROOT,'actual_owned_context')
 need(os.environ.get('LD_PRELOAD')is None and os.environ.get('LD_LIBRARY_PATH')is None,'loader_override')
 root=Path(tempfile.mkdtemp(prefix='coreutils-ordinary-',dir=ROOT));held=ident(root.lstat())[:5];other=None;closed=False;cases=[]
 def ok(name,value=True):need(value,'ordinary_'+name);cases.append(name)
 try:
  source=root/'source';source.mkdir();item=source/'item';item.write_bytes(b'owned public Coreutils compatibility\n');item.chmod(0o640)
  run([TOOLS['setfacl'],'-m','u:10002:r--',str(item)],root);os.setxattr(item,'user.gold-ordinary',b'owned')
  copied=root/'copied';run([TOOLS['cp'],'--preserve=mode,xattr',str(item),str(copied)],root)
  ok('regular_acl_mode_xattr',payload(copied)==payload(item)and acl(copied,root)==acl(item,root)and stat.S_IMODE(copied.stat().st_mode)==stat.S_IMODE(item.stat().st_mode)and os.getxattr(copied,'user.gold-ordinary')==b'owned')
  link=source/'link';link.symlink_to('item')
  p=root/'copy-P';run([TOOLS['cp'],'-P',str(link),str(p)],root);ok('cp_P_command_line_symlink',p.is_symlink()and os.readlink(p)=='item')
  p=root/'copy-H';run([TOOLS['cp'],'-H',str(link),str(p)],root);ok('cp_H_command_line_dereference',not p.is_symlink()and payload(p)==payload(item))
  for flag in ('-P','-H','-L'):
   p=root/('tree'+flag);run([TOOLS['cp'],'-R',flag,str(source),str(p)],root)
   expected=flag!='-L';ok('recursive_'+flag[1:]+'_nested_symlink',((p/'link').is_symlink()==expected)and(payload(p/'link')==payload(item)if not expected else os.readlink(p/'link')=='item'))
  p=root/'symbolic';run([TOOLS['cp'],'-s',str(item),str(p)],root);ok('cp_s_intended_symlink_destination',p.is_symlink()and os.readlink(p)==str(item))
  d=source/'directory';d.mkdir();(d/'child').write_bytes(b'owned child\n');d.chmod(0o750)
  run([TOOLS['setfacl'],'-d','-m','u:10002:r-x',str(d)],root);os.setxattr(d,'user.gold-dir',b'held-dir')
  p=root/'archive-directory';run([TOOLS['cp'],'-a',str(d),str(p)],root)
  ok('directory_default_acl_general_xattr',acl(p,root,True)==acl(d,root,True)and os.getxattr(p,'user.gold-dir')==b'held-dir'and payload(p/'child')==payload(d/'child'))
  p=root/'no-preserve-directory';run([TOOLS['cp'],'-R','--no-preserve=mode',str(d),str(p)],root);ok('directory_no_preserve_mode',p.is_dir()and payload(p/'child')==payload(d/'child'))
  fifo=source/'fifo';os.mkfifo(fifo,0o640);p=root/'fifo';run([TOOLS['cp'],'-a',str(fifo),str(p)],root,timeout=5);ok('fifo_no_payload_open',stat.S_ISFIFO(p.lstat().st_mode))
  move=source/'rename-directory';move.mkdir();(move/'item').write_bytes(b'move\n');p=root/'renamed'
  original=move.stat();run([TOOLS['mv'],str(move),str(p)],root);ok('mv_same_filesystem_rename',not move.exists()and p.stat().st_ino==original.st_ino and payload(p/'item')==b'move\n')
  need(Path('/dev/shm').is_dir()and os.stat('/dev/shm').st_dev!=root.stat().st_dev,'required_cross_device_namespace')
  other=Path(tempfile.mkdtemp(prefix='coreutils-ordinary-',dir='/dev/shm'));otherheld=ident(other.lstat())[:5]
  cross=root/'cross-device';cross.mkdir();(cross/'item').write_bytes(b'cross-device\n');run([TOOLS['setfacl'],'-d','-m','u:10002:r-x',str(cross)],root);expected_acl=acl(cross,root,True);p=other/'moved'
  run([TOOLS['mv'],str(cross),str(p)],root);ok('mv_cross_device_default_acl',not cross.exists()and payload(p/'item')==b'cross-device\n'and acl(p,root,True)==expected_acl)
  p=root/'installed';run([TOOLS['install'],'-m','0750',str(item),str(p)],root);ok('install_regular_mode',payload(p)==payload(item)and stat.S_IMODE(p.stat().st_mode)==0o750)
  p=root/'installed-directory';run([TOOLS['install'],'-d','-m','0710',str(p/'child')],root);ok('install_directory_mode',stat.S_IMODE((p/'child').stat().st_mode)==0o710)
  binaries={name:{'path':TOOLS[name],'sha256':binary_hash(Path(TOOLS[name]))}for name in ('cp','mv','install')}
  clean(other,otherheld);other=None;clean(root,held);closed=True
  return {'schema':'coreutils_owned_cli_compatibility/1','status':'PASS','actual_uid':os.getuid(),'actual_gid':os.getgid(),'cases':cases,'actual_case_count':len(cases),'skip_count':0,
   'actual_installed_candidate_binaries':binaries,'all_commands_actual_zero_and_pipes_closed':True,'owned_cleanup_complete':True,'production_data_accessed':False,'exploit_or_race_reproduction_performed':False,'NFSv4_real_filesystem_exercised':False,'security_accepted':False}
 finally:
  if not closed:
   # Failure cleanup is explicit owned test data only and cannot become PASS.
   if other is not None:
    try:clean(other,otherheld)
    except BaseException:pass
   try:
    for directory,dirs,_ in os.walk(root):
     for name in dirs:
      p=Path(directory)/name
      if not p.is_symlink():p.chmod(0o700)
    clean(root,held)
   except BaseException:pass

def main(argv=None):
 parser=argparse.ArgumentParser();parser.add_argument('--run-owned-compatibility',action='store_true');args=parser.parse_args(argv)
 if not args.run_owned_compatibility:print(json.dumps({'status':'UNARMED','tests_run':False,'security_accepted':False}));return 0
 result=run_owned();raw=json.dumps(result,sort_keys=True);need(len(raw)<=65536,'owned_receipt_bound');print(raw);return 0
if __name__=='__main__':raise SystemExit(main())
