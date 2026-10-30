"""Finite PUBLIC/OWNED source tests; no actual hosted, native or Docker call."""
from pathlib import Path
from types import ModuleType,SimpleNamespace
from hashlib import sha256
from unittest.mock import patch
from contextlib import contextmanager
import ast,builtins,copy,errno,gzip,inspect,io,json,os,stat,symtable,tarfile,tempfile,time,unittest,zipfile
HERE=Path(__file__).resolve().parent
REPO=next(p for p in HERE.parents if (p/'scripts/prepare_canonical_app_image.py').is_file())
def module(p):
 m=ModuleType('owned_probe_'+p.stem);m.__file__=str(p);exec(compile(p.read_bytes(),str(p),'exec'),m.__dict__);return m
Q=module(HERE/'controller.py');S=module(HERE/'seal_and_run.py');V=module(HERE/'probes.py');A=module(HERE/'archive_metadata.py')
N=json.loads((HERE/'native-expectation.json').read_bytes());I=json.loads((HERE/'intake.json').read_bytes());MAN=json.loads((HERE/'source-manifest.json').read_bytes())
OLD=module(REPO/'deploy/maintenance/app_gold2_smoke/seal_and_run.py')
P=module(REPO/'deploy/maintenance/app_r2_observe02/producer02_controller.py');BUILDER=module(REPO/'scripts/prepare_canonical_app_image.py')
F=module(HERE/'retained_inventory.py');POINTER=json.loads((HERE/'retained-inventory-pointer.json').read_bytes())
E=json.loads((HERE/'native-extra-expectation.json').read_bytes());COVER=module(HERE/'caller_payload.py');REQUIRED=json.loads((HERE/'required-caller-payload.json').read_bytes())
# Explicit OWNED protocol fixture: it cannot satisfy the real member SHA pin.
RET={'schema':'image_acl_archive_inventory/1','image_id':I['control']['image_id'],'source_commit':I['control']['source_commit'],'sbom_sha256':I['sbom']['sha256'],'scope':'all_regular_ELF_in_authenticated_flat_image_TAR_without_path_exclusions','inspected_code_executed':False,'archive_extracted':False,'whole_image_acl_closed':False,'security_accepted':False,'inventory':{'runtime_paths_excluded':[],'elf_files':[{'path':'/usr/bin/OWNED-%03d'%n}for n in range(835)]}}
def signature(raw,name):
 n=next(n for n in ast.parse(raw).body if isinstance(n,(ast.FunctionDef,ast.ClassDef))and n.name==name);return ast.dump(n,include_attributes=False)
def actual_native_fixture():
 value={'aliases_verified':28,'packages':[],'bytes_hashed':0};omit=N['distribution_filtered_absences'][0]['original_deb_file']['path']
 for e in N['packages']:
  positive=[r for r in e['expected_files']if r['path']!=omit];value['packages'].append({**copy.deepcopy(e),'actual_files':copy.deepcopy(positive)});value['bytes_hashed']+=sum(r['bytes']for r in positive)
 return value
class Tests(unittest.TestCase):
 def test_default_unarmed_zero_host_or_source_operation(self):
  with patch.object(Q,'hosted',side_effect=AssertionError('OWNED_NO_HOST')):self.assertEqual(Q.main(['--root','/OWNED_NOT_OPENED']),0)
  with patch.object(S,'host',side_effect=AssertionError('OWNED_NO_HOST')):self.assertEqual(S.main([]),0)
  self.assertFalse(S.EXECUTION_ARMED)
 def test_exact_current_scope_source_DAG_and_authoritative_manifest_pin(self):
  Q.validate_inputs(I,MAN);self.assertEqual(len(S.SOURCES),36);self.assertEqual(I['control']['run_id'],40844680297);self.assertEqual(I['control']['image_artifact_id'],12885065260)
  for name,pin in S.SOURCES.items():
   p=HERE/name if S.SOURCE_PATHS[name].startswith('deploy/maintenance/app_current2389_probe/')else REPO/S.SOURCE_PATHS[name];raw=p.read_bytes()
   with self.subTest(name=name):self.assertEqual((len(raw),sha256(raw).hexdigest()),pin);self.assertEqual(stat.S_IMODE(p.stat().st_mode),0o644)
  self.assertEqual(Q.PROBE_PINS['native-expectation.json'],(52714,'65f30c3e6789e081ff4b00f19d575f753d2eaa64fbb0991aeff9ccce815de53b'))
  for n,p in Q.PROBE_PINS.items():self.assertEqual(S.SOURCES[n],p)
 def test_mature_custody_literal_bodies_unchanged(self):
  old=(REPO/'deploy/maintenance/app_gold2_smoke/seal_and_run.py').read_bytes();new=(HERE/'seal_and_run.py').read_bytes()
  for name in('MetadataPass','Held','Saved','metadata_check','finalize','save','completion_save','closed_error'):
   with self.subTest(name=name):self.assertEqual(signature(new,name),signature(old,name))
 def test_original_counter_and_probe_mechanics_preserved(self):
  expected={'pax': '9108abd5503be521891e4dd86331321034282c2f9e83b1d9550df85169862c28', 'scan_layer': '0b8e9f9d67b5526bf2cd543604e9ca3d5b2f823f1e229930268773a080822565', 'Slice': '4aeba051f3ce8e2b7229596a8109faa2ade4f767a040d934aad9da67683d5076'}
  for name,digest in expected.items():self.assertEqual(sha256(signature((HERE/'archive_metadata.py').read_bytes(),name).encode()).hexdigest(),digest)
  source=(REPO/'deploy/maintenance/app_r2_observe02/producer02_controller.py').read_bytes()
  for name in('presence_args','public_probe_start'):self.assertEqual(signature((HERE/'probes.py').read_bytes(),name),signature(source,name))
 def test_current20_DEB_full_scope_and_real_gold2_versions(self):
  self.assertEqual(len(N['packages']),20);self.assertEqual(N['scope']['image_id'],I['control']['image_id']);self.assertEqual((N['native_file_count'],N['historical_native_file_count'],N['alias_count']),(259,260,28))
  rows={p['package']:p for p in N['packages']};self.assertEqual(rows['coreutils']['installed_version'],'9.7-3+gold2');self.assertEqual(rows['coreutils']['deb_sha256'],'dc169c5e7223af3b0001403400e4c6ae5be4b089a52ee50c6b3d7d4049dc5ed1');self.assertEqual(rows['tar']['installed_version'],'1.35+dfsg-5~trixie+gold2');self.assertEqual(sum(len(p['expected_files'])for p in rows.values()),260)
  paths={r['path']for p in rows.values()for r in p['expected_files']};self.assertTrue(all(a['resolved_native_target']in paths for a in N['aliases']))
 def test_changed_old_or_arbitrary_expectation_refuses_source_pin(self):
  raw=(HERE/'native-expectation.json').read_bytes()
  for changed in(raw+b' ',Q.encoded({**N,'scope':{**N['scope'],'image_id':'sha256:'+'f'*64}})):
   with self.assertRaisesRegex(RuntimeError,'source_pin'):Q.checked_document(changed,Q.PROBE_PINS['native-expectation.json'])
 def test_owned835_protocol_count_missing_unknown_duplicate_refuse(self):
  self.assertEqual(Q.inventory_gate(RET,I['control']['image_id'],I),RET);self.assertEqual(len(RET['inventory']['elf_files']),835)
  for mode in('empty','duplicate','image','source'):
   v=copy.deepcopy(RET)
   if mode=='empty':v['inventory']['elf_files']=[]
   if mode=='duplicate':v['inventory']['elf_files'][1]=copy.deepcopy(v['inventory']['elf_files'][0])
   if mode=='image':v['image_id']='unknown'
   if mode=='source':v['source_commit']='unknown'
   with self.subTest(mode=mode),self.assertRaises(RuntimeError):Q.inventory_gate(v,I['control']['image_id'],I)
 def test_retained_origin_unknown_or_fresh_relabel_refuses(self):
  for key,value in(('artifact_id',0),('fresh_inventory_executed_by_this_cohort',True)):
   changed=copy.deepcopy(I);changed['retained_inventory_origin'][key]=value
   with self.assertRaisesRegex(RuntimeError,'intake_shape'):Q.validate_inputs(changed,MAN)
 def test_exact_source_policy_is_not_generic_docs_absence_or_Root_approval(self):
  p=N['distribution_filtered_absences'][0];self.assertEqual(p['original_deb_file']['path'],'/usr/share/doc/util-linux/examples/getopt-example.bash');self.assertFalse(p['generic_documentation_waiver']);self.assertFalse(p['filesystem_ENOENT_measured']);self.assertFalse(p['current_flat_export_absence_measured']);self.assertNotIn('Root_approved',p);self.assertEqual(p['policy_origin_layer'],I['control']['rootfs']['Layers'][0]);self.assertEqual(p['image_id'],I['control']['image_id'])
 def test_native_report_complete_positives(self):V.native_complete(actual_native_fixture(),N)
 def test_native_report_alias_metadata_hash_and_missing_package_negatives(self):
  for mode in('aliases','metadata','hash','missing','duplicate','bytes'):
   v=actual_native_fixture()
   if mode=='aliases':v['aliases_verified']=27
   if mode=='metadata':v['packages'][0]['actual_files'][0]['mode']^=1
   if mode=='hash':v['packages'][0]['actual_files'][0]['sha256']='0'*64
   if mode=='missing':v['packages'].pop()
   if mode=='duplicate':v['packages'][1]=copy.deepcopy(v['packages'][0])
   if mode=='bytes':v['bytes_hashed']=True
   with self.subTest(mode=mode),self.assertRaisesRegex(RuntimeError,'presence_identity'):V.native_complete(v,N)
 def test_same_image_official_public_five_projection_and_duplicates(self):
  expected=json.loads((HERE/'public-default-five.json').read_bytes());cfg={'config':{'Env':[k+'='+v for k,v in expected['public_defaults'].items()]}}
  value=A.public_defaults(cfg,I['control'],expected);self.assertEqual(value['public_defaults'],expected['public_defaults']);self.assertTrue(value['derived_from_actual_authenticated_original_Config']);self.assertFalse(value['private_configuration_read'])
  for mode in('duplicate','missing','changed'):
   c=copy.deepcopy(cfg)
   if mode=='duplicate':c['config']['Env'].append(c['config']['Env'][0])
   if mode=='missing':c['config']['Env'].pop()
   if mode=='changed':c['config']['Env'][0]+='changed'
   with self.subTest(mode=mode),self.assertRaises(RuntimeError):A.public_defaults(c,I['control'],expected)
 def test_owned_layer_counter_actual_PAX_and_exact14_row_keys(self):
  stream=io.BytesIO()
  with tarfile.open(fileobj=stream,mode='w',format=tarfile.PAX_FORMAT)as t:
   d=tarfile.TarInfo('own');d.type=tarfile.DIRTYPE;t.addfile(d);f=tarfile.TarInfo('own/file');f.size=3;f.pax_headers={'SCHILY.xattr.user.fixture':'yes'};t.addfile(f,io.BytesIO(b'abc'))
  result=A.scan_layer(io.BytesIO(stream.getvalue()),end=time.monotonic()+120);self.assertEqual(result['entries'],2);self.assertEqual(result['directories'],1);self.assertGreater(result['xattr_bytes'],0);self.assertEqual(set({'layer_index':0,**result}),{'layer_index',*A.FIELDS})
 def test_owned_layer_counter_truncation_bad_PAX_and_original_deadline(self):
  for data in(b'bad',b'9 invalid\n'):
   with self.assertRaises(RuntimeError):A.scan_layer(io.BytesIO(data),end=time.monotonic()+120)
  with self.assertRaises(RuntimeError):A.scan_layer(io.BytesIO(bytes(1024)),end=time.monotonic()-1)
  with self.assertRaises(RuntimeError):A.pax(b'8 bad=x')
 def test_execute_contains_no_recensus_and_explicit_source_only_output(self):
  source=ast.get_source_segment((HERE/'controller.py').read_text(),next(n for n in ast.parse((HERE/'controller.py').read_bytes()).body if isinstance(n,ast.FunctionDef)and n.name=='execute'))
  self.assertNotIn('inventory_attempt(',source);self.assertNotIn('whole_image_inventory(',source);self.assertIn("'fresh_inventory_executed':False",source);self.assertIn('P.run_smoke(',source);self.assertIn('Q.native_payload(',source)
 def test_authentic_original_build_evidence_and_current_probe_schema(self):
  raw=(REPO/'deploy/maintenance/app_gold2_smoke/fixtures/public-evidence.zip').read_bytes();evidence=P.read_zip(io.BytesIO(raw),Q.EVIDENCE_NAMES);self.assertEqual(Q.admit_evidence(evidence,I,BUILDER),I['evidence_members']);self.assertEqual(set(V.VERIFIERS),{'verify_image_component_presence.py','verify_python_runtime.py'});self.assertEqual(V.EXPECTED_NATIVE_FILES,259)
 def test_extracted_helpers_have_no_undefined_global_names(self):
  allowed=set(dir(builtins))|{'__name__','__file__','__doc__','__package__','__builtins__'}
  for name in('probes.py','controller.py','archive_metadata.py','retained_inventory.py','caller_payload.py','seal_and_run.py','probe_native_payload.py','probe_native_extra.py'):
   table=symtable.symtable((HERE/name).read_text(),name,'exec');known=allowed|{s.get_name()for s in table.get_symbols()if s.is_assigned()or s.is_imported()or s.is_namespace()};missing=[]
   def walk(t):
    for child in t.get_children():
     missing.extend(s.get_name()for s in child.get_symbols()if s.is_global()and s.is_referenced()and s.get_name()not in known);walk(child)
   walk(table)
   with self.subTest(name=name):self.assertEqual(sorted(set(missing)),[])
 def test_actual_public_probe_start_invokes_diagnostic_write_before_real_pipe_closes(self):
  for fail_write in(False,True):
   with self.subTest(fail_write=fail_write),tempfile.TemporaryDirectory()as directory:
    r1,w1=os.pipe();r2,w2=os.pipe();os.write(w1,b'{"OWNED":true}\n');os.close(w1);os.write(w2,b'OWNED diagnostics');os.close(w2)
    class InertChild:
     def __init__(self):self.stdout=os.fdopen(r1,'rb',buffering=0);self.stderr=os.fdopen(r2,'rb',buffering=0);self.returncode=None
     def wait(self,timeout):self.returncode=0;return 0
    child=InertChild();written=[];closed=[]
    def emit(path,raw):
     self.assertFalse(child.stdout.closed);self.assertFalse(child.stderr.closed);written.append(json.loads(raw))
     if fail_write:raise OSError(errno.EIO,'OWNED writer')
     with open(path,'xb')as stream:stream.write(raw)
    root=Path(directory);(root/'reports').mkdir();ctx=SimpleNamespace(root=root,end=time.monotonic()+30,check=lambda:None,C=SimpleNamespace(write=emit,cleanup_owned_child=lambda c:self.assertEqual(c.returncode,0)));old=SimpleNamespace(DOCKER=['/usr/bin/docker','--host=unix:///var/run/docker.sock'])
    with patch.object(V.subprocess,'Popen',return_value=child):
     if fail_write:
      with self.assertRaises(OSError):V.public_probe_start(ctx,old,'a'*64,'verify_python_runtime.py')
     else:self.assertEqual(V.public_probe_start(ctx,old,'a'*64,'verify_python_runtime.py'),b'{"OWNED":true}\n')
    self.assertEqual(len(written),1);self.assertTrue(written[0]['capture_before_collector_physical_closes']);self.assertEqual(written[0]['stderr_text'],'OWNED diagnostics')
    for f in(r1,r2):
     with self.assertRaises(OSError)as caught:os.fstat(f)
     self.assertEqual(caught.exception.errno,errno.EBADF)
 def test_actual_admit_app_owned_zip_decode_and_metadata_before_load(self):
  with tempfile.TemporaryDirectory()as directory:
   root=Path(directory);(root/'source').mkdir();(root/'reports').mkdir();default=(HERE/'public-default-five.json').read_bytes();(root/'source/public-default-five.json').write_bytes(default)
   payload=b'OWNED archive bytes only';gz=gzip.compress(payload,mtime=0);archive=io.BytesIO()
   with zipfile.ZipFile(archive,'w')as z:z.writestr('image-export.json',b'{"OWNED":true}');z.writestr('candidate-image.tar.gz',gz)
   raw=archive.getvalue();control={**I['control'],'image_zip_bytes':len(raw),'image_zip_sha256':sha256(raw).hexdigest()};events=[];fds=[]
   @contextmanager
   def pinned(path,maximum):
    f=open(path,'rb');number=f.fileno();fds.append(number)
    try:yield f,os.fstat(number).st_size
    finally:
     f.close()
     with self.assertRaises(OSError)as caught:os.fstat(number)
     self.assertEqual(caught.exception.errno,errno.EBADF)
   def digest(stream,size):return sha256(stream.read(size+1)).hexdigest()
   def decode(stream,size,path,maximum):
    data=gzip.decompress(stream.read(size+1));self.assertEqual(data,payload);path.write_bytes(data)
   def metadata(ctx,stream,size,config,proof,actual_control,defaults):
    events.append('actual_archive_metadata_hook');self.assertEqual(os.pread(stream.fileno(),size+1,0),payload);return{'OWNED':True},{'returncode':0,'security_accepted':False,'result':{'OWNED':True}}
   def load(ctx,stream,image,config,layers,identities):self.assertIn('actual_archive_metadata_hook',events);events.append('owned_load_seam');return image,{'OWNED':True}
   ctx=SimpleNamespace(root=root,stack=__import__('contextlib').ExitStack(),checks=[],modules={'archive_metadata.py':SimpleNamespace(collect=metadata)},check=lambda:None,capacity=lambda n:None,write=lambda p,v:p.write_bytes(Q.encoded(v)),download=lambda i,p,s,h:p.write_bytes(raw),P=SimpleNamespace(decode_gzip=decode),C=SimpleNamespace(pinned=pinned,stream_digest=digest))
   old=SimpleNamespace(sealed=lambda stack,path,pin,maximum:(path.read_bytes(),lambda:None),load_exact=load)
   source=SimpleNamespace(export_record=lambda value,c,e:{'bytes':len(gz),'sha256':sha256(gz).hexdigest(),'uncompressed_bytes':len(payload)},validate_archive=lambda stream,size,ctx,c:({'config':{}},{'source_identities':{c['image_id']:{'OWNED':True}}}))
   try:self.assertEqual(Q.admit_app(ctx,old,control,{},source),control['image_id'])
   finally:ctx.stack.close()
   self.assertEqual(events,['actual_archive_metadata_hook','owned_load_seam']);self.assertEqual(len(fds),3)
 def test_actual_main_collect_after_fileloop_requires_current_gold2(self):
  native=module(HERE/'probe_native_payload.py')
  class AfterLoopReached(Exception):pass
  class OwnedAbsent:
   def __init__(self,path):pass
   def observation(self,root):return {'OWNED':True}
   def check(self):pass
   def close(self):pass
   def named(self):pass
   def cleanup(self):pass
  for version in('9.7-3+gold2','9.7-3+gold1'):
   value=copy.deepcopy(N);next(p for p in value['packages']if p['package']=='coreutils')['installed_version']=version;seen=[];reads=[];original_need=native.need
   def file(path,spec,held,end,total):reads.append(spec);total[0]+=spec['bytes'];return dict(spec)
   def checked(ok,code):
    original_need(ok,code)
    frame=inspect.currentframe().f_back
    if frame.f_code is native.observe.__code__ and code=='manifest_package'and'coreutils'in frame.f_locals:
     seen.append((frame.f_locals['count'],frame.f_locals['historical_count'],len(frame.f_locals['packages'])));raise AfterLoopReached()
   with self.subTest(version=version),patch.object(native,'file_hold',side_effect=file),patch.object(native,'Absent',OwnedAbsent),patch.object(native,'need',new=checked):
    if version.endswith('gold2'):
     with self.assertRaises(AfterLoopReached):native.observe(Q.encoded(value),image=I['control']['image_id'],commit=I['control']['source_commit'],verifier='0'*64,root=Path('/OWNED_NOT_OPENED'))
     self.assertEqual(seen,[(259,260,20)])
    else:
     with self.assertRaisesRegex(ValueError,'manifest_package'):native.observe(Q.encoded(value),image=I['control']['image_id'],commit=I['control']['source_commit'],verifier='0'*64,root=Path('/OWNED_NOT_OPENED'))
     self.assertEqual(seen,[])
    self.assertEqual(len(reads),260)
 def test_required12_preflight_across_both_source_bound_manifests(self):
  result=COVER.check(N,E,REQUIRED);self.assertEqual(len(result['rows']),12);self.assertFalse(result['complete_semantic_caller_inventory_claimed'])
  for name in('sed','passwd','libattr1'):
   extra=copy.deepcopy(E);extra['packages']=[p for p in extra['packages']if p['package']!=name]
   with self.subTest(name=name),self.assertRaises(RuntimeError):COVER.check(N,extra,REQUIRED)
  extra=copy.deepcopy(E);next(f for p in extra['packages']for f in p['expected_files']if f['path']=='/usr/bin/sed')['sha256']='0'*64
  with self.assertRaises(RuntimeError):COVER.check(N,extra,REQUIRED)
 def test_extra_three_exact_policy_and_expected_native_body_shape(self):
  self.assertEqual((len(E['packages']),E['native_file_count'],E['historical_native_file_count'],E['alias_count']),(3,25,26,2));self.assertEqual({p['package']for p in E['packages']},{'sed','passwd','libattr1'});self.assertEqual(E['distribution_filtered_absences'][0]['original_deb_file']['path'],'/usr/share/doc/passwd/examples/passwd.expire.cron');self.assertNotEqual(E['distribution_filtered_absences'][0]['original_deb_file']['path'],N['distribution_filtered_absences'][0]['original_deb_file']['path'])
  native=module(HERE/'probe_native_extra.py');self.assertEqual(native.EXTRA_POLICY_FILE,{'path':'/etc/xattr.conf','bytes':681,'sha256':'0fc794a9826011c88b118c5ff4e30dfcbebd73518e64b0cda7aaec3ad7e578bd','mode':0o100644,'uid':0,'gid':0})
 def test_retained_provider_and_owned_zip_member_boundary(self):
  v={'id':POINTER['artifact_id'],'name':POINTER['artifact_name'],'size_in_bytes':POINTER['archive_bytes'],'digest':'sha256:'+POINTER['archive_sha256'],'expired':False,'workflow_run':{'id':POINTER['run_id'],'head_sha':POINTER['head_sha'],'head_branch':'main'}};F.provider(v,POINTER)
  for key,value in(('expired',True),('digest','sha256:'+'0'*64),('id',0)):
   bad=copy.deepcopy(v);bad[key]=value
   with self.subTest(key=key),self.assertRaises(RuntimeError):F.provider(bad,POINTER)
  payload=b'OWNED retained body, no real image proof';stream=io.BytesIO()
  with zipfile.ZipFile(stream,'w')as z:z.writestr(POINTER['member'],payload)
  raw=stream.getvalue();p={**POINTER,'archive_bytes':len(raw),'archive_sha256':sha256(raw).hexdigest(),'member_bytes':len(payload),'member_sha256':sha256(payload).hexdigest()};self.assertEqual(F.original_member(raw,p),payload)
  with self.assertRaises(RuntimeError):F.original_member(raw,POINTER)
 def test_actual_retained_collect_owned_download_protocol_and_original_close(self):
  payload=b'OWNED member, no actual image proof';stream=io.BytesIO()
  with zipfile.ZipFile(stream,'w')as z:z.writestr(POINTER['member'],payload)
  raw=stream.getvalue();p={**POINTER,'archive_bytes':len(raw),'archive_sha256':sha256(raw).hexdigest(),'member_bytes':len(payload),'member_sha256':sha256(payload).hexdigest()};pointer=Q.encoded(p)
  metadata={'id':p['artifact_id'],'name':p['artifact_name'],'size_in_bytes':len(raw),'digest':'sha256:'+p['archive_sha256'],'expired':False,'workflow_run':{'id':p['run_id'],'head_sha':p['head_sha'],'head_branch':p['head_branch']}}
  with tempfile.TemporaryDirectory()as directory:
   root=Path(directory);(root/'reports').mkdir();calls=[]
   def command(argv,**kwargs):self.assertTrue(kwargs['auth']);self.assertEqual(kwargs['maximum'],32768);calls.append('provider');return Q.encoded(metadata)
   @contextmanager
   def pinned(path,maximum):
    f=open(path,'rb');number=f.fileno()
    try:yield f,os.fstat(number).st_size
    finally:
     f.close()
     with self.assertRaises(OSError)as caught:os.fstat(number)
     self.assertEqual(caught.exception.errno,errno.EBADF)
   ctx=SimpleNamespace(root=root,command=command,write=lambda path,v:path.write_bytes(Q.encoded(v)),download=lambda i,path,size,digest:path.write_bytes(raw),C=SimpleNamespace(pinned=pinned),check=lambda:None)
   self.assertEqual(F.collect(ctx,SimpleNamespace(document=Q.document),pointer,(len(pointer),sha256(pointer).hexdigest())),payload);self.assertEqual(calls,['provider'])
 def test_actual_metadata_collect_scans37_owned_layers_then_rejects_forged_archive(self):
  layer=io.BytesIO()
  with tarfile.open(fileobj=layer,mode='w')as t:
   f=tarfile.TarInfo('OWNED/file');f.size=1;t.addfile(f,io.BytesIO(b'x'))
  block=layer.getvalue();descriptors=[{'digest':'sha256:'+sha256(block+str(i).encode()).hexdigest(),'size':len(block),'mediaType':'application/vnd.oci.image.layer.v1.tar'}for i in range(37)];manifest=Q.encoded({'layers':descriptors});digest=sha256(manifest).hexdigest();data=bytearray(manifest);members={'blobs/sha256/'+digest:(0,len(manifest))}
  for d in descriptors:members['blobs/sha256/'+d['digest'][7:]]=(len(data),len(block));data.extend(block)
  expected=json.loads((HERE/'public-default-five.json').read_bytes());config={'config':{'Env':[k+'='+v for k,v in expected['public_defaults'].items()]}};count=[];original=A.scan_layer
  def counted(stream,**kwargs):result=original(stream,**kwargs);count.append(result);return result
  with tempfile.TemporaryFile()as f:
   f.write(data);f.flush();ctx=SimpleNamespace(end=time.monotonic()+120,T=SimpleNamespace(ArchiveReader=lambda *args:None,inventory=lambda reader:(members,None)),D=SimpleNamespace(MEDIA={'application/vnd.oci.image.layer.v1.tar':False}),check=lambda:None,C=SimpleNamespace(stream_digest=lambda source,size:sha256(source.read(size+1)).hexdigest()))
   # This OWNED archive cannot satisfy current476681728/5f98... identity.
   # The fixed final stream bound refuses after every genuine layer scan.
   with patch.object(A,'scan_layer',side_effect=counted),self.assertRaises(RuntimeError):A.collect(ctx,f,476681728,config,{'archive_sha256':'5f98cb3c408e8b58e8ed576ac8cf459e25cb409a87f4c559fed9e890ea708194','manifest_id':'sha256:'+digest},I['control'],expected)
   self.assertEqual(len(count),37);self.assertTrue(all(c['entries']==1 for c in count))
if __name__=='__main__':unittest.main(verbosity=2)
