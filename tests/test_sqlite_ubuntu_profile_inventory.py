"""Real public Git/context geometry; no vendor binary, SQL, compiler or Docker.

The four archive inputs are explicitly synthetic unsigned public bytes with a
matching DSC-format seam. This validates transport/pin/role wiring, never vendor
authentication. Production archive/signature/platform pins remain unchanged.
"""
from contextlib import ExitStack,contextmanager
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import ModuleType,SimpleNamespace
from unittest.mock import patch
import ast,errno,json,os,subprocess,tempfile,unittest

ROOT=Path(__file__).resolve().parent.parent
BASE=Path('deploy/security/sqlite-ubuntu-gold2')
SOURCE_HOLDS=[]
GIT_CHILDREN=[]
GIT_PROCESS_FINALS=[]
def load(relative,name):
 p=ROOT/relative;m=ModuleType(name);m.__file__=str(p)
 exec(compile(p.read_bytes(),str(p),'exec'),m.__dict__);return m
def pin(b):return(len(b),sha256(b).hexdigest())
def write(p,b):
 p.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
 with p.open('xb')as f:f.write(b)
 p.chmod(0o600)

class UbuntuProfileInventoryTests(unittest.TestCase):
 def setUp(self):
  self.stack=ExitStack();self.addCleanup(self.stack.close)
  self.tmp=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.owned-ubuntu-inventory-',dir=ROOT/'tests')))
  self.home=self.tmp/'home';self.home.mkdir(mode=0o700)
  self.env={'PATH':'/usr/bin:/bin','HOME':str(self.home),'XDG_CONFIG_HOME':str(self.home),
   'LC_ALL':'C.UTF-8','LANG':'C.UTF-8','GIT_CONFIG_NOSYSTEM':'1',
   'GIT_CONFIG_GLOBAL':'/dev/null','GIT_TERMINAL_PROMPT':'0',
   'GIT_OPTIONAL_LOCKS':'0','GIT_CONFIG_COUNT':'0'}
  self.stack.enter_context(patch.dict(os.environ,self.env,clear=True))
  self.index=0
 def new(self,variant,*,manifest_edit=None,ignore_edit=None):
  self.index+=1;work=self.tmp/(variant+'-'+str(self.index));work.mkdir(mode=0o700)
  A=load(Path('scripts/prepare_sqlite_ubuntu_gold2_ci_context.py'),'owned_profile_caller')
  R=load(BASE/variant/'sourcebuild.py','owned_'+variant+'_recipe');A.select_variant(variant)
  R.ENV=dict(self.env);repo=work/'repo';repo.mkdir(mode=0o700)
  actual_bounded=R.bounded_command;started=[]
  def spawn(argv,**kwargs):
   self.assertEqual(argv[:3],['/usr/bin/git','-C',str(repo)]);self.assertTrue(kwargs['close_fds']and kwargs['start_new_session'])
   self.assertFalse(any(k in kwargs for k in('user','group','extra_groups')))
   child=subprocess.Popen(argv,**kwargs);started.append((child,child.stdout.fileno()));return child
  R.subprocess=SimpleNamespace(Popen=spawn,DEVNULL=subprocess.DEVNULL,PIPE=subprocess.PIPE,STDOUT=subprocess.STDOUT)
  def bounded(*args,**kwargs):
   cut=len(started)
   try:return actual_bounded(*args,**kwargs)
   finally:
    for child,fd in started[cut:]:
     self.assertIsNotNone(child.returncode);self.assertTrue(child.stdout.closed)
     with self.assertRaises(OSError)as closed:os.fstat(fd)
     self.assertEqual(closed.exception.errno,errno.EBADF)
     GIT_PROCESS_FINALS.append({'actual_returncode':child.returncode,'original_stdout_closed_EBADF':True,'reaped':True})
  R.bounded_command=bounded
  for name in A.PINS:write(repo/BASE/name,(ROOT/BASE/name).read_bytes())
  # Real named archive/content functions consume these declared UNIT bytes.
  actual_sources=dict(R.SOURCE_FILES)
  payload={name:('OWNED UNSIGNED PUBLIC ARCHIVE SEAM '+name+'\n').encode()
           for name in actual_sources if not name.endswith('.dsc')}
  dsc=next(n for n in actual_sources if n.endswith('.dsc'))
  payload[dsc]=('Format: 3.0 (quilt)\nVersion: '+R.BASE_VERSION+'\nChecksums-Sha256:\n'+
   ''.join(' '+sha256(b).hexdigest()+' '+str(len(b))+' '+n+'\n'for n,b in payload.items())).encode()
  R.SOURCE_FILES={name:pin(payload[name])for name in actual_sources}
  manifest_path=repo/BASE/variant/'context-manifest.json'
  manifest=json.loads(manifest_path.read_bytes())
  for name,b in payload.items():manifest['files']['inputs/'+name]={'bytes':len(b),'sha256':sha256(b).hexdigest()}
  if manifest_edit:manifest_edit(manifest)
  manifest_path.write_bytes(A.encoded(manifest));A.PINS[variant+'/context-manifest.json']=pin(manifest_path.read_bytes())
  if ignore_edit:
   ip=repo/BASE/variant/'.dockerignore';ip.write_bytes(ignore_edit(ip.read_bytes()));A.PINS[variant+'/.dockerignore']=pin(ip.read_bytes())
  def fetch(url,target,expected):
   self.assertTrue(url.startswith(R.SNAPSHOT+'/pool/main/s/sqlite3/'))
   name=Path(target).name;self.assertEqual(pin(payload[name]),expected)
   write(Path(target),payload[name])
  R.fetch=fetch
  def git(*args):
   self.assertIn(args[0],('init','add','commit','rev-parse'))
   argv=['/usr/bin/git','-C',str(repo),*args]
   raw=R.bounded_command(argv,cwd=repo,timeout=10,maximum=65536,capture=True)
   GIT_CHILDREN.append({'argv_kind':args[0],'actual_exit0_EOF_closed':True});return raw
  git('init','-q','--initial-branch=owned-public-fixture')
  git('add','--all','--',str(BASE))
  # Local-only inline identity; no config/global/credential/remote/hooks access.
  commit_argv=['/usr/bin/git','-C',str(repo),'-c','user.name=Public Source Fixture',
   '-c','user.email=public-source-fixture@example.invalid','-c','commit.gpgsign=false',
   'commit','--no-gpg-sign','-qm','Owned public source geometry']
  R.bounded_command(commit_argv,cwd=repo,timeout=10,maximum=65536,capture=True)
  GIT_CHILDREN.append({'argv_kind':'commit','actual_exit0_EOF_closed':True})
  commit=git('rev-parse','HEAD').decode().strip();self.assertRegex(commit,r'^[a-f0-9]{40}$')
  return SimpleNamespace(A=A,R=R,work=work,repo=repo,commit=commit,payload=payload,
   actual_sources=actual_sources,commit_argv=commit_argv)
 @contextmanager
 def seal(self,f):
  root=f.work/('context-run-'+str(len(SOURCE_HOLDS)));root.mkdir(mode=0o700)
  frames=[];checks=[];f.A.FINALIZED.clear()
  try:
   with ExitStack()as loan:
    blobs=f.A.git_blobs(f.R,f.repo,f.commit)
    context,bindings=f.A.seal_context(f.R,root,blobs,loan,frames,0,checks)
    for check in checks:check()
    f.A.context_inventory(context,f.R)
    yield context,bindings
  finally:
   f.A.named(frames)
   self.assertEqual(len(f.A.FINALIZED),len(frames))
   SOURCE_HOLDS.append({'variant':f.A.VARIANT,'originals_full9_flags_offset_parent_EBADF_closed':len(frames)})
 def platform(self,f,context):
  R=f.R;platform_dir=f.work/'platform';platform_dir.mkdir(mode=0o700,exist_ok=True)
  release=platform_dir/'public-os-release'
  if not release.exists():write(release,('ID=ubuntu\nVERSION_ID="'+R.VERSION_ID+'"\nVERSION_CODENAME='+R.CODENAME+'\n').encode())
  python=platform_dir/'public-python-identity'
  if not python.exists():write(python,b'PUBLIC PATH IDENTITY ONLY; NOT AN EXECUTABLE\n')
  class PlatformPath:
   def __new__(cls,value):
    if str(value)=='/build':return platform_dir
    if str(value)=='/etc/os-release':return release
    if str(value)==R.PYTHON:return python
    return Path(value)
   @classmethod
   def cwd(cls):return platform_dir
  # Only platform identity is a seam. All manifest/file reads and hashes are real.
  with patch.object(R,'RECIPE',context),patch.object(R,'INPUTS',context/'inputs'),patch.object(R,'Path',PlatformPath),patch.object(R,'sys',SimpleNamespace(version_info=R.PYTHON_VERSION,executable=str(python),base_prefix='/usr')):
   R.builder_context()
 def test_real_git_to_sealed_context_platform_and_second_stage_both_profiles(self):
  for variant,count in(('noble',13),('resolute',12)):
   with self.subTest(variant=variant):
    f=self.new(variant)
    with self.seal(f)as(context,bindings):
     self.assertEqual(len(bindings),count);self.platform(f,context)
     self.assertEqual(f.A.source_inventory_counts(),{'tracked_small_files':23,'selected_small_files':count-2,
      'context_payload_files':count,'context_control_files':2,'context_regular_files':count+2})
     destination=f.work/'second-stage';old_cwd=Path.cwd()
     try:
      os.chdir(context)
      with patch.object(f.R,'__file__',str(context/'sourcebuild.py')):
       result=f.R.stage_context(destination)
     finally:os.chdir(old_cwd)
     self.assertEqual(result['files'],bindings);self.assertFalse(result['build_performed'])
     f.A.context_inventory(destination,f.R);self.platform(f,destination)
     self.assertEqual((destination/'.dockerignore').read_bytes(),(context/'.dockerignore').read_bytes())
     self.assertEqual((destination/'context-manifest.json').read_bytes(),(context/'context-manifest.json').read_bytes())
 def test_actual_pins_manifests_runner_ignore_and_docker_COPY_join_both_profiles(self):
  A=load(Path('scripts/prepare_sqlite_ubuntu_gold2_ci_context.py'),'actual_pin_caller')
  runner=load(BASE/'runner/build_package.py','actual_public_runner')
  self.assertEqual(len(A.PINS),23)
  for variant in('noble','resolute'):
   A.select_variant(variant);R=load(BASE/variant/'sourcebuild.py','actual_'+variant)
   counts=A.source_inventory_counts();manifest=json.loads((ROOT/BASE/variant/'context-manifest.json').read_bytes())['files']
   self.assertEqual(len(manifest),counts['context_payload_files'])
   expected={name:{'bytes':pin[0],'sha256':pin[1]}for name,pin in runner.VARIANTS[variant]['pins'].items()}
   self.assertEqual(manifest,expected)
   for name,pinvalue in A.PINS.items():self.assertEqual(pin((ROOT/BASE/name).read_bytes()),pinvalue)
   for name,pinvalue in R.SOURCE_FILES.items():self.assertEqual(manifest['inputs/'+name],{'bytes':pinvalue[0],'sha256':pinvalue[1]})
   allowed='**\n!.dockerignore\n!context-manifest.json\n'+''.join('!'+n+'\n'for n in A.CONTEXT_PAYLOADS[variant])+'!inputs/\n'+''.join('!inputs/'+n+'\n'for n in R.SOURCE_FILES)
   self.assertEqual((ROOT/BASE/variant/'.dockerignore').read_bytes(),allowed.encode())
   copy=next(line for line in(ROOT/BASE/variant/'Dockerfile').read_text().splitlines()if line.startswith('COPY sourcebuild.py '))
   self.assertEqual(set(copy.split()[1:-1]),set(A.CONTEXT_PAYLOADS[variant])|{'context-manifest.json'})
 def test_exact_pin_names_refuse_missing_extra_and_same_count_substitution(self):
  A=load(Path('scripts/prepare_sqlite_ubuntu_gold2_ci_context.py'),'reject_pin_names');A.select_variant('noble');original=deepcopy(A.PINS)
  for kind in('missing','extra','same_count'):
   with self.subTest(kind=kind):
    A.PINS=deepcopy(original)
    if kind!='extra':del A.PINS['noble/0004-rowid-in-view-nested-join-core.patch']
    if kind!='missing':A.PINS['noble/unknown.patch']=(1,'0'*64)
    with self.assertRaisesRegex(RuntimeError,'tracked_source_inventory'):A.git_blobs(SimpleNamespace(),Path('/OWNED-NOT-ACCESSED'),'0'*40)
 def test_real_Git_committed_missing_and_same_size_tampered_members_refuse(self):
  for variant in('noble','resolute'):
   for kind in('missing','tampered'):
    with self.subTest(variant=variant,kind=kind):
     f=self.new(variant);name=next(iter(f.A.PINS));path=f.repo/BASE/name;raw=path.read_bytes()
     if kind=='missing':path.unlink()
     else:path.write_bytes(bytes([raw[0]^1])+raw[1:])
     f.R.bounded_command(['/usr/bin/git','-C',str(f.repo),'add','--all','--',str(BASE)],cwd=f.repo,timeout=10,maximum=65536,capture=True)
     f.R.bounded_command(f.commit_argv,cwd=f.repo,timeout=10,maximum=65536,capture=True)
     commit=f.R.metadata(['/usr/bin/git','-C',str(f.repo),'rev-parse','HEAD'],maximum=64).decode().strip()
     with self.assertRaises(RuntimeError):f.A.git_blobs(f.R,f.repo,commit)
 def test_actual_directory_inventory_rejects_unknown_missing_and_foreign_core(self):
  for variant in('noble','resolute'):
   f=self.new(variant)
   with self.seal(f)as(context,_):pass
   with self.subTest(variant=variant):
    extra=context/'UNDECLARED';write(extra,b'PUBLIC EXTRA\n')
    with self.assertRaises(RuntimeError):f.A.context_inventory(context,f.R)
    extra.unlink();known=context/'verify_positive.py';raw=known.read_bytes();known.unlink()
    with self.assertRaises(RuntimeError):f.A.context_inventory(context,f.R)
    write(known,raw)
    if variant=='resolute':
     foreign=context/'0004-rowid-in-view-nested-join-core.patch';write(foreign,b'NOT ALLOWED ON RESOLUTE\n')
     with self.assertRaises(RuntimeError):f.A.context_inventory(context,f.R)
 def test_real_builder_manifest_rejects_extra_missing_and_tampered_bytes(self):
  for variant in('noble','resolute'):
   f=self.new(variant)
   with self.seal(f)as(context,_):pass
   manifest=context/'context-manifest.json';raw=manifest.read_bytes();parsed=json.loads(raw)
   for kind in('extra','missing'):
    value=deepcopy(parsed)
    if kind=='extra':value['files']['UNDECLARED']={'bytes':1,'sha256':'0'*64}
    else:del value['files']['verify_positive.py']
    manifest.write_bytes(f.A.encoded(value))
    with self.subTest(variant=variant,kind=kind),self.assertRaisesRegex(RuntimeError,'build_context'):self.platform(f,context)
   manifest.write_bytes(raw);target=context/'verify_positive.py';body=target.read_bytes();target.write_bytes(bytes([body[0]^1])+body[1:])
   with self.subTest(variant=variant,kind='tampered'),self.assertRaisesRegex(RuntimeError,'input_pin'):self.platform(f,context)
 def test_real_sealer_rejects_stale_manifest_counts_and_omitted_ignore_member(self):
  for variant in('noble','resolute'):
   member='0004-rowid-in-view-nested-join-core.patch'if variant=='noble'else'0001-wal-reset-production-only.patch'
   f=self.new(variant,manifest_edit=lambda d:d['files'].pop(member))
   with self.subTest(variant=variant,kind='stale_manifest'),self.assertRaisesRegex(RuntimeError,'frozen_context_manifest'):
    with self.seal(f):pass
   f=self.new(variant,ignore_edit=lambda b:b.replace(('!'+member+'\n').encode(),b''))
   with self.subTest(variant=variant,kind='stale_ignore'),self.assertRaisesRegex(RuntimeError,'frozen_denyall_ignore'):
    with self.seal(f):pass
 def test_Noble_stage_requires_exact_core_bytes_and_never_drops_it(self):
  f=self.new('noble')
  with self.seal(f)as(context,_):pass
  core=context/f.R.ROWID_NESTED_JOIN['name'];raw=core.read_bytes();core.write_bytes(bytes([raw[0]^1])+raw[1:])
  old=Path.cwd()
  try:
   os.chdir(context)
   with patch.object(f.R,'__file__',str(context/'sourcebuild.py')):
    with self.assertRaisesRegex(RuntimeError,'input_pin'):f.R.stage_context(f.work/'must-not-stage')
  finally:os.chdir(old)
  self.assertFalse((f.work/'must-not-stage').exists())

if __name__=='__main__':unittest.main()
