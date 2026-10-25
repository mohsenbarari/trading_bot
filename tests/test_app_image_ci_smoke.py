"""Hermetic fixed-image orchestration checks; no CI/Docker/network invocation."""
from contextlib import ExitStack,redirect_stdout
from copy import deepcopy
from hashlib import sha256
import ast
import importlib.util
import io
import json
import os
from pathlib import Path
import runpy
import stat
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
import zipfile

ROOT=Path(__file__).resolve().parents[1]
TARGETS=('subprocess.Popen','subprocess.run','subprocess.call','subprocess.check_call','subprocess.check_output',
    'os.system','os.popen','os.kill','os.killpg','socket.socket','socket.create_connection','socket.getaddrinfo',
    'urllib.request.urlopen','ctypes.CDLL','ctypes.PyDLL','signal.signal','signal.alarm','resource.setrlimit')


def fence(stack):
    for target in TARGETS:stack.enter_context(patch(target,side_effect=AssertionError('ambient operation denied')))


with ExitStack() as importing:
    fence(importing)
    spec=importlib.util.spec_from_file_location('_fixed_app_image_smoke',ROOT/'scripts/run_app_image_ci_smoke.py')
    M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)
    comparison_spec=importlib.util.spec_from_file_location('_actual_image_semantics',ROOT/'scripts/compose_market_security_os_candidate.py')
    COMPARISON=importlib.util.module_from_spec(comparison_spec);comparison_spec.loader.exec_module(COMPARISON)


class AppImageCISmokeTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close);fence(self.stack)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='owned-image-smoke-')))
        self.root.chmod(0o700);prior=os.umask(0o077);self.addCleanup(os.umask,prior)

    def private(self,name,raw):
        path=self.root/name;path.write_bytes(raw);path.chmod(0o600);return path

    def receipt(self):
        return {'schema':'app_image_ci_smoke/1','status':'passed','image_config_id':M.APP['image_config_id'],
            'runtime':{'expected':8,'run':8,'passed':8,'failures':[]},
            'components':{'expected':24,'run':24,'passed':24,'failures':[],'errors':0,'skipped':0},
            'production_config_guards_active':True,'app_source_masked':False,'real_provider_used':False,
            'security_accepted':False,'full_matrix_accepted':False}

    def comparison(self):return SimpleNamespace(oci_runtime_config=deepcopy,same_json_value=COMPARISON.same_json_value)

    def test_actual_public_app_identity_layers_and_artifact_are_fixed(self):
        self.assertEqual(M.APP['image_config_id'],'sha256:ed59f14519b7329306f334e05d8666f808ac2c37da78afa922ddd21c60f3cedd')
        self.assertEqual(M.APP['manifest_id'],'sha256:e477ada2bb698f1dfaf7a5db9d40777ac135fdda768d6df9789e67aaf3c7e494')
        self.assertEqual(len(M.APP['rootfs_layers']),33);self.assertEqual(M.APP['image_artifact_id'],12587747836)
        self.assertEqual(M.APP['image_zip_bytes'],183818338);self.assertFalse(M.APP['security_accepted']);self.assertFalse(M.APP['activation_allowed'])
        self.assertEqual(sha256(json.dumps(M.APP['rootfs_layers'],separators=(',',':')).encode()).hexdigest(),'f9b10901fa6b94f5b82d4a9514c15ac09b16088e87f0a8d5fbfab9f4e4e87991')

    def test_sealed_real_fd_refuses_same_bytes_replacement_hardlink_symlink_bad_mode_and_pin(self):
        raw=b'ordinary immutable public source';path=self.private('source.py',raw)
        with ExitStack() as stack:
            value,check=M.sealed(stack,path,sha256(raw).hexdigest(),64);self.assertEqual(value,raw)
            replacement=self.private('replacement',raw);os.replace(replacement,path)
            with self.assertRaisesRegex(RuntimeError,'source_changed'):check()
        os.link(path,self.root/'hardlink')
        with self.assertRaisesRegex(RuntimeError,'source_metadata'):M.sealed(self.stack,path,sha256(raw).hexdigest(),64)
        link=self.root/'symlink';link.symlink_to(path)
        with self.assertRaisesRegex(RuntimeError,'source_path'):M.sealed(self.stack,link,sha256(raw).hexdigest(),64)
        good=self.private('good',raw)
        with self.assertRaisesRegex(RuntimeError,'source_pin'):M.sealed(self.stack,good,'0'*64,64)
        good.chmod(0o644)
        with self.assertRaisesRegex(RuntimeError,'source_metadata'):M.sealed(self.stack,good,sha256(raw).hexdigest(),64)

    def test_real_non_hosted_environment_refuses_before_any_external_operation(self):
        with patch.dict(os.environ,{'GITHUB_ACTIONS':'false','RUNNER_ENVIRONMENT':'self-hosted'}),self.assertRaisesRegex(RuntimeError,'hosted_ci_only'):M.ci(self.root)

    def test_json_bounds_duplicates_nonfinite_and_private_receipts_refuse(self):
        for raw,reason in [(b'', 'json_bound'),(b'x'*(M.MAX_JSON+1),'json_bound'),(b'{"a":1,"a":2}','duplicate_json_key'),(b'{"n":NaN}','json_constant')]:
            with self.assertRaisesRegex(RuntimeError,reason):M.document(raw)
        for name in ('Config','Env','runtime_config','access_token','refresh_token','otp_code'):
            with self.assertRaisesRegex(RuntimeError,'private_output'):M.public({'nested':[{name:'ordinary-sensitive-fixture'}]})

    def test_receipt_requires_all_32_actual_tests_no_skips_no_guard_or_security_waiver(self):
        self.assertIs(M.validate_result(self.receipt())['security_accepted'],False)
        for section,key,value in [('runtime','run',7),('components','skipped',1),('components','errors',1),('components','errors',False)]:
            with self.subTest(section=section,key=key,value=value):
                bad=self.receipt();bad[section][key]=value
                with self.assertRaisesRegex(RuntimeError,'smoke_receipt|smoke_counter_types'):M.validate_result(bad)
        for key in ('production_config_guards_active','app_source_masked','real_provider_used','security_accepted','full_matrix_accepted'):
            bad=self.receipt();bad[key]=not bad[key]
            with self.assertRaisesRegex(RuntimeError,'smoke_receipt'):M.validate_result(bad)
        bad=self.receipt();bad['status']='failed'
        with self.assertRaisesRegex(RuntimeError,'smoke_receipt'):M.validate_result(bad)

    def fake_sms(self,body):
        import http.server
        tree=ast.parse(M.CAPSULE);node=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='FakeSMS')
        ns={'http':SimpleNamespace(server=http.server),'json':json,'need':M.need}
        exec(compile(ast.Module(body=[node],type_ignores=[]),'<owned-fake-sms>','exec'),ns)
        handler=ns['FakeSMS'].__new__(ns['FakeSMS']);raw=M.encode(body)
        handler.path='/v1/send/verify';handler.headers={'Content-Length':str(len(raw)),'X-API-KEY':'ci-public-fake-provider'}
        handler.rfile=io.BytesIO(raw);handler.wfile=io.BytesIO()
        handler.send_response=Mock();handler.send_header=Mock();handler.end_headers=Mock()
        return handler

    def test_fake_sms_protocol_uses_real_provider_response_shape_and_public_fixture_scope(self):
        handler=self.fake_sms({'mobile':'09120000000','templateId':123456,'parameters':[{'name':'CODE','value':'12345'}]})
        handler.do_POST();handler.send_response.assert_called_once_with(200)
        self.assertEqual(json.loads(handler.wfile.getvalue()),{'status':1,'data':{'messageId':1}})
        self.assertEqual(handler.records,[('09120000000','12345')])
        bad=self.fake_sms({'mobile':'outside-fixture','templateId':123456,'parameters':[{'name':'CODE','value':'12345'}]})
        with self.assertRaisesRegex(RuntimeError,'fake_sms_fixture_scope'):bad.do_POST()
        self.assertEqual(bad.wfile.getvalue(),b'')

    def test_fake_sms_refuses_oversized_body_before_read_and_wrong_provider_path(self):
        handler=self.fake_sms({'ordinary':'fixture'});handler.headers['Content-Length']='4097';handler.rfile=Mock()
        with self.assertRaisesRegex(RuntimeError,'fake_sms_request_bound'):handler.do_POST()
        handler.rfile.read.assert_not_called();handler.path='/v1/send/bulk';handler.headers['Content-Length']='10'
        with self.assertRaisesRegex(RuntimeError,'fake_sms_request_bound'):handler.do_POST()
        handler.rfile.read.assert_not_called()

    def test_public_scratch_configuration_has_real_role_and_no_auth_shortcut_or_provider(self):
        env=M.fixture_env('coin_intelligence_ci_0123456789ab')
        self.assertEqual(env['ENVIRONMENT'],'production');self.assertEqual(env['TRADING_BOT_SERVICE'],'api');self.assertEqual(env['SERVER_MODE'],'iran')
        for key in ('TELEGRAM_PROVIDER_TEST_AUTHORITY','STAGING_LOG_OTP_CODES','TELEGRAM_LOGIN_OTP_ENABLED','BACKGROUND_JOBS_ENABLED'):self.assertEqual(env[key],'false')
        self.assertEqual(env['SMSIR_BASE_URL'],'http://127.0.0.1:9080');self.assertNotIn('BOT_TOKEN',env)
        self.assertTrue(env['REDIS_URL'].endswith('/0'));self.assertTrue(env['STAGE6_TEST_REDIS_URL'].endswith('/1'))
        self.assertEqual(env['TRADING_BOT_EXPECTED_CHECKOUT'],'/app');self.assertEqual(env['TRADING_BOT_MIGRATION_MODE'],'scratch')
        with self.assertRaisesRegex(RuntimeError,'scratch_database_name'):M.fixture_env('trading_bot_db')

    def test_app_argv_has_no_source_mask_docker_socket_host_network_or_privileged_mode(self):
        ctx=SimpleNamespace(network='app-ci-net-123-1')
        with patch.dict(os.environ,{'GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1'}):
            args=M.container_args(ctx,'app-ci-api-123-1',M.APP['image_config_id'],['python','-I'],proof=self.root,output_limit=True)
        self.assertIn('fsize=2097153:2097153',args);self.assertIn('--security-opt=no-new-privileges',args);self.assertIn('--cap-drop=ALL',args)
        mount=args[args.index('--mount')+1];self.assertIn('target=/proof,readonly',mount)
        self.assertNotIn('target=/app',str(args));self.assertNotIn('docker.sock',str(args));self.assertNotIn('--privileged',args);self.assertNotIn('--publish',args)
        pg=M.container_args(ctx,'app-ci-pg-123-1','sha256:'+'1'*64,['postgres'])
        self.assertNotIn('fsize=2097153:2097153',pg)  # Real PostgreSQL WAL segments exceed2MiB.

    def test_create_records_unknown_intent_before_rpc_and_foreign_names_refuse(self):
        ctx=SimpleNamespace(created=[],network='app-ci-net-123-1')
        def unknown(args,**kwargs):self.assertEqual(ctx.created,[('app-ci-api-123-1',M.APP['image_config_id'])]);raise RuntimeError('named_unknown_rpc')
        ctx.docker=unknown
        with patch.dict(os.environ,{'GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1'}),self.assertRaisesRegex(RuntimeError,'named_unknown_rpc'):M.create(ctx,'app-ci-api-123-1',M.APP['image_config_id'],['python'])
        with self.assertRaisesRegex(RuntimeError,'container_name'):M.container_args(ctx,'trading_bot_app',M.APP['image_config_id'],[])

    def test_cleanup_removes_only_exact_owned_disposable_ids_then_empty_owned_network(self):
        calls=[];name='app-ci-api-123-1';image=M.APP['image_config_id'];cid='a'*64
        def docker(args,**kwargs):
            calls.append(args)
            if args[:2]==['container','ls']:return (cid+'\n').encode()
            if args[:2]==['container','inspect']:return M.encode({'name':'/'+name,'image':image,'owner':'123-1'})
            if args[:2]==['network','ls']:return b'app-ci-net-123-1\n'
            if args[:2]==['network','inspect']:return M.encode({'name':'app-ci-net-123-1','owner':'123-1','internal':True,'containers':{}})
            return b'ordinary owned removal\n'
        ctx=SimpleNamespace(created=[(name,image)],network='app-ci-net-123-1',docker=docker)
        with patch.dict(os.environ,{'GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1'}):self.assertEqual(M.cleanup(ctx),[])
        self.assertIn(['container','rm','--force',cid],calls);self.assertEqual(calls[-1],['network','rm','app-ci-net-123-1'])

    def test_cleanup_foreign_image_or_owner_never_removed_and_all_owned_clients_attempted(self):
        calls=[];names=['app-ci-api-123-1','app-ci-redis-123-1'];image=M.APP['image_config_id']
        def docker(args,**kwargs):
            calls.append(args)
            if args[:2]==['container','ls']:return ('a'*64+'\n').encode()
            if args[:2]==['container','inspect']:return M.encode({'name':'/'+names[1],'image':image,'owner':'foreign'})
            raise AssertionError('must not remove foreign resource')
        ctx=SimpleNamespace(created=[(n,image) for n in names],network=None,docker=docker)
        with patch.dict(os.environ,{'GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1'}):self.assertEqual(M.cleanup(ctx),['owned_container_cleanup_unproven'])
        self.assertEqual(sum(c[:2]==['container','ls'] for c in calls),2);self.assertFalse(any('rm' in c for c in calls))

    def test_cleanup_known_absent_after_unknown_create_needs_no_force_or_adoption(self):
        ctx=SimpleNamespace(created=[('app-ci-api-123-1',M.APP['image_config_id'])],network='app-ci-net-123-1',docker=Mock(return_value=b''))
        with patch.dict(os.environ,{'GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1'}):self.assertEqual(M.cleanup(ctx),[])
        self.assertEqual(ctx.docker.call_count,2);self.assertFalse(any('rm' in c.args[0] for c in ctx.docker.call_args_list))

    def test_cleanup_has_independent_finite_budget_after_main_deadline(self):
        ctx=M.Controller.__new__(M.Controller);ctx.end=-1;ctx.cleanup_end=None;ctx.checks=[];ctx.created=[];ctx.network=None
        with self.assertRaisesRegex(RuntimeError,'deadline'):ctx.check()
        self.assertEqual(M.cleanup(ctx),[]);ctx.check();self.assertIsNotNone(ctx.cleanup_end)

    def test_dependency_whole_config_and_layer_order_checked_in_ram(self):
        expected={'Env':['PUBLIC_FIXTURE=ordinary'],'Labels':{'ordinary':'fixture'}};layers=['sha256:'+'1'*64]
        image='sha256:'+'2'*64
        row={'id':image,'os':'linux','arch':'amd64','rootfs':{'Type':'layers','Layers':layers},'config':deepcopy(expected)}
        ctx=SimpleNamespace(docker=Mock(return_value=M.encode(row)),M=self.comparison())
        receipt=M.inspect_image(ctx,image,expected,layers);self.assertTrue(receipt['whole_config_compared_in_ram']);self.assertNotIn('config',receipt)
        row['config']['Env']=['CHANGED=ordinary'];ctx.docker.return_value=M.encode(row)
        with self.assertRaisesRegex(RuntimeError,'loaded_image_identity'):M.inspect_image(ctx,image,expected,layers)

    def test_actual_shared_eof_stream_resets_before_one_measured_classic_import_without_descriptor(self):
        source=io.BytesIO(b'ordinary bounded image TAR fixture');source.seek(0,2)
        image='sha256:'+'2'*64;expected={'Env':['PUBLIC_FIXTURE=ordinary']};layers=['sha256:'+'1'*64];calls=[]
        def docker(args,**kwargs):
            calls.append(args)
            if args[:2]==['image','ls']:return b'' if sum(x[:2]==['image','ls'] for x in calls)==1 else (image+'\n').encode()
            if args[:2]==['image','load']:
                self.assertEqual(kwargs['input_stream'].tell(),0);self.assertEqual(kwargs['input_stream'].read(),b'ordinary bounded image TAR fixture');return b'loaded fixture'
            return M.encode({'id':image,'os':'linux','arch':'amd64','rootfs':{'Type':'layers','Layers':layers},'config':expected})
        ctx=SimpleNamespace(docker=docker,M=self.comparison())
        actual,binding=M.load_exact(ctx,source,image,expected,layers,{image:{'kind':'Docker_config_identity'}})
        self.assertEqual(actual,image);self.assertEqual(binding['image_config_id'],image);self.assertNotIn('Descriptor',str(calls))

    def test_manifest_daemon_binding_uses_guarded_actual_archive_descriptor_and_rejects_bool(self):
        config_id='sha256:'+'1'*64;manifest='sha256:'+'2'*64;layers=['sha256:'+'3'*64];expected={'Env':['PUBLIC_FIXTURE=ordinary']}
        identifiers={config_id:{'kind':'Docker_config_identity'},manifest:{'kind':'OCI_manifest_to_config','size':123,'media_type':'application/vnd.oci.image.manifest.v1+json'}}
        row={'id':manifest,'os':'linux','arch':'amd64','rootfs':{'Type':'layers','Layers':layers},'config':expected}
        descriptor={'digest':manifest,'mediaType':'application/vnd.oci.image.manifest.v1+json','size':123}
        ctx=SimpleNamespace(docker=Mock(side_effect=[M.encode(row),M.encode(descriptor)]),M=self.comparison())
        result=M.inspect_image(ctx,manifest,expected,layers,config_id=config_id,identifiers=identifiers)
        self.assertEqual(result['daemon_image_id'],manifest);self.assertEqual(result['image_config_id'],config_id);self.assertIn('index . "Descriptor"',str(ctx.docker.call_args))
        descriptor['size']=True;ctx.docker.side_effect=[M.encode(row),M.encode(descriptor)]
        with self.assertRaisesRegex(RuntimeError,'loaded_descriptor'):M.inspect_image(ctx,manifest,expected,layers,config_id=config_id,identifiers=identifiers)

    def test_wrong_or_multiple_new_images_refuse_before_any_configuration_observation(self):
        image='sha256:'+'1'*64;unknown='sha256:'+'2'*64
        for after in ((unknown+'\n').encode(),(image+'\n'+unknown+'\n').encode()):
            calls=[]
            def docker(args,**kwargs):
                calls.append(args)
                if args[:2]==['image','ls']:return b'' if len(calls)==1 else after
                self.assertEqual(args[:2],['image','load']);return b'ordinary loaded fixture'
            ctx=SimpleNamespace(docker=docker,M=self.comparison())
            with self.assertRaisesRegex(RuntimeError,'image_import_delta'):M.load_exact(ctx,io.BytesIO(b'public fixture'),image,{},[],{image:{'kind':'Docker_config_identity'}})
            self.assertFalse(any('inspect' in c for c in calls))

    def test_full_config_comparison_rejects_nested_bool_int_and_int_float_aliases(self):
        image='sha256:'+'1'*64;layers=['sha256:'+'2'*64]
        for expected_value,actual_value in ((True,1),(1,True),(1,1.0),(0,False)):
            expected={'Healthcheck':{'Test':['CMD','ordinary'],'Retries':expected_value}}
            actual={'Healthcheck':{'Test':['CMD','ordinary'],'Retries':actual_value}}
            self.assertEqual(expected,actual)  # Python == alone would silently accept each mismatch.
            row={'id':image,'os':'linux','arch':'amd64','rootfs':{'Type':'layers','Layers':layers},'config':actual}
            ctx=SimpleNamespace(docker=Mock(return_value=M.encode(row)),M=self.comparison())
            with self.assertRaisesRegex(RuntimeError,'loaded_image_identity'):M.inspect_image(ctx,image,expected,layers)

    def test_unpack_real_zip_enforces_inventory_and_pin_before_expansion(self):
        raw_export=M.encode({'schema':'app_mountpoint_image_export/1','image_id':M.APP['image_config_id'],
            'archive':{'size_bytes':3,'uncompressed_bytes':10240,'sha256':sha256(b'abc').hexdigest()},
            'security_accepted':False,'deployment_eligible':False,'runtime_guards_changed':False})
        path=self.root/'public.zip'
        with zipfile.ZipFile(path,'w') as z:z.writestr('image-export.json',raw_export);z.writestr('candidate-image.tar.gz',b'abc')
        path.chmod(0o600);raw=path.read_bytes();folder=self.root/'unpacked';folder.mkdir(mode=0o700)
        spec={'image_zip_bytes':len(raw),'image_zip_sha256':sha256(raw).hexdigest(),'image_config_id':M.APP['image_config_id'],
            'gzip':json.loads(raw_export)['archive'],'export_bytes':len(raw_export),'export_sha256':sha256(raw_export).hexdigest()}
        from contextlib import contextmanager
        @contextmanager
        def pinned(p,maximum):
            with p.open('rb') as f:yield f,p.stat().st_size
        def digest(stream,size):return sha256(stream.read(size)).hexdigest()
        decode=Mock()
        ctx=SimpleNamespace(C=SimpleNamespace(pinned=pinned,stream_digest=digest),P=SimpleNamespace(decode_gzip=decode),capacity=Mock(),check=Mock())
        tar=M.unpack(ctx,spec,path,folder);self.assertEqual(tar,folder/'candidate-image.tar');self.assertEqual((folder/'candidate-image.tar.gz').read_bytes(),b'abc');decode.assert_called_once()
        spec['image_zip_sha256']='0'*64
        with self.assertRaisesRegex(RuntimeError,'image_zip_pin'):M.unpack(ctx,spec,path,folder)

    def test_fixed_capsule_compiles_and_keeps_real_application_and_provider_boundaries(self):
        tree=ast.parse(M.CAPSULE);compile(tree,'<fixed-public-capsule>','exec')
        calls=[ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n,ast.Call)]
        self.assertIn('main.app.router.lifespan_context',calls);self.assertIn('httpx.ASGITransport',calls);self.assertIn('subprocess.run',calls)
        self.assertFalse(any(c.startswith(('patch','Mock','AsyncMock')) for c in calls))
        self.assertNotIn('dependency_overrides[',M.CAPSULE);self.assertNotIn('create_access_token(',M.CAPSULE)
        self.assertIn('server.shutdown()',M.CAPSULE);self.assertIn('thread.join(timeout=5)',M.CAPSULE)
        self.assertIn("'upgrade','head'",M.CAPSULE);self.assertNotIn("'stamp'",M.CAPSULE)
        self.assertIn("select(func.count()).select_from(Message)",M.CAPSULE)
        self.assertIn('total==before_count+2',M.CAPSULE)

    def test_fixed_capsule_catalogue_covers_literal_failures_and_has_no_unbounded_capture(self):
        tree=ast.parse(M.CAPSULE)
        codes=next(ast.literal_eval(n.value.args[0]) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='CLOSED_CODES' for t in n.targets))
        literals={n.args[1].value for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='need' and len(n.args)==2 and isinstance(n.args[1],ast.Constant)}
        self.assertTrue(literals<=set(codes));self.assertIn('resource.RLIMIT_FSIZE',M.CAPSULE)
        self.assertNotIn('capture_output=True',M.CAPSULE);self.assertNotIn('stream=io.StringIO()',M.CAPSULE)

    def test_three_exact_selected_modules_match_real_public_sources_and_exclude_historical_56(self):
        self.assertEqual(sum(row[1] for row in M.SELECTORS.values()),24)
        for name,(class_name,count,pin) in M.SELECTORS.items():
            raw=(ROOT/'tests'/name).read_bytes();self.assertEqual(sha256(raw).hexdigest(),pin)
            tree=ast.parse(raw);cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==class_name)
            self.assertEqual(sum(isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name.startswith('test_') for n in cls.body),count)
        self.assertNotIn('test_chat_upload_image_security.py',M.SELECTORS);self.assertNotIn('test_jwt_codec_security.py',M.SELECTORS)

    def test_sealed_helper_pins_match_published_read_only_admission_sources(self):
        for name,(path,pin) in M.HELPERS.items():self.assertEqual(sha256((ROOT/path).read_bytes()).hexdigest(),pin,name)

    def controller(self):
        parent=self.root/'public-ci-parent';parent.mkdir(mode=0o700)
        root=parent/'security-app-ci-smoke-fixture';root.mkdir(mode=0o700)
        for name in ('source','reports','docker-config'):(root/name).mkdir(mode=0o700)
        for name,(relative,_) in M.HELPERS.items():
            target=root/'source'/name;target.write_bytes((ROOT/relative).read_bytes());target.chmod(0o600)
        self.assertEqual(os.geteuid(),1001,'run hosted ownership fixtures as the real UID1001 in a public scratch copy')
        self.stack.enter_context(patch.dict(os.environ,{'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'github-hosted',
            'GITHUB_REPOSITORY':'mohsenbarari/trading_bot','RUNNER_TEMP':str(parent),'GITHUB_SHA':'a'*40,
            'GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1','GH_TOKEN':'ordinary-public-fixture-token'}))
        return M.Controller(root,self.stack)

    def test_actual_hosted_uid_fixture_constructs_all_sealed_modules_without_external_effects(self):
        ctx=self.controller();ctx.check();self.assertEqual(set(ctx.modules),set(M.HELPERS))
        self.assertIs(ctx.P.PRIOR,ctx.M);self.assertIs(ctx.P.LEGACY,ctx.L)
        self.assertEqual(ctx.D.PROFILES['postgres']['config_id'],'sha256:49a811b28ed9289d4e981c8f6cd36e1cf05259653a858e40df0cb6d6413c9aa1')
        self.assertEqual(ctx.D.PROFILES['redis']['config_id'],'sha256:98c4c0abd245b4b65178494e4727c69c63a28ed604cfb7dbf74205203a987015')
        path=ctx.root/'source/compose_app_mountpoint_image.py';path.write_bytes(path.read_bytes()+b'\n')
        with self.assertRaisesRegex(RuntimeError,'source_changed'):ctx.check()

    def test_real_controller_delegates_only_bounded_commands_downloads_and_owned_public_outputs(self):
        ctx=self.controller();observed=[]
        def command(argv,**kw):observed.append((argv,kw));return b'ordinary bounded public result'
        with patch.object(ctx.C,'command',side_effect=command):
            self.assertEqual(ctx.command(['named-public-command']),b'ordinary bounded public result')
            ctx.command(['named-official-artifact-metadata'],auth=True,seconds=99999,maximum=128)
            ctx.docker(['image','ls'],maximum=256)
        self.assertEqual(observed[0][1]['environment'],{'DOCKER_CONFIG':str(ctx.root/'docker-config')})
        self.assertEqual(observed[1][1]['environment'],{'GH_TOKEN':'ordinary-public-fixture-token'})
        self.assertLess(observed[1][1]['seconds'],1800);self.assertEqual(observed[2][0][:2],M.DOCKER)
        with patch.object(ctx.C,'fetch_artifact') as fetch:ctx.download(123,ctx.root/'public.zip',42,'a'*64)
        fetch.assert_called_once_with(123,ctx.root/'public.zip',42,'a'*64)
        path=ctx.root/'reports/ordinary.json';ctx.write(path,{'public':'fixture'});self.assertEqual(json.loads(path.read_bytes()),{'public':'fixture'})
        with self.assertRaisesRegex(RuntimeError,'output_bound'):ctx.write(ctx.root/'too-large.json',{'public':'x'*M.MAX_JSON})
        with patch.object(M.os,'statvfs',return_value=SimpleNamespace(f_bavail=3000000,f_frsize=4096)):ctx.capacity(1024)
        with patch.object(M.os,'statvfs',return_value=SimpleNamespace(f_bavail=0,f_frsize=4096)),self.assertRaisesRegex(RuntimeError,'ci_capacity'):ctx.capacity(1024)

    def admission_context(self):
        from contextlib import contextmanager
        @contextmanager
        def pinned(path,maximum):
            with Path(path).open('rb') as source:yield source,Path(path).stat().st_size
        ctx=SimpleNamespace(root=self.root,end=time.monotonic()+20,write=Mock(),C=SimpleNamespace(pinned=pinned,read=lambda p,n:Path(p).read_bytes()),
            M=SimpleNamespace(oci_runtime_config=deepcopy))
        def download(identifier,path,size,pin):Path(path).write_bytes(b'ordinary pinned public artifact fixture')
        ctx.download=Mock(side_effect=download)
        return ctx

    def test_app_admission_binds_actual_directory_scope_config_and_tar_manifest_before_load(self):
        import tarfile
        app=deepcopy(M.APP);expected={'Labels':deepcopy(app['added_provenance_labels']),'Env':['PUBLIC_FIXTURE=ordinary']}
        app['runtime_config_semantic_sha256']=sha256(M.encode(expected)[:-1]).hexdigest()
        ctx=self.admission_context();tar=self.root/'ordinary-image.tar'
        with tarfile.open(tar,'w') as archive:
            member=tarfile.TarInfo('blobs/sha256/'+app['manifest_id'][7:]);member.size=3;archive.addfile(member,io.BytesIO(b'abc'))
        app['gzip']['uncompressed_bytes']=tar.stat().st_size
        proof={'archive_sha256':app['tar_sha256'],'layers':33,'physical_graph_closed':True,'oci_manifest_ids':[app['manifest_id']],
            'config':{'rootfs':{'type':'layers','diff_ids':app['rootfs_layers']},'config':expected}}
        ctx.P=SimpleNamespace(validate_archive=Mock(side_effect=lambda *a,**k:deepcopy(proof)))
        def load(c,source,image,config,layers,identifiers):
            self.assertEqual(image,app['image_config_id']);self.assertEqual(config,expected)
            self.assertEqual(identifiers[app['manifest_id']]['size'],3);self.assertFalse(source.closed)
            return image,{'whole_config_compared_in_ram':True}
        with patch.object(M,'APP',app),patch.object(M,'unpack',return_value=tar),patch.object(M,'load_exact',side_effect=load):self.assertEqual(M.admit_app(ctx),app['image_config_id'])
        receipt=ctx.write.call_args.args[1];self.assertNotIn('config',receipt['archive']);self.assertTrue(receipt['smoke_only'])
        proof['physical_graph_closed']=False
        # Use a new owned namespace for the failed admission; previous folder is never adopted.
        ctx.root=self.root/'failed';ctx.root.mkdir()
        with patch.object(M,'APP',app),patch.object(M,'unpack',return_value=tar),self.assertRaisesRegex(RuntimeError,'app_archive_identity'):M.admit_app(ctx)

    def test_dependency_admission_selects_only_exact_public_evidence_digest_and_image_profile(self):
        ctx=self.admission_context();image='sha256:'+'1'*64;layers=['sha256:'+'2'*64]
        spec={'run_id':123,'evidence_bytes':7,'evidence_sha256':'a'*64,'image_artifact_id':456,'image_zip_bytes':9,'image_zip_sha256':'b'*64,
            'config_id':image,'layers':layers,'archive':{'uncompressed_bytes':10240}}
        tar=self.private('ordinary-dependency.tar',b'public');identity={image:{'kind':'Docker_config_identity'}}
        ctx.D=SimpleNamespace(PROFILES={'postgres':spec},evidence=Mock(return_value={'image':{'Config':{'Env':['PUBLIC_FIXTURE=ordinary']}}}),
            validate_archive=Mock(return_value={'source_identities':identity,'physical_graph_closed':True}))
        ctx.T,ctx.L=object(),object()
        ctx.command=Mock(return_value=M.encode({'artifacts':[{'id':42,'size_in_bytes':7,'digest':'sha256:'+'a'*64,'expired':False}]}))
        with patch.object(M,'unpack',return_value=tar),patch.object(M,'load_exact',return_value=(image,{'whole_config_compared_in_ram':True})):
            self.assertEqual(M.admit_dependency(ctx,'postgres'),image)
        self.assertEqual(ctx.download.call_args_list[0].args[0],42);self.assertEqual(ctx.download.call_args_list[1].args[0],456)
        ctx.root=self.root/'bad-evidence';ctx.root.mkdir();ctx.command.return_value=M.encode({'artifacts':[{'id':42,'size_in_bytes':7,'digest':'sha256:'+'c'*64,'expired':False}]})
        with self.assertRaisesRegex(RuntimeError,'dependency_evidence_identity'):M.admit_dependency(ctx,'postgres')

    def test_prepare_proof_copies_only_three_pinned_tests_and_explicit_synthetic_environment(self):
        source=self.root/'source';source.mkdir(mode=0o700)
        for name,(_,_,pin) in M.SELECTORS.items():self.private('source/'+name,(ROOT/'tests'/name).read_bytes())
        writes=[]
        def write(path,raw,mode=0o600):path.write_bytes(raw);path.chmod(mode);writes.append(path)
        ctx=SimpleNamespace(root=self.root,stack=self.stack,checks=[],C=SimpleNamespace(write=write))
        proof=M.prepare_proof(ctx,'coin_intelligence_ci_0123456789ab')
        self.assertEqual(set(p.name for p in proof.iterdir()),set(M.SELECTORS)|{'selectors.json','runtime.env'})
        self.assertTrue(all(stat.S_IMODE(p.stat().st_mode)==0o444 for p in writes));self.assertEqual(len(ctx.checks),3)

    def execute_context(self,mounts=None,success=True,readiness_failures=0):
        result=self.receipt();raw=M.encode(result);calls=[];remaining=[readiness_failures]
        def docker(args,**kw):
            calls.append((args,kw))
            if args[:2]==['exec','pg-fixture'] and remaining[0]:remaining[0]-=1;raise RuntimeError('command_failed')
            if args[:2]==['exec','redis-fixture']:return b'PONG\n'
            if args[:2]==['container','inspect']:return M.encode(mounts if mounts is not None else self.mount_metadata())
            if args[:2]==['start','--attach']:
                path=kw['public_log'];path.write_bytes(raw);path.chmod(0o600)
                if not success:raise RuntimeError('command_failed')
                return raw
            if args[:2]==['inspect','--format']:return b'0\n'
            return b'ordinary fixture acknowledgement\n'
        ctx=SimpleNamespace(root=self.root,network=None,write=Mock(),docker=Mock(side_effect=docker),C=SimpleNamespace(read=lambda p,n:p.read_bytes()),M=self.comparison())
        return ctx,calls

    def mount_metadata(self):
        return {'mounts':[{'Type':'bind','Destination':'/proof','Source':str(self.root/'proof'),'RW':False}],
            'tmpfs':{'/tmp':'rw,nosuid,nodev,noexec,size=128m,mode=1777'}}

    def execute_patches(self,ctx):
        self.stack.enter_context(patch.object(M,'admit_app',return_value=M.APP['image_config_id']))
        self.stack.enter_context(patch.object(M,'admit_dependency',side_effect=['sha256:'+'1'*64,'sha256:'+'2'*64]))
        self.stack.enter_context(patch.object(M,'prepare_proof',return_value=self.root/'proof'))
        self.stack.enter_context(patch.object(M,'create',side_effect=['pg-fixture','redis-fixture','api-fixture']))
        self.stack.enter_context(patch.dict(os.environ,{'GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1'}))

    def test_complete_named_disposable_orchestration_requires_real_receipt_and_never_masks_app(self):
        ctx,calls=self.execute_context();self.execute_patches(ctx)
        self.assertEqual(M.execute(ctx)['components']['run'],24)
        self.assertIn(['network','create','--internal','--label',M.LABEL+'=123-1','app-ci-net-123-1'],[a for a,_ in calls])
        self.assertEqual(ctx.write.call_args.args[0],self.root/'reports/actual-smoke.json')

    def test_postgres_official_setup_client_and_server_share_owned_standard_socket_without_pghost_workaround(self):
        # Exact admitted entrypoint SHA9c440299ae04a0a79d55b8bf03307036d890a40979d2fb698073c9050d4b20a5:
        # docker_temp_server_start preserves -c options and disables TCP, while
        # docker_process_sql explicitly clears PGHOST and PGHOSTADDR before psql.
        ctx,calls=self.execute_context();self.execute_patches(ctx);M.execute(ctx)
        args,kwargs=M.create.call_args_list[0].args,M.create.call_args_list[0].kwargs
        self.assertEqual(args[0],ctx);self.assertEqual(args[1],'app-ci-pg-123-1')
        self.assertEqual(args[3][:3],['postgres','-c','unix_socket_directories=/var/run/postgresql'])
        self.assertNotIn('unix_socket_directories=/tmp',args[3])
        self.assertEqual(kwargs['user'],'postgres')
        self.assertIn('/var/run/postgresql:rw,nosuid,nodev,size=16m,uid=70,gid=70,mode=0775',kwargs['tmpfs'])
        self.assertFalse({'PGHOST','PGHOSTADDR'}&set(kwargs['env']))
        launch=M.container_args(ctx,args[1],args[2],args[3],**kwargs)
        self.assertIn('--cap-drop=ALL',launch);self.assertIn('--security-opt=no-new-privileges',launch)
        self.assertNotIn('--entrypoint',launch);self.assertEqual(launch[-len(args[3]):],args[3])

    def test_dependency_readiness_retries_are_finite_and_failed_capsule_receipt_is_preserved(self):
        ctx,calls=self.execute_context(success=False,readiness_failures=1);self.execute_patches(ctx)
        with patch.object(M.time,'sleep') as sleep,self.assertRaisesRegex(RuntimeError,'smoke_container_exit'):M.execute(ctx)
        sleep.assert_called_once_with(1);self.assertEqual(ctx.write.call_args.args[0],self.root/'reports/actual-smoke.json')
        self.assertEqual(ctx.write.call_args.args[1]['runtime']['run'],8)

    def test_product_source_overlay_refuses_before_starting_the_application(self):
        ctx,calls=self.execute_context(mounts=[{'Destination':'/app'},{'Destination':'/tmp'}]);self.execute_patches(ctx)
        with self.assertRaisesRegex(RuntimeError,'installed_app_must_not_be_masked'):M.execute(ctx)
        self.assertFalse(any(a[:2]==['start','--attach'] for a,_ in calls))

    def test_actual_moby_bind_and_legacy_tmpfs_metadata_are_distinct_and_source_is_exact(self):
        value=self.mount_metadata()
        proof=M.validate_app_mounts(value,self.root/'proof',self.comparison())
        self.assertEqual(proof,{'scope':'owned_hosted_mount_metadata_only','bind_mounts':1,'tmpfs_mounts':1,
            'proof_readonly':True,'proof_source_bound':True,'app_mounts':0,'security_accepted':False})
        self.assertNotIn(str(self.root),M.encode(proof).decode());M.public(proof)
        ctx,calls=self.execute_context();self.execute_patches(ctx);M.execute(ctx)
        self.assertTrue(any('HostConfig.Tmpfs' in args[3] and '.Mounts' in args[3]
            for args,_ in calls if args[:3]==['container','inspect','--format']))
        receipts={call.args[0].name:call.args[1] for call in ctx.write.call_args_list}
        self.assertEqual(receipts['app-mount-admission.json'],proof)

    def test_hosted_mount_admission_refuses_missing_extra_mutable_foreign_or_app_mounts(self):
        variants=[]
        for key,bad in [('Type','volume'),('Source',str(self.root/'foreign')),('Destination','/app'),
                        ('Destination','/app/core'),('Destination','/tmp'),('RW',True),('RW',0),('RW',0.0),('RW',None)]:
            value=self.mount_metadata();value['mounts'][0][key]=bad;variants.append(value)
        for bad in ([],[{}],[None],self.mount_metadata()['mounts']+[{'Type':'bind','Destination':'/app/models'}]):
            value=self.mount_metadata();value['mounts']=bad;variants.append(value)
        for bad in (None,[],{},True,{'/tmp':True},{'/tmp':0},{'/tmp':128},
                    {'/tmp':'rw,nosuid,nodev,noexec,size=256m,mode=1777'},
                    {'/tmp':'rw,nosuid,nodev,noexec,size=128m,mode=1777','/app':'rw'}):
            value=self.mount_metadata();value['tmpfs']=bad;variants.append(value)
        variants.extend([[],None,{'mounts':self.mount_metadata()['mounts']},
            {**self.mount_metadata(),'arbitrary_extra_key':False}])
        for value in variants:
            with self.subTest(value=value),self.assertRaisesRegex(RuntimeError,'installed_app_must_not_be_masked'):
                M.validate_app_mounts(value,self.root/'proof',self.comparison())
        # An actual extra product mount is refused before capsule start; inspection
        # success and image identity alone cannot establish an unmasked source tree.
        value=self.mount_metadata();value['mounts'].append({'Type':'bind','Destination':'/app'})
        ctx,calls=self.execute_context(mounts=value);self.execute_patches(ctx)
        with self.assertRaisesRegex(RuntimeError,'installed_app_must_not_be_masked'):M.execute(ctx)
        self.assertFalse(any(args[:2]==['start','--attach'] for args,_ in calls))

    def test_main_success_primary_failure_and_secondary_cleanup_failure_are_distinct(self):
        for primary,cleanup in ((False,[]),(True,[]),(False,['owned_container_cleanup_unproven']),(True,['owned_container_cleanup_unproven'])):
            ctx=SimpleNamespace(root=self.root,write=Mock(),check=Mock())
            with self.subTest(primary=primary,cleanup=cleanup),patch.object(M,'Controller',return_value=ctx), \
                 patch.object(M,'execute',side_effect=RuntimeError('named_primary_failure') if primary else None,return_value=self.receipt()), \
                 patch.object(M,'cleanup',return_value=cleanup),redirect_stdout(io.StringIO()) as output:
                self.assertEqual(M.main(['--root',str(self.root)]),1 if primary or cleanup else 0)
            rows=[c.args[1] for c in ctx.write.call_args_list]
            self.assertEqual(rows[0]['primary_failure_retained'],primary)
            self.assertEqual(json.loads(output.getvalue())['security_accepted'],False)
        with patch.object(M,'Controller',side_effect=RuntimeError('early_source_refusal')),redirect_stdout(io.StringIO()):self.assertEqual(M.main(['--root',str(self.root)]),1)

    def test_real_cli_default_outside_hosted_ci_fails_closed_without_effects(self):
        with patch.dict(os.environ,{'GITHUB_ACTIONS':'false'}),patch.object(__import__('sys'),'argv',['run_app_image_ci_smoke.py','--root',str(self.root)]),redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(SystemExit) as exit:runpy.run_path(str(ROOT/'scripts/run_app_image_ci_smoke.py'),run_name='__main__')
        self.assertEqual(exit.exception.code,1);self.assertEqual(json.loads(output.getvalue())['status'],'fixed_image_smoke_failed')

    def test_datastore_zip_uses_real_strict_export_validator_before_gzip_expansion(self):
        ctx=self.controller();folder=ctx.root/'synthetic-datastore';folder.mkdir()
        archive={'format':'docker-archive+gzip','name':'candidate-image.tar.gz','size_bytes':3,'uncompressed_bytes':10240,'sha256':sha256(b'abc').hexdigest()}
        spec={'archive':archive,'config_id':'sha256:'+'1'*64,'profile':'public_fixture','candidate_sha256':'a'*64,
              'raw_counts':{'CRITICAL':0,'HIGH':0,'MEDIUM':0,'LOW':0,'UNKNOWN':0}}
        value={k:None for k in ('gosu_dependency_security_overlay','gosu_windows_advisory_scope','libc_vendor_security_revision','parent_config_id','parent_reference')}
        value.update(schema='alpine_datastore_security_export/1',status='exported_not_accepted',image_id=spec['config_id'],profile=spec['profile'],
            candidate_receipt_sha256='a'*64,full_role_matrix='NOT_TESTED',production_data_used=False,production_installed=False,
            scanner_gate_waived=False,security_accepted=False,raw_counts=spec['raw_counts'],archive=archive,
            reports_sha256={'ordinary.json':'b'*64},evidence_sha256={'ordinary.json':'b'*64})
        raw=M.encode(value);path=ctx.root/'dependency.zip'
        with zipfile.ZipFile(path,'w') as z:z.writestr('image-export.json',raw);z.writestr('candidate-image.tar.gz',b'abc')
        path.chmod(0o600);spec.update(image_zip_bytes=path.stat().st_size,image_zip_sha256=sha256(path.read_bytes()).hexdigest(),export_bytes=len(raw),export_sha256=sha256(raw).hexdigest())
        with patch.object(ctx,'capacity'),patch.object(ctx.P,'decode_gzip') as decoder:M.unpack(ctx,spec,path,folder)
        decoder.assert_called_once();self.assertEqual((folder/'candidate-image.tar.gz').read_bytes(),b'abc')

    def test_successful_create_binds_returned_id_and_invalid_id_never_becomes_owned_receipt(self):
        ctx=SimpleNamespace(created=[],network='app-ci-net-123-1',docker=Mock(return_value=('a'*64+'\n').encode()))
        with patch.dict(os.environ,{'GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1'}):
            self.assertEqual(M.create(ctx,'app-ci-api-123-1',M.APP['image_config_id'],['python']),'a'*64)
            ctx.docker.return_value=b'not-a-container-id'
            with self.assertRaisesRegex(RuntimeError,'container_identity'):M.create(ctx,'app-ci-api-123-1',M.APP['image_config_id'],['python'])

    def test_nonempty_or_foreign_network_never_removed_and_reports_cleanup_unknown(self):
        calls=[]
        def docker(args,**kwargs):
            calls.append(args)
            if args[:2]==['network','ls']:return b'app-ci-net-123-1\n'
            return M.encode({'name':'app-ci-net-123-1','owner':'foreign','internal':True,'containers':{'ordinary':'foreign'}})
        ctx=SimpleNamespace(created=[],network='app-ci-net-123-1',docker=docker)
        with patch.dict(os.environ,{'GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1'}):self.assertEqual(M.cleanup(ctx),['owned_network_cleanup_unproven'])
        self.assertFalse(any('rm' in c for c in calls))

    def test_failure_evidence_write_loss_still_returns_closed_failed_receipt(self):
        def write(path,value):
            if path.name=='failure.json':raise OSError('ordinary public fixture write loss')
        ctx=SimpleNamespace(root=self.root,write=Mock(side_effect=write),check=Mock())
        with patch.object(M,'Controller',return_value=ctx),patch.object(M,'execute',side_effect=RuntimeError('named_primary_failure')), \
             patch.object(M,'cleanup',return_value=[]),redirect_stdout(io.StringIO()) as output:
            self.assertEqual(M.main(['--root',str(self.root)]),1)
        self.assertEqual(json.loads(output.getvalue())['status'],'fixed_image_smoke_failed')

    def test_failure_subphase_is_closed_and_unknown_values_cannot_be_persisted(self):
        M.subphase('postgres','metadata');self.assertEqual(M.SUBPHASE,'postgres_metadata')
        for profile,stage in [('private-profile','metadata'),('postgres','private-stage')]:
            with self.assertRaisesRegex(RuntimeError,'diagnostic_subphase'):M.subphase(profile,stage)
        self.assertEqual(M.SUBPHASE,'postgres_metadata')

    def test_closed_failure_diagnostic_uses_only_catalogues_and_whitelisted_class_names(self):
        ctx=SimpleNamespace(modules={'sealed_helper':SimpleNamespace(PUBLIC_FAILURE_CODES=('command_timeout',),PUBLIC_CODES=('archive_identity',))})
        M.subphase('postgres','evidence_admission')
        self.assertEqual(M.failure_diagnostic(RuntimeError('command_timeout'),ctx),{'closed_code':'command_timeout',
            'exception_class':'RuntimeError','subphase':'postgres_evidence_admission'})
        self.assertEqual(M.failure_diagnostic(RuntimeError('capacity_floor'),None)['closed_code'],'unexpected_failure')
        for error in (ValueError('ordinary-private-fixture'),RuntimeError({'Env':'ordinary-private-fixture'}),KeyError('ordinary-private-fixture')):
            value=M.failure_diagnostic(error,ctx);self.assertNotIn('ordinary-private-fixture',M.encode(value).decode());self.assertEqual(value['closed_code'],'unexpected_failure')
        class PrivateFailure(Exception):pass
        self.assertEqual(M.failure_diagnostic(PrivateFailure('ordinary-private-fixture'),ctx)['exception_class'],'unclassified_exception')
        self.assertEqual(M.failure_diagnostic(RuntimeError('source_changed'),None)['closed_code'],'source_changed')

    def test_failed_metadata_request_retains_exact_subphase_without_any_download_retry(self):
        ctx=SimpleNamespace(root=self.root,D=SimpleNamespace(PROFILES={'postgres':{'run_id':40142076769}}),
            command=Mock(side_effect=RuntimeError('command_timeout')),download=Mock())
        with self.assertRaisesRegex(RuntimeError,'command_timeout'):M.admit_dependency(ctx,'postgres')
        self.assertEqual(M.SUBPHASE,'postgres_metadata');ctx.download.assert_not_called();self.assertEqual(ctx.command.call_count,1)

    def test_startup_classification_emits_only_closed_markers_and_bounds_before_matching(self):
        markers={
            'postgres':[(b'No space left on device','filesystem_capacity_marker'),(b'Permission denied','filesystem_permission_marker'),
                (b'Read-only file system','filesystem_readonly_marker'),
                (b'Database is uninitialized and superuser password is not specified','postgres_password_required_marker'),
                (b'database files are incompatible with server','postgres_data_version_marker'),
                (b'initdb: error:','postgres_initdb_error_marker'),(b'FATAL:','postgres_fatal_marker')],
            'redis':[(b"Fatal error, can't open config file",'redis_config_marker'),
                (b'Failed listening on port','redis_listen_marker'),(b'FATAL CONFIG FILE ERROR','redis_config_marker')]}
        for profile,rows in markers.items():
            for marker,expected in rows:
                with self.subTest(profile=profile,marker=marker):
                    self.assertEqual(M.startup_classification(profile,b'ordinary-private-value '+marker),expected)
        self.assertEqual(M.startup_classification('postgres',b'ordinary-private-value'),'unclassified_startup')
        for raw in ('private text',b'x'*8193):
            with self.assertRaisesRegex(RuntimeError,'dependency_startup_bound'):M.startup_classification('postgres',raw)
        with self.assertRaisesRegex(RuntimeError,'diagnostic_subphase'):M.startup_classification('private-profile',b'')

    def test_comprehensive_startup_tokens_classify_shell_client_and_server_errors_without_echoing_values(self):
        examples=[('missing_file','No such file or directory'),('operation_not_permitted','Operation not permitted'),
            ('permission_denied','Permission denied'),('readonly_filesystem','Read-only file system'),
            ('filesystem_capacity','No space left on device'),('invalid_option','illegal option'),
            ('invalid_argument','unrecognized configuration parameter'),('unknown_user','user ordinary-private-user does not exist'),
            ('command_not_found','ordinary-private-command: not found'),('shared_memory','could not create shared memory'),
            ('unix_socket','connection to server on socket ordinary-private-path'),('connection_failure','connection refused'),
            ('invalid_locale','invalid locale'),('directory_not_empty','is not empty'),
            ('configuration_file','could not access the server configuration file'),('shell_syntax','syntax error'),
            ('cannot_execute','exec format error'),('resource_unavailable','cannot allocate memory'),
            ('initdb_error','initdb: error:'),('postgres_fatal','FATAL:'),
            ('password_required','Database is uninitialized and superuser password is not specified'),
            ('data_version','database files are incompatible with server')]
        for expected,text in examples:
            with self.subTest(expected=expected):
                raw=('psql: '+text+' ordinary-private-value').encode();value=M.startup_tokens(raw)
                self.assertEqual(value['bytes'],len(raw));self.assertFalse(value['empty']);self.assertEqual(value['line_count'],1)
                self.assertEqual(value['closed_lines'][0]['process'],'psql');self.assertIn(expected,value['closed_lines'][0]['markers'])
                self.assertNotIn('ordinary-private',M.encode(value).decode());M.public(value)
        raw=b'psql: error: connection to server on socket ordinary-private-path failed: No such file or directory'
        self.assertEqual(M.startup_tokens(raw)['closed_lines'][0]['markers'],['missing_file','unix_socket','connection_failure'])

    def test_startup_tokens_limit_sequence_and_accept_only_closed_process_prefixes(self):
        for process in ('bash','sh','ls','mkdir','chmod','chown','find','initdb','postgres','psql','pg_ctl',
                        'pg_isready','id','locale','getent','docker-entrypoint.sh','redis-server','redis-cli'):
            value=M.startup_tokens(('/ordinary-private-path/'+process+': ordinary-private-body').encode())
            self.assertEqual(value['closed_lines'],[{'line':0,'process':process,'markers':[]}])
        self.assertEqual(M.startup_tokens(b'ordinary-private-process: ordinary-private-body')['closed_lines'],
            [{'line':0,'process':'unclassified','markers':[]}])
        empty=M.startup_tokens(b'');self.assertTrue(empty['empty']);self.assertEqual(empty['bytes'],0);self.assertEqual(empty['closed_lines'],[])
        full=M.startup_tokens(b'x\n'*4096);self.assertEqual(full['bytes'],8192);self.assertEqual(full['line_count'],4096)
        self.assertEqual(len(full['closed_lines']),64);self.assertEqual(full['additional_lines'],4032)
        for raw in ('ordinary-private-text',b'x'*8193):
            with self.assertRaisesRegex(RuntimeError,'dependency_startup_bound'):M.startup_tokens(raw)

    def state(self):
        return {'running':False,'status':'exited','exit_code':1,'oom_killed':False,'restarting':False,'dead':False}

    def test_readiness_diagnostics_keep_typed_state_and_never_persist_log_text_or_exception_args(self):
        def docker(args,**kwargs):
            if args[:2]==['container','inspect']:return M.encode(self.state())
            self.assertEqual(args[:3],['logs','--tail','40'])
            self.assertEqual(kwargs,{'seconds':10,'maximum':8192,'combine_stderr':True})
            return b'ordinary-private-value Permission denied'
        ctx=SimpleNamespace(root=self.root,docker=Mock(side_effect=docker),write=Mock(),modules={})
        M.subphase('postgres','readiness')
        M.readiness_failure(ctx,{'postgres':'pg-fixture','redis':'redis-fixture'},'postgres',RuntimeError('ordinary-private-exception'))
        path,value=ctx.write.call_args.args
        self.assertEqual(path,self.root/'reports/dependency-readiness.json')
        self.assertEqual(value['failed_service'],'postgres');self.assertFalse(value['security_accepted'])
        self.assertEqual(value['probe_failure']['closed_code'],'unexpected_failure')
        self.assertEqual(value['probe_failure']['subphase'],'postgres_readiness')
        for row in value['services'].values():
            self.assertEqual(row['state'],self.state());self.assertEqual(row['state_observation'],'observed')
            self.assertEqual(row['startup_classification'],'filesystem_permission_marker')
        M.public(value);self.assertNotIn('ordinary-private',M.encode(value).decode())

    def test_readiness_diagnostics_refuse_malformed_state_but_attempt_every_owned_service_and_logs(self):
        for key,bad in [('running',1),('oom_killed',0),('restarting',None),('dead','false'),('exit_code',True),
                        ('exit_code',-1),('exit_code',256),('status','ordinary-private-status')]:
            state=self.state();state[key]=bad
            def docker(args,**kwargs):
                return M.encode(state) if args[:2]==['container','inspect'] else b'ordinary-private-value'
            ctx=SimpleNamespace(root=self.root,docker=Mock(side_effect=docker),write=Mock(),modules={})
            M.readiness_failure(ctx,{'postgres':'pg-fixture','redis':'redis-fixture'},'redis',RuntimeError('redis_readiness'))
            value=ctx.write.call_args.args[1]
            self.assertEqual(ctx.docker.call_count,4)
            for row in value['services'].values():
                self.assertEqual(row['state_observation'],'unavailable');self.assertNotIn('state',row)
                self.assertEqual(row['startup_classification'],'unclassified_startup')
                self.assertEqual(row['startup_log']['bytes'],len(b'ordinary-private-value'));self.assertFalse(row['startup_log']['empty'])
                self.assertEqual(row['startup_log']['closed_lines'],[{'line':0,'process':'unclassified','markers':[]}])
            self.assertNotIn('ordinary-private',M.encode(value).decode())

    def test_readiness_diagnostic_rpc_and_report_loss_cannot_replace_primary_or_skip_second_service(self):
        ctx=SimpleNamespace(root=self.root,docker=Mock(side_effect=OSError('ordinary-private-rpc')),
            write=Mock(side_effect=OSError('ordinary-private-report')),modules={})
        M.readiness_failure(ctx,{'postgres':'pg-fixture','redis':'redis-fixture'},'postgres',RuntimeError('dependency_readiness'))
        self.assertEqual(ctx.docker.call_count,4);ctx.write.assert_called_once()
        self.assertEqual(set(ctx.write.call_args.args[1]['services']),{'postgres','redis'})
        self.assertTrue(all(row=={'state_observation':'unavailable','startup_classification':'log_observation_unavailable'}
            for row in ctx.write.call_args.args[1]['services'].values()))

    def test_actual_readiness_timeout_records_failing_service_before_cleanup_without_running_capsule(self):
        for profile in ('postgres','redis'):
            with self.subTest(profile=profile):
                ctx,calls=self.execute_context();self.execute_patches(ctx);original=ctx.docker.side_effect
                def docker(args,**kwargs):
                    if args[:2]==['exec','pg-fixture'] and profile=='postgres':raise RuntimeError('command_failed')
                    if args[:2]==['exec','redis-fixture']:return b'ordinary-wrong-pong'
                    return original(args,**kwargs)
                ctx.docker.side_effect=docker
                with patch.object(M.time,'monotonic',side_effect=[0,91]),self.assertRaisesRegex(RuntimeError,'dependency_readiness'):
                    M.execute(ctx)
                self.assertEqual(M.SUBPHASE,profile+'_readiness')
                path,value=ctx.write.call_args.args
                self.assertEqual(path,self.root/'reports/dependency-readiness.json');self.assertEqual(value['failed_service'],profile)
                self.assertEqual(value['probe_failure']['subphase'],profile+'_readiness')
                self.assertFalse(any(args[:2]==['start','--attach'] for args,_ in calls))

    def test_every_literal_controller_refusal_has_a_closed_diagnostic_label(self):
        tree=ast.parse((ROOT/'scripts/run_app_image_ci_smoke.py').read_bytes())
        literals={n.args[1].value for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='need'
                  and len(n.args)==2 and isinstance(n.args[1],ast.Constant) and isinstance(n.args[1].value,str)}
        self.assertTrue(literals<=M.FAILURE_CODES)


if __name__=='__main__':unittest.main()
