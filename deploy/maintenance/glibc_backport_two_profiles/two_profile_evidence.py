"""Fixed two-profile raw test evidence; no operations on import or generic waiver."""
from pathlib import Path
from hashlib import sha256
from collections import Counter
from types import SimpleNamespace
import errno,fcntl,io,json,os,re,stat,tarfile

PROFILES={'trixie241': {'name': 'trixie241', 'old_version': '2.41-12+deb13u4', 'source_prefix': 'glibc-2.41/', 'classification': (12385, '29a094e9626537e10345d1c66c20cf8a232ea89507fdfbe5d19387c16fc3256c'), 'cases': ('elf/tst-ifunc-isa-1', 'elf/tst-ifunc-isa-1-static', 'elf/tst-ifunc-isa-2', 'elf/tst-ifunc-isa-2-static', 'nptl/tst-create-detached', 'nptl/tst-robust8', 'rt/tst-cpuclock2', 'rt/tst-cputimer1', 'rt/tst-timer', 'support/tst-support_descriptors'), 'evaluator': (1243, '7e8b09577d6a4620e98c60fbc1c3678abdd53e5e6d218aafe2144f197ccc0397'), 'expectations': (25227, '0554f6b6497f74bc5291c529d43f2a6ee023f838363c45d55de4909dfea0ba80'), 'merge_producer': (1919, '2c9199c21a9897f75d26aca4b84fa3bb5f9accc1414977e2378bf396c59f06c8')}, 'ubuntu243': {'name': 'ubuntu243', 'old_version': '2.43-2ubuntu2.4', 'source_prefix': 'glibc-2.43/', 'classification': (19644, '27c1ae50b2bd42cce35c699e36f310cd80068c2b0d60d72d17a520f746683a16'), 'cases': ('elf/tst-cpu-features-cpuinfo', 'elf/tst-cpu-features-cpuinfo-static', 'elf/tst-ifunc-isa-1', 'elf/tst-ifunc-isa-1-static', 'elf/tst-ifunc-isa-2', 'elf/tst-ifunc-isa-2-static', 'nptl/tst-create-detached', 'nptl/tst-robust8', 'nptl/tst-stack4', 'rt/tst-cpuclock2', 'rt/tst-cputimer1', 'rt/tst-timer', 'stdlib/tst-getrandom', 'support/tst-support_descriptors'), 'evaluator': (1243, '14bd21815cc132cf151ed1cf68f44cfe7cf23cdaa397af3b33fdc8dfc3352752'), 'expectations': (27267, '4337298acc05e3ab033cf373fb45964d5a943912ae1dbe967521db25a2d35eb7'), 'merge_producer': (1919, 'fcacf5a61cbf6f9cfe3d41695874b9d90f885c14c4582068239d6947c4a00f48')}}
REGRESSION='intl/tst-gettext-path-traversal'
REGRESSION_SOURCE=(3336,'c732e301bbe037a6aa612f1452d7768d0bb3efd893f4fbf1d4d02e4651e2d3f6')
EARLY=('nptl/tst-robust8','rt/tst-cpuclock2','rt/tst-cputimer1')
FLAVOURS=('libc','i386','x32')
RAW_NAMES={'two-profile-raw-tests.json':131072,'two-profile-raw-tests.tar.xz':16*1024**2,**{'two-profile-raw-'+flavour+'.sum':16*1024**2 for flavour in FLAVOURS}}
EMPTY_RAW_NAMES=frozenset('two-profile-raw-'+flavour+'.sum'for flavour in FLAVOURS)
STATES={'PASS','FAIL','XFAIL','XPASS','UNSUPPORTED'}
FORMAT=None
def need(ok,code):
 if ok is not True:raise RuntimeError(code)
def frame(s):return(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def pin(raw):return{'bytes':len(raw),'sha256':sha256(raw).hexdigest()}
def enc(value):return(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
def same_pin(raw,expected):need((len(raw),sha256(raw).hexdigest())==expected,'two_profile_source_pin')
def close_owned(fd):
 os.close(fd)
 try:os.fstat(fd)
 except OSError as error:need(error.errno==errno.EBADF,'two_profile_close_unknown')
 else:need(False,'two_profile_close_unknown')
def read_regular(path,maximum,*,empty=False):
 path=Path(path);parents=[(p,frame(p.lstat())[:5])for p in path.parents]
 need(path.resolve(strict=True)==path and all(p.resolve(strict=True)==p and stat.S_ISDIR(value[2])for p,value in parents),'two_profile_file_changed')
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
 primary=None;answer=None;secondary=[]
 try:
  before=frame(os.fstat(fd));flags=(fcntl.fcntl(fd,fcntl.F_GETFL),fcntl.fcntl(fd,fcntl.F_GETFD))
  need(stat.S_ISREG(before[2])and before[5]==1 and (0 if empty else 1)<=before[6]<=maximum,'two_profile_file_bound')
  need(flags[1]==fcntl.FD_CLOEXEC and os.lseek(fd,0,os.SEEK_CUR)==0,'two_profile_OFD')
  raw=bytearray();offset=0
  while offset<before[6]:
   block=os.pread(fd,min(65536,before[6]-offset),offset);need(len(block)>0 and len(raw)+len(block)<=maximum,'two_profile_read_bound');raw.extend(block);offset+=len(block)
  need(os.pread(fd,1,offset)==b''and frame(os.fstat(fd))==frame(path.lstat())==before
   and(fcntl.fcntl(fd,fcntl.F_GETFL),fcntl.fcntl(fd,fcntl.F_GETFD))==flags and os.lseek(fd,0,os.SEEK_CUR)==0
   and path.resolve(strict=True)==path
   and all(p.resolve(strict=True)==p and frame(p.lstat())[:5]==value and stat.S_ISDIR(value[2])for p,value in parents),'two_profile_file_changed')
  answer=bytes(raw)
 except BaseException as error:primary=error
 try:close_owned(fd)
 except BaseException:
  secondary.append('two_profile_close_unknown')
 if primary is not None:
  primary.two_profile_secondary_codes=secondary;raise primary
 need(not secondary,'two_profile_close_unknown');return answer
def rows(raw):
 need(FORMAT is not None,'two_profile_vendor_format_unbound')
 parsed=FORMAT.parse_merge_ledger(raw);result={name:state for state,name in parsed['rows']}
 need(len(result)>=1000 and len(result)==len(parsed['rows']),'two_profile_ledger_incomplete');return result
def result_row(raw,name):
 need(type(raw)is bytes and 0<len(raw)<=65536,'two_profile_result_bound')
 m=re.fullmatch(r'(PASS|FAIL|XFAIL|XPASS|UNSUPPORTED): '+re.escape(name)+r'\noriginal exit status ([0-9]{1,3})\n',raw.decode())
 need(m is not None,'two_profile_result_shape');state,code=m[1],int(m[2]);need(code<=255,'two_profile_result_shape')
 need((state=='UNSUPPORTED'and code==77)or(state in{'PASS','XPASS'}and code==0)or(state in{'FAIL','XFAIL'}and code not in{0,77}),'two_profile_result_exit')
 return {'raw_state':state,'original_exit_status':code,'xfail_true_proven':state in{'XFAIL','XPASS'}}
def write_new(path,raw,maximum,*,empty=False):
 need(type(raw)is bytes and (0 if empty else 1)<=len(raw)<=maximum,'two_profile_output_bound')
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o644);primary=None
 try:
  os.fchmod(fd,0o644);view=memoryview(raw)
  while view:
   n=os.write(fd,view[:65536]);need(n>0,'two_profile_short_write');view=view[n:]
  os.fsync(fd)
 except BaseException as error:primary=error
 try:close_owned(fd)
 except BaseException:
  if primary is not None:primary.two_profile_secondary_codes=['two_profile_close_unknown']
  else:raise
 if primary is not None:raise primary
class BoundedArchive:
 def __init__(self,path):
  self.fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o644);self.n=0
  try:os.fchmod(self.fd,0o644)
  except BaseException as primary:
   try:close_owned(self.fd)
   except BaseException:primary.two_profile_secondary_codes=['two_profile_close_unknown']
   raise
 def write(self,raw):
  need(type(raw)is bytes and self.n+len(raw)<=RAW_NAMES['two-profile-raw-tests.tar.xz'],'two_profile_archive_bound')
  view=memoryview(raw)
  while view:
   count=os.write(self.fd,view[:65536]);need(count>0,'two_profile_short_write');view=view[count:]
  self.n+=len(raw);return len(raw)
 def flush(self):pass
 def close(self):
  primary=None
  try:os.fsync(self.fd)
  except BaseException as error:primary=error
  try:close_owned(self.fd)
  except BaseException:
   if primary is not None:primary.two_profile_secondary_codes=['two_profile_close_unknown']
   else:raise
  if primary is not None:raise primary
def subject(classification):
 need(type(classification)is bytes,'two_profile_classification_type')
 matches=[value for value in PROFILES.values()if value is not None and(len(classification),sha256(classification).hexdigest())==value['classification']]
 need(len(matches)==1,'two_profile_classification_pin');profile=matches[0];table=json.loads(classification)
 need(tuple(row['test']for row in table['rows'])==profile['cases'],'two_profile_case_table')
 return profile,table

def capture(source,out,build_code,classification):
 """Retain every fixed raw sum and case output before semantic acceptance."""
 source=Path(source);out=Path(out);profile,table=subject(classification);native=source/'build-tree/amd64-libc';files=[];entries=[];errors=[];ledgers={}
 for flavour in FLAVOURS:
  path=source/('build-tree/amd64-'+flavour)/'tests.sum';name='two-profile-raw-'+flavour+'.sum'
  try:
   raw=read_regular(path,RAW_NAMES[name],empty=True);write_new(out/name,raw,RAW_NAMES[name],empty=True)
   ledgers[flavour]={'status':'CAPTURED',**pin(raw)}
  except FileNotFoundError:
   ledgers[flavour]={'status':'NOT_CREATED_ORIGINAL_VENDOR_DEFAULT','vendor_test_execution_qualified':False,'production_runtime_scope':flavour=='libc'}
   if flavour=='libc':errors.append('two_profile_native_ledger_missing')
 for case in(*profile['cases'],REGRESSION):
  expected_source=next(row['source']for row in table['rows']if row['test']==case)if case!=REGRESSION else{'bytes':REGRESSION_SOURCE[0],'sha256':REGRESSION_SOURCE[1],'member':profile['source_prefix']+REGRESSION}
  if case==REGRESSION:expected_source['member']+='.c'
  entry={'test':case,'source_original':expected_source,
   'prior_raw_state':'XPASS'if case!=REGRESSION else'EXPLICIT_CVE_PASS_REQUIRED','current_body_extent':'UNQUALIFIED_REQUIRES_ROOT_REVIEW'if case in EARLY else'NOT_RECLASSIFIED_FROM_RAW','files':{}}
  for suffix in('.out','.test-result'):
   name=case+suffix;maximum=1024**2 if suffix=='.out'else 65536
   try:
    raw=read_regular(native/name,maximum,empty=suffix=='.out');files.append((name,raw));entry['files'][suffix]={'status':'CAPTURED',**pin(raw)}
    if suffix=='.test-result':
     try:entry['result']=result_row(raw,case)
     except (RuntimeError,UnicodeError):entry['result_refusal']='two_profile_result_shape';errors.append('two_profile_result_shape')
   except FileNotFoundError:entry['files'][suffix]={'status':'MISSING'};errors.append('two_profile_case_file_missing')
  entries.append(entry)
 archive=BoundedArchive(out/'two-profile-raw-tests.tar.xz');primary=None;tar=None;secondary=[]
 try:
  tar=tarfile.open(fileobj=archive,mode='w|xz')
  for name,raw in files:
   info=tarfile.TarInfo(name);info.size=len(raw);info.mode=0o644;info.uid=info.gid=info.mtime=0;tar.addfile(info,io.BytesIO(raw))
 except BaseException as error:primary=error
 try:
  if tar is not None:tar.close()
 except BaseException as error:
  if primary is not None:secondary.append('two_profile_tar_close_unknown')
  else:primary=error
 try:archive.close()
 except BaseException as error:
  if primary is not None:secondary.append('two_profile_archive_close_unknown')
  else:primary=error
 if primary is not None:
  primary.two_profile_secondary_codes=getattr(primary,'two_profile_secondary_codes',[])+secondary;raise primary
 # Source joins follow raw retention; an unknown source can never grant pending
 # qualification, and its refusal cannot erase already-captured test outputs.
 evaluator=read_regular(source/'scripts/evaluate-test.sh',65536);expectations=read_regular(source/'debian/testsuite-xfail-debian.mk',65536);merge=read_regular(source/'scripts/merge-test-results.sh',65536)
 source_refs={}
 for key,value in table.get('additional_source_refs',{}).items():
  member=value['member'];need(member.startswith(profile['source_prefix'])and'..'not in member.split('/'),'two_profile_source_path')
  source_refs[key]=pin(read_regular(source/member.removeprefix(profile['source_prefix']),1024**2))
 for entry in entries:
  member=entry['source_original']['member'];need(member.startswith(profile['source_prefix'])and'..'not in member.split('/'),'two_profile_source_path')
  entry['source_actual']=pin(read_regular(source/member.removeprefix(profile['source_prefix']),1024**2))
 document={'schema':'two_profile_raw_fixed_vendor_evidence/1','profile':profile['name'],'old_version':profile['old_version'],'actual_full_build_returncode':build_code,
  'merge_producer':pin(merge),'classification':pin(classification),'evaluator':pin(evaluator),'expectations':pin(expectations),'raw_ledgers':ledgers,
  'archive':pin(read_regular(out/'two-profile-raw-tests.tar.xz',RAW_NAMES['two-profile-raw-tests.tar.xz'])),
  'cases':entries,'additional_source_refs_actual':source_refs,'capture_refusals':errors,'raw_results_relabelled':False,'vendor_qualification':False,
  'Host_packages_eligible':False,'security_accepted':False,'Root_review':'PENDING'}
 write_new(out/'two-profile-raw-tests.json',enc(document),RAW_NAMES['two-profile-raw-tests.json']);return document

def review(raw,document,classification):
 """Only the exact own-profile finite rc0 XPASS records may remain pending."""
 profile,table=subject(classification);trusted_sources={row['test']:row['source']for row in table['rows']};trusted_sources[REGRESSION]={'bytes':REGRESSION_SOURCE[0],'sha256':REGRESSION_SOURCE[1],'member':profile['source_prefix']+REGRESSION+'.c'};need(FORMAT is not None,'two_profile_vendor_format_unbound')
 merged=FORMAT.parse_merge_ledger(raw);parsed=rows(raw)
 need(document['schema']=='two_profile_raw_fixed_vendor_evidence/1'and document['profile']==profile['name']and document['old_version']==profile['old_version']
  and type(document['actual_full_build_returncode'])is int and document['actual_full_build_returncode']==0 and document['capture_refusals']==[]
  and document['classification']=={'bytes':profile['classification'][0],'sha256':profile['classification'][1]}
  and document['merge_producer']=={'bytes':profile['merge_producer'][0],'sha256':profile['merge_producer'][1]}
  and document['evaluator']=={'bytes':profile['evaluator'][0],'sha256':profile['evaluator'][1]}
  and document['expectations']=={'bytes':profile['expectations'][0],'sha256':profile['expectations'][1]}
  and document['raw_ledgers'].get('libc')=={'status':'CAPTURED',**pin(raw)},'two_profile_evidence_subject')
 need(tuple(row['test']for row in document['cases'])==(*profile['cases'],REGRESSION),'two_profile_case_table')
 need(all(state!='FAIL'for state in parsed.values()),'vendor_regression_failed');xpass={name for name,state in parsed.items()if state=='XPASS'};need(xpass<=set(profile['cases']),'two_profile_unknown_XPASS')
 need(parsed.get('intl/tst-gettext-path-traversal')=='PASS','new_regression_not_executed')
 refs={key:{field:value[field]for field in('bytes','sha256')}for key,value in table.get('additional_source_refs',{}).items()}
 need(document.get('additional_source_refs_actual')==refs,'two_profile_case_source_changed')
 for row in document['cases']:
  case=row['test'];need(all(row['files'].get(suffix,{}).get('status')=='CAPTURED'for suffix in('.out','.test-result'))and'result_refusal'not in row,'two_profile_case_file_missing')
  need(row['source_original']==trusted_sources[case]and row['source_actual']=={key:trusted_sources[case][key]for key in('bytes','sha256')},'two_profile_case_source_changed')
  result=row.get('result');need(type(result)is dict and result['raw_state']==parsed.get(case),'two_profile_result_ledger_mismatch')
  if case==REGRESSION:need(result['raw_state']=='PASS'and type(result['original_exit_status'])is int and result['original_exit_status']==0,'new_regression_not_executed')
  if result['raw_state']=='XPASS':need(type(result['original_exit_status'])is int and result['original_exit_status']==0 and result['xfail_true_proven']is True,'two_profile_result_exit')
 return {'counts':dict(sorted(Counter(parsed.values()).items())),'tests':len(parsed),'CVE_regression_PASS':True,'default_XFAIL_UNSUPPORTED_retained':True,
  'test_body_selection_or_skip_override':False,'raw_XPASS_names':sorted(xpass),'merge_sections':list(merged['sections']),
  'case_merge_sections':{name:section for(_,name),section in zip(merged['rows'],merged['row_sections'])if name in profile['cases']},'raw_merge_subject':merged['raw'],
  'disposition':'ROOT_REVIEW_PENDING','Root_review':'PENDING','body_extent_unqualified_cases':[case for case in EARLY if case in profile['cases']],
  'vendor_qualification':False,'Host_packages_eligible':False,'security_accepted':False}

def failure_manifest_script():
 """Only these three public fixed leaves; no recursive or package export."""
 return r'''from pathlib import Path
from hashlib import sha256
import os,stat,json
limits='''+repr(RAW_NAMES)+r'''
empty='''+repr(sorted({'two-profile-raw-'+flavour+'.sum'for flavour in('libc','i386','x32')}))+r'''
rows={}
for name,limit in limits.items():
 p=Path('/out')/name
 try:fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
 except FileNotFoundError:continue
 try:
  s=os.fstat(fd);key=lambda t:(t.st_dev,t.st_ino,t.st_mode,t.st_uid,t.st_gid,t.st_nlink,t.st_size,t.st_mtime_ns,t.st_ctime_ns)
  before=key(s);assert stat.S_ISREG(s.st_mode)and s.st_uid==s.st_gid==0 and s.st_nlink==1 and stat.S_IMODE(s.st_mode)==0o644 and (0 if name in empty else 1)<=s.st_size<=limit
  h=sha256();n=0
  while True:
   b=os.read(fd,min(65536,limit-n+1))
   if not b:break
   n+=len(b);assert n<=limit;h.update(b)
  assert n==s.st_size and key(os.fstat(fd))==key(p.lstat())==before
  rows[name]={'bytes':n,'sha256':h.hexdigest()}
 finally:os.close(fd)
print(json.dumps(rows,sort_keys=True))'''
