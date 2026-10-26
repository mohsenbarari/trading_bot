"""Public package-only CI driver. Import/default CLI never builds or fetches.

Execution is confined to a workflow-selected dedicated GitHub-hosted Ubuntu VM.
The Docker client is not a BuildKit CPU/memory cgroup controller. VM resource
facts, finite wall/output limits and a monitored disk floor are reported honestly.
"""
from contextlib import ExitStack,redirect_stderr
from hashlib import sha256
from pathlib import Path
from types import ModuleType
import argparse,json,os,re,signal,stat,sys,threading,time

TRACKED=Path('deploy/security/sqlite-wal-backport')
PINS={
 'sourcebuild.py':(28401,'5d9834419b6d7400f5489dbebc5af83d960e808baa628382c1e75039a522dd34'),
 'Dockerfile':(1238,'9a0b4ee5584d9bc212a7a3b3254ff7a48fe992a2f622032448a8d985c74fd363'),
 'verify_positive.py':(16377,'623384cc7e63c04834d1042c63064b85a83c6966d0cc95da172f5e719f8f06ab'),
 '0001-wal-reset-production-only.patch':(6746,'ab1d8c75b8ed521cc5d1d0c64da66be26ca3b72d53326c28b6a86473f42ddc85')}
VERSION='3.46.1-7+deb13u2+gold1';BASE='3.46.1-7+deb13u2'
PACKAGE='libsqlite3-0_'+VERSION+'_amd64.deb'
CHANGES='sqlite3_'+VERSION+'_amd64.changes';BUILDINFO='sqlite3_'+VERSION+'_amd64.buildinfo'
OUTPUTS={PACKAGE:32*1024**2,CHANGES:4*1024**2,BUILDINFO:4*1024**2,
 'provenance.json':4*1024**2,'positive-baseline.json':65536,'positive-candidate.json':65536,'abi-contract.json':4*1024**2}
PATH='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
DISK_RESERVE=2*1024**3;DISK_GROWTH=8*1024**3
GLOBAL_SECONDS=3200;BUILD_SECONDS=3000;LOG_MAX=32*1024**2
PROVENANCE_FIELDS={'schema','package','base_package_version','local_package_version','upstream_version_unchanged','architecture',
 'repository_snapshot','repository_release','source_files','source_maintainer_signature_verified','debian_rules','production_patch','wal_before',
 'wal_after','positive_verifier','source_date_epoch','build_options','upstream_fault_suites_run','wal_reset_reproduction_performed',
 'full_upstream_suite_pass_claimed','baseline_library','candidate_library','package_file','local_build_metadata','local_build_metadata_signed',
 'compile_options_equal','elf_soname_exports_dependencies_equal','python_binary_unchanged','sqlite_stdlib_extension_unchanged',
 'mapped_installed_libraries_verified','build_toolchain_packages','full_sqlite_documentation_restored','dpkg_path_includes',
 'runtime_loader_override_used','production_data_accessed','app_ci_restore_gate_required','security_accepted'}

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
def held(path,stack,frames,*,uid,pin=None,maximum=32*1024**2):
 path=Path(path);chain=parents(path,uid);before=identity(path.lstat())
 need(path.is_absolute() and path.resolve(strict=True)==path and stat.S_ISREG(before[2]) and before[3]in{0,uid}
  and before[5]==1 and not before[2]&0o022 and 0<before[6]<=maximum,'file_identity')
 if pin is not None:need(before[6]==pin[0],'file_size')
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC);stack.callback(os.close,fd)
 need(identity(os.fstat(fd))==before,'opened_file_identity');parts=[];count=0;h=sha256()
 while True:
  raw=os.read(fd,min(65536,maximum-count+1))
  if not raw:break
  count+=len(raw);need(count<=maximum,'file_bound');h.update(raw);parts.append(raw)
 need(count==before[6] and (pin is None or (count,h.hexdigest())==pin),'file_pin')
 def check():
  need(identity(os.fstat(fd))==before,'file_changed');named([(path,before,chain)])
 check();stack.callback(check);frames.append((path,before,chain));return b''.join(parts),check
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
def layout(args,environ):
 need(type(args.commit)is str and re.fullmatch('[a-f0-9]{40}',args.commit),'commit_scope')
 run=environ.get('GITHUB_RUN_ID','');attempt=environ.get('GITHUB_RUN_ATTEMPT','')
 need(re.fullmatch('[1-9][0-9]{0,19}',run)and re.fullmatch('[1-9][0-9]{0,7}',attempt),'run_scope')
 temp=Path(environ.get('RUNNER_TEMP',''));repo=Path(args.repo);root=Path(args.root)
 need(temp.is_absolute()and temp.resolve(strict=True)==temp and repo.is_absolute()and repo.resolve(strict=True)==repo,'host_path_scope')
 need(root==temp/('sqlite-wal-'+run+'-'+attempt)and not os.path.lexists(root),'fresh_root_required')
 return repo,root,run,attempt
def git_blobs(R,repo,commit):
 rows={}
 for leaf,pin in PINS.items():
  ref=commit+':'+(TRACKED/leaf).as_posix()
  kind=R.metadata(['/usr/bin/git','-C',str(repo),'cat-file','-t',ref],maximum=16)
  size=R.metadata(['/usr/bin/git','-C',str(repo),'cat-file','-s',ref],maximum=32)
  need(kind==b'blob\n'and size==str(pin[0]).encode()+b'\n','tracked_blob_metadata')
  raw=R.bounded_command(['/usr/bin/git','-C',str(repo),'cat-file','blob',ref],cwd=repo,timeout=30,maximum=pin[0]+1,capture=True)
  need(type(raw)is bytes and (len(raw),sha256(raw).hexdigest())==pin,'tracked_blob_pin');rows[leaf]=raw
 return rows
def context_inventory(context,R):
 expected=set(PINS)|{'context-manifest.json','.dockerignore','inputs'}
 need({p.name for p in context.iterdir()}==expected and {p.name for p in(context/'inputs').iterdir()}==set(R.SOURCE_FILES),'context_inventory')
def seal_context(R,root,blobs,stack,frames,uid):
 context=root/'context';context.mkdir(mode=0o700);(context/'inputs').mkdir(mode=0o700);bindings={}
 for leaf,pin in PINS.items():
  raw=blobs[leaf];need((len(raw),sha256(raw).hexdigest())==pin,'context_source_pin');publish(context/leaf,raw,uid)
  held(context/leaf,stack,frames,uid=uid,pin=pin);bindings[leaf]={'bytes':pin[0],'sha256':pin[1]}
 need(len(R.SOURCE_FILES)==4,'source_archive_scope')
 for leaf,pin in R.SOURCE_FILES.items():
  need(re.fullmatch('sqlite3_[A-Za-z0-9.+_-]+\\.(?:dsc|tar\\.xz)',leaf),'source_archive_name')
  target=context/'inputs'/leaf;R.fetch(R.SNAPSHOT+'/pool/main/s/sqlite3/'+leaf,target,pin)
  held(target,stack,frames,uid=uid,pin=pin);bindings['inputs/'+leaf]={'bytes':pin[0],'sha256':pin[1]}
 manifest=encoded({'schema':'sqlite_allowlisted_context/1','files':bindings});publish(context/'context-manifest.json',manifest,uid)
 ignore=('**\n!.dockerignore\n!context-manifest.json\n'+''.join('!'+name+'\n'for name in PINS)+'!inputs/\n'+''.join('!inputs/'+name+'\n'for name in R.SOURCE_FILES)).encode()
 publish(context/'.dockerignore',ignore,uid)
 for leaf,raw in (('context-manifest.json',manifest),('.dockerignore',ignore)):held(context/leaf,stack,frames,uid=uid,pin=(len(raw),sha256(raw).hexdigest()))
 context_inventory(context,R);return context,bindings

def binding(value):
 need(type(value)is dict and set(value)=={'bytes','sha256'}and type(value['bytes'])is int and 0<value['bytes']<=64*1024**2
  and type(value['sha256'])is str and re.fullmatch('[a-f0-9]{64}',value['sha256']),'artifact_binding');return value
def positive(value,version):
 fields={'schema','status','python_version','sqlite_package','sqlite_runtime','system_library','sqlite_system_library','sqlite_stdlib_extension',
  'python_environment_ignored','compile_options','compile_options_sha256','compatibility','production_data_accessed','wal_reset_reproduction_performed','all_cves_attested','security_accepted'}
 need(type(value)is dict and set(value)==fields and value['schema']=='sqlite_ordinary_compatibility/1'and value['status']=='PASS'
  and value['python_version']=='3.11.17'and value['sqlite_package']==version and value['sqlite_runtime']=='3.46.1','positive_scope')
 need(value['python_environment_ignored']is True and all(value[k]is False for k in ('production_data_accessed','wal_reset_reproduction_performed','all_cves_attested','security_accepted')),'positive_claims')
 options=value['compile_options'];need(type(options)is list and 0<len(options)<=256 and all(type(v)is str and 0<len(v)<=256 for v in options)
  and options==sorted(set(options))and sha256((json.dumps(options,separators=(',',':'))+'\n').encode()).hexdigest()==value['compile_options_sha256'],'compile_options')
 for key in ('sqlite_system_library','sqlite_stdlib_extension'):
  row=value[key];need(type(row)is dict and set(row)=={'path','bytes','sha256','mapped_inode_verified'}and row['mapped_inode_verified']is True,'mapped_artifact')
  binding({k:row[k]for k in ('bytes','sha256')});need(type(row['path'])is str and row['path'].startswith('/usr/')and '..'not in Path(row['path']).parts,'mapped_artifact_path')
 need(value['system_library']==value['sqlite_system_library']['path'],'mapped_artifact_path')
 compat=value['compatibility'];need(type(compat)is dict and set(compat)=={'wal_snapshot','rollback_upsert','json_fts','backup_readonly_reopen','lookup_index','query_result_sha256','query_plan'},'positive_checks')
 need(all(compat[k]is True for k in ('wal_snapshot','rollback_upsert','json_fts','backup_readonly_reopen','lookup_index'))and type(compat['query_result_sha256'])is str and re.fullmatch('[a-f0-9]{64}',compat['query_result_sha256'])
  and type(compat['query_plan'])is list and 0<len(compat['query_plan'])<=64 and all(type(v)is str and len(v)<=1024 for v in compat['query_plan']),'positive_checks')
 return value
def verify_artifacts(R,rows,proofs):
 need(type(rows)is dict and set(rows)==set(OUTPUTS)and type(proofs)is dict and set(proofs)==set(OUTPUTS),'output_inventory')
 for name in OUTPUTS:binding(proofs[name])
 before=positive(rows['positive-baseline.json'],BASE);after=positive(rows['positive-candidate.json'],VERSION)
 need(before['compile_options']==after['compile_options']and before['compatibility']==after['compatibility']
  and before['sqlite_stdlib_extension']==after['sqlite_stdlib_extension'],'positive_parity')
 abi=rows['abi-contract.json'];need(type(abi)is dict and set(abi)=={'soname','needed','exports','glibc_versions'}and abi['soname']=='libsqlite3.so.0','native_abi')
 for key in ('needed','exports','glibc_versions'):
  values=abi[key];need(type(values)is list and 0<len(values)<=65536 and all(type(v)is str and 0<len(v)<=256 for v in values)and values==sorted(set(values)),'native_abi')
 need(all(re.fullmatch('GLIBC_[0-9.]+',v)for v in abi['glibc_versions']),'native_abi')
 need('libc.so.6'in abi['needed']and {'sqlite3_open_v2','sqlite3_prepare_v2','sqlite3_backup_init','sqlite3_wal_checkpoint_v2'}<=set(abi['exports']),'native_abi')
 p=rows['provenance.json'];need(type(p)is dict and set(p)==PROVENANCE_FIELDS and p.get('schema')=='sqlite_wal_local_debian_package/1','provenance_schema')
 for key,want in {'package':'libsqlite3-0','base_package_version':BASE,'local_package_version':VERSION,'upstream_version_unchanged':'3.46.1','architecture':'amd64',
  'repository_snapshot':R.SNAPSHOT,'repository_release':{'bytes':R.INRELEASE[0],'sha256':R.INRELEASE[1]},'source_date_epoch':R.SOURCE_EPOCH,
  'build_options':'nocheck parallel=2','production_patch':{'bytes':R.PATCH_PIN[0],'sha256':R.PATCH_PIN[1]},'positive_verifier':{'bytes':R.VERIFY_PIN[0],'sha256':R.VERIFY_PIN[1]},
  'wal_before':{'bytes':R.WAL_BEFORE[0],'sha256':R.WAL_BEFORE[1]},'wal_after':{'bytes':R.WAL_AFTER[0],'sha256':R.WAL_AFTER[1]},
  'dpkg_path_includes':list(R.DOC_INCLUDES)}.items():need(type(p.get(key))is type(want)and p[key]==want,'provenance_pin')
 for key in ('source_maintainer_signature_verified','compile_options_equal','elf_soname_exports_dependencies_equal','python_binary_unchanged','sqlite_stdlib_extension_unchanged','mapped_installed_libraries_verified','full_sqlite_documentation_restored','app_ci_restore_gate_required'):need(p.get(key)is True,'provenance_proof')
 for key in ('upstream_fault_suites_run','wal_reset_reproduction_performed','full_upstream_suite_pass_claimed','local_build_metadata_signed','runtime_loader_override_used','production_data_accessed','security_accepted'):need(p.get(key)is False,'provenance_claims')
 need(p.get('source_files')=={name:{'bytes':pin[0],'sha256':pin[1]}for name,pin in R.SOURCE_FILES.items()},'source_provenance')
 binding(p['debian_rules']);tools=p['build_toolchain_packages'];need(type(tools)is list and 1<=len(tools)<=64 and all(type(v)is str and 0<len(v)<=256 for v in tools),'toolchain_provenance')
 need(p.get('package_file')=={'name':PACKAGE,**proofs[PACKAGE]}and p.get('local_build_metadata')=={name:proofs[name]for name in (CHANGES,BUILDINFO)},'package_provenance')
 for key,value in (('baseline_library',before['sqlite_system_library']),('candidate_library',after['sqlite_system_library'])):need(p.get(key)=={k:value[k]for k in ('bytes','sha256')},'library_provenance')
 for name in (CHANGES,BUILDINFO):
  listed=debian_checksums(rows[name]);need(listed.get(PACKAGE)==proofs[PACKAGE],'debian_package_binding')
  if name==CHANGES:need(listed.get(BUILDINFO)==proofs[BUILDINFO],'debian_buildinfo_binding')
 return {'baseline_pass':True,'candidate_pass':True,'native_abi_checked':True,'compile_options_equal':True,'python_version':'3.11.17','sqlite_version':'3.46.1','security_accepted':False}

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
def execute(args):
 uid=1001;repo,root,run,attempt=layout(args,os.environ);facts=host_facts(root.parent);parents(root,uid);parents(repo/'unused',uid)
 root.mkdir(mode=0o700);(root/'reports').mkdir(mode=0o700);(root/'host-home').mkdir(mode=0o700);(root/'docker-config').mkdir(mode=0o700)
 frames=[];checks=[];end=time.monotonic()+GLOBAL_SECONDS;stop=threading.Event();worker=None
 need(signal.getitimer(signal.ITIMER_REAL)==(0.0,0.0),'existing_alarm');previous=signal.getsignal(signal.SIGALRM);prior_disk=signal.getsignal(signal.SIGUSR1)
 def expired(*_):raise RuntimeError('ci_wall_budget')
 def disk_expired(*_):raise RuntimeError('ci_vm_disk_budget')
 signal.signal(signal.SIGALRM,expired);signal.signal(signal.SIGUSR1,disk_expired);signal.setitimer(signal.ITIMER_REAL,GLOBAL_SECONDS)
 try:
  with ExitStack()as stack:
   raw,check=held(repo/TRACKED/'sourcebuild.py',stack,frames,uid=uid,pin=PINS['sourcebuild.py'],maximum=131072);checks.append(check)
   R=ModuleType('_sealed_public_sqlite_recipe');R.__file__=str(repo/TRACKED/'sourcebuild.py');exec(compile(raw,R.__file__,'exec'),R.__dict__)
   # Only this cold host module's ENV changes. No source text or builder ENV is changed.
   R.ENV={'PATH':PATH,'HOME':str(root/'host-home'),'DOCKER_CONFIG':str(root/'docker-config'),'LC_ALL':'C.UTF-8','LANG':'C.UTF-8',
    'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null','GIT_TERMINAL_PROMPT':'0','GIT_OPTIONAL_LOCKS':'0','GIT_CONFIG_COUNT':'0'}
   blobs=git_blobs(R,repo,args.commit)
   for leaf,pin in PINS.items():
    local,check=held(repo/TRACKED/leaf,stack,frames,uid=uid,pin=pin,maximum=131072);checks.append(check);need(local==blobs[leaf],'checkout_blob_drift')
   context,inputs=seal_context(R,root,blobs,stack,frames,uid)
   publish(root/'source-identity.json',encoded({'schema':'sqlite_hosted_source_identity/1','commit':args.commit,'run_id':run,'attempt':attempt,
    'tracked_files':{str(TRACKED/name):{'bytes':pin[0],'sha256':pin[1]}for name,pin in PINS.items()},'context_payload_files':8,'context_control_files':2,'security_accepted':False}),uid)
   publish(root/'reports/vm-resources.json',encoded({'schema':'sqlite_hosted_vm_resources/1','facts':facts,
    'admission_basis':'trusted-workflow-hosted-selection-plus-uid-os-environment','buildkit_client_cgroup_limits_claimed':False,
    'global_wall_seconds':GLOBAL_SECONDS,'build_command_max_seconds':BUILD_SECONDS,'build_output_limit_bytes':LOG_MAX,
    'vm_disk_growth_limit_bytes':DISK_GROWTH,'vm_disk_free_floor_bytes':DISK_RESERVE,'security_accepted':False}),uid)
   default_driver(R)
   docker_root=document(R.metadata(['/usr/bin/docker','info','--format','{{json .DockerRootDir}}'],maximum=4096))
   need(type(docker_root)is str and 0<len(docker_root)<=4096 and '\n'not in docker_root and '..'not in Path(docker_root).parts,'docker_disk_path')
   disks=[disk_scope(root,uid,initial=facts['free_bytes']),disk_scope(Path(docker_root),uid)]
   publish(root/'reports/disk-scopes.json',encoded({'schema':'sqlite_hosted_disk_scopes/1','scopes':disks,
    'distinct_filesystems':len({scope['filesystem_id']for scope in disks}),
    'growth_limit_per_filesystem_bytes':DISK_GROWTH,'free_floor_per_filesystem_bytes':DISK_RESERVE,'hard_quota_claimed':False}),uid)
   def monitor():
    while not stop.wait(1):
     try:
      for scope in disks:check_disk_scope(scope)
      for check in checks:check()
     except BaseException:os.kill(os.getpid(),signal.SIGUSR1);return
   worker=threading.Thread(target=monitor,name='owned-ci-disk-monitor',daemon=True);worker.start()
   output=root/'package-output';output.mkdir(mode=0o700)
   fd=os.open(root/'build.log',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
   with os.fdopen(fd,'w',encoding='utf-8')as log,redirect_stderr(log):
    timeout=min(BUILD_SECONDS,end-time.monotonic()-60);need(timeout>0,'ci_wall_budget')
    R.bounded_command(build_argv(context,output),cwd=context,timeout=timeout,maximum=LOG_MAX,capture=False)
    log.flush();os.fsync(log.fileno())
   need({p.name for p in output.iterdir()}==set(OUTPUTS),'output_inventory');rows={};proofs={}
   for name,maximum in OUTPUTS.items():
    data,check=held(output/name,stack,frames,uid=uid,maximum=maximum);checks.append(check);proofs[name]={'bytes':len(data),'sha256':sha256(data).hexdigest()}
    rows[name]=document(data)if name.endswith('.json')else data
   verdict=verify_artifacts(R,rows,proofs)
   for field,value in (('Package','libsqlite3-0'),('Version',VERSION),('Architecture','amd64'),('Multi-Arch','same')):
    need(R.metadata(['/usr/bin/dpkg-deb','-f',str(output/PACKAGE),field],maximum=4096).decode('ascii').strip()==value,'package_control')
   context_inventory(context,R)
   for check in checks:check()
   stop.set();worker.join(timeout=2);need(not worker.is_alive(),'monitor_close')
  named(frames);need(time.monotonic()<end,'ci_wall_budget')
  for scope in disks:check_disk_scope(scope)
  receipt={'schema':'sqlite_hosted_package_ci_receipt/1','status':'PASS','commit':args.commit,'run_id':run,'attempt':attempt,
   'package_outputs':proofs,'context_files':inputs,'checks':verdict,'source_finalizers_closed':True,
   'buildkit_client_cgroup_limits_claimed':False,'artifact_id_assigned':False,'deployed':False,'security_accepted':False}
  publish(root/'reports/ci-receipt.json',encoded(receipt),uid);return receipt
 except BaseException:
  publish(root/'reports/ci-failure.json',encoded({'schema':'sqlite_hosted_ci_failure/1','status':'FAIL','automatic_retry':False,
   'daemon_cancellation_confirmed':False,'ephemeral_vm_retirement_required':True,'artifact_accepted':False,'security_accepted':False}),uid)
  raise
 finally:
  stop.set()
  if worker is not None:worker.join(timeout=2)
  signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,previous);signal.signal(signal.SIGUSR1,prior_disk)
def main(argv=None):
 p=argparse.ArgumentParser();p.add_argument('--repo');p.add_argument('--commit');p.add_argument('--root');p.add_argument('--execute-hosted-build',action='store_true');args=p.parse_args(argv)
 if not args.execute_hosted_build:
  print(json.dumps({'schema':'sqlite_hosted_ci_unarmed/1','build_performed':False,'network_access_performed':False,'security_accepted':False},sort_keys=True));return 0
 need(all(type(v)is str and v for v in (args.repo,args.commit,args.root)),'hosted_arguments')
 result=execute(args);print(json.dumps({'schema':result['schema'],'status':result['status'],'security_accepted':False},sort_keys=True));return 0
if __name__=='__main__':raise SystemExit(main())
