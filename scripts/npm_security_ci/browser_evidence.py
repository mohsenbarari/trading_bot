"""Finite, bounded evidence for this workflow's three browsers/four shards.

Collection is not execution; collecting a report never grants acceptance.
The original browser step remains authoritative and is never continued-on-error.
"""
import argparse
from collections import Counter
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat

BROWSERS=('chromium','firefox','webkit')
STATES=('expected','unexpected','flaky','skipped')
MAX_REPORT=32*1024**2
MAX_SUMMARY=512*1024
MAX_CASES=5000

def need(ok,code):
    if not ok:raise ValueError(code)

def identity(s):
    return (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)

def unique(rows):
    result={}
    for key,value in rows:need(key not in result,'duplicate_json_key');result[key]=value
    return result

def read_document(path,maximum):
    path=Path(path).absolute();need(path.resolve(strict=True)==path,'input_path')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    try:
        before=os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and before.st_uid==os.geteuid()
             and not before.st_mode&0o022 and 0<before.st_size<=maximum,'input_metadata_or_bound')
        raw=os.pread(fd,before.st_size+1,0)
        need(len(raw)==before.st_size and identity(before)==identity(os.fstat(fd))==identity(path.lstat()),'input_changed')
        value=json.loads(raw,object_pairs_hook=unique,parse_constant=lambda _:need(False,'nonfinite_json'))
        need(identity(before)==identity(os.fstat(fd))==identity(path.lstat()),'input_changed')
        return value,{'bytes':len(raw),'sha256':sha256(raw).hexdigest()}
    finally:os.close(fd)

def write_new(path,value):
    # Count every encoded chunk before retaining it or opening the output.
    chunks=[];total=1
    encoder=json.JSONEncoder(sort_keys=True,separators=(',',':'),allow_nan=False)
    for text in encoder.iterencode(value):
        chunk=text.encode();need(total+len(chunk)<=MAX_SUMMARY,'summary_bound')
        total+=len(chunk);chunks.append(chunk)
    chunks.append(b'\n');path=Path(path).absolute()
    need(path.parent.resolve(strict=True)==path.parent,'output_parent')
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    try:
        for raw in chunks:
            view=memoryview(raw)
            while view:
                written=os.write(fd,view);need(written>0,'output_write');view=view[written:]
        os.fsync(fd)
    finally:os.close(fd)

def subject(head,run,attempt):
    need(re.fullmatch('[a-f0-9]{40}',head) is not None and type(run) is int and run>0
         and type(attempt) is int and 0<attempt<1000,'subject')
    return {'project_commit':head,'run_id':run,'run_attempt':attempt}

def cases(document,browser):
    need(type(document) is dict and type(document.get('suites')) is list,'report_shape')
    pending=[(s,0) for s in document['suites']];result={};nodes=0
    while pending:
        suite,depth=pending.pop();nodes+=1
        need(nodes<=20000 and depth<=32 and type(suite) is dict,'suite_bound')
        need(type(suite.get('specs',[])) is list and type(suite.get('suites',[])) is list,'suite_shape')
        pending.extend((s,depth+1) for s in suite.get('suites',[]))
        for spec in suite.get('specs',[]):
            need(type(spec) is dict and type(spec.get('id')) is str and 0<len(spec['id'])<=512
                 and not any(ord(c)<32 for c in spec['id']) and spec['id'] not in result,'case_identity')
            tests=spec.get('tests');need(type(tests) is list and len(tests)==1,'case_project_multiplicity')
            test=tests[0];need(type(test) is dict and test.get('projectName')==browser,'case_project')
            result[spec['id']]=test;need(len(result)<=MAX_CASES,'case_bound')
    return result

def summarize(args):
    need(args.browser in BROWSERS and type(args.shard) is int and 1<=args.shard<=4,'matrix')
    whole,whole_pin=read_document(args.collection,MAX_REPORT);report,report_pin=read_document(args.report,MAX_REPORT)
    need(whole.get('config',{}).get('shard') is None,'whole_collection_sharded')
    shard=report.get('config',{}).get('shard')
    need(type(shard) is dict and set(shard)=={'current','total'} and type(shard['current']) is int
         and type(shard['total']) is int and shard=={'current':args.shard,'total':4},'reported_shard')
    all_cases=cases(whole,args.browser);actual=cases(report,args.browser)
    need(bool(all_cases) and set(actual)<=set(all_cases),'case_collection_join')
    outcomes=Counter();details=[]
    for identifier,test in sorted(actual.items()):
        state=test.get('status');need(state in STATES,'case_status');outcomes[state]+=1
        attempts=test.get('results');need(type(attempts) is list and len(attempts)<=16,'attempt_bound')
        need(test.get('expectedStatus') in {'passed','failed','skipped','timedOut','interrupted'},'expected_status')
        need(all(type(r) is dict and r.get('status') in {'passed','failed','skipped','timedOut','interrupted'}
                 for r in attempts),'attempt_status')
        details.append({'id':identifier,'outcome':state,'expectedStatus':test['expectedStatus'],
                        'actual_attempt_statuses':[r.get('status') for r in attempts]})
    stats=report.get('stats');need(type(stats) is dict,'stats')
    for state in STATES:need(type(stats.get(state)) is int and stats[state]>=0 and stats[state]==outcomes[state],'stats_join')
    errors=report.get('errors');need(type(errors) is list and len(errors)<=1000,'global_errors')
    value={'schema':'npm_browser_shard_evidence/1','subject':subject(args.head,args.run,args.attempt),
           'browser':args.browser,'shard':args.shard,'total_shards':4,'gate_outcome':args.outcome,
           'whole_collection':whole_pin,'runtime_report':report_pin,'whole_case_ids':sorted(all_cases),
           'actual_case_ids':sorted(actual),'actual_cases':details,'counts':{s:outcomes[s] for s in STATES},
           'global_error_count':len(errors),'collection_is_test_execution':False,
           'collector_success_is_test_acceptance':False,'security_accepted':False}
    write_new(args.out,value);return value

def aggregate(args):
    root=Path(args.reports).absolute();need(root.resolve(strict=True)==root and root.is_dir(),'reports_directory')
    expected={f'{b}-{s}.json' for b in BROWSERS for s in range(1,5)}
    need({p.name for p in root.iterdir()}==expected,'matrix_summary_inventory')
    common=subject(args.head,args.run,args.attempt);result={};all_steps_success=True
    for browser in BROWSERS:
        union=set();whole_ids=None;counts=Counter();inputs=[]
        for shard in range(1,5):
            report,pin=read_document(root/f'{browser}-{shard}.json',MAX_SUMMARY)
            claimed=report.get('subject')
            need(type(claimed) is dict and set(claimed)==set(common)
                 and type(claimed['run_id']) is int and type(claimed['run_attempt']) is int,'summary_subject_types')
            need(report.get('schema')=='npm_browser_shard_evidence/1' and claimed==common
                 and report.get('browser')==browser and type(report.get('shard')) is int and report['shard']==shard
                 and type(report.get('total_shards')) is int and report['total_shards']==4
                 and report.get('security_accepted') is False,'summary_subject')
            ids=report.get('actual_case_ids');whole=report.get('whole_case_ids')
            need(type(ids) is list and type(whole) is list and len(ids)==len(set(ids)) and len(whole)==len(set(whole))
                 and len(whole)<=MAX_CASES and all(type(x) is str for x in ids+whole),'summary_case_ids')
            if whole_ids is None:whole_ids=set(whole)
            need(set(whole)==whole_ids and not union.intersection(ids),'shard_duplicate_or_collection_changed');union.update(ids)
            need(report.get('gate_outcome') in {'success','failure','cancelled','skipped'},'gate_outcome')
            all_steps_success=all_steps_success and report['gate_outcome']=='success'
            need(type(report.get('counts')) is dict and set(report['counts'])==set(STATES),'summary_count_keys')
            for state in STATES:
                count=report.get('counts',{}).get(state);need(type(count) is int and count>=0,'summary_counts');counts[state]+=count
            need(sum(report['counts'].values())==len(ids),'summary_count_join');inputs.append(pin)
        need(union==whole_ids,'shard_coverage_gap')
        result[browser]={'complete_disjoint_union':True,'collected_case_count':len(whole_ids),'counts':dict(counts),'inputs':inputs}
    value={'schema':'npm_browser_matrix_evidence/1','subject':common,'matrix':result,
           'all_original_gate_steps_success':all_steps_success,'collector_success_is_test_acceptance':False,'security_accepted':False}
    write_new(args.out,value);need(all_steps_success,'original_browser_gate_failed');return value

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);modes=parser.add_subparsers(dest='mode',required=True)
    for name in ('summarize','aggregate'):
        item=modes.add_parser(name);item.add_argument('--head',required=True);item.add_argument('--run',type=int,required=True)
        item.add_argument('--attempt',type=int,required=True);item.add_argument('--out',type=Path,required=True)
        if name=='summarize':
            item.add_argument('--collection',type=Path,required=True);item.add_argument('--report',type=Path,required=True)
            item.add_argument('--browser',choices=BROWSERS,required=True);item.add_argument('--shard',type=int,required=True)
            item.add_argument('--outcome',choices=('success','failure','cancelled','skipped'),required=True)
        else:item.add_argument('--reports',type=Path,required=True)
    try:
        args=parser.parse_args(argv);(summarize if args.mode=='summarize' else aggregate)(args)
    except Exception as error:
        print(json.dumps({'status':'evidence_refused','error_class':type(error).__name__,
                          'code':str(error) if type(error) is ValueError else 'input_or_output_refusal','security_accepted':False}));return 1
    print(json.dumps({'status':'evidence_recorded','security_accepted':False}));return 0

if __name__=='__main__':raise SystemExit(main())
