#!/usr/bin/env python3
"""Root-only bounded disposable native Ubuntu SQLite package builder; default is unarmed.

No code executes on import.  Root reviews this source and supplies the fresh
allowlisted public context.  This does not build or activate the ordinary app.
"""
from contextlib import ExitStack
from hashlib import sha256
from pathlib import Path
import argparse,json,os,re,selectors,signal,stat,subprocess,sys,time

PLAN=Path('/root/trading-bot/trading_bot/.local/security-sqlite-vendor-compat-source-20261101-03/proposal/deploy/security/sqlite-ubuntu-gold2/runner')
# Keep the already admitted public source at its exact immutable01 path.
PUBLIC_CA=Path('/root/trading-bot/trading_bot/.local/security-sqlite-ubuntu-package-build-runner-20261026-01/public-ca/ca-certificates.crt')
CA_CONTAINER='/build/public-bootstrap-ca.crt'
CA_PIN=(222073,'e9c7b63ee2623386e9b0a3ee32f499d01a8ea23dc83b8383ddff9f8e749543b7')
VARIANTS={'noble': {'base': 'ubuntu:24.04@sha256:a2d43bffc46670d7108bb3c0468c67c5557c4c92e83e1e34cbff1322ad1b94dc', 'version': '3.45.1-1ubuntu2.8+gold2', 'python': '/usr/bin/python3.12', 'pins': {'sourcebuild.py': (94318, '78d31ee5bef3e31be45cb1be7386fe76879c4a895e3ca638918211d3b6ac2ba9'), 'Dockerfile': (4177, '3043465b46c5b81ebf260bcbc4e854d55b752a86c0a7e38429b7d0bd2e6147f0'), '0001-wal-reset-production-only.patch': (6746, 'ab1d8c75b8ed521cc5d1d0c64da66be26ca3b72d53326c28b6a86473f42ddc85'), '0002-fts5-detail-none-merge-bound.patch': (1252, 'bc1403e417a5673ad51712c36f3b9b33f87b77a6f6f06d7bedba02b46d098231'), '0003-fts5-detail-none-upstream-regression.patch': (931, '81edf417c615f7a76fe9506c74e4a05a5ee1a135bce805de7ae8ac02c3dbfaed'), 'verify_positive.py': (17714, '7a3e34209f7a638caf481d51031dfbdc54c7a3001ccbf0eb205591b5dc6265ed'), 'source-signer.asc': (14582, '91b93e45fb77b7098882dc1e2035951b7c351b9d3f754990642be80191030a73'), 'consumer-profile.json': (662, 'bc10b4fe4ccc1a668ce1c55a00daf8ed0f129b9298ebaf9f0487a2a14c0b4af3'), 'inputs/sqlite3_3.45.1-1ubuntu2.8.debian.tar.xz': (37512, 'd543bf91c6f14ccd52ebe44d6ef3b360196b2596e0a7ca86089aef5fc52f5ec0'), 'inputs/sqlite3_3.45.1-1ubuntu2.8.dsc': (2601, 'f3c0a904d2d2386e81b39774f2a2f00f92c42adb2f141ff59cc6a347c4487a69'), 'inputs/sqlite3_3.45.1.orig-www.tar.xz': (5693812, '79b60798195a024d447e661e5bbc1eb40af50387ebf840e6f581190cc02064b6'), 'inputs/sqlite3_3.45.1.orig.tar.xz': (8257884, 'e32e817f7b4166a301f60b14a711871bfab7d35c1d7e29b585dfc479ae150aa4'), '0004-rowid-in-view-nested-join-core.patch': (8073, 'fc7990f7375b8e8d63abc04a5c2fd66a2a6ed880e53342d37a370526e11ff018')}}, 'resolute': {'base': 'ubuntu:26.04@sha256:42527649dbc4f9197a6073f426f488cbff0e9cc9f2afce32d91c383e4875634a', 'version': '3.46.1-9ubuntu0.3+gold2', 'python': '/usr/bin/python3.14', 'pins': {'sourcebuild.py': (89701, 'cef93d3970aa30b0143258cd0a0580be12f757f2e10ff9ebfc8c2961c3739da4'), 'Dockerfile': (4367, '85920342ff6922b3236c06791650633127286deb3bce279a32fb6bf26245bb66'), '0001-wal-reset-production-only.patch': (6746, 'ab1d8c75b8ed521cc5d1d0c64da66be26ca3b72d53326c28b6a86473f42ddc85'), '0002-fts5-detail-none-merge-bound.patch': (1252, 'bc1403e417a5673ad51712c36f3b9b33f87b77a6f6f06d7bedba02b46d098231'), '0003-fts5-detail-none-upstream-regression.patch': (931, '81edf417c615f7a76fe9506c74e4a05a5ee1a135bce805de7ae8ac02c3dbfaed'), 'verify_positive.py': (16835, '809d13becc325d5711ceeb5557c809a1a545dfec77ff0bba8a0bdd1651b035c4'), 'source-signer.asc': (14582, '91b93e45fb77b7098882dc1e2035951b7c351b9d3f754990642be80191030a73'), 'consumer-profile.json': (665, '737e920c3421f62f90dbd814d13488778580760b401e060d1a3ec479867cd8d6'), 'inputs/sqlite3_3.46.1-9ubuntu0.3.debian.tar.xz': (38312, '5d102099a3ed96c4e7edf6a53ef3a9121cd6b36c4809536a2033d348c54ea29c'), 'inputs/sqlite3_3.46.1-9ubuntu0.3.dsc': (2756, 'bb0151d1479c9a498043a0c86b943805b0b6eeae0e997ed227280e717b848d5e'), 'inputs/sqlite3_3.46.1.orig-www.tar.xz': (5861820, '648df41a8e532882b1905df45919aae4bafaf74c455f66bc86f1f52f45c8b8f0'), 'inputs/sqlite3_3.46.1.orig.tar.xz': (8456776, 'd0cdd2ece271b29e7ce18095745d892517ee26d0f270065b3a25c2e9eb11639c')}}}
VARIANT=None;BASE=None;VERSION=None;PYTHON=None;PINS={};OUTPUTS={};MANIFEST=None
MEMORY=3*1024**3;CPUS=2*10**9;PIDS=256;RESERVE=2*1024**3
CONFIRM='BUILD-ONLY-NATIVE-UBUNTU-SQLITE-GOLD2-PACKAGE-20261101-01'
DOCKER='/usr/bin/docker';HOST='unix:///var/run/docker.sock'
DOCKER_ROOT=Path('/srv/trading-bot/production-data/docker-data')
CAPS=('CHOWN','DAC_OVERRIDE','FOWNER','FSETID','KILL','SETGID','SETUID','SYS_CHROOT')
LABEL='trading-bot.security.ubuntu-sqlite-package-build'
INSPECT='{"id":{{json .Id}},"name":{{json .Name}},"image_reference":{{json .Config.Image}},"label":{{json (index .Config.Labels "'+LABEL+'")}},"state":{{json .State.Status}},"running":{{json .State.Running}},"paused":{{json .State.Paused}},"pid":{{json .State.Pid}},"memory":{{json .HostConfig.Memory}},"memory_swap":{{json .HostConfig.MemorySwap}},"nano_cpus":{{json .HostConfig.NanoCpus}},"pids":{{json .HostConfig.PidsLimit}},"privileged":{{json .HostConfig.Privileged}},"init":{{json .HostConfig.Init}},"readonly_rootfs":{{json .HostConfig.ReadonlyRootfs}},"tmpfs":{{json .HostConfig.Tmpfs}},"network_mode":{{json .HostConfig.NetworkMode}},"networks":{{json .NetworkSettings.Networks}},"caps_add":{{json .HostConfig.CapAdd}},"caps_drop":{{json .HostConfig.CapDrop}},"security":{{json .HostConfig.SecurityOpt}},"mounts":{{json .Mounts}}}'
OFFLINE="import json,os; print(json.dumps(sorted(os.listdir('/sys/class/net'))))"
# This reads only the nine owned public package/provenance/vendor outputs, never
# business inputs. Full9 includes no atime, so normal archive reads are neutral.
MANIFEST_TEMPLATE=r'''
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
'''

def require(value,code):
 if not value:raise RuntimeError(code)
def select_variant(name):
 global VARIANT,BASE,VERSION,PYTHON,PINS,OUTPUTS,MANIFEST
 require(type(name) is str and name in VARIANTS,'native_variant')
 spec=VARIANTS[name];VARIANT=name;BASE=spec['base'];VERSION=spec['version'];PYTHON=spec['python'];PINS=dict(spec['pins'])
 OUTPUTS={'libsqlite3-0_'+VERSION+'_amd64.deb':32*1024**2,
  'sqlite3_'+VERSION+'_amd64.changes':4*1024**2,'sqlite3_'+VERSION+'_amd64.buildinfo':4*1024**2,
  'provenance.json':4*1024**2,'positive-baseline.json':65536,'positive-candidate.json':65536,'abi-contract.json':4*1024**2,'vendor-standard.log':32*1024**2,'vendor-fts5-regression.log':4*1024**2}
 MANIFEST=MANIFEST_TEMPLATE.replace('LIMITS',repr(OUTPUTS))
def bootstrap_code(raw):
 # The caller holds the exact per-variant Dockerfile SHA before this function.
 require(type(raw) is bytes and 0<len(raw)<=65536,'bootstrap_source')
 rows=raw.decode('utf-8').replace('\\\n',' ').splitlines()
 runs=[row[4:] for row in rows if row.startswith('RUN ')]
 require(len(runs)==2 and runs[0].startswith('test -f /etc/ssl/certs/ca-certificates.crt')
  and runs[1]=='--network=none '+PYTHON+' -I -B /recipe/sourcebuild.py --build','bootstrap_source')
 return ('test -f '+CA_CONTAINER+' && test ! -L '+CA_CONTAINER
  +' && test "$(/usr/bin/wc -c < '+CA_CONTAINER+')" = '+str(CA_PIN[0])
  +" && echo '"+CA_PIN[1]+'  '+CA_CONTAINER+"' | /usr/bin/sha256sum -c - >/dev/null"
  +' && /usr/bin/install -D -o 0 -g 0 -m 0444 '+CA_CONTAINER+' /etc/ssl/certs/ca-certificates.crt && '+runs[0])

def ident(s):return (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def encoded(value):return (json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
def parent_frame(path):
 rows=[]
 for p in path.parents:
  s=p.lstat();require(p.resolve(strict=True)==p and stat.S_ISDIR(s.st_mode) and s.st_uid==s.st_gid==0 and not s.st_mode&0o022,'parent')
  rows.append((p,ident(s)[:5]))
 return rows
def parents_ok(rows):require(all(p.resolve(strict=True)==p and ident(p.lstat())[:5]==old for p,old in rows),'parent_changed')
class Held:
 def __init__(self,path,maximum,pin=None):
  self.path=path;self.parents=parent_frame(path);self.before=ident(path.lstat())
  require(path.resolve(strict=True)==path and stat.S_ISREG(self.before[2]) and self.before[3:6]==(0,0,1) and not self.before[2]&0o022 and 0<self.before[6]<=maximum,'public_file')
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
 require(type(raw) is bytes and len(raw)<=1024**2,'receipt_bound')
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
 try:
  view=memoryview(raw)
  while view:n=os.write(fd,view);require(n>0,'receipt_write');view=view[n:]
  os.fsync(fd)
 finally:os.close(fd)
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
 total=0;parts=[]
 try:
  with selectors.DefaultSelector() as selector:
   fd=child.stdout.fileno();os.set_blocking(fd,False);selector.register(fd,selectors.EVENT_READ)
   while True:
    guard();left=deadline-time.monotonic();require(left>0,'deadline')
    if not selector.select(min(1,left)):continue
    block=os.read(fd,min(65536,maximum-total+1))
    if not block:break
    total+=len(block);require(total<=maximum,'command_output_bound')
    if log is None:parts.append(block)
    else:
     view=memoryview(block)
     while view:n=os.write(log,view);require(n>0,'log_write');view=view[n:]
  require(child.wait(timeout=max(.1,deadline-time.monotonic()))==0,'command_failed');guard()
  return b''.join(parts)
 except BaseException:
  try:cleanup_child(child)
  except BaseException:pass
  raise
 finally:child.stdout.close()
def create_argv(nonce,context):
 args=[DOCKER,'--host',HOST,'container','create','--name','security-ubuntu-sqlite-package-build-'+nonce,
  '--label',LABEL+'='+nonce,'--memory',str(MEMORY),'--memory-swap',str(MEMORY),'--cpus','2','--pids-limit',str(PIDS),
  '--shm-size','134217728','--ulimit','core=0:0','--init','--cap-drop','ALL','--security-opt','no-new-privileges:true',
  '--network','bridge','--log-driver','none','--workdir','/build',
  '--mount','type=bind,src='+str(context)+',dst=/recipe,readonly',
  '--mount','type=bind,src='+str(context/'inputs')+',dst=/inputs,readonly',
  '--tmpfs','/tmp:rw,nosuid,nodev,mode=1777,size=268435456']
 for cap in CAPS:args+=['--cap-add',cap]
 return args+['--user','0:0','--env','PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
  '--env','LC_ALL=C.UTF-8','--env','DEBIAN_FRONTEND=noninteractive','--env','PYTHONDONTWRITEBYTECODE=1',
  '--entrypoint','/bin/sleep',BASE,'9000']
def validate_inspect(value,nonce,cid,context,*,offline,running=True,paused=False):
 require(type(value) is dict and value.get('id')==cid and value.get('name')=='/security-ubuntu-sqlite-package-build-'+nonce
  and value.get('label')==nonce and value.get('image_reference')==BASE,'owned_builder')
 for key,expected in [('memory',MEMORY),('memory_swap',MEMORY),('nano_cpus',CPUS),('pids',PIDS)]:
  require(type(value.get(key)) is int and value[key]==expected,'resource')
 require(value.get('privileged') is False and value.get('init') is True and value.get('readonly_rootfs') is False
  and value.get('tmpfs')=={'/tmp':'rw,nosuid,nodev,mode=1777,size=268435456'} and value.get('network_mode')=='bridge'
  and value.get('running') is running and value.get('paused') is paused,'isolation')
 require(value.get('state')==('paused' if paused else 'running') if running else value.get('state') in ('exited','created'),'builder_state')
 require(type(value.get('pid')) is int and (value['pid']>0 if running else value['pid']==0),'builder_pid')
 def caps(values):
  require(type(values) is list and all(type(v) is str for v in values),'capability_type')
  result=[v.removeprefix('CAP_') for v in values];require(len(result)==len(set(result)),'capability_type');return set(result)
 require(caps(value.get('caps_add'))==set(CAPS) and caps(value.get('caps_drop'))=={'ALL'}
  and value.get('security')==['no-new-privileges:true'],'capabilities')
 networks=value.get('networks');require(type(networks) is dict and set(networks)==(set() if offline else {'bridge'}),'network')
 mounts=value.get('mounts');require(type(mounts) is list and len(mounts) in (2,3),'mounts')
 actual={}
 for row in mounts:
  require(type(row) is dict and type(row.get('Destination')) is str,'mounts')
  if row.get('Type')=='tmpfs':
   require(row.get('Destination')=='/tmp' and row.get('RW') is True and row.get('Source','')=='','mounts');continue
  require(row.get('Type')=='bind' and row.get('RW') is False,'mounts')
  require(row['Destination'] not in actual,'mounts');actual[row['Destination']]=row.get('Source')
 require(actual=={'/recipe':str(context),'/inputs':str(context/'inputs')},'mounts')
 return True
def validate_manifest(value):
 require(type(value) is dict and set(value)==set(OUTPUTS),'output_inventory')
 for name,row in value.items():
  require(type(row) is dict and set(row)=={'bytes','sha256','identity'} and type(row['bytes']) is int
   and 0<row['bytes']<=OUTPUTS[name] and type(row['sha256']) is str and re.fullmatch('[a-f0-9]{64}',row['sha256']),'output_binding')
  frame=row['identity'];require(type(frame) is list and len(frame)==9 and all(type(v) is int for v in frame)
   and frame[2:7]==[stat.S_IFREG|0o644,0,0,1,row['bytes']],'output_identity')
 return value
def main(argv=None):
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--context');parser.add_argument('--output');parser.add_argument('--evidence');parser.add_argument('--nonce');parser.add_argument('--apply',action='store_true');parser.add_argument('--confirm');parser.add_argument('--variant',choices=tuple(VARIANTS));parser.add_argument('--public-ca');args=parser.parse_args(argv)
 if not args.apply:
  print(json.dumps({'schema':'sqlite_bounded_package_builder/1','status':'UNARMED','variants':{key:value['base'] for key,value in VARIANTS.items()},'required_public_ca':str(PUBLIC_CA),'build_performed':False,'security_accepted':False}));return 0
 require(os.geteuid()==os.getegid()==0 and args.confirm==CONFIRM and type(args.nonce) is str and re.fullmatch('[a-f0-9]{32}',args.nonce),'root_explicit_build')
 select_variant(args.variant)
 require(all(type(v) is str for v in (args.context,args.output,args.evidence)),'paths')
 require(args.public_ca==str(PUBLIC_CA),'public_ca_scope')
 context=Path(args.context);output=Path(args.output);evidence=Path(args.evidence);nonce=args.nonce
 require(context.is_absolute() and context.resolve(strict=True)==context and ',' not in str(context) and '\n' not in str(context),'context')
 context_inventory(context)
 held=[];cid=None;phase='public_admission';paused=False;finished=False;root=None
 new_dir(evidence);(evidence/'docker-config').mkdir(mode=0o700);new_dir(output)
 env={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','HOME':str(evidence),'DOCKER_CONFIG':str(evidence/'docker-config'),'LC_ALL':'C.UTF-8'}
 end=time.monotonic()+8400
 def guard():
  for row in held:row.check()
  for path in (output,root):
   if path is not None:require(os.statvfs(path).f_bavail*os.statvfs(path).f_frsize>=RESERVE,'disk_reserve')
 def cli(parts,seconds=60,maximum=65536,log=None,enforce=True):
  return run([DOCKER,'--host',HOST,*parts],env=env,deadline=min(end,time.monotonic()+seconds) if enforce else time.monotonic()+seconds,maximum=maximum,log=log,guard=guard if enforce else lambda:None)
 def inspect(*,offline,running=True,paused_value=False,enforce=True):
  value=json.loads(cli(['container','inspect','--format',INSPECT,cid],enforce=enforce))
  validate_inspect(value,nonce,cid,context,offline=offline,running=running,paused=paused_value);return value
 def exec_public(code):return json.loads(cli(['container','exec','--workdir','/build',cid,PYTHON,'-I','-B','-c',code]))
 def phase_log(name,action):
  fd=os.open(evidence/(name+'.log'),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
  try:return action(fd)
  finally:
   try:os.fsync(fd)
   finally:os.close(fd)
 try:
  for name,pin in PINS.items():held.append(Held(context/name,pin[0],pin))
  held.append(Held(PUBLIC_CA,CA_PIN[0],CA_PIN))
  meta=Held(context/'context-manifest.json',65536);held.append(meta);value=json.loads(meta.raw)
  require(value=={'schema':'sqlite_allowlisted_context/1','files':{name:{'bytes':pin[0],'sha256':pin[1]} for name,pin in PINS.items()}},'context_manifest')
  ignored=Held(context/'.dockerignore',65536);held.append(ignored)
  allow='**\n!.dockerignore\n!context-manifest.json\n'+''.join('!'+name+'\n' for name in ('sourcebuild.py','Dockerfile','0001-wal-reset-production-only.patch','0002-fts5-detail-none-merge-bound.patch','0003-fts5-detail-none-upstream-regression.patch','verify_positive.py','source-signer.asc','consumer-profile.json'))+'!inputs/\n'+''.join('!'+name+'\n' for name in PINS if name.startswith('inputs/'))
  require(ignored.raw==allow.encode(),'dockerignore')
  root_raw=cli(['info','--format','{{.DockerRootDir}}']).decode('utf-8').strip();root=Path(root_raw)
  require(root==DOCKER_ROOT and root.resolve(strict=True)==root,'docker_root');guard()
  phase='public_base_identity';digests=json.loads(cli(['image','inspect','--format','{{json .RepoDigests}}',BASE]))
  require(type(digests) is list and all(type(v) is str for v in digests)
   and any(v in {'ubuntu@'+BASE.split('@')[1],'docker.io/library/ubuntu@'+BASE.split('@')[1]} for v in digests),'base_digest')
  phase='create';created=cli(create_argv(nonce,context)[3:]).decode('ascii').strip();require(re.fullmatch('[a-f0-9]{64}',created),'container_id');cid=created
  cli(['container','start',cid]);inspect(offline=False)
  phase='public_ca_copy';cli(['container','cp',str(PUBLIC_CA),cid+':'+CA_CONTAINER]);inspect(offline=False)
  phase='configure_repository';dockerfile=next(item for item in held if item.path==context/'Dockerfile')
  script=bootstrap_code(dockerfile.raw)
  phase_log(phase,lambda fd:cli(['container','exec','--workdir','/build',cid,'/bin/sh','-ec',script],seconds=1800,maximum=64*1024**2,log=fd))
  inspect(offline=False);phase='network_cutoff';cli(['network','disconnect','bridge',cid]);inspect(offline=True)
  require(exec_public(OFFLINE)==['lo'],'loopback_only')
  phase='offline_build';phase_log(phase,lambda fd:cli(['container','exec','--workdir','/build',cid,PYTHON,'-I','-B','/recipe/sourcebuild.py','--build'],seconds=4800,maximum=96*1024**2,log=fd))
  inspect(offline=True);require(exec_public(OFFLINE)==['lo'],'loopback_only');before=validate_manifest(exec_public(MANIFEST))
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
  result={'schema':'sqlite_bounded_package_builder/1','status':'PACKAGE_BUILT','container_id':cid,'variant':VARIANT,'base':BASE,'outputs':before,
   'public_ca':{'path':str(PUBLIC_CA),'bytes':CA_PIN[0],'sha256':CA_PIN[1]},
   'offline_build_network_mode':'bridge_with_zero_endpoints_and_loopback_only','resources':{'memory':MEMORY,'nano_cpus':CPUS,'pids':PIDS},
   'container_stopped_and_retained':True,'ordinary_image_ci_still_required':True,'production_data_accessed':False,'fault_tests_performed':False,'security_accepted':False}
  save(evidence/'result.json',encoded(result));print(json.dumps(result,sort_keys=True));return 0
 except BaseException:
  # Never recreate, resume, reuse or remove a prior builder. Retain logs and the
  # owned stopped container. Cleanup uses only this fresh exact returned ID.
  cleanup=False
  if cid is not None:
   try:
    # Match fresh name/label/base and fixed resources before any stop action.
    value=json.loads(cli(['container','inspect','--format',INSPECT,cid],enforce=False))
    require(value.get('id')==cid and value.get('label')==nonce and value.get('name')=='/security-ubuntu-sqlite-package-build-'+nonce and value.get('image_reference')==BASE,'cleanup_owner')
    if value.get('paused') is True:cli(['container','unpause',cid],enforce=False);paused=False
    cli(['container','stop','--time','10',cid],seconds=30,enforce=False)
    value=json.loads(cli(['container','inspect','--format',INSPECT,cid],enforce=False))
    require(value.get('id')==cid and value.get('running') is False and value.get('paused') is False and value.get('pid')==0,'cleanup_not_stopped');cleanup=True
   except BaseException:pass
  failure={'schema':'sqlite_bounded_package_builder/1','status':'FAILED','phase':phase,'container_id':cid,'owned_stop_requested_successfully':cleanup,'security_accepted':False}
  try:save(evidence/'failure.json',encoded(failure))
  except BaseException:pass
  raise
 finally:
  if not finished:
   for item in reversed(held):
    if item.fd is not None:
     try:item.close()
     except BaseException:pass

if __name__=='__main__':raise SystemExit(main())
