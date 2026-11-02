"""Source template: exact future qualified Debian runtime pair, default UNBOUND.

The original cache/installer IO primitives are borrowed, never their Coreutils
package policy. No download, deployment, Host install or arbitrary command API.
"""
from contextlib import ExitStack
from hashlib import sha256
from pathlib import Path
from types import ModuleType
import argparse,ctypes,errno,fcntl,json,os,re,signal,stat,sys,tempfile

HERE=Path(__file__).absolute().parent;CAPSULE=Path('/glibc-capsule')
IO_PIN=(40590,'e5a1587af0db34acfb5ab04434754cf75b9ad0aa43c0550f7ec7fedf5dd8a65f')
MANIFEST_PIN={'bytes':1245,'sha256':'d2b8bf088159575896f7924cda1f33036c4a257b640522055ae8adb1121b88aa'} # Only a NEW Source may bind genuine CI0 + Root qualification.
ROOT_QUALIFICATION_PIN={'bytes':23392,'sha256':'7cacf5aaf2c03759343c4a355fe9e8f0e414412fae22ccce0d17eb22c7a2073d'}
OLD='2.41-12+deb13u4';NEW=OLD+'+gold1';NAMES=('libc6','libc-bin')
TARGETS={'libc':'/usr/lib/x86_64-linux-gnu/libc.so.6','loader':'/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2'}
OLD_PAYLOAD={'libc':{'bytes':1995216,'sha256':'9792e3cbb541c8f44c7acf5f14f4022ea62998ecc787d326bed4d8b6547dfd92'},
 'loader':{'bytes':225672,'sha256':'c8438e4fde1934e61c88311633f00949ff645d5c04cdb8671fa3d78164d2f307'}}
SOURCE_INVENTORY=('/usr/bin/dpkg-query','-W','-f=${binary:Package}\t${source:Package}\n')
DOC_FLAGS=tuple(v for root in('/usr/share/doc/libc6','/usr/share/doc/libc-bin','/usr/share/man','/usr/share/locale')for v in('--path-include='+root,'--path-include='+root+'/*'))

FIXED_VENDOR_CASES=('elf/tst-ifunc-isa-1', 'elf/tst-ifunc-isa-1-static', 'elf/tst-ifunc-isa-2', 'elf/tst-ifunc-isa-2-static', 'nptl/tst-create-detached', 'nptl/tst-robust8', 'rt/tst-cpuclock2', 'rt/tst-cputimer1', 'rt/tst-timer', 'support/tst-support_descriptors')
FIXED_VENDOR_SOURCES={'elf/tst-ifunc-isa-1': {'bytes': 1070, 'member': 'glibc-2.41/sysdeps/x86/tst-ifunc-isa-1.c', 'sha256': '27bf7bc0d7d8df24c4d4c44368d4a918b8b0ee284a5533c3729176332c0347a0'}, 'elf/tst-ifunc-isa-1-static': {'bytes': 29, 'member': 'glibc-2.41/sysdeps/x86/tst-ifunc-isa-1-static.c', 'sha256': '477aee3b0cb81643d408a3663a5125b39d9e79a502f0dc5e4ac5b1199cbd4ebc'}, 'elf/tst-ifunc-isa-2': {'bytes': 1234, 'member': 'glibc-2.41/sysdeps/x86/tst-ifunc-isa-2.c', 'sha256': '5af004b4189d67aa7f938586b09206ea790376b6d4b1f6341540d8191f1bf863'}, 'elf/tst-ifunc-isa-2-static': {'bytes': 29, 'member': 'glibc-2.41/sysdeps/x86/tst-ifunc-isa-2-static.c', 'sha256': '330d3178e694901dcb8c99acfae10fdb38bbe1eafcc3dc52f92a241b828113d4'}, 'nptl/tst-create-detached': {'bytes': 4441, 'member': 'glibc-2.41/sysdeps/pthread/tst-create-detached.c', 'sha256': 'ae52a270fad70fd9d7ead9ae4f3b76f96a584a2e45d798d5a6e381788a07fc66'}, 'nptl/tst-robust8': {'bytes': 5301, 'member': 'glibc-2.41/sysdeps/pthread/tst-robust8.c', 'sha256': 'c61f6659307f57951e6f1a9faf6fcea753d2f8e29abccb70f8e57936e85cc946'}, 'rt/tst-cpuclock2': {'bytes': 7575, 'member': 'glibc-2.41/rt/tst-cpuclock2.c', 'sha256': '7874f552517eee9e55abae6e51b5e3b6cd761225cc4748a1114b6d019128a766'}, 'rt/tst-cputimer1': {'bytes': 1518, 'member': 'glibc-2.41/rt/tst-cputimer1.c', 'sha256': '891ee9b6c3772faaf07fd8a079adfc96922970d9858315c46c8c44347c817070'}, 'rt/tst-timer': {'bytes': 4024, 'member': 'glibc-2.41/sysdeps/pthread/tst-timer.c', 'sha256': 'ef86234102322d384517a81765d84719881d0dd66fd51bfc963133f81b4dea08'}, 'support/tst-support_descriptors': {'bytes': 7106, 'member': 'glibc-2.41/support/tst-support_descriptors.c', 'sha256': 'eb8c5988b493b3ab1d862d05423959816824de18188465d2856a9ba971dbeeec'}}
FIXED_ADDITIONAL_SOURCES={'rt/tst-cpuclock2.c': {'bytes': 7575, 'sha256': '7874f552517eee9e55abae6e51b5e3b6cd761225cc4748a1114b6d019128a766'}, 'rt/tst-cputimer1.c': {'bytes': 1518, 'sha256': '891ee9b6c3772faaf07fd8a079adfc96922970d9858315c46c8c44347c817070'}, 'rt/tst-timer4.c': {'bytes': 16776, 'sha256': '092718fe9728e570bcfe1a9dc53235960b96241c4121cefe6cd61dc663a75ee9'}, 'scripts/evaluate-test.sh': {'bytes': 1243, 'sha256': '7e8b09577d6a4620e98c60fbc1c3678abdd53e5e6d218aafe2144f197ccc0397'}, 'support/tst-support_descriptors.c': {'bytes': 7106, 'sha256': 'eb8c5988b493b3ab1d862d05423959816824de18188465d2856a9ba971dbeeec'}, 'sysdeps/pthread/tst-create-detached.c': {'bytes': 4441, 'sha256': 'ae52a270fad70fd9d7ead9ae4f3b76f96a584a2e45d798d5a6e381788a07fc66'}, 'sysdeps/pthread/tst-robust8.c': {'bytes': 5301, 'sha256': 'c61f6659307f57951e6f1a9faf6fcea753d2f8e29abccb70f8e57936e85cc946'}, 'sysdeps/pthread/tst-timer.c': {'bytes': 4024, 'sha256': 'ef86234102322d384517a81765d84719881d0dd66fd51bfc963133f81b4dea08'}, 'sysdeps/x86/Makefile': {'bytes': 5463, 'sha256': '0b043149cdd1bc4d7ae88a536657025f715f8c1e734a83f43add2362cbe67968'}, 'sysdeps/x86/tst-ifunc-isa-1-static.c': {'bytes': 29, 'sha256': '477aee3b0cb81643d408a3663a5125b39d9e79a502f0dc5e4ac5b1199cbd4ebc'}, 'sysdeps/x86/tst-ifunc-isa-1.c': {'bytes': 1070, 'sha256': '27bf7bc0d7d8df24c4d4c44368d4a918b8b0ee284a5533c3729176332c0347a0'}, 'sysdeps/x86/tst-ifunc-isa-2-static.c': {'bytes': 29, 'sha256': '330d3178e694901dcb8c99acfae10fdb38bbe1eafcc3dc52f92a241b828113d4'}, 'sysdeps/x86/tst-ifunc-isa-2.c': {'bytes': 1234, 'sha256': '5af004b4189d67aa7f938586b09206ea790376b6d4b1f6341540d8191f1bf863'}}
EARLY_VENDOR_CASES=('nptl/tst-robust8','rt/tst-cpuclock2','rt/tst-cputimer1')
CVE_CASE='intl/tst-gettext-path-traversal'
CVE_SOURCE={'bytes':3336,'sha256':'c732e301bbe037a6aa612f1452d7768d0bb3efd893f4fbf1d4d02e4651e2d3f6','member':'glibc-2.41/intl/tst-gettext-path-traversal.c'}
CLASSIFICATION_PIN={'bytes':12385,'sha256':'29a094e9626537e10345d1c66c20cf8a232ea89507fdfbe5d19387c16fc3256c'}
EVALUATOR_PIN={'bytes': 1243, 'sha256': '7e8b09577d6a4620e98c60fbc1c3678abdd53e5e6d218aafe2144f197ccc0397'}
EXPECTATIONS_PIN={'bytes': 25227, 'sha256': '0554f6b6497f74bc5291c529d43f2a6ee023f838363c45d55de4909dfea0ba80'}
MERGE_PRODUCER_PIN={'bytes':1919,'sha256':'2c9199c21a9897f75d26aca4b84fa3bb5f9accc1414977e2378bf396c59f06c8'}
REVIEW_EVIDENCE=('CI_completion','source_proof','vendor_suite','old_counterfactual','ELF_ABI','Source_finality','raw_libc_sum','raw_cases_manifest','raw_cases_archive','classification','evaluator','expectations')

def need(ok,code):
 if ok is not True:raise RuntimeError(code)
def frame(s):return(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def canonical(value):return(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
def pin(value,maximum):
 need(type(value)is dict and set(value)=={'bytes','sha256'}and type(value['bytes'])is int and 0<value['bytes']<=maximum
  and type(value['sha256'])is str and re.fullmatch('[a-f0-9]{64}',value['sha256'])is not None,'manifest_binding')
 return value
def manifest(value):
 need(type(value)is dict and set(value)=={'schema','packages','payload','producer','Root_qualification','security_accepted'}
  and value['schema']=='glibc84243_image_runtime_pair_capsule/1'and value['security_accepted']is False,'manifest_shape')
 need(type(value['packages'])is list and len(value['packages'])==2 and [r['name']for r in value['packages']]==list(NAMES),'runtime_pair_only')
 for row in value['packages']:
  need(set(row)=={'name','version','baseline_version','architecture','path','binding'}and row['version']==NEW and row['baseline_version']==OLD
   and row['architecture']=='amd64'and row['path']==row['name']+'_'+NEW+'_amd64.deb','runtime_pair_only');pin(row['binding'],32*1024**2)
 need(type(value['payload'])is dict and set(value['payload'])==set(TARGETS),'payload_scope')
 for item in value['payload'].values():pin(item,32*1024**2)
 producer=value['producer'];need(type(producer)is dict and set(producer)=={'workflow_run_id','attempt','head_sha','profile','worker_sha256','patch_sha256'}
  and type(producer['workflow_run_id'])is int and producer['workflow_run_id']>0 and type(producer['attempt'])is int and producer['attempt']>0
  and re.fullmatch('[a-f0-9]{40}',producer['head_sha'])is not None and producer['profile']=='trixie241'
  and producer['worker_sha256']=='550d6e4014cbf6c3a11a5c517d0e8fb2dd5fc503f2c355ecc669c1732ab0616b'
  and producer['patch_sha256']=='b242ad410ae54c64cd77fd95a7813a6e1ea6367a6d81af56a9dc6318d4b0c76b','producer_scope')
 pin(value['Root_qualification'],1024**2);return value

def cold_io(stack):
 """Original named source FD survives the body and closes physically last."""
 path=HERE/'glibc_image_io.py';fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
 state={'before':None,'closed':False};parents=[]
 def check():
  need(state['before']is not None and frame(path.lstat())==state['before']and path.resolve(strict=True)==path,'IO_source_changed')
  need(all(p.resolve(strict=True)==p and frame(p.lstat())[:5]==v for p,v in parents),'IO_parent_changed')
  if not state['closed']:need(frame(os.fstat(fd))==state['before']and(fcntl.fcntl(fd,fcntl.F_GETFL),fcntl.fcntl(fd,fcntl.F_GETFD))==flags
   and flags[1]==fcntl.FD_CLOEXEC and os.lseek(fd,0,os.SEEK_CUR)==0,'IO_source_OFD')
 def close():
  primary=None
  try:check()
  except BaseException as error:primary=error
  try:
   need(state['before']is None or frame(os.fstat(fd))[:6]==state['before'][:6],'IO_foreign_FD');os.close(fd);state['closed']=True
   try:os.fstat(fd)
   except OSError as error:need(error.errno==errno.EBADF,'IO_close_unknown')
   else:need(False,'IO_close_unknown')
   check()
  except BaseException:
   if primary is None:raise
   # Keep the first Source failure AND the independent physical-close outcome.
   # A refused operation never converts that unknown close into success.
   primary.secondary_finalizer_codes=['IO_close_unknown'];primary.Source_closure='UNKNOWN'
  if primary is not None:raise primary
 stack.callback(close) # Registered immediately after open, before metadata.
 state['before']=frame(os.fstat(fd));flags=(fcntl.fcntl(fd,fcntl.F_GETFL),fcntl.fcntl(fd,fcntl.F_GETFD))
 readonly_capsule=HERE==CAPSULE and bool(os.statvfs(CAPSULE).f_flag&os.ST_RDONLY)
 owners={0,os.geteuid()}|({1001}if readonly_capsule else set())
 need(stat.S_ISREG(state['before'][2])and state['before'][3]in owners and state['before'][5:7]==(1,IO_PIN[0])
  and not state['before'][2]&0o022,'IO_source_metadata')
 for p in path.parents:
  value=frame(p.lstat());parent_owners=owners if readonly_capsule and p==CAPSULE else{0,os.geteuid()}
  need(p.resolve(strict=True)==p and stat.S_ISDIR(value[2])and value[3]in parent_owners and not value[2]&0o022,'IO_parent_metadata');parents.append((p,value[:5]))
 raw=os.pread(fd,IO_PIN[0]+1,0);need((len(raw),sha256(raw).hexdigest())==IO_PIN,'IO_source_pin');check()
 module=ModuleType('_glibc_existing_installer_IO');module.__file__=str(path);exec(compile(raw,str(path),'exec'),module.__dict__);module.CAPSULE=CAPSULE
 return module,check

def transition(before,after):
 keys={name:next((key for key in before if key.split(':',1)[0]==name),None)for name in NAMES}
 need(all(keys.values())and all(before[key]==(OLD,'installed')for key in keys.values()),'baseline_version')
 expected=dict(before)
 for key in keys.values():expected[key]=(NEW,'installed')
 need(after==expected,'non_pair_package_changed');return keys

def output_pin(value):
 need(type(value)is dict and set(value)=={'bytes','sha256'}and type(value['bytes'])is int and 0<=value['bytes']<=1024**2,'review_output_binding')
 if value['bytes']==0:need(value['sha256']==sha256(b'').hexdigest(),'review_output_binding');return value
 return pin(value,1024**2)

def finite_vendor_review(value,qualified):
 """Closed data joins under the separately held literal Root receipt pin.

 This does not create a Root decision. Root reads actual raw operands before
 publishing that immutable record; worker success and pending flags cannot do it.
 """
 need(type(value)is dict and set(value)=={'schema','status','producer','evidence','raw_case_manifest','vendor_suite','raw_XPASS_names','cases','CVE','full_raw_sum_review','finality'}
  and value['schema']=='Root_finite_trixie241_vendor_review/1'and value['status']=='ACTUAL_SOURCE_AND_OUTPUT_REVIEW_COMPLETE'
  and value['producer']==qualified['producer']and value['evidence']==qualified['evidence'],'finite_review_subject')
 evidence=value['evidence']
 for label,limit in(('raw_libc_sum',16*1024**2),('raw_cases_manifest',131072),('raw_cases_archive',16*1024**2),('classification',131072),('evaluator',65536),('expectations',65536)):pin(evidence[label],limit)
 need(evidence['classification']==CLASSIFICATION_PIN and evidence['evaluator']==EVALUATOR_PIN and evidence['expectations']==EXPECTATIONS_PIN,'finite_review_source')
 document=value['raw_case_manifest'];suite=value['vendor_suite']
 for item,label in((document,'raw_cases_manifest'),(suite,'vendor_suite')):
  raw=canonical(item);need({'bytes':len(raw),'sha256':sha256(raw).hexdigest()}==evidence[label],'finite_review_raw_subject')
 need(type(document)is dict and set(document)=={'schema','profile','old_version','actual_full_build_returncode','merge_producer','classification','evaluator','expectations','raw_ledgers','archive','cases','additional_source_refs_actual','capture_refusals','raw_results_relabelled','vendor_qualification','Host_packages_eligible','security_accepted','Root_review'}
  and document['schema']=='two_profile_raw_fixed_vendor_evidence/1'and document['profile']=='trixie241'and document['old_version']==OLD
  and type(document['actual_full_build_returncode'])is int and document['actual_full_build_returncode']==0
  and document['capture_refusals']==[]and document['merge_producer']==MERGE_PRODUCER_PIN and document['classification']==CLASSIFICATION_PIN and document['evaluator']==EVALUATOR_PIN and document['expectations']==EXPECTATIONS_PIN
  and document['raw_results_relabelled']is False and document['Root_review']=='PENDING'
  and all(document[key]is False for key in('vendor_qualification','Host_packages_eligible','security_accepted'))
  and document['additional_source_refs_actual']==FIXED_ADDITIONAL_SOURCES
  and set(document['raw_ledgers'])=={'libc','i386','x32'}and document['raw_ledgers']['libc']=={'status':'CAPTURED',**evidence['raw_libc_sum']}and document['archive']==evidence['raw_cases_archive'],'finite_review_raw_subject')
 need(suite['schema']=='glibc84243_full_default_Debian_vendor_ledger/1'and suite['actual_command']==['dpkg-buildpackage','-b','-us','-uc','-rfakeroot']
  and type(suite['actual_returncode'])is int and suite['actual_returncode']==0 and suite['build_options']=='parallel=4'and suite['build_profiles']=='noudeb'
  and suite['no_test_body_or_filter_override']is True and suite['native_CVE_explicit_PASS_required']is True and set(suite['flavours'])=={'libc','i386','x32'},'finite_review_vendor')
 native=suite['flavours']['libc'];counts=native['counts'];need(type(native['tests'])is int and 1000<=native['tests']<=131072
  and type(counts)is dict and set(counts)<={'PASS','FAIL','XFAIL','XPASS','UNSUPPORTED'}and all(type(v)is int and 0<=v<=131072 for v in counts.values())
  and sum(counts.values())==native['tests']and counts.get('PASS',0)>=1 and counts.get('FAIL',0)==0 and native['CVE_regression_PASS']is True
  and native['raw_merge_subject']==evidence['raw_libc_sum']and native['tests_sum']==evidence['raw_libc_sum'],'finite_review_vendor')
 xpass=value['raw_XPASS_names'];need(type(xpass)is list and xpass==sorted(set(xpass))and set(xpass)<=set(FIXED_VENDOR_CASES)
  and native['raw_XPASS_names']==xpass and counts.get('XPASS',0)==len(xpass)
  and value['full_raw_sum_review']=='ALL_ACTUAL_ROWS_REVIEWED_NO_FAIL_OR_UNKNOWN_XPASS','finite_review_XPASS')
 for flavour in('i386','x32'):
  row=suite['flavours'][flavour];observed=document['raw_ledgers'][flavour]
  if row.get('vendor_default_test_execution_unqualified')is True:
   need(row=={'vendor_default_test_execution_unqualified':True,'production_runtime_scope':False}and observed['status']=='NOT_CREATED_ORIGINAL_VENDOR_DEFAULT','finite_review_flavour')
  else:
   need(type(row['tests'])is int and 1000<=row['tests']<=131072 and type(row['counts'])is dict and set(row['counts'])<={'PASS','FAIL','XFAIL','XPASS','UNSUPPORTED'}
    and all(type(n)is int and 0<=n<=131072 for n in row['counts'].values())and sum(row['counts'].values())==row['tests']and row['counts'].get('FAIL',0)==row['counts'].get('XPASS',0)==0
    and observed=={'status':'CAPTURED',**row['tests_sum']},'finite_review_flavour')
 need(type(document['cases'])is list and [row['test']for row in document['cases']]==[*FIXED_VENDOR_CASES,CVE_CASE]
  and type(value['cases'])is list and [row['test']for row in value['cases']]==list(FIXED_VENDOR_CASES),'finite_review_case_inventory')
 need(type(value['CVE'])is dict and value['CVE'].get('test')==CVE_CASE,'finite_review_CVE')
 observed_cases={row['test']:row for row in document['cases']};observed_xpass=[]
 for reviewed in[*value['cases'],value['CVE']]:
  need(type(reviewed)is dict and set(reviewed)=={'test','source','out','test_result','conclusion'},'finite_review_case_shape');name=reviewed['test']
  need(name in observed_cases and(name in FIXED_VENDOR_CASES or name==CVE_CASE),'finite_review_case_inventory');observed=observed_cases[name]
  need(type(observed)is dict and set(observed)=={'test','source_original','source_actual','prior_raw_state','current_body_extent','files','result'}
   and observed['prior_raw_state']==('EXPLICIT_CVE_PASS_REQUIRED'if name==CVE_CASE else'XPASS')
   and observed['current_body_extent']==('UNQUALIFIED_REQUIRES_ROOT_REVIEW'if name in EARLY_VENDOR_CASES else'NOT_RECLASSIFIED_FROM_RAW'),'finite_review_case_shape')
  expected=CVE_SOURCE if name==CVE_CASE else FIXED_VENDOR_SOURCES[name];expected_pin={key:expected[key]for key in('bytes','sha256')}
  need(observed['source_original']==expected and observed['source_actual']==expected_pin and reviewed['source']==expected_pin,'finite_review_case_source')
  need(observed['files']['.out']=={'status':'CAPTURED',**output_pin(reviewed['out'])}
   and observed['files']['.test-result']=={'status':'CAPTURED',**pin(reviewed['test_result'],65536)}and'result_refusal'not in observed,'finite_review_case_output')
  result=observed['result'];need(type(result)is dict and set(result)=={'raw_state','original_exit_status','xfail_true_proven'},'finite_review_case_shape');state=result['raw_state'];code=result['original_exit_status'];need(type(code)is int and 0<=code<=255,'finite_review_exit')
  if state in{'PASS','XPASS'}:
   need(code==0 and result['xfail_true_proven']is(state=='XPASS'),'finite_review_exit')
   permitted={'MEANINGFUL_BODY_COMPLETION_CONFIRMED'}if name in EARLY_VENDOR_CASES else{'MEANINGFUL_BODY_COMPLETION_CONFIRMED','SOURCE_OUTPUT_VENDOR_SUCCESS_CONFIRMED'}
  elif state=='XFAIL':need(code not in{0,77}and result['xfail_true_proven']is True,'finite_review_exit');permitted={'SOURCE_OUTPUT_EXPECTATION_CONFIRMED_XFAIL'}
  elif state=='UNSUPPORTED':need(code==77 and result['xfail_true_proven']is False,'finite_review_exit');permitted={'SOURCE_OUTPUT_EXPECTATION_CONFIRMED_UNSUPPORTED'}
  else:need(False,'finite_review_failed_or_unknown_state')
  if name==CVE_CASE:need(state=='PASS'and code==0 and value['CVE']['test']==CVE_CASE,'finite_review_CVE');permitted={'SOURCE_OUTPUT_EXPLICIT_CVE_PASS_CONFIRMED'}
  need(reviewed['conclusion']in permitted,'finite_review_body_unqualified')
  if state=='XPASS':observed_xpass.append(name)
 need(sorted(observed_xpass)==xpass,'finite_review_XPASS')
 final=value['finality'];need(type(final)is dict and set(final)=={'CI_exit_code','Source_and_export_readers_physically_closed','Root_observer_receipt','Root_conclusion'}
  and type(final['CI_exit_code'])is int and final['CI_exit_code']==0 and final['Source_and_export_readers_physically_closed']is True
  and final['Root_observer_receipt']==evidence['Source_finality']and final['Root_conclusion']=='ACTUAL_PROCESS_ZERO_EOF_AND_SOURCE_FINALITY_REVIEWED','finite_review_finality')
 return value

def qualified_manifest(value):
 need(ROOT_QUALIFICATION_PIN is not None,'future_qualification_UNBOUND')
 need(type(value)is dict and set(value)=={'schema','status','packages','payload','producer','evidence','finite_vendor_review','security_accepted','image_activation_authority'}
  and value['schema']=='Root_qualified_glibc_image_runtime_pair/2'and value['status']=='EXACT_DEBIAN_IMAGE_PAIR_QUALIFIED'
  and value['security_accepted']is False and value['image_activation_authority']is False,'Root_qualification_shape')
 need(type(value['evidence'])is dict and set(value['evidence'])==set(REVIEW_EVIDENCE),'Root_qualification_evidence')
 for row in value['evidence'].values():pin(row,32*1024**2)
 capsule=manifest({'schema':'glibc84243_image_runtime_pair_capsule/1','packages':value['packages'],'payload':value['payload'],
  'producer':value['producer'],'Root_qualification':ROOT_QUALIFICATION_PIN,'security_accepted':False})
 finite_vendor_review(value['finite_vendor_review'],value);return capsule

def verify_cache(I,held,cache,value):
 expected={'manifest.json','prepare_glibc_security_os_cache.py','glibc_image_io.py'}|{row['path']for row in value['packages']}
 need(cache.resolve(strict=True)==cache and stat.S_ISDIR(cache.lstat().st_mode)and stat.S_IMODE(cache.lstat().st_mode)==0o755
  and {p.name for p in cache.iterdir()}==expected,'capsule_inventory')
 owner=(cache.stat().st_uid,cache.stat().st_gid);before=frame(cache.lstat())
 raw=held.read(cache/'manifest.json',1024**2,MANIFEST_PIN,owner=owner)
 need(raw.raw==canonical(value),'manifest_changed')
 for row in value['packages']:held.read(cache/row['path'],32*1024**2,row['binding'],owner=owner)
 held.read(cache/'glibc_image_io.py',131072,{'bytes':IO_PIN[0],'sha256':IO_PIN[1]},owner=owner)
 held.read(cache/'prepare_glibc_security_os_cache.py',131072,owner=owner)
 def check():need(frame(cache.lstat())==before and {p.name for p in cache.iterdir()}==expected,'capsule_changed')
 check();held.final_checks.append(check);held.check()

def prepare_admitted(cache,package_dir,*,check_only=False):
 """Only two already-intaken public DEBs; no network or artifact permission."""
 need(MANIFEST_PIN is not None and ROOT_QUALIFICATION_PIN is not None,'future_qualification_UNBOUND')
 need(cache.name=='glibc-capsule'and cache.is_absolute()and package_dir.is_absolute(),'capsule_path')
 with ExitStack()as stack:
  I,sourcecheck=cold_io(stack)
  with I.Held()as held:
   qualified=held.read(package_dir/'Root-qualification.json',1024**2,ROOT_QUALIFICATION_PIN,owner=(os.geteuid(),os.getegid()))
   value=qualified_manifest(I.document(qualified.raw));raw=canonical(value)
   need({'bytes':len(raw),'sha256':sha256(raw).hexdigest()}==MANIFEST_PIN,'manifest_binding')
   sourcecheck();inputs={}
   inputs['prepare_glibc_security_os_cache.py']=held.read(Path(__file__).absolute(),131072,owner=(os.geteuid(),os.getegid())).raw
   inputs['glibc_image_io.py']=held.read(HERE/'glibc_image_io.py',131072,{'bytes':IO_PIN[0],'sha256':IO_PIN[1]},owner=(os.geteuid(),os.getegid())).raw
   for row in value['packages']:inputs[row['path']]=held.read(package_dir/row['path'],32*1024**2,row['binding'],owner=(os.geteuid(),os.getegid())).raw
   with I.CacheLock(cache)as busy:
    if cache.exists():verify_cache(I,held,cache,value);status='cache_verified'
    else:
     need(not check_only,'capsule_missing');stage=Path(tempfile.mkdtemp(prefix='.glibc-capsule-',dir=cache.parent));stage.chmod(0o755);identity=frame(stage.lstat())[:2]
     # Unknown prefixes remain under their unique owned name for diagnosis.
     # They are never adopted or silently reused by another invocation.
     created=[I.write_new(stage/name,body,held=held)for name,body in sorted({**inputs,'manifest.json':raw}.items())]
     with I.Held()as staged:verify_cache(I,staged,stage,value)
     sourcecheck();held.check();busy.check();I.rename_noreplace(stage,cache)
     need(not os.path.lexists(stage)and frame(cache.lstat())[:2]==identity,'capsule_publication')
     for row in created:
      need(row.path.parent==stage and frame(os.fstat(row.fd))==row.before,'capsule_publication')
      row.path=cache/row.path.name;row.parents=I.parents(row.path);row.check()
     verify_cache(I,held,cache,value);status='cache_prepared'
    sourcecheck();busy.check();held.check()
  sourcecheck()
 return {'status':status,'packages':list(NAMES),'version':NEW,'security_accepted':False}

def prepare(cache,package_dir,*,check_only=False):
 need(os.getuid()==os.geteuid()==1001 and os.environ.get('GITHUB_ACTIONS')=='true'
  and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted'and os.environ.get('GITHUB_REPOSITORY')=='mohsenbarari/trading_bot'
  and os.environ.get('GITHUB_REF')=='refs/heads/main','hosted_cache_only')
 temporary=Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
 need(package_dir is not None and package_dir.resolve(strict=True)==package_dir and package_dir.is_relative_to(temporary)
  and package_dir!=temporary,'qualified_inputs_path')
 return prepare_admitted(cache,package_dir,check_only=check_only)

def install(cache):
 need(MANIFEST_PIN is not None,'future_qualification_UNBOUND');need(cache==CAPSULE,'capsule_path')
 with ExitStack()as stack:
  I,sourcecheck=cold_io(stack);I.image_boundary(cache)
  def expired(*_):raise RuntimeError('installer_deadline')
  old=signal.signal(signal.SIGALRM,expired);signal.alarm(600);stack.callback(signal.signal,signal.SIGALRM,old);stack.callback(signal.alarm,0)
  with I.Held()as held:
   owner=(cache.stat().st_uid,cache.stat().st_gid);raw=held.read(cache/'manifest.json',1024**2,MANIFEST_PIN,owner=owner,cache_owner=owner)
   value=manifest(I.document(raw.raw));need({p.name for p in cache.iterdir()}=={'manifest.json','prepare_glibc_security_os_cache.py','glibc_image_io.py'}|{r['path']for r in value['packages']},'capsule_inventory')
   packages=[held.read(cache/row['path'],32*1024**2,row['binding'],owner=owner,cache_owner=owner)for row in value['packages']]
   def check():sourcecheck();held.check();need(os.statvfs(cache).f_flag&os.ST_RDONLY!=0,'capsule_not_readonly')
   paths=[str(row.path)for row in packages]
   allowed={I.INVENTORY,SOURCE_INVENTORY,('/usr/bin/dpkg','--audit'),('/usr/bin/dpkg',*DOC_FLAGS,'--install',*paths),
    *(('/usr/bin/dpkg-deb','-f',path,field)for path in paths for field in('Package','Version','Architecture','Source'))}
   need(ctypes.CDLL(None,use_errno=True).prctl(36,1,0,0,0)==0,'installer_cleanup')
   before=I.package_inventory(I.bounded(list(I.INVENTORY),allowed,guard=check))
   source_rows=I.bounded(list(SOURCE_INVENTORY),allowed,guard=check).decode('ascii').splitlines();family=[]
   for line in source_rows:
    fields=line.split('\t');need(len(fields)==2,'source_inventory')
    if fields[1]=='glibc':family.append(fields[0].split(':',1)[0])
   need(sorted(family)==sorted(NAMES),'runtime_pair_only')
   for row,path in zip(value['packages'],paths):
    for field,expected in [('Package',row['name']),('Version',NEW),('Architecture','amd64')]:
     need(I.bounded(['/usr/bin/dpkg-deb','-f',path,field],allowed,guard=check).decode('ascii').strip()==expected,'package_control')
    need(I.bounded(['/usr/bin/dpkg-deb','-f',path,'Source'],allowed,guard=check).decode('ascii').strip()in{'glibc','glibc ('+NEW+')'},'package_control')
   # Old target FDs are deliberately closed before our own replacement; only
   # immutable capsule/source descriptors span the transition.
   with I.Held()as old_payload:
    for name,path in TARGETS.items():old_payload.read(Path(path),32*1024**2,OLD_PAYLOAD[name],owner=(0,0))
   need(I.bounded(['/usr/bin/dpkg','--audit'],allowed,guard=check).strip()==b'','package_audit')
   # Validate old versions before mutation, rather than after an avoidable stop.
   transition(before,{key:((NEW,'installed')if key.split(':',1)[0]in NAMES else version)for key,version in before.items()})
   I.bounded(['/usr/bin/dpkg',*DOC_FLAGS,'--install',*paths],allowed,guard=check)
   after=I.package_inventory(I.bounded(list(I.INVENTORY),allowed,guard=check));transition(before,after)
   need(I.bounded(['/usr/bin/dpkg','--audit'],allowed,guard=check).strip()==b'','package_audit')
   with I.Held()as current:
    for name,path in TARGETS.items():current.read(Path(path),32*1024**2,value['payload'][name],owner=(0,0))
   check()
  sourcecheck()
 return {'status':'EXACT_GLIBC_RUNTIME_PAIR_INSTALLED','packages':list(NAMES),'version':NEW,'other_package_inventory_preserved':True,'security_accepted':False}

def main(argv=None):
 parser=argparse.ArgumentParser();mode=parser.add_mutually_exclusive_group();mode.add_argument('--install-image-cache',action='store_true');mode.add_argument('--check',action='store_true')
 parser.add_argument('--cache-dir',type=Path);parser.add_argument('--package-dir',type=Path);args=parser.parse_args(argv)
 if args.cache_dir is None or MANIFEST_PIN is None or ROOT_QUALIFICATION_PIN is None:
  print('{"status":"FUTURE_QUALIFICATION_UNBOUND","installed":false,"security_accepted":false}');return 1 if args.cache_dir is not None else 0
 try:result=install(args.cache_dir)if args.install_image_cache else prepare(args.cache_dir,args.package_dir,check_only=args.check)
 except BaseException as error:
  code=str(error)if type(error)is RuntimeError and re.fullmatch('[A-Za-z0-9_]{1,80}',str(error))else'unknown_refusal'
  print(json.dumps({'status':'REFUSED','code':code,'secondary_codes':getattr(error,'secondary_finalizer_codes',[]),
   'Source_closure':getattr(error,'Source_closure','NOT_CLAIMED'),'retry_allowed':False,'security_accepted':False}));return 1
 print(json.dumps(result,sort_keys=True));return 0
if __name__=='__main__':raise SystemExit(main())
