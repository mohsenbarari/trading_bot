"""Synthetic own files, actual flock/CAS, mocked Docker; no live/session DB/key."""
from pathlib import Path
from tempfile import TemporaryDirectory
from contextlib import redirect_stdout
from copy import deepcopy
from unittest.mock import patch
import importlib.util,json,os,fcntl,time,io,unittest
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('service_fixtures',ROOT/'tests/test_activate_services.py');F=importlib.util.module_from_spec(spec);spec.loader.exec_module(F);M=F.M
A=M.authority_source();PRIVATE='SYNTHETIC_PRIVATE_EXTRA_MUST_STAY_LOCAL'
class OwnedFixture:
 def __init__(self,root,role):
  self.role=role;self.session=root/'sessions'/role;self.statebase=root/'states';self.state=self.statebase/role
  for p in(self.session,self.state):p.mkdir(mode=0o700,parents=True);os.chown(p,A.UID,A.GID);p.chmod(0o700)
  self.marker=self.session/'authority-container.json';self.original={'contract':A.CONTRACT,'authority':'container','role':role,'release_sha':F.OLDREV,'authorized_at_utc':'2026-09-03T10:21:22.459009Z','extra_private':PRIVATE};self.raw=json.dumps(self.original,ensure_ascii=False,indent=3).encode()+b'\n';self.marker.write_bytes(self.raw);os.chown(self.marker,A.UID,A.GID);self.marker.chmod(0o600)
  self.locks=(self.state/'owner.lock',self.session/'owner.lock');self.lock_before={}
  for lock in self.locks:lock.write_bytes(b'111\n');os.chown(lock,A.UID,A.GID);lock.chmod(0o600);self.lock_before[lock]=(lock.stat().st_ino,lock.read_bytes())
  self.plan,self.rows,self.images=F.fixture();item=self.plan['services'][0];old=next(iter(self.rows.values()));item.update(role='web/'+role,service=role,capture_authority_release_handover=True);old['Config']['Labels']['com.docker.compose.service']=role;old['Config']['User']='10001:10001';old['Config']['Cmd']=['python','-m',M.FOUNDATION,'service','--role',role];old['Config']['Env']+=['MARKET_PIPELINE_SESSION_ROOT=/var/lib/market-data/session','MARKET_PIPELINE_STATE_ROOT=/var/lib/market-data/state']
  old['Mounts']=[{'Type':'bind','Name':None,'Source':str(self.session),'Destination':'/var/lib/market-data/session','RW':True,'Propagation':'rprivate'},{'Type':'bind','Name':None,'Source':str(self.statebase),'Destination':'/var/lib/market-data/state','RW':True,'Propagation':'rprivate'}];old['HostConfig']['Binds']=[m['Source']+':'+m['Destination']+':rw'for m in old['Mounts']]
 def assert_locks_unchanged(self,test):
  for p,(inode,raw)in self.lock_before.items():test.assertEqual(p.stat().st_ino,inode);test.assertEqual(p.read_bytes(),raw)
class Engine(F.Engine):
 def __init__(self,fixture):super().__init__(fixture.rows,fixture.images);self.fixture=fixture;self.fail_candidate=False;self.lifecycle=[]
 def request(self,method,path,value=None,**kw):
  if method=='GET'and path=='/containers/json?all=1':return[{'Id':r['Id'],'State':'running'if r['State']['Running']else'exited','Labels':r['Config']['Labels'],'Mounts':deepcopy(r['Mounts'])}for r in self.rows.values()]
  if method=='POST'and path.endswith('/start'):
   cid=path.split('/')[2];new=M.B.LABEL in self.rows[cid]['Config']['Labels'];marker=json.loads(self.fixture.marker.read_bytes());self.lifecycle.append('new_start'if new else'old_start');assert marker['release_sha']==(self.fixture.plan['services'][0]['release']if new else F.OLDREV)
   if not new:assert self.fixture.marker.read_bytes()==self.fixture.raw
   result=super().request(method,path,value,**kw)
   if new and self.fail_candidate:self.rows[cid]['State'].update(Running=False,Pid=0,Status='exited');self.rows[cid]['State']['Health']={'Status':'unhealthy'}
   return result
  return super().request(method,path,value,**kw)
class Tests(unittest.TestCase):
 def run_fixture(self,role,fail=False,mutate=None):
  tmp=TemporaryDirectory(dir=ROOT);self.addCleanup(tmp.cleanup);root=Path(tmp.name);f=OwnedFixture(root,role)
  if mutate:mutate(f)
  e=Engine(f);e.fail_candidate=fail;op=M.Services(f.plan,root/'fresh-op',time.time()+1800,e);output=io.StringIO()
  with redirect_stdout(output),patch.object(op,'fence'):
   try:result=op.run();error=None
   except Exception as ex:error=ex;result=json.loads((root/'fresh-op/receipt.json').read_bytes())if(root/'fresh-op/receipt.json').exists()else None
  self.assertNotIn(PRIVATE,output.getvalue())
  for p in(root/'fresh-op').glob('*.json'):self.assertNotIn(PRIVATE,p.read_text())
  return f,e,op,result,error,output.getvalue()
 def test_both_capture_roles_change_only_public_release_and_preserve_all_fields_owner_mode_locks(self):
  for role in sorted(A.CAPTURES):
   with self.subTest(role=role):
    f,e,op,result,error,output=self.run_fixture(role);self.assertIsNone(error);self.assertEqual(result['status'],'APPLIED_RUNTIME_ONLY');value=json.loads(f.marker.read_bytes());self.assertEqual(value,{**f.original,'release_sha':F.REV});self.assertEqual(value['authorized_at_utc'],f.original['authorized_at_utc']);self.assertEqual((f.marker.stat().st_uid,f.marker.stat().st_gid,f.marker.stat().st_mode&0o777),(A.UID,A.GID,0o600));f.assert_locks_unchanged(self);self.assertEqual(e.lifecycle,['new_start'])
 def test_same_release_is_validated_exact_noop_without_marker_inode_or_bytes_change(self):
  for role in sorted(A.CAPTURES):
   def same(f):f.plan['services'][0]['release']=F.OLDREV;f.images[f.plan['services'][0]['new_image']]['Config']['Labels']['org.opencontainers.image.revision']=F.OLDREV;f.marker_before=A.frame(f.marker.stat())
   with self.subTest(role=role):
    f,e,op,result,error,output=self.run_fixture(role,mutate=same);self.assertIsNone(error);self.assertEqual(result['status'],'APPLIED_RUNTIME_ONLY');self.assertEqual(f.marker.read_bytes(),f.raw);self.assertEqual(A.frame(f.marker.stat()),f.marker_before);self.assertIn('capture_marker_release_noop_validated',output);self.assertNotIn('capture_marker_release_transferred',output);f.assert_locks_unchanged(self)
 def test_new_start_failure_restores_exact_original_marker_before_old_start_for_both_roles(self):
  for role in sorted(A.CAPTURES):
   with self.subTest(role=role):
    f,e,op,result,error,output=self.run_fixture(role,fail=True);self.assertIsNotNone(error);self.assertEqual(result['status'],'FAILED_RESTORED_OLD');self.assertEqual(f.marker.read_bytes(),f.raw);self.assertEqual(e.lifecycle,['new_start','old_start']);self.assertLess(output.index('capture_marker_original_restored'),output.index('restart_old:'));f.assert_locks_unchanged(self)
 def test_missing_marker_mismatch_key_type_owner_mode_and_symlink_refuse_before_any_stop(self):
  mutations=[lambda f:f.marker.unlink(),lambda f:f.marker.write_text(json.dumps({**f.original,'release_sha':'d'*40})),lambda f:f.marker.write_text(json.dumps({**f.original,'role':123})),lambda f:os.chown(f.marker,0,0),lambda f:f.marker.chmod(0o644)]
  for mutate in mutations:
   with self.subTest(mutate=mutate):
    f,e,op,result,error,output=self.run_fixture('market-capture-account1',mutate=mutate);self.assertIsNotNone(error);self.assertFalse(any(method=='POST'for method,_,_ in e.calls));self.assertTrue(next(iter(e.rows.values()))['State']['Running'])
 def test_unknown_marker_rename_outcome_never_retries_or_starts_old_owner(self):
  with TemporaryDirectory(dir=ROOT)as tmp:
   root=Path(tmp);f=OwnedFixture(root,'market-capture-account1');e=Engine(f);op=M.Services(f.plan,root/'fresh',time.time()+1800,e);replace=A.os.replace;calls=[]
   def uncertain(*args,**kwargs):
    if str(args[1])!=f.marker.name:return replace(*args,**kwargs)
    calls.append(1);replace(*args,**kwargs);raise OSError('synthetic_unknown_rename_ack')
   with redirect_stdout(io.StringIO()),patch.object(op,'fence'),patch.object(A.os,'replace',side_effect=uncertain):
    with self.assertRaises(OSError):op.run()
   result=json.loads((root/'fresh/receipt.json').read_bytes())if(root/'fresh/receipt.json').exists()else json.loads((root/'fresh/receipt.json.partial').read_bytes());self.assertEqual(result['status'],'NEEDS_RECONCILIATION');self.assertTrue(result['pending_requested_action'].startswith('capture_marker_transfer:'));self.assertEqual(e.lifecycle,[]);self.assertEqual(json.loads(f.marker.read_bytes())['release_sha'],F.REV)
 def test_marker_race_after_preflight_refuses_write_and_does_not_start_candidate(self):
  with TemporaryDirectory(dir=ROOT)as tmp:
   root=Path(tmp);f=OwnedFixture(root,'market-capture-account1');e=Engine(f);op=M.Services(f.plan,root/'fresh',time.time()+1800,e)
   with redirect_stdout(io.StringIO()),patch.object(op,'fence'):
    op.preflight();store=op.stores[0];op.stop(store['old']['Id']);changed={**f.original,'extra_private':'concurrent'};f.marker.write_text(json.dumps(changed))
    with self.assertRaisesRegex(RuntimeError,'capture_marker_CAS_changed'):store['capture_authority'].apply()
    op.journal.close()
   self.assertEqual(json.loads(f.marker.read_bytes()),changed);self.assertEqual(e.lifecycle,[])
 def test_busy_session_owner_lock_refuses_without_marker_write(self):
  with TemporaryDirectory(dir=ROOT)as tmp:
   root=Path(tmp);f=OwnedFixture(root,'market-capture-account2');e=Engine(f);op=M.Services(f.plan,root/'fresh',time.time()+1800,e)
   with redirect_stdout(io.StringIO()),patch.object(op,'fence'):
    op.preflight();store=op.stores[0];op.stop(store['old']['Id']);fd=os.open(f.locks[1],os.O_RDWR);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    try:
     with self.assertRaisesRegex(RuntimeError,'capture_owner_lock_busy'):store['capture_authority'].apply()
    finally:os.close(fd);op.journal.close()
   self.assertEqual(f.marker.read_bytes(),f.raw);f.assert_locks_unchanged(self)
 def test_extra_running_physical_session_owner_refuses_preflight_without_unrelated_Config_inspection(self):
  with TemporaryDirectory(dir=ROOT)as tmp:
   root=Path(tmp);f=OwnedFixture(root,'market-capture-account1');e=Engine(f);extra=deepcopy(next(iter(e.rows.values())));extra['Id']=F.CID(777);extra['Config']['Labels']['com.docker.compose.project']='unrelated';e.rows[extra['Id']]=extra;op=M.Services(f.plan,root/'fresh',time.time()+1800,e);original=e.inspect
   def inspect(cid):self.assertNotEqual(cid,extra['Id'],'unrelated Config/Env must not be inspected');return original(cid)
   e.inspect=inspect
   with redirect_stdout(io.StringIO()),patch.object(op,'fence'),self.assertRaisesRegex(RuntimeError,'capture_physical_session_owner_overlap'):op.run()
   self.assertEqual(f.marker.read_bytes(),f.raw);self.assertFalse(any(method=='POST'for method,_,_ in e.calls))
if __name__=='__main__':unittest.main()
