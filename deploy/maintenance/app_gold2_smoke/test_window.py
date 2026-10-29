"""Bounded owned public DATA fixtures, not image/hosted evidence."""
from pathlib import Path
from types import ModuleType
from hashlib import sha256
from io import BytesIO
from unittest.mock import patch
import ast,fcntl,json,os,stat,struct,tarfile,tempfile,time,unittest

HERE=Path(__file__).absolute().parent
REPO=HERE.parents[1]
R2=HERE/'dependencies' if(HERE/'dependencies').is_dir()else HERE.parents[2]/'deploy/maintenance/app_r2_observe02'
def load(path):
    m=ModuleType('owned_'+path.stem);m.__file__=str(path)
    exec(compile(path.read_bytes(),str(path),'exec'),m.__dict__);return m
A=load(HERE/'archive_inventory_gold2.py');O=load(R2/'archive_inventory.py');I=load(R2/'inventory.py')
builder=next(n for n in ast.parse((HERE/'fixtures/inventory_elf_fixture.py').read_bytes()).body if isinstance(n,ast.FunctionDef)and n.name=='elf')
namespace={'struct':struct,'HN':{'perm_copy_fd','attr_copy_fd'}}
exec(compile(ast.Module(body=[builder],type_ignores=[]),'<owned-existing-ELF-fixture>','exec'),namespace)
elf=namespace['elf']

def archive(path,*,count=8,large=False,bad=False):
    data={'var/lib/dpkg/status':b'Package: owned\nStatus: install ok installed\nVersion: 1.0\nArchitecture: amd64\n\n',
          'var/lib/dpkg/info/owned.list':b'/usr/bin/a\n/proof/a\n/proc/a\n/sys/a\n/dev/a\n'}
    for name in ('usr/bin/a','proof/a','proc/a','sys/a','dev/a'):data[name]=elf()
    data['usr/bin/unsupported']=b'\x7fELF'+bytes(28)
    for n in range(count):data[f'usr/share/owned/{n}']=b'x'*(4096 if large else 20)
    with tarfile.open(path,'w',format=tarfile.USTAR_FORMAT)as out:
        for name,raw in data.items():
            item=tarfile.TarInfo(name);item.size=len(raw);item.mode=0o644;out.addfile(item,BytesIO(raw))
        item=tarfile.TarInfo('usr/bin/hard');item.type=tarfile.LNKTYPE;item.linkname='usr/bin/a';out.addfile(item)
        item=tarfile.TarInfo('usr/bin/alias');item.type=tarfile.SYMTYPE;item.linkname='a';out.addfile(item)
    if bad:
        with path.open('r+b')as stream:stream.write(b'BAD')
    path.chmod(0o600)
    return path.read_bytes()

def collect(module,path,check=lambda:None):
    raw=path.read_bytes();sbom=b'{"components":[]}';pin=sha256(raw).hexdigest()
    with path.open('rb')as stream:
        before=os.fstat(stream.fileno())
        receipt={'schema':'same_image_never_started_export/1','container_id':'3'*64,'image_id':'sha256:'+'1'*64,'source_commit':'2'*40,
                 'before':{'OWNED_PROTOCOL_ONLY':True},'after':{'OWNED_PROTOCOL_ONLY':True},
                 'archive':{'bytes':len(raw),'sha256':pin,'full9':list(module.identity(before))},
                 'actual_export_client_returncode':0,'container_started':False,'private_mounts_used':False,'security_accepted':False}
        report=module.collect(I,None,stream,len(raw),image_id=receipt['image_id'],source_commit=receipt['source_commit'],
               sbom_raw=sbom,sbom_sha256=sha256(sbom).hexdigest(),verifier_sha256=sha256(Path(module.__file__).read_bytes()).hexdigest(),
               export_sha256=pin,container_receipt=receipt,check=check,end=time.monotonic()+30)
        assert not stream.closed and os.fstat(stream.fileno())==before
        return report

class Window(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory(prefix='owned-window-',dir=HERE);self.root=Path(self.directory.name)
        self.path=self.root/'archive.tar';self.raw=archive(self.path);self.stream=self.path.open('rb');self.source=None;self.fixture_disposals=[]
    def tearDown(self):
        if self.source is not None and not self.source.close_attempted:
            try:self.source.close()
            except BaseException:pass
        if self.source is not None and self.source.fd is not None:
            # Test-owned private fd only, after asserting no product cleanup PASS.
            fd=self.source.fd;os.close(fd)
            with self.assertRaises(OSError)as result:os.fstat(fd)
            self.assertEqual(result.exception.errno,9);self.fixture_disposals.append(fd);self.source.fd=None
        self.stream.close();self.directory.cleanup()
    def source_for(self,check=lambda:None):
        self.source=A.Source(self.stream,len(self.raw),check,time.monotonic()+30)
        self.source.verify(sha256(self.raw).hexdigest());self.source.digest_pin=sha256(self.raw).hexdigest();return self.source
    def test_exact_every_report_field_except_truthful_current_verifier_source(self):
        old=collect(O,self.path);new=collect(A,self.path)
        self.assertEqual(old['verifier_sha256'],sha256(Path(O.__file__).read_bytes()).hexdigest())
        self.assertEqual(new['verifier_sha256'],sha256(Path(A.__file__).read_bytes()).hexdigest())
        self.assertNotEqual(old['verifier_sha256'],new['verifier_sha256'])
        del old['verifier_sha256'];del new['verifier_sha256'];self.assertEqual(old,new)
        inventory=new['inventory'];self.assertEqual(inventory['runtime_paths_excluded'],[])
        self.assertTrue({'/proof/a','/proc/a','/sys/a','/dev/a'}<={r['path']for r in inventory['elf_files']})
        self.assertEqual(len(inventory['elf_files']),7)
        self.assertEqual(next(r for r in inventory['elf_files']if r['path']=='/usr/bin/unsupported')['elf_parse_status'],'UNASSESSED_UNSUPPORTED_METADATA')
        self.assertFalse(new['security_accepted']);self.assertFalse(new['whole_image_acl_closed'])
    def test_post_hash_only_after_physical_duplicate_close_original_retained(self):
        source=self.source_for();duplicate=source.duplicate;seen=[];actual=os.pread
        def observed(fd,size,offset):
            if fd==source.original:
                with self.assertRaises(OSError)as c:os.fstat(duplicate)
                self.assertEqual(c.exception.errno,9)
            seen.append(fd);return actual(fd,size,offset)
        with patch.object(os,'pread',observed):source.read(512,4);source.close()
        self.assertEqual(source.counts['full_hash_passes_completed'],2);self.assertEqual(source.counts['full_hash_bytes'],2*len(self.raw))
        self.assertEqual(seen[-1],source.original);self.assertTrue(source.duplicate_closed);self.assertFalse(self.stream.closed)
    def test_same_window_snapshot_data_never_caches_guards_across_io(self):
        calls=[];source=self.source_for(lambda:calls.append(1));source.read(0,4);n=len(calls)
        for _ in range(10):source.read(100,4)
        self.assertEqual(len(calls),n);self.assertGreater(source.counts['window_hits'],0)
        source.finish_pure();self.assertEqual(len(calls),n+1)
        source.actual(0,10);self.assertGreaterEqual(len(calls),n+3);self.assertFalse(source.pure_active)
    def test_corrupt_window_on_actual_reload_refused_even_unchanged_full9(self):
        source=self.source_for();actual=os.pread
        def corrupt(fd,size,offset):
            raw=actual(fd,size,offset);return bytes([raw[0]^1])+raw[1:]
        with patch.object(os,'pread',corrupt),self.assertRaisesRegex(RuntimeError,'archive_window_digest'):source.read(0,4)
        self.assertIsNone(source.snapshot)
        with self.assertRaisesRegex(RuntimeError,'archive_window_digest'):source.read(0,4)
    def test_body_and_postguard_failure_both_retained(self):
        failed=False;sentinel=OSError(5,'owned I/O failure');post=RuntimeError('owned_postguard_failure')
        def check():
            if failed:raise post
        source=self.source_for(check)
        def pread(*args):
            nonlocal failed
            failed=True;raise sentinel
        with patch.object(os,'pread',pread),self.assertRaises(A.ArchiveIOFailure)as captured:source.actual(0,10)
        self.assertIs(captured.exception.errors[0],sentinel);self.assertIs(captured.exception.errors[1],post)
        self.assertFalse(source.pure_active)
    def test_pread_exception_always_has_fresh_postguard(self):
        calls=[];source=self.source_for(lambda:calls.append(1));before=len(calls)
        with patch.object(os,'pread',side_effect=OSError(5,'owned')),self.assertRaises(OSError):source.actual(0,10)
        self.assertEqual(len(calls)-before,2)
    def test_before_guard_failure_still_checks_finally_and_never_reads(self):
        source=self.source_for();failure=RuntimeError('owned_beforeguard_failure');calls=[]
        def check():calls.append(1);raise failure
        source.checker=check
        with patch.object(os,'pread')as read,self.assertRaises(A.ArchiveIOFailure)as error:source.actual(0,1)
        self.assertEqual(calls,[1,1]);read.assert_not_called()
        self.assertTrue(all(e is failure for e in error.exception.errors))
        source.checker=lambda:None
        with self.assertRaises(BaseException):source.read(0,1)
    def test_pure_batch_128_limit_and_final_guard(self):
        calls=[];source=self.source_for(lambda:calls.append(1));before=len(calls)
        for _ in range(129):source.tick()
        self.assertEqual(len(calls)-before,3);self.assertEqual(source.pure_count,1)
        source.finish_pure();self.assertEqual(len(calls)-before,4)
    def test_cheap_deadline_every_tick_and_sticky_refusal(self):
        source=self.source_for();source.tick();saved=source.end;source.end=time.monotonic()-1
        with self.assertRaisesRegex(RuntimeError,'archive_deadline'):source.tick()
        source.end=saved
        with self.assertRaisesRegex(RuntimeError,'archive_deadline'):source.tick()
    def test02_flag_drift_stays_HELD_until_separate_fixture_disposal(self):
        source=self.source_for();source.read(0,4);original=fcntl.fcntl(source.original,fcntl.F_GETFL)
        fcntl.fcntl(source.original,fcntl.F_SETFL,original|os.O_NONBLOCK)
        with self.assertRaisesRegex(RuntimeError,'archive_source_changed'):source.finish_pure()
        with self.assertRaises(BaseException):source.close()
        self.assertFalse(source.duplicate_closed);self.assertIsNotNone(source.fd);self.assertTrue(source.held);self.assertFalse(source.actual_close_attempted);self.assertFalse(self.stream.closed)
        fcntl.fcntl(source.original,fcntl.F_SETFL,original)
        with self.assertRaises(BaseException):source.read(0,1)
    def test_private_duplicate_replacement_never_closes_foreign_descriptor(self):
        source=self.source_for();foreign=self.root/'foreign';foreign.write_bytes(b'foreign')
        with foreign.open('rb')as stream:
            os.dup2(stream.fileno(),source.duplicate)
            with self.assertRaises(BaseException):source.close()
            self.assertFalse(source.duplicate_closed);self.assertIsNotNone(source.fd)
            self.assertEqual(os.pread(source.duplicate,7,0),b'foreign')
            with self.assertRaises(BaseException):source.close()
    def test_no_repeated_close_attempt_and_second_full_hash_not_skipped(self):
        source=self.source_for();source.close()
        self.assertEqual(source.counts['full_hash_passes_completed'],2)
        with self.assertRaisesRegex(RuntimeError,'archive_close_unknown'):source.close()
    def test_malformed_archive_body_failure_ends_pure_batch_and_closes(self):
        archive(self.path,bad=True)
        with self.assertRaisesRegex(RuntimeError,'tar_checksum'):collect(A,self.path)
        observation=A.LAST_OBSERVATION
        self.assertTrue(observation['duplicate_physically_closed']);self.assertEqual(observation['counts']['full_hash_passes_completed'],2)
        self.assertIn('tar_checksum',[e['code']for e in observation['errors']])
    def test_final_pure_guard_even_when_index_body_raises(self):
        source=self.source_for();source.read(0,4);calls=[];source.checker=lambda:calls.append(1)
        # Force a parser failure in a pure path after the actual window was read.
        with patch.object(A,'canonical',side_effect=RuntimeError('owned_parse_failure')),self.assertRaisesRegex(RuntimeError,'owned_parse_failure'):A.index(source)
        self.assertFalse(source.pure_active);self.assertGreaterEqual(len(calls),1)
    def test_parser_body_and_final_pure_guard_both_preserved(self):
        source=self.source_for();source.read(0,4);post=RuntimeError('owned_finalguard_failure');body=RuntimeError('owned_parse_failure')
        def parser(*args,**kwargs):
            source.checker=lambda:(_ for _ in()).throw(post)
            raise body
        with patch.object(A,'canonical',parser),self.assertRaises(A.ArchiveIOFailure)as error:A.index(source)
        self.assertIs(error.exception.errors[0],body);self.assertIs(error.exception.errors[1],post);self.assertFalse(source.pure_active)
    def test_source_metadata_change_in_pure_batch_fails_final_guard(self):
        source=self.source_for();source.tick();self.path.chmod(0o640)
        with self.assertRaisesRegex(RuntimeError,'archive_source_changed'):source.finish_pure()
        with self.assertRaises(BaseException):source.close()
        self.assertFalse(source.duplicate_closed);self.assertTrue(source.held);self.assertFalse(source.actual_close_attempted)
    def test_closed_failure_mapping_preserves_causes_without_operands(self):
        a=RuntimeError('archive_digest');b=OSError(5,'SECRET_OPERAND');c=RuntimeError('SECRET_OPERAND')
        result=A.public_failure(A.ArchiveIOFailure([a,b,c]));raw=json.dumps(result)
        self.assertNotIn('SECRET_OPERAND',raw);self.assertEqual(len(result['causes']),3)
        self.assertEqual(result['causes'][0]['code'],'archive_digest');self.assertEqual(result['causes'][1]['os_errno'],5)
        self.assertEqual(result['causes'][2]['code'],'archive_unproven');self.assertFalse(result['security_accepted'])
    def test_invalid_digest_and_truncated_actual_read_refused(self):
        source=self.source_for()
        with self.assertRaisesRegex(RuntimeError,'archive_digest'):source.verify('0'*64)
        with self.assertRaisesRegex(RuntimeError,'archive_digest'):source.actual(0,1)
        # The prior01 truncation assertion is retained on a fresh independent
        # source; a failed full verify can no longer be healed/reused.
        try:source.close()
        except BaseException:pass
        self.source=None;source=self.source_for()
        with patch.object(os,'pread',return_value=b''),self.assertRaisesRegex(RuntimeError,'archive_truncated'):source.actual(0,1)
    def test_empty_and_cross_window_reads_exact_authenticated_bytes(self):
        self.stream.close();self.raw=archive(self.path,count=300,large=True);self.stream=self.path.open('rb');source=self.source_for()
        self.assertEqual(source.read(0,0),b'');start=A.WINDOW-17
        self.assertEqual(source.read(start,100),self.raw[start:start+100]);self.assertEqual(source.read(start,100),self.raw[start:start+100])
        self.assertLessEqual(len(source.snapshot),A.WINDOW)
    def test_max_table_and_memory_bound_are_honest_without_2GiB_fixture(self):
        self.assertEqual(((A.MAX_BYTES+A.WINDOW-1)//A.WINDOW)*32,65536)
        source=self.source_for();o=source.observation();self.assertEqual(o['window_digest_raw_bytes'],32)
        self.assertGreater(o['window_digest_Python_bytes'],32);self.assertEqual(o['window_digest_build_peak_raw_bytes'],64)
        self.assertTrue(o['other_parser_report_Python_overhead_not_measured']);self.assertFalse(o['whole_image_proof'])
    def test_all_original_limits_and_no_exclusions_unchanged(self):
        for key in ('MAX_ENTRIES','MAX_FILES','MAX_BYTES','MAX_ELF','MAX_REPORT','MAX_NAMES','MAX_META','MAX_PAX','MAX_PACKAGE_LIST_TOTAL','MAX_INVENTORY_SECONDS','EXCLUDED','PURE_INVENTORY_SHA256'):
            self.assertEqual(getattr(A,key),getattr(O,key),key)
    def test_bad_read_bounds_before_payload_io(self):
        source=self.source_for();n=source.counts['pread_calls']
        for args in ((-1,1),(0,-1),(len(self.raw),1),(0,A.MAX_ELF+1)):
            with self.subTest(args=args),self.assertRaisesRegex(RuntimeError,'archive_read_bound'):source.read(*args)
        self.assertEqual(source.counts['pread_calls'],n)

if __name__=='__main__':unittest.main()
