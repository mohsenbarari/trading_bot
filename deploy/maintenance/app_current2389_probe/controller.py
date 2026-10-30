"""Default-unarmed hosted proof of the exact public2389 image.

Only the new intake is authority. Existing producer globals, historical profiles,
images, fixtures and32 assertion bodies remain unchanged. No action on import.
"""
from contextlib import ExitStack
from hashlib import sha256
from pathlib import Path
from types import ModuleType
import argparse,errno,fcntl,json,os,re,stat,time,zipfile
CONFIRM='RUN-ONLY-CURRENT2389-MISSING-CVE-PROBES-AND-UNCHANGED32-20261030-01'
GOLD2_EXPORT_PIN=(27863, '5480efd9fb0a5443e19262323821f917c57e2168f4aec6151bc44c27ecae95ad')
GOLD2_INVENTORY_PIN=(33083, '04a58e38792d9c5e11ac3ee37db593b6a624b059f0378d0ff2bf6aa560dff005')
PHASE='unarmed'
LAST_FAILURE_RECORD=None
PINS={'admit_security_component_image_reuse.py': (27518, '9adddae3d6966443f33040f54a57ce597a1816af918e8c606557801d27bf4025'), 'archive_inventory.py': (16581, '05780cb0566e7e9c77395eaef84c039190fa610befc46437ea1daf5204bddd23'), 'compose_app_mountpoint_image.py': (49042, '14849bd8c616b27ade1c8488b8e6f276b369480456d0acc1a8c8eb4284f40226'), 'compose_market_security_os_candidate.py': (52231, 'c53cfd7ae4570314ef7f13d43ecd73de26436cf56407676ac2fe24d19dc1caa9'), 'compose_security_os_candidate.py': (60894, '4156ad042885bc7104c5a9ce537193adf2211ade8447e77676ad29d520e7633d'), 'datastore_candidate_contract.py': (38383, '253f234f2ba68dea6929e16128d8dcb48021cda7bf71ffd004ecb8e709c575ee'), 'export_controller.py': (15009, '965c335b56b852ccb9d1aa512e0d19fb91d0198a431c0e685aaa8c4c037f380e'), 'inventory.py': (27509, 'e8171fb6d28a5b3e938e0136dab230c13a9e864f66aad4c2727878e22a3ea73f'), 'prepare_canonical_app_image.py': (33828, '403e95c4f59a4f22c6154f5ebddfd8d6f77b1b3a9c84fff4f7ab84a10ae698f3'), 'producer02_controller.py': (57553, '9de2b86dedbb773351b042bcd3177ebb41a88272cfd76e13f24de5ece6d49a25'), 'run_app_image_ci_smoke.py': (68163, 'b2c958060d176c0098b86a44d0dc225d4494023120b3253239bef9e2f3f2acb2'), 'runtime_image_archive_transport.py': (35148, '20c4b90f1c719d4cdee69df8ad1c31c2bcd663b99619b1d58dcbafa1eafecad2'), 'test_security_form_parser.py': (3910, 'de0090351837742ba6b0be747f0b586d4943590831215a4b4ea2619ebd5149fb'), 'test_stage6_otp_delivery_redis.py': (25543, 'b1e59ad7285a3c1f034aa269e024e091fe331cbdb446ac5e82f85de64530bd50'), 'test_telegram_otp_ephemeral_queue_redis.py': (8024, 'cbe02df0618239e4affa1f8e51b2e69cdfe13f332a44d46e8b4626b21d80e22e')}
INTAKE_PIN = (7718, '34935acb8e529998aa6556591ef45fe3b829e1f4858b8539ef7d17bf6b5cf487')
MANIFEST_PIN=(3594, '8db473c668f50a3ab4c8da6f19debd01e42ba886911a0e598c028faad1718e48')
OLD_PIN=PINS['run_app_image_ci_smoke.py']
BUILDER_PIN=PINS['prepare_canonical_app_image.py'][1]

def need(ok,code):
 if not ok:raise RuntimeError(code)

def encoded(value):return(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()

def source(old,root,stack,checks,name,pin):
 raw,check=old.sealed(stack,root/'source'/name,pin[1],131072);need(len(raw)==pin[0],'source_pin');checks.append(check);return raw

class BootstrapFailure(RuntimeError):
 def __init__(self,errors):
  super().__init__('bootstrap_io_finality');self.errors=tuple(errors)

def bootstrap_fail(errors):
 if len(errors)==1:raise errors[0]
 raise BootstrapFailure(errors)from errors[0]

class Bootstrap:
 """One published original source prefix; no callback adopts a new FD."""
 def __init__(self,path,scope):
  self.path=path;self.scope=scope;self.fd=None;self.original_fd=None
  self.before=None;self.flags=None;self.raw=None;self.errors=[];self.close_attempted=False
 def note(self,error):
  for item in(error.errors if type(error)is BootstrapFailure else(error,)):
   if all(item is not old for old in self.errors):self.errors.append(item)
 def identity(self,s):return(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
 def named(self):need(self.path.resolve(strict=True)==self.path and self.identity(self.path.lstat())==self.before,'source_pin')
 def metadata(self):
  self.named();need(self.fd==self.original_fd and self.identity(os.fstat(self.original_fd))==self.before and(fcntl.fcntl(self.original_fd,fcntl.F_GETFL),fcntl.fcntl(self.original_fd,fcntl.F_GETFD))==self.flags,'bootstrap_flags_or_source')
 def guard(self,*,retired=False):
  errors=[]
  for function in(self.scope,self.named if retired else self.metadata):
   try:function()
   except BaseException as error:self.note(error);errors.extend(error.errors if type(error)is BootstrapFailure else(error,))
  if errors:bootstrap_fail(errors)
 def read(self):
  if self.errors:bootstrap_fail(self.errors)
  errors=[];raw=None
  try:self.guard();raw=os.pread(self.original_fd,self.before[6]+1,0)
  except BaseException as error:self.note(error);errors.extend(error.errors if type(error)is BootstrapFailure else(error,))
  finally:
   try:self.guard()
   except BaseException as error:self.note(error);errors.extend(error.errors if type(error)is BootstrapFailure else(error,))
  if errors:bootstrap_fail(errors)
  return raw
 def acquire(self,pin):
  try:
   self.scope();self.fd=os.open(self.path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC);self.original_fd=self.fd
   self.before=self.identity(os.fstat(self.original_fd));self.flags=(fcntl.fcntl(self.original_fd,fcntl.F_GETFL),fcntl.fcntl(self.original_fd,fcntl.F_GETFD))
   need(self.before[2:7]==(0o100600,os.geteuid(),os.getegid(),1,pin[0])and 0<pin[0]<=131072 and self.flags[0]&os.O_ACCMODE==os.O_RDONLY and self.flags[1]&fcntl.FD_CLOEXEC,'source_pin')
   self.raw=self.read();need((len(self.raw),sha256(self.raw).hexdigest())==pin,'source_pin')
  except BaseException as error:self.note(error);raise
 def check(self):
  raw=self.read()
  try:need(raw==self.raw,'source_pin')
  except BaseException as error:self.note(error);raise
 def close(self):
  if self.fd is None:return
  need(not self.close_attempted,'bootstrap_close_unknown');self.close_attempted=True;errors=[]
  try:
   if self.raw is not None and not self.errors:self.check()
   else:self.guard()
  except BaseException as error:self.note(error);errors.extend(error.errors if type(error)is BootstrapFailure else(error,))
  try:
   os.close(self.original_fd)
   try:os.fstat(self.original_fd)
   except OSError as error:need(error.errno==errno.EBADF,'bootstrap_close_unknown')
   else:raise RuntimeError('bootstrap_close_unknown')
   self.fd=None
  except BaseException as error:self.note(error);errors.append(error)
  try:self.guard(retired=self.fd is None)
  except BaseException as error:self.note(error);errors.extend(error.errors if type(error)is BootstrapFailure else(error,))
  if errors:bootstrap_fail(errors)

def bootstrap_module(root,stack,name,pin,*,scope=lambda:None):
 path=root/'source'/name;item=Bootstrap(path,scope)
 if not hasattr(stack,'bootstrap_prefixes'):stack.bootstrap_prefixes=[]
 stack.bootstrap_prefixes.append(item);stack.callback(item.close)
 item.acquire(pin);item.check()
 m=ModuleType('_Q_bootstrap_'+name[:-3]);m.__file__=str(path);exec(compile(item.raw,str(path),'exec'),m.__dict__);return m,item.check

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
  need(not self.closed and self.count<64,'inner_source_callback_bound');index=self.count;self.count+=1
  def finish():
   try:function(*args,**kwargs)
   except BaseException as error:self.failures.append({'stage':'inner_source_finalizer','index':index,**self.classify(error)})
  self.stack.callback(finish)
 def enter_context(self,manager):
  # Only ctx.C.pinned's synchronous generator context is used by admit_app.
  need(not self.closed and self.count<64,'inner_source_callback_bound')
  value=type(manager).__enter__(manager)
  self.callback(type(manager).__exit__,manager,None,None,None);return value
 def close(self):
  if not self.closed:
   try:self.stack.close()
   finally:self.closed=True

EVIDENCE_NAMES=frozenset(('source-identity.json','input-manifest.json','image-build.json','prepare.json','finalize.json','reports/vulnerabilities.json','reports/sbom.cdx.json','reports/docker-build-output.json'))
MAX_JSON=2*1024**2
PUBLIC_CLASSES=frozenset(('RuntimeError','Refusal','AssertionError','OSError','FileNotFoundError','PermissionError','TimeoutError','ValueError','TypeError','KeyError','JSONDecodeError','BootstrapFailure','ExportIOFailure','ArchiveIOFailure','InventoryAttemptFailure'))
CODES=frozenset(('bootstrap_io_finality','bootstrap_flags_or_source','bootstrap_close_unknown','explicit_action','outer_keeper_required','source_pin','source_manifest','intake_shape','evidence_shape','evidence_identity','evidence_signature','evidence_reports','inventory_receipt','inventory_cardinality_changed','source_identity','hosted_ci_only','owned_cleanup_unproven','unexpected_failure','inner_source_callback_bound','inner_clock','inventory_attempt_finality','inventory_attempt_identity','inventory_observation_identity','inventory_observation_bound'))

def same(a,b):
 if type(a)is not type(b):return False
 if type(a)is dict:return set(a)==set(b)and all(same(a[k],b[k])for k in a)
 if type(a)is list:return len(a)==len(b)and all(same(x,y)for x,y in zip(a,b))
 return a==b

def document(raw):
 need(type(raw)is bytes and 0<len(raw)<=MAX_JSON,'evidence_shape')
 def pairs(rows):
  value={}
  for key,item in rows:need(key not in value,'evidence_shape');value[key]=item
  return value
 return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:need(False,'evidence_shape'))

def checked_document(raw,pin):
 need((len(raw),sha256(raw).hexdigest())==pin,'source_pin');return document(raw)

def hosted(root):
 need(os.getuid()==os.geteuid()==1001 and os.environ.get('GITHUB_ACTIONS')=='true'and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted'and os.environ.get('GITHUB_REPOSITORY')=='mohsenbarari/trading_bot'and os.environ.get('GITHUB_REF')=='refs/heads/main','hosted_ci_only')
 parent=Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
 need(root.is_absolute()and root.resolve(strict=True)==root and root.parent==parent and root.name=='security-app-ci-probe2389-'+os.environ['GITHUB_RUN_ID']+'-'+os.environ['GITHUB_RUN_ATTEMPT']and root.stat().st_uid==1001 and stat.S_IMODE(root.stat().st_mode)==0o700 and not parent.stat().st_mode&0o022,'hosted_ci_only')

def validate_inputs(intake,manifest):
 need(type(manifest)is dict and manifest.get('schema')=='canonical_gold2_hosted_source_dependencies/1'and manifest.get('total_files')==15 and manifest.get('code_limit')==131072 and manifest.get('data_limit')==2097152,'source_manifest')
 rows=manifest.get('files');need(type(rows)is list and len(rows)==15 and len({r['name']for r in rows})==15 and {r['name']:(r['bytes'],r['sha256'])for r in rows}==PINS and all(r['git_mode']=='100644'and 0<r['bytes']<=131072 for r in rows),'source_manifest')
 need(type(intake)is dict and intake.get('schema')=='current2389_probe_hosted_intake/1'and intake.get('expected_elf_paths')==835 and intake.get('runtime')=={'runtime':8,'components':24,'skips':0,'errors':0}and intake.get('security_accepted')is False and intake.get('full_matrix_accepted')is False,'intake_shape')
 control=intake['control'];need(control['image_id']=='sha256:2389c900dbbf520bd7d7f723e8c48a5c258f1612c43e80d2b66c6d046f9f8434'and control['source_commit']=='4df2fed29859c0b82367666521f1a345f269f58a'and control['run_id']==40844680297 and control['run_attempt']==1 and control['raw_counts']['HIGH']==44,'intake_shape')
 need(control['archive_observation']['member_count']==78 and control['archive_observation']['legacy_compatibility_configs']==37 and len(control['rootfs']['Layers'])==37 and manifest['source_origin_commit']==control['source_commit'],'intake_shape')
 need(intake.get('probe_sources')=={k:list(v)for k,v in PROBE_PINS.items()}and intake.get('retained_inventory_origin')=={'run_id':40902155013,'artifact_id':12910526841,'archive_bytes':459147,'archive_sha256':'97b4deecbb2c0f3112379b9f9cc8f784c297f321553e4f05a8a27b85deb122a5','fresh_inventory_executed_by_this_cohort':False},'intake_shape')
 return control

def inventory_gate(value,image,intake):
 need(type(value)is dict and value.get('schema')=='image_acl_archive_inventory/1'and value.get('image_id')==image and value.get('source_commit')==intake['control']['source_commit']and value.get('sbom_sha256')==intake['sbom']['sha256']and value.get('scope')=='all_regular_ELF_in_authenticated_flat_image_TAR_without_path_exclusions'and value.get('inspected_code_executed')is False and value.get('archive_extracted')is False and value.get('whole_image_acl_closed')is False and value.get('security_accepted')is False,'inventory_receipt')
 inventory=value.get('inventory');need(type(inventory)is dict and inventory.get('runtime_paths_excluded')==[]and type(inventory.get('elf_files'))is list,'inventory_receipt')
 rows=inventory['elf_files'];need(all(type(r)is dict and type(r.get('path'))is str for r in rows)and len({r['path']for r in rows})==len(rows),'inventory_receipt')
 # The full report is already retained before this measured gate. Never trim,
 # drop UNASSESSED rows or rewrite a result to obtain the historical cardinality.
 need(len(rows)==intake['expected_elf_paths'],'inventory_cardinality_changed')
 return value

class InventoryAttemptFailure(RuntimeError):
 def __init__(self,errors,record=None,artifact_written=False,artifact_publication_complete=False):
  super().__init__('inventory_attempt_finality');self.errors=tuple(errors);self.record=record;self.artifact_written=artifact_written;self.artifact_publication_complete=artifact_publication_complete

def inventory_causes(error,old,ctx):
 # Only the exact admitted aggregate types are traversed, with public closed
 # rows for every actual leaf. Exception text and unknown operands stay private.
 kinds=[InventoryAttemptFailure,BootstrapFailure]
 for module in (()if ctx is None else ctx.modules.values()):
  for name in('ArchiveIOFailure','ExportIOFailure'):
   kind=getattr(module,name,None)
   if type(kind)is type:kinds.append(kind)
 pending=[error];rows=[];count=0
 while pending:
  item=pending.pop();count+=1;need(count<=8192,'inventory_observation_bound')
  if type(item)in kinds:pending.extend(reversed(item.errors))
  else:rows.append(closed_error(item,allowed_codes(old,ctx)))
 return rows

def inventory_guarded(ctx,function):
 errors=[];value=None
 try:ctx.check();value=function()
 except BaseException as error:errors.append(error)
 finally:
  try:ctx.check()
  except BaseException as error:errors.append(error)
 if errors:
  if len(errors)==1:raise errors[0]
  raise InventoryAttemptFailure(errors)from errors[0]
 return value

def inventory_attempt(ctx,old,E,A,I,image,control,sbom):
 # This one-shot synchronous bridge observes only the fixed E05 -> A02 call.
 # It grants no parser, file, cleanup or completion authority of its own.
 before=A.ATTEMPT_SEQUENCE;original_collect=A.collect;original_public=A.public_failure
 need(type(before)is int and before>=0,'inventory_attempt_identity')
 calls=0;entered=False;binding=None;collection_error=None;errors=[];value=None;observation=None;written=False
 def collect(parser,transport,stream,size,**kwargs):
  nonlocal calls,entered,binding,collection_error
  calls+=1
  def actual():
   nonlocal entered,binding,collection_error
   need(calls==1 and A.ATTEMPT_SEQUENCE==before and A.collect is original_collect and A.public_failure is original_public
    and parser is I and transport is ctx.T and type(size)is int and 0<size<=E.MAX_EXPORT
    and kwargs.get('image_id')==image and kwargs.get('source_commit')==control['source_commit']
    and kwargs.get('sbom_sha256')==control['sbom_sha256']and kwargs.get('verifier_sha256')==GOLD2_INVENTORY_PIN[1]
    and type(kwargs.get('export_sha256'))is str and re.fullmatch('[a-f0-9]{64}',kwargs['export_sha256']),'inventory_attempt_identity')
   binding={'ordinal':before+1,'image_id':image,'source_commit':control['source_commit'],'verifier_sha256':GOLD2_INVENTORY_PIN[1],'export_sha256':kwargs['export_sha256']}
   entered=True
   try:return original_collect(parser,transport,stream,size,**kwargs)
   except BaseException as error:collection_error=error;raise
  return inventory_guarded(ctx,actual)
 bridge=ModuleType('_gold2_current_attempt_only');bridge.collect=collect
 try:value=inventory_guarded(ctx,lambda:E.whole_image_inventory(ctx,old,image,control,sbom,I=I,T=ctx.T,A=bridge,adapter_sha256=GOLD2_INVENTORY_PIN[1]))
 except BaseException as error:errors.append(error)
 after=A.ATTEMPT_SEQUENCE
 try:
  ctx.check();need(A.collect is original_collect and A.public_failure is original_public,'inventory_attempt_identity')
  if entered:
   candidate=A.LAST_OBSERVATION
   need(after==before+1 and type(candidate)is dict and candidate.get('attempt')==binding,'inventory_observation_identity')
   if value is not None:need(value.get('image_id')==image and value.get('source_commit')==control['source_commit']and value.get('verifier_sha256')==binding['verifier_sha256']and value.get('export_sha256')==binding['export_sha256'],'inventory_observation_identity')
   raw=encoded(candidate);need(len(raw)<=1024**2,'inventory_observation_bound');observation=document(raw)
  else:need(after==before and calls<=1 and value is None,'inventory_observation_identity')
 except BaseException as error:errors.append(error)
 closed_collection=None
 if collection_error is not None:
  try:closed_collection=original_public(collection_error)
  except BaseException as error:errors.append(error)
 record={'schema':'gold2_current_inventory_attempt/1','ordinal_before':before,'ordinal_after':after,
  'collect_forward_calls':calls,'original_collect_entered':entered,'bound_current_operands':binding,
  'current_observation':observation,'previous_observation_reused':False,'collection_failure':closed_collection,
  'call_failures':[row for error in errors for row in inventory_causes(error,old,ctx)],
  'security_accepted':False,'whole_image_proof':False,'artifact_finality':'conditional_on_actual_publication_and_outer_finalizers'}
 def publish():
  nonlocal written
  ctx.write(ctx.root/'reports/archive-inventory-attempt.json',record);written=True
 publication_complete=False
 try:
  need(len(encoded(record))<=1024**2,'inventory_observation_bound')
  inventory_guarded(ctx,publish);publication_complete=True
 except BaseException as error:errors.append(error)
 if errors:raise InventoryAttemptFailure(errors,record,written,publication_complete)from errors[0]
 return value

def execute(ctx,old,P,builder,E,A,I,intake):
 global PHASE
 control=intake['control'];need(intake.get('probe_sources')=={k:list(v)for k,v in PROBE_PINS.items()},'source_pin')
 PHASE='current2389_required12_manifest_coverage';main_expected=checked_document(source(old,ctx.root,ctx.stack,ctx.checks,'native-expectation.json',PROBE_PINS['native-expectation.json']),PROBE_PINS['native-expectation.json']);extra_expected=checked_document(source(old,ctx.root,ctx.stack,ctx.checks,'native-extra-expectation.json',PROBE_PINS['native-extra-expectation.json']),PROBE_PINS['native-extra-expectation.json']);required=checked_document(source(old,ctx.root,ctx.stack,ctx.checks,'required-caller-payload.json',PROBE_PINS['required-caller-payload.json']),PROBE_PINS['required-caller-payload.json']);ctx.modules['caller_payload.py'].check(main_expected,extra_expected,required)
 PHASE='current2389_retained_inventory';pointer_raw=source(old,ctx.root,ctx.stack,ctx.checks,'retained-inventory-pointer.json',PROBE_PINS['retained-inventory-pointer.json']);pointer=checked_document(pointer_raw,PROBE_PINS['retained-inventory-pointer.json']);raw=ctx.modules['retained_inventory.py'].collect(ctx,old,pointer_raw,PROBE_PINS['retained-inventory-pointer.json']);retained=inventory_gate(checked_document(raw,(pointer['member_bytes'],pointer['member_sha256'])),control['image_id'],intake);need(retained['source_bindings_complete']is False and retained['whole_image_acl_closed']is False,'inventory_receipt');ctx.C.write(ctx.root/'reports/retained-whole-image-acl-inventory.json',raw)
 folder=ctx.root/'evidence';folder.mkdir(mode=0o700);path=folder/'public-evidence.zip';ctx.download(control['evidence_artifact_id'],path,control['evidence_zip_bytes'],control['evidence_zip_sha256'])
 with ctx.C.pinned(path,control['evidence_zip_bytes'])as(stream,size):
  need(size==control['evidence_zip_bytes']and ctx.C.stream_digest(stream,size)==control['evidence_zip_sha256'],'evidence_identity');stream.seek(0);raws=P.read_zip(stream,EVIDENCE_NAMES)
 evidence=admit_evidence(raws,intake,builder)
 ctx.write(ctx.root/'reports/source-binding.json',{'schema':'fresh_product_source_binding/1','image_id':control['image_id'],'image_scope':P.image_scope(control),'controller_commit':os.environ['GITHUB_SHA'],'product_profile':'canonical_app32','product_originals':{'build':evidence['image-build.json'],'source':evidence['source-identity.json'],'manifest':evidence['input-manifest.json'],'recipe':control['recipe_pin']},'raw_counts':control['raw_counts'],'security_accepted':False})
 PHASE='current2389_archive_Config37_daemon';image=admit_app(ctx,old,control,evidence,P)
 PHASE='current2389_missing_actual_probes';Q=ctx.modules['probes.py'];present=Q.presence(ctx,old,image,control);native,expected=Q.native_payload(ctx,old,image,control);extra_native,extra_manifest=Q.native_extra(ctx,old,image,control);coverage=ctx.modules['caller_payload.py'].check(native,extra_native,required,observed=True);ctx.write(ctx.root/'reports/required12-actual-payload-coverage.json',coverage)
 sqlite=[p for p in native['packages']if p['package']=='libsqlite3-0'];py=present['verify_python_runtime.py'];need(len(sqlite)==1 and len(sqlite[0]['actual_files'])==1 and py['actual_sqlite_runtime']=='3.46.1','presence_identity');actual=sqlite[0]['actual_files'][0];need(any(r['path']==actual['path']and r['sha256']==actual['sha256']and r['actual_mapped_device_inode_bound']is True for r in py['actual_native_libraries']),'presence_identity')
 ctx.write(ctx.root/'reports/fresh-installed-sqlite-native.json',{'schema':'current2389_installed_sqlite_native/1','scope':P.image_scope(control),'actual_native_package':sqlite[0],'actual_python_sqlite_version':py['actual_sqlite_runtime'],'same_image_native_payload_and_actual_mapped_library_joined':True,'security_accepted':False})
 PHASE='current2389_unchanged32';result=P.run_smoke(ctx,old,image,control);ctx.check();ctx.write(ctx.root/'reports/collection.json',{'schema':'current2389_missing_probes_and_runtime32/1','image_id':image,'image_source_commit':control['source_commit'],'controller_commit':os.environ['GITHUB_SHA'],'fresh_inventory_executed':False,'retained835_original':{'bytes':pointer['member_bytes'],'sha256':pointer['member_sha256']},'runtime':result['runtime'],'components':result['components'],'migration':result['migration'],'native_files':native['native_files_verified'],'native_aliases':native['aliases_verified'],'extra_native_files':extra_native['native_files_verified'],'extra_native_aliases':extra_native['aliases_verified'],'required12_actual_body_coverage':True,'complete_semantic_caller_inventory_claimed':False,'fresh_presence_reports':2,'actual_current_CVE_or_caller_fix_or_security_accepted':False,'raw_counts':control['raw_counts'],'security_accepted':False,'full_matrix_accepted':False});return result

def admit_evidence(raw_files, intake, builder):
    control=intake['control']
    need({name:{'bytes':len(raw),'sha256':sha256(raw).hexdigest()} for name,raw in raw_files.items()}==intake['evidence_members'], 'evidence_identity')
    need(set(raw_files) == EVIDENCE_NAMES and all(type(v) is bytes and 0 < len(v) <= MAX_JSON for v in raw_files.values()), 'evidence_shape')
    data = {name: document(raw) for name, raw in raw_files.items()}
    source, inputs, build = (data[name] for name in ('source-identity.json','input-manifest.json','image-build.json'))
    for row in (source, inputs, build):
        need(row.get('source_commit') == control['source_commit'] and row.get('source_tree') == control['source_tree']
            and row.get('security_accepted') is False, 'evidence_identity')
    need(source.get('schema') == 'canonical_app_source/1' and source.get('run_id') == str(control['run_id'])
        and source.get('run_attempt') == str(control['run_attempt']) and source.get('repo') and type(source.get('files')) is list, 'evidence_identity')
    need(build.get('schema') == 'canonical_app_image_build/1' and build.get('image_id') == control['image_id']
        and same(build.get('rootfs'), control['rootfs']) and build.get('run_id') == str(control['run_id'])
        and build.get('run_attempt') == str(control['run_attempt']) and build.get('platform') == 'linux/amd64'
        and build.get('activation_performed') is False and build.get('market_roles_built') is False, 'evidence_identity')
    rows = inputs.get('files'); empty = inputs.get('empty_directories')
    need(inputs.get('schema') == 'canonical_app_inputs/1' and type(rows) is list and 0 < len(rows) <= 20000
        and all(type(r) is dict and type(r.get('path')) is str and type(r.get('sha256')) is str
                and re.fullmatch('[a-f0-9]{64}',r['sha256']) and type(r.get('bytes')) is int and r['bytes'] >= 0 for r in rows)
        and len({r['path'] for r in rows}) == len(rows) and type(empty) is list and all(type(x) is str and x in builder.INPUTS for x in empty), 'evidence_signature')
    need(builder.foreign_signature(rows, empty) == inputs.get('input_signature') == build.get('input_signature') == control['input_signature'], 'evidence_signature')
    scan, sbom = data['reports/vulnerabilities.json'], data['reports/sbom.cdx.json']
    component = sbom.get('metadata',{}).get('component',{})
    need(scan.get('Metadata',{}).get('ImageID') == control['image_id'] and component.get('type') == 'container'
        and component.get('name') == control['image_id'] and [p.get('value') for p in component.get('properties',[]) if p.get('name') == 'aquasecurity:trivy:ImageID'] == [control['image_id']], 'evidence_reports')
    counts = {key:0 for key in control['raw_counts']}
    need(type(scan.get('Results')) is list, 'evidence_reports')
    for row in scan['Results']:
        need(type(row) is dict and (row.get('Vulnerabilities') is None or type(row['Vulnerabilities']) is list), 'evidence_reports')
        for finding in row.get('Vulnerabilities') or []:
            need(type(finding) is dict and finding.get('Severity') in counts, 'evidence_reports'); counts[finding['Severity']] += 1
    need(same(counts, control['raw_counts']) and data['finalize.json'] == {'schema':'canonical_app_finalize/1','status':'exported_not_accepted','security_accepted':False}, 'evidence_reports')
    log=data['reports/docker-build-output.json']
    need(type(log)is dict and log.get('schema')=='canonical_app_docker_build_output/2'
        and type(log.get('actual_command_exit'))is int and log['actual_command_exit']==0
        and log.get('combined_stdout_stderr')is True and log.get('build_accepted')is False
        and log.get('security_accepted')is False,'evidence_reports')
    need(build.get('dockerfile_checks') == ['trust','coreutils_acl','python_micro_release','psycopg2','sqlite_libmagic','pip_removed'], 'evidence_identity')
    source_map = {r['path']:{k:r[k] for k in ('bytes','sha256')} for r in source['files']}
    expected_sources = control['image_source_pins']
    need(type(expected_sources) is dict and 'scripts/prepare_canonical_app_image.py' in expected_sources
        and all(source_map.get(path) == row for path,row in expected_sources.items())
        and expected_sources['scripts/prepare_canonical_app_image.py']['sha256'] == BUILDER_PIN, 'evidence_identity')
    recipe_rows = [r for r in rows if r['path']=='Dockerfile']
    need(len(recipe_rows)==1 and {k:recipe_rows[0][k] for k in ('bytes','sha256')}==control['recipe_pin'], 'evidence_identity')
    return {name:{'bytes':len(raw),'sha256':sha256(raw).hexdigest()} for name,raw in raw_files.items()}

def main(argv=None,*,outer_keeper=None,outer_check=None):
 global PHASE,LAST_FAILURE_RECORD
 PHASE='unarmed';LAST_FAILURE_RECORD=None
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--execute-reviewed-current2389-probes',action='store_true');p.add_argument('--confirm');args=p.parse_args(argv)
 if not args.execute_reviewed_current2389_probes:print(json.dumps({'status':'UNARMED','collection_performed':False,'security_accepted':False}));return 0
 PHASE='source_admission';ctx=None;old=None;primary=None;cleanup_errors=[];secondary=[];sources=None;failure_phase=None
 try:
  need(args.confirm==CONFIRM,'explicit_action');hosted(args.root)
  need(outer_keeper is not None and outer_keeper.root==args.root and not outer_keeper.closed,'outer_keeper_required')
  sources=ObservationSources(lambda error:closed_error(error,allowed_codes(old,ctx)))
  old,old_check=bootstrap_module(args.root,sources,'run_app_image_ci_smoke.py',OLD_PIN,scope=outer_check or(lambda:None))
  ctx=old.Controller(args.root,sources);ctx.C.PROOF_KEEPER=outer_keeper;ctx.checks.extend((old_check,outer_keeper.check))
  if outer_check is not None:ctx.checks.append(outer_check)
  def load(name):
   pin=GOLD2_EXPORT_PIN if name=='export_controller_gold2.py'else GOLD2_INVENTORY_PIN if name=='archive_inventory_gold2.py'else PROBE_PINS[name]if name in PROBE_PINS else PINS[name];raw=source(old,args.root,sources,ctx.checks,name,pin);module=ModuleType('_gold2_'+name[:-3]);module.__file__=str(args.root/'source'/name);exec(compile(raw,module.__file__,'exec'),module.__dict__);ctx.modules[name]=module;return module
  P=load('producer02_controller.py');builder=load('prepare_canonical_app_image.py');E=load('export_controller_gold2.py');A=load('archive_inventory_gold2.py');I=load('inventory.py')
  intake_raw=source(old,args.root,sources,ctx.checks,'intake.json',INTAKE_PIN);manifest_raw=source(old,args.root,sources,ctx.checks,'source-manifest.json',MANIFEST_PIN)
  intake=checked_document(intake_raw,INTAKE_PIN);manifest=checked_document(manifest_raw,MANIFEST_PIN);validate_inputs(intake,manifest)
  load('probes.py');load('archive_metadata.py');load('retained_inventory.py');load('caller_payload.py');execute(ctx,old,P,builder,E,A,I,intake)
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
  print(json.dumps({'status':'CURRENT2389_PROBE_INNER_PENDING_OUTER_FINALS','outer_physical_finalizers_completed':False,'runtime_assertions_executed':32,'fresh_inventory_executed':False,'retained_ELF_paths':835,'security_accepted':False}));return 0
 # Physical failures take the top diagnostic position, while the original
 # cause and every closed cleanup/finalizer failure remain separately visible.
 selected=finalizers[0]if finalizers else secondary[0]if secondary else closed_error(RuntimeError('owned_cleanup_unproven'),CODES)if cleanup_errors else closed_error(primary,allowed_codes(old,ctx))
 inventory_failure=primary if type(primary)is InventoryAttemptFailure else None
 primary_rows=[]if primary is None else inventory_causes(primary,old,ctx)
 record={'schema':'gold2_hosted_public_proof_failure/1','phase':failure_phase or PHASE,**{k:selected[k]for k in('code','exception_class','os_errno')},'primary_failure':None if not primary_rows else primary_rows[0],'primary_failure_causes':primary_rows,'inventory_attempt':None if inventory_failure is None else{'artifact_written':inventory_failure.artifact_written,'artifact_publication_complete':inventory_failure.artifact_publication_complete,'record':inventory_failure.record},'cleanup_causes':cleanup_errors,'secondary_failures':secondary,'inner_finalizer_failures':finalizers,'bootstrap_io_failures':[closed_error(error,allowed_codes(old,ctx))for item in getattr(sources,'bootstrap_prefixes',())for error in item.errors],'inner_sources_physically_closed':sources is not None and sources.closed and not finalizers,'security_accepted':False}
 LAST_FAILURE_RECORD=record
 if ctx is not None:
  try:ctx.write(ctx.root/'reports/failure.json',record)
  except BaseException as error:record['secondary_failures'].append({'stage':'failure_publication',**closed_error(error,allowed_codes(old,ctx))})
 print(json.dumps(record,sort_keys=True));return 1
PROBE_PINS = {'probes.py': (20264, 'a69bde78e99813a5c36e50bb5560af6a20a7e5724539071b0612c870467733cf'), 'archive_metadata.py': (8851, 'bfa2fee95a92893c89ca1dab6f7bfd00d3b6152eea5d7c0bd92ddafa247a04a7'), 'probe_native_payload.py': (20260, '1071f5988c2b0ed256a5973211bee1a2ec0ab860b7f60e59162b81ad353c6042'), 'native-expectation.json': (52714, '65f30c3e6789e081ff4b00f19d575f753d2eaa64fbb0991aeff9ccce815de53b'), 'verify_image_component_presence.py': (12257, '3cd10d7bc591615723f7b85a87011d0d7029120324f09ef2f099d49137bcfd94'), 'verify_python_runtime.py': (7504, '1c984804bedc8eb2b52fb77add484a53ba9df8e65420f0e3f8a2e754255627f3'), 'retained-inventory-pointer.json': (858, 'e0d58233856b9474ec56d073d854cbf9758429f4fccad1f74134db5f845995da'), 'retained_inventory.py': (2885, '517ddd12b4c6e220651bb0d2100c7348f83a1db6064c5250fb154f490124dc38'), 'public-default-five.json': (649, 'c5bf928a56e84ce8a306481b9b40a4feed9e37c18fe00ee1578609a3e9872e0e'), 'native-policy-audit.json': (1059, '7c7ce97ce184ba90b1f1cba526a5f2d91062548233e5b0aee1a39dccb94c2825'), 'caller_payload.py': (1642, 'd92200e0b34015488dec15610dd3abe24efc0b497b7f6822ccdc9f5493bb49ea'), 'required-caller-payload.json': (2326, '6c80c4a38305ab5c658354bf98659ab92283db498501bbbed6924033bbf45a17'), 'probe_native_extra.py': (20458, '541c5ad694ec2235bb553d6e2fb2b9b9ab5d42732f7cd40e96994ec9666b491c'), 'native-extra-expectation.json': (7615, '18af9a553ad648912e2e347e69c7bc7462bdaee5f2d1b007b2ee1b8e49ded3f9'), 'native-extra-policy-audit.json': (1421, 'fc6529866aaed36eb446baf3781ffa34c62e791a36df59e7aeddf8866ce2f195'), 'native-extra-package-contract.json': (1428, 'ca26b4bf6eb7a37eee2ce706441c87749a4696c5a24b5bb73dbffc128f599974')}

def admit_app(ctx, old, control, evidence, P):
    global PHASE
    PHASE = 'canonical_artifact_download'; folder = ctx.root/'canonical-image'; folder.mkdir(mode=0o700)
    path = folder/'image.zip'; ctx.download(control['image_artifact_id'],path,control['image_zip_bytes'],control['image_zip_sha256'])
    with ctx.C.pinned(path,control['image_zip_bytes']) as (stream,size):
        need(size == control['image_zip_bytes'] and ctx.C.stream_digest(stream,size) == control['image_zip_sha256'], 'export_identity')
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            rows = archive.infolist()
            need(len(rows) == 2 and {r.filename for r in rows} == {'image-export.json','candidate-image.tar.gz'}
                and all(not r.is_dir() and not r.flag_bits & 1 and stat.S_IFMT(r.external_attr >> 16) in (0,stat.S_IFREG) for r in rows), 'export_shape')
            info = archive.getinfo('image-export.json'); need(0 < info.file_size <= 65536, 'export_shape')
            export_raw = archive.read(info); exported = document(export_raw); gz = P.export_record(exported,control,evidence)
            info = archive.getinfo('candidate-image.tar.gz'); need(info.file_size == gz['bytes'], 'export_shape')
            ctx.capacity(gz['bytes']+gz['uncompressed_bytes']+MAX_JSON)
            target = folder/'candidate-image.tar.gz'; total = 0; digest = sha256()
            with archive.open(info) as incoming:
                fd = os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
                with os.fdopen(fd,'wb') as outgoing:
                    while block := incoming.read(min(65536,gz['bytes']+1-total)):
                        total += len(block); need(total <= gz['bytes'],'export_shape'); ctx.check(); outgoing.write(block); digest.update(block)
                    need(total == gz['bytes'] and digest.hexdigest() == gz['sha256'],'export_identity'); outgoing.flush(); os.fsync(outgoing.fileno())
    tar = folder/'candidate-image.tar'
    with ctx.C.pinned(target,gz['bytes']) as (source,size): ctx.P.decode_gzip(source,size,tar,gz['uncompressed_bytes'])
    PHASE = 'canonical_physical_archive_admission'
    source,size = ctx.stack.enter_context(ctx.C.pinned(tar,gz['uncompressed_bytes']))
    need(size == gz['uncompressed_bytes'],'archive_observation')
    config, proof = P.validate_archive(source,size,ctx,control)
    ctx.write(ctx.root/'reports/archive-admission.json',proof)
    ctx.write(ctx.root/'reports/authenticated-export.json',exported)
    default_raw,default_check=old.sealed(ctx.stack,ctx.root/'source/public-default-five.json',PROBE_PINS['public-default-five.json'][1],131072);ctx.checks.append(default_check);defaults=checked_document(default_raw,PROBE_PINS['public-default-five.json'])
    public,counts=ctx.modules['archive_metadata.py'].collect(ctx,source,size,config,proof,control,defaults)
    ctx.write(ctx.root/'reports/public-default-five.json',public);ctx.write(ctx.root/'reports/resource-inner-inventory.json',counts)
    PHASE = 'canonical_image_load'
    image, receipt = old.load_exact(ctx,source,control['image_id'],config['config'],control['rootfs']['Layers'],proof['source_identities'])
    ctx.write(ctx.root/'reports/daemon-admission.json',receipt)
    ctx.check()  # Stack retains this TAR through presence, smoke and cleanup.
    return image

if __name__=='__main__':raise SystemExit(main())
