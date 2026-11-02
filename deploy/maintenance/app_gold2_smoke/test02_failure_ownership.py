"""02 negative acquisition/ownership and attempt-publication regressions."""
from pathlib import Path
from hashlib import sha256
from unittest.mock import patch
import errno,json,os,tempfile,time,unittest
import test_window as F
A=F.A

class FailureOwnership(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory(prefix='owned02-failure-',dir=F.HERE);self.root=Path(self.directory.name)
        self.path=self.root/'archive.tar';self.raw=F.archive(self.path);self.source=None;self.stream=None;self.fixture_disposals=[]
    def tearDown(self):
        source=self.source
        if source is not None:
            if not source.close_attempted:
                try:source.close()
                except BaseException:pass
            if source.fd is not None:
                # The negative fixture owns the allocation/replacement action.
                # This is separate disposal; adapter HELD stays unchanged.
                original=source.fd;os.close(original)
                with self.assertRaises(OSError)as result:os.fstat(original)
                self.assertEqual(result.exception.errno,errno.EBADF);self.fixture_disposals.append(original);source.fd=None
        if self.stream is not None:self.stream.close()
        self.directory.cleanup();self.assertFalse(self.root.exists())
    def direct(self):
        self.stream=self.path.open('rb');self.source=A.Source(self.stream,len(self.raw),lambda:None,time.monotonic()+30)
        self.source.verify(sha256(self.raw).hexdigest());self.source.digest_pin=sha256(self.raw).hexdigest();return self.source
    def admitted_call(self,**overrides):
        self.stream=self.path.open('rb');pin=sha256(self.raw).hexdigest();sbom=b'{"components":[]}'
        receipt={'schema':'same_image_never_started_export/1','container_id':'3'*64,'image_id':'sha256:'+'1'*64,'source_commit':'2'*40,'before':{},'after':{},
                 'archive':{'bytes':len(self.raw),'sha256':pin,'full9':list(A.identity(os.fstat(self.stream.fileno())))},
                 'actual_export_client_returncode':0,'container_started':False,'private_mounts_used':False,'security_accepted':False}
        kw=dict(image_id=receipt['image_id'],source_commit=receipt['source_commit'],sbom_raw=sbom,sbom_sha256=sha256(sbom).hexdigest(),
                verifier_sha256=sha256(Path(A.__file__).read_bytes()).hexdigest(),export_sha256=pin,container_receipt=receipt,check=lambda:None,end=time.monotonic()+30)
        kw.update(overrides)
        try:return A.collect(F.I,None,self.stream,len(self.raw),**kw)
        finally:
            self.source=A.LAST_ATTEMPT_SOURCE;self.stream.close()
    def assert_held(self,source):
        self.assertTrue(source.dup_acquired);self.assertTrue(source.close_attempted);self.assertTrue(source.duplicate_close_attempted)
        self.assertTrue(source.held);self.assertTrue(source.uncertain_hold);self.assertFalse(source.actual_close_attempted);self.assertFalse(source.duplicate_closed)
        os.fstat(source.duplicate)
        with self.assertRaisesRegex(RuntimeError,'archive_close_unknown'):source.close()
    def constructor_flags_failure(self,replacement=None):
        actual=A.flags;injected=[];sentinel=OSError(5,'OWNED_CAPTURE_FAILURE');foreign=self.root/'foreign';foreign.write_bytes(b'foreign-data'+bytes(50))
        def flags(fd):
            source=A.LAST_ATTEMPT_SOURCE
            if source is not None and source.dup_acquired and fd==source.duplicate and not source.duplicate_admitted and not injected:
                injected.append(fd)
                if replacement:
                    target=self.path if replacement=='same_inode'else foreign
                    os.close(fd);opened=os.open(target,os.O_RDONLY|os.O_CLOEXEC)
                    if opened!=fd:os.dup2(opened,fd,inheritable=False);os.close(opened)
                    os.lseek(fd,17,os.SEEK_SET)
                    if replacement=='same_inode':
                        self.assertEqual(A.identity(os.fstat(fd)),source.frame);self.assertEqual(actual(fd),source.original_flags)
                        self.assertEqual(os.lseek(fd,0,os.SEEK_CUR),17);self.assertEqual(os.lseek(source.original,0,os.SEEK_CUR),0)
                raise sentinel
            return actual(fd)
        with patch.object(A,'flags',flags),self.assertRaises(BaseException)as error:self.admitted_call()
        source=self.source;self.assert_held(source);self.assertFalse(source.duplicate_admitted)
        causes=A.public_failure(error.exception)['causes'];self.assertEqual(causes[0]['os_errno'],5)
        self.assertIn('archive_close_held',[row['code']for row in causes]);self.assertEqual(A.LAST_OBSERVATION['attempt'],source.attempt)
        if replacement:self.assertEqual(os.lseek(source.duplicate,0,os.SEEK_CUR),17)
    def test_constructor_flag_capture_failure_retains_owned_prefix_HELD(self):self.constructor_flags_failure()
    def test_constructor_flag_failure_closed_reused_distinct_inode_never_closed(self):self.constructor_flags_failure('distinct_inode')
    def test_constructor_flag_failure_closed_reused_same_inode_exact_flags_offset17_never_closed(self):self.constructor_flags_failure('same_inode')
    def test_constructor_duplicate_fstat_failure_retains_prefix_HELD(self):
        actual=os.fstat;raised=[]
        def fstat(fd):
            source=A.LAST_ATTEMPT_SOURCE
            if source is not None and source.dup_acquired and fd==source.duplicate and not source.duplicate_admitted:
                raised.append(1);raise OSError(5,'OWNED_STAT_FAILURE')
            return actual(fd)
        with patch.object(os,'fstat',fstat),self.assertRaises(BaseException):self.admitted_call()
        self.assertTrue(raised);self.assert_held(self.source)
    def test_constructor_callback_failure_then_exact_restore_never_creates_close_authority(self):
        def check():
            source=A.LAST_ATTEMPT_SOURCE
            if source is not None and source.dup_acquired:raise RuntimeError('archive_source_changed')
        with self.assertRaises(BaseException):self.admitted_call(check=check)
        self.source.checker=lambda:None;self.assert_held(self.source)
    def test_same_inode_exact_flags_offset17_after_admission_is_HELD(self):
        source=self.direct();fd=source.duplicate;os.close(fd);opened=os.open(self.path,os.O_RDONLY|os.O_CLOEXEC)
        if opened!=fd:os.dup2(opened,fd,inheritable=False);os.close(opened)
        os.lseek(fd,17,os.SEEK_SET)
        self.assertEqual(A.identity(os.fstat(fd)),source.frame);self.assertEqual(A.flags(fd),source.duplicate_flags)
        with self.assertRaisesRegex(RuntimeError,'archive_source_changed'):source.check()
        os.lseek(fd,source.duplicate_offset,os.SEEK_SET)
        with self.assertRaises(BaseException):source.close()
        self.assert_held(source);self.assertEqual(os.lseek(fd,0,os.SEEK_CUR),0)
    def test_stale_observation_cleared_before_bad_admission_or_constructor(self):
        good=self.admitted_call();previous=A.LAST_OBSERVATION;self.assertEqual(previous['phase'],'complete')
        with self.assertRaisesRegex(RuntimeError,'source_identity'):self.admitted_call(image_id='BAD')
        current=A.LAST_OBSERVATION;self.assertIsNot(current,previous);self.assertEqual(current['attempt']['ordinal'],previous['attempt']['ordinal']+1)
        self.assertEqual(current['phase'],'admission');self.assertFalse(current['source_prefix_published']);self.assertIsNone(A.LAST_ATTEMPT_SOURCE)
        self.assertEqual(current['failure']['causes'][0]['code'],'source_identity');self.assertNotIn('counts',current)
        # Constructor admission follows a distinct attempt, with a visible prefix.
        with self.assertRaisesRegex(RuntimeError,'archive_deadline'):self.admitted_call(end=time.monotonic()-1)
        self.assertTrue(A.LAST_OBSERVATION['source_prefix_published']);self.assertFalse(self.source.dup_acquired)
    def test_failed_full_digest_restore_same_Source_stays_refused(self):
        source=self.direct();original=sha256(self.raw).hexdigest()
        with self.assertRaisesRegex(RuntimeError,'archive_digest'):source.verify('0'*64)
        calls=source.counts['pread_calls']
        with self.assertRaisesRegex(RuntimeError,'archive_digest'):source.verify(original)
        with self.assertRaisesRegex(RuntimeError,'archive_digest'):source.read(0,1)
        self.assertEqual(source.counts['pread_calls'],calls)
    def test_failed_window_verify_then_table_restore_same_Source_stays_refused(self):
        source=self.direct();table=source.window_digests;source.window_digests=bytes([table[0]^1])+table[1:]
        with self.assertRaisesRegex(RuntimeError,'archive_window_digest'):source.verify(sha256(self.raw).hexdigest())
        source.window_digests=table
        with self.assertRaisesRegex(RuntimeError,'archive_window_digest'):source.verify(sha256(self.raw).hexdigest())
    def test_actual_hash_failure_restore_same_Source_stays_refused(self):
        source=self.direct();failure=RuntimeError('archive_digest')
        with patch.object(A,'sha256',side_effect=failure),self.assertRaises(RuntimeError)as captured:source.verify(sha256(self.raw).hexdigest())
        self.assertIs(captured.exception,failure)
        with self.assertRaises(RuntimeError)as second:source.read(0,1)
        self.assertIs(second.exception,failure)
    def test_read_postguard_and_close_failure_all_causes_retained(self):
        source=self.direct();io=OSError(5,'OWNED_BODY_FAILURE');post=RuntimeError('archive_source_changed');failed=[]
        def checker():
            if failed:raise post
        def read(*args):failed.append(1);raise io
        source.checker=checker
        with patch.object(os,'pread',read),self.assertRaises(A.ArchiveIOFailure):source.actual(0,1)
        source.checker=lambda:None
        with self.assertRaises(A.ArchiveIOFailure)as closing:source.close()
        public=A.public_failure(closing.exception);codes=[row['code']for row in public['causes']]
        self.assertEqual(public['causes'][0]['os_errno'],5);self.assertIn('archive_source_changed',codes);self.assertIn('archive_close_held',codes)
        self.assertNotIn('OWNED_BODY_FAILURE',json.dumps(public));self.assert_held(source)
    def test_recursive_owned_aggregate_maps_actual_causes_and_count_refusal(self):
        one=RuntimeError('archive_digest');two=OSError(5,'operand');three=RuntimeError('archive_close_held')
        result=A.public_failure(A.ArchiveIOFailure([one,A.ArchiveIOFailure([two,three])]))
        self.assertEqual([row['code']for row in result['causes']],['archive_digest','archive_unproven','archive_close_held'])
        self.assertIn('archive_error_count',A.PUBLIC_FAILURE_CODES)
        with self.assertRaisesRegex(RuntimeError,'archive_error_count'):A.public_failure(A.ArchiveIOFailure([one]*4097))

if __name__=='__main__':unittest.main()
