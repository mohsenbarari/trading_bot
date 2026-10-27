"""Synthetic proof packets only; no real image/native/package/proc operations.

Every success packet uses an obviously synthetic image/release and invented
ordinary public fixture bytes. It is not an acceptance artifact.
"""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import ModuleType
import json,os,tempfile,unittest
HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
A=ModuleType('_owned_assessor');exec(compile((ROOT/'scripts/assess_image_security_evidence.py').read_bytes(),str(ROOT/'scripts/assess_image_security_evidence.py'),'exec'),A.__dict__)
NOW=1800000000
def pin(raw):return {'bytes':len(raw),'sha256':sha256(raw).hexdigest()}
def object_bytes(value):return A.encoded(value)
UTIL=['bsdutils','libblkid1','liblastlog2-2','libmount1','libsmartcols1','libuuid1','login','mount','util-linux']
NCURSES=['libncursesw6','libtinfo6','ncurses-base','ncurses-bin']
CVES=[('CVE-2026-76642','util-linux',UTIL),('CVE-2026-78408','util-linux',UTIL),
 ('CVE-2026-78409','util-linux',UTIL),('CVE-2026-78410','util-linux',UTIL),
 ('CVE-2025-69720','ncurses',NCURSES),('CVE-2026-54369','acl',['libacl1']),
 ('CVE-2026-16742','systemd',['libsystemd0','libudev1']),('CVE-2026-9538','perl',['perl-base'])]
PRODUCT_NAMES={'build':'build-receipt.json','source':'source-identity.json','manifest':'input-manifest.json','recipe':'recipe.original'}

def install_app_product(p):
 recipe=b'# OWNED synthetic recipe metadata; never built or accepted\n'
 row={'path':'Dockerfile','bytes':len(recipe),'sha256':sha256(recipe).hexdigest()}
 p.scope['input_signature']=sha256(('docker_image=trading_bot_base\n'+row['sha256']+'  Dockerfile\n').encode()).hexdigest()
 source={'schema':'canonical_app_source/1','source_commit':p.scope['source_commit'],'source_tree':p.scope['source_tree'],
  'run_id':str(p.scope['build_run_id']),'run_attempt':str(p.scope['build_run_attempt']),'files':[row],'security_accepted':False}
 manifest={'schema':'canonical_app_inputs/1','signature_format':'deploy.sh:foreign_image_signature',
  'source_commit':p.scope['source_commit'],'source_tree':p.scope['source_tree'],'input_signature':p.scope['input_signature'],
  'empty_directories':[],'files':[row],'security_accepted':False}
 build={'schema':'canonical_app_image_build/1','image_id':p.scope['image_id'],'platform':'linux/amd64',
  'source_commit':p.scope['source_commit'],'source_tree':p.scope['source_tree'],'input_signature':p.scope['input_signature'],
  'run_id':source['run_id'],'run_attempt':source['run_attempt'],'activation_performed':False,'market_roles_built':False,
  'dockerfile_checks':['trust','coreutils_acl','python_micro_release','psycopg2','sqlite_libmagic','pip_removed'],'security_accepted':False}
 for key,value in(('build',build),('source',source),('manifest',manifest)):p.inputs[PRODUCT_NAMES[key]]=object_bytes(value)
 p.inputs[PRODUCT_NAMES['recipe']]=recipe

def install_market_product(p,role=None):
 # Only public approved source metadata is reused; image/run/package fixtures
 # remain visibly synthetic and no actual build/profile result is invented.
 recipe=b'# OWNED synthetic Market recipe metadata; never built or accepted\n'
 r={'path':'deploy/market-data/Dockerfile.capture2','bytes':len(recipe),'sha256':sha256(recipe).hexdigest()}
 receipt={'schema':'normal_market_git_component_inputs/1','signature_format':'normal-market-two-git-sources-v1',
  'role':'market-capture-account2','component':A.F79_COMPONENT,'release_commit':p.scope['source_commit'],
  'release_tree':p.scope['source_tree'],'business_commit':A.F79['BUSINESS_COMMIT'],'business_tree':A.F79['BUSINESS_TREE'],
  'repository_identity':{'host':'github.com','repository':'mohsenbarari/trading_bot'},'release_files':[r],
  'accepted_manifest_sha256':A.F79_MANIFEST,'product_files':json.loads((ROOT/'deploy/market-data/accepted-f79-business.manifest.json').read_bytes())['product_files']}
 if role is not None:
  spec=A.MARKET_ROLES[role];catalog=json.loads((ROOT/'deploy/market-data/role-business-components.json').read_bytes())
  locked={'business_commit':spec['business_commit'],'business_tree':spec['business_tree'],'repository_identity':receipt['repository_identity'],
   'role':role,'catalog_sha256':A.MARKET_CATALOG_SHA,'source_policy':'whole_git_component_no_main_fallback'}
  receipt.update(schema='normal_market_git_component_inputs/2',role=role,role_source=spec,role_catalog_sha256=A.MARKET_CATALOG_SHA,
   component=spec['component'],business_commit=spec['business_commit'],business_tree=spec['business_tree'],
   business_manifest_sha256=spec['manifest_sha256'],product_files=catalog['components'][spec['rows_sha256']]['product_files'],
   product_lock_sha256=sha256(object_bytes(locked)).hexdigest())
 p.scope['input_signature']=sha256(object_bytes(receipt)).hexdigest()
 controller={'schema':'normal_market_controller_inputs/1','release_commit':receipt['release_commit'],'release_tree':receipt['release_tree'],
  'two_git_input_signature':p.scope['input_signature']}
 sig=sha256(object_bytes(controller)).hexdigest()
 source={'schema':'normal_market_source_identity/1','inputs':receipt,'input_signature':p.scope['input_signature'],
  'controller':controller,'controller_signature':sig,'security_accepted':False}
 manifest={'schema':'normal_market_two_context_inputs/1','security_accepted':False,'two_git_input_signature':p.scope['input_signature'],'controller_input_signature':sig,
  'release':{r['path']:{k:r[k]for k in('bytes','sha256')}},'product':{r['path']:{k:r[k]for k in('bytes','sha256')}for r in receipt['product_files']}}
 build={'schema':'normal_market_capture2_image_build/1'if role is None else'normal_market_role_image_build/1',
  'image_id':p.scope['image_id'],'source_inputs':receipt,'two_git_input_signature':p.scope['input_signature'],
  'run_id':p.scope['build_run_id'],'run_attempt':p.scope['build_run_attempt'],'status':'built_not_accepted',
  'historical81_business_tests_completed':True,'actual_sqlite_completed':True,'complete_deployment_control_payload':False,
  'actual_source93_completed':True,'controller_inputs':controller,'controller_input_signature':sig,'security_accepted':False}
 if role is not None:build.update(role=role,actual_role_source_completed=build.pop('actual_source93_completed'))
 for key,value in(('build',build),('source',source),('manifest',manifest)):p.inputs[PRODUCT_NAMES[key]]=object_bytes(value)
 p.inputs[PRODUCT_NAMES['recipe']]=recipe
 for proof in p.proofs.values():
  proof['scope']=deepcopy(p.scope)
  for name in PRODUCT_NAMES.values():proof['original_inputs'][name]=pin(p.inputs[name])
 p.proofs['compat.json']['observations']['scope']='normal_market_81_unchanged'
 p.proofs['admission.json']['observations']['scope']='normal_market_source_archive_daemon'

class Packet:
 def __init__(self,*,acl_fixed=True):
  self.scope={'image_id':'sha256:'+'a'*64,'platform':'linux/amd64','source_commit':'b'*40,'source_tree':'c'*40,
   'input_signature':'d'*64,'build_run_id':123,'build_run_attempt':1}
  self.inputs={'producer.py':b'# OWNED SYNTHETIC PRODUCER; NOT EXECUTED\n',
   'original-receipt.json':b'{"owned_fixture":true,"actual_build_or_image":false}\n',
   'reviewed-caller.c':b'/* OWNED SYNTHETIC REVIEWED CALLER SOURCE ONLY */\n'}
  install_app_product(self)
  self.proofs={};self.descriptors={};self.components={};vulns=[];native={}
  for cve,source,names in CVES:
   for name in names:
    purl='pkg:deb/debian/'+name+'@OWNED-SYNTHETIC-1?arch=amd64&distro=debian-13.7'
    self.components[name]={'name':name,'version':'OWNED-SYNTHETIC-1','purl':purl,'bom-ref':purl,
     'properties':[{'name':'aquasecurity:trivy:'+k,'value':v}for k,v in {'SrcName':source,'SrcVersion':'OWNED-SYNTHETIC','SrcRelease':'1','PkgType':'debian','LayerDiffID':'sha256:'+'e'*64}.items()]}
    vulns.append({'VulnerabilityID':cve,'PkgName':name,'InstalledVersion':'OWNED-SYNTHETIC-1',
     'PkgIdentifier':{'PURL':purl},'Severity':'HIGH','Status':'affected','Layer':{'DiffID':'sha256:'+'e'*64}})
    file={'path':'/usr/lib/OWNED-SYNTHETIC-'+name+'.so','bytes':32,'sha256':sha256(('PUBLIC'+name).encode()).hexdigest(),'mode':0o100644,'uid':0,'gid':0}
    native[name]={'package':name,'installed_version':'OWNED-SYNTHETIC-1','source_package':source,'source_version':'OWNED-SYNTHETIC','source_release':'1',
     'deb_sha256':sha256(('OWNED PACKAGE'+name).encode()).hexdigest(),'expected_files':[file],'actual_files':[deepcopy(file)]}
  tar={'path':'/usr/bin/OWNED-SYNTHETIC-tar','bytes':32,'sha256':sha256(b'OWNED TAR ONLY').hexdigest(),'mode':0o100755,'uid':0,'gid':0}
  native['tar']={'package':'tar','installed_version':'OWNED-SYNTHETIC-1','source_package':'tar','source_version':'OWNED-SYNTHETIC','source_release':'1',
   'deb_sha256':sha256(b'OWNED TAR PACKAGE').hexdigest(),'expected_files':[tar],'actual_files':[deepcopy(tar)]}
  self.scan={'Metadata':{'ImageID':self.scope['image_id'],'OS':{'Family':'debian','Name':'13.7'}},'Results':[{'Type':'debian','Target':'OWNED SYNTHETIC DEBIAN TARGET','Vulnerabilities':vulns}]}
  self.sbom={'metadata':{'component':{'type':'container','name':self.scope['image_id'],'properties':[{'name':'aquasecurity:trivy:ImageID','value':self.scope['image_id']}]}},'components':list(self.components.values())}
  for name,kind in [('compat.json','canonical_32_unchanged'),('cleanup.json','owned_cleanup'),('admission.json','canonical_source_archive_daemon'),('sqlite.json','sqlite_native_installed')]:
   self.proof(name,'mandatory_gate',{'passed':True,'failures':0,'skipped':0,'scope':kind})
  self.proof('advisories.json','advisory_review',{'official_sources_reviewed':True,'advisory_ids':[c for c,_,_ in CVES]})
  self.proof('payload.json','runtime_payload',{'inventory_complete':True,'packages':list(native.values())})
  for source in ('util-linux','ncurses','acl'):
   if source=='acl'and not acl_fixed:continue
   self.proof(source+'.json','source_backport',{'advisory_ids':[c for c,s,_ in CVES if s==source],
    'source_package':source,'source_version':'OWNED-SYNTHETIC','source_release':'1','source_verified':True,'build_abi_verified':True,
    'packages':[{k:p[k]for k in ('package','installed_version','deb_sha256')}for p in native.values()if p['source_package']==source]})
  for name,cve,component,scope in [('homed.json','CVE-2026-16742','systemd_homed','known_vendor_paths_names_and_actual_perl_inc'),
   ('perl.json','CVE-2026-9538','archive_tar','known_vendor_paths_names_and_actual_perl_inc'),
   ('subdir.json','CVE-2026-78409','restricted_user_detached_tree_fast_path','stable_source_vulnerable_path_absent')]:
   self.proof(name,'component_absence',{'advisory_ids':[cve],'component':component,'absent':True,'inventory_complete':True,
    'unreadable_inputs':0,'limit_exceeded':False,'scope':scope})
  if acl_fixed:self.proof('callers.json','caller_fix',{'whole_image_inventory_complete':True,'all_callers_bound_to_reviewed_source':True,
   'unresolved_legacy_callers':0,'ordinary_compatibility_passed':True,'callers':[{'package':'tar','path':tar['path'],'sha256':tar['sha256'],'reviewed_source_input':'reviewed-caller.c'}]})
  rows,_=A.findings(self.scan,self.sbom,self.scope);rules=[]
  for row in rows:
   cve=row['advisory'];status='fixed';names=['payload.json',row['source_package']+'.json']
   absent={'CVE-2026-16742':'homed.json','CVE-2026-9538':'perl.json','CVE-2026-78409':'subdir.json'}
   if cve in absent:status='component_not_present';names=['payload.json',absent[cve]]
   if cve=='CVE-2026-54369':
    if acl_fixed:names+=['callers.json']
    else:status='unresolved';names=[]
   rules.append({'row':row,'disposition':status,'proofs':names})
  self.lock={'schema':'image_security_evidence_lock/2','armed':True,'scope':self.scope,'scan':{},'sbom':{},
   'review':{'reviewed_at':NOW-60,'valid_until':NOW+3600},'inputs':{},'proofs':self.descriptors,
   'required_gates':['compat.json','cleanup.json','admission.json','sqlite.json'],'findings':rules,'product':dict(PRODUCT_NAMES)}
 def proof(self,name,kind,observations):
  original=['original-receipt.json','reviewed-caller.c',*PRODUCT_NAMES.values()]
  self.descriptors[name]={'kind':kind,'producer':'producer.py','original_inputs':original}
  self.proofs[name]={'schema':'image_security_evidence_proof/1','kind':kind,'scope':deepcopy(self.scope),
   'producer_sha256':pin(self.inputs['producer.py'])['sha256'],'original_inputs':{key:pin(self.inputs[key])for key in original},
   'completed_at':NOW-10,'observations':observations}
 def buffers(self):
  self.inputs.update({name:object_bytes(proof)for name,proof in self.proofs.items()})
  scan=object_bytes(self.scan);sbom=object_bytes(self.sbom)
  self.lock['scan']=pin(scan);self.lock['sbom']=pin(sbom);self.lock['inputs']={name:pin(raw)for name,raw in self.inputs.items()}
  return object_bytes(self.lock),scan,sbom,dict(self.inputs)
 def run(self,finalizer=lambda:None,**kwargs):return A.assess(*self.buffers(),now=kwargs.get('now',NOW),finalizer=finalizer)

class AssessorTests(unittest.TestCase):
 def rejects(self,packet,code=None):
  with self.assertRaisesRegex(A.Refused,code or '.'):
   packet.run()
 def test_complete_44_row_fixture_resolves_without_rewriting_raw_or_global_acceptance(self):
  p=Packet();before=object_bytes(p.scan);calls=[];r=p.run(lambda:calls.append('closed'))
  self.assertEqual(calls,['closed']);self.assertEqual(r['status'],'PASS');self.assertEqual(r['unresolved_count'],0)
  self.assertEqual(len(r['resolutions']),44);self.assertEqual(r['raw_counts']['HIGH'],44)
  self.assertFalse(r['raw_gate_passed']);self.assertFalse(r['security_accepted']);self.assertEqual(object_bytes(p.scan),before)
 def test_real_acl_gap_stays_blocked_in_synthetic_43_resolved_fixture(self):
  r=Packet(acl_fixed=False).run();self.assertEqual(r['status'],'BLOCKED');self.assertEqual(r['unresolved_count'],1)
  self.assertEqual([x['row']['advisory']for x in r['resolutions']if x['disposition']=='unresolved'],['CVE-2026-54369'])
 def test_unknown_missing_extra_and_duplicate_raw_or_rule_rows_are_denied(self):
  for edit in ('unknown','missing','extra','duplicate','rule_duplicate'):
   p=Packet();rows=p.scan['Results'][0]['Vulnerabilities']
   if edit=='unknown':rows[0]['VulnerabilityID']='CVE-2099-99999'
   elif edit=='missing':rows.pop()
   elif edit=='extra':other=deepcopy(rows[0]);other['VulnerabilityID']='CVE-2026-78410';other['PkgName']='OTHER';rows.append(other)
   elif edit=='duplicate':rows.append(deepcopy(rows[0]))
   else:p.lock['findings'].append(deepcopy(p.lock['findings'][0]))
   with self.subTest(edit=edit):self.rejects(p)
 def test_same_cve_wrong_version_source_layer_purl_or_image_cannot_reuse_rule(self):
  for edit in ('version','source','layer','purl','image','sbom_image'):
   p=Packet();component=p.sbom['components'][0]
   if edit=='version':component['version']='OTHER'
   elif edit=='source':component['properties'][0]['value']='ncurses'
   elif edit=='layer':component['properties'][-1]['value']='sha256:'+'f'*64
   elif edit=='purl':component['purl']='OTHER'
   elif edit=='image':p.scan['Metadata']['ImageID']='sha256:'+'f'*64
   else:p.sbom['metadata']['component']['name']='sha256:'+'f'*64
   with self.subTest(edit=edit):self.rejects(p)
 def test_changed_or_missing_raw_bytes_and_proof_inputs_never_reach_finalizer(self):
  p=Packet();lock,scan,sbom,inputs=p.buffers();calls=[]
  for kind in ('scan','sbom','input','missing','extra'):
   actual=dict(inputs);s=scan;b=sbom
   if kind=='scan':s=scan+b' '
   elif kind=='sbom':b=sbom+b' '
   elif kind=='input':actual['original-receipt.json']+=b' '
   elif kind=='missing':actual.pop('producer.py')
   else:actual['UNKNOWN']=b'X'
   with self.subTest(kind=kind),self.assertRaises(A.Refused):A.assess(lock,s,b,actual,now=NOW,finalizer=lambda:calls.append(True))
  self.assertFalse(calls)
 def test_no_unarmed_lock_or_expired_future_or_boolean_clock_acceptance(self):
  p=Packet();p.lock['armed']=False;self.rejects(p,'lock_unarmed')
  for clock in (True,NOW+86401,NOW-100):
   with self.subTest(clock=clock),self.assertRaises(A.Refused):Packet().run(now=clock)
  p=Packet();p.lock['review']['valid_until']=NOW+86401;self.rejects(p,'security_review_expired')
 def test_current_image_or_producer_or_original_receipt_or_time_drift_in_proof_refuses(self):
  for edit in ('image','producer','original','future','stale'):
   p=Packet();proof=p.proofs['util-linux.json']
   if edit=='image':proof['scope']['image_id']='sha256:'+'f'*64
   elif edit=='producer':proof['producer_sha256']='f'*64
   elif edit=='original':proof['original_inputs']['original-receipt.json']['sha256']='f'*64
   elif edit=='future':proof['completed_at']=NOW+1
   else:proof['completed_at']=NOW-86401
   with self.subTest(edit=edit):self.rejects(p,'proof_binding')
 def test_full_payload_hash_mode_owner_version_and_duplicate_path_must_match(self):
  for edit in ('hash','mode','owner','version','path','empty','incomplete'):
   p=Packet();facts=p.proofs['payload.json']['observations'];item=facts['packages'][0]
   if edit=='hash':item['actual_files'][0]['sha256']='f'*64
   elif edit=='mode':item['actual_files'][0]['mode']=0o100777
   elif edit=='owner':item['actual_files'][0]['uid']=1001
   elif edit=='version':item['installed_version']='OTHER'
   elif edit=='path':facts['packages'][1]['expected_files']=deepcopy(item['expected_files']);facts['packages'][1]['actual_files']=deepcopy(item['actual_files'])
   elif edit=='empty':item['expected_files']=item['actual_files']=[]
   else:facts['inventory_complete']=False
   with self.subTest(edit=edit):self.rejects(p)
 def test_wrong_source_deb_hash_or_false_source_or_abi_cannot_resolve(self):
  for edit in ('deb','source','abi','version','missing'):
   p=Packet();facts=p.proofs['util-linux.json']['observations']
   if edit=='deb':facts['packages'][0]['deb_sha256']='f'*64
   elif edit=='source':facts['source_verified']=False
   elif edit=='abi':facts['build_abi_verified']=1
   elif edit=='version':facts['source_version']='OTHER'
   else:p.lock['findings'][0]['proofs']=['payload.json']
   with self.subTest(edit=edit):self.rejects(p)
 def test_component_absence_cannot_be_inactive_or_unreadable_incomplete_or_wrong_scope(self):
  for key,value in [('absent',False),('inventory_complete',False),('unreadable_inputs',1),('unreadable_inputs',False),('limit_exceeded',True),('component','UNRELATED'),('scope','service_inactive')]:
   p=Packet();p.proofs['homed.json']['observations'][key]=value
   with self.subTest(key=key,value=value):self.rejects(p)
 def test_acl_library_only_or_unreviewed_missing_payload_caller_or_absence_cannot_resolve(self):
  for edit in ('count','inventory','compatibility','payload','source','absent','missing'):
   p=Packet();facts=p.proofs['callers.json']['observations']
   if edit=='count':facts['unresolved_legacy_callers']=1
   elif edit=='inventory':facts['whole_image_inventory_complete']=False
   elif edit=='compatibility':facts['ordinary_compatibility_passed']=False
   elif edit=='payload':facts['callers'][0]['sha256']='f'*64
   elif edit=='source':facts['callers'][0]['reviewed_source_input']='UNKNOWN.c'
   else:
    rule=next(r for r in p.lock['findings']if r['row']['advisory']=='CVE-2026-54369')
    if edit=='absent':rule['disposition']='component_not_present'
    else:rule['proofs'].remove('callers.json')
   with self.subTest(edit=edit):self.rejects(p)
 def test_every_mandatory_gate_has_typed_zero_failures_no_skips_and_complete_coverage(self):
  for edit in ('false','failures','skipped','coverage'):
   p=Packet();facts=p.proofs['compat.json']['observations']
   if edit=='false':facts['passed']=1
   elif edit=='failures':facts['failures']=False
   elif edit=='skipped':facts['skipped']=1
   else:p.lock['required_gates'].pop()
   with self.subTest(edit=edit):self.rejects(p)
 def test_missing_fresh_advisory_review_refuses_even_unchanged_binary_bytes(self):
  p=Packet();p.proofs['advisories.json']['observations']['advisory_ids'].pop();self.rejects(p,'advisory_review_missing')
 def test_duplicate_json_nonfinite_depth_and_oversize_are_refused_before_finalizer(self):
  for raw in (b'{"x":1,"x":2}',b'{"x":NaN}',b'{"x":1e999}',b'{"x":-1e999}',b'['*49+b'0'+b']'*49,b'x'*(A.MAX_JSON+1)):
   with self.subTest(raw=raw[:20]),self.assertRaises(A.Refused):A.document(raw)
 def test_legitimate_repeated_image_diffids_do_not_allow_duplicate_consumed_properties(self):
  p=Packet();top=p.sbom['metadata']['component']['properties']
  top.extend([{'name':'aquasecurity:trivy:DiffID','value':'sha256:'+'e'*64}]*3)
  self.assertEqual(p.run()['status'],'PASS')
  p.sbom['components'][0]['properties'].append(deepcopy(p.sbom['components'][0]['properties'][0]))
  self.rejects(p,'sbom_property')
 def test_repeated_nonfinding_python_location_does_not_admit_ambiguous_finding_component(self):
  p=Packet();p.sbom['components'].extend([{'name':'packaging','version':'OWNED','purl':'pkg:pypi/packaging@OWNED'}]*2)
  self.assertEqual(p.run()['status'],'PASS')
  p.sbom['components'].append(deepcopy(p.sbom['components'][0]));self.rejects(p,'finding_sbom_ambiguous')
 def test_late_actual_owned_close_metadata_change_cannot_return_pass(self):
  with tempfile.TemporaryDirectory(prefix='.owned-finalizer-',dir=HERE)as name:
   path=Path(name)/'PUBLIC';path.write_bytes(b'OWNED PUBLIC INPUT');fd=os.open(path,os.O_RDONLY);before=path.stat().st_mode
   def finalizer():
    os.close(fd);path.chmod(0o400);A.need(path.stat().st_mode==before,'owned_postclose_drift')
   with self.assertRaisesRegex(A.Refused,'owned_postclose_drift'):Packet().run(finalizer)
 def test_false_or_missing_finalizer_never_returns_pass(self):
  with self.assertRaisesRegex(A.Refused,'finalizer_failed'):Packet().run(lambda:False)
  with self.assertRaisesRegex(A.Refused,'finalizer_required'):Packet().run(None)
 def test_raw_layer_cannot_disagree_with_sbom_or_be_missing_or_malformed(self):
  for layer in ({'DiffID':'sha256:'+'f'*64},{},None,{'DiffID':'not-a-digest'},{'DiffID':'sha256:'+'e'*64,'UNKNOWN':'x'}):
   p=Packet();p.scan['Results'][0]['Vulnerabilities'][0]['Layer']=layer
   with self.subTest(layer=layer):self.rejects(p,'finding_layer')
 def test_consistently_renamed_purl_cannot_hide_wrong_package_version_arch_or_distro(self):
  for purl in ('pkg:deb/debian/WRONG-NAME@WRONG-VERSION?arch=arm64',
   'pkg:deb/debian/libacl1@OTHER?arch=amd64&distro=debian-13.7',
   'pkg:deb/debian/libacl1@OWNED-SYNTHETIC-1?arch=arm64&distro=debian-13.7',
   'pkg:deb/debian/libacl1@OWNED-SYNTHETIC-1?arch=amd64&distro=ubuntu-24.04',
   'pkg:deb/debian/libacl1@OWNED-SYNTHETIC-1?arch=amd64&distro=debian-13.7&arch=all',
   'pkg:deb/debian/libacl1@OWNED-SYNTHETIC-1?arch=amd64&distro=debian-13.7&UNKNOWN=1'):
   p=Packet();component=next(c for c in p.sbom['components']if c['name']=='libacl1')
   component['purl']=component['bom-ref']=purl
   vuln=next(v for v in p.scan['Results'][0]['Vulnerabilities']if v['PkgName']=='libacl1');vuln['PkgIdentifier']['PURL']=purl
   rule=next(r for r in p.lock['findings']if r['row']['package']=='libacl1');rule['row']['purl']=rule['row']['bom_ref']=purl
   with self.subTest(purl=purl):self.rejects(p,'purl_')
 def test_real_public_epoch_percent_encoding_and_all_arch_interfaces(self):
  A.deb_purl('pkg:deb/debian/bsdutils@2.41.6-0%2Bdeb13u0%2Bgold1?arch=amd64&distro=debian-13.7&epoch=1',
   'bsdutils','1:2.41.6-0+deb13u0+gold1','debian-13.7')
  A.deb_purl('pkg:deb/debian/ncurses-base@6.6%2B20260608-2~trixie%2Bgold1?arch=all&distro=debian-13.7',
   'ncurses-base','6.6+20260608-2~trixie+gold1','debian-13.7')
  for epoch in ('01','True','-1'):
   with self.subTest(epoch=epoch),self.assertRaisesRegex(A.Refused,'purl_epoch'):
    A.deb_purl('pkg:deb/debian/bsdutils@1?arch=amd64&distro=debian-13.7&epoch='+epoch,'bsdutils','1:1','debian-13.7')

class ProductProfileTests(unittest.TestCase):
 def edited(self,p,key,fn):
  name=PRODUCT_NAMES[key];value=json.loads(p.inputs[name]);fn(value);p.inputs[name]=object_bytes(value)

 def test_two_derived_profiles_preserve_raw44_without_security_authority(self):
  for market in(False,True):
   p=Packet()
   if market:install_market_product(p)
   r=p.run();self.assertEqual(r['status'],'PASS');self.assertEqual(r['raw_counts']['HIGH'],44)
   self.assertEqual(r['product_profile'],'normal_market81'if market else'canonical_app32')
   self.assertFalse(r['raw_gate_passed']);self.assertFalse(r['security_accepted'])
   self.assertEqual(r['product_recipe'],'deploy/market-data/Dockerfile.capture2'if market else'Dockerfile')

 def test_all_nine_published_role_metadata_sets_derive_only_market81(self):
  for role in A.MARKET_ROLES:
   with self.subTest(role=role):
    p=Packet();install_market_product(p,role);lock,_,_,raw=p.buffers()
    derived=A.product_profile(A.document(lock),raw)
    self.assertEqual(derived['profile'],'normal_market81');self.assertEqual(derived['role'],role)

 def test_cross_product_gate_sets_cannot_replace_each_other(self):
  p=Packet();p.proofs['compat.json']['observations']['scope']='normal_market_81_unchanged'
  with self.assertRaisesRegex(A.Refused,'required_gate_failed'):p.run()
  p=Packet();install_market_product(p);p.proofs['compat.json']['observations']['scope']='canonical_32_unchanged'
  with self.assertRaisesRegex(A.Refused,'required_gate_failed'):p.run()

 def test_profile_or_waiver_cannot_be_caller_selected(self):
  for extra in('profile','allow_raw_findings','required_gates','authenticated'):
   p=Packet();p.lock['product'][extra]='normal_market81'
   with self.subTest(extra=extra),self.assertRaisesRegex(A.Refused,'product_shape'):p.run()
  p=Packet();p.lock['schema']='image_security_evidence_lock/1'
  with self.assertRaisesRegex(A.Refused,'lock_unarmed'):p.run()

 def test_build_scope_types_and_actual_recipe_bytes_must_bind(self):
  for key,value in(('image_id','sha256:'+'f'*64),('source_commit','f'*40),('source_tree','f'*40),
   ('input_signature','f'*64),('run_id',123),('run_attempt',True),('market_roles_built',True),('schema','invented_builder/1')):
   p=Packet();self.edited(p,'build',lambda b:b.update({key:value}))
   with self.subTest(key=key),self.assertRaises(A.Refused):p.run()
  p=Packet();p.inputs[PRODUCT_NAMES['recipe']]+=b'changed'
  with self.assertRaisesRegex(A.Refused,'product_recipe_binding'):p.run()

 def test_source_and_input_manifest_cannot_claim_other_git_or_recipe(self):
  for key in('source','manifest'):
   p=Packet();self.edited(p,key,lambda b:b.update(source_tree='f'*40))
   with self.subTest(key=key),self.assertRaises(A.Refused):p.run()
  p=Packet();self.edited(p,'manifest',lambda b:b.update(security_accepted=True))
  with self.assertRaisesRegex(A.Refused,'product_build_binding'):p.run()
  p=Packet();self.edited(p,'source',lambda b:b['files'][0].update(path='deploy/market-data/Dockerfile.capture2'))
  with self.assertRaisesRegex(A.Refused,'product_recipe_binding'):p.run()
  p=Packet();self.edited(p,'manifest',lambda b:b['files'].append(dict(b['files'][0])))
  with self.assertRaisesRegex(A.Refused,'product_file_inventory'):p.run()
  p=Packet();self.edited(p,'manifest',lambda b:b.update(empty_directories=[['invalid']]))
  with self.assertRaisesRegex(A.Refused,'product_app_manifest'):p.run()

 def test_market_role_and_controller_signature_have_no_fallback(self):
  for value in('native/unknown','market-capture-account2',None):
   p=Packet();install_market_product(p,'native/coin-estimator');self.edited(p,'build',lambda b:b.update(role=value))
   with self.subTest(role=value),self.assertRaisesRegex(A.Refused,'product_market_role'):p.run()
  p=Packet();install_market_product(p);self.edited(p,'build',lambda b:b['controller_inputs'].update(release_tree='f'*40))
  with self.assertRaisesRegex(A.Refused,'product_controller_signature'):p.run()
  p=Packet();install_market_product(p);self.edited(p,'source',lambda b:b.update(controller_signature='f'*64))
  with self.assertRaisesRegex(A.Refused,'product_market_source'):p.run()

 def test_every_current_image_proof_retains_original_product_documents(self):
  for name in('compat.json','payload.json','homed.json','callers.json'):
   p=Packet();p.proofs[name]['original_inputs'].pop(PRODUCT_NAMES['build'])
   p.descriptors[name]['original_inputs'].remove(PRODUCT_NAMES['build'])
   with self.subTest(name=name),self.assertRaisesRegex(A.Refused,'product_proof_originals'):p.run()

 def test_original_product_substitution_never_reaches_finalizer(self):
  p=Packet();lock,scan,sbom,raw=p.buffers();calls=[]
  for key in PRODUCT_NAMES.values():
   changed=dict(raw);changed[key]+=b' '
   with self.subTest(name=key),self.assertRaisesRegex(A.Refused,'input_binding'):
    A.assess(lock,scan,sbom,changed,now=NOW,finalizer=lambda:calls.append('should-not-close'))
  self.assertFalse(calls)

if __name__=='__main__':unittest.main()
