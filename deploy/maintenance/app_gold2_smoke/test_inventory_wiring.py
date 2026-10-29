"""Owned source20 orchestration regressions; no image/Docker/hosted evidence."""
from pathlib import Path
from types import ModuleType,SimpleNamespace
from hashlib import sha256
from unittest.mock import patch
import ast,copy,errno,json,os,sys,tempfile,time,unittest
HERE=Path(__file__).absolute().parent
sys.path.insert(0,str(HERE))
import test_window as F
def load(name):
 p=HERE/name;m=ModuleType('owned20_'+p.stem);m.__file__=str(p);exec(compile(p.read_bytes(),str(p),'exec'),m.__dict__);return m
Q=load('controller.py');S=load('seal_and_run.py');E=load('export_controller_gold2.py')
def dependency(name):
 p=HERE/'dependencies'/name
 return p if p.exists()else HERE.parents[2]/S.SOURCE_PATHS[name]
INTAKE=json.loads((HERE/'intake.json').read_bytes())

class Wiring(unittest.TestCase):
 def setUp(self):
  self.directory=tempfile.TemporaryDirectory(prefix='owned20-wiring-',dir=HERE);self.root=Path(self.directory.name)
  (self.root/'reports').mkdir();self.path=self.root/'archive.tar';self.raw=F.archive(self.path)
  self.A=load('archive_inventory_gold2.py');self.control={**INTAKE['control'],'sbom_sha256':sha256(b'{"components":[]}').hexdigest()}
  self.sbom=b'{"components":[]}';self.records={};self.checks=[];self.closed_streams=[]
  self.ctx=SimpleNamespace(root=self.root,T=object(),modules={'archive_inventory_gold2.py':self.A,'export_controller_gold2.py':E},checks=[])
  self.ctx.check=lambda:self.checks.append(1)
  self.ctx.write=lambda path,value:self.records.__setitem__(path.name,copy.deepcopy(value))
  self.original_failure_codes=SimpleNamespace(FAILURE_CODES=())
 def tearDown(self):
  source=self.A.LAST_ATTEMPT_SOURCE
  if source is not None and source.duplicate is not None:
   self.assertTrue(source.duplicate_closed);self.assertFalse(source.held)
   with self.assertRaises(OSError)as caught:os.fstat(source.duplicate)
   self.assertEqual(caught.exception.errno,errno.EBADF)
  self.assertTrue(all(self.closed_streams));self.directory.cleanup();self.assertFalse(self.root.exists())
 def exporter(self,*,bad=False,after=None,extra=None):
  def run(ctx,old,image,control,sbom,*,I,T,A,adapter_sha256):
   self.assertEqual(adapter_sha256,Q.GOLD2_INVENTORY_PIN[1]);self.assertIs(T,ctx.T)
   raw=self.raw
   if bad:
    with self.path.open('r+b')as writer:writer.write(b'BAD')
    raw=self.path.read_bytes()
   pin=sha256(raw).hexdigest();stream=self.path.open('rb');fd=stream.fileno()
   try:
    receipt={'schema':'same_image_never_started_export/1','container_id':'3'*64,'image_id':image,'source_commit':control['source_commit'],
     'before':{},'after':{},'archive':{'bytes':len(raw),'sha256':pin,'full9':list(self.A.identity(os.fstat(fd)))},
     'actual_export_client_returncode':0,'container_started':False,'private_mounts_used':False,'security_accepted':False}
    try:
     result=A.collect(I,T,stream,len(raw),image_id=image,source_commit=control['source_commit'],sbom_raw=sbom,
      sbom_sha256=control['sbom_sha256'],verifier_sha256=adapter_sha256,export_sha256=pin,
      container_receipt=receipt,check=ctx.check,end=time.monotonic()+20)
    except BaseException as error:
     if extra is not None:raise E.ExportIOFailure([error,extra])from error
     raise
    if after is not None:after(result)
    return result
   finally:
    stream.close()
    try:os.fstat(fd)
    except OSError as error:self.closed_streams.append(error.errno==errno.EBADF)
    else:self.closed_streams.append(False)
  return SimpleNamespace(whole_image_inventory=run,MAX_EXPORT=E.MAX_EXPORT)
 def call(self,exporter):
  return Q.inventory_attempt(self.ctx,self.original_failure_codes,exporter,self.A,F.I,self.control['image_id'],self.control,self.sbom)
 def artifact(self):return self.records['archive-inventory-attempt.json']
 def test_success_uses_actual_A02_current_operands_and_closed_duplicate(self):
  result=self.call(self.exporter());record=self.artifact();self.assertEqual(record['ordinal_before'],0);self.assertEqual(record['ordinal_after'],1)
  self.assertEqual(record['collect_forward_calls'],1);self.assertTrue(record['original_collect_entered']);self.assertFalse(record['previous_observation_reused'])
  self.assertEqual(record['bound_current_operands'],record['current_observation']['attempt']);self.assertEqual(record['bound_current_operands']['export_sha256'],result['export_sha256'])
  self.assertEqual(result['verifier_sha256'],Q.GOLD2_INVENTORY_PIN[1]);self.assertTrue(record['current_observation']['duplicate_physically_closed'])
  self.assertIsNone(record['collection_failure']);self.assertEqual(record['call_failures'],[]);self.assertGreater(len(self.checks),4)
 def test_failed_actual_collect_publishes_current_cost_and_nested_causes(self):
  with self.assertRaises(Q.InventoryAttemptFailure)as caught:self.call(self.exporter(bad=True,extra=OSError(errno.EIO,'OWNED_EXTRA')))
  error=caught.exception;self.assertTrue(error.artifact_written);record=self.artifact();self.assertEqual(record['ordinal_after'],1)
  self.assertEqual(record['current_observation']['attempt'],record['bound_current_operands']);self.assertIsNotNone(record['collection_failure'])
  self.assertIn('tar_checksum',[row['code']for row in record['collection_failure']['causes']])
  rows=Q.inventory_causes(error,self.original_failure_codes,self.ctx);self.assertEqual(rows[0]['code'],'tar_checksum');self.assertEqual(rows[-1]['os_errno'],errno.EIO)
 def test_precollect_failure_never_borrows_stale_success(self):
  self.A.ATTEMPT_SEQUENCE=4;stale={'attempt':{'ordinal':4},'phase':'old','security_accepted':False};self.A.LAST_OBSERVATION=stale
  body=RuntimeError('flat_export_timeout')
  def run(*args,**kwargs):raise body
  with self.assertRaises(Q.InventoryAttemptFailure)as caught:self.call(SimpleNamespace(MAX_EXPORT=E.MAX_EXPORT,whole_image_inventory=run))
  self.assertIs(caught.exception.errors[0],body);record=self.artifact();self.assertEqual(record['ordinal_before'],4);self.assertEqual(record['ordinal_after'],4)
  self.assertEqual(record['collect_forward_calls'],0);self.assertIsNone(record['current_observation']);self.assertIsNone(record['bound_current_operands']);self.assertIs(self.A.LAST_OBSERVATION,stale)
 def test_mismatched_current_observation_refuses_after_success(self):
  def corrupt(value):self.A.LAST_OBSERVATION={**self.A.LAST_OBSERVATION,'attempt':{**self.A.LAST_OBSERVATION['attempt'],'export_sha256':'9'*64}}
  with self.assertRaises(Q.InventoryAttemptFailure)as caught:self.call(self.exporter(after=corrupt))
  self.assertTrue(caught.exception.artifact_written);record=self.artifact();self.assertIsNone(record['current_observation'])
  self.assertIn('inventory_observation_identity',[r['code']for r in record['call_failures']])
 def test_second_collect_refuses_synchronously_without_second_A_attempt(self):
  original=self.exporter().whole_image_inventory
  def run(*args,**kwargs):original(*args,**kwargs);return original(*args,**kwargs)
  with self.assertRaises(Q.InventoryAttemptFailure):self.call(SimpleNamespace(MAX_EXPORT=E.MAX_EXPORT,whole_image_inventory=run))
  record=self.artifact();self.assertEqual(record['collect_forward_calls'],2);self.assertEqual(record['ordinal_after'],1)
  self.assertIn('inventory_attempt_identity',[r['code']for r in record['call_failures']])
 def test_failed_publication_preserves_body_and_publication_objects(self):
  body=RuntimeError('flat_export_timeout');publication=OSError(errno.EACCES,'OWNED_PRIVATE_TEXT')
  def run(*args,**kwargs):raise body
  def write(*args):raise publication
  self.ctx.write=write
  with self.assertRaises(Q.InventoryAttemptFailure)as caught:self.call(SimpleNamespace(MAX_EXPORT=E.MAX_EXPORT,whole_image_inventory=run))
  self.assertEqual(caught.exception.errors,(body,publication));self.assertFalse(caught.exception.artifact_written)
  rows=Q.inventory_causes(caught.exception,self.original_failure_codes,self.ctx);self.assertEqual(rows[0]['code'],'flat_export_timeout');self.assertEqual(rows[1]['os_errno'],errno.EACCES)
  self.assertNotIn('OWNED_PRIVATE_TEXT',Q.encoded(caught.exception.record).decode())
 def test_post_success_publication_failure_is_not_silently_discarded(self):
  publication=OSError(errno.ENOSPC,'OWNED_FULL');self.ctx.write=lambda *args:(_ for _ in()).throw(publication)
  with self.assertRaises(Q.InventoryAttemptFailure)as caught:self.call(self.exporter())
  self.assertIs(caught.exception.errors[0],publication);self.assertFalse(caught.exception.artifact_written)
  self.assertEqual(caught.exception.record['call_failures'],[]);self.assertIsNotNone(caught.exception.record['current_observation'])
 def test_success_without_original_collect_refuses_even_with_stale_observation(self):
  self.A.LAST_OBSERVATION={'attempt':{'ordinal':0}}
  def run(*args,**kwargs):return {'schema':'OWNED_FAKE_SUCCESS'}
  with self.assertRaises(Q.InventoryAttemptFailure)as caught:self.call(SimpleNamespace(MAX_EXPORT=E.MAX_EXPORT,whole_image_inventory=run))
  self.assertIn('inventory_observation_identity',[r['code']for r in caught.exception.record['call_failures']]);self.assertIsNone(caught.exception.record['current_observation'])
 def test_actual_publication_postguard_failure_distinguishes_written_from_complete(self):
  write=self.ctx.write;post=RuntimeError('inner_clock');published=[]
  def save(path,value):write(path,value);published.append(1)
  def check():
   if published:raise post
  self.ctx.write=save;self.ctx.check=check
  with self.assertRaises(Q.InventoryAttemptFailure)as caught:self.call(self.exporter())
  self.assertTrue(caught.exception.artifact_written);self.assertFalse(caught.exception.artifact_publication_complete)
  self.assertIs(caught.exception.errors[-1],post);self.assertIn('archive-inventory-attempt.json',self.records)
 def test_wrong_verifier_operand_refuses_before_original_collect(self):
  # Malformed protocol-only E -> A witness; the actual Q bridge checks it
  # before the original A collector acquires any owned prefix.
  def malformed(ctx,old,image,control,sbom,*,I,T,A,adapter_sha256):
   with self.path.open('rb')as stream:return A.collect(I,T,stream,len(self.raw),image_id=image,source_commit=control['source_commit'],sbom_sha256=control['sbom_sha256'],verifier_sha256='9'*64,export_sha256=sha256(self.raw).hexdigest())
  with self.assertRaises(Q.InventoryAttemptFailure)as caught:self.call(SimpleNamespace(MAX_EXPORT=E.MAX_EXPORT,whole_image_inventory=malformed))
  record=caught.exception.record;self.assertEqual(self.A.ATTEMPT_SEQUENCE,0);self.assertFalse(record['original_collect_entered']);self.assertIsNone(record['bound_current_operands'])
  self.assertIn('inventory_attempt_identity',[r['code']for r in record['call_failures']])
 def test_original_body_and_postguard_error_order_remains_explicit(self):
  body=RuntimeError('flat_export_timeout');post=RuntimeError('inner_clock');failed=[]
  def run(*args,**kwargs):failed.append(1);raise body
  def check():
   if failed:raise post
  self.ctx.check=check
  with self.assertRaises(Q.InventoryAttemptFailure)as caught:self.call(SimpleNamespace(MAX_EXPORT=E.MAX_EXPORT,whole_image_inventory=run))
  self.assertIs(caught.exception.errors[0].errors[0],body);self.assertIs(caught.exception.errors[0].errors[1],post)
  rows=Q.inventory_causes(caught.exception,self.original_failure_codes,self.ctx);self.assertEqual(rows[0]['code'],'flat_export_timeout');self.assertEqual(rows[1]['code'],'inner_clock')
  self.assertFalse(caught.exception.artifact_written)
 def test_closed_A_unknown_failure_never_outputs_unknown_operands(self):
  unknown=RuntimeError('OWNED_SECRET_LIKE_OPERAND');report=self.A.public_failure(self.A.ArchiveIOFailure([unknown,RuntimeError('archive_deadline')]))
  self.assertEqual([r['code']for r in report['causes']],['archive_unproven','archive_deadline']);self.assertNotIn('OWNED_SECRET',Q.encoded(report).decode())
 def test_actual_main_retains_body_publication_cleanup_and_inner_finalizer_priority(self):
  body=RuntimeError('flat_export_timeout');publication=OSError(errno.ENOSPC,'OWNED_FULL');cleanup=RuntimeError('owned_cleanup_unproven')
  final=RuntimeError('bootstrap_close_unknown');original_write=self.ctx.write
  def write(path,value):
   if path.name=='archive-inventory-attempt.json':raise publication
   original_write(path,value)
  self.ctx.write=write;self.ctx.C=SimpleNamespace(PROOF_KEEPER=None)
  def constructor(root,sources):sources.callback(lambda:(_ for _ in()).throw(final));return self.ctx
  old=SimpleNamespace(Controller=constructor,FAILURE_CODES=(),cleanup=lambda ctx:(_ for _ in()).throw(cleanup))
  def source(old,root,stack,checks,name,pin):
   p=HERE/name
   if not p.exists():p=dependency(name)
   return p.read_bytes()
  def execute(ctx,old,*args):
   def run(*args,**kwargs):raise body
   return self.call(SimpleNamespace(MAX_EXPORT=E.MAX_EXPORT,whole_image_inventory=run))
  keeper=SimpleNamespace(root=self.root,closed=False,check=lambda:None)
  with patch.object(Q,'hosted',return_value=None),patch.object(Q,'bootstrap_module',return_value=(old,lambda:None)),patch.object(Q,'source',side_effect=source),patch.object(Q,'execute',side_effect=execute):
   code=Q.main(['--root',str(self.root),'--execute-reviewed-gold2-proof','--confirm',Q.CONFIRM],outer_keeper=keeper)
  self.assertEqual(code,1);record=Q.LAST_FAILURE_RECORD;self.assertEqual(record['code'],'bootstrap_close_unknown')
  self.assertEqual(record['primary_failure']['code'],'flat_export_timeout');self.assertEqual(record['primary_failure_causes'][1]['os_errno'],errno.ENOSPC)
  self.assertEqual(record['cleanup_causes'],['owned_cleanup_unproven']);self.assertTrue(record['inner_finalizer_failures'])
  self.assertFalse(record['inventory_attempt']['artifact_written']);self.assertIn('failure.json',self.records)
 def test_source20_has_exact_new_binding_and_original_E_and_15(self):
  self.assertEqual(len(S.SOURCES),20);self.assertEqual(Q.GOLD2_INVENTORY_PIN,(33083,'04a58e38792d9c5e11ac3ee37db593b6a624b059f0378d0ff2bf6aa560dff005'))
  self.assertEqual(S.SOURCES['archive_inventory_gold2.py'],Q.GOLD2_INVENTORY_PIN);self.assertEqual(S.SOURCE_PATHS['archive_inventory_gold2.py'],'deploy/maintenance/app_gold2_smoke/archive_inventory_gold2.py')
  self.assertEqual((len((HERE/'export_controller_gold2.py').read_bytes()),sha256((HERE/'export_controller_gold2.py').read_bytes()).hexdigest()),(27863,'5480efd9fb0a5443e19262323821f917c57e2168f4aec6151bc44c27ecae95ad'))
  self.assertEqual(len(Q.PINS),15);self.assertEqual(E.MAX_INVENTORY_SECONDS,600)
  for n,pin in Q.PINS.items():raw=dependency(n).read_bytes();self.assertEqual((len(raw),sha256(raw).hexdigest()),pin)

if __name__=='__main__':unittest.main(verbosity=2)
