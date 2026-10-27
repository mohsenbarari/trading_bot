"""Benign owned stdlib checks for the new product microrelease. Default UNARMED.
No network, application import, archive extraction, persistent test data or DB.
A source-unit pass cannot substitute for execution on an actual final image.
"""
from hashlib import sha256
from pathlib import Path
import argparse,json,os,platform,stat,struct,sys
EXPECTED=(3,11,18)
EXECUTABLE=Path('/usr/local/bin/python3.11')
def need(ok,code):
 if not ok:raise RuntimeError(code)
def frame(s):return(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def elf_header(raw):
 need(type(raw)is bytes and len(raw)==64 and raw[:7]==b'\x7fELF\x02\x01\x01','runtime_elf_header')
 need(struct.unpack_from('<H',raw,18)[0]==62 and struct.unpack_from('<H',raw,16)[0]in(2,3),'runtime_amd64_elf')
 return {'class':64,'machine':'EM_X86_64','byte_order':'little'}
def interpreter():
 need(platform.python_implementation()=='CPython'and tuple(sys.version_info[:3])==EXPECTED,'runtime_microrelease')
 need(sys.flags.isolated==1 and sys.flags.no_site==1 and sys.dont_write_bytecode and sys.prefix==sys.base_prefix=='/usr/local','runtime_isolated_image_layout')
 need(platform.machine()=='x86_64'and struct.calcsize('P')==8 and sys.byteorder=='little'and Path(sys.executable).resolve(strict=True)==EXECUTABLE,'runtime_platform')
 before=frame(EXECUTABLE.lstat());need(EXECUTABLE.resolve(strict=True)==EXECUTABLE and stat.S_ISREG(before[2])and before[3:6]==(0,0,1)and not before[2]&0o022 and 64<=before[6]<=32*1024**2,'runtime_executable_metadata')
 fd=os.open(EXECUTABLE,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
 try:
  need(frame(os.fstat(fd))==before,'runtime_executable_identity');raw=os.read(fd,64);value=elf_header(raw);need(frame(os.fstat(fd))==frame(EXECUTABLE.lstat())==before,'runtime_executable_changed')
 finally:os.close(fd)
 need(frame(EXECUTABLE.lstat())==before,'runtime_executable_closed_changed')
 return {'version':list(EXPECTED),'implementation':'CPython','executable':str(EXECUTABLE),'elf':value,'actual_header_sha256':sha256(raw).hexdigest(),'actual_full9':list(before)}
def observe():
 actual=interpreter()
 import pyexpat,ssl,tarfile,urllib.request,http.cookiejar
 from email.message import EmailMessage
 from email.policy import SMTP
 need(pyexpat.EXPAT_VERSION=='expat_2.9.0'and tuple(pyexpat.version_info)==(2,9,0),'runtime_bundled_expat')
 events=[];parser=pyexpat.ParserCreate();parser.StartElementHandler=lambda name,attrs:events.append(name)
 parser.Parse(b'<owned><value>safe</value></owned>',True);need(events==['owned','value'],'benign_xml_parse')
 member=tarfile.TarInfo('owned.txt');member.size=5;member.mode=0o644
 filtered=tarfile.tar_filter(member,'/owned-stdlib-fixture')
 need(filtered is not None and filtered.name=='owned.txt'and filtered.isreg(),'benign_tar_filter')
 # This is an in-memory invalid cookie boundary on an owned .invalid host,
 # with no cross-site payload, connection, response or real user cookie.
 cookie=http.cookiejar.Cookie(version=-1,name='owned',value='safe',port=None,port_specified=False,domain='owned.invalid',domain_specified=False,domain_initial_dot=False,
  path='/',path_specified=True,secure=True,expires=None,discard=True,comment=None,comment_url=None,rest={})
 jar=http.cookiejar.CookieJar();jar.set_cookie(cookie);request=urllib.request.Request('https://owned.invalid/');jar.add_cookie_header(request)
 need(request.get_header('Cookie')is None,'negative_cookie_version_rejected')
 message=EmailMessage(policy=SMTP);message['Subject']='Owned safe header';message.set_content('owned body')
 rendered=message.as_bytes();need(len(rendered)<4096 and b'Subject: Owned safe header' in rendered,'benign_email_folding')
 return {'schema':'python_microrelease_owned_runtime/1','status':'PASS','actual_runtime':actual,'actual_expat':pyexpat.EXPAT_VERSION,
  'actual_openssl':ssl.OPENSSL_VERSION,'checks':{'benign_xml_parse':True,'benign_regular_tar_filter':True,'negative_cookie_version_rejected':True,'benign_email_folding':True},
  'actual_checks':4,'network_used':False,'archive_extracted':False,'persistent_test_data_written':False,'application_or_DB_used':False,'exploit_or_fault_payload_used':False,
  'CVE_specific_ssl_handshake_performed':False,'whole_image_security_proven':False,'security_accepted':False}
def main(argv=None):
 p=argparse.ArgumentParser();p.add_argument('--image-self-check',action='store_true');args=p.parse_args(argv)
 if not args.image_self_check:print(json.dumps({'status':'UNARMED','checks_executed':False,'security_accepted':False},sort_keys=True));return 0
 try:value=observe()
 except (RuntimeError,OSError,ValueError)as error:
  code=error.args[0]if type(error)is RuntimeError and len(error.args)==1 and type(error.args[0])is str and error.args[0].startswith(('runtime_','benign_','negative_'))else'probe_unproven'
  print(json.dumps({'schema':'python_microrelease_owned_runtime/1','status':'FAIL','reason':code,'security_accepted':False},sort_keys=True));return 1
 raw=(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode();need(len(raw)<=8192,'runtime_receipt_bound');sys.stdout.buffer.write(raw);return 0
if __name__=='__main__':raise SystemExit(main())
