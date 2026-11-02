"""Finite native SQLite facts and exact old-library consumers; no service actions."""
from hashlib import sha256
from pathlib import Path
from types import ModuleType
import argparse,json,os,re,sqlite3,subprocess,time

SOURCE_ID='555e07ef33f69a0957d24d8e555b702464a131d6f1607f4c3c1e4551e8e6ca76'
CAST_DIGEST='7f9294a1e9de0388a8d1c5330b6213b3025d87d0d577d137e5810a3cbcfabde1'
def need(value,code):
 if not value:raise RuntimeError(code)
def read(path,maximum):
 with Path(path).open('rb')as incoming:raw=incoming.read(maximum+1)
 need(len(raw)<=maximum,'metadata_bound');return raw
def pid_state(path):
 raw=read(path/'stat',16384).decode();tail=raw[raw.rindex(')')+2:].split()
 need(len(tail)>=20,'process_stat');return int(tail[19])
def unit(path):
 rows=read(path/'cgroup',32768).decode().splitlines()
 names={part for row in rows for part in row.split(':',2)[-1].split('/')if part.endswith('.service')}
 need(len(names)<=1,'process_unit');return next(iter(names),None)
def object_frame(path):
 info=path.stat();return {'device':info.st_dev,'inode':info.st_ino,'path':str(path)}
def old_mappings(frame,proc=Path('/proc'),*,different=False,admitted=None):
 """Require the library name AND object identity; unrelated inode reuse is ignored."""
 paths={frame['path']}
 if frame['path'].startswith('/usr/lib/'):paths.add(frame['path'][4:])
 paths={name+suffix for name in paths for suffix in ('','.dpkg-tmp','.dpkg-old','.dpkg-new')}
 rows=[];started=time.monotonic();total=0
 pids=[p for p in proc.iterdir()if p.name.isdecimal()];need(len(pids)<=8192,'process_bound')
 for path in pids:
  if int(path.name)==os.getpid():continue
  need(time.monotonic()-started<30,'process_scan_deadline')
  try:
   before=pid_state(path);raw=read(path/'maps',4*1024**2);total+=len(raw);need(total<=128*1024**2,'process_scan_bound')
   found=False
   for line in raw.decode().splitlines():
    parts=line.split(maxsplit=5)
    if len(parts)!=6:continue
    name=parts[5].removesuffix(' (deleted)')
    dev=tuple(int(x,16)for x in parts[3].split(':'))
    same=int(parts[4])==frame['inode']and dev==(os.major(frame['device']),os.minor(frame['device']))
    if name in paths and same!=different:found=True
   if found:
    current_unit=unit(path)
    if (admitted is None or current_unit in admitted)and pid_state(path)==before:rows.append({'pid':int(path.name),'start_ticks':before,'unit':current_unit})
  except (FileNotFoundError,ProcessLookupError):continue
 return sorted(rows,key=lambda row:row['pid'])
def consumers_admitted(rows,admitted):
 need(all(row['unit']in admitted for row in rows),'unadmitted_old_library_consumer')
def original_consumers_gone(rows,proc=Path('/proc')):
 for row in rows:
  try:current=pid_state(proc/str(row['pid']))
  except (FileNotFoundError,ProcessLookupError):continue
  need(current!=row['start_ticks'],'original_old_library_consumer_alive')
def verify_consumers(baseline,frame,admitted,proc=Path('/proc')):
 original_consumers_gone(baseline['consumers'],proc)
 reused=frame==baseline['object']
 # An old mapped unlinked inode cannot be recycled while it remains mapped.
 # Current payload facts are already verified before entering this function.
 if not reused:need(not old_mappings(baseline['object'],proc),'old_library_mappings_remain')
 need(not old_mappings(frame,proc,different=True,admitted=admitted),'admitted_unit_stale_sqlite_mapping')
 return {'previous_object_identity_reused_for_verified_current_payload':reused,'old_object_mappings_checked':not reused,'old_object_mapping_count':None if reused else 0,'admitted_unit_stale_mapping_count':0,'original_consumers_gone_or_new_process_identity':True}
def load_recipe(path,digest):
 raw=read(path,65536);need(sha256(raw).hexdigest()==digest,'recipe_pin')
 m=ModuleType('_qualified_sqlite_verifier');m.__file__=str(path)
 exec(compile(raw,str(path),'exec'),m.__dict__);return m
def fresh_facts(recipe,package,library,expected_hash):
 actual=subprocess.check_output(['/usr/bin/dpkg-query','--show','--showformat=${Version}','libsqlite3-0:amd64'],stderr=subprocess.DEVNULL,timeout=15).decode().strip()
 need(actual==package,'installed_package_version');recipe.LIBRARY=library
 with sqlite3.connect(':memory:')as connection:
  connection.execute('CREATE TABLE quotes(event_id INTEGER PRIMARY KEY,source TEXT,commodity TEXT,price INTEGER,seen_at INTEGER)')
  connection.executemany('INSERT INTO quotes VALUES(?,?,?,?,?)',[(1,'g1','emami',61500000,1),(2,'g1','bahar',59000000,2),(3,'g2','emami',62000000,3)])
  recipe.ordinary(connection);facts=recipe.facts(connection,actual)
 need(facts['library']['sha256']==expected_hash,'installed_library_payload')
 need(facts['numeric_samples']==2048 and facts['numeric_digest']==CAST_DIGEST,'legacy_CAST_15_digest')
 return facts
def verify_facts(recipe,baseline,facts):
 need(re.fullmatch(r'3[.]54[.]1-0[+]trading1~(?:noble|resolute)1',facts['package'])is not None,'canonical_package_version')
 need(facts['sqlite_runtime']=='3.54.1'and facts['sqlite_source_id'].endswith(SOURCE_ID),'canonical_installed_identity')
 need(facts['stdlib_extension']==baseline['facts']['stdlib_extension'],'native_stdlib_changed')
 need(facts['limits']==baseline['facts']['limits'],'connection_limits_changed')
 recipe.require_numeric_compatibility(baseline['facts'],facts)
def verify_rollback_facts(baseline,facts):
 before=baseline['facts']
 keys=('package','library','sqlite_runtime','sqlite_source_id','stdlib_extension','limits','compile_options','numeric_contract','numeric_digest','numeric_format_digests','numeric_roundtrip_failures')
 need(all(facts[k]==before[k]for k in keys),'original_baseline_not_restored')
def write(path,value):
 raw=(json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n').encode();need(len(raw)<=1024**2,'receipt_bound')
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'wb')as output:output.write(raw);output.flush();os.fsync(output.fileno())
def main(argv=None):
 p=argparse.ArgumentParser();p.add_argument('mode',choices=('baseline','verify'))
 for name in('recipe-verify','library','baseline','output'):p.add_argument('--'+name,type=Path,required=name!='output')
 for name in('recipe-verify-sha256','package-version','library-sha256'):p.add_argument('--'+name,required=True)
 p.add_argument('--admitted-unit',action='append',default=[])
 p.add_argument('--rollback',action='store_true');a=p.parse_args(argv)
 need(os.geteuid()==0,'Root_only');need(all(re.fullmatch('[0-9a-f]{64}',v)for v in(a.recipe_verify_sha256,a.library_sha256)),'expected_hash')
 need(a.library.is_absolute()and a.library.resolve(strict=True)==a.library,'library_path')
 admitted=set(a.admitted_unit);need(admitted and all(re.fullmatch('[A-Za-z0-9_.@:-]+[.]service',v)for v in admitted),'admitted_units')
 recipe=load_recipe(a.recipe_verify,a.recipe_verify_sha256);facts=fresh_facts(recipe,a.package_version,a.library,a.library_sha256)
 frame=object_frame(a.library);need(recipe.identity(a.library)==facts['library'],'library_changed')
 boot=read('/proc/sys/kernel/random/boot_id',128).decode().strip()
 if a.mode=='baseline':
  need(not a.rollback,'rollback_requires_verify')
  rows=old_mappings(frame);consumers_admitted(rows,admitted)
  result={'schema':'sqlite_host_runtime_baseline/1','status':'BASELINE_CAPTURED','facts':facts,'object':frame,'consumers':rows,'admitted_units':sorted(admitted),'boot_id':boot,'recipe_sha256':a.recipe_verify_sha256,'security_accepted':False}
  write(a.baseline,result)
  if a.output is not None and a.output!=a.baseline:write(a.output,result)
 else:
  need(a.output is not None and a.output!=a.baseline,'fresh_verification_receipt')
  raw=read(a.baseline,1024**2);baseline=json.loads(raw)
  need(baseline.get('schema')=='sqlite_host_runtime_baseline/1'and baseline['status']=='BASELINE_CAPTURED'and baseline['boot_id']==boot and baseline['recipe_sha256']==a.recipe_verify_sha256 and set(baseline['admitted_units'])==admitted,'baseline_identity')
  consumers_admitted(baseline['consumers'],admitted)
  if a.rollback:verify_rollback_facts(baseline,facts)
  else:verify_facts(recipe,baseline,facts)
  consumer_result=verify_consumers(baseline,frame,admitted)
  result={'schema':'sqlite_host_runtime_verification/1','status':'PASS_ROLLBACK_RUNTIME_ONLY'if a.rollback else'PASS_RUNTIME_ONLY','facts':facts,'object':frame,'baseline':{'bytes':len(raw),'sha256':sha256(raw).hexdigest()},**consumer_result,'production_data_accessed':False,'security_accepted':False}
  write(a.output,result)
 print(json.dumps({'status':result['status'],'security_accepted':False}))
if __name__=='__main__':main()
