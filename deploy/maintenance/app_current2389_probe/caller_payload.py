"""Twelve fixed current body identities across the two genuine payload roles.

This proves coverage only. No complete semantic caller inventory or caller-fix
success is inferred from package metadata or consistent byte pins.
"""
import json
CODES=frozenset(('required_caller_contract','required_caller_payload'))
def need(v,c):
 if not v:raise RuntimeError(c)
def check(main,extra,contract,*,observed=False):
 need(type(contract)is dict and contract.get('schema')=='current2389_required12_payload_preflight/1'
  and contract.get('complete_semantic_caller_inventory_claimed')is False and contract.get('security_accepted')is False,'required_caller_contract')
 rows=contract.get('rows');need(type(rows)is list and len(rows)==12 and len({r['path']for r in rows})==12,'required_caller_contract')
 need(main.get('scope')==extra.get('scope')==contract['scope'],'required_caller_payload')
 packages=main.get('packages',[])+extra.get('packages',[]);need(len(packages)==23 and len({p['package']for p in packages})==23,'required_caller_payload')
 field='actual_files'if observed else'expected_files';matched=[]
 for row in rows:
  entries=[f for p in packages if p.get('package')==row['package']for f in p.get(field,[])if f.get('path')==row['path']and f.get('sha256')==row['sha256']and f.get('bytes')==row['bytes']]
  need(len(entries)==1,'required_caller_payload');matched.append(row)
 return {'schema':'current2389_required12_payload_coverage/1','scope':contract['scope'],'rows':matched,
  'observed_in_two_actual_payload_reports':observed,'complete_semantic_caller_inventory_claimed':False,'caller_fix_proof_created':False,'security_accepted':False}
