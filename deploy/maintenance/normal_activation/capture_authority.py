"""Finite same-owner capture marker release handover; session DB/key unread.
Normal market_capture_authority/1.0 semantics, bounded4096 CAS, no controller.
"""
from pathlib import Path,PurePosixPath
from contextlib import ExitStack,contextmanager
from datetime import datetime
from copy import deepcopy
import fcntl,json,os,re,stat
CAPTURES={'market-capture-account1','market-capture-account2'}
CONTRACT='market_capture_authority/1.0'
UID=GID=10001
MAXIMUM=4096

def need(value,code):
 if not value:raise RuntimeError(code)
def frame(s):return(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def unique(pairs):
 result={}
 for k,v in pairs:need(k not in result,'capture_marker_duplicate_key');result[k]=v
 return result

def no_symlink(path):
 need(path.is_absolute(),'capture_authority_absolute_path')
 try:
  for parent in (*reversed(path.parents),path):need(not parent.is_symlink(),'capture_authority_symlink')
  need(path.resolve(strict=True)==path,'capture_authority_canonical_path')
 except OSError:raise RuntimeError('capture_authority_path_unavailable')from None
def read_marker(path):
 no_symlink(path);fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
 try:
  old=os.fstat(fd);need(stat.S_ISREG(old.st_mode)and(old.st_uid,old.st_gid)==(UID,GID)and stat.S_IMODE(old.st_mode)==0o600 and old.st_nlink==1 and 0<old.st_size<=MAXIMUM,'capture_marker_identity')
  raw=os.pread(fd,MAXIMUM+1,0);need(len(raw)==old.st_size and frame(old)==frame(os.fstat(fd))==frame(path.lstat()),'capture_marker_changed');value=json.loads(raw,object_pairs_hook=unique);need(type(value)is dict,'capture_marker_shape');return value,raw,old
 finally:os.close(fd)
def validate(value,role,release):
 need(type(release)is str and re.fullmatch('[a-f0-9]{40}',release)is not None,'capture_marker_release_type')
 need(value.get('contract')==CONTRACT and value.get('authority')=='container'and value.get('role')==role and value.get('release_sha')==release,'capture_marker_public_binding')
 stamp=value.get('authorized_at_utc');need(type(stamp)is str and 0<len(stamp)<=100,'capture_authorized_time')
 try:parsed=datetime.fromisoformat(stamp.replace('Z','+00:00'))
 except ValueError:raise RuntimeError('capture_authorized_time')from None
 need(parsed.tzinfo is not None and parsed.utcoffset().total_seconds()==0,'capture_authorized_time')
def environment_path(config,key,default):
 rows=[v.split('=',1)[1]for v in config.get('Env')or[]if type(v)is str and v.startswith(key+'=')];need(len(rows)<=1,'capture_path_key_ambiguous');value=rows[0]if rows else default;p=PurePosixPath(value);need(p.is_absolute()and'..'not in p.parts and str(p)==value,'capture_container_path');return p

def host_path(old,container):
 matches=[]
 for m in old['Mounts']:
  target=PurePosixPath(m['Destination'])
  if container==target or target in container.parents:matches.append((len(target.parts),m,container.relative_to(target)))
 need(matches,'capture_authority_mount_missing');_,mount,relative=max(matches,key=lambda x:x[0]);need(mount['Type']in('bind','volume')and mount.get('RW')is True,'capture_authority_mount_not_writable');result=Path(mount['Source']).joinpath(*relative.parts);no_symlink(result);return result

@contextmanager
def held_existing(path,expected_inode):
 no_symlink(path);before=path.lstat();need((before.st_dev,before.st_ino)==expected_inode,'capture_owner_lock_inode_replaced');need(stat.S_ISREG(before.st_mode)and(before.st_uid,before.st_gid)==(UID,GID)and stat.S_IMODE(before.st_mode)==0o600 and before.st_nlink==1 and before.st_size<=4096,'capture_existing_owner_lock')
 fd=os.open(path,os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC)
 try:
  need(frame(os.fstat(fd))==frame(before),'capture_owner_lock_changed')
  try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:raise RuntimeError('capture_owner_lock_busy')from None
  need(frame(os.fstat(fd))==frame(path.lstat())==frame(before),'capture_owner_lock_changed');yield
  need(frame(os.fstat(fd))==frame(path.lstat())==frame(before),'capture_owner_lock_changed')
 finally:os.close(fd)

class Binding:
 def __init__(self,operation,store):
  self.op=operation;self.store=store;self.role=store['plan']['service'];need(operation.plan['site']=='web'and self.role in CAPTURES,'capture_authority_closed_scope');need(store['plan'].get('capture_authority_release_handover')is True,'explicit_capture_marker_handover_required')
  old=store['old'];config=old['Config'];need(config.get('User')in('10001','10001:10001'),'capture_authority_runtime_uid');self.previous=store['old_version'];self.target=store['plan']['release'];need(type(self.previous)is str and re.fullmatch('[a-f0-9]{40}',self.previous)is not None,'capture_release_transition')
  self.session=host_path(old,environment_path(config,'MARKET_PIPELINE_SESSION_ROOT','/var/lib/market-data/session'));state_container=environment_path(config,'MARKET_PIPELINE_STATE_ROOT','/var/lib/market-data/state')/self.role;self.state=host_path(old,state_container);self.marker=self.session/'authority-container.json';self.locks=(self.state/'owner.lock',self.session/'owner.lock');self.lock_inodes={}
  for lock in self.locks:no_symlink(lock);s=lock.stat();self.lock_inodes[lock]=(s.st_dev,s.st_ino)
  need(len(set(self.lock_inodes.values()))==2,'capture_owner_lock_alias')
  for root in(self.state,self.session):need(root.stat().st_uid==UID and root.stat().st_gid==GID and not root.stat().st_mode&0o022,'capture_authority_directory')
  for p in self.locks:
   no_symlink(p);s=p.stat();need(stat.S_ISREG(s.st_mode)and(s.st_uid,s.st_gid)==(UID,GID)and stat.S_IMODE(s.st_mode)==0o600 and s.st_nlink==1,'capture_existing_owner_lock')
  self.original,self.original_raw,self.original_stat=read_marker(self.marker);validate(self.original,self.role,self.previous);self.changed=None;self.changed_raw=None;self.changed_stat=None;self.applied=False;self.owners(old['Id'])
 def owners(self,allowed):
  rows=self.op.engine.request('GET','/containers/json?all=1');need(type(rows)is list,'capture_physical_owner_inventory');seen=[]
  for row in rows:
   if row.get('State')!='running':continue
   for mount in row.get('Mounts')or[]:
    if mount.get('Type')not in('bind','volume')or not mount.get('RW'):continue
    source=Path(mount['Source']);need(source.is_absolute(),'capture_owner_mount_source')
    if source==self.session or source in self.session.parents or self.session in source.parents:seen.append(row['Id']);break
  need(set(seen)==({allowed}if allowed is not None else set()),'capture_physical_session_owner_overlap')
 def stopped(self):
  old=self.op.inspect(self.store['old']['Id']);need(old['State']['Running']is False and old['State']['Pid']==0,'capture_old_owner_not_stopped')
  if self.store['new']is not None:
   new=self.op.inspect(self.store['new']);need(new['State']['Running']is False and new['State']['Pid']==0,'capture_candidate_owner_not_stopped')
  self.owners(None)
 def replace(self,target_raw,expected_raw,expected_stat,action):
  self.op.guard();self.stopped()
  with ExitStack()as held:
   for path in self.locks:held.enter_context(held_existing(path,self.lock_inodes[path]))
   self.stopped();current,raw,observed=read_marker(self.marker);need(raw==expected_raw and frame(observed)==frame(expected_stat),'capture_marker_CAS_changed');parent=self.marker.parent;no_symlink(parent);parent_before=parent.stat();directory=os.open(parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC);temporary='.'+self.marker.name+'.security-'+self.op.nonce+'.'+action+'.partial'
   try:
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory)
    try:
     os.fchmod(fd,0o600);os.fchown(fd,UID,GID)
     with os.fdopen(fd,'wb',closefd=False)as out:out.write(target_raw);out.flush();os.fsync(fd)
    finally:os.close(fd)
    _,raw,observed=read_marker(self.marker);need(raw==expected_raw and frame(observed)==frame(expected_stat)and(parent.stat().st_dev,parent.stat().st_ino)==(parent_before.st_dev,parent_before.st_ino),'capture_marker_CAS_changed');self.stopped();self.op.pending='capture_marker_'+action+':'+self.role;self.op.event('requested',action=self.op.pending)
    os.replace(temporary,self.marker.name,src_dir_fd=directory,dst_dir_fd=directory);os.fsync(directory)
    value,raw,observed=read_marker(self.marker);need(raw==target_raw,'capture_marker_write_verification');return value,raw,observed
   finally:os.close(directory)
 def apply(self):
  need(not self.applied,'capture_marker_already_transferred')
  if self.previous==self.target:
   self.op.guard();self.stopped()
   with ExitStack()as held:
    for path in self.locks:held.enter_context(held_existing(path,self.lock_inodes[path]))
    self.stopped();value,raw,observed=read_marker(self.marker);need(raw==self.original_raw and frame(observed)==frame(self.original_stat),'capture_marker_CAS_changed');validate(value,self.role,self.previous)
   self.op.event('capture_marker_release_noop_validated',role=self.role,release=self.target);return
  target=deepcopy(self.original);target['release_sha']=self.target;validate(target,self.role,self.target);without=deepcopy(target);without['release_sha']=self.previous;need(without==self.original,'capture_marker_other_fields_changed');raw=(json.dumps(target,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n').encode();need(len(raw)<=MAXIMUM,'capture_marker_bound')
  self.changed,self.changed_raw,self.changed_stat=self.replace(raw,self.original_raw,self.original_stat,'transfer');self.applied=True;self.op.pending=None;self.op.event('capture_marker_release_transferred',role=self.role,old_release=self.previous,new_release=self.target)
 def restore(self):
  if not self.applied:return
  value,_,_=self.replace(self.original_raw,self.changed_raw,self.changed_stat,'restore');validate(value,self.role,self.previous);self.applied=False;self.op.pending=None;self.op.event('capture_marker_original_restored',role=self.role,old_release=self.previous)
