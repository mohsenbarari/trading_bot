"""Collect Root-selected public SOURCE DATA in disposable CI; never build it."""
from pathlib import Path,PurePosixPath
from hashlib import sha256
from types import ModuleType
import argparse,ast,bz2,fnmatch,json,lzma,os,re,resource,ssl,sys,tarfile,time,urllib.request,urllib.error,zlib
from source_io import Scope,need,publish
HERE=Path(__file__).absolute().parent
PROFILE_PIN=(174072,'99389b6bd9e15f237a8365175636b13f70517efd7ea2788e9d32b9fbc534a433')
SELECTION_PIN=(87842,'201f624fa69b3f3efb44b06047725ccc92d91e40e0d8db2596582f58677582b6')
def enc(v):return(json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
def unique(raw):
 def pairs(items):
  d={}
  for k,v in items:need(k not in d,'duplicate_JSON');d[k]=v
  return d
 return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:need(False,'JSON_constant'))
def small(scope,path,pin,maximum=4194304):
 r=scope.open(path,pin,maximum);b=b''.join(r.read(min(65536,pin[0]-off),off)for off in range(0,pin[0],65536));need((len(b),sha256(b).hexdigest())==pin,'small_pin');return unique(b)
def load_bundle(scope):
 p=small(scope,HERE/'profiles.json',PROFILE_PIN);selection=small(scope,HERE/'selection.json',SELECTION_PIN);docs={}
 for ref in p['inputs']:
  path=HERE/ref['path'];pin=(ref['bytes'],ref['sha256']);r=scope.open(path,pin,4194304);raw=b''.join(r.read(min(65536,pin[0]-off),off)for off in range(0,pin[0],65536))
  if path.suffix=='.json':docs[path.name]=unique(raw)
  if path.name=='fetch-reference.py':
   # Reuse only the unchanged mature official URL grammar and no-redirect
   # handler. Never invoke its ROOT path, old CLI, generic8MiB fetcher or IO.
   tree=ast.parse(raw);nodes=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.ClassDef))and n.name in{'official_url','NoRedirect'}];need(len(nodes)==2,'fixed_fetch_primitives');env={'need':need,'urlsplit':__import__('urllib.parse',fromlist=['urlsplit']).urlsplit,'PurePosixPath':PurePosixPath,'re':re,'urllib':urllib};exec(compile(ast.Module(body=nodes,type_ignores=[]),str(path),'exec'),env);official_url=env['official_url'];no_redirect=env['NoRedirect']
 need(selection['selected_for_public_source_CI_collection']is True and selection['source_groups']==len(p['groups'])==43 and sum(len(g['current_bodies'])for g in p['groups'])==78,'exact_selection43_78');need(p['generic_assessor_input_cap_unchanged']==8388608,'generic_cap_unchanged')
 cover=docs['signed-source-membership.json'];signed=docs['signed-source-roots.json'];need(cover['signed_roots_original']=={'bytes':3192,'sha256':'a44c577a9eb66ff98d85f0351958825c5596a95223011c13aa61174aee161307'},'signed_root_chain')
 for suite,row in signed['releases'].items():need(row['valid_signature_primary_fingerprints']and{k:row['source_index'][k]for k in('bytes','sha256')}==cover['indices'][suite],'retained_signed_index_chain')
 frontier={g.get('source_package'):g for g in docs['frontier.json']['source_group_matrix']}
 for i,g in enumerate(p['groups']):
  sg=selection['groups'][i];fg=frontier[g['source_package']];need(g['index']==i and g['source_package']==sg['source_package']and g['source_version']==sg['source_version']==fg['source_version']and g['architecture']==sg['architecture']==fg['architecture']and g['current_bodies']==fg['physical_rows']and g['current_full_owner_variants']==fg['full_current_owner_variants'],'exact_current_group_frontier_join')
 return p,official_url,no_redirect
def download(scope,row,directory,official_url,no_redirect):
 official_url(row['official_url'],row['name']);need(row['bytes']==row['exact_compressed_max']and type(row['bytes'])is int and row['bytes']>0,'exact_named_compressed_role');need(os.statvfs(directory).f_bavail*os.statvfs(directory).f_frsize>=1073741824+row['bytes'],'CI_disk_reserve')
 w=scope.create(directory/row['name'],row['exact_compressed_max']);opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=ssl.create_default_context()),no_redirect());response=None;error=None;closing_error=None
 try:
  request=urllib.request.Request(row['official_url'],headers={'Accept-Encoding':'identity','Connection':'close','User-Agent':'Root-residual43-public-SOURCE/1'});response=opener.open(request,timeout=25)
  need(response.geturl()==row['official_url']and response.status==200 and response.headers.get('Content-Encoding','identity')=='identity','public_response_identity');length=response.headers.get('Content-Length');need(length is None or length.isascii()and length.isdecimal()and int(length)==row['bytes'],'public_response_length');total=0
  while True:
   scope.check();requested=min(65536,row['bytes']-total+1);b=response.read(requested);need(type(b)is bytes and len(b)<=requested and total+len(b)<=row['bytes'],'public_response_bound_before_write')
   if not b:break
   w.append(b);total+=len(b)
  need(total==row['bytes'],'public_response_short')
 except BaseException as e:
  error=e
  if isinstance(e,urllib.error.HTTPError):response=e
 finally:
  if response is not None:
   try:response.close();need(response.closed,'public_response_close')
   except BaseException as e:closing_error=e
 if error is not None or closing_error is not None:
  for e in(error,closing_error):
   if e is not None:scope.note(e)
  raise error if error is not None else closing_error
 ref=w.seal((row['bytes'],row['sha256']));r=scope.open(Path(ref['path']),(row['bytes'],row['sha256']),row['exact_compressed_max']);w.close();return r,ref
def canonical(name):
 need(type(name)is str and len(name.encode())<=4096 and'\\'not in name and'\x00'not in name,'member_name')
 while name.startswith('./'):name=name[2:]
 name=name.rstrip('/')
 if name in{'','.'}:return''
 need(not name.startswith('/')and'..'not in name.split('/')and str(PurePosixPath(name))==name,'member_path');return name
class Input:
 def __init__(self,row):self.row=row;self.size=row.before[6];self.offset=0;self.digest=sha256();self.closed=False
 def read(self,n):
  need(not self.closed and type(n)is int and 0<=n<=65536,'compressed_read_bound')
  if self.offset==self.size:return b''
  b=self.row.read(min(n,self.size-self.offset),self.offset);need(b,'compressed_short');self.offset+=len(b);self.digest.update(b);return b
 def close(self):need(not self.closed,'input_reclose');self.closed=True
class MemberInput:
 def __init__(self,f,size):self.f=f;self.size=size;self.offset=0;self.digest=sha256();self.closed=False
 def read(self,n):
  need(not self.closed and type(n)is int and 0<=n<=65536,'nested_read_bound');b=self.f.read(min(n,self.size-self.offset));need(type(b)is bytes and len(b)<=n and(self.offset==self.size or b),'nested_short');self.offset+=len(b);self.digest.update(b);return b
 def close(self):need(not self.closed,'nested_reclose');self.f.close();need(self.f.closed,'nested_member_close');self.closed=True
class Decoded:
 def __init__(self,source,name,profile,check):
  self.source=source;self.profile=profile;self.check=check;self.offset=0;self.expanded=0;self.pending=b'';self.compressed_pending=b'';self.closed=False;self.kind='xz'if name.endswith('.xz')else'bz2'if name.endswith('.bz2')else'gz'if name.endswith('.gz')else'raw';self.decoder=lzma.LZMADecompressor(format=lzma.FORMAT_XZ,memlimit=profile['decoder_memory_max'])if self.kind=='xz'else bz2.BZ2Decompressor()if self.kind=='bz2'else zlib.decompressobj(16+zlib.MAX_WBITS)if self.kind=='gz'else None;self.eof=False
 def read(self,n):
  need(not self.closed and type(n)is int and 0<=n<=65536,'decoded_request');parts=[];count=0
  while count<n:
   self.check()
   if not self.pending:
    if self.eof:break
    if self.kind=='raw':
     out=self.source.read(min(65536,n-count));self.eof=self.source.offset==self.source.size
    else:
     if self.decoder.eof:self.eof=True;break
     requires=not self.compressed_pending if self.kind=='gz'else self.decoder.needs_input
     if requires:block=self.source.read(65536);need(block,'compressed_truncated')
     else:block=self.compressed_pending if self.kind=='gz'else b''
     out=self.decoder.decompress(block,65536)if self.kind=='gz'else self.decoder.decompress(block,max_length=65536)
     if self.kind=='gz':self.compressed_pending=self.decoder.unconsumed_tail
     if self.decoder.eof:need(self.source.offset==self.source.size and not self.decoder.unused_data and not self.compressed_pending,'compressed_trailing');self.eof=True
    need(self.expanded+len(out)<=self.profile['expanded_max'],'expanded_quota');self.expanded+=len(out);self.pending=out
    if not out:continue
   length=min(n-count,len(self.pending));parts.append(self.pending[:length]);self.pending=self.pending[length:];count+=length
  return b''.join(parts)
 def close(self):need(not self.closed,'decoder_reclose');self.closed=True
ACL=re.compile(rb'\b(?:acl_(?:get|set|copy|free|valid|init|delete|entries|from|to|calc|create|dup|cmp|check)[A-Za-z0-9_]*|(?:qcopy|copy|set|get|file_has|chmod_or_fchmod)_acl|permission_context|perm_copy_fd|attr_copy_fd|system\.posix_acl_(?:access|default))\b')
COPY=re.compile(rb'\b(?:copy_tree|copy_file|copyfile|set_permissions|set_acl|qset_acl|qcopy_acl|chmod|fchmod|fchmodat|setxattr|fsetxattr|lsetxattr|CAP_SETFCAP|security\.capability|GNULIB_ACL|USE_ACL)\b')
SOURCE_EXT=('.c','.cc','.cpp','.cxx','.h','.hh','.hpp','.hxx','.inc','.tcc','.ipp','.S','.s','.asm','.rs','.xs','.xsh','.y','.l','.m4','.ac','.am','.mk','.cmake','.in','.diff','.patch','.list','.def','.pl','.pm','.py','.sh','.awk')
BUILD_NAMES={'Makefile','GNUmakefile','CMakeLists.txt','meson.build','meson_options.txt','configure','configure.ac','configure.in','build.info','Cargo.toml','Config.in','series','rules'}
SEEDS={'audit':['lib/Makefile.am','lib/libaudit.c','lib/lookup_table.c','lib/audit_logging.c','lib/riscv64_table.h'],'e2fsprogs':['lib/et/Makefile.in','lib/et/com_err.c','lib/et/error_message.c','misc/fuse2fs.c'],'gcc-14':['libgcc/Makefile.in','libgcc/config.host','libgcc/unwind-dw2.c','libstdc++-v3/src/Makefile.am','libstdc++-v3/src/c++98/Makefile.am'],'libcap2':['libcap/Makefile','libcap/cap_file.c','libcap/cap_proc.c','libpsx/Makefile','libpsx/psx.c'],'diffutils':['src/Makefile.am','lib/gnulib.mk','lib/acl-internal.c','lib/set-permissions.c','lib/copy-file.c'],'findutils':['find/Makefile.am','xargs/Makefile.am','gl/lib/gnulib.mk','gl/lib/acl-internal.c','gl/lib/set-permissions.c'],'grep':['src/Makefile.am','lib/gnulib.mk','lib/acl-internal.c','lib/set-permissions.c'],'gzip':['Makefile.am','lib/gnulib.mk','lib/acl-internal.c','lib/set-permissions.c'],'postgresql-17':['src/interfaces/libpq/Makefile','src/interfaces/libpq/fe-connect.c'],'systemd':['src/libsystemd/meson.build','src/libudev/meson.build'],'perl':['Makefile.SH','perl.c','miniperlmain.c','ext/XS-APItest/Makefile.PL'],'libselinux':['src/Makefile'],'libsemanage':['src/Makefile'],'libsepol':['src/Makefile']}
class Pages:
 def __init__(self,scope,out,limit):self.scope=scope;self.out=out;self.limit=limit;self.rows={};self.refs={};self.n={};self.sizes={};self.observed=0
 def add(self,kind,row):
  length=len(enc(row))+1;need(length+512<=self.limit,'one_DATA_row_quota');items=self.rows.setdefault(kind,[])
  if items and self.sizes.get(kind,512)+length>self.limit:self.flush(kind);items=self.rows.setdefault(kind,[])
  items.append(row);self.sizes[kind]=self.sizes.get(kind,512)+length;self.observed+=length
 def flush(self,kind):
  rows=self.rows.get(kind,[])
  if rows:
   n=self.n.get(kind,0);self.n[kind]=n+1;ref=publish(self.scope,self.out/(kind+'-'+str(n).zfill(4)+'.json'),enc({'schema':'residual43_SOURCE_DATA_rows/1','kind':kind,'rows':rows}),self.limit);self.refs.setdefault(kind,[]).append(ref);self.rows[kind]=[];self.sizes[kind]=512
 def finish(self):
  for kind in tuple(self.rows):self.flush(kind)
  return self.refs
class Census:
 def __init__(self,scope,out,group,fixture=False):self.scope=scope;self.out=out;self.g=group;self.pages=Pages(scope,out,group['metadata_DATA_shard_max']);self.selected_bytes=0;self.selected=[];self.archives=[];self.missing_complete=[];self.fixture=fixture;self.tokens=None;self.tokens=self.target_tokens()
 def target_tokens(self):
  if self.tokens is not None:return self.tokens
  tokens=set()
  for row in self.g['current_bodies']:
   n=Path(row['path']).name;n=re.sub(r'\.so(?:\..*)?$','',n);tokens.update([n,n.removeprefix('lib'),n.replace('-','_')])
  return sorted(t for t in tokens if len(t)>=3)
 def select(self,path,body,reason,profile,archive):
  if body is None:self.missing_complete.append({'archive':archive,'member':path,'reason':reason,'precise_requirement':'Complete body exceeds this predeclared 2MiB capture role; exact member metadata and all candidate lines retained. No source property is granted.'});return
  need(self.selected_bytes+len(body)<=self.g['all_selected_complete_SOURCE_DATA_max'],'selected_complete_DATA_quota');self.selected_bytes+=len(body);h=sha256(body).hexdigest();refs=[]
  for off in range(0,len(body),48000):
   raw=enc({'member':path,'archive':archive,'offset':off,'hex':body[off:off+48000].hex(),'complete_member_bytes':len(body),'complete_member_sha256':h});refs.append(publish(self.scope,self.out/('source-'+str(len(self.selected)).zfill(4)+'-'+str(off).zfill(8)+'.json'),raw,131072))
  self.selected.append({'archive':archive,'member':path,'bytes':len(body),'sha256':h,'complete_source':True,'selection_role':reason,'chunks':refs})
 def scan(self,source,name,profile,authority,depth=0):
  need(depth in{0,1},'nested_depth');stream=Decoded(source,name,profile,self.scope.check);metadata=[0];seen=set();header_count=names=content=code_count=0;types={};nested=0;archive=None;collectors_closed=False;metadata_before=self.pages.observed;last_member_end=0
  class Info(tarfile.TarInfo):
   def _proc_member(self,t):
    need(self.type!=tarfile.GNUTYPE_SPARSE,'sparse_source_refused')
    if self.type in(tarfile.GNUTYPE_LONGNAME,tarfile.GNUTYPE_LONGLINK,tarfile.XHDTYPE,tarfile.XGLTYPE,tarfile.SOLARIS_XHDTYPE):need(0<=self.size<=131072,'TAR_metadata_one');metadata[0]+=self.size;need(metadata[0]<=16777216,'TAR_metadata_total')
    return super()._proc_member(t)
  try:
   if name.endswith('.diff.gz'):
    body=[];h=sha256();total=0
    while True:
     b=stream.read(65536)
     if not b:break
     total+=len(b);need(total<=2097152,'plain_diff_capture_quota');h.update(b);body.append(b)
    self.select(name,b''.join(body),'complete Debian source diff',profile,authority);content=total
   else:
    archive=tarfile.open(fileobj=stream,mode='r|',tarinfo=Info,bufsize=10240)
    for m in archive:
     self.scope.check();header_count+=1;need(header_count<=profile['headers_max'],'source_header_quota');path=canonical(m.name);need(path not in seen and len(m.linkname.encode())<=4096 and not m.issparse(),'source_duplicate_or_link');seen.add(path);names+=len(path.encode())+len(m.linkname.encode());need(names<=profile['names_max'],'source_names_quota');need(m.type in{tarfile.REGTYPE,tarfile.AREGTYPE,tarfile.DIRTYPE,tarfile.SYMTYPE,tarfile.LNKTYPE},'source_member_type');need(0<=m.size<=profile['regular_member_max'],'source_member_quota');content+=m.size;need(content<=profile['expanded_max'],'declared_content_quota');types[m.type.decode()]=types.get(m.type.decode(),0)+1
     row={'archive':authority,'member':path,'bytes':m.size,'type':m.type.decode(),'mode':m.mode,'uid':m.uid,'gid':m.gid,'linkname':m.linkname,'link_followed':False};makefile=Path(path).name.startswith(('Makefile.','GNUmakefile.'));seeded=any(path==seed or path.endswith('/'+seed)for seed in SEEDS.get(self.g['source_package'],[]));debian_patch='/debian/patches/'in'/'+path;debian_meta=any(path==n or path.endswith('/'+n)for n in('debian/rules','debian/control','debian/source/format','debian/patches/series'))or('/debian/'in'/'+path and'.install'in Path(path).name);is_source=path.endswith(SOURCE_EXT)or Path(path).name in BUILD_NAMES or makefile or seeded or debian_patch or debian_meta;is_build=Path(path).name in BUILD_NAMES or makefile or debian_meta or path.endswith(('.am','.mk','.cmake','.m4','.ac','.diff','.patch'))
     if m.isfile():
      f=archive.extractfile(m);member=MemberInput(f,m.size)
      if depth==0 and profile.get('nested_GCC_basename')and Path(path).name==profile['nested_GCC_basename']:
       need(self.g['source_package']=='gcc-14'and nested==0,'exact_GCC_nested_role');nested+=1;np=dict(profile,expanded_max=profile['nested_expanded_max'],headers_max=profile['nested_headers_max'],nested_GCC_basename=None);self.scan(member,Path(path).name,np,{'outer':authority,'outer_member':path},1);need(member.offset==m.size,'GCC_nested_full_member');row['sha256']=member.digest.hexdigest();row['nested_complete']=True
      else:
       h=sha256();count=0;linebuf=b'';lineno=0;hits=[];build_hits=[];parts=[];capture=is_source and m.size<=2097152
       while count<m.size:
        b=member.read(min(65536,m.size-count));count+=len(b);h.update(b)
        if capture:parts.append(b)
        if is_source:
         lines=(linebuf+b).split(b'\n');linebuf=lines.pop();need(len(linebuf)<=profile['source_line_max'],'source_line_quota')
         for line in lines:
          need(len(line)<=profile['source_line_max'],'source_line_quota');lineno+=1;category=[]
          if ACL.search(line):category.append('ACL_provider_or_copy_candidate')
          if COPY.search(line):category.append('ordinary_permission_xattr_or_copy_candidate')
          if category:need(len(line)<=8192 and len(hits)<5000,'candidate_line_DATA_quota');hits.append({'line':lineno,'categories':category,'literal':line.decode('utf-8','replace'),'line_bytes':len(line),'line_sha256':sha256(line).hexdigest()})
          if is_build and any(t.encode()in line for t in self.target_tokens()):need(len(line)<=8192 and len(build_hits)<5000,'build_line_DATA_quota');build_hits.append({'line':lineno,'literal':line.decode('utf-8','replace')})
       if is_source and linebuf:
        need(len(linebuf)<=profile['source_line_max'],'source_line_quota');lineno+=1
        category=[]
        if ACL.search(linebuf):category.append('ACL_provider_or_copy_candidate')
        if COPY.search(linebuf):category.append('ordinary_permission_xattr_or_copy_candidate')
        if category:need(len(linebuf)<=8192 and len(hits)<5000,'candidate_line_DATA_quota');hits.append({'line':lineno,'categories':category,'literal':linebuf.decode('utf-8','replace'),'line_bytes':len(linebuf),'line_sha256':sha256(linebuf).hexdigest(),'newline_present':False})
       need(count==m.size,'source_regular_complete');row['sha256']=h.hexdigest()
       if is_source:
        code_count+=1;self.pages.add('code-build-census',{**row,'lines':lineno,'candidate_lines':hits,'positive_target_build_lines':build_hits,'candidate_is_not_classification':True})
        acl_hit=any('ACL_provider_or_copy_candidate'in h['categories']for h in hits)
        if seeded or acl_hit or build_hits or debian_patch or debian_meta:self.select(path,b''.join(parts)if capture else None,'explicit role seed'if seeded else'complete relevant Debian patch'if debian_patch else'complete Debian package/patch-selection metadata'if debian_meta else'positive current-target build text'if build_hits else'affected-implementation candidate; not disposition',profile,authority)
       member.close();need(member.closed,'source_member_collector_close')
      need(member.closed,'source_member_reader_closed')
     self.pages.add('full-member-inventory',row);need(self.pages.observed-metadata_before<=profile['metadata_max'],'source_inventory_metadata_quota');last_member_end=max(last_member_end,m.offset_data+((m.size+511)//512)*512);archive.members.clear()
    need(not any(archive.fileobj.buf),'TAR_buffered_nonzero_tail')
    while True:
     tail=stream.read(65536)
     if not tail:break
     need(not any(tail),'TAR_nonzero_tail')
    need(stream.expanded>=last_member_end+1024,'TAR_two_zero_end_blocks_required')
   need(stream.eof and source.offset==source.size,'compressed_full_EOF')
   if profile.get('nested_GCC_basename'):need(nested==1,'required_GCC_nested_source_missing')
  finally:
   if archive is not None:archive.close();need(archive.closed,'TAR_close')
   stream.close();need(stream.closed,'decoder_close');source.close();need(source.closed,'source_collector_close');collectors_closed=True
  record={'authority':authority,'name':name,'depth':depth,'headers':header_count,'regular_declared_content':content,'expanded_bytes':stream.expanded,'member_type_counts':types,'source_build_members':code_count,'TAR_metadata_bytes':metadata[0],'XZ_gzip_bzip2_integrity_and_exact_EOF':True,'zero_TAR_tail':not name.endswith('.diff.gz'),'collectors_closed':collectors_closed,'compressed_bytes':source.size,'compressed_sha256':source.digest.hexdigest(),'quota_profile':profile};self.archives.append(record);return record
 def finish(self):return self.pages.finish()
def verify_DSC(raw,g):
 text=raw.decode('utf-8');lines=text.splitlines();need(text.startswith('-----BEGIN PGP SIGNED MESSAGE-----\n')and lines.count('-----BEGIN PGP SIGNATURE-----')==1 and lines.count('Source: '+g['source_package'])==1 and lines.count('Version: '+g['source_version'])==1 and lines.count('Checksums-Sha256:')==1,'exact_DSC_identity');i=lines.index('Checksums-Sha256:')+1;actual=[]
 while i<len(lines)and lines[i].startswith(' '):
  fields=lines[i].split();need(len(fields)==3 and fields[1].isdigit(),'DSC_row');actual.append((fields[2],int(fields[1]),fields[0]));i+=1
 expected=[(m['name'],m['bytes'],m['sha256'])for m in g['profiles']if m['kind']!='descriptor'];need(sorted(actual)==sorted(expected),'complete_DSC_signed_member_binding')
def collect(scope,g,out,cache,official_url=None,no_redirect=None,fixtures=None):
 census=Census(scope,out,g,fixtures is not None);downloaded=[];dsc=None
 for row in g['profiles']:
  if fixtures is None:r,ref=download(scope,row,cache,official_url,no_redirect)
  else:
   path=fixtures/row['name'];r=scope.open(path,(row['bytes'],row['sha256']),row['exact_compressed_max']);ref={'path':str(path),'bytes':row['bytes'],'sha256':row['sha256']}
  downloaded.append(ref)
  if row['name'].endswith(('.tar.xz','.tar.gz','.tar.bz2','.tar','.diff.gz')):
   source=Input(r);observed=census.scan(source,row['name'],row,{'name':row['name'],'bytes':row['bytes'],'sha256':row['sha256']});need(observed['compressed_sha256']==row['sha256'],'whole_archive_hash_from_consumed_stream');need(r.hash()==row['sha256'],'archive_original_hash_after_all_collectors_close')
  elif row['kind']=='descriptor':
   need(row['bytes']<=131072,'DSC_max');dsc=b''.join(r.read(min(65536,row['bytes']-i),i)for i in range(0,row['bytes'],65536))
  r.close()
 if fixtures is None:need(dsc is not None,'DSC_required');verify_DSC(dsc,g)
 refs=census.finish();index=publish(scope,out/'selected-complete-source-index.json',enc({'selected':census.selected,'complete_SOURCE_DATA_bytes':census.selected_bytes,'exact_complete_source_gaps':census.missing_complete}),4194304)
 review={'schema':'OWNED_SYNTHETIC_SOURCE_PROTOCOL_FIXTURE/1'if fixtures is not None else'Root_selected_residual43_authentic_SOURCE_CI_collection/1','source_package':g['source_package'],'source_version':g['source_version'],'architecture':g['architecture'],'current_bodies':g['current_bodies'],'current_full_owner_variants':g['current_full_owner_variants'],'source_role_question':g['source_role_question'],'exact_archive_finalities':census.archives,'full_inventory_and_code_build_census':refs,'selected_complete_source_index':index,'exact_complete_source_gaps':census.missing_complete,'source_property_or_no_ACL_classification_granted':False,'source_roles':'Positive observed current-target build text and complete selected target/candidate sources are evidence for subsequent finite member/patch classification; candidate names and zero scans alone are never noncaller proof.','qualifications':['audit RISC-V syscall catalogues require separation from implementations.','libcom_err target differs from e2fsprogs fuse2fs ACL code.','security.capability xattrs are capability semantics, not automatically POSIX ACL-copy CVE.','gnulib ACL/permission/copy implementations in diff/find/grep/gzip remain candidates until exact build-selection/patch-role review.','Compiler flags, arbitrary providers/children, generated historical output and source-to-instruction equivalence are not inferred.'],'downloaded':downloaded,'security_accepted':False,'SOURCE_FLAGS_created':False,'caller_fix_created':False,'actual_security_review':None,'actual_runtime_or_operational_gates':None,'receipt_finality':'Conditional until all actual originals/publishers close, deadline recheck and actor returns0.'};raw=enc(review);need(len(raw)<=g['review_packet_max'],'review_packet_512KiB');return publish(scope,out/'review.json',raw,g['review_packet_max'])
def main(argv=None):
 parser=argparse.ArgumentParser(description=__doc__);m=parser.add_mutually_exclusive_group(required=True);m.add_argument('--collect-group',type=int);m.add_argument('--owned-fixture-plan',type=Path);parser.add_argument('--fixture-plan-sha256');parser.add_argument('--collector-sha256',required=True);parser.add_argument('--source-io-sha256',required=True);parser.add_argument('--output',required=True,type=Path);args=parser.parse_args(argv);scope=None
 try:
  resource.setrlimit(resource.RLIMIT_AS,(805306368,805306368))
  output=args.output.absolute();need(not os.path.lexists(output)and output.parent.resolve(strict=True)==output.parent,'fresh_output');output.mkdir(mode=0o700);cache=output/'compressed-inputs';cache.mkdir(mode=0o700)
  if args.owned_fixture_plan is not None:
   need(re.fullmatch('[0-9a-f]{64}',args.fixture_plan_sha256 or'')is not None,'fixture_plan_pin');scope=Scope(60)
  else:
   need(os.getuid()==os.geteuid()==1001 and os.getgid()==os.getegid(),'hosted_SOURCE_UID1001');scope=Scope(900)
  for path,digest in[(Path(__file__).absolute(),args.collector_sha256),(HERE/'source_io.py',args.source_io_sha256)]:need(re.fullmatch('[0-9a-f]{64}',digest or'')is not None,'own_source_pin');scope.open(path,(path.stat().st_size,digest),131072)
  if args.owned_fixture_plan is not None:
   p=args.owned_fixture_plan.absolute();g=small(scope,p,(p.stat().st_size,args.fixture_plan_sha256));need(g['schema']=='EXPLICITLY_OWNED_SYNTHETIC_SOURCE_PROTOCOL_FIXTURE/1','fixture_schema');result=collect(scope,g,output,cache,fixtures=p.parent)
  else:
   profiles,official_url,no_redirect=load_bundle(scope);need(type(args.collect_group)is int and 0<=args.collect_group<43,'fixed_group_index');g=profiles['groups'][args.collect_group];need(g['seconds_max']==900,'declared_scope_before_first_open');result=collect(scope,g,output,cache,official_url,no_redirect)
  finals=scope.finish();print(enc({'status':'SOURCE_DATA_COLLECTED_NOT_ACCEPTED','review':result,'actual_group_index':args.collect_group,'explicitly_owned_fixture':args.owned_fixture_plan is not None,'physical_original_and_publication_finalizers':finals,'physical_errors':[],'security_accepted':False}).decode(),end='');need(time.monotonic()<scope.end and(scope.started,scope.end)==scope.original_clock,'actual_return_original_deadline');return 0
 except BaseException as e:
  # Failed prefixes stay held and the owned CI child exits. Do not retry
  # uncertain closes or manufacture a normal physical-finality PASS.
  if scope is not None:scope.note(e)
  print(enc({'status':'SOURCE_COLLECTION_REFUSED','class':type(e).__name__,'code':str(e)[:180],'latched_scope_errors':scope.errors if scope else[],'failed_prefixes':[{'path':str(getattr(r,'path','')),'original_fd_captured':getattr(r,'original_fd',None)is not None,'admitted':getattr(r,'admitted',False),'closed':getattr(r,'closed',False),'uncertain':getattr(r,'uncertain',False)}for r in(scope.rows if scope else[])],'normal_physical_finality_proven':False,'security_accepted':False}).decode(),end='');return 1
if __name__=='__main__':raise SystemExit(main())
