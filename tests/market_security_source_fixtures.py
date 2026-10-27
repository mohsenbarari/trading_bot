"""Explicit owned negative policy fixtures; no release/admission authority.

Mutable production approval state is never inferred from a fixture. Actual
published stage checks invoke the unchanged authentic source validators.
"""
from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch
import json

NULL_COREUTILS_FIELDS=('artifact','outputs','source','runtime_payload','installed_callers')
def encoded(value):return(json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode()
def binding(raw):return{'bytes':len(raw),'sha256':sha256(raw).hexdigest()}
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
def assert_actual_assertion_stage(test,M,raws):
 """The actual current pin-bound preflight always runs, at its declared stage."""
 key=M.BUSINESS_DIR+'/python31118-root-assertion-approval.json';value=json.loads(raws[key]);test.assertIs(type(value.get('approved')),bool)
 if value['approved']is False:
  with test.assertRaisesRegex(RuntimeError,'^business81_assertion_approval$'):M.preflight_assertion_sources(raws)
  return'PENDING_UNAPPROVED_NOT_ADMITTED'
 M.preflight_assertion_sources(raws);return'ROOT_APPROVED_SOURCE_VALIDATOR_PASSED_NOT_ACTIVATED'
