"""Owned inert children/files and pure hosted contracts; no native build/actions."""
from contextlib import redirect_stdout
from hashlib import sha256
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
import ast,io,json,os,stat,sys,tempfile,unittest,zipfile
REPO=Path(__file__).resolve().parents[1]
HERE=REPO/'deploy/maintenance/acl_tar_descriptor_build'
TEST_ROOT=Path(__file__).resolve().parent
GENUINE_ROOT=os.geteuid()==os.getegid()==0
M=ModuleType('_owned_hosted_tar');M.__file__=str(HERE/'build_hosted.py')
exec(compile((HERE/'build_hosted.py').read_bytes(),M.__file__,'exec'),M.__dict__)

# Independent immutable Native02 reference fingerprints; no local-history dependency.
NATIVE02_RECIPE_PINS={'0001-tar-descriptor-acl-caller.patch': {'bytes': 26083,
                                          'sha256': '8fbdc8332e1a9dc131bb18423f1bc57ced307ae2d2589ec23a8afa5a639637ee'},
 'Dockerfile': {'bytes': 642, 'sha256': 'df6d216f1136aa4eeffc7bfa65ea8aa68ef76d50ea6e205518ac82e53475ace2'},
 'debian-local-changelog.patch': {'bytes': 617,
                                  'sha256': '1a3e1273bfc436fe8dab20d7c09a704ac187da3f1550e0251ea025963d6ddff0'},
 'ordinary_acl_fd_fixture.c': {'bytes': 4514,
                               'sha256': 'a0c7e9a44cd960058b2c906578cd2d224bf8b52d4d4ba6c64d4623aa825f2042'},
 'source-manifest.json': {'bytes': 1866, 'sha256': '3bb405d134275541b4ecdecdce9fb597a9ed1d88adcfdca43574532be3bad77d'},
 'sourcebuild.py': {'bytes': 33670, 'sha256': '1c545c0a01eeeea2d599bee2cf56ba91c094d82c62e6fbdab9a642c9a262a23a'},
 'verify_ordinary.py': {'bytes': 13120, 'sha256': '868809e04a23a8797f3b79b04cc22416ff2ddbbfd8abdafe902de41eb3a011ca'}}
REVIEWED_HOSTED03_SOURCEBUILD_PIN={'bytes': 33884, 'sha256': 'cb81a8923984881e427672d47928a597ed0d70f85ec3c8e5ce52d8202e28a5da'}
NATIVE02_HELPER_SOURCE_SHA256={'cleanup_child': '189a3b8840a8496a35d63fd09bd51d8399f2f6177c92644ec4655c9fd1a48581',
 'context_inventory': 'b0035122aebe6539155da1114c69e5614938589d2c18062486395a4d600cc2d9',
 'encoded': 'aef615adb295aff97b7a383001acf3b2d20b156450ef0244b21246f6c0fba3bb',
 'ident': 'b5f6f323ec29ca8f6e057cce56e35c6948b3b2a098255d3584dea2e3e63ce243',
 'new_dir': '996f30afbd4b8e7da97e251602a54c1aeaed0f04f8ba6abf3d00f2c0951e95e8',
 'parents_ok': '61c09f14b21546619af7db7806668c3caad55326276afb24eb677c92056f923d',
 'require': '014d2ed3b294b60ec6c360a5a8b0137fbed791304db5bb57d5cb591a0c85e216',
 'run': 'a1c51bcf5a38f67cdcc5bcb6740b94d4113363e03dffecd605d6703e2966cf6e',
 'save': '1ec81fb0933c910a37df43d2fea47c27c2c648ae5d4f28d7bd244b93e253059e',
 'validate_manifest': '3acf6696e1a27b2e1f432b2044f10c1aa8eaabaaad7df669b5a695e1522c3838'}

NATIVE02_RECIPE_FUNCTION_SOURCE_SHA256={'acl_library_binding': '4e32c2816cb0c6f606fd38a05bb42f457fd2d6470e13cee5fb57e9d8ffaabad8',
 'build': '2bc1e6af8f3b947266f1b0fee5fa862cea1a7d62ac0c776d95e0c95b7bfc2f1e',
 'build_vendor': 'a1d5e80e37018b618505192fda0d61dc4cfbf63abdbc4b67e70cdee45d7c0b61',
 'builder_context': '1b93a03d07823333dbeeed66e7139ccbc56f6a774af19daaa9d005a8f995ecca',
 'bytes_at': '30d2278d0749a62ba54af1109f92b8cc9b67dfab8a814fe1976cb3e2577adc5e',
 'cleanup_owned': 'f00df40cc7d10a7079ba2e742d10529c5ec3c9eec34c3e01f35a3471d926593b',
 'command': '6cf38eb68689dd5217abbb301af04b7494d3051d6959fab7f17d8ca49a097eb0',
 'configure_repository': '5ffb5ead0c3ecfe6fddaa338aa18a27c06c2ad42e8fa37a9607116608d62b19f',
 'copy_exact': '38ab22be838fd4d7f81f52a7c518acc6e326073892cb0d35ab1974c3c75031f5',
 'current_environment': '466a5f9e5dd5ea40c5ea05dd58dfa84e2487c26561146c5bb253c85ca94d67e8',
 'digest': '82deb322393c025eafff984d61248ca1e8b4d7a08539415d8ea89f6fb37c6723',
 'encoded': 'aef615adb295aff97b7a383001acf3b2d20b156450ef0244b21246f6c0fba3bb',
 'exported_symbols': '3ffaaefebc50d9b7a3e12d1042ebb37ba6e29d89eebadd7e47f39b181d68d08f',
 'family_outputs': 'c5989033eff69deeb742aaec0de31d478242695e71f2c1a25b41528b1f0d41d1',
 'forward': 'b54a4eec1887ce8c2e7fd33bc2d35988dbbf4c2916e10d733dd56411f5d07b53',
 'identity': 'd3ba0d56db3b4fc11165f3b434f51c9437065dcd6f303df5f1836eb657545355',
 'install_packages': 'a8f7bfb6094a05a1bb2420730ff6dd75a12cb6bcdc96ab23d39a879983a20f7e',
 'inventory': '08262b4f7e56077c72d9a6c160d7fac8fea2902ac5b60f9df7be9c94b1d10777',
 'main': '72a6894f07943b056ea5406e4f7891019bea7331c9a1911c26153092e91ccf1c',
 'metadata': '01f1e20e43db2999696a1b340a7979fd4c95d51a8142683b799bf73f7c86b64b',
 'owned_read': '8497ae6ccc9a018722eb74ecdbbe07069785aef74f37ccf3bef0d39d9aaa9e01',
 'package_fields': 'e3a064d8244239aae409fb20c54cc8d506bdf71fcf41c29505eda6cec0b96453',
 'publish': 'e13b6a77eef9709cd753de30051cc8fe17f28b2188d7fe254f43d4c727c9ac94',
 'regular_payload': 'b8040fe8c23237deabe4d71849396fa75f9ad41a032daf082a71faa051ceace7',
 'require': 'bdda49153807696b211bdcb0e3e08c0223f409ef1fa6d338b31f88dde6712fb1',
 'run': '948fd008cce230bfc0ca0d778afd0b5ace6f66e4aa1f9c721e2ec6692f4d210c',
 'tar_binary_contract': '78f58c0a8e378da555b9550a48beb3e888946475b5075a906e510b040e401222',
 'uid_idle': '531e22c07ab22ce4f81578b99a0708a115826f089ac79e9db470eca30ece2b60'}

class HostedTests(unittest.TestCase):
 def setUp(self):
  temp=tempfile.TemporaryDirectory(prefix='.owned-hosted-',dir=TEST_ROOT);self.addCleanup(temp.cleanup);self.root=Path(temp.name)
 def facts(self):
  return{'uid':1001,'euid':1001,'runner_environment':'github-hosted','machine':'x86_64','os_id':'ubuntu','os_version':'24.04',
   'cpus':4,'affinity_cpus':4,'mem_total_bytes':8*1024**3,'free_bytes':40*1024**3,'disk_bytes':80*1024**3}
 def stage_module(self):
  tree=ast.parse(M.STAGE);body=[n for n in tree.body if isinstance(n,(ast.Import,ast.ImportFrom,ast.FunctionDef))]
  module=ModuleType('_owned_stage_copy');exec(compile(ast.Module(body=body,type_ignores=[]),'<owned-stage>','exec'),module.__dict__);return module
 def close(self,held):
  for item in reversed(held):
   if item.fd is not None:item.close()
 def test_default_is_unarmed_before_environment_metadata_or_any_process(self):
  with patch.object(M,'execute_hosted',side_effect=AssertionError('actual execution denied')),patch.object(M.os,'open',side_effect=AssertionError('read denied')),redirect_stdout(io.StringIO())as out:
   self.assertEqual(M.main([]),0)
  self.assertEqual(json.loads(out.getvalue())['status'],'UNARMED')
 def test_host_contract_actual_facts_not_mocked_capacity_gate(self):
  self.assertEqual(M.validate_host(self.facts()),self.facts())
  for key,value in [('uid',0),('euid',0),('runner_environment','self-hosted'),('machine','aarch64'),('os_version','26.04'),('free_bytes',40*1024**3-1),('cpus',True)]:
   facts=self.facts();facts[key]=value
   with self.subTest(key=key),self.assertRaises(RuntimeError):M.validate_host(facts)
 def test_six_native_recipe_files_reviewed_install_delta_and_shared_helpers(self):
  self.assertEqual(set(M.RECIPE_NAMES),set(NATIVE02_RECIPE_PINS))
  for name,before_pin in NATIVE02_RECIPE_PINS.items():
   pin=REVIEWED_HOSTED03_SOURCEBUILD_PIN if name=='sourcebuild.py'else before_pin
   raw=(HERE/name).read_bytes();self.assertEqual({'bytes':len(raw),'sha256':sha256(raw).hexdigest()},pin,name)
   self.assertEqual(M.PINS[name],(pin['bytes'],pin['sha256']),name)
  source=(HERE/'build_hosted.py').read_text();tree=ast.parse(source)
  actual={n.name:sha256(ast.get_source_segment(source,n).encode()).hexdigest()for n in tree.body if isinstance(n,ast.FunctionDef)and n.name in NATIVE02_HELPER_SOURCE_SHA256}
  self.assertEqual(actual,NATIVE02_HELPER_SOURCE_SHA256)
  self.assertEqual(M.DOCKER_ROOT,Path('/var/lib/docker'));self.assertEqual(M.RESERVE,8*1024**3);self.assertEqual(M.DISK_GROWTH,32*1024**3)
 def test_recipe_hashes_and_approved_old_runtime_hashes_are_exact(self):
  for name in M.RECIPE_NAMES:
   raw=(HERE/name).read_bytes();self.assertEqual((len(raw),sha256(raw).hexdigest()),M.PINS[name])
  self.assertEqual(M.PINS['inputs/libacl1_2.4.0-1~trixie+gold1_amd64.deb'],(37076,'bbb12e190bfe19b876f73a669848ebd8d04293d95000babf51058c88879966b1'))
  self.assertEqual(M.PINS['inputs/tar_1.35+dfsg-5~trixie+gold1_amd64.deb'],(814984,'b6a5715adc801680c94ab653641668d03f429dddc1efa822015b6395482195c4'))
 def test_one_readonly_public_mount_and_same_real_cgroup_limits_caps(self):
  args=M.create_argv('a'*32,Path('/OWNED-PUBLIC-CONTEXT'));mounts=[args[i+1]for i,v in enumerate(args)if v=='--mount']
  self.assertEqual(mounts,['type=bind,src=/OWNED-PUBLIC-CONTEXT,dst=/public-context,readonly'])
  self.assertEqual(args[args.index('--memory')+1],str(3*1024**3));self.assertEqual(args[args.index('--cpus')+1],'2');self.assertEqual(args[args.index('--pids-limit')+1],'256')
  self.assertEqual(set(args[i+1]for i,v in enumerate(args)if v=='--cap-add'),{'CHOWN','DAC_OVERRIDE','FOWNER','FSETID','SETGID','SETUID','SYS_CHROOT'})
 @unittest.skipUnless(GENUINE_ROOT,'requires genuine kernel UID0/GID0; no identity masking')
 def test_real_root_copy_creates_real_root0_target_and_exact_bytes(self):
  stage=self.stage_module();source=self.root/'public';target=self.root/'copy';source.write_bytes(b'OWNED PUBLIC SOURCE');pin=(source.stat().st_size,sha256(source.read_bytes()).hexdigest())
  stage.copy_public(source,target,pin);row=stage.binding(target,pin);self.assertEqual(row['identity'][2:6],[stat.S_IFREG|0o600,0,0,1]);self.assertEqual(target.read_bytes(),source.read_bytes())
 @unittest.skipUnless(GENUINE_ROOT,'requires genuine kernel UID0/GID0; no identity masking')
 def test_real_public_copy_is_exclusive_and_bounded_before_extra_write(self):
  stage=self.stage_module();source=self.root/'public';target=self.root/'copy';source.write_bytes(b'PUBLIC');pin=(6,sha256(b'PUBLIC').hexdigest())
  target.write_bytes(b'KEEP')
  with self.assertRaises(FileExistsError):stage.copy_public(source,target,pin)
  self.assertEqual(target.read_bytes(),b'KEEP');target.unlink();real=os.read;changed=[]
  def read(fd,size):
   part=real(fd,size)
   if part and not changed:changed.append(True);source.write_bytes(b'PUBLIC-GROWN')
   return part
  with patch.object(stage.os,'read',read),self.assertRaises(RuntimeError):stage.copy_public(source,target,pin)
  self.assertLessEqual(target.stat().st_size,6)
 @unittest.skipUnless(GENUINE_ROOT,'requires genuine kernel UID0/GID0; no identity masking')
 def test_real_public_copy_physical_close_drift_never_passes(self):
  stage=self.stage_module();source=self.root/'public';target=self.root/'copy';source.write_bytes(b'PUBLIC');pin=(6,sha256(b'PUBLIC').hexdigest());real=os.close;changed=[]
  def close(fd):
   row=os.fstat(fd);same=(row.st_dev,row.st_ino)==(source.stat().st_dev,source.stat().st_ino);real(fd)
   if same and not changed:changed.append(True);source.chmod(0o400)
  with patch.object(stage.os,'close',close),self.assertRaises(RuntimeError):stage.copy_public(source,target,pin)
  self.assertTrue(changed)
 def test_real_owned_inert_child_remains_bounded_and_no_postreap_signal(self):
  env={'PATH':'/usr/bin:/bin','LC_ALL':'C.UTF-8'}
  with patch.object(M.os,'killpg',side_effect=AssertionError('postreap signal denied')):
   self.assertEqual(M.run([sys.executable,'-I','-S','-B','-c',"print('PUBLIC')"],env=env,deadline=M.time.monotonic()+3,maximum=128),b'PUBLIC\n')
   with self.assertRaisesRegex(RuntimeError,'command_failed'):M.run([sys.executable,'-I','-S','-B','-c','raise SystemExit(7)'],env=env,deadline=M.time.monotonic()+3,maximum=128)
 def test_large_sparse_default_case_requires_real_pass_not_skip_or_missing(self):
  for suffix in ('ok','skipped (resource)','FAILED','missing'):
   path=self.root/('vendor-'+suffix.split()[0]+'.log');path.write_bytes(('173. sparse05.at:21:  '+suffix+'\n').encode());held=[]
   try:
    if suffix=='ok':self.assertEqual(M.vendor_case_evidence(path,held)['actual_pass_lines'],1)
    else:
     with self.assertRaisesRegex(RuntimeError,'mandatory_large_sparse_case_unproven'):M.vendor_case_evidence(path,held)
   finally:self.close(held)
 def test_no_newline_final_case_pass_is_real_complete_stdout_not_synthetic_skip(self):
  path=self.root/'vendor.log';path.write_bytes(b'173. sparse05.at:21:  ok');held=[]
  try:self.assertEqual(M.vendor_case_evidence(path,held)['actual_pass_lines'],1)
  finally:self.close(held)
 def test_archive_only_two_exact_runtime_members_never_extracts_other_members(self):
  data=io.BytesIO();pins={'inputs/old-a.deb':(6,sha256(b'PUBLIC').hexdigest()),'inputs/old-b.deb':(4,sha256(b'DATA').hexdigest())}
  with zipfile.ZipFile(data,'w')as z:
   z.writestr('nested/old-a.deb',b'PUBLIC');z.writestr('nested/old-b.deb',b'DATA')
   for n in range(14):z.writestr('PUBLIC-UNUSED-'+str(n),b'UNUSED')
  raw=data.getvalue();context=self.root/'context';context.mkdir();(context/'inputs').mkdir();held=[]
  try:
   with patch.object(M,'PINS',pins),patch.object(M,'OLD_ZIP_PIN',(len(raw),sha256(raw).hexdigest())):M.extract_runtime_inputs(raw,context,held)
   self.assertEqual({str(p.relative_to(context))for p in context.rglob('*')},{'inputs','inputs/old-a.deb','inputs/old-b.deb'})
  finally:self.close(held)
 def test_closed_failure_never_serializes_unknown_exception_text(self):
  self.assertEqual(M.failure(RuntimeError('hosted_disk_capacity'))['reason_code'],'hosted_disk_capacity')
  self.assertNotIn('SECRET_UNKNOWN_OPERAND',json.dumps(M.failure(RuntimeError('SECRET_UNKNOWN_OPERAND'))))
 def test_no_vendor_skip_flags_or_native_storage_fallback_in_new_profile(self):
  source=(HERE/'build_hosted.py').read_text();recipe=(HERE/'sourcebuild.py').read_text()
  for value in ('TARTEST_SKIP_LARGE_FILES','nocheck','/srv/trading-bot/production-data/docker-data'):self.assertNotIn(value,source)
  self.assertIn("'DEB_BUILD_OPTIONS':'parallel=2'",recipe);self.assertIn("'default_vendor_package_build_exit_codes':{'acl':0,'tar':0}",recipe)
 @unittest.skipUnless(GENUINE_ROOT,'requires genuine kernel UID0/GID0; no identity masking')
 def test_all_twelve_real_host_exports_remain_bound_through_last_outer_source_close(self):
  output=self.root/'exports';output.mkdir();rows={}
  for name in M.OUTPUTS:
   path=output/name;raw=('OWNED PUBLIC OUTPUT '+name).encode();path.write_bytes(raw);path.chmod(0o644)
   rows[name]={'bytes':len(raw),'sha256':sha256(raw).hexdigest(),'identity':list(M.ident(path.lstat()))}
  source=self.root/'original-source';source.write_bytes(b'OWNED PUBLIC ORIGINAL SOURCE');source.chmod(0o600)
  held=[M.Held(source,65536)];M.retain_exported_outputs(output,{'outputs':rows},held)
  self.assertEqual(len(held),13);self.assertEqual({item.path.name for item in held[1:]},set(M.OUTPUTS))
  inode=(source.stat().st_dev,source.stat().st_ino);original=os.close;changed=[];victim=output/next(iter(M.OUTPUTS))
  def close(fd):
   info=os.fstat(fd);original(fd)
   if(info.st_dev,info.st_ino)==inode and not changed:changed.append(True);victim.write_bytes(b'OWNED LATE OUTPUT CHANGE')
  # Actual original-source close occurs after all12 export FDs closed; the
  # final named-frame checks must still reject the real changed output.
  with patch.object(M.os,'close',side_effect=close),self.assertRaisesRegex(RuntimeError,'public_closed_changed'):M.finalize_public_inputs(held)
  self.assertTrue(changed);self.assertTrue(all(item.fd is None for item in held))

 def recipe_module(self):
   module=ModuleType('_owned_tar03_recipe');module.__file__=str(HERE/'sourcebuild.py')
   exec(compile((HERE/'sourcebuild.py').read_bytes(),module.__file__,'exec'),module.__dict__);return module
 def test_candidate_tar_install_restores_all_strictly_validated_payload_roots(self):
   recipe=self.recipe_module();calls=[]
   with patch.object(recipe,'command',side_effect=lambda argv,**kw:calls.append((argv,kw))),patch.object(recipe,'metadata',return_value=b''):
    recipe.install_packages([Path('/OWNED-CANDIDATE.deb')],documentation=('tar',),complete_tar_payload=True)
   self.assertEqual(len(calls),1);argv,options=calls[0]
   roots=('/usr/share/doc/tar','/usr/share/locale','/usr/share/man','/usr/share/info')
   expected=['/usr/bin/dpkg']
   for root in roots:expected+=['--path-include='+root,'--path-include='+root+'/*']
   self.assertEqual(argv,expected+['--install','/OWNED-CANDIDATE.deb']);self.assertEqual(options,{'timeout':120})
   # Every required root and descendants is explicitly named; exclusions and
   # global dpkg configuration are not altered by this call.
   self.assertFalse(any('path-exclude' in arg for arg in argv))
 def test_sdk_and_old_runtime_keep_the_exact_previous_install_contract(self):
   recipe=self.recipe_module();calls=[]
   for docs in (('libacl1','libacl1-dev'),('libacl1',)):
    with patch.object(recipe,'command',side_effect=lambda argv,**kw:calls.append(argv)),patch.object(recipe,'metadata',return_value=b''):
     recipe.install_packages([Path('/OWNED-LIBACL.deb')],documentation=docs)
    expected=['/usr/bin/dpkg']
    for name in docs:expected+=['--path-include=/usr/share/doc/'+name,'--path-include=/usr/share/doc/'+name+'/*']
    self.assertEqual(calls[-1],expected+['--install','/OWNED-LIBACL.deb'])

 def test_full_payload_validator_and_exact_reviewed_install_only_delta(self):
  text=(HERE/'sourcebuild.py').read_text();tree=ast.parse(text);functions={n.name:n for n in tree.body if isinstance(n,ast.FunctionDef)}
  self.assertEqual(set(functions),set(NATIVE02_RECIPE_FUNCTION_SOURCE_SHA256))
  for name,node in functions.items():
   segment=ast.get_source_segment(text,node)
   if name=='install_packages':
    segment=segment.replace('def install_packages(paths,*,documentation,complete_tar_payload=False):','def install_packages(paths,*,documentation):')
    block=" if complete_tar_payload:\n  for root in ('/usr/share/locale','/usr/share/man','/usr/share/info'):\n   flags+=['--path-include='+root,'--path-include='+root+'/*']\n"
    self.assertEqual(segment.count(block),1);segment=segment.replace(block,'',1)
   elif name=='build':
    before="install_packages([Path(tar['path'])],documentation=('tar',))";after="install_packages([Path(tar['path'])],documentation=('tar',),complete_tar_payload=True)"
    self.assertEqual(segment.count(after),1);segment=segment.replace(after,before,1)
   self.assertEqual(sha256(segment.encode()).hexdigest(),NATIVE02_RECIPE_FUNCTION_SOURCE_SHA256[name],name)
  calls=[n for n in ast.walk(functions['build'])if isinstance(n,ast.Call)and isinstance(n.func,ast.Name)and n.func.id=='install_packages']
  enabled=[n for n in calls if any(k.arg=='complete_tar_payload'and isinstance(k.value,ast.Constant)and k.value.value is True for k in n.keywords)]
  self.assertEqual(len(calls),3);self.assertEqual(len(enabled),1);self.assertIn("tar['path']",ast.unparse(enabled[0]))


if __name__=='__main__':unittest.main()
