"""Actual CLI tests for bounded residual43 JSON selection; no collection."""
from pathlib import Path
import json,subprocess,sys
HERE=Path(__file__).absolute().parent
def run(raw=None):
 args=[sys.executable,'-B',str(HERE/'select_groups.py')]
 if raw is not None:args+=['--groups-json',raw]
 return subprocess.run(args,capture_output=True,text=True,timeout=10)
def main():
 rows=[]
 for raw,groups,jobs in[(None,list(range(43)),9),('[10,38]',[10,38],2),('[38,10]',[10,38],2),('[0]',[0],1),('[42]',[42],1),('[35,38,42]',[35,38,42],1)]:
  r=run(raw);assert r.returncode==0 and not r.stderr;assert len(r.stdout.encode())<=2048;matrix=json.loads(r.stdout);actual=[int(v)for item in matrix['include']for v in item['indices'].split()];assert actual==groups and len(matrix['include'])==jobs and len(actual)==len(set(actual));rows.append({'input':raw,'actual_exit':0,'groups':actual,'jobs':jobs})
 for raw in['','[]','null','{}','10','true','[true]','[false,10]','[10,10]','[-1]','[43]','[10.0]','["10"]','[NaN]','[1e999]','[10,38]; echo OWNED','[10,38,]',' '*513,'['+','.join('0'for _ in range(44))+']']:
  r=run(raw);assert r.returncode!=0 and not r.stdout and'Invalid bounded'in r.stderr;rows.append({'input':raw,'actual_exit':r.returncode,'matrix_emitted':False})
 print(json.dumps({'schema':'OWNED_residual43_selection_CLI_QA/1','cases':len(rows),'rows':rows,'network_or_collection_actions':False,'security_accepted':False},sort_keys=True,separators=(',',':')))
if __name__=='__main__':main()
