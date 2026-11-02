"""Actual pure reader and inert probe children; no SSH/Git/Docker/live inputs."""
from contextlib import ExitStack
from hashlib import sha256
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
import ast,json,os,selectors,subprocess,sys,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1];PLANS=ROOT.parents[1]
def load(path):
 m=ModuleType('owned_'+path.stem);m.__file__=str(path);exec(compile(path.read_bytes(),str(path),'exec'),m.__dict__);return m
M=load(ROOT/'scripts/prepare_market_capture2_image.py')
class ReaderPayload(unittest.TestCase):
 def own(self):
  temporary=tempfile.TemporaryDirectory(prefix='.owned-reader-',dir=Path(__file__).parent);self.addCleanup(temporary.cleanup);root=Path(temporary.name);root.chmod(0o700);return root
 def reader(self):return(ROOT/M.READER).read_bytes()
 def test_exact_pure_reader_import_has_no_ssh_private_paths_or_ambient_actions(self):
  raw=self.reader();self.assertEqual((len(raw),sha256(raw).hexdigest()),(30085,'da8455903a8194b3b321331ed6fa5eb70b5c4e09a227004be3519e3a5e8d8d66'))
  for forbidden in(b'subprocess',b'/root/',b'/srv/',b'.ssh',b'65.109.',b'os.environ'):
   self.assertNotIn(forbidden,raw)
  reader=ModuleType('owned_pure_reader')
  with ExitStack()as guard:
   for name in('os.open','subprocess.Popen','subprocess.run','socket.socket','socket.create_connection','urllib.request.urlopen'):
    guard.enter_context(patch(name,side_effect=AssertionError('ambient action on import denied')))
   exec(compile(raw,'owned_reader.py','exec'),reader.__dict__)
  self.assertTrue(callable(reader._inspect));self.assertEqual(len(reader.ROWS),93)
 def test_tracked_payload_executes_real_owned93_metadata_without_owner_mask(self):
  reader=load(ROOT/M.READER);fixture=PLANS/'security-market-product-lock-proposal-20261026-02/public-product-fixture-01';owner=(os.geteuid(),os.getegid())
  self.assertEqual((fixture.stat().st_uid,fixture.stat().st_gid),owner)
  self.assertEqual(reader._inspect(fixture,reader.ROWS,owner),{'source_files':93,'excluded_cache_metadata_files':0})
 def test_controller_admits_exact_manifest93_and_rows_then_refuses_old_operator(self):
  result=M.validate_reader_source(self.reader());self.assertEqual(result['source_files'],93);self.assertEqual(result['manifest_sha256'],M.MANIFEST_SHA);self.assertEqual(result['rows_sha256'],M.ROWS_SHA)
  old=(PLANS/'security-market-normal-ci-integration-20261027-06/tree'/M.READER).read_bytes()
  with self.assertRaisesRegex(RuntimeError,'reader_source_pin'):M.validate_reader_source(old)
  changed=bytearray(self.reader());changed[-2]^=1
  with self.assertRaisesRegex(RuntimeError,'reader_source_pin'):M.validate_reader_source(bytes(changed))
 def test_wrong_tracked_reader_refuses_before_copy_or_expensive_build(self):
  root=self.own();old=(PLANS/'security-market-normal-ci-integration-20261027-06/tree'/M.READER).read_bytes();raws={name:(ROOT/name).read_bytes()for name in M.CONTROLLER_FILES};raws[M.READER]=old
  def git(repo,*args,**kwargs):
   self.assertEqual(args[:2],('ls-tree','-z'));name=args[-1];body=raws[name];blob=M.sha1(b'blob '+str(len(body)).encode()+b'\0'+body).hexdigest();return('100644 blob '+blob+'\t'+name+'\0').encode()
  with patch.object(M,'git_source',side_effect=lambda repo,release,name:raws[name]),patch.object(M,'git',side_effect=git),self.assertRaisesRegex(RuntimeError,'reader_source_pin'):M.controller_sources(root,'a'*40,root)
  self.assertFalse((root/'controller-source'/M.READER).exists())
 def test_genuine_nonzero_callback_is_bounded_and_before_selector_pipe_close(self):
  actual=subprocess.Popen;events=[];child=[];original_close=selectors.DefaultSelector.close;body=b'BEGIN'+b'X'*20000+b'END'
  def spawn(*args,**kwargs):c=actual(*args,**kwargs);child.append(c);return c
  def nonzero(code,raw,count):
   self.assertEqual(code,17);self.assertEqual(raw,body);self.assertEqual(count,len(body));self.assertEqual(child[0].returncode,17);events.append('failure')
  def close(selector):events.append('selector');self.assertEqual(events[0],'failure');return original_close(selector)
  with patch.object(M.subprocess,'Popen',spawn),patch.object(M.selectors.DefaultSelector,'close',close),patch.object(M.os,'killpg',side_effect=AssertionError('reaped child must never be signalled')),self.assertRaisesRegex(RuntimeError,'command_failed'):
   M.command([sys.executable,'-I','-S','-B','-c','import os;os.write(1,'+repr(body)+');raise SystemExit(17)'],environment={'PATH':os.defpath},maximum=32768,seconds=5,nonzero=nonzero)
  self.assertEqual(events,['failure','selector']);self.assertTrue(child[0].stdout.closed)
 def test_nonzero_hook_does_not_change_default_or_success_and_bounds_precede_capture(self):
  env={'PATH':os.defpath};seen=[]
  self.assertEqual(M.command([sys.executable,'-I','-S','-B','-c',"print('PUBLIC')"],environment=env,maximum=128,nonzero=lambda *args:seen.append(args)),b'PUBLIC\n');self.assertEqual(seen,[])
  with self.assertRaisesRegex(RuntimeError,'command_failed'):M.command([sys.executable,'-I','-S','-B','-c','raise SystemExit(17)'],environment=env,maximum=128)
  with self.assertRaisesRegex(RuntimeError,'command_bound'):M.command([sys.executable,'-I','-S','-B','-c',"print('X'*1024);raise SystemExit(17)"],environment=env,maximum=128,nonzero=lambda *args:seen.append(args))
  self.assertEqual(seen,[])
 def test_fixed_probe_start_failure_retains_code_bounded_prefix_tail_without_command_env(self):
  for kind in('source93','sqlite'):
   with self.subTest(kind=kind):
    root=self.own();(root/'reports').mkdir(mode=0o700);image='sha256:'+'a'*64;signature='b'*64;cid='c'*64;name='normal-market-'+kind+'-'+signature[:24];plan=M.probe_plan(root,image,M.READER_SHA256);body=b'PUBLIC BEGIN\n'+b'X'*20000+b'\nPUBLIC END';commands=[];children=[];actual=subprocess.Popen
    info={'Id':cid,'Name':'/'+name,'Image':image,'Config':{'Labels':{'io.gold-trade.normal-market-ci-owner':signature},'User':plan[kind]['user'],'Entrypoint':['/usr/local/bin/python3']},'HostConfig':{'ReadonlyRootfs':True,'Privileged':False,'NetworkMode':'none','CapAdd':None,'CapDrop':['ALL'],'SecurityOpt':['no-new-privileges:true'],'Memory':536870912,'MemorySwap':536870912,'NanoCpus':10**9,'PidsLimit':128,'Tmpfs':None if kind=='source93'else{'/tmp':'rw,nosuid,nodev,mode=1777,size=67108864'}},'Mounts':[{'Type':'bind','Source':str(root/'proof/reader.py'),'Destination':'/proof/reader.py','RW':False}]if kind=='source93'else[{'Type':'tmpfs','Destination':'/tmp'}],'State':{'Running':False,'Paused':False,'Pid':0,'ExitCode':17}}
    def spawn(argv,**kwargs):
     self.assertEqual(argv[:2],M.DOCKER);self.assertNotIn('GH_TOKEN',kwargs['env']);tail=argv[2:];commands.append(tail)
     if tail[:2]==['container','create']:raw=(cid+'\n').encode();code=0
     elif tail[:2]==['container','inspect']:raw=M.encoded(info);code=0
     elif tail==['container','start','--attach',cid]:raw=body;code=17
     elif tail==['container','rm',cid]:raw=(cid+'\n').encode();code=0
     else:raise AssertionError('unaccepted inert command')
     program='import os;raw='+repr(raw)+'\nwhile raw:\n n=os.write(1,raw);raw=raw[n:]\nraise SystemExit('+str(code)+')';child=actual([sys.executable,'-I','-S','-B','-c',program],**kwargs);children.append(child);return child
    with patch.object(M.subprocess,'Popen',spawn),self.assertRaisesRegex(RuntimeError,'command_failed'):M.run_owned_probe(root,image,signature,kind,plan,guard=lambda:None)
    path=root/'reports'/('probe-'+kind+'-start-failure.json');result=json.loads(path.read_bytes());self.assertEqual(result['actual_client_returncode'],17);self.assertEqual(result['kind'],kind);self.assertEqual(result['prefix_bytes'],8192);self.assertEqual(result['tail_bytes'],8192);self.assertTrue(result['output_truncated']);self.assertTrue(result['output_tail'].endswith('PUBLIC END'));self.assertFalse(result['acceptance_evidence']);self.assertFalse(result['security_accepted'])
    self.assertFalse({'argv','environment','Env','command'}&set(result));self.assertEqual(path.stat().st_mode&0o777,0o600);self.assertEqual(len(list((root/'reports').iterdir())),1);self.assertEqual(commands[-1],['container','rm',cid])
    for child in children:
     self.assertIsNotNone(child.returncode)
     with self.assertRaises(ChildProcessError):os.waitpid(child.pid,os.WNOHANG)
if __name__=='__main__':unittest.main()
