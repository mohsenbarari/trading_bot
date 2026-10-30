"""Bounded JSON selection of already fixed residual43 groups; data only."""
import argparse,json,sys
BATCHES=(('small-a',(0,1,2,3,4)),('small-b',(5,6,7,8,9)),('gcc',(10,)),('middle-a',(11,12,13,14,15,16)),('middle-b',(17,18,19,20,21,22)),('middle-c',(23,24,25,26,27,28,29)),('crypto',(30,31,32,33)),('perl',(34,)),('remaining',(35,36,37,38,39,40,41,42)))
DEFAULT_GROUPS='['+','.join(map(str,range(43)))+']'
def matrix(raw):
 if type(raw)is not str or not 0<len(raw.encode('utf-8'))<=512:raise ValueError('groups_JSON_bound')
 value=json.loads(raw,parse_constant=lambda _:(_ for _ in()).throw(ValueError('groups_JSON_constant')))
 if type(value)is not list or not 1<=len(value)<=43 or any(type(v)is not int or not 0<=v<=42 for v in value)or len(set(value))!=len(value):raise ValueError('groups_JSON_exact_unique_integer_selection')
 wanted=set(value);out={'include':[{'batch':name,'indices':' '.join(str(v)for v in group if v in wanted)}for name,group in BATCHES if wanted.intersection(group)]};encoded=json.dumps(out,sort_keys=True,separators=(',',':'))
 if len(encoded.encode())>2048:raise ValueError('matrix_output_bound')
 return out
def main(argv=None):
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--groups-json',default=DEFAULT_GROUPS);args=parser.parse_args(argv)
 try:out=matrix(args.groups_json)
 except (ValueError,TypeError,UnicodeError):print('Invalid bounded unique JSON integer group selection',file=sys.stderr);return 1
 print(json.dumps(out,sort_keys=True,separators=(',',':')));return 0
if __name__=='__main__':raise SystemExit(main())
