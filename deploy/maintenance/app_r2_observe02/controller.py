"""Fixed AppR2 public archive/ELF/policy observation on disposable hosted CI.

The separately typed Q02 trusted compile probe may start only its fixed public
readonly actor. Producer02.execute, native/caller acceptance, source-flags review
and the original32 application assertions remain uninvoked.
"""
from contextlib import ExitStack
from hashlib import sha256,sha1
from pathlib import Path
from types import ModuleType
import argparse,json,os,re,stat,time
CONFIRM='OBSERVE-ONLY-APP-R2-Q02-HELPERS-AND-SINGLE-CACHE-20261028-01'
PRODUCER_PIN=(57553,'9de2b86dedbb773351b042bcd3177ebb41a88272cfd76e13f24de5ece6d49a25')
OLD_PIN=(68163,'b2c958060d176c0098b86a44d0dc225d4494023120b3253239bef9e2f3f2acb2')
EXPORT_PIN=(15009, '965c335b56b852ccb9d1aa512e0d19fb91d0198a431c0e685aaa8c4c037f380e')
ARCHIVE_PIN=(16581, '05780cb0566e7e9c77395eaef84c039190fa610befc46437ea1daf5204bddd23')
INVENTORY_PIN=(27509, 'e8171fb6d28a5b3e938e0136dab230c13a9e864f66aad4c2727878e22a3ea73f')
CACHE_PROBE_PIN=(18483, '24d84685ba591717c9f918c41e0bcb51eab075721ba0766a1514a5565fb59f72')
POLICY_PIN=(3264, '358de5ab355c73d5cc803d4b64da5b17b3b1460940303065f3893a692481a7aa')
BUILDER_PIN=(33828,'86eac6a326237e7a8758c5ca50357db2d8717484ba983cfcda7b72b8bc9053c0')
GIT_DOCUMENT_PIN=(656,'f88d042d8a4dbe05f6e636635f60d7aa0abdc947bcfd84b4c430a9014cec8b30')
GIT_WORKFLOW_PIN=(11885,'9d68c472faf4aa4dcb1c5770d0eb4a69bb2048658b21f23fe52ff1069e3d0144')
SBOM_SHA256='ad43d3e61763ab49b6365fee657804a633a57d0fd63a6c52924b5c240ce6cf53'
CONTROL = {'source_commit': '6cefc83c5c2da812d99c549e383ada2e43cd722d', 'source_tree': '1b763730f0b85f5e515c914d8b229f588275c61b', 'image_id': 'sha256:f685744c572f14025fe81ab8f2b1d5371a23d7e19fd9e572e3c6ab83d53effc7', 'rootfs': {'Layers': ['sha256:a70f6272827f64e1427d048ced77491aab5d64ab95725e56920802ff391197d7', 'sha256:cc28e9b8727bd558eb76d69889c971452f06f6f6c91bb2e56b786e46e276de0f', 'sha256:c8624fffa1d06e06d3bc5dcc16e8da97838cd0279166bda3a2086e48d9d6c5bc', 'sha256:95e5c677aa457a1c20a4e8cebe2559c1d572003dfb9ad923e260ae20c09a9195', 'sha256:8d70decb824806060240d7390b358610bbebb48f56b04231dc0a9d56dd5432ba', 'sha256:bea897daf9571b7771e91e5f2edba2fbf4fa27aa5a64453f61efd93284b9a766', 'sha256:40cb10af13a1b55ceee9e150efd7b6e82a39ee5a70a264972ba5ed1d6b37f30e', 'sha256:806c08f3735e3133950e2965981c8a6486365f1fcc94cbd9e34c30ff3458f164', 'sha256:2b615c2646aa4dddb05291f9c5cdea6a4b3b9fe5f923697113ec0b1d999383be', 'sha256:6e10e823408b23c0f05c4179955111a20a040ea4d631c0018dbee1846dcc1222', 'sha256:5a1e39975310710a921d163eb79a16d8f2531964031d037ca78155a7771f2a2c', 'sha256:e88167cca7f1e7d364752908c9f0bb6e5f94872dc8bbee855f9844bf3c918e43', 'sha256:0df47d6e5ac0cb7008f37d9f62dccc69f053b35e319092b7d4e9f66ca0c526d8', 'sha256:35bfad11e4fdd2cdb57949c612e2c2e0f2b211be2be92ce758f41b3028c5ce3f', 'sha256:39a97ef1a298146c7d4f45a488fac7edcd5ac467e2ca65f276a3bbdddbfd49be', 'sha256:cefd777ba01125c4797af5d7e6417588cc7b1b6add13dd5b791c74040db4adc0', 'sha256:e96db2cc210c9c1507fb6ac82be2f369fd5a9dc5040b1d5ac1528563024b8a15', 'sha256:f6866ab1e12dd3d8fa44086060f2aff1d63ed6489eeda1203998bf810d0153e3', 'sha256:31b2452c6a4fb5f60c7545d1559ffd64828b9f5d4247465a03c89c6f7d1761c0', 'sha256:cda49073c4a9165f37023a85f4535f0a39f46ccd7cdec476d18bac7f578b9f5d', 'sha256:58c3f2037fe93822ffe3690a0e0746c1b43ccf05ff46409231fee071becdf702', 'sha256:cabc19f2204112ef2e03e867a8fd8c896a455d2de6afef0d7771f91b501fab53', 'sha256:2b395c7f440ec5acaf6b9910a9b5f56bd8a0ec9e3fc8136838a3a0c63a8a0dd1', 'sha256:4389e3577e14d302a528af7449faebab00588e5da9f13a489b2fdc8f30aba503', 'sha256:3fa61042136a01832b04b80c7f89b47812339948d46e59d90ef684b16abe6044', 'sha256:7b8959105ab6e88de0bb801fa767a88eb2ba331ff32e75b1a1db1a0663d5d5ff', 'sha256:8c3b90c0b745aab4e00434bee3921a78e079456ed3c7a2917599e8c6674b491f', 'sha256:0f9086e1a2644a7d2a3793cea8c5990d55e21a0c0e0bd47b5b86a7ae209d29b4', 'sha256:094e83b2e3c47cbbb169d5ac59f3314b4f60cf3364ebf9cb5643ace662830b58', 'sha256:dd939ffb2dc62e0e320363cfa1f7999a13e584fb3d229f19b105bd45e7385520', 'sha256:5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef', 'sha256:5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef', 'sha256:5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef', 'sha256:5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef', 'sha256:0ce2d84bca220328b7b49e2b00f0201fe57dc74a097373198f69697554aaa27c', 'sha256:0962a59ac8a004c556694ec1d20ee4ff07861c004f68023112036ac558a9443d', 'sha256:d326e3769445c432b01c92408414809d41261507f33e47045ff2f428f1d3dca5'], 'Type': 'layers'}, 'input_signature': '42800179feadc3ce9ccc1dc6e94094b58ad4bba3350a129fb9a2a294f8259f9b', 'run_id': 40550906521, 'run_attempt': 1, 'raw_counts': {'CRITICAL': 0, 'HIGH': 44, 'MEDIUM': 68, 'LOW': 82, 'UNKNOWN': 1}, 'evidence_artifact_id': 12755002602, 'evidence_zip_bytes': 359271, 'evidence_zip_sha256': 'eeec4936373a3738589e021beec939597d7b556ee6708248382eee4d7634a2b8', 'image_artifact_id': 12754319870, 'image_zip_bytes': 192666642, 'image_zip_sha256': 'f9dba235795e095403bd054a8dc5236ca2bfd22bc1957e6d48a747cf3e064d6a', 'archive_observation': {'archive_bytes': 476675072, 'archive_sha256': 'ec813db4bb41f525d4f384a2fb1f1650cd181020cec64ea9d897536f943a4a2a', 'expanded_layer_bytes': 476583424, 'index_sha256': 'b2eb0dec4ffdecfe9ab56e25d101c4af9d666381fb178b476a909b4b2f17d333', 'legacy_compatibility_configs': 37, 'manifest_id': 'sha256:ddf51e959cff1e5d140787afdd1f7c1d2a53877b458353f0b707bc9644991a20', 'member_count': 78}, 'recipe_pin': {'bytes': 5812, 'sha256': '54447fdee488e17246238796cb5e6778a382227a51399256da0b22eeb147178e'}, 'image_source_pins': {'scripts/prepare_canonical_app_image.py': {'bytes': 33828, 'sha256': '86eac6a326237e7a8758c5ca50357db2d8717484ba983cfcda7b72b8bc9053c0'}}}
PHASE='unarmed'
CODES=frozenset(('explicit_action','fixed_R2_control','outer_keeper_required','source_pin','R2_Git_source_binding','R2_Git_source_scope','R2_Git_source_inventory','R2_Git_blob','R2_Git_original_match','observation_export_missing','observation_export_changed','observation_scope','owned_cleanup_unproven','cleanup_report_unproven','unexpected_failure','hosted_ci_only','inner_source_callback_bound'))
def need(ok,code):
 if not ok:raise RuntimeError(code)
def encoded(value):return(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
def source(old,root,stack,checks,name,pin):
 raw,check=old.sealed(stack,root/'source'/name,pin[1],131072);need(len(raw)==pin[0],'source_pin');checks.append(check);return raw

def hosted(root):
 need(os.getuid()==os.geteuid()==1001 and os.environ.get('GITHUB_ACTIONS')=='true'and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted'and os.environ.get('GITHUB_REPOSITORY')=='mohsenbarari/trading_bot'and os.environ.get('GITHUB_REF')=='refs/heads/main','hosted_ci_only')
 parent=Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
 need(root.is_absolute()and root.resolve(strict=True)==root and root.parent==parent and root.name.startswith('security-app-ci-smoke-r2-observe02-')and root.stat().st_uid==1001 and stat.S_IMODE(root.stat().st_mode)==0o700 and not parent.stat().st_mode&0o022,'hosted_ci_only')
def bootstrap_module(root,stack,name,pin):
 path=root/'source'/name;fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC);stack.callback(os.close,fd)
 identity=lambda x:(x.st_dev,x.st_ino,x.st_mode,x.st_uid,x.st_gid,x.st_nlink,x.st_size,x.st_mtime_ns,x.st_ctime_ns)
 before=identity(os.fstat(fd));need(before[2:7]==(0o100600,os.geteuid(),os.getegid(),1,pin[0])and 0<pin[0]<=131072,'source_pin');raw=os.pread(fd,pin[0]+1,0)
 def check():need(identity(os.fstat(fd))==before==identity(path.lstat())and os.pread(fd,pin[0]+1,0)==raw,'source_pin')
 need((len(raw),sha256(raw).hexdigest())==pin,'source_pin');check();stack.callback(check)
 m=ModuleType('_Q_bootstrap_'+name[:-3]);m.__file__=str(path);exec(compile(raw,str(path),'exec'),m.__dict__);return m,check

def r_sources(C,raw_source,supplement,workflow,builder):
 need((len(supplement),sha256(supplement).hexdigest())==GIT_DOCUMENT_PIN and(len(workflow),sha256(workflow).hexdigest())==GIT_WORKFLOW_PIN and(len(builder),sha256(builder).hexdigest())==BUILDER_PIN,'R2_Git_source_binding')
 doc=C.document(supplement);need(type(doc)is dict and set(doc)=={'schema','repository','source_commit','source_tree','files'}and doc['schema']=='root_authenticated_image_controller_sources/1'and doc['repository']=='mohsenbarari/trading_bot'and doc['source_commit']==CONTROL['source_commit']and doc['source_tree']==CONTROL['source_tree'],'R2_Git_source_scope')
 wanted={'.github/workflows/canonical-app-image.yml':workflow,'scripts/prepare_canonical_app_image.py':builder};rows=doc['files'];need(type(rows)is list and len(rows)==2 and{r.get('path')for r in rows}==set(wanted),'R2_Git_source_inventory')
 original=C.document(raw_source);mapping={r['path']:r for r in original['files']}
 for row in rows:
  need(type(row)is dict and set(row)=={'path','git_mode','git_blob','bytes','sha256'}and row['git_mode']=='100644','R2_Git_source_inventory');raw=wanted[row['path']]
  need((row['bytes'],row['sha256'])==(len(raw),sha256(raw).hexdigest())and row['git_blob']==sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest(),'R2_Git_blob')
  if row['path']in mapping:need(all(mapping[row['path']][k]==row[k]for k in('bytes','sha256','git_blob','git_mode')),'R2_Git_original_match')
  else:need(row['path']=='.github/workflows/canonical-app-image.yml','R2_Git_original_match')
 return {'schema':'App_R2_original_Git_source_observation/1','source_commit':doc['source_commit'],'source_tree':doc['source_tree'],'supplement':{'bytes':len(supplement),'sha256':sha256(supplement).hexdigest()},'original_source_document_modified':False,'workflow_documentary_supplement_only':True,'security_accepted':False}

def image_scope(control):
 return {**{k:control[k]for k in('image_id','source_commit','source_tree','input_signature')},'platform':'linux/amd64','build_run_id':control['run_id'],'build_run_attempt':control['run_attempt']}

def validate(C):
 need(type(CONTROL)is dict and set(CONTROL)==C.CONTROL_KEYS,'fixed_R2_control')
 need(CONTROL['image_id']=='sha256:f685744c572f14025fe81ab8f2b1d5371a23d7e19fd9e572e3c6ab83d53effc7'and CONTROL['source_commit']=='6cefc83c5c2da812d99c549e383ada2e43cd722d'and CONTROL['source_tree']=='1b763730f0b85f5e515c914d8b229f588275c61b'and CONTROL['run_id']==40550906521 and type(CONTROL['run_id'])is int and CONTROL['run_attempt']==1 and type(CONTROL['run_attempt'])is int,'fixed_R2_control')
 need(CONTROL['image_artifact_id']==12754319870 and CONTROL['image_zip_bytes']==192666642 and CONTROL['image_zip_sha256']=='f9dba235795e095403bd054a8dc5236ca2bfd22bc1957e6d48a747cf3e064d6a'and CONTROL['evidence_artifact_id']==12755002602 and CONTROL['evidence_zip_bytes']==359271 and CONTROL['evidence_zip_sha256']=='eeec4936373a3738589e021beec939597d7b556ee6708248382eee4d7634a2b8','fixed_R2_control')
 need(CONTROL['archive_observation']['archive_bytes']==476675072 and CONTROL['archive_observation']['expanded_layer_bytes']==476583424 and CONTROL['archive_observation']['member_count']==78 and CONTROL['archive_observation']['archive_sha256']=='ec813db4bb41f525d4f384a2fb1f1650cd181020cec64ea9d897536f943a4a2a','fixed_R2_control')

def execute(ctx,old,C,builder,export,A,I,P,H,git_raw,workflow_raw,builder_raw):
 global PHASE
 control=CONTROL;PHASE='original_R2_evidence';folder=ctx.root/'evidence';folder.mkdir(mode=0o700);path=folder/'public-evidence.zip'
 ctx.download(control['evidence_artifact_id'],path,control['evidence_zip_bytes'],control['evidence_zip_sha256'])
 with ctx.C.pinned(path,control['evidence_zip_bytes'])as(stream,size):
  need(size==control['evidence_zip_bytes']and ctx.C.stream_digest(stream,size)==control['evidence_zip_sha256'],'fixed_R2_control');stream.seek(0);raws=C.read_zip(stream,C.EVIDENCE_NAMES);evidence=C.admit_evidence(raws,control,builder)
 need(sha256(raws['reports/sbom.cdx.json']).hexdigest()==SBOM_SHA256,'fixed_R2_control')
 provenance=r_sources(C,raws['source-identity.json'],git_raw,workflow_raw,builder_raw);ctx.write(ctx.root/'reports/original-R-Git-source-observation.json',provenance)
 ctx.write(ctx.root/'reports/source-binding.json',{'schema':'App_R2_public_observation_source_binding/1','image_id':control['image_id'],'image_scope':C.image_scope(control),'collector_commit':os.environ['GITHUB_SHA'],'product_originals':{'build':evidence['image-build.json'],'source':evidence['source-identity.json'],'manifest':evidence['input-manifest.json'],'recipe':control['recipe_pin']},'raw_counts':control['raw_counts'],'runtime_assertions_executed':0,'security_accepted':False})
 PHASE='canonical_archive_and_daemon_admission';image=C.admit_app(ctx,old,control,evidence)
 PHASE='never_started_export_and_whole_ELF';ctx.capacity(export.MAX_EXPORT+export.MAX_REPORT)
 inventory=export.whole_image_inventory(ctx,old,image,{**control,'sbom_sha256':SBOM_SHA256},raws['reports/sbom.cdx.json'],I=I,T=ctx.T,A=A,adapter_sha256=ARCHIVE_PIN[1])
 need(inventory.get('schema')=='image_acl_archive_inventory/1'and inventory.get('inspected_code_executed')is False and inventory.get('whole_image_acl_closed')is False and inventory.get('security_accepted')is False,'observation_scope')
 exports=[row for row in ctx.C.PROOF_KEEPER.records if isinstance(row,export.ExportFile)and row.path==ctx.root/'whole-image-export.tar'];need(len(exports)==1 and exports[0].frozen,'observation_export_missing');output=exports[0];output.check()
 PHASE='fixed_public_documentation_facts'
 with output.borrow(ctx.check)as stream:policy=P.collect(A,stream,output.count,image_id=control['image_id'],source_commit=control['source_commit'],export_sha256=output.expected_sha256,check=ctx.check,end=min(ctx.end,time.monotonic()+599))
 output.check();ctx.write(ctx.root/'reports/public-doc-policy-facts.json',policy)
 PHASE='trusted_single_cache_compile_probe';probe=H.run(ctx,old,export,image);ctx.check()
 ctx.write(ctx.root/'reports/collection.json',{'schema':'App_R2_Q02_inventory_policy_and_single_cache_collection/1','image_scope':C.image_scope(control),'whole_ELF_inventory':{'bytes':len(export.encode(inventory)),'sha256':sha256(export.encode(inventory)).hexdigest()},'public_policy_facts':{'bytes':len(old.encode(policy)),'sha256':sha256(old.encode(policy)).hexdigest()},'runtime_assertions_executed':0,'trusted_compile_probes_executed':1,'image_container_started':True,'flat_export_container_started':False,'inspected_source_or_cache_executed':False,'helper_import_inventory_source':{'bytes':INVENTORY_PIN[0],'sha256':INVENTORY_PIN[1]},'policy_origin_layer_collected':False,'policy_absence_authority_created':False,'source_flags_review_created':False,'full_P_proof_created':False,'security_accepted':False})

PUBLIC_CLASSES=frozenset(('RuntimeError','Refusal','OSError','FileNotFoundError','PermissionError','TimeoutError','ValueError','TypeError','KeyError','BadZipFile'))
LAST_FAILURE_RECORD=None

def closed_error(error,allowed):
 code=error.args[0]if isinstance(error,RuntimeError)and len(error.args)==1 and type(error.args[0])is str else None
 return {'code':code if code in allowed else'unexpected_failure','exception_class':type(error).__name__ if type(error).__name__ in PUBLIC_CLASSES else'unclassified_exception','os_errno':error.errno if isinstance(error,OSError)and type(error.errno)is int and 0<error.errno<=4095 else None}

def allowed_codes(old,ctx):
 allowed=set(CODES)
 if old is not None:allowed.update(old.FAILURE_CODES)
 if ctx is not None:
  for module in ctx.modules.values():
   for name in('PUBLIC_FAILURE_CODES','PUBLIC_CODES','CODES','FLAT_FAILURE_CODES'):allowed.update(getattr(module,name,()))
 return allowed

class ObservationSources:
 """The two fixed inner source APIs, retaining every close failure as enums.

 This collector only supplies callback/enter_context/close to the pinned old
 source readers. It has no admission, operation or completion authority.
 """
 def __init__(self,classify):
  self.stack=ExitStack();self.classify=classify;self.failures=[];self.count=0;self.closed=False
 def callback(self,function,*args,**kwargs):
  need(not self.closed and self.count<32,'inner_source_callback_bound');index=self.count;self.count+=1
  def finish():
   try:function(*args,**kwargs)
   except BaseException as error:self.failures.append({'stage':'inner_source_finalizer','index':index,**self.classify(error)})
  self.stack.callback(finish)
 def enter_context(self,manager):
  # Only ctx.C.pinned's synchronous generator context is used by admit_app.
  need(not self.closed and self.count<32,'inner_source_callback_bound')
  value=type(manager).__enter__(manager)
  self.callback(type(manager).__exit__,manager,None,None,None);return value
 def close(self):
  if not self.closed:
   try:self.stack.close()
   finally:self.closed=True


def main(argv=None,*,outer_keeper=None):
 global PHASE,LAST_FAILURE_RECORD
 PHASE='unarmed';LAST_FAILURE_RECORD=None
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--execute-fixed-R2-observation',action='store_true');p.add_argument('--confirm');args=p.parse_args(argv)
 if not args.execute_fixed_R2_observation:print(json.dumps({'status':'UNARMED','collection_performed':False,'security_accepted':False}));return 0
 PHASE='source_admission';ctx=None;old=None;primary=None;cleanup_errors=[];secondary=[];sources=None;failure_phase=None
 try:
  need(args.confirm==CONFIRM,'explicit_action');hosted(args.root)
  need(outer_keeper is not None and outer_keeper.root==args.root and not outer_keeper.closed,'outer_keeper_required')
  sources=ObservationSources(lambda error:closed_error(error,allowed_codes(old,ctx)))
  old,old_check=bootstrap_module(args.root,sources,'run_app_image_ci_smoke.py',OLD_PIN)
  ctx=old.Controller(args.root,sources);ctx.C.PROOF_KEEPER=outer_keeper;ctx.checks.extend((old_check,outer_keeper.check))
  source(old,args.root,sources,ctx.checks,'run_app_image_ci_smoke.py',OLD_PIN)
  def load(name,pin):
   raw=source(old,args.root,sources,ctx.checks,name,pin);module=ModuleType('_Q_'+name[:-3]);module.__file__=str(args.root/'source'/name);exec(compile(raw,module.__file__,'exec'),module.__dict__);ctx.modules[name]=module;return module
  C=load('producer02_controller.py',PRODUCER_PIN);validate(C);C.hosted(args.root)
  builder=load('prepare_canonical_app_image.py',BUILDER_PIN);export=load('export_controller.py',EXPORT_PIN);A=load('archive_inventory.py',ARCHIVE_PIN);I=load('inventory.py',INVENTORY_PIN);P=load('public_policy_facts.py',POLICY_PIN);H=load('cache_probe.py',CACHE_PROBE_PIN)
  git_raw=source(old,args.root,sources,ctx.checks,'image-controller-sources.json',GIT_DOCUMENT_PIN);workflow_raw=source(old,args.root,sources,ctx.checks,'original-R-workflow.yml',GIT_WORKFLOW_PIN);builder_raw=source(old,args.root,sources,ctx.checks,'prepare_canonical_app_image.py',BUILDER_PIN)
  ctx.D.legacy_configs=lambda config,moby:C.canonical_legacy_configs(config,moby,ctx.D)
  execute(ctx,old,C,builder,export,A,I,P,H,git_raw,workflow_raw,builder_raw)
 except BaseException as error:primary=error;failure_phase=PHASE
 # Cleanup runs with the original sources still open, including after a partial
 # controller admission. All original owned cleanup guards remain unchanged.
 if ctx is not None:
  PHASE='owned_container_cleanup'
  try:cleanup_errors=old.cleanup(ctx)
  except BaseException as error:
   cleanup_errors=['owned_cleanup_unproven'];secondary.append({'stage':'owned_cleanup',**closed_error(error,allowed_codes(old,ctx))})
  try:ctx.write(ctx.root/'reports/cleanup.json',{'owned_cleanup_complete':not cleanup_errors,'closed_causes':cleanup_errors,'primary_failure_retained':primary is not None,'security_accepted':False})
  except BaseException as error:secondary.append({'stage':'cleanup_publication',**closed_error(error,allowed_codes(old,ctx))})
  try:ctx.check()
  except BaseException as error:secondary.append({'stage':'inner_preclose_check',**closed_error(error,allowed_codes(old,ctx))})
 PHASE='inner_source_finalization'
 if sources is not None:sources.close()
 finalizers=[]if sources is None else sources.failures
 if primary is None and not cleanup_errors and not secondary and not finalizers:
  try:outer_keeper.check()
  except BaseException as error:secondary.append({'stage':'outer_keeper_post_inner_close',**closed_error(error,allowed_codes(old,ctx))})
 if primary is None and not cleanup_errors and not secondary and not finalizers:
  print(json.dumps({'status':'OBSERVED_NOT_ACCEPTED','runtime_assertions_executed':0,'policy_origin_layer_collected':False,'security_accepted':False}));return 0
 # Physical failures take the top diagnostic position, while the original
 # cause and every closed cleanup/finalizer failure remain separately visible.
 selected=finalizers[0]if finalizers else secondary[0]if secondary else closed_error(RuntimeError('owned_cleanup_unproven'),CODES)if cleanup_errors else closed_error(primary,allowed_codes(old,ctx))
 record={'schema':'App_R2_public_observation_failure/2','phase':failure_phase or PHASE,**{k:selected[k]for k in('code','exception_class','os_errno')},'primary_failure':None if primary is None else closed_error(primary,allowed_codes(old,ctx)),'cleanup_causes':cleanup_errors,'secondary_failures':secondary,'inner_finalizer_failures':finalizers,'inner_sources_physically_closed':sources is not None and sources.closed and not finalizers,'security_accepted':False}
 LAST_FAILURE_RECORD=record
 if ctx is not None:
  try:ctx.write(ctx.root/'reports/failure.json',record)
  except BaseException as error:record['secondary_failures'].append({'stage':'failure_publication',**closed_error(error,allowed_codes(old,ctx))})
 print(json.dumps(record,sort_keys=True));return 1
if __name__=='__main__':raise SystemExit(main())
