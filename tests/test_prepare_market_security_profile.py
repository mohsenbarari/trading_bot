"""Ordinary public fixtures; all ambient execution/network is denied at import."""
from contextlib import ExitStack
from copy import deepcopy
from hashlib import sha256
import ast
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

FENCE_TARGETS=('subprocess.Popen','subprocess.run','os.system','os.kill','os.killpg','socket.socket',
    'socket.create_connection','socket.getaddrinfo','urllib.request.urlopen','urllib.request.OpenerDirector.open',
    'ctypes.CDLL','ctypes.PyDLL','signal.signal','signal.alarm','resource.setrlimit')
ROOT=Path(__file__).resolve().parents[1]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
with ExitStack() as _import_fence:
    for target in FENCE_TARGETS:
        _import_fence.enter_context(patch(target,side_effect=AssertionError('ambient_host_network_forbidden')))
    M=load('profile_preparer',ROOT/'scripts/prepare_market_security_profile.py')
    P=load('profile_original_preparer',ROOT/'scripts/prepare_market_security_payload.py')


class ProfilePreparationTests(unittest.TestCase):
    def setUp(self):
        self.fence=ExitStack();self.addCleanup(self.fence.close)
        for target in FENCE_TARGETS:
            self.fence.enter_context(patch(target,side_effect=AssertionError('ambient_host_network_forbidden')))
        self.dossier=M.document((ROOT/M.DOSSIER[0]).read_bytes());self.stack=M.document((ROOT/M.STACK[0]).read_bytes())

    def test_actual_closed_nine_metadata_and_scopes_match(self):
        for path,pin in (M.DOSSIER,M.STACK):self.assertEqual(sha256((ROOT/path).read_bytes()).hexdigest(),pin)
        for name in M.PROFILES:
            profile,actual=M.choose_profile(self.dossier,self.stack,name)
            self.assertEqual(actual['image_id'],profile['image_id']);self.assertTrue(actual['running'])
            self.assertEqual(len(actual['public_installed_metadata']['packages']),9)
            self.assertEqual(actual['public_source_revision']['expected_public_source_commit'],profile['label_commit'])

    def test_unknown_profile_and_forged_profile_set_refuse(self):
        with self.assertRaisesRegex(RuntimeError,'profile_unknown'):M.choose_profile(self.dossier,self.stack,'latest')
        for change in ('missing','duplicate','different'):
            value=deepcopy(self.dossier)
            if change=='missing':value['profiles'].pop()
            elif change=='duplicate':value['profiles'][0]=deepcopy(value['profiles'][1])
            else:value['profiles'][0]['image_id']='sha256:'+'1'*64
            with self.subTest(change=change),self.assertRaisesRegex(RuntimeError,'profile_dossier'):
                M.choose_profile(value,self.stack,M.PROFILES[0])

    def test_application_package_or_interpreter_drift_refuses(self):
        for field,value in (('packages',[{'name':'unexpected','version':'1'}]),('site_packages','/app')):
            stack=deepcopy(self.stack);q=next(q for q in stack['profiles'] if q['image_id'][7:19]==M.PROFILES[0])
            q['public_installed_metadata'][field]=value
            with self.subTest(field=field),self.assertRaisesRegex(RuntimeError,'profile_dependency_control'):
                M.choose_profile(self.dossier,stack,M.PROFILES[0])

    def test_launch_user_health_port_and_volume_drift_each_refuse(self):
        for key,value in (('User','0'),('Cmd',['unreviewed']),('Entrypoint',None),('Healthcheck',{'Test':['CMD','true']}),
                          ('ExposedPorts',{'99/tcp':{}}),('Volumes',{'/private':{}}),('WorkingDir','/root'),('ArgsEscaped',1)):
            stack=deepcopy(self.stack);q=next(q for q in stack['profiles'] if q['image_id'][7:19]==M.PROFILES[0])
            q['image']['config'][key]=value
            with self.subTest(key=key),self.assertRaisesRegex(RuntimeError,'profile_runtime_control'):
                M.choose_profile(self.dossier,stack,M.PROFILES[0])

    def test_source_revision_default_and_stopped_container_are_not_inferred(self):
        for key,value in (('running',False),('container_identity_stable',False)):
            stack=deepcopy(self.stack);q=next(q for q in stack['profiles'] if q['image_id'][7:19]==M.PROFILES[0]);q[key]=value
            with self.assertRaisesRegex(RuntimeError,'profile_stack_control'):M.choose_profile(self.dossier,stack,M.PROFILES[0])
        stack=deepcopy(self.stack);q=next(q for q in stack['profiles'] if q['image_id'][7:19]==M.PROFILES[0])
        q['public_source_revision']['expected_public_source_commit']='a'*40
        with self.assertRaisesRegex(RuntimeError,'profile_dependency_control'):M.choose_profile(self.dossier,stack,M.PROFILES[0])

    def test_no_env_projection_or_stronger_scope_claim_is_accepted(self):
        for key in ('private_container_env_read','old_installed_dependency_file_bytes_equivalence_asserted',
                    'application_imported','candidate_built','security_accepted'):
            stack=deepcopy(self.stack);stack[key]=True
            with self.subTest(key=key),self.assertRaisesRegex(RuntimeError,'profile_stack_control'):
                M.choose_profile(self.dossier,stack,M.PROFILES[0])
        stack=deepcopy(self.stack)
        for q in stack['profiles']:q['image']['config']['Env']=[]
        with self.assertRaisesRegex(RuntimeError,'profile_runtime_control'):M.choose_profile(self.dossier,stack,M.PROFILES[0])

    def patch_fixture(self):
        patch=(ROOT/'docs/security/evidence/2026-10-25-market-f79-public-foundation.patch').read_bytes()
        removed=[q[1:] for q in patch.splitlines(keepends=True)[3:] if q.startswith(b'-')]
        added=[q[1:] for q in patch.splitlines(keepends=True)[3:] if q.startswith(b'+')]
        prefix=b'# ordinary inert public line\n'*899;tail=b'# ordinary end\n';base=prefix+b''.join(removed)+tail
        expected=prefix+b''.join(added)+tail
        record={'base_sha256':sha256(base).hexdigest(),'patch_sha256':M.PATCH_SHA,
            'actual_bytes':len(expected),'actual_sha256':sha256(expected).hexdigest()}
        return base,patch,record,expected

    def test_actual_f79_zero_context_patch_replays_exactly_without_git_or_execution(self):
        base,patch,record,expected=self.patch_fixture()
        self.assertEqual(sha256(patch).hexdigest(),M.PATCH_SHA)
        self.assertEqual(M.patch_foundation(base,patch,record),expected)

    def test_f79_wrong_base_patch_or_result_refuses(self):
        base,patch,record,expected=self.patch_fixture()
        for args in ((base+b' ',patch,record),(base,patch+b' ',record),
                     (base,patch,{**record,'actual_sha256':'0'*64})):
            with self.assertRaises(RuntimeError):M.patch_foundation(*args)

    def test_f79_hunks_cannot_fuzz_or_reorder_the_observed_base(self):
        base,patch,record,_=self.patch_fixture();changed=base.replace(b'except (OSError',b'except (RuntimeError',1)
        with self.assertRaisesRegex(RuntimeError,'profile_patch_context'):
            M.patch_foundation(changed,patch,{**record,'base_sha256':sha256(changed).hexdigest()})

    def test_method_inventory_preserves_ids_and_identifies_shared_changes(self):
        before=b'class Example:\n def test_old(self):\n  assert True\n'
        after=b'class Example:\n def test_old(self):\n  assert 1\n def test_replay(self):\n  assert True\n'
        left,right=M.method_sources(before),M.method_sources(after)
        self.assertEqual(sorted(set(right)-set(left)),['Example.test_replay'])
        self.assertNotEqual(left['Example.test_old'],right['Example.test_old'])

    def test_historical_and_added_replay_programs_bind_disjoint_selections_and_no_site_fence_weakening(self):
        count=80;ids=[P.ROOT_FIXTURE_TEST_ID]+['tests.test_market_pipeline_stage4_capture.Example.test_'+str(n) for n in range(79)]
        ids=sorted(ids);historical={'expected_tests':count,'test_sources_sha256':'a'*64,'test_ids':ids,
            'collected_test_ids':ids,'baseline_commit':'b'*40,'modules':list(P.TEST_MODULES)}
        replay={'expected_tests':1,'test_sources_sha256':'c'*64,'test_ids':[ids[0]],'collected_test_ids':ids,
            'baseline_commit':M.CAPTURE_SOURCE,'modules':['test_market_pipeline_stage4_capture']}
        payload={'files':[],'source_commit':'d'*40,'historical':historical,'replay':replay}
        for kwargs,expected in (({},79),({'root_fixture':True},1),({'replay':True},1)):
            program=M.candidate_test_program(payload,P,**kwargs);tree=ast.parse(program)
            prefix=[]
            for node in tree.body:
                if not isinstance(node,ast.Assign):break
                prefix.append(node)
            scope={};exec(compile(ast.Module(body=prefix,type_ignores=[]),'<ordinary assignments>','exec'),scope)
            self.assertEqual(scope['expected_count'],1 if kwargs.get('replay') else 80)
            self.assertEqual(scope['selected_count'],expected)
            self.assertIn(P.TEST_GUARD_SOURCE,program);self.assertIn(P.PRODUCT_CHECK_SOURCE,program)
            self.assertIn('result.skipped',program);self.assertIn('owned_nonmain_test_thread',program)
            self.assertNotIn('==85',program);self.assertNotIn('else 84',program)
        with self.assertRaisesRegex(RuntimeError,'profile_test_execution'):
            M.candidate_test_program(payload,P,replay=True,root_fixture=True)

    def test_public_publication_is_readable_despite_private_umask_and_refuses_aliases(self):
        with tempfile.TemporaryDirectory(prefix='public-profile-preparation-') as directory:
            root=Path(directory);root.chmod(0o700);public=root/'product';public.mkdir(mode=0o755);public.chmod(0o755)
            previous=os.umask(0o077)
            try:
                path=P.public_destination(public,'core/market_intelligence/ordinary.py');P.write(path,b'# ordinary public file\n',0o644)
            finally:os.umask(previous)
            self.assertEqual(root.stat().st_mode&0o777,0o700);self.assertEqual(path.stat().st_mode&0o777,0o644)
            for q in path.parents:
                if q==root:break
                self.assertEqual(q.stat().st_mode&0o777,0o755)
            (public/'alias').symlink_to(public/'core',target_is_directory=True)
            with self.assertRaises(OSError):P.public_destination(public,'alias/ordinary.py')

    def test_json_duplicate_and_nonfinite_values_refuse(self):
        for raw in (b'{"a":1,"a":2}',b'{"a":NaN}',b'{} '*1024**2):
            with self.assertRaises(RuntimeError):M.document(raw)

    def source_fixture(self):
        product={'core/__init__.py':('100644',b'# ordinary\n'),
            'config/market_data_sources.v1.json':('100644',b'{}\n')}
        for root in sorted(P.COPY_ROOTS):product[root+'/ordinary.py']=('100644',b'# ordinary\n')
        for n in range(88):product['core/market_intelligence/file'+str(n)+'.py']=('100644',b'# inert '+str(n).encode()+b'\n')
        rows=[{'path':n,'bytes':len(b),'sha256':sha256(b).hexdigest(),'mode':'0644','uid':10001,'gid':10001}
            for n,(_,b) in product.items()]
        cache_path='core/market_intelligence/__pycache__/ordinary.cpython-312.pyc'
        rows.append({'path':cache_path,'bytes':16,'sha256':'f'*64,'mode':'0644','uid':10001,'gid':10001});rows.sort(key=lambda q:q['path'])
        selected=deepcopy(self.dossier['profiles'][0]);selected['cache_count']=1;selected['non_pyc_file_count']=93
        source={'schema':'market_security_public_source_fingerprint/1','image_id':selected['image_id'],
            'release_sha':selected['label_commit'],'release_tree':selected['label_tree'],'platform':'linux/amd64',
            'runtime_user':'10001:10001','stable_before_after':True,'mount_overlays_absent':True,
            'application_imported':False,'secrets_read':False,'runtime_changed':False,'container_id':'a'*64,
            'files':rows,'file_count':len(rows),'total_bytes':sum(q['bytes'] for q in rows),
            'fingerprint_sha256':sha256(P.encoded(rows)).hexdigest()}
        raw=M.encoded(source);selected['source']['sha256']=sha256(raw).hexdigest()
        cache={'schema':'market_security_public_cache_classification/1','image_id':selected['image_id'],
            'container_id':'a'*64,'source_manifest_sha256':selected['source']['sha256'],'python_version':[3,11,16],
            'stable_before_after':True,'all_standard_cache_paths_nonselected_or_stale':True,
            'scope':'normal_CPython_3_11_SourceFileLoader_cache_selection_and_header_rejection_only',
            **{q:False for q in ('explicit_bytecode_loader_reachability_assessed','cached_code_deserialized','cached_code_executed',
                                'application_imported','secrets_read','runtime_changed','security_accepted')},
            'cache_count':1,'caches':[{'path':cache_path,'bytes':16,'sha256':'f'*64,'classification':'nonselected_cache_tag'}]}
        cache_raw=M.encoded({'before_after_container_public_identity_equal':True,'classification':cache})
        selected['cache']['sha256']=sha256(cache_raw).hexdigest();return product,source,cache,selected,raw,cache_raw

    def test_complete_public_product_and_discarded_cache_controls_verify(self):
        product,source,cache,selected,raw,cache_raw=self.source_fixture()
        result=M.verify_source(raw,cache_raw,selected,product,P)
        self.assertEqual(result['observed_product_files'],93);self.assertEqual(result['observed_cache_files'],1)
        self.assertFalse(result['explicit_bytecode_loader_reachability_assessed']);self.assertFalse(result['security_accepted'])

    def test_source_cache_extra_file_type_and_execution_scope_drift_refuse(self):
        for mutation in ('source_bytes','extra_source','cache_selected','cache_executed','source_uid_bool','source_count_bool'):
            product,source,cache,selected,raw,cache_raw=self.source_fixture()
            if mutation=='source_bytes':product['core/__init__.py']=('100644',b'changed')
            if mutation=='extra_source':source['files'].append({**source['files'][0],'path':'core/market_intelligence/unexpected.py'})
            if mutation=='cache_selected':cache['caches'][0]['classification']='valid_selected_cache'
            if mutation=='cache_executed':cache['cached_code_executed']=True
            if mutation=='source_uid_bool':source['files'][0]['uid']=True
            if mutation=='source_count_bool':source['file_count']=True
            if mutation=='extra_source':
                source['files'].sort(key=lambda q:q['path']);source['file_count']=len(source['files'])
                source['total_bytes']=sum(q['bytes'] for q in source['files'])
                source['fingerprint_sha256']=sha256(P.encoded(source['files'])).hexdigest()
            raw=M.encoded(source);cache_raw=M.encoded({'before_after_container_public_identity_equal':True,'classification':cache})
            selected['source']['sha256']=sha256(raw).hexdigest();cache['source_manifest_sha256']=selected['source']['sha256']
            cache_raw=M.encoded({'before_after_container_public_identity_equal':True,'classification':cache})
            selected['cache']['sha256']=sha256(cache_raw).hexdigest()
            with self.subTest(mutation=mutation),self.assertRaises(RuntimeError):M.verify_source(raw,cache_raw,selected,product,P)


if __name__=='__main__':unittest.main()
