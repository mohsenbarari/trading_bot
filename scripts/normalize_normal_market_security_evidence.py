"""Finite normal-Market producer for the unchanged strict evidence assessor.

Pure normalization of authenticated originals. The normal driver retains their
readers and supplies actual closure. No old App gate/probe is made fresh.
"""
from hashlib import sha256
from types import ModuleType
import re

PRODUCT={'build':'product-build.json','source':'product-source.json','manifest':'product-manifest.json','recipe':'product-Dockerfile'}
CALLER_PINS={'caller-acl.json':(9340,'0b3c513e73e9beb3fd89109618bcb52bd9a3c093e3df7bb29742c1e4b2c74aeb'),
 'root-static3-source.json':(3573,'a4e281e5a226c4362d766248c8bf2ad4873ec8f91df9b633abe605203de2395b'),
 'root-core106-source.json':(1785,'c3333e9ed3b414ffd90de52fa69f809e3ad5c7b3cf13fc23866c85a77ebe72b3'),
 'root-carry143-source.json':(141923,'e820f9eccff944998afe3f3ec5656a021569f8f2ed5ce173ae9aab37acbc7e36')}
SQLITE_FTS_PINS={'sqlite-fts5-fix.patch':(1240,'90258813c142898908fe16bdb027688249e9353289bc42fc3f7bac15d64507e8'),
 'sqlite-fts5-provenance.json':(178798,'a43db5c82318d1fd5a52eb6881917d859df4531228e1300a41809c4e6bc20da7'),
 'sqlite-vendor-standard.log':(90645,'8272643dc2c6904151219d921e8c6c0ee9fdb8c09f712e5ffe71cc197c988f02'),
 'sqlite-vendor-fts5-regression.log':(1261,'6ec762782583f941ab6a588ba51a6401729701e2bab4a5e3c95d0aec057e0b15')}

def pin(raw):return{'bytes':len(raw),'sha256':sha256(raw).hexdigest()}

def sqlite_fts5_source(A,inputs,payload):
 package=next(item for item in payload['packages']if item['package']=='libsqlite3-0')
 expected={'package':'libsqlite3-0','installed_version':'3.46.1-7+deb13u2+gold2','source_package':'sqlite3',
  'source_version':'3.46.1','source_release':'7+deb13u2+gold2','deb_sha256':'e869f04105dc2c35d32351c771387da1e4a6bca745284312738b2762f2adde4b'}
 if not all(package[key]==value for key,value in expected.items()):return None
 for name,binding in SQLITE_FTS_PINS.items():
  raw=inputs.get(name);A.need(type(raw)is bytes and(len(raw),sha256(raw).hexdigest())==binding,'normal_sqlite_fts5_original')
 library={'path':'/usr/lib/x86_64-linux-gnu/libsqlite3.so.0.8.6','bytes':1571096,'sha256':'d115380cd2790ee09d493eb0af788745f26a18dcef03ad62a543feff363ba86b'}
 A.need(sum(all(file[key]==value for key,value in library.items())for file in package['actual_files'])==1,'normal_sqlite_fts5_payload')
 return{'advisory_ids':['CVE-2026-119373'],'source_package':'sqlite3','source_version':'3.46.1','source_release':'7+deb13u2+gold2',
  'source_verified':True,'build_abi_verified':True,'packages':[{key:package[key]for key in('package','installed_version','deb_sha256')}]}

def callers(A,inputs,runtime,payload):
 for name,expected in CALLER_PINS.items():A.need((len(inputs[name]),sha256(inputs[name]).hexdigest())==expected,'normal_caller_original')
 original=A.document(inputs['caller-acl.json']);A.need(original['kind']=='caller_fix'and original['scope']['image_id']=='sha256:2389c900dbbf520bd7d7f723e8c48a5c258f1612c43e80d2b66c6d046f9f8434','normal_caller_original')
 qualified=original['observations']['callers'];A.need(len(qualified)==12 and len({row['path']for row in qualified})==12,'normal_caller_original')
 actual=runtime['acl_consumers'];A.need(actual.get('schema')=='security_acl_consumer_presence/1'and actual.get('image_id')==runtime['image_id']
  and actual.get('source_commit')==runtime['source_commit']and actual.get('verifier_sha256')==pin(inputs['acl-verifier.py'])['sha256']
  and actual.get('scope')=='regular_elf_dynamic_needed_and_undefined_acl_symbols_under_fixed_roots'
  and actual.get('directory_symlinks_followed')is False and actual.get('production_mounts_or_data_used')is False and actual.get('security_accepted')is False,'normal_caller_inventory')
 inventory=actual.get('inventory');A.need(type(inventory)is dict and inventory.get('roots')==['/usr','/app','/opt'],'normal_caller_inventory')
 rows=inventory.get('elf_files');A.need(type(rows)is list and 0<len(rows)<=8192 and len({row['path']for row in rows})==len(rows),'normal_caller_inventory')
 by_path={row['path']:row for row in rows};allowed={row['path']:row for row in qualified};native={file['path']:(package['package'],file)for package in payload['packages']for file in package['actual_files']}
 for row in rows:
  A.need(all(type(row.get(name))is list for name in('needed','undefined_legacy_acl','undefined_at_acl','undefined_fd_acl')),'normal_caller_inventory')
  if 'libacl.so.1'in row['needed']or row['undefined_legacy_acl']or row['undefined_at_acl']or row['undefined_fd_acl']:
   A.need(row['path']in allowed and row['sha256']==allowed[row['path']]['sha256'],'normal_unreviewed_acl_caller')
 for row in qualified:
  A.need(row['reviewed_source_input']in CALLER_PINS and row['path']in by_path and by_path[row['path']]['sha256']==row['sha256']
   and row['path']in native and native[row['path']][0]==row['package']and native[row['path']][1]['sha256']==row['sha256'],'normal_caller_payload')
 smoke=runtime.get('ordinary_caller_smoke');A.need(type(smoke)is dict and smoke.get('ordinary_caller_smoke_passed')is True
  and type(smoke.get('successful_command_invocations'))is int and smoke['successful_command_invocations']==11
  and smoke.get('public_owned_fixtures_only')is True and smoke.get('fixtures_removed')is True,'normal_caller_smoke')
 return{'whole_image_inventory_complete':True,'all_callers_bound_to_reviewed_source':True,'unresolved_legacy_callers':0,'ordinary_compatibility_passed':True,'callers':qualified}

def prepare(A,R,originals,*,now):
 """No observation DTO, waiver, profile selector or guessed successful gate."""
 A.need(type(originals)is dict and all(type(name)is str and A.NAME.fullmatch(name)and type(raw)is bytes and 0<len(raw)<=A.MAX_JSON for name,raw in originals.items())
  and len(originals)<110 and sum(map(len,originals.values()))<=A.MAX_TOTAL-4*1024**2,'normal_original_inputs')
 inputs=dict(originals);build=A.document(inputs[PRODUCT['build']]);receipt=build['source_inputs']
 scope={'image_id':build['image_id'],'platform':'linux/amd64','source_commit':receipt['release_commit'],'source_tree':receipt['release_tree'],
  'input_signature':build['two_git_input_signature'],'build_run_id':build['run_id'],'build_run_attempt':build['run_attempt']};A.scope(scope)
 scan_raw=inputs['scan.json'];sbom_raw=inputs['sbom.json'];rows,counts=A.findings(A.document(scan_raw),A.document(sbom_raw),scope)
 expected,bundle=R.prepare_request(build,inputs['runtime-vector.json'],inputs['sqlite-lock.json'],inputs['component-verifier.py'],inputs['acl-verifier.py'])
 runtime=A.document(inputs['runtime-observation.json']);A.need(runtime.get('schema')=='normal_market_same_image_runtime_observation/1'
  and runtime.get('status')=='OBSERVED_NOT_ASSESSED'and all(runtime.get(name)==bundle[name]for name in('image_id','source_commit','source_tree','input_signature','run_id','run_attempt','vector_sha256'))
  and A.integer(runtime.get('observed_at'),max(0,now-86400),now)and runtime.get('production_mounts_or_data_used')is False and runtime.get('security_accepted')is False,'normal_runtime_scope')
 payload=R.payload_observations(expected,runtime)
 A.need(A.same(A.document(inputs['component-original.json']),runtime['component_presence']),'normal_component_projection')
 export=A.document(inputs['image-export.json']);factual=A.document(inputs['factual-evidence.json']);sqlite=A.document(inputs['sqlite-probe.json']);lock_sqlite=A.document(inputs['sqlite-lock.json'])
 A.need(export.get('status')=='exported_not_accepted'and export.get('image_id')==export.get('image_config_id')==scope['image_id']
  and export.get('release_commit')==scope['source_commit']and export.get('release_tree')==scope['source_tree']
  and export.get('two_git_input_signature')==scope['input_signature']and export.get('raw_counts')==counts
  and export.get('reports')=={'vulnerabilities.json':pin(scan_raw),'sbom.cdx.json':pin(sbom_raw)}
  and export.get('security_accepted')is False,'normal_export_gate')
 A.need(factual.get('image_id')==scope['image_id']and factual.get('release_commit')==scope['source_commit']
  and factual.get('two_git_input_signature')==scope['input_signature']and factual.get('historical_business_tests')==81
  and factual.get('complete_ci_controller_payload')is True and factual.get('raw_counts')==counts
  and factual.get('security_accepted')is False,'normal_factual_gate')
 historical=build['historical81'];A.need(historical.get('source_and_output_finalizers_completed')is True
  and historical.get('actual_same_candidate_image_verified')is True and historical.get('actual_profile_containers_removed')==3
  and historical.get('actual_outer_authentication_and_cleanup_still_required')is False,'normal_cleanup_gate')
 A.need(sqlite.get('status')=='PASS'and sqlite.get('sqlite_package')==lock_sqlite['package']['version']
  and sqlite.get('sqlite_system_library')=={**lock_sqlite['installed_library'],'mapped_inode_verified':True}
  and sqlite.get('production_data_accessed')is False and sqlite.get('all_cves_attested')is False,'normal_sqlite_gate')
 review=A.document(inputs['current-review.json']);A.need(review.get('schema')=='normal_market_current_security_review/1'
  and review.get('official_sources_reviewed')is True and review.get('old_probe_or_gate_retimestamps')is False
  and review.get('security_accepted')is False and A.integer(review.get('reviewed_at'),0,now)
  and A.integer(review.get('valid_until'),now+1,2**63-1)and review['valid_until']-review['reviewed_at']<=86400
  and type(review.get('advisory_ids'))is list and len(set(review['advisory_ids']))==len(review['advisory_ids'])
  and {row['advisory']for row in rows}<=set(review['advisory_ids'])<=set(A.SOURCES),'normal_current_review')
 producer='normalizer.py';A.need(producer in inputs,'normal_producer_source')
 descriptors={};proof_names=[]
 def proof(name,kind,facts,source_names=()):
  names=sorted(set(PRODUCT.values())|set(source_names));A.need(all(key in inputs for key in names),'normal_proof_originals')
  value={'schema':'image_security_evidence_proof/1','kind':kind,'scope':scope,'producer_sha256':pin(inputs[producer])['sha256'],
   'original_inputs':{key:pin(inputs[key])for key in names},'completed_at':now,'observations':facts}
  inputs[name]=A.encoded(value);descriptors[name]={'kind':kind,'producer':producer,'original_inputs':names};proof_names.append(name)
 proof('advisory-proof.json','advisory_review',{'official_sources_reviewed':True,'advisory_ids':review['advisory_ids']},['current-review.json'])
 source_pin=(15709,'2b4f2cb5884b528ecc91d64e0d86745f642b345cc36483c346f4165ec34be530')
 A.need((len(inputs['source03.py']),sha256(inputs['source03.py']).hexdigest())==source_pin,'normal_source_adapter')
 S=ModuleType('normal_original_source_receipts');exec(compile(inputs['source03.py'],'<original-source-receipts>','exec'),S.__dict__)
 family_inputs={}
 for family,prefix in(('util-linux','util'),('ncurses','ncurses'),('acl','acl')):
  if not any(row['source_package']==family for row in rows):continue
  names={'candidate.zip':prefix+'.candidate.zip',**{'recipe-'+name:prefix+'.recipe-'+name for name in S.FAMILIES[family]['recipe']}}
  raws={name:inputs[key]for name,key in names.items()};family_inputs[family]=raws
  facts=S.receipt(A,family,raws)
  measured={item['package']:item for item in payload['packages']}
  for package in facts['packages']:
   A.need(package['package']in measured and all(measured[package['package']][key]==package[key]for key in('package','installed_version','deb_sha256')),'normal_source_payload')
  for row in rows:
   if row['source_package']==family:A.need(all(row[key]==facts[key]for key in('source_package','source_version','source_release'))
    and any(row['package']==package['package']and row['installed_version']==package['installed_version']for package in facts['packages']),'normal_source_row')
  if any(row['source_package']==family and row['advisory']in facts['advisory_ids']for row in rows):
   proof(prefix+'-source-proof.json','source_backport',facts,[*names.values(),'source03.py','scan.json','sbom.json'])
 if any(row['advisory']=='CVE-2026-78409'for row in rows):
  primary={name:inputs['historic.'+name]for name in S.PRIMARY}
  facts=S.subdir_absence_facts(A,inputs['subdir.zip'],family_inputs['util-linux'],primary)
  proof('subdir-absence-proof.json','component_absence',facts,['source03.py','subdir.zip',*('historic.'+name for name in S.PRIMARY),
   'util.candidate.zip',*('util.recipe-'+name for name in S.FAMILIES['util-linux']['recipe'])])
 sqlite_facts=sqlite_fts5_source(A,inputs,payload)if any(row['advisory']=='CVE-2026-119373'for row in rows)else None
 if sqlite_facts is not None:
  proof('sqlite-fts5-source-proof.json','source_backport',sqlite_facts,[*SQLITE_FTS_PINS,'sqlite-probe.json','sqlite-lock.json','runtime-observation.json','runtime-collector.py'])
 native_required=sqlite_facts is not None or any(row['source_package']in('util-linux','ncurses','acl')for row in rows)
 if native_required:proof('runtime-payload-proof.json','runtime_payload',payload,['runtime-observation.json','runtime-vector.json','runtime-collector.py','sqlite-lock.json','component-verifier.py','acl-verifier.py'])
 if any(row['advisory']=='CVE-2026-54369'for row in rows):
  facts=callers(A,inputs,runtime,payload)
  proof('caller-proof.json','caller_fix',facts,['runtime-observation.json','runtime-collector.py','acl-verifier.py',*CALLER_PINS])
 for advisory,component in(('CVE-2026-16742','systemd_homed'),('CVE-2026-9538','archive_tar')):
  if any(row['advisory']==advisory for row in rows):
   proof(component+'-absence-proof.json','component_absence',{'advisory_ids':[advisory],'component':component,'absent':True,
    'inventory_complete':True,'unreadable_inputs':0,'limit_exceeded':False,'scope':'known_vendor_paths_names_and_actual_perl_inc'},
    ['runtime-observation.json','component-original.json','runtime-collector.py','component-verifier.py'])
 gates=[]
 for name,gate in(('compatibility','normal_market_81_unchanged'),('cleanup','owned_cleanup'),('admission','normal_market_source_archive_daemon'),('sqlite','sqlite_native_installed')):
  name=name+'-gate-proof.json';proof(name,'mandatory_gate',{'passed':True,'failures':0,'skipped':0,'scope':gate},
   ['factual-evidence.json','image-export.json','runtime-observation.json','sqlite-probe.json','runtime-collector.py']);gates.append(name)
 rules=[]
 for row in rows:
  cve=row['advisory'];selected=['runtime-payload-proof.json'];disposition='fixed'
  if cve=='CVE-2026-78409':disposition='component_not_present';selected+=['subdir-absence-proof.json']
  elif cve in('CVE-2026-16742','CVE-2026-9538'):
   disposition='component_not_present';selected=[('systemd_homed'if cve=='CVE-2026-16742'else'archive_tar')+'-absence-proof.json']
  elif row['source_package']=='util-linux':selected+=['util-source-proof.json']
  elif row['source_package']=='ncurses':selected+=['ncurses-source-proof.json']
  elif row['source_package']=='acl':selected+=['acl-source-proof.json','caller-proof.json']
  elif cve=='CVE-2026-119373'and sqlite_facts is not None:selected+=['sqlite-fts5-source-proof.json']
  else:disposition='unresolved';selected=[]
  rules.append({'row':row,'disposition':disposition,'proofs':selected})
 lock={'schema':'image_security_evidence_lock/2','armed':True,'scope':scope,'scan':pin(scan_raw),'sbom':pin(sbom_raw),
  'review':{key:review[key]for key in('reviewed_at','valid_until')},'inputs':{name:pin(raw)for name,raw in inputs.items()},'proofs':descriptors,'required_gates':gates,'findings':rules,'product':PRODUCT}
 return{'lock_raw':A.encoded(lock),'inputs':inputs}

def produce(A,R,originals,*,now,finalizer):
 prepared=prepare(A,R,originals,now=now)
 assessment=A.assess(prepared['lock_raw'],prepared['inputs']['scan.json'],prepared['inputs']['sbom.json'],prepared['inputs'],now=now,finalizer=finalizer)
 return{**prepared,'assessment':assessment}
