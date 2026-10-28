"""UNARMED read-only ACL import/ownership inventory of one isolated public image.

Nothing inspected is executed or loaded. This is not a safe-use determination,
vulnerability disposition, installed-payload gate, or release authorization.
The external controller must attest exact image identity, readonly rootfs, no
network/capabilities and exactly the public /proof bind before collection.
"""
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path,PurePosixPath
from urllib.parse import parse_qsl,unquote,urlsplit
import argparse,json,math,os,posixpath,re,signal,stat,struct,sys,time

IMAGE='sha256:ba938a7dd1ab1a0d9ebacdae768861a45276ba7caacead40ed319fa8e1bef218'
COMMIT='6d257bf4a92ce196e90fdbe138d59f48eddd95c0'
SBOM_SHA='5e6a6930bad65191881b6c8366ef58a67c414318c14c0ca28f8dc0570952867c'
PARSER_SOURCE_SHA='001e51d270c079153cc38db1be53c723fbbc8a8bb9e981e718e7b657127bc778'
MAX_ENTRIES=100000;MAX_FILES=50000;MAX_BYTES=2*1024**3;MAX_ELF=128*1024**2
MAX_REPORT=4*1024**2;MAX_SECONDS=120;MAX_META=4*1024**2;MAX_PACKAGE_LIST_TOTAL=64*1024**2
EXCLUDED=frozenset(('/proc','/sys','/dev','/proof','/etc/hosts','/etc/hostname','/etc/resolv.conf','/.dockerenv'))
LEGACY=frozenset(('acl_get_file','acl_set_file','acl_extended_file','acl_delete_def_file'))
AT=frozenset(name+'_at'for name in LEGACY)
FD=frozenset(('acl_get_fd','acl_set_fd','acl_extended_fd'))
HELPER_IMPORTS=frozenset(('perm_copy_fd','attr_copy_fd'))
PHASE='unarmed'

def require(value,code):
 if not value:raise RuntimeError(code)
def identity(s):return(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)

def elf_metadata(raw):
    """Read bounded ELF section metadata; missing dynamic sections fail closed.

    Both ELF classes and byte orders are supported. Sectionless dynamic objects
    are refused rather than silently omitted from the consumer inventory.
    """
    require(16 <= len(raw) <= MAX_ELF and raw[:4] == b'\x7fELF', 'elf_identity')
    kind, order = raw[4], raw[5]
    require(kind in (1, 2) and order in (1, 2) and raw[6] == 1, 'elf_encoding')
    endian = '<' if order == 1 else '>'
    hfmt = endian + ('HHIIIIIHHHHHH' if kind == 1 else 'HHIQQQIHHHHHH')
    hsize = 16 + struct.calcsize(hfmt)
    require(len(raw) >= hsize, 'elf_header_bound')
    header = struct.unpack_from(hfmt, raw, 16)
    etype, machine, version, _, phoff, shoff, _, ehsize, phsize, phnum, shsize, shnum, _ = header
    require(etype in (1, 2, 3) and version == 1 and ehsize == hsize, 'elf_header')
    sfmt = endian + ('IIIIIIIIII' if kind == 1 else 'IIQQQQIIQQ')
    pfmt = endian + ('IIIIIIII' if kind == 1 else 'IIQQQQQQ')
    require(0 < shnum <= 8192 and shsize == struct.calcsize(sfmt), 'elf_section_inventory')
    require(shoff >= hsize and shoff + shnum * shsize <= len(raw), 'elf_section_bound')
    require(phnum <= 4096 and (phnum == 0 or phsize == struct.calcsize(pfmt)
            and phoff >= hsize and phoff + phnum * phsize <= len(raw)), 'elf_program_bound')
    sections = [struct.unpack_from(sfmt, raw, shoff + n * shsize) for n in range(shnum)]
    require(sections[0] == (0,) * 10, 'elf_null_section')
    for section in sections:
        require(section[1] == 8 or section[4] + section[5] <= len(raw), 'elf_section_bound')

    def string(table, offset):
        require(table[1] == 3 and 0 <= offset < table[5], 'elf_string_bound')
        start, end = table[4] + offset, table[4] + table[5]
        stop = raw.find(b'\0', start, min(end, start + 4097))
        require(stop >= start, 'elf_string_bound')
        value = raw[start:stop]
        require(all(32 <= c < 127 for c in value), 'elf_string_encoding')
        return value.decode('ascii')

    dynamic = [section for section in sections if section[1] == 6]
    require(len(dynamic) <= 1, 'elf_dynamic_inventory')
    segments = []
    for n in range(phnum):
        ph = struct.unpack_from(pfmt, raw, phoff + n * phsize)
        offset, size = (ph[1], ph[4]) if kind == 1 else (ph[2], ph[5])
        require(offset + size <= len(raw), 'elf_program_bound')
        if ph[0] == 2:
            segments.append((offset, size))
    require(segments == [(s[4], s[5]) for s in dynamic], 'elf_dynamic_section_required')
    needed, tags = [], {}
    if dynamic:
        section = dynamic[0]
        entry = 8 if kind == 1 else 16
        require(section[9] == entry and section[5] % entry == 0
                and section[6] < shnum, 'elf_dynamic_shape')
        table = sections[section[6]]
        ended = False
        for pos in range(section[4], section[4] + section[5], entry):
            tag, value = struct.unpack_from(endian + ('iI' if kind == 1 else 'qQ'), raw, pos)
            if tag == 0:
                ended = True
            else:
                require(not ended, 'elf_dynamic_after_null')
                if tag == 1:
                    name = string(table, value)
                    require(name and '/' not in name and len(name) <= 256, 'elf_needed_name')
                    needed.append(name)
                elif tag in (5, 6, 10, 11):
                    require(tag not in tags, 'elf_dynamic_shape')
                    tags[tag] = value
        require(ended and len(needed) <= 256, 'elf_dynamic_bound')
        require(tags.get(5) == table[3] and tags.get(10) == table[5], 'elf_dynamic_string_binding')
    symbols = [section for section in sections if section[1] == 11]
    require(len(symbols) <= 1, 'elf_symbol_inventory')
    imported = set()
    helper_imports = set()
    for section in symbols:
        entry = 16 if kind == 1 else 24
        require(section[9] == entry and section[5] % entry == 0
                and section[5] // entry <= 300000 and section[6] < shnum, 'elf_symbol_bound')
        table = sections[section[6]]
        if dynamic:
            require(section[6] == dynamic[0][6] and tags.get(6) == section[3]
                    and tags.get(11) == entry, 'elf_dynamic_symbol_binding')
        for pos in range(section[4], section[4] + section[5], entry):
            fields = struct.unpack_from(endian + ('IIIBBH' if kind == 1 else 'IBBHQQ'), raw, pos)
            name = string(table, fields[0])
            undefined = fields[5] == 0 if kind == 1 else fields[3] == 0
            if undefined and name in LEGACY | AT | FD:
                imported.add(name)
            if undefined and name in HELPER_IMPORTS:
                helper_imports.add(name)
    require(not dynamic or symbols, 'elf_dynamic_symbols_required')
    return {'class': kind * 32, 'byte_order': 'little' if order == 1 else 'big',
            'machine': machine, 'type': etype, 'needed': sorted(needed),
            'undefined_legacy_acl': sorted(imported & LEGACY),
            'undefined_at_acl': sorted(imported & AT), 'undefined_fd_acl': sorted(imported & FD),
            'undefined_acl_attr_helper_imports': sorted(helper_imports)}


def document(raw):
 require(type(raw)is bytes and 0<len(raw)<=MAX_META,'metadata_bound')
 def unique(pairs):
  result={}
  for name,value in pairs:require(name not in result,'metadata_duplicate');result[name]=value
  return result
 def finite(value):
  number=float(value);require(math.isfinite(number),'metadata_nonfinite');return number
 return json.loads(raw,object_pairs_hook=unique,parse_float=finite,
  parse_constant=lambda _:require(False,'metadata_nonfinite'))


def canonical_name(value):
 require(type(value)is str and value.startswith('/')and len(os.fsencode(value))<=4096
  and '\x00'not in value and str(PurePosixPath(value))==value and '..'not in PurePosixPath(value).parts,'metadata_path')
 return value


@contextmanager
def directories(rootfd,parts):
 """Every component is opened relative to an already held directory FD."""
 opened=[];parent=rootfd
 try:
  for name in parts:
   require(type(name)is str and name not in ('','.','..')and '/'not in name,'directory_component')
   before=identity(os.stat(name,dir_fd=parent,follow_symlinks=False))
   require(stat.S_ISDIR(before[2]),'directory_type')
   fd=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=parent)
   opened.append((fd,parent,name,before));require(identity(os.fstat(fd))==before,'directory_changed');parent=fd
  yield parent
 finally:
  for fd,parent,name,before in reversed(opened):
   try:require(identity(os.fstat(fd))==identity(os.stat(name,dir_fd=parent,follow_symlinks=False))==before,'directory_changed')
   finally:os.close(fd)
   require(identity(os.stat(name,dir_fd=parent,follow_symlinks=False))==before,'directory_closed_changed')


def read_at(parent,name,maximum,*,prefix_only=False,expected=None):
 before=identity(os.stat(name,dir_fd=parent,follow_symlinks=False))
 require(stat.S_ISREG(before[2])and 0<=before[6]<=maximum,'metadata_file')
 fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,dir_fd=parent)
 try:
  require(identity(os.fstat(fd))==before,'metadata_open_changed')
  if expected is not None:require(before==expected,'metadata_open_changed')
  limit=min(4,before[6])if prefix_only else before[6]
  data=bytearray()
  while len(data)<limit:
   part=os.read(fd,min(65536,limit-len(data)));require(part,'metadata_short');data.extend(part)
  if not prefix_only:require(os.read(fd,1)==b'','metadata_grew')
  require(identity(os.fstat(fd))==identity(os.stat(name,dir_fd=parent,follow_symlinks=False))==before,'metadata_changed')
 finally:os.close(fd)
 require(identity(os.stat(name,dir_fd=parent,follow_symlinks=False))==before,'metadata_closed_changed')
 return bytes(data),before


def installed_packages(raw):
 require(type(raw)is bytes and 0<len(raw)<=MAX_META,'package_status_bound');result={}
 for paragraph in raw.decode('utf-8').split('\n\n'):
  fields={};previous=None
  for line in paragraph.splitlines():
   if not line:continue
   if line[0]in ' \t':
    require(previous is not None and previous not in {'package','status','version','architecture','source'},'package_status_shape');continue
   name,sep,value=line.partition(':');key=name.lower()
   require(sep and re.fullmatch('[!-9;-~]+',name)and name[0]not in '#-'and key not in fields,'package_status_shape')
   fields[key]=value.strip(' \t');previous=key
  if fields.get('status')!='install ok installed':continue
  name,version,arch=(fields.get(k,'')for k in ('package','version','architecture'))
  require(re.fullmatch('[a-z0-9][a-z0-9+.-]{0,127}',name)and re.fullmatch('[A-Za-z0-9.+:~_-]{1,256}',version)
   and arch in {'amd64','all'},'package_identity')
  source=fields.get('source',name)
  match=re.fullmatch(r'([a-z0-9][a-z0-9+.-]{0,127})(?: \(([A-Za-z0-9.+:~_-]{1,256})\))?',source)
  require(match is not None,'package_source');key=name+':'+arch;require(key not in result,'package_duplicate')
  result[key]={'package':name,'installed_version':version,'architecture':arch,
   'source_package':match[1],'source_version':match[2]or version}
 require(0<len(result)<=2048,'package_count');return result


def sbom_packages(raw):
 value=document(raw);require(type(value)is dict and type(value.get('components'))is list
  and len(value['components'])<=20000,'sbom_shape');result={}
 for component in value['components']:
  require(type(component)is dict,'sbom_component')
  purl=component.get('purl','')
  if not(type(purl)is str and purl.startswith('pkg:deb/debian/')):continue
  require(len(purl)<=4096,'sbom_purl')
  parsed=urlsplit(purl);path=parsed.path[len('deb/debian/'):];name,sep,version=path.partition('@')
  require(sep and not parsed.fragment and not parsed.netloc,'sbom_purl')
  name,version=unquote(name),unquote(version);pairs=parse_qsl(parsed.query,keep_blank_values=True,strict_parsing=True)
  require(len(pairs)==len({k for k,_ in pairs})and {'arch','distro'}<={k for k,_ in pairs}<={'arch','distro','epoch'},'sbom_purl')
  qualifiers=dict(pairs);epoch=qualifiers.get('epoch')
  require(epoch is None or re.fullmatch('(?:0|[1-9][0-9]*)',epoch),'sbom_purl')
  if epoch is not None:
   require(':'not in version,'sbom_purl');version=epoch+':'+version
  arch=qualifiers['arch'];require(arch in {'amd64','all'}and qualifiers['distro']=='debian-13.7'
   and component.get('name')==name and component.get('version')==version,'sbom_purl')
  properties=component.get('properties');require(type(properties)is list and len(properties)<=256,'sbom_properties');props={}
  for item in properties:
   require(type(item)is dict and type(item.get('name'))is str and len(item['name'])<=256 and type(item.get('value'))is str and len(item['value'])<=4096
    and item['name']not in props,'sbom_properties');props[item['name']]=item['value']
  require(props.get('aquasecurity:trivy:PkgType')=='debian','sbom_package_type')
  source,upstream,release=(props.get('aquasecurity:trivy:'+k,'')for k in ('SrcName','SrcVersion','SrcRelease'))
  source_epoch=props.get('aquasecurity:trivy:SrcEpoch')
  require(re.fullmatch('[a-z0-9][a-z0-9+.-]{0,127}',source)and re.fullmatch('[A-Za-z0-9.+:~_-]{1,256}',upstream)
   and re.fullmatch('[A-Za-z0-9.+:~_-]{0,256}',release)
   and(source_epoch is None or re.fullmatch('(?:0|[1-9][0-9]*)',source_epoch)and ':'not in upstream+release),'sbom_source')
  layer=props.get('aquasecurity:trivy:LayerDiffID');require(type(layer)is str and re.fullmatch('sha256:[a-f0-9]{64}',layer),'sbom_layer')
  key=name+':'+arch;require(key not in result,'sbom_duplicate')
  result[key]={'package':name,'installed_version':version,'architecture':arch,'source_package':source,
   'source_version':(source_epoch+':'if source_epoch is not None else'')+upstream+('-'+release if release else''),'purl':purl,'layer_diffid':layer}
 return result


def owner_lists(rootfd,packages,end):
 result={};total=0
 with directories(rootfd,('var','lib','dpkg','info'))as info:
  with os.scandir(info)as entries:
   names=set()
   for entry in entries:
    require(time.monotonic()<end and len(names)<20000,'package_list_inventory');names.add(entry.name)
  for key,package in packages.items():
   require(time.monotonic()<end,'inventory_timeout')
   choices={package['package']+'.list',key+'.list'}&names
   require(len(choices)==1,'package_list_missing_or_ambiguous')
   raw,_=read_at(info,next(iter(choices)),MAX_META);total+=len(raw);require(total<=MAX_PACKAGE_LIST_TOTAL,'package_list_total')
   lines=raw.decode('utf-8').splitlines();require(len(lines)<=100000,'package_list_count')
   for value in lines:
    if value=='/.':continue
    path=canonical_name(value);result.setdefault(path,[]).append(key)
    require(len(result)<=200000,'package_path_total')
 return result


def canonical_target(path,links):
 """Resolve only observed symlink text; never open through a symlink."""
 path=canonical_name(path);seen=set()
 for _ in range(32):
  require(path not in seen,'alias_cycle');seen.add(path);parts=PurePosixPath(path).parts[1:];prefix='';replaced=False
  for index,name in enumerate(parts):
   prefix+='/'+name
   if prefix in links:
    path=posixpath.normpath(links[prefix]+('/'+'/'.join(parts[index+1:])if index+1<len(parts)else''))
    canonical_name(path);replaced=True;break
  if not replaced:return path
 raise RuntimeError('alias_depth')


def classify_elf(raw):
 row=elf_metadata(raw);kind,order=raw[4],raw[5];endian='<'if order==1 else'>'
 hfmt=endian+('HHIIIIIHHHHHH'if kind==1 else'HHIQQQIHHHHHH');header=struct.unpack_from(hfmt,raw,16)
 phoff,shoff,phsize,phnum,shsize,shnum=header[4],header[5],header[8],header[9],header[10],header[11]
 pfmt=endian+('IIIIIIII'if kind==1 else'IIQQQQQQ');dynamic=any(struct.unpack_from(pfmt,raw,phoff+n*phsize)[0]==2 for n in range(phnum))
 sfmt=endian+('IIIIIIIIII'if kind==1 else'IIQQQQIIQQ');sections=[struct.unpack_from(sfmt,raw,shoff+n*shsize)for n in range(shnum)]
 definitions=set();api_names={n.encode()for n in LEGACY|AT|FD}
 for section in sections:
  if section[1]!=2:continue
  entry=16 if kind==1 else 24
  require(section[9]==entry and section[5]%entry==0 and section[5]//entry<=300000 and section[6]<shnum,'elf_static_symbol_bound')
  table=sections[section[6]];require(table[1]==3,'elf_static_string_bound')
  for offset in range(section[4],section[4]+section[5],entry):
   fields=struct.unpack_from(endian+('IIIBBH'if kind==1 else'IBBHQQ'),raw,offset);undefined=fields[5]==0 if kind==1 else fields[3]==0
   if undefined:continue
   require(fields[0]<table[5],'elf_static_string_bound');start=table[4]+fields[0];stop=raw.find(b'\0',start,min(table[4]+table[5],start+4097))
   require(stop>=start,'elf_static_string_bound');value=raw[start:stop]
   if value in api_names:definitions.add(value.decode('ascii'))
 row.update(elf_parse_status='PARSED',dynamic_segment_present=dynamic,
  defined_acl_symbols=sorted(definitions),static_or_relocatable_candidate=not dynamic,
  embedded_acl_xattr_string_candidate=any(v in raw for v in (b'system.posix_acl_access',b'system.posix_acl_default')),
  callsite_safety_status='UNASSESSED')
 return row


def inventory(rootfd,end):
 count=regular=advertised=elf_bytes=names_bytes=0;rows=[];links={};exclusions=[];special=[];hardlinks={}
 def walk(parent,public,depth):
  nonlocal count,regular,advertised,elf_bytes,names_bytes
  require(depth<=64,'inventory_depth');before=identity(os.fstat(parent))
  with os.scandir(parent)as entries:
   for entry in entries:
    require(time.monotonic()<end,'inventory_timeout');count+=1;path=public+'/'+entry.name;names_bytes+=len(os.fsencode(path))
    require(count<=MAX_ENTRIES and len(os.fsencode(path))<=4096 and names_bytes<=8*1024**2,'inventory_count_bound')
    info=identity(os.stat(entry.name,dir_fd=parent,follow_symlinks=False))
    if path in EXCLUDED:exclusions.append(path);continue
    if stat.S_ISLNK(info[2]):
     text=os.readlink(entry.name,dir_fd=parent);require(len(os.fsencode(text))<=4096,'symlink_bound');names_bytes+=len(os.fsencode(text))
     require(names_bytes<=8*1024**2,'inventory_name_bound')
     normalized=posixpath.normpath(text if text.startswith('/')else posixpath.join(posixpath.dirname(path),text))
     links[path]='/'+normalized.lstrip('/')
     require(identity(os.stat(entry.name,dir_fd=parent,follow_symlinks=False))==info,'inventory_changed')
    elif stat.S_ISDIR(info[2]):
     fd=os.open(entry.name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=parent)
     try:require(identity(os.fstat(fd))==info,'inventory_changed');walk(fd,path,depth+1);require(identity(os.fstat(fd))==identity(os.stat(entry.name,dir_fd=parent,follow_symlinks=False))==info,'inventory_changed')
     finally:os.close(fd)
     require(identity(os.stat(entry.name,dir_fd=parent,follow_symlinks=False))==info,'inventory_closed_changed')
    elif stat.S_ISREG(info[2]):
     regular+=1;advertised+=info[6];require(regular<=MAX_FILES and advertised<=MAX_BYTES,'inventory_byte_bound')
     prefix,_=read_at(parent,entry.name,MAX_BYTES,prefix_only=True,expected=info)
     if prefix!=b'\x7fELF':continue
     raw,actual=read_at(parent,entry.name,MAX_ELF,expected=info);elf_bytes+=len(raw);require(elf_bytes<=MAX_BYTES,'inventory_byte_bound')
     try:metadata=classify_elf(raw)
     except (RuntimeError,struct.error,OverflowError,UnicodeError):metadata={'elf_parse_status':'UNASSESSED_UNSUPPORTED_METADATA','callsite_safety_status':'UNASSESSED'}
     rows.append({'path':path,'bytes':len(raw),'sha256':sha256(raw).hexdigest(),'identity':list(actual),**metadata})
     hardlinks.setdefault((actual[0],actual[1]),[]).append(path)
     require(len(json.dumps(rows,separators=(',',':')))<=MAX_REPORT-1048576,'report_bound')
    else:
     special.append({'path':path,'mode':info[2]});require(len(special)<=256,'special_file_count')
  require(identity(os.fstat(parent))==before,'inventory_changed')
 walk(rootfd,'',0);require(0<len(rows)<=8192,'elf_inventory_count')
 return {'entries_examined':count,'regular_files_examined':regular,'advertised_regular_bytes':advertised,
  'elf_bytes_read':elf_bytes,'elf_files':rows,'symlinks':links,'runtime_paths_excluded':sorted(exclusions),
  'special_metadata_only':special,'hardlink_sets':[sorted(paths)for paths in hardlinks.values()if len(paths)>1]}


def collect(root,sbom_raw,verifier):
 global PHASE
 end=time.monotonic()+MAX_SECONDS;root=Path(root);before=identity(root.lstat())
 require(root.resolve(strict=True)==root and stat.S_ISDIR(before[2]),'image_root')
 fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
 try:
  require(identity(os.fstat(fd))==before,'image_root');PHASE='package_metadata'
  with directories(fd,('var','lib','dpkg'))as dpkg:status_raw,_=read_at(dpkg,'status',MAX_META)
  packages=installed_packages(status_raw);sbom=sbom_packages(sbom_raw);owners=owner_lists(fd,packages,end)
  PHASE='elf_inventory';result=inventory(fd,end);links=result.pop('symlinks');canonical_owners={}
  for path,keys in owners.items():
   try:target=canonical_target(path,links)
   except RuntimeError:continue
   canonical_owners.setdefault(target,set()).update(keys)
  for row in result['elf_files']:
   bindings=[]
   for key in sorted(canonical_owners.get(row['path'],set())):
    actual=packages[key];expected=sbom.get(key)
    equal=expected is not None and all(actual[k]==expected[k]for k in ('package','installed_version','architecture','source_package','source_version'))
    bindings.append({**actual,'sbom_source_binding_matches':equal,'sbom':expected})
   row['package_owners']=bindings;row['source_binding_status']='BOUND_DPKG_AND_SEALED_SBOM'if bindings and all(b['sbom_source_binding_matches']for b in bindings)else'UNASSESSED'
   row['symlink_aliases']=[]
  by_path={row['path']:row for row in result['elf_files']}
  for path in sorted(links):
   try:target=canonical_target(path,links)
   except RuntimeError:continue
   if target in by_path:by_path[target]['symlink_aliases'].append(path)
  result['elf_files'].sort(key=lambda r:r['path']);result['directory_alias_prefixes']={k:v for k,v in links.items()if k in {'/bin','/sbin','/lib','/lib64'}}
  require(identity(os.fstat(fd))==identity(root.lstat())==before and time.monotonic()<end,'image_root_changed')
 finally:os.close(fd)
 require(identity(root.lstat())==before,'image_root_closed_changed')
 PHASE='complete'
 return {'schema':'image_acl_import_inventory/1','image_id':IMAGE,'source_commit':COMMIT,'verifier_sha256':verifier,
  'sbom_sha256':sha256(sbom_raw).hexdigest(),'parser_original_source_sha256':PARSER_SOURCE_SHA,
  'inventory':result,'scope':'all_regular_ELF_under_readonly_image_root_except_explicit_kernel_proof_and_runtime_injections',
  'elf_metadata_inventory_complete':all(r['elf_parse_status']=='PARSED'for r in result['elf_files']),
  'source_bindings_complete':all(r['source_binding_status']=='BOUND_DPKG_AND_SEALED_SBOM'for r in result['elf_files']),
  'snapshot_atomic':False,'directory_symlinks_followed':False,'inspected_code_executed':False,
  'static_or_obfuscated_code_completely_analyzed':False,'dlopen_or_ctypes_reachability_analyzed':False,
  'acl_flags_or_callsite_safety_proven':False,'whole_image_acl_closed':False,'production_mounts_or_data_used':False,
  'scanner_findings_modified':False,'security_accepted':False}


def main(argv=None):
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--collect-isolated-public-image',action='store_true')
 parser.add_argument('--image-id');parser.add_argument('--source-commit');parser.add_argument('--verifier-sha256');parser.add_argument('--proof-uid',type=int);parser.add_argument('--proof-gid',type=int)
 args=parser.parse_args(argv)
 if not args.collect_isolated_public_image:
  print(json.dumps({'status':'UNARMED','collection_performed':False,'security_accepted':False}));return 0
 require(os.geteuid()==os.getegid()==0 and sys.flags.isolated==sys.flags.no_site==sys.flags.ignore_environment==1,'isolated_container_root_required')
 require(args.image_id==IMAGE and args.source_commit==COMMIT and type(args.verifier_sha256)is str
  and re.fullmatch('[a-f0-9]{64}',args.verifier_sha256)and type(args.proof_uid)is int and args.proof_uid in {0,1001}
  and type(args.proof_gid)is int and 0<=args.proof_gid<=65535,'source_identity')
 require(Path(__file__)==Path('/proof/inventory.py'),'sealed_source_path')
 rootfd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC);frames=[]
 signal.signal(signal.SIGALRM,lambda *_:(_ for _ in ()).throw(RuntimeError('inventory_timeout')));signal.alarm(MAX_SECONDS+5)
 try:
  with directories(rootfd,('proof',))as proof:
   for name,pin in (('inventory.py',args.verifier_sha256),('sbom.cdx.json',SBOM_SHA)):
    frame=identity(os.stat(name,dir_fd=proof,follow_symlinks=False));require(frame[2:6]==(stat.S_IFREG|0o444,args.proof_uid,args.proof_gid,1)
     and 0<frame[6]<=MAX_META,'sealed_proof')
    raw,_=read_at(proof,name,MAX_META,expected=frame);require(sha256(raw).hexdigest()==pin,'sealed_proof');frames.append((name,frame))
    if name=='sbom.cdx.json':sbom_raw=raw
   result=collect(Path('/'),sbom_raw,args.verifier_sha256)
   for name,before in frames:require(identity(os.stat(name,dir_fd=proof,follow_symlinks=False))==before,'sealed_proof_changed')
 finally:os.close(rootfd);signal.alarm(0)
 for name,before in frames:require(identity((Path('/proof')/name).lstat())==before,'sealed_proof_closed_changed')
 raw=(json.dumps(result,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode();require(len(raw)<=MAX_REPORT,'report_bound')
 sys.stdout.buffer.write(raw);return 0


PUBLIC_FAILURE_CODES=frozenset(('alias_cycle','alias_depth','directory_changed','directory_closed_changed','directory_component','directory_type',
 'elf_dynamic_after_null','elf_dynamic_bound','elf_dynamic_inventory','elf_dynamic_section_required','elf_dynamic_shape','elf_dynamic_string_binding',
 'elf_dynamic_symbol_binding','elf_dynamic_symbols_required','elf_encoding','elf_header','elf_header_bound','elf_identity','elf_inventory_count',
 'elf_needed_name','elf_null_section','elf_program_bound','elf_section_bound','elf_section_inventory','elf_static_string_bound','elf_static_symbol_bound',
 'elf_string_bound','elf_string_encoding','elf_symbol_bound','elf_symbol_inventory','image_root','image_root_changed','image_root_closed_changed',
 'inventory_byte_bound','inventory_changed','inventory_closed_changed','inventory_count_bound','inventory_depth','inventory_name_bound','inventory_timeout',
 'isolated_container_root_required','metadata_bound','metadata_changed','metadata_closed_changed','metadata_duplicate','metadata_file','metadata_grew',
 'metadata_nonfinite','metadata_open_changed','metadata_path','metadata_short','package_count','package_duplicate','package_identity','package_list_count',
 'package_list_inventory','package_list_missing_or_ambiguous','package_list_total','package_path_total','package_source','package_status_bound',
 'package_status_shape','report_bound','sbom_component','sbom_duplicate','sbom_layer','sbom_package_type','sbom_properties','sbom_purl','sbom_shape',
 'sbom_source','sealed_proof','sealed_proof_changed','sealed_proof_closed_changed','sealed_source_path','source_identity','special_file_count','symlink_bound'))
PUBLIC_PHASES=frozenset(('unarmed','package_metadata','elf_inventory','complete'))


def failure(error):
 code=error.args[0]if type(error)is RuntimeError and len(error.args)==1 and type(error.args[0])is str and error.args[0]in PUBLIC_FAILURE_CODES else'acl_image_inventory_refused'
 phase=PHASE if type(PHASE)is str and PHASE in PUBLIC_PHASES else'unarmed'
 return{'status':'REFUSED','phase':phase,'code':code,'security_accepted':False}


if __name__=='__main__':
 try:raise SystemExit(main())
 except Exception as error:
  print(json.dumps(failure(error),sort_keys=True));raise SystemExit(1)from None
