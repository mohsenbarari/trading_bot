"""UNARMED pure bounded inventory of one caller-held flattened public-image TAR.

No extraction, execution, imports of inspected packages, network or Docker API.
I is the caller's source-pinned original pure ELF/DPKG/SBOM parser module. T is
accepted for the fixed controller interface; this module parses physical TAR
records itself. The caller retains source pathname/parents and genuine client,
container, image and cleanup evidence through its own outer physical finals.
"""
from hashlib import sha256
from pathlib import PurePosixPath
import errno,fcntl,json,math,os,posixpath,re,stat,struct,sys,time
MAX_ENTRIES=100000;MAX_FILES=50000;MAX_BYTES=2*1024**3;MAX_ELF=128*1024**2
MAX_REPORT=4*1024**2;MAX_NAMES=8*1024**2;MAX_META=4*1024**2;MAX_PAX=65536
MAX_PACKAGE_LIST_TOTAL=64*1024**2
MAX_INVENTORY_SECONDS=600
PURE_INVENTORY_SHA256='e8171fb6d28a5b3e938e0136dab230c13a9e864f66aad4c2727878e22a3ea73f'
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



WINDOW=1024**2
PURE_BATCH_TICKS=128
MAX_WINDOW_DIGEST_BYTES=2048*32
LAST_OBSERVATION=None
LAST_ATTEMPT_SOURCE=None
ATTEMPT_SEQUENCE=0
PUBLIC_FAILURE_CODES=frozenset(('archive_error_count', 'archive_bound', 'archive_close_held', 'archive_close_unknown', 'archive_closed', 'archive_deadline', 'archive_digest', 'archive_io_finality', 'archive_phase', 'archive_read_bound', 'archive_source', 'archive_source_changed', 'archive_terminal_scope', 'archive_truncated', 'archive_unproven', 'archive_unverified', 'archive_window_digest', 'archive_window_table_bound', 'container_receipt', 'elf_inventory_count', 'inventory_byte_bound', 'metadata_bound', 'package_list_count', 'package_list_inventory', 'package_list_missing_or_ambiguous', 'package_list_total', 'package_list_type', 'package_path_total', 'report_bound', 'source_identity', 'special_file_count', 'tar_checksum', 'tar_count', 'tar_device', 'tar_duplicate', 'tar_file_count', 'tar_format', 'tar_hardlink', 'tar_header', 'tar_length', 'tar_link', 'tar_name', 'tar_names_bound', 'tar_number', 'tar_padding', 'tar_path', 'tar_pax', 'tar_pax_bound', 'tar_pax_duplicate', 'tar_pax_key', 'tar_pax_time', 'tar_root', 'tar_shadow', 'tar_special_payload', 'tar_tail', 'tar_type', 'tar_whiteout', 'trusted_parser_interface'))

def flags(fd):return fcntl.fcntl(fd,fcntl.F_GETFL),fcntl.fcntl(fd,fcntl.F_GETFD)

class ArchiveIOFailure(RuntimeError):
    def __init__(self,errors):super().__init__('archive_io_finality');self.errors=tuple(errors)

def causes(error):return error.errors if type(error)is ArchiveIOFailure else(error,)

def fail(errors):
    if len(errors)==1:raise errors[0]
    raise ArchiveIOFailure(errors)from errors[0]

class Source:
    """Original private duplicate and authenticated immutable DATA snapshot.

    Window bytes are data, never a cached source/metadata validation. Every
    real pread has independent before/finally-after guards; every pure RAM batch
    starts and finishes with fresh guards and is invalidated before actual I/O.
    """
    def prefix(self,attempt=None):
        self.attempt=attempt;self.original_stream=None;self.original=None;self.frame=None;self.original_flags=None;self.original_offset=None
        self.size=None;self.checker=None;self.end=None;self.fd=None;self.duplicate=None;self.duplicate_flags=None;self.duplicate_offset=None
        self.dup_attempted=False;self.dup_acquired=False;self.duplicate_admitted=False;self.uncertain_hold=False;self.held=False
        self.digest_pin=None;self.window_digests=None;self.snapshot=None;self.snapshot_start=None
        self.pure_active=False;self.pure_count=0;self.errors=[];self.close_attempted=False;self.duplicate_close_attempted=False;self.actual_close_attempted=False;self.duplicate_closed=False;self.final_hash_running=False
        self.current_phase='admission';self.phase_start=time.monotonic();self.phases={}
        self.counts={k:0 for k in ('full_guards','pread_calls','pread_bytes','requested_reads','window_hits','window_loads','pure_ticks','pure_batches','full_hash_passes_started','full_hash_passes_completed','full_hash_bytes','max_requested_read')}
    def __init__(self,stream,size,check,end,attempt=None):
        self.prefix(attempt)
        try:
            need(callable(check)and type(size)is int and 1024<=size<=MAX_BYTES and size%512==0,'archive_bound')
            need(type(end)in(int,float)and math.isfinite(end)and 0<end-time.monotonic()<=MAX_INVENTORY_SECONDS,'archive_deadline')
            self.size=size;self.checker=check;self.end=end;self.original_stream=stream;self.original=stream.fileno()
            self.frame=identity(os.fstat(self.original));self.original_flags=flags(self.original);self.original_offset=os.lseek(self.original,0,os.SEEK_CUR)
            need(stat.S_ISREG(self.frame[2])and self.frame[5]==1 and self.frame[6]==size,'archive_source');self.check()
            self.dup_attempted=True;self.fd=self.duplicate=os.dup(self.original);self.dup_acquired=True
            # Prefix ownership is published before any fallible flag/stat read.
            # Failure before admission is HELD, even if a number now names an
            # indistinguishable replacement. Metadata never recreates authority.
            self.duplicate_flags=flags(self.duplicate);self.duplicate_offset=os.lseek(self.duplicate,0,os.SEEK_CUR)
            self.check();self.duplicate_admitted=True
        except BaseException as error:
            self.note(causes(error))
            if self.dup_acquired:self.uncertain_hold=True
            errors=list(causes(error))
            try:self.close()
            except BaseException as close_error:
                errors.extend(e for e in causes(close_error)if all(e is not old for old in errors))
            fail(errors)
    def clock(self):
        if not time.monotonic()<self.end:
            error=RuntimeError('archive_deadline');self.note((error,));raise error
    def note(self,errors):
        for error in errors:
            if all(error is not old for old in self.errors):self.errors.append(error)
    def forward(self):
        if self.errors:fail(self.errors)
        need(not self.close_attempted,'archive_closed')
    def check(self):
        self.counts['full_guards']+=1
        try:
            self.clock();need(callable(self.checker),'archive_source');self.checker()
            need(self.original is not None and self.original_stream.fileno()==self.original and identity(os.fstat(self.original))==self.frame and flags(self.original)==self.original_flags and os.lseek(self.original,0,os.SEEK_CUR)==self.original_offset,'archive_source_changed')
            if self.fd is not None:
                need(self.fd==self.duplicate and self.dup_acquired and self.duplicate_flags is not None and self.duplicate_offset is not None and identity(os.fstat(self.duplicate))==self.frame and flags(self.duplicate)==self.duplicate_flags and os.lseek(self.duplicate,0,os.SEEK_CUR)==self.duplicate_offset==self.original_offset,'archive_source_changed')
        except BaseException as error:
            if self.dup_acquired:self.uncertain_hold=True
            self.note(causes(error));raise
    def tick(self):
        self.forward();self.clock();self.counts['pure_ticks']+=1
        if self.pure_active and self.pure_count>=PURE_BATCH_TICKS:self.finish_pure()
        if not self.pure_active:
            self.check();self.pure_active=True;self.pure_count=0;self.counts['pure_batches']+=1
        self.pure_count+=1
    def finish_pure(self):
        if self.pure_active:
            try:self.check()
            except BaseException as error:self.note(causes(error));raise
            finally:self.pure_active=False;self.pure_count=0
    def phase(self,name):
        need(name in {'admission','preparse_full_hash','tar_index','package_status','package_owners','canonical_owners','regular_files','symlink_aliases','report_encoding','post_dup_close_full_hash','complete'},'archive_phase')
        self.finish_pure();now=time.monotonic();self.phases[self.current_phase]=self.phases.get(self.current_phase,0)+(now-self.phase_start)
        self.current_phase=name;self.phase_start=now;self.clock()
    def actual(self,offset,count,*,terminal=False):
        if terminal:need(self.final_hash_running and self.close_attempted and self.duplicate_closed and self.fd is None,'archive_terminal_scope')
        else:self.forward()
        need(type(offset)is int and type(count)is int and 0<=offset<=self.size and 0<count<=min(WINDOW,self.size-offset),'archive_read_bound')
        errors=[];raw=None;self.snapshot=None;self.snapshot_start=None
        try:
            self.finish_pure();self.check();self.counts['pread_calls']+=1
            raw=os.pread(self.original if self.fd is None else self.duplicate,count,offset)
            need(type(raw)is bytes and len(raw)==count,'archive_truncated');self.counts['pread_bytes']+=len(raw)
        except BaseException as error:
            self.uncertain_hold=True;self.note(causes(error));errors.extend(causes(error))
        finally:
            self.pure_active=False;self.pure_count=0
            try:self.check()
            except BaseException as error:errors.extend(causes(error))
        if errors:self.note(errors);fail(errors)
        return raw
    def verify(self,pin,*,terminal=False):
        if not terminal:self.forward()
        need(type(pin)is str and len(pin)==64 and all(c in'0123456789abcdef'for c in pin),'archive_digest')
        try:
            self.counts['full_hash_passes_started']+=1;digest=sha256();blocks=(self.size+WINDOW-1)//WINDOW
            need(blocks*32<=MAX_WINDOW_DIGEST_BYTES,'archive_window_table_bound')
            table=bytearray(blocks*32)if self.window_digests is None else None
            for index,offset in enumerate(range(0,self.size,WINDOW)):
                raw=self.actual(offset,min(WINDOW,self.size-offset),terminal=terminal);digest.update(raw);self.counts['full_hash_bytes']+=len(raw);value=sha256(raw).digest()
                if table is not None:table[index*32:(index+1)*32]=value
                else:need(value==self.window_digests[index*32:(index+1)*32],'archive_window_digest')
            need(digest.hexdigest()==pin,'archive_digest')
            if table is not None:self.window_digests=bytes(table)
            self.counts['full_hash_passes_completed']+=1
        except BaseException as error:self.note(causes(error));raise
    def read(self,offset,size):
        self.forward();self.clock();need(type(offset)is int and type(size)is int and 0<=offset<=self.size and 0<=size<=min(MAX_ELF,self.size-offset),'archive_read_bound')
        need(type(self.window_digests)is bytes and len(self.window_digests)==((self.size+WINDOW-1)//WINDOW)*32,'archive_unverified')
        self.counts['requested_reads']+=1;self.counts['max_requested_read']=max(self.counts['max_requested_read'],size)
        if size==0:self.tick();return b''
        parts=[];end=offset+size
        while offset<end:
            self.clock();start=offset//WINDOW*WINDOW
            if self.snapshot is None or self.snapshot_start!=start:
                raw=self.actual(start,min(WINDOW,self.size-start));index=start//WINDOW
                try:need(sha256(raw).digest()==self.window_digests[index*32:(index+1)*32],'archive_window_digest')
                except BaseException as error:self.note(causes(error));raise
                self.snapshot=raw;self.snapshot_start=start;self.counts['window_loads']+=1
            else:self.counts['window_hits']+=1
            self.tick();stop=min(end,start+len(self.snapshot));parts.append(self.snapshot[offset-start:stop-start]);offset=stop
        return b''.join(parts)
    def retire_duplicate(self):
        if self.duplicate_closed or not self.dup_acquired:return
        need(not self.duplicate_close_attempted,'archive_close_unknown');self.duplicate_close_attempted=True;self.held=True
        if not self.duplicate_admitted or self.uncertain_hold:
            self.held=True;raise RuntimeError('archive_close_held')
        self.check()
        # Fresh exact full9/flags/offset plus an uninterrupted admitted ledger
        # is necessary, never merely dev/inode/type or a restored observation.
        need(not self.uncertain_hold and self.fd==self.duplicate and identity(os.fstat(self.duplicate))==self.frame and flags(self.duplicate)==self.duplicate_flags and os.lseek(self.duplicate,0,os.SEEK_CUR)==self.duplicate_offset,'archive_close_held')
        self.actual_close_attempted=True;errors=[]
        try:os.close(self.duplicate)
        except BaseException as error:self.uncertain_hold=True;errors.extend(causes(error))
        finally:
            try:os.fstat(self.duplicate)
            except OSError as error:
                if error.errno==errno.EBADF:self.duplicate_closed=True;self.fd=None;self.held=False
                else:self.held=True;self.uncertain_hold=True;errors.append(error)
            else:self.held=True;self.uncertain_hold=True;errors.append(RuntimeError('archive_close_unknown'))
        if errors:self.note(errors);fail(errors)
    def close(self):
        need(not self.close_attempted,'archive_close_unknown');self.close_attempted=True;errors=list(self.errors)
        if not self.dup_acquired:
            if errors:fail(errors)
            return
        try:self.finish_pure()
        except BaseException as error:errors.extend(causes(error))
        try:self.check()
        except BaseException as error:errors.extend(causes(error))
        try:self.retire_duplicate()
        except BaseException as error:errors.extend(causes(error))
        if self.duplicate_closed:
            try:self.check()
            except BaseException as error:errors.extend(causes(error))
        self.snapshot=None;self.snapshot_start=None
        if self.digest_pin is not None and self.duplicate_closed:
            self.final_hash_running=True
            try:self.phase('post_dup_close_full_hash');self.verify(self.digest_pin,terminal=True)
            except BaseException as error:errors.extend(causes(error))
            finally:self.final_hash_running=False
        if errors:self.note(errors);fail(errors)
    def observation(self):
        phases=dict(self.phases);phases[self.current_phase]=phases.get(self.current_phase,0)+(time.monotonic()-self.phase_start)
        size=0 if self.window_digests is None else len(self.window_digests)
        return{'schema':'gold2_archive_cost_observation/2','attempt':self.attempt,'source_prefix_published':True,'dup_acquired':self.dup_acquired,'duplicate_admitted':self.duplicate_admitted,'descriptor_state_uncertain':self.uncertain_hold,'private_duplicate_HELD':self.held,'actual_close_attempted':self.actual_close_attempted,'phase':self.current_phase,'phase_seconds':phases,'counts':dict(self.counts),'window_bytes_limit':WINDOW,'pure_batch_ticks_limit':PURE_BATCH_TICKS,
               'window_digest_raw_bytes':size,'window_digest_Python_bytes':0 if self.window_digests is None else sys.getsizeof(self.window_digests),'window_digest_build_peak_raw_bytes':2*size,
               'large_read_join_raw_buffer_bound':2*MAX_ELF+WINDOW,'other_parser_report_Python_overhead_not_measured':True,'duplicate_close_attempted':self.duplicate_close_attempted,'duplicate_physically_closed':self.duplicate_closed,
               'original_stream_closed_by_adapter':False,'errors':[{'class':type(e).__name__,'code':e.args[0]if isinstance(e,RuntimeError)and len(e.args)==1 and type(e.args[0])is str and e.args[0]in PUBLIC_FAILURE_CODES else'archive_unproven'}for e in self.errors],
               'security_accepted':False,'whole_image_proof':False}

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
 source.phase('tar_index');_primary=None
 try:
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
   source.tick();parts=path.split('/')[1:-1];parent=''
   for part in parts:
    source.tick();parent+='/'+part;need(parent not in rows or rows[parent]['type']=='directory','tar_shadow')
  targets={}
  def resolve(path,seen):
   source.tick();need(path in rows and path not in seen and len(seen)<128,'tar_hardlink')
   row=rows[path]
   if row['type']=='regular':return path
   need(row['type']=='hardlink','tar_hardlink');return resolve(row['link_target'],seen|{path})
  for path,row in rows.items():
   source.tick()
   if row['type']in('regular','hardlink'):targets[path]=resolve(path,set())
  return rows,targets,physical

 except BaseException as error:
  _primary=error;source.note(causes(error));raise
 finally:
  try:source.finish_pure()
  except BaseException as error:
   source.note(causes(error))
   if _primary is not None:fail([*causes(_primary),*causes(error)])
   raise

def walk(source,values):
 for value in values:
  source.tick();yield value

def member_bytes(source,rows,targets,path,maximum):
 need(path in targets,'package_list_missing_or_ambiguous');target=rows[targets[path]];need(0<=target['size']<=maximum,'metadata_bound')
 return source.read(target['data_offset'],target['size'])

def inventory(I,source,rows,targets,physical,sbom):
 source.phase('package_status');_primary=None
 try:
  status=member_bytes(source,rows,targets,'/var/lib/dpkg/status',MAX_META);packages=I.installed_packages(status)
  owners={};total=0
  info={p.rsplit('/',1)[-1]:p for p in walk(source,rows)if p.startswith('/var/lib/dpkg/info/')and '/'not in p[len('/var/lib/dpkg/info/'):]}
  need(len(info)<=20000,'package_list_inventory')
  source.phase('package_owners')
  for key,package in packages.items():
   source.tick();choices={package['package']+'.list',key+'.list'}&info.keys();need(len(choices)==1,'package_list_missing_or_ambiguous')
   selected=info[next(iter(choices))];need(rows[selected]['type']=='regular','package_list_type');raw=member_bytes(source,rows,targets,selected,MAX_META)
   total+=len(raw);need(total<=MAX_PACKAGE_LIST_TOTAL,'package_list_total');lines=raw.decode('utf-8').splitlines();need(len(lines)<=100000,'package_list_count')
   for value in lines:
    source.tick()
    if value=='/.':continue
    path=I.canonical_name(value);owners.setdefault(path,set()).add(key);need(len(owners)<=200000,'package_path_total')
  links={p:r['link_target']for p,r in walk(source,rows.items())if r['type']=='symlink'and not excluded(p)};canonical_owners={}
  source.phase('canonical_owners')
  for path,keys in owners.items():
   source.tick()
   try:target=I.canonical_target(path,links)
   except RuntimeError:continue
   canonical_owners.setdefault(target,set()).update(keys)
  elf_rows=[];elf_bytes=0;advertised=0;hardlinks={};special=[];exclusions=[];row_bytes=0
  source.phase('regular_files')
  for path,row in rows.items():
   source.tick()
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
     source.tick();actual=packages[key];expected=sbom.get(key);equal=expected is not None and all(actual[k]==expected[k]for k in('package','installed_version','architecture','source_package','source_version'))
     bindings.append({**actual,'sbom_source_binding_matches':equal,'sbom':expected})
    item={'path':path,'bytes':len(raw),'sha256':sha256(raw).hexdigest(),'archive_member':row,'archive_payload_member':targets[path],**metadata,
     'package_owners':bindings,'source_binding_status':'BOUND_DPKG_AND_SEALED_SBOM'if bindings and all(b['sbom_source_binding_matches']for b in bindings)else'UNASSESSED','symlink_aliases':[]}
    row_bytes+=len(encoded(item));need(len(elf_rows)<8192 and row_bytes<=MAX_REPORT-1048576,'report_bound');elf_rows.append(item)
    hardlinks.setdefault(targets[path],[]).append(path)
   elif row['type']not in('symlink','directory'):
    special.append(row);need(len(special)<=256,'special_file_count')
  need(elf_rows,'elf_inventory_count');by_path={r['path']:r for r in walk(source,elf_rows)}
  source.phase('symlink_aliases')
  for path in sorted(links):
   source.tick()
   try:target=I.canonical_target(path,links)
   except RuntimeError:continue
   if target in by_path:by_path[target]['symlink_aliases'].append(path)
  elf_rows.sort(key=lambda r:r['path'])
  return{'entries_examined':len(rows),'physical_headers_examined':physical,'regular_files_examined':len(targets),'advertised_regular_bytes':advertised,
   'elf_bytes_read':elf_bytes,'elf_files':elf_rows,'runtime_paths_excluded':sorted(exclusions),'special_metadata_only':special,
   'hardlink_sets':[sorted(p)for p in walk(source,hardlinks.values())if len(p)>1],'directory_alias_prefixes':{k:v for k,v in links.items()if k in('/bin','/sbin','/lib','/lib64')}}

 except BaseException as error:
  _primary=error;source.note(causes(error));raise
 finally:
  try:source.finish_pure()
  except BaseException as error:
   source.note(causes(error))
   if _primary is not None:fail([*causes(_primary),*causes(error)])
   raise

def collect(I,T,stream,size,*,image_id,source_commit,sbom_raw,sbom_sha256,verifier_sha256,export_sha256,container_receipt,check,end):
 """Caller references are not authority; all actual outer receipts stay required."""
 global LAST_OBSERVATION,LAST_ATTEMPT_SOURCE,ATTEMPT_SEQUENCE
 ATTEMPT_SEQUENCE+=1;attempt={'ordinal':ATTEMPT_SEQUENCE}
 LAST_OBSERVATION={'schema':'gold2_archive_attempt_observation/2','attempt':attempt,'phase':'admission','source_prefix_published':False,'security_accepted':False,'whole_image_proof':False}
 LAST_ATTEMPT_SOURCE=None;source=None;errors=[];report=None
 try:
  need(type(image_id)is str and re.fullmatch('sha256:[a-f0-9]{64}',image_id)and type(source_commit)is str and re.fullmatch('[a-f0-9]{40}',source_commit)
   and all(hex64(v)for v in(sbom_sha256,verifier_sha256,export_sha256))and type(sbom_raw)is bytes and 0<len(sbom_raw)<=MAX_META and sha256(sbom_raw).hexdigest()==sbom_sha256,'source_identity')
  need(all(callable(getattr(I,name,None))for name in('installed_packages','sbom_packages','classify_elf','canonical_name','canonical_target')),'trusted_parser_interface')
  need(type(container_receipt)is dict and set(container_receipt)=={'schema','container_id','image_id','source_commit','before','after','archive','actual_export_client_returncode','container_started','private_mounts_used','security_accepted'}
   and container_receipt['schema']=='same_image_never_started_export/1'and hex64(container_receipt['container_id'])and container_receipt['image_id']==image_id
   and container_receipt['source_commit']==source_commit and type(container_receipt['actual_export_client_returncode'])is int and container_receipt['actual_export_client_returncode']==0
   and container_receipt['container_started']is False and container_receipt['private_mounts_used']is False and container_receipt['security_accepted']is False
   and type(container_receipt['before'])is dict and container_receipt['before']==container_receipt['after']and len(encoded(container_receipt))<=16384,'container_receipt')
  # Bind only validated public identities, never arbitrary rejected operands.
  attempt.update({'image_id':image_id,'source_commit':source_commit,'verifier_sha256':verifier_sha256,'export_sha256':export_sha256})
  source=Source.__new__(Source);LAST_ATTEMPT_SOURCE=source
  Source.__init__(source,stream,size,check,end,attempt)
  need(container_receipt['archive']=={'bytes':size,'sha256':export_sha256,'full9':list(source.frame)},'container_receipt')
  source.phase('preparse_full_hash');source.verify(export_sha256);source.digest_pin=export_sha256
  rows,targets,physical=index(source);sbom=I.sbom_packages(sbom_raw)
  result=inventory(I,source,rows,targets,physical,sbom);source.check()
  report={'schema':'image_acl_archive_inventory/1','image_id':image_id,'source_commit':source_commit,'verifier_sha256':verifier_sha256,
   'sbom_sha256':sbom_sha256,'parser_original_source_sha256':I.PARSER_SOURCE_SHA,'pure_inventory_source_sha256':PURE_INVENTORY_SHA256,
   'export_sha256':export_sha256,'export_bytes':size,'container_receipt':container_receipt,'inventory':result,
   'scope':'all_regular_ELF_in_authenticated_flat_image_TAR_without_path_exclusions',
   'elf_metadata_inventory_complete':all(r['elf_parse_status']=='PARSED'for r in walk(source,result['elf_files'])),
   'source_bindings_complete':all(r['source_binding_status']=='BOUND_DPKG_AND_SEALED_SBOM'for r in walk(source,result['elf_files'])),
   'archive_member_metadata_is_filesystem_full9':False,'snapshot_atomic':False,'directory_symlinks_followed':False,
   'inspected_code_executed':False,'archive_extracted':False,'static_or_obfuscated_code_completely_analyzed':False,'dlopen_or_ctypes_reachability_analyzed':False,
   'acl_flags_or_callsite_safety_proven':False,'whole_image_acl_closed':False,'production_mounts_or_data_used':False,
   'scanner_findings_modified':False,'security_accepted':False}
  source.phase('report_encoding');need(len(encoded(report))<=MAX_REPORT,'report_bound')
 except BaseException as error:
  if source is not None and hasattr(source,'errors'):source.note(causes(error))
  errors.extend(causes(error))
 finally:
  if source is not None and hasattr(source,'errors'):
   if not source.close_attempted:
    try:source.close()
    except BaseException as error:errors.extend(e for e in causes(error)if all(e is not old for old in errors))
   if not errors:
    try:source.phase('complete')
    except BaseException as error:source.note(causes(error));errors.extend(causes(error))
   LAST_OBSERVATION=source.observation()
  LAST_OBSERVATION['failure']=public_failure(ArchiveIOFailure(errors))if errors else None
 if errors:fail(errors)
 return report

if __name__=='__main__':print(json.dumps({'status':'UNARMED','collection_performed':False,'security_accepted':False}))

def public_failure(error):
 # Flatten only actual source-owned aggregates. Other exception args never
 # become authority or public operands. Each actual cause remains a separate row.
 pending=[error];values=[];walked=0
 while pending:
  value=pending.pop();walked+=1
  need(walked<=4096,'archive_error_count')
  if type(value)is ArchiveIOFailure:pending.extend(reversed(value.errors))
  else:values.append(value)
 need(values,'archive_error_count');rows=[]
 for value in values:
  code=value.args[0]if isinstance(value,RuntimeError)and len(value.args)==1 and type(value.args[0])is str and value.args[0]in PUBLIC_FAILURE_CODES else'archive_unproven'
  label=type(value).__name__
  rows.append({'code':code,'exception_class':label if label in{'RuntimeError','ArchiveIOFailure','OSError','TimeoutError','FileNotFoundError','PermissionError'}else'unclassified_exception','os_errno':value.errno if isinstance(value,OSError)and type(value.errno)is int and 0<value.errno<=4095 else None})
 return{'schema':'gold2_archive_closed_failure/2','code':'archive_io_finality'if type(error)is ArchiveIOFailure else rows[0]['code'],'causes':rows,'security_accepted':False}
