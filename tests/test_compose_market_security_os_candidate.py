"""Inert public fixtures; globally deny ambient execution/network before imports."""
from contextlib import ExitStack, redirect_stderr
from copy import deepcopy
from hashlib import sha1, sha256
import gzip
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from zipfile import ZipFile

FENCE=ExitStack()
for target in ('subprocess.Popen','subprocess.run','os.system','os.kill','os.killpg','socket.socket',
               'socket.create_connection','socket.getaddrinfo','urllib.request.urlopen',
               'urllib.request.OpenerDirector.open','ctypes.CDLL','ctypes.PyDLL','signal.signal','signal.alarm'):
    FENCE.enter_context(patch(target,side_effect=AssertionError('ambient_host_network_forbidden')))
ROOT=Path(__file__).resolve().parents[1]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
M=load('market_os_recipe',ROOT/'scripts/compose_market_security_os_candidate.py')
C=load('market_os_common',ROOT/'scripts/compose_security_os_candidate.py')
P=load('market_os_payload',ROOT/'scripts/prepare_market_security_payload.py')


class MarketCompositionTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='public-market-os-')))
        self.root.chmod(0o700);previous=os.umask(0o077);self.addCleanup(os.umask,previous)
        self.stack.enter_context(patch.object(M,'COMMON',C));self.stack.enter_context(patch.object(M,'MARKET',P))
        self.stack.enter_context(patch.dict(os.environ,{'GITHUB_SHA':'a'*40,'GITHUB_RUN_ID':'123',
            'GITHUB_RUN_ATTEMPT':'1','GITHUB_OUTPUT':str(self.root/'output')}))

    def write(self,path,raw):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw);path.chmod(0o600);return path

    def config(self,diffs):
        return {'architecture':'amd64','created':'2026-10-24T22:01:02.123400Z','history':[], 'os':'linux',
            'rootfs':{'type':'layers','diff_ids':diffs},'config':{'Cmd':['service','--role','market-processor'],
            'Entrypoint':['python','-m','ordinary.fixture'],'Env':['PUBLIC_DEFAULT=yes'],'Labels':{'z':'last','a':'first'},
            'User':'10001:10001','WorkingDir':'/app','ArgsEscaped':True}}

    def tar(self,rows):
        out=io.BytesIO()
        with tarfile.open(fileobj=out,mode='w',format=tarfile.USTAR_FORMAT) as archive:
            for name,raw,kind in rows:
                info=tarfile.TarInfo(name);info.type=kind;info.size=len(raw) if kind==tarfile.REGTYPE else 0
                archive.addfile(info,io.BytesIO(raw) if kind==tarfile.REGTYPE else None)
        return out.getvalue()

    def archive(self,*,oci=True,legacy=False,compressed=False,extras=(),duplicate=False):
        bodies=[b'ordinary layer one',b'ordinary empty layer',b'ordinary empty layer']
        diffs=['sha256:'+sha256(raw).hexdigest() for raw in bodies];config=self.config(diffs)
        config_raw=M.encoded(config);image='sha256:'+sha256(config_raw).hexdigest()
        rows=[];layer_names=[];descriptors=[]
        for index,body in enumerate(bodies):
            stored=gzip.compress(body,mtime=0) if compressed else body
            name='blobs/sha256/'+sha256(stored).hexdigest() if oci else str(index)+'/layer.tar'
            layer_names.append(name);descriptors.append({'digest':'sha256:'+sha256(stored).hexdigest(),'size':len(stored)})
            if name not in {r[0] for r in rows}:rows.append((name,stored,tarfile.REGTYPE))
        config_name='blobs/sha256/'+image[7:] if oci else image[7:]+'.json'
        rows.append((config_name,config_raw,tarfile.REGTYPE))
        rows.append(('manifest.json',M.encoded([{'Config':config_name,'Layers':layer_names,'RepoTags':None}]),tarfile.REGTYPE))
        if oci:
            manifest=M.encoded({'schemaVersion':2,'config':{'digest':image,'size':len(config_raw)},'layers':descriptors})
            name='blobs/sha256/'+sha256(manifest).hexdigest();rows.append((name,manifest,tarfile.REGTYPE))
            rows.append(('index.json',M.encoded({'schemaVersion':2,'manifests':[{'digest':'sha256:'+sha256(manifest).hexdigest(),'size':len(manifest)}]}),tarfile.REGTYPE))
            rows.append(('oci-layout',M.encoded({'imageLayoutVersion':'1.0.0'}),tarfile.REGTYPE))
            if legacy:rows.extend((name,raw,tarfile.REGTYPE) for name,raw in M.legacy_configs(config).items())
        rows.extend(extras)
        if duplicate:rows.append(rows[0])
        return self.tar(rows),image,config,rows

    def validate(self,raw,image,config):
        with patch.object(M,'IMAGE',image):return M.validate_archive(io.BytesIO(raw),len(raw),config)

    def test_fixed_c22_and_three_family_inputs(self):
        self.assertEqual(M.IMAGE_ARTIFACT,(12578501302,59814206,'da7ee559c4815113ed3ff74be845443b48185c410cc4f8b474cabb0b7efdd236'))
        self.assertEqual(M.EVIDENCE_ARTIFACT[0],12578306625);self.assertEqual(M.RUN,40140063747)
        self.assertEqual(M.SOURCE,'88299eb42bfa05361f8b8ed82358873336a6480e')
        self.assertEqual(M.IMAGE,'sha256:c22d9d459ab4ca0586066d87e2b2456958309fa12e47dfaac2651a1743e0fde4')
        for name,digest in M.HELPERS.items():self.assertEqual(sha256((ROOT/'scripts'/name).read_bytes()).hexdigest(),digest)
        self.assertEqual(len(C.PACKAGES),15);self.assertEqual(set(C.GROUPS),{'util','ncurses','acl-tar'})

    def test_host_refusal_has_no_commands_or_failure_writes(self):
        with patch.dict(os.environ,{'GITHUB_ACTIONS':'false'}),redirect_stderr(io.StringIO()) as error:
            self.assertEqual(M.main(['compose','--root',str(self.root),'--repo',str(ROOT)]),1)
        self.assertEqual(json.loads(error.getvalue())['code'],'github_ci_only');self.assertFalse((self.root/'reports').exists())

    def test_retained_fd_rejects_replacement_and_hardlinks(self):
        path=self.write(self.root/'public',b'ordinary')
        with self.assertRaisesRegex(RuntimeError,'input_changed'):
            with M.pinned(path,1024):os.replace(self.write(self.root/'other',b'ordinary'),path)
        os.link(path,self.root/'alias')
        with self.assertRaisesRegex(RuntimeError,'input_metadata'):
            with M.pinned(path,1024):pass

    def test_sources_are_checked_first_buffer_and_retained_through_work(self):
        source=self.root/'source';source.mkdir();records={}
        for name in {Path(M.__file__).name,*M.HELPERS}:
            raw=(ROOT/'scripts'/name).read_bytes();self.write(source/name,raw);records[name]={'bytes':len(raw),'sha256':sha256(raw).hexdigest()}
        self.write(source/'identity.json',M.encoded({'recipe_commit':'a'*40,'files':records}))
        with patch.object(M,'__file__',str(source/'compose_market_security_os_candidate.py')):
            with self.assertRaisesRegex(RuntimeError,'input_changed'):
                with M.sources(self.root):
                    name='prepare_market_security_payload.py';os.replace(self.write(source/'replacement', (source/name).read_bytes()),source/name)
            self.write(source/'compose_security_os_candidate.py',b'ordinary drift')
            with self.assertRaisesRegex(RuntimeError,'source_changed'):
                with M.sources(self.root):pass

    def test_classic_and_oci_ordered_repeated_empty_layers(self):
        for oci,legacy,gzip_layers in ((False,False,False),(True,False,False),(True,True,False),(True,True,True)):
            raw,image,config,_=self.archive(oci=oci,legacy=legacy,compressed=gzip_layers)
            result=self.validate(raw,image,config)
            self.assertEqual(result['layers'],3);self.assertEqual(result['legacy_compatibility_configs'],3 if legacy else 0)
            self.assertEqual(result['docker_archive_sha256'],sha256(raw).hexdigest())

    def test_wrong_diffid_and_config_order_refuse(self):
        raw,image,config,rows=self.archive()
        broken=deepcopy(config);broken['rootfs']['diff_ids'].reverse()
        with self.assertRaisesRegex(RuntimeError,'archive_config_changed'):self.validate(raw,image,broken)
        rows[0]=(rows[0][0],b'ordinary changed layer',tarfile.REGTYPE)
        with self.assertRaisesRegex(RuntimeError,'archive_layer_diff_id'):self.validate(self.tar(rows),image,config)

    def test_duplicate_physical_member_and_undeclared_blob_refuse(self):
        for options,code in (({'duplicate':True},'archive_member'),({'extras':(('blobs/sha256/'+'f'*64,b'ordinary',tarfile.REGTYPE),)},'archive_closed_inventory')):
            raw,image,config,_=self.archive(**options)
            with self.assertRaisesRegex(RuntimeError,code):self.validate(raw,image,config)

    def test_legacy_content_and_parent_chain_refuse(self):
        raw,image,config,rows=self.archive(legacy=True)
        indices=[i for i,row in enumerate(rows) if row[0] in M.legacy_configs(config)]
        i=indices[-1];name,data,kind=rows[i];value=json.loads(data);value['parent']='0'*64
        rows[i]=(name,M.go_json(value),kind)
        with self.assertRaisesRegex(RuntimeError,'legacy_bytes'):self.validate(self.tar(rows),image,config)

    def test_legacy_projection_has_real_go_order_and_omitempty(self):
        value=self.config(['sha256:'+'1'*64])['config'];raw=M.go_json(M.container_config(value))
        self.assertLess(raw.index(b'"Cmd"'),raw.index(b'"ArgsEscaped"'));self.assertLess(raw.index(b'"ArgsEscaped"'),raw.index(b'"Image"'))
        self.assertLess(raw.index(b'"WorkingDir"'),raw.index(b'"Entrypoint"'));self.assertIn(b'"Labels":{"a":"first","z":"last"}',raw)
        value['ArgsEscaped']=False;self.assertNotIn(b'ArgsEscaped',M.go_json(M.container_config(value)))
        value['ArgsEscaped']=0
        with self.assertRaisesRegex(RuntimeError,'legacy_projection'):M.container_config(value)
        self.assertEqual(M.go_json({'a':'<&>\u2028\u2029'}),b'{"a":"\\u003c\\u0026\\u003e\\u2028\\u2029"}')

    def test_directory_type_and_pax_extensions_refuse_before_payload(self):
        raw,image,config,_=self.archive(extras=(('blobs',b'not a directory',tarfile.REGTYPE),))
        with self.assertRaisesRegex(RuntimeError,'archive_closed_inventory'):self.validate(raw,image,config)
        header=tarfile.TarInfo('public-pax');header.type=tarfile.XHDTYPE;header.size=0
        with self.assertRaisesRegex(RuntimeError,'archive_member'):self.validate(header.tobuf()+b'\0'*1024,image,config)

    def test_trailer_growth_and_nonzero_payload_refuse(self):
        raw,image,config,rows=self.archive();actual=sum(512+(len(r[1])+511)//512*512 for r in rows)
        with self.assertRaisesRegex(RuntimeError,'archive_trailer'):self.validate(raw[:actual+512],image,config)
        value=bytearray(raw);value[-512]=1
        with self.assertRaisesRegex(RuntimeError,'archive_trailing_payload'):self.validate(bytes(value),image,config)

    def test_slice_bounds_and_deadline_precede_underlying_access(self):
        source=Mock();reader=M.SliceReader(source,100,200,1)
        with patch.object(M.time,'monotonic',return_value=2):
            with self.assertRaisesRegex(RuntimeError,'archive_timeout'):reader.read(1)
        source.read.assert_not_called();source.seek.assert_not_called()
        with patch.object(M.time,'monotonic',return_value=0):
            with self.assertRaisesRegex(RuntimeError,'archive_interval'):reader.seek(201)
        source.read.assert_not_called();source.seek.assert_not_called()

    def test_outer_archive_reads_cannot_grow_past_advertised_size(self):
        raw,image,config,_=self.archive()
        class Guard(io.BytesIO):
            def read(self,n=-1):
                self.assert_bound(n);return super().read(n)
            def assert_bound(self,n):
                if n<0 or n>len(raw)-self.tell():raise AssertionError('underlying unbounded read')
        with patch.object(M,'IMAGE',image):self.assertEqual(M.validate_archive(Guard(raw+b'ignored growth'),len(raw),config)['layers'],3)

    def export(self,evidence,archive):
        payload=evidence['payload'];build=evidence['build']
        return {'schema':'market_security_candidate_image_export/1','status':'exported_not_accepted','image_id':M.IMAGE,
            **payload,'payload_sha256':M.PAYLOAD_SHA,'build_receipt_sha256':M.BUILD_SHA,'test_sources_sha256':M.TEST_SHA,
            'reports_sha256':{'vulnerabilities.json':M.RAW_SHA,'sbom.cdx.json':M.SBOM_SHA},
            **{k:build[k] for k in ('native_sha256','focused_sha256','focused_nonroot_sha256','focused_root_fixture_sha256')},
            'product_files_equal_baseline':True,'public_source_directory_mode':'0755',
            **{k:False for k in ('vendor_debs_reused','full_runtime_matrix_completed','security_accepted','findings_waived','runtime_data_used','deployment_eligible','deployment_performed')},
            'archive':archive}

    def evidence(self,config):
        return {'payload':{k:k for k in ('baseline_image_id','baseline_commit','baseline_tree','source_commit','source_tree','payload_tree','image_input_signature','public_source_binding')},
            'build':{k:'1'*64 for k in ('native_sha256','focused_sha256','focused_nonroot_sha256','focused_root_fixture_sha256')},'config':config}

    def test_fixed_zip_seal_precedes_member_extraction_and_actual_parser(self):
        raw,image,config,_=self.archive(legacy=True);compressed=gzip.compress(raw,mtime=0)
        evidence=self.evidence(config);archive={'name':'candidate-image.tar.gz','format':'docker-archive+gzip',
            'size_bytes':len(compressed),'sha256':sha256(compressed).hexdigest(),'uncompressed_bytes':len(raw)}
        with patch.object(M,'IMAGE',image):export=self.export(evidence,archive)
        out=io.BytesIO()
        with ZipFile(out,'w') as z:z.writestr('candidate-image.tar.gz',compressed);z.writestr('image-export.json',M.encoded(export))
        value=out.getvalue();path=self.write(self.root/'image.zip',value)
        with patch.object(M,'IMAGE',image),patch.object(M,'IMAGE_ARTIFACT',(1,len(value),sha256(value).hexdigest())):
            result=M.admit_image(path,self.root/'admitted',evidence);self.assertEqual(result['layers'],3)
            self.assertEqual(result['export_receipt_sha256'],sha256(M.encoded(export)).hexdigest())
        path.write_bytes(value[:-1]+b'X')
        with patch.object(M,'IMAGE_ARTIFACT',(1,len(value),sha256(value).hexdigest())):
            with self.assertRaisesRegex(RuntimeError,'image_zip_identity'):M.admit_image(path,self.root/'bad',evidence)
        self.assertFalse((self.root/'bad').exists())

    def test_export_scope_provenance_and_boolean_bounds(self):
        evidence=self.evidence(self.config([]));archive={'name':'candidate-image.tar.gz','format':'docker-archive+gzip',
            'size_bytes':100,'sha256':'a'*64,'uncompressed_bytes':1000};value=self.export(evidence,archive)
        self.assertEqual(M.validate_export(value,evidence),archive)
        for key,replacement,code in (('source_commit','changed','export_provenance'),('security_accepted',0,'export_scope'),('test_sources_sha256','changed','export_evidence')):
            broken=deepcopy(value);broken[key]=replacement
            with self.assertRaisesRegex(RuntimeError,code):M.validate_export(broken,evidence)
        value['archive']['size_bytes']=True
        with self.assertRaisesRegex(RuntimeError,'export_archive'):M.validate_export(value,evidence)

    def test_package_comparison_preserves_all_python_opt_and_libc_bytes(self):
        before={'inventory':{**{n:'old' for n in C.PACKAGES},'libc6':'2.41-12+deb13u4'},'app_and_python_sha256':'a'*64,'files':100,'bytes':1000,'libc_sha256':'b'*64}
        after=deepcopy(before);after['inventory'].update({n:r[0] for n,r in C.PACKAGES.items()});M.compare_inventory(before,after)
        for key in ('app_and_python_sha256','files','bytes','libc_sha256'):
            broken=deepcopy(after);broken[key]='drift'
            with self.assertRaises(RuntimeError):M.compare_inventory(before,broken)
        after['inventory']['extra']='new'
        with self.assertRaisesRegex(RuntimeError,'installed_delta_changed'):M.compare_inventory(before,after)
        program=M.audit_program();self.assertIn("('/app','/usr/local','/opt')",program);self.assertIn('onerror=',program)

    def test_short_build_runs_root_only_for_installer_and_restores_user(self):
        text=M.dockerfile('security-market-os-base-123-1:ci')
        self.assertTrue(text.startswith('FROM security-market-os-base-123-1:ci\nUSER 0:0\n'))
        self.assertTrue(text.endswith('USER 10001:10001\n'));self.assertEqual(text.count('RUN '),1)
        self.assertIn('RUN --network=none',text);self.assertIn('--no-new-privs',text);self.assertNotIn('LABEL ',text)
        with self.assertRaisesRegex(RuntimeError,'alias_identity'):M.dockerfile('unowned:latest')

    def test_whole_config_exact_despite_oci_default_projection(self):
        before={'Id':M.IMAGE,'Config':self.config([])['config'],'RootFS':{'Layers':['sha256:'+'1'*64]}}
        after=deepcopy(before);after['Id']='sha256:'+'2'*64;after['RootFS']['Layers'].append('sha256:'+'3'*64);C.compare_images(before,after)
        after['Config']['Env'].append('changed=yes')
        with self.assertRaisesRegex(RuntimeError,'runtime_config_changed'):C.compare_images(before,after)
        self.assertEqual(M.oci_runtime_config({'User':'10001:10001','AttachStdin':False}),{'User':'10001:10001'})
        self.assertNotEqual(M.oci_runtime_config({'AttachStdin':0}),{})

    def test_component_probe_recipe_identity_has_no_app19d_constant(self):
        value={'schema':'security_image_component_presence/1','image_id':'sha256:'+'a'*64,'source_commit':'b'*40,
            'verifier_sha256':M.HELPERS['verify_image_component_presence.py'],'scope':'known_vendor_component_paths_and_names_plus_actual_perl_inc',
            **{k:False for k in ('security_accepted','scanner_findings_modified','production_mounts_or_data_used','dynamic_runtime_code_downloads_analyzed','renamed_or_obfuscated_embedded_code_analyzed','directory_symlinks_followed')}}
        M.validate_component(M.encoded(value),value['image_id'],'b'*40)
        with self.assertRaisesRegex(RuntimeError,'component_receipt_identity'):M.validate_component(M.encoded(value),value['image_id'],C.SOURCE)
        value['security_accepted']=0
        with self.assertRaisesRegex(RuntimeError,'component_receipt_scope'):M.validate_component(M.encoded(value),value['image_id'],'b'*40)

    def test_raw_unknown_and_high_rows_retained_and_strict_package_sbom(self):
        image='sha256:'+'a'*64;config={'User':'10001:10001'};layers=['sha256:'+'b'*64]
        record={'image_id':image,'baseline_config':config,'candidate_layers':layers}
        raw={'SchemaVersion':2,'ArtifactType':'container_image','Metadata':{'ImageID':image,'ImageConfig':{'config':config,'rootfs':{'diff_ids':layers}}},
            'Results':[{'Class':'os-pkgs','Type':'debian','Vulnerabilities':[{'Severity':s,'VulnerabilityID':'CVE-public','PkgName':'ordinary'} for s in ('UNKNOWN','HIGH')]}]}
        sbom={'bomFormat':'CycloneDX','metadata':{'component':{'type':'container','name':image,'properties':[{'name':'aquasecurity:trivy:ImageID','value':image}]}},
            'components':[{'name':n,'version':v[0]} for n,v in C.PACKAGES.items()]}
        self.assertEqual(M.validate_reports(record,raw,sbom),{'UNKNOWN':1,'LOW':0,'MEDIUM':0,'HIGH':1,'CRITICAL':0})
        sbom['components'][0]['version']='wrong'
        with self.assertRaisesRegex(RuntimeError,'sbom_package_version'):M.validate_reports(record,raw,sbom)

    def test_exact_alias_cleanup_after_export_and_foreign_refusal(self):
        (self.root/'reports').mkdir();plan=M.alias_plan();self.write(self.root/'reports/base-alias-plan.json',M.encoded(plan))
        inspect={'Id':M.IMAGE,'Os':'linux','Architecture':'amd64','RootFS':{'Type':'layers','Layers':['sha256:'+'1'*64]},'Config':{'User':'10001:10001'}}
        calls=[]
        def docker(root,argv,**kwargs):
            calls.append(argv)
            if argv[:2]==['image','ls']:return M.IMAGE.encode()
            if argv[:2]==['image','inspect']:return M.encoded([inspect])
            return b'owned alias removed'
        self.stack.enter_context(patch.object(M,'ci'));self.stack.enter_context(patch.object(M,'sources',return_value=ExitStack()))
        with patch.object(C,'docker',side_effect=docker):M.cleanup(self.root)
        self.assertEqual(calls[-1],['image','rm','--no-prune',plan['name']]);self.assertNotIn('--force',str(calls))
        inspect['Id']='sha256:'+'f'*64
        with patch.object(C,'docker',side_effect=docker):
            with self.assertRaisesRegex(RuntimeError,'image_identity'):M.cleanup(self.root)

    def test_unknown_create_propagates_after_best_effort_cleanup(self):
        calls=[]
        def docker(root,argv,**kwargs):
            calls.append(argv)
            if argv[0]=='create':raise RuntimeError('command_timeout')
            raise RuntimeError('not observed')
        (self.root/'reports').mkdir()
        with patch.object(C,'docker',side_effect=docker):
            with self.assertRaisesRegex(RuntimeError,'command_timeout'):C.isolated(self.root,M.IMAGE,'pass','inert')
        outcome=json.loads((self.root/'reports/inert-daemon-outcome.json').read_bytes())
        self.assertEqual(outcome['daemon_outcome'],'UNKNOWN');self.assertFalse(outcome['cancellation_proven'])

    def test_evidence_hashes_detect_changed_report(self):
        for name in M.REPORTS:self.write(self.root/'reports'/name,b'ordinary public report')
        original=M.evidence_hashes(self.root);self.write(self.root/'reports/native.json',b'changed')
        self.assertNotEqual(M.evidence_hashes(self.root),original)

    def test_real_capsule_reconstruction_checks_original_effective_blobs_and_modes(self):
        paths=(*P.TEST_PATHS,'deploy/market-data/Dockerfile');original={};rows=[]
        control=b'{"ordinary_public_control":true}\n';control_sha=sha256(control).hexdigest()
        for name in paths:
            raw=P.TEST_OLD if name=='tests/test_market_pipeline_stage3_foundation.py' else ('# public fixture '+name+'\n').encode()
            effective=P.test_transform(name,raw);original[name]=raw
            rows.append({'path':name,'source_commit':M.BASELINE if name in P.TEST_PATHS else M.SOURCE,
                'git_blob':sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest(),'original_sha256':sha256(raw).hexdigest(),
                'bytes':len(effective),'sha256':sha256(effective).hexdigest(),
                'transformation':'none' if raw==effective else 'security_base_assertion_only'})
        proof={'files':rows,'public_baseline_sha256':control_sha};proof_raw=M.encoded(proof)
        evidence={'tests':proof,'raw':{'payload/tests/test-sources.json':proof_raw}}
        calls=[]
        def command(argv,**kwargs):
            calls.append(argv);kind=argv[4];operand=argv[-1]
            if kind=='ls-tree':
                name=argv[-1];row=next(r for r in rows if r['path']==name)
                mode='100755' if name.startswith('scripts/') else '100644'
                return (mode+' blob '+row['git_blob']+'\t'+name+'\n').encode()
            name=operand.split(':',1)[1]
            raw=control if name==P.PUBLIC_PROOF_PATH else original[name]
            return str(len(raw)).encode()+b'\n' if argv[5]=='-s' else raw
        with patch.object(C,'command',side_effect=command),patch.object(P,'E46_PUBLIC_FINGERPRINT_SHA256',control_sha):
            folder=M.test_capsule(self.root,ROOT,evidence)
        self.assertEqual((folder/'test-sources.json').read_bytes(),proof_raw)
        self.assertEqual((folder/'public-baseline.json').read_bytes(),control)
        for path in folder.rglob('*'):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o755 if path.is_dir() or path.relative_to(folder).as_posix().startswith('scripts/') else 0o644)
        self.assertEqual(len([x for x in calls if x[4]=='cat-file' and x[5]=='blob']),18)
        self.assertFalse(any('show' in a for a in calls));self.assertTrue(all(a[0]=='git' for a in calls))
        bad=deepcopy(evidence);bad['tests']['files'][0]['sha256']='0'*64
        other=self.root/'other';other.mkdir(mode=0o700)
        with patch.object(C,'command',side_effect=command),patch.object(P,'E46_PUBLIC_FINGERPRINT_SHA256',control_sha):
            with self.assertRaisesRegex(RuntimeError,'test_source_digest'):M.test_capsule(other,ROOT,bad)

    def test_gate_retains_unknown_but_never_waives_high_or_accepts_boolean_counts(self):
        value={'schema':'market_os_composition_image_export/1','security_accepted':False,
            'raw_counts':{'UNKNOWN':1,'LOW':0,'MEDIUM':0,'HIGH':0,'CRITICAL':0}}
        M.raw_gate(value);value['raw_counts']['HIGH']=1
        with self.assertRaisesRegex(RuntimeError,'raw_gate_failed'):M.raw_gate(value)
        value['raw_counts']['HIGH']=False
        with self.assertRaisesRegex(RuntimeError,'gate_receipt'):M.raw_gate(value)
        del value['raw_counts']['HIGH']
        with self.assertRaisesRegex(RuntimeError,'gate_receipt'):M.raw_gate(value)

    def test_final_evidence_change_refuses_before_docker_or_export(self):
        for name in M.REPORTS:self.write(self.root/'reports'/name,b'ordinary public report')
        record={'schema':'market_os_composition/1','recipe':{'fixture':True},'parent_image_id':M.IMAGE,
            'original_source_commit':M.SOURCE,'evidence_sha256':M.evidence_hashes(self.root),
            'whole_config_unchanged':True,'protected_app_python_opt_unchanged':True,'libc_unchanged':True,'historical_tests':85,'product_files':93,
            **{k:False for k in ('security_accepted','deployment_eligible','raw_scanner_gate_waived','production_installed','deployment_performed')}}
        self.write(self.root/'reports/composition.json',M.encoded(record));self.write(self.root/'reports/native.json',b'changed')
        @__import__('contextlib').contextmanager
        def source(root):yield {'fixture':True}
        with patch.object(M,'ci'),patch.object(M,'sources',side_effect=source),patch.object(C,'docker') as docker:
            with self.assertRaisesRegex(RuntimeError,'composition_evidence_changed'):M.finalize(self.root)
        docker.assert_not_called()

    def test_actual_compose_call_chain_uses_nonroot_config_and_exact_85_case_receipts(self):
        image='sha256:'+'c'*64;layers=['sha256:'+'1'*64]
        rootid=P.ROOT_FIXTURE_TEST_ID;ids=sorted([rootid]+['tests.ordinary.Case.test_%02d'%i for i in range(84)])
        payload={'source_commit':M.SOURCE,'payload_tree':'b'*40,'image_input_signature':'d'*64,
            'source_tree':'e'*40,'test_ids':ids,'test_sources_sha256':M.TEST_SHA,'files':[]}
        config={'User':'10001:10001','WorkingDir':'/app','Entrypoint':['python','-m','core.market_intelligence.private_pipeline_foundation'],
            'Cmd':['service','--role','market-processor'],'Env':['PUBLIC_DEFAULT=yes'],
            'Labels':{'org.opencontainers.image.revision':P.BASELINE,'io.gold-trade.release.tree':payload['payload_tree'],
                'io.gold-trade.release.input-signature':payload['image_input_signature'],'io.gold-trade.security.baseline.tree':P.BASELINE_TREE,
                'io.gold-trade.security.baseline.image':P.BASELINE_IMAGE,'io.gold-trade.security.overlay.revision':M.SOURCE,
                'io.gold-trade.security.overlay.tree':payload['source_tree']}}
        parent={'Id':M.IMAGE,'Os':'linux','Architecture':'amd64','Config':config,'RootFS':{'Type':'layers','Layers':layers}}
        child=deepcopy(parent);child['Id']=image;child['RootFS']['Layers'].append('sha256:'+'2'*64)
        before={'inventory':{**{n:'old' for n in C.PACKAGES},'libc6':'2.41-12+deb13u4'},'libc_sha256':'a'*64,'app_and_python_sha256':'b'*64,'files':100,'bytes':1000}
        after=deepcopy(before);after['inventory'].update({n:r[0] for n,r in C.PACKAGES.items()})
        original={'payload':payload,'build':{'rootfs_layers':layers},'config':{'config':config}}
        calls=[]
        def docker(root,argv,**kwargs):
            calls.append(argv)
            if argv[:2]==['image','inspect']:return M.encoded([child if argv[-1]==image else parent])
            if argv[:2]==['image','ls']:return b''
            if argv[0]=='build':
                self.write(root/'candidate.id',image.encode());self.write(root/'reports/build.log',b'ordinary build');return b'ordinary build'
            return b'ordinary'
        def receipt(*,fixture=False):
            selected=[rootid] if fixture else [x for x in ids if x!=rootid]
            return {'schema':'market_security_candidate_tests/1','image_id':image,'test_sources_sha256':M.TEST_SHA,'status':'passed',
                'tests':len(selected),'skips':0,'selected_test_ids':selected,'suite_execution_thread':'owned_nonmain_test_thread',
                'public_source_directory_mode':'0755','scope':'root_owned_metadata_fixture' if fixture else 'nonroot_product_tests',
                'execution_uid':0 if fixture else 10001,'execution_gid':0 if fixture else 10001,
                'product_files_equal_baseline':True,'runtime_data_used':False,'security_accepted':False}
        def isolated(root,target,program,purpose,**kwargs):
            self.write(root/'reports'/(purpose+'-run.log'),b'ordinary isolated result')
            if purpose=='before':return M.encoded(before)
            if purpose=='after':return M.encoded(after)
            if purpose=='market-native':return M.encoded({'schema':'market_security_native_checks/1','image_id':image,
                'status':'passed','execution_uid':10001,'execution_gid':10001,'security_accepted':False,
                'pydantic':'2.8.2','psycopg2':'2.9.13','runtime_data_used':False})
            self.assertEqual(kwargs['mounts'],((root/'payload/tests','/evidence'),));return M.encoded(receipt())
        def root_fixture(helpers,root,target,program):
            self.assertIn('root_fixture=True',program);self.write(root/'reports/market-root-fixture-run.log',b'ordinary root fixture');return M.encoded(receipt(fixture=True))
        @__import__('contextlib').contextmanager
        def source(root):yield {'fixture':True}
        def admission(path,target,evidence):
            target.mkdir();self.write(target/'candidate-image.tar',b'ordinary archive')
            return {'docker_archive_bytes':16,'docker_archive_sha256':sha256(b'ordinary archive').hexdigest()}
        def capsule(root,repo,evidence):
            (root/'payload/tests').mkdir(parents=True);return root/'payload/tests'
        def inspect_builder(root):self.write(root/'reports/builder-inspect.log',b'ordinary docker builder')
        def package(path,group,target):
            return {'packages':[{'name':n,'version':C.PACKAGES[n][0]} for n in C.GROUPS[group][5]]}
        component={'schema':'security_image_component_presence/1','image_id':image,'source_commit':'a'*40,
            'verifier_sha256':M.HELPERS['verify_image_component_presence.py'],'scope':'known_vendor_component_paths_and_names_plus_actual_perl_inc',
            **{k:False for k in ('security_accepted','scanner_findings_modified','production_mounts_or_data_used','dynamic_runtime_code_downloads_analyzed','renamed_or_obfuscated_embedded_code_analyzed','directory_symlinks_followed')}}
        def acl(root,target,commit):
            self.write(root/'reports/acl-consumer-presence.json',b'ordinary ACL result');self.write(root/'reports/acl-run.log',b'ordinary ACL log')
        self.write(self.root/'source/verify_image_component_presence.py',b'ordinary sealed component helper')
        patches=((M,'ci',Mock()),(M,'sources',source),(M,'admit_evidence',lambda p:original),(M,'admit_image',admission),
            (M,'test_capsule',capsule),(C,'fetch_artifact',Mock()),(C,'admit_packages',package),(C,'docker',docker),
            (C,'isolated',isolated),(C,'inspect_builder',inspect_builder),(C,'acl_consumer_probe',acl),(P,'root_fixture_container',root_fixture),
            (M,'COMPONENT',SimpleNamespace(in_container=lambda *a:M.encoded(component))))
        with ExitStack() as mocks:
            mocks.enter_context(patch.object(M.os,'statvfs',return_value=SimpleNamespace(f_bavail=20*1024**3,f_frsize=1)))
            for obj,name,value in patches:mocks.enter_context(patch.object(obj,name,side_effect=value) if callable(value) else patch.object(obj,name,value))
            M.compose(self.root,ROOT)
        result=json.loads((self.root/'reports/composition.json').read_bytes())
        self.assertEqual(result['historical_tests'],85);self.assertFalse(result['security_accepted'])
        self.assertEqual(result['baseline_config'],config);self.assertEqual(result['candidate_layers'],child['RootFS']['Layers'])
        self.assertEqual(len(result['evidence_sha256']),len(M.REPORTS))
        self.assertEqual(calls[0][:2],['image','load']);self.assertIn(['image','tag',M.IMAGE,M.alias_plan()['name']],calls)
        self.assertFalse(any(a[:2]==['image','rm'] for a in calls),'alias must survive through finalize/export')

    def test_workflow_keeps_three_mutually_exclusive_lanes_and_cleanup_after_export(self):
        import yaml
        workflow=yaml.safe_load((ROOT/'.github/workflows/security-os-candidate-compose.yml').read_bytes())
        jobs=workflow['jobs'];self.assertEqual(set(jobs),{'composition','datastore','market-e46'})
        for name in ('composition','datastore'):self.assertIn('!inputs.market_e46_os',jobs[name]['if'])
        market=jobs['market-e46'];steps=market['steps'];ids={s.get('id'):i for i,s in enumerate(steps)}
        self.assertLess(ids['compose'],ids['export']);self.assertLess(ids['export'],ids['cleanup'])
        upload=next(s for s in steps if s.get('with',{}).get('name','').startswith('market-e46-os-composed-image-'))
        self.assertIn("steps.cleanup.outcome == 'success'",upload['if'])
        self.assertIn("steps.export.outcome == 'success'",upload['if'])
        seal=steps[ids['seal']]['run'];self.assertIn("in ('','none')",seal);self.assertIn("fetch-depth",str(steps))
        scans=[s for s in steps if s.get('uses','').startswith('aquasecurity/trivy-action@')]
        self.assertEqual(len(scans),2);self.assertEqual(scans[0]['with']['severity'],'UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL')
        self.assertEqual(scans[0]['with']['ignore-unfixed'],'false')

    def test_duplicate_json_keys_refuse_without_content(self):
        with self.assertRaisesRegex(RuntimeError,'duplicate_json_key'):M.document(b'{"public":1,"public":2}')

    def test_unrecognized_malformed_exception_is_content_free(self):
        with patch.object(M,'compose',side_effect=RuntimeError(['ordinary-unrecognized-detail'])),redirect_stderr(io.StringIO()) as output:
            self.assertEqual(M.main(['compose','--root',str(self.root),'--repo',str(ROOT)]),1)
        value=json.loads(output.getvalue());self.assertEqual(value['code'],'unclassified_refusal')
        self.assertNotIn('ordinary-unrecognized-detail',output.getvalue())

    def test_actual_nine_python_base_history_vectors_use_explicit_lossy_policy(self):
        actual=self.config(['sha256:'+'1'*64]);actual['history']=[{'created':'2026-10-21T00:00:00Z','created_by':'ordinary base'}]
        actual['history'] += [{'created':created,'created_by':'ordinary inherited layer','comment':'buildkit.dockerfile.v0'}
            for created in ['2026-10-21T01:01:43.788094843Z']*6+['2026-10-21T01:09:58.239258151Z']+['2026-10-21T01:09:58.372959533Z']*2]
        scanner=deepcopy(actual)
        for row in scanner['history']:row['created']=row['created'].split('.')[0]+'Z' if '.' in row['created'] else row['created']
        proof=M.compare_scanner_config(actual,scanner)
        self.assertEqual(proof['history_created_entries_changed'],9);self.assertIs(proof['scanner_history_projection_lossy'],True)
        self.assertEqual(proof['nonzero_subsecond_entries_lost'],9)
        self.assertIs(proof['full_metadata_identical'],False);self.assertIs(proof['every_other_key_type_value_equal'],True)
        self.assertIs(proof['whole_runtime_config_equal'],True);self.assertIs(proof['ordered_rootfs_equal'],True)
        self.assertIs(proof['raw_scanner_report_modified'],False);self.assertEqual(proof['original_nanosecond_config_sha256'],M.IMAGE[7:])
        self.assertEqual(actual['history'][1]['created'],'2026-10-21T01:01:43.788094843Z')

    def test_history_seconds_projection_timezone_fraction_precision_and_invalid_refusal(self):
        vectors={'2026-10-21T01:01:43.788094843Z':'2026-10-21T01:01:43Z',
            '2026-10-21T02:01:43.000000001+01:00':'2026-10-21T01:01:43Z',
            '2026-10-20T23:01:43.999999999-02:00':'2026-10-21T01:01:43Z',
            '2026-10-21T01:01:43.000000000Z':'2026-10-21T01:01:43Z'}
        for raw,second in vectors.items():self.assertEqual(M.trivy_history_second(raw),second)
        for value in ('2026-10-21T01:01:43.0000000001Z','2026-10-21T01:01:60Z','2026-02-30T01:01:43Z',
                      '2026-10-21T01:01:43+24:00','2026-10-21T01:01:43+01:60','2026-10-21T01:01:43',True):
            with self.assertRaisesRegex(RuntimeError,'history_timestamp_invalid'):M.trivy_history_second(value)

    def test_wrong_second_other_history_runtime_rootfs_unknown_and_type_drift_refuse(self):
        actual=self.config(['sha256:'+'1'*64]);actual['history']=[{'created':'2026-10-21T01:01:43.788094843Z','created_by':'ordinary','empty_layer':True}]
        scanner=deepcopy(actual);scanner['history'][0]['created']='2026-10-21T01:01:43Z'
        M.compare_scanner_config(actual,scanner)
        mutations=[lambda x:x['history'][0].update(created='2026-10-21T01:01:44Z'),
            lambda x:x['history'][0].update(created_by='changed'),lambda x:x['history'][0].update(empty_layer=1),
            lambda x:x['config'].update(User='0:0'),lambda x:x['rootfs']['diff_ids'].append('sha256:'+'2'*64),
            lambda x:x.update(unknown_metadata=None),lambda x:x['history'][0].update(unknown_metadata=None),
            lambda x:x.update(created='2026-10-24T22:01:02Z')]
        for mutate in mutations:
            changed=deepcopy(scanner);mutate(changed)
            with self.assertRaisesRegex(RuntimeError,'archive_config_changed'):M.compare_scanner_config(actual,changed)

    def test_same_second_subsecond_raw_mutation_still_refuses_known_config_digest(self):
        raw,image,config,rows=self.archive();old=next(i for i,r in enumerate(rows) if r[0].endswith(image[7:]))
        value=deepcopy(config);value['created']='2026-10-24T22:01:02.123499999Z'
        rows[old]=(rows[old][0],M.encoded(value),tarfile.REGTYPE)
        with self.assertRaisesRegex(RuntimeError,'archive_config_identity'):self.validate(self.tar(rows),image,config)

    def test_actual_archive_parser_uses_raw_history_for_legacy_chain_and_only_projects_comparison(self):
        raw,image,config,rows=self.archive(legacy=True)
        # Existing fixture timestamps are integral; put the real inherited
        # nanoseconds into one history entry and regenerate its config identity.
        actual=deepcopy(config);actual['history']=[{'created':'2026-10-21T01:01:43.788094843Z','created_by':'ordinary'}]
        actual_raw=M.encoded(actual);new_image='sha256:'+sha256(actual_raw).hexdigest();old_name='blobs/sha256/'+image[7:];new_name='blobs/sha256/'+new_image[7:]
        changed=[]
        old_legacy=M.legacy_configs(config)
        for name,body,kind in rows:
            if name in old_legacy:continue
            if name==old_name:changed.append((new_name,actual_raw,kind))
            elif name=='manifest.json':
                value=json.loads(body);value[0]['Config']=new_name;changed.append((name,M.encoded(value),kind))
            elif name in ('index.json','oci-layout') or name.startswith('blobs/sha256/') and name not in [r[0] for r in rows[:2]]:
                # Rebuild only the standard OCI controls below.
                if name in [r[0] for r in rows[:2]]:changed.append((name,body,kind))
            else:changed.append((name,body,kind))
        manifest=json.loads(next(r[1] for r in rows if r[0] not in (old_name,'manifest.json','index.json','oci-layout') and r[0] not in old_legacy and r[1].startswith(b'{')))
        manifest['config']={'digest':new_image,'size':len(actual_raw)};body=M.encoded(manifest);digest=sha256(body).hexdigest()
        changed.extend([('blobs/sha256/'+digest,body,tarfile.REGTYPE),('index.json',M.encoded({'schemaVersion':2,'manifests':[{'digest':'sha256:'+digest,'size':len(body)}]}),tarfile.REGTYPE),('oci-layout',M.encoded({'imageLayoutVersion':'1.0.0'}),tarfile.REGTYPE)])
        changed.extend((n,b,tarfile.REGTYPE) for n,b in M.legacy_configs(actual).items())
        scanner=deepcopy(actual);scanner['history'][0]['created']='2026-10-21T01:01:43Z'
        result=self.validate(self.tar(changed),new_image,scanner)
        self.assertEqual(result['legacy_compatibility_configs'],3)
        self.assertIs(result['scanner_history_projection']['scanner_history_projection_lossy'],True)


if __name__=='__main__':unittest.main()
