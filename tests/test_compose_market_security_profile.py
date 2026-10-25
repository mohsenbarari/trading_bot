"""Hermetic ordinary archive, metadata and controller fixtures; no image actions."""
from contextlib import ExitStack, contextmanager, redirect_stderr
from copy import deepcopy
from hashlib import sha256
import ast
import gzip
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from zipfile import ZipFile

FENCE=ExitStack()
for target in ('subprocess.Popen','subprocess.run','os.system','os.kill','os.killpg','socket.socket',
    'socket.create_connection','socket.getaddrinfo','urllib.request.urlopen','urllib.request.OpenerDirector.open',
    'ctypes.CDLL','ctypes.PyDLL','signal.signal','signal.alarm','resource.setrlimit'):
    FENCE.enter_context(patch(target,side_effect=AssertionError('ambient_host_network_forbidden')))
ROOT=Path(__file__).resolve().parents[1]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
M=load('profile_composition',ROOT/'scripts/compose_market_security_profile.py')
C=load('profile_common',ROOT/'scripts/compose_security_os_candidate.py')
O=load('profile_prior',ROOT/'scripts/compose_market_security_os_candidate.py')
P=load('profile_original',ROOT/'scripts/prepare_market_security_payload.py')
Q=load('profile_preparer_again',ROOT/'scripts/prepare_market_security_profile.py')


class ProfileCompositionTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='public-market-profile-')))
        self.root.chmod(0o700);previous=os.umask(0o077);self.addCleanup(os.umask,previous)
        for name,value in (('COMMON',C),('PRIOR',O),('MARKET',P),('PREP',Q)):
            self.stack.enter_context(patch.object(M,name,value))
        self.stack.enter_context(patch.dict(os.environ,{'GITHUB_SHA':'a'*40,'GITHUB_RUN_ID':'123',
            'GITHUB_RUN_ATTEMPT':'1','GITHUB_OUTPUT':str(self.root/'output')}))

    def write(self,path,raw):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw);path.chmod(0o600);return path

    def tar(self,rows):
        out=io.BytesIO()
        with tarfile.open(fileobj=out,mode='w',format=tarfile.USTAR_FORMAT) as archive:
            for name,raw,kind,uid,mode in rows:
                info=tarfile.TarInfo(name);info.type=kind;info.uid=info.gid=uid;info.mode=mode
                info.size=len(raw) if kind==tarfile.REGTYPE else 0
                archive.addfile(info,io.BytesIO(raw) if kind==tarfile.REGTYPE else None)
        return out.getvalue()

    def archive(self,*,new_rows=None,compressed=False,extra=False,duplicate=False,wrong_order=False,legacy=False,bad_legacy=False):
        raw=b'# ordinary public product\n';product=[{'path':'core/market_intelligence/ordinary.py',
            'bytes':len(raw),'sha256':sha256(raw).hexdigest(),'mode':'100644'}]
        entry=('app/core/market_intelligence/ordinary.py',raw,tarfile.REGTYPE,10001,0o644)
        empty=self.tar([]);new=self.tar([entry] if new_rows is None else new_rows)
        bodies=[empty]*18+[new];diffs=['sha256:'+sha256(q).hexdigest() for q in bodies]
        config={'architecture':'amd64','created':'2026-10-25T01:02:03.123400Z','history':[],'os':'linux',
            'rootfs':{'type':'layers','diff_ids':diffs},'config':{'Cmd':['service','--role','market-processor'],
            'Entrypoint':['python','-m','ordinary.fixture'],'Env':['PUBLIC_DEFAULT=yes'],'Labels':{'public':'fixture'},
            'User':'10001:10001','WorkingDir':'/app','ArgsEscaped':True}}
        original=deepcopy(config)
        if wrong_order:config['rootfs']['diff_ids'][-1],config['rootfs']['diff_ids'][0]=diffs[0],diffs[-1]
        config_raw=M.encoded(config);image='sha256:'+sha256(config_raw).hexdigest();rows=[];names=[];descriptors=[]
        for body in bodies:
            stored=gzip.compress(body,mtime=0) if compressed else body;digest=sha256(stored).hexdigest();name='blobs/sha256/'+digest
            names.append(name);descriptors.append({'digest':'sha256:'+digest,'size':len(stored)})
            if name not in {q[0] for q in rows}:rows.append((name,stored,tarfile.REGTYPE,0,0o644))
        config_name='blobs/sha256/'+image[7:];rows.append((config_name,config_raw,tarfile.REGTYPE,0,0o644))
        rows.append(('manifest.json',M.encoded([{'Config':config_name,'Layers':names,'RepoTags':None}]),tarfile.REGTYPE,0,0o644))
        oci=M.encoded({'schemaVersion':2,'config':{'digest':image,'size':len(config_raw)},'layers':descriptors})
        rows.append(('blobs/sha256/'+sha256(oci).hexdigest(),oci,tarfile.REGTYPE,0,0o644))
        rows.append(('index.json',M.encoded({'schemaVersion':2,'manifests':[{'digest':'sha256:'+sha256(oci).hexdigest(),'size':len(oci)}]}),tarfile.REGTYPE,0,0o644))
        rows.append(('oci-layout',b'{"imageLayoutVersion":"1.0.0"}',tarfile.REGTYPE,0,0o644))
        if legacy:
            for index,(name,body) in enumerate(O.legacy_configs(config).items()):
                rows.append((name,body+b' ' if bad_legacy and index==0 else body,tarfile.REGTYPE,0,0o644))
        if extra:rows.append(('blobs/sha256/'+'f'*64,b'ordinary extra',tarfile.REGTYPE,0,0o644))
        if duplicate:rows.append(rows[0])
        return self.tar(rows),config,image,product

    def public_payload(self):
        stack=json.loads((ROOT/Q.STACK[0]).read_bytes());dossier=json.loads((ROOT/Q.DOSSIER[0]).read_bytes())
        selected,actual=Q.choose_profile(dossier,stack,'f79cdda21c19')
        payload={'profile':'f79cdda21c19','old_public_source_revision':selected['label_commit'],
            'old_image_nonenv_config':actual['image']['config'],'source_commit':'a'*40,
            'source_binding':{'source_sha256':selected['source']['sha256'],'cache_sha256':selected['cache']['sha256']},
            'source_overrides':{'ordinary':'proof'},'image_input_signature':'d'*64}
        before={k:v for k,v in actual['image']['config'].items() if v is not None}
        before['Labels']={k:v for k,v in before['Labels'].items() if not k.startswith(('io.gold-trade.capture.','io.gold-trade.observability.'))}
        before['Env']=['PYTHON_VERSION=3.11.17','PYTHONDONTWRITEBYTECODE=1','PUBLIC_OTHER=unchanged',
            'MARKET_PIPELINE_IMAGE_REVISION=a6dcd636a1525a3a3d47e2a6083ca887b16080db']
        return before,payload

    def test_full_ordered_repeated_layer_archive_and_exact_public_overlay_pass(self):
        for compressed in (False,True):
            raw,config,image,product=self.archive(compressed=compressed)
            result=M.validate_archive(io.BytesIO(raw),len(raw),config,image,product=product)
            self.assertEqual(result['layers'],19);self.assertEqual(result['image_id'],image)
            self.assertEqual(result['scanner_history_projection']['original_nanosecond_config_sha256'],image[7:])

    def test_outside_product_file_or_directory_change_refuses(self):
        for row in (('usr/local/bin/ordinary',b'x',tarfile.REGTYPE,10001,0o644),
                    ('etc',b'',tarfile.DIRTYPE,10001,0o755),('app',b'',tarfile.DIRTYPE,0,0o755)):
            raw,config,image,product=self.archive(new_rows=[row])
            with self.subTest(name=row[0]),self.assertRaises(RuntimeError):
                M.validate_archive(io.BytesIO(raw),len(raw),config,image,product=product)

    def test_only_known_product_whiteouts_and_opaque_directories_are_accepted(self):
        for name in ('app/core/.wh.market_intelligence','app/core/market_intelligence/.wh..wh..opq'):
            raw,config,image,product=self.archive(new_rows=[(name,b'',tarfile.REGTYPE,0,0)])
            M.validate_archive(io.BytesIO(raw),len(raw),config,image,product=product)
        for name in ('app/.wh.core','app/core/.wh..wh..opq','usr/.wh.local'):
            raw,config,image,product=self.archive(new_rows=[(name,b'',tarfile.REGTYPE,0,0)])
            with self.assertRaisesRegex(RuntimeError,'product_layer_whiteout'):
                M.validate_archive(io.BytesIO(raw),len(raw),config,image,product=product)

    def test_public_file_bytes_owner_mode_and_extra_entries_are_strict(self):
        for body,uid,mode in ((b'wrong',10001,0o644),(b'# ordinary public product\n',0,0o644),
                              (b'# ordinary public product\n',10001,0o666)):
            raw,config,image,product=self.archive(new_rows=[('app/core/market_intelligence/ordinary.py',body,tarfile.REGTYPE,uid,mode)])
            with self.assertRaisesRegex(RuntimeError,'product_layer_file'):
                M.validate_archive(io.BytesIO(raw),len(raw),config,image,product=product)
        for kwargs in ({'extra':True},{'duplicate':True},{'wrong_order':True}):
            raw,config,image,product=self.archive(**kwargs)
            with self.assertRaises(RuntimeError):M.validate_archive(io.BytesIO(raw),len(raw),config,image,product=product)

    def test_physical_layer_symlinks_and_hardlinks_refuse_without_following(self):
        for kind in (tarfile.SYMTYPE,tarfile.LNKTYPE):
            raw,config,image,product=self.archive(new_rows=[('app/core/market_intelligence/ordinary.py',b'',kind,10001,0o644)])
            with self.assertRaisesRegex(RuntimeError,'product_layer_outside_scope'):
                M.validate_archive(io.BytesIO(raw),len(raw),config,image,product=product)

    def test_exact_legacy_chain_is_closed_and_each_legacy_byte_is_required(self):
        raw,config,image,product=self.archive(legacy=True)
        result=M.validate_archive(io.BytesIO(raw),len(raw),config,image,product=product)
        self.assertEqual(result['legacy_compatibility_configs'],19)
        raw,config,image,product=self.archive(legacy=True,bad_legacy=True)
        with self.assertRaisesRegex(RuntimeError,'legacy_bytes'):
            M.validate_archive(io.BytesIO(raw),len(raw),config,image,product=product)

    def test_borrowed_archive_stream_never_reads_past_advertised_size(self):
        raw,config,image,product=self.archive()
        class Spy(io.BytesIO):
            def read(self,size=-1):
                self.asserted=self.tell()+size;assert size>=0 and self.asserted<=len(raw);return super().read(size)
        result=M.validate_archive(Spy(raw+b'ordinary appended bytes'),len(raw),config,image,product=product)
        self.assertEqual(result['docker_archive_bytes'],len(raw))

    def test_retained_input_growth_and_path_ABA_refuse(self):
        for replace in (False,True):
            path=self.write(self.root/('ordinary-'+str(replace)),b'public fixture')
            with self.assertRaisesRegex(RuntimeError,'input_changed'):
                with M.pinned(path,64):
                    if replace:
                        old=path.with_suffix('.old');path.rename(old);self.write(path,b'public fixture')
                    else:
                        with path.open('ab') as out:out.write(b'grow')

    def test_gzip_source_reader_is_bounded_before_reading_a_growing_source(self):
        body=b'ordinary public input';compressed=gzip.compress(body,mtime=0)
        class Spy(io.BytesIO):
            def read(self,size=-1):
                assert size>=0 and self.tell()+size<=len(compressed);return super().read(size)
        target=self.root/'decoded.tar';M.decode_gzip(Spy(compressed+b'ordinary growth'),len(compressed),target,len(body))
        self.assertEqual(target.read_bytes(),body)

    def test_workflow_defaults_closed_matrix_first_buffer_and_report_gate_contracts(self):
        import yaml
        workflow=yaml.safe_load((ROOT/'.github/workflows/market-security-profile-compose.yml').read_bytes())
        trigger=workflow.get('on',workflow.get(True));choice=trigger['workflow_dispatch']['inputs']['profile']
        self.assertEqual(choice['default'],'none');self.assertEqual(set(choice['options']),{'none','all',*Q.PROFILES})
        job=workflow['jobs']['profile'];self.assertFalse(job['strategy']['fail-fast']);self.assertEqual(job['strategy']['max-parallel'],3)
        self.assertEqual(job['env']['CONTROLLER_SHA'],sha256((ROOT/'scripts/compose_market_security_profile.py').read_bytes()).hexdigest())
        self.assertEqual(job['env']['PREPARER_SHA'],sha256((ROOT/'scripts/prepare_market_security_profile.py').read_bytes()).hexdigest())
        wrappers=[]
        for step in job['steps']:
            script=step.get('run','')
            if "<<'PY'" in script:
                code=script.split("<<'PY'",1)[1].split('\n',1)[1].rsplit('\nPY',1)[0];ast.parse(code)
                if 'exec(compile(raw' in code:
                    wrappers.append(code);self.assertLess(code.index('sha256(raw)'),code.index('exec(compile(raw'))
                    self.assertIn('finally:assert identity(before)==identity(os.fstat(fd))==identity(path.lstat())',code)
        self.assertEqual(len(wrappers),4)
        raw=next(step for step in job['steps'] if step.get('id')=='vulnerabilities')
        self.assertEqual(raw['with']['severity'],'UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL')
        self.assertEqual(raw['with']['ignore-unfixed'],'false');self.assertEqual(raw['with']['exit-code'],'0')
        names=[step.get('name','') for step in job['steps']]
        self.assertLess(names.index('Release only the owned parent alias after its final consumer'),names.index('Retain only complete public image exports'))
        self.assertEqual(job['steps'][-1]['name'],'Keep the raw HIGH and CRITICAL gate unwaived')

    def test_public_source_revision_is_only_env_delta_order_is_preserved_and_f79_labels_survive(self):
        before,payload=self.public_payload();expected=M.expected_config(before,payload)
        self.assertEqual(expected['Env'][:-1],before['Env'][:-1]);self.assertEqual(len(expected['Env']),len(before['Env']))
        self.assertEqual(expected['Env'][-1],'MARKET_PIPELINE_IMAGE_REVISION='+payload['old_public_source_revision'])
        for key,value in payload['old_image_nonenv_config']['Labels'].items():
            if key!='io.gold-trade.release.input-signature':self.assertEqual(expected['Labels'][key],value)
        self.assertEqual(expected['Labels']['io.gold-trade.release.input-signature'],payload['image_input_signature'])
        self.assertEqual(expected['Labels']['io.gold-trade.security.product-overrides'],Q.OVERRIDES[1])
        self.assertEqual(expected['User'],'10001:10001')

    def test_missing_repeated_or_wrong_source_default_refuses(self):
        for change in ('missing','duplicate','wrong'):
            before,payload=self.public_payload()
            if change=='missing':before['Env'].pop()
            elif change=='duplicate':before['Env'].append(before['Env'][-1])
            else:before['Env'][-1]='MARKET_PIPELINE_IMAGE_REVISION='+'b'*40
            with self.subTest(change=change),self.assertRaisesRegex(RuntimeError,'public_source_default'):
                M.expected_config(before,payload)

    def test_observed_omitted_deprecated_fields_match_only_documented_api_zero_defaults(self):
        before,payload=self.public_payload()
        for key,value in (('Hostname',''),('AttachStdin',False),('NetworkDisabled',False),('Image','')):
            self.assertIsNone(payload['old_image_nonenv_config'][key]);before[key]=value
        expected=M.expected_config(before,payload)
        self.assertEqual(expected['AttachStdin'],False)
        for key,value in (('Hostname','unexpected'),('AttachStdin',0),('NetworkDisabled',1)):
            changed={**before,key:value}
            with self.assertRaisesRegex(RuntimeError,'historical_runtime_config_drift'):M.expected_config(changed,payload)

    def test_overlay_input_digest_binds_real_recipe_bytes_proofs_and_product_without_alias_or_self_cycle(self):
        before,payload=self.public_payload();payload.update({'files':[{'path':'core/__init__.py','bytes':1,
            'sha256':sha256(b'x').hexdigest(),'mode':'100644'}],
            'historical':{'test_sources_sha256':'b'*64},'replay':None})
        identity={'recipe_commit':'a'*40,'files':{'ordinary-helper.py':{'bytes':1,'sha256':'c'*64}}}
        left,recipe,program=M.bind_overlay_inputs(before,payload,[],identity,'security-market-profile-base-123-1:ci')
        right,other_recipe,other_program=M.bind_overlay_inputs(before,payload,[],identity,'security-market-profile-base-456-2:ci')
        self.assertEqual(left['image_input_signature'],right['image_input_signature'])
        self.assertEqual(recipe,other_recipe);self.assertEqual(program,other_program)
        self.assertEqual(left['image_input_signature'],sha256(M.encoded(left['overlay_inputs'])).hexdigest())
        self.assertEqual(left['baseline_input_signature'],payload['old_image_nonenv_config']['Labels']['io.gold-trade.release.input-signature'])
        self.assertIn(b'ARG IMAGE_INPUT_SIGNATURE',recipe);self.assertIn(b'"${IMAGE_INPUT_SIGNATURE}"',recipe)
        self.assertNotIn(left['image_input_signature'].encode(),recipe)
        for change in ('product','cache','helper'):
            changed=deepcopy(payload);identity_changed=deepcopy(identity)
            if change=='product':changed['files'][0]['sha256']='d'*64
            elif change=='cache':changed['source_binding']['cache_sha256']='d'*64
            else:identity_changed['files']['ordinary-helper.py']['sha256']='d'*64
            value,_,_=M.bind_overlay_inputs(before,changed,[],identity_changed,'security-market-profile-base-123-1:ci')
            self.assertNotEqual(value['image_input_signature'],left['image_input_signature'])

    def test_full_config_env_rootfs_and_user_drift_each_refuse(self):
        before,payload=self.public_payload();base={'Id':M.PARENT,'Config':before,'RootFS':{'Layers':['sha256:'+'1'*64]*18}}
        after={'Id':'sha256:'+'2'*64,'Config':M.expected_config(before,payload),
            'RootFS':{'Layers':base['RootFS']['Layers']+['sha256:'+'3'*64]}}
        M.compare_images(base,after,payload)
        for key,value in (('User','0'),('Cmd',['different']),('Env',after['Config']['Env'][::-1]),('Unexpected',True)):
            changed=deepcopy(after);changed['Config'][key]=value
            with self.subTest(key=key),self.assertRaisesRegex(RuntimeError,'candidate_config_drift'):
                M.compare_images(base,changed,payload)
        changed=deepcopy(after);changed['RootFS']['Layers'][0]='sha256:'+'f'*64
        with self.assertRaisesRegex(RuntimeError,'candidate_rootfs_drift'):M.compare_images(base,changed,payload)

    def test_every_package_and_non_product_stack_hash_are_unchanged(self):
        value={'inventory':{k:v[0] for k,v in C.PACKAGES.items()},'non_product_stack_sha256':'a'*64,
            'files':10,'bytes':100,'libc_sha256':'b'*64}
        M.compare_inventory(value,deepcopy(value))
        for key in ('inventory','non_product_stack_sha256','files','bytes','libc_sha256'):
            changed=deepcopy(value)
            if key=='inventory':changed[key]['ordinary-extra']='1'
            elif type(changed[key]) is int:changed[key]+=1
            else:changed[key]='f'*64
            with self.subTest(key=key),self.assertRaisesRegex(RuntimeError,'non_product_stack_changed'):
                M.compare_inventory(value,changed)

    def test_recipe_has_only_public_overlay_no_install_and_restores_uid(self):
        before,payload=self.public_payload();program=M.replacement_program([],{'files':[]})
        ast.parse(program);dockerfile=M.dockerfile('security-market-profile-base-123-1:ci',payload,M.expected_config(before,payload))
        self.assertIn('USER 0:0\nRUN --network=none',dockerfile);self.assertIn('\nUSER 10001:10001\n',dockerfile)
        self.assertNotIn('apt-get',dockerfile);self.assertNotIn('pip install',dockerfile);self.assertNotIn('COPY',dockerfile)
        self.assertIn("roots=('core/market_intelligence','contracts/market_data','deploy/market-data/migrations')",program)
        self.assertIn("files=('core/__init__.py','config/market_data_sources.v1.json')",program)
        self.assertIn('verify_product_files',program)

    def test_replay_receipt_requires_only_added_ids_separate_from_unchanged_historical_suite(self):
        root=P.ROOT_FIXTURE_TEST_ID;old='tests.test_market_pipeline_stage4_capture.Fixture.test_old'
        added='tests.test_market_pipeline_stage4_capture.Fixture.test_replay';image='sha256:'+'a'*64
        payload={'historical':{'test_ids':sorted([root,old]),'expected_tests':2,'test_sources_sha256':'b'*64},
            'replay':{'test_ids':[added],'test_sources_sha256':'c'*64,'shared_method_changes':['Fixture.test_old'],
                'shared_support_method_changes':['Fixture.setUp']}}
        def receipt(uid,ids,scope,proof):
            return {'schema':'market_security_profile_tests/1','image_id':image,'test_sources_sha256':proof,
                'status':'passed','scope':scope,'tests':len(ids),'skips':0,'execution_uid':uid,'execution_gid':uid,
                'selected_test_ids':ids,'product_files_equal_baseline':True,'public_source_directory_mode':'0755',
                'suite_execution_thread':'owned_nonmain_test_thread','runtime_data_used':False,'security_accepted':False}
        normal=receipt(10001,[old],'nonroot_product_tests','b'*64)
        fixture=receipt(0,[root],'root_owned_metadata_fixture','b'*64)
        replay=receipt(10001,[added],'capture_replay_regressions','c'*64)
        result=M.validate_tests(payload,image,normal,fixture,replay)
        self.assertEqual(result['historical_tests'],2);self.assertEqual(result['capture_replay_test_ids'],[added])
        self.assertEqual(result['shared_adca_support_methods_changed_and_bound_to_replay_capsule'],['Fixture.setUp'])
        for key,value in (('selected_test_ids',[old]),('execution_uid',0),('tests',True),('skips',1)):
            changed={**replay,key:value}
            with self.assertRaisesRegex(RuntimeError,'profile_test_result'):M.validate_tests(payload,image,normal,fixture,changed)
        with self.assertRaisesRegex(RuntimeError,'profile_replay_inventory'):M.validate_tests(payload,image,normal,fixture)

    def test_strict_private_report_reader_does_not_adopt_scanner_022_publication(self):
        path=self.write(self.root/'raw.json',b'{}');path.chmod(0o644)
        with self.assertRaisesRegex(RuntimeError,'input_metadata'):M.read(path)
        path2=self.root/'owned-raw.json';M.write(path2,b'');old=os.umask(0o022)
        try:
            with path2.open('wb') as out:out.write(b'{}')
        finally:os.umask(old)
        self.assertEqual(M.read(path2),b'{}');self.assertEqual(path2.stat().st_mode&0o777,0o600)

    def test_raw_high_gate_remains_unwaived_and_type_strict(self):
        record={'schema':'market_security_profile_image_export/1','security_accepted':False,
            'raw_counts':{k:0 for k in ('UNKNOWN','LOW','MEDIUM','HIGH','CRITICAL')}}
        M.raw_gate(record);record['raw_counts']['HIGH']=44
        with self.assertRaisesRegex(RuntimeError,'raw_gate_failed'):M.raw_gate(record)
        record['raw_counts']['HIGH']=False
        with self.assertRaisesRegex(RuntimeError,'gate_receipt'):M.raw_gate(record)

    def test_every_severity_and_unfixed_row_remains_in_raw_reports(self):
        before,payload=self.public_payload();stack=json.loads((ROOT/Q.STACK[0]).read_bytes())
        payload['old_public_packages']=stack['profiles'][0]['public_installed_metadata']['packages']
        image='sha256:'+'a'*64;config=M.expected_config(before,payload)
        record={'image_id':image,'candidate_config':config,'candidate_layers':['sha256:'+'b'*64]}
        rows=[{'Severity':level,'VulnerabilityID':'CVE-ordinary-'+level,'PkgName':'ordinary','FixedVersion':''}
              for level in ('UNKNOWN','LOW','MEDIUM','HIGH','CRITICAL')]
        packages=[{'Name':q['name'],'Version':'2.9.13' if q['name']=='psycopg2-binary' else q['version'],
            'FilePath':'opt/market-python/ordinary.dist-info/METADATA'} for q in payload['old_public_packages']]
        raw={'SchemaVersion':2,'ArtifactType':'container_image','Metadata':{'ImageID':image,
            'ImageConfig':{'config':config,'rootfs':{'diff_ids':record['candidate_layers']}}},'Results':[
                {'Class':'os-pkgs','Type':'debian','Vulnerabilities':rows},
                {'Class':'lang-pkgs','Type':'python-pkg','Packages':packages}]}
        sbom={'bomFormat':'CycloneDX','metadata':{'component':{'type':'container','name':image,'properties':[
            {'name':'aquasecurity:trivy:ImageID','value':image}]}},'components':[{'name':n,'version':q[0]} for n,q in C.PACKAGES.items()]}
        original=M.encoded(raw);counts=M.validate_reports(record,payload,raw,sbom)
        self.assertEqual(counts,{level:1 for level in ('UNKNOWN','LOW','MEDIUM','HIGH','CRITICAL')})
        self.assertEqual(M.encoded(raw),original)
        raw['Results'][1]['Packages'][0]['Version']='unexpected'
        with self.assertRaisesRegex(RuntimeError,'raw_python_packages'):M.validate_reports(record,payload,raw,sbom)

    def evidence_fixture(self,*,changed_report=False):
        recipe={'recipe_commit':M.PARENT_SOURCE,'files':{}};layers=['sha256:'+'1'*64]*18
        reports={'ordinary-'+str(i)+'.json':b'{"public":true}' for i in range(19)}
        composition={'image_id':M.PARENT,'recipe':recipe,'candidate_layers':layers,
            'evidence_sha256':{n:sha256(b).hexdigest() for n,b in reports.items()}}
        raw=M.encoded({'Metadata':{'ImageID':M.PARENT,'ImageConfig':{'rootfs':{'diff_ids':layers},'config':{}}}})
        sbom=b'{"bomFormat":"CycloneDX"}'
        export={'schema':'market_os_composition_image_export/1','status':'exported_not_accepted','image_id':M.PARENT,
            'recipe':recipe,'composition_sha256':sha256(M.encoded(composition)).hexdigest(),
            'evidence_sha256':composition['evidence_sha256'],
            'reports_sha256':{'vulnerabilities.json':sha256(raw).hexdigest(),'sbom.cdx.json':sha256(sbom).hexdigest()},
            'whole_config_unchanged':True,'product_files_equal_baseline':True,'vendor_debs_reused':True,'historical_tests':85,
            **{k:False for k in ('app_or_market_recompiled','full_runtime_matrix_completed','security_accepted',
                               'findings_waived','deployment_eligible','deployment_performed')}}
        members={'image-export.json':M.encoded(export),'source/identity.json':M.encoded(recipe),
            'reports/composition.json':M.encoded(composition),'reports/vulnerabilities.json':raw,'reports/sbom.cdx.json':sbom,
            'reports/base-alias-cleanup.json':M.encoded({'status':'removed_exact_owned_alias','security_accepted':False}),
            **{'reports/'+n:b for n,b in reports.items()},'reports/ordinary-phase.json':b'{}'}
        self.assertEqual(len(members),26)
        if changed_report:members['reports/ordinary-0.json']=b'{"public":false}'
        path=self.root/'evidence.zip'
        with ZipFile(path,'w') as archive:
            for n,b in members.items():archive.writestr(n,b)
        path.chmod(0o600);buf=path.read_bytes()
        self.stack.enter_context(patch.object(M,'EVIDENCE_ARTIFACT',(12580311665,len(buf),sha256(buf).hexdigest())))
        self.stack.enter_context(patch.object(M,'EXPORT_SHA',sha256(M.encoded(export)).hexdigest()))
        return path

    def test_parent_small_evidence_all_report_hashes_are_checked_before_image_commands(self):
        value=M.admit_parent_evidence(self.evidence_fixture())
        self.assertEqual(value['export']['image_id'],M.PARENT)

    def test_parent_changed_public_report_refuses_even_with_valid_outer_fixture_zip(self):
        with self.assertRaisesRegex(RuntimeError,'parent_evidence_hash'):
            M.admit_parent_evidence(self.evidence_fixture(changed_report=True))

    def test_cli_unknown_errors_are_content_free(self):
        with patch.object(M,'ci',side_effect=ValueError('ordinary-unrecognized-public-detail')):
            output=io.StringIO()
            with redirect_stderr(output):self.assertEqual(M.main(['gate','--root',str(self.root)]),1)
        self.assertNotIn('ordinary-unrecognized-public-detail',output.getvalue())
        for code,expected in (('baseline_test_missing','baseline_test_missing'),('ordinary-unrecognized-public-detail','unexpected_failure')):
            with patch.object(M,'compose',side_effect=P.PayloadError(code)):
                output=io.StringIO()
                with redirect_stderr(output):self.assertEqual(M.main(['compose','--root',str(self.root),'--repo',str(ROOT),'--profile','2239469ab3dd']),1)
                self.assertEqual(json.loads(output.getvalue())['code'],expected)

    def lifecycle(self,*,omit_report_precreation=False,premature_cleanup=False):
        """Exercise the real controller; only named external I/O is modeled."""
        (self.root/'source').mkdir()
        self.write(self.root/'source/verify_image_component_presence.py',(ROOT/'scripts/verify_image_component_presence.py').read_bytes())
        self.stack.enter_context(patch.object(M,'ci'))
        self.stack.enter_context(patch.object(M.os,'statvfs',return_value=SimpleNamespace(f_bavail=20*1024**3,f_frsize=1)))
        identity={'recipe_commit':'a'*40,'files':{}}
        @contextmanager
        def sealed(root):yield identity
        self.stack.enter_context(patch.object(M,'sources',sealed))
        before_config,payload=self.public_payload();payload['profile']='f79cdda21c19'
        ids=sorted([P.ROOT_FIXTURE_TEST_ID]+['tests.test_market_pipeline_stage4_capture.Fixture.test_'+str(i) for i in range(79)])
        payload.update({'files':[{'path':'core/__init__.py','mode':'100644','bytes':1,'sha256':sha256(b'x').hexdigest()}],
            'old_public_packages':[{'name':n,'version':v} for n,v in [('Telethon','1.44.0'),('annotated-types','0.7.0'),
                ('psycopg2-binary','2.9.9'),('pyaes','1.6.1'),('pyasn1','0.6.4'),('pydantic','2.8.2'),
                ('pydantic_core','2.20.1'),('rsa','4.9.1'),('typing_extensions','4.15.0')]],
            'historical':{'test_ids':ids,'collected_test_ids':ids,'expected_tests':80,'test_sources_sha256':'b'*64,
                'baseline_commit':'c'*40,'modules':list(P.TEST_MODULES)},'replay':None})
        base_layers=['sha256:'+'1'*64]*18;new_layers=base_layers+['sha256:'+'2'*64];image='sha256:'+'3'*64
        before={'Id':M.PARENT,'Config':before_config,'RootFS':{'Type':'layers','Layers':base_layers},'Os':'linux','Architecture':'amd64'}
        expected=M.expected_config(before_config,payload)
        after={'Id':image,'Config':expected,'RootFS':{'Type':'layers','Layers':new_layers},'Os':'linux','Architecture':'amd64'}
        evidence={'config':{'config':before_config,'rootfs':{'diff_ids':base_layers}},'raw_sha256':'d'*64,'sbom_sha256':'e'*64}
        state={'tagged':False,'parent_present':True};calls=[]
        def docker(root,argv,**kwargs):
            calls.append(argv)
            if argv[:2]==['image','load']:return b'ordinary fixture loaded'
            if argv[:2]==['image','ls']:return M.PARENT.encode() if state['tagged'] else b''
            if argv[:2]==['image','tag']:state['tagged']=True;return b''
            if argv[:2]==['image','inspect']:
                target=argv[2]
                if target==image:return M.encoded([after])
                require=state['parent_present']
                if not require:raise RuntimeError('fixture_parent_deleted')
                return M.encoded([before])
            if argv[:2]==['image','rm']:
                self.assertEqual(argv[2],'--no-prune');state['tagged']=False;state['parent_present']=False;return b''
            if argv[0]=='build':
                prepared=json.loads((root/'reports/profile-payload.json').read_bytes());payload.update(prepared)
                expected.clear();expected.update(M.expected_config(before_config,prepared))
                self.write(root/'candidate.id',image.encode());self.write(root/'reports/build.log',b'ordinary build');return b''
            raise AssertionError('unexpected mocked docker command')
        self.stack.enter_context(patch.object(C,'docker',side_effect=docker))
        self.stack.enter_context(patch.object(C,'fetch_artifact',side_effect=lambda identifier,path,*args:self.write(path,b'public artifact fixture')))
        self.stack.enter_context(patch.object(M,'admit_parent_evidence',return_value=evidence))
        def admission(path,output,evidence):
            output.mkdir();raw=b'ordinary authenticated archive fixture';self.write(output/'candidate-image.tar',raw)
            return {'docker_archive_bytes':len(raw),'docker_archive_sha256':sha256(raw).hexdigest(),'original_oci_config':before_config}
        self.stack.enter_context(patch.object(M,'admit_parent_image',side_effect=admission))
        def prepare(repo,commit,output,profile,market):
            output.mkdir();(output/'product').mkdir();(output/'tests').mkdir();return payload
        self.stack.enter_context(patch.object(Q,'prepare',side_effect=prepare))
        self.stack.enter_context(patch.object(O,'admit_evidence',return_value={'payload':{'files':[
            {'path':'core/market_intelligence/file'+str(i)+'.py'} for i in range(93)]}}))
        self.stack.enter_context(patch.object(C,'inspect_builder',side_effect=lambda root:self.write(root/'reports/builder-inspect.log',b'ordinary builder')))
        inventory={'inventory':{n:r[0] for n,r in C.PACKAGES.items()},'non_product_stack_sha256':'f'*64,
            'files':10,'bytes':100,'libc_sha256':'a'*64}
        def test_receipt(root_fixture=False):
            selected=[P.ROOT_FIXTURE_TEST_ID] if root_fixture else [n for n in ids if n!=P.ROOT_FIXTURE_TEST_ID]
            uid=0 if root_fixture else 10001
            return {'schema':'market_security_profile_tests/1','image_id':image,'test_sources_sha256':'b'*64,
                'status':'passed','tests':len(selected),'skips':0,'execution_uid':uid,'execution_gid':uid,
                'selected_test_ids':selected,'scope':'root_owned_metadata_fixture' if root_fixture else 'nonroot_product_tests',
                'public_source_directory_mode':'0755','suite_execution_thread':'owned_nonmain_test_thread',
                'product_files_equal_baseline':True,'runtime_data_used':False,'security_accepted':False}
        def isolated(root,image_arg,program,purpose,**kwargs):
            self.write(root/'reports'/(purpose+'-run.log'),b'ordinary isolated log')
            if purpose in ('before','after'):return M.encoded(inventory)
            if purpose=='market-native':return M.encoded({'schema':'market_security_native_checks/1','image_id':image,'status':'passed',
                'pydantic':'2.8.2','psycopg2':'2.9.13','execution_uid':10001,'execution_gid':10001,'runtime_data_used':False,'security_accepted':False})
            if purpose=='market-profile-tests':return M.encoded(test_receipt())
            raise AssertionError('unexpected isolated fixture')
        self.stack.enter_context(patch.object(C,'isolated',side_effect=isolated))
        def root_fixture(*args):
            self.write(self.root/'reports/market-root-fixture-run.log',b'ordinary root metadata fixture');return M.encoded(test_receipt(True))
        self.stack.enter_context(patch.object(P,'root_fixture_container',side_effect=root_fixture))
        component={'schema':'security_image_component_presence/1','image_id':image,'source_commit':'a'*40,
            'verifier_sha256':M.HELPERS['verify_image_component_presence.py'],
            'scope':'known_vendor_component_paths_and_names_plus_actual_perl_inc',**{k:False for k in (
                'security_accepted','scanner_findings_modified','production_mounts_or_data_used','dynamic_runtime_code_downloads_analyzed',
                'renamed_or_obfuscated_embedded_code_analyzed','directory_symlinks_followed')}}
        self.stack.enter_context(patch.object(M,'COMPONENT',SimpleNamespace(in_container=Mock(return_value=M.encoded(component)))))
        def acl(root,*args):
            self.write(root/'reports/acl-consumer-presence.json',b'{}');self.write(root/'reports/acl-run.log',b'ordinary ACL inventory')
        self.stack.enter_context(patch.object(C,'acl_consumer_probe',side_effect=acl))
        original_write=M.write
        if omit_report_precreation:
            self.stack.enter_context(patch.object(M,'write',side_effect=lambda path,raw,*args:
                None if not raw and path.name in ('vulnerabilities.json','sbom.cdx.json') else original_write(path,raw,*args)))
        M.compose(self.root,ROOT,'f79cdda21c19')
        raw={'SchemaVersion':2,'ArtifactType':'container_image','Metadata':{'ImageID':image,
            'ImageConfig':{'config':expected,'rootfs':{'diff_ids':new_layers}}},'Results':[
                {'Class':'os-pkgs','Type':'debian','Vulnerabilities':[{'Severity':'HIGH','VulnerabilityID':'CVE-ordinary','PkgName':'ordinary'}]},
                {'Class':'lang-pkgs','Type':'python-pkg','Packages':[{'Name':n['name'],'Version':'2.9.13' if n['name']=='psycopg2-binary' else n['version'],
                    'FilePath':'opt/market-python/ordinary.dist-info/METADATA'} for n in payload['old_public_packages']]}]}
        sbom={'bomFormat':'CycloneDX','metadata':{'component':{'type':'container','name':image,'properties':[
            {'name':'aquasecurity:trivy:ImageID','value':image}]}},'components':[{'name':n,'version':r[0]} for n,r in C.PACKAGES.items()]}
        old=os.umask(0o022)
        try:
            for name,value in (('vulnerabilities.json',raw),('sbom.cdx.json',sbom)):
                with (self.root/'reports'/name).open('wb') as out:out.write(M.encoded(value))
        finally:os.umask(old)
        archive_body=b'ordinary export validation fixture';compressed=gzip.compress(archive_body,mtime=0)
        def export(root,identifier):
            self.assertTrue(state['parent_present']);self.write(root/'candidate-image.tar.gz',compressed)
            return {'size_bytes':len(compressed),'sha256':sha256(compressed).hexdigest(),'uncompressed_bytes':len(archive_body)}
        self.stack.enter_context(patch.object(C,'export_image',side_effect=export))
        proof={'layers':19,'whole_oci_config':{'rootfs':{'diff_ids':new_layers}}}
        self.stack.enter_context(patch.object(M,'validate_archive',return_value=proof))
        if premature_cleanup:M.cleanup(self.root)
        M.finalize(self.root)
        result=json.loads((self.root/'image-export.json').read_bytes());self.assertTrue(result['new_layer_product_scope_verified'])
        self.assertFalse(result['security_accepted']);self.assertEqual(result['raw_counts']['HIGH'],1)
        self.assertTrue(state['parent_present']);M.cleanup(self.root);self.assertFalse(state['parent_present'])
        return result,calls

    def test_real_controller_lifecycle_preserves_alias_through_export_and_raw_highs(self):
        result,calls=self.lifecycle();self.assertEqual(result['historical_tests'],80)
        self.assertEqual(calls[-1][0:3],['image','rm','--no-prune'])
        with self.assertRaisesRegex(RuntimeError,'raw_gate_failed'):M.raw_gate(result)

    def test_premature_last_alias_cleanup_fails_real_finalizer(self):
        with self.assertRaisesRegex(RuntimeError,'fixture_parent_deleted'):self.lifecycle(premature_cleanup=True)

    def test_missing_0600_report_precreation_fails_real_finalizer_after_022_scanner_write(self):
        with self.assertRaisesRegex(RuntimeError,'input_metadata'):self.lifecycle(omit_report_precreation=True)


if __name__=='__main__':unittest.main()
