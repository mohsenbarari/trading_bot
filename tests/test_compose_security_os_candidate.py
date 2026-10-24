"""Owned public fixtures only; every ambient command and network is denied."""
from contextlib import ExitStack
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
            for call in (M.compose,M.finalize):
                with self.assertRaisesRegex(RuntimeError,'github_ci_only'):call(self.root)

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

    def test_package_failure_prevents_any_docker_command(self):
        admission=Mock(IMAGE=M.IMAGE,SOURCE=M.SOURCE,ARTIFACT_ID=12552928309,ZIP_BYTES=1,ZIP_SHA256='a'*64)
        with patch.object(M,'ci_root'),patch.object(M,'load_sealed',return_value=({}, {'admit_security_component_image_reuse.py':admission})), \
                patch.object(M.os,'statvfs',return_value=Mock(f_bavail=16*1024**3,f_frsize=1)), \
                patch.object(M,'fetch_artifact'),patch.object(M,'admit_packages',side_effect=RuntimeError('known_package_refused')), \
                patch.object(M,'docker',side_effect=AssertionError('docker_before_admission')) as docker:
            with self.assertRaisesRegex(RuntimeError,'known_package_refused'):M.compose(self.root)
        docker.assert_not_called();admission.admit_image.assert_not_called()

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

    def inventories(self):
        values={name:'old-version' for name in M.PACKAGES};values.update(libc6='2.41-12+deb13u4',unchanged='1')
        old={'inventory':values,'app_and_python_sha256':'a'*64,'files':5,'bytes':100,'libc_sha256':'b'*64}
        new=deepcopy(old);new['inventory'].update({name:row[0] for name,row in M.PACKAGES.items()})
        return old,new

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
        scans=[s for s in steps if s.get('uses','').startswith('aquasecurity/trivy-action@')]
        self.assertEqual(len(scans),2)
        raw=next(s for s in scans if s['with'].get('format')=='json')
        self.assertEqual(raw['with']['severity'],'UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL')
        self.assertEqual(raw['with']['ignore-unfixed'],'false');self.assertEqual(raw['env']['TRIVY_IGNOREFILE'],'/dev/null')
        for step in steps:
            for value in step.get('env',{}).values():self.assertNotIn('failure()',str(value));self.assertNotIn('always()',str(value))
        self.assertNotIn('ssh ',WORKFLOW.read_text());self.assertNotIn('packages: write',WORKFLOW.read_text())


if __name__=='__main__':unittest.main()
