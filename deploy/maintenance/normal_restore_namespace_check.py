"""Public no-secret network checks, entered only into an owned clone netns.

No App imports, environment reads, resolver files, mounts or container profiles.
The parent admits the namespace/lifecycle and bounds/reaps this owned client.
"""
import errno,ipaddress,json,socket,struct,sys

MAX_PACKET=4096
QUERY_ID=0x4E52
ALLOWED_NAMES=frozenset(('db','redis','normal-restore-egress-test.invalid'))
BLOCKED=frozenset((errno.ENETUNREACH,errno.EHOSTUNREACH,errno.ETIMEDOUT,errno.EAGAIN))

def need(ok):
 if not ok:raise RuntimeError('namespace_check_refused')
def question(name):
 need(name in ALLOWED_NAMES)
 return b''.join(bytes((len(part),))+part.encode('ascii') for part in name.split('.'))+b'\0'+struct.pack('!HH',1,1)
def name_at(raw,offset,seen=()):
 need(type(offset) is int and 0<=offset<len(raw) and len(seen)<16 and offset not in seen)
 parts=[];end=None;start=offset
 for _ in range(128):
  need(offset<len(raw));length=raw[offset];offset+=1
  if length==0:return '.'.join(parts).lower(),offset if end is None else end
  if length&0xC0==0xC0:
   need(offset<len(raw));pointer=((length&0x3f)<<8)|raw[offset]
   need(pointer<offset-1);suffix,_=name_at(raw,pointer,seen+(start,));parts.append(suffix)
   return '.'.join(parts).lower(),offset+1 if end is None else end
  need(0<length<=63 and offset+length<=len(raw));label=raw[offset:offset+length].decode('ascii','strict')
  need(all(c.isalnum() or c in '-_' for c in label));parts.append(label);offset+=length
 raise RuntimeError('namespace_check_refused')
def answers(raw,name):
 need(type(raw) is bytes and 12<=len(raw)<=MAX_PACKET and name in ALLOWED_NAMES)
 ident,flags,qd,an,ns,ar=struct.unpack('!HHHHHH',raw[:12])
 need(ident==QUERY_ID and flags&0x8000 and not flags&0x7A00 and qd==1 and an<=32 and ns<=32 and ar<=32)
 asked,offset=name_at(raw,12);need(asked==name and raw[offset:offset+4]==struct.pack('!HH',1,1));offset+=4
 addresses=[]
 for i in range(an+ns+ar):
  owner,offset=name_at(raw,offset);need(offset+10<=len(raw))
  typ,cls,ttl,length=struct.unpack('!HHIH',raw[offset:offset+10]);offset+=10;need(offset+length<=len(raw))
  if i<an and typ==1:
   need(cls==1 and length==4 and owner==name);addresses.append(str(ipaddress.IPv4Address(raw[offset:offset+4])))
  offset+=length
 need(offset==len(raw));return flags&15,set(addresses)
def dns(name):
 packet=struct.pack('!HHHHHH',QUERY_ID,0x0100,1,0,0,0)+question(name)
 with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as client:
  client.settimeout(1);client.sendto(packet,('127.0.0.11',53));raw,peer=client.recvfrom(MAX_PACKET+1)
  need(peer==('127.0.0.11',53));return answers(raw,name)
def connect(address,port):
 family=socket.AF_INET6 if ':' in address else socket.AF_INET
 with socket.socket(family,socket.SOCK_STREAM) as client:
  client.settimeout(.5)
  try:return client.connect_ex((address,port))
  except TimeoutError:return errno.ETIMEDOUT
def run(expected,gateway):
 need(type(expected) is dict and set(expected)=={'db','redis'} and type(gateway) is str)
 values=[ipaddress.ip_address(v) for v in [*expected.values(),gateway]]
 need(all(v.version==4 and v.is_private and not v.is_loopback for v in values) and len(set(values))==3)
 for role,port in (('db',5432),('redis',6379)):
  rc,found=dns(role);need(rc==0 and found=={expected[role]});need(connect(expected[role],port)==0)
 try:
  rc,found=dns('normal-restore-egress-test.invalid');need(not found and rc in (2,3,5))
 except socket.timeout:pass
 for address in (gateway,'192.0.2.1'):
  for port in (22,443,5432,6379,8000):need(connect(address,port) in BLOCKED)
 need(connect('2001:db8::1',443) in BLOCKED)
 return {'schema':'normal_owned_namespace_check/1','clone_dns_and_tcp_passed':True,'reserved_dns_name_not_resolved':True,
  'fixed_ipv4_witnesses_blocked':True,'fixed_ipv6_witness_blocked':True,'security_accepted':False}
def main(argv=None):
 args=sys.argv[1:] if argv is None else argv
 try:
  need(len(args)==2 and len(args[0])<=256 and len(args[1])<=64);value=run(json.loads(args[0]),args[1])
  print(json.dumps(value,sort_keys=True));return 0
 except Exception:
  print('{"schema":"normal_owned_namespace_check/1","status":"isolation_refused","security_accepted":false}');return 3
if __name__=='__main__':raise SystemExit(main())
