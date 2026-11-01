"""Finite isolated CI repair qualification; default UNARMED, never deploys."""
from pathlib import Path
from hashlib import sha256,sha512
from collections import Counter
import argparse,base64,gzip,hashlib,io,json,os,re,selectors,shlex,shutil,signal,stat,subprocess,tarfile,time,traceback
from pathlib import PurePosixPath
from types import ModuleType
from copy import deepcopy
MAX_LOG=32*1024**2
ACTIVE_STAGE={'profile':None,'name':'entry'}
ALLOWED_REPOS={'https://github.com/isaacs/node-glob.git','https://github.com/isaacs/minimatch.git','https://github.com/rollup/plugins.git'}
def need(ok,code):
 if not ok:raise RuntimeError(code)
def stage(profile,name,state):
 need(state in('start','complete')and re.fullmatch('[a-z0-9-]{1,48}',name),'declared_stage')
 ACTIVE_STAGE.update(profile=profile,name=name);print(json.dumps({'event':'npm_qualification_stage','profile':profile,'name':name,'state':state}),flush=True)
def encode(v):return(json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
def read_json(path,maximum=1024*1024):
 path=Path(path);before=path.lstat();need(before.st_mode&0o170000==0o100000 and before.st_nlink==1 and 0<before.st_size<=maximum,'json_bound')
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
 try:
  initial=os.fstat(fd);need((initial.st_dev,initial.st_ino,initial.st_size)==(before.st_dev,before.st_ino,before.st_size),'json_changed');data=os.pread(fd,before.st_size+1,0);need(len(data)==before.st_size and path.lstat().st_ino==initial.st_ino and os.fstat(fd).st_size==before.st_size,'json_changed')
 finally:os.close(fd)
 return json.loads(data)
def digest(path):
 h=sha256()
 with Path(path).open('rb')as source:
  while block:=source.read(65536):h.update(block)
 return h.hexdigest()
def write(path,body):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 with path.open('xb')as out:out.write(body)
 return{'path':str(path),'bytes':len(body),'sha256':sha256(body).hexdigest()}
def environment(node,home):
 home.mkdir(parents=True,exist_ok=True)
 return{'PATH':str(node.parent)+':/usr/local/bin:/usr/bin:/bin','HOME':str(home),'LANG':'C.UTF-8','LC_ALL':'C.UTF-8','CI':'true','SHELL':'/bin/bash','COREPACK_ENABLE_PROJECT_SPEC':'1','COREPACK_DEFAULT_TO_LATEST':'0','GIT_CONFIG_GLOBAL':'/dev/null','GIT_CONFIG_NOSYSTEM':'1','GIT_TERMINAL_PROMPT':'0','npm_config_registry':'https://registry.npmjs.org/','npm_config_audit':'false','npm_config_fund':'false'}
def qualified_test_node(spec,phase,profile):
 if phase!='upstream'or profile not in('glob10','glob11'):return None
 ref=spec['maintainer_test_node'];need(ref['version']=='v22.23.4'and type(ref['binary_sha256'])is str and re.fullmatch('[a-f0-9]{64}',ref['binary_sha256']),'test_Node22_binding_required')
 cache=Path(os.environ.get('RUNNER_TOOL_CACHE',''));need(cache.is_absolute()and cache.is_dir(),'test_Node_toolcache');node=(cache/'node/22.23.4/x64/bin/node').resolve(strict=True);need(node.is_relative_to(cache.resolve()),'test_Node_toolcache_scope');need(node.stat().st_size==ref['binary_bytes'],'exact_test_Node22_binary_size');need(digest(node)==ref['binary_sha256'],'exact_test_Node22_binary');need(subprocess.check_output([node,'--version'],text=True,timeout=10).strip()==ref['version'],'test_Node22_version');return node
def test_environment(node,test_node,workspace,env):
 shim=workspace/'maintainer-test-bin';shim.mkdir();npm_cli=node.parent.parent/'lib/node_modules/npm/bin/npm-cli.js';body=('#!/bin/sh\nexec '+shlex.join([str(test_node),str(npm_cli)])+' "$@"\n').encode();write(shim/'npm',body);os.chmod(shim/'npm',0o755)
 return{**env,'PATH':str(shim)+':'+str(test_node.parent)+':'+env['PATH']}
def run(argv,cwd,out,env,seconds=1800):
 """Stream directly to bounded evidence, never accumulate command output."""
 out=Path(out);need(not out.exists(),'new_log_required');out.parent.mkdir(parents=True,exist_ok=True)
 start=time.monotonic();count=0;p=subprocess.Popen([str(v)for v in argv],cwd=cwd,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,start_new_session=True);sel=selectors.DefaultSelector();sel.register(p.stdout,selectors.EVENT_READ)
 completed=False
 try:
  with out.open('xb')as target:
   while sel.get_map():
    need(time.monotonic()-start<seconds,'command_timeout')
    for key,_ in sel.select(.5):
     part=os.read(key.fileobj.fileno(),65536)
     if not part:sel.unregister(key.fileobj);continue
     need(len(part)<=MAX_LOG-count,'command_output_bound');target.write(part);count+=len(part)
   remaining=seconds-(time.monotonic()-start);need(remaining>0,'command_timeout')
   try:rc=p.wait(timeout=remaining)
   except subprocess.TimeoutExpired:raise RuntimeError('command_timeout')from None
   need(time.monotonic()-start<seconds,'command_timeout');need(rc==0,'command_nonzero')
   # EOF and leader zero do not prove descendants closed: a child can close
   # stdout/stderr and continue in our group. Allow a bounded reap interval,
   # then refuse and clean up that original group rather than report success.
   group_end=min(time.monotonic()+1,start+seconds)
   while True:
    try:os.killpg(p.pid,0)
    except ProcessLookupError:break
    need(time.monotonic()<group_end,'command_group_survived_leader');time.sleep(.01)
   need(time.monotonic()-start<seconds,'command_timeout');completed=True
 finally:
  if not completed:
   # The group can outlive its leader; never decide child cleanup from poll().
   try:os.killpg(p.pid,signal.SIGTERM)
   except ProcessLookupError:pass
   try:p.wait(timeout=10)
   except subprocess.TimeoutExpired:pass
   try:os.killpg(p.pid,signal.SIGKILL)
   except ProcessLookupError:pass
   p.wait(timeout=10)
  p.stdout.close();sel.close()
 return{'argv':[str(v)for v in argv],'returncode':rc,'log':str(out),'bytes':count,'sha256':digest(out)}
def patch_source(kind,raw):
 text=raw.decode()
 if kind=='glob':
  old='if (this.signal?.aborted) cb()';need(text.count(old)==4,'glob_exact_four_sites');text=text.replace(old,'if (this.signal?.aborted) return cb()')
 elif kind=='minimatch':
  old='firstPhasePreProcess(globParts: string[][]) {';need(text.count(old)==1,'minimatch_first_phase');text=text.replace(old,old+'\n    const { braceExpandMax = 100_000 } = this.options')
  old="p2 === '..'\n";need(text.count(old)==1,'minimatch_exact_split_guard');text=text.replace(old,"p2 === '..' ||\n            globParts.length >= braceExpandMax\n")
  old='secondPhasePreProcess(globParts: string[][]): string[][] {';need(text.count(old)==1,'minimatch_second_phase');text=text.replace(old,old+'\n    if (globParts.length > 1000) return globParts')
  if 'braceExpandMax?: number'not in text:
   old='  optimizationLevel?: number';need(text.count(old)==1,'minimatch_option');text=text.replace(old,old+'\n  braceExpandMax?: number')
 else:
  need(text.count("import deepMerge from 'deepmerge';")==1 and text.count('deepMerge({}, defaults)')==1,'resolve_exact_known_clone');text=text.replace("import deepMerge from 'deepmerge';",'').replace('deepMerge({}, defaults)','structuredClone(defaults)')
 return text.encode()
def patch_glob10_type_source(raw):
 """Four erased TS assertions; the original published bin runtime is gated."""
 text=raw.decode();need(text.count('cmdArg.push(')==2 and text.count('cmdArg.unshift(')==2,'glob10_exact_type_assertion_sites')
 return text.replace('cmdArg.push(','cmdArg!.push(').replace('cmdArg.unshift(','cmdArg!.unshift(').encode()
MINIMATCH_GUARD_TEST=b'''import t from 'tap'
import { Minimatch } from '../src/index.js'

t.test('project security backport bounded compiler guards', t => {
  const mm = new Minimatch('a', { optimizationLevel: 0, braceExpandMax: 1 })
  t.same(mm.firstPhasePreProcess([['**', '..', 'a', 'b']]), [['**', '..', 'a', 'b']])
  const many = Array.from({ length: 1001 }, (_, i) => ['file' + i])
  t.equal(mm.secondPhasePreProcess(many), many)
  t.end()
})
'''
def dist_payloads(package,profile):
 """Bounded regular inventory; only frozen tshy's exact selflink omitted."""
 source=package/'dist'
 def identity(path):
  s=path.lstat();return(s.st_dev,s.st_ino,s.st_mode,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
 directories=[];originals=[];links_checked=[];files=[];omitted=[];count=0;total=0;pending=[(source,Path('.'))]
 while pending:
  path,relative=pending.pop();before=identity(path);need(stat.S_ISDIR(before[2]),'snapshot_directory_type');directories.append((path,before))
  children=[]
  with os.scandir(path)as entries:
   for entry in entries:count+=1;need(count<=4096,'snapshot_member_bound');children.append(entry)
  for entry in sorted(children,key=lambda e:e.name):
   leaf=Path(entry.path);rel=relative/entry.name;initial=identity(leaf)
   if rel==Path('node_modules'):
    need(stat.S_ISDIR(initial[2])and profile['kind']in('glob','minimatch'),'snapshot_selflink_directory')
    links=[]
    with os.scandir(leaf)as nested:
     for item in nested:need(len(links)<1,'snapshot_selflink_inventory');links.append(item)
    need(len(links)==1 and links[0].name==profile['name'],'snapshot_selflink_inventory');link=Path(links[0].path);old=identity(link)
    need(stat.S_ISLNK(old[2])and old[3]==1 and os.readlink(link)=='../..'and identity(link)==old and identity(leaf)==initial,'snapshot_exact_vendor_selflink');omitted.append({'path':str(rel/profile['name']),'target':'../..','purpose':'frozen_tshy_development_selflink_not_runtime_payload'});directories.append((leaf,initial));originals.append((link,old));links_checked.append(link);continue
   if stat.S_ISDIR(initial[2]):pending.append((leaf,rel));continue
   need(stat.S_ISREG(initial[2])and initial[3]==1 and 0<=initial[4]<=1024**2,'snapshot_regular_type_or_bound');need(initial[4]<=32*1024**2-total,'snapshot_total_bound')
   fd=os.open(leaf,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
   try:
    s=os.fstat(fd);need((s.st_dev,s.st_ino,s.st_mode,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)==initial,'snapshot_file_changed');body=os.pread(fd,initial[4]+1,0);need(len(body)==initial[4]and identity(leaf)==initial,'snapshot_file_changed');s=os.fstat(fd);need((s.st_dev,s.st_ino,s.st_mode,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)==initial,'snapshot_file_changed')
   finally:os.close(fd)
   total+=len(body);files.append((rel,body));originals.append((leaf,initial))
 need(files,'snapshot_empty_dist')
 for path,before in directories+originals:need(identity(path)==before,'snapshot_directory_or_file_changed')
 for path in links_checked:need(os.readlink(path)=='../..','snapshot_exact_vendor_selflink')
 return files,omitted
def dist_hashes(package,profile):
 files,_=dist_payloads(package,profile);return{str(relative):sha256(body).hexdigest()for relative,body in files}
def snapshot_original(package,profile):
 """Independent old package metadata plus regular runtime; never live links."""
 need(profile['name']in('glob','minimatch','@rollup/plugin-node-resolve'),'snapshot_declared_package');destination=package/'.security-original-package';need(not os.path.lexists(destination),'snapshot_destination_exists');files,omitted=dist_payloads(package,profile)
 def identity(path):
  s=path.lstat();return(s.st_dev,s.st_ino,s.st_mode,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
 meta=package/'package.json';before=identity(meta);need(stat.S_ISREG(before[2])and before[3]==1 and 0<before[4]<=1024**2,'snapshot_metadata_bound');fd=os.open(meta,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
 try:
  s=os.fstat(fd);need((s.st_dev,s.st_ino,s.st_mode,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)==before,'snapshot_metadata_changed');metadata=os.pread(fd,before[4]+1,0);need(len(metadata)==before[4]and identity(meta)==before,'snapshot_metadata_changed');s=os.fstat(fd);need((s.st_dev,s.st_ino,s.st_mode,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)==before,'snapshot_metadata_changed')
 finally:os.close(fd)
 info=json.loads(metadata);need(info['name']==profile['name']and info['version']==profile['version'],'snapshot_original_metadata')
 write(destination/'package.json',metadata)
 for relative,body in files:write(destination/'dist'/relative,body)
 need(tree(destination/'dist')=={str(relative):sha256(body).hexdigest()for relative,body in files},'snapshot_written_inventory')
 selfref=destination/'node_modules'/profile['name'];selfref.parent.mkdir(parents=True);target=os.path.relpath(destination,selfref.parent);os.symlink(target,selfref);need(selfref.resolve()==destination.resolve(),'snapshot_internal_self_reference')
 return{'directory':str(destination),'regular_files':len(files),'bytes':sum(len(body)for _,body in files),'original_metadata_sha256':sha256(metadata).hexdigest(),'omitted_exact_vendor_selflinks':omitted,'created_internal_self_reference':{'path':str(selfref.relative_to(destination)),'target':target,'resolves_only_saved_package':True},'regular_payload_sha256':{str(relative):sha256(body).hexdigest()for relative,body in files}}
def tree(root):return{str(p.relative_to(root)):digest(p)for p in sorted(root.rglob('*'))if p.is_file()}
def package_identity(key,row):
 name=row.get('name')or key.rsplit('node_modules/',1)[-1]
 return name,row.get('version'),row.get('integrity'),row.get('resolved')
def graph_gate(before,after,profiles):
 targets={(p['name'],p['version'])for p in profiles};forks={(p['name'],p['fork_version'])for p in profiles}
 def unchanged(doc):return Counter(package_identity(k,v)for k,v in doc['packages'].items()if k and (package_identity(k,v)[:2]not in targets|forks|{('deepmerge','4.3.1')}))
 old,new=unchanged(before),unchanged(after);delta={'removed':[list(k)+[v]for k,v in(old-new).items()],'added':[list(k)+[v]for k,v in(new-old).items()]}
 need(not delta['removed']and not delta['added'],'unexpected_non_target_version_integrity_or_multiplicity_delta')
 identities={package_identity(k,v)[:2]for k,v in after['packages'].items()if k}
 need(forks<=identities and not targets&identities and not any(n=='deepmerge'for n,_ in identities),'fork_graph_not_exact')
 return delta

def qualify_tar(path,profile,package):
 need(path.stat().st_size<=8*1024**2,'fork_tar_bound')
 with gzip.GzipFile(fileobj=io.BytesIO(path.read_bytes()))as source:raw=source.read(32*1024**2+1);need(len(raw)<=32*1024**2,'fork_expanded_bound')
 seen=set();canonical={};aliases={};directories=set();metadata=None
 with tarfile.open(fileobj=io.BytesIO(raw),mode='r:')as archive:
  for i,member in enumerate(archive,1):
   need(i<=4096 and member.name not in seen,'fork_physical_member');seen.add(member.name);parts=PurePosixPath(member.name);need(not parts.is_absolute()and '..'not in parts.parts and parts.parts[0]=='package','fork_member_path');relative='/'.join(parts.parts[1:])
   need('.security-original-package'not in parts.parts,'fork_original_snapshot_included')
   need(not(member.issym()or member.islnk()or member.isdev()),'fork_member_type')
   if member.isdir():
    need(relative not in directories and relative not in canonical,'fork_directory_alias');directories.add(relative);continue
   need(relative not in directories,'fork_cross_kind_alias')
   need(member.isfile()and 0<=member.size<=1024*1024,'fork_regular_bound')
   with archive.extractfile(member)as source:data=source.read(member.size+1)
   need(len(data)==member.size,'fork_member_body');aliases.setdefault(relative,[]).append(member.name)
   if relative in canonical:
    need(profile['kind']=='resolve'and relative=='dist/cjs/index.js'and aliases[relative]==['package/./dist/cjs/index.js','package/dist/cjs/index.js']and canonical[relative]==data==(package/'dist/cjs/index.js').read_bytes(),'fork_unapproved_alias')
   canonical[relative]=data
   if relative=='package.json':metadata=json.loads(data)
 need(metadata['name']==profile['name']and metadata['version']==profile['fork_version'],'fork_package_identity')
 need(any(Path(name).name.lower().startswith(('license','licence'))for name in canonical),'fork_license_missing')
 if profile['kind']=='resolve':need('deepmerge'not in metadata.get('dependencies',{}),'fork_deepmerge_dependency_present')
 return{'physical_members':len(seen),'regular_targets':len(canonical),'retained_exact_aliases':{k:v for k,v in aliases.items()if len(v)>1},'sha512':'sha512-'+base64.b64encode(sha512(path.read_bytes()).digest()).decode()}

def upstream(spec,profile,node,workspace,out,env,*,test_node=None):
 p=next(x for x in spec['profiles']if x['id']==profile);need(p['repo']in ALLOWED_REPOS and re.fullmatch('[a-f0-9]{40}',p['commit']),'upstream_identity')
 if p['kind']=='glob':need(test_node is not None,'test_Node22_binding_required')
 root=workspace/p['id'];root.mkdir();log=out/p['id'];log.mkdir(parents=True);records=[]
 def command(args,cwd=root,name='stage',timeout=1800,environment_override=None):
  stage(profile,name,'start');result=run(args,cwd,log/(f'{len(records):02}-{name}.log'),env if environment_override is None else environment_override,timeout);records.append(result);stage(profile,name,'complete');return result
 command(['git','init','.'],name='git-init');command(['git','remote','add','origin',p['repo']],name='git-remote');command(['git','fetch','--depth','1','origin',p['commit']],name='git-fetch');command(['git','checkout','--detach','FETCH_HEAD'],name='git-checkout')
 command(['git','diff','--exit-code'],name='clean-source');actual=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,env=env,text=True).strip();need(actual==p['commit'],'upstream_commit')
 source=root/p['source'];raw=source.read_bytes();need(len(raw)<=1024*1024,'upstream_source_bound')
 import hashlib
 need(hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==p['source_git_blob'],'upstream_source_blob')
 package=root/p['package_dir'];metadata=read_json(package/'package.json');need(metadata['name']==p['name']and metadata['version']==p['version'],'upstream_package')
 npm=[str(node),str(node.parent.parent/'lib/node_modules/npm/bin/npm-cli.js')]
 if p['kind']=='resolve':
  manager=node.parent.parent/'lib/node_modules/corepack/dist/corepack.js';manager_bins=workspace/'package-manager-bin';manager_bins.mkdir()
  command([node,manager,'enable','--install-directory',manager_bins,'pnpm'],name='isolated-pnpm-shims');env['PATH']=str(manager_bins)+':'+env['PATH']
  corepack=[str(node),str(manager),'pnpm']
  need(read_json(root/'package.json')['packageManager']==p['package_manager'],'pnpm_exact_manager');command(corepack+['install','--frozen-lockfile','--ignore-scripts'],name='maintainer-install',timeout=2400)
 else:command(npm+['ci','--ignore-scripts','--audit=false','--fund=false'],name='maintainer-install',timeout=2400)
 compatibility=None
 if p.get('type_only_compatibility'):
  r=p['type_only_compatibility'];leaf=root/r['source'];before=leaf.read_bytes();need(hashlib.sha1(b'blob '+str(len(before)).encode()+b'\0'+before).hexdigest()==r['source_git_blob'],'glob10_type_source_blob');after=patch_glob10_type_source(before);leaf.write_bytes(after);compatibility={'path':r['source'],'preimage_sha256':sha256(before).hexdigest(),'postimage_sha256':sha256(after).hexdigest(),'runtime_bin_must_equal_authenticated_publisher':True}
 # First reproduce the publisher runtime before applying any repair. This joins
 # pinned Git sources/build lock to Root-authenticated official tar preimages.
 if p['kind']=='resolve':command(corepack+['-r','--filter','@rollup/plugin-node-resolve...','run','build'],name='original-source-build',timeout=2400)
 else:command(npm+['run','prepare'],name='original-source-build',timeout=1800)
 for relative,expected in p['original_dist_preimages'].items():
  leaf=package/relative;need(leaf.stat().st_size==expected['bytes']and digest(leaf)==expected['sha256'],'original_git_build_not_equal_official_publisher')
 stage(profile,'original-dist-snapshot','start');snapshot=snapshot_original(package,p);stage(profile,'original-dist-snapshot','complete')
 patched=patch_source(p['kind'],raw);source.write_bytes(patched)
 regression=None
 if p['kind']=='minimatch':regression=write(package/'test/project-security-compiler-guards.ts',MINIMATCH_GUARD_TEST)
 metadata['version']=p['fork_version']
 if p['kind']=='resolve':need(metadata['dependencies'].pop('deepmerge')=='^4.2.2','deepmerge_single_removal');metadata['engines']['node']='^22.23.4 || ^24.22.1 || ^26.12.1'
 (package/'package.json').write_bytes(encode(metadata))
 if p['kind']=='resolve':
  command(corepack+['-r','--filter','@rollup/plugin-node-resolve...','run','build'],name='maintainer-build',timeout=2400);command(corepack+['--dir',str(package),'test'],name='maintainer-full-tests',timeout=2400)
 else:
  command(npm+['run','prepare'],name='maintainer-build',timeout=1800)
  if p['kind']=='glob':
   node24_tree=dist_hashes(package,p);test_env=test_environment(node,test_node,workspace,env);command([test_node,npm[1],'test','--','-c','-t0'],name='maintainer-full-tests',timeout=2400,environment_override=test_env);need(dist_hashes(package,p)==node24_tree,'test_hooks_changed_Node24_compiler_output')
  else:command(npm+['test'],name='maintainer-full-tests',timeout=2400)
 # Test-only lifecycle hooks may not silently alter reviewed source.
 need(source.read_bytes()==patched,'maintainer_lifecycle_changed_source')
 rebuilt=dist_hashes(package,p)
 command([node,Path(__file__).absolute().parent/spec['runtime_helper']['path'],package,p['kind']],name='actual-cjs-esm-security-regressions',timeout=120)
 # Preserve original license rather than silently omitting monorepo's root file.
 if p['kind']=='resolve'and not any(x.name.lower().startswith(('license','licence'))for x in package.iterdir()):shutil.copyfile(root/'LICENSE',package/'LICENSE')
 # Two clean compiler outputs and two npm pack bodies must be identical.
 before=rebuilt;packs=[]
 for trial in(1,2):
  if trial==2:
   shutil.rmtree(package/'dist')
   command(corepack+['--dir',str(package),'run','build']if p['kind']=='resolve'else npm+['run','prepare'],cwd=package,name='second-clean-build',timeout=1800);need(dist_hashes(package,p)==before,'compiler_output_not_deterministic')
  target=log/f'pack-{trial}';target.mkdir();command(npm+['pack','--ignore-scripts','--json','--pack-destination',str(target)],cwd=package,name='pack');items=list(target.glob('*.tgz'));need(len(items)==1,'single_package');packs.append(items[0])
 need(digest(packs[0])==digest(packs[1]),'npm_pack_not_deterministic')
 artifact=log/(p['id']+'.tgz');shutil.copyfile(packs[0],artifact);archive_proof=qualify_tar(artifact,p,package)
 return{'schema':'qualified_upstream_fork/1','profile':p['id'],'original_commit':p['commit'],'original_version':p['version'],'fork_version':p['fork_version'],'source_preimage_sha256':sha256(raw).hexdigest(),'source_postimage_sha256':sha256(patched).hexdigest(),'type_only_compatibility':compatibility,'bounded_maintainer_regression':regression,'maintainer_test_node_version':spec['maintainer_test_node']['version']if p['kind']=='glob'else spec['node_version'],'Node24_compiler_output_unchanged_through_test_hooks':True if p['kind']=='glob'else None,'original_runtime_snapshot':snapshot,'archive_proof':archive_proof,'full_upstream_tests_passed':True,'two_builds_and_packages_identical':True,'tar':{'name':artifact.name,'bytes':artifact.stat().st_size,'sha256':digest(artifact)},'records':records,'security_accepted':False}

def frontend(spec,repo,artifacts,node,out,env):
 front=repo/'frontend';original=(front/'package-lock.json').read_bytes();need(sha256(original).hexdigest()==spec['lock_sha256'],'project_lock_changed');baseline=read_json(front/'package-lock.json');records=[]
 npm=[str(node),str(node.parent.parent/'lib/node_modules/npm/bin/npm-cli.js')]
 def command(args,name,timeout=2400):
  stage('frontend',name,'start');result=run(args,front,out/(f'{len(records):02}-{name}.log'),env,timeout);records.append(result);stage('frontend',name,'complete')
 command(npm+['ci','--audit=false','--fund=false'],'baseline-frozen-install');build_env={**env,'FRONTEND_BUILD_OUT_DIR':str(out/'baseline-dist')};stage('frontend','baseline-build','start');records.append(run(npm+['run','build'],front,out/'baseline-build.log',build_env,2400));stage('frontend','baseline-build','complete');baseline_dist=tree(out/'baseline-dist')
 vendor=front/'vendor/security-backports';vendor.mkdir(parents=True);forkpaths={}
 for p in spec['profiles']:
  reports=list(artifacts.rglob(p['id']+'/RESULT.json'));need(len(reports)==1,'upstream_result_inventory');result=read_json(reports[0]);need(result['profile']==p['id']and result['original_commit']==p['commit']and result['fork_version']==p['fork_version']and result['full_upstream_tests_passed']is True and result['two_builds_and_packages_identical']is True,'upstream_success_required')
  need(result['tar']['name']==p['id']+'.tgz','fixed_upstream_tar_name');tar=reports[0].parent/result['tar']['name'];need(digest(tar)==result['tar']['sha256'],'upstream_tar');target=vendor/(p['id']+'.tgz');shutil.copyfile(tar,target);forkpaths[p['id']]='file:vendor/security-backports/'+target.name
 package=read_json(front/'package.json');overrides=package.setdefault('overrides',{})
 for parent,children in{'workbox-build@7.4.0':{'@rollup/plugin-node-resolve':forkpaths['resolve'],'glob':forkpaths['glob11']},'js-beautify@1.15.4':{'glob':forkpaths['glob10']},'glob@10.5.0-project.security.1':{'minimatch':forkpaths['mm9']},'glob@11.1.0-project.security.1':{'minimatch':forkpaths['mm10']},'@typescript-eslint/typescript-estree@8.46.1':{'minimatch':forkpaths['mm9']},'editorconfig@1.0.7':{'minimatch':forkpaths['mm9']}}.items():need(parent not in overrides,'existing_override_collision');overrides[parent]=children
 (front/'package.json').write_bytes(encode(package));command(npm+['install','--package-lock-only','--ignore-scripts','--audit=false','--fund=false'],'proposal-lock-generation');updated=read_json(front/'package-lock.json');delta=graph_gate(baseline,updated,spec['profiles']);write(out/'GRAPH-DELTA.json',encode(delta))
 # Fresh frozen npmci twice; no npm update or drift adoption between runs.
 installed=[]
 for trial in(1,2):
  command(npm+['ci','--audit=false','--fund=false'],f'candidate-install-{trial}')
  installed.append({k:package_identity(k,v)for k,v in read_json(front/'node_modules/.package-lock.json')['packages'].items()})
 need(installed[0]==installed[1],'fresh_installs_differ')
 command([node,'scripts/check-node-security-baseline.mjs'],'project-node-baseline');command(npm+['exec','--no','--','eslint','.'],'project-lint');command(npm+['exec','--no','--','vue-tsc','--noEmit','-p','tsconfig.app.json'],'project-types');command(npm+['run','test:unit:run'],'project-unit-tests',3600)
 candidate_env={**env,'FRONTEND_BUILD_OUT_DIR':str(out/'candidate-dist')};stage('frontend','candidate-build','start');records.append(run(npm+['run','build'],front,out/'candidate-build.log',candidate_env,2400));stage('frontend','candidate-build','complete');need(tree(out/'candidate-dist')==baseline_dist,'frontend_output_differs_from_baseline')
 # Query only name/version data. Forks are explicitly mapped back to their
 # authenticated publisher preimages, so prerelease/file specs cannot fake zero.
 logical=deepcopy(updated);by_fork={(p['name'],p['fork_version']):p for p in spec['profiles']};query_map=[]
 for key,row in logical['packages'].items():
  if not key:continue
  name=package_identity(key,row)[0];profile=by_fork.get((name,row.get('version')))
  if profile:
   query_map.append({'path':key,'actual_fork':row['version'],'publisher_preimage':profile['version']});row['version']=profile['version'];row.pop('resolved',None)
 helper_ref=spec['advisory_helper'];helper=Path(__file__).parent/helper_ref['path'];need(digest(helper)==helper_ref['sha256'],'advisory_helper_source');module=ModuleType('_official_names_versions_only');module.__file__=str(helper);exec(compile(helper.read_bytes(),str(helper),'exec'),module.__dict__)
 body,coverage=module.request_from_lock(encode(logical));need(not coverage['gaps'],'advisory_coverage_gap');write(out/'advisory-request-names-versions.json',body);started=time.time_ns();response=module.fetch(body);received=time.time_ns();response_ref=write(out/'advisory-raw-response.json',response)
 advisory=json.loads(response);allowed=set(spec['expected_patched_advisory_ids']);need(all(type(rows)is list and all(row.get('id')in allowed for row in rows)for rows in advisory.values()),'new_unreviewed_npm_advisory')
 write(out/'ADVISORY-PROVENANCE.json',encode({'actual_started_ns':started,'actual_received_ns':received,'query_uses_published_preimages_for_private_forks':True,'fork_mapping':query_map,'raw_response':response_ref,'raw_findings_not_suppressed':True,'security_accepted':False}))
 proposal=out/'proposal';proposal.mkdir();shutil.copyfile(front/'package.json',proposal/'package.json');shutil.copyfile(front/'package-lock.json',proposal/'package-lock.json');shutil.copytree(vendor,proposal/'vendor/security-backports')
 return{'schema':'qualified_frontend_source_proposal/1','two_fresh_npmci_passed':True,'non_target_graph_identities_preserved':True,'frontend_output_byte_identical_to_baseline':True,'upstream_and_project_tests_passed':True,'browser_matrix_still_required':True,'raw_advisory_baseline_count':3,'patched_forks_not_official_fixed_versions':True,'proposal':str(proposal),'records':records,'security_accepted':False}

def main(argv=None):
 parser=argparse.ArgumentParser();parser.add_argument('--execute',action='store_true');parser.add_argument('--spec',type=Path,default=Path(__file__).with_name('spec.UNARMED.json'));parser.add_argument('--spec-sha256');parser.add_argument('--self-sha256');parser.add_argument('--phase',choices=('upstream','frontend'));parser.add_argument('--profile');parser.add_argument('--repo',type=Path);parser.add_argument('--expected-project-commit');parser.add_argument('--workspace',type=Path);parser.add_argument('--out',type=Path);parser.add_argument('--artifacts',type=Path);a=parser.parse_args(argv)
 spec=read_json(a.spec)
 if not a.execute or spec.get('armed')is not True:print(json.dumps({'status':'UNARMED','Host_or_network_or_source_mutation':False}));return 0
 need(os.environ.get('GITHUB_ACTIONS')=='true'and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted'and os.geteuid()!=0,'disposable_nonroot_GitHub_runner_required');need(digest(Path(__file__))==a.self_sha256 and digest(a.spec)==a.spec_sha256,'Source_pins');need(a.workspace.is_absolute()and a.out.is_absolute()and not a.workspace.exists()and not a.out.exists(),'new_ci_namespace');need(a.repo.is_absolute()and re.fullmatch('[a-f0-9]{40}',a.expected_project_commit),'project_scope')
 node=Path(shutil.which('node')).resolve();need(digest(node)==spec['node_binary_sha256'],'exact_Node_binary');need(subprocess.check_output([node,'--version'],text=True).strip()==spec['node_version'],'Node_version');test_node=qualified_test_node(spec,a.phase,a.profile);need(subprocess.check_output(['git','rev-parse','HEAD'],cwd=a.repo,text=True).strip()==a.expected_project_commit,'project_commit')
 # All public authenticated prerequisites must be verified before any network.
 for key in('official_preimage_auth','legacy_exact_decision','advisory_helper','runtime_helper'):
  r=spec[key];p=a.spec.parent/r['path'];need(p.stat().st_size==r['bytes']and digest(p)==r['sha256'],'Root_source_auth')
 need(a.workspace.parent.is_dir()and a.out.parent.is_dir(),'ci_output_parents_required')
 need(a.phase in('upstream','frontend')and(a.phase!='upstream'or a.profile in {p['id']for p in spec['profiles']}),'declared_phase_profile')
 need(shutil.disk_usage(a.workspace.parent).free>=8*1024**3,'CI_free_space_reserve')
 a.workspace.mkdir(parents=True);a.out.mkdir(parents=True);env=environment(node,a.workspace/'home');result=None;error=None
 try:
  if a.phase=='upstream':result=upstream(spec,a.profile,node,a.workspace,a.out,env,test_node=test_node);destination=a.out/a.profile/'RESULT.json'
  else:result=frontend(spec,a.repo,a.artifacts,node,a.out,env);destination=a.out/'RESULT.json'
  write(destination,encode(result));print(json.dumps({'status':'COMPLETE','phase':a.phase,'result':str(destination),'security_accepted':False}));return 0
 except BaseException as exc:
  frames=[{'file':Path(f.filename).name[:96],'line':f.lineno,'function':f.name[:96]}for f in traceback.extract_tb(exc.__traceback__)[-8:]]
  error={'schema':'finite_npm_ci_refusal/1','status':'REFUSED_OR_UNKNOWN','phase':a.phase,'profile':a.profile,'stage':dict(ACTIVE_STAGE),'error_class':type(exc).__name__,'code':exc.args[0]if type(exc)is RuntimeError and len(exc.args)==1 and type(exc.args[0])is str else None,'Source_frames_no_locals_or_values':frames,'security_accepted':False};write(a.out/'REFUSAL.json',encode(error));print(json.dumps(error));return 1
if __name__=='__main__':raise SystemExit(main())
