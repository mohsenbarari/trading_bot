"""Pure authenticated Market rebuild evidence; private Config values stay in RAM."""
from hashlib import sha256
import io,json,re,zipfile

CATALOG_SHA='c354498c3659fdf99613753d950416f134d81e0fe61fbdc80a32e7eec29b8d12'
STACK_SHA='ec7c9455e1e780b525b96095b1c62eeee66bf17b57c4196c2f8c9557de85d7e4'
PARENT='sha256:2c6ddd332f0aac17613208e66980e0154933bcb23cf2b94bd3d228c3ad5fd4e7'
E46='sha256:e46aedbe135117301fb19183ff39587174162ca4a454ac60c7fccb2b302033cd'
E46_PARENT='sha256:c22d9d459ab4ca0586066d87e2b2456958309fa12e47dfaac2651a1743e0fde4'
DEFAULT_KEYS=frozenset(('PYTHON_VERSION','PYTHON_SHA256','PYTHONDONTWRITEBYTECODE'))
ANCESTRY_LABELS=frozenset(('io.gold-trade.security.baseline.image','io.gold-trade.security.baseline.tree',
 'io.gold-trade.security.overlay.revision','io.gold-trade.security.overlay.tree'))
PROFILE_PROVENANCE_KEYS=ANCESTRY_LABELS|frozenset(('io.gold-trade.release.input-signature','io.gold-trade.security.parent-image',
 'io.gold-trade.security.product-overrides','io.gold-trade.security.product-proof','io.gold-trade.security.recipe'))
E46_PROVENANCE_KEYS=ANCESTRY_LABELS|frozenset(('io.gold-trade.release.input-signature','io.gold-trade.release.tree','org.opencontainers.image.version'))
LOADS=frozenset((('security-market-candidate-images-20261025-05','market_image_store_control/1','cd8b11fbc8abc5f3f09db023e72613bf68f77ec622d647f50dbcf0103d56eb3e'),
 ('security-market-candidate-images-20261025-06','market_image_store_control/1','1041cc2d04100e2f6aebb95dca30c2339de41342b350ef44522c05277e9c146e'),
 ('security-market-candidate-images-20261025-06','market_image_store_control/2','5017b7546a3f117e74fdd6314582e19e39395ae1ac183149be924ba720d68aac')))
def need(ok,code):
 if not ok:raise RuntimeError(code)
def same(a,b):
 if type(a) is not type(b):return False
 if type(a) is dict:return set(a)==set(b) and all(same(a[k],b[k]) for k in a)
 if type(a) is list:return len(a)==len(b) and all(same(x,y) for x,y in zip(a,b))
 return a==b
def document(raw):
 need(type(raw) is bytes and 0<len(raw)<=2*1024**2,'market_json')
 def pairs(rows):
  result={}
  for key,value in rows:need(key not in result,'market_json');result[key]=value
  return result
 return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:need(False,'market_json'))
def environment(rows):
 need(type(rows) is list and all(type(v) is str and '=' in v for v in rows),'market_image_defaults')
 result={}
 for value in rows:
  key,content=value.split('=',1);need(key and key not in result,'market_image_defaults');result[key]=content
 return result

def lineage(catalog_raw,zip_raw,stack_raw,role):
 """Return private expected configs. Never serialize this return value."""
 need(sha256(catalog_raw).hexdigest()==CATALOG_SHA and sha256(stack_raw).hexdigest()==STACK_SHA,'market_catalog')
 catalog=document(catalog_raw);selected=[v for v in catalog['profiles'] if v['role']==role];need(len(selected)==1,'market_profile');spec=selected[0]
 row=spec['evidence_artifact'];need(type(zip_raw) is bytes and len(zip_raw)==row['size_in_bytes'] and sha256(zip_raw).hexdigest()==row['digest'][7:],'market_zip')
 with zipfile.ZipFile(io.BytesIO(zip_raw)) as archive:
  infos=archive.infolist();need(len(infos)<=64 and len({r.filename for r in infos})==len(infos),'market_evidence_inventory')
  def read(name,maximum=2*1024**2):
   item=archive.getinfo(name);need(not item.is_dir() and not item.flag_bits&1 and 0<item.file_size<=maximum,'market_evidence_bound');return archive.read(item)
  export_raw=read('image-export.json',65536);need(len(export_raw)==spec['public_export_bytes'] and sha256(export_raw).hexdigest()==spec['public_export_sha256'],'market_export')
  export=document(export_raw);composition_raw=read('reports/composition.json');composition=document(composition_raw)
  report_raw=read('reports/vulnerabilities.json');need(sha256(report_raw).hexdigest()==spec['public_raw_report_sha256'],'market_raw_report')
  image=document(report_raw)['Metadata'];need(image['ImageID']==spec['candidate_config_id'],'market_config_identity');config=image['ImageConfig']
  need(sha256(composition_raw).hexdigest()==export['composition_sha256'] and export['image_id']==composition['image_id']==spec['candidate_config_id']
   and same(export['archive'],spec['archive']) and same(config['rootfs']['diff_ids'],spec['candidate_rootfs'])
   and same(composition['candidate_layers'],spec['candidate_rootfs']),'market_composition')
  need(export['security_accepted'] is False and export['deployment_eligible'] is False and export['deployment_performed'] is False
   and composition['security_accepted'] is False and composition['deployment_eligible'] is False,'market_false_claims')
  if spec['profile']=='e46aedbe1351':
   baseline=E46;need(export['parent_image_id']==composition['parent_image_id']==E46_PARENT and len(composition['baseline_layers'])==17
    and len(spec['candidate_rootfs'])==18 and same(spec['candidate_rootfs'][:17],composition['baseline_layers'])
    and composition['whole_config_unchanged'] is True and export['whole_config_unchanged'] is True
    and composition['app_or_market_recompiled'] is False and export['app_or_market_recompiled'] is False,'market_e46_lineage')
   payload=None;expected=composition['baseline_config']
  else:
   payload_raw=read('reports/profile-payload.json');payload=document(payload_raw)
   need(payload['schema']=='market_security_profile_payload/1' and payload['profile']==composition['profile']==export['profile']==spec['profile']
    and sha256(payload_raw).hexdigest()==export['payload_sha256']==composition['payload_sha256']
    and payload['stack_parity_sha256']==STACK_SHA and payload['runtime_environment_read'] is False
    and payload['security_accepted'] is False and payload['deployment_eligible'] is False,'market_payload')
   baseline=payload['baseline_image_id'];need(baseline.startswith('sha256:'+spec['profile']) and len(baseline)==71
    and export['parent_image_id']==composition['parent_image_id']==PARENT and len(composition['parent_layers'])==18
    and len(spec['candidate_rootfs'])==19 and same(spec['candidate_rootfs'][:18],composition['parent_layers'])
    and payload['image_input_signature']==composition['image_input_signature']==export['image_input_signature']==spec['candidate_input_signature']
    and payload['baseline_input_signature']==composition['baseline_input_signature']==export['baseline_input_signature']
    and composition['all_dependencies_reused'] is True and composition['all_non_product_stack_hashes_equal'] is True,'market_profile_lineage')
   expected=composition['candidate_config']
 stack=document(stack_raw);rows=[v for v in stack['profiles'] if v['image_id']==baseline];need(len(rows)==1,'market_old_public_config');old=rows[0]['image']
 need(old['image_id']==baseline and old['rootfs']['Type']=='layers' and old['rootfs']['Layers'] and not same(old['rootfs']['Layers'],spec['candidate_rootfs'][:len(old['rootfs']['Layers'])]),'market_rebuild_model')
 if payload is not None:need(same(payload['old_image_nonenv_config'],old['config']),'market_old_public_config')
 return {'spec':spec,'baseline_image_id':baseline,'baseline_rootfs':old['rootfs']['Layers'],'old_nonenv_config':old['config'],
  'expected_candidate_config':expected,'raw_candidate_config':config['config'],'payload':payload,'export':export,'composition':composition,
  'public':{'schema':'normal_market_authenticated_rebuild/1','profile':spec['profile'],'role':role,'baseline_image_id':baseline,
   'candidate_config_id':spec['candidate_config_id'],'baseline_layer_count':len(old['rootfs']['Layers']),
   'candidate_layer_count':len(spec['candidate_rootfs']),'model':'authenticated_security_rebuild','physical_admission_rechecked':False,
   'configuration_values_disclosed':False,'security_accepted':False}}

def admit_load(data,receipt_raw,control_raw,admitted_raw,site,machine):
 need(site in {'bot','web'} and type(machine) is str and re.fullmatch('[a-f0-9]{32}',machine),'market_load_control')
 spec=data['spec'];r=document(receipt_raw);op=document(control_raw);a=document(admitted_raw)
 need((op['operation'],op['schema'],op['worker_sha256']) in LOADS and op['profile']==spec['profile']
  and op['phase']==('load-native' if site=='bot' else 'load-web') and type(op['nonce']) is str and re.fullmatch('[a-f0-9]{32}',op['nonce']),'market_load_control')
 need(r['schema']=='market_preloaded_image/1' and r['profile']==spec['profile'] and r['role']==spec['role']
  and r['catalog_sha256']==CATALOG_SHA and r['site']==('native' if site=='bot' else 'web') and r['host_machine_id']==machine
  and r['ci_run_id']==spec['ci_run_id'] and r['image_artifact_sha256']==spec['image_artifact']['digest'][7:]
  and r['candidate_config_id']==spec['candidate_config_id'] and same(r['rootfs'],spec['candidate_rootfs'])
  and r['operation_sha256']==sha256(control_raw).hexdigest() and r['source_admission_sha256']==op['admission_sha256']==sha256(admitted_raw).hexdigest()
  and r['archive_and_config_association_verified'] is True and r['whole_typed_config_compared_in_ram'] is True
  and r['configuration_values_disclosed'] is False and r['security_accepted'] is False and r['serving_roles_activated']==[],'market_load_receipt')
 need(a['schema']=='market_candidate_archive/1' and a['profile']==spec['profile'] and a['candidate_config_id']==spec['candidate_config_id']
  and same(a['rootfs'],spec['candidate_rootfs']) and a['archive_sha256']==r['source_archive_sha256']==spec['tar_sha256']
  and type(a['archive_bytes']) is int and a['archive_bytes']==spec['archive']['uncompressed_bytes']
  and type(a['tar_identity']) is list and len(a['tar_identity'])==9 and all(type(v) is int for v in a['tar_identity'])
  and a['tar_identity'][0]>0 and a['tar_identity'][1]>0 and a['tar_identity'][2:7]==[0o100600,0,0,1,a['archive_bytes']]
  and a['security_accepted'] is False and a['configuration_values_disclosed'] is False,'market_archive_receipt')
 local=r['candidate_local_image_id'];ids=a['source_identities'];need(type(ids) is dict and len(ids)==3 and spec['candidate_config_id'] in ids
  and type(local) is str and re.fullmatch('sha256:[a-f0-9]{64}',local) and local in ids,'market_local_identity')
 expected={'Docker_config_identity':{'kind'},'OCI_manifest_to_config':{'kind','media_type','size'},'OCI_index_to_manifest_to_config':{'kind','media_type','size'}}
 kinds=set();media={'OCI_manifest_to_config':'application/vnd.oci.image.manifest.v1+json','OCI_index_to_manifest_to_config':'application/vnd.oci.image.index.v1+json'}
 for digest,row in ids.items():
  need(type(digest) is str and re.fullmatch('sha256:[a-f0-9]{64}',digest) and type(row) is dict
   and row.get('kind') in expected and set(row)==expected[row['kind']] and row['kind'] not in kinds,'market_local_identity');kinds.add(row['kind'])
  if row['kind']=='Docker_config_identity':need(digest==spec['candidate_config_id'],'market_local_identity')
  else:need(row['media_type']==media[row['kind']] and type(row['size']) is int and 0<row['size']<=2*1024**2,'market_local_identity')
 need(kinds==set(expected),'market_local_identity')
 return local,r,a

def image_delta(D,data,old,candidate,local_id):
 """Actual daemon Config and defaults admission. No private return is emitted."""
 spec=data['spec'];need(old['Id']==data['baseline_image_id'] and same(old['RootFS'],{'Type':'layers','Layers':data['baseline_rootfs']})
  and candidate['Id']==local_id and same(candidate['RootFS'],{'Type':'layers','Layers':spec['candidate_rootfs']}),'market_daemon_identity')
 # Old public inspect observations used null for omitted API fields. This
 # normalization is only that observation/API boundary, never business Env.
 old_expected={k:v for k,v in data['old_nonenv_config'].items() if v is not None};actual={k:v for k,v in old['Config'].items() if k!='Env'}
 need(D.same(D.container_config(actual),D.container_config(old_expected)),'market_old_public_config')
 wanted=D.container_config(data['raw_candidate_config'])
 need(D.same(D.container_config(candidate['Config']),wanted) and D.same(D.container_config(data['expected_candidate_config']),wanted),'market_candidate_config')
 old_labels=old['Config'].get('Labels') or {};new_labels=candidate['Config'].get('Labels') or {}
 need(type(old_labels) is type(new_labels) is dict,'market_image_provenance')
 label_changes={k for k in set(old_labels)|set(new_labels) if old_labels.get(k)!=new_labels.get(k) or (k in old_labels)!=(k in new_labels)}
 # Exact keysets measured in all ten authenticated exports, derived from
 # the fixed CI composition recipes. Business labels receive no exception.
 need(label_changes==(E46_PROVENANCE_KEYS if spec['profile']=='e46aedbe1351' else PROFILE_PROVENANCE_KEYS),'market_image_provenance')
 before=environment(old['Config'].get('Env',[]));after=environment(candidate['Config'].get('Env',[]))
 changed={k for k in set(before)|set(after) if before.get(k)!=after.get(k) or (k in before)!=(k in after)}
 need(changed<=DEFAULT_KEYS,'market_image_default_policy')
 need('PYTHONDONTWRITEBYTECODE' not in changed or after.get('PYTHONDONTWRITEBYTECODE')=='1','market_image_default_policy')
 # Every non-provenance/non-default field must remain exactly equal under
 # the same fixed OCI/API serializer used by the physical archive contract.
 a=D.container_config(old['Config']);b=D.container_config(candidate['Config'])
 for value in (a,b):value.pop('Env',None);value.pop('Labels',None)
 need(D.same(a,b),'market_business_image_config')
 return {'schema':'normal_market_image_delta_admission/1','candidate_config_id':spec['candidate_config_id'],'candidate_local_image_id':local_id,
  'image_default_change_count':len(changed),'authenticated_build_provenance_changed':bool(label_changes),
  'business_image_config_equal_in_ram':True,'candidate_config_equal_authenticated_report_in_ram':True,'configuration_values_disclosed':False,
  'configuration_values_hashed':False,'serving_equivalence_proven':False,'security_accepted':False}
