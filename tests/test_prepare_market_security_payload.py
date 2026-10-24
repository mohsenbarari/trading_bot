"""Hermetic public-source/parity checks; never inspect live configuration."""
from contextlib import ExitStack
from copy import deepcopy
from hashlib import sha1,sha256
import ast
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('market_security_payload_fixture',
    ROOT / 'scripts/prepare_market_security_payload.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class MarketSecurityPayloadTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        for target in ('subprocess.run','subprocess.Popen','os.system','os.kill','os.killpg',
                       'socket.socket','socket.create_connection','socket.getaddrinfo',
                       'urllib.request.urlopen','ctypes.CDLL','resource.setrlimit'):
            self.stack.enter_context(patch(target,side_effect=AssertionError('ambient_host_network_denied')))

    def fixture(self):
        files = {'core/__init__.py':('100644',b''),
            'core/market_intelligence/private_pipeline_foundation.py':('100644',b'# ordinary public source\n'),
            'config/market_data_sources.v1.json':('100644',b'{}'),
            'contracts/market_data/fact.schema.json':('100644',b'{}'),
            'deploy/market-data/migrations/001.sql':('100644',b'SELECT 1;')}
        rows=[{'path':path,'bytes':len(data),'sha256':sha256(data).hexdigest(),'mode':'0644',
               'uid':10001,'gid':10001} for path,(mode,data) in sorted(files.items())]
        value={'schema':'market_security_public_source_fingerprint/1','container_id':'1'*64,
               'image_id':M.BASELINE_IMAGE,'release_sha':M.BASELINE,'release_tree':M.BASELINE_TREE,
               'image_input_signature':M.BASELINE_INPUT_SIGNATURE,'platform':'linux/amd64','runtime_user':'10001:10001',
               'files':rows,'file_count':len(rows),'total_bytes':sum(row['bytes'] for row in rows),
               'fingerprint_sha256':sha256(M.encoded(rows)).hexdigest(),'stable_before_after':True,
               'mount_overlays_absent':True,'application_imported':False,'secrets_read':False,'runtime_changed':False}
        return files,value

    def verify(self,files,value):
        raw=M.encoded(value)
        return M.verify_public_fingerprint(raw,files,sha256(raw).hexdigest())

    def test_whole_public_copy_scope_accepts_and_explicitly_does_not_attest_writable_layer_absence(self):
        files,value=self.fixture();result=self.verify(files,value)
        self.assertEqual(result['file_count'],5);self.assertTrue(result['runtime_files_equal_public_git'])
        self.assertFalse(result['writable_layer_absence_asserted']);self.assertFalse(result['security_accepted'])

    def test_unreviewed_pin_or_drift_refuses_before_json_interpretation(self):
        files,value=self.fixture();raw=M.encoded(value)
        with patch.object(M,'parse_document',side_effect=AssertionError('interpreted_before_pin')) as parse:
            for pin in (None,'','0'*64):
                with self.subTest(pin=pin),self.assertRaises(M.PayloadError):M.verify_public_fingerprint(raw,files,pin)
        parse.assert_not_called()

    def test_complete_actual_runtime_rows_must_equal_baseline_public_git_bytes(self):
        files,value=self.fixture()
        files['core/market_intelligence/private_pipeline_foundation.py']=('100644',b'# ordinary source change\n')
        with self.assertRaisesRegex(M.PayloadError,'baseline_copy_scope_drift'):self.verify(files,value)

    def test_missing_or_unexpected_regular_files_are_never_silently_filtered(self):
        files,value=self.fixture()
        for extra in (False,True):
            wrong=deepcopy(value)
            if extra:
                row=dict(wrong['files'][0]);row['path']='core/market_intelligence/ordinary.pyc';wrong['files'].append(row)
            else:wrong['files'].pop()
            wrong['files'].sort(key=lambda row:row['path']);wrong['file_count']=len(wrong['files'])
            wrong['total_bytes']=sum(row['bytes'] for row in wrong['files']);wrong['fingerprint_sha256']=sha256(M.encoded(wrong['files'])).hexdigest()
            with self.assertRaises(M.PayloadError):self.verify(files,wrong)

    def test_exact_identity_and_strict_source_observation_flags(self):
        files,value=self.fixture()
        for field,replacement in (('image_id','sha256:'+'0'*64),('release_sha','f'*40),('release_tree','f'*40),
                ('image_input_signature',''),('runtime_user','0'),('stable_before_after',1),
                ('mount_overlays_absent',False),('application_imported',True),('secrets_read',True),('runtime_changed',True)):
            wrong=deepcopy(value);wrong[field]=replacement
            with self.subTest(field=field),self.assertRaises(M.PayloadError):self.verify(files,wrong)

    def test_mode_uid_gid_count_and_byte_metadata_are_strict(self):
        files,value=self.fixture()
        for field,replacement in (('mode','0600'),('uid',0),('gid',0),('bytes',True),('bytes',M.MAX_FILE_BYTES+1)):
            wrong=deepcopy(value);wrong['files'][0][field]=replacement
            with self.subTest(field=field,replacement=replacement),self.assertRaises(M.PayloadError):self.verify(files,wrong)
        for field,replacement in (('file_count',True),('total_bytes',True),('fingerprint_sha256','0'*64)):
            wrong=deepcopy(value);wrong[field]=replacement
            with self.assertRaises(M.PayloadError):self.verify(files,wrong)

    def test_public_private_named_modules_allowed_but_secret_files_refuse(self):
        self.assertTrue(M.copied_path('core/market_intelligence/private_pipeline_foundation.py'))
        self.assertTrue(M.copied_path('core/market_intelligence/private_capture_telegram.py'))
        for name in ('.env','.env.production','credentials.json','secrets.json','id_ed25519','capture.session','capture.session-journal','transport.key','transport.pem'):
            self.assertFalse(M.copied_path('core/market_intelligence/'+name))
        for path in ('/app/core/__init__.py','core/../other.py','core//__init__.py','apps/coin_rate_estimator/app.py'):
            self.assertFalse(M.copied_path(path))

    def cache_fixture(self):
        files,value=self.fixture();source_path='core/market_intelligence/private_pipeline_foundation.py'
        path='core/market_intelligence/__pycache__/private_pipeline_foundation.cpython-311.pyc'
        header=bytes.fromhex('a70d0d0a')+struct.pack('<III',0,1,1)
        row={'path':path,'bytes':16,'sha256':sha256(header).hexdigest(),'mode':'0644','uid':10001,'gid':10001}
        value['files'].append(row);value['files'].sort(key=lambda row:row['path']);value['file_count']+=1;value['total_bytes']+=16
        value['fingerprint_sha256']=sha256(M.encoded(value['files'])).hexdigest();control=M.encoded(value)
        source=files[source_path][1]
        cache={'schema':'market_security_public_cache_classification/1','source_manifest_sha256':sha256(control).hexdigest(),
            'container_id':value['container_id'],'image_id':M.BASELINE_IMAGE,'python_version':[3,11,16],
            'cache_tag':'cpython-311','magic_hex':'a70d0d0a','hash_check_policy':'default','standard_pycache_location':True,
            'scope':'normal_CPython_3_11_SourceFileLoader_cache_selection_and_header_rejection_only','cache_count':1,
            'caches':[{'path':path,'bytes':16,'sha256':row['sha256'],'cache_tag':'cpython-311','header_hex':header.hex(),
                'flags':0,'source_path':source_path,'source_exists':True,'source_bytes':len(source),'source_mtime_ns':0,
                'source_sha256':sha256(source).hexdigest(),'source_hash':M._imp.source_hash(int.from_bytes(bytes.fromhex('a70d0d0a'),'little'),source).hex(),
                'classification':'timestamp_stale'}],
            'stable_before_after':True,'all_standard_cache_paths_nonselected_or_stale':True,'status':'classified_not_accepted',
            'explicit_bytecode_loader_reachability_assessed':False,'cached_code_deserialized':False,'cached_code_executed':False,
            'application_imported':False,'secrets_read':False,'runtime_changed':False,'security_accepted':False}
        return files,control,cache

    def verify_cache(self,files,control,cache):
        raw=M.encoded(cache)
        with patch.object(M,'E46_CACHE_COUNT',1):
            return M.verify_public_fingerprint(control,files,sha256(control).hexdigest(),cache_raw=raw,cache_sha256=sha256(raw).hexdigest())

    def test_exact_supplement_keeps_full_inventory_distinct_from_product_parity(self):
        files,control,cache=self.cache_fixture();result=self.verify_cache(files,control,cache)
        self.assertEqual(result['file_count'],6);self.assertEqual(result['product_file_count'],5)
        self.assertTrue(result['product_files_equal_public_git']);self.assertFalse(result['runtime_files_equal_public_git'])
        self.assertEqual(result['cache_classification']['classifications'],{'nonselected_cache_tag':0,'timestamp_stale':1})
        self.assertTrue(result['full_observed_inventory_preserved_in_control']);self.assertFalse(result['security_accepted'])

    def test_cache_pin_missing_or_wrong_refuses_before_interpretation(self):
        files,control,cache=self.cache_fixture();raw=M.encoded(cache)
        with patch.object(M,'E46_CACHE_COUNT',1):
            for pin in (None,'0'*64):
                with self.subTest(pin=pin),self.assertRaisesRegex(M.PayloadError,'cache_classification_pin'):
                    M.verify_public_fingerprint(control,files,sha256(control).hexdigest(),cache_raw=raw,cache_sha256=pin)

    def test_valid_timestamp_or_unchecked_cache_cannot_be_waived_by_claimed_stale_status(self):
        files,control,cache=self.cache_fixture();source=next(iter(cache['caches']))['source_bytes']
        for flags,mtime,size in ((0,0,source),(1,1,1),(3,1,1)):
            wrong=deepcopy(cache);wrong['caches'][0]['flags']=flags
            wrong['caches'][0]['header_hex']=(bytes.fromhex('a70d0d0a')+struct.pack('<III',flags,mtime,size)).hex()
            with self.subTest(flags=flags),self.assertRaisesRegex(M.PayloadError,'cache_classification_unknown'):
                self.verify_cache(files,control,wrong)

    def test_cache_source_identity_header_and_json_types_are_independently_checked(self):
        files,control,cache=self.cache_fixture()
        for field,replacement in (('bytes',True),('sha256','0'*64),('flags',False),('source_bytes',True),
                ('source_mtime_ns',True),('source_sha256','0'*64),('source_hash','0'*16),('source_exists',1),
                ('classification','nonselected_cache_tag'),('path','core/market_intelligence/ordinary.pyc')):
            wrong=deepcopy(cache);wrong['caches'][0][field]=replacement
            with self.subTest(field=field),self.assertRaises(M.PayloadError):self.verify_cache(files,control,wrong)
        for field,replacement in (('container_id','0'*64),('cache_count',True),('cached_code_executed',True),
                ('source_manifest_sha256','0'*64),('cache_tag','cpython-312'),('status','accepted')):
            wrong=deepcopy(cache);wrong[field]=replacement
            with self.subTest(field=field),self.assertRaises(M.PayloadError):self.verify_cache(files,control,wrong)

    def test_extra_cache_row_or_missing_row_is_not_a_blanket_extension_ignore(self):
        files,control,cache=self.cache_fixture()
        for rows in ([],cache['caches']*2):
            wrong=deepcopy(cache);wrong['caches']=rows;wrong['cache_count']=len(rows)
            with self.assertRaises(M.PayloadError):self.verify_cache(files,control,wrong)
        files,value=self.fixture();value['files'].append({'path':'core/market_intelligence/extra.pyc','bytes':1,
            'sha256':sha256(b'x').hexdigest(),'mode':'0644','uid':10001,'gid':10001})
        value['files'].sort(key=lambda row:row['path']);value['file_count']+=1;value['total_bytes']+=1
        value['fingerprint_sha256']=sha256(M.encoded(value['files'])).hexdigest()
        with self.assertRaises(M.PayloadError):self.verify(files,value)

    def test_actual_public_controls_are_unchanged_and_all105_rows_remain_present(self):
        control=(ROOT/M.PUBLIC_PROOF_PATH).read_bytes();cache=(ROOT/M.CACHE_PROOF_PATH).read_bytes()
        self.assertEqual(sha256(control).hexdigest(),M.E46_PUBLIC_FINGERPRINT_SHA256)
        self.assertEqual(sha256(cache).hexdigest(),M.E46_CACHE_CLASSIFICATION_SHA256)
        self.assertEqual(len(json.loads(control)['files']),198);self.assertEqual(len(json.loads(cache)['caches']),105)

    def test_unknown_profile_or_unreviewed_control_refuses_before_git(self):
        with patch.object(M,'git',side_effect=AssertionError('git_before_profile')) as git:
            with self.assertRaisesRegex(M.PayloadError,'unreviewed_market_profile'):M.prepare(ROOT,'0'*40,ROOT/'unused',profile='f79')
            with patch.object(M,'E46_PUBLIC_FINGERPRINT_SHA256',None),self.assertRaisesRegex(M.PayloadError,'unreviewed'):
                M.prepare(ROOT,'0'*40,ROOT/'unused')
        git.assert_not_called()

    def test_published_main_ref_matches_workflow_and_refuses_detached_or_advanced_checkout(self):
        commit='d'*40
        def git(repo,*args,**options):
            records={('rev-parse','--show-object-format'):b'sha1\n',('rev-parse','HEAD'):(commit+'\n').encode(),
                ('symbolic-ref','--short','HEAD'):b'main\n',('status','--porcelain','--untracked-files=normal'):b'',
                ('remote','get-url','origin'):b'https://github.com/mohsenbarari/trading_bot.git\n',
                ('ls-remote','--exit-code','origin','refs/heads/main'):(commit+'\trefs/heads/main\n').encode()}
            return records[args]
        with patch.object(M,'git',side_effect=git):M.published(ROOT,commit)
        for boundary in (('symbolic-ref','--short','HEAD'),('rev-parse','HEAD')):
            def changed(repo,*args,**options):
                if args==boundary:return b'' if args[0]=='symbolic-ref' else b'0'*40+b'\n'
                return git(repo,*args,**options)
            with patch.object(M,'git',side_effect=changed),self.assertRaisesRegex(M.PayloadError,'published_main_required'):
                M.published(ROOT,commit)

    def test_baseline_test_overlay_changes_only_one_controlled_assertion(self):
        original=b'class Public:\n    def test_same(self):\n'+M.TEST_OLD+b'        self.assertEqual(1,1)\n'
        result=M.test_transform('tests/test_market_pipeline_stage3_foundation.py',original)
        self.assertEqual(result.replace(M.TEST_NEW,M.TEST_OLD),original)
        self.assertIn(b'self.assertEqual(1,1)',result)
        self.assertEqual(M.test_transform('tests/other.py',original),original)
        for wrong in (original.replace(M.TEST_OLD,b''),original+M.TEST_OLD):
            with self.assertRaisesRegex(M.PayloadError,'baseline_test_assertion'):
                M.test_transform('tests/test_market_pipeline_stage3_foundation.py',wrong)

    def context(self):
        tmp=self.stack.enter_context(tempfile.TemporaryDirectory());root=Path(tmp);(root/'context').mkdir()
        files,_=self.fixture();files.update({name:('100644',b'# public security overlay\n') for name in M.OVERLAY})
        rows=[]
        for name,(mode,raw) in files.items():
            path=root/'context'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw);path.chmod(0o644)
            rows.append({'path':name,'bytes':len(raw),'sha256':sha256(raw).hexdigest(),
                'git_blob':sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest(),'mode':mode})
        return root,{'schema':'market_security_payload/1','profile':'e46','baseline_commit':M.BASELINE,
            'baseline_tree':M.BASELINE_TREE,'baseline_image_id':M.BASELINE_IMAGE,'files':rows}

    def test_real_context_hash_extra_path_and_symlink_boundaries(self):
        root,record=self.context();M.verify_context(root,record)
        path=root/'context/core/market_intelligence/private_pipeline_foundation.py';original=path.read_bytes()
        path.write_bytes(b'# changed public source\n')
        with self.assertRaisesRegex(M.PayloadError,'context_source_drift'):M.verify_context(root,record)
        path.write_bytes(original);extra=root/'context/core/market_intelligence/extra.py';extra.write_bytes(b'# public\n')
        with self.assertRaisesRegex(M.PayloadError,'context_inventory'):M.verify_context(root,record)
        extra.unlink();extra.symlink_to(path)
        with self.assertRaisesRegex(M.PayloadError,'context_metadata'):M.verify_context(root,record)

    def test_command_helper_is_pinned_before_any_compilation(self):
        with patch.object(M,'read',return_value=b'changed public helper'),patch.object(M,'ModuleType') as module:
            with self.assertRaisesRegex(M.PayloadError,'command_helper_pin'):M.primitives()
        module.assert_not_called()

    def test_candidate_program_parses_and_places_host_network_fence_before_all_test_imports(self):
        program=M.candidate_test_program({'files':[],'test_sources_sha256':'a'*64,'source_commit':'b'*40})
        ast.parse(program)
        self.assertLess(program.index("fence.enter_context"),program.index("exec(compile(sources[name]"))
        self.assertIn('suite.countTestCases()==85',program);self.assertIn('not result.skipped',program)
        self.assertIn('actual==set(expected)',program);self.assertNotIn('test_capture_public_replay_idempotence',program)

    def test_host_refusal_creates_no_files_and_emits_only_closed_diagnostics(self):
        directory=self.stack.enter_context(tempfile.TemporaryDirectory());root=Path(directory)/'unused'
        with patch.object(M,'ci_admit',side_effect=M.PayloadError('github_ci_only')),patch.object(M.sys,'stderr',io.StringIO()) as output:
            self.assertEqual(M.main(['prepare','--root',str(root),'--repo',str(ROOT)]),1)
        self.assertFalse(root.exists());value=json.loads(output.getvalue())
        self.assertEqual(value['code'],'github_ci_only');self.assertFalse(value['security_accepted'])

    def test_exact_dependency_delta_preserves_pydantic_and_every_other_lock_entry(self):
        new=(ROOT/'deploy/market-data/requirements.lock').read_bytes()
        old=new.replace(b' @ https://files.pythonhosted.org/packages/ed/76/7b4383014be0fcc6c1c0e24292845a14e1672cf17fca62ca0a2bd5f4563d/psycopg2_binary-2.9.13.tar.gz',b'==2.9.9').replace(
            b'e324ecf60f952d21dd11413b8bbed0951bbd99579a06fd06f28bfc37737cd373',
            b'977646e05232579d2e7b9c59e21dbe5261f403a88417f6a6512e70d3f8a046be')
        self.assertEqual(M.dependency_delta(old,new)['pydantic_unchanged'],'2.8.2')
        for wrong in (new.replace(b'pydantic==2.8.2',b'pydantic==2.9.0'),new.replace(b'telethon==1.44.0',b'telethon==1.45.0'),
                      new.replace(b'2.9.13.tar.gz',b'2.9.14.tar.gz')):
            with self.assertRaises(M.PayloadError):M.dependency_delta(old,wrong)

    def reports(self):
        directory=self.stack.enter_context(tempfile.TemporaryDirectory());root=Path(directory);(root/'reports').mkdir()
        for name in ('native.json','focused.json'):(root/'reports'/name).write_bytes(b'{}')
        image='sha256:'+'9'*64;record={'test_sources_sha256':'a'*64}
        build={'schema':'market_security_candidate_build/1','image_id':image,
               'rootfs_layers':['sha256:'+'b'*64],'payload_sha256':sha256(M.encoded(record)).hexdigest(),
               'test_sources_sha256':'a'*64,'native_sha256':sha256(b'{}').hexdigest(),'focused_sha256':sha256(b'{}').hexdigest(),
               'product_files_equal_baseline':True,'buildkit_resource_cgroup_asserted':False,'vendor_debs_reused':False,
               'full_runtime_matrix_completed':False,'security_accepted':False,'deployment_eligible':False}
        raw={'SchemaVersion':2,'ArtifactType':'container_image','Metadata':{'ImageID':image,
            'ImageConfig':{'rootfs':{'diff_ids':build['rootfs_layers']}}},'Results':[{'Class':'os-pkgs','Type':'debian',
            'Vulnerabilities':[{'VulnerabilityID':'CVE-2026-10000','PkgName':'ordinary','Severity':severity}
                for severity in ('LOW','HIGH','CRITICAL')]}]}
        sbom={'bomFormat':'CycloneDX','metadata':{'component':{'type':'container','name':image,
            'properties':[{'name':'aquasecurity:trivy:ImageID','value':image}]}}}
        return root,record,build,raw,sbom

    def test_raw_lower_high_and_critical_findings_remain_present_and_bound_to_image_layers(self):
        root,record,build,raw,sbom=self.reports();before=deepcopy(raw)
        counts=M.bind_reports(root,record,build,raw,sbom)
        self.assertEqual(counts,{'UNKNOWN':0,'LOW':1,'MEDIUM':0,'HIGH':1,'CRITICAL':1});self.assertEqual(raw,before)
        for field in ('ImageID','ImageConfig'):
            wrong=deepcopy(raw);wrong['Metadata'][field]='foreign' if field=='ImageID' else {'rootfs':{'diff_ids':[]}}
            with self.assertRaisesRegex(M.PayloadError,'raw_report_identity'):M.bind_reports(root,record,build,wrong,sbom)
        wrong=deepcopy(sbom);wrong['metadata']['component']['properties'][0]['value']='sha256:'+'0'*64
        with self.assertRaisesRegex(M.PayloadError,'sbom_image_identity'):M.bind_reports(root,record,build,raw,wrong)
        (root/'reports/native.json').write_bytes(b'{"changed":true}')
        with self.assertRaisesRegex(M.PayloadError,'build_evidence_changed'):M.bind_reports(root,record,build,raw,sbom)

    def test_final_gate_recounts_bound_raw_findings_and_refuses_zeroed_or_boolean_summary_counts(self):
        root,record,build,raw,sbom=self.reports();counts=M.bind_reports(root,record,build,raw,sbom)
        reports={'vulnerabilities.json':M.encoded(raw),'sbom.cdx.json':M.encoded(sbom)}
        for name,data in reports.items():(root/'reports'/name).write_bytes(data)
        build_raw=M.encoded(build);(root/'reports/build-receipt.json').write_bytes(build_raw)
        proof={'schema':'market_security_candidate_image_export/1','status':'exported_not_accepted','security_accepted':False,
            'findings_waived':False,'deployment_eligible':False,'image_id':build['image_id'],
            'payload_sha256':sha256(M.encoded(record)).hexdigest(),'build_receipt_sha256':sha256(build_raw).hexdigest(),
            'reports_sha256':{name:sha256(data).hexdigest() for name,data in reports.items()},'raw_counts':counts}
        for mode in ('original','zeroed','boolean'):
            wrong=deepcopy(proof)
            if mode=='zeroed':wrong['raw_counts'].update(HIGH=0,CRITICAL=0)
            if mode=='boolean':wrong['raw_counts']['LOW']=True
            (root/'image-export.json').write_bytes(M.encoded(wrong))
            with patch.object(M,'ci_admit'),patch.object(M,'load_payload',return_value=record),patch.object(M.sys,'stderr',io.StringIO()) as output:
                self.assertEqual(M.main(['gate','--root',str(root)]),1)
            self.assertEqual(json.loads(output.getvalue())['code'],'raw_high_critical_findings' if mode=='original' else 'export_receipt')

    def test_actual_workflow_opt_in_and_raw_scan_export_gate_contract(self):
        import yaml
        value=yaml.load((ROOT/'.github/workflows/pre-release-gate.yml').read_text(),Loader=yaml.BaseLoader)
        self.assertEqual(value['on']['workflow_dispatch']['inputs']['market_security_payload']['default'],'false')
        jobs=value['jobs'];job=jobs['market-security-payload'];self.assertNotIn('needs',job)
        self.assertEqual(job['steps'][0]['with']['ref'],'main')
        for name in ('repository-governance','market-postgres-gate','observability-gate','frontend-browser-matrix'):
            self.assertEqual(jobs[name]['if'],'!inputs.market_security_payload')
        self.assertIn('!inputs.market_security_payload',jobs['security-consumer-payload']['if'])
        steps={step['id']:step for step in job['steps'] if 'id' in step}
        self.assertIn(sha256((ROOT/'scripts/prepare_market_security_payload.py').read_bytes()).hexdigest(),steps['market_seal']['run'])
        self.assertIn(M.COMMAND_HELPERS_SHA256,steps['market_seal']['run'])
        scan=steps['market_vulnerabilities'];self.assertEqual(scan['with']['severity'],'UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL')
        self.assertEqual(scan['with']['ignore-unfixed'],'false');self.assertEqual(scan['env']['TRIVY_IGNOREFILE'],'/dev/null')
        self.assertEqual(scan['with']['scanners'],'vuln');self.assertEqual(scan['with']['image-ref'],'${{ steps.market_build.outputs.image_id }}')
        self.assertIn("steps.market_vulnerabilities.outcome == 'success'",steps['market_export']['if'])
        self.assertIn("steps.market_sbom.outcome == 'success'",steps['market_export']['if'])
        self.assertIn(' gate --root ',job['steps'][-1]['run'])
        for step in job['steps']:
            if step.get('run','').startswith('python3 -I -B - <<'):
                ast.parse(step['run'].split("<<'PY'\n",1)[1].rsplit('\nPY',1)[0])

    def test_real_build_orchestration_binds_historical_source_and_sealed_full_suite(self):
        directory=self.stack.enter_context(tempfile.TemporaryDirectory());root=Path(directory);(root/'reports').mkdir()
        image='sha256:'+'9'*64;calls=[]
        record={'payload_tree':'c'*40,'source_commit':'d'*40,'source_tree':'e'*40,
            'image_input_signature':'f'*64,'test_sources_sha256':'a'*64,'files':[]}
        def docker(parent,argv,**options):
            calls.append(('docker',argv,options));M.write(root/'candidate-image.id',(image+'\n').encode());return b''
        def isolated(parent,actual,program,purpose,**options):
            calls.append((purpose,actual,options));self.assertEqual(actual,image);ast.parse(program)
            if purpose=='market-native':value={'schema':'market_security_native_checks/1','image_id':image,
                'status':'passed','pydantic':'2.8.2','psycopg2':'2.9.13','runtime_data_used':False,'security_accepted':False}
            else:value={'schema':'market_security_candidate_tests/1','image_id':image,'test_sources_sha256':'a'*64,
                'status':'passed','tests':85,'skips':0,'product_files_equal_baseline':True,'runtime_data_used':False,'security_accepted':False}
            return M.encoded(value)
        with patch.object(M,'ci_admit'),patch.object(M,'verify_context'),patch.object(M,'primitives',return_value=SimpleNamespace(docker=docker,isolated=isolated)),\
             patch.object(M,'image_metadata',return_value={'image_id':image}),patch.object(M.os,'statvfs',return_value=SimpleNamespace(f_bavail=20*1024**3,f_frsize=1)):
            self.assertEqual(M.build_candidate(root,record),image)
        argv=calls[0][1];self.assertIn('SOURCE_SHA='+M.BASELINE,argv);self.assertNotIn('SOURCE_SHA='+record['source_commit'],argv)
        self.assertIn('SOURCE_TREE='+record['payload_tree'],argv);self.assertIn('IMAGE_INPUT_SIGNATURE='+record['image_input_signature'],argv)
        self.assertEqual(argv[-1],str(root/'payload/context'));self.assertEqual(calls[0][2]['seconds'],2400)
        self.assertEqual([row[0] for row in calls],['docker','market-native','market-focused'])
        self.assertEqual(calls[-1][2]['mounts'],((root/'payload/tests','/evidence'),))
        proof=json.loads((root/'reports/build-receipt.json').read_bytes())
        self.assertFalse(proof['full_runtime_matrix_completed']);self.assertFalse(proof['deployment_eligible'])


if __name__=='__main__':unittest.main()
