"""Pure canonical SQLite package selection; no network, process or Host action.

The lock is armed only after Root reviews genuine CI artifacts. A package-only
qualification never grants App/Market release or global security acceptance.
"""
from hashlib import sha256
from pathlib import PurePosixPath
from zipfile import ZipFile, ZIP_STORED, ZIP_DEFLATED, BadZipFile
import io,json,re,stat

SOURCE={'url':'https://www.sqlite.org/2026/sqlite-src-3540100.zip','bytes':14899528,
 'sha3_256':'877c6f4996ce7adb368686bce21638de9e3d91534832ad3d9d119037d1bc22ad',
 'source_id':'555e07ef33f69a0957d24d8e555b702464a131d6f1607f4c3c1e4551e8e6ca76'}
BASELINES={'trixie':'3.46.1-7+deb13u2','noble':'3.45.1-1ubuntu2.8','resolute':'3.46.1-9ubuntu0.3'}
LIBRARY='/usr/lib/x86_64-linux-gnu/libsqlite3.so.0.8.6'
INITIALIZER={'bytes': 571, 'sha256': '355a7d4a50329335b4260492214ba8750d8fe4c166d8b4f90fe954cf88914ec9'}
MAX_ARCHIVE=64*1024**2; MAX_EXPANDED=128*1024**2; MAX_MEMBER=32*1024**2; MAX_PROVENANCE=4*1024**2

NUMERIC_CONTRACT='legacy_cast15_exact_quote_json_numeric_roundtrip_exact'

def need(value,code='lock_contract'):
 if not value:raise RuntimeError(code)
def encoded(value):return(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
def document(raw):
 need(type(raw)is bytes and 0<len(raw)<=MAX_PROVENANCE,'source_contract')
 def unique(rows):
  value={}
  for key,item in rows:need(key not in value,'source_contract');value[key]=item
  return value
 try:return json.loads(raw,object_pairs_hook=unique,parse_constant=lambda _:need(False,'source_contract'))
 except (ValueError,UnicodeError)as error:raise RuntimeError('source_contract')from error
def pin(value,maximum):
 need(type(value)is dict and set(value)=={'bytes','sha256'}and type(value['bytes'])is int and 0<value['bytes']<=maximum
  and type(value['sha256'])is str and re.fullmatch('[0-9a-f]{64}',value['sha256']))
 return value
def content_pin(raw):
 need(type(raw)is bytes and len(raw)<=MAX_ARCHIVE,'archive_bound')
 return{'bytes':len(raw),'sha256':sha256(raw).hexdigest()}
def version(variant):
 need(type(variant)is str and variant in BASELINES)
 return '3.54.1-0+trading1~'+variant+'1'
def variant(profile):
 need(type(profile)is dict)
 matches=[name for name in BASELINES if profile.get('version')==version(name)]
 need(len(matches)==1);return matches[0]
def validate_profile(profile,selected=None):
 chosen=variant(profile);need(selected is None or chosen==selected)
 need(set(profile)=={'version','package','installed_library','artifact','provenance','root_qualification'})
 package=profile['package'];need(type(package)is dict and set(package)=={'path','bytes','sha256'}
  and package['path']=='libsqlite3-0_'+version(chosen)+'_amd64.deb')
 pin({k:package[k]for k in('bytes','sha256')},MAX_MEMBER)
 library=profile['installed_library'];need(type(library)is dict and set(library)=={'path','bytes','sha256'}and library['path']==LIBRARY)
 pin({k:library[k]for k in('bytes','sha256')},16*1024**2)
 artifact=profile['artifact'];need(type(artifact)is dict and set(artifact)=={'id','run_id','run_attempt','job_id','head_sha','workflow_id','name','bytes','sha256','members'})
 need(all(type(artifact[k])is int and artifact[k]>0 for k in('id','run_id','run_attempt','job_id','workflow_id','members'))
  and 2<=artifact['members']<=512 and type(artifact['head_sha'])is str and re.fullmatch('[0-9a-f]{40}',artifact['head_sha'])
  and artifact['name']=='sqlite3541-library-'+chosen+'-'+str(artifact['run_id'])+'-'+str(artifact['run_attempt']))
 pin({k:artifact[k]for k in('bytes','sha256')},MAX_ARCHIVE)
 pin(profile['provenance'],MAX_PROVENANCE);pin(profile['root_qualification'],MAX_PROVENANCE)
 return profile

def validate_lock(value,variant='trixie'):
 need(type(value)is dict and value.get('schema')=='sqlite3541_qualified_packages/1')
 need(value.get('armed')is True,'lock_unarmed')
 need(set(value)=={'schema','armed','source','profiles'}and value['source']==SOURCE
  and type(value['profiles'])is dict and 0<len(value['profiles'])<=3 and set(value['profiles'])<=set(BASELINES))
 for name,profile in value['profiles'].items():validate_profile(profile,name)
 need(type(variant)is str and variant in value['profiles'])
 return value['profiles'][variant]

def cache_manifest(profile):
 validate_profile(profile)
 return encoded({'schema':'sqlite3541_image_package_cache/1','variant':variant(profile),'version':profile['version'],
  'package':profile['package'],'installed_library':profile['installed_library'],'source_id':SOURCE['source_id'],'provenance':profile['provenance']})

def check_provenance(raw,profile):
 validate_profile(profile);need(content_pin(raw)==profile['provenance'],'archive_pin');value=document(raw)
 need(type(value)is dict and value.get('schema')=='sqlite3541_library_candidate/1'and value.get('status')=='BUILT_NOT_ACCEPTED'
  and value.get('variant')==variant(profile)and value.get('source')==SOURCE,'source_contract')
 package=profile['package'];need(value.get('package')=={'name':package['path'],'version':profile['version'],**{k:package[k]for k in('bytes','sha256')}}
  and value.get('candidate_library')=={k:profile['installed_library'][k]for k in('bytes','sha256')},'source_contract')
 need(all(value.get(k)is True for k in('production15_targeted_compatibility','old_new_database_reopen_passed','thread_initialize_shutdown_passed','old_required_public_exports_retained'))
  and all(value.get(k)is False for k in('CLI_in_runtime_package','upstream_core_source_modified','production_data_accessed','business81_or_App32_run','host_installed','security_accepted')),'source_contract')
 need(value.get('numeric_contract')==NUMERIC_CONTRACT,'source_contract')
 need(value.get('legacy_numeric_initializer')==INITIALIZER and type(value.get('production_cflags'))is str
  and '-DSQLITE_EXTRA_AUTOEXT=sqlite3_trading_legacy_fp_init'in value['production_cflags'].split(),'source_contract')
 baseline=value.get('baseline_package');need(type(baseline)is dict and baseline.get('version')==BASELINES[variant(profile)],'source_contract')
 pin({k:baseline.get(k)for k in('bytes','sha256')},MAX_MEMBER)
 vendor=value.get('vendor_default17_tests');need(type(vendor)is dict and vendor.get('argv')==['make','-j2','test']
  and type(vendor.get('actual_returncode'))is int and vendor['actual_returncode']==0
  and vendor.get('same_library_as_production15')is False and vendor.get('vendor_assertions_modified')is False,'source_contract')
 pin(vendor.get('raw_log'),MAX_MEMBER)
 for key in('baseline_abi','candidate_abi'):
  abi=value.get(key);need(type(abi)is dict and abi.get('soname')=='libsqlite3.so.0','source_contract')
 payload=value.get('payload_regular_files');need(type(payload)is list and 1<=len(payload)<=32,'source_contract')
 targets=[item for item in payload if type(item)is dict and item.get('path')==LIBRARY.lstrip('/')]
 need(len(targets)==1 and targets[0]=={'path':LIBRARY.lstrip('/'),**value['candidate_library']},'source_contract')
 return value

def admit_archive(raw,profile):
 """Validate the whole original ZIP before admitting its original DEB/proof."""
 validate_profile(profile);need(type(raw)is bytes and content_pin(raw)=={k:profile['artifact'][k]for k in('bytes','sha256')},'archive_pin')
 selected={};seen=set();expanded=0
 try:
  with ZipFile(io.BytesIO(raw))as archive:
   rows=archive.infolist();need(len(rows)==profile['artifact']['members'],'archive_contract')
   for row in rows:
    path=PurePosixPath(row.filename);kind=stat.S_IFMT(row.external_attr>>16)
    need(type(row.filename)is str and 0<len(row.filename)<=4096 and not path.is_absolute()
     and '..'not in path.parts and '.'not in path.parts and row.filename==str(path)+('/'if row.is_dir()else'')
     and not any(c in row.filename for c in('\\','\0','\n','\r'))and row.filename not in seen
     and not row.flag_bits&1 and row.compress_type in(ZIP_STORED,ZIP_DEFLATED)
     and kind in(0,stat.S_IFREG,stat.S_IFDIR),'archive_member');seen.add(row.filename)
    need((not row.is_dir()and kind!=stat.S_IFDIR)or(row.is_dir()and kind in(0,stat.S_IFDIR)and row.file_size==0),'archive_member')
    need(0<=row.file_size<=MAX_MEMBER and 0<=row.compress_size<=MAX_ARCHIVE,'archive_bound')
    expanded+=row.file_size;need(expanded<=MAX_EXPANDED,'archive_bound')
    if row.is_dir():continue
    want=row.filename in(profile['package']['path'],'provenance.json');parts=[];count=0
    with archive.open(row)as incoming:
     while True:
      block=incoming.read(min(65536,row.file_size-count+1))
      if not block:break
      count+=len(block);need(count<=row.file_size,'archive_bound')
      if want:parts.append(block)
    need(count==row.file_size,'archive_bound')
    if want:selected[row.filename]=b''.join(parts)
 except (ValueError,OSError,EOFError,BadZipFile)as error:raise RuntimeError('archive_contract')from error
 need(set(selected)=={profile['package']['path'],'provenance.json'},'archive_contract')
 package=selected[profile['package']['path']];proof=selected['provenance.json']
 need(content_pin(package)=={k:profile['package'][k]for k in('bytes','sha256')},'archive_pin');check_provenance(proof,profile)
 return{'package':package,'provenance':proof}
