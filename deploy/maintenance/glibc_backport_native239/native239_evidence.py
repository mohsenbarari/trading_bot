"""Fixed Ubuntu239 raw test evidence; no operations on import or generic waiver."""
from pathlib import Path
from hashlib import sha256
from collections import Counter
from types import SimpleNamespace
import errno,fcntl,io,json,os,re,stat,tarfile

CASES=('elf/tst-cpu-features-cpuinfo','elf/tst-cpu-features-cpuinfo-static',
 'elf/tst-ifunc-isa-1','elf/tst-ifunc-isa-1-static','elf/tst-ifunc-isa-2','elf/tst-ifunc-isa-2-static',
 'nptl/tst-create-detached','nptl/tst-robust8','nptl/tst-stack4',
 'rt/tst-cpuclock2','rt/tst-cputimer1','rt/tst-timer','stdlib/tst-getrandom')
EARLY=('nptl/tst-robust8','rt/tst-cpuclock2','rt/tst-cputimer1')
CLASSIFICATION=(15662,'e275c91e7e983fad217ccef6406f4a12bb95b90ac9508b94ace5e051f69be09f')
EVALUATOR=(1243,'fefaba26b61c19d6ca76ad00826ebe2c1bea9c5d345be3bccf489ef996b812ed')
EXPECTATIONS=(29265,'786d5a8142cf0994b92c2ee7e85870757e83eb6505271f9fe17ba565ce72650e')
RAW_NAMES={'native239-raw-tests.json':131072,'native239-raw-tests.tar.xz':16*1024**2,
 'native239-raw-libc.sum':16*1024**2}
STATES={'PASS','FAIL','XFAIL','XPASS','UNSUPPORTED'}
FORMAT=None
OLD_VERSION='2.39-0ubuntu8.9';NEW_VERSION=OLD_VERSION+'+gold1'
FAMILY=('libc6','libc-bin','libc-dev-bin','libc-devtools','libc6-dev','locales')
QUARANTINE_DEBS=tuple(name+'_'+NEW_VERSION+'_'+('all'if name=='locales'else'amd64')+'.deb'for name in FAMILY)
QUARANTINE_PROOFS=('source-proof.json','vendor-suite.json','old-counterfactual.json','old-counterfactual.log','preparation.json','provenance.json')
QUARANTINE_NAMES={**RAW_NAMES,'native239-payload-layout.json':4*1024**2,**{name:32*1024**2 for name in QUARANTINE_DEBS},**{name:16*1024**2 for name in QUARANTINE_PROOFS}}
QUARANTINE_MAX=192*1024**2;QUARANTINE_DEBS_MAX=128*1024**2;QUARANTINE_METADATA_MAX=16*1024**2
ABI_OBSERVATION_BYTES=0

def payload_layout(root):
 """Same five metadata values as the original guard, with stable bounded reads."""
 root=Path(root);before=frame(root.lstat());result={};originals=[];total=0
 for path in root.rglob('*'):
  need(len(result)<32768,'payload_layout_bound');relative=str(path.relative_to(root));s=path.lstat();initial=frame(s)
  row={'type':stat.S_IFMT(s.st_mode),'mode':stat.S_IMODE(s.st_mode),'uid':s.st_uid,'gid':s.st_gid,'link':os.readlink(path)if stat.S_ISLNK(s.st_mode)else None}
  size=len(enc({relative:row}));need(total+size<=1024**2,'payload_layout_bound');total+=size
  need(frame(path.lstat())==initial,'payload_layout_changed');result[relative]=row;originals.append((path,initial,row['link']))
 need(frame(root.lstat())==before,'payload_layout_changed')
 for path,initial,link in originals:
  need(frame(path.lstat())==initial and(link is None or os.readlink(path)==link),'payload_layout_changed')
 return result

def capture_layout(out,old,new,old_packages,new_packages,context):
 need(type(old)is dict and type(new)is dict,'payload_layout_shape')
 expected_old={name+'_'+OLD_VERSION+'_'+('all'if name=='locales'else'amd64')+'.deb'for name in FAMILY}
 need(set(old_packages)==expected_old and set(new_packages)==set(QUARANTINE_DEBS),'payload_layout_family')
 for collection in(old_packages,new_packages):
  for filename,row in collection.items():
   need(type(row)is dict and type(row.get('bytes'))is int and 0<row['bytes']<=32*1024**2 and re.fullmatch('[a-f0-9]{64}',row.get('sha256',''))is not None,'payload_layout_package_pin')
 need(sum(row['bytes']for row in new_packages.values())<=QUARANTINE_DEBS_MAX,'payload_layout_package_bound')
 differences=[]
 for name in sorted(set(old)|set(new)):
  if old.get(name)!=new.get(name):differences.append({'path':name,'old':{'status':'PRESENT','metadata':old[name]}if name in old else{'status':'ABSENT'},'new':{'status':'PRESENT','metadata':new[name]}if name in new else{'status':'ABSENT'},'fields':sorted(k for k in set(old.get(name,{}))|set(new.get(name,{}))if old.get(name,{}).get(k)!=new.get(name,{}).get(k))})
 refs={}
 for name in QUARANTINE_PROOFS:
  try:refs[name]={'status':'PRESENT',**pin(read_regular(Path(out)/name,QUARANTINE_NAMES[name]))}
  except FileNotFoundError:refs[name]={'status':'ABSENT'}
 document={'schema':'Native239_quarantined_payload_layout/1','disposition':'QUARANTINED_NOT_QUALIFIED','old':old,'new':new,'delta':differences,'counts':{'old':len(old),'new':len(new),'changed_paths':len(differences)},'old_packages':old_packages,'new_packages':new_packages,'source_context':context,'preexisting_proofs':refs,'layout_equal':old==new,'vendor_qualification':False,'Host_packages_eligible':False,'security_accepted':False}
 raw=enc(document);write_new(Path(out)/'native239-payload-layout.json',raw,QUARANTINE_NAMES['native239-payload-layout.json']);return document

def abi_observation(phase,path,facts):
 """Finite public operand prefixes in the existing bounded full-build log."""
 global ABI_OBSERVATION_BYTES
 need(phase in{'headers','properties','dynsym','inventory'}and type(path)is str and len(path)<=4096 and not path.startswith('/')and'..'not in Path(path).parts,'ABI_observation_scope')
 raw=enc({'schema':'Native239_public_ABI_operand/1','phase':phase,'path':path,'facts':facts,'disposition':'QUARANTINED_NOT_QUALIFIED','security_accepted':False})
 need(len(raw)<=1024**2 and ABI_OBSERVATION_BYTES+len(raw)<=32*1024**2,'ABI_observation_bound');ABI_OBSERVATION_BYTES+=len(raw);print(raw.decode(),end='',flush=True)
MERGE_PRODUCER=(1919,'5b65a8b05c2efbe927cea77d796ffcde38013ba71f42781556027e64711169a6')
def need(ok,code):
 if ok is not True:raise RuntimeError(code)
def frame(s):return(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def pin(raw):return{'bytes':len(raw),'sha256':sha256(raw).hexdigest()}
def enc(value):return(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
def same_pin(raw,expected):need((len(raw),sha256(raw).hexdigest())==expected,'native239_source_pin')
def close_owned(fd):
 os.close(fd)
 try:os.fstat(fd)
 except OSError as error:need(error.errno==errno.EBADF,'native239_close_unknown')
 else:need(False,'native239_close_unknown')
def read_regular(path,maximum,*,empty=False,guard=lambda:None):
 guard();path=Path(path);parents=[(p,frame(p.lstat())[:5])for p in path.parents]
 need(path.resolve(strict=True)==path and all(p.resolve(strict=True)==p and stat.S_ISDIR(value[2])for p,value in parents),'native239_file_changed')
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
 primary=None;answer=None;secondary=[]
 try:
  before=frame(os.fstat(fd));flags=(fcntl.fcntl(fd,fcntl.F_GETFL),fcntl.fcntl(fd,fcntl.F_GETFD))
  need(stat.S_ISREG(before[2])and before[5]==1 and (0 if empty else 1)<=before[6]<=maximum,'native239_file_bound')
  need(flags[1]==fcntl.FD_CLOEXEC and os.lseek(fd,0,os.SEEK_CUR)==0,'native239_OFD')
  raw=bytearray();offset=0
  while offset<before[6]:
   guard();block=os.pread(fd,min(65536,before[6]-offset),offset);need(len(block)>0 and len(raw)+len(block)<=maximum,'native239_read_bound');raw.extend(block);offset+=len(block)
  need(os.pread(fd,1,offset)==b''and frame(os.fstat(fd))==frame(path.lstat())==before
   and(fcntl.fcntl(fd,fcntl.F_GETFL),fcntl.fcntl(fd,fcntl.F_GETFD))==flags and os.lseek(fd,0,os.SEEK_CUR)==0
   and path.resolve(strict=True)==path
   and all(p.resolve(strict=True)==p and frame(p.lstat())[:5]==value and stat.S_ISDIR(value[2])for p,value in parents),'native239_file_changed')
  guard();answer=bytes(raw)
 except BaseException as error:primary=error
 try:close_owned(fd)
 except BaseException:
  secondary.append('native239_close_unknown')
 if primary is not None:
  primary.native239_secondary_codes=secondary;raise primary
 need(not secondary,'native239_close_unknown');guard();return answer
def rows(raw):
 need(FORMAT is not None,'native239_vendor_format_unbound')
 parsed=FORMAT.parse_merge_ledger(raw);result={name:state for state,name in parsed['rows']}
 need(len(result)>=1000 and len(result)==len(parsed['rows']),'native239_ledger_incomplete');return result
def result_row(raw,name):
 need(type(raw)is bytes and 0<len(raw)<=65536,'native239_result_bound')
 m=re.fullmatch(r'(PASS|FAIL|XFAIL|XPASS|UNSUPPORTED): '+re.escape(name)+r'\noriginal exit status ([0-9]{1,3})\n',raw.decode())
 need(m is not None,'native239_result_shape');state,code=m[1],int(m[2]);need(code<=255,'native239_result_shape')
 need((state=='UNSUPPORTED'and code==77)or(state in{'PASS','XPASS'}and code==0)or(state in{'FAIL','XFAIL'}and code not in{0,77}),'native239_result_exit')
 return {'raw_state':state,'original_exit_status':code,'xfail_true_proven':state in{'XFAIL','XPASS'}}
def write_new(path,raw,maximum):
 need(type(raw)is bytes and 0<len(raw)<=maximum,'native239_output_bound')
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o644);primary=None
 try:
  os.fchmod(fd,0o644);view=memoryview(raw)
  while view:
   n=os.write(fd,view[:65536]);need(n>0,'native239_short_write');view=view[n:]
  os.fsync(fd)
 except BaseException as error:primary=error
 try:close_owned(fd)
 except BaseException:
  if primary is not None:primary.native239_secondary_codes=['native239_close_unknown']
  else:raise
 if primary is not None:raise primary
class BoundedArchive:
 def __init__(self,path):
  self.fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o644);self.n=0
  try:os.fchmod(self.fd,0o644)
  except BaseException as primary:
   try:close_owned(self.fd)
   except BaseException:primary.native239_secondary_codes=['native239_close_unknown']
   raise
 def write(self,raw):
  need(type(raw)is bytes and self.n+len(raw)<=RAW_NAMES['native239-raw-tests.tar.xz'],'native239_archive_bound')
  view=memoryview(raw)
  while view:
   count=os.write(self.fd,view[:65536]);need(count>0,'native239_short_write');view=view[count:]
  self.n+=len(raw);return len(raw)
 def flush(self):pass
 def close(self):
  primary=None
  try:os.fsync(self.fd)
  except BaseException as error:primary=error
  try:close_owned(self.fd)
  except BaseException:
   if primary is not None:primary.native239_secondary_codes=['native239_close_unknown']
   else:raise
  if primary is not None:raise primary
def preflight_vendor_inputs(source,classification):
 """Fail early on the same sixteen pinned inputs; no vendor-result authority."""
 source=Path(source);same_pin(classification,CLASSIFICATION);table=json.loads(classification)
 need(tuple(row['test']for row in table['rows'])==CASES,'native239_case_table');inputs={}
 for name,expected in(('scripts/evaluate-test.sh',EVALUATOR),('debian/testsuite-xfail-debian.mk',EXPECTATIONS),('scripts/merge-test-results.sh',MERGE_PRODUCER)):
  raw=read_regular(source/name,65536);same_pin(raw,expected);inputs[name]=pin(raw)
 for row in table['rows']:
  expected=row['source'];member=expected['member'];need(member.startswith('glibc-2.39/')and'..'not in member.split('/'),'native239_source_path')
  name=member.removeprefix('glibc-2.39/');raw=read_regular(source/name,1024**2)
  need(pin(raw)=={key:expected[key]for key in('bytes','sha256')},'native239_case_source_changed');inputs[name]=pin(raw)
 return {'schema':'Native239_fixed_vendor_input_preflight/1','classification':pin(classification),'inputs':inputs,
  'included_headers_separately_pinned':False,'actual_vendor_tests_executed':False,'vendor_qualification':False,'Host_packages_eligible':False,'security_accepted':False}
def capture(source,out,build_code,classification):
 """Capture all fixed raw files before semantic checks, including genuine empty outputs."""
 source=Path(source);out=Path(out);same_pin(classification,CLASSIFICATION);table=json.loads(classification)
 need(tuple(row['test']for row in table['rows'])==CASES,'native239_case_table')
 evaluator=read_regular(source/'scripts/evaluate-test.sh',65536)
 expectations=read_regular(source/'debian/testsuite-xfail-debian.mk',65536)
 merge_producer=read_regular(source/'scripts/merge-test-results.sh',65536)
 native=source/'build-tree/amd64-libc';files=[];entries=[];errors=[]
 ledger=None
 try:
  ledger=read_regular(native/'tests.sum',RAW_NAMES['native239-raw-libc.sum'])
  write_new(out/'native239-raw-libc.sum',ledger,RAW_NAMES['native239-raw-libc.sum'])
 except FileNotFoundError:errors.append('native239_native_ledger_missing')
 for case in CASES:
  entry={'test':case,'source_original':next(row['source']for row in table['rows']if row['test']==case),
   'prior_raw_state':'XPASS','current_body_extent':'UNQUALIFIED_REQUIRES_ROOT_REVIEW'if case in EARLY else'NOT_RECLASSIFIED_FROM_RAW',
   'files':{}}
  source_row=entry['source_original'];member=source_row['member'];need(member.startswith('glibc-2.39/')and'..'not in member.split('/'),'native239_source_path')
  source_raw=read_regular(source/member.removeprefix('glibc-2.39/'),1024**2)
  entry['source_actual']=pin(source_raw)
  for suffix in('.out','.test-result'):
   name=case+suffix;maximum=1024**2 if suffix=='.out'else 65536
   try:
    raw=read_regular(native/name,maximum,empty=suffix=='.out');files.append((name,raw));entry['files'][suffix]={'status':'CAPTURED',**pin(raw)}
    if suffix=='.test-result':
     try:entry['result']=result_row(raw,case)
     except (RuntimeError,UnicodeError):entry['result_refusal']='native239_result_shape';errors.append('native239_result_shape')
   except FileNotFoundError:entry['files'][suffix]={'status':'MISSING'};errors.append('native239_case_file_missing')
  entries.append(entry)
 archive=BoundedArchive(out/'native239-raw-tests.tar.xz');primary=None;tar=None;secondary=[]
 try:
  tar=tarfile.open(fileobj=archive,mode='w|xz')
  for name,raw in files:
   info=tarfile.TarInfo(name);info.size=len(raw);info.mode=0o644;info.uid=info.gid=info.mtime=0;tar.addfile(info,io.BytesIO(raw))
 except BaseException as error:primary=error
 try:
  if tar is not None:tar.close()
 except BaseException as error:
  if primary is not None:secondary.append('native239_tar_close_unknown')
  else:primary=error
 try:archive.close()
 except BaseException as error:
  if primary is not None:secondary.append('native239_archive_close_unknown')
  else:primary=error
 if primary is not None:
  primary.native239_secondary_codes=getattr(primary,'native239_secondary_codes',[])+secondary;raise primary
 document={'schema':'native239_raw_fixed13_vendor_evidence/1','profile':'ubuntu239','actual_full_build_returncode':build_code,
  'merge_producer':pin(merge_producer),'classification':pin(classification),'evaluator':pin(evaluator),'expectations':pin(expectations),'raw_ledger':pin(ledger)if ledger is not None else None,
  'archive':pin(read_regular(out/'native239-raw-tests.tar.xz',RAW_NAMES['native239-raw-tests.tar.xz'])),
  'cases':entries,'capture_refusals':errors,'raw_results_relabelled':False,'vendor_qualification':False,
  'Host_packages_eligible':False,'security_accepted':False,'Root_review':'PENDING'}
 write_new(out/'native239-raw-tests.json',enc(document),RAW_NAMES['native239-raw-tests.json']);return document
def review(raw,document,classification):
 """Fixed Native239 pending disposition; never a caller-selected allowlist."""
 same_pin(classification,CLASSIFICATION);trusted_sources={row['test']:row['source']for row in json.loads(classification)['rows']}
 merged=FORMAT.parse_merge_ledger(raw)if FORMAT is not None else None;parsed=rows(raw);need(document['schema']=='native239_raw_fixed13_vendor_evidence/1'and document['profile']=='ubuntu239'
  and type(document['actual_full_build_returncode'])is int and document['actual_full_build_returncode']==0 and document['capture_refusals']==[]
  and document['classification']=={'bytes':CLASSIFICATION[0],'sha256':CLASSIFICATION[1]}
  and document['merge_producer']=={'bytes':MERGE_PRODUCER[0],'sha256':MERGE_PRODUCER[1]}
  and document['evaluator']=={'bytes':EVALUATOR[0],'sha256':EVALUATOR[1]}
  and document['expectations']=={'bytes':EXPECTATIONS[0],'sha256':EXPECTATIONS[1]}
  and document['raw_ledger']==pin(raw),'native239_evidence_subject')
 need(tuple(row['test']for row in document['cases'])==CASES,'native239_case_table')
 need(all(state!='FAIL'for state in parsed.values()),'vendor_regression_failed')
 xpass={name for name,state in parsed.items()if state=='XPASS'};need(xpass<=set(CASES),'native239_unknown_XPASS')
 need(parsed.get('intl/tst-gettext-path-traversal')=='PASS','new_regression_not_executed')
 for row in document['cases']:
  case=row['test'];need(all(row['files'].get(suffix,{}).get('status')=='CAPTURED'for suffix in('.out','.test-result'))and'result_refusal'not in row,'native239_case_file_missing')
  need(row['source_original']==trusted_sources[case],'native239_case_source_changed')
  need(row['source_actual']=={key:row['source_original'][key]for key in('bytes','sha256')},'native239_case_source_changed')
  result=row.get('result');need(type(result)is dict and result['raw_state']==parsed.get(case),'native239_result_ledger_mismatch')
  if result['raw_state']=='XPASS':need(type(result['original_exit_status'])is int and result['original_exit_status']==0 and result['xfail_true_proven']is True,'native239_result_exit')
 return {'counts':dict(sorted(Counter(parsed.values()).items())),'tests':len(parsed),'CVE_regression_PASS':True,
  'default_XFAIL_UNSUPPORTED_retained':True,'test_body_selection_or_skip_override':False,
  'raw_XPASS_names':sorted(xpass),'merge_sections':list(merged['sections']),'case_merge_sections':{name:section for (_,name),section in zip(merged['rows'],merged['row_sections'])if name in CASES},'raw_merge_subject':merged['raw'],'Root_review':'PENDING','body_extent_unqualified_cases':list(EARLY),
  'vendor_qualification':False,'Host_packages_eligible':False,'security_accepted':False}

def failure_manifest_script():
 """Only sixteen fixed quarantine leaves; no directory/glob/export authority."""
 return r'''from pathlib import Path
from hashlib import sha256
import os,stat,json,fcntl,errno
limits='''+repr(QUARANTINE_NAMES)+r'''
rows={}
sizes={}
for name,limit in limits.items():
 try:s=(Path('/out')/name).lstat()
 except FileNotFoundError:continue
 assert stat.S_ISREG(s.st_mode) and s.st_uid==s.st_gid==0 and s.st_nlink==1 and stat.S_IMODE(s.st_mode)==0o644 and 0<s.st_size<=limit
 sizes[name]=(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
assert sum(row[6]for row in sizes.values())<='''+str(QUARANTINE_MAX)+r'''
assert sum(sizes[name][6]for name in '''+repr(QUARANTINE_DEBS)+r''' if name in sizes)<='''+str(QUARANTINE_DEBS_MAX)+r'''
assert sum(sizes[name][6]for name in '''+repr(QUARANTINE_PROOFS)+r''' if name in sizes)<='''+str(QUARANTINE_METADATA_MAX)+r'''
for name,limit in limits.items():
 p=Path('/out')/name
 try:fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
 except FileNotFoundError:
  assert name not in sizes;continue
 try:
  s=os.fstat(fd);key=lambda t:(t.st_dev,t.st_ino,t.st_mode,t.st_uid,t.st_gid,t.st_nlink,t.st_size,t.st_mtime_ns,t.st_ctime_ns)
  before=key(s);flags=(fcntl.fcntl(fd,fcntl.F_GETFL),fcntl.fcntl(fd,fcntl.F_GETFD));assert flags[1]==fcntl.FD_CLOEXEC and os.lseek(fd,0,os.SEEK_CUR)==0
  assert name in sizes and key(s)==sizes[name] and stat.S_ISREG(s.st_mode)and s.st_uid==s.st_gid==0 and s.st_nlink==1 and stat.S_IMODE(s.st_mode)==0o644 and 0<s.st_size<=limit
  h=sha256();n=0
  while True:
   b=os.pread(fd,min(65536,limit-n+1),n)
   if not b:break
   n+=len(b);assert n<=limit;h.update(b)
  assert n==s.st_size and key(os.fstat(fd))==key(p.lstat())==before and (fcntl.fcntl(fd,fcntl.F_GETFL),fcntl.fcntl(fd,fcntl.F_GETFD))==flags and os.lseek(fd,0,os.SEEK_CUR)==0
  rows[name]={'bytes':n,'sha256':h.hexdigest(),'full9':list(before)}
 finally:os.close(fd)
 try:os.fstat(fd)
 except OSError as error:assert error.errno==errno.EBADF
 else:raise AssertionError('native239_close_unknown')
 assert key(p.lstat())==before
assert set(rows)==set(sizes)
print(json.dumps(rows,sort_keys=True))'''
