"""Reviewed normal two-Git Capture2 image build and isolated evidence.

No legacy image profile, layer composer, release controller or activation API
is imported. Activation and deployment remain outside this build-only API.
"""
from contextlib import ExitStack
from hashlib import sha1,sha256
from pathlib import Path,PurePosixPath
from types import ModuleType
import argparse,ast,gzip,json,math,os,re,selectors,signal,stat,subprocess,sys,tarfile,time


# Closed published source expectations, never runtime-identity substitutes.
from hashlib import sha256 as _role_sha256
ROLE_CATALOG_PATH = 'deploy/market-data/role-business-components.json'
ROLE_CATALOG_SHA = '33c092cafd2cd301bd1f7b92a73aeca7c685ea189ddc44e8f98bd8d7c09c2831'
ROLE_MAP = {'native/coin-estimator': {'business_commit': 'f4871edbe507546a0a6544c56bc7d6b34bf770a6', 'business_tree': '9b049e5031a33f416b6c6a2a45d89c3ba334fc3e', 'bytes': 1928470, 'component': 'market-role-sources-20261027-fc622107fadf1de9-v1', 'count': 94, 'manifest_sha256': '6ddbb7459a9b69c20accb2e277c8e25c701b95958b8219cfd38a08e998f1b1ca', 'rows_sha256': 'fc622107fadf1de9adec789425c1d3816271829e1310b75347a524c5c104bbcd'}, 'native/estimator-snapshot-sender': {'business_commit': '6c7bfb3fe3714f1b9ecb375e68bbb0b1263ef328', 'business_tree': 'bc90e1224cda5bd2c58be81ea2aa12d1c6fbcadd', 'bytes': 1848102, 'component': 'market-role-sources-20261027-971dc9e57b1a4220-v1', 'count': 93, 'manifest_sha256': '3ce2f0f0b6ed975c2665f68ca0dc53198b9c00c9673fde3979c094c4ae05ec5c', 'rows_sha256': '971dc9e57b1a4220b3604af0ed101377a62ae7b9f6510340b0cc9c6b9a3cb33f'}, 'native/market-fact-receiver': {'business_commit': 'c9f8ada468c94af99a30ede8cdcbd9a220e9b917', 'business_tree': '08d9d82693f8a42a49da89067c4def1862ce1e83', 'bytes': 1934716, 'component': 'market-role-sources-20261027-83c61098e5581bcb-v1', 'count': 95, 'manifest_sha256': '384995da7738128ca902737e7324cd8fc1abff5a581047e41533b06c14683f16', 'rows_sha256': '83c61098e5581bcb9795859444df9730888c46cac911046f293a8056ef3bc3c1'}, 'native/market-store-adapter': {'business_commit': '6cad2c7e4a4b4622ec3610ddb91cce32cd04f2c0', 'business_tree': 'c3a25a925927802f413dc1eda59ca87b3af3e6b7', 'bytes': 1937533, 'component': 'market-role-sources-20261027-0c440e791bde7779-v1', 'count': 95, 'manifest_sha256': '88a982cf6c6da9f736fa11708be694a444ee7d6b3ffcfaea81b43e1c6c52f133', 'rows_sha256': '0c440e791bde77790179ef65b30b9f5abda5434287c09bf87648b61f19011157'}, 'web/estimator-snapshot-receiver': {'business_commit': '89b64c47c952c6f6a6ba75df2b2f40d0208de196', 'business_tree': '9ba0c52b9a9d3a1cbdb01d0417eaa9891b2585e2', 'bytes': 1934784, 'component': 'market-role-sources-20261027-45ccb288a62edc6a-v1', 'count': 95, 'manifest_sha256': 'bacda97ab7e51af550207a9930977be8e12d37d28ed5354d60cc3c078decedc9', 'rows_sha256': '45ccb288a62edc6a5f08042ca33060ce81ca45e34a2a2bf4e18b55f2de73a884'}, 'web/market-capture-account1': {'business_commit': '4ee3fd21003ebb3038a2e4b572ca2eb01cb45d2f', 'business_tree': 'c63f575f2a67287076a2188a72494ee236b2109c', 'bytes': 1896295, 'component': 'market-role-sources-20261027-12cac77ad8481fa6-v1', 'count': 93, 'manifest_sha256': '1693511310372cb9e8906141bfcb0818d19cd32ed4ca5066acf61d8719013394', 'rows_sha256': '12cac77ad8481fa6d11f7885c97ab4e8593290ed70cf388a7b15e86c4139dbad'}, 'web/market-capture-external': {'business_commit': '6c7bfb3fe3714f1b9ecb375e68bbb0b1263ef328', 'business_tree': 'bc90e1224cda5bd2c58be81ea2aa12d1c6fbcadd', 'bytes': 1848102, 'component': 'market-role-sources-20261027-971dc9e57b1a4220-v1', 'count': 93, 'manifest_sha256': '3ce2f0f0b6ed975c2665f68ca0dc53198b9c00c9673fde3979c094c4ae05ec5c', 'rows_sha256': '971dc9e57b1a4220b3604af0ed101377a62ae7b9f6510340b0cc9c6b9a3cb33f'}, 'web/market-fact-sync-worker': {'business_commit': '064451c17c9f2f1b72f3f33fbe68f15fc1384f60', 'business_tree': '7f7f209b17b06984b2ac2fa989177277ed876b24', 'bytes': 1935251, 'component': 'market-role-sources-20261027-4a848dcf6a46d6f5-v1', 'count': 95, 'manifest_sha256': '83a42c0507c28c40574538c61382034a6f376001cf5cb76a1aed97148416de36', 'rows_sha256': '4a848dcf6a46d6f5a0f0e2784f537f6deb2cd6722fcb6ae580a12dfdc7cbf53d'}, 'web/market-processor': {'business_commit': 'db0f8eba339b830ab7ae5fb1c810f149992458b3', 'business_tree': 'e956bcc0ae619fd7495bb4ba41d8a1aeaa7885c1', 'bytes': 1946368, 'component': 'market-role-sources-20261027-5cc7196abb5cfafe-v1', 'count': 95, 'manifest_sha256': 'a5b587cc382563735bb66df783e8def505d12509a9133a08a2537e562cbaf2c9', 'rows_sha256': '5cc7196abb5cfafeb5ca00aab2d34579c94d493c933768253bfba7a34d995c30'}}

def _role_encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()

def role_data(raw, role):
    need(type(role) is str and role in ROLE_MAP, 'role_component')
    need(type(raw) is bytes and len(raw) == 179771 and
         _role_sha256(raw).hexdigest() == ROLE_CATALOG_SHA, 'role_catalog')
    value = json.loads(raw)
    spec = ROLE_MAP[role]
    manifest = value['components'][spec['rows_sha256']]
    need(value['schema'] == 'normal_market_published_role_components/1' and
         _role_sha256(_role_encoded(manifest)).hexdigest() == spec['manifest_sha256'] and
         role in manifest['roles'] and manifest['business_commit'] == spec['business_commit'] and
         manifest['business_tree'] == spec['business_tree'] and
         manifest['source_files'] == spec['count'] and manifest['source_bytes'] == spec['bytes'] and
         _role_sha256(_role_encoded(manifest['product_files'])).hexdigest() == spec['rows_sha256'] and
         all(manifest[key] is False for key in ('already_running_code_proven',
             'complete_effective_business_inventory_proven', 'full_outer_business_proven',
             'activation_authority', 'security_accepted')), 'role_manifest')
    return manifest

def receipt_role(receipt):
    need(type(receipt) is dict and receipt.get('schema') == 'normal_market_git_component_inputs/2'
         and type(receipt.get('role')) is str and receipt['role'] in ROLE_MAP, 'role_receipt')
    spec = ROLE_MAP[receipt['role']]
    locked = {'business_commit': spec['business_commit'], 'business_tree': spec['business_tree'],
              'repository_identity': {'host': 'github.com', 'repository': 'mohsenbarari/trading_bot'},
              'role': receipt['role'], 'catalog_sha256': ROLE_CATALOG_SHA,
              'source_policy': 'whole_git_component_no_main_fallback'}
    need(receipt.get('role_source') == spec and receipt.get('role_catalog_sha256') == ROLE_CATALOG_SHA and
         receipt.get('repository_identity') == locked['repository_identity'] and
         receipt.get('component') == spec['component'] and
         receipt.get('business_commit') == spec['business_commit'] and
         receipt.get('business_tree') == spec['business_tree'] and
         receipt.get('business_manifest_sha256') == spec['manifest_sha256'] and
         receipt.get('product_lock_sha256') == _role_sha256(_role_encoded(locked)).hexdigest() and
         type(receipt.get('product_files')) is list and len(receipt['product_files']) == spec['count'] and
         _role_sha256(_role_encoded(receipt['product_files'])).hexdigest() == spec['rows_sha256'],
         'role_receipt')
    return dict(spec)

def candidate_spec(value):
    need(type(value) is dict and type(value.get('role')) is str and value['role'] in ROLE_MAP,
         'role_candidate')
    role = value['role']
    need(value == {'role': role, 'role_source': ROLE_MAP[role],
                   'catalog_sha256': ROLE_CATALOG_SHA}, 'role_candidate')
    return dict(ROLE_MAP[role])


EXECUTION_ARMED=True
ROLE_READER='scripts/verify_market_role_product_source.py'
REPOSITORY='mohsenbarari/trading_bot'
SELF='scripts/prepare_market_capture2_image.py'
SEALER='scripts/prepare_market_product_source.py'
READER='scripts/verify_market_product_source.py'
READER_BYTES=30085
READER_SHA256='da8455903a8194b3b321331ed6fa5eb70b5c4e09a227004be3519e3a5e8d8d66'
WORKFLOW='.github/workflows/market-capture2-image.yml'
BUSINESS='scripts/market_business81.py'
OUTER='scripts/market_business81_outer.py'
BUSINESS_DIR='deploy/market-data/business81'
BUSINESS_SUPPORT=('run_business81.py','test_guard_source.py','product_check_source.py','f79-business-test-selection.json','python31118-root-assertion-approval.json','python31118-security-base-assertion-proposal.json','python31118-security-base-assertion.patch','fixtures/base/tests/test_market_pipeline_stage3_foundation.py','fixtures/base/tests/test_market_pipeline_stage4_capture.py','fixtures/base/tests/test_market_pipeline_stage5_coin_processor.py','fixtures/base/tests/test_market_pipeline_stage6_channel_processor.py','fixtures/base/tests/test_market_private_pipeline_contracts.py','fixtures/base/tests/__init__.py','fixtures/base/scripts/manage_market_pipeline_stage3.py','fixtures/base/scripts/audit_production_market_catchup.py','fixtures/base/scripts/export_market_private_pipeline_schemas.py','fixtures/base/tests/fixtures/market_private_pipeline/capture_record.json','fixtures/base/tests/fixtures/market_private_pipeline/market_fact.json','fixtures/base/tests/fixtures/market_private_pipeline/market_fact_batch.json','fixtures/base/tests/fixtures/market_private_pipeline/estimator_snapshot.json','fixtures/base/deploy/market-data/compose.yml','fixtures/base/deploy/market-data/compose.web.yml','fixtures/base/deploy/market-data/compose.bot.yml','fixtures/replay/tests/test_market_pipeline_stage4_capture.py')
CONTROLLER_FILES=(SELF,SEALER,READER,WORKFLOW,BUSINESS,OUTER,*(BUSINESS_DIR+'/'+name for name in BUSINESS_SUPPORT))
DOCKER=['/usr/bin/docker','--host=unix:///var/run/docker.sock']
MANIFEST_SHA='fef6305b1d7e6db851d1d0b77f3aa9910aad535e83f64f012790165a3d62c81b'
ROWS_SHA='75d7d2cfb2c20fd036501e7aac198cb5567fb82c53301fb3972f8c81b935e57f'
MAX_SOURCE=131072;MAX_CONTROLLER_SOURCE=262144;MAX_JSON=4*1024**2;MAX_FILE=64*1024**2;MAX_CONTEXT=128*1024**2
MAX_LOG=64*1024**2;RESERVE=2*1024**3;PHASE='bootstrap'
APT_LOCK='scripts/app_security_os_packages.lock.json'
SQLITE_LOCK='scripts/sqlite_wal_package.lock.json'
TAR_LOCK='scripts/tar_acl_package.lock.json'
COREUTILS_LOCK='scripts/coreutils_acl_package.lock.json'
CACHE_ROOTS=('pip_packages/security-os-capsule','pip_packages/sqlite-wal-capsule','pip_packages/tar-acl-capsule','pip_packages/coreutils-acl-capsule')


ROLE_READER_PIN = (19587, '7de62bb2c8564fd13271413ca3362bcbd9bf6c3775a3d57536a6566c2347f606')

def source_parameters(receipt=None):
 if receipt is not None and receipt.get('schema')=='normal_market_git_component_inputs/2':return receipt_role(receipt)
 if receipt is not None and 'schema'in receipt:need(receipt.get('schema')=='normal_market_git_component_inputs/1'and receipt.get('accepted_manifest_sha256')==MANIFEST_SHA,'two_git_signature')
 return {'manifest_sha256':MANIFEST_SHA,'rows_sha256':ROWS_SHA,'count':93}

def require_selected(receipt,role):
 if role is None:need(receipt.get('schema')=='normal_market_git_component_inputs/1','role_receipt')
 else:need(receipt_role(receipt)==ROLE_MAP.get(role)and receipt['role']==role,'role_receipt')

def controller_files(role=None):
 need(role is None or type(role)is str and role in ROLE_MAP,'role_component')
 return CONTROLLER_FILES if role is None else(*CONTROLLER_FILES,ROLE_READER,ROLE_CATALOG_PATH)

def business_schema(receipt):
 return 'normal_market_historical81_business_tests/2'if receipt.get('schema')=='normal_market_git_component_inputs/2'else'normal_market_historical81_business_tests/1'

def preflight_assertion_sources(raws):
 # Source approval only. No candidate image, plan or completed-test DTO is invented.
 B=module(raws[BUSINESS],Path(BUSINESS));O=module(raws[OUTER],Path(OUTER))
 proposal,_=B.security_assertion_proposal(raws[BUSINESS_DIR+'/fixtures/base/'+B.FOUNDATION])
 update={'path':B.FOUNDATION,'old_line':B.OLD_ASSERTION.decode(),'new_line':B.NEW_ASSERTION.decode(),
  'approved_source_delta_sha256':proposal['after']['sha256']}
 changed=[{'path':prefix+'/'+B.FOUNDATION,'transformation':'reviewed_security_base_assertion_only',
  **proposal['after'],'original_bytes':proposal['before']['bytes'],'original_sha256':proposal['before']['sha256']}for prefix in('base','replay')]
 O.assertion_approval(B,raws[BUSINESS_DIR+'/python31118-root-assertion-approval.json'],
  raws[BUSINESS_DIR+'/python31118-security-base-assertion-proposal.json'],
  raws[BUSINESS_DIR+'/python31118-security-base-assertion.patch'],{'security_assertion_update':update,'files':changed})

def public_probe_file(path,raw,retained,guard):
 need(type(raw)is bytes and 0<len(raw)<=MAX_CONTROLLER_SOURCE,'source_bound')
 guard();fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o444)
 try:
  os.fchmod(fd,0o444);view=memoryview(raw)
  while view:n=os.write(fd,view);need(n>0,'output_write');view=view[n:]
  os.fsync(fd);before=identity(os.fstat(fd));need(before==identity(path.lstat()),'source_changed')
  row=Held(path,maximum=MAX_CONTROLLER_SOURCE,binding={'bytes':len(raw),'sha256':sha256(raw).hexdigest()})
  retained.append(row);need(row.before==before,'source_changed');guard()
 finally:os.close(fd)
 guard();row.check();return row


def need(value,code):
 if not value:raise RuntimeError(code)
def encoded(value):return(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
def document(raw,maximum=MAX_JSON):
 need(type(raw)is bytes and type(maximum)is int and 0<maximum<=32*1024**2 and 0<len(raw)<=maximum,'json_bound')
 def pairs(items):
  result={}
  for key,value in items:need(key not in result,'json_duplicate');result[key]=value
  return result
 def finite(raw):
  value=float(raw);need(math.isfinite(value),'json_nonfinite');return value
 return json.loads(raw,object_pairs_hook=pairs,parse_float=finite,parse_constant=lambda _:need(False,'json_nonfinite'))
def hex40(value):return type(value)is str and re.fullmatch('[a-f0-9]{40}',value)is not None
def hex64(value):return type(value)is str and re.fullmatch('[a-f0-9]{64}',value)is not None
def relative(value):
 return type(value)is str and 0<len(value)<=4096 and not any(c in value for c in ('\0','\n','\\'))and str(PurePosixPath(value))==value and not value.startswith('/')and not {'..','.git'}&set(PurePosixPath(value).parts)
def identity(s):return(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def cleanup_child(child):
 if child.returncode is not None:return
 try:os.waitid(os.P_PID,child.pid,os.WEXITED|os.WNOHANG|os.WNOWAIT)
 except ChildProcessError:return
 try:os.killpg(child.pid,signal.SIGKILL)
 except ProcessLookupError:pass
 child.wait(timeout=10)
def periodic(check,seconds=1):
 last=[None]
 def guard():
  now=time.monotonic()
  if last[0]is None or now-last[0]>=seconds:check();last[0]=now
 return guard
def command(argv,*,environment,seconds=60,maximum=MAX_JSON,target=None,guard=lambda:None,stream_complete=None,nonzero=None):
 """Same owned-unreaped rule as the reviewed ordinary bounded runner."""
 need(type(argv)is list and bool(argv)and all(type(v)is str and '\0'not in v for v in argv)
  and type(maximum)is int and 0<maximum<=2*1024**3 and type(seconds)in(int,float)and math.isfinite(seconds)and 0<seconds<=3600,'command_contract')
 need(stream_complete is None or target is not None and callable(stream_complete),'command_contract')
 need(nonzero is None or target is None and callable(nonzero),'command_contract')
 guard();end=time.monotonic()+seconds
 child=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
  env=environment,close_fds=True,start_new_session=True);parts=[];count=0;stream_hash=sha256()
 try:
  with selectors.DefaultSelector()as selector:
   fd=child.stdout.fileno();os.set_blocking(fd,False);selector.register(fd,selectors.EVENT_READ)
   while True:
    guard();left=end-time.monotonic();need(left>0,'command_timeout')
    if not selector.select(min(1,left)):continue
    raw=os.read(fd,min(65536,maximum-count+1))
    if not raw:break
    count+=len(raw);need(count<=maximum,'command_bound')
    if target is None:parts.append(raw)
    else:
     view=memoryview(raw)
     while view:n=os.write(target,view);need(n>0,'output_write');view=view[n:]
     if stream_complete is not None:stream_hash.update(raw)
   if nonzero is not None:
    code=child.wait(timeout=max(.1,end-time.monotonic()));guard()
    if code!=0:
     need(nonzero(code,b''.join(parts),count)is None,'command_contract')
     need(False,'command_failed')
   if stream_complete is not None:
    need(child.wait(timeout=max(.1,end-time.monotonic()))==0,'command_failed');guard();os.fsync(target)
    created=identity(os.fstat(target));need(created[6]==count,'source_bound')
    stream_complete({'bytes':count,'sha256':stream_hash.hexdigest()},created)
  need(child.wait(timeout=max(.1,end-time.monotonic()))==0,'command_failed');guard();return b''.join(parts)
 except BaseException:
  cleanup_child(child);raise
 finally:child.stdout.close()
def environment(root,*,token=False):
 value={'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':str(root),'DOCKER_CONFIG':str(root/'docker-config'),
  'GH_CONFIG_DIR':str(root/'gh-config'),'LC_ALL':'C.UTF-8','DOCKER_BUILDKIT':'1','PYTHONDONTWRITEBYTECODE':'1'}
 if token:
  # Normal workflow authentication is passed only to gh or the reviewed
  # artifact cache helper. It is never an argument, file, diagnostic or hash.
  need(type(os.environ.get('GH_TOKEN'))is str and bool(os.environ['GH_TOKEN']),'normal_workflow_token_required')
  value['GH_TOKEN']=os.environ['GH_TOKEN']
 return value
def hosted(root,repo):
 need(os.geteuid()==1001 and sys.flags.isolated==sys.flags.no_site==sys.flags.dont_write_bytecode==1
  and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted'and os.environ.get('GITHUB_REPOSITORY')==REPOSITORY
  and os.environ.get('GITHUB_REF')=='refs/heads/main'and os.uname().machine=='x86_64','hosted_main_only')
 temporary=Path(os.environ['RUNNER_TEMP']);workspace=Path(os.environ['GITHUB_WORKSPACE'])
 need(all(p.is_absolute()and p.resolve(strict=True)==p for p in (root,repo,temporary,workspace))and repo==workspace
  and root.is_relative_to(temporary)and root!=temporary and not root.is_relative_to(repo),'root_scope')
 need(root.stat().st_uid==repo.stat().st_uid==1001 and stat.S_IMODE(root.stat().st_mode)==0o700,'root_scope')
 need(not temporary.stat().st_mode&0o022 and not repo.stat().st_mode&0o022,'root_scope')
def git(repo,*args,maximum=MAX_SOURCE):
 env={'PATH':'/usr/bin:/bin','LC_ALL':'C.UTF-8','HOME':'/nonexistent','GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null',
  'GIT_TERMINAL_PROMPT':'0','GIT_NO_REPLACE_OBJECTS':'1','GIT_ALLOW_PROTOCOL':'','GIT_NO_LAZY_FETCH':'1'}
 return command(['/usr/bin/git','--no-optional-locks','-c','core.fsmonitor=false','-c','core.hooksPath=/dev/null','-C',str(repo),*args],
  environment=env,seconds=30,maximum=maximum)
def git_source(repo,commit,path):
 need(hex40(commit)and path in (*CONTROLLER_FILES,ROLE_READER,ROLE_CATALOG_PATH),'controller_source')
 operand=commit+':'+path;size=git(repo,'cat-file','-s',operand,maximum=64)
 need(re.fullmatch(rb'[1-9][0-9]{0,5}\n',size)and int(size)<=MAX_CONTROLLER_SOURCE,'controller_source_bound')
 raw=git(repo,'cat-file','blob',operand,maximum=int(size));need(len(raw)==int(size),'controller_source_bound')
 return raw
def source_epoch(repo,commit):
 need(hex40(commit),'release_commit');raw=git(repo,'show','--no-patch','--format=%ct',commit,maximum=64)
 need(re.fullmatch(rb'[1-9][0-9]{8,10}\n',raw)and int(raw)<=4102444800,'source_epoch');return int(raw)
def live_main(root,identity_value,release):
 need(identity_value=={'host':'github.com','repository':REPOSITORY}and hex40(release),'live_main_scope')
 raw=command(['/usr/bin/gh','api','repos/'+REPOSITORY+'/git/ref/heads/main','--jq','.object.sha'],
  environment=environment(root,token=True),seconds=30,maximum=128)
 need(re.fullmatch(rb'[a-f0-9]{40}\n',raw)and raw.strip().decode()==release,'live_main_changed')
 return {'schema':'normal_live_github_main_observation/1','repository':identity_value,'ref':'refs/heads/main',
  'observed_commit':release,'actual_exit_code':0,'observed_at':int(time.time()),'authentication':'normal_workflow_contents_read_gh_only'}
def default_driver(root):
 raw=command([*DOCKER,'buildx','ls','--format','{{if eq .Builder.Name "default"}}{{json .Builder.Driver}}{{end}}'],
  environment=environment(root),maximum=1024,seconds=30)
 lines=[line.strip()for line in raw.splitlines()if line.strip()];need(1<=len(lines)<=16,'buildx_driver')
 need(all(document(line)=='docker'for line in lines),'buildx_driver');return {'builder':'default','driver':'docker','json_rows':len(lines)}
def save(path,raw,mode=0o600):
 need(type(raw)is bytes and len(raw)<=MAX_JSON,'output_bound');fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,mode)
 try:
  os.fchmod(fd,mode);view=memoryview(raw)
  while view:n=os.write(fd,view);need(n>0,'output_write');view=view[n:]
  os.fsync(fd)
 finally:os.close(fd)
class Held:
 def __init__(self,path,*,maximum=MAX_FILE,binding=None,empty=False):
  need(type(maximum)is int and 0<maximum<=2*1024**3 and type(empty)is bool,'source_contract')
  self.path=Path(path);self.parents=[];self.fd=None
  for parent in self.path.parents:
   info=identity(parent.lstat());need(parent.resolve(strict=True)==parent and stat.S_ISDIR(info[2])and not info[2]&0o022,'source_parent');self.parents.append((parent,info[:5]))
  self.before=identity(self.path.lstat());v=self.before
  need(self.path.resolve(strict=True)==self.path and stat.S_ISREG(v[2])and v[3:6]==(os.geteuid(),os.getegid(),1)
   and stat.S_IMODE(v[2])in (0o444,0o600,0o644,0o755)and(0 if empty else 1)<=v[6]<=maximum,'source_file')
  self.fd=os.open(self.path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
  try:
   self.check();h=sha256();count=0
   while True:
    raw=os.read(self.fd,min(65536,v[6]-count+1))
    if not raw:break
    count+=len(raw);need(count<=v[6],'source_bound');h.update(raw)
   self.binding={'bytes':count,'sha256':h.hexdigest()};need(count==v[6]and(binding is None or self.binding==binding),'source_binding');self.check()
  except BaseException:os.close(self.fd);self.fd=None;raise
 def check(self):self.named();need(identity(os.fstat(self.fd))==self.before,'source_changed')
 def named(self):
  need(self.path.resolve(strict=True)==self.path and identity(self.path.lstat())==self.before
   and all(p.resolve(strict=True)==p and identity(p.lstat())[:5]==old for p,old in self.parents),'source_changed')
 def raw(self,maximum=MAX_JSON):
  need(self.before[6]<=maximum,'source_bound');self.check();raw=os.pread(self.fd,self.before[6]+1,0);self.check();need(len(raw)==self.before[6],'source_bound');return raw
 def close(self):
  try:self.check()
  finally:os.close(self.fd);self.fd=None
  self.named()
class Inputs:
 def named(self):
  for row in self.files:row.named()
 def __init__(self,paths):
  self.files=[]
  try:
   for path,binding,empty in paths:self.files.append(Held(path,binding=binding,empty=empty))
  except BaseException:self.close();raise
 def check(self):
  for row in self.files:row.check()
 def close(self):
  error=None
  for row in reversed(self.files):
   if row.fd is not None:
    try:row.close()
    except BaseException as e:error=e
  for row in self.files:
   try:row.named()
   except BaseException as e:error=e
  if error is not None:raise error
def cache_inputs(release,receipt):
 rows={row['path']:row for row in receipt['release_files']};values={}
 for name in (APT_LOCK,SQLITE_LOCK,TAR_LOCK,COREUTILS_LOCK):
  held=Held(release/name,maximum=1024**2 if name==COREUTILS_LOCK else 65536,binding={k:rows[name][k]for k in ('bytes','sha256')})
  try:values[name]=document(held.raw())
  finally:held.close()
 old,new,tar=values[APT_LOCK],values[SQLITE_LOCK],values[TAR_LOCK]
 need(old.get('schema')=='app_security_os_packages_lock/1'and type(old.get('packages'))is list and len(old['packages'])==15,'vendor_lock')
 need(new.get('schema')=='sqlite_wal_image_package_lock/1'and new.get('armed')is True and type(new.get('package'))is dict,'sqlite_lock')
 expected={}
 def add(path,pin):
  need(relative(path)and path not in expected and type(pin)is dict and type(pin.get('bytes'))is int and 0<pin['bytes']<=MAX_FILE and hex64(pin.get('sha256')),'cache_binding')
  expected[path]={'bytes':pin['bytes'],'sha256':pin['sha256']}
 add(CACHE_ROOTS[0]+'/manifest.json',old['manifest'])
 for package in old['packages']:add(CACHE_ROOTS[0]+'/'+package['path'],package)
 package=new['package'];need(relative(package['path'])and '/'not in package['path'],'cache_binding')
 add(CACHE_ROOTS[1]+'/'+package['path'],new['outputs'][package['path']])
 meta=encoded({'schema':'sqlite_wal_image_package_cache/1','package':package,'binding':new['outputs'][package['path']],
  'source':{key:new['source'][key]for key in ('recipe_sha256','production_patch_sha256')}})
 add(CACHE_ROOTS[1]+'/manifest.json',{'bytes':len(meta),'sha256':sha256(meta).hexdigest()})
 need(tar.get('schema')=='tar_acl_image_package_lock/1'and tar.get('armed')is True and type(tar.get('package'))is dict,'tar_lock')
 package=tar['package'];need(package.get('name')=='tar'and package.get('version')=='1.35+dfsg-5~trixie+gold2'
  and relative(package.get('path'))and '/'not in package['path'],'tar_lock')
 add(CACHE_ROOTS[2]+'/'+package['path'],tar['outputs'][package['path']])
 meta=encoded({'schema':'tar_acl_image_package_cache/1','package':package,'binding':tar['outputs'][package['path']],
  'source':{key:tar['source'][key]for key in('recipe_sha256','production_patch_sha256')},'runtime_payload':tar['outputs']['runtime-payload.json']})
 add(CACHE_ROOTS[2]+'/manifest.json',{'bytes':len(meta),'sha256':sha256(meta).hexdigest()})
 core=values[COREUTILS_LOCK];need(core.get('schema')=='coreutils_acl_image_package_lock/1'and core.get('armed')is True and type(core.get('package'))is dict,'coreutils_lock')
 package=core['package'];need(package.get('name')=='coreutils'and package.get('version')=='9.7-3+gold1'and package.get('path')=='coreutils_9.7-3+gold1_amd64.deb','coreutils_lock')
 add(CACHE_ROOTS[3]+'/'+package['path'],core['outputs'][package['path']])
 meta=encoded({'schema':'coreutils_acl_image_package_cache/1','package':package,'binding':core['outputs'][package['path']],
  'source':{key:core['source'][key]for key in('recipe_sha256','production_patch_sha256')},'runtime_payload':core['outputs']['runtime-payload.json']})
 add(CACHE_ROOTS[3]+'/manifest.json',{'bytes':len(meta),'sha256':sha256(meta).hexdigest()})
 for path in ('pip_packages/.security-os-capsule.lock','pip_packages/.sqlite-wal-capsule.lock','pip_packages/.tar-acl-capsule.lock','pip_packages/.coreutils-acl-capsule.lock'):
  expected[path]={'bytes':0,'sha256':sha256(b'').hexdigest()}
 need(len(expected)==26,'cache_inventory');return expected,new
def context_rows(root,expected):
 need(type(expected)is dict and all(relative(name)for name in expected),'context_inventory')
 directories={''}
 for name in expected:
  parent=PurePosixPath(name).parent
  while str(parent)!='.':directories.add(str(parent));parent=parent.parent
 observed=set();count=0
 for name in sorted(directories):
  path=root/name;info=path.lstat();need(path.resolve(strict=True)==path and stat.S_ISDIR(info.st_mode)and info.st_uid==os.geteuid()and not info.st_mode&0o022,'context_directory')
  with os.scandir(path)as entries:
   for row in entries:
    count+=1;need(count<=2048,'context_bound');child=(name+'/'if name else'')+row.name
    need(child in expected or child in directories,'context_inventory')
    if child in expected:observed.add(child)
 need(observed==set(expected),'context_inventory');return directories
def restamp(root,expected,epoch,*,source_rows=(),retained=None,final_checks=None):
 """Only mtime/ctime transition on the SAME retained source inode is admitted."""
 need(type(epoch)is int and 0<epoch<=4102444800,'source_epoch');directories=context_rows(root,expected)
 rows=[];known={row.path:row for row in source_rows};owned=[]
 try:
  for name in sorted(expected):
   path=root/name;row=known.get(path)
   if row is None:row=Held(path,binding=expected[name],empty=expected[name]['bytes']==0);owned.append(row)
   need(row.binding==expected[name],'source_binding');row.check();before=row.before
   os.utime(row.fd,ns=(epoch*10**9,epoch*10**9));after=identity(os.fstat(row.fd))
   need(after[:7]==before[:7]and after[7]==epoch*10**9 and identity(path.lstat())==after,'source_changed')
   count=0;digest=sha256();end=time.monotonic()+120
   while count<after[6]:
    need(time.monotonic()<end and identity(os.fstat(row.fd))==identity(path.lstat())==after,'source_changed')
    raw=os.pread(row.fd,min(65536,after[6]-count),count);need(raw,'source_bound');count+=len(raw);digest.update(raw)
   need(count==row.binding['bytes']and digest.hexdigest()==row.binding['sha256']and identity(os.fstat(row.fd))==identity(path.lstat())==after,'source_binding')
   row.before=after;row.check();rows.append(row)
  for name in sorted(directories,key=lambda v:len(v.split('/')),reverse=True):os.utime(root/name,ns=(epoch*10**9,epoch*10**9),follow_symlinks=False)
  for row in rows:row.check()
  context_rows(root,expected)
 finally:finish_or_transfer(owned,[],retained,final_checks)
 return rows


def build_argv(root,contract,controller_signature,epoch,run,attempt,*,role=None):
 need(hex64(controller_signature)and type(epoch)is int and epoch>0 and re.fullmatch('[1-9][0-9]{0,19}',run)
  and re.fullmatch('[1-9][0-9]{0,9}',attempt),'build_contract')
 need(contract['named_contexts']=={'business-source':str(root/'sealed/product')}
  and contract['default_context']==str(root/'sealed/release')and contract['dockerfile']=='deploy/market-data/Dockerfile.capture2','build_contract')
 need(type(contract['build_args'])is dict and set(contract['build_args'])=={'SOURCE_SHA','SOURCE_TREE','IMAGE_INPUT_SIGNATURE','IMAGE_VERSION',
  'BUSINESS_SOURCE_COMMIT','BUSINESS_SOURCE_TREE','BUSINESS_SOURCE_MANIFEST_SHA256'}and contract['build_args']['BUSINESS_SOURCE_MANIFEST_SHA256']==(MANIFEST_SHA if role is None else ROLE_MAP[role]['manifest_sha256']),'build_contract')
 args=[*DOCKER,'buildx','build','--builder','default','--load','--platform','linux/amd64',
  '--iidfile',str(root/'candidate-image.id'),'--tag','normal-market-capture2:'+run+'-'+attempt,
  '--build-context','business-source='+str(root/'sealed/product'),
  '--label','io.gold-trade.release.controller-input-signature='+controller_signature]
 for name,value in sorted({**contract['build_args'],'SOURCE_DATE_EPOCH':str(epoch)}.items()):
  need(type(name)is str and re.fullmatch('[A-Z_][A-Z0-9_]*',name)and type(value)is str and '\n'not in value,'build_contract');args+=['--build-arg',name+'='+value]
 return args+['--file',str(root/'sealed/release/deploy/market-data/Dockerfile.capture2'),str(root/'sealed/release')]
def image_contract(value,receipt,signature,controller_signature):
 need(type(value)is dict and type(value.get('Id'))is str and re.fullmatch('sha256:[a-f0-9]{64}',value['Id'])
  and value.get('Os')=='linux'and value.get('Architecture')=='amd64','image_identity')
 config=value.get('Config');need(type(config)is dict and config.get('User')=='10001:10001'and config.get('WorkingDir')=='/app'
  and config.get('Entrypoint')==['python','-m','core.market_intelligence.private_pipeline_foundation']
  and config.get('Cmd')==['service','--role','market-processor']and not config.get('OnBuild')and not config.get('Volumes'),'image_config')
 labels={'org.opencontainers.image.revision':receipt['release_commit'],'io.gold-trade.release.tree':receipt['release_tree'],
  'io.gold-trade.release.input-signature':signature,'io.gold-trade.release.controller-input-signature':controller_signature,
  'io.gold-trade.business.source-revision':receipt['business_commit'],'io.gold-trade.business.source-tree':receipt['business_tree'],
  'io.gold-trade.business.source-manifest':source_parameters(receipt)['manifest_sha256']}
 need(type(config.get('Labels'))is dict and all(config['Labels'].get(key)==item for key,item in labels.items()),'image_labels')
 layers=value.get('RootFS',{}).get('Layers');need(value.get('RootFS',{}).get('Type')=='layers'and type(layers)is list and 0<len(layers)<=128 and all(type(v)is str and re.fullmatch('sha256:[a-f0-9]{64}',v)for v in layers),'image_layers')
 return {'image_id':value['Id'],'rootfs':value['RootFS'],'expected_labels':labels,'public_config_retained_in_ram_only':True}
def probe_plan(root,image,reader_sha,*,role=None):
 need(re.fullmatch('sha256:[a-f0-9]{64}',image)and hex64(reader_sha),'probe_scope')
 common=['--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges:true',
  '--memory','536870912','--memory-swap','536870912','--cpus','1','--pids-limit','128','--log-driver','none']
 result={'source93':{'user':'10001:10001','argv':[*common,'--user','10001:10001','--mount','type=bind,src='+str(root/'proof/reader.py')+',dst=/proof/reader.py,readonly',
   '--entrypoint','/usr/local/bin/python3',image,'-I','-S','-B','/proof/reader.py'],'verifier_sha256':reader_sha},
  'sqlite':{'user':'0:0','argv':[*common,'--user','0:0','--tmpfs','/tmp:rw,nosuid,nodev,mode=1777,size=67108864',
   '--entrypoint','/usr/local/bin/python3',image,'-I','-S','-B','/usr/local/libexec/verify_sqlite_runtime.py']},
  'historical81_business_tests':{'producer_implemented':True,'required_before_acceptance':True},
  'complete_ci_controller_archive':{'producer_implemented':True,'operational_deployment_control':False},'security_accepted':False}
 if role is not None:
  need(role in ROLE_MAP,'role_component');source=result.pop('source93');source['argv']+=['--role',role]
  index=source['argv'].index('--entrypoint');source['argv'][index:index]=['--mount','type=bind,src='+str(root/'proof/role-catalog.json')+',dst=/proof/role-catalog.json,readonly']
  result['source-role']=source
 return result
def validate_probe_container(value,name,image,signature,kind,root,*,role=None):
 need(type(value)is dict and value.get('Name')=='/'+name and value.get('Image')==image
  and type(value.get('Config'))is dict and value['Config'].get('Labels',{}).get('io.gold-trade.normal-market-ci-owner')==signature,'probe_owner')
 config=value['Config'];host=value.get('HostConfig');need(type(host)is dict,'probe_isolation')
 expected_user='10001:10001'if kind in('source93','source-role')else'0:0'
 need(config.get('User')==expected_user and config.get('Entrypoint')==['/usr/local/bin/python3']and host.get('ReadonlyRootfs')is True
  and host.get('Privileged')is False and host.get('NetworkMode')=='none'and host.get('CapAdd')in (None,[]),'probe_isolation')
 drop=host.get('CapDrop');need(type(drop)is list and len(drop)==1 and drop[0]in {'ALL','CAP_ALL'},'probe_isolation')
 need(host.get('SecurityOpt')==['no-new-privileges:true'],'probe_isolation')
 for key,expected in [('Memory',536870912),('MemorySwap',536870912),('NanoCpus',10**9),('PidsLimit',128)]:
  need(type(host.get(key))is int and host[key]==expected,'probe_resources')
 mounts=value.get('Mounts');need(type(mounts)is list,'probe_mounts')
 if kind=='source93':
  need(host.get('Tmpfs')in (None,{})and len(mounts)==1 and mounts[0].get('Type')=='bind'and mounts[0].get('Source')==str(root/'proof/reader.py')
   and mounts[0].get('Destination')=='/proof/reader.py'and mounts[0].get('RW')is False,'probe_mounts')
 elif kind=='source-role':
  need(role in ROLE_MAP and config.get('Cmd')==['-I','-S','-B','/proof/reader.py','--role',role]and host.get('Tmpfs')in(None,{}),'probe_scope')
  expected={'/proof/reader.py':str(root/'proof/reader.py'),'/proof/role-catalog.json':str(root/'proof/role-catalog.json')}
  need(len(mounts)==len(expected)and{row.get('Destination'):row.get('Source')for row in mounts}==expected
   and all(row.get('Type')=='bind'and row.get('RW')is False for row in mounts),'probe_mounts')
 else:
  need(kind=='sqlite'and host.get('Tmpfs')=={'/tmp':'rw,nosuid,nodev,mode=1777,size=67108864'}and all(row.get('Type')=='tmpfs'and row.get('Destination')=='/tmp'for row in mounts),'probe_mounts')
 return value
def validate_source_probe(value,receipt=None):
 spec=source_parameters(receipt);role=receipt is not None and receipt.get('schema')=='normal_market_git_component_inputs/2'
 keys={'schema','status','expected_manifest_sha256','expected_rows_sha256','source_files','excluded_cache_metadata_files','all_source_rows_match',
  'project_cache_bodies_read','unknown_noncache_files_admitted','project_imported','source_only_nonatomic_observation','already_running_code_proven','business_data_freshness_proven','security_accepted'}
 if role:keys|={'role','business_commit','business_tree','role_catalog_sha256'}
 need(type(value)is dict and set(value)==keys and value.get('schema')==('market_role_public_source_readonly_observation/1'if role else'market_public_source93_readonly_observation/1')and value.get('status')=='match'
  and value.get('expected_manifest_sha256')==spec['manifest_sha256'] and value.get('expected_rows_sha256')==spec['rows_sha256']
  and type(value.get('source_files'))is int and value['source_files']==spec['count']
  and type(value.get('excluded_cache_metadata_files'))is int and value['excluded_cache_metadata_files']==0,'source93_probe')
 need(value.get('all_source_rows_match')is True and value.get('source_only_nonatomic_observation')is True
  and all(value.get(k)is False for k in ('project_cache_bodies_read','unknown_noncache_files_admitted','project_imported',
   'already_running_code_proven','business_data_freshness_proven','security_accepted')),'source93_probe')
 if role:need(value.get('role')==receipt['role']and value.get('business_commit')==spec['business_commit']and value.get('business_tree')==spec['business_tree']and value.get('role_catalog_sha256')==ROLE_CATALOG_SHA,'role_receipt')
 return value
def validate_sqlite_probe(value,lock):
 keys={'status','sqlite_package','sqlite_runtime','libmagic_checked','sqlite_system_library','sqlite_stdlib_extension','python_environment_ignored','compatibility',
  'wal_reset_package_identity_checked','wal_reset_reproduction_performed','production_data_accessed','all_cves_attested'}
 need(type(value)is dict and set(value)==keys and value.get('status')=='PASS'and value.get('sqlite_package')==lock['package']['version']
  and value.get('sqlite_runtime')==lock['package']['version'].split('-',1)[0]
  and value.get('python_environment_ignored')is True and value.get('wal_reset_package_identity_checked')is True
  and all(value.get(key)is False for key in ('libmagic_checked','wal_reset_reproduction_performed','production_data_accessed','all_cves_attested')),'sqlite_probe')
 actual=value.get('sqlite_system_library');need(type(actual)is dict and actual.get('mapped_inode_verified')is True
  and all(actual.get(key)==item for key,item in lock['installed_library'].items())and type(value.get('compatibility'))is dict,'sqlite_probe')
 ext=value['sqlite_stdlib_extension'];need(type(ext)is dict and set(ext)=={'path','bytes','sha256','mapped_inode_verified'}and ext['mapped_inode_verified']is True
  and type(ext['path'])is str and Path(ext['path']).parent==Path('/usr/local/lib/python3.11/lib-dynload')and re.fullmatch(r'_sqlite3\.[A-Za-z0-9_-]+\.so',Path(ext['path']).name)
  and type(ext['bytes'])is int and 0<ext['bytes']<=16*1024**2 and hex64(ext['sha256']),'sqlite_probe')
 compat=value['compatibility'];need(set(compat)=={'wal_snapshot','rollback_upsert','json_fts','backup_readonly_reopen','lookup_index','query_result_sha256','query_plan'}
  and all(compat.get(k)is True for k in ('wal_snapshot','rollback_upsert','json_fts','backup_readonly_reopen','lookup_index'))
  and compat['query_result_sha256']==sha256(json.dumps([['g2',3,'emami',62000000],['g1',1,'emami',61500000]],ensure_ascii=False).encode()).hexdigest()
  and type(compat['query_plan'])is list and 0<len(compat['query_plan'])<=16
  and all(type(v)is str and len(v)<=1024 for v in compat['query_plan'])and any('quote_lookup'in v for v in compat['query_plan']),'sqlite_probe');return value
def run_owned_probe(root,image,signature,kind,plan,*,guard,role=None):
 need(kind in {'source93','source-role','sqlite'}and hex64(signature),'probe_scope')
 name='normal-market-'+kind+'-'+signature[:24];cid=None;env=environment(root)
 def failed_start(code,raw,count):
  # Only these two fixed public no-business-Env probes may publish child text.
  # No command arguments, auth, environment or arbitrary subprocess is logged.
  need(type(code)is int and code!=0 and type(raw)is bytes and type(count)is int and count==len(raw)<=MAX_JSON,'probe_diagnostic')
  maximum=8192;prefix=raw[:maximum];tail=raw[max(len(prefix),len(raw)-maximum):]
  value={'schema':'normal_market_fixed_public_probe_failure/1','kind':kind,'stage':'start',
   'actual_client_returncode':code,'combined_stdout_stderr':True,'observed_bytes':count,
   'prefix_bytes':len(prefix),'prefix_offset':0,'output_prefix':prefix.decode('utf-8',errors='replace'),
   'tail_bytes':len(tail),'tail_offset':count-len(tail),'output_tail':tail.decode('utf-8',errors='replace'),
   'output_truncated':len(prefix)+len(tail)<count,'diagnostic_only':True,'acceptance_evidence':False,'security_accepted':False}
  save(root/'reports'/('probe-'+kind+'-start-failure.json'),encoded(value))
 def cli(argv,maximum=MAX_JSON,seconds=60,enforce=True):
  kwargs={'nonzero':failed_start}if enforce and argv==['container','start','--attach',cid]else{}
  return command([*DOCKER,*argv],environment=env,maximum=maximum,seconds=seconds,guard=guard if enforce else lambda:None,**kwargs)
 def inspect(enforce=True):
  value=document(cli(['container','inspect','--format','{{json .}}',cid],enforce=enforce));need(value.get('Id')==cid,'probe_owner')
  return validate_probe_container(value,name,image,signature,kind,root,role=role)
 try:
  raw=cli(['container','create','--name',name,'--label','io.gold-trade.normal-market-ci-owner='+signature,*plan[kind]['argv']],maximum=128)
  cid=raw.decode().strip();need(re.fullmatch('[a-f0-9]{64}',cid),'probe_owner');inspect()
  raw=cli(['container','start','--attach',cid],maximum=MAX_JSON,seconds=90);state=inspect().get('State')
  need(type(state)is dict and state.get('Running')is False and state.get('Paused')is False and state.get('Pid')==0
   and type(state.get('ExitCode'))is int and state['ExitCode']==0,'probe_exit')
  return document(raw)
 finally:
  if cid is not None:
   value=inspect(False);state=value.get('State');need(type(state)is dict and type(state.get('Running'))is bool,'probe_cleanup')
   if state['Running']:cli(['container','stop','--time','3',cid],maximum=128,seconds=15,enforce=False)
   state=inspect(False).get('State');need(state.get('Running')is False and state.get('Pid')==0,'probe_cleanup')
   cli(['container','rm',cid],maximum=128,seconds=15,enforce=False)
def receipt_contract(receipt,signature,controllers,cache_rows,epoch):
 spec=source_parameters(receipt);role=receipt.get('role')if receipt.get('schema')=='normal_market_git_component_inputs/2'else None
 names=controller_files(role)
 need(type(receipt)is dict and receipt.get('schema')in('normal_market_git_component_inputs/1','normal_market_git_component_inputs/2')
  and receipt.get('signature_format')=='normal-market-two-git-sources-v1'and receipt.get('business_manifest_sha256'if role is not None else'accepted_manifest_sha256')==spec['manifest_sha256']
  and receipt.get('repository_identity')=={'host':'github.com','repository':REPOSITORY}
  and all(hex40(receipt.get(key))for key in ('release_commit','release_tree','business_commit','business_tree'))
  and receipt['release_commit']!=receipt['business_commit']and hex64(signature)and sha256(encoded(receipt)).hexdigest()==signature,'two_git_signature')
 need(type(controllers)is list and len(controllers)==len(names)and all(type(r)is dict and set(r)=={'path','mode','git_blob','bytes','sha256'}
  and r['path']in names and r['mode']in {'100644','100755'}and hex40(r['git_blob'])and hex64(r['sha256'])
  and type(r['bytes'])is int and 0<r['bytes']<=MAX_CONTROLLER_SOURCE for r in controllers)
  and controllers==sorted(controllers,key=lambda r:r['path'])and len({r['path']for r in controllers})==len(names),'controller_inventory')
 need(type(cache_rows)is dict and len(cache_rows)==26 and type(epoch)is int and 0<epoch<=4102444800,'controller_inventory')
 for name,row in cache_rows.items():
  need(relative(name)and name.startswith('pip_packages/')and type(row)is dict and set(row)=={'bytes','sha256'}
   and type(row['bytes'])is int and 0<=row['bytes']<=MAX_FILE and hex64(row['sha256']),'cache_binding')
 value={'schema':'normal_market_controller_inputs/1','release_commit':receipt['release_commit'],'release_tree':receipt['release_tree'],
  'repository_identity':receipt['repository_identity'],'two_git_input_signature':signature,'controller_files':controllers,
  'normal_cache_files':cache_rows,'source_date_epoch':epoch,'source_signature_format_unchanged':'normal-market-two-git-sources-v1',
  'business_tests_actual':False,'complete_control_payload':False,'activation_authority':False,'security_accepted':False}
 return value,sha256(encoded(value)).hexdigest()
def validate_reader_source(raw):
 """Admit the exact pure image payload, never its Root SSH launcher."""
 need(type(raw)is bytes and len(raw)==READER_BYTES and sha256(raw).hexdigest()==READER_SHA256,'reader_source_pin')
 tree=ast.parse(raw);values={}
 for node in tree.body:
  if isinstance(node,ast.Assign)and len(node.targets)==1 and isinstance(node.targets[0],ast.Name)and node.targets[0].id in {'ROWS','ROWS_SHA','MANIFEST_SHA'}:
   need(node.targets[0].id not in values,'reader_source_contract');values[node.targets[0].id]=ast.literal_eval(node.value)
 need(set(values)=={'ROWS','ROWS_SHA','MANIFEST_SHA'}and values['MANIFEST_SHA']==MANIFEST_SHA and values['ROWS_SHA']==ROWS_SHA
  and type(values['ROWS'])is list and len(values['ROWS'])==93 and sha256(encoded(values['ROWS'])).hexdigest()==ROWS_SHA,'reader_source_contract')
 need(all(type(row)is dict and set(row)=={'path','mode','bytes','sha256','git_blob'}and relative(row['path'])and row['mode']=='100644'
  and type(row['bytes'])is int and 0<=row['bytes']<=MAX_FILE and hex64(row['sha256'])and hex40(row['git_blob'])for row in values['ROWS'])
  and len({row['path']for row in values['ROWS']})==93,'reader_source_contract')
 return {'bytes':READER_BYTES,'sha256':READER_SHA256,'manifest_sha256':MANIFEST_SHA,'rows_sha256':ROWS_SHA,'source_files':93}

def controller_sources(repo,release,root,*,role=None):
 """A separate actual Git receipt; explicit separate actual Git controller inventory."""
 folder=root/'controller-source';folder.mkdir(mode=0o700);rows=[];raws={}
 for name in sorted(controller_files(role)):
  raw=git_source(repo,release,name)
  if name==READER:validate_reader_source(raw)
  if name==ROLE_READER:need((len(raw),sha256(raw).hexdigest())==ROLE_READER_PIN,'reader_role_source_pin')
  if name==ROLE_CATALOG_PATH:role_data(raw,role)
  entry=git(repo,'ls-tree','-z',release,'--',name,maximum=4096)
  match=re.fullmatch(rb'(100644|100755) blob ([a-f0-9]{40})\t'+re.escape(name.encode())+rb'\x00',entry)
  need(match is not None,'controller_source')
  blob=sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest();need(blob==match[2].decode(),'controller_source')
  (folder/name).parent.mkdir(mode=0o755,parents=True,exist_ok=True);save(folder/name,raw);raws[name]=raw
  rows.append({'path':name,'mode':match[1].decode(),'git_blob':blob,'bytes':len(raw),'sha256':sha256(raw).hexdigest()})
 return rows,raws
def module(raw,path):
 value=ModuleType('_normal_market_'+path.stem);value.__file__=str(path);exec(compile(raw,str(path),'exec'),value.__dict__);return value
class ContextSnapshot:
 def __init__(self,root,expected):
  self.root=root;self.expected=expected;self.dirs=[];self.inputs=None;self.closed=False
  try:
   for name in sorted(context_rows(root,expected)):
    path=root/name;before=identity(path.lstat());fd=os.open(path,os.O_PATH|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC);self.dirs.append((path,fd,before))
    need(identity(os.fstat(fd))==before,'context_changed')
   self.inputs=Inputs([(root/name,pin,pin['bytes']==0)for name,pin in sorted(expected.items())]);self.check()
  except BaseException:self.close();raise
 def check(self):
  if self.inputs is not None:self.inputs.check()
  for path,fd,before in self.dirs:need(identity(os.fstat(fd))==identity(path.lstat())==before,'context_changed')
 def inventory(self):self.check();context_rows(self.root,self.expected)
 def named(self):
  if self.inputs is not None:
   for row in self.inputs.files:row.named()
  for path,_,before in self.dirs:need(identity(path.lstat())==before,'context_changed')
 def close(self):
  if self.closed:return self.named()
  self.closed=True;error=None
  if self.inputs is not None:
   try:self.inputs.close()
   except BaseException as e:error=e
  for path,fd,before in reversed(self.dirs):
   try:need(identity(os.fstat(fd))==identity(path.lstat())==before,'context_changed')
   except BaseException as e:error=e
   finally:
    try:os.close(fd)
    except BaseException as e:error=e
  for path,_,before in self.dirs:
   try:need(identity(path.lstat())==before,'context_changed')
   except BaseException as e:error=e
  if error is not None:raise error
def historical81_sources(raws,release,receipt,image,signature,context):
 B=module(raws[BUSINESS],release/BUSINESS);O=module(raws[OUTER],release/OUTER)
 role=receipt.get('schema')=='normal_market_git_component_inputs/2'
 candidate=B.role_candidate(raws[ROLE_CATALOG_PATH],receipt['role'])if role else None
 selection=document(raws[BUSINESS_DIR+'/f79-business-test-selection.json'])
 base={r['path']:raws[BUSINESS_DIR+'/fixtures/base/'+r['path']]for r in selection['historical_fixture_files']}
 replay=raws[BUSINESS_DIR+'/fixtures/replay/'+B.CAPTURE]
 required={r['path']:r for r in receipt['release_files']}
 recipe_pin=required['deploy/market-data/Dockerfile.capture2'];recipe=Held(context/'deploy/market-data/Dockerfile.capture2',binding={k:recipe_pin[k]for k in('bytes','sha256')})
 accepted=Held(context/'deploy/market-data/accepted-f79-business.manifest.json',maximum=65536,binding={k:required['deploy/market-data/accepted-f79-business.manifest.json'][k]for k in('bytes','sha256')})
 try:
  helpers={name:(accepted.raw()if name=='accepted-f79-business-manifest.json'else raws[BUSINESS_DIR+'/'+name])for name in B.HELPERS}
  approval=document(raws[BUSINESS_DIR+'/python31118-root-assertion-approval.json'])
  contents,manifest,plans=B.prepare_bundle(selection,base,replay,image=image,release_commit=receipt['release_commit'],two_git_signature=signature,
   release_recipe=recipe.raw(),recipe_spec={'path':'deploy/market-data/Dockerfile.capture2','source_commit':receipt['release_commit'],**{k:recipe_pin[k]for k in('bytes','sha256','git_blob')}},
   helper_sources=helpers,approved_assertion_source_sha256=approval['after']['sha256'],
   candidate_product=candidate,role_catalog_raw=raws[ROLE_CATALOG_PATH]if role else None)
 finally:
  accepted.close();recipe.close()
 return B,O,contents,manifest,plans


def actual_business81(root,raws,controllers,receipt,signature,controller_signature,image,context,guard):
 """Only this genuine producer can emit the historical81 completion proof."""
 B,O,contents,manifest,plans=historical81_sources(raws,root/'controller-source',receipt,image,signature,context)
 bindings={r['path']:{k:r[k]for k in('bytes','sha256')}for r in controllers}
 self_path=Path(__file__).resolve()
 source_paths={'outer':{'path':root/'controller-source'/OUTER,'binding':bindings[OUTER]},
  'business':{'path':root/'controller-source'/BUSINESS,'binding':bindings[BUSINESS]},
  'normal':{'path':self_path,'binding':bindings[SELF]}}
 approval_paths={k:root/'controller-source'/BUSINESS_DIR/v for k,v in [('approval','python31118-root-assertion-approval.json'),('proposal','python31118-security-base-assertion-proposal.json'),('patch','python31118-security-base-assertion.patch')]}
 api=ModuleType('_current_authenticated_normal_market');api.__dict__.update(globals())
 value=O.produce(api,B,root=root,repo=Path(os.environ['GITHUB_WORKSPACE']),image=image,receipt=receipt,signature=signature,
  controller_signature=controller_signature,contents=contents,manifest=manifest,plans=plans,source_paths=source_paths,approval_paths=approval_paths,guard=guard)
 need(type(value)is dict and value.get('schema')==business_schema(receipt)and value.get('tests')==81
  and value.get('actual_profile_containers_removed')==3 and value.get('actual_outer_authentication_and_cleanup_still_required')is False
  and value.get('source_and_output_finalizers_completed')is True and value.get('image_id')==image
  and value.get('two_git_input_signature')==signature and value.get('controller_input_signature')==controller_signature,'business81_actual')
 if receipt.get('schema')=='normal_market_git_component_inputs/2':
  need(value.get('candidate_product')==B.role_candidate(raws[ROLE_CATALOG_PATH],receipt['role'])and value.get('business_commit')==receipt['business_commit']and value.get('business_tree')==receipt['business_tree']and value.get('fixture_business_provenance')==B.fixture_business_provenance(),'role_candidate')
 return value


def controller_parcel_inputs(root,receipt,controllers,cache):
 """Explicit full CI source closure, not an operational deployment control."""
 role=receipt.get('role')if receipt.get('schema')=='normal_market_git_component_inputs/2'else None
 count=source_parameters(receipt)['count'];names=controller_files(role);release_count=20+(1 if role is not None else 0)
 result={}
 def add(name,path,pin,mode):
  need(relative(name)and name not in result and type(pin)is dict and set(pin)=={'bytes','sha256'}
   and type(pin['bytes'])is int and 0<=pin['bytes']<=MAX_FILE and hex64(pin['sha256'])and mode in(0o644,0o755),'controller_parcel_inventory')
  result[name]={'path':path,'binding':pin,'mode':mode}
 for prefix,location,rows in [('release',root/'sealed/release',receipt['release_files']),('product',root/'sealed/product',receipt['product_files']),('controller',root/'controller-source',controllers)]:
  for row in rows:add(prefix+'/'+row['path'],location/row['path'],{k:row[k]for k in('bytes','sha256')},int(row['mode'],8)&0o777)
 for name,pin in cache.items():add('release/'+name,root/'sealed/release'/name,pin,0o644)
 need(len(receipt['release_files'])==release_count and len(receipt['product_files'])==count and len(controllers)==len(names)
  and len(cache)==26 and len(result)==release_count+count+len(names)+26,'controller_parcel_inventory')
 need(sum(s['binding']['bytes']for s in result.values())<=MAX_CONTEXT,'controller_parcel_bound')
 return result


def controller_parcel(root,receipt,controllers,cache,signature,controller_signature,epoch,guard,*,retained=None,final_checks=None):
 """Write and verify every selected source byte; no archive extraction/actions."""
 import io
 entries=controller_parcel_inputs(root,receipt,controllers,cache);held=[];out=None;archive=None
 rows=[{'path':name,**spec['binding'],'mode':spec['mode']}for name,spec in sorted(entries.items())]
 index={'schema':'normal_market_complete_ci_controller_sources/1','release_commit':receipt['release_commit'],
  'release_tree':receipt['release_tree'],'business_commit':receipt['business_commit'],'business_tree':receipt['business_tree'],
  'two_git_input_signature':signature,'controller_input_signature':controller_signature,'files':rows,
  'source_date_epoch':epoch,'operational_deployment_control':False,'activation_authority':False,'security_accepted':False}
 raw_index=encoded(index);need(len(raw_index)<=MAX_JSON,'controller_parcel_bound')
 target=root/'controller-sources.tar.gz';fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
 class Sink:
  def __init__(self):self.count=0;self.digest=sha256()
  def write(self,raw):
   need(self.count+len(raw)<=MAX_CONTEXT+4*1024**2,'controller_parcel_bound');view=memoryview(raw)
   while view:n=os.write(fd,view);need(n>0,'output_write');view=view[n:]
   self.count+=len(raw);self.digest.update(raw);return len(raw)
  def flush(self):os.fsync(fd)
 sink=Sink();end=time.monotonic()+120;last=[None]
 def check():
  now=time.monotonic();need(now<end,'controller_parcel_deadline')
  if last[0]is None or now-last[0]>=1:
   guard()
   for row in held:row.check()
   last[0]=now
 try:
  for name,spec in sorted(entries.items()):held.append(Held(spec['path'],binding=spec['binding'],empty=spec['binding']['bytes']==0))
  with gzip.GzipFile(filename='',fileobj=sink,mode='wb',mtime=0)as compressed:
   with tarfile.open(fileobj=compressed,mode='w|',format=tarfile.USTAR_FORMAT)as archive:
    def write(name,raw,mode):
     check();item=tarfile.TarInfo(name);item.uid=item.gid=0;item.uname=item.gname='';item.mode=mode;item.mtime=epoch;item.size=len(raw);archive.addfile(item,io.BytesIO(raw));check()
    write('controller-manifest.json',raw_index,0o644)
    for(name,spec),row in zip(sorted(entries.items()),held):write(name,row.raw(MAX_FILE),spec['mode'])
  os.fsync(fd);created=identity(os.fstat(fd));written_binding={'bytes':sink.count,'sha256':sink.digest.hexdigest()}
  need(created[6]==sink.count and identity(target.lstat())==created,'controller_parcel_binding')
 finally:
  os.close(fd)
  close_error=None
  for row in reversed(held):
   try:row.close()
   except BaseException as e:close_error=e
  for row in held:
   try:row.named()
   except BaseException as e:close_error=e
  if close_error is not None:raise close_error
 archive=Held(target,maximum=MAX_CONTEXT+4*1024**2,binding=written_binding);proofs=[];proof_checks=[]
 prior=[row.named for row in held]+[archive.named]
 try:
  need(archive.before==created,'controller_parcel_binding')
  verify_controller_parcel(archive,index,guard)
  value={'schema':'normal_market_complete_ci_controller_parcel/1','archive':archive.binding,'index_sha256':sha256(raw_index).hexdigest(),
   'members':len(rows)+1,'sources':len(rows),'release_files':len(receipt['release_files']),'product_files':len(receipt['product_files']),'controller_files':len(controllers),'cache_files':26,
   'release_commit':receipt['release_commit'],'business_commit':receipt['business_commit'],'two_git_input_signature':signature,
   'controller_input_signature':controller_signature,'complete_ci_controller_payload':True,
   'complete_deployment_control_payload':False,'activation_authority':False,'security_accepted':False}
  guard();need(time.monotonic()<end,'controller_parcel_deadline');publish_known(root/'controller-sources.json',encoded(value),prior,retained=proofs,final_checks=proof_checks)
 finally:finish_or_transfer([archive,*proofs],[*prior,*proof_checks],retained,final_checks)
 need(time.monotonic()<end,'controller_parcel_deadline');return value


def verify_controller_parcel(held,index,guard):
 """Strict physical member inventory and full payload hashes; never extract."""
 expected={r['path']:r for r in index['files']};index_raw=encoded(index);expected['controller-manifest.json']={'bytes':len(index_raw),'sha256':sha256(index_raw).hexdigest(),'mode':0o644}
 seen=set();end=time.monotonic()+120
 with os.fdopen(os.dup(held.fd),'rb')as source:
  source.seek(0)
  with tarfile.open(fileobj=source,mode='r|gz')as archive:
   for item in archive:
    guard();held.check();need(time.monotonic()<end,'controller_parcel_deadline')
    need(item.name in expected and item.name not in seen and item.isfile()and not item.pax_headers
     and item.uid==item.gid==0 and item.uname==item.gname==''and item.mtime==index['source_date_epoch'],'controller_parcel_inventory')
    spec=expected[item.name];need(item.size==spec['bytes']and item.mode==spec['mode'],'controller_parcel_inventory');seen.add(item.name);h=sha256();count=0
    with archive.extractfile(item)as payload:
     while True:
      guard();need(time.monotonic()<end,'controller_parcel_deadline');block=payload.read(min(65536,spec['bytes']-count+1))
      if not block:break
      count+=len(block);need(count<=spec['bytes'],'controller_parcel_bound');h.update(block)
    need(count==spec['bytes']and h.hexdigest()==spec['sha256'],'controller_parcel_binding')
 need(seen==set(expected),'controller_parcel_inventory');held.check();return True


def finish_evidence(resources,checks=()):
 """All physical closes precede every retained named check, even on failure."""
 unique=[];seen=set();error=None
 for row in resources:
  if row is not None and id(row)not in seen:unique.append(row);seen.add(id(row))
 for row in reversed(unique):
  try:
   if not isinstance(row,Held)or row.fd is not None:row.close()
  except BaseException as exc:
   if error is None:error=exc
 for row in unique:
  try:row.named()
  except BaseException as exc:
   if error is None:error=exc
 for check in checks:
  try:check()
  except BaseException as exc:
   if error is None:error=exc
 if error is not None:raise error


def finish_or_transfer(resources,checks,retained,final_checks):
 if retained is None:
  need(final_checks is None,'evidence_ownership');finish_evidence(resources,checks)
 else:
  need(type(retained)is list and type(final_checks)is list,'evidence_ownership')
  retained.extend(row for row in resources if row is not None);final_checks.extend(checks)


def hold_written(path,fd,maximum,*,binding=None,guard=lambda:None,expected_frame=None):
 """Bind an exclusive output while its first writer FD is still retained."""
 created=identity(os.fstat(fd));need(expected_frame is None or created==expected_frame,'source_changed');need(created==identity(path.lstat())and created[2]==0o100600
  and created[3:6]==(os.geteuid(),os.getegid(),1)and 0<created[6]<=maximum,'source_file')
 if binding is None:
  count=0;h=sha256();end=time.monotonic()+120;bounded=periodic(guard)
  while count<created[6]:
   need(time.monotonic()<end,'command_timeout');bounded();raw=os.pread(fd,min(65536,created[6]-count),count)
   need(raw,'source_bound');count+=len(raw);h.update(raw)
  binding={'bytes':count,'sha256':h.hexdigest()}
 need(type(binding)is dict and set(binding)=={'bytes','sha256'}and type(binding['bytes'])is int
  and binding['bytes']==created[6]and hex64(binding['sha256'])and identity(os.fstat(fd))==identity(path.lstat())==created,'source_changed')
 row=Held(path,maximum=maximum,binding=binding)
 try:need(row.before==created,'source_changed');guard();row.check();return row
 except BaseException:
  row.close();raise


def outer_main(argv=None,*,close_driver,driver_named):
 """The verified workflow closes its original driver before global finals."""
 resources=[];checks=[];error=None;code=None
 try:code=main(argv,retained=resources,final_checks=checks)
 except BaseException as exc:error=exc
 try:close_driver()
 except BaseException as exc:
  if error is None:error=exc
 try:finish_evidence(resources,[*checks,driver_named])
 except BaseException as exc:
  if error is None:error=exc
 if error is not None:raise error
 return code


def prepare_checked_caches(root,context,receipt,source_guard,*,retained=None,final_checks=None):
 """Cache temp namespaces finish before immutable context admission."""
 global PHASE
 release=Inputs([(context/r['path'],{k:r[k]for k in('bytes','sha256')},False)for r in receipt['release_files']])
 def check():source_guard();release.check()
 try:
  for name,cache in [('prepare_app_security_os_cache.py',CACHE_ROOTS[0]),('prepare_sqlite_wal_os_cache.py',CACHE_ROOTS[1]),('prepare_tar_acl_os_cache.py',CACHE_ROOTS[2]),('prepare_coreutils_acl_os_cache.py',CACHE_ROOTS[3])]:
   PHASE='normal_cache_prepare';fd=os.open(root/('cache-'+name+'-prepare.log'),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
   try:command([sys.executable,'-I','-B',str(context/'scripts'/name),'--cache-dir',str(context/cache)],
    environment=environment(root,token=True),seconds=900,maximum=MAX_JSON,guard=check,target=fd)
   finally:os.fsync(fd);os.close(fd)
  for name,cache_name in [('prepare_app_security_os_cache.py',CACHE_ROOTS[0]),('prepare_sqlite_wal_os_cache.py',CACHE_ROOTS[1]),('prepare_tar_acl_os_cache.py',CACHE_ROOTS[2]),('prepare_coreutils_acl_os_cache.py',CACHE_ROOTS[3])]:
   PHASE='normal_cache_check';fd=os.open(root/('cache-'+name+'-check.log'),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
   try:command([sys.executable,'-I','-B',str(context/'scripts'/name),'--check','--cache-dir',str(context/cache_name)],
    environment=environment(root),seconds=180,guard=check,target=fd)
   finally:os.fsync(fd);os.close(fd)
  check()
 finally:finish_or_transfer([release],[],retained,final_checks)
 return release


def normal_build(root,repo,*,retained=None,final_checks=None,role=None):
 """Concrete normal build; separate full business/acceptance gates stay open."""
 global PHASE
 hosted(root,repo);release=os.environ.get('GITHUB_SHA');need(hex40(release),'release_commit')
 need(git(repo,'rev-parse','--abbrev-ref','@{u}',maximum=128).strip()==b'origin/main','clean_main_required')
 for folder in ('reports','docker-config','gh-config','proof'):(root/folder).mkdir(mode=0o700)
 save(root/'empty-trivy.yaml',b'{}\n')
 PHASE='controller_source';controllers,raws=controller_sources(repo,release,root,role=role)
 sealer=module(raws[SEALER],root/'controller-source'/SEALER)
 original=Held(Path(__file__),maximum=MAX_SOURCE,binding={'bytes':len(raws[SELF]),'sha256':sha256(raws[SELF]).hexdigest()})
 controller_hold=None;snapshots=[];published=None;business_hold=None;evidence=[];evidence_checks=[]
 try:
  controller_hold=Inputs([(root/'controller-source'/row['path'],{k:row[k]for k in ('bytes','sha256')},False)for row in controllers])
  preflight_assertion_sources(raws)
  PHASE='git_product_seal';sealer.seal(sealer.GitRepository(repo),release,root/'sealed',role=role)
  source=Held(root/'sealed/source-inputs.json',maximum=MAX_JSON);evidence.append(source);sealed=document(source.raw())
  receipt=sealed['inputs'];signature=sealed['input_signature'];sealer.input_receipt(receipt,signature);require_selected(receipt,role)
  need(receipt['repository_identity']=={'host':'github.com','repository':REPOSITORY},'repository_identity')
  before_main=live_main(root,receipt['repository_identity'],release);epoch=source_epoch(repo,release)
  default_driver(root);context=root/'sealed/release';product=root/'sealed/product';(context/'pip_packages').mkdir(mode=0o755)
  def source_check():
   original.check();controller_hold.check()
   space=os.statvfs(root);need(space.f_bavail*space.f_frsize>=RESERVE,'disk_reserve')
  release_sources=prepare_checked_caches(root,context,receipt,source_check,retained=evidence,final_checks=evidence_checks)
  cache,sqlite=cache_inputs(context,receipt)
  expected={r['path']:{k:r[k]for k in ('bytes','sha256')}for r in receipt['release_files']};need(not set(expected)&set(cache),'context_inventory');expected.update(cache)
  product_expected={r['path']:{k:r[k]for k in ('bytes','sha256')}for r in receipt['product_files']}
  need(sum(row['bytes']for row in expected.values())+sum(row['bytes']for row in product_expected.values())<=MAX_CONTEXT,'context_bound')
  PHASE='context_restamp';restamp(context,expected,epoch,source_rows=release_sources.files,retained=evidence,final_checks=evidence_checks);restamp(product,product_expected,epoch,retained=evidence,final_checks=evidence_checks)
  snapshots.append(ContextSnapshot(context,expected));snapshots.append(ContextSnapshot(product,product_expected))
  controller,controller_signature=receipt_contract(receipt,signature,controllers,cache,epoch)
  def check():
   original.check();controller_hold.check()
   for row in evidence:row.check()
   if business_hold is not None:business_hold.check()
   for snap in snapshots:snap.check()
   space=os.statvfs(root);need(space.f_bavail*space.f_frsize>=RESERVE,'disk_reserve')
  def revalidate_source():
   check();observed=sealer.collect(sealer.GitRepository(repo),release,raws[SEALER],role=role)
   need(observed[0]==receipt and observed[1]==signature,'source_changed')
   need(git(repo,'rev-parse','--abbrev-ref','@{u}',maximum=128).strip()==b'origin/main','clean_main_required')
   for snap in snapshots:snap.inventory()
  revalidate_source();before_main=live_main(root,receipt['repository_identity'],release)
  publish_known(root/'source-identity.json',encoded({'schema':'normal_market_source_identity/1','inputs':receipt,'input_signature':signature,
   'controller':controller,'controller_signature':controller_signature,'live_main_before_build':before_main,'security_accepted':False}),retained=evidence,final_checks=evidence_checks)
  publish_known(root/'input-manifest.json',encoded({'schema':'normal_market_two_context_inputs/1','release':expected,'product':product_expected,
   'two_git_input_signature':signature,'controller_input_signature':controller_signature,'source_date_epoch':epoch,'security_accepted':False}),retained=evidence,final_checks=evidence_checks)
  argv=build_argv(root,sealer.build_contract(receipt,signature,root/'sealed'),controller_signature,epoch,os.environ['GITHUB_RUN_ID'],os.environ['GITHUB_RUN_ATTEMPT'],role=role)
  PHASE='ordinary_build';log=os.open(root/'build.log',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
  try:command(argv,environment=environment(root),seconds=2400,maximum=MAX_LOG,target=log,guard=periodic(check))
  finally:os.fsync(log);os.close(log)
  iid=Held(root/'candidate-image.id',maximum=128);evidence.append(iid);image=iid.raw(128).decode().strip()
  need(re.fullmatch('sha256:[a-f0-9]{64}',image),'image_identity')
  def inspect():return document(command([*DOCKER,'image','inspect','--format','{{json .}}',image],environment=environment(root),guard=check))
  PHASE='actual_image';before=inspect();image_record=image_contract(before,receipt,signature,controller_signature)
  if role is None:
   save(root/'proof/reader.py',raws[READER],mode=0o444);proof=Held(root/'proof/reader.py',maximum=MAX_SOURCE,binding={'bytes':len(raws[READER]),'sha256':sha256(raws[READER]).hexdigest()})
  else:
   proof=public_probe_file(root/'proof/reader.py',raws[ROLE_READER],evidence,check)
   public_probe_file(root/'proof/role-catalog.json',raws[ROLE_CATALOG_PATH],evidence,check)
  try:
   plan=probe_plan(root,image,proof.binding['sha256'],role=role);PHASE='actual_source_role'if role is not None else'actual_source93'
   kind='source-role'if role is not None else'source93'
   actual_source=validate_source_probe(run_owned_probe(root,image,controller_signature,kind,plan,guard=check,role=role),receipt)
   PHASE='actual_sqlite';actual_sqlite=validate_sqlite_probe(run_owned_probe(root,image,controller_signature,'sqlite',plan,guard=check),sqlite)
   proof.check()
  finally:
   if role is None:evidence.append(proof)
  publish_known(root/('reports/actual-source-role.json'if role is not None else'reports/actual-source93.json'),encoded(actual_source),retained=evidence,final_checks=evidence_checks);publish_known(root/'reports/actual-sqlite.json',encoded(actual_sqlite),retained=evidence,final_checks=evidence_checks)
  PHASE='actual_historical81';actual81=actual_business81(root,raws,controllers,receipt,signature,controller_signature,image,context,check)
  business_root=Path(actual81['run_directory']);need(business_root.parent==root and re.fullmatch('business81-[a-f0-9]{32}',business_root.name),'business81_actual')
  needed=[(business_root/'completed.json',{'bytes':len(encoded(actual81)),'sha256':sha256(encoded(actual81)).hexdigest()},False)]
  for name,pin in actual81['profile_outputs'].items():
   need(relative(name)and name.split('/')[0]in('base-nonroot','base-root','added-replay')and name.split('/')[-1]in('intent.json','received.json','profile.json','tests.stderr','container-closed.json'),'business81_actual')
   needed.append((business_root/name,pin,pin['bytes']==0))
  business_hold=Inputs(needed)
  PHASE='controller_sources_parcel';parcel=controller_parcel(root,receipt,controllers,cache,signature,controller_signature,epoch,check,retained=evidence,final_checks=evidence_checks)
  publish_known(root/'reports/actual-historical81.json',encoded(actual81),retained=evidence,final_checks=evidence_checks)

  PHASE='build_finalization';revalidate_source();after_main=live_main(root,receipt['repository_identity'],release)
  need(inspect()['Config']==before['Config'],'image_config');image_contract(inspect(),receipt,signature,controller_signature)
  published={'schema':'normal_market_capture2_image_build/1','status':'built_not_accepted',**image_record,'source_inputs':receipt,
   'run_id':int(os.environ['GITHUB_RUN_ID']),'run_attempt':int(os.environ['GITHUB_RUN_ATTEMPT']),
   'two_git_input_signature':signature,'controller_inputs':controller,'controller_input_signature':controller_signature,
   'live_main_before_build':before_main,'live_main_after_build':after_main,'actual_source93_completed':True,'actual_sqlite_completed':True,
   'hosted_vm_observed':{'cpu_count':os.cpu_count(),'physical_memory_bytes':os.sysconf('SC_PHYS_PAGES')*os.sysconf('SC_PAGE_SIZE'),
    'resource_boundary':'dedicated_GitHub_hosted_job_VM','job_timeout_minutes':60,'client_cgroup_limit_claimed':False},
   'historical81_business_tests_completed':True,'historical81':actual81,'complete_ci_controller_payload':True,'controller_parcel':parcel,
   'complete_deployment_control_payload':False,'security_accepted':False}
  if role is not None:
   published.update(schema='normal_market_role_image_build/1',role=role,actual_role_source_completed=published.pop('actual_source93_completed'),actual_role_source=actual_source)
 finally:
  resources=[*evidence,business_hold,controller_hold,original,*snapshots]
  finish_or_transfer(resources,evidence_checks,retained,final_checks)
 need(published is not None,'build_not_completed')
 finals=[row.named for row in resources if row is not None]
 publish_known(root/'image-build.json',encoded(published),finals,retained=retained,final_checks=final_checks);return published
def raw_reports(root,image,*,retained=None,final_checks=None):
 held=[]
 try:
  for name in ('vulnerabilities.json','sbom.cdx.json'):held.append(Held(root/'reports'/name,maximum=32*1024**2))
  scan,sbom=(document(row.raw(32*1024**2),32*1024**2)for row in held)
  need(scan.get('Metadata',{}).get('ImageID')==image,'report_identity')
  component=sbom.get('metadata',{}).get('component',{})
  need(component.get('type')=='container'and component.get('name')==image and
   [r.get('value')for r in component.get('properties',[])if r.get('name')=='aquasecurity:trivy:ImageID']==[image],'report_identity')
  counts={key:0 for key in ('UNKNOWN','LOW','MEDIUM','HIGH','CRITICAL')};need(type(scan.get('Results'))is list,'report_shape')
  for result in scan['Results']:
   need(type(result)is dict and(result.get('Vulnerabilities')is None or type(result['Vulnerabilities'])is list),'report_shape')
   for row in result.get('Vulnerabilities')or[]:need(type(row)is dict and row.get('Severity')in counts,'report_shape');counts[row['Severity']]+=1
  bindings={row.path.name:row.binding for row in held}
 finally:
  finish_or_transfer(held,[],retained,final_checks)
 return counts,bindings
def archive_configuration(fd,image,layers):
 with os.fdopen(os.dup(fd),'rb')as incoming:
  incoming.seek(0)
  with tarfile.open(fileobj=incoming,mode='r:')as archive:
   members={}
   for item in archive:
    need(len(members)<4096 and item.name not in members and relative(item.name)and(item.isfile()or item.isdir()),'archive_shape');members[item.name]=item
   def read(name,maximum):
    item=members.get(name);need(item is not None and item.isfile()and 0<item.size<=maximum,'archive_config')
    raw=archive.extractfile(item).read(maximum+1);need(len(raw)==item.size,'archive_config');return raw
   value=document(read('manifest.json',1024**2));need(type(value)is list and len(value)==1 and type(value[0])is dict,'archive_config')
   raw=read(value[0]['Config'],MAX_JSON);need('sha256:'+sha256(raw).hexdigest()==image,'archive_config')
   config=document(raw);need(config.get('rootfs')=={'type':'layers','diff_ids':layers},'archive_config')
 return image
def finalize(root,repo,*,retained=None,final_checks=None,role=None):
 """Export the same unaccepted image; never rewrite/filter the raw reports."""
 global PHASE
 hosted(root,repo);build=Held(root/'image-build.json',maximum=MAX_JSON);inputs=Held(root/'input-manifest.json',maximum=MAX_JSON)
 tar_hold=None;gz_hold=None;snapshots=[];output=None;evidence=[];evidence_checks=[]
 try:
  record=document(build.raw());contexts=document(inputs.raw());release=record['source_inputs']['release_commit'];receipt=record['source_inputs'];require_selected(receipt,role);signature=record['two_git_input_signature'];csig=record['controller_input_signature'];image=record['image_id']
  need(release==os.environ.get('GITHUB_SHA')and record.get('schema')==('normal_market_role_image_build/1'if role is not None else'normal_market_capture2_image_build/1')
   and record.get('status')=='built_not_accepted'and record.get('security_accepted')is False
   and type(record.get('run_id'))is int and record['run_id']==int(os.environ['GITHUB_RUN_ID'])
   and type(record.get('run_attempt'))is int and record['run_attempt']==int(os.environ['GITHUB_RUN_ATTEMPT']),'build_receipt')
  need(contexts.get('two_git_input_signature')==signature and contexts.get('controller_input_signature')==csig,'build_receipt')
  # Re-admit the same normal Git sources; no old single-tree receipt is eligible.
  sealer_raw=git_source(repo,release,SEALER);sealer=module(sealer_raw,root/'controller-source'/SEALER)
  current=sealer.collect(sealer.GitRepository(repo),release,sealer_raw,role=role);need(current[0]==receipt and current[1]==signature,'source_changed')
  snapshots.append(ContextSnapshot(root/'sealed/release',contexts['release']));snapshots.append(ContextSnapshot(root/'sealed/product',contexts['product']))
  for row in record['controller_inputs']['controller_files']:
   raw=git_source(repo,release,row['path']);need(len(raw)==row['bytes']and sha256(raw).hexdigest()==row['sha256'],'controller_source')
  reconstructed,recomputed=receipt_contract(receipt,signature,record['controller_inputs']['controller_files'],record['controller_inputs']['normal_cache_files'],source_epoch(repo,release))
  need(reconstructed==record['controller_inputs']and recomputed==csig,'controller_signature')
  need(record.get('actual_role_source_completed'if role is not None else'actual_source93_completed')is True,'source93_probe')
  if role is not None:validate_source_probe(record['actual_role_source'],receipt)
  before_main=live_main(root,receipt['repository_identity'],release)
  def check():
   build.check();inputs.check()
   for row in evidence:row.check()
   for snap in snapshots:snap.check()
   space=os.statvfs(root);need(space.f_bavail*space.f_frsize>=RESERVE,'disk_reserve')
  def inspect():
   value=document(command([*DOCKER,'image','inspect','--format','{{json .}}',image],environment=environment(root),guard=check))
   image_contract(value,receipt,signature,csig);return value
  before=inspect();PHASE='raw_report_binding';counts,reports=raw_reports(root,image,retained=evidence,final_checks=evidence_checks)
  PHASE='ordinary_image_export';path=root/'candidate-image.tar';fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
  completion={}
  def stream_complete(binding,frame):
   need(not completion,'source_binding');completion.update(binding=binding,frame=frame)
  try:
   command([*DOCKER,'image','save',image],environment=environment(root),seconds=600,maximum=2*1024**3,target=fd,guard=check,stream_complete=stream_complete);os.fsync(fd)
   need(set(completion)=={'binding','frame'},'source_binding')
   tar_hold=hold_written(path,fd,2*1024**3,binding=completion['binding'],expected_frame=completion['frame'],guard=check)
  finally:os.close(fd)
  tar_hold.check();config_id=archive_configuration(tar_hold.fd,image,before['RootFS']['Layers']);tar_hold.check()
  PHASE='ordinary_image_compression';target=root/'candidate-image.tar.gz';out=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
  class BoundedOutput:
   def __init__(self):self.count=0;self.digest=sha256()
   def write(self,raw):
    need(self.count+len(raw)<=2*1024**3,'archive_bound');view=memoryview(raw)
    while view:n=os.write(out,view);need(n>0,'output_write');view=view[n:]
    self.count+=len(raw);self.digest.update(raw);return len(raw)
   def flush(self):os.fsync(out)
  try:
   end=time.monotonic()+300;offset=0;source_guard=periodic(check)
   sink=BoundedOutput()
   with gzip.GzipFile(filename='',fileobj=sink,mode='wb',mtime=0)as compressed:
    while offset<tar_hold.before[6]:
     need(time.monotonic()<end,'command_timeout');source_guard();tar_hold.check();raw=os.pread(tar_hold.fd,min(65536,tar_hold.before[6]-offset),offset)
     need(raw,'archive_bound');offset+=len(raw);compressed.write(raw)
   os.fsync(out);gz_hold=hold_written(target,out,2*1024**3,binding={'bytes':sink.count,'sha256':sink.digest.hexdigest()},guard=check)
  finally:os.close(out)
  tar_hold.check();gz_hold.check();PHASE='export_finalization'
  need(inspect()['Config']==before['Config'],'image_config');need(raw_reports(root,image,retained=evidence,final_checks=evidence_checks)==(counts,reports),'report_changed')
  need(sealer.collect(sealer.GitRepository(repo),release,sealer_raw,role=role)[:2]==(receipt,signature),'source_changed')
  after_main=live_main(root,receipt['repository_identity'],release)
  for snap in snapshots:snap.inventory()
  output={'schema':'normal_market_two_git_image_export/1','status':'exported_not_accepted','image_id':image,'image_config_id':config_id,
   'release_commit':release,'release_tree':receipt['release_tree'],'business_commit':receipt['business_commit'],'business_tree':receipt['business_tree'],
   'two_git_input_signature':signature,'controller_input_signature':csig,'controller_inputs':record['controller_inputs'],
   'run_id':os.environ['GITHUB_RUN_ID'],'run_attempt':os.environ['GITHUB_RUN_ATTEMPT'],'rootfs':before['RootFS'],
   'archive':{'format':'docker-archive+gzip',**gz_hold.binding,'uncompressed_bytes':tar_hold.binding['bytes'],'uncompressed_sha256':tar_hold.binding['sha256']},
   'raw_counts':counts,'reports':reports,'live_main_before_export':before_main,'live_main_after_export':after_main,
   ('actual_role_source_completed'if role is not None else'actual_source93_completed'):record['actual_role_source_completed'if role is not None else'actual_source93_completed'],'actual_sqlite_completed':record['actual_sqlite_completed'],
   'historical81_business_tests_completed':record['historical81_business_tests_completed'],'historical81':record['historical81'],
   'complete_ci_controller_payload':record['complete_ci_controller_payload'],'controller_parcel':record['controller_parcel'],
   'complete_deployment_control_payload':False,'physical_archive_admission_complete':False,
   'full_public_config_compared_in_ram':True,'activation_authority':False,'security_accepted':False}
  if role is not None:output.update(schema='normal_market_two_git_image_export/2',role=role,actual_role_source=record['actual_role_source'])
 finally:
  resources=[*evidence,gz_hold,tar_hold,inputs,build,*snapshots]
  finish_or_transfer(resources,evidence_checks,retained,final_checks)
 need(output is not None,'export_not_completed');publish_known(root/'image-export.json',encoded(output),[row.named for row in resources if row is not None],retained=retained,final_checks=final_checks);return output
def raw_gate(root,repo,*,retained=None,final_checks=None,role=None):
 hosted(root,repo);row=Held(root/'image-build.json',maximum=MAX_JSON);evidence=[];checks=[]
 try:record=document(row.raw());require_selected(record['source_inputs'],role);counts,_=raw_reports(root,record['image_id'],retained=evidence,final_checks=checks)
 finally:finish_or_transfer([*evidence,row],checks,retained,final_checks)
 need(counts['HIGH']==counts['CRITICAL']==0,'raw_high_critical_findings')
 return {'status':'raw_high_critical_zero','security_accepted':False}
def publish_known(path,raw,prior_checks=(),*,retained=None,final_checks=None):
 """Known bytes and all earlier named operands survive the writer's closes."""
 need(type(raw)is bytes and 0<len(raw)<=MAX_JSON,'output_bound')
 pin={'bytes':len(raw),'sha256':sha256(raw).hexdigest()};fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
 try:
  view=memoryview(raw)
  while view:n=os.write(fd,view);need(n>0,'output_write');view=view[n:]
  os.fchmod(fd,0o600);os.fsync(fd);created=identity(os.fstat(fd))
  need(created[2]==0o100600 and created[3:6]==(os.geteuid(),os.getegid(),1)and created[6]==len(raw)and identity(path.lstat())==created,'source_file')
 finally:os.close(fd)
 held=Held(path,maximum=MAX_JSON,binding=pin)
 try:need(held.before==created,'source_changed');held.check()
 finally:finish_or_transfer([held],prior_checks,retained,final_checks)
 for check in prior_checks:check()
 held.named();return pin


def business81_outputs(root,build,guard,*,retained=None,final_checks=None):
 """Re-admit the captured true profile exits; never synthesize exit-code0."""
 value=build['historical81'];need(type(value)is dict and value.get('schema')==business_schema(build['source_inputs'])
  and value.get('actual_outer_authentication_and_cleanup_still_required')is False and value.get('actual_profile_containers_removed')==3
  and value.get('source_and_output_finalizers_completed')is True and value.get('actual_same_candidate_image_verified')is True
  and value.get('security_accepted')is False and value.get('deployment_authority')is False,'business81_actual')
 for key,expected in [('image_id',build['image_id']),('two_git_input_signature',build['two_git_input_signature']),
  ('controller_input_signature',build['controller_input_signature']),('release_commit',build['source_inputs']['release_commit'])]:need(value.get(key)==expected,'business81_actual')
 work=Path(value['run_directory']);need(work.parent==root and re.fullmatch('business81-[a-f0-9]{32}',work.name),'business81_actual')
 expected_names={profile+'/'+leaf for profile in('base-nonroot','base-root','added-replay')for leaf in('intent.json','received.json','profile.json','tests.stderr','container-closed.json')}
 need(type(value.get('profile_outputs'))is dict and set(value['profile_outputs'])==expected_names,'business81_actual')
 rows=[]
 try:
  original=encoded(value);rows.append(Held(work/'completed.json',maximum=MAX_JSON,binding={'bytes':len(original),'sha256':sha256(original).hexdigest()}))
  for name,pin in sorted(value['profile_outputs'].items()):
   need(type(pin)is dict and set(pin)=={'bytes','sha256'}and type(pin['bytes'])is int and 0<=pin['bytes']<=MAX_JSON and hex64(pin['sha256']),'business81_actual')
   rows.append(Held(work/name,maximum=MAX_JSON,binding=pin,empty=pin['bytes']==0))
  by_name={row.path.relative_to(work).as_posix():row for row in rows};observations={}
  for profile in('base-nonroot','base-root','added-replay'):
   guard();received=document(by_name[profile+'/received.json'].raw());state=document(by_name[profile+'/container-closed.json'].raw());observation=document(by_name[profile+'/profile.json'].raw())
   need(set(received)=={'schema','actual_client_returncode','stderr_bytes','stdout_bytes'}
    and received['schema']=='normal_market_business81_start_observation/1'and type(received['actual_client_returncode'])is int
    and type(received['stderr_bytes'])is int and received['stderr_bytes']==by_name[profile+'/tests.stderr'].binding['bytes']
    and type(received['stdout_bytes'])is int and received['stdout_bytes']==by_name[profile+'/profile.json'].binding['bytes'],'business81_actual')
   need(set(state)=={'schema','profile','container_id','image_id','running','paused','pid','exit_code'}
    and state['schema']=='normal_market_business81_closed_container/1'and state['profile']==profile
    and type(state['container_id'])is str and re.fullmatch('[a-f0-9]{64}',state['container_id'])and state['image_id']==build['image_id']
    and state['running']is False and state['paused']is False and type(state['pid'])is int and state['pid']==0
    and type(state['exit_code'])is int,'business81_actual')
   observations[profile]={'actual_outer_returncode':received['actual_client_returncode'],'actual_container_exit_code':state['exit_code'],'observation':observation}
  return value,observations
 finally:
  finish_or_transfer(rows,[],retained,final_checks)


def complete_evidence(root,repo,*,retained=None,final_checks=None,role=None):
 """Observe existing same-image/controller evidence; no security acceptance."""
 global PHASE
 PHASE='complete_ci_gate';hosted(root,repo);held=[];snapshots=[];value=None;evidence=[];evidence_checks=[]
 try:
  build=Held(root/'image-build.json',maximum=MAX_JSON);held.append(build);export=Held(root/'image-export.json',maximum=MAX_JSON);held.append(export)
  record=document(build.raw());out=document(export.raw());receipt=record['source_inputs'];require_selected(receipt,role);release=receipt['release_commit'];signature=record['two_git_input_signature'];csig=record['controller_input_signature']
  need(release==os.environ.get('GITHUB_SHA')and out.get('image_id')==record['image_id']and out.get('two_git_input_signature')==signature
   and out.get('controller_input_signature')==csig and out.get('run_id')==os.environ['GITHUB_RUN_ID']and out.get('run_attempt')==os.environ['GITHUB_RUN_ATTEMPT']
   and record.get('historical81_business_tests_completed')is True and out.get('historical81_business_tests_completed')is True,'business81_actual')
  raw=git_source(repo,release,SEALER);sealer=module(raw,root/'controller-source'/SEALER);observed=sealer.collect(sealer.GitRepository(repo),release,raw,role=role)
  need(observed[:2]==(receipt,signature),'source_changed');live_main(root,receipt['repository_identity'],release)
  for row in record['controller_inputs']['controller_files']:
   raw=git_source(repo,release,row['path']);need((len(raw),sha256(raw).hexdigest())==(row['bytes'],row['sha256']),'controller_source')
   held.append(Held(root/'controller-source'/row['path'],maximum=MAX_CONTROLLER_SOURCE,binding={k:row[k]for k in('bytes','sha256')}))
  recomputed,rsig=receipt_contract(receipt,signature,record['controller_inputs']['controller_files'],record['controller_inputs']['normal_cache_files'],source_epoch(repo,release))
  need(recomputed==record['controller_inputs']and rsig==csig,'controller_signature')
  def check():
   for row in [*held,*evidence]:row.check()
   for snap in snapshots:snap.check()
  business,profiles=business81_outputs(root,record,check,retained=evidence,final_checks=evidence_checks)
  B=module(git_source(repo,release,BUSINESS),root/'controller-source'/BUSINESS)
  selection=document(git_source(repo,release,BUSINESS_DIR+'/f79-business-test-selection.json'))
  candidate=B.role_candidate(git_source(repo,release,ROLE_CATALOG_PATH),role)if role is not None else None
  plans=B.profiles(selection,image=record['image_id'],release_commit=release,two_git_signature=signature,candidate_product=candidate)
  joined=B.join_actual_results(selection,plans,profiles,expected_manifest_sha256=business['test_sources_sha256'])
  need(joined['tests']==81 and joined['selected_test_ids']==business['selected_test_ids'],'business81_actual')
  cache=record['controller_inputs']['normal_cache_files'];entries=controller_parcel_inputs(root,receipt,record['controller_inputs']['controller_files'],cache)
  snapshots.append(ContextSnapshot(root/'sealed/release',{r['path']:{k:r[k]for k in('bytes','sha256')}for r in receipt['release_files']}|cache))
  snapshots.append(ContextSnapshot(root/'sealed/product',{r['path']:{k:r[k]for k in('bytes','sha256')}for r in receipt['product_files']}))
  index={'schema':'normal_market_complete_ci_controller_sources/1','release_commit':release,'release_tree':receipt['release_tree'],
   'business_commit':receipt['business_commit'],'business_tree':receipt['business_tree'],'two_git_input_signature':signature,'controller_input_signature':csig,
   'files':[{'path':name,**spec['binding'],'mode':spec['mode']}for name,spec in sorted(entries.items())],
   'source_date_epoch':record['controller_inputs']['source_date_epoch'],'operational_deployment_control':False,'activation_authority':False,'security_accepted':False}
  pin=record['controller_parcel']['archive'];parcel=Held(root/'controller-sources.tar.gz',maximum=MAX_CONTEXT+4*1024**2,binding=pin);held.append(parcel)
  verify_controller_parcel(parcel,index,check)
  need(record['controller_parcel']==out['controller_parcel']and record['controller_parcel']['sources']==len(entries)
   and record['controller_parcel']['index_sha256']==sha256(encoded(index)).hexdigest(),'controller_parcel_binding')
  counts,report_pins=raw_reports(root,record['image_id'],retained=evidence,final_checks=evidence_checks)
  live_main(root,receipt['repository_identity'],release);check()
  value={'schema':'normal_market_ci_observed_evidence/1','status':'existing_evidence_observed_not_security_accepted','image_id':record['image_id'],
   'release_commit':release,'business_commit':receipt['business_commit'],'two_git_input_signature':signature,'controller_input_signature':csig,
   'historical_business_tests':81,'source_files':len(receipt['product_files']),'complete_ci_controller_payload':True,'controller_sources':len(entries),
   'complete_deployment_control_payload':False,'physical_image_archive_admission_complete':False,
   'owned_cleanup_evidence_complete':False,'same_image_security_assessment_complete':False,
   'raw_counts':counts,'raw_reports':report_pins,'raw_gate_passed':counts['HIGH']==counts['CRITICAL']==0,
   'activation_authority':False,'security_accepted':False}
  if role is not None:value.update(schema='normal_market_ci_observed_evidence/2',role=role)
 finally:
  resources=[*evidence,*held,*snapshots]
  finish_or_transfer(resources,evidence_checks,retained,final_checks)
 need(value is not None,'business81_actual');publish_known(root/'ci-evidence.json',encoded(value),[row.named for row in resources],retained=retained,final_checks=final_checks);return value
def complete_gate(root,repo,*,retained=None,final_checks=None,role=None):
 """Legacy strict raw-zero gate; factual completion cannot waive findings."""
 resources=[];checks=[];value=None
 try:
  observed=complete_evidence(root,repo,retained=resources,final_checks=checks,role=role)
  counts=observed['raw_counts'];need(counts['HIGH']==counts['CRITICAL']==0,'raw_high_critical_findings')
  value=dict(observed,schema='normal_market_ci_complete_evidence/2'if role is not None else'normal_market_ci_complete_evidence/1',status='ci_evidence_complete_not_activated')
 finally:finish_or_transfer(resources,checks,retained,final_checks)
 need(value is not None,'business81_actual')
 publish_known(root/'ci-completed.json',encoded(value),[row.named for row in resources],retained=retained,final_checks=final_checks)
 return value

def main(argv=None,*,retained=None,final_checks=None):
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--execute',action='store_true');p.add_argument('--root',type=Path);p.add_argument('--repo',type=Path);p.add_argument('--phase',choices=('build','finalize','raw-gate','complete-evidence','complete-gate'),default='build')
 p.add_argument('--role',choices=tuple(ROLE_MAP))
 args=p.parse_args(argv)
 if not args.execute:
  print(json.dumps({'schema':'normal_market_two_source_ci_proposal/1','status':'UNARMED','execution_armed':EXECUTION_ARMED,
   'build_performed':False,'activation_authority':False,'security_accepted':False}));return 0
 # No CLI flag or input document can change the reviewed source gate.
 # Hosted Main/source/ownership and strict evidence checks remain mandatory.
 need(EXECUTION_ARMED is True,'normal_main_integration_unarmed')
 need(args.root is not None and args.repo is not None,'arguments')
 value={'build':normal_build,'finalize':finalize,'raw-gate':raw_gate,'complete-evidence':complete_evidence,'complete-gate':complete_gate}[args.phase](args.root,args.repo,retained=retained,final_checks=final_checks,role=args.role)
 if args.phase=='build':
  need(type(value.get('image_id'))is str and re.fullmatch('sha256:[a-f0-9]{64}',value['image_id']),'image_identity')
  with open(os.environ['GITHUB_OUTPUT'],'a',encoding='utf-8')as output:output.write('image_id='+value['image_id']+'\n')
 print(json.dumps(value,sort_keys=True));return 0

CODES=frozenset(('archive_bound', 'archive_config', 'archive_shape', 'arguments', 'build_contract', 'build_not_completed', 'build_receipt', 'buildx_driver', 'cache_binding', 'cache_inventory', 'clean_main_required', 'command_bound', 'command_contract', 'command_failed', 'command_timeout', 'context_bound', 'context_changed', 'context_directory', 'context_inventory', 'controller_inventory', 'controller_signature', 'controller_source', 'controller_source_bound', 'disk_reserve', 'export_not_completed', 'historical81_business_producer_not_integrated', 'hosted_main_only', 'image_config', 'image_identity', 'image_labels', 'image_layers', 'json_bound', 'json_duplicate', 'json_nonfinite', 'live_main_changed', 'live_main_scope', 'normal_main_integration_unarmed', 'normal_workflow_token_required', 'output_bound', 'output_write', 'probe_cleanup', 'probe_exit', 'probe_isolation', 'probe_mounts', 'probe_owner', 'probe_resources', 'probe_scope', 'raw_high_critical_findings', 'release_commit', 'report_changed', 'report_identity', 'report_shape', 'repository_identity', 'root_scope', 'source93_probe', 'source_binding', 'source_bound', 'source_changed', 'source_contract', 'source_epoch', 'source_file', 'source_parent', 'sqlite_lock', 'sqlite_probe', 'two_git_signature', 'vendor_lock'))
CODES=CODES|frozenset(('coreutils_lock','role_component','role_catalog','role_manifest','role_receipt','role_candidate','reader_role_source_pin','reader_source_pin','reader_source_contract','probe_diagnostic','evidence_ownership','actual_profile_exit', 'actual_test_completion', 'actual_test_selection', 'baseline_pin', 'business81_actual', 'business81_assertion_approval', 'business81_assertion_approval_pin', 'business81_assertion_paths', 'business81_assertion_scope', 'business81_bundle', 'business81_cleanup', 'business81_deadline', 'business81_exit', 'business81_fixture_pin', 'business81_helper_pin', 'business81_image', 'business81_image_changed', 'business81_isolation', 'business81_mounts', 'business81_network', 'business81_output_bound', 'business81_output_finalizers', 'business81_output_write', 'business81_owner', 'business81_plans', 'business81_resources', 'business81_source', 'business81_start', 'business81_start_timeout', 'business81_two_git', 'candidate_scope', 'combined_actual_selection', 'container_plan', 'controller_parcel_binding', 'controller_parcel_bound', 'controller_parcel_deadline', 'controller_parcel_inventory', 'fixture_inventory', 'fixture_original', 'helper_inventory', 'helper_pin', 'historical_inventory', 'physical_finalizer', 'profile_identity', 'profile_result', 'profile_results', 'profile_selection', 'release_recipe_source', 'replay_original', 'replay_selection', 'security_assertion_delta', 'security_assertion_original', 'security_assertion_scope', 'selection', 'separate_security_assertion_review_required', 'source_changed', 'source_closed_changed', 'source_metadata', 'source_open', 'source_parent', 'source_pin', 'tar_lock', 'test_id_duplicate'))
def closed_failure(error):
 code=error.args[0]if type(error)is RuntimeError and len(error.args)==1 and type(error.args[0])is str and error.args[0]in CODES else'unclassified'
 kind=type(error).__name__ if type(error)in (RuntimeError,OSError,FileNotFoundError,PermissionError,ValueError,TypeError,KeyError)else'unclassified'
 return {'schema':'normal_market_two_source_ci_failure/1','status':'refused','phase':PHASE if PHASE in ('bootstrap','controller_source','git_product_seal','normal_cache_prepare','normal_cache_check','context_restamp','ordinary_build','actual_image','actual_source93','actual_source_role','actual_sqlite','actual_historical81','controller_sources_parcel','build_finalization','raw_report_binding','ordinary_image_export','ordinary_image_compression','export_finalization','complete_ci_gate')else'bootstrap',
  'code':code,'exception_class':kind,'activation_authority':False,'security_accepted':False}

if __name__=='__main__':
 try:raise SystemExit(main())
 except Exception as error:print(json.dumps(closed_failure(error),sort_keys=True));raise SystemExit(1)from None
