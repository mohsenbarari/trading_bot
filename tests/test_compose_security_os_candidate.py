"""Owned public fixtures only; every ambient command and network is denied."""
from contextlib import ExitStack,contextmanager,redirect_stderr
from copy import deepcopy
from hashlib import sha256
import ast
import base64
import gzip
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tarfile
import tempfile
import types
import unittest
from unittest.mock import Mock,patch
from zipfile import ZipFile

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'scripts/compose_security_os_candidate.py'
WORKFLOW=ROOT/'.github/workflows/security-os-candidate-compose.yml'
spec=importlib.util.spec_from_file_location('os_composition',SCRIPT)
M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)


class CompositionTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        for name in ('subprocess.run','subprocess.Popen','os.system','os.kill','os.killpg','socket.socket',
                     'socket.create_connection','socket.getaddrinfo','urllib.request.urlopen','ctypes.CDLL'):
            self.stack.enter_context(patch(name,side_effect=AssertionError('ambient_host_network_forbidden')))
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='compose-public-')))
        self.root.chmod(0o700)
        previous=os.umask(0o077);self.addCleanup(os.umask,previous)

    def test_exact_fixed_input_scope_and_old_tests(self):
        self.assertEqual(len(M.PACKAGES),15);self.assertEqual([len(r[5]) for r in M.GROUPS.values()],[9,4,2])
        self.assertEqual([r[1] for r in M.GROUPS.values()],[12567076825,12567123180,12567979094])
        self.assertEqual(M.EVIDENCE[0],12553191740);self.assertEqual(len(M.TESTS),6)
        self.assertEqual(M.TESTS['test_coin_intelligence_coin_rate_engine.py'][1],'7b94be9e424c290e9738fd8e6566733582a3ddf3')
        self.assertTrue(M.PACKAGES['bsdutils'][0].startswith('1:'))
        self.assertTrue(M.PACKAGES['login'][0].startswith('1:4.16.0-2+really'))

    def test_host_entrypoints_refuse_before_any_command(self):
        with patch.dict(os.environ,{'GITHUB_ACTIONS':'false'}):
            for call in (M.compose,M.finalize,M.cleanup):
                with self.assertRaisesRegex(RuntimeError,'github_ci_only'):call(self.root)
            output=io.StringIO()
            with redirect_stderr(output):self.assertEqual(M.main(['compose','--root',str(self.root)]),1)
            self.assertEqual(json.loads(output.getvalue())['code'],'github_ci_only')
            self.assertFalse((self.root/'reports').exists())

    def test_stable_read_refuses_bounds_links_and_rewrites(self):
        path=self.root/'public';path.write_bytes(b'public fixture')
        self.assertEqual(M.read(path),b'public fixture')
        with patch.object(M.os,'read',side_effect=AssertionError('body_read')):
            with self.assertRaisesRegex(RuntimeError,'input_metadata'):M.read(path,1)
        os.link(path,self.root/'alias')
        with self.assertRaisesRegex(RuntimeError,'input_metadata'):M.read(path)
        (self.root/'alias').unlink()
        with self.assertRaisesRegex(RuntimeError,'input_changed'):
            with M.pinned(path,100) as (stream,_):
                self.assertEqual(stream.read(),b'public fixture');path.write_bytes(b'changed fixture')

    def test_stream_hash_is_bounded_before_unadvertised_bytes(self):
        self.assertEqual(M.stream_digest(io.BytesIO(b'ordinary'),8),sha256(b'ordinary').hexdigest())
        with self.assertRaisesRegex(RuntimeError,'stream_bound'):M.stream_digest(io.BytesIO(b'ordinary extra'),8)
        with self.assertRaisesRegex(RuntimeError,'stream_size'):M.stream_digest(io.BytesIO(b'short'),8)

    def package_fixture(self, *, damaged=False):
        data=b'ordinary public DEB fixture'
        row={'name':'fixture','version':'1~trixie+gold1','bytes':len(data),'sha256':sha256(data).hexdigest(),'path':'fixture.deb'}
        receipt={'selected_runtime_packages':['fixture'],'all_family_packages':[row],
                 'security_accepted':False,'production_installed':False,'status':'candidate_built_not_accepted','abi':{'status':'passed'}}
        receipt_raw=json.dumps(receipt).encode();buffer=io.BytesIO()
        with ZipFile(buffer,'w') as z:
            z.writestr('build-receipt.json',receipt_raw);z.writestr('fixture.deb',b'different ordinary data' if damaged else data)
        raw=buffer.getvalue();path=self.root/'fixture.zip';path.write_bytes(raw)
        group=(1,2,len(raw),sha256(raw).hexdigest(),sha256(receipt_raw).hexdigest(),{'fixture'},2)
        return path,row,group

    def test_package_admission_hashes_and_parses_same_public_snapshot(self):
        path,row,group=self.package_fixture();target=self.root/'capsule';target.mkdir()
        with patch.dict(M.GROUPS,{'fixture':group}),patch.dict(M.PACKAGES,{'fixture':(row['version'],row['bytes'],row['sha256'])}):
            record=M.admit_packages(path,'fixture',target)
        self.assertEqual(record['packages'],[row]);self.assertEqual((target/'fixture.deb').read_bytes(),b'ordinary public DEB fixture')
        self.assertEqual(stat.S_IMODE((target/'fixture.deb').stat().st_mode),0o644)

    def test_package_digest_refuses_before_output_and_member_bytes_rechecked(self):
        path,row,group=self.package_fixture(damaged=True);target=self.root/'capsule';target.mkdir()
        with patch.dict(M.GROUPS,{'fixture':group}),patch.dict(M.PACKAGES,{'fixture':(row['version'],row['bytes'],row['sha256'])}):
            with self.assertRaisesRegex(RuntimeError,'package_bytes'):M.admit_packages(path,'fixture',target)
            bad=(*group[:3],'0'*64,*group[4:])
            with patch.dict(M.GROUPS,{'fixture':bad}),self.assertRaisesRegex(RuntimeError,'package_zip_identity'):
                M.admit_packages(path,'fixture',target)
        self.assertFalse(tuple(target.iterdir()))

    def test_test_blob_download_never_accepts_current_or_changed_source(self):
        raw=b'print("ordinary fixture")\n';blob=M.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
        row=(len(raw),blob,sha256(raw).hexdigest());record={'sha':blob,'size':len(raw),'encoding':'base64','content':base64.b64encode(raw).decode()}
        with patch.object(M,'TESTS',{'fixture.py':row}),patch.object(M,'command',return_value=json.dumps(record).encode()) as call, \
                patch.dict(os.environ,{'GH_TOKEN':'public-test-token'}):
            M.fetch_tests(self.root)
        self.assertEqual((self.root/'fixture.py').read_bytes(),raw)
        self.assertEqual(call.call_args.args[0],['gh','api','repos/mohsenbarari/trading_bot/git/blobs/'+blob])
        (self.root/'fixture.py').unlink();record['content']=base64.b64encode(b'changed fixture').decode()
        with patch.object(M,'TESTS',{'fixture.py':row}),patch.object(M,'command',return_value=json.dumps(record).encode()), \
                patch.dict(os.environ,{'GH_TOKEN':'public-test-token'}),self.assertRaisesRegex(RuntimeError,'test_blob_identity'):
            M.fetch_tests(self.root)
        self.assertFalse((self.root/'fixture.py').exists())

    def test_generated_dockerfile_preserves_all_runtime_directives(self):
        text=M.dockerfile_for('security-os-base-123-1:ci');lines=text.splitlines()
        self.assertEqual(len(lines),2);self.assertEqual(lines[0],'FROM security-os-base-123-1:ci')
        self.assertTrue(lines[1].startswith('RUN --network=none '))
        self.assertIn('source=capsule,target=/capsule,ro',text);self.assertIn('source=installer.py,target=/compose-installer.py,ro',text)
        argv=json.loads(lines[1][lines[1].index('['):])
        self.assertEqual(argv[0],'/usr/bin/setpriv');self.assertIn('--no-new-privs',argv)
        self.assertEqual(argv[1],'--bounding-set=-all,+chown,+fowner,+fsetid,+setuid,+setgid')
        self.assertNotIn('COPY ',text);self.assertNotIn('USER ',text);self.assertNotIn('LABEL ',text)
        with self.assertRaisesRegex(RuntimeError,'base_alias'):M.dockerfile_for('external-repository:latest')

    def test_documented_builder_output_and_actual_cli_construction(self):
        # Official docker/buildx v0.29.1 commands/inspect.go prints this shape;
        # inspect exposes --bootstrap but no --format flag.
        raw=b'Name:          default\nDriver:        docker\n\nNodes:\nName:          default\nEndpoint:      unix:///var/run/docker.sock\nStatus:        running\nBuildKit version: v0.25.1\n'
        calls=[]
        def docker(root,args,**kwargs):
            calls.append(args)
            if '--format' in args:raise RuntimeError('command_failed')
            self.assertEqual(args,['buildx','inspect','default'])
            self.assertTrue(kwargs['combine_stderr']);self.assertEqual(kwargs['maximum'],65536)
            self.assertEqual(kwargs['log_path'],root/'reports/builder-inspect.log')
            return raw
        with patch.object(M,'docker',side_effect=docker):
            self.assertEqual(M.inspect_builder(self.root),raw)
            with self.assertRaisesRegex(RuntimeError,'command_failed'):
                M.docker(self.root,['buildx','inspect','default','--format','{{.Driver}}'])
        self.assertEqual(calls[0],['buildx','inspect','default'])
        for changed in (raw.replace(b'Driver:        docker',b'Driver:        docker-container'),
                        raw.replace(b'Name:          default',b'Name:          different',1),
                        raw.replace(b'Status:        running',b'Status:        stopped'),
                        raw+b'Error: context deadline exceeded\n',raw+b'Driver: docker\n'):
            with self.assertRaisesRegex(RuntimeError,'local_docker_builder_required'):M.parse_builder(changed)

    def test_phase_failure_survives_later_cleanup_and_is_atomically_written(self):
        M.phase(self.root,'inspect_builder')
        M.failed_phase(self.root,RuntimeError('command_failed'))
        M.phase(self.root,'cleanup_base_alias')
        value=M.failed_phase(self.root,RuntimeError('base_alias_cleanup_owner'))
        self.assertEqual(value['first_failure_phase'],'inspect_builder')
        self.assertEqual(value['first_failure_code'],'command_failed')
        self.assertEqual(value['last_failure_code'],'base_alias_cleanup_owner')
        self.assertEqual(M.phase_state(self.root),value)
        self.assertEqual(stat.S_IMODE((self.root/'reports/phase.json').stat().st_mode),0o600)
        self.assertFalse(tuple((self.root/'reports').glob('.phase-*')))
        with self.assertRaisesRegex(RuntimeError,'phase_invalid'):M.phase(self.root,'not-an-admitted-phase')

    def test_main_reports_only_closed_codes_without_exception_text(self):
        M.phase(self.root,'inspect_builder');output=io.StringIO()
        text='ordinary fixture argument must not enter diagnostics'
        with patch.object(M,'compose',side_effect=RuntimeError(text)),patch.object(M,'ci_root'),redirect_stderr(output):
            self.assertEqual(M.main(['compose','--root',str(self.root)]),1)
        record=json.loads(output.getvalue())
        self.assertEqual(record['phase'],'inspect_builder');self.assertEqual(record['code'],'unclassified_refusal')
        self.assertNotIn(text,output.getvalue());self.assertNotIn(text,(self.root/'reports/phase.json').read_text())
        self.assertFalse(record['security_accepted']);self.assertFalse(record['deployment_performed'])

    def test_public_error_catalog_covers_static_refusals_and_hides_unknown_values(self):
        tree=ast.parse(SCRIPT.read_text());codes=set()
        for node in ast.walk(tree):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Name):
                index=1 if node.func.id=='require' else 0 if node.func.id=='RuntimeError' else None
                if index is not None and len(node.args)>index and isinstance(node.args[index],ast.Constant) and type(node.args[index].value) is str:
                    codes.add(node.args[index].value)
        self.assertLessEqual(codes,M.PUBLIC_FAILURE_CODES)
        self.assertEqual(M.public_error(RuntimeError('command_failed')),'command_failed')
        self.assertEqual(M.public_error(RuntimeError('unlisted fixture value')),'unclassified_refusal')
        self.assertEqual(M.public_error(FileNotFoundError('fixture path')),'input_unavailable')
        self.assertEqual(M.public_error(ValueError('fixture argument')),'unexpected_failure')

    def test_package_failure_prevents_any_docker_command(self):
        admission=Mock(IMAGE=M.IMAGE,SOURCE=M.SOURCE,ARTIFACT_ID=12552928309,ZIP_BYTES=1,ZIP_SHA256='a'*64)
        with patch.object(M,'ci_root'),patch.object(M,'load_sealed',return_value=({}, {'admit_security_component_image_reuse.py':admission})), \
                patch.object(M.os,'statvfs',return_value=Mock(f_bavail=16*1024**3,f_frsize=1)), \
                patch.object(M,'fetch_artifact'),patch.object(M,'admit_packages',side_effect=RuntimeError('known_package_refused')), \
                patch.object(M,'docker',side_effect=AssertionError('docker_before_admission')) as docker:
            with self.assertRaisesRegex(RuntimeError,'known_package_refused'):M.compose(self.root)
        docker.assert_not_called();admission.admit_image.assert_not_called()
        self.assertEqual(M.phase_state(self.root)['phase'],'admit_util')

    def test_missing_historical_blob_never_falls_back_to_checkout_or_build(self):
        admission=Mock(IMAGE=M.IMAGE,SOURCE=M.SOURCE,ARTIFACT_ID=12552928309,ZIP_BYTES=1,ZIP_SHA256='a'*64)
        def packages(path,group,target):
            return {'packages':[{'name':name,'path':name+'.deb','version':M.PACKAGES[name][0],
                                 'bytes':M.PACKAGES[name][1],'sha256':M.PACKAGES[name][2]} for name in M.GROUPS[group][5]]}
        with patch.object(M,'ci_root'),patch.object(M,'load_sealed',return_value=({}, {'admit_security_component_image_reuse.py':admission})), \
                patch.object(M.os,'statvfs',return_value=Mock(f_bavail=16*1024**3,f_frsize=1)), \
                patch.object(M,'fetch_artifact'),patch.object(M,'admit_packages',side_effect=packages), \
                patch.object(M,'admit_test_proof',return_value=b'sealed historical proof'), \
                patch.object(M,'fetch_tests',side_effect=RuntimeError('fixed_blob_unavailable')), \
                patch.object(M,'docker',side_effect=AssertionError('docker_before_sources')) as docker:
            with self.assertRaisesRegex(RuntimeError,'fixed_blob_unavailable'):M.compose(self.root)
        docker.assert_not_called();admission.admit_image.assert_not_called()
        self.assertEqual(M.phase_state(self.root)['phase'],'fetch_test_sources')

    def image(self, prefix='a', layers=None):
        return {'Id':'sha256:'+prefix*64,'Os':'linux','Architecture':'amd64',
                'Config':{'Cmd':['python3'],'Env':['LANG=C.UTF-8'],'Labels':{'source':M.SOURCE},'WorkingDir':'/app'},
                'RootFS':{'Type':'layers','Layers':layers or ['sha256:'+'1'*64,'sha256:'+'2'*64]}}

    def test_whole_config_and_ordered_layer_prefix_are_exact(self):
        before=self.image();after=self.image('b',before_layers:=before['RootFS']['Layers']+['sha256:'+'3'*64])
        M.compare_images(before,after)
        for key,value in (('User','0'),('Entrypoint',['python3']),('Cmd',['installer']),('Env',['DEBIAN_FRONTEND=noninteractive']),
                          ('Labels',{'owner':'temporary'}),('Healthcheck',None),('WorkingDir','/tmp')):
            changed=deepcopy(after);changed['Config'][key]=value
            with self.subTest(key=key),self.assertRaisesRegex(RuntimeError,'runtime_config_changed'):M.compare_images(before,changed)
        for layers in (list(reversed(before_layers)),before['RootFS']['Layers'],['sha256:'+'4'*64,*before_layers[1:]]):
            changed=deepcopy(after);changed['RootFS']['Layers']=layers
            with self.assertRaisesRegex(RuntimeError,'baseline_layer_prefix_changed'):M.compare_images(before,changed)
        self.assertEqual(M.image_record(json.dumps([before]).encode(),before['Id']),before)

    def test_actual_archive_config_retains_optional_public_repo_tags(self):
        value={'config':{'Cmd':['python3']},'rootfs':{'diff_ids':['sha256:'+'1'*64]}}
        raw=M.encoded(value);image='sha256:'+sha256(raw).hexdigest();name=image[7:]+'.json'
        for index,tags in enumerate((None,[],['public-owned-fixture:ci'])):
            path=self.root/('archive-'+str(index)+'.tar');manifest={'Config':name,'Layers':[]}
            if tags is not None:manifest['RepoTags']=tags
            with tarfile.open(path,'w') as archive:
                for member,data in (('manifest.json',M.encoded([manifest])),(name,raw)):
                    info=tarfile.TarInfo(member);info.size=len(data);archive.addfile(info,io.BytesIO(data))
            self.assertEqual(M.archive_config(path,image),value)
            self.assertEqual(M.archive_config(path,image,include_tags=True),(value,tags))

    def inventories(self):
        values={name:'old-version' for name in M.PACKAGES};values.update(libc6='2.41-12+deb13u4',unchanged='1')
        old={'inventory':values,'app_and_python_sha256':'a'*64,'files':5,'bytes':100,'libc_sha256':'b'*64}
        new=deepcopy(old);new['inventory'].update({name:row[0] for name,row in M.PACKAGES.items()})
        return old,new

    def composed_untagged_store(self):
        # Named Docker boundary models documented last-tag eviction. All source,
        # package, process and export boundaries remain explicit owned fixtures.
        baseline=self.image();baseline['Id']=M.IMAGE;baseline['RepoTags']=[]
        candidate=self.image('b',baseline['RootFS']['Layers']+['sha256:'+'3'*64])
        original,installed=self.inventories();identity={'recipe_commit':'f'*40}
        state={'baseline':True,'candidate':False,'tags':{},'events':[],'baseline_record':baseline,'candidate_record':candidate}
        component={'schema':'security_image_component_presence/1','image_id':candidate['Id'],'source_commit':M.SOURCE,
            'verifier_sha256':M.PROBE_SHA,'scope':'known_vendor_component_paths_and_names_plus_actual_perl_inc'}
        for key in ('security_accepted','scanner_findings_modified','production_mounts_or_data_used',
                    'dynamic_runtime_code_downloads_analyzed','renamed_or_obfuscated_embedded_code_analyzed','directory_symlinks_followed'):
            component[key]=False
        @contextmanager
        def public_archive(path,maximum):yield io.BytesIO(b'x'),1
        admission=Mock(IMAGE=M.IMAGE,SOURCE=M.SOURCE,ARTIFACT_ID=12552928309,ZIP_BYTES=1,ZIP_SHA256='a'*64,
                       MAX_TAR=100,pinned=public_archive,digest=Mock(return_value='c'*64))
        admission.admit_image.return_value={'docker_archive_bytes':1,'docker_archive_sha256':'c'*64,'export_provenance':{'source':M.SOURCE}}
        probe=Mock(in_container=Mock(return_value=M.encoded(component)))
        (self.root/'source').mkdir();(self.root/'source/verify_image_component_presence.py').write_bytes(b'owned public fixture')
        def packages(path,group,target):
            return {'packages':[{'name':name,'path':name+'.deb','version':M.PACKAGES[name][0],
                'bytes':M.PACKAGES[name][1],'sha256':M.PACKAGES[name][2]} for name in M.GROUPS[group][5]]}
        def docker(root,args,**kwargs):
            state['events'].append(tuple(args))
            if args==['image','load']:return b'loaded ordinary fixture'
            if args[:2]==['image','tag']:
                self.assertTrue(state['baseline']);self.assertEqual(args[2],M.IMAGE);state['tags'][args[3]]=M.IMAGE;return b''
            if args[:2]==['image','ls']:
                self.assertEqual(args[:4],['image','ls','--quiet','--no-trunc'])
                value=state['tags'].get(args[4]);return (value+'\n').encode() if value else b''
            if args[:2]==['image','rm']:
                self.assertEqual(args[:3],['image','rm','--no-prune'])
                self.assertEqual(state['tags'].pop(args[3]),M.IMAGE)
                if M.IMAGE not in state['tags'].values():state['baseline']=False
                return b'untagged and removed ordinary fixture image'
            if args[:2]==['image','inspect']:
                target=args[2]
                if target==candidate['Id'] and state['candidate']:return M.encoded([candidate])
                if (target==M.IMAGE or state['tags'].get(target)==M.IMAGE) and state['baseline']:return M.encoded([baseline])
                raise RuntimeError('command_failed')
            if args==['buildx','inspect','default']:
                raw=b'Name: default\nDriver: docker\nStatus: running\n';M.write(kwargs['log_path'],raw);return raw
            if args[0]=='build':
                self.assertTrue(state['baseline']);state['candidate']=True
                M.write(root/'candidate.id',(candidate['Id']+'\n').encode());M.write(kwargs['log_path'],b'ordinary public build fixture\n');return b''
            raise AssertionError('unexpected Docker fixture command')
        def isolated(root,image,program,purpose,**kwargs):
            if purpose=='before':value=original
            elif purpose=='after':value=installed
            elif purpose=='focused':value={'image_id':candidate['Id'],'tests':56,'modules':5,'status':'passed'}
            else:self.assertEqual(purpose,'native');value={'status':'passed'}
            raw=M.encoded(value);M.write(root/'reports'/(purpose+'-run.log'),raw);return raw
        def archive_config(path,image,*,include_tags=False):
            self.assertEqual(image,M.IMAGE);value={'config':baseline['Config'],'rootfs':{'diff_ids':baseline['RootFS']['Layers']}}
            return (value,[]) if include_tags else value
        def export(root,image):
            self.assertTrue(state['baseline']);self.assertEqual(image,candidate['Id']);state['events'].append(('export',image))
            return {'name':'candidate-image.tar.gz','format':'docker-archive+gzip','size_bytes':1,'sha256':'d'*64,'uncompressed_bytes':1}
        for name,value in {'ci_root':Mock(),'load_sealed':Mock(return_value=(identity,{'admit_security_component_image_reuse.py':admission,
                'verify_image_component_presence.py':probe})),'fetch_artifact':Mock(),'admit_packages':packages,
                'admit_test_proof':Mock(return_value=b'owned public proof'),'fetch_tests':Mock(),'docker':docker,
                'isolated':isolated,'archive_config':archive_config,'export_image':export}.items():
            self.stack.enter_context(patch.object(M,name,value))
        self.stack.enter_context(patch.object(M.os,'statvfs',return_value=Mock(f_bavail=16*1024**3,f_frsize=1)))
        self.stack.enter_context(patch.dict(os.environ,{'GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1','GITHUB_SHA':'f'*40,
                                                       'GITHUB_OUTPUT':str(self.root/'github-output')}))
        M.compose(self.root)
        record=json.loads((self.root/'reports/composition.json').read_bytes())
        raw={'SchemaVersion':2,'ArtifactType':'container_image','Metadata':{'ImageID':candidate['Id'],
            'ImageConfig':{'config':baseline['Config'],'rootfs':{'diff_ids':candidate['RootFS']['Layers']}}},'Results':[{'Vulnerabilities':[{'Severity':'HIGH'}]}]}
        sbom={'metadata':{'component':{'type':'container','name':candidate['Id'],
                'properties':[{'name':'aquasecurity:trivy:ImageID','value':candidate['Id']}]}},
            'components':[{'name':name,'version':row[0]} for name,row in M.PACKAGES.items()]}
        (self.root/'reports/vulnerabilities.json').write_bytes(M.encoded(raw));(self.root/'reports/sbom.cdx.json').write_bytes(M.encoded(sbom))
        state['composition']=record;return state

    def test_untagged_baseline_survives_real_finalizer_and_export_before_alias_cleanup(self):
        state=self.composed_untagged_store();alias=M.base_alias_plan()['name']
        self.assertTrue(state['baseline']);self.assertEqual(state['tags'],{alias:M.IMAGE})
        self.assertEqual(state['composition']['baseline_archive_repo_tags'],[])
        self.assertEqual(state['composition']['baseline_repo_tags_before_alias'],[])
        identity=json.loads((self.root/'reports/baseline-image-identity.json').read_bytes())
        self.assertEqual(identity['archive_repo_tags'],[]);self.assertEqual(identity['daemon_repo_tags_before_alias'],[])
        M.finalize(self.root)
        self.assertTrue(state['baseline']);self.assertTrue((self.root/'image-export.json').exists())
        M.cleanup(self.root)
        self.assertFalse(state['baseline']);self.assertFalse(state['tags'])
        self.assertLess(state['events'].index(('export',state['candidate_record']['Id'])),
                        state['events'].index(('image','rm','--no-prune',alias)))
        receipt=json.loads((self.root/'reports/base-alias-cleanup.json').read_bytes())
        self.assertEqual(receipt['status'],'removed_owned_alias');self.assertFalse(receipt['cancellation_proven'])

    def test_early_alias_cleanup_reproduces_missing_baseline_and_identifies_exact_lookup(self):
        state=self.composed_untagged_store();M.cleanup(self.root)
        self.assertFalse(state['baseline']);self.assertTrue(state['candidate'])
        with redirect_stderr(io.StringIO()):self.assertEqual(M.main(['finalize','--root',str(self.root)]),1)
        failure=M.phase_state(self.root)
        self.assertEqual(failure['first_failure_phase'],'inspect_final_baseline');self.assertEqual(failure['first_failure_code'],'command_failed')
        self.assertFalse((self.root/'image-export.json').exists());self.assertFalse(any(row[0]=='export' for row in state['events']))

    def test_final_candidate_failure_has_separate_phase_and_owned_cleanup_preserves_it(self):
        state=self.composed_untagged_store();state['candidate']=False
        with redirect_stderr(io.StringIO()):self.assertEqual(M.main(['finalize','--root',str(self.root)]),1)
        M.cleanup(self.root)
        self.assertEqual(M.phase_state(self.root)['first_failure_phase'],'inspect_final_candidate')
        self.assertFalse((self.root/'image-export.json').exists());self.assertFalse(state['baseline'])

    def test_alias_cleanup_refuses_foreign_image_without_removal(self):
        state=self.composed_untagged_store();state['tags'][M.base_alias_plan()['name']]='sha256:'+'e'*64
        with self.assertRaisesRegex(RuntimeError,'base_alias_cleanup_owner'):M.cleanup(self.root)
        self.assertFalse(any(row[:2]==('image','rm') for row in state['events']))
        self.assertTrue(state['baseline'])

    def test_alias_cleanup_rejects_other_run_plan_before_docker(self):
        self.composed_untagged_store();path=self.root/'reports/base-alias-plan.json'
        changed=json.loads(path.read_bytes());changed['owner']='456-1';path.write_bytes(M.encoded(changed))
        with patch.object(M,'docker',side_effect=AssertionError('foreign_plan_command')), \
                self.assertRaisesRegex(RuntimeError,'base_alias_cleanup_owner'):M.cleanup(self.root)

    def test_alias_cleanup_timeout_never_becomes_known_absence_or_success(self):
        self.composed_untagged_store();M.phase(self.root,'bind_raw_reports');M.failed_phase(self.root,RuntimeError('raw_report_identity'))
        with patch.object(M,'docker',side_effect=RuntimeError('command_timeout')),redirect_stderr(io.StringIO()):
            self.assertEqual(M.main(['cleanup','--root',str(self.root)]),1)
        self.assertFalse((self.root/'reports/base-alias-cleanup.json').exists())
        failure=M.phase_state(self.root)
        self.assertEqual(failure['first_failure_phase'],'bind_raw_reports');self.assertEqual(failure['first_failure_code'],'raw_report_identity')
        self.assertEqual(failure['last_failure_code'],'command_timeout')

    def test_alias_cleanup_successful_empty_listing_is_only_an_observation(self):
        state=self.composed_untagged_store();state['tags'].clear()
        M.cleanup(self.root)
        receipt=json.loads((self.root/'reports/base-alias-cleanup.json').read_bytes())
        self.assertEqual(receipt['status'],'absent_at_observation');self.assertFalse(receipt['cancellation_proven'])
        self.assertFalse(any(row[:2]==('image','rm') for row in state['events']))
        self.assertEqual(M.phase_state(self.root)['status'],'complete')

    def test_alias_cleanup_before_plan_uses_no_docker_and_missing_completed_plan_refuses(self):
        with patch.object(M,'ci_root'),patch.object(M,'load_sealed',side_effect=AssertionError('sealed_before_plan')), \
                patch.object(M,'docker',side_effect=AssertionError('docker_before_plan')):
            M.cleanup(self.root)
            (self.root/'reports').mkdir();(self.root/'reports/composition.json').write_bytes(b'ordinary fixture')
            with self.assertRaisesRegex(RuntimeError,'base_alias_plan_missing'):M.cleanup(self.root)

    def test_full_inventory_rejects_extra_changes_libc_and_application_drift(self):
        before,after=self.inventories();M.compare_inventory(before,after)
        for mutation in (lambda r:r['inventory'].update(extra='1'),lambda r:r['inventory'].pop('unchanged'),
                         lambda r:r['inventory'].update(unchanged='2'),lambda r:r['inventory'].update(libc6='2.42'),
                         lambda r:r.update(libc_sha256='c'*64),lambda r:r.update(app_and_python_sha256='d'*64)):
            changed=deepcopy(after);mutation(changed)
            with self.assertRaises(RuntimeError):M.compare_inventory(before,changed)

    def execute_installer(self, *, extra_plan=False, extra_upgrade=False, caps=0xd9):
        capsule=self.root/'capsule';capsule.mkdir();rows=[]
        for index in range(15):
            data=('ordinary DEB '+str(index)).encode();name='fixture'+str(index);filename=name+'.deb'
            (capsule/filename).write_bytes(data)
            rows.append({'name':name,'version':'2','path':filename,'bytes':len(data),'sha256':sha256(data).hexdigest()})
        (capsule/'manifest.json').write_text(json.dumps({'schema':'security_os_composition_packages/1','packages':rows}))
        before={r['name']:'1' for r in rows};before.update(libc6='2.41-12+deb13u4',unchanged='1')
        after={**before,**{r['name']:'2' for r in rows}}
        if extra_upgrade:after['unchanged']='2'
        commands=[];inventories=iter((before,after))
        def run(argv,**kwargs):
            commands.append(argv)
            if argv[0]=='/usr/bin/dpkg-deb':
                name=Path(argv[2]).stem;data=f'Package: {name}\nVersion: 2\nArchitecture: amd64\n'.encode()
            elif argv[0]=='/usr/bin/dpkg-query':
                data=''.join(f'{name}\t{version}\tinstalled\n' for name,version in next(inventories).items()).encode()
            elif '--simulate' in argv:
                plan=''.join('Inst '+r['name']+'\n' for r in rows)+('Inst unrelated\n' if extra_plan else '')
                kwargs['stdout'].write(plan.encode());data=b''
            else:data=b''
            return subprocess.CompletedProcess(argv,0,stdout=data)
        real_import=__import__;real_path=Path
        class Status:
            def read_text(self):return f'NoNewPrivs: 1\nCapEff: {caps:x}\nCapPrm: {caps:x}\nCapBnd: {caps:x}\nCapInh: 0\nCapAmb: 0\n'
        def path(value):
            if value=='/capsule':return capsule
            if value=='/proc/self/status':return Status()
            if value=='/tmp/os-compose-simulation':return self.root/'simulation'
            return real_path(value)
        real_open=open
        def opened(name,*args,**kwargs):return real_open(self.root/'simulation' if name=='/tmp/os-compose-simulation' else name,*args,**kwargs)
        def imported(name,*args,**kwargs):return types.SimpleNamespace(Path=path) if name=='pathlib' else real_import(name,*args,**kwargs)
        namespace={'__builtins__':{**vars(__import__('builtins')),'__import__':imported,'open':opened}}
        with patch.object(M.subprocess,'run',side_effect=run),patch.object(M.os,'geteuid',return_value=0):
            exec(compile(M.INSTALLER,'<ordinary-installer-fixture>','exec'),namespace)
        return commands

    def test_actual_installer_uses_apt_ordering_and_exact_inventory(self):
        commands=self.execute_installer()
        actual=next(argv for argv in commands if argv[0]=='/usr/bin/apt-get' and '--simulate' not in argv)
        self.assertNotIn('--no-download',actual);self.assertIn('--no-remove',actual)
        self.assertEqual(len(actual[4:]),15);self.assertTrue(all(Path(p).is_absolute() for p in actual[4:]))
        self.assertIn(['/usr/bin/dpkg','--audit'],commands)

    def test_actual_installer_refuses_simulation_extra_before_real_install(self):
        with self.assertRaises(AssertionError):self.execute_installer(extra_plan=True)

    def test_actual_installer_refuses_unrelated_final_upgrade(self):
        with self.assertRaises(AssertionError):self.execute_installer(extra_upgrade=True)

    def test_actual_installer_refuses_broad_caps_before_commands(self):
        with self.assertRaises(AssertionError):self.execute_installer(caps=0xffff)

    def test_owned_container_cleanup_rejects_foreign_label(self):
        cid='a'*64;commands=[]
        def docker(root,args,**kwargs):
            commands.append(args)
            if args[0]=='create':return (cid+'\n').encode()
            if args[0]=='start':return b'public output'
            if '.State.ExitCode' in args:return b'0\n'
            if args[0]=='inspect' and len(args)>2 and args[2]=='{{.State.ExitCode}}':return b'0\n'
            if args[0]=='inspect':return json.dumps([{'Id':cid,'Image':M.IMAGE,'Name':'/security-os-fixture-123-1',
                'Config':{'Labels':{'io.gold-trade.os-compose-owner':'foreign-label'}}}]).encode()
            raise AssertionError('foreign_delete_attempted')
        with patch.dict(os.environ,{'GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1'}),patch.object(M,'docker',side_effect=docker), \
                self.assertRaisesRegex(RuntimeError,'container_cleanup_owner'):
            M.isolated(self.root,M.IMAGE,'print("fixture")','fixture')
        self.assertFalse(any(argv[0]=='rm' for argv in commands))
        create=commands[0]
        for flag in ('--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges','--memory=1g','--pids-limit=128'):
            self.assertIn(flag,create)

    def test_late_create_cleanup_is_exact_owned_and_original_outcome_stays_unknown(self):
        (self.root/'reports').mkdir();cid='a'*64;calls=[]
        def docker(root,args,**kwargs):
            calls.append(args)
            if args[0]=='create':raise RuntimeError('command_timeout')
            if args[0]=='inspect':return json.dumps([{'Id':cid,'Image':M.IMAGE,'Name':'/security-os-fixture-123-1',
                'Config':{'Labels':{'io.gold-trade.os-compose-owner':'123-1'}}}]).encode()
            self.assertEqual(args,['rm','--force',cid]);return b''
        with patch.dict(os.environ,{'GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1'}),patch.object(M,'docker',side_effect=docker), \
                self.assertRaisesRegex(RuntimeError,'^command_timeout$'):
            M.isolated(self.root,M.IMAGE,'print("fixture")','fixture')
        self.assertIn(['inspect','security-os-fixture-123-1'],calls)
        self.assertIn(['rm','--force',cid],calls)
        receipt=json.loads((self.root/'reports/fixture-daemon-outcome.json').read_bytes())
        self.assertEqual(receipt['daemon_outcome'],'UNKNOWN');self.assertFalse(receipt['cancellation_proven'])
        self.assertEqual(receipt['best_effort_cleanup'],'removed_owned_container')

    def test_late_create_never_deletes_foreign_image_or_label(self):
        (self.root/'reports').mkdir();cid='a'*64;calls=[]
        def docker(root,args,**kwargs):
            calls.append(args)
            if args[0]=='create':return b'invalid-container-id'
            if args[0]=='inspect':return json.dumps([{'Id':cid,'Image':'sha256:'+'b'*64,'Name':'/security-os-fixture-123-1',
                'Config':{'Labels':{'io.gold-trade.os-compose-owner':'123-1'}}}]).encode()
            raise AssertionError('foreign_delete_attempted')
        with patch.dict(os.environ,{'GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1'}),patch.object(M,'docker',side_effect=docker), \
                self.assertRaisesRegex(RuntimeError,'^container_identity$'):
            M.isolated(self.root,M.IMAGE,'print("fixture")','fixture')
        self.assertFalse(any(args[0]=='rm' for args in calls))
        receipt=json.loads((self.root/'reports/fixture-daemon-outcome.json').read_bytes())
        self.assertEqual(receipt['daemon_outcome'],'UNKNOWN');self.assertEqual(receipt['best_effort_cleanup'],'not_confirmed')

    def test_actual_pipe_output_bound_and_clean_child_environment(self):
        reader,writer=os.pipe();os.write(writer,b'abcdef');os.close(writer)
        stdout=os.fdopen(reader,'rb',buffering=0);self.addCleanup(stdout.close)
        child=Mock(stdout=stdout,pid=12345,wait=Mock(return_value=0),poll=Mock(return_value=None))
        with patch.object(M.subprocess,'Popen',return_value=child) as start,patch.object(M.os,'killpg') as kill, \
                patch.dict(os.environ,{'GH_TOKEN':'public-fixture-token'}):
            with self.assertRaisesRegex(RuntimeError,'command_output_bound'):M.command(['owned-fixture'],maximum=4)
        self.assertNotIn('GH_TOKEN',start.call_args.kwargs['env']);kill.assert_called_once()
        self.assertEqual(child.wait.call_args.kwargs['timeout'],10);self.assertTrue(stdout.closed)

    def test_public_failure_log_survives_nonzero_command(self):
        reader,writer=os.pipe();os.write(writer,b'ordinary public build failure\n');os.close(writer)
        stdout=os.fdopen(reader,'rb',buffering=0);self.addCleanup(stdout.close)
        child=Mock(stdout=stdout,pid=12345,wait=Mock(return_value=42),poll=Mock(return_value=42))
        path=self.root/'public-build.log'
        with patch.object(M.subprocess,'Popen',return_value=child) as launch,patch.object(M.os,'killpg'), \
                self.assertRaisesRegex(RuntimeError,'command_failed'):
            M.command(['owned-fixture'],combine_stderr=True,log_path=path)
        self.assertEqual(launch.call_args.kwargs['stderr'],subprocess.STDOUT)
        self.assertEqual(path.read_bytes(),b'ordinary public build failure\n')
        self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)

    def test_actual_export_stream_hashes_compressed_owned_bytes(self):
        raw=b'ordinary public archive-stream fixture\n'
        reader,writer=os.pipe();os.write(writer,raw);os.close(writer)
        stdout=os.fdopen(reader,'rb',buffering=0);self.addCleanup(stdout.close)
        child=Mock(stdout=stdout,pid=12345,wait=Mock(return_value=0),poll=Mock(return_value=0))
        with patch.object(M.subprocess,'Popen',return_value=child) as launch:
            record=M.export_image(self.root,M.IMAGE)
        data=(self.root/record['name']).read_bytes()
        self.assertEqual(gzip.decompress(data),raw);self.assertEqual(record['uncompressed_bytes'],len(raw))
        self.assertEqual(record['size_bytes'],len(data));self.assertEqual(record['sha256'],sha256(data).hexdigest())
        self.assertEqual(launch.call_args.args[0],[*M.DOCKER,'image','save',M.IMAGE])
        self.assertTrue(stdout.closed)

    def test_export_timeout_never_returns_archive_proof(self):
        reader,writer=os.pipe();self.addCleanup(os.close,writer)
        stdout=os.fdopen(reader,'rb',buffering=0);self.addCleanup(stdout.close)
        child=Mock(stdout=stdout,pid=12345,wait=Mock(return_value=0),poll=Mock(return_value=None))
        with patch.object(M.subprocess,'Popen',return_value=child),patch.object(M.os,'killpg') as kill, \
                patch.object(M.time,'monotonic',side_effect=[0,301]),self.assertRaisesRegex(RuntimeError,'export_timeout'):
            M.export_image(self.root,M.IMAGE)
        kill.assert_called_once();self.assertTrue(stdout.closed)

    def report_fixture(self):
        image='sha256:'+'a'*64;layers=['sha256:'+'1'*64];config={'Cmd':['python3']}
        record={'image_id':image,'candidate_layers':layers,'original_oci_config':config}
        raw={'SchemaVersion':2,'ArtifactType':'container_image','Metadata':{'ImageID':image,'ImageConfig':{'config':config,'rootfs':{'diff_ids':layers}}},
             'Results':[{'Vulnerabilities':[{'Severity':'HIGH'},{'Severity':'LOW'},{'Severity':'UNKNOWN'}]}]}
        sbom={'metadata':{'component':{'type':'container','name':image,'properties':[{'name':'aquasecurity:trivy:ImageID','value':image}]}},
              'components':[{'name':name,'version':row[0]} for name,row in M.PACKAGES.items()]}
        return record,raw,sbom

    def test_component_receipt_identity_and_all_evidence_artifacts_are_bound(self):
        image='sha256:'+'a'*64
        value={'schema':'security_image_component_presence/1','image_id':image,'source_commit':M.SOURCE,'verifier_sha256':M.PROBE_SHA,
               'scope':'known_vendor_component_paths_and_names_plus_actual_perl_inc'}
        for key in ('security_accepted','scanner_findings_modified','production_mounts_or_data_used',
                    'dynamic_runtime_code_downloads_analyzed','renamed_or_obfuscated_embedded_code_analyzed','directory_symlinks_followed'):
            value[key]=False
        self.assertEqual(M.validate_component(json.dumps(value).encode(),image),value)
        changed={**value,'image_id':'sha256:'+'b'*64}
        with self.assertRaisesRegex(RuntimeError,'component_receipt_identity'):M.validate_component(json.dumps(changed).encode(),image)
        changed={**value,'security_accepted':True}
        with self.assertRaisesRegex(RuntimeError,'component_receipt_scope'):M.validate_component(json.dumps(changed).encode(),image)
        reports=self.root/'reports';reports.mkdir()
        for name in M.EVIDENCE_REPORTS:(reports/name).write_bytes(('public evidence '+name).encode())
        before=M.evidence_hashes(self.root)
        self.assertEqual(set(before),set(M.EVIDENCE_REPORTS));self.assertIn('native-run.log',before)
        self.assertIn('component-presence.json',before);self.assertIn('focused-tests.json',before)
        (reports/'native-run.log').write_bytes(b'changed public native result')
        self.assertNotEqual(before,M.evidence_hashes(self.root))

    def test_raw_findings_remain_all_severities_and_reports_bind_image(self):
        record,raw,sbom=self.report_fixture();self.assertEqual(M.validate_reports(record,raw,sbom),{'HIGH':1,'LOW':1,'UNKNOWN':1})
        for mutation in (lambda r:r['Metadata'].update(ImageID='sha256:'+'b'*64),
                         lambda r:r['Metadata']['ImageConfig']['config'].update(User='installer')):
            changed=deepcopy(raw);mutation(changed)
            with self.assertRaises(RuntimeError):M.validate_reports(record,changed,sbom)
        sbom['components'][0]['version']='unexpected'
        with self.assertRaisesRegex(RuntimeError,'sbom_package_version'):M.validate_reports(record,raw,sbom)

    def test_all_embedded_programs_parse_without_execution(self):
        for program in (M.INSTALLER,M.AUDIT,M.FOCUSED,M.NATIVE):ast.parse(program)
        self.assertIn('result.testsRun==56',M.FOCUSED);self.assertIn('not result.skipped',M.FOCUSED)
        self.assertNotIn('compileall',M.INSTALLER);self.assertNotIn('pip install',M.INSTALLER)

    def test_workflow_preserves_raw_scans_and_no_deployment_authority(self):
        import yaml
        workflow=yaml.safe_load(WORKFLOW.read_text())
        self.assertEqual(workflow['permissions'],{'contents':'read','actions':'read'})
        steps=workflow['jobs']['composition']['steps']
        identifiers=[s.get('id') for s in steps]
        self.assertLess(identifiers.index('export'),identifiers.index('cleanup'))
        cleanup=next(s for s in steps if s.get('id')=='cleanup')
        self.assertEqual(cleanup['if'],"always() && steps.seal.outcome == 'success'")
        upload=next(s for s in steps if s.get('name')=='Retain only complete public image exports')
        self.assertIn("steps.export.outcome == 'success'",upload['if']);self.assertIn("steps.cleanup.outcome == 'success'",upload['if'])
        scans=[s for s in steps if s.get('uses','').startswith('aquasecurity/trivy-action@')]
        self.assertEqual(len(scans),2)
        raw=next(s for s in scans if s['with'].get('format')=='json')
        self.assertEqual(raw['with']['severity'],'UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL')
        self.assertEqual(raw['with']['ignore-unfixed'],'false');self.assertEqual(raw['env']['TRIVY_IGNOREFILE'],'/dev/null')
        for step in steps:
            for value in step.get('env',{}).values():self.assertNotIn('failure()',str(value));self.assertNotIn('always()',str(value))
        self.assertNotIn('ssh ',WORKFLOW.read_text());self.assertNotIn('packages: write',WORKFLOW.read_text())


if __name__=='__main__':unittest.main()
