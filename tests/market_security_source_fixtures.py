"""Explicit owned negative policy fixtures; no release/admission authority.

Mutable production approval state is never inferred from a fixture. Actual
published stage checks invoke the unchanged authentic source validators.
"""
from contextlib import contextmanager, ExitStack
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch
import json

NULL_COREUTILS_FIELDS=('artifact','outputs','source','runtime_payload','installed_callers')
def encoded(value):return(json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode()
def binding(raw):return{'bytes':len(raw),'sha256':sha256(raw).hexdigest()}
def owned_glibc_inputs(M):
 """Explicit inert UNIT payloads; never DEBs, helpers or qualification."""
 values={name:('OWNED UNIT MOCK ONLY; NOT PACKAGE OR QUALIFIED RUNTIME:'+name+'\n').encode()
         for name in M.GLIBC_FILES}
 values['Root-TrixieSelected01-qualification.json']=encoded({'owned_unit_fixture_only':True,'security_accepted':False})
 return values
def owned_glibc_runtime(test,M,root):
 """Private unittest M only; real file/FD/parcel guards, no runtime claim.

 The real unpatched module refuses these bytes. Patches are registered with
 the test cleanup and never modify Source files, global modules or approvals.
 """
 if not M.__name__.startswith(('owned_','_owned_')):
  raise AssertionError('owned_private_unit_module_required')
 values=owned_glibc_inputs(M);files={name:binding(values[name])for name in M.GLIBC_FILES}
 stack=ExitStack();test.addCleanup(stack.close)
 stack.enter_context(patch.object(M,'GLIBC_FILES',files))
 stack.enter_context(patch.object(M,'GLIBC_ROOT_PIN',binding(values['Root-TrixieSelected01-qualification.json'])))
 stack.enter_context(patch.object(M,'GLIBC_NAMED',{'schema':'glibc84243_exact_runtime_named_context/1','files':files,'security_accepted':False}))
 capsule=root/'glibc-capsule';capsule.mkdir(mode=0o755)
 for name in files:M.save(capsule/name,values[name],mode=0o644)
 originals=M.Inputs([(capsule/name,pin,False)for name,pin in sorted(files.items())]);cap=None
 try:
  M.restamp(capsule,files,1793491200,source_rows=originals.files)
  cap=M.RuntimeCapsule(capsule,inputs=originals);return cap.record()
 finally:
  if cap is not None:cap.close()
  else:originals.close()
def unarmed_lock(template):
 value=deepcopy(json.loads(template)if type(template)is bytes else template)
 value['armed']=False
 for key in NULL_COREUTILS_FIELDS:value[key]=None
 return value
def unapproved_assertion(template):
 value=deepcopy(json.loads(template)if type(template)is bytes else template)
 value.update(approved=False,reviewer='OWNED_NEGATIVE_NOT_ROOT_APPROVAL',reviewed_at=None)
 return encoded(value)
@contextmanager
def owned_unapproved_preflight_sources(M,original):
 """Only an owned negative DTO and its scoped test-source pin are substituted.

 The real preflight and assertion validator bodies run. No runtime, UID,
 version, identity, image, successful approval or artifact is fabricated.
 """
 raws=dict(original);key=M.BUSINESS_DIR+'/python31118-root-assertion-approval.json';raws[key]=unapproved_assertion(raws[key]);actual=M.module
 def module(raw,path):
  result=actual(raw,path)
  if str(path)==M.OUTER:result.APPROVAL_PIN=binding(raws[key])
  return result
 with patch.object(M,'module',side_effect=module):yield raws

def assert_actual_coreutils_stage(test,H):
 """No skip: pending is rejected; an armed record must pass the full validator."""
 value=H.document((H.ROOT/H.LOCK_NAME).read_bytes());test.assertIs(type(value.get('armed')),bool)
 if value['armed']is False:
  for key in NULL_COREUTILS_FIELDS:test.assertIsNone(value[key])
  with H.Held()as held,test.assertRaisesRegex(RuntimeError,'^lock_unarmed$'):H.load_lock(held)
  return'PENDING_UNARMED_NOT_ADMITTED'
 with H.Held()as held:observed=H.load_lock(held)
 test.assertEqual(observed,value);return'ARMED_STATIC_VALIDATOR_PASSED_NOT_ACTIVATED'
def historical_gold1_coreutils_helper(root):
 """Exact prior PUBLIC source for historical delta checks, never execution.

 Gold2 adds its own admission contract. Historical one-line-delta assertions
 continue to authenticate their original gold1 source rather than the successor.
 """
 raw=(root/'tests/fixtures/coreutils-gold1-cache-helper.original.py.txt').read_bytes()
 if binding(raw)!={'bytes':37882,'sha256':'b9aea27a7400837c85ebc3da8c71ac6833662af86b8c8d8f5eef94182fc42e48'}:raise AssertionError('historical gold1 source pin')
 return raw
def assert_actual_assertion_stage(test,M,raws):
 """The actual current pin-bound preflight always runs, at its declared stage."""
 key=M.BUSINESS_DIR+'/python31118-root-assertion-approval.json';value=json.loads(raws[key]);test.assertIs(type(value.get('approved')),bool)
 if value['approved']is False:
  with test.assertRaisesRegex(RuntimeError,'^business81_assertion_approval$'):M.preflight_assertion_sources(raws)
  return'PENDING_UNAPPROVED_NOT_ADMITTED'
 M.preflight_assertion_sources(raws);return'ROOT_APPROVED_SOURCE_VALIDATOR_PASSED_NOT_ACTIVATED'
