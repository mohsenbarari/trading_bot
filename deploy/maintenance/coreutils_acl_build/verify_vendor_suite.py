"""Read-only ordinary Coreutils9.7 vendor-result collector; default UNARMED.

The caller owns real unfiltered dh_auto_test execution, actual return code and
process/source closure. This parser consumes bounded actual Make selections
and filesystem outputs; neither a ready JSON nor callback establishes that
external command authority here. No compiler, test, package or subprocess runs.
"""
from collections import Counter
from hashlib import sha256
from pathlib import Path,PurePosixPath
import io,json,os,re,stat,time

ROOT=Path('/build/coreutils-acl/coreutils/source')
SCOPES={'coreutils':('', 'tests/test-suite.log'),'gnulib':('gnulib-tests','test-suite.log')}
RESULTS=('PASS','SKIP','XFAIL','FAIL','XPASS','ERROR')
MAX_SELECTION=4*1024**2;MAX_TESTS=8192;MAX_FILE=4*1024**2;MAX_TOTAL=64*1024**2
MAX_RECEIPT=4*1024**2;BLOCK=65536;MAX_DIRS=1024
DRIVERS={(4879,'08ff8986c3a0ce8bf32d25ec7e4752ffbce9c89589f018836c97a08b6573ac2c'):
 {'name':'coreutils9.7-original','collect_skipped_logs_option':False},
 (5215,'05d9c1057354db27b965fcd154860f938186324ccd72fdb8c5767083194c8b5a'):
 {'name':'Debian-Automake1.17-4','collect_skipped_logs_option':True}}

def need(ok,code):
 if not ok:raise RuntimeError(code)
def encode(v):return(json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
def identity(s):return[s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns]
def canonical(value):
 return(type(value)is str and 0<len(value)<=512 and re.fullmatch(r'[A-Za-z0-9_+./-]+',value)is not None and not value.startswith('/')
  and str(PurePosixPath(value))==value and all(p not in{'.','..'}for p in PurePosixPath(value).parts))

def driver_profile(binding):
 need(type(binding)is dict and set(binding)=={'bytes','sha256'}and type(binding['bytes'])is int
  and type(binding['sha256'])is str,'vendor_driver_source')
 key=(binding['bytes'],binding['sha256']);need(key in DRIVERS,'vendor_driver_source');return DRIVERS[key]

def selection(raw,scope):
 need(scope in SCOPES and type(raw)is bytes and 0<len(raw)<=MAX_SELECTION,'vendor_selection_bound')
 source=io.BytesIO(raw);need(source.readline(4097)==b'GOLD_VENDOR_SELECTION_V2\n','vendor_selection_header')
 suite=None;collect_skipped_logs=None;tests=[];logs=[];extensions=[];seen=set();line_count=0
 while True:
  line=source.readline(4097)
  if not line:break
  line_count+=1;need(line_count<=MAX_TESTS*2+16 and len(line)<=4096 and line.endswith(b'\n'),'vendor_selection_line_bound')
  try:text=line[:-1].decode('ascii')
  except UnicodeError:raise RuntimeError('vendor_selection_ascii')from None
  need('='in text,'vendor_selection_field');key,value=text.split('=',1)
  need((key,value)not in seen,'vendor_selection_duplicate');seen.add((key,value))
  if key=='suite_log':need(suite is None and value==SCOPES[scope][1],'vendor_suite_path');suite=value
  elif key=='collect_skipped_logs':need(collect_skipped_logs is None and value in{'yes','no'},'vendor_skip_log_policy');collect_skipped_logs=value
  elif key in{'test','log'}:
   need(canonical(value),'vendor_selection_path');target=tests if key=='test'else logs
   need(len(target)<MAX_TESTS,'vendor_selection_count');target.append(value)
  elif key=='extension':
   need(len(extensions)<8 and re.fullmatch(r'\.[A-Za-z0-9_]{1,16}',value)is not None,'vendor_selection_extension');extensions.append(value)
  else:raise RuntimeError('vendor_selection_field')
 need(suite is not None and collect_skipped_logs is not None and 0<len(tests)==len(logs)<=MAX_TESTS and all(x.endswith('.log')for x in logs),'vendor_selection_inventory')
 # TEST_LOGS is the genuine configured Makefile expansion. Its declared
 # TEST_EXTENSIONS govern stripping, not a guessed universal .sh rule.
 expected=[]
 for name in tests:
  stem=name
  for ext in extensions:
   if stem.endswith(ext):stem=stem[:-len(ext)];break
  expected.append(stem+'.log')
 need(logs==expected and len(set(logs))==len(logs),'vendor_selection_mapping')
 if scope=='coreutils':need(all(x.startswith('tests/')for x in tests+logs),'vendor_selection_scope')
 # gnulib's genuine unfiltered Make selection includes nested directories
 # (e.g. uniwidth/test-uc_width2.sh). Canonical relative paths, no symlink
 # parents and the fixed gnulib-tests root enforce confinement, not prefixes.
 return{'suite_log':suite,'collect_skipped_logs':collect_skipped_logs,'tests':tests,'logs':logs,'extensions':extensions,'raw_bytes':len(raw),'raw_sha256':sha256(raw).hexdigest()}

def trs(raw,*,skip_copy):
 need(skip_copy in{'yes','no'},'vendor_skip_log_policy')
 need(type(raw)is bytes and 0<len(raw)<=4096,'vendor_trs_bound');rows={}
 for line in raw.splitlines():
  match=re.fullmatch(rb':(test-result|global-test-result|recheck|copy-in-global-log): ([A-Za-z]+)',line)
  need(match is not None,'vendor_trs_format');key,value=(x.decode('ascii')for x in match.groups())
  need(key not in rows,'vendor_trs_duplicate');rows[key]=value
 need(set(rows)=={'test-result','global-test-result','recheck','copy-in-global-log'}and rows['test-result']in RESULTS
  and rows['global-test-result']==rows['test-result'],'vendor_trs_inventory')
 result=rows['test-result'];need(result not in{'FAIL','XPASS','ERROR'},'vendor_failed_test')
 need(rows['recheck']=='no'and rows['copy-in-global-log']==('no'if result=='PASS'else skip_copy if result=='SKIP'else'yes'),'vendor_trs_driver_contract')
 return result

def log_facts(tail,result,test):
 # Exact Debian9.7 build-aux/test-driver appends this ordinary suffix. Keep
 # actual skip/XFAIL messages, never manufacture a platform explanation.
 match=re.search(rb'(PASS|SKIP|XFAIL|FAIL|XPASS|ERROR) ([^\r\n]{1,1024}) \(exit status: ([0-9]{1,3})\)\n?\Z',tail)
 need(match is not None and match.group(1).decode()==result and match.group(2).decode('ascii',errors='replace')==test,'vendor_log_driver_suffix')
 code=int(match.group(3));need(code<=255 and(code==0 if result=='PASS'else code==77 if result=='SKIP'else code not in{0,77}),'vendor_log_exit')
 reasons=[]
 if result in{'SKIP','XFAIL'}:
  for line in tail.splitlines():
   if re.search(rb'skip|not supported|not available|requires|unsupported|expected fail',line,re.I):
    if len(reasons)==4:break
    reasons.append({'verbatim':line[:512].decode('utf-8',errors='replace'),'line_bytes':len(line),
     'line_sha256':sha256(line).hexdigest(),'excerpt_truncated':len(line)>512})
 return{'status':result,'actual_driver_exit_status':code,'actual_driver_test_name':match.group(2).decode('utf-8',errors='replace'),
  'reason_excerpts':reasons,'explicit_reason_observed':any(b'skipped test:'in line.lower()or b'requires'in line.lower()
   or b'not supported'in line.lower()for line in tail.splitlines()),'reason_source':'bounded_actual_log_tail'if reasons else'not_emitted_in_bounded_tail'}

def summary(prefix,counts):
 values={}
 for line in prefix.splitlines():
  match=re.fullmatch(rb'# (TOTAL|PASS|SKIP|XFAIL|FAIL|XPASS|ERROR):[ \t]+([0-9]{1,8})[ \t]*',line)
  if match:
   key=match.group(1).decode();need(key not in values,'vendor_summary_duplicate');values[key]=int(match.group(2))
   if len(values)==7:break # Later copied test logs may themselves contain '#'.
 need(set(values)=={'TOTAL',*RESULTS}and values['TOTAL']==sum(counts.values())
  and all(values[k]==counts[k]for k in RESULTS),'vendor_summary_counts')
 return values

class Scope:
 def __init__(self,root,owner,guard,end,started_at_ns):
  need(type(owner)is int and owner>=0 and callable(guard)and type(end)in(int,float)and 0<end-time.monotonic()<=600
   and type(started_at_ns)is int and 0<started_at_ns<=time.time_ns(),'vendor_scope')
  self.root=Path(root);self.owner=owner;self.guard=guard;self.end=end;self.start=started_at_ns
  self.dirs={};self.files=[];self.current=None;self.total=0;self.closed=False
  need(self.root.is_absolute()and self.root.resolve(strict=True)==self.root,'vendor_root')
  try:self.directory(self.root)
  except BaseException:
   self.close();raise
 def external(self):
  need(time.monotonic()<self.end,'vendor_deadline');need(self.guard()is None,'vendor_authority_result')
 def directory(self,path):
  if path in self.dirs:return self.dirs[path][0]
  need(len(self.dirs)<MAX_DIRS,'vendor_directory_bound')
  if path.parent!=path:self.directory(path.parent)
  before=identity(path.lstat());need(path.resolve(strict=True)==path and stat.S_ISDIR(before[2])
   and before[3]in{0,self.owner}and before[4]in{0,self.owner}and not before[2]&0o022,'vendor_parent')
  fd=os.open(path,os.O_PATH|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC);self.dirs[path]=(fd,before[:5],True)
  need(identity(os.fstat(fd))[:5]==before[:5],'vendor_parent_changed');self.check();return fd
 def check(self):
  self.external()
  for path,(fd,frame,opened)in self.dirs.items():
   need(path.resolve(strict=True)==path and identity(path.lstat())[:5]==frame
    and(not opened or identity(os.fstat(fd))[:5]==frame),'vendor_parent_changed')
  if self.current is not None:
   path,fd,before=self.current;need(path.resolve(strict=True)==path and identity(path.lstat())==before
    and identity(os.fstat(fd))==before,'vendor_output_changed')
 def read(self,relative,maximum):
  need(canonical(relative),'vendor_read_path');path=self.root/relative;parent=self.directory(path.parent);self.check()
  before=identity(os.stat(path.name,dir_fd=parent,follow_symlinks=False))
  need(stat.S_ISREG(before[2])and before[3:6]==[self.owner,self.owner,1]and not before[2]&0o133
   and 0<before[6]<=maximum and before[7]>=self.start and before[8]>=self.start,'vendor_output_metadata')
  need(self.total+before[6]<=MAX_TOTAL,'vendor_aggregate_bound')
  fd=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,dir_fd=parent);self.current=(path,fd,before)
  digest=sha256();count=0;prefix=b'';tail=b'';failure=None
  try:
   self.check()
   while count<before[6]:
    self.check();block=os.read(fd,min(BLOCK,before[6]-count));need(block,'vendor_output_short')
    need(count+len(block)<=before[6],'vendor_output_bound');count+=len(block);digest.update(block)
    if len(prefix)<16384:prefix+=block[:16384-len(prefix)]
    tail=(tail+block)[-8192:];self.check()
   need(not os.read(fd,1),'vendor_output_excess');self.check()
  except BaseException as error:failure=error
  finally:
   try:self.check()
   except BaseException as error:
    if failure is None:failure=error
   try:os.close(fd)
   except BaseException as error:
    if failure is None:failure=error
   self.current=None
   try:self.check();need(path.resolve(strict=True)==path and identity(path.lstat())==before,'vendor_output_closed_changed')
   except BaseException as error:
    if failure is None:failure=error
  if failure is not None:raise failure
  binding={'path':relative,'bytes':count,'sha256':digest.hexdigest(),'full9':before};self.files.append(binding);self.total+=count
  return binding,prefix,tail
 def named(self):
  self.check()
  for binding in self.files:
   path=self.root/binding['path'];need(path.resolve(strict=True)==path and identity(path.lstat())==binding['full9'],'vendor_output_closed_changed')
 def close(self):
  if self.closed:return
  errors=[]
  try:self.named()
  except BaseException as error:errors.append(error)
  for path,(fd,frame,opened)in reversed(tuple(self.dirs.items())):
   try:os.close(fd)
   except BaseException as error:errors.append(error)
   self.dirs[path]=(fd,frame,False)
  self.closed=True
  try:self.named()
  except BaseException as error:errors.append(error)
  if errors:raise errors[0]

def collect(selection_raws,*,driver_binding,guard,end,started_at_ns):
 """Production namespace/identity are fixed. Caller proves the actual command."""
 need(os.geteuid()==os.getegid()==10001,'vendor_actual_unprivileged_user')
 return _collect_at(ROOT,selection_raws,driver_binding=driver_binding,owner=10001,guard=guard,end=end,started_at_ns=started_at_ns)

def _collect_at(root,selection_raws,*,driver_binding,owner,guard,end,started_at_ns):
 """Explicit owned-test path only; no production override or CLI invokes it."""
 need(type(selection_raws)is dict and set(selection_raws)==set(SCOPES),'vendor_scopes')
 driver=driver_profile(driver_binding);parsed={name:selection(raw,name)for name,raw in selection_raws.items()};scope=Scope(root,owner,guard,end,started_at_ns)
 values={};budget=65536
 try:
  for name,(base,_)in SCOPES.items():
   chosen=parsed[name];skip_copy=chosen['collect_skipped_logs']if driver['collect_skipped_logs_option']else'yes';counts=Counter({k:0 for k in RESULTS});rows=[]
   for test,log in zip(chosen['tests'],chosen['logs']):
    relative=(base+'/'if base else'')+log;receipt=relative[:-4]+'.trs'
    tr,body,_=scope.read(receipt,4096);result=trs(body,skip_copy=skip_copy)
    lr,_,tail=scope.read(relative,MAX_FILE);facts=log_facts(tail,result,test);counts[result]+=1
    row={'test':test,'trs':tr,'log':lr,**facts};cost=len(encode(row));need(budget+cost<=MAX_RECEIPT,'vendor_receipt_bound');budget+=cost;rows.append(row)
   target=(base+'/'if base else'')+chosen['suite_log'];sr,prefix,_=scope.read(target,MAX_FILE);totals=summary(prefix,counts)
   values[name]={'selection_bytes':chosen['raw_bytes'],'selection_sha256':chosen['raw_sha256'],
    'actual_configured_test_count':len(chosen['tests']),'actual_summary':totals,'summary_file':sr,'tests':rows}
  scope.named()
 finally:scope.close()
 scope.named()
 result={'schema':'coreutils_9_7_vendor_suite_output_observation/1','source_root':str(root),'source_owner_uid':owner,'scopes':values,
  'all_selected_trs_log_pairs_observed':True,'all_observed_file_and_parent_fds_physically_closed':True,
  'all_observed_files_named_after_physical_closes':True,'observed_skip_count':sum(v['actual_summary']['SKIP']for v in values.values()),
  'caller_observed_exact_test_driver':{'binding':driver_binding,'profile':driver['name']},
  'observed_xfail_count':sum(v['actual_summary']['XFAIL']for v in values.values()),'failed_unexpected_or_error_results':0,
  'actual_unfiltered_command_authority_implemented':False,'actual_vendor_process_completion_proven':False,
  'all_configured_default_tests_proven_by_parser':False,'zero_skip_coverage_claimed':False,'owned_c_fixtures_included':False,
  'all_cves_fixed':False,'security_accepted':False}
 need(len(encode(result))<=MAX_RECEIPT,'vendor_receipt_bound');return result

def main():
 print(json.dumps({'status':'UNARMED','files_read':False,'command_executed':False,'security_accepted':False},sort_keys=True));return 0
if __name__=='__main__':raise SystemExit(main())
