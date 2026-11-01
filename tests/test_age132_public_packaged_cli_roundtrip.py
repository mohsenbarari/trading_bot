"""Real ProductHeld + producer/wire/consumer, external CLI operations UNIT mocks.

The identity is the exact published upstream x25519.txt fixture. No crypto,
Go/age/dpkg binary, network, operational identity or real data executes here.
Future genuine CI qualification remains required even when these tests pass.
"""
from contextlib import ExitStack
from copy import deepcopy
import errno,fcntl,hashlib,json,os,tempfile,time
from pathlib import Path
from types import ModuleType
import unittest

ROOT=Path(__file__).absolute().parents[1]
CODE=ROOT/'scripts/security_age132_tool'
def frame(s):return(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def load(name):
 path=CODE/name;fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
 try:
  before=frame(os.fstat(fd));flags=(fcntl.fcntl(fd,fcntl.F_GETFL),fcntl.fcntl(fd,fcntl.F_GETFD));raw=os.pread(fd,131073,0)
  assert 0<len(raw)==before[6]<=131072 and frame(os.fstat(fd))==frame(path.lstat())==before and flags==(fcntl.fcntl(fd,fcntl.F_GETFL),fcntl.fcntl(fd,fcntl.F_GETFD))and os.lseek(fd,0,os.SEEK_CUR)==0
 finally:os.close(fd)
 try:os.fstat(fd)
 except OSError as error:assert error.errno==errno.EBADF
 else:raise AssertionError('original Source close')
 assert frame(path.lstat())==before
 module=ModuleType('owned_public_CLI_'+name);module.__file__=str(path);exec(compile(raw,str(path),'exec'),module.__dict__);return module

class PublicPackagedCLITests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.C=load('contracts.py');cls.W=load('worker.py');cls.M=load('host_io_original.py')
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(prefix='owned-public-age-CLI-',dir=ROOT);self.addCleanup(self.temp.cleanup)
  self.root=Path(self.temp.name);self.root.chmod(0o700)
  for path in ['age-output','package/usr/bin']:(self.root/path).mkdir(mode=0o700,parents=True,exist_ok=True)
  self.products=[];self.calls=[];self.fault=None;self.fail_finality=False;self.addCleanup(self.finish_products)
  self.fixture=(CODE/'public-x25519-test-fixture.txt').read_bytes();self.deadline=time.monotonic()+60
  self.binaries={name:self.save(self.root/'age-output'/name,('UNIT public executable fixture '+name+'\n').encode(),0o755)for name in('age','age-keygen')}
  self.staged={name:self.save(self.root/'package/usr/bin'/name,r.body(),0o755)for name,r in self.binaries.items()}
 def hold(self,path,maximum,pin=None,allow_empty=False):
  r=self.W.ProductHeld.__new__(self.W.ProductHeld);self.products.append(r);self.W.ProductHeld.__init__(r,self.M,path,maximum,pin,allow_empty);return r
 def save(self,path,body,mode=0o600):
  fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,mode)
  try:
   view=memoryview(body)
   while view:n=os.write(fd,view);self.assertGreater(n,0);view=view[n:]
   os.fchmod(fd,mode);os.fsync(fd)
  finally:os.close(fd)
  return self.hold(path,32*1024**2,(len(body),hashlib.sha256(body).hexdigest()))
 def finish_products(self):
  for row in reversed(self.products):
   if row.closed:continue
   row.close();self.assertTrue(row.physical_EBADF)
   with self.assertRaises(OSError)as error:os.fstat(row.original)
   self.assertEqual(error.exception.errno,errno.EBADF)
 def guard(self):
  if self.fail_finality:raise RuntimeError('UNIT_source_finality_failure')
  for r in self.products:r.check()
 def command(self,argv,name,maximum):
  self.assertEqual(maximum,4096);self.assertIn(name,['packaged-public-keygen-derive','packaged-public-encrypt','packaged-public-decrypt']);self.guard();self.calls.append((argv,name))
  root=self.root;_,recipient=self.C.public_cli_fixture(self.fixture)
  if name=='packaged-public-keygen-derive':
   self.assertEqual(argv,[str(root/'package/usr/bin/age-keygen'),'-y','-o',str(root/'PUBLIC-PACKAGED-RECIPIENT.txt'),str(root/'PUBLIC-UPSTREAM-TEST-IDENTITY.txt')])
   (root/'PUBLIC-PACKAGED-RECIPIENT.txt').write_bytes(b'age1wrong\n'if self.fault=='recipient'else recipient)
  elif name=='packaged-public-encrypt':
   self.assertEqual(argv,[str(root/'package/usr/bin/age'),'--encrypt','--recipient',recipient.strip().decode(),'--output',str(root/'PUBLIC-PACKAGED-CIPHERTEXT.age'),str(root/'PUBLIC-PACKAGED-PLAINTEXT.txt')])
   # UNIT data only, no encryption. Real CLI wire is required in future CI.
   (root/'PUBLIC-PACKAGED-CIPHERTEXT.age').write_bytes(b'age-encryption.org/v1\n-> X25519 UNIT\n'+b'U'*100+b'\n--- UNIT\n')
  else:
   self.assertEqual(argv,[str(root/'package/usr/bin/age'),'--decrypt','--identity',str(root/'PUBLIC-UPSTREAM-TEST-IDENTITY.txt'),'--output',str(root/'PUBLIC-PACKAGED-RESTORED.txt'),str(root/'PUBLIC-PACKAGED-CIPHERTEXT.age')])
   if self.fault=='decrypt':raise RuntimeError('UNIT_failed_decrypt')
   (root/'PUBLIC-PACKAGED-RESTORED.txt').write_bytes(b'wrong'if self.fault=='plaintext'else self.C.PUBLIC_CLI_PAYLOAD)
   if self.fault=='finality':self.fail_finality=True
  return b'not allowed'if self.fault=='stdout'else b''
 def produce(self):return self.W.public_packaged_roundtrip(self.C,self.root,self.staged,self.binaries,self.fixture,self.command,self.save,self.hold,self.guard,self.deadline)
 def expected(self):return{name:{'bytes':r.pin[0],'sha256':r.pin[1]}for name,r in self.staged.items()}
 def test_owned_producer_serialized_wire_real_consumer_passes_without_crypto(self):
  value=self.produce();wire=self.C.encoded(value);parsed=json.loads(wire)
  self.assertEqual(self.C.public_packaged_cli_roundtrip(parsed,self.expected()),parsed);self.assertEqual(len(self.calls),3)
  self.assertTrue(value['CLI_decrypt_roundtrip_proven']);self.assertTrue(value['provisional_until_outer_Source_and_product_finality'])
  self.assertFalse(value['runtime_install_allowed']);self.assertFalse(value['backup_eligibility']);self.assertFalse(value['security_accepted'])
  self.assertNotIn(b'AGE-SECRET-KEY-',wire);self.assertFalse(value['raw_identity_exported'])
 def test_wrong_recipient_refuses_before_encrypt(self):
  self.fault='recipient'
  with self.assertRaisesRegex(RuntimeError,'public_CLI_recipient_equality'):self.produce()
  self.assertEqual(len(self.calls),1)
 def test_failed_decrypt_produces_no_success_receipt(self):
  self.fault='decrypt'
  with self.assertRaisesRegex(RuntimeError,'UNIT_failed_decrypt'):self.produce()
  self.assertEqual(len(self.calls),3)
 def test_wrong_restored_bytes_refuse(self):
  self.fault='plaintext'
  with self.assertRaisesRegex(RuntimeError,'public_CLI_plaintext_equality'):self.produce()
 def test_guard_finality_failure_cannot_become_success(self):
  self.fault='finality'
  try:
   with self.assertRaisesRegex(RuntimeError,'UNIT_source_finality_failure'):self.produce()
  finally:self.fail_finality=False
 def test_original_budget_error_precedes_any_command(self):
  self.deadline=time.monotonic()-1
  with self.assertRaisesRegex(RuntimeError,'public_CLI_deadline'):self.produce()
  self.assertFalse(self.calls)
 def test_wrong_staged_binary_hash_refuses_before_reading_fixture(self):
  wrong=self.root/'package/usr/bin/age';old=self.staged['age'];old.close();self.products.remove(old);wrong.unlink()
  self.staged['age']=self.save(wrong,b'UNIT deliberately different delivered payload\n',0o755)
  with self.assertRaisesRegex(RuntimeError,'public_CLI_delivered_binary_pin'):self.produce()
  self.assertFalse(self.calls)
 def test_exact_fixture_pin_and_no_existing_input_or_output_adoption(self):
  self.fixture+=b'\n'
  with self.assertRaisesRegex(RuntimeError,'public_CLI_fixture_pin'):self.produce()
  self.fixture=self.fixture[:-1];(self.root/'PUBLIC-PACKAGED-CIPHERTEXT.age').symlink_to(self.root/'does-not-exist')
  with self.assertRaisesRegex(RuntimeError,'public_CLI_path_preexists'):self.produce()
  self.assertFalse(self.calls)
 def test_unexpected_stdout_is_not_retained_as_key_or_accepted(self):
  self.fault='stdout'
  with self.assertRaisesRegex(RuntimeError,'public_CLI_unexpected_stdout'):self.produce()
 def test_consumer_refuses_forged_success_process_recipient_payload_and_authority(self):
  value=self.produce();mutations=[lambda x:x.update(unexpected=True),lambda x:x['CLI_files']['age'].update(sha256='0'*64),
   lambda x:x['commands'][2].update(actual_returncode=1),lambda x:x['commands'][2].update(actual_returncode=True),
   lambda x:x['commands'][2].update(stdout_eof_observed=False),lambda x:x['commands'][2].update(actual_pipe_closed=False),
   lambda x:x['commands'][2].update(stdout_bytes=1),lambda x:x['restored'].update(sha256='0'*64),lambda x:x['derived_recipient'].update(sha256='0'*64),
   lambda x:x.update(provisional_until_outer_Source_and_product_finality=False),lambda x:x.update(security_accepted=True),lambda x:x.update(backup_eligibility=True)]
  for i,mutate in enumerate(mutations):
   with self.subTest(mutation=i):
    changed=deepcopy(value);mutate(changed)
    with self.assertRaises(RuntimeError):self.C.public_packaged_cli_roundtrip(changed,self.expected())

if __name__=='__main__':unittest.main(verbosity=2)
