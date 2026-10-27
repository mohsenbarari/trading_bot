"""Source-bound historical81 fixture preparation; default UNARMED.

No test, Git/GH/Docker command, installed package or operational input is used.
The actual CI producer must still run every declared profile on the admitted
candidate image. This preparer never returns a completed-business-test receipt.
"""
from hashlib import sha1,sha256
from pathlib import Path,PurePosixPath
import argparse,ast,json,os,re,stat,time

HERE=Path(__file__).resolve().parent
SELECTION_SHA='7a35410166254bb9e6f1d354fb9283550d7814bdc8455a2b070c633edbded833'
BUSINESS_SHA='fef6305b1d7e6db851d1d0b77f3aa9910aad535e83f64f012790165a3d62c81b'
BUSINESS_COMMIT='126d6b97eecdc7af4bd6415ae12079c84ca4f0c8'
BUSINESS_TREE='08606163f552f01e4ca64a2250f360a71edadd78'
BUSINESS_TAG='security-product/capture2-f79-20261026-v1'
BASE='4ef8e6dcb2d361b763bd8b72dc730c1f978f564a'
REPLAY='adca507b261d3e139befc67b4a937c48c96bb949'
OLD_ASSERTION=b'        self.assertIn("python:3.11-slim-bookworm@sha256:", dockerfile)\n'
NEW_ASSERTION=b'        self.assertIn("python:3.11.17-slim-trixie@sha256:b95327ce1439e687237841b0210677aeadd62d55a060a5f6bcb9c15bf6251dd3", dockerfile)\n'
FOUNDATION='tests/test_market_pipeline_stage3_foundation.py'
CAPTURE='tests/test_market_pipeline_stage4_capture.py'
MODULES=('test_market_pipeline_stage3_foundation','test_market_pipeline_stage4_capture',
 'test_market_pipeline_stage5_coin_processor','test_market_pipeline_stage6_channel_processor','test_market_private_pipeline_contracts')
HELPERS={
 'run_business81.py':(14246,'6af5068b00d60291b2896097d2980837164430e69b28490cabfc92acaede9298'),
 'test_guard_source.py':(1055,'eddfce484567bccb6cfe40f4587f89aad9d700104ca99fb09f0f6ebe5eca4340'),
 'product_check_source.py':(2292,'e022ec3d0847808167f2401fd5208f04506fa723f0433312ab6606f31a88a007'),
 'f79-business-test-selection.json':(17201,SELECTION_SHA),
 'accepted-f79-business-manifest.json':(27889,BUSINESS_SHA)}


def need(value,code):
 if not value:raise RuntimeError(code)
def encode(value):return(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
def identity(s):return(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def blob(raw):return sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
def relative(value):return type(value)is str and str(PurePosixPath(value))==value and not value.startswith('/')and not{'..','.'}&set(PurePosixPath(value).parts)


class Sources:
 """Admit expected public byte identities before payload; retain all to finals."""
 def __init__(self):self.rows=[]
 def read(self,path,spec):
  path=Path(path);parents=[]
  for parent in path.parents:
   row=identity(parent.lstat());need(parent.resolve(strict=True)==parent and stat.S_ISDIR(row[2])and row[3]in{0,os.geteuid()}and not row[2]&0o022,'source_parent');parents.append((parent,row[:5]))
  before=identity(path.lstat());need(path.resolve(strict=True)==path and stat.S_ISREG(before[2])and before[3]in{0,os.geteuid()}
   and before[5]==1 and not before[2]&0o022 and before[6]==spec['bytes']and 0<before[6]<=4*1024**2,'source_metadata')
  fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC);self.rows.append((path,fd,before,parents))
  need(identity(os.fstat(fd))==before,'source_open');raw=os.pread(fd,before[6]+1,0)
  need(len(raw)==spec['bytes']and sha256(raw).hexdigest()==spec['sha256']and('git_blob'not in spec or blob(raw)==spec['git_blob']),'source_pin')
  self.check();return raw
 def check(self):
  for path,fd,before,parents in self.rows:
   need(identity(os.fstat(fd))==identity(path.lstat())==before and all(p.resolve(strict=True)==p and identity(p.lstat())[:5]==old for p,old in parents),'source_changed')
 def close(self):
  error=None
  for path,fd,before,parents in reversed(self.rows):
   try:need(identity(os.fstat(fd))==identity(path.lstat())==before,'source_changed')
   except BaseException as exc:
    if error is None:error=exc
   try:os.close(fd)
   except BaseException as exc:
    if error is None:error=exc
  for path,fd,before,parents in self.rows:
   try:need(identity(path.lstat())==before and all(p.resolve(strict=True)==p and identity(p.lstat())[:5]==old for p,old in parents),'source_closed_changed')
   except BaseException as exc:
    if error is None:error=exc
  if error is not None:raise error


def test_ids(raw,module):
 tree=ast.parse(raw);ids=[]
 for node in tree.body:
  if isinstance(node,ast.ClassDef):
   ids.extend('tests.'+module+'.'+node.name+'.'+method.name for method in node.body
    if isinstance(method,(ast.FunctionDef,ast.AsyncFunctionDef))and method.name.startswith('test_'))
 need(len(ids)==len(set(ids)),'test_id_duplicate');return sorted(ids)


def validate_sources(selection,base,replay):
 need(selection['schema']=='normal_market_f79_business_test_selection/1'and selection['base_commit']==BASE
  and selection['capture_override_commit']==REPLAY and selection['historical_test_count']==80
  and selection['capture_added_replay_test_count']==1 and selection['runtime_data_used']is False,'selection')
 rows=selection['historical_fixture_files'];need(len(rows)==16 and set(base)=={r['path']for r in rows},'fixture_inventory')
 for row in rows:
  name=row['path'];need(relative(name)and row['source_commit']==BASE and(len(base[name]),sha256(base[name]).hexdigest(),blob(base[name]))
   ==(row['bytes'],row['sha256'],row['git_blob']),'fixture_original')
 extra=selection['capture_added_replay_test_source'];need(extra['path']==CAPTURE and extra['source_commit']==REPLAY
  and(len(replay),sha256(replay).hexdigest(),blob(replay))==(extra['bytes'],extra['sha256'],extra['git_blob']),'replay_original')
 historical=sorted(name for module in MODULES for name in test_ids(base['tests/'+module+'.py'],module))
 need(historical==selection['historical_test_ids']and len(historical)==80,'historical_inventory')
 previous=set(test_ids(base[CAPTURE],'test_market_pipeline_stage4_capture'));current=set(test_ids(replay,'test_market_pipeline_stage4_capture'))
 need(current-previous==set(selection['capture_added_replay_test_ids'])and previous<=current,'replay_selection')
 return {'historical_ids':historical,'replay_ids':selection['capture_added_replay_test_ids'],
  'root_id':selection['root_fixture_test_id'],'original_test_count':80,'replay_test_count':1,'test_execution_performed':False}


def security_assertion_proposal(original):
 """Only proposes the separately reviewed security baseline update; no apply."""
 need(original.count(OLD_ASSERTION)==1 and NEW_ASSERTION not in original,'security_assertion_original')
 candidate=original.replace(OLD_ASSERTION,NEW_ASSERTION,1);need(candidate.replace(NEW_ASSERTION,OLD_ASSERTION,1)==original,'security_assertion_delta')
 before=ast.parse(original);after=ast.parse(candidate)
 methods=lambda tree:{c.name+'.'+m.name:ast.dump(m)for c in tree.body if isinstance(c,ast.ClassDef)for m in c.body if isinstance(m,ast.FunctionDef)}
 a,b=methods(before),methods(after);need(set(a)==set(b)and[n for n in a if a[n]!=b[n]]==['MarketPipelineStage3FoundationTests.test_compose_is_split_by_host_and_pins_security_contract'],'security_assertion_scope')
 number=original[:original.index(OLD_ASSERTION)].count(b'\n')+1
 patch=b'--- a/'+FOUNDATION.encode()+b'\n+++ b/'+FOUNDATION.encode()+b'\n@@ -'+str(number).encode()+b' +'+str(number).encode()+b' @@\n-'+OLD_ASSERTION+b'+'+NEW_ASSERTION
 return {'schema':'historical_business_security_assertion_proposal/1','path':FOUNDATION,
  'before':{'bytes':len(original),'sha256':sha256(original).hexdigest()},'after':{'bytes':len(candidate),'sha256':sha256(candidate).hexdigest()},
  'old_literal':OLD_ASSERTION.decode().strip(),'proposed_literal':NEW_ASSERTION.decode().strip(),
  'changed_test_method':'MarketPipelineStage3FoundationTests.test_compose_is_split_by_host_and_pins_security_contract',
  'business_fixture_bodies_otherwise_unchanged':True,'approved':False,'test_execution_performed':False},patch


def profiles(selection,*,image,release_commit,two_git_signature):
 need(re.fullmatch('sha256:[a-f0-9]{64}',image)and re.fullmatch('[a-f0-9]{40}',release_commit)
  and release_commit!=BUSINESS_COMMIT and re.fullmatch('[a-f0-9]{64}',two_git_signature),'candidate_scope')
 historical=selection['historical_test_ids'];root_id=selection['root_fixture_test_id'];replay=selection['capture_added_replay_test_ids']
 need(len(historical)==80 and root_id in historical and len(replay)==1 and not set(replay)&set(historical),'profile_selection')
 common={'image_id':image,'release_commit':release_commit,'business_commit':BUSINESS_COMMIT,'business_tree':BUSINESS_TREE,
  'business_manifest_sha256':BUSINESS_SHA,'selection_sha256':SELECTION_SHA,'two_git_input_signature':two_git_signature}
 return {name:{**common,'fixture_set':fixture,'uid':uid,'gid':uid,'test_ids':ids,'expected_test_count':len(ids)}
  for name,fixture,uid,ids in [('base-nonroot','base',10001,[i for i in historical if i!=root_id]),
   ('base-root','base',0,[root_id]),('added-replay','replay',10001,replay)]}


def prepare_bundle(selection,base,replay,*,image,release_commit,two_git_signature,release_recipe,recipe_spec,helper_sources,approved_assertion_source_sha256=None):
 """Prepare exact bytes only; caller still owns actual isolated image execution.

 The one security assertion proposal must be explicitly source-reviewed and
 pinned by the integrating controller. No serialized boolean can apply it.
 """
 validate_sources(selection,base,replay);plan=profiles(selection,image=image,release_commit=release_commit,two_git_signature=two_git_signature)
 proposal,_=security_assertion_proposal(base[FOUNDATION])
 need(approved_assertion_source_sha256==proposal['after']['sha256'],'separate_security_assertion_review_required')
 need(type(recipe_spec)is dict and set(recipe_spec)=={'path','source_commit','bytes','sha256','git_blob'}
  and recipe_spec['path']=='deploy/market-data/Dockerfile.capture2'and recipe_spec['source_commit']==release_commit
  and(len(release_recipe),sha256(release_recipe).hexdigest(),blob(release_recipe))==(recipe_spec['bytes'],recipe_spec['sha256'],recipe_spec['git_blob']),'release_recipe_source')
 contents={};rows=[];originals={r['path']:r for r in selection['historical_fixture_files']}
 for prefix in('base','replay'):
  for name,before in base.items():
   spec=selection['capture_added_replay_test_source']if prefix=='replay'and name==CAPTURE else originals[name]
   raw=replay if prefix=='replay'and name==CAPTURE else before;original=raw;transform='none'
   if name==FOUNDATION:raw=raw.replace(OLD_ASSERTION,NEW_ASSERTION,1);transform='reviewed_security_base_assertion_only'
   path=prefix+'/'+name;contents[path]=raw;rows.append({'path':path,'bytes':len(raw),'sha256':sha256(raw).hexdigest(),
    'original_bytes':len(original),'original_sha256':sha256(original).hexdigest(),'git_blob':spec['git_blob'],
    'source_commit':spec['source_commit'],'transformation':transform})
  path=prefix+'/deploy/market-data/Dockerfile';contents[path]=release_recipe
  rows.append({'path':path,'bytes':len(release_recipe),'sha256':sha256(release_recipe).hexdigest(),
   'original_bytes':len(release_recipe),'original_sha256':sha256(release_recipe).hexdigest(),'git_blob':recipe_spec['git_blob'],
   'source_commit':release_commit,'transformation':'release_build_recipe'})
 need(type(helper_sources)is dict and set(helper_sources)==set(HELPERS),'helper_inventory')
 for name,(size,digest)in HELPERS.items():
  raw=helper_sources[name];need(type(raw)is bytes and 0<len(raw)<=131072 and(size is None or len(raw)==size)
   and sha256(raw).hexdigest()==digest,'helper_pin');contents[name]=raw
 need(sha256(contents['f79-business-test-selection.json']).hexdigest()==SELECTION_SHA
  and sha256(contents['accepted-f79-business-manifest.json']).hexdigest()==BUSINESS_SHA,'baseline_pin')
 manifest={'release_recipe_path':'deploy/market-data/Dockerfile.capture2','schema':'normal_market_business81_sources/2','image_id':image,'release_commit':release_commit,'two_git_input_signature':two_git_signature,
  'business_commit':BUSINESS_COMMIT,'business_tree':BUSINESS_TREE,'selection_sha256':SELECTION_SHA,'business_manifest_sha256':BUSINESS_SHA,
  'files':sorted(rows,key=lambda r:r['path']),'runner_sha256':sha256(contents['run_business81.py']).hexdigest(),
  'security_assertion_update':{'path':FOUNDATION,'old_line':OLD_ASSERTION.decode(),'new_line':NEW_ASSERTION.decode(),
   'approved_source_delta_sha256':approved_assertion_source_sha256},'security_accepted':False}
 contents['suite-manifest.json']=encode(manifest)
 return contents,manifest,plan


def container_args(profile,*,image,evidence,manifest_sha256):
 need(profile in{'base-nonroot','base-root','added-replay'}and re.fullmatch('sha256:[a-f0-9]{64}',image)
  and re.fullmatch('[a-f0-9]{64}',manifest_sha256)and type(evidence)is str and evidence.startswith('/')and ','not in evidence,'container_plan')
 user='0:0'if profile=='base-root'else'10001:10001'
 return['--pull=never','--user='+user,'--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges',
  '--memory=1g','--memory-swap=1g','--cpus=2','--pids-limit=128','--log-driver=none','--ulimit','core=0',
  '--tmpfs','/tmp:rw,nosuid,nodev,noexec,size=128m',
  '--mount','type=bind,source='+evidence+',target=/evidence,readonly','--entrypoint','/usr/local/bin/python3',image,
  '-I','-S','-B','/evidence/run_business81.py','--profile',profile,'--image-id',image,'--manifest-sha256',manifest_sha256]


def join_actual_results(selection,plans,results,*,expected_manifest_sha256):
 """Only validates three real child observations; never authorizes release."""
 need(set(plans)==set(results)=={'base-nonroot','base-root','added-replay'}
  and type(expected_manifest_sha256)is str and re.fullmatch('[a-f0-9]{64}',expected_manifest_sha256),'profile_results')
 combined=[]
 for name,plan in plans.items():
  envelope=results[name];need(type(envelope)is dict and set(envelope)=={'actual_outer_returncode','actual_container_exit_code','observation'}
   and type(envelope['actual_outer_returncode'])is int and envelope['actual_outer_returncode']==0
   and type(envelope['actual_container_exit_code'])is int and envelope['actual_container_exit_code']==0,'actual_profile_exit')
  value=envelope['observation'];need(type(value)is dict and set(value)=={'schema','image_id','release_commit','business_commit','business_tree',
   'two_git_input_signature','test_sources_sha256','profile','execution_uid','execution_gid','discarded_fixture_stdout_bytes','selected_test_ids','tests','skips','expected_failures',
   'unexpected_successes','failures','errors','status','actual_candidate_source93_verified','runtime_data_used','business_data_freshness_proven','security_accepted'}
   and value.get('schema')=='normal_market_business81_profile_result/1'
   and value.get('status')=='passed'and value.get('profile')==name,'profile_result')
  for key in('image_id','release_commit','business_commit','business_tree','two_git_input_signature'):
   need(value.get(key)==plan[key],'profile_identity')
  need(value['test_sources_sha256']==expected_manifest_sha256
   and type(value['discarded_fixture_stdout_bytes'])is int and 0<=value['discarded_fixture_stdout_bytes']<=131072
   and all(plan[k]==plans['base-nonroot'][k]for k in('image_id','release_commit','business_commit','business_tree','two_git_input_signature'))
   and value.get('selected_test_ids')==sorted(plan['test_ids'])and type(value.get('tests'))is int and value['tests']==plan['expected_test_count']
   and type(value.get('execution_uid'))is int and value['execution_uid']==plan['uid']and type(value.get('execution_gid'))is int and value['execution_gid']==plan['gid'],'actual_test_selection')
  need(all(type(value.get(k))is int and value[k]==0 for k in('skips','expected_failures','unexpected_successes','failures','errors'))
   and value.get('actual_candidate_source93_verified')is True and all(value.get(k)is False for k in('runtime_data_used','business_data_freshness_proven','security_accepted')),'actual_test_completion')
  combined+=value['selected_test_ids']
 need(len(combined)==len(set(combined))==81 and sorted(combined)==sorted(selection['historical_test_ids']+selection['capture_added_replay_test_ids']),'combined_actual_selection')
 return{'schema':'normal_market_historical81_business_tests/1','image_id':plans['base-nonroot']['image_id'],
  'release_commit':plans['base-nonroot']['release_commit'],'two_git_input_signature':plans['base-nonroot']['two_git_input_signature'],
  'test_sources_sha256':expected_manifest_sha256,'business_commit':BUSINESS_COMMIT,'business_tree':BUSINESS_TREE,'tests':81,'skips':0,'selected_test_ids':sorted(combined),
  'profiles':['base-nonroot','base-root','added-replay'],'runtime_data_used':False,'security_accepted':False,
  'actual_outer_authentication_and_cleanup_still_required':True,'deployment_authority':False}


def main(argv=None):
 parser=argparse.ArgumentParser();parser.add_argument('--analyze-public-materialization',type=Path);args=parser.parse_args(argv)
 if args.analyze_public_materialization is None:
  print(json.dumps({'status':'UNARMED','test_execution_performed':False,'security_accepted':False}));return 0
 origin=args.analyze_public_materialization;sources=Sources()
 try:
  selection_raw=sources.read(HERE/'f79-business-test-selection.json',{'bytes':17201,'sha256':SELECTION_SHA});selection=json.loads(selection_raw)
  sources.read(HERE/'accepted-f79-business-manifest.json',{'bytes':27889,'sha256':BUSINESS_SHA})
  base={row['path']:sources.read(origin/'base'/row['path'],row)for row in selection['historical_fixture_files']}
  replay=sources.read(origin/'adca'/CAPTURE,selection['capture_added_replay_test_source']);analysis=validate_sources(selection,base,replay)
  proposal,patch=security_assertion_proposal(base[FOUNDATION]);sources.check()
 finally:sources.close()
 print(json.dumps({'schema':'historical_business81_source_analysis/1',**analysis,'security_assertion_proposal':proposal,
  'business_commit':BUSINESS_COMMIT,'business_tree':BUSINESS_TREE,'actual_candidate_test_execution_performed':False,'security_accepted':False},sort_keys=True));return 0


if __name__=='__main__':raise SystemExit(main())
