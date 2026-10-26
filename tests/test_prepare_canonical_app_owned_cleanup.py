"""Real owned inert children only; no Docker/Git/native/build/runtime actions."""
from pathlib import Path
from hashlib import sha256
import ast
from types import ModuleType
from unittest.mock import patch
import os,subprocess,sys,unittest
P=Path(__file__).resolve().parents[1]
K=ModuleType('_owned_canonical_cleanup');K.__file__=str(P/'scripts/prepare_canonical_app_image.py');SOURCE=Path(K.__file__).read_bytes()
exec(compile(SOURCE,K.__file__,'exec'),K.__dict__)
UNCHANGED_COMMAND_AST_SHA256='77e35861be950e8762f24ab5f25f8006169d9a28daeed3d79236e70373aad2bf'
EXPECTED_FINALIZER="clean = True\nif child is not None and child.returncode is None:\n    try:\n        os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)\n        try:\n            os.killpg(child.pid, signal.SIGKILL)\n        except ProcessLookupError:\n            pass\n        child.wait(timeout=3)\n    except BaseException:\n        clean = False\nif target is None:\n    try:\n        os.close(private)\n    except OSError:\n        clean = False\nif not clean and (not primary):\n    raise RuntimeError('cleanup_unproven')"

def admitted_command():
 node=next(n for n in ast.parse(SOURCE).body if isinstance(n,ast.FunctionDef)and n.name=='command')
 blocks=[n for n in node.body if isinstance(n,ast.Try)]
 if len(blocks)!=1:raise AssertionError('command structure differs from reviewed baseline')
 block=blocks[0];actual=ast.dump(ast.Module(body=block.finalbody,type_ignores=[]),include_attributes=False)
 if actual!=ast.dump(ast.parse(EXPECTED_FINALIZER),include_attributes=False):raise AssertionError('cleanup differs from reviewed owned-child finalizer')
 block.finalbody=[]
 for item in ast.walk(node):
  # Empty type_params was added to FunctionDef in Python3.12. Normalize
  # only that absent/empty field; a nonempty type parameter list refuses.
  if hasattr(item,'type_params'):
   if item.type_params:raise AssertionError('unreviewed command type parameters')
   item._fields=tuple(name for name in item._fields if name!='type_params')
 if sha256(ast.dump(node,include_attributes=False).encode()).hexdigest()!=UNCHANGED_COMMAND_AST_SHA256:raise AssertionError('command limits/lifecycle differ from reviewed baseline')

class CleanupTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  # Refuse unsafe/stale command code BEFORE any inert child is created.
  admitted_command()
 def command(self,code,seconds=5,maximum=128):
  return K.command([sys.executable,'-I','-S','-B','-c',code],environment={'PATH':os.defpath},seconds=seconds,maximum=maximum)
 def test_normal_reaped_success_never_signals_a_released_process_group(self):
  with patch.object(K.os,'killpg',side_effect=AssertionError('postreap signal forbidden')):
   self.assertEqual(self.command("print('OWNED PUBLIC')"),b'OWNED PUBLIC\n')
 def test_normal_reaped_nonzero_preserves_command_failure_without_signal(self):
  with patch.object(K.os,'killpg',side_effect=AssertionError('postreap signal forbidden')),self.assertRaisesRegex(RuntimeError,'command_failed'):
   self.command("print('OWNED PUBLIC');raise SystemExit(3)")
 def test_real_reap_then_interrupted_wait_never_signals_reused_identity(self):
  real=subprocess.Popen;children=[]
  class Interrupted:
   def __init__(self,child):self.child=child;self.once=True
   def __getattr__(self,name):return getattr(self.child,name)
   def wait(self,*a,**kw):
    value=self.child.wait(*a,**kw)
    if self.once:self.once=False;raise RuntimeError('owned_interrupted_after_reap')
    return value
  def create(*a,**kw):child=real(*a,**kw);children.append(child);return Interrupted(child)
  with patch.object(K.subprocess,'Popen',side_effect=create),patch.object(K.os,'killpg',side_effect=AssertionError('postreap signal forbidden')),self.assertRaisesRegex(RuntimeError,'owned_interrupted_after_reap'):
   self.command("print('OWNED PUBLIC')")
  self.assertEqual(children[0].returncode,0)
 def test_live_owned_timeout_is_signalled_once_only_while_kernel_still_owns_pid(self):
  real=subprocess.Popen;signal=K.os.killpg;waitid=K.os.waitid;children=[];verified=[];signals=[]
  def create(*a,**kw):child=real(*a,**kw);children.append(child);return child
  def observe(kind,pid,flags):
   self.assertEqual(kind,os.P_PID);self.assertEqual(pid,children[0].pid);self.assertTrue(flags&os.WNOWAIT);self.assertIsNone(children[0].returncode)
   value=waitid(kind,pid,flags);verified.append(pid);return value
  def kill(pid,value):
   self.assertEqual(verified,[pid]);self.assertEqual(pid,children[0].pid);self.assertIsNone(children[0].returncode);signals.append(pid);return signal(pid,value)
  with patch.object(K.subprocess,'Popen',side_effect=create),patch.object(K.os,'waitid',side_effect=observe),patch.object(K.os,'killpg',side_effect=kill),self.assertRaisesRegex(RuntimeError,'command_timeout'):
   self.command('import time;time.sleep(5)',seconds=.04)
  self.assertEqual(signals,[children[0].pid]);self.assertIsNotNone(children[0].returncode)
 def test_existing_kernel_output_bound_still_refuses_without_accumulation(self):
  with self.assertRaisesRegex(RuntimeError,'command_output_bound'):self.command("import os;os.write(1,b'x'*65536)",maximum=16)
 def test_patch_changes_only_cleanup_in_command_and_preserves_existing_bounds(self):
  admitted_command()
  self.assertIn('child.wait(timeout=3)',SOURCE.decode())
  self.assertIn('resource.RLIMIT_FSIZE,(maximum+1,maximum+1)',SOURCE.decode())
if __name__=='__main__':unittest.main()
