"""Hermetic ncurses packaging boundaries; no compiler/install/daemon/network use."""
import ast
import builtins
from contextlib import ExitStack, redirect_stdout
from hashlib import sha256
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import textwrap
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT/'deploy/maintenance/ncurses_build'
WORKFLOW = ROOT/'.github/workflows/trixie-ncurses-build.yml'
SPEC = importlib.util.spec_from_file_location('ncurses_packaging_fixture',RECIPE/'build.py')
B = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(B)
VERIFY_SPEC = importlib.util.spec_from_file_location('ncurses_ordinary_fixture',RECIPE/'verify.py')
V = importlib.util.module_from_spec(VERIFY_SPEC); VERIFY_SPEC.loader.exec_module(V)

def blocks():
    return [textwrap.dedent(v) for v in re.findall(r"(?ms)^          python3 -I -B - <<'PY'\n(.*?)^          PY$",WORKFLOW.read_text())]

def function(block,name,namespace):
    node=next(n for n in ast.parse(block).body if isinstance(n,ast.FunctionDef) and n.name==name)
    exec(compile(ast.Module(body=[node],type_ignores=[]),'<actual-ncurses-workflow-function>','exec'),namespace)
    return namespace[name]

class NativeNcursesPackagingTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        for name in ('subprocess.Popen','subprocess.run','os.system','os.killpg','socket.socket','socket.create_connection',
                     'socket.getaddrinfo','urllib.request.urlopen','ctypes.CDLL'):
            self.stack.enter_context(patch(name,side_effect=AssertionError('native_operation_forbidden')))
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.ncurses-package-',dir=ROOT)))
        old=os.umask(0o077);self.addCleanup(os.umask,old)

    def test_full_coherent_family_contains_thirteen_packages_only_four_are_exported(self):
        self.assertEqual(len(B.FAMILY),13);self.assertEqual(B.SELECTED,{'ncurses-base','ncurses-bin','libtinfo6','libncursesw6'})
        self.assertLess(B.SELECTED,B.FAMILY);self.assertLessEqual(B.BASELINE,B.FAMILY)
        self.assertEqual(B.VERSION,'6.6+20260608-2~trixie+gold1')

    def test_four_vendor_archives_are_pinned_and_downloaded_by_authenticated_apt(self):
        self.assertEqual(len(B.INPUTS),4)
        self.assertEqual(B.INPUTS['ncurses_6.6+20260608-2.dsc'],
                         (4163,'d12b31c11fad1c5baf42e36d643edd72cecacec67c2d14e759da42a61759582a'))
        for name,(size,digest) in B.INPUTS.items():
            self.assertTrue(name.startswith('ncurses_6.6+20260608'))
            self.assertGreater(size,0);self.assertLess(size,4*1024**2);self.assertRegex(digest,'^[a-f0-9]{64}$')
        self.assertNotIn('urllib',(RECIPE/'build.py').read_text())

    def test_docker_recipe_keeps_one_distro_signed_snapshot_and_nonroot_builder(self):
        source=(RECIPE/'Dockerfile').read_text()
        self.assertIn('python:3.11.17-slim-trixie@sha256:b95327ce1439e687237841b0210677aeadd62d55a060a5f6bcb9c15bf6251dd3',source)
        self.assertIn('Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg',source)
        self.assertIn('USER 1001:1001',source);self.assertIn('20261024T000000Z',source)
        for forbidden in ('trusted=yes','allow-unauthenticated','nocheck','sid','bookworm'):
            self.assertNotIn(forbidden,source)

    def test_normal_build_environment_does_not_disable_tests(self):
        self.assertEqual(B.environment()['DEB_BUILD_OPTIONS'],'parallel=2')
        self.assertEqual(B.environment()['DEB_BUILD_PROFILES'],'noudeb')
        self.assertNotIn('nocheck',' '.join(B.environment().values()))

    def test_forky_index_is_source_only_and_all_binary_sources_are_trixie(self):
        source=(RECIPE/'Dockerfile').read_text()
        stanza=source.split("RUN printf '%s\\n' 'Types: deb-src'",1)[1].split('> /etc/apt/sources.list.d/vendor-source.sources',1)[0]
        self.assertIn("'Suites: forky'",stanza)
        self.assertNotIn('Types: deb deb-src',stanza)
        self.assertNotIn('Types: deb\'',stanza)
        self.assertIn('apt-get source --download-only --only-source ncurses=6.6+20260608-2',source)
        self.assertIn('apt-get build-dep --yes --no-install-recommends --build-profiles=noudeb --only-source ncurses=6.6+20260608-2',source)
        self.assertNotIn('--no-check',source)

    def test_ordinary_compatibility_scope_and_old_caller_checks(self):
        self.assertEqual(set(V.CALLERS),{'ncurses','ncursesw','tinfo'})
        self.assertEqual(V.TERMINALS,('xterm','linux'))
        source=(RECIPE/'verify.py').read_text()
        for requirement in ('old_caller_changed','old_export_or_soname_changed','newer_than_trixie_glibc',
                            'ordinary_functional_compatibility','interactive_upstream_suite'):
            self.assertIn(requirement,source)
        self.assertIn("link = [str(before)]",source)
        self.assertIn('dladdr(dlsym(RTLD_DEFAULT',source)
        compile(V.PYTHON_SMOKE,'<normal-curses-readline-smoke>','exec')
        with self.assertRaisesRegex(RuntimeError,'nonroot_ci_container_required'):
            V.run(Path('/work/baseline'),Path('/work/candidate'))

    def test_library_inventory_keeps_actual_files_below_extracted_package_root(self):
        folder=self.root/'old/usr/lib/x86_64-linux-gnu';folder.mkdir(parents=True)
        target=folder/'libtinfo.so.6.5';target.write_bytes(b'public library identity fixture')
        (folder/'libtinfo.so.6').symlink_to(target.name)
        self.assertEqual(V.library(self.root/'old','tinfo'),target)
        (folder/'libtinfo.so.6').unlink();target.unlink()
        outside=self.root/'outside-public-library';outside.write_bytes(b'public identity')
        (folder/'libtinfo.so.6').symlink_to(outside)
        with self.assertRaisesRegex(RuntimeError,'library_escape'):V.library(self.root/'old','tinfo')

    def test_normal_elf_symbol_inventory_and_terminfo_comparison(self):
        text='  1: 0000000000000010 1 FUNC GLOBAL DEFAULT 12 normal@@NCURSES6_5.0.19991023\n'
        self.assertEqual(V.exports(text),{'normal@NCURSES6_5.0.19991023'})
        self.assertEqual(V.soname(' 0x000000000000000e (SONAME) Library soname: [libtinfo.so.6]'), 'libtinfo.so.6')
        self.assertEqual(V.normalized_entry('# normal reconstructed comment\nxterm|terminal,\n\tcols#80,\n'),
                         V.normalized_entry('xterm|terminal,\ncols#80,\n'))

    def test_entrypoints_refuse_host_execution_before_any_process_or_network(self):
        with self.assertRaisesRegex(RuntimeError,'nonroot_immutable_container_required'):B.run('mutable-tag')
        with patch.object(B.os,'geteuid',return_value=0):
            with self.assertRaisesRegex(RuntimeError,'nonroot_immutable_container_required'):B.run('sha256:'+'a'*64)
            with self.assertRaisesRegex(RuntimeError,'preparation_container_only'):B.verify_prepared_inputs()

    def test_normal_input_read_returns_exact_bytes_and_refuses_unsafe_metadata(self):
        path=self.root/'public';path.write_bytes(b'public')
        self.assertEqual(B.read(path),b'public')
        path.chmod(0o666)
        with self.assertRaisesRegex(RuntimeError,'input_metadata'):B.read(path)
        path.chmod(0o600);alias=self.root/'hard';os.link(path,alias)
        with self.assertRaisesRegex(RuntimeError,'input_metadata'):B.read(path)
        alias.unlink();alias.symlink_to(path)
        with self.assertRaises(OSError):B.read(alias)
        fifo=self.root/'fifo';os.mkfifo(fifo)
        with self.assertRaisesRegex(RuntimeError,'input_metadata'):B.read(fifo)

    def test_input_size_is_checked_before_reading_body(self):
        path=self.root/'large';path.write_bytes(b'1234')
        with patch.object(B.os,'read',side_effect=AssertionError('late_bound')):
            with self.assertRaisesRegex(RuntimeError,'input_metadata'):B.read(path,3)

    def test_input_inode_swap_during_read_is_detected(self):
        path=self.root/'changing';path.write_bytes(b'public');real_read=os.read;changed=False
        def read(fd,size):
            nonlocal changed
            value=real_read(fd,size)
            if not changed:
                changed=True;other=self.root/'replacement';other.write_bytes(b'public');os.replace(other,path)
            return value
        with patch.object(B.os,'read',side_effect=read):
            with self.assertRaisesRegex(RuntimeError,'input_changed'):B.read(path)

    def test_prepared_vendor_inputs_require_exact_inventory_sizes_and_hashes(self):
        folder=self.root/'inputs';folder.mkdir()
        rows={}
        for name in ('source.dsc','vendor.orig.tar.gz'):
            raw=('normal-public-'+name).encode();(folder/name).write_bytes(raw)
            rows[name]=(len(raw),sha256(raw).hexdigest())
        with patch.object(B,'INPUTS',rows):
            B.validate_inputs(folder)
            (folder/'extra').write_bytes(b'excluded')
            with self.assertRaisesRegex(RuntimeError,'vendor_input_inventory'):B.validate_inputs(folder)
            (folder/'extra').unlink();(folder/'source.dsc').write_bytes(b'changed')
            with self.assertRaisesRegex(RuntimeError,'vendor_input_identity'):B.validate_inputs(folder)

    def test_default_vendor_build_uses_dpkg_source_without_local_patches_or_test_skips(self):
        source=(RECIPE/'build.py').read_text()
        self.assertIn("'/usr/bin/dpkg-source', '-x'",source)
        self.assertIn("'/usr/bin/dpkg-buildpackage', '-us', '-uc', '-b', '-Pnoudeb'",source)
        self.assertNotIn('nocheck',source)
        self.assertEqual(B.RECIPE,{'Dockerfile','build.py','verify.py'})
        self.assertEqual({p.name for p in RECIPE.iterdir() if p.is_file()},B.RECIPE)

    def child_fixture(self,*,code=0,has_output=True):
        stdout=SimpleNamespace(fileno=lambda:42,close=Mock())
        child=SimpleNamespace(stdout=stdout,pid=99999999,poll=lambda:code,wait=Mock(return_value=code))
        key=SimpleNamespace(fileobj=stdout)
        selector=MagicMock();selector.__enter__.return_value=selector
        selector.get_map.return_value={42:key} if has_output else {}
        selector.select.return_value=[(key,None)]
        return child,selector

    def test_command_live_output_cap_refuses_before_persisting_extra_bytes(self):
        child,selector=self.child_fixture();log=self.root/'stream.log'
        with patch.object(B.subprocess,'Popen',return_value=child),patch.object(B.selectors,'DefaultSelector',return_value=selector),\
             patch.object(B.os,'read',return_value=b'123456789'),patch.object(B,'LOG_LIMIT',8),patch.object(B.os,'killpg') as kill:
            with self.assertRaisesRegex(RuntimeError,'command_log_bound'):B.command(['/never-executed'],self.root,log)
        self.assertEqual(log.read_bytes(),b'');kill.assert_called_once();child.stdout.close.assert_called_once()

    def test_command_timeout_kills_only_its_mock_process_and_leaves_no_false_success(self):
        child,selector=self.child_fixture();log=self.root/'timeout.log'
        with patch.object(B.subprocess,'Popen',return_value=child),patch.object(B.selectors,'DefaultSelector',return_value=selector),\
             patch.object(B.time,'monotonic',side_effect=[0,2]),patch.object(B.os,'killpg') as kill:
            with self.assertRaisesRegex(RuntimeError,'command_timeout'):B.command(['/never-executed'],self.root,log,1)
        self.assertEqual(log.read_bytes(),b'');kill.assert_called_once()

    def test_every_workflow_python_program_and_nested_installer_parses(self):
        self.assertEqual(len(blocks()),5)
        for block in blocks():
            tree=ast.parse(block)
            for node in tree.body:
                if isinstance(node,ast.Assign) and any(isinstance(n,ast.Name) and n.id=='install' for n in node.targets):
                    ast.parse(ast.literal_eval(node.value))

    def test_workflow_is_manual_scoped_and_compiles_offline_without_privileges(self):
        source=WORKFLOW.read_text()
        for value in ('workflow_dispatch:',"refs/heads/main","refs/heads/security/remediation-20261023",'contents: read',
                      "'--user=1001:1001','--network=none','--read-only'","'--cap-drop=ALL'","'--cpus=2','--memory=4g'",
                      "'compile-tests',4950",'timeout-minutes: 150'):
            self.assertIn(value,source)
        for value in ('--privileged','secrets.','pull_request:','primary-config.h','primary_configuration_sha256','util_linux'):
            self.assertNotIn(value,source)
        self.assertIn('signal.alarm(4800)',(RECIPE/'build.py').read_text())

    def artifact_fixture(self, *, folder=None):
        root=folder if folder is not None else self.root/'ci';root.mkdir();(root/'output').mkdir();source=root/'output/artifacts';source.mkdir()
        (root/'public').mkdir();(root/'reports').mkdir()
        def write(path,data):path.write_bytes(data);path.chmod(0o600);return data
        packages=[]
        for name in sorted(B.FAMILY):
            raw=('public-package-'+name).encode()
            packages.append({'name':name,'path':name+'.deb','version':B.VERSION,'bytes':len(raw),'sha256':sha256(raw).hexdigest()})
            if name in B.SELECTED:write(source/(name+'.deb'),raw)
        records=[]
        for suffix in ('changes','buildinfo'):
            name='ncurses_candidate.'+suffix;raw=write(source/name,b'public-build-record')
            records.append({'path':name,'sha256':sha256(raw).hexdigest()})
        recipe={name:sha256(name.encode()).hexdigest() for name in ('Dockerfile','build.py','verify.py')}
        builder='sha256:'+'a'*64
        write(root/'recipe-identity.json',json.dumps({'source_commit':'b'*40,'recipe_sha256':recipe}).encode())
        write(root/'builder.id',(builder+'\n').encode())
        abi={'status':'passed','ordinary_functional_compatibility':'passed'}
        write(source/'abi-tests.json',json.dumps(abi).encode())
        write(source/'upstream-build.log',b'public compiler log\n')
        toolchain=write(source/'toolchain-packages.txt',b'public-toolchain\t1\n')
        receipt={'schema':'trixie_ncurses_vendor_candidate/1','status':'candidate_built_not_accepted','security_accepted':False,
            'production_installed':False,'raw_scanner_gate_waived':False,'version':B.VERSION,'selected_runtime_packages':sorted(B.SELECTED),
            'abi':abi,'maintainer_signatures_authenticated':False,
            'source_authentication':'signed_debian_snapshot_sources_index_and_pinned_archive_bytes','vendor_source_version':B.VENDOR,
            'interactive_upstream_suite':'NOT_TESTED','recipe_sha256':recipe,'all_family_packages':packages,'build_records':records,
            'toolchain_inventory_sha256':sha256(toolchain).hexdigest(),'builder_image_id':builder}
        write(source/'build-receipt.json',json.dumps(receipt).encode())
        return root,source,receipt

    def export(self,root):
        block=next(value for value in blocks() if "source=root/'output/artifacts';target=root/'public'" in value)
        with patch.dict(os.environ,{'CANDIDATE_ROOT':str(root)}),redirect_stdout(io.StringIO()):
            exec(compile(block,'<actual-workflow-export>','exec'),{})

    def test_actual_export_retains_only_four_runtime_debs_and_public_proof_bytes(self):
        root,source,receipt=self.artifact_fixture();self.export(root)
        target=root/'public'
        self.assertEqual({p.name for p in target.glob('*.deb')},{n+'.deb' for n in B.SELECTED})
        self.assertEqual(len(list(target.iterdir())),11)
        for path in target.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)
            if path.name!='recipe-identity.json':self.assertEqual(path.read_bytes(),(source/path.name).read_bytes())

    def test_public_installer_capsule_has_exact_five_inputs_without_opening_private_tree(self):
        root,source,receipt=self.artifact_fixture();source.chmod(0o700);root.chmod(0o700)
        block=next(value for value in blocks() if 'def install_capsule(' in value)
        capsule=function(block,'install_capsule',{'os':os,'pathlib':__import__('pathlib'),'stat':stat,'json':json,'sha256':sha256})
        target=capsule(root,receipt)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode),0o755)
        self.assertEqual(stat.S_IMODE(source.stat().st_mode),0o700)
        self.assertEqual({p.name for p in target.iterdir()},{'build-receipt.json'}|{name+'.deb' for name in B.SELECTED})
        for item in target.iterdir():
            self.assertEqual(stat.S_IMODE(item.stat().st_mode),0o644)
            self.assertEqual(item.read_bytes(),(source/item.name).read_bytes())

    def test_capsule_validation_precedes_creation(self):
        root,source,receipt=self.artifact_fixture()
        block=next(value for value in blocks() if 'def install_capsule(' in value)
        capsule=function(block,'install_capsule',{'os':os,'pathlib':__import__('pathlib'),'stat':stat,'json':json,'sha256':sha256})
        (source/'libncursesw6.deb').write_bytes(b'changed public package')
        with self.assertRaises(AssertionError):capsule(root,receipt)
        self.assertFalse((root/'install-public').exists())

    def nested_installer_fixture(self, *, simulation=None):
        folder = Path(self.stack.enter_context(tempfile.TemporaryDirectory(dir=self.root))) / 'ci'
        root, source, receipt = self.artifact_fixture(folder=folder)
        block = next(value for value in blocks() if 'def install_capsule(' in value)
        tree=ast.parse(block)
        install=next(ast.literal_eval(node.value) for node in tree.body if isinstance(node,ast.Assign)
                     and any(isinstance(target,ast.Name) and target.id=='install' for target in node.targets))
        rows = [row for row in receipt['all_family_packages'] if row['name'] in B.SELECTED]
        paths = [str(source / row['path']) for row in rows]
        if simulation is None:
            simulation = ''.join('Inst ' + row['name'] + ':amd64 (' + row['version'] + ')\n' for row in rows)
        def run(arguments, **options):
            if arguments[:5] == ['/usr/bin/apt-get', '--simulate', '--no-remove', '--no-download', 'install']:
                options['stdout'].write(simulation.encode())
                return SimpleNamespace(returncode=0)
            if arguments[:4] == ['/usr/bin/apt-get', '--yes', '--no-remove', 'install']:
                return SimpleNamespace(returncode=0)
            if arguments == ['/usr/bin/dpkg', '--audit']:
                return SimpleNamespace(returncode=0, stdout=b'')
            if arguments[:3] == ['/usr/bin/dpkg-query', '-W', '-f=${Package}=${Version}\n']:
                return SimpleNamespace(returncode=0, stdout=''.join(
                    row['name'] + '=' + row['version'] + '\n' for row in rows).encode())
            raise AssertionError('unexpected_installer_command')
        command = Mock(side_effect=run)
        modules = {'pathlib': SimpleNamespace(Path=lambda value: source if value == '/packages' else Path(value)),
                   'subprocess': SimpleNamespace(run=command), 'json': json, 'os': os,
                   'stat': stat, 'hashlib': SimpleNamespace(sha256=sha256)}
        def import_module(name, *args, **kwargs):
            if name not in modules:
                raise AssertionError('unexpected_installer_import')
            return modules[name]
        def simulation_file(path, mode):
            self.assertEqual(path, '/tmp/simulation')
            return builtins.open(root / 'simulation', mode)
        def execute():
            exec(compile(install, '<actual-disposable-ncurses-installer>', 'exec'), {
                '__builtins__': {**vars(builtins), '__import__': import_module}, 'open': simulation_file})
        return execute, command, paths

    def test_actual_installer_acquires_exact_local_files_then_audits_all_four_versions(self):
        execute, command, paths = self.nested_installer_fixture()
        execute()
        self.assertEqual([call.args[0] for call in command.call_args_list], [
            ['/usr/bin/apt-get', '--simulate', '--no-remove', '--no-download', 'install', *paths],
            ['/usr/bin/apt-get', '--yes', '--no-remove', 'install', *paths],
            ['/usr/bin/dpkg', '--audit'],
            ['/usr/bin/dpkg-query', '-W', '-f=${Package}=${Version}\n', *sorted(B.SELECTED)]])
        self.assertTrue(all(call.kwargs['check'] for call in command.call_args_list))
        self.assertEqual(command.call_args_list[1].kwargs['timeout'], 120)

    def test_actual_installer_refuses_removal_or_unrelated_plan_before_local_acquisition(self):
        normal = ''.join('Inst ' + name + ':amd64 (candidate)\n' for name in sorted(B.SELECTED))
        for suffix in ('Remv unrelated [1]\n', 'Inst unrelated (1)\n'):
            with self.subTest(suffix=suffix):
                execute, command, _ = self.nested_installer_fixture(simulation=normal + suffix)
                with self.assertRaises(AssertionError):
                    execute()
                self.assertEqual(command.call_count, 1)

    def test_actual_installer_container_keeps_network_disabled_and_existing_capabilities(self):
        tree = next(ast.parse(block) for block in blocks() if 'def install_capsule(' in block)
        create = next(call for call in ast.walk(tree) if isinstance(call, ast.Call)
                      and isinstance(call.func, ast.Name) and call.func.id == 'command'
                      and len(call.args) > 1 and isinstance(call.args[1], ast.Constant)
                      and call.args[1].value == 'candidate-create')
        flags = [node.value for node in create.args[0].elts if isinstance(node, ast.Constant)]
        self.assertIn('--network=none', flags)
        self.assertIn('--cap-drop=ALL', flags)
        self.assertEqual([flag for flag in flags if flag.startswith('--cap-add=')],
                         ['--cap-add=CHOWN', '--cap-add=FOWNER', '--cap-add=FSETID',
                          '--cap-add=SETUID', '--cap-add=SETGID'])

    def test_export_refuses_incomplete_family_or_false_acceptance_before_copy(self):
        root,source,receipt=self.artifact_fixture()
        for key,value in (('all_family_packages',receipt['all_family_packages'][:-1]),('security_accepted',True),
                          ('production_installed',True),('raw_scanner_gate_waived',True),('schema','wrong')):
            changed={**receipt,key:value};(source/'build-receipt.json').write_text(json.dumps(changed))
            with self.subTest(key=key),self.assertRaises(AssertionError):self.export(root)
            self.assertEqual(list((root/'public').iterdir()),[])

    def test_export_rejects_path_escape_extra_artifact_and_wrong_toolchain_digest(self):
        root,source,receipt=self.artifact_fixture()
        copy_packages=json.loads(json.dumps(receipt['all_family_packages']))
        changed={**receipt,'all_family_packages':copy_packages}
        copy_packages[0]['path']='../escape.deb';(source/'build-receipt.json').write_text(json.dumps(changed))
        with self.assertRaises(AssertionError):self.export(root)
        (source/'build-receipt.json').write_text(json.dumps(receipt));extra=source/'extra';extra.write_bytes(b'not-exportable')
        with self.assertRaises(AssertionError):self.export(root)
        extra.unlink();(source/'toolchain-packages.txt').write_bytes(b'changed-toolchain')
        with self.assertRaises(AssertionError):self.export(root)

    def test_export_rejects_package_byte_tampering(self):
        root,source,receipt=self.artifact_fixture();(source/'libncursesw6.deb').write_bytes(b'tampered')
        with self.assertRaises(AssertionError):self.export(root)

    def report_read(self):
        block=next(value for value in blocks() if 'def verify_report_identity' in value)
        return function(block,'read',{'os':os,'stat':stat,'json':json})

    def test_report_limits_are_early_and_metadata_refusals_do_not_leak_contents(self):
        read=self.report_read();path=self.root/'vulnerabilities.json';path.write_bytes(b'PRIVATE-BODY-MARKER')
        output=io.StringIO()
        with redirect_stdout(output),patch.object(os,'read',side_effect=AssertionError('late_bound')):
            with self.assertRaises(AssertionError) as error:read(path,1)
        self.assertNotEqual(str(error.exception),'late_bound');self.assertNotIn('PRIVATE-BODY',output.getvalue())
        os.link(path,self.root/'alias')
        with redirect_stdout(io.StringIO()),self.assertRaises(AssertionError):read(path,1024)

    def test_raw_report_schema_and_actual_image_identity_are_required(self):
        block=next(value for value in blocks() if 'def verify_report_identity' in value)
        verify=function(block,'verify_report_identity',{})
        ident='sha256:'+'c'*64
        value={'SchemaVersion':2,'ArtifactType':'container_image','Metadata':{'ImageID':ident},'Results':[{'Vulnerabilities':[{'Severity':'HIGH'}]}]}
        verify(value,ident)
        for changed in ({},{**value,'SchemaVersion':1},{**value,'Metadata':{'ImageID':'wrong'}},{**value,'Results':[]}):
            with self.assertRaises(AssertionError):verify(changed,ident)

    def test_actual_report_admission_preserves_high_unfixed_rows_and_requires_all_four_versions(self):
        root,source,receipt=self.artifact_fixture();ident='sha256:'+'c'*64
        (root/'reports/candidate-image.json').write_text(json.dumps({'image_id':ident,'security_accepted':False}))
        findings={'SchemaVersion':2,'ArtifactType':'container_image','Metadata':{'ImageID':ident},
                  'Results':[{'Vulnerabilities':[{'VulnerabilityID':'PUBLIC-FIXTURE','Severity':'HIGH','FixedVersion':''}]}]}
        raw=json.dumps(findings).encode();(root/'reports/vulnerabilities.json').write_bytes(raw)
        sbom={'components':[{'name':name,'version':B.VERSION} for name in sorted(B.SELECTED)]}
        (root/'reports/sbom.cdx.json').write_text(json.dumps(sbom))
        block=next(value for value in blocks() if 'def verify_report_identity' in value)
        with patch.dict(os.environ,{'CANDIDATE_ROOT':str(root)}),redirect_stdout(io.StringIO()) as output:
            exec(compile(block,'<actual-report-admission>','exec'),{})
        self.assertEqual((root/'reports/vulnerabilities.json').read_bytes(),raw)
        self.assertIn('"security_accepted": false',output.getvalue())
        sbom['components'].pop();(root/'reports/sbom.cdx.json').write_text(json.dumps(sbom))
        with patch.dict(os.environ,{'CANDIDATE_ROOT':str(root)}),redirect_stdout(io.StringIO()),self.assertRaises(AssertionError):
            exec(compile(block,'<actual-report-admission-negative>','exec'),{})

    def test_report_parent_redirect_refuses_without_reading_target_contents(self):
        read=self.report_read();target=self.root/'target';target.mkdir();path=target/'report';path.write_bytes(b'public')
        alias=self.root/'redirect';alias.symlink_to(target,target_is_directory=True)
        with redirect_stdout(io.StringIO()),patch.object(os,'read',side_effect=AssertionError('redirect_was_followed')):
            with self.assertRaises(AssertionError) as error:read(alias/'report',1024)
        self.assertNotEqual(str(error.exception),'redirect_was_followed')

    def test_failure_only_public_diagnostic_is_bounded_escaped_and_closed_to_unknown_logs(self):
        root=self.root/'diagnostics';root.mkdir();(root/'output').mkdir()
        (root/'output/build.log').write_bytes(b'x'*70000+b'\nerror: bounded public failure ::error::test\n')
        (root/'unlisted.log').write_bytes(b'UNLISTED_MARKER')
        block=next(value for value in blocks() if "names=('runtime-prepare.log'" in value)
        with patch.dict(os.environ,{'CANDIDATE_ROOT':str(root)}),redirect_stdout(io.StringIO()) as output:
            exec(compile(block,'<actual-public-failure-diagnostic>','exec'),{})
        value=output.getvalue();self.assertNotIn('UNLISTED_MARKER',value);self.assertLess(len(value.encode()),32768)
        records=[json.loads(line) for line in value.splitlines()]
        self.assertEqual([v['name'] for v in records],['output/build.log']);self.assertIn('bounded public failure',records[0]['tail'])
        self.assertNotIn('\n::error::',value)

    def test_all_severities_and_unfixed_rows_are_evidence_and_never_acceptance(self):
        source=WORKFLOW.read_text()
        for value in ('severity: UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL',"ignore-unfixed: 'false'",'TRIVY_IGNOREFILE: /dev/null',
                      '32*1024**2','8*1024**2',"steps.export.outcome == 'success'","steps.reports.outcome == 'success'"):
            self.assertIn(value,source)
        self.assertEqual(source.count('retention-days: 7'),2)
        self.assertEqual(source.count("if: always() && steps.build.outputs.image_id != ''"),3)

    def test_candidate_receipt_binds_toolchain_and_does_not_claim_interactive_or_security_pass(self):
        source=(RECIPE/'build.py').read_text()
        self.assertIn("'toolchain_inventory_sha256': sha256(read(inputs / 'toolchain-packages.txt')).hexdigest()",source)
        self.assertIn("'interactive_upstream_suite': 'NOT_TESTED'",source)
        self.assertIn("'maintainer_signatures_authenticated': False",source)
        self.assertIn("'security_accepted': False",source)

if __name__=='__main__':unittest.main()
