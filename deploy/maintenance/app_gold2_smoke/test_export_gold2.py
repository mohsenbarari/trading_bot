"""Owned successor I/O regressions; actual32 assertions stay in pinned source.

No real Docker executable is invoked. File/pipe FD tests are real kernel I/O.
"""
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from types import ModuleType,SimpleNamespace
from unittest.mock import patch
import ast,errno,fcntl,json,os,stat,subprocess,sys,tempfile,time,unittest
HERE=Path(__file__).absolute().parent

def load(path):
 m=ModuleType('owned_gold2_'+path.stem);m.__file__=str(path);exec(compile(path.read_bytes(),str(path),'exec'),m.__dict__);return m
E=load(HERE/'export_controller_gold2.py');S=load(HERE/'seal_and_run.py');Q=load(HERE/'controller.py')
def dependency(name):
 p=HERE/'dependencies'/name
 return p if p.exists()else HERE.parents[2]/S.SOURCE_PATHS[name]
def flat(error):return error.errors if type(error)is E.ExportIOFailure else(error,)

class Tests(unittest.TestCase):
 @contextmanager
 def fixture(self):
  with tempfile.TemporaryDirectory(prefix='owned-new-export-',dir=HERE)as td:
   root=Path(td);(root/'reports').mkdir();C=load(dependency('compose_security_os_candidate.py'));keeper=C.ProofKeeper(root);C.PROOF_KEEPER=keeper
   state=SimpleNamespace(root=root,C=C,keeper=keeper,output=None)
   try:yield state
   finally:
    try:keeper.close()
    except BaseException:pass
    for item in keeper.records:
     if type(item)is E.ExportFile:
      for original,closed in((item.original_writer,item.writer_closed),(item.original_reader,item.reader_closed)):
       if original is not None and closed:
        try:os.fstat(original)
        except OSError as error:self.assertEqual(error.errno,errno.EBADF)
        else:self.fail('owned original FD remained open')
 def output(self,f):f.output=E.ExportFile(f.C,f.keeper,f.root);return f.output
 def test_new_source_is_additive_and_historical15_exact(self):
  rows=json.loads((HERE/'source-manifest.json').read_bytes())['files'];self.assertEqual(len(rows),15);self.assertEqual(len(Q.PINS),15);self.assertEqual(len(S.SOURCES),19)
  for row in rows:
   raw=dependency(row['name']).read_bytes();self.assertEqual((len(raw),sha256(raw).hexdigest()),Q.PINS[row['name']]);self.assertEqual(S.SOURCES[row['name']],Q.PINS[row['name']])
  raw=(HERE/'export_controller_gold2.py').read_bytes();self.assertEqual(Q.GOLD2_EXPORT_PIN,(len(raw),sha256(raw).hexdigest()));self.assertEqual(S.SOURCES['export_controller_gold2.py'],Q.GOLD2_EXPORT_PIN)
  self.assertEqual(S.SOURCE_PATHS['export_controller_gold2.py'],'deploy/maintenance/app_gold2_smoke/export_controller_gold2.py');self.assertEqual(Q.CONFIRM,S.CONFIRM);self.assertTrue(Q.CONFIRM.endswith('20261029-04'))
 def test_actual32_assertions_and_wire_inventory_functions_unchanged(self):
  old=ast.parse(dependency('export_controller.py').read_bytes());new=ast.parse((HERE/'export_controller_gold2.py').read_bytes())
  for name in('create_args','inspect_template','admit_container','whole_image_inventory'):
   a=next(n for n in old.body if isinstance(n,ast.FunctionDef)and n.name==name);b=next(n for n in new.body if isinstance(n,ast.FunctionDef)and n.name==name);self.assertEqual(ast.dump(a),ast.dump(b))
  self.assertEqual(E.MAX_EXPORT,2*1024**3);self.assertEqual(E.MAX_STDERR,8192);self.assertEqual(E.MAX_CHUNK,1024**2);self.assertEqual(E.MAX_INVENTORY_SECONDS,600)
 def test_actual_megabyte_write_and_independent_ro_readback(self):
  with self.fixture()as f:
   o=self.output(f);raw=b'X'*E.MAX_CHUNK;o.write(raw,lambda:None);binding=o.freeze(lambda:None,time.monotonic()+30)
   self.assertEqual(binding['bytes'],len(raw));self.assertEqual(binding['sha256'],sha256(raw).hexdigest());self.assertIsNotNone(o.original_reader);self.assertFalse(o.writer_closed);self.assertEqual(fcntl.fcntl(o.original_reader,fcntl.F_GETFL)&os.O_ACCMODE,os.O_RDONLY)
   with o.borrow(lambda:None)as stream:self.assertEqual(stream.read(),raw)
   f.keeper.close();self.assertTrue(o.writer_closed and o.reader_closed)
 def test_partial_megabyte_writes_keep_actual_prefix_hash_and_guard_counts(self):
  with self.fixture()as f:
   o=self.output(f);actual=os.write;calls=[];guards=[]
   def short(fd,raw):calls.append(len(raw));return actual(fd,raw[:32768])
   with patch.object(E.os,'write',short):o.write(b'Z'*131072,lambda:guards.append(1))
   self.assertEqual(calls,[131072,98304,65536,32768]);self.assertEqual(len(guards),8);self.assertEqual(o.count,131072);self.assertEqual(o.before[6],131072);self.assertEqual(o.digest.hexdigest(),sha256(b'Z'*131072).hexdigest())
 def test_per_operation_and_total_bounds_precede_payload(self):
  for raw in(b'',b'X'*(E.MAX_CHUNK+1)):
   with self.subTest(size=len(raw)),self.fixture()as f:
    o=self.output(f)
    with patch.object(E.os,'write',side_effect=AssertionError('payload')):
     with self.assertRaisesRegex(RuntimeError,'flat_export_write'):o.write(raw,lambda:None)
  with self.fixture()as f:
   o=self.output(f)
   with patch.object(E,'MAX_EXPORT',511),patch.object(E.os,'write',side_effect=AssertionError('payload')):
    with self.assertRaisesRegex(RuntimeError,'flat_export_bound'):o.write(b'X'*512,lambda:None)
 def test_failed_write_and_immediate_writer_flag_postcheck_both_retained(self):
  with self.fixture()as f:
   o=self.output(f);body=OSError(errno.EIO,'OWNED_BODY');guards=[]
   def broken(fd,raw):fcntl.fcntl(fd,fcntl.F_SETFD,0);raise body
   with patch.object(E.os,'write',broken):
    with self.assertRaises(E.ExportIOFailure)as caught:o.write(b'X'*512,lambda:guards.append(1))
   self.assertIs(caught.exception.errors[0],body);self.assertTrue(any(e.args==('flat_export_changed',)for e in caught.exception.errors[1:]));self.assertEqual(len(guards),2);self.assertIs(o.errors[0],body)
 def test_failed_fsync_and_full9_name_postcheck_both_retained(self):
  with self.fixture()as f:
   o=self.output(f);o.write(b'X'*512,lambda:None);body=OSError(errno.EIO,'OWNED_FSYNC')
   def broken(fd):o.path.chmod(0o644);raise body
   with patch.object(E.os,'fsync',broken):
    with self.assertRaises(E.ExportIOFailure)as caught:o.freeze(lambda:None,time.monotonic()+30)
   self.assertIs(caught.exception.errors[0],body);self.assertTrue(any(e.args==('flat_export_changed',)for e in caught.exception.errors[1:]))
 def test_readback_pread_failure_and_reader_flag_postguard_both_retained(self):
  with self.fixture()as f:
   o=self.output(f);o.write(b'X'*512,lambda:None);body=OSError(errno.EIO,'OWNED_PREAD')
   def broken(fd,size,offset):fcntl.fcntl(fd,fcntl.F_SETFD,0);raise body
   with patch.object(E.os,'pread',broken):
    with self.assertRaises(E.ExportIOFailure)as caught:o.freeze(lambda:None,time.monotonic()+30)
   self.assertIs(caught.exception.errors[0],body);self.assertTrue(any(e.args==('flat_export_reader_changed',)for e in caught.exception.errors[1:]));self.assertFalse(o.frozen)
 def test_readback_corruption_cannot_freeze(self):
  with self.fixture()as f:
   o=self.output(f);o.write(b'X'*512,lambda:None);actual=os.pread
   def bad(fd,size,offset):raw=actual(fd,size,offset);return b'Y'+raw[1:]if raw else raw
   with patch.object(E.os,'pread',bad):
    with self.assertRaisesRegex(RuntimeError,'flat_export_readback'):o.freeze(lambda:None,time.monotonic()+30)
   self.assertFalse(o.frozen)
 def test_readback_eof_is_independent_payload_operation(self):
  with self.fixture()as f:
   o=self.output(f);o.write(b'X'*512,lambda:None);actual=os.pread;events=[]
   def read(fd,size,offset):events.append((size,offset));return actual(fd,size,offset)
   with patch.object(E.os,'pread',read):o.freeze(lambda:None,time.monotonic()+30)
   self.assertEqual(events,[(512,0),(1,512)])
 def test_original_readback_deadline_is_never_extended(self):
  with self.fixture()as f:
   o=self.output(f);o.write(b'X'*512,lambda:None)
   with patch.object(E.os,'fsync',side_effect=AssertionError('IO')):
    with self.assertRaises(E.ExportIOFailure)as caught:o.freeze(lambda:None,time.monotonic()-1)
   self.assertTrue(all(e.args==('flat_export_timeout',)for e in caught.exception.errors));self.assertFalse(o.reader_attempted)
 def test_original_writer_replaced_by_same_bytes_at_close_stays_failed(self):
  with self.fixture()as f:
   o=self.output(f);raw=b'X'*512;o.write(raw,lambda:None);actual=os.close;events=[]
   def close(fd):
    if fd==o.original_writer:
     self.assertIsNotNone(o.original_reader);os.fstat(o.original_reader);events.append('writer');o.path.rename(o.path.with_suffix('.old'));o.path.write_bytes(raw);o.path.chmod(0o600)
    if fd==o.original_reader:events.append('reader')
    return actual(fd)
   with patch.object(E.os,'close',close):
    with self.assertRaises(E.ExportIOFailure):o.close()
   self.assertEqual(events,['writer','reader']);self.assertTrue(o.writer_closed and o.reader_closed)
 def test_unknown_writer_close_before_or_after_is_not_retried(self):
  for after in(False,True):
   with self.subTest(after=after),self.fixture()as f:
    o=self.output(f);o.write(b'X'*512,lambda:None);actual=os.close;calls=[];body=OSError(errno.EIO,'UNKNOWN_CLOSE')
    def unknown(fd):
     if fd==o.original_writer:
      calls.append(fd)
      if after:actual(fd)
      raise body
     return actual(fd)
    with patch.object(E.os,'close',unknown):
     with self.assertRaises((E.ExportIOFailure,OSError))as caught:o.close()
     self.assertTrue(any(e is body for e in E.causes(caught.exception)))
     with self.assertRaisesRegex(RuntimeError,'flat_export_close_unknown'):o.close()
    self.assertEqual(calls,[o.original_writer]);self.assertTrue(o.reader_closed);self.assertFalse(o.writer_closed)
    if not after:actual(o.original_writer)
 def test_original_writer_flags_not_adopted_after_frozen_reader(self):
  with self.fixture()as f:
   o=self.output(f);o.write(b'X'*512,lambda:None);o.freeze(lambda:None,time.monotonic()+30);fcntl.fcntl(o.original_writer,fcntl.F_SETFD,0)
   with self.assertRaisesRegex(RuntimeError,'flat_export_changed'):o.check()
 def test_original_ro_reader_flags_not_adopted(self):
  with self.fixture()as f:
   o=self.output(f);o.write(b'X'*512,lambda:None);o.freeze(lambda:None,time.monotonic()+30);fcntl.fcntl(o.original_reader,fcntl.F_SETFD,0)
   with self.assertRaisesRegex(RuntimeError,'flat_export_reader_changed'):o.check()
 def test_frozen_output_never_accepts_new_writes(self):
  with self.fixture()as f:
   o=self.output(f);o.write(b'X'*512,lambda:None);o.freeze(lambda:None,time.monotonic()+30)
   with self.assertRaisesRegex(RuntimeError,'flat_export_write'):o.write(b'Y'*512,lambda:None)
 def test_borrow_failure_preserves_body_and_immediate_original_reader_postguard(self):
  with self.fixture()as f:
   o=self.output(f);o.write(b'X'*512,lambda:None);o.freeze(lambda:None,time.monotonic()+30);body=OSError(errno.EIO,'OWNED_BORROW')
   with self.assertRaises(E.ExportIOFailure)as caught:
    with o.borrow(lambda:None):fcntl.fcntl(o.original_reader,fcntl.F_SETFD,0);raise body
   self.assertIs(caught.exception.errors[0],body);self.assertTrue(any(e.args==('flat_export_reader_changed',)for e in caught.exception.errors[1:]))
 def test_failed_partial_constructor_is_published_before_original_fd_checks(self):
  with self.fixture()as f:
   actual=fcntl.fcntl
   def broken(fd,op,*args):
    value=actual(fd,op,*args)
    return 0 if op==fcntl.F_GETFD else value
   with patch.object(E.fcntl,'fcntl',broken):
    with self.assertRaisesRegex(RuntimeError,'flat_export_metadata'):E.ExportFile(f.C,f.keeper,f.root)
   self.assertEqual(len(f.keeper.records),1);o=f.keeper.records[0];self.assertIsNotNone(o.original_writer);self.assertTrue(o.errors)
 def test_every_original_error_stays_failed_before_later_payload(self):
  with self.fixture()as f:
   o=self.output(f);body=OSError(errno.EIO,'OLD');o.note(body)
   with patch.object(E.os,'write',side_effect=AssertionError('payload')):
    with self.assertRaises(OSError)as caught:o.write(b'X'*512,lambda:None)
   self.assertIs(caught.exception,body)
 def test_real_original_pipe_fifo7_flags_and_close_once(self):
  r,w=os.pipe();stream=os.fdopen(r,'rb',buffering=0)
  try:
   pipe=E.Pipe(stream,fcntl.fcntl(r,fcntl.F_GETPIPE_SZ));pipe.check();fcntl.fcntl(r,fcntl.F_SETFD,0)
   with self.assertRaisesRegex(RuntimeError,'flat_export_pipe_changed'):pipe.check()
   with self.assertRaisesRegex(RuntimeError,'flat_export_pipe_changed'):pipe.close()
   self.assertTrue(pipe.closed)
   with self.assertRaisesRegex(RuntimeError,'flat_export_close_unknown'):pipe.close()
  finally:
   if not stream.closed:stream.close()
   os.close(w)
 def test_guarded_read_preserves_body_and_finally_postcheck_failures(self):
  body=OSError(errno.EIO,'BODY');post=RuntimeError('POST');events=[]
  def guard():
   events.append('guard')
   if len(events)>2:raise post
  def read():events.append('read');raise body
  with self.assertRaises(E.ExportIOFailure)as caught:E.guarded(guard,read)
  self.assertEqual(events,['guard','read','guard']);self.assertIs(caught.exception.errors[0],body);self.assertIs(caught.exception.errors[1],post)
 def test_closed_default_entry_does_not_spawn(self):
  with patch.object(E.subprocess,'Popen',side_effect=AssertionError('spawn')):
   with self.assertRaisesRegex(RuntimeError,'flat_export_stage'):E.cli(None,SimpleNamespace(DOCKER=['WRONG']),'export',image='sha256:'+'a'*64)
 def test_failed_readback_cannot_be_retried_or_borrowed(self):
  with self.fixture()as f:
   o=self.output(f);o.write(b'X'*512,lambda:None);actual=os.pread
   def bad(fd,size,offset):raw=actual(fd,size,offset);return b'Y'+raw[1:]if raw else raw
   with patch.object(E.os,'pread',bad):
    with self.assertRaisesRegex(RuntimeError,'flat_export_readback'):o.freeze(lambda:None,time.monotonic()+30)
   self.assertTrue(o.errors);original=o.errors[0]
   with patch.object(E.os,'pread',side_effect=AssertionError('retry')):
    with self.assertRaises(RuntimeError)as caught:o.freeze(lambda:None,time.monotonic()+30)
    self.assertIs(caught.exception,original)
   with self.assertRaises(RuntimeError)as caught:
    with o.borrow(lambda:None):self.fail('failed forward admitted')
   self.assertIs(caught.exception,original)

 def test_borrow_same_bytes_distinct_inode_with_exact_flags_is_refused(self):
  with self.fixture()as f:
   o=self.output(f);o.write(b'X'*512,lambda:None);o.freeze(lambda:None,time.monotonic()+30);other=f.root/'foreign';other.write_bytes(b'X'*512);other.chmod(0o600);fd=os.open(other,os.O_RDONLY|os.O_NONBLOCK|os.O_CLOEXEC)
   try:
    with self.assertRaises(E.ExportIOFailure)as caught:
     with o.borrow(lambda:None)as stream:
      original_flags=fcntl.fcntl(stream.fileno(),fcntl.F_GETFL),fcntl.fcntl(stream.fileno(),fcntl.F_GETFD);os.dup2(fd,stream.fileno(),inheritable=False);self.assertEqual((fcntl.fcntl(stream.fileno(),fcntl.F_GETFL),fcntl.fcntl(stream.fileno(),fcntl.F_GETFD)),original_flags)
    self.assertTrue(any(e.args==('flat_export_borrow_changed',)for e in caught.exception.errors));os.fstat(fd);self.assertFalse(o.original_loans[0].closed);self.assertTrue(o.original_loans[0].held);self.assertTrue(o.errors);os.fstat(o.original_loans[0].original)
   finally:
    if o.original_loans and o.original_loans[0].original_stream is not None:o.original_loans[0].original_stream.close()
    os.close(fd)
 def test_borrow_clear_cloexec_independently_refuses(self):
  with self.fixture()as f:
   o=self.output(f);o.write(b'X'*512,lambda:None);o.freeze(lambda:None,time.monotonic()+30);original_reader_flags=fcntl.fcntl(o.original_reader,fcntl.F_GETFD)
   with self.assertRaises(E.ExportIOFailure)as caught:
    with o.borrow(lambda:None)as stream:fcntl.fcntl(stream.fileno(),fcntl.F_SETFD,0)
   self.assertTrue(any(e.args==('flat_export_borrow_changed',)for e in caught.exception.errors));self.assertEqual(fcntl.fcntl(o.original_reader,fcntl.F_GETFD),original_reader_flags);self.assertFalse(o.original_loans[0].closed);self.assertTrue(o.original_loans[0].held);os.fstat(o.original_loans[0].original);o.original_loans[0].original_stream.close()
 def test_borrow_body_and_loan_postcheck_causes_preserved_and_sticky(self):
  with self.fixture()as f:
   o=self.output(f);o.write(b'X'*512,lambda:None);o.freeze(lambda:None,time.monotonic()+30);body=OSError(errno.EIO,'BODY')
   with self.assertRaises(E.ExportIOFailure)as caught:
    with o.borrow(lambda:None)as stream:fcntl.fcntl(stream.fileno(),fcntl.F_SETFD,0);raise body
   self.assertIs(caught.exception.errors[0],body);self.assertTrue(any(e.args==('flat_export_borrow_changed',)for e in caught.exception.errors[1:]));self.assertIs(o.errors[0],body)
   with self.assertRaises(E.ExportIOFailure)as again:
    with o.borrow(lambda:None):self.fail('failed borrow admitted')
   self.assertIs(again.exception.errors[0],body);self.assertEqual(len(o.original_loans),1);self.assertTrue(o.original_loans[0].held);o.original_loans[0].original_stream.close()
 def test_dup_failure_retains_published_prefix_and_original_reader(self):
  with self.fixture()as f:
   o=self.output(f);o.write(b'X'*512,lambda:None);o.freeze(lambda:None,time.monotonic()+30);body=OSError(errno.EMFILE,'DUP')
   with patch.object(E.os,'dup',side_effect=body):
    with self.assertRaises(OSError)as caught:
     with o.borrow(lambda:None):self.fail('borrow admitted')
   self.assertIs(caught.exception,body);self.assertEqual(len(o.original_loans),1);self.assertIsNone(o.original_loans[0].original);self.assertTrue(o.original_loans[0].closed);os.fstat(o.original_reader)
 def test_fdopen_failure_holds_the_original_published_duplicate_without_fabricated_close(self):
  with self.fixture()as f:
   o=self.output(f);o.write(b'X'*512,lambda:None);o.freeze(lambda:None,time.monotonic()+30);body=OSError(errno.EIO,'FDOPEN');actual=os.dup;dups=[]
   def duplicate(fd):n=actual(fd);dups.append(n);return n
   with patch.object(E.os,'dup',duplicate),patch.object(E.os,'fdopen',side_effect=body):
    with self.assertRaises(E.ExportIOFailure)as caught:
     with o.borrow(lambda:None):self.fail('borrow admitted')
   self.assertIs(caught.exception.errors[0],body);self.assertTrue(any(e.args==('flat_export_borrow_held',)for e in caught.exception.errors));self.assertEqual(len(dups),1);self.assertTrue(o.original_loans[0].held);self.assertFalse(o.original_loans[0].closed);os.fstat(dups[0]);os.fstat(o.original_reader)
   # Separate fixture disposal of the exact owned dup whose creation we saw;
   # component authority intentionally stays HELD and never retries this close.
   os.close(dups[0])
 def test_fdopen_physically_closed_then_raises_never_replays_close(self):
  with self.fixture()as f:
   o=self.output(f);o.write(b'X'*512,lambda:None);o.freeze(lambda:None,time.monotonic()+30);body=OSError(errno.EIO,'FDOPEN_AFTER_CLOSE');actual=os.fdopen;actual_close=os.close;calls=[]
   def broken(fd,*args,**kwargs):stream=actual(fd,*args,**kwargs);stream.close();raise body
   def close(fd):calls.append(fd);return actual_close(fd)
   with patch.object(E.os,'fdopen',broken),patch.object(E.os,'close',close):
    with self.assertRaises(E.ExportIOFailure)as caught:
     with o.borrow(lambda:None):self.fail('borrow admitted')
   loan=o.original_loans[0];self.assertIs(caught.exception.errors[0],body);self.assertNotIn(loan.original,calls);self.assertTrue(loan.closed)
 def test_wrapped_stream_alias_substitution_is_not_adopted(self):
  with self.fixture()as f:
   o=self.output(f);o.write(b'X'*512,lambda:None);o.freeze(lambda:None,time.monotonic()+30);other=f.root/'foreign';other.write_bytes(b'Y'*512);fd=os.open(other,os.O_RDONLY|os.O_CLOEXEC);actual=os.fdopen
   class Alias:
    def __init__(self,stream):self.actual=stream;self.alias=None
    def fileno(self):return self.actual.fileno()if self.alias is None else self.alias
    def seek(self,*args):return self.actual.seek(*args)
    def close(self):return self.actual.close()
   def wrapped(*args,**kwargs):return Alias(actual(*args,**kwargs))
   try:
    with patch.object(E.os,'fdopen',wrapped):
     with self.assertRaises(E.ExportIOFailure)as caught:
      with o.borrow(lambda:None)as stream:stream.alias=fd
    self.assertTrue(any(e.args==('flat_export_borrow_changed',)for e in caught.exception.errors));os.fstat(fd);self.assertFalse(o.original_loans[0].closed);self.assertTrue(o.original_loans[0].held)
   finally:
    if o.original_loans and o.original_loans[0].original_stream is not None:o.original_loans[0].original_stream.close()
    os.close(fd)
 def test_borrow_close_return_without_physical_close_cannot_succeed(self):
  with self.fixture()as f:
   o=self.output(f);o.write(b'X'*512,lambda:None);o.freeze(lambda:None,time.monotonic()+30);saved=[];calls=[]
   try:
    with self.assertRaisesRegex(RuntimeError,'flat_export_close_unknown'):
     with o.borrow(lambda:None):
      loan=o.original_loans[0];saved.append(loan.closer);loan.closer=lambda:calls.append('returned_without_close')
    self.assertEqual(calls,['returned_without_close']);self.assertFalse(loan.closed);self.assertTrue(loan.held);os.fstat(loan.original)
    with self.assertRaisesRegex(RuntimeError,'flat_export_close_unknown'):loan.close()
    self.assertEqual(calls,['returned_without_close']);self.assertTrue(o.errors)
   finally:
    if saved:saved[0]()
 def test_borrow_unknown_close_before_or_after_never_reissues(self):
  for after in(False,True):
   with self.subTest(after=after),self.fixture()as f:
    o=self.output(f);o.write(b'X'*512,lambda:None);o.freeze(lambda:None,time.monotonic()+30);body=OSError(errno.EIO,'UNKNOWN_BORROW_CLOSE');saved=[];calls=[]
    try:
     with self.assertRaises((OSError,E.ExportIOFailure))as caught:
      with o.borrow(lambda:None):
       loan=o.original_loans[0];saved.append(loan.closer)
       def broken():
        calls.append(1)
        if after:saved[0]()
        raise body
       loan.closer=broken
     self.assertIs(E.causes(caught.exception)[0],body);self.assertEqual(calls,[1]);self.assertEqual(loan.closed,after)
     if not after:self.assertTrue(any(e.args==('flat_export_close_unknown',)for e in E.causes(caught.exception)[1:]))
     with self.assertRaisesRegex(RuntimeError,'flat_export_close_unknown'):loan.close()
     self.assertEqual(calls,[1]);self.assertIs(o.errors[0],body)
    finally:
     if saved and not after:saved[0]()

 def test_failed_fdopen_reused_number_never_closes_foreign_replacement(self):
  for same_inode,same_flags in((False,True),(True,False),(True,True)):
   with self.subTest(same_inode=same_inode,same_flags=same_flags),self.fixture()as f:
    o=self.output(f);o.write(b'X'*512,lambda:None);o.freeze(lambda:None,time.monotonic()+30);other=f.root/'foreign';other.write_bytes(b'X'*512);other.chmod(0o600);body=OSError(errno.EIO,'FDOPEN_REUSED');actual_close=os.close;calls=[];replacement=[];reader_offset=os.lseek(o.original_reader,0,os.SEEK_CUR)
    def close(fd):calls.append(fd);return actual_close(fd)
    def reused(fd,*args,**kwargs):
     os.close(fd);new=os.open(o.path if same_inode else other,os.O_RDONLY|os.O_NONBLOCK|os.O_CLOEXEC);self.assertEqual(new,fd);replacement.append(new)
     if not same_flags:fcntl.fcntl(new,fcntl.F_SETFD,0)
     if same_inode:
      self.assertEqual(E.identity(os.fstat(new)),o.before);os.lseek(new,17,os.SEEK_SET);self.assertEqual(os.lseek(o.original_reader,0,os.SEEK_CUR),reader_offset)
     raise body
    try:
     with patch.object(E.os,'fdopen',reused),patch.object(E.os,'close',close):
      with self.assertRaises(E.ExportIOFailure)as caught:
       with o.borrow(lambda:None):self.fail('borrow admitted')
     loan=o.original_loans[0];self.assertEqual(calls.count(loan.original),1);self.assertIs(caught.exception.errors[0],body);self.assertTrue(loan.held);self.assertFalse(loan.closed);self.assertFalse(loan.admitted);os.fstat(replacement[0])
     if same_inode:self.assertEqual(os.lseek(replacement[0],0,os.SEEK_CUR),17)
     self.assertTrue(any(e.args==('flat_export_borrow_held',)for e in caught.exception.errors))
     with self.assertRaisesRegex(RuntimeError,'flat_export_close_unknown'):loan.close()
     os.fstat(replacement[0])
    finally:
     if replacement:actual_close(replacement[0])
 def test_replacement_before_bound_stream_close_is_held_without_calling_closer(self):
  with self.fixture()as f:
   o=self.output(f);o.write(b'X'*512,lambda:None);o.freeze(lambda:None,time.monotonic()+30);other=f.root/'foreign';other.write_bytes(b'Y'*512);other.chmod(0o600);calls=[];replacement=[];saved=[]
   try:
    with self.assertRaises(E.ExportIOFailure)as caught:
     with o.borrow(lambda:None)as stream:
      loan=o.original_loans[0];saved.append(loan.closer);original=stream.fileno();os.close(original);new=os.open(other,os.O_RDONLY|os.O_NONBLOCK|os.O_CLOEXEC);self.assertEqual(new,original);replacement.append(new)
      def closer():calls.append(1);return saved[0]()
      loan.closer=closer
    self.assertEqual(calls,[]);self.assertTrue(loan.held);self.assertFalse(loan.closed);self.assertTrue(loan.admitted);os.fstat(replacement[0]);self.assertTrue(any(e.args==('flat_export_borrow_held',)for e in caught.exception.errors))
   finally:
    # Only this independent fixture created the replacement and may dispose it.
    if saved:saved[0]()

class RealCLI(unittest.TestCase):
 """Full new collector on actual UID1001 pipes, public owned producer only."""
 fixture=Tests.fixture
 output=Tests.output
 def setUp(self):
  self.assertEqual(os.geteuid(),1001,'RealCLI requires genuine UID1001, never a permission mask')
  self.assertEqual(os.getegid(),1001)
 def context(self,f):
  ctx=SimpleNamespace(root=f.root,C=f.C,end=time.monotonic()+30)
  def check():f.keeper.check()
  ctx.check=check;return ctx
 def old(self):return SimpleNamespace(DOCKER=['/usr/bin/docker','--host=unix:///var/run/docker.sock'],owner=lambda:'OWNED_ONLY',LABEL='owned.public.protocol')
 def popen(self,program,children):
  actual=subprocess.Popen
  def spawn(argv,**kwargs):
   self.assertEqual(argv[:2],self.old().DOCKER);child=actual([sys.executable,'-I','-S','-B','-c',program],**kwargs);children.append(child);return child
  return spawn
 def assert_retired(self,children):
  self.assertEqual(len(children),1);self.assertIsNotNone(children[0].returncode);self.assertTrue(children[0].stdout.closed and children[0].stderr.closed)
 def test_real_create_pipe_EOF_and_original_wire(self):
  with self.fixture()as f:
   ctx=self.context(f);children=[]
   with patch.object(E.subprocess,'Popen',self.popen("import os;os.write(1,b'a'*64+bytes([10]))",children)):
    self.assertEqual(E.cli(ctx,self.old(),'create',image='sha256:'+'b'*64),b'a'*64+b'\n')
   self.assert_retired(children);report=json.loads((f.root/'reports/flat-export-cli-create.json').read_bytes());self.assertTrue(report['pipe_original_descriptions_physically_closed']);self.assertEqual(report['io_errors'],[])
 def test_real_export16MiB_megabyte_chunks_hash_EOF_and_reader_before_collectors(self):
  with self.fixture()as f:
   ctx=self.context(f);o=self.output(f);children=[];closed=[];actual=E.Pipe.close
   def close(pipe):self.assertIsNotNone(o.original_reader);os.fstat(o.original_reader);self.assertFalse(o.writer_closed);closed.append(pipe.fd);return actual(pipe)
   program="import os\nb=b'Z'*1048576\nfor _ in range(16):\n v=memoryview(b)\n while v:\n  n=os.write(1,v);v=v[n:]\n"
   with patch.object(E.subprocess,'Popen',self.popen(program,children)),patch.object(E.Pipe,'close',close):binding=E.cli(ctx,self.old(),'export',image='sha256:'+'b'*64,cid='a'*64,output=o)
   self.assert_retired(children);self.assertEqual(binding['bytes'],16*1024**2);self.assertEqual(binding['sha256'],sha256(b'Z'*(16*1024**2)).hexdigest());self.assertEqual(len(closed),2)
   report=json.loads((f.root/'reports/flat-export-cli-export.json').read_bytes());self.assertEqual(report['pipe_capacities'],[1048576,1048576]);self.assertLessEqual(report['stdout_read_calls_including_EOF'],48);self.assertEqual(report['io_errors'],[]);self.assertTrue(report['pipe_original_descriptions_physically_closed']);self.assertEqual(report['original_stage_seconds'],300)
 def test_real_stdout_limit_refuses_before_retaining_overflow(self):
  with self.fixture()as f:
   ctx=self.context(f);children=[]
   with patch.object(E.subprocess,'Popen',self.popen("import os;os.write(1,b'Z'*257)",children)):
    with self.assertRaisesRegex(RuntimeError,'flat_export_stdout_bound'):E.cli(ctx,self.old(),'create',image='sha256:'+'b'*64)
   self.assert_retired(children);report=json.loads((f.root/'reports/flat-export-cli-create.json').read_bytes());self.assertEqual(report['stdout_bytes_observed'],257);self.assertEqual(report['io_errors'][0]['code'],'flat_export_stdout_bound')
 def test_real_stderr_limit_refuses_before_retaining_overflow(self):
  with self.fixture()as f:
   ctx=self.context(f);children=[]
   with patch.object(E.subprocess,'Popen',self.popen("import os;os.write(2,b'Z'*8193)",children)):
    with self.assertRaisesRegex(RuntimeError,'flat_export_stderr_bound'):E.cli(ctx,self.old(),'create',image='sha256:'+'b'*64)
   self.assert_retired(children);report=json.loads((f.root/'reports/flat-export-cli-create.json').read_bytes());self.assertEqual(report['stderr_bytes_observed'],8193);self.assertEqual(report['stderr_text'],'');self.assertTrue(report['stderr_truncated'])
 def test_real_read_error_and_original_pipe_flag_postcheck_both_retained(self):
  with self.fixture()as f:
   ctx=self.context(f);children=[];body=OSError(errno.EIO,'OWNED_READ');actual=os.read
   def broken(fd,size):
    if children and fd==children[0].stdout.fileno():fcntl.fcntl(fd,fcntl.F_SETFD,0);raise body
    return actual(fd,size)
   with patch.object(E.subprocess,'Popen',self.popen("import os;os.write(1,b'a'*64+bytes([10]))",children)),patch.object(E.os,'read',broken):
    with self.assertRaises(E.ExportIOFailure)as caught:E.cli(ctx,self.old(),'create',image='sha256:'+'b'*64)
   self.assertIs(caught.exception.errors[0],body);self.assertTrue(any(e.args==('flat_export_pipe_changed',)for e in caught.exception.errors[1:]));self.assert_retired(children)
 def test_real_export_deadline_remains_original_and_owned_child_reaped(self):
  with self.fixture()as f:
   ctx=self.context(f);ctx.end=time.monotonic()+.02;o=self.output(f);children=[]
   with patch.object(E.subprocess,'Popen',self.popen("import time;time.sleep(5)",children)):
    with self.assertRaisesRegex(RuntimeError,'flat_export_timeout'):E.cli(ctx,self.old(),'export',image='sha256:'+'b'*64,cid='a'*64,output=o)
   self.assert_retired(children);self.assertIsNotNone(o.original_reader);self.assertFalse(o.frozen);self.assertEqual(o.count,0)
 def test_real_export_capacity_tamper_refuses_without_fallback(self):
  with self.fixture()as f:
   ctx=self.context(f);o=self.output(f);children=[];actual=self.popen("import os;os.write(1,b'Z'*512)",children)
   def wrong(argv,**kwargs):self.assertEqual(kwargs['pipesize'],1048576);kwargs['pipesize']=65536;return actual(argv,**kwargs)
   with patch.object(E.subprocess,'Popen',wrong):
    with self.assertRaisesRegex(RuntimeError,'flat_export_pipe_changed'):E.cli(ctx,self.old(),'export',image='sha256:'+'b'*64,cid='a'*64,output=o)
   self.assert_retired(children);self.assertFalse(o.frozen)
 def assert_original_output_denies_forward(self,o,first):
  self.assertIs(o.errors[0],first)
  for action in('write','freeze','borrow'):
   with self.subTest(action=action),self.assertRaises(BaseException)as caught:
    if action=='write':o.write(b'Z'*512,lambda:None)
    elif action=='freeze':o.freeze(lambda:None,time.monotonic()+30)
    else:
     with o.borrow(lambda:None):self.fail('failed borrow admitted')
   self.assertIs(E.causes(caught.exception)[0],first)
 def test_real_export_read_failure_latches_same_original_output(self):
  with self.fixture()as f:
   ctx=self.context(f);o=self.output(f);children=[];body=OSError(errno.EIO,'READ_BODY');actual=os.read
   def broken(fd,size):
    if children and fd==children[0].stdout.fileno():raise body
    return actual(fd,size)
   with patch.object(E.subprocess,'Popen',self.popen("import os;os.write(1,b'Z'*512)",children)),patch.object(E.os,'read',broken):
    with self.assertRaises(OSError)as caught:E.cli(ctx,self.old(),'export',image='sha256:'+'b'*64,cid='a'*64,output=o)
   self.assertIs(caught.exception,body);self.assert_retired(children);self.assert_original_output_denies_forward(o,body)
 def test_real_nonzero_child_latches_original_output(self):
  with self.fixture()as f:
   ctx=self.context(f);o=self.output(f);children=[]
   with patch.object(E.subprocess,'Popen',self.popen("import sys;sys.exit(7)",children)):
    with self.assertRaisesRegex(RuntimeError,'flat_export_client')as caught:E.cli(ctx,self.old(),'export',image='sha256:'+'b'*64,cid='a'*64,output=o)
   self.assert_retired(children);self.assert_original_output_denies_forward(o,caught.exception)
 def test_real_collector_close_after_physical_close_latches_frozen_output(self):
  with self.fixture()as f:
   ctx=self.context(f);o=self.output(f);children=[];body=OSError(errno.EIO,'CLOSE_AFTER');actual=E.Pipe.close;once=[]
   def broken(pipe):
    actual(pipe)
    if not once:once.append(pipe.fd);raise body
   with patch.object(E.subprocess,'Popen',self.popen("import os;os.write(1,b'Z'*512)",children)),patch.object(E.Pipe,'close',broken):
    with self.assertRaises(OSError)as caught:E.cli(ctx,self.old(),'export',image='sha256:'+'b'*64,cid='a'*64,output=o)
   self.assertIs(caught.exception,body);self.assertTrue(o.frozen);self.assert_retired(children);self.assert_original_output_denies_forward(o,body)
 def test_real_report_publication_failure_latches_frozen_output(self):
  with self.fixture()as f:
   ctx=self.context(f);o=self.output(f);children=[];body=OSError(errno.EIO,'REPORT_WRITE');actual=f.C.write
   def broken(path,*args,**kwargs):
    if Path(path).name=='flat-export-cli-export.json':raise body
    return actual(path,*args,**kwargs)
   with patch.object(E.subprocess,'Popen',self.popen("import os;os.write(1,b'Z'*512)",children)),patch.object(f.C,'write',broken):
    with self.assertRaises(OSError)as caught:E.cli(ctx,self.old(),'export',image='sha256:'+'b'*64,cid='a'*64,output=o)
   self.assertIs(caught.exception,body);self.assertTrue(o.frozen);self.assert_retired(children);self.assert_original_output_denies_forward(o,body)
 def test_real_read_postguard_and_selector_close_preserve_three_original_causes(self):
  with self.fixture()as f:
   ctx=self.context(f);o=self.output(f);children=[];body=OSError(errno.EIO,'READ_BODY');post=RuntimeError('POST_GUARD');close_error=OSError(errno.EIO,'SELECTOR_CLOSE');state={'post':False};actual_read=os.read;actual_selector=E.selectors.DefaultSelector;closed=[];original_ctx=ctx.check
   def check():
    original_ctx()
    if state['post']:state['post']=False;raise post
   ctx.check=check
   def broken(fd,size):
    if children and fd==children[0].stdout.fileno():state['post']=True;raise body
    return actual_read(fd,size)
   class Selector:
    def __init__(self):self.actual=actual_selector();self.original=self.actual.fileno()
    def fileno(self):return self.actual.fileno()
    def register(self,*args):return self.actual.register(*args)
    def get_map(self):return self.actual.get_map()
    def select(self,*args):return self.actual.select(*args)
    def unregister(self,*args):return self.actual.unregister(*args)
    def close(self):
     closed.append(self.original);self.actual.close()
     try:os.fstat(self.original)
     except OSError as error:self.closed_proven=error.errno==errno.EBADF
     else:self.closed_proven=False
     if not self.closed_proven:raise AssertionError('owned selector remains open')
     raise close_error
   with patch.object(E.subprocess,'Popen',self.popen("import os;os.write(1,b'Z'*512)",children)),patch.object(E.os,'read',broken),patch.object(E.selectors,'DefaultSelector',Selector):
    with self.assertRaises(E.ExportIOFailure)as caught:E.cli(ctx,self.old(),'export',image='sha256:'+'b'*64,cid='a'*64,output=o)
   self.assertEqual(caught.exception.errors,(body,post,close_error));self.assertEqual(len(closed),1);self.assert_retired(children);self.assert_original_output_denies_forward(o,body);self.assertTrue(any(e is post for e in o.errors));self.assertTrue(any(e is close_error for e in o.errors))
if __name__=='__main__':unittest.main(verbosity=2)
