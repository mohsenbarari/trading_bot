"""Owned regressions for the three SOURCE02 I/O blockers; no hosted proof."""
from hashlib import sha256
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
import errno,fcntl,os,tempfile,unittest

HERE=Path(__file__).absolute().parent
def load(name):
    p=HERE/name;m=ModuleType('owned02_'+p.stem);m.__file__=str(p)
    exec(compile(p.read_bytes(),str(p),'exec'),m.__dict__);return m
Q=load('controller.py');S=load('seal_and_run.py')
RAW=(HERE/'dependencies/run_app_image_ci_smoke.py').read_bytes()if(HERE/'dependencies').exists()else(HERE.parents[2]/'deploy/maintenance/app_r2_observe02/run_app_image_ci_smoke.py').read_bytes()
assert len(RAW)==68163
PIN=(len(RAW),sha256(RAW).hexdigest())

def flat(error):return error.errors if hasattr(error,'errors')else(error,)

class IO(unittest.TestCase):
    def source(self,root,raw=RAW,mode=0o644):
        path=Path(root)/'owned.py';path.write_bytes(raw);path.chmod(mode);return path
    def close_failed(self,item):
        try:item.close()
        except BaseException:pass
    def assert_closed(self,fd):
        with self.assertRaises(OSError)as caught:os.fstat(fd)
        self.assertEqual(caught.exception.errno,errno.EBADF)

    def test_bootstrap_initial_pread_exception_and_immediate_flag_postguard(self):
        with tempfile.TemporaryDirectory(dir=HERE,prefix='owned-bootstrap-')as td:
            root=Path(td);(root/'source').mkdir();path=root/'source/run_app_image_ci_smoke.py';path.write_bytes(RAW);path.chmod(0o600)
            stack=Q.ObservationSources(lambda e:Q.closed_error(e,Q.CODES));body=OSError(errno.EIO,'OWNED_BODY');calls=[]
            def broken(fd,size,offset):
                calls.append(fd);fcntl.fcntl(fd,fcntl.F_SETFD,0);raise body
            with patch.object(Q.os,'pread',side_effect=broken):
                with self.assertRaises(Q.BootstrapFailure)as caught:Q.bootstrap_module(root,stack,path.name,PIN)
            item=stack.bootstrap_prefixes[0]
            self.assertIs(caught.exception.errors[0],body)
            self.assertTrue(any(e.args==('bootstrap_flags_or_source',)for e in caught.exception.errors[1:]))
            self.assertEqual(len(calls),1);os.fstat(item.original_fd)
            stack.close();self.assert_closed(item.original_fd)
            self.assertIs(item.errors[0],body)

    def test_bootstrap_checker_pread_gets_its_own_postguard(self):
        with tempfile.TemporaryDirectory(dir=HERE,prefix='owned-checker-')as td:
            root=Path(td);(root/'source').mkdir();path=root/'source/run_app_image_ci_smoke.py';path.write_bytes(RAW);path.chmod(0o600)
            stack=Q.ObservationSources(lambda e:Q.closed_error(e,Q.CODES));module,check=Q.bootstrap_module(root,stack,path.name,PIN)
            item=stack.bootstrap_prefixes[0];actual=os.pread;calls=[]
            def changed(fd,size,offset):
                raw=actual(fd,size,offset);calls.append(fd);fcntl.fcntl(fd,fcntl.F_SETFD,0);return raw
            with patch.object(Q.os,'pread',side_effect=changed):
                with self.assertRaisesRegex(RuntimeError,'bootstrap_flags_or_source'):check()
            self.assertEqual(calls,[item.original_fd]);self.assertTrue(callable(module.Controller))
            stack.close();self.assert_closed(item.original_fd)

    def test_68163_multiread_stops_at_first_actual_read_flag_drift(self):
        with tempfile.TemporaryDirectory(dir=HERE,prefix='owned-read-')as td:
            path=self.source(td);item=S.Held.__new__(S.Held);actual=os.read;events=[];reads=[]
            def scope():events.append('guard')
            def changed(fd,size):
                raw=actual(fd,size);events.append('read');reads.append(len(raw));fcntl.fcntl(fd,fcntl.F_SETFD,0);return raw
            with patch.object(S.os,'read',side_effect=changed):
                with self.assertRaisesRegex(RuntimeError,'sealed_changed'):item.__init__(path,PIN,owner=os.geteuid(),mode=0o644,scope=scope)
            self.assertEqual(reads,[65536]);self.assertEqual(events,['guard','read','guard'])
            self.close_failed(item);self.assert_closed(item.original_fd)

    def test_each_read_including_eof_has_fresh_pre_and_finally_postguard(self):
        with tempfile.TemporaryDirectory(dir=HERE,prefix='owned-eof-')as td:
            path=self.source(td);actual=os.read;events=[];sizes=[]
            def scope():events.append('guard')
            def read(fd,size):events.append('read');raw=actual(fd,size);sizes.append(len(raw));return raw
            with patch.object(S.os,'read',side_effect=read):item=S.Held(path,PIN,owner=os.geteuid(),mode=0o644,scope=scope)
            self.assertEqual(sizes,[65536,2627,0]);self.assertEqual(events,['guard','read','guard']*3)
            item.close();self.assert_closed(item.original_fd)

    def test_failed_read_retains_original_and_postguard_causes(self):
        with tempfile.TemporaryDirectory(dir=HERE,prefix='owned-read-error-')as td:
            path=self.source(td);item=S.Held.__new__(S.Held);body=OSError(errno.EIO,'OWNED_BODY');events=[]
            def scope():events.append('guard')
            def broken(fd,size):events.append('read');fcntl.fcntl(fd,fcntl.F_SETFD,0);raise body
            with patch.object(S.os,'read',side_effect=broken):
                with self.assertRaises(S.IOFailure)as caught:item.__init__(path,PIN,owner=os.geteuid(),mode=0o644,scope=scope)
            self.assertEqual(events,['guard','read','guard']);self.assertIs(caught.exception.errors[0],body)
            self.assertTrue(any(e.args==('sealed_changed',)for e in caught.exception.errors[1:]))
            self.assertIs(item.io_errors[0],body);self.close_failed(item);self.assert_closed(item.original_fd)

    def test_save_keeps_original_ro_reader_before_and_after_writer_close(self):
        with tempfile.TemporaryDirectory(dir=HERE,prefix='owned-save-')as td:
            path=Path(td)/'copy.py';owned=[];actual=os.close;events=[]
            def close(fd):
                item=owned[0]
                if fd==item.original_fd:
                    self.assertIsNotNone(item.original_reader);os.fstat(item.original_reader)
                    self.assertEqual(fcntl.fcntl(item.original_reader,fcntl.F_GETFL)&os.O_ACCMODE,os.O_RDONLY);events.append('writer')
                actual(fd)
            with patch.object(S.os,'close',side_effect=close):item=S.save(path,RAW,owned=owned)
            self.assertEqual(events,['writer']);self.assertTrue(item.writer_closed);self.assertFalse(item.reader_closed)
            self.assert_closed(item.original_fd);os.fstat(item.original_reader);self.assertEqual(path.read_bytes(),RAW)
            S.finalize([],saved=owned);self.assertTrue(item.reader_closed);self.assert_closed(item.original_reader)

    def test_identical_bytes_replacement_at_writer_close_cannot_be_adopted(self):
        with tempfile.TemporaryDirectory(dir=HERE,prefix='owned-replace-')as td:
            path=Path(td)/'copy.py';owned=[];actual=os.close;replaced=[]
            def close(fd):
                item=owned[0];actual(fd)
                if fd==item.original_fd:
                    other=Path(td)/'replacement';other.write_bytes(RAW);other.chmod(0o600);os.replace(other,path);replaced.append(True)
            with patch.object(S.os,'close',side_effect=close):
                with self.assertRaises((RuntimeError,OSError)):S.save(path,RAW,owned=owned)
            item=owned[0];self.assertEqual(replaced,[True]);self.assertEqual(path.read_bytes(),RAW)
            self.assertNotEqual(os.fstat(item.original_reader).st_ino,path.stat().st_ino)
            self.assertTrue(item.writer_closed);self.assertFalse(item.reader_closed)
            self.close_failed(item);self.assertTrue(item.reader_closed);self.assert_closed(item.original_reader)

    def test_partial_write_error_keeps_known_prefix_and_reader_custody(self):
        with tempfile.TemporaryDirectory(dir=HERE,prefix='owned-partial-')as td:
            path=Path(td)/'copy.py';owned=[];actual=os.write;body=OSError(errno.EIO,'OWNED_WRITE');calls=[]
            def write(fd,raw):
                calls.append(fd)
                if len(calls)==1:return actual(fd,raw[:2])
                actual(fd,raw[:2]);fcntl.fcntl(fd,fcntl.F_SETFD,0);raise body
            with patch.object(S.os,'write',side_effect=write):
                with self.assertRaises((RuntimeError,OSError))as caught:S.save(path,RAW,owned=owned)
            item=owned[0];self.assertEqual(item.count,2);self.assertEqual(item.before[6],2);self.assertEqual(path.stat().st_size,4)
            self.assertTrue(any(e is body for e in flat(caught.exception)));self.assertTrue(any(e is body for e in item.errors))
            self.assertIsNotNone(item.original_reader);self.assertEqual(os.fstat(item.original_reader).st_size,4)
            self.assertTrue(item.writer_closed);self.close_failed(item);self.assert_closed(item.original_reader)

    def test_writer_unknown_close_once_then_all_attempted_reader_finalizer(self):
        with tempfile.TemporaryDirectory(dir=HERE,prefix='owned-unknown-writer-')as td:
            path=Path(td)/'copy.py';owned=[];actual=os.close;calls=[]
            def close(fd):
                if fd==owned[0].original_fd:calls.append(fd);raise OSError(errno.EIO,'OWNED_UNKNOWN_CLOSE')
                actual(fd)
            with patch.object(S.os,'close',side_effect=close):
                with self.assertRaises(OSError):S.save(path,RAW,owned=owned)
                self.close_failed(owned[0])
            item=owned[0];self.assertEqual(calls,[item.original_fd]);self.assertFalse(item.writer_closed)
            self.assertTrue(item.reader_closed);os.fstat(item.original_fd);actual(item.original_fd)

    @unittest.skipUnless(hasattr(os,'fork'),'requires owned fork')
    def test_saved_writer_fd_zero_and_retained_reader_finality(self):
        with tempfile.TemporaryDirectory(dir=HERE,prefix='owned-save-fd0-')as td:
            path=Path(td)/'copy.py';pid=os.fork()
            if pid==0:
                code=1
                try:
                    os.close(0);owned=[];item=S.save(path,RAW,owned=owned);assert item.original_fd==0
                    assert item.writer_closed and item.original_reader is not None;S.finalize([],saved=owned);assert item.reader_closed;code=0
                finally:os._exit(code)
            _,status=os.waitpid(pid,0);self.assertEqual(os.waitstatus_to_exitcode(status),0)

    def test_completion_uses_retired_names_only_and_physically_closes_itself(self):
        with tempfile.TemporaryDirectory(dir=HERE,prefix='owned-completion-')as td:
            path=self.source(td,b'X');held=S.Held(path,(1,sha256(b'X').hexdigest()),owner=os.geteuid(),mode=0o644);S.finalize([held])
            completion=[];events=[]
            def clock():events.append('clock')
            with patch.object(held,'check',side_effect=AssertionError('RETIRED_CALLBACK')):
                S.completion_save(Path(td)/'completion.json',b'{"OWNED_PROTOCOL_ONLY":true}\n',[held],None,[],clock,completion)
            item=completion[0];self.assertTrue(item.writer_closed and item.reader_closed);self.assertTrue(events)
            self.assert_closed(item.original_fd);self.assert_closed(item.original_reader)

    def test_failed_completion_writer_replacement_still_checks_names_and_clock(self):
        with tempfile.TemporaryDirectory(dir=HERE,prefix='owned-completion-fail-')as td:
            path=self.source(td,b'X');held=S.Held(path,(1,sha256(b'X').hexdigest()),owner=os.geteuid(),mode=0o644);S.finalize([held])
            target=Path(td)/'completion.json';raw=b'{"OWNED_PROTOCOL_ONLY":true}\n';completion=[];actual=os.close;events=[]
            def clock():events.append('clock')
            def close(fd):
                item=completion[0];actual(fd)
                if fd==item.original_fd:
                    other=Path(td)/'replacement';other.write_bytes(raw);other.chmod(0o600);os.replace(other,target);events.append('replaced')
            with patch.object(held,'check',side_effect=AssertionError('RETIRED_CALLBACK')),patch.object(S.os,'close',side_effect=close):
                with self.assertRaises((RuntimeError,OSError)):S.completion_save(target,raw,[held],None,[],clock,completion)
            item=completion[0];self.assertTrue(item.writer_closed and item.reader_closed)
            self.assertGreater(len(events[events.index('replaced')+1:]),0);self.assertEqual(events[-1],'clock')
            self.assert_closed(item.original_reader)

    def test_all20_owned_source_copies_retain_readers_through_finalization(self):
        with tempfile.TemporaryDirectory(dir=HERE,prefix='owned19-')as td:
            root=Path(td);held=[];saved=[]
            def check():
                for item in held:
                    if item.fd is None:item.named()
                    else:item.check()
                for item in saved:
                    if item.original_fd is not None:item.check()
            for name,pin in S.SOURCES.items():
                source=HERE/'dependencies'/name
                if not source.exists():source=HERE/name
                if not source.exists():source=HERE.parents[2]/S.SOURCE_PATHS[name]
                original=S.Held(source,pin,owner=os.geteuid(),mode=0o644,scope=check);held.append(original)
                output=S.save(root/name,original.raw,owned=saved,check=check)
                copy=S.Held(root/name,pin,owner=os.geteuid(),mode=0o600,scope=check);held.append(copy)
                self.assertTrue(output.writer_closed);os.fstat(output.original_reader)
            self.assertEqual(len(saved),20);self.assertEqual(len(held),40)
            S.finalize(held,saved=saved)
            for item in saved:self.assertTrue(item.writer_closed and item.reader_closed)
            for item in held:self.assertIsNone(item.fd)

    def test_completion_final_clock_failure_follows_actual_reader_close(self):
        with tempfile.TemporaryDirectory(dir=HERE,prefix='owned-final-clock-')as td:
            completion=[];events=[]
            def clock():
                events.append('clock')
                if completion and completion[0].reader_closed:raise RuntimeError('original_deadline')
            with self.assertRaisesRegex(RuntimeError,'sealed_io_finality')as caught:
                S.completion_save(Path(td)/'completion.json',b'{"OWNED_PROTOCOL_ONLY":true}\n',[],None,[],clock,completion)
            item=completion[0];self.assertTrue(item.writer_closed and item.reader_closed)
            self.assertTrue(any(e.args==('original_deadline',)for e in flat(caught.exception)))
            self.assertEqual(events[-1],'clock');self.assert_closed(item.original_reader)

if __name__=='__main__':unittest.main(verbosity=2)
