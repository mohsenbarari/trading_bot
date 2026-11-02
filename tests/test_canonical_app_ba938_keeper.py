"""Actual owned public files/FDs only; no archive/image/network/Git operations."""
from contextlib import ExitStack
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
import os,stat,tempfile,unittest
REPO=Path(__file__).resolve().parents[1]
HERE=REPO/'deploy/maintenance/ba938_smoke'
TEST_ROOT=Path(__file__).resolve().parent

def load(leaf):
    p=HERE/leaf;m=ModuleType('_owned_keeper_'+leaf.replace('.','_'));m.__file__=str(p)
    exec(compile(p.read_bytes(),str(p),'exec'),m.__dict__);return m
B=load('compose_security_os_candidate.py');S=load('seal_and_run.py')


class KeeperTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix='.owned-proof-',dir=TEST_ROOT);self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        self.keeper=B.ProofKeeper(self.root);self.addCleanup(self.close_keeper)
    def close_keeper(self):
        if not self.keeper.closed:
            try:self.keeper.close()
            except RuntimeError:pass
    def file(self,name,raw=b'PUBLIC ARCHIVE BYTES'):
        path=self.root/name;path.write_bytes(raw);path.chmod(0o600);return path
    def test_first_pinned_use_is_retained_streaming_without_a_heap_body_copy(self):
        path=self.file('large-public.archive');path.write_bytes(b'PUBLIC-HEADER')
        with path.open('r+b')as stream:stream.truncate(8*1024**2)
        with patch.object(B,'PROOF_KEEPER',self.keeper),patch.object(B.os,'read',side_effect=AssertionError('constructor payload read denied')),patch.object(B.os,'pread',side_effect=AssertionError('constructor payload read denied')):
            with B.pinned(path,8*1024**2)as(stream,size):
                self.assertEqual(len(self.keeper.records),1);record=self.keeper.records[0]
                self.assertIsNotNone(record.fd);self.assertFalse(hasattr(record,'raw'));self.assertEqual(size,8*1024**2)
                self.assertEqual(stream.read(13),b'PUBLIC-HEADER')
        self.assertIsNotNone(record.fd);self.keeper.check()
    def test_each_pinned_context_has_fresh_offset_without_releasing_old_binding(self):
        path=self.file('repeat.archive',b'PUBLIC-FIRST-THEN-SECOND')
        with patch.object(B,'PROOF_KEEPER',self.keeper):
            with B.pinned(path,128)as(stream,_):self.assertEqual(stream.read(6),b'PUBLIC')
            with B.pinned(path,128)as(stream,_):self.assertEqual(stream.read(6),b'PUBLIC')
        self.assertEqual(len(self.keeper.records),2);self.assertTrue(all(r.fd is not None for r in self.keeper.records))
    def test_real_inner_source_physical_close_cannot_change_an_early_archive_unnoticed(self):
        archive=self.file('admitted.archive');source=self.file('source.py',b'OWNED PUBLIC SOURCE')
        real=os.close;changed=[];source_inode=(source.stat().st_dev,source.stat().st_ino)
        def close(fd):
            frame=os.fstat(fd);real(fd)
            if(frame.st_dev,frame.st_ino)==source_inode and not changed:changed.append(True);archive.write_bytes(b'PUBLIC CHANGED ARCHIVE')
        with patch.object(B,'PROOF_KEEPER',self.keeper),patch.object(B.os,'close',close):
            with ExitStack()as inner:
                fd=os.open(source,os.O_RDONLY|os.O_CLOEXEC);inner.callback(os.close,fd)
                with B.pinned(archive,128)as(stream,_):self.assertEqual(stream.read(),b'PUBLIC ARCHIVE BYTES')
            with self.assertRaisesRegex(RuntimeError,'proof_changed'):self.keeper.check()
        self.assertTrue(changed)
    def test_real_late_outer_source_close_cannot_modify_the_first_used_archive(self):
        archive=self.file('admitted.archive');source=self.file('original-source.py',b'PUBLIC SOURCE')
        held=[S.Held(source,None,owner=os.geteuid(),mode=0o600)]
        with patch.object(B,'PROOF_KEEPER',self.keeper):
            with B.pinned(archive,128)as(stream,_):stream.read()
        inode=(source.stat().st_dev,source.stat().st_ino);real=os.close;changed=[]
        def close(fd):
            frame=os.fstat(fd);real(fd)
            if(frame.st_dev,frame.st_ino)==inode and not changed:changed.append(True);archive.write_bytes(b'PUBLIC LATE ARCHIVE CHANGE')
        with patch.object(B.os,'close',close),self.assertRaisesRegex(RuntimeError,'sealed_finalization'):S.finalize(held,self.keeper)
        self.assertTrue(changed);self.assertTrue(self.keeper.closed);self.assertTrue(all(r.fd is None and r.parents.closed for r in self.keeper.records))
    def test_immutable_report_is_bound_at_creation_before_any_inner_finalizer(self):
        report=self.root/'report.json';source=self.file('source.py',b'PUBLIC SOURCE');held=[S.Held(source,None,owner=os.geteuid(),mode=0o600)]
        with patch.object(B,'PROOF_KEEPER',self.keeper):B.write(report,b'{"public":"original"}\n')
        self.assertEqual(len(self.keeper.records),1);record=self.keeper.records[0]
        self.assertEqual(record.path,report);self.assertIsNotNone(record.fd)
        inode=(source.stat().st_dev,source.stat().st_ino);real=os.close;changed=[]
        def close(fd):
            frame=os.fstat(fd);real(fd)
            if(frame.st_dev,frame.st_ino)==inode and not changed:changed.append(True);report.write_bytes(b'{"public":"modified"}\n')
        with patch.object(B.os,'close',close),self.assertRaisesRegex(RuntimeError,'sealed_finalization'):S.finalize(held,self.keeper)
        self.assertTrue(changed);self.assertIsNone(record.fd)
    def test_report_mutation_at_its_actual_owned_fd_close_cannot_complete(self):
        report=self.root/'report.json'
        with patch.object(B,'PROOF_KEEPER',self.keeper):B.write(report,b'{"owned":true}\n')
        record=self.keeper.records[0];inode=(report.stat().st_dev,report.stat().st_ino);real=os.close;changed=[]
        def close(fd):
            frame=os.fstat(fd);real(fd)
            if(frame.st_dev,frame.st_ino)==inode and not changed:changed.append(True);report.write_bytes(b'{"owned":false}\n')
        with patch.object(B.os,'close',close),self.assertRaisesRegex(RuntimeError,'proof_finalization'):self.keeper.close()
        self.assertTrue(changed);self.assertTrue(record.parents.closed)
    def test_standalone_pinned_checks_file_after_all_parent_physical_closes(self):
        parent=self.root/'parent';parent.mkdir();archive=parent/'archive';archive.write_bytes(b'PUBLIC');archive.chmod(0o600)
        parent_inode=(parent.stat().st_dev,parent.stat().st_ino);real=os.close;changed=[]
        def close(fd):
            frame=os.fstat(fd);real(fd)
            if(frame.st_dev,frame.st_ino)==parent_inode and not changed:changed.append(True);archive.write_bytes(b'CHANGED')
        with patch.object(B,'PROOF_KEEPER',None),patch.object(B.os,'close',close),self.assertRaisesRegex(RuntimeError,'proof_changed'):
            with B.pinned(archive,128)as(stream,_):self.assertEqual(stream.read(),b'PUBLIC')
        self.assertTrue(changed)
    def test_actual_wrong_gid_refuses_before_any_input_payload_read(self):
        archive=self.file('wrong-gid')
        try:os.chown(archive,os.geteuid(),65534 if os.getegid()!=65534 else 65533)
        except OSError as error:
            if error.errno in (1,22):self.skipTest('alternate genuine GID unavailable in this namespace; no metadata masking')
            raise
        with patch.object(B.os,'read',side_effect=AssertionError('payload read denied')),patch.object(B.os,'pread',side_effect=AssertionError('payload read denied')),self.assertRaisesRegex(RuntimeError,'input_metadata'):
            with B.pinned(archive,128):self.fail('unadmitted body')
    def test_parent_admission_precedes_relative_leaf_open_and_payload(self):
        parent=self.root/'parent';parent.mkdir();archive=parent/'archive';archive.write_bytes(b'PUBLIC ORIGINAL');archive.chmod(0o600)
        foreign=self.root/'foreign';foreign.mkdir();(foreign/'archive').write_bytes(b'PUBLIC FOREIGN');(foreign/'archive').chmod(0o600)
        real=os.open;changed=[]
        def opened(path,flags,*args,**kwargs):
            if path=='archive' and 'dir_fd'in kwargs and not changed:
                changed.append(True);parent.rename(self.root/'retained');foreign.rename(parent)
            return real(path,flags,*args,**kwargs)
        with patch.object(B.os,'open',opened),patch.object(B.os,'read',side_effect=AssertionError('payload read denied')),patch.object(B.os,'pread',side_effect=AssertionError('payload read denied')),self.assertRaisesRegex(RuntimeError,'proof_parent_changed'):
            with B.pinned(archive,128):self.fail('unadmitted body')
        self.assertTrue(changed);self.assertEqual((parent/'archive').read_bytes(),b'PUBLIC FOREIGN')
    def test_only_exact_mutable_phase_files_are_exempt_from_retention(self):
        reports=self.root/'reports';reports.mkdir(mode=0o700)
        values={'schema':'security_os_composition_phase/1','phase':'controller_admission','status':'running','first_failure_phase':None,'first_failure_code':None,'security_accepted':False}
        with patch.object(B,'PROOF_KEEPER',self.keeper):
            B.save_phase(self.root,values);self.assertEqual(B.phase_state(self.root)['status'],'running')
            B.save_phase(self.root,{**values,'status':'complete'});self.assertEqual(B.phase_state(self.root)['status'],'complete')
            self.assertEqual(self.keeper.records,[])
            B.write(reports/'phase-proof.json',b'{"immutable":true}\n')
        self.assertEqual(len(self.keeper.records),1);self.keeper.check()
    def test_unrelated_path_cannot_use_the_outer_keeper(self):
        with patch.object(B.os,'open',side_effect=AssertionError('unrelated target must not be opened')),self.assertRaisesRegex(RuntimeError,'proof_scope'):
            self.keeper.input(Path('/UNOPENED-UNRELATED-PUBLIC-FIXTURE'),128)
    def test_bindings_are_public_metadata_not_loaded_archive_bodies(self):
        archive=self.file('archive',b'PUBLIC-ARCHIVE');report=self.root/'report.json'
        with patch.object(B,'PROOF_KEEPER',self.keeper):
            with B.pinned(archive,128)as(stream,_):stream.read(1)
            B.write(report,b'{"public":true}\n')
        rows=self.keeper.bindings();self.assertEqual([r['path']for r in rows],['archive','report.json'])
        self.assertIsNone(rows[0]['created_public_sha256']);self.assertEqual(len(rows[0]['full9']),9)
        self.assertNotIn(b'PUBLIC-ARCHIVE',repr(rows).encode())


if __name__=='__main__':unittest.main()
