"""Fresh GitHub-hosted CI only; dedicated Debian glibc candidate, default unarmed.

Borrow unchanged public IO/native-workspace primitives, never the old package
producer. No Ubuntu package installation or production image/serving operation.
"""
from pathlib import Path
from hashlib import sha256
from types import ModuleType
import argparse,errno,fcntl,json,os,re,stat,sys,time,uuid

HERE=Path(__file__).absolute().parent;ENTRY=Path(__file__).absolute();TRACKED=Path('deploy/maintenance/glibc_backport_two_profiles')
PYTHON='/usr/local/bin/python3';WORKER_ENTRY='sourcebuild.py';PACKAGE_VERSION='2.41-12+deb13u4+gold1';ROOT_PREFIX='glibc84243-two-trixie241';PROFILE_NAME='trixie241';BOOTSTRAP_PREP=[];_PROFILE_CONFIGURED=False
RUNTIME_PACKAGES=['libc6','libc-bin']
BASE='python:3.11.17-slim-trixie@sha256:b95327ce1439e687237841b0210677aeadd62d55a060a5f6bcb9c15bf6251dd3'
HOST_IO=(53351,'fc9fc8e4ae7fe1af75fd0b4c63fedb0ebfa5820148bb311c5eaafcaaa5bbbb82')
SCOPE_IO=(8831,'b2e6159d62ded26c81c5a72ad9dbda73f6029719451ff2b27d7edeefeae7b568')
CONFIRM='BUILD-ONLY-DISPOSABLE-CI-TWO-TRIXIE241-GLIBC-84243-FULL-PACKAGES-20261101-01'
LABEL='io.gold-trade.glibc84243-build';CAPS=('CHOWN','DAC_OVERRIDE','FOWNER','FSETID','SETGID','SETUID','SYS_CHROOT','KILL')
MEMORY=8*1024**3;PIDS=2048;GROWTH=48*1024**3;RESERVE=8*1024**3
MAX_OUTPUT=2*1024**3;MAX_FILE=512*1024**2
INSPECT='{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Config.Image}},"owner":{{json (index .Config.Labels "'+LABEL+'")}},"state":{{json .State.Status}},"running":{{json .State.Running}},"paused":{{json .State.Paused}},"pid":{{json .State.Pid}},"oom":{{json .State.OOMKilled}},"memory":{{json .HostConfig.Memory}},"swap":{{json .HostConfig.MemorySwap}},"cpu":{{json .HostConfig.NanoCpus}},"pids":{{json .HostConfig.PidsLimit}},"privileged":{{json .HostConfig.Privileged}},"init":{{json .HostConfig.Init}},"network":{{json .HostConfig.NetworkMode}},"endpoints":{{json .NetworkSettings.Networks}},"caps_add":{{json .HostConfig.CapAdd}},"caps_drop":{{json .HostConfig.CapDrop}},"security":{{json .HostConfig.SecurityOpt}},"mounts":{{json .Mounts}}}'
IDLE='import time;time.sleep(19800)'
IDLE_ARGV=[PYTHON,'-I','-S','-B','-u','-c',IDLE]
# Exact immutable-base inventory precedes any distro Python bootstrap. Removing
# this public hook here retains bootstrap DEBs for the later complete closure.
BASE_EVIDENCE=r'''set -eu; umask 022
test ! -e /build/bootstrap-evidence
mkdir -m 755 /build/bootstrap-evidence
dpkg-query -W '-f=${db:Status-Status}\t${binary:Package}\t${Version}\t${Architecture}\n' > /build/bootstrap-evidence/base-status.tsv
if test -e /etc/apt/apt.conf.d/docker-clean; then
 test -f /etc/apt/apt.conf.d/docker-clean; test ! -L /etc/apt/apt.conf.d/docker-clean
 cp /etc/apt/apt.conf.d/docker-clean /build/bootstrap-evidence/docker-clean
 rm /etc/apt/apt.conf.d/docker-clean
fi
printf 'IMMUTABLE_BASE_STATUS_AND_PUBLIC_CACHE_HOOK_PRESERVED\n'
'''

def need(ok,code):
 if ok is not True:raise RuntimeError(code)
def configure_profile(entry,value):
 """Closed reviewed Source presets; no untrusted caller policy or success DTO."""
 global HERE,ENTRY,BASE,PYTHON,IDLE_ARGV,BOOTSTRAP_PREP,PACKAGE_VERSION,ROOT_PREFIX,PROFILE_NAME,CONFIRM,WORKER_ENTRY,RUNTIME_PACKAGES,_PROFILE_CONFIGURED
 keys={'name','base','python','idle_argv','bootstrap_prepare','package_version','root_prefix','confirm','runtime_packages'}
 need(_PROFILE_CONFIGURED is False and type(value)is dict and set(value)==keys,'profile_shape')
 expected={'ubuntu243':'2.43-2ubuntu2.4+gold1'}
 name=value['name'];need(name in expected and value['package_version']==expected[name]and Path(entry).name=='host_'+name+'.py','profile_version')
 need(re.fullmatch(r'ubuntu:[A-Za-z0-9._-]+@sha256:[a-f0-9]{64}',value['base'])is not None
  and value['python']=='/usr/bin/python3'and type(value['idle_argv'])is list and len(value['idle_argv'])>0
  and type(value['bootstrap_prepare'])is list and len(value['bootstrap_prepare'])>0,'profile_builder')
 need(value['root_prefix']=='glibc84243-two-'+name and type(value['confirm'])is str and value['confirm'].startswith('BUILD-ONLY-DISPOSABLE-CI-'),'profile_authority')
 families={'ubuntu243':['libc6','libc-bin','libc-dev-bin','libc-gconv-modules-extra','libc6-dev','locales']}
 need(value['runtime_packages']==families[name],'profile_runtime_family');RUNTIME_PACKAGES=value['runtime_packages'][:]
 ENTRY=Path(entry).absolute();HERE=ENTRY.parent;BASE=value['base'];PYTHON=value['python'];IDLE_ARGV=value['idle_argv'];BOOTSTRAP_PREP=value['bootstrap_prepare'];PACKAGE_VERSION=value['package_version'];ROOT_PREFIX=value['root_prefix'];PROFILE_NAME=name;CONFIRM=value['confirm'];WORKER_ENTRY='worker_'+name+'.py';_PROFILE_CONFIGURED=True


def enc(value):return(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
def frame(s):return(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def load(raw,path):
 m=ModuleType('_glibc84243_'+Path(path).stem);m.__file__=str(path);exec(compile(raw,str(path),'exec'),m.__dict__);return m

def bootstrap(name,pin):
 path=HERE/name;parents=[(p,frame(p.lstat())[:5])for p in path.parents]
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC);closed=False
 try:
  before=frame(os.fstat(fd));flags=(fcntl.fcntl(fd,fcntl.F_GETFL),fcntl.fcntl(fd,fcntl.F_GETFD))
  need(before[3]in{0,1001}and stat.S_ISREG(before[2])and before[5:7]==(1,pin[0])and not before[2]&0o022,'bootstrap_metadata')
  raw=os.pread(fd,pin[0]+1,0);need((len(raw),sha256(raw).hexdigest())==pin,'bootstrap_pin')
  def check():
   need(all(p.resolve(strict=True)==p and frame(p.lstat())[:5]==v and stat.S_ISDIR(v[2])and v[3]in{0,1001}and not v[2]&0o022 for p,v in parents),'bootstrap_parent')
   need(path.resolve(strict=True)==path and frame(path.lstat())==before,'bootstrap_changed')
   if not state['closed']:need(frame(os.fstat(fd))==before and(fcntl.fcntl(fd,fcntl.F_GETFL),fcntl.fcntl(fd,fcntl.F_GETFD))==flags and flags[1]==fcntl.FD_CLOEXEC and os.lseek(fd,0,os.SEEK_CUR)==0,'bootstrap_OFD')
  state={'fd':fd,'closed':False,'identity':before[:6]};check();return load(raw,path),check,state
 except BaseException:os.close(fd);raise

def hosted(args):
 e=os.environ
 need(os.getuid()==os.geteuid()==1001 and e.get('GITHUB_ACTIONS')=='true'and e.get('RUNNER_ENVIRONMENT')=='github-hosted'
  and e.get('GITHUB_REPOSITORY')=='mohsenbarari/trading_bot'and e.get('GITHUB_REF')in{'refs/heads/main','refs/heads/security/remediation-20261023'},'fresh_GitHub_host_only')
 need(args.confirm==CONFIRM and re.fullmatch('[a-f0-9]{40}',args.commit or'')is not None and args.commit==e.get('GITHUB_SHA')
  and re.fullmatch('[a-f0-9]{64}',args.source_sha256 or'')is not None,'source_authority')
 repo=Path(args.repo);temp=Path(e.get('RUNNER_TEMP',''));root=Path(args.root);run_id=e.get('GITHUB_RUN_ID','');attempt=e.get('GITHUB_RUN_ATTEMPT','')
 need(re.fullmatch('[1-9][0-9]{0,19}',run_id)is not None and re.fullmatch('[1-9][0-9]{0,7}',attempt)is not None,'run_identity')
 need(repo.resolve(strict=True)==repo and repo==Path(e['GITHUB_WORKSPACE'])and HERE==repo/TRACKED
  and temp.resolve(strict=True)==temp and root==temp/(ROOT_PREFIX+'-'+run_id+'-'+attempt)and not root.exists(),'fresh_workspace')
 return repo,root

def validate(row,nonce,cid,context,workspace,*,offline):
 need(row['id']==cid and row['name']=='/glibc84243-'+nonce and row['image']==BASE and row['owner']==nonce,'owned_builder')
 need(row['memory']==row['swap']==MEMORY and row['cpu']==4000000000 and row['pids']==PIDS
  and row['privileged']is False and row['init']is True and row['oom']is False,'builder_resources')
 need(row['network']=='bridge'and set(row['endpoints'])==(set()if offline else{'bridge'}),'builder_network')
 normalize=lambda v:{s.removeprefix('CAP_')for s in v}
 need(type(row['caps_add'])is list and type(row['caps_drop'])is list and len(row['caps_add'])==len(CAPS)
  and normalize(row['caps_add'])==set(CAPS)and normalize(row['caps_drop'])=={'ALL'}
  and row['security']==['no-new-privileges:true','apparmor=unconfined'],'builder_caps')
 mounts={r['Destination']:(r.get('Type'),r.get('Source'),r.get('RW'))for r in row['mounts']}
 need(len(mounts)==len(row['mounts'])==2 and mounts=={'/public-context':('bind',str(context),False),'/build':('bind',str(workspace),True)},'builder_mounts')
 return row

MANIFEST=r'''from pathlib import Path
from hashlib import sha256
import json,os,stat
root=Path('/out');rows={};total=0
for p in sorted(root.iterdir()):
 s=p.lstat();assert p.resolve(strict=True)==p and stat.S_ISREG(s.st_mode) and s.st_uid==s.st_gid==0 and s.st_nlink==1 and stat.S_IMODE(s.st_mode)==0o644 and 0<s.st_size<=536870912
 assert p.suffix in {'.deb','.changes','.buildinfo','.json','.log','.sum','.tsv','.dsc','.xz','.test-result','.out',''}
 h=sha256();fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
 try:
  n=0
  while True:
   b=os.read(fd,65536)
   if not b:break
   n+=len(b);assert n<=s.st_size;h.update(b)
  assert n==s.st_size
 finally:os.close(fd)
 total+=n;assert total<=2147483648;rows[p.name]={'bytes':n,'sha256':h.hexdigest()}
assert {'source-proof.json','vendor-suite.json','old-counterfactual.json','old-counterfactual.log','abi-contract.json','provenance.json','full-Debian-build.log','two-profile-raw-tests.json','two-profile-raw-tests.tar.xz','two-profile-raw-libc.sum','preparation.json','dependency-status.tsv','signed-APT-metadata.tar.xz'}<=set(rows)
assert set(RUNTIME_PRODUCTS)<=set(rows)
print(json.dumps(rows,sort_keys=True))'''

def manifest_script():
 return MANIFEST.replace('RUNTIME_PRODUCTS',repr([name+'_'+PACKAGE_VERSION+'_'+('all'if name=='locales'else'amd64')+'.deb'for name in RUNTIME_PACKAGES]))



def preserve_two_profile_raw_failure(command,cid,nonce,destination,expected_directory,codec,export_guard):
 """Profile-declared fixed failed-only leaves, within the ORIGINAL cleanup end minus45s."""
 contract=codec.quarantine_contract(PROFILE_NAME);names=contract['names'];debs=contract['debs']
 docker=['/usr/bin/docker','--host','unix:///var/run/docker.sock'];captured={};rows={}
 receipt={'schema':'Two_profile_failed_only_quarantine_evidence_export/1','original_container_id':cid,'original_nonce':nonce,
  'disposition':'QUARANTINED_NOT_QUALIFIED','outputs':captured,'missing_fixed_leaves':sorted(names),
  'raw_only_not_vendor_or_security_qualified':True,'same_failed_forward_operation_resumed':False,'security_accepted':False,
  'manifest_admitted':False,'complete':False,'secondary_codes':[]}
 before=frame(destination.lstat())[:6]
 def check():
  export_guard()
  need(before==expected_directory and destination.resolve(strict=True)==destination and stat.S_ISDIR(before[2])and stat.S_IMODE(before[2])==0o700
   and before[3]==os.geteuid() and frame(destination.lstat())[:6]==before,'failure_export_directory')
 def owner():
  check();row=json.loads(command([*docker,'container','inspect','--format',INSPECT,cid],maximum=65536,seconds=10))
  need(row['id']==cid and row['owner']==nonce and row['name']=='/glibc84243-'+nonce and row['image']==BASE,'failure_export_owner');check()
 def manifest():
  check();value=json.loads(command([*docker,'container','exec',cid,PYTHON,'-I','-S','-B','-c',codec.failure_manifest_script(PROFILE_NAME)],maximum=16384,seconds=10));check()
  need(type(value)is dict and set(value)<=set(names),'failure_export_scope')
  for name,row in value.items():
   need(type(row)is dict and set(row)=={'bytes','sha256','full9','source_path'}and type(row['bytes'])is int and (0 if name in codec.EMPTY_RAW_NAMES else 1)<=row['bytes']<=names[name]
    and type(row['full9'])is list and len(row['full9'])==9 and all(type(item)is int for item in row['full9'])
    and row['full9'][6]==row['bytes'] and stat.S_ISREG(row['full9'][2])and stat.S_IMODE(row['full9'][2])==0o644
    and row['source_path']in({'/out/'+name,'/build/build-area/'+name}if name in debs else{'/out/'+name})
    and row['full9'][3:6]==([0,0,1]if row['source_path']=='/out/'+name else[10001,10001,1])and re.fullmatch('[a-f0-9]{64}',row['sha256'])is not None,'failure_export_bound')
  need(sum(row['bytes']for row in value.values())<=codec.QUARANTINE_MAX
   and sum(value[name]['bytes']for name in debs if name in value)<=codec.QUARANTINE_DEBS_MAX
   and sum(value[name]['bytes']for name in codec.QUARANTINE_PROOFS if name in value)<=codec.QUARANTINE_METADATA_MAX,'failure_export_aggregate')
  return value
 try:
  owner();rows=manifest();receipt['manifest_admitted']=True;receipt['admitted_sources']=rows
  # Full fixed aggregate admission above precedes the first Docker copy.
  for name,value in rows.items():
   check();target=destination/name;need(not target.exists()and not target.is_symlink(),'failure_export_exclusive')
   receipt['active_copy']={'name':name,'command_completed':False,'verified':False}
   command([*docker,'container','cp',cid+':'+value['source_path'],str(target)],maximum=65536,seconds=5);receipt['active_copy']['command_completed']=True;check()
   raw=codec.read_regular(target,names[name],empty=name in codec.EMPTY_RAW_NAMES,guard=check)
   need(codec.pin(raw)=={key:value[key]for key in('bytes','sha256')},'failure_export_changed');captured[name]=codec.pin(raw);receipt['active_copy']['verified']=True;receipt['active_copy']=None
  need(rows==manifest(),'failure_export_changed');owner();receipt['complete']=True
  receipt['missing_fixed_leaves']=sorted(set(names)-set(captured));check()
  codec.write_new(destination/'failure-export.json',enc(receipt),131072);check();return receipt
 except BaseException as primary:
  receipt['secondary_codes']=getattr(primary,'two_profile_secondary_codes',[])
  receipt['missing_fixed_leaves']=sorted(set(names)-set(captured))
  primary.public_quarantine_export=receipt
  # The caller retains this prefix in hosted-failure.json; do not spend the
  # reserved final45s on a separate receipt write after the cutoff.
  raise

def execute(args):
 need((PROFILE_NAME=='trixie241'and _PROFILE_CONFIGURED is False)or(PROFILE_NAME=='ubuntu243'and _PROFILE_CONFIGURED is True),'two_profile_only');repo,root=hosted(args);M,mcheck,mstate=bootstrap('host_io_original.py',HOST_IO)
 try:S,scheck,sstate=bootstrap('source_io.py',SCOPE_IO)
 except BaseException as primary:
  # The first original is already owned even if the second constructor fails.
  # Preserve that first cause and close only this registered original prefix.
  try:
   need(frame(os.fstat(mstate['fd']))[:6]==mstate['identity'],'bootstrap_foreign_FD');os.close(mstate['fd']);mstate['closed']=True
   try:os.fstat(mstate['fd'])
   except OSError as e:need(e.errno==errno.EBADF,'bootstrap_close')
   else:need(False,'bootstrap_close')
  except BaseException:primary.prefix_finalizer_codes=['first_bootstrap_close_unknown']
  raise
 scope=None;workspace=None;cid=None;nonce=uuid.uuid4().hex;primary=None;phase='capacity';closed=False;result=None;final=[];end=time.monotonic()+18000;forward_end=end-180;cleanup_state='NOT_CREATED';raw_codec=None;raw_failure=None;raw_export_codes=[]
 env={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','HOME':str(root),'LC_ALL':'C.UTF-8',
  'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null','GIT_OPTIONAL_LOCKS':'0','GIT_TERMINAL_PROMPT':'0'}
 def guard():
  need(time.monotonic()<forward_end,'hosted_forward_deadline');mcheck();scheck()
  if scope is not None:
   if not scope.finished:scope.check()
   else:
    for row in scope.rows:row.named()
  if workspace is not None:workspace.check()
  for p,old in initial.items():
   fs=os.statvfs(p);free=fs.f_bavail*fs.f_frsize;need(free>=RESERVE and old-free<=GROWTH,'disk_reserve')
 def public_log(path,raw):
  need(type(raw)is bytes and 0<len(raw)<=256*1024**2,'public_log_bound')
  w=scope.create(path,256*1024**2)
  for offset in range(0,len(raw),65536):w.append(raw[offset:offset+65536])
  w.seal();w.close()
 def command(argv,maximum=65536,seconds=60):return M.run(argv,env=env,deadline=min(forward_end,time.monotonic()+seconds),maximum=maximum,guard=guard)
 def export_guard():
  need(time.monotonic()<end-45,'hosted_export_deadline');mcheck();scheck()
 def export_command(argv,maximum=65536,seconds=5):
  export_guard();return M.run(argv,env=env,deadline=min(end-45,time.monotonic()+seconds),maximum=maximum,guard=export_guard)
 def cleanup_command(argv,maximum=65536,seconds=30):
  # Exact-owned failure stop needs no new allocation or healthy forward Scope.
  # Its finite reserve was declared above before any source/workspace loans.
  def cleanup_guard():need(time.monotonic()<end,'hosted_cleanup_deadline');mcheck();scheck()
  return M.run(argv,env=env,deadline=min(end,time.monotonic()+seconds),maximum=maximum,guard=cleanup_guard)
 initial={str(root.parent):os.statvfs(root.parent).f_bavail*os.statvfs(root.parent).f_frsize,'/':os.statvfs('/').f_bavail*os.statvfs('/').f_frsize}
 try:
  # The unchanged helper validates its actual facts before returning. Observe
  # those same public values BEFORE that validation can refuse capacity, so a
  # failed preflight is diagnosable without creating the reserved operation.
  original_validate=M.validate_host
  def capacity_observed(value):
   raw=enc({'schema':'glibc84243_actual_host_preflight/1','actual_host_facts':value,'initial_available_bytes':initial,
    'required_initial_free_bytes':GROWTH+RESERVE,'required_CPUs':4,'required_RAM_bytes':12*1024**3,'security_accepted':False})
   need(len(raw)<=16384,'capacity_telemetry_bound');print(raw.decode(),end='',flush=True);return original_validate(value)
  M.validate_host=capacity_observed
  try:facts=M.host_facts(root.parent)
  finally:M.validate_host=original_validate
  need(min(initial.values())>=GROWTH+RESERVE,'capacity_floor')
  root.mkdir(mode=0o700);context=root/'context';context.mkdir(mode=0o755);context.chmod(0o755);reports=root/'reports';reports.mkdir(mode=0o700);raw_failure_dir=reports/'two-profile-raw-failure';raw_failure_dir.mkdir(mode=0o700);raw_failure_identity=frame(raw_failure_dir.lstat())[:6];out=root/'package-output';out.mkdir(mode=0o700)
  config=root/'docker-client';config.mkdir(mode=0o700);env['DOCKER_CONFIG']=str(config)
  scope=S.Scope(18000);own=scope.open(ENTRY,(ENTRY.stat().st_size,args.source_sha256),131072)
  # Manifest itself is a tracked Git blob; no self-referential hash cycle.
  need(command(['/usr/bin/git','-C',str(repo),'rev-parse','HEAD'],maximum=64).strip().decode()==args.commit,'checkout_commit')
  raw=command(['/usr/bin/git','-C',str(repo),'show',args.commit+':'+str(TRACKED/'PINS.json')],maximum=131072);pins=json.loads(raw)
  pin=(len(raw),sha256(raw).hexdigest());scope.open(HERE/'PINS.json',pin,131072)
  need(set(pins)=={'schema','files'}and pins['schema']=='glibc84243_source_recipe/1','recipe_manifest')
  for name,row in pins['files'].items():
   need('/'not in name and name not in{'.','..'}and type(row['bytes'])is int and 0<row['bytes']<=131072,'recipe_leaf')
   held=scope.open(HERE/name,(row['bytes'],row['sha256']),131072);body=b''.join(held.read(min(65536,row['bytes']-n),n)for n in range(0,row['bytes'],65536))
   expected=command(['/usr/bin/git','-C',str(repo),'show',args.commit+':'+str(TRACKED/name)],maximum=row['bytes']+1)
   need(body==expected,'tracked_recipe_blob')
   if name=='two_profile_evidence.py':raw_codec=load(body,HERE/name)
   target=context/name;target.write_bytes(body);target.chmod(0o644);scope.open(target,(row['bytes'],row['sha256']),131072)
  manifest={'schema':'glibc84243_allowlisted_context/1','files':pins['files'],'immutable_base':BASE,'profile':PROFILE_NAME};data=enc(manifest);target=context/'context-manifest.json';target.write_bytes(data);target.chmod(0o644);scope.open(target,(len(data),sha256(data).hexdigest()),131072)
  need(raw_codec is not None,'two_profile_codec_missing');phase='native_workspace';workspace=M.create_native_workspace(root,command);guard()
  docker=['/usr/bin/docker','--host','unix:///var/run/docker.sock']
  def cli(parts,seconds=60,maximum=65536):return command([*docker,*parts],seconds=seconds,maximum=maximum)
  def inspect(offline):return validate(json.loads(cli(['container','inspect','--format',INSPECT,cid])),nonce,cid,context,workspace.path,offline=offline)
  phase='tool_interface';need('--timeout'in cli(['container','stop','--help']).decode(),'unsupported_stop_API')
  phase='public_base';log=cli(['pull',BASE],seconds=600,maximum=16*1024**2);public_log(reports/'pull.log',log)
  phase='builder_create';argv=['container','create','--name','glibc84243-'+nonce,'--label',LABEL+'='+nonce,'--memory',str(MEMORY),'--memory-swap',str(MEMORY),'--cpus','4','--pids-limit',str(PIDS),'--shm-size','134217728','--ulimit','core=0:0','--init','--cap-drop','ALL','--security-opt','no-new-privileges:true','--security-opt','apparmor=unconfined','--network','bridge','--log-driver','none','--workdir','/build','--mount','type=bind,src='+str(context)+',dst=/public-context,readonly','--mount','type=bind,src='+str(workspace.path)+',dst=/build']
  for cap in CAPS:argv+=['--cap-add',cap]
  argv+=['--entrypoint',IDLE_ARGV[0],BASE,*IDLE_ARGV[1:]]
  cid=cli(argv).decode().strip();need(re.fullmatch('[a-f0-9]{64}',cid)is not None,'builder_CID');cleanup_state='HELD';cli(['container','start',cid]);inspect(False)
  phase='immutable_base_dependency_inventory';cli(['container','exec',cid,'/bin/sh','-c',BASE_EVIDENCE])
  if BOOTSTRAP_PREP:
   phase='fixed_distro_Python_bootstrap';log=cli(['container','exec',cid,*BOOTSTRAP_PREP],seconds=1200,maximum=64*1024**2);public_log(reports/'Python-bootstrap.log',log)
  stage=r'''from pathlib import Path
from hashlib import sha256
import json,os
source=Path('/public-context');target=Path('/recipe');target.mkdir(mode=0o755)
m=json.loads((source/'context-manifest.json').read_bytes())
for name,pin in m['files'].items():
 p=source/name;b=p.read_bytes();assert len(b)==pin['bytes'] and sha256(b).hexdigest()==pin['sha256'];q=target/name;assert not q.exists();q.write_bytes(b);q.chmod(0o644)
b=(source/'context-manifest.json').read_bytes();(target/'context-manifest.json').write_bytes(b);(target/'context-manifest.json').chmod(0o644)
print('PUBLIC_COPIES_VERIFIED')'''
  cli(['container','exec',cid,PYTHON,'-I','-S','-B','-c',stage])
  phase='authenticated_prepare';log=cli(['container','exec',cid,PYTHON,'-I','-S','-B','/recipe/'+WORKER_ENTRY,'--prepare'],seconds=3600,maximum=128*1024**2);public_log(reports/'prepare.log',log);inspect(False)
  phase='network_cutoff';cli(['network','disconnect','bridge',cid]);inspect(True)
  need(json.loads(cli(['container','exec',cid,PYTHON,'-I','-S','-B','-c',"import os,json;print(json.dumps(sorted(os.listdir('/sys/class/net'))))"]))==['lo'],'offline_loopback')
  need(forward_end-time.monotonic()>=14100,'full_build_and_finalization_budget')
  phase='full_Debian_build';log=cli(['container','exec',cid,PYTHON,'-I','-S','-B','/recipe/'+WORKER_ENTRY,'--build'],seconds=13800,maximum=256*1024**2);public_log(reports/'build.log',log);inspect(True)
  phase='public_export';rows=json.loads(cli(['container','exec',cid,PYTHON,'-I','-S','-B','-c',manifest_script()],maximum=131072));need(0<len(rows)<=96,'output_inventory')
  for name,row in rows.items():
   need(Path(name).name==name and 0<row['bytes']<=MAX_FILE,'output_leaf');cli(['container','cp',cid+':/out/'+name,str(out/name)],seconds=180)
   scope.open(out/name,(row['bytes'],row['sha256']),MAX_FILE)
  again=json.loads(cli(['container','exec',cid,PYTHON,'-I','-S','-B','-c',manifest_script()],maximum=131072));need(rows==again,'export_changed')
  phase='stop_owned_builder';cli(['container','stop','--timeout','10',cid],seconds=30);last=inspect(True);need(last['running']is False and last['pid']==0 and not last['paused'],'builder_not_stopped');cleanup_state='STOPPED'
  scope.finish();guard();closed=True
  result={'schema':'Root_disposable_glibc84243_full_Debian_candidate_build/1','status':'PACKAGES_BUILT_PENDING_TWO_PROFILE_ROOT_REVIEW','commit':args.commit,'run_id':os.environ['GITHUB_RUN_ID'],'run_attempt':os.environ['GITHUB_RUN_ATTEMPT'],'base':BASE,'profile':PROFILE_NAME,'package_version':PACKAGE_VERSION,'outputs':rows,'actual_host_capacity':facts,'native_workspace':workspace.observation(),'original_Source_and_export_readers_physical_finality':True,'ordinary_new_image_build_scan_runtime_new_old_restore_and_rollout_still_required':True,'vendor_qualification':False,'Host_packages_eligible':False,'two_profile_XPASS_Root_review':'PENDING','production_or_Ubuntu_packages_installed':False,'security_accepted':False}
 except BaseException as error:
  primary=error
  observed=getattr(error,'public_command_failure',None)
  if scope is not None and not scope.finished and type(observed)is dict and observed.get('captured_output'):
   try:public_log(reports/(phase+'-partial-failure.log'),observed['captured_output'])
   except BaseException:pass
  if phase=='full_Debian_build'and cid is not None and raw_codec is not None:
   try:raw_failure=preserve_two_profile_raw_failure(export_command,cid,nonce,raw_failure_dir,raw_failure_identity,raw_codec,export_guard)
   except BaseException as error:
    raw_failure=getattr(error,'public_quarantine_export',None)
    raw_export_codes.append(str(error)if type(error)is RuntimeError and re.fullmatch('[A-Za-z0-9_]{1,80}',str(error))else'failure_export_unknown')
    final.append(type(error).__name__)
 finally:
  if cid is not None and not closed:
   try:
    value=json.loads(cleanup_command(['/usr/bin/docker','--host','unix:///var/run/docker.sock','container','inspect','--format',INSPECT,cid]))
    need(value['id']==cid and value['owner']==nonce and value['name']=='/glibc84243-'+nonce and value['image']==BASE,'cleanup_owner')
    cleanup_command(['/usr/bin/docker','--host','unix:///var/run/docker.sock','container','stop','--timeout','10',cid],seconds=30)
    stopped=json.loads(cleanup_command(['/usr/bin/docker','--host','unix:///var/run/docker.sock','container','inspect','--format',INSPECT,cid]))
    need(stopped['id']==cid and stopped['owner']==nonce and stopped['name']=='/glibc84243-'+nonce and stopped['image']==BASE
     and stopped['running']is False and stopped['paused']is False and stopped['pid']==0,'cleanup_not_closed');cleanup_state='STOPPED'
   except BaseException as error:final.append(type(error).__name__)
  if scope is not None and not scope.finished:
   try:scope.finish()
   except BaseException as error:final.append(type(error).__name__)
  if workspace is not None:
   try:
    fd=workspace.fd;workspace.close()
    try:os.fstat(fd)
    except OSError as e:need(e.errno==errno.EBADF,'workspace_close')
    else:need(False,'workspace_close')
   except BaseException as error:final.append(type(error).__name__)
  for check,state in((scheck,sstate),(mcheck,mstate)):
   prefix_error=None
   try:check()
   except BaseException as error:prefix_error=error
   try:
    need(frame(os.fstat(state['fd']))[:6]==state['identity'],'bootstrap_foreign_FD')
    os.close(state['fd']);state['closed']=True
    try:os.fstat(state['fd'])
    except OSError as e:need(e.errno==errno.EBADF,'bootstrap_close')
    else:need(False,'bootstrap_close')
    check()
   except BaseException as error:final.append(type(error).__name__)
   if prefix_error is not None:final.append(type(prefix_error).__name__)
 if primary is not None or final:
  code=str(primary)if type(primary)is RuntimeError and re.fullmatch('[A-Za-z0-9_]{1,80}',str(primary))else'refused_or_unknown'
  error={'status':'FAILED','phase':phase,'code':code,'finalizer_classes':final,'container_id':cid,'nonce':nonce,'owned_container_closure':cleanup_state,'raw_evidence_export':raw_failure,'raw_evidence_export_codes':raw_export_codes,'retry_allowed':False,'security_accepted':False}
  if root.exists():M.save(root/'hosted-failure.json',enc(error))
  print(json.dumps(error,sort_keys=True));return 1
 need(time.monotonic()<end,'final_deadline');M.save(root/'result.json',enc(result));print(json.dumps(result,sort_keys=True));return 0


def main(argv=None):
 p=argparse.ArgumentParser();p.add_argument('--execute-hosted-build',action='store_true');p.add_argument('--confirm');p.add_argument('--repo');p.add_argument('--root');p.add_argument('--commit');p.add_argument('--source-sha256');a=p.parse_args(argv)
 if not a.execute_hosted_build:print('{"status":"UNARMED","build":false}');return 0
 try:return execute(a)
 except BaseException as e:print(json.dumps({'status':'FAILED','code':str(e)if type(e)is RuntimeError and re.fullmatch('[A-Za-z0-9_]{1,80}',str(e))else'unknown','prefix_finalizer_codes':getattr(e,'prefix_finalizer_codes',[]),'security_accepted':False}));return 1
if __name__=='__main__':raise SystemExit(main())
