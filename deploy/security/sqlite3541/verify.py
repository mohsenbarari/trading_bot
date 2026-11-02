"""Disposable synthetic DB compatibility; never run against project databases."""
from hashlib import sha256
from pathlib import Path
import argparse,json,math,os,random,stat,struct,sys

LIBRARY=Path('/usr/lib/x86_64-linux-gnu/libsqlite3.so.0.8.6')
SOURCE_ID='555e07ef33f69a0957d24d8e555b702464a131d6f1607f4c3c1e4551e8e6ca76'
QUERY='SELECT source,event_id,commodity,price FROM quotes WHERE commodity=? ORDER BY seen_at DESC,event_id DESC,source'
EXPECTED=[['g2',3,'emami',62000000],['g1',1,'emami',61500000]]
def need(value,code):
 if not value:raise RuntimeError(code)
def encoded(value):return(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
def put(path,value):
 raw=encoded(value);need(len(raw)<=1024**2,'public_result_bound')
 with path.open('xb')as out:out.write(raw)
def identity(path):
 path=path.resolve(strict=True);before=path.stat();need(stat.S_ISREG(before.st_mode)and 0<before.st_size<=32*1024**2,'system_binary')
 digest=sha256()
 with path.open('rb')as incoming:
  for raw in iter(lambda:incoming.read(65536),b''):digest.update(raw)
 after=path.stat();need((before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)==(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns),'system_binary_changed')
 return{'bytes':before.st_size,'sha256':digest.hexdigest()}
def mapped(path,maps):
 path=path.resolve(strict=True);info=path.stat();rows=[line.split(maxsplit=5)for line in maps.splitlines()]
 selected=[r for r in rows if len(r)==6 and r[5]==str(path)]
 need(selected and all(int(r[4])==info.st_ino and tuple(int(v,16)for v in r[3].split(':'))==(os.major(info.st_dev),os.minor(info.st_dev))for r in selected),'actual_system_mapping')
 return identity(path)
def samples():
 values=[0.0,-0.0,0.1,0.2,0.3,1.2345678901234567,61500000.1,62000000.125,1e-300,1e300,2.2250738585072014e-308,9007199254740991.0]
 rng=random.Random(3540100)
 while len(values)<2048:
  value=struct.unpack('!d',rng.getrandbits(64).to_bytes(8,'big'))[0]
  if math.isfinite(value):values.append(value)
 return values
NUMERIC_CONTRACT='legacy_cast15_exact_quote_json_numeric_roundtrip_exact'
def numeric_observation(connection):
 names=('CAST','quote','json_array','json_object');outputs={name:[]for name in names}
 failures={name:0 for name in names[1:]}
 for value in samples():
  row=connection.execute("SELECT CAST(? AS TEXT),quote(?),json_array(?),json_object('p',?),CAST(quote(?) AS REAL),json_extract(json_array(?),'$[0]'),json_extract(json_object('p',?),'$.p')",(value,)*7).fetchone()
  for i,name in enumerate(names):outputs[name].append(row[i])
  for i,name in enumerate(names[1:]):failures[name]+=row[i+4]!=value
 digests={name:sha256(encoded(rows)).hexdigest()for name,rows in outputs.items()}
 return {'numeric_contract':NUMERIC_CONTRACT,'numeric_digest':digests['CAST'],
  'numeric_format_digests':digests,'numeric_roundtrip_failures':failures}
def numeric(connection):return numeric_observation(connection)['numeric_digest']
def require_numeric_compatibility(baseline,result):
 need(baseline.get('numeric_contract')==result.get('numeric_contract')==NUMERIC_CONTRACT,'numeric_contract')
 need(result['numeric_digest']==baseline['numeric_digest'],'legacy_CAST_numeric_parity')
 need(result.get('numeric_roundtrip_failures')=={'quote':0,'json_array':0,'json_object':0},'quote_JSON_numeric_roundtrip')
def facts(connection,package):
 import _sqlite3,sqlite3
 need(sys.flags.isolated and sys.flags.dont_write_bytecode and not any(k in os.environ for k in('LD_PRELOAD','LD_LIBRARY_PATH','PYTHONPATH','PYTHONHOME')),'unmodified_system_python')
 need(sqlite3.sqlite_version==package.split('-',1)[0],'sqlite_package_runtime')
 with open('/proc/self/maps','r')as incoming:maps=incoming.read(2*1024**2+1)
 need(len(maps)<=2*1024**2,'mapping_bound')
 options=sorted(r[0]for r in connection.execute('PRAGMA compile_options'))
 return{'sqlite_runtime':sqlite3.sqlite_version,'sqlite_source_id':connection.execute('SELECT sqlite_source_id()').fetchone()[0],
  'package':package,'compile_options':options,'limits':[connection.getlimit(i)for i in range(12)],
  'numeric_samples':len(samples()),**numeric_observation(connection),'library':mapped(LIBRARY,maps),
  'stdlib_extension':mapped(Path(_sqlite3.__file__),maps),'production_data_accessed':False,'security_accepted':False}
def ordinary(connection):
 import sqlite3
 need(json.loads(connection.execute("SELECT json_object('a',1,'b',json_array(2,3))").fetchone()[0])=={'a':1,'b':[2,3]},'JSON_contract')
 for kind in('fts3','fts4','fts5'):
  connection.execute('CREATE VIRTUAL TABLE temp.search_'+kind+' USING '+kind+'(text)')
  connection.execute('INSERT INTO temp.search_'+kind+' VALUES(?)',('emami coin',))
  need(connection.execute('SELECT count(*) FROM temp.search_'+kind+' WHERE text MATCH ?',( 'emami',)).fetchone()[0]==1,'FTS_contract')
 connection.execute('CREATE VIRTUAL TABLE temp.bounds USING rtree(id,x0,x1)');connection.execute('INSERT INTO temp.bounds VALUES(1,2,3)')
 need(connection.execute('SELECT id FROM temp.bounds WHERE x0<=2 AND x1>=3').fetchall()==[(1,)],'RTREE_contract')
 connection.execute('UPDATE quotes SET price=price WHERE event_id>0 ORDER BY event_id LIMIT 1')
 connection.execute('SAVEPOINT ordinary');connection.execute('INSERT INTO quotes VALUES(99,\'g1\',\'emami\',1,99)');connection.execute('ROLLBACK TO ordinary');connection.execute('RELEASE ordinary')
 need(connection.execute('SELECT count(*) FROM quotes WHERE event_id=99').fetchone()[0]==0,'savepoint_rollback')
 connection.set_authorizer(lambda code,a,b,db,source:sqlite3.SQLITE_DENY if code==sqlite3.SQLITE_READ and a=='quotes'and b=='price'else sqlite3.SQLITE_OK)
 try:
  try:connection.execute('SELECT price FROM quotes').fetchall()
  except sqlite3.DatabaseError:pass
  else:raise RuntimeError('authorizer_not_enforced')
 finally:connection.set_authorizer(None)
 connection.commit()
def query(connection):return[list(r)for r in connection.execute(QUERY,('emami',))]
def integrity(connection):need(connection.execute('PRAGMA integrity_check').fetchall()==[('ok',)],'database_integrity')

def main():
 import sqlite3
 parser=argparse.ArgumentParser();parser.add_argument('phase',choices=('baseline','candidate','fresh','old-reopen','candidate-reopen'));parser.add_argument('root',type=Path);parser.add_argument('package');args=parser.parse_args()
 root=args.root.resolve(strict=True);need(root==Path('/build/compat')and root.stat().st_uid==os.geteuid()==10001,'owned_disposable_database_root')
 path=root/'old-format.db';phase=args.phase;con=sqlite3.connect(path);con.execute('PRAGMA busy_timeout=2000')
 try:
  if phase=='baseline':
   need(con.execute('PRAGMA journal_mode=WAL').fetchone()[0]=='wal','WAL_mode')
   con.execute('CREATE TABLE quotes(event_id INTEGER PRIMARY KEY,source TEXT,commodity TEXT,price INTEGER,seen_at INTEGER)')
   con.execute('CREATE INDEX quote_lookup ON quotes(commodity,seen_at DESC,event_id DESC)')
   con.executemany('INSERT INTO quotes VALUES(?,?,?,?,?)',[(1,'g1','emami',61500000,1),(2,'g1','bahar',59000000,2),(3,'g2','emami',62000000,3)]);con.commit()
   need(query(con)==EXPECTED,'baseline_market_query');ordinary(con)
   result=facts(con,args.package);result['query_result']=query(con);result['phase']=phase;put(root/'baseline.json',result)
  elif phase=='candidate':
   baseline=json.loads((root/'baseline.json').read_bytes());result=facts(con,args.package)
   need(result['sqlite_source_id'].endswith(SOURCE_ID),'canonical_installed_source_id')
   require_numeric_compatibility(baseline,result)
   need(result['limits']==baseline['limits'],'connection_limit_parity');need(query(con)==EXPECTED,'old_database_read')
   need(any('quote_lookup'in r[3]for r in con.execute('EXPLAIN QUERY PLAN '+QUERY,('emami',))),'market_lookup_index')
   ordinary(con)
   reader=sqlite3.connect(path);reader.execute('BEGIN');before=query(reader)
   con.execute('INSERT INTO quotes VALUES(4,\'g2\',\'emami\',64000000,4) ON CONFLICT(event_id) DO UPDATE SET price=excluded.price');con.commit()
   need(query(reader)==before,'WAL_snapshot');reader.commit();need(query(reader)[0][3]==64000000,'WAL_refresh');reader.close()
   backup=sqlite3.connect(root/'candidate-backup.db');con.backup(backup);integrity(backup);backup.close();integrity(con)
   result.update(phase=phase,query_result=query(con),WAL_upsert_snapshot_backup=True,authorizer_enforced=True,old_format_reopened=True)
   put(root/'candidate.json',result)
  elif phase=='fresh':
   baseline=json.loads((root/'baseline.json').read_bytes())
   for _ in range(8):
    fresh=sqlite3.connect(':memory:')
    try:require_numeric_compatibility(baseline,numeric_observation(fresh))
    finally:fresh.close()
   need(sqlite3.sqlite_version=='3.54.1','fresh_candidate_version');print('PASS: eight independent connections preserve legacy CAST and exact quote/JSON numeric roundtrip');return
  elif phase=='old-reopen':
   expected=json.loads((root/'candidate.json').read_bytes())['query_result'];need(query(con)==expected,'old_library_reopens_new_write');integrity(con)
   backup=sqlite3.connect(root/'candidate-backup.db');need(query(backup)==expected,'old_library_reopens_new_backup');integrity(backup);backup.close()
   con.execute('INSERT INTO quotes VALUES(5,\'g1\',\'emami\',65000000,5)');con.commit()
   result={'phase':phase,'package':args.package,'old_library_reopened_candidate_and_backup':True,'query_result':query(con),'library':identity(LIBRARY),'security_accepted':False};need(sqlite3.sqlite_version==args.package.split('-',1)[0],'old_reopen_runtime');put(root/'old-reopen.json',result)
  else:
   expected=json.loads((root/'old-reopen.json').read_bytes())['query_result'];need(query(con)==expected,'candidate_reopens_old_rollback_write');integrity(con)
   baseline=json.loads((root/'baseline.json').read_bytes());result=facts(con,args.package);require_numeric_compatibility(baseline,result);result.update(phase=phase,query_result=query(con));put(root/'candidate-reopen.json',result)
 finally:con.close()
 print('PASS: '+phase)
if __name__=='__main__':main()
