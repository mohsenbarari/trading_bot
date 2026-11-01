"""Two-profile public package-only hosted CI. Default/import perform no operations.
No host install; all SQL/compilation stays in disposable authenticated Ubuntu stages.
"""
from contextlib import ExitStack,redirect_stderr
from hashlib import sha256
from pathlib import Path
from types import ModuleType
import argparse,errno,fcntl,json,os,re,signal,stat,sys,threading,time

TRACKED=Path('deploy/security/sqlite-ubuntu-gold2')
PINS={'noble/sourcebuild.py': (87281, '5df3c0efd95b73c6d65eee0ca89804efde313dad34af95d3caa5527d51a979a7'), 'noble/verify_positive.py': (16832, '0c2027475c4445244eb546965e6205cc1c0bd98f7834d4bf60890015cf71a489'), 'noble/Dockerfile': (4177, '3043465b46c5b81ebf260bcbc4e854d55b752a86c0a7e38429b7d0bd2e6147f0'), 'noble/.dockerignore': (471, '73312f546dcddd8f4d2afb00c0aa8050e13ad6c9b72ad65173300988abdd4ebf'), 'noble/context-manifest.json': (1677, 'ef3b68cdd977f9bfc5eeda74767e46b8f6bd4ad950d7ed1743a5c89c6156df51'), 'noble/0002-fts5-detail-none-merge-bound.patch': (1252, 'bc1403e417a5673ad51712c36f3b9b33f87b77a6f6f06d7bedba02b46d098231'), 'noble/0003-fts5-detail-none-upstream-regression.patch': (931, '81edf417c615f7a76fe9506c74e4a05a5ee1a135bce805de7ae8ac02c3dbfaed'), 'resolute/sourcebuild.py': (84372, '7e14dbc6beaaf4d094d9ac6c9b8fd6fd1fda21c414a370694ab2795d29de808f'), 'resolute/verify_positive.py': (16835, '809d13becc325d5711ceeb5557c809a1a545dfec77ff0bba8a0bdd1651b035c4'), 'resolute/Dockerfile': (4367, '85920342ff6922b3236c06791650633127286deb3bce279a32fb6bf26245bb66'), 'resolute/.dockerignore': (428, 'ee05a695af0be06e87dc330a7438a1ec4daf90f0f6e9250f3a311acdccae2950'), 'resolute/context-manifest.json': (1542, '19d4cc4e9be3f7f01ae46b04913a7000b0276403bfbe6e27418d6d7763472d77'), 'resolute/0002-fts5-detail-none-merge-bound.patch': (1252, 'bc1403e417a5673ad51712c36f3b9b33f87b77a6f6f06d7bedba02b46d098231'), 'resolute/0003-fts5-detail-none-upstream-regression.patch': (931, '81edf417c615f7a76fe9506c74e4a05a5ee1a135bce805de7ae8ac02c3dbfaed'), 'runner/build_package.py': (24788, 'f4af92752cab1cc34fd10c1b78a00ada10cabd05dd0a82fff970ba92b4279a50'), 'runner/manifest.json': (4074, '66e8fb9401b36bd64a80021a41b6dd1ed5899ca9f1f0c92a75139e733704b2ad'), 'noble/0001-wal-reset-production-only.patch': (6746, 'ab1d8c75b8ed521cc5d1d0c64da66be26ca3b72d53326c28b6a86473f42ddc85'), 'noble/source-signer.asc': (14582, '91b93e45fb77b7098882dc1e2035951b7c351b9d3f754990642be80191030a73'), 'noble/consumer-profile.json': (662, 'bc10b4fe4ccc1a668ce1c55a00daf8ed0f129b9298ebaf9f0487a2a14c0b4af3'), 'resolute/0001-wal-reset-production-only.patch': (6746, 'ab1d8c75b8ed521cc5d1d0c64da66be26ca3b72d53326c28b6a86473f42ddc85'), 'resolute/source-signer.asc': (14582, '91b93e45fb77b7098882dc1e2035951b7c351b9d3f754990642be80191030a73'), 'resolute/consumer-profile.json': (665, '737e920c3421f62f90dbd814d13488778580760b401e060d1a3ec479867cd8d6'), 'noble/0004-rowid-in-view-nested-join-core.patch': (8073, 'fc7990f7375b8e8d63abc04a5c2fd66a2a6ed880e53342d37a370526e11ff018')}
PROFILES={'noble': {'base': '3.45.1-1ubuntu2.8', 'version': '3.45.1-1ubuntu2.8+gold2', 'upstream': '3.45.1', 'python': '3.12.3', 'python_path': '/usr/bin/python3.12', 'os_version': '24.04', 'library': '/usr/lib/x86_64-linux-gnu/libsqlite3.so.0.8.6', 'extension': '/usr/lib/python3.12/lib-dynload/_sqlite3.cpython-312-x86_64-linux-gnu.so'}, 'resolute': {'base': '3.46.1-9ubuntu0.3', 'version': '3.46.1-9ubuntu0.3+gold2', 'upstream': '3.46.1', 'python': '3.14.4', 'python_path': '/usr/bin/python3.14', 'os_version': '26.04', 'library': '/usr/lib/x86_64-linux-gnu/libsqlite3.so.0.8.6', 'extension': '/usr/lib/python3.14/lib-dynload/_sqlite3.cpython-314-x86_64-linux-gnu.so'}}
VARIANT=None;SPEC=None;BASE=None;VERSION=None;PACKAGE=None;CHANGES=None;BUILDINFO=None;OUTPUTS={}
PATH='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
DISK_RESERVE=2*1024**3;DISK_GROWTH=8*1024**3
GLOBAL_SECONDS=9120;BUILD_SECONDS=8400;FINALIZE_SECONDS=120;LOG_MAX=64*1024**2
FROZEN_NATIVE_LIFETIME_SECONDS=9000;WORKFLOW_JOB_SECONDS=10800

def need(value,code):
 if not value:raise RuntimeError(code)


def encoded(value):return (json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()


def document(raw):
 need(type(raw)is bytes and 0<len(raw)<=4*1024**2,'metadata_bound')
 def unique(rows):
  value={}
  for k,v in rows:need(k not in value,'duplicate_key');value[k]=v
  return value
 return json.loads(raw,object_pairs_hook=unique,parse_constant=lambda _:need(False,'nonfinite_json'))


def identity(s):return (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)


def parents(path,uid):
 rows=[]
 for p in path.parents:
  row=identity(p.lstat());need(p.resolve(strict=True)==p and stat.S_ISDIR(row[2]) and row[3]in{0,uid} and not row[2]&0o022,'parent_identity');rows.append((p,row[:5]))
 return rows


def named(frames):
 for path,before,chain in frames:
  need(path.resolve(strict=True)==path and identity(path.lstat())==before,'named_source_changed')
  need(all(p.resolve(strict=True)==p and identity(p.lstat())[:5]==old for p,old in chain),'parent_changed')


def publish(path,raw,uid):
 need(type(raw)is bytes and 0<len(raw)<=32*1024**2,'write_bound');chain=parents(path,uid)
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
 try:
  view=memoryview(raw)
  while view:
   n=os.write(fd,view);need(n>0,'short_write');view=view[n:]
  os.fsync(fd)
 finally:os.close(fd)
 need(all(p.resolve(strict=True)==p and identity(p.lstat())[:5]==old for p,old in chain),'parent_changed')
 fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
 try:os.fsync(fd)
 finally:os.close(fd)


def validate_host(value):
 need(type(value)is dict and set(value)=={'uid','euid','runner_environment','machine','os_id','os_version','cpus','affinity_cpus','mem_total_bytes','free_bytes','disk_bytes'},'host_metadata_fields')
 need(value['uid']==1001 and value['euid']==1001 and value['runner_environment']=='github-hosted','hosted_uid_required')
 need(value['machine']=='x86_64' and value['os_id']=='ubuntu' and value['os_version']=='24.04','hosted_platform_required')
 need(type(value['cpus'])is int and 2<=value['cpus']<=256 and type(value['affinity_cpus'])is int and 2<=value['affinity_cpus']<=value['cpus'],'hosted_cpu_capacity')
 need(type(value['mem_total_bytes'])is int and 4*1024**3<=value['mem_total_bytes']<=1024**4,'hosted_memory_capacity')
 need(type(value['free_bytes'])is int and value['free_bytes']>=DISK_RESERVE+DISK_GROWTH,'hosted_disk_capacity')
 return value


def host_facts(temp):
 # These public VM metadata files are bounded; no environment values are dumped.
 def small(path,maximum):
  with open(path,'rb')as f:raw=f.read(maximum+1)
  need(len(raw)<=maximum,'host_metadata_bound');return raw.decode('ascii')
 release=dict(row.split('=',1)for row in small('/etc/os-release',65536).splitlines()if '='in row)
 memory=dict(row.split(':',1)for row in small('/proc/meminfo',65536).splitlines()if ':'in row)
 total=memory.get('MemTotal','').split();need(len(total)==2 and total[0].isdigit()and total[1]=='kB','host_memory_metadata')
 fs=os.statvfs(temp)
 return validate_host({'uid':os.getuid(),'euid':os.geteuid(),'runner_environment':os.environ.get('RUNNER_ENVIRONMENT'),
  'machine':os.uname().machine,'os_id':release.get('ID','').strip('"'),'os_version':release.get('VERSION_ID','').strip('"'),
  'cpus':os.cpu_count(),'affinity_cpus':len(os.sched_getaffinity(0)),'mem_total_bytes':int(total[0])*1024,
  'free_bytes':fs.f_bavail*fs.f_frsize,'disk_bytes':fs.f_blocks*fs.f_frsize})


def binding(value):
 need(type(value)is dict and set(value)=={'bytes','sha256'}and type(value['bytes'])is int and 0<value['bytes']<=64*1024**2
  and type(value['sha256'])is str and re.fullmatch('[a-f0-9]{64}',value['sha256']),'artifact_binding');return value


def debian_checksums(raw):
 need(type(raw)is bytes and 0<len(raw)<=4*1024**2,'debian_metadata');rows={};inside=False;found=False
 for line in raw.splitlines():
  need(len(line)<=4096,'debian_metadata_line')
  if line==b'Checksums-Sha256:':need(not found,'duplicate_checksum_section');found=inside=True;continue
  if not inside:continue
  if not line.startswith(b' '):inside=False;continue
  fields=line.split();need(len(fields)==3 and re.fullmatch(b'[a-f0-9]{64}',fields[0])and re.fullmatch(b'[0-9]{1,10}',fields[1])and re.fullmatch(b'[A-Za-z0-9.+_:-]{1,255}',fields[2]),'debian_checksum_row')
  leaf=fields[2].decode('ascii');need(leaf not in rows and len(rows)<256,'debian_checksum_duplicate');rows[leaf]={'bytes':int(fields[1]),'sha256':fields[0].decode('ascii')}
 need(found and rows,'debian_metadata');return rows


def build_argv(context,output):
 return ['/usr/bin/docker','buildx','build','--builder','default','--no-cache','--pull','--target','package-export','--progress','plain',
  '--output','type=local,dest='+str(output),'--file',str(context/'Dockerfile'),str(context)]


def default_driver(R):
 # buildx ls formats one builder row and its node rows. .Builder is the
 # documented builder object on both; unrelated builders emit empty lines.
 raw=R.metadata(['/usr/bin/docker','buildx','ls','--format','{{if eq .Builder.Name "default"}}{{json .Builder.Driver}}{{end}}'],maximum=1024)
 need(type(raw)is bytes and 0<len(raw)<=1024,'dedicated_default_driver')
 rows=[line.strip()for line in raw.splitlines()if line.strip()]
 need(1<=len(rows)<=16,'dedicated_default_driver')
 try:drivers=[document(line)for line in rows]
 except (ValueError,UnicodeError):raise RuntimeError('dedicated_default_driver')from None
 need(all(type(value)is str and value=='docker'for value in drivers),'dedicated_default_driver')
 return 'docker'


def check_disk(initial,current):
 need(type(initial)is int and type(current)is int and current>=DISK_RESERVE and initial-current<=DISK_GROWTH,'vm_disk_budget')


def disk_scope(path,uid,*,initial=None):
 path=Path(path);need(path.is_absolute()and path.resolve(strict=True)==path,'disk_path')
 row=identity(path.lstat());need(stat.S_ISDIR(row[2])and row[3]in{0,uid}and not row[2]&0o022,'disk_root_identity')
 fs=os.statvfs(path);free=fs.f_bavail*fs.f_frsize;start=free if initial is None else initial
 need(type(start)is int and start>=DISK_RESERVE+DISK_GROWTH,'disk_preflight_capacity');check_disk(start,free)
 return {'path':str(path),'identity':list(row[:5]),'filesystem_id':fs.f_fsid,'initial_free_bytes':start,
  'disk_bytes':fs.f_blocks*fs.f_frsize}


def check_disk_scope(scope):
 path=Path(scope['path']);need(path.resolve(strict=True)==path and list(identity(path.lstat())[:5])==scope['identity'],'disk_root_changed')
 fs=os.statvfs(path);need(fs.f_fsid==scope['filesystem_id'],'disk_filesystem_changed');check_disk(scope['initial_free_bytes'],fs.f_bavail*fs.f_frsize)


PROVENANCE_FIELDS={'upstream_fault_suites_run', 'candidate_library', 'baseline_authenticated_reinstall_required', 'package_doc_path_includes', 'schema', 'full_upstream_suite_pass_claimed', 'base_package_version', 'root_observed_consumer_profile', 'production_patch', 'source_files', 'native_bundle_replaces_bootstrap_after_authenticated_install', 'debian_rules', 'package_file', 'native_ca_certificates_package', 'vendor_tests', 'elf_soname_exports_dependencies_equal', 'public_ca_bootstrap', 'package', 'wal_after', 'baseline_library', 'fts5_before', 'python_binary_unchanged', 'runtime_loader_override_used', 'ubuntu_control', 'repository_releases', 'source_maintainer_signature_verified', 'local_package_version', 'wal_before', 'source_date_epoch', 'app_ci_restore_gate_required', 'wal_reset_reproduction_performed', 'architecture', 'production_data_accessed', 'upstream_version_unchanged', 'base_image', 'build_options', 'source_signer', 'sqlite_stdlib_extension_unchanged', 'fts5_after', 'fts5_upstream_regression_patch', 'mapped_installed_libraries_verified', 'build_toolchain_packages', 'test_only_compatibility', 'local_build_metadata_signed', 'official_fts5_commit', 'strict_full_package_payload_verification', 'local_build_metadata', 'security_accepted', 'positive_verifier', 'compile_options_equal', 'repository_snapshot', 'fts5_merge_patch'}
FINALIZED=[]
def select_variant(name):
 global VARIANT,SPEC,BASE,VERSION,PACKAGE,CHANGES,BUILDINFO,OUTPUTS
 need(type(name)is str and name in PROFILES,'ubuntu_profile')
 VARIANT=name;SPEC=PROFILES[name];BASE=SPEC['base'];VERSION=SPEC['version']
 PACKAGE='libsqlite3-0_'+VERSION+'_amd64.deb';CHANGES='sqlite3_'+VERSION+'_amd64.changes';BUILDINFO='sqlite3_'+VERSION+'_amd64.buildinfo'
 OUTPUTS={PACKAGE:32*1024**2,CHANGES:4*1024**2,BUILDINFO:4*1024**2,'provenance.json':4*1024**2,
  'positive-baseline.json':65536,'positive-candidate.json':65536,'abi-contract.json':4*1024**2,
  'vendor-standard.log':32*1024**2,'vendor-fts5-regression.log':4*1024**2}
def held(path,stack,frames,*,uid,pin=None,maximum=32*1024**2,mode=None):
 path=Path(path);chain=parents(path,uid);before=identity(path.lstat())
 need(path.is_absolute()and path.resolve(strict=True)==path and stat.S_ISREG(before[2])and before[3]in{0,uid}
  and before[4]in{0,uid}and before[5]==1 and not before[2]&0o022 and 0<before[6]<=maximum,'file_identity')
 need(mode is None or stat.S_IMODE(before[2])==mode,'file_mode')
 if pin is not None:need(before[6]==pin[0],'file_size')
 frame=(path,before,chain);flags=None;offset=0;latched=[];attempted=False;confirmed=False;failure=None;framed=False
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
 def check():
  try:
   need(not latched,'prior_source_failure');named([frame])
   need(identity(os.fstat(fd))==before and os.lseek(fd,0,os.SEEK_CUR)==offset,'file_fd_changed')
   if flags is not None:need((fcntl.fcntl(fd,fcntl.F_GETFD),fcntl.fcntl(fd,fcntl.F_GETFL))==flags,'file_fd_flags_changed')
  except BaseException as e:latched.append(type(e).__name__);raise
 def close():
  nonlocal attempted,confirmed,failure
  if attempted:
   if failure is not None:raise failure
   need(confirmed,'source_close_unproven');named([frame]);return
  # Latch BEFORE the syscall. A raised close may already have closed the OFD;
  # no later callback may retry that numeric descriptor or inspect its reuse.
  attempted=True;errors=[]
  try:check()
  except BaseException as e:errors.append(e)
  try:
   os.close(fd)
   try:os.fstat(fd)
   except OSError as e:need(e.errno==errno.EBADF,'original_fd_close');confirmed=True
   else:raise RuntimeError('original_fd_still_open')
  except BaseException as e:errors.append(e)
  try:named([frame])
  except BaseException as e:errors.append(e)
  if errors:
   failure=errors[0]if len(errors)==1 else BaseExceptionGroup('source finalizer failures',errors)
   raise failure
  need(confirmed,'source_close_unproven')
  if framed:FINALIZED.append(frame)
 try:
  flags=(fcntl.fcntl(fd,fcntl.F_GETFD),fcntl.fcntl(fd,fcntl.F_GETFL))
  frames.append(frame);framed=True;stack.callback(close)
 except BaseException as primary:
  try:close()
  except BaseException as cleanup:raise BaseExceptionGroup('source acquisition and finalizer failures',[primary,cleanup])
  raise
 check();parts=[];h=sha256()
 while True:
  raw=os.read(fd,min(65536,maximum-offset+1))
  if not raw:break
  offset+=len(raw);need(offset<=maximum,'file_bound');h.update(raw);parts.append(raw)
 need(offset==before[6]and(pin is None or(offset,h.hexdigest())==pin),'file_pin');check()
 return b''.join(parts),check
def layout(args,environ):
 need(type(args.commit)is str and re.fullmatch('[a-f0-9]{40}',args.commit),'commit_scope')
 need(type(args.nonce)is str and re.fullmatch('[a-f0-9]{32}',args.nonce),'actual_nonce')
 need(type(args.variant)is str and args.variant in PROFILES,'ubuntu_profile')
 run=environ.get('GITHUB_RUN_ID','');attempt=environ.get('GITHUB_RUN_ATTEMPT','')
 need(re.fullmatch('[1-9][0-9]{0,19}',run)and re.fullmatch('[1-9][0-9]{0,7}',attempt),'run_scope')
 temp=Path(environ.get('RUNNER_TEMP',''));repo=Path(args.repo);root=Path(args.root)
 need(temp.is_absolute()and temp.resolve(strict=True)==temp and repo.is_absolute()and repo.resolve(strict=True)==repo,'host_path_scope')
 need(root==temp/('sqlite-ubuntu-gold2-'+args.variant+'-'+run+'-'+attempt+'-'+args.nonce)and not os.path.lexists(root),'fresh_root_required')
 return repo,root,run,attempt
def git_blobs(R,repo,commit):
 rows={}
 need(len(PINS)==22,'tracked_source_inventory')
 for relative,pin in PINS.items():
  ref=commit+':'+(TRACKED/relative).as_posix()
  kind=R.metadata(['/usr/bin/git','-C',str(repo),'cat-file','-t',ref],maximum=16)
  size=R.metadata(['/usr/bin/git','-C',str(repo),'cat-file','-s',ref],maximum=32)
  need(kind==b'blob\n'and size==str(pin[0]).encode()+b'\n','tracked_blob_metadata')
  raw=R.bounded_command(['/usr/bin/git','-C',str(repo),'cat-file','blob',ref],cwd=repo,timeout=30,maximum=pin[0]+1,capture=True)
  need(type(raw)is bytes and(len(raw),sha256(raw).hexdigest())==pin,'tracked_blob_pin');rows[relative]=raw
 return rows
def names(path,expected):
 with os.scandir(path)as stream:
  values=[]
  for row in stream:values.append(row.name);need(len(values)<=len(expected),'bounded_inventory')
 need(set(values)==set(expected)and len(values)==len(expected),'exact_inventory')
def context_inventory(context,R):
 small={name.removeprefix(VARIANT+'/')for name in PINS if name.startswith(VARIANT+'/')}
 need(len(small)==10 and len(R.SOURCE_FILES)==4,'context_inventory_counts')
 names(context,small|{'inputs'});names(context/'inputs',R.SOURCE_FILES)
def seal_context(R,root,blobs,stack,frames,uid,checks):
 context=root/'context';context.mkdir(mode=0o700);(context/'inputs').mkdir(mode=0o700);bindings={}
 for relative,pin in PINS.items():
  if not relative.startswith(VARIANT+'/'):continue
  leaf=relative.split('/',1)[1];raw=blobs[relative]
  need((len(raw),sha256(raw).hexdigest())==pin,'context_source_pin');publish(context/leaf,raw,uid)
  _,check=held(context/leaf,stack,frames,uid=uid,pin=pin);checks.append(check)
  if leaf not in{'.dockerignore','context-manifest.json'}:bindings[leaf]={'bytes':pin[0],'sha256':pin[1]}
 need(len(bindings)==8 and len(R.SOURCE_FILES)==4,'context_payload_counts')
 for leaf,pin in R.SOURCE_FILES.items():
  need(re.fullmatch(r'sqlite3_[A-Za-z0-9.+_-]+\.(?:dsc|tar\.xz)',leaf),'source_archive_name')
  target=context/'inputs'/leaf;R.fetch(R.SNAPSHOT+'/pool/main/s/sqlite3/'+leaf,target,pin)
  _,check=held(target,stack,frames,uid=uid,pin=pin);checks.append(check);bindings['inputs/'+leaf]={'bytes':pin[0],'sha256':pin[1]}
 manifest=document(blobs[VARIANT+'/context-manifest.json'])
 need(type(manifest)is dict and set(manifest)=={'schema','files'}and manifest['schema']=='sqlite_allowlisted_context/1'
  and manifest['files']==bindings and len(bindings)==12,'frozen_context_manifest')
 expected='**\n!.dockerignore\n!context-manifest.json\n'+''.join('!'+name+'\n'for name in('sourcebuild.py','Dockerfile',R.PATCH,R.FTS_PATCH,R.FTS_TEST_PATCH,'verify_positive.py','source-signer.asc','consumer-profile.json'))
 expected+='!inputs/\n'+''.join('!inputs/'+name+'\n'for name in R.SOURCE_FILES)
 need(blobs[VARIANT+'/.dockerignore']==expected.encode(),'frozen_denyall_ignore')
 context_inventory(context,R);return context,bindings
def positive(value,version):
 fields={'schema','status','python_version','sqlite_package','sqlite_runtime','system_library','sqlite_system_library','sqlite_stdlib_extension',
  'python_environment_ignored','python_no_site','compile_options','compile_options_sha256','compatibility','production_data_accessed','wal_reset_reproduction_performed','all_cves_attested','security_accepted'}
 need(type(value)is dict and set(value)==fields and value['schema']=='sqlite_ordinary_compatibility/1'and value['status']=='PASS'
  and value['python_version']==SPEC['python']and value['sqlite_package']==version and value['sqlite_runtime']==SPEC['upstream'],'positive_scope')
 need(value['python_environment_ignored']is True and value['python_no_site']is True
  and all(value[k]is False for k in('production_data_accessed','wal_reset_reproduction_performed','all_cves_attested','security_accepted')),'positive_claims')
 options=value['compile_options'];need(type(options)is list and 0<len(options)<=256 and all(type(v)is str and 0<len(v)<=256 for v in options)
  and options==sorted(set(options))and sha256((json.dumps(options,separators=(',',':'))+'\n').encode()).hexdigest()==value['compile_options_sha256'],'compile_options')
 need('ENABLE_FTS5'in options,'fts5_positive_required')
 for key,want in(('sqlite_system_library',SPEC['library']),('sqlite_stdlib_extension',SPEC['extension'])):
  row=value[key];need(type(row)is dict and set(row)=={'path','bytes','sha256','mapped_inode_verified'}and row['mapped_inode_verified']is True and row['path']==want,'mapped_artifact')
  binding({k:row[k]for k in('bytes','sha256')})
 need(value['system_library']==SPEC['library'],'mapped_artifact_path')
 compat=value['compatibility'];need(type(compat)is dict and set(compat)=={'wal_snapshot','rollback_upsert','json_fts','backup_readonly_reopen','lookup_index','query_result_sha256','query_plan'},'positive_checks')
 need(all(compat[k]is True for k in('wal_snapshot','rollback_upsert','json_fts','backup_readonly_reopen','lookup_index'))and type(compat['query_result_sha256'])is str and re.fullmatch('[a-f0-9]{64}',compat['query_result_sha256'])
  and type(compat['query_plan'])is list and 0<len(compat['query_plan'])<=64 and all(type(v)is str and len(v)<=1024 for v in compat['query_plan']),'positive_checks')
 return value
def verify_artifacts(R,rows,proofs):
 need(type(rows)is dict and set(rows)==set(OUTPUTS)and type(proofs)is dict and set(proofs)==set(OUTPUTS),'output_inventory')
 for name,maximum in OUTPUTS.items():
  binding(proofs[name]);raw=encoded(rows[name])if name.endswith('.json')else rows[name]
  need(type(raw)is bytes and 0<len(raw)<=maximum and proofs[name]=={'bytes':len(raw),'sha256':sha256(raw).hexdigest()},'output_raw_binding')
 before=positive(rows['positive-baseline.json'],BASE);after=positive(rows['positive-candidate.json'],VERSION)
 need(before['compile_options']==after['compile_options']and before['compatibility']==after['compatibility']and before['sqlite_stdlib_extension']==after['sqlite_stdlib_extension'],'positive_parity')
 abi=rows['abi-contract.json'];need(type(abi)is dict and set(abi)=={'soname','needed','exports','glibc_versions'}and abi['soname']=='libsqlite3.so.0','native_abi')
 for key in('needed','exports','glibc_versions'):
  values=abi[key];need(type(values)is list and 0<len(values)<=65536 and all(type(v)is str and 0<len(v)<=256 for v in values)and values==sorted(set(values)),'native_abi')
 need(all(re.fullmatch('GLIBC_[0-9.]+',v)for v in abi['glibc_versions'])and 'libc.so.6'in abi['needed']
  and{'sqlite3_open_v2','sqlite3_prepare_v2','sqlite3_backup_init','sqlite3_wal_checkpoint_v2'}<=set(abi['exports']),'native_abi')
 p=rows['provenance.json'];need(type(p)is dict and set(p)==PROVENANCE_FIELDS|({'rowid_nested_join_backport'}if VARIANT=='noble'else set())and p.get('schema')=='sqlite_wal_fts5_local_ubuntu_package/2','provenance_schema')
 bound=lambda pin:{'bytes':pin[0],'sha256':pin[1]}
 expected={'package':'libsqlite3-0','base_package_version':BASE,'local_package_version':VERSION,'upstream_version_unchanged':SPEC['upstream'],'architecture':'amd64',
  'repository_snapshot':R.SNAPSHOT,'repository_releases':{suite:bound(pin)for suite,pin in R.INRELEASES.items()},'source_date_epoch':R.SOURCE_EPOCH,
  'build_options':'nocheck parallel=2','production_patch':bound(R.PATCH_PIN),'positive_verifier':bound(R.VERIFY_PIN),'wal_before':bound(R.WAL_BEFORE),'wal_after':bound(R.WAL_AFTER),
  'fts5_merge_patch':bound(R.FTS_PATCH_PIN),'fts5_upstream_regression_patch':bound(R.FTS_TEST_PATCH_PIN),'fts5_before':bound(R.FTS_BEFORE),'fts5_after':bound(R.FTS_AFTER),
  'official_fts5_commit':R.UPSTREAM_FTS_COMMIT,'base_image':R.BASE_IMAGE,'root_observed_consumer_profile':bound(R.PROFILE_PIN),
  'source_signer':{'fingerprint':R.SIGNER_FINGERPRINT,**bound(R.SIGNER_PIN)},'public_ca_bootstrap':R.CA_BOOTSTRAP,
  'package_doc_path_includes':['/usr/share/doc/libsqlite3-0','/usr/share/doc/libsqlite3-0/*']}
 for key,want in expected.items():need(type(p.get(key))is type(want)and encoded(p[key])==encoded(want),'provenance_pin')
 for key in('native_bundle_replaces_bootstrap_after_authenticated_install','source_maintainer_signature_verified','baseline_authenticated_reinstall_required',
  'strict_full_package_payload_verification','compile_options_equal','elf_soname_exports_dependencies_equal','python_binary_unchanged',
  'sqlite_stdlib_extension_unchanged','mapped_installed_libraries_verified','app_ci_restore_gate_required'):need(p[key]is True,'provenance_proof')
 for key in('upstream_fault_suites_run','wal_reset_reproduction_performed','full_upstream_suite_pass_claimed','local_build_metadata_signed',
  'runtime_loader_override_used','production_data_accessed','security_accepted'):need(p[key]is False,'provenance_claims')
 native_ca=p['native_ca_certificates_package'];need(type(native_ca)is str and re.fullmatch('[A-Za-z0-9.+:~_-]{1,100}',native_ca),'native_ca_package')
 need(p['source_files']=={name:bound(pin)for name,pin in R.SOURCE_FILES.items()},'source_provenance')
 R.vendor_test_compatibility_contract(p['test_only_compatibility'])
 if VARIANT=='noble':R.rowid_nested_join_contract(p['rowid_nested_join_backport'])
 vendor=p['vendor_tests'];need(type(vendor)is dict and set(vendor)=={'same_configured_source','standard_and_official_regression','standard_scope',
  'no_new_filters_or_exclusions','no_new_filters_or_exclusions_scope','testfixture_harness','full_all_permutations_claimed','default_omissions_are_not_waivers','unprivileged_execution'},'vendor_provenance')
 need(type(vendor['standard_scope'])is dict and encoded(vendor['standard_scope'])==encoded(R.VENDOR_STANDARD_SCOPE),'vendor_standard_scope')
 need(vendor['no_new_filters_or_exclusions']is True and vendor['no_new_filters_or_exclusions_scope']=='test_selection_only'
  and vendor['full_all_permutations_claimed']is False and vendor['default_omissions_are_not_waivers']is True,'vendor_claims')
 R.vendor_privilege_contract(vendor['unprivileged_execution'])
 harness=R.testfixture_harness_contract(vendor['testfixture_harness'])
 watched=vendor['same_configured_source'];need(type(watched)is dict and set(watched)=={'Makefile','debian/rules','debian/control','src/wal.c','ext/fts5/fts5_index.c','ext/fts5/test/fts5prefix2.test',*R.VENDOR_HELPERS},'vendor_same_source')
 for value in watched.values():binding(value)
 need(watched['Makefile']=={key:harness['generated_after'][key]for key in('bytes','sha256')},'testfixture_generated_source_changed')
 need(all(watched[name]==bound(pin)for name,pin in R.VENDOR_HELPERS.items()),'vendor_naming_helper_changed')
 need(watched['debian/rules']==p['debian_rules']and watched['debian/control']==p['ubuntu_control']and watched['src/wal.c']==p['wal_after']
  and watched['ext/fts5/fts5_index.c']==p['fts5_after']and watched['ext/fts5/test/fts5prefix2.test']==bound(R.FTS_TEST_AFTER),'vendor_same_source')
 need(type(vendor['standard_and_official_regression'])is dict and set(vendor['standard_and_official_regression'])==set(R.VENDOR_COMMANDS),'vendor_inventory')
 for name in R.VENDOR_COMMANDS:need(vendor['standard_and_official_regression'][name]==R.vendor_log_contract(name,rows[name]),'vendor_actual_log_contract')
 binding(p['debian_rules']);binding(p['ubuntu_control']);tools=p['build_toolchain_packages']
 need(type(tools)is list and 1<=len(tools)<=64 and all(type(v)is str and 0<len(v)<=256 for v in tools),'toolchain_provenance')
 need(p['package_file']=={'name':PACKAGE,**proofs[PACKAGE]}and p['local_build_metadata']=={name:proofs[name]for name in(CHANGES,BUILDINFO)},'package_provenance')
 for key,value in(('baseline_library',before['sqlite_system_library']),('candidate_library',after['sqlite_system_library'])):need(p[key]=={k:value[k]for k in('bytes','sha256')},'library_provenance')
 for name in(CHANGES,BUILDINFO):
  listed=debian_checksums(rows[name]);need(listed.get(PACKAGE)==proofs[PACKAGE],'debian_package_binding')
  if name==CHANGES:need(listed.get(BUILDINFO)==proofs[BUILDINFO],'debian_buildinfo_binding')
 return{'baseline_pass':True,'candidate_pass':True,'native_abi_checked':True,'compile_options_equal':True,'python_version':SPEC['python'],
  'sqlite_version':SPEC['upstream'],'vendor_standard_zero_errors':True,'official_six_fts5_cases':True,'security_accepted':False}
def build_timeout(end,now):
 value=min(BUILD_SECONDS,end-now-FINALIZE_SECONDS);need(value>0,'ci_wall_budget');return value
def failure(phase):
 return{'schema':'ubuntu_sqlite_hosted_ci_failure/1','status':'FAIL','phase':phase,'automatic_retry':False,
  'daemon_cancellation_confirmed':False,'ephemeral_vm_retirement_required':True,'artifact_accepted':False,'security_accepted':False}
def execute(args):
 select_variant(args.variant);FINALIZED.clear();uid=1001;repo,root,run,attempt=layout(args,os.environ)
 facts=host_facts(root.parent);parents(root,uid);parents(repo/'unused',uid)
 root.mkdir(mode=0o700);(root/'reports').mkdir(mode=0o700);(root/'host-home').mkdir(mode=0o700);(root/'docker-config').mkdir(mode=0o700)
 frames=[];checks=[];started=time.monotonic();end=started+GLOBAL_SECONDS;stop=threading.Event();worker=None;phase='source_admission';disks=[]
 need(signal.getitimer(signal.ITIMER_REAL)==(0.0,0.0),'existing_alarm');previous=signal.getsignal(signal.SIGALRM);prior_disk=signal.getsignal(signal.SIGUSR1)
 def expired(*_):raise RuntimeError('ci_wall_budget')
 def disk_expired(*_):raise RuntimeError('ci_vm_disk_or_source_budget')
 signal.signal(signal.SIGALRM,expired);signal.signal(signal.SIGUSR1,disk_expired);signal.setitimer(signal.ITIMER_REAL,GLOBAL_SECONDS)
 try:
  with ExitStack()as stack:
   source=repo/TRACKED/VARIANT/'sourcebuild.py'
   raw,check=held(source,stack,frames,uid=uid,pin=PINS[VARIANT+'/sourcebuild.py'],maximum=131072);checks.append(check)
   R=ModuleType('_sealed_public_ubuntu_sqlite_recipe');R.__file__=str(source);exec(compile(raw,R.__file__,'exec'),R.__dict__)
   # Cold host module only. The exact recipe Source and Docker builder ENV remain unchanged.
   R.ENV={'PATH':PATH,'HOME':str(root/'host-home'),'DOCKER_CONFIG':str(root/'docker-config'),'LC_ALL':'C.UTF-8','LANG':'C.UTF-8',
    'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null','GIT_TERMINAL_PROMPT':'0','GIT_OPTIONAL_LOCKS':'0','GIT_CONFIG_COUNT':'0'}
   blobs=git_blobs(R,repo,args.commit)
   for relative,pin in PINS.items():
    local,check=held(repo/TRACKED/relative,stack,frames,uid=uid,pin=pin,maximum=131072);checks.append(check);need(local==blobs[relative],'checkout_blob_drift')
   context,inputs=seal_context(R,root,blobs,stack,frames,uid,checks)
   publish(root/'source-identity.json',encoded({'schema':'ubuntu_sqlite_hosted_source_identity/1','commit':args.commit,'run_id':run,'attempt':attempt,
    'variant':VARIANT,'nonce':args.nonce,'tracked_files':{str(TRACKED/name):{'bytes':pin[0],'sha256':pin[1]}for name,pin in PINS.items()},
    'tracked_small_files':22,'selected_small_files':10,'context_payload_files':12,'context_control_files':2,'context_regular_files':14,'security_accepted':False}),uid)
   publish(root/'reports/vm-resources.json',encoded({'schema':'ubuntu_sqlite_hosted_vm_resources/1','facts':facts,
    'admission_basis':'trusted-workflow-hosted-selection-plus-uid-os-environment','buildkit_client_cgroup_limits_claimed':False,
    'global_wall_seconds':GLOBAL_SECONDS,'build_command_max_seconds':BUILD_SECONDS,'finalize_reserve_seconds':FINALIZE_SECONDS,
    'frozen_native_lifetime_seconds':FROZEN_NATIVE_LIFETIME_SECONDS,'native_runner_executed':False,'workflow_job_seconds':WORKFLOW_JOB_SECONDS,
    'build_output_limit_bytes':LOG_MAX,'vm_disk_growth_limit_bytes':DISK_GROWTH,'vm_disk_free_floor_bytes':DISK_RESERVE,'security_accepted':False}),uid)
   default_driver(R)
   docker_root=document(R.metadata(['/usr/bin/docker','info','--format','{{json .DockerRootDir}}'],maximum=4096))
   need(type(docker_root)is str and 0<len(docker_root)<=4096 and '\n'not in docker_root and '..'not in Path(docker_root).parts,'docker_disk_path')
   disks=[disk_scope(root,uid,initial=facts['free_bytes']),disk_scope(Path(docker_root),uid)]
   publish(root/'reports/disk-scopes.json',encoded({'schema':'ubuntu_sqlite_hosted_disk_scopes/1','scopes':disks,
    'distinct_filesystems':len({scope['filesystem_id']for scope in disks}),'growth_limit_per_filesystem_bytes':DISK_GROWTH,
    'free_floor_per_filesystem_bytes':DISK_RESERVE,'hard_quota_claimed':False}),uid)
   def monitor():
    while not stop.wait(1):
     try:
      for scope in disks:check_disk_scope(scope)
      for check in checks:check()
     except BaseException:os.kill(os.getpid(),signal.SIGUSR1);return
   worker=threading.Thread(target=monitor,name='owned-ubuntu-ci-disk-source-monitor',daemon=True);worker.start()
   output=root/'package-output';output.mkdir(mode=0o700);phase='buildkit'
   fd=os.open(root/'build.log',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
   with os.fdopen(fd,'w',encoding='utf-8')as log,redirect_stderr(log):
    R.bounded_command(build_argv(context,output),cwd=context,timeout=build_timeout(end,time.monotonic()),maximum=LOG_MAX,capture=False)
    log.flush();os.fsync(log.fileno())
   phase='artifact_validation';names(output,OUTPUTS);rows={};proofs={}
   for name,maximum in OUTPUTS.items():
    data,check=held(output/name,stack,frames,uid=uid,maximum=maximum,mode=0o644);checks.append(check)
    proofs[name]={'bytes':len(data),'sha256':sha256(data).hexdigest()};rows[name]=document(data)if name.endswith('.json')else data
   verdict=verify_artifacts(R,rows,proofs)
   for field,value in(('Package','libsqlite3-0'),('Version',VERSION),('Architecture','amd64'),('Multi-Arch','same')):
    need(R.metadata(['/usr/bin/dpkg-deb','-f',str(output/PACKAGE),field],maximum=4096).decode('ascii').strip()==value,'package_control')
   context_inventory(context,R)
   for check in checks:check()
   stop.set();worker.join(timeout=2);need(not worker.is_alive(),'monitor_close');phase='source_finalizers'
  named(frames);need(len(FINALIZED)==len(frames)and time.monotonic()<end,'source_finality_or_wall_budget')
  for scope in disks:check_disk_scope(scope)
  receipt={'schema':'ubuntu_sqlite_hosted_package_ci_receipt/1','status':'PASS','commit':args.commit,'run_id':run,'attempt':attempt,
   'variant':VARIANT,'nonce':args.nonce,'package_outputs':proofs,'context_files':inputs,'checks':verdict,'source_original_roles':len(frames),
   'source_original_fds_physically_closed':len(FINALIZED),'source_finalizers_closed':True,'elapsed_seconds':time.monotonic()-started,
   'buildkit_client_cgroup_limits_claimed':False,'artifact_id_assigned':False,'deployed':False,'security_accepted':False}
  publish(root/'reports/ci-receipt.json',encoded(receipt),uid);return receipt
 except BaseException:
  publish(root/'reports/ci-failure.json',encoded(failure(phase)),uid);raise
 finally:
  stop.set()
  if worker is not None:worker.join(timeout=2)
  signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,previous);signal.signal(signal.SIGUSR1,prior_disk)
def main(argv=None):
 p=argparse.ArgumentParser();p.add_argument('--repo');p.add_argument('--commit');p.add_argument('--root');p.add_argument('--nonce');p.add_argument('--variant',choices=tuple(PROFILES));p.add_argument('--execute-hosted-build',action='store_true');args=p.parse_args(argv)
 if not args.execute_hosted_build:
  print(json.dumps({'schema':'ubuntu_sqlite_hosted_ci_unarmed/1','build_performed':False,'network_access_performed':False,'security_accepted':False},sort_keys=True));return 0
 need(all(type(v)is str and v for v in(args.repo,args.commit,args.root,args.nonce,args.variant)),'hosted_arguments')
 result=execute(args);print(json.dumps({'schema':result['schema'],'status':result['status'],'security_accepted':False},sort_keys=True));return 0
if __name__=='__main__':raise SystemExit(main())
