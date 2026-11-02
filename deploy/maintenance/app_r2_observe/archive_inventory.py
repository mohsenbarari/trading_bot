"""UNARMED pure bounded inventory of one caller-held flattened public-image TAR.

No extraction, execution, imports of inspected packages, network or Docker API.
I is the caller's source-pinned original pure ELF/DPKG/SBOM parser module. T is
accepted for the fixed controller interface; this module parses physical TAR
records itself. The caller retains source pathname/parents and genuine client,
container, image and cleanup evidence through its own outer physical finals.
"""
from hashlib import sha256
from pathlib import PurePosixPath
import json,math,os,posixpath,re,stat,struct,time
MAX_ENTRIES=100000;MAX_FILES=50000;MAX_BYTES=2*1024**3;MAX_ELF=128*1024**2
MAX_REPORT=4*1024**2;MAX_NAMES=8*1024**2;MAX_META=4*1024**2;MAX_PAX=65536
MAX_PACKAGE_LIST_TOTAL=64*1024**2
MAX_INVENTORY_SECONDS=600
PURE_INVENTORY_SHA256='8b4b0091a1e8d20786e33c97a6bb4b63c28b263180527b313883815c67cfe5dc'
EXCLUDED=frozenset()

def need(value,code):
 if not value:raise RuntimeError(code)
def identity(s):return(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def encoded(v):return(json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
def hex64(v):return type(v)is str and re.fullmatch('[a-f0-9]{64}',v)is not None

def text(raw):
 value=raw.decode('utf-8');need(len(raw)<=4096 and all(ord(c)>=32 and ord(c)!=127 for c in value),'tar_name');return value

def field(raw):
 value,sep,pad=raw.partition(b'\0');need(not sep or not pad.strip(b'\0'),'tar_header');return text(value)

def number(raw,maximum,*,signed=False):
 if raw[0]&128:
  # POSIX/GNU base256 sign bit is the second high bit; no overflow acceptance.
  value=int.from_bytes(raw,'big',signed=False)&((1<<(len(raw)*8-1))-1)
  if raw[0]&64:value-=1<<(len(raw)*8-1)
 else:
  value=raw.strip(b'\0 ');need(not value or re.fullmatch(b'[0-7]+',value),'tar_number');value=int(value or b'0',8)
 need(type(value)is int and(-maximum<=value<=maximum if signed else 0<=value<=maximum),'tar_number');return value

def canonical(name,*,directory=False,hardlink=False):
 need(type(name)is str and 0<len(name.encode())<=4096 and '\\'not in name,'tar_name')
 if name in('.','./'):
  need(directory and not hardlink,'tar_root');return '/'
 if name.startswith('./'):name=name[2:]
 if directory and name.endswith('/'):name=name[:-1]
 need(name and not name.startswith('/')and not name.endswith('/')and all(p not in('','.','..')for p in name.split('/')),'tar_path')
 need(not any(p.startswith('.wh.')for p in name.split('/')),'tar_whiteout')
 need(str(PurePosixPath(name))==name and all(ord(c)>=32 and ord(c)!=127 for c in name),'tar_path');return '/'+name

def excluded(path):return any(path==p or p in('/proc','/sys','/dev')and path.startswith(p+'/')for p in EXCLUDED)

class Source:
 """A private duplicate; the original already-held stream is never closed."""
 def __init__(self,stream,size,check,end):
  need(callable(check)and type(size)is int and 1024<=size<=MAX_BYTES and size%512==0,'archive_bound')
  need(type(end)in(int,float)and math.isfinite(end)and 0<end-time.monotonic()<=MAX_INVENTORY_SECONDS,'archive_deadline')
  self.original=stream.fileno();self.size=size;self.checker=check;self.end=end;self.fd=None;self.digest_pin=None
  check();self.frame=identity(os.fstat(self.original));need(stat.S_ISREG(self.frame[2])and self.frame[5]==1 and self.frame[6]==size,'archive_source')
  self.fd=os.dup(self.original)
  try:self.check()
  except BaseException:os.close(self.fd);self.fd=None;raise
 def check(self):
  need(time.monotonic()<self.end,'archive_deadline');self.checker()
  need(identity(os.fstat(self.original))==self.frame and(self.fd is None or identity(os.fstat(self.fd))==self.frame),'archive_source_changed')
 def read(self,offset,size):
  need(type(offset)is int and type(size)is int and 0<=offset<=self.size and 0<=size<=min(MAX_ELF,self.size-offset),'archive_read_bound')
  self.check();raw=os.pread(self.original if self.fd is None else self.fd,size,offset);need(len(raw)==size,'archive_truncated');self.check();return raw
 def verify(self,pin):
  need(hex64(pin),'archive_digest');digest=sha256()
  for offset in range(0,self.size,65536):digest.update(self.read(offset,min(65536,self.size-offset)))
  need(digest.hexdigest()==pin,'archive_digest')
 def close(self):
  try:self.check()
  finally:
   if self.fd is not None:fd=self.fd;self.fd=None;os.close(fd)
  self.check()
  if self.digest_pin is not None:self.verify(self.digest_pin)

def pax(raw):
 need(0<len(raw)<=MAX_PAX,'tar_pax_bound');result={};offset=0
 while offset<len(raw):
  space=raw.find(b' ',offset,min(len(raw),offset+12));need(space>offset and re.fullmatch(b'[1-9][0-9]*',raw[offset:space]),'tar_pax')
  length=int(raw[offset:space]);stop=offset+length;need(space+3<=stop<=len(raw)and raw[stop-1:stop]==b'\n','tar_pax')
  key,sep,value=raw[space+1:stop-1].partition(b'=');need(sep and re.fullmatch(b'[A-Za-z0-9_.-]{1,256}',key),'tar_pax')
  key=key.decode('ascii');need(key not in result,'tar_pax_duplicate')
  if key in('path','linkpath'):result[key]=text(value)
  elif key in('mtime','atime','ctime'):
   need(re.fullmatch(rb'-?(?:0|[1-9][0-9]{0,18})(?:\.[0-9]{1,9})?',value),'tar_pax_time');result[key]=value.decode('ascii')
  else:
   need(key.startswith(('SCHILY.xattr.','LIBARCHIVE.xattr.'))and len(value)<=MAX_PAX,'tar_pax_key');result[key]=None
  offset=stop
 need(offset==len(raw),'tar_pax');return result

def index(source):
 rows={};offset=0;physical=0;names=0;files=0;pending=None;zero=False
 while offset<source.size:
  header=source.read(offset,512)
  if header==bytes(512):
   need(pending is None and offset+1024<=source.size and source.read(offset+512,512)==bytes(512),'tar_tail')
   for start in range(offset+1024,source.size,65536):need(not any(source.read(start,min(65536,source.size-start))),'tar_tail')
   zero=True;break
  physical+=1;need(physical<=MAX_ENTRIES,'tar_count')
  stored=number(header[148:156],(1<<32)-1);need(stored==sum(header[:148])+8*32+sum(header[156:]),'tar_checksum')
  need(header[257:265]in(b'ustar\x0000',b'ustar  \x00')and not any(header[500:512]),'tar_format')
  name=field(header[:100]);prefix=field(header[345:500]);name=(prefix+'/'+name)if prefix else name
  size=number(header[124:136],MAX_BYTES);mode=number(header[100:108],0o7777);uid=number(header[108:116],(1<<32)-1);gid=number(header[116:124],(1<<32)-1)
  mtime=number(header[136:148],(1<<63)-1,signed=True);kind=header[156:157];link=field(header[157:257]);data=offset+512
  major=number(header[329:337],(1<<32)-1);minor=number(header[337:345],(1<<32)-1)
  need(kind in(b'3',b'4')or major==minor==0,'tar_device')
  need(size<=source.size-data,'tar_length');padded=(size+511)//512*512;need(data+padded<=source.size,'tar_length')
  if padded>size:need(not any(source.read(data+size,padded-size)),'tar_padding')
  offset=data+padded
  if kind==b'x':
   need(pending is None and not link and size<=MAX_PAX,'tar_pax');pending=pax(source.read(data,size));names+=size;need(names<=MAX_NAMES,'tar_names_bound');continue
  need(kind in(b'\0',b'0',b'1',b'2',b'3',b'4',b'5',b'6'),'tar_type')
  extra=pending or{};pending=None;name=extra.get('path',name);link=extra.get('linkpath',link)
  path=canonical(name,directory=kind==b'5');need(path not in rows,'tar_duplicate')
  names+=len(name.encode())+len(link.encode());need(names<=MAX_NAMES,'tar_names_bound')
  label={b'\0':'regular',b'0':'regular',b'1':'hardlink',b'2':'symlink',b'3':'character',b'4':'block',b'5':'directory',b'6':'fifo'}[kind]
  need(label=='regular'or size==0,'tar_special_payload');need(label in('hardlink','symlink')or not link,'tar_link')
  if label=='hardlink':link=canonical(link,hardlink=True)
  elif label=='symlink':
   need(link and len(link.encode())<=4096 and '\\'not in link,'tar_link');link='/'+posixpath.normpath(link if link.startswith('/')else posixpath.join(posixpath.dirname(path),link)).lstrip('/')
  if label in('regular','hardlink'):files+=1;need(files<=MAX_FILES,'tar_file_count')
  rows[path]={'schema':'tar_archive_member/1','path':path,'type':label,'uid':uid,'gid':gid,'mode':mode,'mtime':extra.get('mtime',mtime),
   'physical_header_offset':data-512,'data_offset':data,'size':size,'link_target':link or None,'pax_keys':sorted(extra),
   'device_major':major if label in('character','block')else None,'device_minor':minor if label in('character','block')else None}
 need(zero and pending is None,'tar_tail')
 # No non-directory can shadow a real member's ancestor. Missing directory
 # headers are allowed by normal TAR, but symlink traversal is never followed.
 for path in rows:
  parts=path.split('/')[1:-1];parent=''
  for part in parts:
   parent+='/'+part;need(parent not in rows or rows[parent]['type']=='directory','tar_shadow')
 targets={}
 def resolve(path,seen):
  need(path in rows and path not in seen and len(seen)<128,'tar_hardlink')
  row=rows[path]
  if row['type']=='regular':return path
  need(row['type']=='hardlink','tar_hardlink');return resolve(row['link_target'],seen|{path})
 for path,row in rows.items():
  source.check()
  if row['type']in('regular','hardlink'):targets[path]=resolve(path,set())
 return rows,targets,physical

def member_bytes(source,rows,targets,path,maximum):
 need(path in targets,'package_list_missing_or_ambiguous');target=rows[targets[path]];need(0<=target['size']<=maximum,'metadata_bound')
 return source.read(target['data_offset'],target['size'])

def inventory(I,source,rows,targets,physical,sbom):
 status=member_bytes(source,rows,targets,'/var/lib/dpkg/status',MAX_META);packages=I.installed_packages(status)
 owners={};total=0
 info={p.rsplit('/',1)[-1]:p for p in rows if p.startswith('/var/lib/dpkg/info/')and '/'not in p[len('/var/lib/dpkg/info/'):]}
 need(len(info)<=20000,'package_list_inventory')
 for key,package in packages.items():
  source.check();choices={package['package']+'.list',key+'.list'}&info.keys();need(len(choices)==1,'package_list_missing_or_ambiguous')
  selected=info[next(iter(choices))];need(rows[selected]['type']=='regular','package_list_type');raw=member_bytes(source,rows,targets,selected,MAX_META)
  total+=len(raw);need(total<=MAX_PACKAGE_LIST_TOTAL,'package_list_total');lines=raw.decode('utf-8').splitlines();need(len(lines)<=100000,'package_list_count')
  for value in lines:
   if value=='/.':continue
   path=I.canonical_name(value);owners.setdefault(path,set()).add(key);need(len(owners)<=200000,'package_path_total')
 links={p:r['link_target']for p,r in rows.items()if r['type']=='symlink'and not excluded(p)};canonical_owners={}
 for path,keys in owners.items():
  source.check()
  try:target=I.canonical_target(path,links)
  except RuntimeError:continue
  canonical_owners.setdefault(target,set()).update(keys)
 elf_rows=[];elf_bytes=0;advertised=0;hardlinks={};special=[];exclusions=[];row_bytes=0
 for path,row in rows.items():
  source.check()
  if excluded(path):exclusions.append(path);continue
  if row['type']in('regular','hardlink'):
   target=rows[targets[path]];advertised+=target['size'];need(advertised<=MAX_BYTES,'inventory_byte_bound')
   prefix=source.read(target['data_offset'],min(4,target['size']))
   if prefix!=b'\x7fELF':continue
   raw=member_bytes(source,rows,targets,path,MAX_ELF);elf_bytes+=len(raw);need(elf_bytes<=MAX_BYTES,'inventory_byte_bound')
   try:metadata=I.classify_elf(raw)
   except (RuntimeError,struct.error,OverflowError,UnicodeError):metadata={'elf_parse_status':'UNASSESSED_UNSUPPORTED_METADATA','callsite_safety_status':'UNASSESSED'}
   bindings=[]
   for key in sorted(canonical_owners.get(path,set())):
    actual=packages[key];expected=sbom.get(key);equal=expected is not None and all(actual[k]==expected[k]for k in('package','installed_version','architecture','source_package','source_version'))
    bindings.append({**actual,'sbom_source_binding_matches':equal,'sbom':expected})
   item={'path':path,'bytes':len(raw),'sha256':sha256(raw).hexdigest(),'archive_member':row,'archive_payload_member':targets[path],**metadata,
    'package_owners':bindings,'source_binding_status':'BOUND_DPKG_AND_SEALED_SBOM'if bindings and all(b['sbom_source_binding_matches']for b in bindings)else'UNASSESSED','symlink_aliases':[]}
   row_bytes+=len(encoded(item));need(len(elf_rows)<8192 and row_bytes<=MAX_REPORT-1048576,'report_bound');elf_rows.append(item)
   hardlinks.setdefault(targets[path],[]).append(path)
  elif row['type']not in('symlink','directory'):
   special.append(row);need(len(special)<=256,'special_file_count')
 need(elf_rows,'elf_inventory_count');by_path={r['path']:r for r in elf_rows}
 for path in sorted(links):
  source.check()
  try:target=I.canonical_target(path,links)
  except RuntimeError:continue
  if target in by_path:by_path[target]['symlink_aliases'].append(path)
 elf_rows.sort(key=lambda r:r['path'])
 return{'entries_examined':len(rows),'physical_headers_examined':physical,'regular_files_examined':len(targets),'advertised_regular_bytes':advertised,
  'elf_bytes_read':elf_bytes,'elf_files':elf_rows,'runtime_paths_excluded':sorted(exclusions),'special_metadata_only':special,
  'hardlink_sets':[sorted(p)for p in hardlinks.values()if len(p)>1],'directory_alias_prefixes':{k:v for k,v in links.items()if k in('/bin','/sbin','/lib','/lib64')}}

def collect(I,T,stream,size,*,image_id,source_commit,sbom_raw,sbom_sha256,verifier_sha256,export_sha256,container_receipt,check,end):
 """Caller references are not authority; all actual outer receipts stay required."""
 need(type(image_id)is str and re.fullmatch('sha256:[a-f0-9]{64}',image_id)and type(source_commit)is str and re.fullmatch('[a-f0-9]{40}',source_commit)
  and all(hex64(v)for v in(sbom_sha256,verifier_sha256,export_sha256))and type(sbom_raw)is bytes and 0<len(sbom_raw)<=MAX_META and sha256(sbom_raw).hexdigest()==sbom_sha256,'source_identity')
 need(all(callable(getattr(I,name,None))for name in('installed_packages','sbom_packages','classify_elf','canonical_name','canonical_target')),'trusted_parser_interface')
 need(type(container_receipt)is dict and set(container_receipt)=={'schema','container_id','image_id','source_commit','before','after','archive','actual_export_client_returncode','container_started','private_mounts_used','security_accepted'}
  and container_receipt['schema']=='same_image_never_started_export/1'and hex64(container_receipt['container_id'])and container_receipt['image_id']==image_id
  and container_receipt['source_commit']==source_commit and type(container_receipt['actual_export_client_returncode'])is int and container_receipt['actual_export_client_returncode']==0
  and container_receipt['container_started']is False and container_receipt['private_mounts_used']is False and container_receipt['security_accepted']is False
  and type(container_receipt['before'])is dict and container_receipt['before']==container_receipt['after']and len(encoded(container_receipt))<=16384,'container_receipt')
 source=Source(stream,size,check,end)
 try:
  need(container_receipt['archive']=={'bytes':size,'sha256':export_sha256,'full9':list(source.frame)},'container_receipt')
  source.verify(export_sha256);source.digest_pin=export_sha256
  rows,targets,physical=index(source);sbom=I.sbom_packages(sbom_raw)
  result=inventory(I,source,rows,targets,physical,sbom);source.check()
  report={'schema':'image_acl_archive_inventory/1','image_id':image_id,'source_commit':source_commit,'verifier_sha256':verifier_sha256,
   'sbom_sha256':sbom_sha256,'parser_original_source_sha256':I.PARSER_SOURCE_SHA,'pure_inventory_source_sha256':PURE_INVENTORY_SHA256,
   'export_sha256':export_sha256,'export_bytes':size,'container_receipt':container_receipt,'inventory':result,
   'scope':'all_regular_ELF_in_authenticated_flat_image_TAR_without_path_exclusions',
   'elf_metadata_inventory_complete':all(r['elf_parse_status']=='PARSED'for r in result['elf_files']),
   'source_bindings_complete':all(r['source_binding_status']=='BOUND_DPKG_AND_SEALED_SBOM'for r in result['elf_files']),
   'archive_member_metadata_is_filesystem_full9':False,'snapshot_atomic':False,'directory_symlinks_followed':False,
   'inspected_code_executed':False,'archive_extracted':False,'static_or_obfuscated_code_completely_analyzed':False,'dlopen_or_ctypes_reachability_analyzed':False,
   'acl_flags_or_callsite_safety_proven':False,'whole_image_acl_closed':False,'production_mounts_or_data_used':False,
   'scanner_findings_modified':False,'security_accepted':False}
  need(len(encoded(report))<=MAX_REPORT,'report_bound')
 finally:source.close()
 return report

if __name__=='__main__':print(json.dumps({'status':'UNARMED','collection_performed':False,'security_accepted':False}))
