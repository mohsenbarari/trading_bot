"""Five fixed Docker argv contracts, actual owned inert children and files only."""
from pathlib import Path
from types import ModuleType,SimpleNamespace
from unittest.mock import patch
import json,os,signal,subprocess,sys,tempfile,time,unittest
REPO=Path(__file__).resolve().parents[1]
HERE=REPO/'deploy/maintenance/ba938_smoke'
TEST_ROOT=Path(__file__).resolve().parent


def load(leaf):
 path=HERE/leaf;module=ModuleType('_owned_cli_'+leaf.replace('.','_'));module.__file__=str(path)
 exec(compile(path.read_bytes(),str(path),'exec'),module.__dict__);return module


C=load('controller.py');B=load('compose_security_os_candidate.py');O=load('run_app_image_ci_smoke.py')


class InventoryCliTests(unittest.TestCase):
 def setUp(self):
  tmp=tempfile.TemporaryDirectory(prefix='.owned-cli-',dir=TEST_ROOT);self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
  for leaf in('reports','docker-config','whole-acl-proof'):(self.root/leaf).mkdir()
  self.ctx=SimpleNamespace(root=self.root,C=B,cleanup_end=None,end=time.monotonic()+5,check=lambda:None,
   write=lambda path,value:B.write(path,C.encode(value)))
  self.children=[]
 def invoke(self,stage,body,*,pipe_close=None):
  actual=subprocess.Popen
  def spawn(argv,**kwargs):
   self.assertEqual(argv[:2],O.DOCKER);self.assertNotIn('GH_TOKEN',kwargs['env'])
   self.assertEqual(kwargs['env'],{'PATH':'/usr/bin:/bin','LC_ALL':'C.UTF-8','DOCKER_CONFIG':str(self.root/'docker-config')})
   child=actual([sys.executable,'-I','-S','-B','-c',body],**kwargs);self.children.append(child)
   if pipe_close is not None:
    real=child.stdout
    class Pipe:
     def fileno(self):return real.fileno()
     def close(self):real.close();pipe_close()
    child.stdout=Pipe()
   return child
  with patch.object(C.subprocess,'Popen',spawn):
   return C.inventory_cli(self.ctx,O,stage,image=C.CONTROL['image_id'],proof=self.root/'whole-acl-proof',gid=os.getegid(),
    control=C.CONTROL,cid=None if stage=='create'else'a'*64)
 def record(self,stage):return json.loads((self.root/'reports'/('inventory-cli-'+stage+'.json')).read_bytes())
 def test_true_zero_and_stderr_warning_leave_parsed_stdout_unchanged(self):
  raw=b'{"owned":true}\n'
  with patch.object(B.os,'killpg',side_effect=AssertionError('post-reap signal denied')):
   result=self.invoke('preinspect','import os;os.write(1,'+repr(raw)+');os.write(2,b"owned warning\\n")')
  self.assertEqual(result,raw);self.assertEqual(json.loads(result),{'owned':True})
  value=self.record('preinspect');self.assertEqual(value['actual_client_returncode'],0);self.assertEqual(value['stderr_text'],'owned warning\n')
  self.assertEqual(value['stage'],'preinspect');self.assertFalse(value['security_accepted'])
 def test_nonzero_keeps_actual_exit_and_both_public_streams(self):
  raw=b'{"status":"REFUSED"}\n'
  with patch.object(B.os,'killpg',side_effect=AssertionError('post-reap signal denied')),self.assertRaisesRegex(RuntimeError,'inventory_cli_nonzero'):
   self.invoke('start','import os;os.write(1,'+repr(raw)+');os.write(2,b"owned error\\n");raise SystemExit(17)')
  self.assertEqual((self.root/'reports/fresh-whole-image-acl-output.json').read_bytes(),raw)
  self.assertEqual(self.record('start')['actual_client_returncode'],17);self.assertEqual(self.record('start')['stderr_text'],'owned error\n')
 def test_each_stream_is_bounded_before_accumulation_and_live_child_reaped(self):
  for stage,fd,size,code in(('create',1,300,'inventory_cli_stdout_bound'),('exit',2,9000,'inventory_cli_stderr_bound')):
   with self.subTest(stream=fd),self.assertRaisesRegex(RuntimeError,code):
    self.invoke(stage,'import os,time;os.write('+str(fd)+',b"x"*'+str(size)+');time.sleep(2)')
   self.assertIsNotNone(self.children[-1].returncode)
   value=self.record(stage);self.assertLessEqual(len(value['stderr_text'].encode()),8192)
 def test_real_timeout_proves_wnowait_ownership_then_signals_only_owned_child(self):
  self.ctx.end=time.monotonic()+.04;actual=os.killpg;signals=[]
  def killpg(pid,sig):
   self.assertEqual(pid,self.children[-1].pid);self.assertIsNone(self.children[-1].returncode)
   os.waitid(os.P_PID,pid,os.WEXITED|os.WNOHANG|os.WNOWAIT);signals.append((pid,sig));return actual(pid,sig)
  with patch.object(B.os,'killpg',killpg),self.assertRaisesRegex(RuntimeError,'inventory_cli_timeout'):
   self.invoke('exit','import time;time.sleep(2)')
  self.assertEqual(signals,[(self.children[-1].pid,signal.SIGKILL)]);self.assertIsNotNone(self.children[-1].returncode)
 def test_unknown_stage_cannot_launch_any_child(self):
  with patch.object(C.subprocess,'Popen',side_effect=AssertionError('unapproved command denied')),self.assertRaisesRegex(RuntimeError,'inventory_cli_stage'):
   C.inventory_cli(self.ctx,O,'OWNED-UNKNOWN',image=C.CONTROL['image_id'],proof=self.root/'whole-acl-proof',gid=os.getegid(),control=C.CONTROL)
 def test_optional_tmpfs_lookup_is_explicit_without_loosening_mount_policy(self):
  template=C.inventory_inspect_template(O)
  self.assertEqual(template.count('{{json (index .HostConfig "Tmpfs")}}'),1);self.assertNotIn('{{json .HostConfig.Tmpfs}}',template)
  name='app-ci-whole-acl-'+O.owner();image=C.CONTROL['image_id'];proof=self.root/'whole-acl-proof'
  row={'name':'/'+name,'image':image,'owner':O.owner(),'readonly':True,'privileged':False,'user':'0:0','network':'none','networks':{},
   'caps_add':None,'caps_drop':['ALL'],'security':['no-new-privileges'],'memory':512*1024**2,'memory_swap':512*1024**2,
   'nano_cpus':10**9,'pids':64,'tmpfs':None,'running':False,'paused':False,'pid':0,
   'mounts':[{'Type':'bind','Source':str(proof),'Destination':'/proof','RW':False}]}
  for value in(None,{}):
   with self.subTest(tmpfs=value):C.inventory_container({**row,'tmpfs':value},O,name,image,proof)
  with self.assertRaisesRegex(RuntimeError,'presence_identity'):C.inventory_container({**row,'tmpfs':{'/owned-extra':'rw'}},O,name,image,proof)
 def test_late_pipe_close_cannot_mutate_creation_bound_prior_report_unnoticed(self):
  keeper=B.ProofKeeper(self.root);prior=self.root/'reports/prior.json';changed=[]
  self.ctx.check=keeper.check
  def mutate():changed.append(True);prior.write_bytes(b'{"owned":false}\n')
  with patch.object(B,'PROOF_KEEPER',keeper):
   B.write(prior,b'{"owned":true}\n')
   with self.assertRaisesRegex(RuntimeError,'proof_changed'):self.invoke('exit','print(0)',pipe_close=mutate)
   try:keeper.close()
   except RuntimeError:pass
  self.assertTrue(changed);self.assertTrue(all(row.fd is None and row.parents.closed for row in keeper.records))


if __name__=='__main__':unittest.main()
