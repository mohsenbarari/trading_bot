"""Owned source/protocol QA; no real CI/image/profile execution or admission.

External factual collection is an explicitly inert fixture in wrapper tests.
Real owned files exercise strict raw reports and original late-close refusal.
"""
from contextlib import redirect_stdout
from hashlib import sha256
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
import ast,io,json,os,tempfile,unittest

HERE=Path(__file__).resolve().parent;ROOT=HERE.parent
PATH=ROOT/'scripts/prepare_market_capture2_image.py'
M=ModuleType('owned_complete_evidence');M.__file__=str(PATH)
exec(compile(PATH.read_bytes(),str(PATH),'exec'),M.__dict__)

class CompleteEvidenceTests(unittest.TestCase):
 def owned(self):
  t=tempfile.TemporaryDirectory(prefix='.owned-complete-evidence-',dir=HERE)
  self.addCleanup(t.cleanup);return Path(t.name)

 def observed(self,high=0):
  return {'schema':'normal_market_ci_observed_evidence/1','status':'existing_evidence_observed_not_security_accepted',
   'raw_counts':{'HIGH':high,'CRITICAL':0},'raw_gate_passed':high==0,
   'owned_cleanup_evidence_complete':False,'physical_image_archive_admission_complete':False,
   'same_image_security_assessment_complete':False,'activation_authority':False,'security_accepted':False}

 def test_reviewed_armed_source_still_requires_execute_and_real_arguments(self):
  self.assertIs(M.EXECUTION_ARMED,True)
  with redirect_stdout(io.StringIO())as out:self.assertEqual(M.main(['--phase','complete-evidence']),0)
  value=json.loads(out.getvalue());self.assertEqual(value['status'],'UNARMED');self.assertIs(value['execution_armed'],True)
  for name in('build_performed','activation_authority','security_accepted'):self.assertIs(value[name],False)
  with self.assertRaisesRegex(RuntimeError,'^arguments$'):M.main(['--execute','--phase','complete-evidence'])

 def test_real_armed_source_does_not_waive_actual_host_and_workspace_guards(self):
  root=self.owned();self.assertIs(M.EXECUTION_ARMED,True)
  # No source flag, UID, environment, command or runtime validator is patched.
  # An owned fixture directory cannot be the genuine hosted workspace.
  with self.assertRaisesRegex(RuntimeError,'^(hosted_main_only|root_scope)$'):M.main(['--execute','--phase','raw-gate','--root',str(root),'--repo',str(root)])
  self.assertEqual(list(root.iterdir()),[])

 def test_workflow_retains_exact_strict_gates_and_uploads_factual_evidence_after_failure(self):
  raw=(ROOT/'.github/workflows/market-capture2-image.yml').read_bytes();marker=b'\n      - name: Preserve factual completion evidence without acceptance\n';self.assertEqual(raw.count(marker),1);original,appended=raw.split(marker)
  self.assertEqual((len(original),sha256(original).hexdigest()),(18370,'4d948e1f4e788ab31ecc73fdfbdbab71172cbbc4011ed11f2f90e7d228aed2e9'))
  text=raw.decode();upload=appended.decode();self.assertIn("if: always() && steps.finalize.outcome == 'success'",upload);self.assertNotIn('steps.vulnerabilities.outcome',upload);self.assertNotIn('continue-on-error',upload)
  self.assertIn('uses: actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9',upload);self.assertIn('if-no-files-found: error',upload);self.assertIn('factual-completion-unaccepted-',upload);self.assertEqual([line.strip()for line in upload.splitlines()if line.strip().startswith('path:')],['path: ${{ steps.build.outputs.root }}/ci-evidence.json'])
  self.assertLess(text.index("'--phase','raw-gate'"),text.index("'--phase','complete-gate'"));self.assertLess(text.index("'--phase','complete-gate'"),text.index(marker.decode()));self.assertNotIn("'--phase','complete-evidence'",text)
  # The unchanged complete gate generates factual evidence before its strict
  # count refusal. A separate phase would collide with exclusive publication.
  tree=ast.parse(PATH.read_bytes());gate=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='complete_gate');body=ast.get_source_segment(PATH.read_text(),gate)
  self.assertLess(body.index('observed=complete_evidence('),body.index("'raw_high_critical_findings'"));self.assertIn("root/'ci-completed.json'",body)

 def test_original_strict_raw_gate_body_is_byte_protocol_unchanged(self):
  f=next(n for n in ast.parse(PATH.read_bytes()).body if isinstance(n,ast.FunctionDef)and n.name=='raw_gate')
  self.assertEqual(sha256(ast.dump(f).encode()).hexdigest(),'fe4d54b4a825a405e8967706fc1d78e0050b6479b49589eb5a72390dbff98181')

 def test_legacy_complete_gate_rejects_raw44_after_factual_observation(self):
  root=self.owned()
  with patch.object(M,'complete_evidence',return_value=self.observed(44)),patch.object(M,'publish_known',side_effect=AssertionError('legacy success forbidden')):
   with self.assertRaisesRegex(RuntimeError,'raw_high_critical_findings'):M.complete_gate(root,root)
  self.assertFalse((root/'ci-completed.json').exists())

 def test_legacy_zero_result_is_created_only_after_original_close(self):
  root=self.owned();source=root/'owned-original';source.write_bytes(b'OWNED ORIGINAL');source.chmod(0o600)
  rows=[]
  def inert(root,repo,*,retained,final_checks,role):
   row=M.Held(source,maximum=128);rows.append(row);retained.append(row);return self.observed()
  with patch.object(M,'complete_evidence',side_effect=inert):value=M.complete_gate(root,root)
  self.assertIsNone(rows[0].fd);rows[0].named()
  self.assertEqual(json.loads((root/'ci-completed.json').read_bytes()),value)
  self.assertEqual(value['schema'],'normal_market_ci_complete_evidence/1')
  self.assertFalse(value['security_accepted']);self.assertFalse(value['physical_image_archive_admission_complete'])
  self.assertFalse(value['owned_cleanup_evidence_complete'])

 def test_late_actual_original_close_mutation_prevents_legacy_publication(self):
  root=self.owned();source=root/'owned-original';source.write_bytes(b'OWNED ORIGINAL');source.chmod(0o600)
  chosen=[];physical=os.close
  def inert(root,repo,*,retained,final_checks,role):
   row=M.Held(source,maximum=128);chosen.append(row.fd);retained.append(row);return self.observed()
  def close(fd):
   selected=fd in chosen;physical(fd)
   if selected:source.chmod(0o400)
  with patch.object(M,'complete_evidence',side_effect=inert),patch.object(M.os,'close',side_effect=close):
   with self.assertRaisesRegex(RuntimeError,'source_changed'):M.complete_gate(root,root)
  self.assertFalse((root/'ci-completed.json').exists())

 def test_real_raw44_reports_keep_original_findings_and_strict_failure(self):
  root=self.owned();(root/'reports').mkdir();image='sha256:'+'a'*64
  values={'image-build.json':{'image_id':image,'source_inputs':{}},
   'reports/vulnerabilities.json':{'Metadata':{'ImageID':image},'Results':[{'Vulnerabilities':[{'Severity':'HIGH'}for _ in range(44)]}]},
   'reports/sbom.cdx.json':{'metadata':{'component':{'type':'container','name':image,'properties':[{'name':'aquasecurity:trivy:ImageID','value':image}]}}}}
  originals={}
  for name,value in values.items():
   raw=M.encoded(value);path=root/name;path.write_bytes(raw);path.chmod(0o600);originals[name]=raw
  with patch.object(M,'hosted'),patch.object(M,'require_selected'):
   with self.assertRaisesRegex(RuntimeError,'raw_high_critical_findings'):M.raw_gate(root,root)
  self.assertTrue(all((root/name).read_bytes()==raw for name,raw in originals.items()))

 def test_factual_extraction_retains_existing_validation_and_explicit_limits(self):
  tree=ast.parse(PATH.read_bytes());f=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='complete_evidence')
  calls={n.func.id if isinstance(n.func,ast.Name)else n.func.attr for n in ast.walk(f)if isinstance(n,ast.Call)and isinstance(n.func,(ast.Name,ast.Attribute))}
  self.assertTrue({'business81_outputs','join_actual_results','receipt_contract','verify_controller_parcel','ContextSnapshot','live_main','raw_reports','finish_or_transfer','publish_known'}<=calls)
  strings={n.value for n in ast.walk(f)if isinstance(n,ast.Constant)and type(n.value)is str}
  self.assertNotIn('raw_high_critical_findings',strings)
  self.assertIn('ci-evidence.json',strings);self.assertNotIn('ci-completed.json',strings)
  literal=next(n.value for n in ast.walk(f)if isinstance(n,ast.Assign)and any(isinstance(t,ast.Name)and t.id=='value'for t in n.targets)and isinstance(n.value,ast.Dict))
  fields={k.value:v for k,v in zip(literal.keys,literal.values)if isinstance(k,ast.Constant)}
  for name in('owned_cleanup_evidence_complete','physical_image_archive_admission_complete','same_image_security_assessment_complete','activation_authority','security_accepted'):
   self.assertIs(fields[name].value,False)

if __name__=='__main__':unittest.main()
