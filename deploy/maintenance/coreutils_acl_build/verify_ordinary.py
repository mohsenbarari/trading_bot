"""Finite public Coreutils CLI fixtures; only the disposable vendor CI may arm.
No imports perform I/O. Genuine UID10001; no mocks, tests skipped, or production
paths. Kernel/libc behavior is exercised by real installed candidate programs.
"""
from hashlib import sha256
from pathlib import Path
import argparse,json,os,re,selectors,signal,stat,subprocess,tempfile,time

ROOT=Path('/work');TOOLS={n:'/usr/bin/'+n for n in ('cp','mv','install','setfacl','getfacl')}
ENV={'PATH':'/usr/bin:/bin','LC_ALL':'C.UTF-8','LANG':'C.UTF-8','HOME':'/work/home'}
LEGACY_CASES=('regular_acl_mode_xattr','cp_P_command_line_symlink','cp_H_command_line_dereference','recursive_P_nested_symlink','recursive_H_nested_symlink','recursive_L_nested_symlink','cp_s_intended_symlink_destination','directory_default_acl_general_xattr','directory_no_preserve_mode','fifo_no_payload_open','mv_same_filesystem_rename','mv_cross_device_default_acl','install_regular_mode','install_directory_mode')
PARENT_CASES=('parents_new_acl_mode','parents_existing_metadata','parents_existing_fast_path','parents_source_alias_P','parents_source_alias_H','parents_source_alias_L','parents_destination_alias','parents_search_without_read','parents_no_preserve_mode')
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
def run_parent_cases(root,ok):
 base=root/'parents';base.mkdir();source=base/'source';source.mkdir();inner=source/'inner';inner.mkdir();(inner/'item').write_bytes(b'ordinary parent item\n')
 for p in (source,inner):
  run([TOOLS['setfacl'],'-m','u:10002:r-x',str(p)],base)
  run([TOOLS['setfacl'],'-d','-m','u:10002:r-x',str(p)],base)
  p.chmod(0o750)
 def copied(name,arg='source/inner/item',flags=('--preserve=mode',)):
  target=base/name;target.mkdir();run([TOOLS['cp'],'--parents',*flags,arg,str(target)],base);return target
 def same(p,q):
  return(stat.S_IMODE(p.stat().st_mode)==stat.S_IMODE(q.stat().st_mode)
   and acl(p,base)==acl(q,base)and acl(p,base,True)==acl(q,base,True))
 target=copied('new')
 ok('parents_new_acl_mode',same(source,target/'source')and same(inner,target/'source/inner')and payload(target/'source/inner/item')==payload(inner/'item'))
 target=base/'existing';target.mkdir();(target/'source').mkdir();(target/'source').chmod(0o700)
 for p in (source,inner):os.utime(p,ns=(1600000000000000000,1600000001000000000))
 run([TOOLS['cp'],'--parents','--preserve=mode,ownership,timestamps','source/inner/item',str(target)],base)
 ok('parents_existing_metadata',all(same(p,q)and(p.stat().st_uid,p.stat().st_gid,p.stat().st_atime_ns,p.stat().st_mtime_ns)==(q.stat().st_uid,q.stat().st_gid,q.stat().st_atime_ns,q.stat().st_mtime_ns)for p,q in((source,target/'source'),(inner,target/'source/inner'))))
 target=base/'present';(target/'source/inner').mkdir(parents=True);(target/'source').chmod(0o700);(target/'source/inner').chmod(0o710)
 before=[(stat.S_IMODE(p.stat().st_mode),acl(p,base),acl(p,base,True))for p in(target/'source',target/'source/inner')]
 run([TOOLS['cp'],'--parents','--preserve=mode','source/inner/item',str(target)],base)
 after=[(stat.S_IMODE(p.stat().st_mode),acl(p,base),acl(p,base,True))for p in(target/'source',target/'source/inner')]
 ok('parents_existing_fast_path',before==after and payload(target/'source/inner/item')==payload(inner/'item'))
 (base/'alias').symlink_to('source',target_is_directory=True)
 for flag in ('-P','-H','-L'):
  target=copied('alias'+flag,'alias/inner/item',(flag,'--preserve=mode'))
  ok('parents_source_alias_'+flag[1:],not(target/'alias').is_symlink()and same(source,target/'alias')and same(inner,target/'alias/inner')and payload(target/'alias/inner/item')==payload(inner/'item'))
 alias_target=base/'alias-target';alias_target.mkdir();target=base/'dest-alias';target.mkdir();link=target/'source';link.symlink_to('../alias-target',target_is_directory=True)
 run([TOOLS['cp'],'--parents','--preserve=mode,ownership,timestamps','source/inner/item',str(target)],base)
 ok('parents_destination_alias',link.is_symlink()and os.readlink(link)=='../alias-target'and(link.lstat().st_uid,link.lstat().st_gid)==(source.stat().st_uid,source.stat().st_gid)and same(source,alias_target)and same(inner,alias_target/'inner')and payload(alias_target/'inner/item')==payload(inner/'item'))
 inner.chmod(0o111)
 target=None
 try:
  target=copied('search-only')
  ok('parents_search_without_read',stat.S_IMODE((target/'source/inner').stat().st_mode)==0o111 and payload(target/'source/inner/item')==payload(inner/'item'))
 finally:
  inner.chmod(0o750)
  if target is not None:(target/'source/inner').chmod(0o700)
 ordinary_mode_reference=base/'ordinary-mode-reference';ordinary_mode_reference.mkdir(mode=0o777)
 ordinary_new_mode=stat.S_IMODE(ordinary_mode_reference.stat().st_mode)
 inner.chmod(0o111);target=None
 try:
  target=copied('no-preserve',flags=('--no-preserve=mode',))
  ok('parents_no_preserve_mode',stat.S_IMODE((target/'source/inner').stat().st_mode)==ordinary_new_mode and payload(target/'source/inner/item')==payload(inner/'item'))
 finally:
  inner.chmod(0o750)
  if target is not None:(target/'source/inner').chmod(0o700)
def run_owned():
 need(os.getuid()==os.geteuid()==os.getgid()==os.getegid()==10001 and Path.cwd()==ROOT,'actual_owned_context')
 need(os.environ.get('LD_PRELOAD')is None and os.environ.get('LD_LIBRARY_PATH')is None,'loader_override')
 root=Path(tempfile.mkdtemp(prefix='coreutils-ordinary-',dir=ROOT));held=ident(root.lstat())[:5];other=None;closed=False;cases=[]
 def ok(name,value=True):
  need(name in LEGACY_CASES+PARENT_CASES and name not in cases and len(cases)<23,'ordinary_case_scope')
  need(value,'ordinary_'+name);cases.append(name)
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
  run_parent_cases(root,ok)
  need(tuple(cases)==LEGACY_CASES+PARENT_CASES,'complete_ordinary_case_inventory')
  binaries={name:{'path':TOOLS[name],'sha256':binary_hash(Path(TOOLS[name]))}for name in ('cp','mv','install')}
  clean(other,otherheld);other=None;clean(root,held);closed=True
  return {'schema':'coreutils_owned_cli_compatibility/1','status':'PASS','actual_uid':os.getuid(),'actual_gid':os.getgid(),'cases':cases,'actual_case_count':len(cases),'skip_count':0,
   'legacy_case_count':len(LEGACY_CASES),'parent_case_count':len(PARENT_CASES),'parent_ownership_and_timestamp_path_semantics_retained':True,
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
