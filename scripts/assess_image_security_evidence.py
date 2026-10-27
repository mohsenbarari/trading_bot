"""Pure, bounded image-finding assessment; imports perform no I/O.

The caller retains every admitted input FD and supplies its finalizer. A reviewed
lock pins genuine producer sources, original evidence and normalized proof bytes.
This module neither discovers proofs nor trusts a caller-selected exception.
"""
from hashlib import sha256
from collections import Counter
from pathlib import PurePosixPath
from urllib.parse import unquote,parse_qsl
import json,math,re

MAX_JSON=8*1024**2;MAX_TOTAL=32*1024**2;MAX_ROWS=1024;MAX_COMPONENTS=8192
SCOPE={'image_id','platform','source_commit','source_tree','input_signature','build_run_id','build_run_attempt'}
ROW={'advisory','package','installed_version','source_package','source_version','source_release','package_type','purl','bom_ref','layer_diff_id','layer_digest','severity','scan_status','fixed_version','target'}
KINDS={'source_backport','runtime_payload','component_absence','caller_fix','mandatory_gate','advisory_review'}
HEX=re.compile('[a-f0-9]{64}');NAME=re.compile('[A-Za-z0-9_.-]{1,128}')
SOURCES={'CVE-2026-76642':'util-linux','CVE-2026-78408':'util-linux','CVE-2026-78409':'util-linux',
 'CVE-2026-78410':'util-linux','CVE-2025-69720':'ncurses','CVE-2026-54369':'acl','CVE-2026-16742':'systemd','CVE-2026-9538':'perl'}
ABSENCE={'CVE-2026-16742':('systemd_homed','known_vendor_paths_names_and_actual_perl_inc'),
 'CVE-2026-9538':('archive_tar','known_vendor_paths_names_and_actual_perl_inc'),
 'CVE-2026-78409':('restricted_user_detached_tree_fast_path','stable_source_vulnerable_path_absent')}

class Refused(ValueError):pass
def need(value,code):
 if not value:raise Refused(code)
def encoded(value):return (json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
def same(a,b):return encoded(a)==encoded(b)
def keys(value,names,code):need(type(value)is dict and set(value)==set(names),code)
def text(value,maximum=4096):return type(value)is str and 0<len(value)<=maximum and all(ord(c)>=32 for c in value)
def integer(value,low,high):return type(value)is int and low<=value<=high
def binding(value):
 keys(value,{'bytes','sha256'},'binding_shape')
 need(integer(value['bytes'],1,MAX_JSON)and type(value['sha256'])is str and HEX.fullmatch(value['sha256']),'binding_value')
def document(raw):
 need(type(raw)is bytes and 0<len(raw)<=MAX_JSON,'json_bound')
 # Bound nesting before json.loads allocates a deeply nested object graph.
 depth=0;string=False;escape=False
 for char in raw:
  if string:
   if escape:escape=False
   elif char==92:escape=True
   elif char==34:string=False
  elif char==34:string=True
  elif char in (91,123):depth+=1;need(depth<=48,'json_depth')
  elif char in (93,125):depth-=1;need(depth>=0,'json_syntax')
 def unique(pairs):
  value={}
  for key,item in pairs:need(key not in value,'duplicate_json_key');value[key]=item
  return value
 def finite(raw):
  value=float(raw);need(math.isfinite(value),'json_nonfinite');return value
 try:return json.loads(raw,object_pairs_hook=unique,parse_float=finite,parse_constant=lambda _:(_ for _ in ()).throw(Refused('json_nonfinite')))
 except (UnicodeError,json.JSONDecodeError,RecursionError):raise Refused('json_syntax')from None
def scope(value):
 keys(value,SCOPE,'scope_shape')
 need(type(value['image_id'])is str and re.fullmatch('sha256:[a-f0-9]{64}',value['image_id'])
  and value['platform']=='linux/amd64'and all(type(value[k])is str and re.fullmatch('[a-f0-9]{40}',value[k])for k in ('source_commit','source_tree'))
  and type(value['input_signature'])is str and HEX.fullmatch(value['input_signature'])
  and integer(value['build_run_id'],1,2**63-1)and integer(value['build_run_attempt'],1,1000),'scope_value')
def properties(component):
 rows=component.get('properties');need(type(rows)is list and len(rows)<=128,'sbom_properties');found={}
 consumed={'ImageID','SrcName','SrcVersion','SrcRelease','PkgType','LayerDiffID'}
 for row in rows:
  need(type(row)is dict and type(row.get('name'))is str,'sbom_property')
  key=row['name']
  # CycloneDX legitimately repeats DiffID for each ordered image layer. Only
  # the singular identity/source properties consumed here require uniqueness.
  if key in {'aquasecurity:trivy:'+name for name in consumed}:
   need(key not in found and text(row.get('value')),'sbom_property');found[key]=row['value']
 return found
def deb_purl(purl,package,installed_version,distro):
 need(text(purl)and purl.startswith('pkg:deb/debian/')and '#'not in purl,'purl_namespace')
 body=purl[len('pkg:deb/debian/'):];core,separator,query=body.partition('?')
 need(separator=='?'and core.count('@')==1 and not re.search('%(?![0-9a-fA-F]{2})',body),'purl_encoding')
 try:
  name,version=core.split('@');name=unquote(name,errors='strict');version=unquote(version,errors='strict')
  pairs=parse_qsl(query,keep_blank_values=True,strict_parsing=True)
 except (ValueError,UnicodeError):raise Refused('purl_encoding')from None
 need(re.fullmatch('[a-z0-9][a-z0-9+.-]{0,127}',name)and name==package and text(version,512)and ':'not in version,'purl_package')
 qualifiers={}
 for key,value in pairs:
  need(key in {'arch','distro','epoch'}and key not in qualifiers and text(value,128),'purl_qualifier');qualifiers[key]=value
 need(set(qualifiers)in ({'arch','distro'},{'arch','distro','epoch'})and qualifiers['arch']in {'amd64','all'}
  and qualifiers['distro']==distro,'purl_platform')
 epoch=qualifiers.get('epoch');need(epoch is None or re.fullmatch('0|[1-9][0-9]{0,9}',epoch),'purl_epoch')
 expected=(epoch+':'if epoch is not None else '')+version
 need(expected==installed_version,'purl_version')
def findings(scan,sbom,expected_scope):
 image=expected_scope['image_id']
 need(type(scan)is dict and type(scan.get('Metadata'))is dict and scan['Metadata'].get('ImageID')==image,'scan_image')
 osinfo=scan['Metadata'].get('OS');need(type(osinfo)is dict and osinfo.get('Family')=='debian'
  and type(osinfo.get('Name'))is str and re.fullmatch(r'[0-9]+(?:\.[0-9]+)*',osinfo['Name']),'scan_os')
 need(type(sbom)is dict and type(sbom.get('metadata'))is dict and type(sbom['metadata'].get('component'))is dict,'sbom_image')
 top=sbom['metadata']['component']
 need(top.get('type')=='container'and top.get('name')==image,'sbom_image')
 need(properties(top).get('aquasecurity:trivy:ImageID')==image,'sbom_image')
 components=sbom.get('components');need(type(components)is list and len(components)<=MAX_COMPONENTS,'sbom_bound');by_purl={}
 for value in components:
  need(type(value)is dict,'sbom_component');purl=value.get('purl')
  if purl is not None:
   need(text(purl),'sbom_purl');by_purl.setdefault(purl,[]).append(value)
 results=scan.get('Results');need(type(results)is list and len(results)<=256,'scan_results');rows=[];counts={key:0 for key in ('UNKNOWN','LOW','MEDIUM','HIGH','CRITICAL')};total=0
 for result in results:
  need(type(result)is dict,'scan_result');vulns=result.get('Vulnerabilities',[])
  need(type(vulns)is list and len(vulns)<=8192,'scan_vulnerabilities')
  for value in vulns:
   total+=1;need(total<=16384 and type(value)is dict and value.get('Severity')in counts,'scan_severity');counts[value['Severity']]+=1
   if value['Severity']not in ('HIGH','CRITICAL'):continue
   need(len(rows)<MAX_ROWS,'finding_bound');identifier=value.get('PkgIdentifier')
   need(type(identifier)is dict and text(identifier.get('PURL')),'finding_identifier');purl=identifier['PURL'];candidates=by_purl.get(purl,[])
   # SBOMs may list the same non-vulnerable Python package at two locations.
   # A consumed HIGH/CRITICAL package must still have one unambiguous identity.
   need(len(candidates)==1,'finding_sbom_ambiguous');component=candidates[0]
   need(type(component)is dict and component.get('name')==value.get('PkgName')and component.get('version')==value.get('InstalledVersion')and component.get('bom-ref')==purl,'finding_sbom')
   prop=properties(component);get=lambda key:prop.get('aquasecurity:trivy:'+key)
   layer=value.get('Layer');need(type(layer)is dict and 'DiffID'in layer and set(layer)<={'DiffID','Digest'}
    and type(layer['DiffID'])is str and re.fullmatch('sha256:[a-f0-9]{64}',layer['DiffID'])and layer['DiffID']==get('LayerDiffID')
    and (layer.get('Digest')is None or type(layer['Digest'])is str and re.fullmatch('sha256:[a-f0-9]{64}',layer['Digest'])),'finding_layer')
   deb_purl(purl,value.get('PkgName'),value.get('InstalledVersion'),'debian-'+osinfo['Name'])
   row={'advisory':value.get('VulnerabilityID'),'package':value.get('PkgName'),'installed_version':value.get('InstalledVersion'),
    'source_package':get('SrcName'),'source_version':get('SrcVersion'),'source_release':get('SrcRelease'),
    'package_type':get('PkgType'),'purl':purl,'bom_ref':component.get('bom-ref'),'layer_diff_id':get('LayerDiffID'),'layer_digest':layer.get('Digest'),
    'severity':value['Severity'],'scan_status':value.get('Status'),'fixed_version':value.get('FixedVersion'),'target':result.get('Target')}
   need(all(text(row[k])for k in ROW-{'fixed_version','layer_digest'})and (row['fixed_version']is None or text(row['fixed_version'])),'finding_fields')
   need(row['advisory']in SOURCES and SOURCES[row['advisory']]==row['source_package']and row['package_type']==result.get('Type')=='debian'
    and re.fullmatch('sha256:[a-f0-9]{64}',row['layer_diff_id']),'finding_scope')
   for key,sourcekey in (('SrcName','source_package'),('SrcVersion','source_version')):
    if value.get(key)is not None:need(value[key]==row[sourcekey],'finding_source_disagreement')
   rows.append(row)
 need(len({encoded(row)for row in rows})==len(rows),'duplicate_finding')
 return rows,counts
def native_packages(observations):
 keys(observations,{'inventory_complete','packages'},'payload_shape');need(observations['inventory_complete']is True,'payload_incomplete')
 packages=observations['packages'];need(type(packages)is list and 0<len(packages)<=128,'payload_packages');found={};paths=set()
 for package in packages:
  keys(package,{'package','installed_version','source_package','source_version','source_release','deb_sha256','expected_files','actual_files'},'payload_package')
  need(all(text(package[k])for k in ('package','installed_version','source_package','source_version','source_release'))and type(package['deb_sha256'])is str and HEX.fullmatch(package['deb_sha256']),'payload_identity')
  need(package['package']not in found and same(package['expected_files'],package['actual_files']),'payload_mismatch');files=package['expected_files']
  need(type(files)is list and 0<len(files)<=8192,'payload_files')
  for file in files:
   keys(file,{'path','bytes','sha256','mode','uid','gid'},'payload_file');path=file['path']
   need(text(path)and str(PurePosixPath(path))==path and path.startswith(('/usr/','/lib/','/lib64/','/bin/','/sbin/'))and '..'not in PurePosixPath(path).parts
    and path not in paths and integer(file['bytes'],1,128*1024**2)and type(file['sha256'])is str and HEX.fullmatch(file['sha256'])
    and integer(file['mode'],0,0o177777)and file['mode']&0o170000==0o100000
    and integer(file['uid'],0,65535)and integer(file['gid'],0,65535),'payload_file')
   paths.add(path)
  found[package['package']]=package
 return found
# This source fragment is incorporated once into the pure assessor successor.
# It does not authenticate a download. The trusted outer caller must retain
# genuine GitHub/build/source originals; the reviewed lock pins their bytes.
PROFILE_GATES={
 'canonical_app32':frozenset(('canonical_32_unchanged','owned_cleanup','canonical_source_archive_daemon','sqlite_native_installed')),
 'normal_market81':frozenset(('normal_market_81_unchanged','owned_cleanup','normal_market_source_archive_daemon','sqlite_native_installed'))}
PRODUCT_INPUT_KEYS={'build','source','manifest','recipe'}
APP_INPUTS=('Dockerfile', '.dockerignore', 'requirements.txt', 'deploy/production/pip-bootstrap-requirements.txt', 'pip_packages', 'api', 'bot', 'core', 'src', 'migrations', 'models', 'templates', 'fonts', 'alembic.ini', 'main.py', 'manage.py', 'run_bot.py', 'schemas.py', 'seed_fake_data.py', 'trading_settings.json', 'scripts', 'mini_app_dist')
MARKET_ROLES={'native/coin-estimator': {'business_commit': 'f4871edbe507546a0a6544c56bc7d6b34bf770a6', 'business_tree': '9b049e5031a33f416b6c6a2a45d89c3ba334fc3e', 'bytes': 1928470, 'component': 'market-role-sources-20261027-fc622107fadf1de9-v1', 'count': 94, 'manifest_sha256': '6ddbb7459a9b69c20accb2e277c8e25c701b95958b8219cfd38a08e998f1b1ca', 'rows_sha256': 'fc622107fadf1de9adec789425c1d3816271829e1310b75347a524c5c104bbcd'}, 'native/estimator-snapshot-sender': {'business_commit': '6c7bfb3fe3714f1b9ecb375e68bbb0b1263ef328', 'business_tree': 'bc90e1224cda5bd2c58be81ea2aa12d1c6fbcadd', 'bytes': 1848102, 'component': 'market-role-sources-20261027-971dc9e57b1a4220-v1', 'count': 93, 'manifest_sha256': '3ce2f0f0b6ed975c2665f68ca0dc53198b9c00c9673fde3979c094c4ae05ec5c', 'rows_sha256': '971dc9e57b1a4220b3604af0ed101377a62ae7b9f6510340b0cc9c6b9a3cb33f'}, 'native/market-fact-receiver': {'business_commit': 'c9f8ada468c94af99a30ede8cdcbd9a220e9b917', 'business_tree': '08d9d82693f8a42a49da89067c4def1862ce1e83', 'bytes': 1934716, 'component': 'market-role-sources-20261027-83c61098e5581bcb-v1', 'count': 95, 'manifest_sha256': '384995da7738128ca902737e7324cd8fc1abff5a581047e41533b06c14683f16', 'rows_sha256': '83c61098e5581bcb9795859444df9730888c46cac911046f293a8056ef3bc3c1'}, 'native/market-store-adapter': {'business_commit': '6cad2c7e4a4b4622ec3610ddb91cce32cd04f2c0', 'business_tree': 'c3a25a925927802f413dc1eda59ca87b3af3e6b7', 'bytes': 1937533, 'component': 'market-role-sources-20261027-0c440e791bde7779-v1', 'count': 95, 'manifest_sha256': '88a982cf6c6da9f736fa11708be694a444ee7d6b3ffcfaea81b43e1c6c52f133', 'rows_sha256': '0c440e791bde77790179ef65b30b9f5abda5434287c09bf87648b61f19011157'}, 'web/estimator-snapshot-receiver': {'business_commit': '89b64c47c952c6f6a6ba75df2b2f40d0208de196', 'business_tree': '9ba0c52b9a9d3a1cbdb01d0417eaa9891b2585e2', 'bytes': 1934784, 'component': 'market-role-sources-20261027-45ccb288a62edc6a-v1', 'count': 95, 'manifest_sha256': 'bacda97ab7e51af550207a9930977be8e12d37d28ed5354d60cc3c078decedc9', 'rows_sha256': '45ccb288a62edc6a5f08042ca33060ce81ca45e34a2a2bf4e18b55f2de73a884'}, 'web/market-capture-account1': {'business_commit': '4ee3fd21003ebb3038a2e4b572ca2eb01cb45d2f', 'business_tree': 'c63f575f2a67287076a2188a72494ee236b2109c', 'bytes': 1896295, 'component': 'market-role-sources-20261027-12cac77ad8481fa6-v1', 'count': 93, 'manifest_sha256': '1693511310372cb9e8906141bfcb0818d19cd32ed4ca5066acf61d8719013394', 'rows_sha256': '12cac77ad8481fa6d11f7885c97ab4e8593290ed70cf388a7b15e86c4139dbad'}, 'web/market-capture-external': {'business_commit': '6c7bfb3fe3714f1b9ecb375e68bbb0b1263ef328', 'business_tree': 'bc90e1224cda5bd2c58be81ea2aa12d1c6fbcadd', 'bytes': 1848102, 'component': 'market-role-sources-20261027-971dc9e57b1a4220-v1', 'count': 93, 'manifest_sha256': '3ce2f0f0b6ed975c2665f68ca0dc53198b9c00c9673fde3979c094c4ae05ec5c', 'rows_sha256': '971dc9e57b1a4220b3604af0ed101377a62ae7b9f6510340b0cc9c6b9a3cb33f'}, 'web/market-fact-sync-worker': {'business_commit': '064451c17c9f2f1b72f3f33fbe68f15fc1384f60', 'business_tree': '7f7f209b17b06984b2ac2fa989177277ed876b24', 'bytes': 1935251, 'component': 'market-role-sources-20261027-4a848dcf6a46d6f5-v1', 'count': 95, 'manifest_sha256': '83a42c0507c28c40574538c61382034a6f376001cf5cb76a1aed97148416de36', 'rows_sha256': '4a848dcf6a46d6f5a0f0e2784f537f6deb2cd6722fcb6ae580a12dfdc7cbf53d'}, 'web/market-processor': {'business_commit': 'db0f8eba339b830ab7ae5fb1c810f149992458b3', 'business_tree': 'e956bcc0ae619fd7495bb4ba41d8a1aeaa7885c1', 'bytes': 1946368, 'component': 'market-role-sources-20261027-5cc7196abb5cfafe-v1', 'count': 95, 'manifest_sha256': 'a5b587cc382563735bb66df783e8def505d12509a9133a08a2537e562cbaf2c9', 'rows_sha256': '5cc7196abb5cfafeb5ca00aab2d34579c94d493c933768253bfba7a34d995c30'}}
MARKET_CATALOG_SHA='33c092cafd2cd301bd1f7b92a73aeca7c685ea189ddc44e8f98bd8d7c09c2831'
F79={'BUSINESS_COMMIT': '126d6b97eecdc7af4bd6415ae12079c84ca4f0c8', 'BUSINESS_TREE': '08606163f552f01e4ca64a2250f360a71edadd78'}
F79_MANIFEST='fef6305b1d7e6db851d1d0b77f3aa9910aad535e83f64f012790165a3d62c81b'
F79_ROWS='75d7d2cfb2c20fd036501e7aac198cb5567fb82c53301fb3972f8c81b935e57f'
F79_COMPONENT='capture2-f79-accepted-20261026'

def public_rows(value,*,recipe=None,recipe_raw=None):
 need(type(value)is list and 0<len(value)<=20000,'product_file_inventory');found={}
 for row in value:
  need(type(row)is dict and type(row.get('path'))is str,'product_file_inventory');name=row['path']
  need(name not in found and str(PurePosixPath(name))==name and not name.startswith('/')
   and '..'not in PurePosixPath(name).parts and all(ord(c)>=32 for c in name)and '\\'not in name,'product_file_inventory')
  need(integer(row.get('bytes'),0,128*1024**2)and type(row.get('sha256'))is str and HEX.fullmatch(row['sha256']),'product_file_inventory')
  found[name]=row
 if recipe is not None:need(recipe in found and {k:found[recipe][k]for k in('bytes','sha256')}=={'bytes':len(recipe_raw),'sha256':sha256(recipe_raw).hexdigest()},'product_recipe_binding')
 return found

def product_profile(lock,inputs):
 """Derive exactly one product from held, separately pinned original bytes.

 No profile selector, waiver, mutable gate set or caller success DTO exists.
 This validates original-document consistency, not GitHub authenticity by JSON.
 """
 names=lock['product'];keys(names,PRODUCT_INPUT_KEYS,'product_shape')
 need(all(type(v)is str and v in inputs for v in names.values())and len(set(names.values()))==4,'product_inputs')
 build,source,manifest=(document(inputs[names[k]])for k in('build','source','manifest'))
 recipe_raw=inputs[names['recipe']];expected=lock['scope']
 need(type(build)is dict and type(source)is dict and type(manifest)is dict
  and build.get('image_id')==expected['image_id']and build.get('security_accepted')is False
  and source.get('security_accepted')is False and manifest.get('security_accepted')is False,'product_build_binding')
 if build.get('schema')=='canonical_app_image_build/1':
  profile='canonical_app32';recipe='Dockerfile';role=None
  need(build.get('platform')==expected['platform']and build.get('source_commit')==expected['source_commit']
   and build.get('source_tree')==expected['source_tree']and build.get('input_signature')==expected['input_signature']
   and build.get('run_id')==str(expected['build_run_id'])and build.get('run_attempt')==str(expected['build_run_attempt'])
   and build.get('activation_performed')is False and build.get('market_roles_built')is False
   and build.get('dockerfile_checks')==['trust','coreutils_acl','python_micro_release','psycopg2','sqlite_libmagic','pip_removed']
   and not any(k in build for k in('role','source_inputs','two_git_input_signature','controller_input_signature')),'product_app_build')
  need(source.get('schema')=='canonical_app_source/1'
   and all(source.get(k)==expected[k]for k in('source_commit','source_tree'))
   and source.get('run_id')==build['run_id']and source.get('run_attempt')==build['run_attempt'],'product_app_source')
  need(manifest.get('schema')=='canonical_app_inputs/1'and manifest.get('signature_format')=='deploy.sh:foreign_image_signature'
   and all(manifest.get(k)==expected[k]for k in('source_commit','source_tree','input_signature')),'product_app_manifest')
  public_rows(source.get('files'),recipe=recipe,recipe_raw=recipe_raw)
  rows=public_rows(manifest.get('files'),recipe=recipe,recipe_raw=recipe_raw)
  need(all(any(name==root or name.startswith(root+'/')for root in APP_INPUTS)for name in rows),'product_app_manifest')
  empty=manifest.get('empty_directories');need(type(empty)is list and len(empty)<=len(APP_INPUTS)
   and all(type(name)is str for name in empty)and len(set(empty))==len(empty)and all(name in APP_INPUTS and not any(p==name or p.startswith(name+'/')for p in rows)for name in empty),'product_app_manifest')
  digest=sha256();digest.update(b'docker_image=trading_bot_base\n')
  for entry in APP_INPUTS:
   selected=[entry]if entry in rows else sorted((p for p in rows if p.startswith(entry+'/')),key=lambda p:p.encode())
   for name in selected:digest.update((rows[name]['sha256']+'  '+name+'\n').encode())
   if not selected and entry in empty:digest.update((sha256(b'').hexdigest()+'  -\n').encode())
  need(digest.hexdigest()==expected['input_signature'],'product_app_signature')
 elif build.get('schema')in('normal_market_capture2_image_build/1','normal_market_role_image_build/1'):
  profile='normal_market81';recipe='deploy/market-data/Dockerfile.capture2';receipt=build.get('source_inputs')
  need(type(receipt)is dict and receipt.get('signature_format')=='normal-market-two-git-sources-v1'
   and receipt.get('release_commit')==expected['source_commit']and receipt.get('release_tree')==expected['source_tree']
   and build.get('two_git_input_signature')==expected['input_signature']
   and sha256(encoded(receipt)).hexdigest()==expected['input_signature']
   and type(build.get('run_id'))is int and build['run_id']==expected['build_run_id']
   and type(build.get('run_attempt'))is int and build['run_attempt']==expected['build_run_attempt']
   and build.get('status')=='built_not_accepted'and build.get('historical81_business_tests_completed')is True
   and build.get('actual_sqlite_completed')is True and build.get('complete_deployment_control_payload')is False
   and receipt.get('repository_identity')=={'host':'github.com','repository':'mohsenbarari/trading_bot'},'product_market_build')
  need(all(type(receipt.get(k))is str and re.fullmatch('[a-f0-9]{40}',receipt[k])for k in('business_commit','business_tree'))
   and receipt['business_commit']!=receipt['release_commit'],'product_market_source')
  controller=build.get('controller_inputs');signature=build.get('controller_input_signature')
  need(type(controller)is dict and controller.get('schema')=='normal_market_controller_inputs/1'
   and controller.get('release_commit')==receipt['release_commit']and controller.get('release_tree')==receipt['release_tree']
   and controller.get('two_git_input_signature')==expected['input_signature']and type(signature)is str
   and HEX.fullmatch(signature)and sha256(encoded(controller)).hexdigest()==signature,'product_controller_signature')
  need(source.get('schema')=='normal_market_source_identity/1'and same(source.get('inputs'),receipt)
   and source.get('input_signature')==expected['input_signature']and same(source.get('controller'),controller)
   and source.get('controller_signature')==signature,'product_market_source')
  need(manifest.get('schema')=='normal_market_two_context_inputs/1'and manifest.get('two_git_input_signature')==expected['input_signature']
   and manifest.get('controller_input_signature')==signature,'product_market_manifest')
  public_rows(receipt.get('release_files'),recipe=recipe,recipe_raw=recipe_raw)
  release=manifest.get('release');need(type(release)is dict and recipe in release
   and release[recipe]=={'bytes':len(recipe_raw),'sha256':sha256(recipe_raw).hexdigest()},'product_recipe_binding')
  if build['schema']=='normal_market_capture2_image_build/1':
   role='market-capture-account2'
   need('role'not in build and receipt.get('schema')=='normal_market_git_component_inputs/1'and receipt.get('role')==role
    and receipt.get('component')==F79_COMPONENT and receipt.get('business_commit')==F79['BUSINESS_COMMIT']
    and receipt.get('business_tree')==F79['BUSINESS_TREE']and receipt.get('accepted_manifest_sha256')==F79_MANIFEST
    and build.get('actual_source93_completed')is True,'product_market_role')
   expected_count=93;rows_sha=F79_ROWS
  else:
   role=build.get('role');need(type(role)is str and role in MARKET_ROLES,'product_market_role');spec=MARKET_ROLES[role]
   locked={'business_commit':spec['business_commit'],'business_tree':spec['business_tree'],
    'repository_identity':receipt['repository_identity'],'role':role,'catalog_sha256':MARKET_CATALOG_SHA,'source_policy':'whole_git_component_no_main_fallback'}
   need(receipt.get('schema')=='normal_market_git_component_inputs/2'and receipt.get('role')==role
    and same(receipt.get('role_source'),spec)and receipt.get('role_catalog_sha256')==MARKET_CATALOG_SHA
    and receipt.get('component')==spec['component']and receipt.get('business_commit')==spec['business_commit']
    and receipt.get('business_tree')==spec['business_tree']and receipt.get('business_manifest_sha256')==spec['manifest_sha256']
    and receipt.get('product_lock_sha256')==sha256(encoded(locked)).hexdigest()
    and build.get('actual_role_source_completed')is True,'product_market_role')
   expected_count=spec['count'];rows_sha=spec['rows_sha256']
  files=receipt.get('product_files');public_rows(files);need(type(files)is list and len(files)==expected_count
   and sha256(encoded(files)).hexdigest()==rows_sha,'product_market_inventory')
  product=manifest.get('product');need(type(product)is dict and len(product)==expected_count
   and product=={r['path']:{k:r[k]for k in('bytes','sha256')}for r in files},'product_market_inventory')
 else:raise Refused('product_build_schema')
 return {'profile':profile,'recipe':recipe,'role':role,'input_names':frozenset(names.values()),
  'original_inputs':{key:lock['inputs'][name]for key,name in names.items()}}


def assess(lock_raw,scan_raw,sbom_raw,inputs,*,now,finalizer):
 """Return only after all byte bindings, row proofs and caller finals pass."""
 need(all(type(raw)is bytes and 0<len(raw)<=MAX_JSON for raw in (lock_raw,scan_raw,sbom_raw)),'input_buffer_type')
 lock=document(lock_raw);keys(lock,{'schema','armed','scope','scan','sbom','review','inputs','proofs','required_gates','findings','product'},'lock_shape')
 need(lock['schema']=='image_security_evidence_lock/2'and lock['armed']is True,'lock_unarmed');scope(lock['scope'])
 need(integer(now,0,2**63-1),'clock_type');keys(lock['review'],{'reviewed_at','valid_until'},'review_shape');review=lock['review']
 need(integer(review['reviewed_at'],0,now)and integer(review['valid_until'],now+1,2**63-1)
  and review['valid_until']-review['reviewed_at']<=86400,'security_review_expired')
 need(type(inputs)is dict and type(lock['inputs'])is dict and set(inputs)==set(lock['inputs'])and len(inputs)<=128,'input_inventory')
 total=len(lock_raw)+len(scan_raw)+len(sbom_raw)
 for name,raw in inputs.items():
  need(type(name)is str and NAME.fullmatch(name)and type(raw)is bytes,'input_name');binding(lock['inputs'][name]);total+=len(raw)
  need(total<=MAX_TOTAL and same({'bytes':len(raw),'sha256':sha256(raw).hexdigest()},lock['inputs'][name]),'input_binding')
 for raw,pin in ((scan_raw,lock['scan']),(sbom_raw,lock['sbom'])):
  binding(pin);need(type(raw)is bytes and same({'bytes':len(raw),'sha256':sha256(raw).hexdigest()},pin),'report_binding')
 product=product_profile(lock,inputs)
 rows,counts=findings(document(scan_raw),document(sbom_raw),lock['scope'])
 rules=lock['findings'];need(type(rules)is list and len(rules)<=MAX_ROWS,'rule_bound')
 need(all(type(rule)is dict and set(rule)=={'row','disposition','proofs'}for rule in rules),'rule_shape')
 need(len({encoded(rule['row'])for rule in rules})==len(rules),'duplicate_rule')
 need(Counter(encoded(rule['row'])for rule in rules)==Counter(encoded(row)for row in rows),'finding_coverage')
 descriptors=lock['proofs'];need(type(descriptors)is dict and len(descriptors)<=64,'proof_inventory');proofs={}
 for name,descriptor in descriptors.items():
  keys(descriptor,{'kind','producer','original_inputs'},'proof_descriptor');need(type(name)is str and NAME.fullmatch(name)and type(descriptor['kind'])is str and descriptor['kind']in KINDS,'proof_kind')
  original=descriptor['original_inputs'];need(type(original)is list and 0<len(original)<=64
   and all(type(item)is str and item in inputs for item in original)and len(set(original))==len(original),'original_proof_inputs')
  need(type(descriptor['producer'])is str and descriptor['producer']in inputs and name in inputs,'proof_source')
  proof=document(inputs[name]);keys(proof,{'schema','kind','scope','producer_sha256','original_inputs','completed_at','observations'},'proof_shape')
  need(proof['schema']=='image_security_evidence_proof/1'and proof['kind']==descriptor['kind']and same(proof['scope'],lock['scope'])
   and proof['producer_sha256']==lock['inputs'][descriptor['producer']]['sha256']and same(proof['original_inputs'],{item:lock['inputs'][item]for item in original})
   and integer(proof['completed_at'],max(0,now-86400),now),'proof_binding')
  if descriptor['kind']in {'mandatory_gate','runtime_payload','component_absence','caller_fix'}:
   need(product['input_names']<=set(original),'product_proof_originals')
  proofs[name]=proof
 gates=lock['required_gates'];need(type(gates)is list and len(gates)==4 and all(type(name)is str for name in gates)and len(set(gates))==len(gates),'required_gates')
 for name in gates:
  need(name in proofs and proofs[name]['kind']=='mandatory_gate','required_gate');facts=proofs[name]['observations']
  keys(facts,{'passed','failures','skipped','scope'},'gate_shape');need(facts['passed']is True and type(facts['failures'])is int and facts['failures']==0
   and type(facts['skipped'])is int and facts['skipped']==0 and facts['scope']in PROFILE_GATES[product['profile']],'required_gate_failed')
 need({proofs[name]['observations']['scope']for name in gates}==PROFILE_GATES[product['profile']],'required_gate_coverage')
 payloads={};advisories=set()
 for name,proof in proofs.items():
  facts=proof['observations']
  if proof['kind']=='runtime_payload':payloads[name]=native_packages(facts)
  if proof['kind']=='advisory_review':
   keys(facts,{'official_sources_reviewed','advisory_ids'},'advisory_shape');need(facts['official_sources_reviewed']is True and type(facts['advisory_ids'])is list and all(text(v,64)for v in facts['advisory_ids']),'advisory_review');advisories.update(facts['advisory_ids'])
 need({row['advisory']for row in rows}<=advisories,'advisory_review_missing')
 resolutions=[];used=set(gates)
 for rule in rules:
  row=rule['row'];status=rule['disposition'];names=rule['proofs']
  need(type(status)is str and status in {'unresolved','fixed','component_not_present'}and type(names)is list and len(names)<=16
   and all(type(name)is str and name in proofs for name in names)and len(set(names))==len(names),'disposition')
  if status=='unresolved':need(not names,'unresolved_proofs');resolutions.append({'row':row,'disposition':status});continue
  selected={name:proofs[name]for name in names};used.update(names);native=[]
  for name in selected:
   package=payloads.get(name,{}).get(row['package'])
   if package and all(package[k]==row[k]for k in ('package','installed_version','source_package','source_version','source_release')):native.append(package)
  need(len(native)==1,'native_payload_missing_or_ambiguous')
  if status=='fixed':
   candidates=[p['observations']for p in selected.values()if p['kind']=='source_backport'];valid=0
   for facts in candidates:
    keys(facts,{'advisory_ids','source_package','source_version','source_release','source_verified','build_abi_verified','packages'},'source_shape')
    need(facts['source_verified']is True and facts['build_abi_verified']is True and type(facts['advisory_ids'])is list and facts['advisory_ids']
     and all(type(v)is str and v in SOURCES for v in facts['advisory_ids'])and len(set(facts['advisory_ids']))==len(facts['advisory_ids'])
     and type(facts['packages'])is list and 0<len(facts['packages'])<=128,'source_proof')
    package_names=set()
    for package in facts['packages']:
     keys(package,{'package','installed_version','deb_sha256'},'source_package')
     need(text(package['package'])and text(package['installed_version'])and type(package['deb_sha256'])is str and HEX.fullmatch(package['deb_sha256'])
      and package['package']not in package_names,'source_package');package_names.add(package['package'])
    if (row['advisory']in facts['advisory_ids']and all(facts[k]==row[k]for k in ('source_package','source_version','source_release'))
     and {'package':row['package'],'installed_version':row['installed_version'],'deb_sha256':native[0]['deb_sha256']}in facts['packages']):valid+=1
   need(valid==1,'source_fix_missing_or_ambiguous')
   if row['advisory']=='CVE-2026-54369':
    callers=[p['observations']for p in selected.values()if p['kind']=='caller_fix'];need(len(callers)==1,'acl_caller_fix_missing');facts=callers[0]
    keys(facts,{'whole_image_inventory_complete','all_callers_bound_to_reviewed_source','unresolved_legacy_callers','ordinary_compatibility_passed','callers'},'caller_shape')
    need(facts['whole_image_inventory_complete']is True and facts['all_callers_bound_to_reviewed_source']is True and facts['ordinary_compatibility_passed']is True
     and type(facts['unresolved_legacy_callers'])is int and facts['unresolved_legacy_callers']==0,'acl_callers_unresolved')
    need(type(facts['callers'])is list and 0<len(facts['callers'])<=128,'acl_caller_inventory');caller_paths=set()
    caller_proof=next(p for p in selected.values()if p['kind']=='caller_fix')
    for caller in facts['callers']:
     keys(caller,{'package','path','sha256','reviewed_source_input'},'acl_caller_identity')
     need(all(text(caller[key])for key in caller)and caller['path']not in caller_paths
      and caller['reviewed_source_input']in caller_proof['original_inputs'],'acl_caller_identity');caller_paths.add(caller['path'])
     matches=[file for name in selected for package in payloads.get(name,{}).values()if package['package']==caller['package']
      for file in package['expected_files']if file['path']==caller['path']and file['sha256']==caller['sha256']]
     need(len(matches)==1,'acl_caller_payload_binding')
  else:
   need(row['advisory']in ABSENCE,'absence_advisory_not_supported')
   candidates=[p['observations']for p in selected.values()if p['kind']=='component_absence'];valid=0
   for facts in candidates:
    keys(facts,{'advisory_ids','component','absent','inventory_complete','unreadable_inputs','limit_exceeded','scope'},'absence_shape')
    need(type(facts['advisory_ids'])is list and text(facts['component'],128)and facts['scope']in {'known_vendor_paths_names_and_actual_perl_inc','stable_source_vulnerable_path_absent'},'absence_scope')
    if row['advisory']in facts['advisory_ids']:
     need((facts['component'],facts['scope'])==ABSENCE[row['advisory']],'absence_advisory_scope')
     need(facts['absent']is True and facts['inventory_complete']is True and type(facts['unreadable_inputs'])is int and facts['unreadable_inputs']==0 and facts['limit_exceeded']is False,'component_not_absent');valid+=1
   need(valid==1,'absence_missing_or_ambiguous')
  resolutions.append({'row':row,'disposition':status,'proofs':sorted(names)})
 need(all(name in used or proof['kind']=='advisory_review'for name,proof in proofs.items()),'unused_proof')
 unresolved=sum(row['disposition']=='unresolved'for row in resolutions)
 result={'schema':'image_security_evidence_assessment/2','product_profile':product['profile'],'product_recipe':product['recipe'],'product_role':product['role'],'product_original_inputs':product['original_inputs'],'status':'PASS'if unresolved==0 else 'BLOCKED','scope':lock['scope'],
  'policy_sha256':sha256(lock_raw).hexdigest(),'scan_sha256':sha256(scan_raw).hexdigest(),'sbom_sha256':sha256(sbom_raw).hexdigest(),
  'assessed_at':now,'valid_until':review['valid_until'],'raw_counts':counts,'raw_gate_passed':counts['HIGH']==counts['CRITICAL']==0,
  'unresolved_count':unresolved,'resolutions':resolutions,'scanner_findings_modified':False,'security_accepted':False,'deployment_performed':False}
 need(len(encoded(result))<=2*1024**2,'result_bound')
 need(callable(finalizer),'finalizer_required');closed=finalizer();need(closed is None or closed is True,'finalizer_failed')
 return result
