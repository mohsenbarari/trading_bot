"""Finite managed age1.3.2+gold1 build contracts; no operational authority."""
from pathlib import PurePosixPath
from hashlib import sha256
import io,json,re,struct,tarfile

def need(v,c):
 if v is not True:raise RuntimeError(c)
def pairs(rows):
 d={}
 for k,v in rows:need(k not in d,'duplicate_json_key');d[k]=v
 return d
DECODER=json.JSONDecoder(object_pairs_hook=pairs,parse_constant=lambda _:need(False,'nonfinite_json'))
def documents(raw,maximum=16*1024**2):
 need(type(raw)is bytes and len(raw)<=maximum,'json_bound');text=raw.decode('utf8');offset=0;values=[]
 while offset<len(text):
  while offset<len(text)and text[offset].isspace():offset+=1
  if offset==len(text):break
  need(len(values)<100000,'json_document_bound');value,offset=DECODER.raw_decode(text,offset);values.append(value)
 return values
def document(raw):
 rows=documents(raw);need(len(rows)==1,'json_document_count');return rows[0]
def encoded(v):
 parts=[];size=0
 for part in json.JSONEncoder(sort_keys=True,separators=(',',':'),allow_nan=False).iterencode(v):
  b=part.encode();need(size+len(b)+1<=4*1024**2,'result_bound');parts.append(b);size+=len(b)
 return b''.join(parts)+b'\n'
def validate_lock(v):
 need(v['schema']=='age132_managed_tool_frozen_public_inputs/1'and v['runtime_install_backup_eligibility']is False,'input_purpose')
 need(v['go']=={'version':'go1.27.3','url':'https://go.dev/dl/go1.27.3.linux-amd64.tar.gz','redirect':'https://dl.google.com/go/go1.27.3.linux-amd64.tar.gz','bytes':70588678,'sha256':'65efbc80fdc1fa2cd2631cad5b481777a68e8725f34e89f50fc6ac714e379b40'},'go_toolchain_pin')
 need(v['age']['path']=='filippo.io/age'and v['age']['version']=='v1.3.2'and v['age']['origin']['Hash']=='b74dce4cdbe35b5e5f66c06d9612b72f89028758','age_source_pin')
 need(v['scanner']['path']=='golang.org/x/vuln'and v['scanner']['version']=='v1.9.0','scanner_source_pin')
 need(v['package']=={'name':'age','version':'1.3.2+gold1','architecture':'amd64','CLI_version':'v1.3.2+gold1','binaries':['age','age-keygen']},'package_scope')
 for key in('age_modules','scanner_modules'):
  rows=v[key];need(type(rows)is list and len(rows)==(17 if key=='age_modules'else 15),'module_inventory')
  names=[]
  for r in rows:
   need(set(r)=={'path','version','origin','module_sum','gomod_sum'}and re.fullmatch(r'[a-zA-Z0-9_.!/-]+',r['path'])is not None and'..'not in r['path'].split('/'),'module_path')
   need(re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+(?:-[a-zA-Z0-9.-]+)?',r['version'])is not None,'module_version')
   need(all(re.fullmatch(r'h1:[A-Za-z0-9+/]{43}=',r[k])is not None for k in('module_sum','gomod_sum')),'module_checksum');names.append(r['path'])
  need(names==sorted(set(names)),'module_inventory')
  versions={r['path']:r['version']for r in rows};need(versions['golang.org/x/crypto']=='v0.58.0'and versions['golang.org/x/net']=='v0.62.0'and versions['github.com/yuin/goldmark']=='v1.7.17','fixed_dependency_versions')
 return v
def download_record(raw,pin,*,origin_required=True):
 r=document(raw);need(type(origin_required)is bool and type(r)is dict and r.get('Path')==pin['path']and r.get('Version')==pin['version']and r.get('Sum')==pin['module_sum']and r.get('GoModSum')==pin['gomod_sum']and'Error'not in r and'Replace'not in r,'module_download_pin')
 # Go1.27.3's dependency wire may omit this optional provenance pointer.
 # Root source calls retain the stricter default; never invent missing VCS data.
 if'Origin'in r:need(type(r['Origin'])is dict and type(pin['origin'])is dict and r['Origin']==pin['origin'],'module_origin_pin')
 else:need(not origin_required,'module_origin_pin')
 return r
def graph(raw,main,expected):
 rows=documents(raw);actual={};mains=[]
 for r in rows:
  need(type(r)is dict and'Error'not in r and'Replace'not in r,'module_graph_replacement')
  name=r['Path'];need(name not in actual and name not in mains,'module_graph_duplicate')
  if r.get('Main')is True:need(name==main,'module_graph_main');mains.append(name)
  else:actual[name]=r['Version']
 need(mains==[main]and actual=={r['path']:r['version']for r in expected},'module_graph_exact');return actual
GO_PACKAGE_FIELDS='ImportPath,Name,Imports,ImportMap,Match,DepOnly,Standard,Module,Error,DepsErrors,Incomplete,ForTest'
def package_projection(raw,main,expected,pattern,required_roots):
 """Closed Linux/amd64/CGO0 production package profile; no test variants."""
 rows=documents(raw,8*1024**2);need(0<len(rows)<=20000 and pattern in{'./...','./cmd/age','./cmd/age-keygen','./cmd/govulncheck'},'package_projection_bound')
 packages={};owners={};roots=[];versions={r['path']:r['version']for r in expected};required=set(GO_PACKAGE_FIELDS.split(','))
 def path(value):return type(value)is str and re.fullmatch(r'[A-Za-z0-9_./!-]+',value)is not None and not value.startswith('/')and'..'not in value.split('/')
 need(type(required_roots)in{tuple,list}and 0<len(required_roots)<=3 and all(path(p)for p in required_roots),'package_projection_required_root')
 # The real partial-field producer preserves JSON omitempty: absent documented
 # zero fields have Go zero values; identifiers and every present type stay strict.
 for row in rows:
  need(type(row)is dict and {'ImportPath','Name'}<=set(row)<=required,'package_projection_fields');name=row['ImportPath']
  need(path(name)and name not in packages and type(row['Name'])is str and re.fullmatch('[A-Za-z_][A-Za-z0-9_]*',row['Name'])is not None,'package_projection_package')
  need(row.get('Error')is None and row.get('DepsErrors')in(None,[])and row.get('Incomplete',False)is False and row.get('ForTest','')=='','package_projection_errors')
  need(type(row.get('Standard',False))is bool and type(row.get('DepOnly',False))is bool,'package_projection_flags')
  imports=row.get('Imports')if row.get('Imports')is not None else[];aliases=row.get('ImportMap')if row.get('ImportMap')is not None else{};matches=row.get('Match')if row.get('Match')is not None else[]
  need(type(imports)is list and all(path(p)for p in imports)and len(imports)==len(set(imports))and type(aliases)is dict and all(path(k)and path(v)and v in imports for k,v in aliases.items()),'package_projection_imports')
  need(type(matches)is list and all(type(p)is str for p in matches),'package_projection_matches')
  if not row.get('DepOnly',False):
   need(pattern in matches and(name==main or name.startswith(main+'/')),'package_projection_root');roots.append(name)
  module=row.get('Module')
  if row.get('Standard',False):
   need(module is None,'package_projection_stdlib');owner='stdlib';version='v1.27.3'
  else:
   need(type(module)is dict and module.get('Error')is None and module.get('Replace')is None,'package_projection_module')
   owner=module.get('Path');version=module.get('Version','')
   need(type(owner)is str and type(version)is str,'package_projection_module_version')
   if owner==main:need(module.get('Main')is True and version=='','package_projection_main')
   else:need(owner in versions and versions[owner]==version and module.get('Main',False)is False,'package_projection_module_version')
  need(owner not in owners or owners[owner]==version,'package_projection_module_conflict');owners[owner]=version;packages[name]={'imports':imports,'owner':owner,'name':row['Name']}
 need(bool(roots)and len(roots)==len(set(roots))and set(required_roots)<=set(roots),'package_projection_required_root')
 if pattern!='./...':need(set(roots)==set(required_roots)and all(packages[p]['name']=='main'for p in roots),'package_projection_entry')
 reachable=set();pending=list(roots)
 while pending:
  name=pending.pop();need(name in packages,'package_projection_incomplete_closure')
  if name in reachable:continue
  reachable.add(name);pending.extend(packages[name]['imports'])
 need(reachable==set(packages),'package_projection_unrelated_package');owners['stdlib']='v1.27.3';need(owners.get(main)=='','package_projection_main')
 return{'schema':'age132_fixed_package_profile/1','pattern':pattern,'tests':False,'CGO_ENABLED':'0','GOOS':'linux','GOARCH':'amd64','roots':sorted(roots),'required_roots':sorted(required_roots),'modules':owners,'package_count':len(packages),'package_paths':sorted(packages),'complete_and_only_reachable_import_closure':True,'raw_bytes':len(raw),'raw_sha256':sha256(raw).hexdigest()}
def source_arguments(directory,pattern,*,level='package',tests=False):
 need(type(directory)is str and PurePosixPath(directory).is_absolute()and'..'not in PurePosixPath(directory).parts and pattern in{'./...','./cmd/age','./cmd/age-keygen','./cmd/govulncheck'}and level=='package'and tests is False,'source_sca_scope')
 return['-C',directory,'-mode=source','-scan=package','-test=false','-json',pattern]
def sca(raw,mode,level='module'):
 rows=documents(raw);need(bool(rows)and set(rows[0])=={'config'},'sca_protocol');config=rows[0]['config'];need(type(config)is dict and mode in{'source','binary'}and level in{'module','package'}and config.get('protocol_version')=='v1.0.0'and config.get('scanner_name')=='govulncheck'and config.get('scan_level')==level and config.get('scan_mode')==mode and config.get('db')=='https://vuln.go.dev','sca_configuration')
 need((config.get('go_version')=='go1.27.3'if mode=='source'else'go_version'not in config),'sca_configuration')
 osvs={};findings=[];sboms=[]
 for row in rows[1:]:
  need(type(row)is dict and len(row)==1,'sca_message');key=next(iter(row));need(key in{'progress','SBOM','osv','finding'},'sca_message')
  if key=='osv':
   osv=row[key];need(type(osv)is dict and re.fullmatch('GO-[0-9]{4}-[0-9]+',osv.get('id',''))is not None,'sca_osv');need(osv['id']not in osvs or osvs[osv['id']]==osv,'sca_osv_conflict');osvs[osv['id']]=osv
  elif key=='finding':findings.append(row[key])
  elif key=='SBOM':sboms.append(row[key])
 need(len(sboms)==1 and type(sboms[0])is dict and sboms[0].get('go_version')=='go1.27.3'and type(sboms[0].get('modules'))is list and bool(sboms[0]['modules']),'sca_sbom')
 for f in findings:need(type(f)is dict and f.get('osv')in osvs and type(f.get('trace'))is list and bool(f['trace']),'sca_finding')
 return {'config':config,'SBOM':sboms[0],'all_OSV':osvs,'all_findings':findings,'unresolved_ids':sorted({f['osv']for f in findings}),'Root_component_review_required':bool(findings),'security_accepted':False}
def query_modules(raw,subjects):
 need(type(subjects)is dict and 0<len(subjects)<=20 and all(type(p)is str and re.fullmatch(r'[A-Za-z0-9_.!/-]+',p)is not None and type(v)is str and re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?',v)is not None for p,v in subjects.items()),'query_subjects')
 rows=documents(raw);need(bool(rows)and set(rows[0])=={'config'},'query_protocol');c=rows[0]['config'];need(type(c)is dict and c.get('protocol_version')=='v1.0.0'and c.get('scanner_name')=='govulncheck'and c.get('scan_mode')=='query'and c.get('scan_level')=='symbol'and'go_version'not in c and c.get('db')=='https://vuln.go.dev','query_configuration');osvs={};observed=set();markers={'Looking up vulnerabilities in '+p+' at '+v+'...':p for p,v in subjects.items()}
 for row in rows[1:]:
  need(type(row)is dict and len(row)==1,'query_message');key=next(iter(row));need(key in{'progress','osv'},'query_message')
  if key=='progress':
   need(type(row[key])is dict and type(row[key].get('message',''))is str,'query_message');message=row[key].get('message','')
   if message.startswith('Looking up vulnerabilities in '):need(message in markers and markers[message]not in observed,'query_subject_not_observed');observed.add(markers[message])
  else:
   value=row[key];need(type(value)is dict and re.fullmatch('GO-[0-9]{4}-[0-9]+',value.get('id',''))is not None,'query_osv');affected=value.get('affected');need(type(affected)is list and all(type(a)is dict and type(a.get('package',{}))is dict for a in affected)and any(a.get('package',{}).get('name')in subjects for a in affected),'query_osv_subject');need(value['id']not in osvs or osvs[value['id']]==value,'query_osv_conflict');osvs[value['id']]=value
 need(observed==set(subjects),'query_subject_not_observed');return {'config':c,'requested_subjects':subjects,'observed_subjects':sorted(observed),'all_OSV':osvs,'all_findings':list(osvs.values()),'unresolved_ids':sorted(osvs),'coverage_kind':'full_selected_module_advisory_metadata_not_package_SBOM','security_accepted':False}
def query_age(raw):return query_modules(raw,{'filippo.io/age':'v1.3.2'})
def sca_subject(scan,expected,main,*,roots):
 actual={}
 need(main not in expected and'stdlib'not in expected,'sca_projection_subject')
 mode=scan['config']['scan_mode'];wanted={main:''if mode=='source'else'(devel)',**expected,'stdlib':'v1.27.3'if mode=='source'else'go1.27.3'}
 for row in scan['SBOM']['modules']:
  need(type(row)is dict and type(row.get('path'))is str and type(row.get('version',''))is str,'sca_subject_version');name=row.get('path');version=row.get('version','');need(name not in actual,'sca_sbom_duplicate')
  if name==main:need(version==wanted[main],'sca_local_main_version')
  elif name=='stdlib':need(version==wanted['stdlib'],'sca_stdlib_version')
  else:need(expected.get(name)==version,'sca_subject_version')
  actual[name]=version
 need(actual==wanted,'sca_incomplete_module_subject');observed_roots=scan['SBOM'].get('roots');need(type(observed_roots)is list and all(type(p)is str for p in observed_roots)and len(observed_roots)==len(set(observed_roots))and set(observed_roots)==set(roots),'sca_roots_exact')
 for finding in scan['all_findings']:
  for item in finding['trace']:
   need(type(item)is dict and type(item.get('module'))is str and type(item.get('version',''))is str,'sca_finding_subject');path=item.get('module');version=item.get('version','');need(path in wanted and version==('v1.27.3'if path=='stdlib'else wanted[path]),'sca_finding_subject')
 return actual
def build_info(raw,main,lock,*,entry,expected_modules):
 need(type(raw)is bytes and 0<len(raw)<=65536 and raw.splitlines()[0].endswith(b': go1.27.3')and type(expected_modules)is dict and type(entry)is str,'CLI_build_info');mods={r['path']:r for r in lock};deps={};seen=False;entries=[]
 for line in raw.decode().splitlines()[1:]:
  fields=line.split()
  if not fields:continue
  need(fields[0]!='=>','build_info_replacement')
  if fields[0]=='path':need(len(fields)==2,'build_info_entry');entries.append(fields[1])
  if fields[0]=='mod':need(len(fields)==3 and fields[1]==main and fields[2]=='(devel)'and not seen,'build_info_main');seen=True
  if fields[0]=='dep':
   need(len(fields)==4 and fields[1]in mods and fields[1]not in deps,'build_info_module');r=mods[fields[1]];need((fields[2],fields[3])==(r['version'],r['module_sum']),'build_info_module_pin');deps[fields[1]]=fields[2]
 need(seen and b'CGO_ENABLED=0'in raw and b'GOARCH=amd64'in raw and b'GOOS=linux'in raw,'CLI_build_info');need(entries==[entry]and deps==expected_modules,'build_info_package_projection');return deps
def test_result(raw):
 rows=documents(raw);need(bool(rows),'empty_upstream_tests');passed=[];failed=[];skipped=[];packages=[]
 for r in rows:
  need(type(r)is dict and type(r.get('Action'))is str and type(r.get('Package'))is str,'upstream_test_json')
  action=r['Action'];name=r.get('Test')
  if action=='pass':(passed if name else packages).append((r['Package'],name))
  elif action=='fail':failed.append((r['Package'],name))
  elif action=='skip':skipped.append((r['Package'],name))
 need(bool(passed)and bool(packages)and not failed,'upstream_tests_failed_or_empty')
 return {'passed_test_events':len(passed),'passed_packages':sorted(set(p for p,_ in packages)),'skips':skipped,'failed':failed,'all_requested_packages_command_completed':True,'zero_skips':not skipped}
def package_graph(raw,root):
 need(type(raw)is bytes and len(raw)<=1024**2,'package_graph_bound');lines=raw.decode().splitlines();need(lines==list(dict.fromkeys(lines))and root in lines and all(re.fullmatch(r'[A-Za-z0-9_./!@-]+',p)is not None for p in lines),'package_graph');return lines
def static_amd64(raw):
 need(type(raw)is bytes and 64<=len(raw)<=32*1024**2 and raw[:6]==b'\x7fELF\x02\x01','static_elf')
 header=struct.unpack('<16sHHIQQQIHHHHHH',raw[:64]);need(header[2]==62 and header[1]in{2,3},'static_elf');offset,entry,count=header[5],header[9],header[10];need(entry==56 and 0<count<=128 and offset+entry*count<=len(raw),'static_elf')
 for i in range(count):
  fields=struct.unpack('<IIQQQQQQ',raw[offset+entry*i:offset+entry*(i+1)]);need(fields[0]!=3,'dynamic_interpreter_forbidden')
  if fields[0]==2:
   off,size=fields[2],fields[5];need(size%16==0 and off+size<=len(raw),'dynamic_section')
   for j in range(off,off+size,16):need(struct.unpack('<QQ',raw[j:j+16])[0]!=1,'dynamic_dependency_forbidden')
 return {'ELF64_little_endian_amd64':True,'PT_INTERP':False,'DT_NEEDED':False}
def deb_control(raw):
 need(len(raw)<=65536,'deb_control_bound');rows={}
 with tarfile.open(fileobj=io.BytesIO(raw),mode='r:')as archive:
  for member in archive:
   name=member.name.removeprefix('./');name=''if name=='.'else name;need(name in{'','control'}and(member.isdir()if name==''else member.isfile()),'deb_hook_or_extra_control')
   if name=='control':
    need(member.size<=8192 and name not in rows,'deb_control');rows[name]=archive.extractfile(member).read(8193)
 need(set(rows)=={'control'},'deb_control');text=rows['control'].decode();fields={}
 for line in text.splitlines():
  key,sep,value=line.partition(':');need(bool(sep)and key not in fields,'deb_control');fields[key]=value.strip()
 need(set(fields)=={'Package','Version','Architecture','Maintainer','Section','Priority','Description'}and(fields['Package'],fields['Version'],fields['Architecture'])==('age','1.3.2+gold1','amd64'),'deb_control');return fields
PACKAGE_FILES={'usr/bin/age':0o755,'usr/bin/age-keygen':0o755,'usr/share/doc/age/copyright':0o644,'usr/share/doc/age/README.Debian':0o644,'usr/share/doc/age/build-provenance.json':0o644}
def deb_data(raw,expected):
 need(len(raw)<=64*1024**2 and set(expected)==set(PACKAGE_FILES),'deb_data_bound');found={}
 dirs={'','usr','usr/bin','usr/share','usr/share/doc','usr/share/doc/age'}
 with tarfile.open(fileobj=io.BytesIO(raw),mode='r:')as archive:
  for m in archive:
   name=m.name.removeprefix('./').rstrip('/');name=''if name=='.'else name;need(m.uid==m.gid==0 and not m.issym()and not m.islnk(),'deb_owner_type')
   if m.isdir():need(name in dirs and m.mode==0o755,'deb_directory');continue
   need(m.isfile()and name in PACKAGE_FILES and name not in found and m.mode==PACKAGE_FILES[name]and m.size<=32*1024**2,'deb_extra_payload');b=archive.extractfile(m).read(m.size+1);need((len(b),sha256(b).hexdigest())==tuple(expected[name]),'deb_payload_binding');found[name]=True
 need(set(found)==set(expected),'deb_payload_inventory');return sorted(found)
def native_format(raw):
 need(type(raw)is bytes and 100<len(raw)<=65536 and raw.startswith(b'age-encryption.org/v1\n-> X25519 '),'native_format')
 need(b'\n--- 'in raw,'native_format')
 return {'age_v1_X25519_header':True,'CLI_encrypted_benign_payload':True,'CLI_decrypt_roundtrip_proven':False,'operational_key_generated_or_read':False}
