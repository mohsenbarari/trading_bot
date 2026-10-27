"""Pure/owned public observation contracts, never an actual CI/image proof."""
from pathlib import Path
from types import ModuleType
from hashlib import sha256,sha1
from io import BytesIO
from unittest.mock import patch
import ast,json,os,tarfile,tempfile,time,unittest
HERE=Path(__file__).absolute().parent
def load(name):
 p=HERE/name;m=ModuleType(name);m.__file__=str(p);exec(compile(p.read_bytes(),str(p),'exec'),m.__dict__);return m
Q=load('controller.py');C=load('producer02_controller.py');S=load('seal_and_run.py');B=load('compose_security_os_candidate.py');A=load('archive_inventory.py');P=load('public_policy_facts.py')
def archive(path,*,policy=b'path-exclude=/usr/share/doc/*\n',example=None,alias=False):
 entries={}
 for target in P.TARGETS:
  for parent in reversed(Path(target).parents):
   if str(parent)!='/':entries.setdefault(str(parent).lstrip('/'),None)
 entries[P.TARGETS[0].lstrip('/')]=policy
 for target in P.TARGETS[2:]:entries[target.lstrip('/')]=b'OWNED_PUBLIC_DOC_FIXTURE'
 if example is not None:entries[P.TARGETS[1].lstrip('/')]=example
 with tarfile.open(path,mode='w',format=tarfile.USTAR_FORMAT)as tar:
  for name,value in entries.items():
   row=tarfile.TarInfo(name);row.mode=0o644;row.uid=row.gid=0
   if value is None:row.type=tarfile.DIRTYPE;tar.addfile(row)
   elif alias and name==P.TARGETS[0].lstrip('/'):row.type=tarfile.SYMTYPE;row.linkname='/owned/unknown';tar.addfile(row)
   else:row.size=len(value);tar.addfile(row,BytesIO(value))
 path.chmod(0o600)
def collect(path):
 raw=path.read_bytes()
 with path.open('rb')as stream:return P.collect(A,stream,len(raw),image_id='OWNED_PROTOCOL_ONLY_NO_IMAGE',source_commit='OWNED_PROTOCOL_ONLY_NO_GIT',export_sha256=sha256(raw).hexdigest(),check=lambda:None,end=time.monotonic()+10)
class Checks(unittest.TestCase):
 def test_closed_fixed_R2_control_and_original_P_remain_distinct(self):
  Q.validate(C);self.assertFalse(C.ARMED);self.assertIsNone(C.CONTROL);self.assertEqual(Q.CONTROL['run_id'],40550906521);self.assertEqual(Q.CONTROL['raw_counts']['HIGH'],44)
  self.assertEqual(Q.CONTROL['archive_observation']['archive_bytes'],476675072);self.assertEqual(Q.CONTROL['archive_observation']['member_count'],78)
 def test_exact_runtime_source_hash_chain(self):
  for name,pin in S.SOURCES.items():
   raw=(HERE/name).read_bytes();self.assertEqual((len(raw),sha256(raw).hexdigest()),pin,name)
  for name,pin in(('producer02_controller.py',Q.PRODUCER_PIN),('export_controller.py',Q.EXPORT_PIN),('archive_inventory.py',Q.ARCHIVE_PIN),('inventory.py',Q.INVENTORY_PIN),('public_policy_facts.py',Q.POLICY_PIN)):
   self.assertEqual(S.SOURCES[name],pin)
 def test_actual_R2_Git_supplement_blobs_and_original_raw_sources(self):
  raw=(HERE/'image-controller-sources.json').read_bytes();self.assertEqual((len(raw),sha256(raw).hexdigest()),Q.GIT_DOCUMENT_PIN);v=json.loads(raw)
  self.assertEqual(v['source_commit'],Q.CONTROL['source_commit']);self.assertEqual(v['source_tree'],Q.CONTROL['source_tree'])
  for row in v['files']:
   source=(HERE/('original-R-workflow.yml'if row['path'].startswith('.github/')else'prepare_canonical_app_image.py')).read_bytes()
   self.assertEqual((len(source),sha256(source).hexdigest()),(row['bytes'],row['sha256']));self.assertEqual(row['git_blob'],sha1(b'blob '+str(len(source)).encode()+b'\0'+source).hexdigest());self.assertEqual(row['git_mode'],'100644')
 def test_collector_call_scope_has_no_P32_native_caller_or_flags_acceptance(self):
  tree=ast.parse((HERE/'controller.py').read_bytes());fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='execute');calls={n.func.attr for n in ast.walk(fn)if isinstance(n,ast.Call)and isinstance(n.func,ast.Attribute)}
  self.assertIn('admit_app',calls);self.assertIn('whole_image_inventory',calls);self.assertFalse(calls&{'run_smoke','native_payload','presence','caller_audit'});self.assertIn('App_R2_observation_controller_completion/1',(HERE/'seal_and_run.py').read_text());self.assertNotIn("'runtime_assertions':32",(HERE/'seal_and_run.py').read_text())
 def test_outer_completion_calls_real_scope_API_after_physical_finalizers(self):
  self.assertEqual(Q.image_scope(Q.CONTROL),C.image_scope(Q.CONTROL))
  tree=ast.parse((HERE/'seal_and_run.py').read_bytes());fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='run')
  for n in ast.walk(fn):
   if isinstance(n,ast.Attribute)and isinstance(n.value,ast.Name)and n.value.id=='controller':self.assertTrue(hasattr(Q,n.attr),n.attr)
  source=ast.get_source_segment((HERE/'seal_and_run.py').read_text(),fn)
  self.assertLess(source.index('finalize(held,keeper)'),source.index("save(root/'reports/hosted-completion.json'"));self.assertLess(source.index("save(root/'reports/hosted-completion.json'"),source.index('keeper.named()'))
 def test_workflow_sealer_pin_and_failure_publication_contract(self):
  workflow=HERE.parents[2]/'.github/workflows/security_app_r2_inventory.yml';raw=workflow.read_text()
  self.assertIn('--source-sha256 '+sha256((HERE/'seal_and_run.py').read_bytes()).hexdigest(),raw)
  self.assertIn("if: always() && steps.run.outputs.root != ''",raw);self.assertIn('if-no-files-found: error',raw);self.assertNotIn('docker load',raw);self.assertIn('default: false',raw)
 def test_default_controller_performs_no_host_or_source_operation(self):
  with patch.object(Q,'hosted',side_effect=AssertionError('no host operation')),patch.object(Q,'bootstrap_module',side_effect=AssertionError('no source read')):self.assertEqual(Q.main(['--root','/owned/not-real-Root']),0)
 def test_owned_exact_target_policy_and_absence_are_facts_only(self):
  with tempfile.TemporaryDirectory(dir=HERE)as d:
   p=Path(d)/'OWNED_NOT_IMAGE.tar';archive(p);v=collect(p);self.assertEqual(len(v['observations']),5);self.assertEqual(v['observations'][0]['public_policy_text'],'path-exclude=/usr/share/doc/*\n');self.assertEqual(v['observations'][1]['status'],'ABSENT_FROM_AUTHENTICATED_FLAT_TAR');self.assertIsNone(v['policy_origin_layer']);self.assertFalse(v['distribution_filtered_absence_authority_created']);self.assertFalse(v['filesystem_extracted'])
 def test_present_example_is_reported_not_relabelled_absent(self):
  with tempfile.TemporaryDirectory(dir=HERE)as d:
   p=Path(d)/'OWNED_NOT_IMAGE.tar';archive(p,example=b'OWNED_PUBLIC_EXAMPLE');v=collect(p);self.assertEqual(v['observations'][1]['status'],'REGULAR_PUBLIC_FILE_OBSERVED');self.assertFalse(v['distribution_filtered_absence_authority_created'])
 def test_target_symlink_or_oversized_policy_refuses(self):
  with tempfile.TemporaryDirectory(dir=HERE)as d:
   for name,options in(('alias',{'alias':True}),('bound',{'policy':b'X'*(P.MAX_POLICY+1)})):
    path=Path(d)/(name+'.tar');archive(path,**options)
    with self.subTest(name=name),self.assertRaises(RuntimeError):collect(path)
 def test_outer_sealer_catches_report_mutation_during_source_physical_close(self):
  with tempfile.TemporaryDirectory(dir=HERE)as d:
   root=Path(d);root.chmod(0o700);source=root/'owned-public-source.py';source.write_bytes(b'VALUE=1\n');source.chmod(0o644);raw=source.read_bytes();held=[S.Held(source,(len(raw),sha256(raw).hexdigest()),owner=os.geteuid(),mode=0o644)];keeper=B.ProofKeeper(root);report=root/'owned-report.json';keeper.write(report,b'{"OWNED_PROTOCOL_ONLY":true}\n',0o600);fd=held[0].fd;close=os.close
   def mutate(actual):
    close(actual)
    if actual==fd:report.write_bytes(b'{"OWNED_PROTOCOL_ONLY":false}\n')
   with patch.object(S.os,'close',side_effect=mutate):
    with self.assertRaisesRegex(RuntimeError,'sealed_finalization'):S.finalize(held,keeper)
   self.assertIsNone(held[0].fd);self.assertTrue(keeper.closed)
 def test_real_owned_flat_file_mutation_during_parser_physical_close_refuses(self):
  with tempfile.TemporaryDirectory(dir=HERE)as d:
   p=Path(d)/'OWNED_NOT_IMAGE.tar';archive(p);raw=p.read_bytes();close=os.close
   with p.open('rb')as stream:
    source=A.Source(stream,len(raw),lambda:None,time.monotonic()+10);source.verify(sha256(raw).hexdigest());source.digest_pin=sha256(raw).hexdigest();fd=source.fd
    def mutate(actual):
     close(actual)
     if actual==fd:p.write_bytes(raw+b'X')
    with patch.object(A.os,'close',side_effect=mutate):
     with self.assertRaises(RuntimeError):source.close()
 def test_inner_source_callbacks_attempt_all_real_closes_and_keep_each_closed_cause(self):
  with tempfile.TemporaryDirectory(dir=HERE)as d:
   paths=[Path(d)/('owned-'+str(i))for i in range(2)]
   for path in paths:path.write_bytes(b'OWNED_PUBLIC')
   fds=[os.open(path,os.O_RDONLY)for path in paths];events=[];sources=Q.ObservationSources(lambda error:Q.closed_error(error,Q.CODES))
   def close(fd,index):
    os.close(fd);events.append(index);raise RuntimeError('OWNED_PRIVATE_DETAIL_MUST_NOT_EXPORT')
   for index,fd in enumerate(fds):sources.callback(close,fd,index)
   sources.close();self.assertEqual(events,[1,0]);self.assertEqual(len(sources.failures),2);self.assertTrue(sources.closed)
   self.assertNotIn('OWNED_PRIVATE_DETAIL',json.dumps(sources.failures))
   for fd in fds:
    with self.assertRaises(OSError):os.fstat(fd)
 def test_inner_source_context_really_closes_the_original_pinned_reader(self):
  with tempfile.TemporaryDirectory(dir=HERE)as d:
   path=Path(d)/'owned';path.write_bytes(b'OWNED_PUBLIC');path.chmod(0o600);sources=Q.ObservationSources(lambda error:Q.closed_error(error,Q.CODES))
   stream,size=sources.enter_context(B.pinned(path,64));self.assertEqual(stream.read(),b'OWNED_PUBLIC');self.assertEqual(size,12)
   sources.close();self.assertTrue(stream.closed);self.assertFalse(sources.failures)
 def test_inner_source_context_failure_is_kept_while_other_real_FD_closes(self):
  with tempfile.TemporaryDirectory(dir=HERE)as d:
   path=Path(d)/'owned';path.write_bytes(b'OWNED_PUBLIC');path.chmod(0o600);sources=Q.ObservationSources(lambda error:Q.closed_error(error,Q.CODES))
   fd=os.open(path,os.O_RDONLY);sources.callback(os.close,fd)
   stream,size=sources.enter_context(B.pinned(path,64));path.write_bytes(b'CHANGED_PUBLIC')
   sources.close();self.assertTrue(stream.closed);self.assertEqual(len(sources.failures),1)
   with self.assertRaises(OSError):os.fstat(fd)
 def test_original_primary_survives_real_confirm_refusal_then_unarmed_clears_it(self):
  with patch.object(Q,'hosted',side_effect=AssertionError('no hosted operation')):
   self.assertEqual(Q.main(['--root','/OWNED_NOT_OPENED','--execute-fixed-R2-observation','--confirm','WRONG']),1)
  self.assertEqual(Q.LAST_FAILURE_RECORD['primary_failure']['code'],'explicit_action');self.assertFalse(Q.LAST_FAILURE_RECORD['inner_sources_physically_closed'])
  self.assertEqual(Q.main(['--root','/OWNED_NOT_OPENED']),0);self.assertIsNone(Q.LAST_FAILURE_RECORD)
 def test_outer_collects_all_real_source_close_and_name_failures(self):
  with tempfile.TemporaryDirectory(dir=HERE)as d:
   root=Path(d);root.chmod(0o700);held=[]
   for i in range(2):
    path=root/('owned-'+str(i));path.write_bytes(b'OWNED_PUBLIC');path.chmod(0o644);data=path.read_bytes();held.append(S.Held(path,(len(data),sha256(data).hexdigest()),owner=os.geteuid(),mode=0o644))
   keeper=B.ProofKeeper(root);keeper.write(root/'owned-report',b'OWNED_PUBLIC',0o600)
   for item in held:item.path.write_bytes(b'CHANGED_PUBLIC')
   with self.assertRaises(S.FinalizationFailure)as result:S.finalize(held,keeper)
   self.assertEqual(len(result.exception.failures),4);self.assertEqual({row['stage']for row in result.exception.failures},{'outer_source_close','outer_source_named'});self.assertTrue(all(item.fd is None for item in held));self.assertTrue(keeper.closed)
 def test_outer_failure_projection_is_identity_bound_and_unarmed_resets_it(self):
  error=RuntimeError('controller_failed');other=RuntimeError('controller_failed');record={'OWNED_PROTOCOL_ONLY':'never_actual_completion'}
  S.LAST_FAILURE=(error,record);self.assertIs(S.closed_failure(error),record);self.assertIsNot(S.closed_failure(other),record);self.assertFalse(S.closed_failure(other)['outer_physical_finalizers_completed'])
  self.assertEqual(S.main([]),0);self.assertIsNone(S.LAST_FAILURE)
 def test_closed_errors_never_export_exception_operands(self):
  error=FileNotFoundError(2,'OWNED_PRIVATE_DETAIL','OWNED_PRIVATE_PATH')
  self.assertNotIn('OWNED_PRIVATE',json.dumps(Q.closed_error(error,Q.CODES)));self.assertNotIn('OWNED_PRIVATE',json.dumps(S.closed_error(error)))
 def test_exact_source_API_closure_and_bounded_observation_closer(self):
  old=load('run_app_image_ci_smoke.py')
  for name,(origin,digest)in old.HELPERS.items():
   self.assertEqual(S.SOURCES[name][1],digest);self.assertEqual(Path(origin).name,name);self.assertEqual(S.SOURCES[name][0],len((HERE/name).read_bytes()))
  tree=ast.parse((HERE/'controller.py').read_bytes());main=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='main');text=ast.get_source_segment((HERE/'controller.py').read_text(),main)
  self.assertLess(text.index('old.cleanup(ctx)'),text.index('sources.close()'));self.assertLess(text.index('sources.close()'),text.index("'OBSERVED_NOT_ACCEPTED'"));self.assertIn('inner_finalizer_failures',text)
  sources=Q.ObservationSources(lambda error:Q.closed_error(error,Q.CODES))
  for _ in range(32):sources.callback(lambda:None)
  with self.assertRaisesRegex(RuntimeError,'inner_source_callback_bound'):sources.callback(lambda:None)
  sources.close();self.assertFalse(sources.failures)
if __name__=='__main__':unittest.main(verbosity=2)
