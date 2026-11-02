"""Fresh canonical SQLite library candidate; never a production installer.

Execution is confined to the disposable hosted CI or its pinned build image.
No historical producer, source lock, rollback artifact or application is edited.
"""
from hashlib import sha256,sha3_256
from pathlib import Path,PurePosixPath
import argparse,gzip,json,os,re,selectors,shutil,signal,stat,subprocess,sys,time,zipfile
from urllib.request import urlopen

SOURCE_URL='https://www.sqlite.org/2026/sqlite-src-3540100.zip'
SOURCE_BYTES=14899528
SOURCE_SHA3='877c6f4996ce7adb368686bce21638de9e3d91534832ad3d9d119037d1bc22ad'
SOURCE_ID='555e07ef33f69a0957d24d8e555b702464a131d6f1607f4c3c1e4551e8e6ca76'
PREFIX='sqlite-src-3540100'
LIBRARY='/usr/lib/x86_64-linux-gnu/libsqlite3.so.0.8.6'
PROFILES={
 'trixie':{'base':'python:3.11.17-slim-trixie@sha256:b95327ce1439e687237841b0210677aeadd62d55a060a5f6bcb9c15bf6251dd3',
  'os':'debian','version':'13','python':'/usr/local/bin/python3','baseline':'3.46.1-7+deb13u2'},
 'noble':{'base':'ubuntu:24.04@sha256:a2d43bffc46670d7108bb3c0468c67c5557c4c92e83e1e34cbff1322ad1b94dc',
  'os':'ubuntu','version':'24.04','python':'/usr/bin/python3.12','baseline':'3.45.1-1ubuntu2.8'},
 'resolute':{'base':'ubuntu:26.04@sha256:42527649dbc4f9197a6073f426f488cbff0e9cc9f2afce32d91c383e4875634a',
  'os':'ubuntu','version':'26.04','python':'/usr/bin/python3.14','baseline':'3.46.1-9ubuntu0.3'}}
FEATURES={'ENABLE_FTS3':'fts3','ENABLE_FTS4':'fts4','ENABLE_FTS5':'fts5','ENABLE_RTREE':'rtree',
 'ENABLE_SESSION':'session','ENABLE_COLUMN_METADATA':'column-metadata','ENABLE_DBPAGE_VTAB':'dbpage',
 'ENABLE_DBSTAT_VTAB':'dbstat','ENABLE_MATH_FUNCTIONS':'math','ENABLE_UPDATE_DELETE_LIMIT':'update-limit'}
ENV={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/build',
 'LC_ALL':'C.UTF-8','LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1','SOURCE_DATE_EPOCH':'1792159200'}

def need(value,code):
 if not value:raise RuntimeError(code)
def encoded(value):return(json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n').encode()
def pin(path):
 path=Path(path);size=path.stat().st_size;need(0<size<=32*1024**2,'public_file_bound')
 digest=sha256()
 with path.open('rb')as incoming:
  for block in iter(lambda:incoming.read(65536),b''):digest.update(block)
 return{'bytes':size,'sha256':digest.hexdigest()}
def write(path,value):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 raw=encoded(value);need(len(raw)<=4*1024**2,'receipt_bound')
 with path.open('xb')as out:out.write(raw)
 path.chmod(0o644)
def run(argv,log,*,cwd=None,seconds=300,maximum=4*1024**2,user=False,extra_env=None):
 """Stream bounded public output to one fresh log; preserve actual failures."""
 log=Path(log);log.parent.mkdir(parents=True,exist_ok=True);env={**ENV,**({'HOME':str(cwd or '/build/compat')}if user else{}),**(extra_env or{})}
 def demote():os.setgroups([]);os.setgid(10001);os.setuid(10001)
 child=subprocess.Popen(argv,cwd=cwd,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
  stderr=subprocess.STDOUT,start_new_session=True,preexec_fn=demote if user else None)
 end=time.monotonic()+seconds;count=0;complete=False
 try:
  with log.open('xb')as output,selectors.DefaultSelector()as selector:
   os.set_blocking(child.stdout.fileno(),False);selector.register(child.stdout,selectors.EVENT_READ)
   while selector.get_map():
    need(time.monotonic()<end,'command_timeout')
    for key,_ in selector.select(min(1,max(.001,end-time.monotonic()))):
     block=os.read(key.fileobj.fileno(),min(65536,maximum-count+1))
     if not block:selector.unregister(key.fileobj);continue
     count+=len(block);need(count<=maximum,'command_output_bound');output.write(block)
     sys.stdout.buffer.write(block);sys.stdout.buffer.flush()
  code=child.wait(timeout=max(.1,end-time.monotonic()));complete=True
  need(code==0,'command_failed:'+log.name)
 finally:
  if not complete and child.poll()is None:
   try:os.killpg(child.pid,signal.SIGKILL)
   except ProcessLookupError:pass
  child.wait(timeout=10);child.stdout.close()
 return log.read_bytes()if count<=65536 else None

def options(baseline):
 """Retain actual distro feature and limit macros; regenerate the parser."""
 need(type(baseline)is list and 1<=len(baseline)<=128 and len(set(baseline))==len(baseline),'baseline_options')
 macros=[]
 for value in baseline:
  need(type(value)is str and re.fullmatch('[A-Z][A-Z0-9_]*(?:=[A-Za-z0-9_.+-]+)?',value),'baseline_option')
  if value.startswith('COMPILER='):continue
  macros.append('-DSQLITE_'+value)
 configure=['--prefix=/usr','--libdir=/usr/lib/x86_64-linux-gnu','--disable-static','--enable-shared','--soname=legacy','--disable-carray',
  '--with-tclsh=/usr/bin/tclsh8.6','--with-tcl=/usr/lib/x86_64-linux-gnu/tcl8.6']
 for flag,option in FEATURES.items():
  configure.append(('--enable-'if flag in baseline else'--disable-')+option)
 need('ENABLE_UPDATE_DELETE_LIMIT'in baseline,'legacy_update_limit_missing')
 return configure,macros

def extract_source(archive,destination):
 """Canonical pinned ZIP only, no extractall or unbounded decompression."""
 need(archive.stat().st_size==SOURCE_BYTES,'source_size');digest=sha3_256()
 with archive.open('rb')as incoming:
  for raw in iter(lambda:incoming.read(65536),b''):digest.update(raw)
 need(digest.hexdigest()==SOURCE_SHA3,'source_sha3')
 destination.mkdir(mode=0o755)
 with zipfile.ZipFile(archive)as package:
  rows=package.infolist();need(0<len(rows)<=4000 and sum(v.file_size for v in rows)<=256*1024**2,'source_inventory')
  seen=set()
  for row in rows:
   path=PurePosixPath(row.filename);mode=row.external_attr>>16
   need(path.parts and path.parts[0]==PREFIX and not path.is_absolute()and '..'not in path.parts
    and '\\'not in row.filename and row.filename not in seen and not row.flag_bits&1
    and not stat.S_ISLNK(mode)and 0<=row.file_size<=32*1024**2,'source_member');seen.add(row.filename)
   relative=Path(*path.parts[1:]);target=destination/relative
   if row.is_dir():target.mkdir(parents=True,exist_ok=True);continue
   need(relative.parts and (not mode or stat.S_ISREG(mode)),'source_member_kind')
   target.parent.mkdir(parents=True,exist_ok=True);count=0
   with package.open(row)as incoming,target.open('xb')as output:
    while True:
     block=incoming.read(min(65536,row.file_size-count+1))
     if not block:break
     count+=len(block);need(count<=row.file_size,'source_member_bound');output.write(block)
   need(count==row.file_size,'source_member_short');target.chmod(0o755 if mode&0o111 else 0o644)
 need((destination/'VERSION').read_text().strip()=='3.54.1'and(destination/'manifest.uuid').read_text().strip()==SOURCE_ID,'source_identity')

def elf(path,root,label):
 dynamic=run(['/usr/bin/readelf','-d','-W',str(path)],root/(label+'-dynamic.log')).decode()
 symbols=run(['/usr/bin/nm','-D','--defined-only','--format=posix',str(path)],root/(label+'-exports.log')).decode()
 sonames=re.findall(r'\(SONAME\).*?\[([^]]+)\]',dynamic);need(sonames==['libsqlite3.so.0'],'library_soname')
 need(not re.search(r'\((?:RPATH|RUNPATH)\)',dynamic),'no_loader_override')
 return{'soname':sonames[0],'needed':sorted(re.findall(r'\(NEEDED\).*?\[([^]]+)\]',dynamic)),
  'exports':sorted({line.split()[0]for line in symbols.splitlines()if line.strip()})}

def compatible_abi(before,after):
 need(before['soname']==after['soname']=='libsqlite3.so.0'and set(before['exports'])<=set(after['exports'])
  and before['needed']==after['needed'],'backward_abi')

def vendor_source_copy(source,destination):
 """Upstream shellB writes test/probe.txt; only its disposable copy is writable."""
 shutil.copytree(source,destination)
 for path in [destination,*destination.rglob('*')]:os.chown(path,10001,10001)
 return destination

def vendor_tests(source,build_directory,out):
 try:run(['/usr/bin/make','-j2','test'],out/'vendor-standard.log',cwd=build_directory,user=True,seconds=5400,maximum=32*1024**2)
 except RuntimeError as primary:
  try:run(['/usr/bin/tclsh8.6',str(source/'test/testrunner.tcl'),'errors','--verbose'],out/'vendor-errors.log',cwd=build_directory,user=True,seconds=60,maximum=4*1024**2)
  finally:raise primary

def build(variant):
 profile=PROFILES[variant];need(os.geteuid()==0 and Path(__file__).resolve()==Path('/recipe/build.py'),'disposable_builder_only')
 release=dict(line.split('=',1)for line in Path('/etc/os-release').read_text().splitlines()if'='in line)
 need(release['ID'].strip('"')==profile['os']and release['VERSION_ID'].strip('"')==profile['version'],'variant_os')
 out=Path('/out');out.mkdir(mode=0o755);recipe=Path('/recipe');work=Path('/build');python=profile['python']
 compat=work/'compat';compat.mkdir(mode=0o700);os.chown(compat,10001,10001)
 base_debs=list((work/'old-package').glob('libsqlite3-0_*.deb'));need(len(base_debs)==1,'baseline_package')
 baseline_deb=base_debs[0];baseline_library=pin(LIBRARY);baseline_elf=elf(LIBRARY,out,'baseline')
 run([python,'-I','-B',str(recipe/'verify.py'),'baseline',str(compat),profile['baseline']],out/'baseline.log',user=True)
 baseline=json.loads((compat/'baseline.json').read_bytes());configure,macros=options(baseline['compile_options'])
 source=work/'canonical-source';extract_source(Path('/inputs/sqlite-src-3540100.zip'),source)
 common_flags=' '.join(['-O2','-fPIC','-fstack-protector-strong','-D_FORTIFY_SOURCE=2',*macros])
 # Upstream tests retain upstream's default17. The installed production
 # library has the explicitly disclosed15-digit connection initializer.
 vendor=work/'vendor';vendor.mkdir();os.chown(vendor,10001,10001)
 vendor_source=vendor_source_copy(source,work/'vendor-source')
 run([str(vendor_source/'configure'),*configure],out/'vendor-configure.log',cwd=vendor,user=True,extra_env={'CFLAGS':common_flags})
 vendor_tests(vendor_source,vendor,out)
 production=work/'production';production.mkdir();os.chown(production,10001,10001)
 flags=common_flags+' -DSQLITE_EXTRA_INIT_MUTEXED=sqlite3_trading_legacy_fp_init'
 run([str(source/'configure'),*configure],out/'production-configure.log',cwd=production,user=True,extra_env={'CFLAGS':flags})
 run(['/usr/bin/make','-j2','libsqlite3.so','EXTRA_SRC=/recipe/legacy_fp.c','LDFLAGS.rpath=',
  'LDFLAGS.configure=-Wl,--as-needed,-z,relro,-z,now'],out/'production-build.log',cwd=production,user=True,seconds=900,maximum=8*1024**2)
 library=production/'libsqlite3.so';need(library.is_file(),'production_library')
 run(['/usr/bin/strip','--strip-unneeded',str(library)],out/'strip.log')
 candidate_elf=elf(library,out,'candidate')
 compatible_abi(baseline_elf,candidate_elf)
 version='3.54.1-0+trading1~'+variant+'1';package=work/'package';(package/'DEBIAN').mkdir(parents=True)
 installed=package/LIBRARY.lstrip('/');installed.parent.mkdir(parents=True);shutil.copyfile(library,installed);installed.chmod(0o644)
 (installed.parent/'libsqlite3.so.0').symlink_to(installed.name)
 doc=package/'usr/share/doc/libsqlite3-0';doc.mkdir(parents=True)
 (doc/'copyright').write_bytes((source/'LICENSE.md').read_bytes())
 (doc/'README.trading').write_text('Canonical SQLite3.54.1. Library only; no CLI. FP_DIGITS15 through an upstream initialization hook and official connection API. Candidate only; no activation authority.\n')
 change=('sqlite3 ('+version+') '+variant+'; urgency=high\n\n  * Fresh official3.54.1 library candidate; retain legacy numeric precision.\n\n -- Trading Bot maintainers <noreply@example.invalid>  Fri, 16 Oct 2026 14:00:00 +0000\n').encode()
 (doc/'changelog.Debian.gz').write_bytes(gzip.compress(change,mtime=0))
 # Let the distro derive native runtime dependencies from the actual ELF.
 (work/'debian').mkdir();(work/'debian/control').write_text('Source: sqlite3\nSection: libs\nPriority: optional\nMaintainer: Trading Bot maintainers <noreply@example.invalid>\n\nPackage: libsqlite3-0\nArchitecture: amd64\nDescription: SQLite3 shared library security candidate\n')
 depends=run(['/usr/bin/dpkg-shlibdeps','-O','-e'+str(installed)],out/'dependencies.log',cwd=work).decode().strip()
 need(depends.startswith('shlibs:Depends=')and '\n'not in depends,'native_dependencies')
 (package/'DEBIAN/control').write_text('Package: libsqlite3-0\nSource: sqlite3\nVersion: '+version+'\nArchitecture: amd64\nMulti-Arch: same\nSection: libs\nPriority: optional\nMaintainer: Trading Bot maintainers <noreply@example.invalid>\nDepends: '+depends.split('=',1)[1]+'\nDescription: SQLite3.54.1 shared library security candidate\n')
 files=sorted(p for p in package.rglob('*')if p.is_file()and not p.is_symlink()and 'DEBIAN'not in p.relative_to(package).parts)
 from hashlib import md5
 (package/'DEBIAN/md5sums').write_text(''.join(md5(p.read_bytes()).hexdigest()+'  '+str(p.relative_to(package))+'\n'for p in files))
 (package/'DEBIAN/triggers').write_text('activate-noawait ldconfig\n')
 deb=out/('libsqlite3-0_'+version+'_amd64.deb')
 run(['/usr/bin/dpkg-deb','--root-owner-group','--build',str(package),str(deb)],out/'package-build.log')
 dpkg=['/usr/bin/dpkg','--path-include=/usr/share/doc/libsqlite3-0','--path-include=/usr/share/doc/libsqlite3-0/*','--install']
 run([*dpkg,str(deb)],out/'candidate-install.log')
 run([python,'-I','-B',str(recipe/'verify.py'),'candidate',str(compat),version],out/'candidate.log',user=True)
 candidate=json.loads((compat/'candidate.json').read_bytes());need(set(baseline['compile_options'])-{s for s in baseline['compile_options']if s.startswith('COMPILER=')}<=set(candidate['compile_options']),'feature_limit_parity')
 need(candidate['library']==pin(LIBRARY)==pin(library)and candidate['stdlib_extension']==baseline['stdlib_extension'],'installed_python_binding')
 run(['/usr/bin/gcc','-O2','-pthread','-I'+str(production),str(recipe/'lifecycle.c'),'-Wl,-l:libsqlite3.so.0','-o',str(work/'lifecycle')],out/'lifecycle-build.log')
 run([str(work/'lifecycle')],out/'lifecycle.log',user=True,seconds=60)
 # Fresh process checks must reproduce15 digits without Python-side hooks.
 for number in range(3):
  run([python,'-I','-B',str(recipe/'verify.py'),'fresh',str(compat),version],out/('fresh-'+str(number)+'.log'),user=True)
 run([*dpkg,str(baseline_deb)],out/'old-reinstall.log')
 run([python,'-I','-B',str(recipe/'verify.py'),'old-reopen',str(compat),profile['baseline']],out/'old-reopen.log',user=True)
 run([*dpkg,str(deb)],out/'candidate-reinstall.log')
 run([python,'-I','-B',str(recipe/'verify.py'),'candidate-reopen',str(compat),version],out/'candidate-reopen.log',user=True)
 run(['/usr/bin/dpkg','--verify','libsqlite3-0'],out/'dpkg-verify.log')
 need((out/'dpkg-verify.log').stat().st_size==0,'package_payload_changed')
 for name in('baseline.json','candidate.json','old-reopen.json','candidate-reopen.json'):shutil.copyfile(compat/name,out/name)
 provenance={'schema':'sqlite3541_library_candidate/1','status':'BUILT_NOT_ACCEPTED','variant':variant,'base_image':profile['base'],
  'source':{'url':SOURCE_URL,'bytes':SOURCE_BYTES,'sha3_256':SOURCE_SHA3,'source_id':SOURCE_ID},'configure_argv':configure,
  'production_cflags':flags,'upstream_core_source_modified':False,'legacy_numeric_initializer':pin(recipe/'legacy_fp.c'),
  'vendor_default17_tests':{'argv':['make','-j2','test'],'actual_returncode':0,'raw_log':pin(out/'vendor-standard.log'),
   'same_library_as_production15':False,'vendor_assertions_modified':False},
  'production15_targeted_compatibility':True,'baseline_package':{'version':profile['baseline'],**pin(baseline_deb)},
  'baseline_library':baseline_library,'candidate_library':pin(LIBRARY),'baseline_abi':baseline_elf,'candidate_abi':candidate_elf,
  'old_required_exports_retained':True,'added_exports':sorted(set(candidate_elf['exports'])-set(baseline_elf['exports'])),
  'package':{'name':deb.name,'version':version,**pin(deb)},'payload_regular_files':[{**pin(p),'path':str(p.relative_to(package))}for p in files],
  'CLI_in_runtime_package':False,'old_new_database_reopen_passed':True,'thread_initialize_shutdown_passed':True,
  'production_data_accessed':False,'business81_or_App32_run':False,'host_installed':False,'security_accepted':False}
 write(out/'provenance.json',provenance)

def stage(variant,root):
 need(os.environ.get('GITHUB_ACTIONS')=='true'and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted'
  and os.environ.get('GITHUB_REPOSITORY')=='mohsenbarari/trading_bot'and os.environ.get('GITHUB_REF')=='refs/heads/main','hosted_CI_only')
 root=root.absolute();temporary=Path(os.environ['RUNNER_TEMP']).resolve();need(root.is_relative_to(temporary)and root!=temporary,'fresh_CI_root')
 root.mkdir(mode=0o700);context=root/'context';recipe=context/'deploy/security/sqlite3541';recipe.mkdir(parents=True);(context/'inputs').mkdir()
 original=Path(__file__).resolve().parent
 for name in('build.py','verify.py','legacy_fp.c','lifecycle.c','bootstrap.sh','Dockerfile','snapshot-public-isrg-root-x1.crt'):
  shutil.copyfile(original/name,recipe/name);(recipe/name).chmod(0o644)
 archive=context/'inputs/sqlite-src-3540100.zip';digest=sha3_256();count=0;end=time.monotonic()+120
 with urlopen(SOURCE_URL,timeout=60)as incoming,archive.open('xb')as output:
  while True:
   block=incoming.read(min(65536,SOURCE_BYTES-count+1))
   if not block:break
   need(time.monotonic()<end,'source_download_timeout')
   count+=len(block);need(count<=SOURCE_BYTES,'source_download_bound');digest.update(block);output.write(block)
 need(count==SOURCE_BYTES and digest.hexdigest()==SOURCE_SHA3,'source_download_identity')
 write(root/'source-identity.json',{'commit':os.environ.get('GITHUB_SHA'),'run_id':os.environ.get('GITHUB_RUN_ID'),
  'run_attempt':os.environ.get('GITHUB_RUN_ATTEMPT'),'variant':variant,'source_sha3':SOURCE_SHA3,
  'recipe_files':{p.name:pin(p)for p in recipe.iterdir()},'security_accepted':False})
 run(['/usr/bin/docker','build','--progress=plain','--platform','linux/amd64','--build-arg','BASE_IMAGE='+PROFILES[variant]['base'],
  '--build-arg','SQLITE_VARIANT='+variant,'--target','package-export','--output','type=local,dest='+str(root/'package-output'),
  '--file',str(recipe/'Dockerfile'),str(context)],root/'build.log',seconds=7800,maximum=64*1024**2,extra_env={'HOME':str(root),'DOCKER_BUILDKIT':'1'})
 need((root/'package-output/provenance.json').is_file(),'package_export_missing')

def main():
 parser=argparse.ArgumentParser();group=parser.add_mutually_exclusive_group(required=True)
 group.add_argument('--stage',choices=PROFILES);group.add_argument('--build',choices=PROFILES);parser.add_argument('--root',type=Path)
 args=parser.parse_args()
 if args.stage:need(args.root is not None,'root_required');stage(args.stage,args.root)
 else:need(args.root is None,'unexpected_root');build(args.build)
if __name__=='__main__':main()
