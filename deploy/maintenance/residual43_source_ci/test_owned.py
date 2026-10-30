"""Explicitly OWNED protocol fixtures; never CVE proof or activation authority."""
from pathlib import Path
from hashlib import sha256
from io import BytesIO
import argparse,bz2,gzip,json,lzma,os,subprocess,sys,tarfile
HERE=Path(__file__).absolute().parent
def enc(v):return(json.dumps(v,sort_keys=True,separators=(',',':'))+'\n').encode()
def need(v,c):
 if not v:raise AssertionError(c)
def tar(rows):
 b=BytesIO()
 with tarfile.open(fileobj=b,mode='w',format=tarfile.GNU_FORMAT)as t:
  for name,data,kind in rows:
   m=tarfile.TarInfo(name);m.mode=0o644;m.uid=m.gid=0;m.type=kind
   if kind in{tarfile.SYMTYPE,tarfile.LNKTYPE}:m.linkname=data.decode();m.size=0;t.addfile(m)
   else:m.size=len(data);t.addfile(m,BytesIO(data))
 return b.getvalue()
def corrupt(b,at):
 at=at if at>=0 else len(b)+at;return b[:at]+bytes([b[at]^1])+b[at+1:]
def profile(name,b):return{'name':name,'bytes':len(b),'sha256':sha256(b).hexdigest(),'kind':'upstream_orig_archive','official_url':'https://deb.debian.org/debian/pool/main/o/owned/'+name,'exact_compressed_max':len(b),'separate_large_role':False,'expanded_max':16777216,'headers_max':100,'regular_member_max':8388608,'names_max':1048576,'metadata_max':4194304,'decoder_memory_max':134217728,'source_line_max':1048576,'nested_GCC_basename':None,'nested_expanded_max':16777216,'nested_headers_max':100}
def fixture(root,name,b,changes=None,package='owned'):
 directory=root/name;directory.mkdir(mode=0o700);filename='owned.tar.xz'if name.startswith('xz')else'owned.tar.bz2'if name.startswith('bz')else'owned.tar.gz'if name.startswith('gz')or name.startswith('nested')else'owned.tar';p=profile(filename,b);p.update(changes or{});(directory/filename).write_bytes(b);(directory/filename).chmod(0o600)
 g={'schema':'EXPLICITLY_OWNED_SYNTHETIC_SOURCE_PROTOCOL_FIXTURE/1','source_package':package,'source_version':'OWNED','architecture':'OWNED','current_bodies':[{'index':0,'path':'/OWNED/libdemo.so.1','bytes':0,'sha256':'0'*64,'symlink_aliases':[]}],'current_full_owner_variants':[],'source_role_question':'OWNED synthetic protocol fixture only; no current image body or CVE authority.','profiles':[p],'seconds_max':60,'all_selected_complete_SOURCE_DATA_max':4194304,'review_packet_max':524288,'metadata_DATA_shard_max':4194304};path=directory/'fixture.json';path.write_bytes(enc(g));path.chmod(0o600);return path,g
def cli(path,out):
 raw=path.read_bytes();args=[sys.executable,'-B',str(HERE/'collector.py'),'--owned-fixture-plan',str(path),'--fixture-plan-sha256',sha256(raw).hexdigest(),'--collector-sha256',sha256((HERE/'collector.py').read_bytes()).hexdigest(),'--source-io-sha256',sha256((HERE/'source_io.py').read_bytes()).hexdigest(),'--output',str(out)];r=subprocess.run(args,capture_output=True,text=True,timeout=70);need(r.stdout.strip(),'actual_CLI_stdout');last=json.loads(r.stdout.strip().splitlines()[-1]);return r,last
def fault(root,name,code):
 p=root/(name+'.txt');p.write_bytes(b'OWNED original\n');p.chmod(0o600)
 prefix="from pathlib import Path\nfrom hashlib import sha256\nfrom unittest.mock import patch\nimport os,json,errno\nimport source_io as I\np=Path("+repr(str(p))+ ")\ns=I.Scope(20)\npin=(p.stat().st_size,sha256(p.read_bytes()).hexdigest())\n"
 r=subprocess.run([sys.executable,'-B','-c',prefix+code],cwd=HERE,capture_output=True,text=True,timeout=25);need(r.returncode==0,'owned_fault_child:'+name+':'+r.stderr[-400:]);return{'case':name,'owned_child_exit':0,'kernel_retirement_after_expected_failure':True,'normal_physical_PASS_inferred_from_failed_prefix':False,'stdout':r.stdout.strip()}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--qa-root',required=True,type=Path);ap.add_argument('--require-hosted-owner',action='store_true');args=ap.parse_args();root=args.qa_root.absolute();need(not os.path.lexists(root),'fresh_owned_QA');root.mkdir(mode=0o700)
 if args.require_hosted_owner:need(os.getuid()==os.geteuid()==1001,'actual_hosted_UID1001_required')
 rows=[('owned/Makefile.am',b'libdemo_la_SOURCES = demo.c\n',tarfile.REGTYPE),('owned/demo.c',b'/* RISC-V table literal: acl_get_file */\nconst char *name="acl_get_file";\n',tarfile.REGTYPE),('owned/blob.bin',b'\x00\xff'*(200000),tarfile.REGTYPE),('owned/link',b'../../never-open',tarfile.SYMTYPE)];raw=tar(rows);results=[]
 cases=[('raw-ok',raw,{},True),('gz-ok',gzip.compress(raw),{},True),('bz-ok',bz2.compress(raw),{},True),('xz-ok',lzma.compress(raw),{},True),('gz-CRC',corrupt(gzip.compress(raw),-8),{},False),('gz-truncated',gzip.compress(raw)[:-7],{},False),('gz-trailing',gzip.compress(raw)+b'BAD',{},False),('gz-concatenated',gzip.compress(raw)*2,{},False),('xz-trailing',lzma.compress(raw)+b'BAD',{},False),('bz-truncated',bz2.compress(raw)[:-4],{},False),('raw-nonzero-tail',raw+b'BAD',{},False),('raw-header-quota',raw,{'headers_max':1},False),('raw-member-quota',raw,{'regular_member_max':8},False),('raw-expanded-quota',raw,{'expanded_max':512},False),('raw-metadata-quota',raw,{'metadata_max':8},False),('raw-line-quota',raw,{'source_line_max':8},False),('raw-duplicate',tar([('same.c',b'a\n',tarfile.REGTYPE),('same.c',b'b\n',tarfile.REGTYPE)]),{},False),('raw-traversal',tar([('../escape',b'OWNED',tarfile.REGTYPE)]),{},False),('raw-long-GNU-metadata',tar([('././@LongLink',b'a'*131073,tarfile.GNUTYPE_LONGNAME)]),{},False),('raw-sparse',tar([('sparse',b'',tarfile.GNUTYPE_SPARSE)]),{},False)]
 with tarfile.open(fileobj=BytesIO(raw),mode='r:')as t:members=t.getmembers();end=max(m.offset_data+((m.size+511)//512)*512 for m in members)
 cases.extend([('raw-no-end-blocks',raw[:end],{},False),('raw-one-end-block',raw[:end]+b'\0'*512,{},False)])
 for name,body in [('xz-corrupt',lzma.compress(raw)),('bz-corrupt',bz2.compress(raw))]:cases.append((name,corrupt(body,-12 if name.startswith('xz')else 10),{},False))
 cases.extend([('xz-concatenated',lzma.compress(raw)*2,{},False),('bz-concatenated',bz2.compress(raw)*2,{},False)])
 eof_acl=b'int f(void) { return acl_get_file("OWNED", 0); }'
 cases.append(('raw-unseeded-ACL-no-LF',tar([('owned/unseeded.c',eof_acl,tarfile.REGTYPE)]),{},True))
 inner=lzma.compress(tar([('gcc-14.2.0/libgcc/Makefile.in',b'libgcc_s: unwind-dw2.c\n',tarfile.REGTYPE),('gcc-14.2.0/libgcc/unwind-dw2.c',b'/* OWNED unwind role */\n',tarfile.REGTYPE)]));nested=gzip.compress(tar([('gcc-source/gcc-14.2.0-dfsg.tar.xz',inner,tarfile.REGTYPE)]));cases.append(('nested-gcc',nested,{'nested_GCC_basename':'gcc-14.2.0-dfsg.tar.xz'},True));cases.append(('nested-missing',gzip.compress(raw),{'nested_GCC_basename':'gcc-14.2.0-dfsg.tar.xz'},False))
 for name,b,changes,expected in cases:
  path,g=fixture(root,name,b,changes,'gcc-14'if name.startswith('nested')else'owned');r,last=cli(path,root/(name+'-out'));need((r.returncode==0)==expected,'actual_CLI_expected:'+name+':'+str(last));need(last.get('status')!='UNARMED'and last['security_accepted']is False,'actual_CLI_no_UNARMED_or_authority');record={'case':name,'actual_child_exit':r.returncode,'expected_success':expected,'status':last['status'],'code':last.get('code'),'source_only_fixture':True}
  if expected:
   review=json.loads(Path(last['review']['path']).read_text());need(review['schema']=='OWNED_SYNTHETIC_SOURCE_PROTOCOL_FIXTURE/1'and review['source_property_or_no_ACL_classification_granted']is False,'synthetic_no_current_authority');need(all(v['physical_EBADF']for v in last['physical_original_and_publication_finalizers'])and last['physical_errors']==[],'real_original_physical_finals');record['physical_originals']=len(last['physical_original_and_publication_finalizers']);record['full_archive_EOF_and_collector_close']=all(x['XZ_gzip_bzip2_integrity_and_exact_EOF']and x['collectors_closed']for x in review['exact_archive_finalities']);need(record['full_archive_EOF_and_collector_close'],'real_collector_finality')
   if name=='raw-ok':
    inventory=[x for ref in review['full_inventory_and_code_build_census']['full-member-inventory']for x in json.loads(Path(ref['path']).read_text())['rows']];need(any(x['member'].endswith('blob.bin')and x['bytes']==400000 for x in inventory),'binary_DATA_hashed_without_line_parser');need(any(x['type']=='2'and not x['link_followed']for x in inventory),'symlink_recorded_never_followed')
   if name=='nested-gcc':need(len(review['exact_archive_finalities'])==2 and{v['depth']for v in review['exact_archive_finalities']}=={0,1},'real_nested_archive_path')
   if name=='raw-unseeded-ACL-no-LF':
    code=[x for ref in review['full_inventory_and_code_build_census']['code-build-census']for x in json.loads(Path(ref['path']).read_text())['rows']];row=next(x for x in code if x['member']=='owned/unseeded.c');hit=row['candidate_lines'][0];need(hit['categories']==['ACL_provider_or_copy_candidate']and hit['newline_present']is False and hit['line_bytes']==len(eof_acl)and hit['line_sha256']==sha256(eof_acl).hexdigest(),'EOF_actual_ACL_categories_and_byte_identity');selected=json.loads(Path(review['selected_complete_source_index']['path']).read_text())['selected'];entry=next(x for x in selected if x['member']=='owned/unseeded.c');body=b''.join(bytes.fromhex(json.loads(Path(ref['path']).read_text())['hex'])for ref in entry['chunks']);need(entry['complete_source']and body==eof_acl and entry['sha256']==sha256(eof_acl).hexdigest(),'EOF_complete_unseeded_source_selection')
  else:need(last['normal_physical_finality_proven']is False,'failed_scope_not_pass')
  results.append(record)
 faultcodes={
 'constructor-fstat':"with patch('source_io.os.fstat',side_effect=OSError(errno.EIO,'OWNED')):\n try:s.open(p,pin,1024)\n except OSError:pass\nr=s.rows[0];assert r.original_fd is not None and r.uncertain and not r.admitted and s.errors\nprint('original captured; failed prefix HELD; kernel retires owned child')\n",
 'constructor-fcntl':"with patch('source_io.fcntl.fcntl',side_effect=OSError(errno.EIO,'OWNED')):\n try:s.open(p,pin,1024)\n except OSError:pass\nr=s.rows[0];assert r.original_fd is not None and r.uncertain and not r.admitted and s.errors\nprint('flags failure retained captured prefix')\n",
 'constructor-pread':"with patch('source_io.os.pread',side_effect=OSError(errno.EIO,'OWNED')):\n try:s.open(p,pin,1024)\n except OSError:pass\nr=s.rows[0];assert r.original_fd is not None and r.uncertain and s.errors and not r.closed\nprint('read failure latched; no normal close')\n",
 'acquisition-prefix':"with patch('source_io.os.open',side_effect=OSError(errno.EACCES,'OWNED')):\n try:s.open(p,pin,1024)\n except OSError:pass\nr=s.rows[0];assert r.original_fd is None and r.uncertain and not r.admitted and s.errors\nprint('published pre-open prefix retained')\n",
 'writer-constructor-fstat':"with patch('source_io.os.fstat',side_effect=OSError(errno.EIO,'OWNED')):\n try:s.create(p.parent/'exclusive-output',1024)\n except OSError:pass\nr=s.rows[0];assert r.original_fd is not None and r.uncertain and not r.admitted\nprint('writer captured prefix retained')\n",
 'noop-close':"r=s.open(p,pin,1024)\nwith patch('source_io.os.close',return_value=None):\n try:r.close()\n except RuntimeError:pass\nassert r.attempted and r.held and r.uncertain and not r.physical_EBADF and not r.closed and s.errors\nprint('no-op close refused; no retry')\n",
 'second-close':"r=s.open(p,pin,1024);real=os.close\nwith patch('source_io.os.close',wraps=real)as close:\n r.close()\n try:r.close();raise AssertionError('replay accepted')\n except RuntimeError:pass\n assert close.call_count==1\nassert r.closed and r.physical_EBADF and s.errors\nfor fn in [s.check,s.finish]:\n try:fn();raise AssertionError('latched close-entry failure accepted')\n except RuntimeError:pass\nassert not s.finished\nprint('one actual close; second refusal latched; no later scope PASS')\n",
 'changed-closed-output':"r=s.open(p,pin,1024);r.close();p.write_bytes(b'OWNED mutation')\ntry:s.finish();raise AssertionError('changed final name accepted')\nexcept RuntimeError:pass\nprint('final full9/named check refused mutation')\n",
 'expired-scope':"r=s.open(p,pin,1024);s.end-=60\ntry:r.read(1,0);raise AssertionError('expired scope accepted')\nexcept RuntimeError:pass\nassert r.uncertain and s.errors\nprint('immutable original clock refused changed deadline')\n"}
 for name,code in faultcodes.items():results.append(fault(root,name,code))
 for condition in ['failed','expired','actor-changed']:
  code="""real=os.open
condition=CONDITION
if condition=='failed':s.note(RuntimeError('OWNED prior failure'))
elif condition=='expired':s.end-=60
else:s.actor=(-1,-1)
out=p.parent/'must-not-create'
with patch('source_io.os.open',wraps=real)as opened:
 for operation in [lambda:s.open(p,pin,1024),lambda:s.create(out,1024)]:
  try:operation();raise AssertionError('invalid scope acquired a file')
  except RuntimeError:pass
 assert opened.call_count==0
assert not out.exists()and not s.rows and not s.active and s.errors
print('both open/create refused before any os.open, prefix, FD or file')
""".replace('CONDITION',repr(condition));results.append(fault(root,'pre-acquisition-'+condition,code))
 for kind in ['positive','length','overread','short','hash','close-noop']:
  directory=root/('response-'+kind);directory.mkdir(mode=0o700)
  code="""from pathlib import Path
from hashlib import sha256
from unittest.mock import patch
import json,urllib.request
from collector import Scope,download,load_bundle
s=Scope(30);bundle,url,nr=load_bundle(s);body=b'OWNED exact public byte stream';kind=KIND
row={'name':'owned.tar.xz','bytes':len(body),'sha256':sha256(body).hexdigest(),'exact_compressed_max':len(body),'official_url':'https://deb.debian.org/debian/pool/main/o/owned/owned.tar.xz'}
class Response:
 status=200
 def __init__(self):self.offset=0;self.closed=False;self.headers={'Content-Length':str(len(body)+(1 if kind=='length'else 0))}
 def geturl(self):return row['official_url']
 def read(self,n):
  if kind=='overread':return body+b'oversize'
  b=body[self.offset:self.offset+n];self.offset+=len(b)
  if kind=='short':return b[:-1]if b else b
  if kind=='hash'and b:return bytes([b[0]^1])+b[1:]
  return b
 def close(self):self.closed=kind!='close-noop'
response=Response()
class Opener:
 def open(self,request,timeout):assert request.full_url==row['official_url'];return response
with patch('collector.urllib.request.build_opener',return_value=Opener()):
 try:
  r,ref=download(s,row,Path(DIRECTORY),url,nr)
  assert kind=='positive';assert ref['sha256']==row['sha256'];r.close();finals=s.finish();assert all(r['physical_EBADF']for r in finals)
  print('OWNED response positive original/publisher physical finality')
 except Exception:
  if kind=='positive':raise
  assert s.rows and not s.finished and s.errors
  print('OWNED response expected refusal; no normal physical PASS; child kernel retirement')
""".replace('KIND',repr(kind)).replace('DIRECTORY',repr(str(directory)))
  r=subprocess.run([sys.executable,'-B','-c',code],cwd=HERE,capture_output=True,text=True,timeout=40);need(r.returncode==0,'owned_response:'+kind+':'+r.stderr[-500:]);results.append({'case':'HTTP_owned_seam_'+kind,'actual_child_exit':0,'network_actions':False,'actual_file_IO':True,'stdout':r.stdout.strip(),'actual_CVE_or_current_source_acceptance':False})
 # Exercise the production bundle loader and unchanged URL grammar, without
 # invoking the production downloader or opening any actual source archive.
 code="from collector import *\ns=Scope(30)\np,url,nr=load_bundle(s)\nassert len(p['groups'])==43 and sum(len(g['current_bodies'])for g in p['groups'])==78\nfor g in p['groups']:\n for m in g['profiles']:url(m['official_url'],m['name'])\ns.finish()\nprint('exact production bundle 43 groups/78 bodies/147 fixed URLs')\n"
 r=subprocess.run([sys.executable,'-B','-c',code],cwd=HERE,capture_output=True,text=True,timeout=40);need(r.returncode==0,'actual_bundle_loader:'+r.stderr[-500:]);results.append({'case':'production_bundle_43_78_and_fixed_URLs','actual_child_exit':r.returncode,'network_actions':False,'stdout':r.stdout.strip()})
 report={'schema':'OWNED_residual43_source_CI_protocol_QA/1','actual_cases':len(results),'cases':results,'actual_UID':os.getuid(),'actual_GID':os.getgid(),'genuine_hosted_UID1001':os.getuid()==1001,'no_actual_source_archives_or_network_or_native_payloads':True,'no_CVE_proof_or_activation_authority':True,'security_accepted':False};b=enc(report);p=root/'report.json'
 with p.open('xb')as f:f.write(b);f.flush();os.fsync(f.fileno())
 print(enc({'OWNED_protocol_cases_passed':len(results),'report':{'path':str(p),'bytes':len(b),'sha256':sha256(b).hexdigest()},'security_accepted':False}).decode(),end='')
if __name__=='__main__':main()
