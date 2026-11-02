"""Portable pure-source preflight; no Git, Docker, network or hosted bypass."""
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
import ast,json,os,tempfile,time,unittest
HERE=Path(__file__).resolve().parent
def load(leaf):
 p=HERE/leaf;m=ModuleType('flat_preflight_'+p.stem);m.__file__=str(p);exec(compile(p.read_bytes(),str(p),'exec'),m.__dict__);return m
A=load('archive_inventory.py');E=load('export_controller.py');C=load('controller.py')
class FlatInventoryPreflight(unittest.TestCase):
 def test_every_literal_flat_parser_refusal_has_closed_public_code(self):
  for leaf in('inventory.py','archive_inventory.py','export_controller.py'):
   tree=ast.parse((HERE/leaf).read_bytes())
   for node in ast.walk(tree):
    if isinstance(node,ast.Call)and isinstance(node.func,ast.Name)and node.func.id in('need','require')and len(node.args)>1 and isinstance(node.args[1],ast.Constant)and type(node.args[1].value)is str:
     code=node.args[1].value;self.assertIn(code,C.FLAT_FAILURE_CODES);self.assertEqual(C.public_failure_code(RuntimeError(code),C.FLAT_FAILURE_CODES),code)
 def test_unknown_private_operands_extra_args_and_nonruntime_errors_stay_private(self):
  sentinel='OWNED_PRIVATE_SENTINEL'
  for error in(RuntimeError(sentinel),RuntimeError('archive_deadline',sentinel),ValueError('archive_deadline'),RuntimeError(True)):
   result=C.public_failure_code(error,C.FLAT_FAILURE_CODES);self.assertEqual(result,'unexpected_failure');self.assertNotIn(sentinel,json.dumps({'code':result}))
 def test_finite600_accepts_work_beyond_old120_and601_expired_refuse(self):
  self.assertEqual(A.MAX_INVENTORY_SECONDS,600);self.assertEqual(E.MAX_INVENTORY_SECONDS,600)
  with tempfile.TemporaryDirectory(prefix='.flat-preflight-',dir=HERE)as temporary:
   p=Path(temporary)/'public.tar';p.write_bytes(bytes(1024));p.chmod(0o600)
   with p.open('rb')as stream:
    source=A.Source(stream,1024,lambda:None,time.monotonic()+121);source.check();source.close()
    source=A.Source(stream,1024,lambda:None,time.monotonic()+600);source.check();source.close()
    for end in(time.monotonic()+601,time.monotonic()-1):
     with self.assertRaisesRegex(RuntimeError,'archive_deadline'):A.Source(stream,1024,lambda:None,end)
 def test_global1800_and_all_read_guards_remain_in_use(self):
  self.assertIn('self.end=time.monotonic()+1800',(HERE/'run_app_image_ci_smoke.py').read_text())
  self.assertIn('end=min(ctx.end,time.monotonic()+MAX_INVENTORY_SECONDS)',(HERE/'export_controller.py').read_text())
  tree=ast.parse((HERE/'archive_inventory.py').read_bytes());source=next(n for n in tree.body if isinstance(n,ast.ClassDef)and n.name=='Source');read=next(n for n in source.body if isinstance(n,ast.FunctionDef)and n.name=='read')
  checks=[n for n in ast.walk(read)if isinstance(n,ast.Call)and isinstance(n.func,ast.Attribute)and n.func.attr=='check'];self.assertGreaterEqual(len(checks),2)
 def test_elapsed_metadata_is_finite_bounded_and_stage_specific(self):
  with patch.object(C,'PHASE','fresh_same_image_whole_acl_inventory'),patch.object(C,'INVENTORY_STARTED',time.monotonic()-1):
   result=C.public_inventory_elapsed();self.assertGreaterEqual(result,1);self.assertLess(result,2)
  with patch.object(C,'PHASE','other'):self.assertIsNone(C.public_inventory_elapsed())
  with patch.object(C,'PHASE','fresh_same_image_whole_acl_inventory'),patch.object(C,'INVENTORY_STARTED',time.monotonic()-4000):self.assertIsNone(C.public_inventory_elapsed())
if __name__=='__main__':unittest.main()
