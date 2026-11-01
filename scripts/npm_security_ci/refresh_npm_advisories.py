"""Unarmed, public lock names/semver versions -> official npm bulk advisories only."""
from hashlib import sha256
from pathlib import Path
import argparse,errno,fcntl,json,os,re,ssl,time,urllib.error,urllib.request,uuid
NETWORK_ARMED=False
BASE=Path('/root/trading-bot/trading_bot');HERE=Path(__file__).absolute().parent
LOCK=BASE/'frontend/package-lock.json'
URL='https://registry.npmjs.org/-/npm/v1/security/advisories/bulk'
INPUT_MAX=2*1024*1024;RESPONSE_MAX=8*1024*1024;HTTP_TIMEOUT=30;TOTAL_SECONDS=120
NUM=r'(?:0|[1-9][0-9]*)';PRE=r'(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)'
SEMVER=re.compile(NUM+r'\.'+NUM+r'\.'+NUM+r'(?:-'+PRE+r'(?:\.'+PRE+r')*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?\Z')
NAME=re.compile(r'(?:@[A-Za-z0-9][A-Za-z0-9._~-]*/)?[A-Za-z0-9][A-Za-z0-9._~-]*\Z')
def need(ok,code):
 if not ok:raise RuntimeError(code)
def encode(value):return(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
def full(s):return(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def package_name(path,row):
 # npm aliases carry their actual registry name explicitly in packages metadata.
 value=row.get('name')
 if value is None:
  parts=path.rsplit('node_modules/',1)
  if len(parts)!=2:return None
  value=parts[1]
 return value if type(value)is str and 0<len(value)<=214 and NAME.fullmatch(value)else None

def request_from_lock(raw):
 need(type(raw)is bytes and 0<len(raw)<=INPUT_MAX,'lock_bound')
 def unique(pairs):
  value={}
  for key,item in pairs:need(key not in value,'duplicate_lock_key');value[key]=item
  return value
 value=json.loads(raw.decode('utf-8'),object_pairs_hook=unique)
 need(type(value)is dict and value.get('lockfileVersion')in(2,3)and type(value.get('packages'))is dict,'lock_schema')
 packages={};gaps=[];entries=0;represented=0;no_resolution=0
 for path,row in value['packages'].items():
  need(type(path)is str and type(row)is dict,'lock_package_schema')
  if path=='':continue # The application itself is not an installed registry dependency.
  entries+=1;name=package_name(path,row);version=row.get('version');resolved=row.get('resolved','')
  if 'resolved'not in row:no_resolution+=1
  reason=None
  if row.get('link')is True:reason='workspace_or_link'
  elif 'node_modules/'not in path:reason='workspace_package'
  elif name is None:reason='unrepresentable_registry_name'
  elif type(version)is not str or not SEMVER.fullmatch(version):reason='unrepresentable_semver'
  elif type(resolved)is not str:reason='unrepresentable_source'
  elif resolved.startswith(('git:','git+','github:','gitlab:','bitbucket:','ssh:','file:','link:','workspace:'))or re.search(r'\.git(?:#|$)',resolved):reason='git_file_or_workspace_source'
  if reason:
   # Never export/record the URL, git ref, local path or unrepresentable version.
   gaps.append({'entry_index':entries,'name':name,'reason':reason});continue
  packages.setdefault(name,set()).add(version);represented+=1
 payload={name:sorted(versions)for name,versions in sorted(packages.items())}
 need(bool(payload),'no_representable_packages');body=encode(payload);need(len(body)<=INPUT_MAX,'request_bound')
 return body,{'installed_package_entries':entries,'represented_entries':represented,'unique_package_names':len(payload),'unique_name_version_pairs':sum(map(len,payload.values())),'entries_without_resolved_metadata':no_resolution,'root_application_excluded':True,'all_dev_and_production_entries_considered':True,'gaps':gaps,'lock_query_coverage_complete':not gaps,'registry_origin_or_meta_vulnerability_proof_claimed':False}

class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):raise RuntimeError('redirect_refused')
def opener():
 # No environment proxies, npmrc, npm credentials, cookies or HTTP auth handlers.
 return urllib.request.OpenerDirector() # constructed explicitly below, not build_opener defaults

def tls_context():
 # Explicit system trust bundle; do not consult SSL_CERT_FILE/DIR/KEYLOGFILE.
 context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT);context.check_hostname=True;context.verify_mode=ssl.CERT_REQUIRED
 context.load_verify_locations(cafile='/etc/ssl/certs/ca-certificates.crt');return context

def official_opener():
 client=opener()
 for handler in(urllib.request.ProxyHandler({}),urllib.request.UnknownHandler(),urllib.request.HTTPDefaultErrorHandler(),NoRedirect(),urllib.request.HTTPSHandler(context=tls_context()),urllib.request.HTTPErrorProcessor()):client.add_handler(handler)
 return client

def fetch(body,client=None):
 need(type(body)is bytes and 0<len(body)<=INPUT_MAX,'request_bound')
 parsed=json.loads(body);need(type(parsed)is dict and all(type(k)is str and NAME.fullmatch(k)and type(v)is list and v and all(type(x)is str and SEMVER.fullmatch(x)for x in v)for k,v in parsed.items()),'request_only_names_versions')
 req=urllib.request.Request(URL,data=body,headers={'Content-Type':'application/json','Accept':'application/json','Accept-Encoding':'identity','User-Agent':'project-public-dependency-audit/1'},method='POST')
 began=time.monotonic();client=official_opener()if client is None else client
 with client.open(req,timeout=HTTP_TIMEOUT)as response:
  need(response.status==200 and response.geturl()==URL,'official_response_scope')
  need(response.headers.get('Content-Encoding','identity').lower()in('identity',''),'content_encoding')
  length=response.headers.get('Content-Length')
  if length is not None:need(length.isascii()and length.isdigit()and int(length)<=RESPONSE_MAX,'response_bound')
  parts=[];count=0
  while True:
   need(time.monotonic()-began<TOTAL_SECONDS,'response_deadline')
   # At most one extra byte is read. Oversized chunks are refused BEFORE append.
   chunk=response.read(min(65536,RESPONSE_MAX-count+1))
   need(type(chunk)is bytes,'response_type')
   if not chunk:break
   need(len(chunk)<=RESPONSE_MAX-count,'response_bound');parts.append(chunk);count+=len(chunk)
  need(time.monotonic()-began<TOTAL_SECONDS,'response_deadline')
 return b''.join(parts)

def summary(raw):
 value=json.loads(raw.decode('utf-8'));need(type(value)is dict,'response_schema');counts={};ids=set();advisories=0
 for name,rows in value.items():
  need(type(name)is str and NAME.fullmatch(name)and type(rows)is list,'response_schema')
  for row in rows:
   need(type(row)is dict and row.get('severity')in('info','low','moderate','high','critical'),'response_schema')
   severity=row['severity'];counts[severity]=counts.get(severity,0)+1;advisories+=1
   if severity in('high','critical'):
    ident=row.get('id');need(type(ident)is int and ident>0 or type(ident)is str and re.fullmatch('[A-Za-z0-9_.:-]{1,128}',ident),'advisory_id')
    ids.add(str(ident))
 return{'returned_advisories':advisories,'returned_counts_by_severity':counts,'returned_high_critical_ids':sorted(ids),'server_returned_bulk_advisories_only':True,'full_npm_meta_vulnerability_or_business_acceptance_claimed':False}

def publish(path,raw):
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
 try:
  view=memoryview(raw)
  while view:n=os.write(fd,view);need(n>0,'output_write');view=view[n:]
  os.fsync(fd)
 finally:os.close(fd)
 d=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
 try:os.fsync(d)
 finally:os.close(d)
 return{'path':str(path),'bytes':len(raw),'sha256':sha256(raw).hexdigest()}
def main(argv=None):
 p=argparse.ArgumentParser();p.add_argument('--execute',action='store_true');p.add_argument('--source-sha256');p.add_argument('--lock-sha256');a=p.parse_args(argv)
 if not a.execute or not NETWORK_ARMED:print(json.dumps({'status':'UNARMED','network_called':False}));return 0
 need(os.geteuid()==os.getegid()==0 and type(a.source_sha256)is str and re.fullmatch('[a-f0-9]{64}',a.source_sha256)and type(a.lock_sha256)is str and re.fullmatch('[a-f0-9]{64}',a.lock_sha256),'Root_pins')
 source_path=Path(__file__).absolute();ss=source_path.lstat();need(ss.st_mode==0o100600 and ss.st_uid==ss.st_gid==0 and ss.st_nlink==1 and 0<ss.st_size<=65536,'source_scope')
 source_fd=os.open(source_path,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW|os.O_CLOEXEC)
 try:
  source=os.pread(source_fd,ss.st_size+1,0);need(full(os.fstat(source_fd))==full(ss)==full(source_path.lstat())and len(source)==ss.st_size and sha256(source).hexdigest()==a.source_sha256,'source_pin')
 finally:os.close(source_fd)
 before=full(LOCK.lstat());need(before[2]in(0o100600,0o100644)and before[3:6]==(0,0,1)and 0<before[6]<=INPUT_MAX,'lock_scope')
 fd=os.open(LOCK,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW|os.O_CLOEXEC);flags=fcntl.fcntl(fd,fcntl.F_GETFL)
 def check():need(full(os.fstat(fd))==before==full(LOCK.lstat())and fcntl.fcntl(fd,fcntl.F_GETFL)==flags and fcntl.fcntl(fd,fcntl.F_GETFD)==fcntl.FD_CLOEXEC and os.lseek(fd,0,os.SEEK_CUR)==0,'lock_changed')
 raw=None;output=None;primary=None
 try:
  check();raw=os.pread(fd,before[6]+1,0);need(len(raw)==before[6]and sha256(raw).hexdigest()==a.lock_sha256,'lock_pin');body,coverage=request_from_lock(raw)
  parent=HERE/'runs';parent.mkdir(mode=0o700,exist_ok=True);s=parent.lstat();need(parent.resolve(strict=True)==parent and s.st_mode==0o40700 and s.st_uid==s.st_gid==0,'output_scope');output=parent/uuid.uuid4().hex;output.mkdir(mode=0o700)
  request_ref=publish(output/'npm-request-names-versions.json',body);started=time.time_ns();publish(output/'attempt.json',encode({'schema':'public_npm_bulk_request_attempt/1','lock_bytes':len(raw),'lock_sha256':a.lock_sha256,'request':request_ref,'actual_request_started_unix_ns':started,'endpoint':URL,'name_version_data_only':True,'automatic_retry':False}));check()
  response=fetch(body);received=time.time_ns();check();response_ref=publish(output/'npm-bulk-response.json',response)
  outcome=summary(response);report={'schema':'public_npm_bulk_advisory_refresh/1','lock':{'path':str(LOCK),'bytes':len(raw),'sha256':a.lock_sha256},'endpoint':URL,'request':request_ref,'response':response_ref,'actual_request_started_unix_ns':started,'actual_response_received_unix_ns':received,'http_timeout_seconds':HTTP_TIMEOUT,'stream_total_check_seconds':TOTAL_SECONDS,'proxy_auth_redirects_allowed':False,'all_dependencies':coverage,**outcome,'security_accepted':False};check();report_ref=publish(output/'report.json',encode(report));check()
 except BaseException as error:primary=error
 finally:
  try:check()
  except BaseException as error:
   if primary is None:primary=error
  os.close(fd)
  try:os.fstat(fd)
  except OSError as error:need(error.errno==errno.EBADF,'lock_close')
  else:raise RuntimeError('lock_close')
 if primary is not None:
  codes={'response_bound','response_deadline','response_type','content_encoding','official_response_scope','redirect_refused','response_schema','advisory_id','lock_changed','lock_pin','request_only_names_versions'}
  code=primary.args[0]if type(primary)is RuntimeError and len(primary.args)==1 and primary.args[0]in codes else type(primary).__name__
  print(json.dumps({'status':'REFUSED_OR_UNKNOWN','code':code,'output':str(output)if output else None,'automatic_retry':False}));return 1
 print(json.dumps({'status':'PUBLIC_NPM_BULK_REFRESH_COMPLETE','report':report_ref,'coverage_complete':coverage['lock_query_coverage_complete'],'gap_count':len(coverage['gaps']),**outcome,'lock_original_EBADF':True,'security_accepted':False},sort_keys=True));return 0
if __name__=='__main__':raise SystemExit(main())
