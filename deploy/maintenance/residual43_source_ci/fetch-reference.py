"""Fixed104 public Debian recipes/native source bytes; Root fetches, default inert.

No archive extraction, downloaded code execution, new signature verification,
build-flag admission, package installation, or image/security acceptance.
"""
from pathlib import Path, PurePosixPath
from hashlib import sha256
from urllib.parse import urlsplit
import argparse,json,os,re,ssl,stat,time,urllib.request,urllib.error

ROOT=Path('/root/trading-bot/trading_bot');HERE=Path(__file__).absolute().parent
OUT=ROOT/'.local/security-AppR2-official-source-config-20261028-01'
CAPACITY=ROOT/'.local/security-AppR2-official-source-54-capacity-20261027-01/capacity.json'
CAPACITY_PIN=(247688,'c99103caa3717567122bd5a9abbfde9c20fab20bd348889769fdce21adb12e2e')
COVER=ROOT/'.local/security-AppR2-signed-Source-membership-Root-20261027-01/receipt.json'
COVER_PIN=(46124,'00af09cc3e47c7b9c73302d758788b290470699a75739ea5082d43afe49d6dc8')
SIGNED=ROOT/'.local/security-Debian-signed-source-roots-20261027-02/receipt.json'
SIGNED_PIN=(3192,'a44c577a9eb66ff98d85f0351958825c5596a95223011c13aa61174aee161307')
CACHE=ROOT/'.local/security-AppR2-official-source-archives-20261027-01/receipt.json'
CACHE_PIN=(2800,'d085b1bdf6f3a3b68535e28ee0f4aba91b343a11c8e4db6693affa09885a6e17')
PLAN=HERE/'plan.json';PLAN_PIN=(48082,'3cbe9d092163b5af54bd5e00853d80539221d468f042d59ec74e807fbeec14c3')
KINDS=frozenset(('descriptor','debian_config_archive_or_diff','native_or_other_full_source_archive'))
EXPECTED_COUNT=104;EXPECTED_BYTES=13988874;MAX_FILE=8*1024**2
MAX_RECEIPT=1024**2;MAX_FAILURE=8*1024**2;SECONDS=900
CONFIRM='ROOT-ONLY-FIXED104-AppR2-PUBLIC-RECIPES-20261028-01'
CODES=frozenset(('explicit','source_scope','fresh_output','parent','changed','original_metadata','original_pin','response_identity','response_length','response_bound_before_accumulation','response_hash','deadline','disk_reserve','writer_metadata','reader_metadata','readback','write','index_root','data_bounds','selection','selection_identity','selection_url','selection_cached','selection_groups','plan_pin','physical_finalization','physical_close_repeated','receipt_bound'))

def need(ok,code):
 if not ok:raise RuntimeError(code)
def frame(s):return tuple(getattr(s,k)for k in('st_dev','st_ino','st_mode','st_uid','st_gid','st_nlink','st_size','st_mtime_ns','st_ctime_ns'))
def encode(v):return(json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
def unique_json(raw):
 def pairs(rows):
  value={}
  for key,item in rows:need(key not in value,'selection');value[key]=item
  return value
 return json.loads(raw,object_pairs_hook=pairs)
def public_error(error):
 name=type(error).__name__;allowed={'RuntimeError','ValueError','OSError','FileNotFoundError','PermissionError','TimeoutError','HTTPError','URLError','KeyboardInterrupt','SystemExit','SSLError'}
 return {'class':name if name in allowed else'OtherException','code':error.args[0]if isinstance(error,RuntimeError)and len(error.args)==1 and type(error.args[0])is str and error.args[0]in CODES else'closed_refusal','errno':error.errno if isinstance(error,OSError)and type(error.errno)is int and -(2**31)<=error.errno<2**31 else None}
class Failure(RuntimeError):
 def __init__(self,primary,finalizers):
  previous=primary.public if type(primary)is Failure else None
  self.public={'status':'FAILED_FIXED_PUBLIC_RECIPE_FETCH','primary':previous['primary']if previous else public_error(primary)if primary is not None else None,'physical_finalizer_failures':(previous['physical_finalizer_failures']if previous else[])+[{'stage':r[0],'error':public_error(r[1]),'observations':r[2]if len(r)==3 else 1}for r in finalizers],'physical_finalizers_completed':False,'new_PGP_verification_performed':False,'source_flags_or_security_accepted':False}
  super().__init__('physical_finalization'if finalizers else'fixed_public_recipe_fetch_failed')

def parents(path):
 result=[]
 for p in path.parents:
  before=frame(p.lstat());need(p.resolve(strict=True)==p and stat.S_ISDIR(before[2])and before[3:5]==(0,0)and not before[2]&0o022,'parent');result.append((p,before[:5]))
 return result
def named(row):
 need(frame(row['path'].lstat())==row['before']and all(p.resolve(strict=True)==p and frame(p.lstat())[:5]==old for p,old in row['parents']),'changed')
def check(row):
 named(row)
 for name in('writer','reader'):
  if row[name]is not None:need(frame(os.fstat(row[name]))==row['before'],'changed')
def hold(path,pin,held,maximum,*,raw=False):
 need(type(pin[0])is int and 0<pin[0]<=maximum and re.fullmatch('[0-9a-f]{64}',pin[1])is not None,'original_metadata')
 ancestors=parents(path);before=frame(path.lstat());need(path.resolve(strict=True)==path and stat.S_ISREG(before[2])and before[3:6]==(0,0,1)and not before[2]&0o022 and before[6]==pin[0],'original_metadata')
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC);row={'path':path,'reader':fd,'writer':None,'before':before,'parents':ancestors,'pin':pin,'attempted':set()};held.append(row);check(row);parts=[];digest=sha256()
 for offset in range(0,pin[0],65536):
  check(row);n=min(65536,pin[0]-offset);part=os.pread(fd,n,offset);need(len(part)==n,'original_pin');digest.update(part)
  if raw:parts.append(part)
 need(os.pread(fd,1,pin[0])==b''and digest.hexdigest()==pin[1],'original_pin');check(row);return b''.join(parts)

def kind_of(name):
 if name.endswith('.asc'):return'detached_signature'
 if name.endswith('.dsc'):return'descriptor'
 if '.debian.tar.'in name or name.endswith('.diff.gz'):return'debian_config_archive_or_diff'
 if '.orig.'in name or '.orig-'in name:return'upstream_orig_archive'
 if '.tar.'in name:return'native_or_other_full_source_archive'
 return'unknown'
def official_url(url,name):
 need(type(url)is str and len(url)<=2048,'selection_url');parts=urlsplit(url)
 prefix={'deb.debian.org':'/debian/pool/','security.debian.org':'/debian-security/pool/'}
 need(parts.scheme=='https'and parts.netloc in prefix and not parts.query and not parts.fragment and not parts.username and not parts.password and parts.port is None,'selection_url')
 need(parts.path.startswith(prefix[parts.netloc])and str(PurePosixPath(parts.path))==parts.path and '..'not in PurePosixPath(parts.path).parts and re.fullmatch('/[A-Za-z0-9+_.~/-]+',parts.path)is not None and PurePosixPath(parts.path).name==name,'selection_url')

def select_members(capacity,coverage,signed):
 need(type(capacity)is dict and capacity.get('schema')=='authenticated_Source54_metadata_capacity_without_fetch/1'and capacity.get('exact_official_groups')==54 and capacity.get('metadata_flags_or_code_equivalence_or_security_accepted')is False,'selection')
 need(capacity['Root_coverage_receipt']=={'bytes':COVER_PIN[0],'sha256':COVER_PIN[1]}and coverage.get('schema')=='Root_actual_signed_Source_DSC_membership_and_Q_coverage/1'and coverage.get('source_group_count')==60 and coverage.get('exact_signed_source_version_matches')==54 and coverage.get('all_source_flags_or_binary_callsite_or_security_accepted')is False,'index_root')
 need(coverage['signed_roots_original']=={'bytes':SIGNED_PIN[0],'sha256':SIGNED_PIN[1]}and signed.get('schema')=='Root_official_Debian_signed_Source_index_roots/1'and signed.get('security_accepted')is False,'index_root')
 official={(r['source_package'],r['source_version'])for r in coverage['source_groups']if r['signed_index_matches']};need(len(official)==54 and {(r['source_package'],r['source_version'])for r in capacity['groups']}==official,'selection_groups')
 for suite in('trixie','trixie-security'):
  ref=coverage['indices'][suite]
  need(signed['releases'][suite]['valid_signature_primary_fingerprints']and signed['releases'][suite]['source_index']=={'path':'main/source/Sources.xz',**ref},'index_root')
 cached=capacity['known_previously_fetched_member_pins'];need(type(cached)is list and len(cached)==7,'selection_cached');known=set()
 for row in cached:
  need(set(row)=={'bytes','sha256','path'}and type(row['bytes'])is int and 0<row['bytes']<=MAX_FILE and re.fullmatch('[0-9a-f]{64}',row['sha256'])is not None,'selection_cached')
  path=Path(row['path']);need(path.parent in(ROOT/'.local/security-AppR2-official-source-archives-20261027-01',ROOT/'.local/security-AppR2-official-source-DSC-20261027-02')and re.fullmatch('[A-Za-z0-9+_.~-]+',path.name)is not None,'selection_cached');known.add((row['bytes'],row['sha256']))
 need(len(known)==7,'selection_cached');selected=[];identities=set();names=set()
 for row in capacity['deduplicated_members']:
  need(type(row)is dict and set(row)=={'name','bytes','sha256','kind','official_url','groups'}and type(row['name'])is str and re.fullmatch('[A-Za-z0-9+_.~-]+',row['name'])is not None and row['name']not in('.', '..')and type(row['bytes'])is int and row['bytes']>0 and re.fullmatch('[0-9a-f]{64}',row['sha256'])is not None,'selection_identity')
  identity=(row['bytes'],row['sha256']);need(identity not in identities and row['kind']==kind_of(row['name']),'selection_identity');identities.add(identity)
  groups={(g['source_package'],g['source_version'])for g in row['groups']};need(groups and groups<=official,'selection_groups');official_url(row['official_url'],row['name'])
  if row['kind']not in KINDS or identity in known:continue
  need(row['bytes']<=MAX_FILE and row['name']not in names,'data_bounds');names.add(row['name']);selected.append(row)
 need(len(capacity['deduplicated_members'])==177 and len(selected)==EXPECTED_COUNT and sum(r['bytes']for r in selected)==EXPECTED_BYTES,'selection')
 need({kind:(sum(r['kind']==kind for r in selected),sum(r['bytes']for r in selected if r['kind']==kind))for kind in KINDS}=={'descriptor':(52,183582),'debian_config_archive_or_diff':(46,5194288),'native_or_other_full_source_archive':(6,8611004)},'selection')
 return selected,cached

def create_public(path,raw,pin,outputs,guard):
 need(type(raw)is bytes and(len(raw),sha256(raw).hexdigest())==pin,'response_hash');ancestors=parents(path);writer=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600);reader=None;primary=None;errors=[]
 try:
  guard();view=memoryview(raw)
  while view:guard();n=os.write(writer,view[:65536]);need(n>0,'write');view=view[n:]
  os.fsync(writer);before=frame(os.fstat(writer));need(before[2:7]==(stat.S_IFREG|0o600,0,0,1,len(raw))and frame(path.lstat())==before,'writer_metadata')
  reader=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC);need(frame(os.fstat(reader))==before,'reader_metadata')
  row={'path':path,'writer':writer,'reader':reader,'before':before,'parents':ancestors,'pin':pin,'attempted':set()};outputs.append(row);writer=reader=None;verify(row);guard()
 except BaseException as error:primary=error
 finally:
  for label,fd in(('partial_writer_close',writer),('partial_reader_close',reader)):
   if fd is not None:
    try:os.close(fd)
    except BaseException as error:errors.append((label,error))
 if primary is not None or errors:raise Failure(primary,errors)from primary
 return {'bytes':pin[0],'sha256':pin[1]}
def verify(row):
 check(row);reader=row['reader'];need(reader is not None,'readback');h=sha256();seen=0
 while seen<row['pin'][0]:
  n=min(65536,row['pin'][0]-seen);part=os.pread(reader,n,seen);need(len(part)==n,'readback');seen+=len(part);h.update(part)
 need(os.pread(reader,1,seen)==b''and h.hexdigest()==row['pin'][1],'readback');check(row)
def finish(outputs,originals):
 errors=[];groups={}
 def attempt(stage,fn):
  try:fn()
  except BaseException as error:
   key=(stage,encode(public_error(error)))
   if key in groups:errors[groups[key]][2]+=1
   else:groups[key]=len(errors);errors.append([stage,error,1])
 def cross(stage):
  for index,row in enumerate(outputs+originals):attempt(stage+':'+str(index),lambda row=row:check(row))
 cross('preclose')
 # Every original remains held through all output writer+reader closes.
 for rows,keys in((outputs,('writer',)),(outputs,('reader',)),(originals,('reader',))):
  for index,row in enumerate(rows):
   for key in keys:
    fd=row[key]
    if fd is None:continue
    def close(row=row,key=key,fd=fd):
     need(key not in row['attempted'],'physical_close_repeated');row['attempted'].add(key)
     os.close(fd);row[key]=None
    attempt(key+'_physical_close:'+str(index),close);cross('postclose')
    if key=='writer'and row[key]is None:attempt('independent_readback:'+str(index),lambda row=row:verify(row))
 for index,row in enumerate(outputs+originals):attempt('final_named:'+str(index),lambda row=row:named(row))
 return errors

class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):return None
def fetch_response(response,size,digest,end,guard):
 need(type(size)is int and 0<size<=MAX_FILE and re.fullmatch('[0-9a-f]{64}',digest)is not None,'data_bounds')
 need(response.status==200 and response.headers.get('Content-Encoding','identity')=='identity','response_identity');claimed=response.headers.get('Content-Length');need(claimed is None or claimed.isascii()and claimed.isdecimal()and int(claimed)==size,'response_length');parts=[];seen=0
 while True:
  need(time.monotonic()<end,'deadline');guard();requested=min(16384,size-seen+1);block=response.read(requested)
  need(type(block)is bytes,'response_identity')
  if not block:break
  need(0<len(block)<=requested and seen+len(block)<=size,'response_bound_before_accumulation');parts.append(block);seen+=len(block)
 need(seen==size,'response_length');raw=b''.join(parts);need(sha256(raw).hexdigest()==digest,'response_hash');return raw

def execute(args):
 need(os.getuid()==os.geteuid()==os.getgid()==os.getegid()==0 and args.confirm==CONFIRM and re.fullmatch('[0-9a-f]{64}',args.source_sha256 or'')is not None,'explicit')
 need(HERE.parent==ROOT/'.local','source_scope');need(not os.path.lexists(OUT),'fresh_output')
 originals=[];outputs=[];rows=[];primary=None;end=time.monotonic()+SECONDS;remaining=EXPECTED_BYTES+MAX_RECEIPT
 def guard():
  need(time.monotonic()<end,'deadline');space=os.statvfs(OUT.parent);need(space.f_bavail*space.f_frsize>=2*1024**3+remaining,'disk_reserve')
  for row in originals+outputs:check(row)
 try:
  own=Path(__file__).absolute();hold(own,(own.stat().st_size,args.source_sha256),originals,131072)
  capacity=unique_json(hold(CAPACITY,CAPACITY_PIN,originals,1024**2,raw=True));coverage=unique_json(hold(COVER,COVER_PIN,originals,65536,raw=True));signed=unique_json(hold(SIGNED,SIGNED_PIN,originals,16384,raw=True))
  plan=unique_json(hold(PLAN,PLAN_PIN,originals,131072,raw=True));cache=unique_json(hold(CACHE,CACHE_PIN,originals,16384,raw=True));selected,cached=select_members(capacity,coverage,signed)
  need(plan.get('schema')=='fixed_AppR2_Source54_recipe_fetch_plan/1'and plan['files']==selected and plan['excluded_exact_cached_members']==cached and plan['files_count']==EXPECTED_COUNT and plan['bytes']==EXPECTED_BYTES and plan['allowed_kinds']==sorted(KINDS)and plan['per_file_maximum']==MAX_FILE,'plan_pin')
  # The previous Root receipt authenticates its five cached source members;
  # the two DSC pins are independently bound by the signed coverage receipt.
  cache_pins={(r['bytes'],r['sha256'])for r in cache['files']if r['kind']=='source_input'}
  dsc_pins={(r['bytes'],r['sha256'])for r in cached if r['path'].endswith('.dsc')}
  need(cache_pins|dsc_pins=={(r['bytes'],r['sha256'])for r in cached}and len(cache_pins)==5 and len(dsc_pins)==2,'selection_cached')
  for r in cached:hold(Path(r['path']),(r['bytes'],r['sha256']),originals,MAX_FILE)
  guard();OUT.mkdir(mode=0o700)
  # No proxy/environment-derived endpoint and no redirect. Every URL is the
  # exact member in the pinned signed-source-derived plan, not a caller URL.
  opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=ssl.create_default_context()),NoRedirect())
  for item in selected:
   guard();url=item['official_url'];official_url(url,item['name']);response=None
   request=urllib.request.Request(url,headers={'User-Agent':'Root-fixed-AppR2-Source54-recipes/1','Accept-Encoding':'identity','Connection':'close'})
   try:response=opener.open(request,timeout=25)
   except urllib.error.HTTPError as error:
    errors=[]
    try:error.close()
    except BaseException as closing:errors.append(('HTTP_error_response_close',closing))
    raise Failure(error,errors)from error
   body_primary=None;body_finals=[]
   try:
    need(response.geturl()==url,'response_identity');raw=fetch_response(response,item['bytes'],item['sha256'],end,guard)
   except BaseException as error:body_primary=error
   finally:
    try:response.close()
    except BaseException as error:body_finals.append(('HTTP_response_close',error))
   if body_primary is not None or body_finals:raise Failure(body_primary,body_finals)from body_primary
   pin=create_public(OUT/item['name'],raw,(item['bytes'],item['sha256']),outputs,guard);rows.append({**item,'saved':pin});remaining-=item['bytes'];guard()
 except BaseException as error:primary=error
 finally:finalizers=finish(outputs,originals)
 if time.monotonic()>=end:finalizers.append(('whole_fetch_deadline',RuntimeError('deadline')))
 if primary is not None or finalizers:raise Failure(primary,finalizers)from primary
 value={'schema':'Root_fixed104_public_Debian_recipe_inputs_fetch/1','status':'FETCHED_BYTES_NOT_ACCEPTED','source_sha256':args.source_sha256,'capacity_original':{'bytes':CAPACITY_PIN[0],'sha256':CAPACITY_PIN[1]},'Root_coverage_original':{'bytes':COVER_PIN[0],'sha256':COVER_PIN[1]},'Root_signed_source_roots_original':{'bytes':SIGNED_PIN[0],'sha256':SIGNED_PIN[1]},'immutable_plan':{'bytes':PLAN_PIN[0],'sha256':PLAN_PIN[1]},'selected_files':EXPECTED_COUNT,'selected_bytes':EXPECTED_BYTES,'files':rows,'excluded_cached_members':cached,'originals_and_data_writer_reader_physical_finalizers_completed':True,'new_PGP_verification_performed':False,'archives_extracted_or_downloaded_code_executed':False,'source_flags_or_security_accepted':False}
 raw=encode(value);need(len(raw)<=MAX_RECEIPT,'receipt_bound');completion=[];primary=None
 def final_guard():
  need(time.monotonic()<end,'deadline');space=os.statvfs(OUT.parent);need(space.f_bavail*space.f_frsize>=2*1024**3+len(raw),'disk_reserve')
  for row in originals+outputs:named(row)
 try:final_guard();create_public(OUT/'receipt.json',raw,(len(raw),sha256(raw).hexdigest()),completion,final_guard)
 except BaseException as error:primary=error
 finally:
  errors=finish(completion,[])
  for row in originals+outputs+completion:
   try:named(row)
   except BaseException as error:errors.append(('receipt_postclose_original_named',error))
  if time.monotonic()>=end:errors.append(('receipt_deadline',RuntimeError('deadline')))
 if primary is not None or errors:raise Failure(primary,errors)from primary
 print(encode({'status':'PUBLIC104_RECIPE_INPUTS_FETCHED_NOT_ACCEPTED','files':EXPECTED_COUNT,'bytes':EXPECTED_BYTES,'receipt':{'bytes':len(raw),'sha256':sha256(raw).hexdigest()},'physical_finalizers_completed':True,'new_PGP_verification_performed':False,'source_flags_or_security_accepted':False}).decode(),end='');return 0

def main(argv=None):
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--fetch-fixed-Source54-recipes',action='store_true');parser.add_argument('--source-sha256');parser.add_argument('--confirm');args=parser.parse_args(argv)
 if not args.fetch_fixed_Source54_recipes:
  print('{"status":"UNARMED","network_actions":false,"source_flags_or_security_accepted":false}');return 0
 try:return execute(args)
 except BaseException as error:
  failure=error if type(error)is Failure else Failure(error,[]);raw=encode(failure.public);need(len(raw)<=MAX_FAILURE,'receipt_bound');print(raw.decode(),end='');return 1
if __name__=='__main__':raise SystemExit(main())
