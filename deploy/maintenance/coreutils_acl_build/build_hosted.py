#!/usr/bin/env python3
"""Explicit GitHub-hosted bounded Coreutils ACL package producer; default UNARMED.

The Native02 profile is separate and unchanged. This producer requires a fresh
workflow-attested Ubuntu24.04 UID1001 VM; it never builds on production storage.
"""
from contextlib import ExitStack
from hashlib import sha256
from pathlib import Path
import argparse,io,json,os,re,selectors,signal,stat,subprocess,sys,time,uuid,zipfile
from types import ModuleType

BASE='python:3.11.17-slim-trixie@sha256:b95327ce1439e687237841b0210677aeadd62d55a060a5f6bcb9c15bf6251dd3'
VERSION='9.7-3+gold1'
MEMORY=8*1024**3;CPUS=4*10**9;PIDS=512
CONFIRM='BUILD-ONLY-HOSTED-COREUTILS-DESCRIPTOR-ACL-PACKAGE-20261027-01'
DOCKER='/usr/bin/docker';HOST='unix:///var/run/docker.sock'
DOCKER_ROOT=Path('/var/lib/docker')
HOST_UID=1001;DISK_GROWTH=48*1024**3;RESERVE=8*1024**3
TRACKED=Path('deploy/maintenance/coreutils_acl_build')
SOURCE_ROOT=Path(__file__).absolute().parent
CAPS=('CHOWN','DAC_OVERRIDE','FOWNER','FSETID','SETGID','SETUID','SYS_CHROOT')
LABEL='trading-bot.security.hosted-coreutils-acl-package-build'
PINS={'0001-coreutils-bound-acl-callers.patch': (60929, '3c91b4386c7608b4620422daaf88ec561d3bc709140ae29dc904ed54a9833d1d'), '0002-coreutils-unfiltered-vendor-tests.patch': (304, 'aac49190579f7b17df5d9cd6cb0d622a1b7ebe78950e3b8e27aeb1825441541d'), 'Dockerfile': (363, 'd1b16de84cd3982f50049b3fc7fcfd3403736468413fd8642a0c264bcefb9dd2'), 'gnulib-fd0-fixture.mk': (495, '9baaa4fa20576ae80793dba2f39362ccc313a7da7e6bf5336267130194ee23f7'), 'local-source-packet.json': (13474, '8300cbac75601f87591263811f95514e1faa8d99c25113189b8e4b91b13f8e0e'), 'ordinary_fd_acl.c': (9979, '5a716da021e7c0e72058f4bf1537b44fae28a5c15fa445d5d99905d9f6025936'), 'ordinary_gnulib_fd0.c': (5384, '17055155d7adb365afdafa5ac7e6e6bf017081f285796cc2770296621818127d'), 'owned-fixture.mk': (671, 'a47139700d07f4dda166c52c63acc8f980dbd28790bcfc91e5280fb82bcfefa0'), 'print-vendor-selection.mk': (398, '26215f73abbcae0e206faf07fbf11389842aa3b5cf0943743213e4408494ef16'), 'root-source-approval.json': (1963, '39f1ddaffbe9f22ffad55edbe07556a62238e606b7a1f05685a6c712592444fa'), 'source-manifest.json': (10477, '600d00b191cdd271ba2c65abb6aa9c3c8f992d47a7d35c0e8572bc33786d6cda'), 'sourcebuild.py': (44892, '78dc7d72cbc9a41055b2c34ac3540bc7c25ce25456be6d85d0aff05456cd4aea'), 'verify_ordinary.py': (9095, 'd4e8233e08441c3630ac3c4d56779d21481f11a60dc0bc52ef25d408579f5e5c'), 'verify_vendor_suite.py': (13055, '3db1e3bf662282fbeea5c43c68713f01d5f9996fdfb202bbca87fdf6d55bafcd'), 'inputs/libacl1_2.4.0-1~trixie+gold1_amd64.deb': (37076, 'bbb12e190bfe19b876f73a669848ebd8d04293d95000babf51058c88879966b1')}
OUTPUTS={'coreutils_9.7-3+gold1_amd64.deb': 67108864, 'coreutils_9.7-3+gold1_amd64.changes': 4194304, 'coreutils_9.7-3+gold1_amd64.buildinfo': 4194304, 'acl_2.4.0-1~trixie+gold1_amd64.changes': 4194304, 'acl_2.4.0-1~trixie+gold1_amd64.buildinfo': 4194304, 'source-proof.json': 4194304, 'sdk-proof.json': 4194304, 'abi-contract.json': 4194304, 'ordinary-compatibility.json': 4194304, 'provenance.json': 4194304, 'runtime-payload.json': 4194304, 'caller-proof.json': 4194304, 'vendor-suite.json': 4194304, 'generated-build-proof.json': 4194304}
IDLE='import time; time.sleep(18000)'
INSPECT='{"id":{{json .Id}},"name":{{json .Name}},"image_reference":{{json .Config.Image}},"label":{{json (index .Config.Labels "'+LABEL+'")}},"state":{{json .State.Status}},"running":{{json .State.Running}},"paused":{{json .State.Paused}},"pid":{{json .State.Pid}},"memory":{{json .HostConfig.Memory}},"memory_swap":{{json .HostConfig.MemorySwap}},"nano_cpus":{{json .HostConfig.NanoCpus}},"pids":{{json .HostConfig.PidsLimit}},"privileged":{{json .HostConfig.Privileged}},"init":{{json .HostConfig.Init}},"readonly_rootfs":{{json .HostConfig.ReadonlyRootfs}},"tmpfs":{{json .HostConfig.Tmpfs}},"network_mode":{{json .HostConfig.NetworkMode}},"networks":{{json .NetworkSettings.Networks}},"caps_add":{{json .HostConfig.CapAdd}},"caps_drop":{{json .HostConfig.CapDrop}},"security":{{json .HostConfig.SecurityOpt}},"mounts":{{json .Mounts}}}'
OFFLINE="import json,os; print(json.dumps(sorted(os.listdir('/sys/class/net'))))"
# This reads only the finite owned public package/provenance outputs, never
# business inputs. Full9 includes no atime, so normal archive reads are neutral.
MANIFEST=r'''
from pathlib import Path
from hashlib import sha256
import json,os,stat
root=Path('/out');limits=LIMITS
def ident(s):return [s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns]
assert root.resolve(strict=True)==root and root.stat().st_uid==root.stat().st_gid==0 and not root.stat().st_mode&0o022
assert len(list(root.iterdir()))==len(limits) and {p.name for p in root.iterdir()}==set(limits)
rows={}
for name,maximum in limits.items():
 p=root/name;s=p.lstat();before=ident(s)
 assert p.resolve(strict=True)==p and stat.S_ISREG(s.st_mode) and s.st_uid==s.st_gid==0 and s.st_nlink==1 and stat.S_IMODE(s.st_mode)==0o644 and 0<s.st_size<=maximum
 fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
 try:
  assert ident(os.fstat(fd))==before
  h=sha256();count=0
  while True:
   part=os.read(fd,min(65536,s.st_size-count+1))
   if not part:break
   count+=len(part);assert count<=s.st_size;h.update(part)
  assert count==s.st_size and ident(os.fstat(fd))==ident(p.lstat())==before
 finally:os.close(fd)
 assert ident(p.lstat())==before
 rows[name]={'bytes':count,'sha256':h.hexdigest(),'identity':before}
assert {p.name for p in root.iterdir()}==set(limits)
print(json.dumps(rows,sort_keys=True))
'''.replace('LIMITS',repr(OUTPUTS))

OLD_ARTIFACT_ID=12567979094;OLD_RUN_ID=40115339187
OLD_ZIP_PIN=(933904,'d367fb3c1b92f069538a6e90a77ea110efec6dccd9286a8f0e1359b285bc1765')
REPOSITORY='mohsenbarari/trading_bot'
PHASE='unarmed'
OBSERVED_CAPACITY=None
RECIPE_NAMES=tuple(name for name in PINS if not name.startswith('inputs/'))
STAGE=r'''
from pathlib import Path
from hashlib import sha256
import json,os,stat,time
pins=PINS;verify=VERIFY
source=Path('/public-context');target=Path('/recipe');inputs=Path('/inputs')
def ident(s):return(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def need(v):
 if not v:raise RuntimeError('public_copy_refused')
def copy_public(original,destination,pin):
 parents=[(p,ident(p.lstat())[:5])for p in (*original.parents,*destination.parents)]
 def check_parents():
  need(all(p.resolve(strict=True)==p and stat.S_ISDIR(old[2])and old[3]in(0,1001)and not old[2]&0o022 and ident(p.lstat())[:5]==old for p,old in parents))
 check_parents();before=ident(original.lstat());need(original.resolve(strict=True)==original and stat.S_ISREG(before[2])and before[3]in(0,1001)and before[5]==1 and not before[2]&0o022 and before[6]==pin[0])
 left=os.open(original,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK);right=None;end=time.monotonic()+30
 try:
  need(ident(os.fstat(left))==before);right=os.open(destination,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600);h=sha256();count=0
  while True:
   need(time.monotonic()<end);part=os.read(left,min(65536,pin[0]-count+1))
   if not part:break
   count+=len(part);need(count<=pin[0]);h.update(part);view=memoryview(part)
   while view:n=os.write(right,view);need(n>0);view=view[n:]
  need((count,h.hexdigest())==pin);os.fsync(right);after=ident(os.fstat(right));need(after[2:7]==(stat.S_IFREG|0o600,0,0,1,pin[0])and ident(destination.lstat())==after)
  need(ident(os.fstat(left))==ident(original.lstat())==before);check_parents()
 finally:
  try:
   if right is not None:os.close(right)
  finally:os.close(left)
 check_parents();need(ident(original.lstat())==before and ident(destination.lstat())==after)
def binding(path,pin):
 before=ident(path.lstat());need(path.resolve(strict=True)==path and before[2:7]==(stat.S_IFREG|0o600,0,0,1,pin[0]));fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC);h=sha256();count=0
 try:
  need(ident(os.fstat(fd))==before)
  while True:
   part=os.read(fd,min(65536,pin[0]-count+1))
   if not part:break
   count+=len(part);need(count<=pin[0]);h.update(part)
  need((count,h.hexdigest())==pin and ident(os.fstat(fd))==ident(path.lstat())==before)
 finally:os.close(fd)
 need(ident(path.lstat())==before);return{'bytes':count,'sha256':h.hexdigest(),'identity':list(before)}
if not verify:
 need(not target.exists()and not inputs.exists());target.mkdir(mode=0o755);(target/'inputs').mkdir(mode=0o755);inputs.mkdir(mode=0o755)
 for name,pin in pins.items():
  copy_public(source/name,target/name,pin)
  if name.startswith('inputs/'):copy_public(source/name,inputs/Path(name).name,pin)
rows={str(target/name):binding(target/name,pin)for name,pin in pins.items()}
rows.update({str(inputs/Path(name).name):binding(inputs/Path(name).name,pin)for name,pin in pins.items()if name.startswith('inputs/')})
need({p.name for p in target.iterdir()}=={Path(n).name for n in pins if not n.startswith('inputs/')}|{'inputs'}and {p.name for p in(target/'inputs').iterdir()}=={Path(n).name for n in pins if n.startswith('inputs/')}and {p.name for p in inputs.iterdir()}=={Path(n).name for n in pins if n.startswith('inputs/')})
print(json.dumps(rows,sort_keys=True))
'''


def stage_code(context,verify):
 pins=dict(PINS)
 for name in ('context-manifest.json','.dockerignore'):
  item=Held(context/name,65536)
  try:pins[name]=(item.binding['bytes'],item.binding['sha256'])
  finally:item.close()
 return STAGE.replace('pins=PINS;verify=VERIFY','pins='+repr(pins)+';verify='+repr(verify),1)


def validate_copies(value):
 expected={'/recipe/'+name:pin for name,pin in PINS.items()}
 expected.update({'/inputs/'+Path(name).name:pin for name,pin in PINS.items()if name.startswith('inputs/')})
 require(type(value)is dict and set(value)==set(expected)|{'/recipe/context-manifest.json','/recipe/.dockerignore'},'public_copy_inventory')
 for name,row in value.items():
  require(type(row)is dict and set(row)=={'bytes','sha256','identity'}and type(row['bytes'])is int and 0<row['bytes']<=32*1024**2
   and type(row['sha256'])is str and re.fullmatch('[a-f0-9]{64}',row['sha256']),'public_copy_binding')
  require(type(row['identity'])is list and len(row['identity'])==9 and all(type(v)is int for v in row['identity'])
   and row['identity'][2:7]==[stat.S_IFREG|0o600,0,0,1,row['bytes']],'public_copy_identity')
  if name in expected:require((row['bytes'],row['sha256'])==expected[name],'public_copy_pin')
 return value

def require(value,code):
 if not value:raise RuntimeError(code)
def ident(s):return (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def encoded(value,maximum=4*1024**2):
 parts=[];count=0
 for part in json.JSONEncoder(sort_keys=True,separators=(',',':'),allow_nan=False).iterencode(value):
  raw=part.encode();require(count+len(raw)+1<=maximum,'receipt_bound');count+=len(raw);parts.append(raw)
 return b''.join(parts)+b'\n'
def parent_frame(path):
 rows=[]
 for p in path.parents:
  s=p.lstat();require(p.resolve(strict=True)==p and stat.S_ISDIR(s.st_mode) and s.st_uid in {0,HOST_UID} and not s.st_mode&0o022,'parent')
  rows.append((p,ident(s)[:5]))
 return rows
def parents_ok(rows):require(all(p.resolve(strict=True)==p and ident(p.lstat())[:5]==old for p,old in rows),'parent_changed')
class Held:
 def __init__(self,path,maximum,pin=None,*,allow_empty=False):
  self.path=path;self.parents=parent_frame(path);self.before=ident(path.lstat())
  require(type(allow_empty)is bool and path.resolve(strict=True)==path and stat.S_ISREG(self.before[2]) and self.before[3]in{0,HOST_UID} and self.before[5]==1 and not self.before[2]&0o022 and (0<=self.before[6]<=maximum if allow_empty else 0<self.before[6]<=maximum),'public_file')
  self.fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
  try:
   self.check();h=sha256();parts=[];count=0
   while True:
    part=os.read(self.fd,min(65536,self.before[6]-count+1))
    if not part:break
    count+=len(part);require(count<=self.before[6],'public_bound');h.update(part)
    if maximum<=65536:parts.append(part)
   require(count==self.before[6],'public_size');self.binding={'bytes':count,'sha256':h.hexdigest()}
   require(pin is None or (count,h.hexdigest())==pin,'public_pin');self.raw=b''.join(parts);self.check()
  except BaseException:os.close(self.fd);self.fd=None;raise
 def check(self):parents_ok(self.parents);require(ident(os.fstat(self.fd))==ident(self.path.lstat())==self.before,'public_changed')
 def named(self):parents_ok(self.parents);require(ident(self.path.lstat())==self.before,'public_closed_changed')
 def close(self):
  try:self.check()
  finally:os.close(self.fd);self.fd=None
  self.named()
def context_inventory(context):
 expected={Path(name).name for name in PINS if not name.startswith('inputs/')}|{'inputs','context-manifest.json','.dockerignore'}
 with os.scandir(context) as stream:
  entries=[]
  for row in stream:entries.append(row.name);require(len(entries)<=len(expected),'context_inventory')
 require(set(entries)==expected and len(entries)==len(expected),'context_inventory')
 expected_inputs={Path(name).name for name in PINS if name.startswith('inputs/')}
 with os.scandir(context/'inputs') as stream:
  entries=[]
  for row in stream:entries.append(row.name);require(len(entries)<=len(expected_inputs),'context_inventory')
 require(set(entries)==expected_inputs and len(entries)==len(expected_inputs),'context_inventory')
def new_dir(path):
 require(path.is_absolute() and ',' not in str(path) and '\n' not in str(path) and not path.exists() and path.parent.resolve(strict=True)==path.parent,'new_directory')
 parent_frame(path);path.mkdir(mode=0o700)
def save(path,raw):
 require(type(raw) is bytes and len(raw)<=4*1024**2,'receipt_bound')
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
 try:
  view=memoryview(raw)
  while view:n=os.write(fd,view);require(n>0,'receipt_write');view=view[n:]
  os.fsync(fd)
 finally:os.close(fd)
def save_known_log(path,raw,owners):
 """Known command bytes; creation frame and reader precede writer close.

 The outer owner retains this original reader/frame through every later source
 and output close. No post-close pathname metadata is adopted as authority.
 """
 require(type(raw)is bytes and len(raw)<=128*1024**2 and type(owners)is list,'known_log_bound')
 parents=parent_frame(path);parents_ok(parents)
 writer=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
 item=None
 try:
  view=memoryview(raw)
  while view:n=os.write(writer,view);require(n>0,'log_write');view=view[n:]
  os.fsync(writer);created=ident(os.fstat(writer));parents_ok(parents)
  require(created==ident(path.lstat()),'known_log_creation_changed')
  item=Held(path,128*1024**2,(len(raw),sha256(raw).hexdigest()),allow_empty=True);owners.append(item)
  require(item.before==created and ident(os.fstat(writer))==created,'known_log_creation_changed');item.check()
 finally:os.close(writer)
 item.check();parents_ok(parents);return item
def cleanup_child(child):
 if child.returncode is not None:return
 try:os.waitid(os.P_PID,child.pid,os.WEXITED|os.WNOHANG|os.WNOWAIT)
 except ChildProcessError:return
 try:os.killpg(child.pid,signal.SIGKILL)
 except ProcessLookupError:pass
 child.wait(timeout=10)
def run(argv,*,env,deadline,maximum,log=None,guard=lambda:None):
 guard();require(time.monotonic()<deadline,'deadline')
 child=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,env=env,close_fds=True,start_new_session=True)
 total=0;parts=[];prefix=b'';tail=b'';failure=None
 try:
  with selectors.DefaultSelector() as selector:
   fd=child.stdout.fileno();os.set_blocking(fd,False);selector.register(fd,selectors.EVENT_READ)
   while True:
    guard();left=deadline-time.monotonic();require(left>0,'deadline')
    if not selector.select(min(1,left)):continue
    block=os.read(fd,min(65536,maximum-total+1))
    if not block:break
    total+=len(block);require(total<=maximum,'command_output_bound')
    if len(prefix)<16384:prefix+=block[:16384-len(prefix)]
    tail=(tail+block)[-16384:]
    if log is None:parts.append(block)
    else:
     view=memoryview(block)
     while view:n=os.write(log,view);require(n>0,'log_write');view=view[n:]
  require(child.wait(timeout=max(.1,deadline-time.monotonic()))==0,'command_failed');guard()
 except BaseException as error:
  failure=error
  try:cleanup_child(child)
  except BaseException:pass
 finally:
  try:child.stdout.close()
  except BaseException as error:
   if failure is None:failure=error
  try:guard();require(child.stdout.closed and child.returncode is not None,'command_physical_close')
  except BaseException as error:
   if failure is None:failure=error
 if failure is not None:
  # Public build commands only use this finite evidence in phase_log. It is
  # explicitly partial and never licenses success, resume, or a package.
  failure.public_command_failure={'actual_returncode':child.returncode,'observed_output_bytes':total,
   'actual_pipe_closed':child.stdout.closed,'prefix':prefix,'tail':tail}
  raise failure
 return b''.join(parts)
def create_argv(nonce,context):
 args=[DOCKER,'--host',HOST,'container','create','--name','security-hosted-coreutils-acl-package-build-'+nonce,
  '--label',LABEL+'='+nonce,'--memory',str(MEMORY),'--memory-swap',str(MEMORY),'--cpus','4','--pids-limit',str(PIDS),
  '--shm-size','134217728','--ulimit','core=0:0','--init','--cap-drop','ALL','--security-opt','no-new-privileges:true',
  '--network','bridge','--log-driver','none','--workdir','/build',
  '--mount','type=bind,src='+str(context)+',dst=/public-context,readonly',
  '--tmpfs','/tmp:rw,exec,nosuid,nodev,mode=1777,size=268435456']
 for cap in CAPS:args+=['--cap-add',cap]
 return args+['--entrypoint','/usr/local/bin/python3',BASE,'-I','-B','-u','-c',IDLE]
def validate_inspect(value,nonce,cid,context,*,offline,running=True,paused=False):
 require(type(value) is dict and value.get('id')==cid and value.get('name')=='/security-hosted-coreutils-acl-package-build-'+nonce
  and value.get('label')==nonce and value.get('image_reference')==BASE,'owned_builder')
 for key,expected in [('memory',MEMORY),('memory_swap',MEMORY),('nano_cpus',CPUS),('pids',PIDS)]:
  require(type(value.get(key)) is int and value[key]==expected,'resource')
 require(value.get('privileged') is False and value.get('init') is True and value.get('readonly_rootfs') is False
  and value.get('tmpfs')=={'/tmp':'rw,exec,nosuid,nodev,mode=1777,size=268435456'} and value.get('network_mode')=='bridge'
  and value.get('running') is running and value.get('paused') is paused,'isolation')
 require(value.get('state')==('paused' if paused else 'running') if running else value.get('state') in ('exited','created'),'builder_state')
 require(type(value.get('pid')) is int and (value['pid']>0 if running else value['pid']==0),'builder_pid')
 def caps(values):
  require(type(values) is list and all(type(v) is str for v in values),'capability_type')
  result=[v.removeprefix('CAP_') for v in values];require(len(result)==len(set(result)),'capability_type');return set(result)
 require(caps(value.get('caps_add'))==set(CAPS) and caps(value.get('caps_drop'))=={'ALL'}
  and value.get('security')==['no-new-privileges:true'],'capabilities')
 networks=value.get('networks');require(type(networks) is dict and set(networks)==(set() if offline else {'bridge'}),'network')
 mounts=value.get('mounts');require(type(mounts) is list and len(mounts) in (1,2),'mounts')
 actual={}
 for row in mounts:
  require(type(row) is dict and type(row.get('Destination')) is str,'mounts')
  if row.get('Type')=='tmpfs':
   require(row.get('Destination')=='/tmp' and row.get('RW') is True and row.get('Source','')=='','mounts');continue
  require(row.get('Type')=='bind' and row.get('RW') is False,'mounts')
  require(row['Destination'] not in actual,'mounts');actual[row['Destination']]=row.get('Source')
 require(actual=={'/public-context':str(context)},'mounts')
 return True
def validate_manifest(value):
 require(type(value) is dict and set(value)==set(OUTPUTS),'output_inventory')
 for name,row in value.items():
  require(type(row) is dict and set(row)=={'bytes','sha256','identity'} and type(row['bytes']) is int
   and 0<row['bytes']<=OUTPUTS[name] and type(row['sha256']) is str and re.fullmatch('[a-f0-9]{64}',row['sha256']),'output_binding')
  frame=row['identity'];require(type(frame) is list and len(frame)==9 and all(type(v) is int for v in frame)
   and frame[2:7]==[stat.S_IFREG|0o644,0,0,1,row['bytes']],'output_identity')
 return value
def build_container(args,admission,outer_guard,log_owners):
 context=Path(args.context);output=Path(args.output);evidence=Path(args.evidence);nonce=args.nonce
 require(context.is_absolute() and context.resolve(strict=True)==context and ',' not in str(context) and '\n' not in str(context),'context')
 context_inventory(context)
 held=[];cid=None;phase='public_admission';paused=False;finished=False;root=None;docker_root_frame=None
 new_dir(evidence);(evidence/'docker-config').mkdir(mode=0o700);new_dir(output)
 env={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','HOME':str(evidence),'DOCKER_CONFIG':str(evidence/'docker-config'),'LC_ALL':'C.UTF-8'}
 end=time.monotonic()+15600;disk_initial={}
 def guard():
  outer_guard()
  if docker_root_frame is not None:require(root.resolve(strict=True)==root and ident(root.lstat())[:5]==docker_root_frame,'docker_root_changed')
  for row in held:row.check()
  for path in (output,root):
   if path is not None:
    scope=Path('/')if path==DOCKER_ROOT else path
    free=os.statvfs(scope).f_bavail*os.statvfs(scope).f_frsize;key=str(scope)
    if key not in disk_initial:disk_initial[key]=free
    require(free>=RESERVE and disk_initial[key]-free<=DISK_GROWTH,'disk_reserve')
 def cli(parts,seconds=60,maximum=65536,log=None,enforce=True):
  return run([DOCKER,'--host',HOST,*parts],env=env,deadline=min(end,time.monotonic()+seconds) if enforce else time.monotonic()+seconds,maximum=maximum,log=log,guard=guard if enforce else lambda:None)
 def inspect(*,offline,running=True,paused_value=False,enforce=True):
  value=json.loads(cli(['container','inspect','--format',INSPECT,cid],enforce=enforce))
  validate_inspect(value,nonce,cid,context,offline=offline,running=running,paused=paused_value);return value
 def exec_public(code):return json.loads(cli(['container','exec','--workdir','/build',cid,'/usr/local/bin/python3','-I','-S','-B','-c',code]))
 def phase_log(name,action):
  try:raw=action(None)
  except BaseException as error:
   observed=getattr(error,'public_command_failure',None)
   if type(observed)is dict:
    header=encoded({'schema':'coreutils_partial_failed_command_log/1','actual_returncode':observed['actual_returncode'],
     'observed_output_bytes':observed['observed_output_bytes'],'actual_pipe_closed':observed['actual_pipe_closed'],
     'complete_log':False,'command_accepted':False,'security_accepted':False},maximum=4096)
    save_known_log(evidence/(name+'.failed.log'),header+b'\nPREFIX (bounded)\n'+observed['prefix']+b'\nTAIL (bounded; may overlap prefix)\n'+observed['tail'],log_owners)
   raise
  save_known_log(evidence/(name+'.log'),raw,log_owners);guard()
 try:
  own=Path(__file__).absolute();held.append(Held(own,65536,(own.stat().st_size,args.source_sha256)))
  for name,pin in PINS.items():held.append(Held(context/name,pin[0],pin))
  meta=Held(context/'context-manifest.json',65536);held.append(meta);value=json.loads(meta.raw)
  require(value=={'schema':'coreutils_acl_allowlisted_context/1','files':{name:{'bytes':pin[0],'sha256':pin[1]} for name,pin in PINS.items()}},'context_manifest')
  ignored=Held(context/'.dockerignore',65536);held.append(ignored)
  allow='**\n!.dockerignore\n!context-manifest.json\n'+''.join('!'+name+'\n' for name in PINS if not name.startswith('inputs/'))+'!inputs/\n'+''.join('!'+name+'\n' for name in PINS if name.startswith('inputs/'))
  require(ignored.raw==allow.encode(),'dockerignore')
  root_raw=cli(['info','--format','{{.DockerRootDir}}']).decode('utf-8').strip();root=Path(root_raw)
  require(root==DOCKER_ROOT and root.resolve(strict=True)==root and root.lstat().st_dev==Path('/').lstat().st_dev,'docker_root')
  docker_root_frame=ident(root.lstat())[:5];require(stat.S_ISDIR(docker_root_frame[2])and docker_root_frame[3:5]==(0,0)and not docker_root_frame[2]&0o022,'docker_root_metadata');guard()
  phase='pull_public_base';phase_log(phase,lambda fd:cli(['pull',BASE],seconds=600,maximum=16*1024**2,log=fd))
  phase='public_base_identity';digests=json.loads(cli(['image','inspect','--format','{{json .RepoDigests}}',BASE]))
  require(type(digests) is list and all(type(v) is str for v in digests)
   and any(v in {'python@'+BASE.split('@')[1],'docker.io/library/python@'+BASE.split('@')[1]} for v in digests),'base_digest')
  phase='create';created=cli(create_argv(nonce,context)[3:]).decode('ascii').strip();require(re.fullmatch('[a-f0-9]{64}',created),'container_id');cid=created
  cli(['container','start',cid]);inspect(offline=False)
  phase='stage_public_copies';copies=exec_public(stage_code(context,False));validate_copies(copies);save(evidence/'root-public-copies.json',encoded(copies))
  phase='configure_repository';phase_log(phase,lambda fd:cli(['container','exec','--workdir','/build',cid,'/usr/local/bin/python3','-I','-S','-B','/recipe/sourcebuild.py','--configure-build-repository'],seconds=2400,maximum=64*1024**2,log=fd))
  require(exec_public(stage_code(context,True))==copies,'public_copy_changed')
  inspect(offline=False);phase='network_cutoff';cli(['network','disconnect','bridge',cid]);inspect(offline=True)
  require(exec_public(OFFLINE)==['lo'],'loopback_only')
  phase='offline_build';phase_log(phase,lambda fd:cli(['container','exec','--workdir','/build',cid,'/usr/local/bin/python3','-I','-S','-B','/recipe/sourcebuild.py','--build'],seconds=13200,maximum=128*1024**2,log=fd))
  inspect(offline=True);require(exec_public(OFFLINE)==['lo'],'loopback_only');require(exec_public(stage_code(context,True))==copies,'public_copy_changed');before=validate_manifest(exec_public(MANIFEST))
  phase='export';cli(['container','pause',cid]);paused=True;inspect(offline=True,paused_value=True)
  for name,row in before.items():
   target=output/name;require(not target.exists(),'export_exclusive')
   cli(['container','cp',cid+':/out/'+name,str(target)],maximum=65536)
   item=Held(target,OUTPUTS[name],(row['bytes'],row['sha256']));held.append(item)
  require({p.name for p in output.iterdir()}==set(OUTPUTS),'export_inventory')
  cli(['container','unpause',cid]);paused=False;inspect(offline=True)
  require(validate_manifest(exec_public(MANIFEST))==before,'export_source_changed')
  cli(['container','stop','--time','10',cid],seconds=30);inspect(offline=True,running=False)
  phase='physical_finalization'
  for item in reversed(held):item.close()
  for item in held:item.named()
  held=[];finished=True
  result={'schema':'coreutils_acl_hosted_package_builder/1','status':'PACKAGE_BUILT','controller_source_sha256':args.source_sha256,'container_id':cid,'base':BASE,'outputs':before,
   'offline_build_network_mode':'bridge_with_zero_endpoints_and_loopback_only','resources':{'memory':MEMORY,'nano_cpus':CPUS,'pids':PIDS},
   'container_stopped_and_retained':True,'hosted_admission':admission,'hosted_disk_initial':disk_initial,'ordinary_image_ci_still_required':True,'production_data_accessed':False,'fault_tests_performed':False,'security_accepted':False}
  save(evidence/'container-result.json',encoded(result));return result
 except BaseException:
  # Never recreate, resume, reuse or remove a prior builder. Retain logs and the
  # owned stopped container. Cleanup uses only this fresh exact returned ID.
  cleanup=False
  if cid is not None:
   try:
    # Match fresh name/label/base and fixed resources before any stop action.
    value=json.loads(cli(['container','inspect','--format',INSPECT,cid],enforce=False))
    require(value.get('id')==cid and value.get('label')==nonce and value.get('name')=='/security-hosted-coreutils-acl-package-build-'+nonce and value.get('image_reference')==BASE,'cleanup_owner')
    if value.get('paused') is True:cli(['container','unpause',cid],enforce=False);paused=False
    cli(['container','stop','--time','10',cid],seconds=30,enforce=False)
    value=json.loads(cli(['container','inspect','--format',INSPECT,cid],enforce=False))
    require(value.get('id')==cid and value.get('running') is False and value.get('paused') is False and value.get('pid')==0,'cleanup_not_stopped');cleanup=True
   except BaseException:pass
  failure={'schema':'coreutils_acl_hosted_package_builder/1','status':'FAILED','phase':phase,'container_id':cid,'owned_stop_requested_successfully':cleanup,'security_accepted':False}
  try:save(evidence/'failure.json',encoded(failure))
  except BaseException:pass
  raise
 finally:
  if not finished:
   for item in reversed(held):
    if item.fd is not None:
     try:item.close()
     except BaseException:pass


def validate_host(value):
 require(type(value)is dict and set(value)=={'uid','euid','runner_environment','machine','os_id','os_version','cpus','affinity_cpus','mem_total_bytes','free_bytes','disk_bytes'},'host_metadata')
 require(type(value['uid'])is int and value['uid']==HOST_UID and type(value['euid'])is int and value['euid']==HOST_UID
  and value['runner_environment']=='github-hosted'and value['machine']=='x86_64'and value['os_id']=='ubuntu'and value['os_version']=='24.04','hosted_platform')
 require(type(value['cpus'])is int and 4<=value['cpus']<=256 and type(value['affinity_cpus'])is int
  and 4<=value['affinity_cpus']<=value['cpus']and type(value['mem_total_bytes'])is int and value['mem_total_bytes']>=12*1024**3,'hosted_resources')
 require(type(value['free_bytes'])is int and value['free_bytes']>=RESERVE+DISK_GROWTH,'hosted_disk_capacity')
 require(type(value['disk_bytes'])is int and value['disk_bytes']>=value['free_bytes'],'hosted_resources');return value


def host_facts(temp):
 global OBSERVED_CAPACITY
 def small(path):
  fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
  try:raw=os.read(fd,65537);require(len(raw)<=65536,'host_metadata_bound');return raw.decode('ascii')
  finally:os.close(fd)
 release=dict(line.split('=',1)for line in small('/usr/lib/os-release').splitlines()if '='in line)
 mem=dict(line.split(':',1)for line in small('/proc/meminfo').splitlines()if ':'in line);words=mem.get('MemTotal','').split()
 require(len(words)==2 and words[0].isdigit()and words[1]=='kB','host_memory');fs=os.statvfs(temp);rootfs=os.statvfs('/')
 OBSERVED_CAPACITY={'workspace_free_bytes':fs.f_bavail*fs.f_frsize,'docker_root_filesystem_free_bytes':rootfs.f_bavail*rootfs.f_frsize,
  'required_initial_free_bytes':RESERVE+DISK_GROWTH,'disk_reserve_bytes':RESERVE,'maximum_growth_bytes':DISK_GROWTH}
 return validate_host({'uid':os.getuid(),'euid':os.geteuid(),'runner_environment':os.environ.get('RUNNER_ENVIRONMENT'),
  'machine':os.uname().machine,'os_id':release.get('ID','').strip('"'),'os_version':release.get('VERSION_ID','').strip('"'),
  'cpus':os.cpu_count(),'affinity_cpus':len(os.sched_getaffinity(0)),'mem_total_bytes':int(words[0])*1024,
  'free_bytes':fs.f_bavail*fs.f_frsize,'disk_bytes':fs.f_blocks*fs.f_frsize})


def hosted_layout(args,environ):
 require(os.getuid()==os.geteuid()==HOST_UID and environ.get('GITHUB_ACTIONS')=='true'
  and environ.get('RUNNER_ENVIRONMENT')=='github-hosted'and environ.get('GITHUB_REPOSITORY')==REPOSITORY
  and environ.get('GITHUB_REF')in {'refs/heads/main','refs/heads/security/remediation-20261023'},'hosted_job')
 require(type(args.commit)is str and re.fullmatch('[a-f0-9]{40}',args.commit)and environ.get('GITHUB_SHA')==args.commit
  and type(args.source_sha256)is str and re.fullmatch('[a-f0-9]{64}',args.source_sha256)and args.confirm==CONFIRM,'hosted_source')
 run_id=environ.get('GITHUB_RUN_ID','');attempt=environ.get('GITHUB_RUN_ATTEMPT','')
 require(re.fullmatch('[1-9][0-9]{0,19}',run_id)and re.fullmatch('[1-9][0-9]{0,7}',attempt),'hosted_run')
 repo=Path(args.repo);root=Path(args.root);temp=Path(environ.get('RUNNER_TEMP',''))
 require(repo.is_absolute()and repo.resolve(strict=True)==repo and str(repo)==environ.get('GITHUB_WORKSPACE')
  and SOURCE_ROOT==repo/TRACKED and Path(__file__).absolute()==SOURCE_ROOT/'build_hosted.py','hosted_source_root')
 require(temp.is_absolute()and temp.resolve(strict=True)==temp and root==temp/('coreutils-acl-package-'+run_id+'-'+attempt)
  and not os.path.lexists(root),'fresh_hosted_root');parent_frame(root);parent_frame(repo/'unused')
 return repo,root,run_id,attempt


def output_bytes(path,pin,held):
 item=Held(path,pin[0],pin);held.append(item);raw=os.pread(item.fd,pin[0]+1,0)
 require((len(raw),sha256(raw).hexdigest())==pin,'source_bytes');item.check();return raw


def extract_runtime_inputs(raw,context,held):
 require(type(raw)is bytes and (len(raw),sha256(raw).hexdigest())==OLD_ZIP_PIN,'old_artifact_pin')
 with zipfile.ZipFile(io.BytesIO(raw))as archive:
  rows=archive.infolist();require(len(rows)==16 and len({r.filename for r in rows})==16,'old_artifact_members');selected={}
  for row in rows:
   path=Path(row.filename)
   require(not row.is_dir()and not row.flag_bits&1 and 0<row.file_size<=32*1024**2 and len(row.filename)<=4096
    and not path.is_absolute()and '..'not in path.parts and str(path)==row.filename and stat.S_IFMT(row.external_attr>>16)in(0,stat.S_IFREG),'old_artifact_member')
   leaf=path.name;name='inputs/'+leaf
   if name not in PINS:continue
   require(name not in selected and row.file_size==PINS[name][0],'old_runtime_member');selected[name]=row
  require(set(selected)=={name for name in PINS if name.startswith('inputs/')},'old_runtime_inventory')
  for name,row in selected.items():
   expected=PINS[name];data=bytearray()
   with archive.open(row)as stream:
    while True:
     part=stream.read(min(65536,expected[0]-len(data)+1))
     if not part:break
     require(len(data)+len(part)<=expected[0],'old_runtime_bound');data.extend(part)
   require((len(data),sha256(data).hexdigest())==expected,'old_runtime_pin');save(context/name,bytes(data));held.append(Held(context/name,expected[0],expected))


def vendor_case_evidence(output,result,held):
 row=result['outputs']['vendor-suite.json'];raw=output_bytes(output/'vendor-suite.json',(row['bytes'],row['sha256']),held);receipt=json.loads(raw)
 require(receipt.get('schema')=='coreutils_unfiltered_vendor_command/1'and receipt.get('actual_command')==['/usr/bin/dh_auto_test']and receipt.get('actual_returncode')==0
  and receipt.get('actual_uid')==receipt.get('actual_gid')==10001 and receipt.get('actual_pipe_closed')is True
  and receipt.get('configured_default_selections_captured_before_test')is True and receipt.get('test_filter_override_used')is False,'unfiltered_vendor_command_receipt')
 observation=receipt.get('observation');require(type(observation)is dict and observation.get('schema')=='coreutils_9_7_vendor_suite_output_observation/1'
  and observation.get('all_selected_trs_log_pairs_observed')is True and observation.get('failed_unexpected_or_error_results')==0
  and observation.get('security_accepted')is False,'actual_vendor_results')
 return {'schema':'coreutils_hosted_vendor_suite_evidence/1','vendor_suite_bytes':row['bytes'],'vendor_suite_sha256':row['sha256'],
  'actual_unfiltered_dh_auto_test_exit_code':0,'actual_unprivileged_test_uid':10001,'actual_configured_default_selections_observed':True,
  'actual_skip_count':observation['observed_skip_count'],'actual_xfail_count':observation['observed_xfail_count'],
  'zero_skip_coverage_claimed':False,'security_accepted':False}


def retain_exported_outputs(output,result,held):
 # Keep actual host bytes alive through ALL later source/log physical closes.
 rows=validate_manifest(result['outputs'])
 for name,row in rows.items():held.append(Held(output/name,OUTPUTS[name],(row['bytes'],row['sha256'])))
def finalize_public_inputs(held):
 for item in reversed(held):item.close()
 for item in held:item.named()

def execute_hosted(args):
 global PHASE
 PHASE='hosted_admission'
 repo,root,run_id,attempt=hosted_layout(args,os.environ);facts=host_facts(root.parent)
 # Refuse before pulling/building if either actual workspace or Docker root FS
 # lacks the 48GiB growth allowance plus the independent8GiB floor.
 rootfs=os.statvfs('/');require(rootfs.f_bavail*rootfs.f_frsize>=RESERVE+DISK_GROWTH,'hosted_docker_disk_capacity')
 root.mkdir(mode=0o700);context=root/'context';context.mkdir(mode=0o700);(context/'inputs').mkdir(mode=0o700)
 evidence=root/'reports';output=root/'package-output';held=[];closed=False;result=None;PHASE='source_admission'
 env={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','HOME':str(root),
  'LC_ALL':'C.UTF-8','GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null','GIT_OPTIONAL_LOCKS':'0','GIT_TERMINAL_PROMPT':'0'}
 start=time.monotonic();end=start+16200
 initial={str(root.parent):facts['free_bytes'],'/':rootfs.f_bavail*rootfs.f_frsize}
 def guard():
  require(time.monotonic()<end,'hosted_deadline')
  for item in held:item.check()
  for path,before in initial.items():
   fs=os.statvfs(path);free=fs.f_bavail*fs.f_frsize;require(free>=RESERVE and before-free<=DISK_GROWTH,'hosted_disk_reserve')
 def command(argv,maximum=65536,seconds=60,child_env=None):
  return run(argv,env=env if child_env is None else child_env,deadline=min(end,time.monotonic()+seconds),maximum=maximum,guard=guard)
 try:
  own=Held(Path(__file__).absolute(),65536,(Path(__file__).stat().st_size,args.source_sha256));held.append(own)
  bindings={}
  for name in (*RECIPE_NAMES,'build_hosted.py'):
   expected=(own.binding['bytes'],args.source_sha256)if name=='build_hosted.py'else PINS[name]
   local=output_bytes(SOURCE_ROOT/name,expected,held);ref=args.commit+':'+(TRACKED/name).as_posix()
   require(command(['/usr/bin/git','-C',str(repo),'cat-file','-t',ref],maximum=16)==b'blob\n'
    and command(['/usr/bin/git','-C',str(repo),'cat-file','-s',ref],maximum=32)==str(expected[0]).encode()+b'\n','tracked_blob_metadata')
   require(command(['/usr/bin/git','-C',str(repo),'cat-file','blob',ref],maximum=expected[0]+1)==local,'tracked_blob_binding')
   if name!='build_hosted.py':save(context/name,local);held.append(Held(context/name,expected[0],expected))
   bindings[(TRACKED/name).as_posix()]={'bytes':expected[0],'sha256':expected[1]}
  PHASE='prior_public_runtime_artifact'
  token=os.environ.get('GH_TOKEN');require(type(token)is str and 0<len(token)<=8192,'artifact_authentication_required')
  fetch_env={**env,'GH_TOKEN':token,'GH_HOST':'github.com','GH_PROMPT_DISABLED':'1','GH_NO_UPDATE_NOTIFIER':'1'}
  metadata=json.loads(command(['/usr/bin/gh','api','repos/'+REPOSITORY+'/actions/artifacts/'+str(OLD_ARTIFACT_ID)],maximum=65536,seconds=60,child_env=fetch_env))
  require(type(metadata)is dict and metadata.get('id')==OLD_ARTIFACT_ID and metadata.get('expired')is False
   and metadata.get('size_in_bytes')==OLD_ZIP_PIN[0]and type(metadata.get('workflow_run'))is dict
   and metadata['workflow_run'].get('id')==OLD_RUN_ID,'prior_artifact_metadata')
  raw=command(['/usr/bin/gh','api','repos/'+REPOSITORY+'/actions/artifacts/'+str(OLD_ARTIFACT_ID)+'/zip'],maximum=OLD_ZIP_PIN[0]+1,seconds=300,child_env=fetch_env)
  del token,fetch_env
  require((len(raw),sha256(raw).hexdigest())==OLD_ZIP_PIN,'prior_artifact_digest');save(root/'prior-public-artifact.zip',raw);held.append(Held(root/'prior-public-artifact.zip',OLD_ZIP_PIN[0],OLD_ZIP_PIN))
  extract_runtime_inputs(raw,context,held)
  manifest=encoded({'schema':'coreutils_acl_allowlisted_context/1','files':{name:{'bytes':pin[0],'sha256':pin[1]}for name,pin in PINS.items()}})
  allow='**\n!.dockerignore\n!context-manifest.json\n'+''.join('!'+n+'\n'for n in PINS if not n.startswith('inputs/'))+'!inputs/\n'+''.join('!'+n+'\n'for n in PINS if n.startswith('inputs/'))
  for name,data in (('context-manifest.json',manifest),('.dockerignore',allow.encode())):
   save(context/name,data);held.append(Held(context/name,len(data),(len(data),sha256(data).hexdigest())))
  context_inventory(context);guard()
  source={'schema':'coreutils_acl_hosted_source_identity/1','commit':args.commit,'repository':REPOSITORY,'run_id':run_id,'run_attempt':attempt,
   'tracked_files':bindings,'prior_runtime_input':{'artifact_id':OLD_ARTIFACT_ID,'run_id':OLD_RUN_ID,'zip_bytes':OLD_ZIP_PIN[0],'zip_sha256':OLD_ZIP_PIN[1]},
   'actual_host_uid':os.getuid(),'actual_host_gid':os.getgid(),'host_facts':facts,'docker_root_contract':str(DOCKER_ROOT),
   'disk_growth_allowance_bytes':DISK_GROWTH,'disk_reserve_bytes':RESERVE,'sdk_paths_removed':[],'security_accepted':False}
  save(root/'source-identity.json',encoded(source));PHASE='container_build'
  params=argparse.Namespace(context=str(context),output=str(output),evidence=str(evidence),nonce=uuid.uuid4().hex,source_sha256=args.source_sha256)
  result=build_container(params,source,guard,held)
  retain_exported_outputs(output,result,held)
  PHASE='mandatory_vendor_coverage';coverage=vendor_case_evidence(output,result,held)
  save(evidence/'vendor-case-evidence.json',encoded(coverage))
  logs={item.path.name:{**item.binding,'creation_full9':list(item.before)}for item in held if item.path.parent==evidence and item.path.name.endswith('.log')}
  require(set(logs)=={'pull_public_base.log','configure_repository.log','offline_build.log'},'known_log_inventory')
  PHASE='outer_physical_finalization'
  finalize_public_inputs(held)
  held=[];closed=True
  final={**result,'source_identity_sha256':sha256(encoded(source)).hexdigest(),'mandatory_vendor_case':coverage,
   'original_command_log_creation_bindings':logs,
   'outer_source_physical_finalizers_complete':True,'native_production_build_profile_used':False,'ordinary_image_ci_still_required':True}
  save(root/'result.json',encoded(final));return final
 except BaseException:
  try:save(root/'hosted-failure.json',encoded({'schema':'coreutils_acl_hosted_package_builder/1','status':'FAILED','phase':PHASE,'security_accepted':False,'automatic_retry':False}))
  except BaseException:pass
  raise
 finally:
  if not closed:
   for item in reversed(held):
    if item.fd is not None:
     try:item.close()
     except BaseException:pass


def main(argv=None):
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--repo');parser.add_argument('--root');parser.add_argument('--commit');parser.add_argument('--source-sha256');parser.add_argument('--confirm');parser.add_argument('--execute-hosted-build',action='store_true');args=parser.parse_args(argv)
 if not args.execute_hosted_build:
  print(json.dumps({'schema':'coreutils_acl_hosted_package_builder/1','status':'UNARMED','build_performed':False,'security_accepted':False}));return 0
 result=execute_hosted(args)
 print(json.dumps({'schema':result['schema'],'status':'HOSTED_PACKAGE_BUILT_NOT_ACCEPTED','container_id':result['container_id'],
  'output_directory':str(Path(args.root)/'package-output'),'source_physical_finalizers_complete':True,'security_accepted':False},sort_keys=True));return 0


def failure(error):
 code=error.args[0]if type(error)is RuntimeError and len(error.args)==1 and type(error.args[0])is str and error.args[0]in PUBLIC_CODES else'hosted_build_unproven'
 name=type(error).__name__ if type(error)in (RuntimeError,ValueError,TypeError,KeyError,FileNotFoundError,PermissionError,OSError,TimeoutError)else'unclassified_exception'
 return{'schema':'coreutils_acl_hosted_package_builder/1','status':'REFUSED_OR_FAILED','phase':PHASE,'reason_code':code,'exception_class':name,
  'observed_capacity':OBSERVED_CAPACITY,'automatic_retry':False,'security_accepted':False}


PUBLIC_CODES=frozenset(('artifact_authentication_required', 'base_digest', 'builder_pid', 'builder_state', 'capabilities', 'capability_type', 'cleanup_not_stopped', 'cleanup_owner', 'command_failed', 'command_output_bound', 'container_id', 'context', 'context_inventory', 'context_manifest', 'deadline', 'disk_reserve', 'docker_root', 'docker_root_changed', 'docker_root_metadata', 'dockerignore', 'export_exclusive', 'export_inventory', 'export_source_changed', 'fresh_hosted_root', 'host_memory', 'host_metadata', 'host_metadata_bound', 'hosted_deadline', 'hosted_disk_capacity', 'hosted_disk_reserve', 'hosted_docker_disk_capacity', 'hosted_job', 'hosted_platform', 'hosted_resources', 'hosted_run', 'hosted_source', 'hosted_source_root', 'isolation', 'log_write', 'loopback_only', 'mandatory_large_sparse_case_unproven', 'mounts', 'network', 'new_directory', 'old_artifact_member', 'old_artifact_members', 'old_artifact_pin', 'old_runtime_bound', 'old_runtime_inventory', 'old_runtime_member', 'old_runtime_pin', 'output_binding', 'output_identity', 'output_inventory', 'owned_builder', 'parent', 'parent_changed', 'prior_artifact_digest', 'prior_artifact_metadata', 'public_bound', 'public_changed', 'public_closed_changed', 'public_copy_binding', 'public_copy_changed', 'public_copy_identity', 'public_copy_inventory', 'public_copy_pin', 'public_file', 'public_pin', 'public_size', 'receipt_bound', 'receipt_write', 'resource', 'source_bytes', 'tracked_blob_binding', 'tracked_blob_metadata', 'vendor_log_deadline', 'vendor_log_line_bound'))


if __name__=='__main__':
 try:raise SystemExit(main())
 except Exception as error:
  print(json.dumps(failure(error),sort_keys=True));raise SystemExit(1)from None
