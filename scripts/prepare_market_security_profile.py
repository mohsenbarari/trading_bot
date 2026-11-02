"""Closed historical Market products for the fixed, tested E46 security stack.

This module prepares public CI inputs only. It neither reads runtime configuration
nor grants activation authority. The sealed E46 preparer supplies bounded public
Git/blob and publication primitives; its E46 profile globals are never changed.
"""
from hashlib import sha1, sha256
import ast
import json
import os
from pathlib import Path
import re

DOSSIER = ('docs/security/evidence/2026-10-25-market-other-nine-public-profiles.json',
           '20c08b20e5797891b5c262a4b4310bc2a7ba75f64850fd3a08c786eb78018f6a')
STACK = ('docs/security/evidence/2026-10-25-market-profile-stack-parity.json',
         'ec7c9455e1e780b525b96095b1c62eeee66bf17b57c4196c2f8c9557de85d7e4')
PROFILES = ('2239469ab3dd','2c3731f573e1','2e325778cb28','414f6bc8f9bf',
            '678aaf5cd8e9','a37ec78ec097','e31aed440f25','f365061d2e76','f79cdda21c19')
OLD_LOCK = '37c5dd760d0816ad0b4546aca8bc5e18875478cdacfb1620bae3d9f5ff29ae29'
SECURITY_SOURCE = '88299eb42bfa05361f8b8ed82358873336a6480e'
CAPTURE_SOURCE = 'adca507b261d3e139befc67b4a937c48c96bb949'
OVERRIDES = ('docs/security/evidence/2026-10-25-market-f79-public-source-overrides.json',
             '03bd91fd5606dad574e4bc165b63a8dab2440658a15f8a3cfab6e50fc5b5f85c')
PATCH_SHA = '40d0bcaf33d5ef5b5ec714b333274f57af1057973ad757f330c71f6eb09cd292'
MAX_FILE, MAX_TOTAL = 4*1024**2, 64*1024**2


def require(ok, code):
    if not ok: raise RuntimeError(code)


def encoded(value): return (json.dumps(value,sort_keys=True)+'\n').encode()


def same_json_value(left,right):
    if type(left) is not type(right):return False
    if type(left) is dict:return set(left)==set(right) and all(same_json_value(left[k],right[k]) for k in left)
    if type(left) is list:return len(left)==len(right) and all(same_json_value(a,b) for a,b in zip(left,right))
    return left==right


def document(raw):
    def pairs(rows):
        value={}
        for key,item in rows:
            require(key not in value,'profile_json');value[key]=item
        return value
    require(type(raw) is bytes and 0<len(raw)<=2*1024**2,'profile_json_bound')
    try:return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:require(False,'profile_json'))
    except (ValueError,UnicodeError,RecursionError):raise RuntimeError('profile_json') from None


def public_blob(repo,commit,path,market):
    require(type(commit) is str and re.fullmatch('[a-f0-9]{40}',commit)
        and market.public_path(path),'profile_source_identity')
    rows=market.tree_entries(market.git(repo,'ls-tree','-r','-z',commit,'--',path))
    require(set(rows)=={path},'profile_source_missing')
    return rows[path],market.blob(repo,rows[path])


def control(repo,commit,path,expected,market):
    _,raw=public_blob(repo,commit,path,market)
    require(sha256(raw).hexdigest()==expected,'profile_control_pin');return raw


def choose_profile(dossier,stack,profile):
    require(profile in PROFILES and type(dossier) is dict and type(stack) is dict,'profile_unknown')
    require(dossier.get('schema')=='market_other_nine_public_profile_evidence/1'
        and dossier.get('historical_lock_identical_to_E46') is True
        and all(dossier.get(k) is False for k in ('new_candidate_built','runtime_changed','security_accepted',
                                                'source_product_admission_implemented')),'profile_dossier')
    rows=dossier.get('profiles');require(type(rows) is list and len(rows)==9,'profile_dossier')
    require({q.get('image_id','')[7:19] for q in rows}==set(PROFILES)
        and all(type(q.get('image_id')) is str and re.fullmatch('sha256:[a-f0-9]{64}',q['image_id']) for q in rows),'profile_dossier')
    selected=[q for q in rows if q['image_id'][7:19]==profile];require(len(selected)==1,'profile_unknown');selected=selected[0]
    require(selected.get('historical_dependency_lock_sha256')==OLD_LOCK
        and type(selected.get('non_pyc_file_count')) is int and selected['non_pyc_file_count'] in (93,94,95),
        'profile_dependency_control')
    require(stack.get('schema')=='market_security_profiles_public_stack_parity/1'
        and stack.get('source_dossier_sha256')==DOSSIER[1]
        and stack.get('all_application_distribution_names_versions_equal_E46') is True
        and stack.get('all_nonenv_config_fields_except_labels_equal_E46') is True
        and stack.get('metadata_path')=='/opt/market-python'
        and stack.get('metadata_interpreter_arguments')==['-I','-S','-B']
        and all(stack.get(k) is False for k in ('private_container_env_read','application_imported',
            'site_or_pth_startup_allowed','runtime_changed','old_installed_dependency_file_bytes_equivalence_asserted',
            'candidate_built','full_runtime_matrix_completed','security_accepted','deployment_eligible')),'profile_stack_control')
    observations=stack.get('profiles');require(type(observations) is list and len(observations)==10,'profile_stack_control')
    baseline=[q for q in observations if q.get('image_id')==stack.get('baseline_image_id')]
    actual=[q for q in observations if q.get('image_id')==selected['image_id']]
    require(len(baseline)==len(actual)==1,'profile_stack_control');baseline,actual=baseline[0],actual[0]
    for q in (baseline,actual):
        require(q.get('running') is True and q.get('container_identity_stable') is True
            and q['image'].get('image_id')==q['image_id'] and q['image'].get('os')=='linux'
            and q['image'].get('architecture')=='amd64'
            and q['public_source_revision'].get('exactly_one_default_matches') is True,'profile_stack_control')
    require(actual['public_source_revision']['expected_public_source_commit']==selected['label_commit']
        and same_json_value(actual['public_installed_metadata'],baseline['public_installed_metadata'])
        and actual['public_installed_metadata']['site_packages']=='/opt/market-python'
        and len(actual['public_installed_metadata']['packages'])==9,'profile_dependency_control')
    left,right=baseline['image']['config'],actual['image']['config']
    require(set(left)==set(right) and 'Env' not in left and 'Env' not in right
        and same_json_value({k:v for k,v in left.items() if k!='Labels'},{k:v for k,v in right.items() if k!='Labels'})
        and right['User']=='10001:10001' and right['WorkingDir']=='/app'
        and right['Entrypoint']==['python','-m','core.market_intelligence.private_pipeline_foundation']
        and right['Cmd']==['service','--role','market-processor']
        and right['Healthcheck'] is None and right['Volumes'] is None,'profile_runtime_control')
    return selected,actual


def patch_foundation(base,patch,record):
    """Replay only the sealed zero-context public F79 patch, never fuzzily."""
    require(type(base) is bytes and sha256(base).hexdigest()==record['base_sha256']
        and type(patch) is bytes and sha256(patch).hexdigest()==PATCH_SHA==record['patch_sha256'],
        'profile_override_base')
    lines=patch.splitlines(keepends=True)
    require(lines[:2]==[b'--- a/core/market_intelligence/private_pipeline_foundation.py\n',
                       b'+++ b/core/market_intelligence/private_pipeline_foundation.py\n'],'profile_patch_shape')
    original=base.splitlines(keepends=True);result=[];cursor=0;index=2
    while index<len(lines):
        match=re.fullmatch(rb'@@ -([0-9]+),([0-9]+) \+([0-9]+),([0-9]+) @@\n',lines[index]);index+=1
        require(match is not None,'profile_patch_shape');start,count,new_start,new_count=map(int,match.groups());start-=1
        require(cursor<=start<=len(original) and new_start==len(result)+start-cursor+1,'profile_patch_shape')
        result+=original[cursor:start];removed=[];added=[]
        while index<len(lines) and not lines[index].startswith(b'@@ '):
            line=lines[index];index+=1;require(line[:1] in (b'-',b'+'),'profile_patch_shape')
            (removed if line[:1]==b'-' else added).append(line[1:])
        require(len(removed)==count and len(added)==new_count and original[start:start+count]==removed,'profile_patch_context')
        result+=added;cursor=start+count
    result+=original[cursor:];raw=b''.join(result)
    require(len(raw)==record['actual_bytes'] and sha256(raw).hexdigest()==record['actual_sha256'],'profile_override_result');return raw


def verify_source(raw,cache_raw,selected,product,market):
    require(sha256(raw).hexdigest()==selected['source']['sha256']
        and sha256(cache_raw).hexdigest()==selected['cache']['sha256'],'profile_control_pin')
    source=document(raw);wrapper=document(cache_raw);cache=wrapper.get('classification')
    require(source.get('schema')=='market_security_public_source_fingerprint/1'
        and source.get('image_id')==selected['image_id'] and source.get('release_sha')==selected['label_commit']
        and source.get('release_tree')==selected['label_tree'] and source.get('platform')=='linux/amd64'
        and source.get('runtime_user')=='10001:10001' and source.get('stable_before_after') is True
        and source.get('mount_overlays_absent') is True
        and all(source.get(k) is False for k in ('application_imported','secrets_read','runtime_changed')),'profile_source_control')
    rows=source.get('files');require(type(rows) is list and len(rows)<=4096,'profile_source_control')
    by={}
    for row in rows:
        require(type(row) is dict and set(row)=={'path','bytes','sha256','mode','uid','gid'}
            and market.copied_path(row['path']) and row['path'] not in by
            and type(row['bytes']) is int and 0<=row['bytes']<=MAX_FILE
            and type(row['uid']) is int and type(row['gid']) is int and row['uid']==row['gid']==10001
            and row['mode'] in ('0644','0755') and re.fullmatch('[a-f0-9]{64}',row['sha256']),'profile_source_control')
        by[row['path']]=row
    require(rows==sorted(rows,key=lambda q:q['path']) and type(source.get('file_count')) is int
        and source['file_count']==len(rows) and type(source.get('total_bytes')) is int
        and source['total_bytes']==sum(q['bytes'] for q in rows)<=MAX_TOTAL
        and source['fingerprint_sha256']==sha256(market.encoded(rows)).hexdigest(),'profile_source_control')
    expected={name:{'path':name,'bytes':len(data),'sha256':sha256(data).hexdigest(),
        'uid':10001,'gid':10001,'mode':'0755' if mode=='100755' else '0644'} for name,(mode,data) in product.items()}
    require(len(expected)==selected['non_pyc_file_count'] and all(by.get(name)==row for name,row in expected.items())
        and {n for n in by if not n.endswith('.pyc')}==set(expected),'profile_product_drift')
    require(wrapper.get('before_after_container_public_identity_equal') is True and type(cache) is dict
        and cache.get('schema')=='market_security_public_cache_classification/1'
        and cache.get('image_id')==selected['image_id'] and cache.get('container_id')==source['container_id']
        and cache.get('source_manifest_sha256')==selected['source']['sha256']
        and cache.get('python_version')==[3,11,16] and cache.get('stable_before_after') is True
        and cache.get('all_standard_cache_paths_nonselected_or_stale') is True
        and cache.get('scope')=='normal_CPython_3_11_SourceFileLoader_cache_selection_and_header_rejection_only'
        and all(cache.get(k) is False for k in ('explicit_bytecode_loader_reachability_assessed','cached_code_deserialized',
            'cached_code_executed','application_imported','secrets_read','runtime_changed','security_accepted')),'profile_cache_control')
    caches=cache.get('caches');require(type(caches) is list and len(caches)==selected['cache_count']
        and type(cache.get('cache_count')) is int and cache['cache_count']==len(caches)
        and {q.get('path') for q in caches}==set(by)-set(expected) and len({q.get('path') for q in caches})==len(caches),
        'profile_cache_control')
    require(all(q.get('classification') in ('nonselected_cache_tag','source_absent','timestamp_stale','hash_stale')
        and q.get('bytes')==by[q['path']]['bytes'] and q.get('sha256')==by[q['path']]['sha256'] for q in caches),'profile_cache_control')
    return {'source_sha256':selected['source']['sha256'],'cache_sha256':selected['cache']['sha256'],
        'observed_product_files':len(product),'observed_cache_files':len(caches),
        'cache_scope':'standard_loader_selection_only_no_cached_code_deserialized_or_executed',
        'explicit_bytecode_loader_reachability_assessed':False,'security_accepted':False}


def method_sources(raw,*,tests_only=True):
    tree=ast.parse(raw);lines=raw.splitlines(keepends=True);result={}
    for node in tree.body:
        if isinstance(node,ast.ClassDef):
            for method in node.body:
                if isinstance(method,(ast.FunctionDef,ast.AsyncFunctionDef)) and (not tests_only or method.name.startswith('test_')):
                    key=node.name+'.'+method.name;require(key not in result,'profile_test_inventory')
                    start=min([method.lineno,*[q.lineno for q in method.decorator_list]])
                    result[key]=b''.join(lines[start-1:method.end_lineno])
    return result


def test_ids(selected,modules):
    result=[]
    for name in modules:
        for method in method_sources(selected['tests/'+name+'.py'][2]):result.append('tests.'+name+'.'+method)
    require(result and len(result)==len(set(result)),'profile_test_inventory');return sorted(result)


def prepare(repo,commit,output,profile,market):
    require(type(commit) is str and re.fullmatch('[a-f0-9]{40}',commit),'profile_recipe')
    dossier=document(control(repo,commit,*DOSSIER,market));stack=document(control(repo,commit,*STACK,market))
    selected,observed=choose_profile(dossier,stack,profile)
    base=market.tree_entries(market.git(repo,'ls-tree','-r','-z',selected['label_commit']))
    require(market.tree_digest(base)==selected['label_tree'],'profile_historical_tree')
    require('deploy/market-data/requirements.lock' in base
        and sha256(market.blob(repo,base['deploy/market-data/requirements.lock'])).hexdigest()==OLD_LOCK,'profile_dependency_control')
    product={n:(entry[0],market.blob(repo,entry)) for n,entry in base.items() if market.copied_path(n)}
    require(len(product)==selected['non_pyc_file_count'] and sum(len(v[1]) for v in product.values())<=MAX_TOTAL
        and all(not n.endswith('.pyc') for n in product),'profile_product_inventory')
    overrides=None
    if profile=='f79cdda21c19':
        overrides=document(control(repo,commit,*OVERRIDES,market))
        require(overrides.get('schema')=='market_f79_observed_public_source_overrides/1'
            and overrides.get('image')==selected['image_id'] and overrides.get('label_commit')==selected['label_commit']
            and set(overrides.get('overrides',{}))=={'core/market_intelligence/private_capture.py',
                                                  'core/market_intelligence/private_pipeline_foundation.py'},'profile_override_control')
        row=overrides['overrides']['core/market_intelligence/private_capture.py']
        require(row['commit']==CAPTURE_SOURCE,'profile_override_control')
        entry,raw=public_blob(repo,CAPTURE_SOURCE,'core/market_intelligence/private_capture.py',market)
        require(len(raw)==row['actual_bytes'] and sha256(raw).hexdigest()==row['actual_sha256'],'profile_override_result')
        product['core/market_intelligence/private_capture.py']=(entry[0],raw)
        name='core/market_intelligence/private_pipeline_foundation.py';row=overrides['overrides'][name]
        patch=control(repo,commit,row['patch_path'],PATCH_SHA,market)
        product[name]=(product[name][0],patch_foundation(product[name][1],patch,row))
    source=control(repo,commit,selected['source']['path'],selected['source']['sha256'],market)
    cache=control(repo,commit,selected['cache']['path'],selected['cache']['sha256'],market)
    binding=verify_source(source,cache,selected,product,market)
    require(output.is_absolute() and output.parent.resolve(strict=True)==output.parent and not os.path.lexists(output),'profile_output')
    os.mkdir(output,0o700);(output/'product').mkdir(mode=0o755);(output/'product').chmod(0o755)
    files=[]
    for name,(mode,raw) in sorted(product.items()):
        require(mode in ('100644','100755'),'profile_product_mode')
        market.write(market.public_destination(output/'product',name),raw,0o755 if mode=='100755' else 0o644)
        files.append({'path':name,'mode':mode,'bytes':len(raw),'sha256':sha256(raw).hexdigest(),
                      'git_blob':sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()})
    historical=capsule(repo,commit,output/'tests',selected['label_commit'],market,source)
    require(historical['expected_tests'] in (80,85,86,87) and market.ROOT_FIXTURE_TEST_ID in historical['test_ids'],'profile_test_inventory')
    replay=None
    if profile=='f79cdda21c19':
        name='test_market_pipeline_stage4_capture';path='tests/'+name+'.py'
        _,old=public_blob(repo,selected['label_commit'],path,market);_,new=public_blob(repo,CAPTURE_SOURCE,path,market)
        before,after=method_sources(old),method_sources(new);added=sorted(set(after)-set(before))
        require(added,'profile_replay_inventory')
        replay=capsule(repo,commit,output/'replay-tests',CAPTURE_SOURCE,market,source,modules=(name,),
            selected_ids=['tests.'+name+'.'+method for method in added])
        replay['shared_method_changes']=[method for method in sorted(set(before)&set(after)) if before[method]!=after[method]]
        old_support,new_support=method_sources(old,tests_only=False),method_sources(new,tests_only=False)
        replay['shared_support_method_changes']=[method for method in sorted(set(old_support)&set(new_support))
            if method not in before and old_support[method]!=new_support[method]]
        replay['only_added_replay_methods_selected']=True
    record={'schema':'market_security_profile_payload/1','profile':profile,'source_commit':commit,
        'baseline_image_id':selected['image_id'],'baseline_commit':selected['label_commit'],'baseline_tree':selected['label_tree'],
        'files':files,'source_binding':binding,'source_overrides':overrides,'old_image_nonenv_config':observed['image']['config'],
        'old_public_packages':observed['public_installed_metadata']['packages'],'old_public_source_revision':selected['label_commit'],
        'historical':historical,'replay':replay,'dossier_sha256':DOSSIER[1],'stack_parity_sha256':STACK[1],
        'runtime_environment_read':False,'full_runtime_matrix_completed':False,'security_accepted':False,'deployment_eligible':False}
    market.write(output/'payload.json',encoded(record));return record


def capsule(repo,commit,target,historical_commit,market,source,*,modules=None,selected_ids=None):
    modules=tuple(modules or market.TEST_MODULES);selected={}
    for name in market.TEST_PATHS:
        entry,raw=public_blob(repo,historical_commit,name,market)
        selected[name]=(entry[0],raw,market.test_transform(name,raw),historical_commit,entry[1])
    path='deploy/market-data/Dockerfile';entry,raw=public_blob(repo,SECURITY_SOURCE,path,market)
    selected[path]=(entry[0],raw,raw,SECURITY_SOURCE,entry[1])
    collected=test_ids(selected,modules);ids=sorted(selected_ids) if selected_ids is not None else collected
    require(ids and set(ids)<=set(collected) and len(ids)==len(set(ids)),'profile_test_inventory')
    target.mkdir(mode=0o755);target.chmod(0o755);rows=[]
    for name,(mode,original,effective,origin,blob) in sorted(selected.items()):
        market.write(market.public_destination(target,name),effective,0o755 if mode=='100755' else 0o644)
        rows.append({'path':name,'mode':mode,'bytes':len(effective),'sha256':sha256(effective).hexdigest(),
            'original_sha256':sha256(original).hexdigest(),'git_blob':blob,'source_commit':origin,
            'transformation':'security_base_assertion_only' if original!=effective else 'none'})
    market.write(target/'public-baseline.json',source,0o644)
    proof={'schema':'market_security_profile_test_sources/1','source_commit':commit,'baseline_commit':historical_commit,
        'security_dockerfile_commit':SECURITY_SOURCE,'modules':list(modules),'expected_tests':len(ids),
        'test_ids':ids,'collected_test_ids':collected,'files':rows,'public_baseline_sha256':sha256(source).hexdigest(),
        'runtime_data_used':False}
    raw=encoded(proof);market.write(target/'test-sources.json',raw,0o644)
    return {'test_sources_sha256':sha256(raw).hexdigest(),'expected_tests':len(ids),'test_ids':ids,
        'collected_test_ids':collected,'baseline_commit':historical_commit,'modules':list(modules),
        'security_dockerfile_commit':SECURITY_SOURCE,'proof':proof}


# Program derived statically from sealed9131; only closed capsule parameters and
# exact historical/replay selections differ. The absolute fences are identical.
def candidate_test_program(record, market, *, replay=False, root_fixture=False):
    """Bind the full historical suite and product bytes before test imports."""
    product=record['files'];capsule=record['replay'] if replay else record['historical']
    require(type(replay) is bool and type(root_fixture) is bool and (not replay or not root_fixture) and capsule is not None,'profile_test_execution')
    count=capsule['expected_tests'];selected_count=count if replay else 1 if root_fixture else count-1
    require(type(root_fixture) is bool,'profile_test_execution')
    setup=("expected_proof="+repr(capsule['test_sources_sha256'])+"\nexpected_commit="+repr(record['source_commit'])+
           "\nexpected_product="+repr(product)+"\nroot_fixture="+repr(root_fixture)+
           "\nexpected_count="+repr(count)+"\nselected_count="+repr(selected_count)+"\nreplay="+repr(replay)+
           "\nexpected_baseline="+repr(capsule['baseline_commit'])+"\nexpected_modules="+repr(capsule['modules'])+
           "\ncollected_ids="+repr(capsule['collected_test_ids'])+"\nexpected_security_source="+repr(SECURITY_SOURCE)+
           "\nexpected_ids="+repr(capsule['test_ids'])+"\nroot_test="+repr(market.ROOT_FIXTURE_TEST_ID)+"\n")
    return setup+r'''
import ctypes,json,os,pathlib,resource,signal,socket,subprocess,sys,threading,types,unittest,urllib.request
from contextlib import ExitStack
from hashlib import sha1,sha256
from unittest.mock import patch
image=sys.argv[1]
assert os.getuid()==os.getgid()==(0 if root_fixture else 10001)
def read(path,maximum):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        before=os.fstat(fd)
        assert __import__('stat').S_ISREG(before.st_mode) and before.st_nlink==1 and not before.st_mode&0o022 and 0<=before.st_size<=maximum
        chunks=bytearray()
        while len(chunks)<=before.st_size:
            chunk=os.read(fd,min(65536,before.st_size+1-len(chunks)))
            if not chunk:break
            chunks.extend(chunk)
        identity=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        assert len(chunks)==before.st_size and identity(before)==identity(os.fstat(fd))==identity(path.lstat())
        return bytes(chunks)
    finally:os.close(fd)
evidence=pathlib.Path('/evidence');proof_raw=read(evidence/'test-sources.json',2097152)
assert sha256(proof_raw).hexdigest()==expected_proof
proof=json.loads(proof_raw)
assert proof['schema']=='market_security_profile_test_sources/1' and proof['source_commit']==expected_commit
assert proof['baseline_commit']==expected_baseline and proof['expected_tests']==expected_count and proof['runtime_data_used'] is False
assert proof['test_ids']==expected_ids and len(expected_ids)==len(set(expected_ids))==expected_count and (replay or root_test in expected_ids)
assert proof['modules']==expected_modules and proof['collected_test_ids']==collected_ids
rows={row['path']:row for row in proof['files']};assert len(rows)==len(proof['files'])
assert {p.relative_to(evidence).as_posix() for p in evidence.rglob('*') if p.is_file()}==set(rows)|{'test-sources.json','public-baseline.json'}
assert not any(p.is_symlink() for p in evidence.rglob('*'))
sources={}
for name,row in rows.items():
    raw=read(evidence/name,4194304)
    assert len(raw)==row['bytes'] and sha256(raw).hexdigest()==row['sha256']
    if row['transformation']=='security_base_assertion_only':
        assert name=='tests/test_market_pipeline_stage3_foundation.py'
        original=raw.replace(TEST_NEW,TEST_OLD,1)
        assert raw.count(TEST_NEW)==1 and original.count(TEST_OLD)==1
    else:
        assert row['transformation']=='none';original=raw
    assert sha256(original).hexdigest()==row['original_sha256']
    assert sha1(b'blob '+str(len(original)).encode()+b'\0'+original).hexdigest()==row['git_blob']
    assert row['source_commit']==(expected_security_source if name=='deploy/market-data/Dockerfile' else proof['baseline_commit'])
    if name.endswith('.py'):sources[name]=raw
assert sha256(read(evidence/'public-baseline.json',2097152)).hexdigest()==proof['public_baseline_sha256']
__PRODUCT_CHECK__
assert verify_product_files(pathlib.Path('/app'),expected_product,read)
sys.path[:0]=['/app','/opt/market-python','/evidence']
__TEST_GUARD__
with ExitStack() as fence:
    apply_test_fence(fence)
    package=types.ModuleType('scripts');package.__path__=[str(evidence/'scripts')];sys.modules['scripts']=package
    for name in ('scripts/manage_market_pipeline_stage3.py','scripts/audit_production_market_catchup.py','scripts/export_market_private_pipeline_schemas.py'):
        key=name[:-3].replace('/','.');module=types.ModuleType(key);module.__file__=str(evidence/name);sys.modules[key]=module
        exec(compile(sources[name],module.__file__,'exec'),module.__dict__)
        setattr(package,key.split('.')[-1],module)
    suite=unittest.TestSuite()
    for name in proof['modules']:
        path='tests/'+name+'.py';module=types.ModuleType('tests.'+name);module.__file__=str(evidence/path);sys.modules[module.__name__]=module
        exec(compile(sources[path],module.__file__,'exec'),module.__dict__)
        suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
    assert suite.countTestCases()==len(collected_ids)
    def cases(value):
        for test in value:
            if isinstance(test,unittest.TestSuite):yield from cases(test)
            else:yield test
    tests=list(cases(suite));assert sorted(test.id() for test in tests)==collected_ids
    selected=[test for test in tests if test.id() in expected_ids and (replay or (test.id()==root_test)==root_fixture)]
    assert len(selected)==selected_count
    selected_ids=sorted(test.id() for test in selected)
    results=[]
    def run_tests():
        results.append(unittest.TextTestRunner(verbosity=2,stream=sys.stderr).run(unittest.TestSuite(selected)))
    # asyncio's main-thread Runner installs SIGINT handlers. Keep the absolute
    # signal fence and use one owned ordinary test thread instead.
    worker=threading.Thread(target=run_tests,name='market-historical-fixtures',daemon=True)
    worker.start();worker.join(150)
    assert not worker.is_alive() and len(results)==1
    result=results[0]
ok=result.wasSuccessful() and not result.skipped and result.testsRun==selected_count
print(json.dumps({'schema':'market_security_profile_tests/1','image_id':image,'test_sources_sha256':expected_proof,'status':'passed' if ok else 'failed','tests':result.testsRun,'skips':len(result.skipped),'selected_test_ids':selected_ids,'scope':'capture_replay_regressions' if replay else 'root_owned_metadata_fixture' if root_fixture else 'nonroot_product_tests','execution_uid':os.getuid(),'execution_gid':os.getgid(),'suite_execution_thread':'owned_nonmain_test_thread','public_source_directory_mode':'0755','product_files_equal_baseline':True,'runtime_data_used':False,'security_accepted':False},sort_keys=True))
raise SystemExit(0 if ok else 1)
'''.replace('TEST_NEW',repr(market.TEST_NEW)).replace('TEST_OLD',repr(market.TEST_OLD)).replace('__TEST_GUARD__',market.TEST_GUARD_SOURCE).replace('__PRODUCT_CHECK__',market.PRODUCT_CHECK_SOURCE)
