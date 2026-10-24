"""Public metadata/pipe fixtures only: never build, install, load a library or use a network."""
import ast
from contextlib import ExitStack
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import types
import unittest
from unittest.mock import Mock,MagicMock,patch

ROOT=Path(__file__).resolve().parents[1]
RECIPE=ROOT/'deploy/maintenance/acl_tar_build'
WORKFLOW=ROOT/'.github/workflows/trixie-acl-tar-build.yml'
def load(name):
    spec=importlib.util.spec_from_file_location('acl_tar_'+name,RECIPE/(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
B,V=load('build'),load('verify')

class AclTarVendorTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        for name in ('subprocess.Popen','subprocess.run','os.system','os.killpg','socket.socket','socket.create_connection','socket.getaddrinfo','urllib.request.urlopen','ctypes.CDLL'):
            self.stack.enter_context(patch(name,side_effect=AssertionError('native_network_forbidden')))
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.acl-tar-fixture-',dir=ROOT)))
        prior=os.umask(0o077);self.addCleanup(os.umask,prior)

    def test_exact_source_pins_and_honest_local_versions(self):
        self.assertEqual(len(B.INPUTS),7)
        self.assertEqual(B.ACL_VERSION,'2.4.0-1~trixie+gold1');self.assertEqual(B.TAR_VERSION,'1.35+dfsg-5~trixie+gold1')
        self.assertEqual(B.INPUTS['tar_1.35+dfsg-5.dsc'],(2428,'e026e7a19c295212fcc3250e3136b134ae09661c559bf707b8ebfdd8f5093c0a'))
        self.assertEqual(B.INPUTS['acl_2.4.0-1.dsc'],(2624,'b987ebbeb3d498794bad29cd65301b77384978493d8765c94dba8bad8fa325b6'))
        for size,digest in B.INPUTS.values():self.assertGreater(size,0);self.assertLess(size,4*1024**2);self.assertRegex(digest,'^[a-f0-9]{64}$')
        self.assertEqual(B.RUNTIME,{'libacl1','tar'});self.assertEqual(len(B.ACL_FAMILY),3);self.assertEqual(len(B.TAR_FAMILY),2)

    def test_signed_historical_source_only_index_and_trixie_binary_dependencies(self):
        source=(RECIPE/'Dockerfile').read_text()
        self.assertIn("'Types: deb-src' 'URIs: https://snapshot.debian.org/archive/debian/20260820T000000Z/'",source)
        self.assertIn("'Suites: forky'",source);self.assertIn('20261024T000000Z',source)
        self.assertIn('Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg',source)
        self.assertIn('apt-get source --download-only --only-source acl=2.4.0-1 tar=1.35+dfsg-5',source)
        for word in ('trusted=yes','allow-unauthenticated','nocheck','--force','Suites: sid'):self.assertNotIn(word,source)

    def test_no_test_disabling_flags_and_only_source_children_get_022(self):
        self.assertEqual(B.environment()['DEB_BUILD_OPTIONS'],'parallel=2');self.assertEqual(B.environment()['DEB_BUILD_PROFILES'],'noudeb')
        source=(RECIPE/'build.py').read_text()
        self.assertIn("'/usr/bin/dpkg-buildpackage','-us','-uc','-b','-Pnoudeb'",source)
        self.assertIn('umask=0o022',source);self.assertIn('os.umask(0o077)',source)
        self.assertEqual({p.name for p in RECIPE.iterdir() if p.is_file()},B.RECIPE)

    def test_host_entrypoints_refuse_before_any_native_action(self):
        with self.assertRaisesRegex(RuntimeError,'nonroot_immutable_container_required'):B.run('mutable','acl')
        with self.assertRaisesRegex(RuntimeError,'ci_controller_only'):B.ci_build(self.root)
        with self.assertRaisesRegex(RuntimeError,'nonroot_ci_container_required'):V.run(Path('/work/baseline'),Path('/work/candidate'),'tar')

    def test_vendor_input_inventory_size_and_content(self):
        folder=self.root/'inputs';folder.mkdir();raw=b'ordinary public descriptor';(folder/'source.dsc').write_bytes(raw)
        with patch.object(B,'INPUTS',{'source.dsc':(len(raw),sha256(raw).hexdigest())}):
            B.validate_inputs(folder)
            (folder/'unlisted').write_bytes(b'public')
            with self.assertRaisesRegex(RuntimeError,'vendor_input_inventory'):B.validate_inputs(folder)
            (folder/'unlisted').unlink();(folder/'source.dsc').write_bytes(b'different')
            with self.assertRaisesRegex(RuntimeError,'vendor_input_identity'):B.validate_inputs(folder)

    def test_read_metadata_bounds_precede_body_and_reject_hardlinks(self):
        path=self.root/'input';path.write_bytes(b'normal public data')
        self.assertEqual(B.read(path),b'normal public data')
        with patch.object(B.os,'read',side_effect=AssertionError('body_was_read')):
            with self.assertRaisesRegex(RuntimeError,'input_metadata'):B.read(path,1)
        os.link(path,self.root/'alias')
        with self.assertRaisesRegex(RuntimeError,'input_metadata'):B.read(path)

    def test_path_replacement_during_public_read_refuses(self):
        path=self.root/'input';path.write_bytes(b'public');original=os.read;changed=[]
        def read(fd,maximum):
            raw=original(fd,maximum)
            if not changed:
                replacement=self.root/'new';replacement.write_bytes(b'public');replacement.replace(path);changed.append(True)
            return raw
        with patch.object(B.os,'read',side_effect=read),self.assertRaisesRegex(RuntimeError,'input_changed'):B.read(path)

    def test_source_output_cap_is_checked_before_writing_excess_and_child_reaped(self):
        stream=types.SimpleNamespace(fileno=lambda:42,close=Mock())
        child=types.SimpleNamespace(stdout=stream,pid=999999,poll=lambda:0,wait=Mock(return_value=0))
        selector=MagicMock();selector.__enter__.return_value=selector;selector.get_map.return_value={42:stream};selector.select.return_value=[(types.SimpleNamespace(fileobj=stream),None)]
        with patch.object(B.subprocess,'Popen',return_value=child) as launch,patch.object(B.selectors,'DefaultSelector',return_value=selector),patch.object(B.os,'read',return_value=b'12345'),patch.object(B,'LOG_LIMIT',4),patch.object(B.os,'killpg') as kill:
            with self.assertRaisesRegex(RuntimeError,'command_log_bound'):B.command(['/never-executed'],self.root,self.root/'log')
        self.assertEqual((self.root/'log').read_bytes(),b'');self.assertEqual(launch.call_args.kwargs['umask'],0o022)
        kill.assert_called_once();stream.close.assert_called_once();child.wait.assert_called()

    def receipt(self,phase,folder):
        folder.mkdir(parents=True)
        families=B.ACL_FAMILY if phase=='acl' else B.ACL_FAMILY|B.TAR_FAMILY
        rows=[]
        for name in sorted(families):
            version=B.ACL_VERSION if name in B.ACL_FAMILY else B.TAR_VERSION
            raw=('public-package-'+name).encode();filename=name+'_'+version+'_amd64.deb'
            rows.append({'name':name,'version':version,'path':filename,'bytes':len(raw),'sha256':sha256(raw).hexdigest()})
            (folder/filename).write_bytes(raw)
        abi={'status':'passed','phase':phase,'normal_file_acl':'passed','libraries':{'acl':{'status':'passed'}},'security_accepted':False}
        records=[]
        for suffix in ('changes','buildinfo'):
            name=phase+'.'+suffix;raw=('public record '+name).encode();(folder/name).write_bytes(raw);records.append({'path':name,'sha256':sha256(raw).hexdigest()})
        tool=b'public-toolchain=1\n';(folder/'toolchain-packages.txt').write_bytes(tool)
        (folder/'upstream-build.log').write_bytes(b'ordinary default vendor build log\n');(folder/'abi-tests.json').write_text(json.dumps(abi))
        value={'schema':B.SCHEMA,'phase':phase,'status':'candidate_built_not_accepted','all_family_packages':rows,
               'builder_image_id':'sha256:'+('a' if phase=='acl' else 'b')*64,
               'selected_runtime_packages':sorted(B.RUNTIME) if phase=='tar' else [],'compiler_phase_outputs':['libacl1','libacl1-dev'] if phase=='acl' else [],
               'inputs_sha256':{n:r[1] for n,r in B.INPUTS.items()},'recipe_sha256':{n:sha256(n.encode()).hexdigest() for n in B.RECIPE},
               'abi':abi,'build_records':records,'toolchain_inventory_sha256':sha256(tool).hexdigest(),'interactive_or_privileged_coverage':'NOT_TESTED',
               'source_authentication':'signed_debian_snapshot_sources_index_and_pinned_archive_bytes','maintainer_signatures_authenticated':False,
               'default_vendor_build':'passed','security_accepted':False,'production_installed':False,'raw_scanner_gate_waived':False,'legacy_file_api_security_assessed':False}
        (folder/'build-receipt.json').write_text(json.dumps(value));return value

    def test_receipts_distinguish_compiler_only_outputs_from_final_runtime(self):
        for phase in ('acl','tar'):
            folder=self.root/phase;value=self.receipt(phase,folder)
            self.assertEqual(B.checked_receipt(folder/'build-receipt.json',phase)[1],value)
        self.assertEqual(B.checked_receipt(self.root/'acl/build-receipt.json','acl')[1]['selected_runtime_packages'],[])

    def test_receipt_false_acceptance_missing_family_or_wrong_runtime_scope_refuses(self):
        folder=self.root/'tar';original=self.receipt('tar',folder)
        for key,value in (('security_accepted',True),('production_installed',True),('legacy_file_api_security_assessed',True),('all_family_packages',original['all_family_packages'][:-1]),('selected_runtime_packages',['libacl1','tar','acl'])):
            (folder/'build-receipt.json').write_text(json.dumps({**original,key:value}))
            with self.subTest(key=key),self.assertRaises(RuntimeError):B.checked_receipt(folder/'build-receipt.json','tar')

    def test_capsule_has_only_two_checked_packages_and_receipt_with_public_modes(self):
        folder=self.root/'acl';value=self.receipt('acl',folder);folder.chmod(0o700)
        target=B.capsule(folder,value,self.root/'capsule',{'libacl1','libacl1-dev'})
        self.assertEqual(len(list(target.iterdir())),3);self.assertEqual(stat.S_IMODE(target.stat().st_mode),0o755)
        self.assertEqual(stat.S_IMODE(folder.stat().st_mode),0o700)
        for path in target.iterdir():self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o644);self.assertEqual(path.read_bytes(),(folder/path.name).read_bytes())

    def test_capsule_tampering_or_link_refuses_before_target_creation(self):
        for kind in ('bytes','hardlink'):
            folder=self.root/kind;value=self.receipt('acl',folder);row=next(r for r in value['all_family_packages'] if r['name']=='libacl1')
            if kind=='bytes':(folder/row['path']).write_bytes(b'changed public bytes')
            else:os.link(folder/row['path'],self.root/'alias')
            with self.assertRaises(RuntimeError):B.capsule(folder,value,self.root/('capsule-'+kind),{'libacl1','libacl1-dev'})
            self.assertFalse((self.root/('capsule-'+kind)).exists())

    def test_publish_exports_exact_runtime_pair_and_retains_both_phase_proofs(self):
        acl_folder,tar_folder=self.root/'acl-work/artifacts',self.root/'tar-work/artifacts'
        acl=self.receipt('acl',acl_folder);tar=self.receipt('tar',tar_folder)
        tar['acl_build_receipt_sha256']=sha256((acl_folder/'build-receipt.json').read_bytes()).hexdigest()
        (tar_folder/'build-receipt.json').write_text(json.dumps(tar));(self.root/'public').mkdir();(self.root/'recipe-identity.json').write_text('{}')
        (self.root/'reports').mkdir()
        (self.root/'reports/compiler-toolchain.json').write_text(json.dumps({'schema':'acl_tar_compiler_layer/1',
            'compiler_only':True,'production_installed':False,'security_accepted':False,'image_id':tar['builder_image_id'],
            'parent_builder_id':acl['builder_image_id'],'acl_build_receipt_sha256':tar['acl_build_receipt_sha256']}))
        B.publish(self.root,acl,tar);output=self.root/'public'
        self.assertEqual({p.name for p in output.glob('*.deb')},{r['path'] for r in tar['all_family_packages'] if r['name'] in B.RUNTIME})
        self.assertTrue((output/'acl-build-receipt.json').is_file());self.assertTrue((output/'acl-abi-tests.json').is_file());self.assertTrue((output/'tar-abi-tests.json').is_file())
        self.assertTrue((output/'compiler-toolchain.json').is_file())
        self.assertTrue(all(stat.S_IMODE(p.stat().st_mode)==0o600 for p in output.iterdir()))

    def test_publish_refuses_wrong_acl_parent_before_any_output(self):
        acl=self.receipt('acl',self.root/'acl-work/artifacts');tar=self.receipt('tar',self.root/'tar-work/artifacts')
        tar['acl_build_receipt_sha256']='0'*64;(self.root/'public').mkdir()
        with self.assertRaisesRegex(RuntimeError,'acl_parent_changed'):B.publish(self.root,acl,tar)
        self.assertEqual(list((self.root/'public').iterdir()),[])

    def test_tar_compiler_requires_candidate_versions_and_public_header_bytes(self):
        header=b'acl_get_file_at acl_set_file_at acl_delete_def_file_at'
        def command(argv,work,log,*args):log.write_text('libacl1='+B.ACL_VERSION+'\nlibacl1-dev='+B.ACL_VERSION+'\n')
        original=B.read
        def read(path,*args):
            if path==Path('/usr/include/sys/acl.h'):return header
            if path==Path('/usr/lib/x86_64-linux-gnu/libacl.so.1'):return b'public candidate ELF identity'
            return original(path,*args)
        with patch.object(B,'command',side_effect=command),patch.object(B,'read',side_effect=read),patch.object(Path,'resolve',lambda p,**kw:p):
            value=B.coherent_acl(self.root)
        self.assertTrue(value['compiler_only']);self.assertFalse(value['production_installed']);self.assertEqual(value['header_sha256'],sha256(header).hexdigest())
        def wrong(argv,work,log,*args):log.write_text('libacl1=2.3.2-2+b1\nlibacl1-dev=2.3.2-2+b1\n')
        with patch.object(B,'command',side_effect=wrong),self.assertRaisesRegex(RuntimeError,'candidate_acl_toolchain_required'):B.coherent_acl(self.root)

    def test_installer_source_uses_absolute_local_apt_and_unchanged_inventory(self):
        ast.parse(B.INSTALL)
        self.assertIn("path.is_absolute()",B.INSTALL)
        self.assertIn("'/usr/bin/apt-get','--yes','--no-remove','install',*paths",B.INSTALL)
        self.assertNotIn("'--yes','--no-remove','--no-download'",B.INSTALL)
        self.assertIn('assert changed==names',B.INSTALL);self.assertIn("before['libc6']==after['libc6']",B.INSTALL)

    def test_actual_installer_program_keeps_scope_and_rejects_extra_or_libc_changes(self):
        for change in ('none','extra_plan','libc','extra_installed'):
            with self.subTest(change=change):
                folder=self.root/change;value=self.receipt('tar',folder)
                capsule=B.capsule(folder,value,self.root/('install-'+change),B.RUNTIME)
                tree=ast.parse(B.INSTALL)
                assignment=next(node for node in tree.body if isinstance(node,ast.Assign)
                                and any(isinstance(target,ast.Name) and target.id=='root' for target in node.targets))
                assignment.value=ast.Call(func=ast.Attribute(value=ast.Name(id='pathlib',ctx=ast.Load()),attr='Path',ctx=ast.Load()),
                                          args=[ast.Constant(str(capsule))],keywords=[])
                ast.fix_missing_locations(tree)
                before={'libacl1:amd64':B.BASELINE['libacl1'],'tar':B.BASELINE['tar'],'libc6:amd64':'2.41-fixture'}
                after={**before,'libacl1:amd64':B.ACL_VERSION,'tar':B.TAR_VERSION}
                if change=='libc':after['libc6:amd64']='2.42-fixture'
                if change=='extra_installed':after['unrelated']='new'
                queries=[];real_requests=[]
                def run(argv,**kwargs):
                    if argv[0]=='/usr/bin/dpkg-query':
                        snapshot=before if not queries else after;queries.append(True)
                        return types.SimpleNamespace(stdout=''.join(name+'='+version+'\n' for name,version in snapshot.items()).encode())
                    if argv[:2]==['/usr/bin/apt-get','--simulate']:
                        lines='Inst libacl1 (candidate)\nInst tar (candidate)\n'
                        if change=='extra_plan':lines+='Inst unrelated (candidate)\n'
                        kwargs['stdout'].write(lines.encode());return types.SimpleNamespace(returncode=0)
                    if argv[:2]==['/usr/bin/apt-get','--yes']:
                        self.assertNotIn('--no-download',argv)
                        self.assertTrue(all(Path(path).is_absolute() for path in argv[4:]))
                        real_requests.append(argv);return types.SimpleNamespace(returncode=0)
                    self.assertEqual(argv,['/usr/bin/dpkg','--audit']);return types.SimpleNamespace(stdout=b'')
                simulation=self.root/('simulation-'+change);real_open=open
                def opened(path,*args,**kwargs):
                    self.assertEqual(path,'/tmp/simulation');return real_open(simulation,*args,**kwargs)
                import io
                with patch('subprocess.run',side_effect=run),patch('sys.argv',['installer','runtime']),patch('sys.stdout',new_callable=io.StringIO) as output:
                    namespace={'open':opened}
                    if change=='none':
                        exec(compile(tree,'<actual-isolated-APT-installer>','exec'),namespace)
                        result=json.loads(output.getvalue());self.assertFalse(result['security_accepted']);self.assertFalse(result['production_installed'])
                    else:
                        with self.assertRaises(AssertionError):exec(compile(tree,'<actual-isolated-APT-installer>','exec'),namespace)
                        self.assertEqual(output.getvalue(),'')
                self.assertEqual(len(real_requests),0 if change=='extra_plan' else 1)

    def test_normal_acl_abi_metadata_and_host_paths_are_strict(self):
        symbols=' 1: 000001 1 FUNC GLOBAL DEFAULT 12 acl_from_text@@ACL_1.0\n'
        self.assertEqual(V.exports(symbols),{'acl_from_text@ACL_1.0'});self.assertEqual(V.soname('(SONAME) Library soname: [libacl.so.1]'),'libacl.so.1')
        V.check_glibc('Name: GLIBC_2.41')
        with self.assertRaisesRegex(RuntimeError,'newer_than_trixie_glibc'):V.check_glibc('Name: GLIBC_2.42')
        self.assertIn('"check"',V.CALLER);self.assertIn('strcmp(argv[3],"create")==0) assert(acl_set_file',V.CALLER)
        self.assertIn('legacy_file_api_security_assessed', (RECIPE/'verify.py').read_text())

    def test_workflow_keeps_raw_scans_and_runtime_proof_separate(self):
        import yaml
        text=WORKFLOW.read_text();job=yaml.safe_load(text)['jobs']['candidate'];self.assertEqual(job['timeout-minutes'],180)
        self.assertNotIn('pull_request:',text);self.assertNotIn('--privileged',text);self.assertNotIn('secrets.',text)
        self.assertIn('severity: UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL',text);self.assertIn("ignore-unfixed: 'false'",text)
        self.assertIn("steps.build.outputs.packages_ready == 'true'",text);self.assertIn("steps.reports.outcome == 'success'",text)
        for step in job['steps']:
            if 'run' in step and "<<'PY'" in step['run']:
                program=step['run'].split("<<'PY'\n",1)[1].rsplit('\nPY',1)[0];ast.parse(program)

    def test_controller_uses_fresh_phase_work_and_only_narrow_install_caps(self):
        text=(RECIPE/'build.py').read_text();self.assertIn("root/(purpose+'-work')",text)
        self.assertIn("('CHOWN','FOWNER','FSETID','SETUID','SETGID')",text);self.assertNotIn('DAC_OVERRIDE',text);self.assertNotIn('SYS_ADMIN',text)
        self.assertIn("container(coherent,'tar'",text);self.assertIn("--network=none",text);self.assertIn("--read-only",text)

    def test_actual_controller_sets_compiler_entrypoints_and_vendor_test_tmpfs(self):
        root=self.root/'ci';root.mkdir()
        for name in ('context','public','reports','docker-config'):(root/name).mkdir()
        hashes={}
        for name in B.RECIPE:
            raw=(RECIPE/name).read_bytes();(root/'context'/name).write_bytes(raw);hashes[name]=sha256(raw).hexdigest()
        (root/'recipe-identity.json').write_text(json.dumps({'source_commit':'f'*40,'recipe_sha256':hashes}))
        output=root/'github-output';output.write_text('')
        runtime,builder,coherent,candidate=('sha256:'+value*64 for value in ('a','b','c','d'))
        ids={name:str(index)*64 for index,name in enumerate(('acl','toolchain','tar','runtime'),1)}
        creates={};commands=[];token='123-1'
        def run(argv,**kwargs):
            self.assertEqual(argv[:2],['/usr/bin/docker','--host=unix:///var/run/docker.sock'])
            args=argv[2:];commands.append(args)
            if args[0]=='build':
                target=args[args.index('--target')+1]
                Path(args[args.index('--iidfile')+1]).write_text((runtime if target=='runtime' else builder)+'\n')
            elif args[0]=='create':
                name=args[args.index('--name')+1];purpose=name.removeprefix('trixie-acl-tar-').removesuffix('-'+token)
                creates[purpose]=args;kwargs['stdout'].write((ids[purpose]+'\n').encode())
            elif args[0]=='start':
                purpose=next(name for name,value in ids.items() if value==args[-1])
                if purpose in ('acl','tar'):
                    folder=root/(purpose+'-work')/'artifacts';value=self.receipt(purpose,folder)
                    value['builder_image_id']=builder if purpose=='acl' else coherent;value['recipe_sha256']=hashes
                    if purpose=='tar':value['acl_build_receipt_sha256']=sha256((root/'acl-work/artifacts/build-receipt.json').read_bytes()).hexdigest()
                    (folder/'build-receipt.json').write_text(json.dumps(value))
            elif args[0]=='inspect':kwargs['stdout'].write((token if 'Labels' in args[2] else '0').encode()+b'\n')
            elif args[0]=='commit':kwargs['stdout'].write((coherent if args[-1]==ids['toolchain'] else candidate).encode()+b'\n')
            else:self.assertEqual(args[0],'rm')
            return types.SimpleNamespace(returncode=0)
        original_stat=Path.stat
        def metadata(path,*args,**kwargs):
            if path==root:return types.SimpleNamespace(st_uid=1001,st_mode=stat.S_IFDIR|0o700)
            return original_stat(path,*args,**kwargs)
        env={'GITHUB_ACTIONS':'true','GITHUB_REPOSITORY':'mohsenbarari/trading_bot','RUNNER_TEMP':str(self.root),
             'GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1','GITHUB_OUTPUT':str(output)}
        with patch.dict(os.environ,env),patch.object(os,'geteuid',return_value=1001),patch.object(Path,'stat',new=metadata), \
                patch.object(os,'statvfs',return_value=types.SimpleNamespace(f_bavail=16*1024**3,f_frsize=1)), \
                patch('subprocess.run',side_effect=run):
            B.ci_build(root)
        for phase,image in (('acl',builder),('tar',coherent)):
            args=creates[phase]
            self.assertEqual(args[args.index('--entrypoint')+1],'/usr/local/bin/python3')
            self.assertEqual(args[-6:],[image,'-I','-B','/recipe/build.py',image,phase])
            self.assertIn('--read-only',args);self.assertIn('--user=1001:1001',args)
            self.assertEqual(args[args.index('--tmpfs')+1],'/tmp:rw,exec,nosuid,nodev,size=256m,uid=1001,gid=1001')
            self.assertIn('--network=none',args);self.assertIn('--cap-drop=ALL',args)
            self.assertIn('--security-opt=no-new-privileges',args)
            self.assertFalse(any(arg.startswith('--cap-add') or arg.startswith('--privileged') for arg in args))
        for phase in ('toolchain','runtime'):
            args=creates[phase]
            self.assertEqual(args[args.index('--tmpfs')+1],'/tmp:rw,nosuid,nodev,size=128m')
        self.assertLess(next(i for i,args in enumerate(commands) if args[0]=='commit' and args[-1]==ids['toolchain']),
                        next(i for i,args in enumerate(commands) if args==creates['tar']))
        self.assertIn('image_id='+candidate,output.read_text())

if __name__=='__main__':unittest.main()
