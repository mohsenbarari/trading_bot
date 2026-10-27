"""Owned late-close and first-writer regressions; no hosted/image/business run."""
from hashlib import sha256
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
import ast,gzip,io,json,os,tarfile,tempfile,unittest
import test_market_ci_controller_integration as fixtures
ROOT=Path(__file__).resolve().parents[1]
M=fixtures.M
class EvidenceLifetimeTests(unittest.TestCase):
 def own(self):
  tmp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(tmp.cleanup);return Path(tmp.name)
 def fixture_case(self):
  case=fixtures.IntegrationTests();self.addCleanup(case.doCleanups);return case
 def source(self,root):
  p=root/'owned-source';p.write_bytes(b'OWNED SOURCE');p.chmod(0o600);return M.Held(p)
 def late_source_close(self,resources,source,target):
  fds=[row.fd for row in resources if isinstance(row,M.Held)];fds.append(source.fd);actual=os.close;changed=[];source_fd=source.fd
  def close(fd):
   actual(fd)
   if fd==source_fd and not changed:changed.append(True);target.write_bytes(b'OWNED LATE MUTATION')
  with patch.object(M.os,'close',side_effect=close),self.assertRaisesRegex(RuntimeError,'source_changed'):
   M.finish_evidence([*resources,source])
  self.assertTrue(changed)
  for fd in fds:
   with self.assertRaises(OSError):os.fstat(fd)
 def test_business_profile_fds_survive_later_source_close_and_refuse_mutation(self):
  case=self.fixture_case();root,build,_,_,_=case.business_fixture();rows=[];checks=[]
  M.business81_outputs(root,build,lambda:None,retained=rows,final_checks=checks)
  self.assertEqual(len(rows),16);self.assertTrue(all(row.fd is not None for row in rows))
  target=Path(build['historical81']['run_directory'])/'base-root/profile.json'
  self.late_source_close(rows,self.source(root),target)
 def reports(self,root):
  image='sha256:'+'a'*64;(root/'reports').mkdir()
  scan={'Metadata':{'ImageID':image},'Results':[]};sbom={'metadata':{'component':{'type':'container','name':image,'properties':[{'name':'aquasecurity:trivy:ImageID','value':image}]}}}
  for name,value in(('vulnerabilities.json',scan),('sbom.cdx.json',sbom)):(root/'reports'/name).write_bytes(M.encoded(value))
  return image
 def test_raw_scan_fds_survive_later_source_close_and_refuse_mutation(self):
  root=self.own();image=self.reports(root);rows=[];checks=[]
  counts,_=M.raw_reports(root,image,retained=rows,final_checks=checks);self.assertEqual(counts['HIGH'],0)
  self.assertEqual(len(rows),2);self.assertTrue(all(row.fd is not None for row in rows))
  self.late_source_close(rows,self.source(root),root/'reports/vulnerabilities.json')
 def test_parcel_archive_and_receipt_survive_later_build_source_closes(self):
  case=self.fixture_case();root,receipt,controllers,cache=case.fixture();rows=[];checks=[]
  M.controller_parcel(root,receipt,controllers,cache,'e'*64,'f'*64,1792972800,lambda:None,retained=rows,final_checks=checks)
  self.assertEqual({row.path.name for row in rows},{'controller-sources.tar.gz','controller-sources.json'})
  self.assertTrue(all(row.fd is not None for row in rows))
  self.late_source_close(rows,self.source(root),root/'controller-sources.tar.gz')
 def test_parcel_receipt_first_writer_close_cannot_mutate_earlier_archive(self):
  case=self.fixture_case();root,receipt,controllers,cache=case.fixture();target=root/'controller-sources.json';archive=root/'controller-sources.tar.gz';actual=os.close;changed=[]
  def close(fd):
   frame=os.fstat(fd);actual(fd)
   if target.exists()and(frame.st_dev,frame.st_ino)==(target.stat().st_dev,target.stat().st_ino)and not changed:
    changed.append(True)
    with archive.open('ab')as stream:stream.write(b'OWNED LATE ARCHIVE MUTATION')
  with patch.object(M.os,'close',side_effect=close),self.assertRaisesRegex(RuntimeError,'source_changed'):
   M.controller_parcel(root,receipt,controllers,cache,'e'*64,'f'*64,1792972800,lambda:None)
  self.assertTrue(changed)
 def tar_bytes(self):
  buf=io.BytesIO()
  with tarfile.open(fileobj=buf,mode='w')as out:
   info=tarfile.TarInfo('owned-public.txt');raw=b'OWNED PUBLIC';info.size=len(raw);out.addfile(info,io.BytesIO(raw))
  return buf.getvalue()
 def test_both_export_streams_are_bound_before_their_first_writer_closes(self):
  root=self.own();raw=self.tar_bytes()
  for name,payload in(('owned.tar',raw),('owned.tar.gz',gzip.compress(raw,mtime=0))):
   with self.subTest(stream=name):
    path=root/name;fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_EXCL,0o600);os.write(fd,payload);os.fsync(fd)
    pin={'bytes':len(payload),'sha256':sha256(payload).hexdigest()};row=M.hold_written(path,fd,65536,binding=pin if name.endswith('.gz')else None)
    self.assertEqual(row.binding,pin);actual=os.close
    def close(number):
     actual(number)
     if number==fd:
      with path.open('ab')as out:out.write(b'OWNED LATE APPEND')
    with patch.object(M.os,'close',side_effect=close):os.close(fd)
    with self.assertRaisesRegex(RuntimeError,'source_changed'):M.finish_evidence([row])
  node=next(n for n in ast.parse((ROOT/'scripts/prepare_market_capture2_image.py').read_bytes()).body if isinstance(n,ast.FunctionDef)and n.name=='finalize')
  bound=[]
  for item in ast.walk(node):
   if isinstance(item,ast.Try)and any(isinstance(n,ast.Call)and isinstance(n.func,ast.Name)and n.func.id=='hold_written'for stmt in item.body if isinstance(stmt,(ast.Assign,ast.Expr))for n in ast.walk(stmt)):
    self.assertTrue(any(isinstance(n,ast.Call)and isinstance(n.func,ast.Attribute)and n.func.attr=='close'for stmt in item.finalbody for n in ast.walk(stmt)));bound.append(item)
  self.assertEqual(len(bound),2)
 def test_last_outer_driver_physical_close_cannot_change_a_phase_output(self):
  root=self.own();driver=root/'driver';driver.write_bytes(b'OWNED DRIVER');fd=os.open(driver,os.O_RDONLY);before=M.identity(os.fstat(fd));proof=root/'proof.json';proof.write_bytes(b'{"owned":true}\n');row=M.Held(proof);closed=[]
  def main(argv,*,retained,final_checks):retained.append(row);return 0
  def named():self.assertEqual(M.identity(driver.lstat()),before)
  def close_driver():os.close(fd);closed.append(True);proof.write_bytes(b'OWNED MUTATED DURING ORIGINAL DRIVER CLOSE')
  with patch.object(M,'main',side_effect=main),self.assertRaisesRegex(RuntimeError,'source_changed'):
   M.outer_main([],close_driver=close_driver,driver_named=named)
  self.assertTrue(closed);self.assertIsNone(row.fd)
 def test_timestamp_changes_only_metadata_on_same_still_open_inode(self):
  root=self.own();path=root/'file';path.write_bytes(b'OWNED SAME BYTES');path.chmod(0o644);row=M.Held(path);before=row.before;fds=[];checks=[]
  M.restamp(root,{'file':row.binding},1792972800,source_rows=[row],retained=fds,final_checks=checks)
  self.assertIsNotNone(row.fd);self.assertEqual(row.before[:7],before[:7]);self.assertEqual(row.before[7],1792972800*10**9);M.finish_evidence([row,*fds],checks)
 def test_same_byte_inode_replacement_during_stamp_refuses_without_adoption(self):
  root=self.own();path=root/'file';raw=b'OWNED SAME BYTES';path.write_bytes(raw);path.chmod(0o644);row=M.Held(path);actual=os.utime;changed=[]
  def stamp(operand,*args,**kwargs):
   actual(operand,*args,**kwargs)
   if operand==row.fd and not changed:path.rename(root/'retained-old');path.write_bytes(raw);path.chmod(0o644);changed.append(True)
  try:
   with patch.object(M.os,'utime',side_effect=stamp),self.assertRaisesRegex(RuntimeError,'source_changed'):
    M.restamp(root,{'file':row.binding},1792972800,source_rows=[row],retained=[],final_checks=[])
   self.assertTrue(changed)
  finally:
   with self.assertRaisesRegex(RuntimeError,'source_changed'):M.finish_evidence([row])
 def test_stamp_cannot_adopt_same_inode_same_size_payload_change_with_ctime_transition(self):
  root=self.own();path=root/'file';raw=b'OWNED SAME BYTES';path.write_bytes(raw);path.chmod(0o644);row=M.Held(path);actual=os.utime;changed=[]
  def stamp(operand,*args,**kwargs):
   actual(operand,*args,**kwargs)
   if operand==row.fd and not changed:
    path.write_bytes(b'X'*len(raw));actual(operand,*args,**kwargs);changed.append(True)
  try:
   with patch.object(M.os,'utime',side_effect=stamp),self.assertRaisesRegex(RuntimeError,'source_binding'):
    M.restamp(root,{'file':row.binding},1792972800,source_rows=[row],retained=[],final_checks=[])
   self.assertTrue(changed)
  finally:
   with self.assertRaisesRegex(RuntimeError,'source_changed'):M.finish_evidence([row])
 def test_all_four_workflow_bootstraps_use_explicit_outer_close_barrier(self):
  text=(ROOT/'.github/workflows/market-capture2-image.yml').read_text()
  self.assertEqual(text.count("namespace['outer_main']"),4);self.assertEqual(text.count("'__name__':'_sealed_normal_market_driver'"),4)
  self.assertEqual(text.count('close_driver=close_driver,driver_named=driver_named'),4);self.assertNotIn("{'__name__':'__main__','__file__':str(path)}",text)
if __name__=='__main__':unittest.main()
