"""Public clock/FD/loader fixtures, no runtime or security authority."""
from contextlib import ExitStack
from copy import deepcopy
import errno,fcntl,hashlib,io,json,os,tarfile,time
from pathlib import Path
from types import ModuleType
import unittest
from unittest.mock import patch

ROOT=Path(__file__).absolute().parents[1]
def frame(s):return(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def code(path,pin=None):
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
 try:
  before=frame(os.fstat(fd));flags=(fcntl.fcntl(fd,fcntl.F_GETFL),fcntl.fcntl(fd,fcntl.F_GETFD));raw=os.pread(fd,131073,0)
  assert 0<len(raw)==before[6]<=131072
  if pin:assert(len(raw),hashlib.sha256(raw).hexdigest())==pin
  assert frame(os.fstat(fd))==before and frame(path.stat())==before and flags==(fcntl.fcntl(fd,fcntl.F_GETFL),fcntl.fcntl(fd,fcntl.F_GETFD))and os.lseek(fd,0,os.SEEK_CUR)==0
 finally:os.close(fd)
 try:os.fstat(fd)
 except OSError as error:assert error.errno==errno.EBADF
 else:raise AssertionError('Source close')
 assert frame(path.stat())==before
 value=ModuleType('owned_public_'+path.stem);value.__file__=str(path);exec(compile(raw,str(path),'exec'),value.__dict__);return value
P=code(ROOT/'scripts/market_sqlite_component_probe.py')
IO=code(ROOT/P.COHORT['IO'][0],P.COHORT['IO'][1:])

def tar(entries):
 out=io.BytesIO()
 with tarfile.open(fileobj=out,mode='w',format=tarfile.USTAR_FORMAT)as archive:
  for path,payload,kind in entries:
   item=tarfile.TarInfo(path);item.mode=0o644
   if kind=='file':item.size=len(payload);archive.addfile(item,io.BytesIO(payload))
   else:item.type=tarfile.LNKTYPE if kind=='hardlink'else tarfile.SYMTYPE;item.linkname=payload;archive.addfile(item)
 return out.getvalue()

class ProbeTests(unittest.TestCase):
 def setUp(self):
  import tempfile
  self.temp=tempfile.TemporaryDirectory(prefix='owned-sqlite-probe-',dir=ROOT.parent);self.addCleanup(self.temp.cleanup)
  self.root=Path(self.temp.name);self.root.chmod(0o700);(self.root/'reports').mkdir(mode=0o700)
  self.scope=IO.Scope(120);self.addCleanup(self.close_scope)
  self.modules,self.sources=P.private_modules(IO,self.scope,ROOT,self.scope.check)
 def close_scope(self):
  if not self.scope.finished and not self.scope.errors:self.scope.finish()
 def fixture(self):
  I=self.modules['I'];image='sha256:'+'a'*64;source='b'*40;diff='sha256:'+'c'*64
  ver='3.46.1-9+gold2';lib='/usr/lib/x86_64-linux-gnu/libsqlite3.so.0.8.6';elf=b'\x7fELF'+bytes(60)
  paths=[lib,'/proof/sqlite-hardlink'];status=('Package: libsqlite3-0\nStatus: install ok installed\nVersion: '+ver+'\nArchitecture: amd64\nSource: sqlite3 ('+ver+')\n\n').encode()
  entries=[('var/lib/dpkg/status',status,'file'),('var/lib/dpkg/info/libsqlite3-0:amd64.list',('\n'.join(paths)+'\n').encode(),'file'),(lib[1:],elf,'file'),
   ('usr/lib/x86_64-linux-gnu/libsqlite3.so.0','libsqlite3.so.0.8.6','symlink'),('proof/sqlite-hardlink',lib[1:],'hardlink'),
   *[(name+'/unowned',elf,'file')for name in('proc','sys','dev','proof')]]
  properties=[{'name':'aquasecurity:trivy:'+key,'value':value}for key,value in{'PkgType':'debian','SrcName':'sqlite3','SrcVersion':ver,'SrcRelease':'','LayerDiffID':diff}.items()]
  sbom=P.encoded({'components':[{'name':'libsqlite3-0','version':ver,'purl':'pkg:deb/debian/libsqlite3-0@'+ver+'?arch=amd64&distro=debian-13.7','properties':properties}]})
  return tar(entries),image,source,diff,sbom
 def produce(self):
  raw,image,source,diff,sbom=self.fixture();E,A,I,C=(self.modules[k]for k in('E','A','I','C'))
  keeper=C.ProofKeeper(self.root);C.PROOF_KEEPER=keeper;output=E.ExportFile(C,keeper,self.root)
  output.write(raw,self.scope.check);binding=output.freeze(self.scope.check,time.monotonic()+30)
  receipt={'schema':'same_image_never_started_export/1','container_id':'d'*64,'image_id':image,'source_commit':source,
   'before':{'UNIT_fixture_only':True},'after':{'UNIT_fixture_only':True},'archive':binding,'actual_export_client_returncode':0,
   'container_started':False,'private_mounts_used':False,'security_accepted':False}
  wire=P.encoded(receipt);receipt=json.loads(wire)
  try:
   with output.borrow(self.scope.check)as stream:
    result=A.collect(I,self.modules['T'],stream,len(raw),image_id=image,source_commit=source,sbom_raw=sbom,sbom_sha256=hashlib.sha256(sbom).hexdigest(),
     verifier_sha256=P.COHORT['A'][2],export_sha256=binding['sha256'],container_receipt=receipt,check=self.scope.check,end=time.monotonic()+30)
  finally:keeper.close()
  for fd in(output.original_writer,output.original_reader):
   with self.assertRaises(OSError)as caught:os.fstat(fd)
   self.assertEqual(caught.exception.errno,errno.EBADF)
  self.assertTrue(all(loan.closed for loan in output.loans));self.assertFalse(result['security_accepted'])
  return result,image,source,diff
 def scan(self,image,version='3.46.1-9+gold2'):
  return{'Metadata':{'ImageID':image},'Results':[{'Vulnerabilities':[{'VulnerabilityID':'CVE-2026-119586','PkgName':'libsqlite3-0','InstalledVersion':version}]}]}
 def layered(self,name='layered-image.tar'):
  D=self.modules['D'];first=tar([('ordinary-public-file',b'public fixture','file')]);empty=tar([])
  contents=[first,empty,empty];diffs=['sha256:'+hashlib.sha256(b).hexdigest()for b in contents]
  config={'architecture':'amd64','created':'2026-11-01T19:00:00Z','history':[],'os':'linux','rootfs':{'type':'layers','diff_ids':diffs},'config':{}}
  raw_config=D.encode(config);image='sha256:'+hashlib.sha256(raw_config).hexdigest();blob=lambda v:'blobs/sha256/'+hashlib.sha256(v).hexdigest()
  manifest=D.encode({'schemaVersion':2,'mediaType':D.OCI_MANIFEST,'config':{'mediaType':'application/vnd.oci.image.config.v1+json','digest':image,'size':len(raw_config)},
   'layers':[{'mediaType':'application/vnd.oci.image.layer.v1.tar','digest':diff,'size':len(body)}for diff,body in zip(diffs,contents)]})
  index=D.encode({'schemaVersion':2,'mediaType':D.OCI_INDEX,'manifests':[{'mediaType':D.OCI_MANIFEST,'digest':'sha256:'+hashlib.sha256(manifest).hexdigest(),'size':len(manifest)}]})
  entries={blob(raw_config):raw_config,blob(manifest):manifest,**{blob(v):v for v in contents},'index.json':index,'oci-layout':D.encode({'imageLayoutVersion':'1.0.0'}),
   'manifest.json':D.encode([{'Config':blob(raw_config),'Layers':[blob(v)for v in contents],'RepoTags':[]}])}
  body=tar([(name,value,'file')for name,value in entries.items()]);p=self.root/name;p.write_bytes(body);p.chmod(0o600)
  row=self.scope.open(p,(len(body),hashlib.sha256(body).hexdigest()),P.MAX_EXPORT)
  inspect={'Id':image,'RootFS':{'Type':'layers','Layers':diffs},'Config':{},'Comment':'','Author':'','DockerVersion':''}
  return row,image,diffs,inspect
 def test_real_DTL_physical_graph_keeps_duplicate_ordered_empty_DiffIDs(self):
  row,image,diffs,inspect=self.layered();proof=P.physical_archive(self.modules,row,image,diffs,inspect,self.scope.check,time.monotonic()+30)
  self.assertEqual(proof['rootfs_layers'],diffs);self.assertEqual(diffs[-1],diffs[-2]);self.assertTrue(proof['physical_graph_closed']);self.assertFalse(proof['security_accepted'])
  self.assertEqual(os.lseek(row.original_fd,0,os.SEEK_CUR),0)
 def test_real_DTL_refuses_reordered_dropped_DiffID_or_wrong_ConfigID(self):
  row,image,diffs,inspect=self.layered()
  for modified in([diffs[1],diffs[0],diffs[2]],diffs[:-1]):
   changed=deepcopy(inspect);changed['RootFS']['Layers']=modified
   with self.subTest(layers=modified),self.assertRaises(RuntimeError):P.physical_archive(self.modules,row,image,modified,changed,self.scope.check,time.monotonic()+30)
  with self.assertRaises(RuntimeError):P.physical_archive(self.modules,row,'sha256:'+'0'*64,diffs,inspect,self.scope.check,time.monotonic()+30)
  self.assertEqual(os.lseek(row.original_fd,0,os.SEEK_CUR),0)
 def test_strict_original_sealed_adapter_refuses_wrong_signature_alias_or_pin(self):
  source=self.root/'source';source.mkdir(mode=0o700);name='compose_security_os_candidate.py';p=source/name
  raw=(ROOT/P.COHORT['C'][0]).read_bytes();p.write_bytes(raw);p.chmod(0o600);pins={name:(len(raw),hashlib.sha256(raw).hexdigest())}
  with ExitStack()as stack:
   sealed=P.strict_sealed(IO,self.scope,self.root,stack,pins,self.scope.check)
   for args in [(ExitStack(),p,pins[name][1],131072),(stack,p,'0'*64,131072),(stack,p,pins[name][1],65536),(stack,source/'foreign.py',pins[name][1],131072)]:
    with self.assertRaises(RuntimeError):sealed(*args)
   actual,guard=sealed(stack,p,pins[name][1],131072);self.assertEqual(actual,raw);guard()
  self.scope.check();self.assertTrue(self.scope.rows[-1].closed);self.assertTrue(self.scope.rows[-1].physical_EBADF)
 def test_actual_export_file_receipt_wire_to_real_no_exclusion_collector(self):
  result,image,source,diff=self.produce();rows=result['inventory']['elf_files'];paths={r['path']for r in rows}
  self.assertEqual(len(rows),6);self.assertTrue({'/'+name+'/unowned'for name in('proc','sys','dev','proof')}<=paths)
  self.assertEqual(result['inventory']['runtime_paths_excluded'],[]);self.assertTrue(result['inventory']['hardlink_sets'])
  self.assertTrue(any(r['symlink_aliases']for r in rows));self.assertFalse(result['elf_metadata_inventory_complete'])
 def test_library_bindings_remain_unassessed_and_others_never_disappear(self):
  inventory,image,source,diff=self.produce();r=P.component_report(inventory,image=image,source_commit=source,layers=[diff],scan=self.scan(image))
  self.assertEqual(r['status'],'UNASSESSED');self.assertEqual(r['source_link_join'],'MISSING');self.assertEqual(len(r['package_ELF_candidates']),2)
  self.assertEqual(len(r['other_ELF_candidates_unassessed']),4);self.assertFalse(r['whole_image_component_absence_proven']);self.assertFalse(r['security_accepted'])
 def test_fake_link_success_never_becomes_authority(self):
  inventory,image,source,diff=self.produce()
  for evidence in ({'success':True},{'status':'PASS'},True):
   with self.subTest(evidence=evidence),self.assertRaisesRegex(RuntimeError,'source_link_evidence_not_implemented'):
    P.component_report(inventory,image=image,source_commit=source,layers=[diff],scan=self.scan(image),link_evidence=evidence)
 def test_image_source_layer_scan_version_and_owners_refuse_mismatches(self):
  inventory,image,source,diff=self.produce()
  cases=[('image',lambda x:x.update(image_id='sha256:'+'0'*64)),('source',lambda x:x.update(source_commit='0'*40)),
   ('owner',lambda x:x['inventory']['elf_files'][-1]['package_owners'].append(deepcopy(x['inventory']['elf_files'][-1]['package_owners'][0])))]
  owned=next(r for r in inventory['inventory']['elf_files']if r['package_owners'])
  for name,mutate in cases[:2]:
   changed=deepcopy(inventory);mutate(changed)
   with self.subTest(name=name),self.assertRaises(RuntimeError):P.component_report(changed,image=image,source_commit=source,layers=[diff],scan=self.scan(image))
  for mutate in(lambda r:r['package_owners'].append(deepcopy(r['package_owners'][0])),lambda r:r['package_owners'][0].update(sbom_source_binding_matches=False),lambda r:r['package_owners'][0]['sbom'].update(layer_diffid='sha256:'+'0'*64)):
   changed=deepcopy(inventory);r=next(r for r in changed['inventory']['elf_files']if r['path']==owned['path']);mutate(r)
   with self.assertRaises(RuntimeError):P.component_report(changed,image=image,source_commit=source,layers=[diff],scan=self.scan(image))
  with self.assertRaises(RuntimeError):P.component_report(inventory,image=image,source_commit=source,layers=[diff],scan=self.scan(image,'wrong'))
 def test_duplicate_scan_or_ELF_is_refused_and_missing_scan_stays_unknown(self):
  inventory,image,source,diff=self.produce();scan=self.scan(image);scan['Results'][0]['Vulnerabilities']*=2
  with self.assertRaises(RuntimeError):P.component_report(inventory,image=image,source_commit=source,layers=[diff],scan=scan)
  changed=deepcopy(inventory);changed['inventory']['elf_files'].append(deepcopy(changed['inventory']['elf_files'][0]))
  with self.assertRaises(RuntimeError):P.component_report(changed,image=image,source_commit=source,layers=[diff],scan=self.scan(image))
  r=P.component_report(inventory,image=image,source_commit=source,layers=[diff],scan={'Metadata':{'ImageID':image},'Results':[]})
  self.assertFalse(r['scanner_target_observed']);self.assertEqual(r['status'],'UNASSESSED')
 def test_genuine_constructor_refuses_actual_nonhosted_root_without_fake_UID(self):
  if os.geteuid()==1001:self.skipTest('negative Root-host test needs actual nonhosted UID')
  with ExitStack()as stack,self.assertRaisesRegex(RuntimeError,'hosted_ci_only'):self.modules['old'].Controller(self.root,stack)
 def test_budget_and_unqualified_entry_never_create_context_or_issue_tool(self):
  for value in(None,True,float('nan'),float('inf'),9):
   with self.subTest(value=value),self.assertRaises(RuntimeError):P.budget(value,now=10)
  b=P.budget(1810,now=10);self.assertEqual((b['healthy_end'],b['hard_end']),(1510,1810))
  with patch.object(P,'context',side_effect=AssertionError('no Context')):
   r=P.collect(None,None,None,None,None,None,None,None);self.assertEqual(r['status'],'UNQUALIFIED');self.assertFalse(r['collection_performed'])
   r=P.collect(None,None,None,None,None,None,None,None,normal_deadline=time.monotonic()+2000);self.assertEqual(r['status'],'UNQUALIFIED');self.assertFalse(r['collection_performed'])
 def test_strict_publication_is_exclusive_and_copies_no_unknown_path_or_mode(self):
  write=P.strict_write(IO,self.scope,self.root,self.scope.check);p=self.root/'reports/flat-export-created.json';write(p,b'{}\n')
  for name,mode,payload in [('unknown.json',0o600,b'{}'),('flat-export-receipt.json',0o644,b'{}'),('flat-export-receipt.json',0o600,b'')]:
   with self.assertRaises(RuntimeError):write(self.root/'reports'/name,payload,mode)
  other=IO.Scope(10);duplicate=P.strict_write(IO,other,self.root,other.check)
  with self.assertRaises(FileExistsError):duplicate(p,b'{}\n')
  self.assertTrue(other.errors)
 def test_source_alias_flag_and_offset_drift_latch_without_false_completion(self):
  path=self.root/'public-source.py';path.write_bytes(b'public fixture\n');path.chmod(0o600);pin=(15,hashlib.sha256(b'public fixture\n').hexdigest())
  row=self.scope.open(path,pin,128);os.lseek(row.original_fd,1,os.SEEK_SET)
  with self.assertRaises(RuntimeError):row.read(1,0)
  os.lseek(row.original_fd,0,os.SEEK_SET)
  with self.assertRaises(RuntimeError):self.scope.check()
  self.assertTrue(row.uncertain);self.assertFalse(row.closed)
  os.close(row.original_fd);row.original_fd=None
  for source in self.scope.rows:
   if source.fd is not None:
    try:os.close(source.fd)
    except OSError:pass
    source.fd=None

 def clock(self):
  folder=self.root/'source';folder.mkdir(mode=0o700);end=time.monotonic_ns()+6000*10**9
  raw=(ROOT/P.COHORT['IO'][0]).read_bytes();ip=P.write_operation_file(folder/'source_io.py',raw,IO,end)
  own_io=code(folder/'source_io.py',P.COHORT['IO'][1:]);raw=(ROOT/'scripts/market_sqlite_component_probe.py').read_bytes()
  pin=P.write_operation_file(folder/'market_sqlite_component_probe.py',raw,own_io,end)
  source=P.ClockSource(folder/'market_sqlite_component_probe.py',pin,own_io,ip)
  context={'root':str(self.root),'release_commit':'b'*40,'run_id':'123','run_attempt':'1','job':'candidate','role':'capture2-f79','driver_sha256':'c'*64,
   'boot_id':'12345678-1234-1234-1234-123456789abc','clock_source_sha256':pin['sha256'],'clock_source_full9':pin['full9'],
   'io_source_sha256':ip['sha256'],'io_source_full9':ip['full9']}
  started=time.monotonic_ns();pin=P.create_normal_operation(self.root,context,started,source)
  clock=P.NormalOperationClock(self.root/'normal-operation-clock.json',pin,context,source);return source,clock,pin
 def retire_clock(self,source,clock):
  clock.close();source.close();clock.final_check()
  for owner in(source,clock):
   self.assertTrue(owner.scope.finished)
   for row in owner.scope.rows:
    self.assertTrue(row.closed and row.physical_EBADF)
    with self.assertRaises(OSError)as error:os.fstat(row.original_fd)
    self.assertEqual(error.exception.errno,errno.EBADF)
 def test_clock_producer_reopen_nanoseconds_and_finality(self):
  source,clock,pin=self.clock();original=clock.original;context=deepcopy(clock.context)
  wire=P.clock_document(P.encoded(pin));self.assertEqual(wire,pin);self.assertEqual(clock.end-clock.started,6000*10**9)
  b=clock.probe_budget();self.assertEqual(b['hard_end_ns']-b['probe_started_ns'],1800*10**9)
  self.assertEqual(b['healthy_end_ns']-b['probe_started_ns'],1500*10**9);self.assertGreaterEqual(clock.end-b['hard_end_ns'],600*10**9)
  self.retire_clock(source,clock)
  other=P.ClockSource(source.path,source.pin,source.io,{'bytes':source.io_row.before[6],'sha256':P.COHORT['IO'][2],'full9':list(source.io_row.before)})
  reopened=P.NormalOperationClock(clock.path,wire,context,other);self.assertEqual(reopened.original,original);self.retire_clock(other,reopened)
  with self.assertRaises(BaseExceptionGroup)as error:P.create_normal_operation(self.root,context,time.monotonic_ns(),source)
  self.assertIsInstance(error.exception.exceptions[0],FileExistsError);self.assertEqual(P.clock_document(clock.path.read_bytes())['started_ns'],original[0])
 def test_exact_context_pin_original_deadline_and_legacy_denials(self):
  source,clock,pin=self.clock();b=clock.original
  for key,value in [('run_attempt','2'),('role','unknown'),('boot_id','bad'),('driver_sha256','0'*64),('clock_source_full9',[0]*9)]:
   changed=deepcopy(clock.context);changed[key]=value
   with self.subTest(key=key),self.assertRaises(RuntimeError):P.NormalOperationClock(clock.path,pin,changed,source)
  now=b[1]-2399*10**9
  with patch.object(P.time,'monotonic_ns',return_value=now),self.assertRaisesRegex(RuntimeError,'budget_missing'):clock.probe_budget()
  with self.assertRaisesRegex(RuntimeError,'legacy_deadline'):P.collect(clock,normal_deadline=b[1]/10**9)
  self.retire_clock(source,clock)
  for raw in (b'{"a":1,"a":2}',b'{"a":1.1}',b'{"a":NaN}'):
   with self.assertRaises(RuntimeError):P.clock_document(raw)
  for started in (True,0,-1,time.monotonic_ns()+10**12):
   with self.assertRaisesRegex(RuntimeError,'bootstrap'):P.create_normal_operation(self.root,{},started,source)
 def test_clock_flags_cannot_replace_physical_finality(self):
  source,clock,pin=self.clock();clock.closed=True
  with self.assertRaisesRegex(RuntimeError,'operation_retired'):clock.check()
  clock.closed=False
  with self.assertRaises(RuntimeError):clock.close()
  self.assertFalse(clock.scope.finished)
  for row in clock.scope.rows:
   if row.fd is not None:os.close(row.fd);row.fd=None
  source.close()
  with self.assertRaises(RuntimeError):clock.final_check()
  source,clock,pin=self.clock_in_other_root();self.retire_clock(source,clock);clock.closed=False
  with self.assertRaises(RuntimeError):clock.final_check()
 def clock_in_other_root(self):
  old=self.root;self.root=old/'other';self.root.mkdir(mode=0o700)
  try:return self.clock()
  finally:self.root=old
 def test_actual_normal_Held_archive_parser_and_pread_loan_preserve_cursor_flags_inode(self):
  normal=code(ROOT/'scripts/prepare_market_capture2_image.py');row,image,layers,inspect=self.layered();path=row.path
  held=normal.Held(path,maximum=P.MAX_EXPORT);fd=held.fd;before=frame(os.fstat(fd));flags=(fcntl.fcntl(fd,fcntl.F_GETFD),fcntl.fcntl(fd,fcntl.F_GETFL))
  try:
   self.assertEqual(normal.archive_configuration(fd,image,layers),image);offset=os.lseek(fd,0,os.SEEK_CUR)
   borrowed=P.OriginalInput(held,path,P.MAX_EXPORT,held.check)
   self.assertEqual(borrowed.offset,offset);self.assertEqual(borrowed.read(17,0),os.pread(fd,17,0))
   self.assertEqual(os.lseek(fd,0,os.SEEK_CUR),offset);self.assertEqual(frame(os.fstat(fd)),before)
   proof=P.physical_archive(self.modules,borrowed,image,layers,inspect,held.check,time.monotonic()+30);self.assertTrue(proof['physical_graph_closed'])
   self.assertEqual(os.lseek(fd,0,os.SEEK_CUR),offset);self.assertEqual((fcntl.fcntl(fd,fcntl.F_GETFD),fcntl.fcntl(fd,fcntl.F_GETFL)),flags)
   os.lseek(fd,offset+1,os.SEEK_SET)
   with self.assertRaises(RuntimeError):borrowed.check()
   os.lseek(fd,offset,os.SEEK_SET)
   with self.assertRaises(RuntimeError):borrowed.check()
  finally:held.close()
  with self.assertRaises(OSError)as error:os.fstat(fd)
  self.assertEqual(error.exception.errno,errno.EBADF)

 def test_genuine_UID1001_loader_and_collector_no_external_tools(self):
  if os.geteuid()!=1001:self.skipTest('requires genuine owned UID1001 public fixture')
  normal=code(ROOT/'scripts/prepare_market_capture2_image.py');row,image,layers,inspected=self.layered('candidate-image.tar')
  for name,_,_ in P.COHORT.values():
   p=self.root/'controller-source'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes((ROOT/name).read_bytes());p.chmod(0o600)
  flat,_,_,diff,sbom=self.fixture();sbom=sbom.replace(diff.encode(),layers[0].encode());source,clock,_=self.clock();held=[];reached=[];state=[]
  for name,raw in [('vulnerabilities.json',P.encoded(self.scan(image))),('sbom.cdx.json',sbom)]:
   p=self.root/'reports'/name;p.write_bytes(raw);p.chmod(0o600);held.append(normal.Held(p))
  held.append(normal.Held(row.path));original_modules=P.private_modules;original_context=P.context;cid='d'*64
  def modules(*args):
   values,owners=original_modules(*args);old,E=values['old'],values['E'];name='app-ci-flat-acl-'+old.owner()
   def cli(ctx,old,stage,**kw):
    reached.append(stage);ctx.check()
    if stage=='create':state.append(cid);return(cid+'\n').encode()
    if stage=='export':kw['output'].write(flat,ctx.check);return kw['output'].freeze(ctx.check,time.monotonic()+30)
    value={'id':cid,'name':'/'+name,'image':image,'config_image':image,'owner':old.owner(),'user':'0:0','entrypoint':['/bin/true'],'cmd':['--inventory-never-started'],
     'readonly':True,'privileged':False,'network':'none','caps_drop':['ALL'],'security':['no-new-privileges'],'mounts':[],'restart_policy':'no','auto_remove':False,'status':'created',
     'running':False,'paused':False,'restarting':False,'dead':False,'oom_killed':False,'started_at':E.ZERO_TIME,'finished_at':E.ZERO_TIME,
     'memory':512*1024**2,'memory_swap':512*1024**2,'nano_cpus':10**9,'pids':64,'pid':0,'exit_code':0,'restart_count':0,'networks':{},'caps_add':None,'volumes':None,'tmpfs':None,'binds':None,'host_mounts':None}
    self.assertIn(stage,('preinspect','postinspect'));return P.encoded(value)
   E.cli=cli;return values,owners
  def context(*args):
   ctx=original_context(*args);self.assertIs(type(ctx),args[0]['old'].Controller)
   def command(argv,**kw):
    self.assertEqual(argv[:2],['/usr/bin/docker','--host=unix:///var/run/docker.sock']);a=argv[2:]
    if a[:2]==['container','ls']:return((cid+'\n').encode()if state else b'')
    if a[:2]==['container','inspect']:return P.encoded({'name':'/app-ci-flat-acl-123-1','image':image,'owner':'123-1'})
    self.assertEqual(a,['container','rm','--force',cid]);state.clear();return b''
   ctx.C.command=command;return ctx
  try:
   env={'RUNNER_TEMP':str(self.root.parent),'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'github-hosted','GITHUB_REPOSITORY':'mohsenbarari/trading_bot','GITHUB_SHA':'b'*40,'GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1'}
   with patch.dict(os.environ,env,clear=True),patch.object(P,'private_modules',side_effect=modules),patch.object(P,'context',side_effect=context):
    result=P.collect(clock,self.root,self.root/'controller-source',image,'b'*40,inspected,*held,lambda:None)
   self.assertEqual(reached,['create','preinspect','export','postinspect']);self.assertFalse(state)
   self.assertEqual(result['status'],'UNASSESSED');self.assertEqual(result['source_link_join'],'MISSING');self.assertFalse(result['security_accepted'])
   self.assertTrue(result['original_normal_input_readers_retained']);self.assertTrue(result['owned_cleanup']['original_owned_cleanup_completed'])
  finally:
   for owner in held:owner.close()
   self.retire_clock(source,clock)

if __name__=='__main__':unittest.main(verbosity=2)
