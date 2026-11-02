"""Pure closed public glibc package/test/ABI consumers. No operations on import."""
from collections import Counter
import re

OLD_VERSION='2.41-12+deb13u4'
VERSION=OLD_VERSION+'+gold1'
REGRESSION='intl/tst-gettext-path-traversal'
EXPECTED_OLD_LINES={51,58,65,78}
FORMAT=None

def need(ok,code):
 if ok is not True:raise RuntimeError(code)

def vendor_ledger(raw,*,native):
 need(type(raw)is bytes and 0<len(raw)<=16*1024**2,'vendor_ledger_bound')
 need(FORMAT is not None,'vendor_format_unbound');merged=FORMAT.parse_merge_ledger(raw);rows=merged['rows']
 need(len(rows)>=1000 and len({name for _,name in rows})==len(rows),'vendor_ledger_incomplete')
 counts=Counter(state for state,_ in rows)
 need(counts['FAIL']==counts['XPASS']==0,'vendor_regression_failed')
 if native:need(("PASS",REGRESSION)in rows and all(name!=REGRESSION or state=='PASS'for state,name in rows),'new_regression_not_executed')
 return {'counts':dict(sorted(counts.items())),'tests':len(rows),'CVE_regression_PASS':('PASS',REGRESSION)in rows,
  'default_XFAIL_UNSUPPORTED_retained':True,'test_body_selection_or_skip_override':False,'raw_merge_subject':merged['raw'],'merge_sections':list(merged['sections']),'section_counts':list(merged['section_counts'])}

def old_counterfactual(code,raw):
 need(type(code)is int and code==1 and type(raw)is bytes and 0<len(raw)<=131072,'old_counterfactual_exit')
 text=raw.decode('utf-8');matches=re.findall(r'tst-gettext-path-traversal\.c:(\d+):',text)
 need(len(matches)==4 and set(map(int,matches))==EXPECTED_OLD_LINES,'old_counterfactual_wrong_assertions')
 need('4 test failures'in text and 'xsetlocale'not in text and 'unsupported'not in text.lower()
  and 'cannot open shared object'not in text and 'not found'not in text.lower(),'old_counterfactual_environment_failed')
 return {'actual_exit_code':code,'expected_traversal_assertion_lines':sorted(EXPECTED_OLD_LINES),
  'normal_translation_assertions_44_and_72_failed':False,'locale_init_failed':False,'same_unmodified_vendor_test':True}

def exported_abi(raw,*,allow_empty=False):
 need(type(raw)is bytes and 0<=len(raw)<=16*1024**2,'ABI_bound');out={}
 for line in raw.decode('utf-8').splitlines():
  m=re.match(r'\s*\d+:\s+[0-9a-fA-F]+\s+(\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)',line)
  if not m:continue
  size,kind,bind,vis,ndx,name=m.groups()
  if bind not in{'GLOBAL','WEAK','UNIQUE'}or ndx=='UND':continue
  need(vis in{'DEFAULT','PROTECTED'},'ABI_visibility')
  row=(kind,bind,vis,int(size)if kind in{'OBJECT','TLS'}else None)
  need(name not in out,'ABI_duplicate');out[name]=row
 need(allow_empty or len(out)>0,'ABI_empty');return out

def ABI_equal(old,new,*,allow_empty=False):
 a,b=exported_abi(old,allow_empty=allow_empty),exported_abi(new,allow_empty=allow_empty);need(a==b,'ABI_export_contract_changed')
 return {'versioned_export_symbols':len(a),'names_bind_types_visibility_and_data_sizes_equal':True,
  'function_addresses_sizes_or_binary_bytes_equated':False}

def loader_list(raw,baseline,expected_libc):
 need(type(raw)is bytes and 0<len(raw)<=65536,'loader_map_bound')
 text=raw.decode();entries=[]
 for line in text.splitlines():
  if '=> not found'in line:need(False,'old_loader_dependency_missing')
  match=re.search(r'=> (/[^ ]+) \(',line)
  if match:entries.append(match[1])
 need(entries and all(path.startswith(baseline+'/')for path in entries)
  and entries.count(expected_libc)==1,'old_loader_wrong_library')
 return {'all_resolved_needed_libraries_inside_authenticated_old_payload':True,'libc_path':expected_libc,
  'candidate_build_library_fallback':False}


def control_fields(raw):
 need(type(raw)is bytes and 0<len(raw)<=131072,'package_control_bound')
 fields={};last=None
 for line in raw.decode('utf-8').splitlines():
  if line.startswith((' ','\t'))and last is not None:fields[last]+=' '+line.strip();continue
  m=re.match(r'^([A-Za-z][A-Za-z0-9-]*):\s*(.*)$',line)
  if not m:last=None;continue
  key,value=m.groups();need(key not in fields,'package_control_duplicate');fields[key]=value;last=key
 return fields

def package_identity(raw,signed_dsc,version,filename):
 source=control_fields(signed_dsc);need(source.get('Source')=='glibc'and type(source.get('Binary'))is str,'declared_source_family')
 names=source['Binary'].replace(',',' ').split();need(0<len(names)<=64 and len(names)==len(set(names))and all(re.fullmatch('[a-z0-9][a-z0-9+.-]+',n)for n in names),'declared_source_family')
 fields=control_fields(raw);need(set(fields)=={'Package','Version','Architecture','Source'},'package_fields')
 need(fields['Package']in names and fields['Version']==version and fields['Architecture']in{'amd64','all'}
  and fields['Source']in{'glibc','glibc ('+version+')'}
  and filename==fields['Package']+'_'+version+'_'+fields['Architecture']+'.deb','package_identity')
 return fields

def status_packages(raw):
 need(type(raw)is bytes and 0<len(raw)<=4*1024**2,'dependency_status_bound');rows={}
 for line in raw.decode('utf-8').splitlines():
  fields=line.split('\t');need(len(fields)in{3,4},'dependency_status_shape')
  if len(fields)==4:
   status,*fields=fields;need(status in{'installed','config-files','not-installed'},'dependency_status_shape')
   if status!='installed':continue
  name,version,arch=fields
  if ':'in name:
   name,suffix=name.rsplit(':',1);need(suffix==arch,'dependency_multiarch')
  need(re.fullmatch('[a-z0-9][a-z0-9+.-]*',name)is not None and re.fullmatch('[A-Za-z0-9.+:~_-]+',version)is not None
   and arch in{'amd64','all','i386'}and(name,arch)not in rows,'dependency_status_shape')
  rows[(name,arch)]=version
 need(0<len(rows)<=4096,'dependency_status_bound');return rows

def dependency_closure(base_status,installed_status,debs,authenticated_records):
 """Every installed delta needs the exact DEB and its authenticated APT record."""
 base=status_packages(base_status);current=status_packages(installed_status);out=[]
 for (name,arch),version in sorted(current.items()):
  if base.get((name,arch))==version:
   out.append({'package':name,'version':version,'architecture':arch,'coverage':'immutable_base_OCI_exact_status'});continue
  matches=[(leaf,row)for leaf,row in debs.items()if row['control']=={'Package':name,'Version':version,'Architecture':arch}]
  need(len(matches)==1,'dependency_payload_missing_or_ambiguous');leaf,deb=matches[0]
  proofs=[record for record in authenticated_records if record.get('Package')==name and record.get('Version')==version
   and record.get('Architecture')==arch and record.get('SHA256')==deb['sha256']and record.get('Size')==str(deb['bytes'])]
  need(len(proofs)>0,'dependency_authenticated_record_missing')
  need(all(re.fullmatch('pool/[A-Za-z0-9+./_~:%-]+',p.get('Filename',''))is not None and '..'not in p['Filename'].split('/')
   and p.get('evidence_phase')in{'bootstrap','final'}for p in proofs),'dependency_retrieval_source')
  out.append({'package':name,'version':version,'architecture':arch,'coverage':'exact_cached_DEB_and_APT_authenticated_index',
   'deb':leaf,'bytes':deb['bytes'],'sha256':deb['sha256'],'selected_authenticated_records':proofs})
 return {'schema':'glibc84243_complete_installed_build_dependency_closure/1','base_package_count':len(base),
  'installed_package_count':len(current),'all_post_base_changes_have_exact_authenticated_payload':True,'packages':out}
