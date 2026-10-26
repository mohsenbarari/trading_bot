"""Pure bounded DNS/owned-network checker fixtures; no namespace/socket is entered."""
from contextlib import ExitStack,redirect_stdout
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
import errno,io,ipaddress,json,socket,struct,unittest

ROOT=Path(__file__).resolve().parents[1]
DENIED=('subprocess.Popen','subprocess.run','os.system','os.popen','os.fork','os.kill','socket.socket',
 'socket.create_connection','socket.getaddrinfo','urllib.request.urlopen','ctypes.CDLL')
with ExitStack() as stack:
 for key in DENIED:stack.enter_context(patch(key,side_effect=AssertionError('ambient denied')))
 path=ROOT/'deploy/maintenance/normal_restore_namespace_check.py';M=ModuleType('_owned_ns_checker');M.__file__=str(path)
 exec(compile(path.read_bytes(),str(path),'exec'),M.__dict__)

class NamespaceCheckerTests(unittest.TestCase):
 def setUp(self):
  self.stack=ExitStack();self.addCleanup(self.stack.close)
  for key in DENIED:self.stack.enter_context(patch(key,side_effect=AssertionError('ambient denied')))
 def response(self,name='db',address='172.29.1.2',rcode=0):
  q=M.question(name);answer=b'\xc0\x0c'+struct.pack('!HHIH',1,1,30,4)+ipaddress.IPv4Address(address).packed if rcode==0 else b''
  return struct.pack('!HHHHHH',M.QUERY_ID,0x8180|rcode,1,int(bool(answer)),0,0)+q+answer
 def test_exact_direct_DNS_A_and_negative_response(self):
  self.assertEqual(M.answers(self.response(),'db'),(0,{'172.29.1.2'}))
  self.assertEqual(M.answers(self.response('normal-restore-egress-test.invalid',rcode=3),'normal-restore-egress-test.invalid'),(3,set()))
 def test_question_only_fixed_public_names(self):
  self.assertEqual(M.question('db'),b'\x02db\0\0\x01\0\x01')
  for value in ('PRIVATE.example','production','db.','localhost','../name'):
   with self.assertRaises(RuntimeError):M.question(value)
 def test_packet_identity_opcode_question_and_size_bounds(self):
  good=self.response()
  for raw in (good[:1],good+b'PRIVATE',good.replace(b'\x4e\x52',b'\0\0',1),
   good[:2]+struct.pack('!H',0x8380)+good[4:],good[:4]+struct.pack('!H',2)+good[6:],b'x'*4097):
   with self.assertRaises((RuntimeError,UnicodeError)):M.answers(raw,'db')
  with self.assertRaises(RuntimeError):M.answers(good,'redis')
 def test_compression_forward_cycle_and_non_ascii_refuse(self):
  for raw,offset in ((b'\xc0\0',0),(b'\xc0\x02\0',0),(b'\xff\xff',0),(b'\x01\xff\0',0)):
   with self.assertRaises((RuntimeError,UnicodeError)):M.name_at(raw,offset)
 def test_direct_DNS_uses_loopback_only_bounded_datagram_and_closes(self):
  events=[];raw=self.response()
  class Client:
   def __enter__(self):return self
   def __exit__(self,*args):events.append('closed')
   def settimeout(self,value):events.append(('timeout',value))
   def sendto(self,data,peer):events.append(('sent',peer));self.data=data
   def recvfrom(self,maximum):events.append(('bound',maximum));return raw,('127.0.0.11',53)
  with patch.object(M.socket,'socket',side_effect=lambda *a:Client()),patch('builtins.open',side_effect=AssertionError('no resolver files')):
   self.assertEqual(M.dns('db'),(0,{'172.29.1.2'}))
  self.assertIn(('sent',('127.0.0.11',53)),events);self.assertIn(('bound',4097),events);self.assertEqual(events[-1],'closed')
 def test_DNS_rejects_wrong_sender_and_overflow(self):
  for raw,peer in ((self.response(),('127.0.0.1',53)),(b'x'*4097,('127.0.0.11',53))):
   client=type('Client',(),{'__enter__':lambda s:s,'__exit__':lambda *a:None,'settimeout':lambda *a:None,'sendto':lambda *a:None,
    'recvfrom':lambda *a:(raw,peer)})()
   with patch.object(M.socket,'socket',return_value=client),self.assertRaises(RuntimeError):M.dns('db')
 def run_public(self,dns=None,connect=None):
  expected={'db':'172.29.1.2','redis':'172.29.1.3'};seen=[]
  def query(name):return (0,{expected[name]}) if name in expected else (3,set())
  def tcp(address,port):seen.append((address,port));return 0 if address in expected.values() else errno.ETIMEDOUT
  with patch.object(M,'dns',side_effect=dns or query),patch.object(M,'connect',side_effect=connect or tcp):result=M.run(expected,'172.29.1.1')
  return result,seen
 def test_actual_run_requires_positive_DNS_TCP_then_fixed_IPv4_IPv6_negatives(self):
  result,seen=self.run_public();self.assertEqual(result,{'schema':'normal_owned_namespace_check/1','clone_dns_and_tcp_passed':True,
   'reserved_dns_name_not_resolved':True,'fixed_ipv4_witnesses_blocked':True,'fixed_ipv6_witness_blocked':True,'security_accepted':False})
  self.assertEqual(seen[:2],[('172.29.1.2',5432),('172.29.1.3',6379)]);self.assertIn(('2001:db8::1',443),seen)
  self.assertEqual(len(seen),13)
 def test_failed_clone_DNS_or_TCP_cannot_be_a_negative_pass(self):
  for dns,connect in ((lambda name:(2,set()),None),(None,lambda *a:errno.ECONNREFUSED)):
   with self.assertRaises(RuntimeError):self.run_public(dns,connect)
 def test_external_positive_OR_connection_refused_are_not_block_proof(self):
  def query(name):return (0,{'172.29.1.2' if name=='db' else '172.29.1.3'})
  with self.assertRaises(RuntimeError):self.run_public(query,None)
  def tcp(address,port):return 0 if address in ('172.29.1.2','172.29.1.3') else errno.ECONNREFUSED
  with self.assertRaises(RuntimeError):self.run_public(None,tcp)
 def test_bad_public_inputs_refuse_before_any_network(self):
  for expected,gateway in (({'db':'127.0.0.1','redis':'172.29.1.3'},'172.29.1.1'),
   ({'db':'172.29.1.2','redis':'172.29.1.2'},'172.29.1.1'),({'db':'172.29.1.2','redis':'172.29.1.3','PRIVATE':'value'},'172.29.1.1')):
   with self.assertRaises(RuntimeError):M.run(expected,gateway)
 def test_cli_unknown_errors_are_closed_and_no_private_values(self):
  out=io.StringIO()
  with patch.object(M,'run',side_effect=OSError('PRIVATE_DATA')),redirect_stdout(out):
   self.assertEqual(M.main(['{}','172.29.1.1']),3)
  self.assertEqual(json.loads(out.getvalue())['status'],'isolation_refused');self.assertNotIn('PRIVATE',out.getvalue())

if __name__=='__main__':unittest.main()
