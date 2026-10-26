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
 def test_native_reference_recipe_pins_and_shared_helpers_are_unchanged(self):
  self.assertEqual(set(M.RECIPE_NAMES),set(NATIVE02_RECIPE_PINS))
  for name,pin in NATIVE02_RECIPE_PINS.items():
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


if __name__=='__main__':unittest.main()
