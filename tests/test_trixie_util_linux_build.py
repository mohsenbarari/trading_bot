"""Hermetic source contracts plus an explicit local actionlint preflight."""
import ast
import builtins
from contextlib import ExitStack
from hashlib import sha256
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT / 'deploy/maintenance/util_linux_build'
WORKFLOW = ROOT / '.github/workflows/trixie-util-linux-build.yml'


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, RECIPE / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


B = load('trixie_build_tests', 'build.py')
V = load('trixie_abi_tests', 'verify.py')
S = load('trixie_source_tests', 'source_regressions.py')


def blocks():
    text = WORKFLOW.read_text()
    return [textwrap.dedent(value) for value in re.findall(
        r"(?ms)^          python3 -I -B - <<'PY'\n(.*?)^          PY$", text)]


def validate_actionlint_step_environment(executable):
    """Lint the real workflow and its rejected predecessor without running steps."""
    workflow = WORKFLOW.read_text()
    invalid, count = re.subn(
        r'(?m)^(\s*PRINT_FAILURE_TAILS:) .+$',
        r'\1 ${{ failure() }}', workflow,
    )
    if count != 1:
        raise AssertionError('expected_one_failure_tail_environment_binding')
    line = next(index for index, value in enumerate(invalid.splitlines(), 1)
                if 'PRINT_FAILURE_TAILS:' in value)
    with tempfile.TemporaryDirectory(prefix='util-linux-actionlint-') as directory:
        config = Path(directory) / 'actionlint.yaml'
        config.write_text('{}\n')
        arguments = [str(executable), '-no-color', '-shellcheck=', '-pyflakes=',
                     '-config-file', str(config), '-format', '{{json .}}',
                     '-stdin-filename', WORKFLOW.name, '-']
        valid = subprocess.run(arguments, input=workflow, text=True, capture_output=True,
                               check=False, timeout=15)
        if valid.returncode != 0 or valid.stderr or json.loads(valid.stdout) != []:
            raise AssertionError('corrected_workflow_actionlint_failed')
        rejected = subprocess.run(arguments, input=invalid, text=True, capture_output=True,
                                  check=False, timeout=15)
        errors = json.loads(rejected.stdout)
        if (rejected.returncode != 1 or rejected.stderr or len(errors) != 1
                or errors[0].get('kind') != 'expression' or errors[0].get('line') != line
                or 'calling function "failure" is not allowed here' not in errors[0].get('message', '')):
            raise AssertionError('old_step_environment_was_not_semantically_rejected')
    return {'status': 'passed', 'corrected_workflow_valid': True,
            'old_failure_function_environment_rejected': True}


class TrixieUtilLinuxBuildTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        for name in ('subprocess.Popen', 'subprocess.run', 'os.system', 'socket.socket',
                     'socket.create_connection', 'socket.getaddrinfo', 'urllib.request.urlopen', 'ctypes.CDLL'):
            self.stack.enter_context(patch(name, side_effect=AssertionError('no_native_build_install_network')))
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.util-linux-source-', dir=ROOT)))

    def test_exact_pinned_inputs_and_honest_local_versions(self):
        self.assertEqual(len(B.INPUTS), 7)
        for name, (url, size, digest) in B.INPUTS.items():
            self.assertRegex(digest, r'^[0-9a-f]{64}$')
            self.assertGreater(size, 0)
            self.assertLessEqual(size, 12 * 1024**2)
            self.assertTrue(url.startswith(('https://deb.debian.org/', 'https://www.kernel.org/', 'https://github.com/util-linux/util-linux/commit/')))
        self.assertEqual(B.expected_version('bsdutils'), '1:2.41.6-0+deb13u0+gold1')
        self.assertEqual(B.expected_version('login'), '1:4.16.0-2+really2.41.6-0+deb13u0+gold1')
        self.assertEqual(B.expected_version('mount'), B.VERSION)
        self.assertNotIn('2.42', B.VERSION)

    def test_runtime_nine_are_a_subset_of_complete_twenty_five_family_build(self):
        self.assertEqual(B.SELECTED, {'bsdutils', 'libblkid1', 'liblastlog2-2', 'libmount1',
                                     'libsmartcols1', 'libuuid1', 'login', 'mount', 'util-linux'})
        self.assertEqual(len(B.FULL_FAMILY), 25)
        self.assertLess(B.SELECTED, B.FULL_FAMILY)
        self.assertLessEqual(B.BASELINE, B.FULL_FAMILY)

    def test_failure_diagnostic_emits_only_closed_codes_not_arbitrary_exception_text(self):
        self.assertEqual(B.refusal_code(RuntimeError('input_metadata')), 'input_metadata')
        for value in ('/private/path/secret', 'unexpected raw data', {'private': 'value'}):
            self.assertEqual(B.refusal_code(RuntimeError(value)), 'RuntimeError')
        for value in B.ERROR_CODES:
            self.assertRegex(value, r'^[a-z0-9_]{1,64}$')

    def test_toolchain_is_trixie_authenticated_snapshot_and_nonroot(self):
        source = (RECIPE / 'Dockerfile').read_text()
        self.assertIn('python:3.11.17-slim-trixie@sha256:b95327ce1439e687237841b0210677aeadd62d55a060a5f6bcb9c15bf6251dd3', source)
        self.assertIn('20261024T000000Z', source)
        self.assertIn('Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg', source)
        self.assertIn('--build-profiles=noudeb util-linux=2.41.5-0+deb13u1', source)
        self.assertIn('USER 1001:1001', source)
        self.assertIn('COPY --chmod=0644 Dockerfile build.py verify.py source_regressions.py /recipe/', source)
        for word in ('sid', 'trusted=yes', 'allow-unauthenticated', 'nocheck', 'stage1', 'noverity'):
            self.assertNotIn(word, source)

    def test_build_environment_retains_all_tests_and_normal_feature_profiles(self):
        value = B.environment()
        self.assertEqual(value['DEB_BUILD_PROFILES'], 'noudeb')
        self.assertEqual(value['DEB_BUILD_OPTIONS'], 'parallel=2')
        self.assertEqual(value['QUILT_PATCH_OPTS'], '--fuzz=0')
        self.assertNotIn('GITLAB_CI', value)

    def test_safe_read_refuses_link_write_fifo_and_oversize_before_read(self):
        path = self.root / 'input'; path.write_bytes(b'public-proof')
        self.assertEqual(B.read(path), b'public-proof')
        with patch.object(B.os, 'read', side_effect=AssertionError('early_bound_missing')):
            with self.assertRaisesRegex(RuntimeError, 'input_metadata'):
                B.read(path, 1)
        path.chmod(0o666)
        with self.assertRaises(RuntimeError): B.read(path)
        path.chmod(0o600); alias = self.root / 'hardlink'; os.link(path, alias)
        with self.assertRaises(RuntimeError): B.read(path)
        alias.unlink(); alias.symlink_to(path)
        with self.assertRaises(OSError): B.read(alias)
        fifo = self.root / 'fifo'; os.mkfifo(fifo)
        with self.assertRaises(RuntimeError): B.read(fifo)

    def archive(self, name, *, kind=None, link=None):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w:xz') as archive:
            info = tarfile.TarInfo(name); info.mode = 0o644
            if kind:
                info.type = kind; info.linkname = link or ''
                archive.addfile(info)
            else:
                info.size = 6; archive.addfile(info, io.BytesIO(b'public'))
        return output.getvalue()

    def test_real_tar_extraction_is_bounded_and_stays_inside_single_root(self):
        folder = B.unpack(self.archive('candidate/file'), self.root, 'candidate')
        self.assertEqual((folder / 'file').read_bytes(), b'public')
        for name in ('../escape', '/candidate/file', 'candidate/../escape', 'other/file'):
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                B.unpack(self.archive(name), self.root, 'candidate')

    def test_public_tar_links_and_special_nodes_cannot_escape(self):
        for kind, target in ((tarfile.LNKTYPE, 'candidate/file'), (tarfile.SYMTYPE, '../../escape'),
                             (tarfile.SYMTYPE, '../other-source-tree'),
                             (tarfile.FIFOTYPE, None), (tarfile.CHRTYPE, None)):
            with self.subTest(kind=kind), self.assertRaises((RuntimeError, tarfile.FilterError)):
                B.unpack(self.archive('candidate/node', kind=kind, link=target), self.root, 'candidate')

    def test_upstream_root_skip_and_missing_test_count_do_not_pass(self):
        log = self.root / 'build.log'
        for text in ('Ignore util-linux test suite [non-root UID expected].\n', 'compiler completed\n', 'All 0 tests PASSED\n'):
            log.write_text(text)
            with self.assertRaises(RuntimeError): B.suite_evidence(log)
        log.write_text('some genuine suite output\nAll 638 tests PASSED\n')
        self.assertEqual(B.suite_evidence(log)['reported_suite_script_counts'], [638])

    def test_command_stream_bound_refuses_block_before_persisting_without_any_process(self):
        stdout = types.SimpleNamespace(fileno=lambda: 42, close=lambda: None)
        child = types.SimpleNamespace(stdout=stdout, poll=lambda: 0, wait=lambda **_: 0)
        key = types.SimpleNamespace(fileobj=stdout)
        selector = types.SimpleNamespace(register=lambda *_: None, get_map=lambda: {42: key},
                                         select=lambda _: [(key, None)], close=lambda: None)
        log = self.root / 'bounded.log'
        with patch.object(B.subprocess, 'Popen', return_value=child), \
             patch.object(B.selectors, 'DefaultSelector', return_value=selector), \
             patch.object(B.os, 'read', return_value=b'x' * 9), patch.object(B, 'LOG_LIMIT', 8):
            with self.assertRaisesRegex(RuntimeError, 'command_log_bound'):
                B.command(['/never-executed'], self.root, log)
        self.assertEqual(log.read_bytes(), b'')

    def test_only_two_integrated_series_entries_are_dropped(self):
        self.assertEqual(len(B.ORIGINAL_SERIES), 11)
        self.assertTrue(all(n.startswith('upstream/') for n in B.ORIGINAL_SERIES[-2:]))
        source = (RECIPE / 'build.py').read_text()
        self.assertIn('(*ORIGINAL_SERIES[:-2], *FOLLOWUPS)', source)
        self.assertEqual(set(B.FOLLOWUPS), {'79c2881c.patch', '485dbb67.patch', '233cf732.patch'})
        self.assertIn("'packaging_series_changed'", source)

    def test_abi_export_inventory_preserves_versions_but_ignores_undefined_imports(self):
        text = '\n'.join(('1: 000001 2 FUNC GLOBAL DEFAULT 12 old_api@@VERS_1',
                          '2: 000002 2 FUNC WEAK DEFAULT 12 weak_api@VERS_1',
                          '3: 000000 0 FUNC GLOBAL DEFAULT UND imported@GLIBC_2.2.5'))
        self.assertEqual(V.exports(text), {'old_api@VERS_1', 'weak_api@VERS_1'})
        self.assertEqual(V.soname(' 0x000 (SONAME) Library soname: [libmount.so.1]'), 'libmount.so.1')
        with self.assertRaises(RuntimeError): V.exports('no defined exports')
        with self.assertRaises(RuntimeError): V.soname('missing')

    def test_library_resolution_rejects_outside_symlink(self):
        folder = self.root / 'usr/lib/x86_64-linux-gnu'; folder.mkdir(parents=True)
        outside = self.root / 'outside'; outside.write_bytes(b'public')
        (folder / 'libmount.so.1').symlink_to(outside)
        with self.assertRaisesRegex(RuntimeError, 'library_escape'):
            V.library(self.root, 'mount')

    def test_old_callers_use_all_six_old_headers_and_retain_binary_hash_checks(self):
        self.assertEqual(set(V.CALLERS), {'blkid', 'mount', 'uuid', 'smartcols', 'fdisk', 'lastlog2'})
        source = (RECIPE / 'verify.py').read_text()
        self.assertIn("'-I' + str(old / 'usr/include')", source)
        self.assertIn('LD_LIBRARY_PATH', source)
        self.assertIn("'old_caller_changed'", source)
        self.assertIn('dlsym(RTLD_DEFAULT', source)
        self.assertIn("'pam_configuration_changed'", source)

    def test_c_function_extraction_preserves_actual_body_and_balanced_comments_strings(self):
        text = 'int selected(int x)\n{ const char *s="}"; /* { } */ if(x){x++;}\n return x;\n}\nint next(void){return 0;}\n'
        actual = S.function(text, 'selected')
        self.assertIn('return x;', actual)
        self.assertNotIn('int next', actual)
        for invalid in (text + text, 'int selected(void){', 'int unrelated(void){return 0;}'):
            with self.assertRaises(RuntimeError): S.function(invalid, 'selected')

    def test_main_join_extraction_keeps_real_closes_and_checks_before_target_credential_drop(self):
        old = 'if(cgroup_procs_fd>=0)\n join_into_cgroup();'
        new = 'if(cgroup_procs_fd>=0){\n join_into_cgroup();\n close(cgroup_procs_fd);\n cgroup_procs_fd=-1;\n}'
        suffix = '\nif(uid_gid_fd>=0){}\nsetgid(gid);\nsetuid(uid);\n}'
        for block in (old, new):
            actual = S.main_join_block('int main(void){\n' + block + suffix)
            self.assertEqual(actual.strip(), block)
        self.assertNotIn('close(', S.main_join_block('int main(void){\n' + old + suffix))
        with self.assertRaisesRegex(RuntimeError, 'main_credential_drop_order'):
            S.main_join_block('int main(void){\nsetgid(gid);\n' + new + '\nif(uid_gid_fd>=0){}\nsetuid(uid);\n}')

    def test_disabled_variant_runs_normal_distclean_after_primary_evidence(self):
        source = (RECIPE / 'build.py').read_text()
        abi = source.index("recipe / 'verify.py'")
        primary = source.index("artifacts / 'primary-config.h'")
        clean = source.index("['/usr/bin/make', 'distclean']")
        configure = source.index("'--disable-libmount-mountfd-support'")
        self.assertLess(abi, primary); self.assertLess(primary, clean); self.assertLess(clean, configure)
        self.assertIn("'primary_distclean_incomplete'", source)

    def test_disabled_mountfd_variant_builds_required_libblkid_and_libmount(self):
        source=ast.parse((RECIPE/'build.py').read_text())
        commands=[n.args[0] for n in ast.walk(source) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)
                  and n.func.id=='command' and n.args and isinstance(n.args[0],ast.List)]
        variant=next(n for n in commands if any(isinstance(v,ast.Constant) and v.value=='--disable-libmount-mountfd-support' for v in n.elts))
        options={v.value for v in variant.elts if isinstance(v,ast.Constant)}
        self.assertLessEqual({'--disable-all-programs','--enable-libblkid','--enable-libmount'},options)
        good='#define HAVE_LIBBLKID 1\n#define HAVE_LIBMOUNT 1\n/* #undef USE_LIBMOUNT_MOUNTFD_SUPPORT */\n'
        B.disabled_configuration(good)
        for invalid in (good.replace('#define HAVE_LIBBLKID 1\n',''),good.replace('#define HAVE_LIBMOUNT 1\n',''),
                        good+'#define USE_LIBMOUNT_MOUNTFD_SUPPORT 1\n',good+'#define USE_LIBMOUNT_MOUNTFD_SUPPORT 0\n'):
            with self.assertRaises(RuntimeError):B.disabled_configuration(invalid)

    def install_capsule_fixture(self):
        tree=next(ast.parse(block) for block in blocks() if 'def install_capsule(' in block)
        node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='install_capsule')
        namespace={'os':os,'pathlib':__import__('pathlib'),'stat':__import__('stat'),'json':__import__('json'),'sha256':sha256}
        exec(compile(ast.Module(body=[node],type_ignores=[]),'<actual-public-install-capsule>','exec'),namespace)
        root=self.root/'ci';root.mkdir(mode=0o700);source=root/'output/artifacts';source.mkdir(parents=True,mode=0o700)
        records=[]
        for name in sorted(B.SELECTED):
            raw=('public-package-'+name).encode();path=source/(name+'.deb');path.write_bytes(raw);path.chmod(0o644)
            records.append({'name':name,'path':path.name,'bytes':len(raw),'sha256':sha256(raw).hexdigest()})
        receipt={'selected_runtime_packages':sorted(B.SELECTED),'all_family_packages':records}
        (source/'build-receipt.json').write_text(__import__('json').dumps(receipt));(source/'build-receipt.json').chmod(0o644)
        (source/'not-an-install-input.log').write_text('excluded public build detail')
        return namespace['install_capsule'],root,source,receipt

    def test_public_install_capsule_preserves_private_tree_and_exposes_exact_ten_inputs(self):
        create,root,source,receipt=self.install_capsule_fixture();target=create(root,receipt)
        self.assertEqual(target.stat().st_mode&0o777,0o755)
        self.assertEqual(source.stat().st_mode&0o777,0o700);self.assertEqual(root.stat().st_mode&0o777,0o700)
        self.assertEqual({p.name for p in target.iterdir()},{'build-receipt.json'}|{r['path'] for r in receipt['all_family_packages']})
        for path in target.iterdir():
            self.assertEqual(path.stat().st_mode&0o777,0o644);self.assertEqual(path.read_bytes(),(source/path.name).read_bytes())

    def test_public_install_capsule_refuses_changed_package_before_any_directory_creation(self):
        create,root,source,receipt=self.install_capsule_fixture()
        (source/receipt['all_family_packages'][0]['path']).write_bytes(b'wrong-public-bytes')
        with self.assertRaises(AssertionError):create(root,receipt)
        self.assertFalse((root/'install-public').exists())

    def reuse_packages_fixture(self):
        block=next(value for value in blocks() if 'def reuse_packages(' in value)
        node=next(n for n in ast.parse(block).body if isinstance(n,ast.FunctionDef) and n.name=='reuse_packages')
        namespace={'os':os,'pathlib':__import__('pathlib'),'stat':__import__('stat'),'json':json,'sha256':sha256,'re':re}
        exec(compile(ast.Module(body=[node],type_ignores=[]),'<actual-public-package-reuse>','exec'),namespace)
        root=self.root/('reuse-'+str(len(list(self.root.iterdir()))));root.mkdir(mode=0o700)
        for name in ('context','output','reuse-download'):(root/name).mkdir(mode=0o700)
        source=root/'reuse-download';recipe={}
        for name in ('Dockerfile','build.py','verify.py','source_regressions.py'):
            raw=('sealed-public-recipe-'+name).encode();(root/'context'/name).write_bytes(raw)
            recipe[name]=sha256(raw).hexdigest()
        old_commit='e9cff872fe9d07718f50ef84eb8fb9362a085694'
        (root/'recipe-identity.json').write_text(json.dumps({'source_commit':'f'*40,'recipe_sha256':recipe}))
        (source/'recipe-identity.json').write_text(json.dumps({'source_commit':old_commit,'recipe_sha256':recipe}))
        packages=[]
        for name in sorted(B.SELECTED):
            raw=('retained-public-package-'+name).encode()
            path=name+'_'+B.expected_version(name).split(':')[-1]+'_amd64.deb'
            (source/path).write_bytes(raw)
            packages.append({'name':name,'version':B.expected_version(name),'path':path,
                             'bytes':len(raw),'sha256':sha256(raw).hexdigest()})
        records=[]
        for suffix in ('.changes','.buildinfo'):
            name='util-linux_2.41.6-0+deb13u0+gold1_amd64'+suffix;raw=('public-record-'+suffix).encode()
            (source/name).write_bytes(raw);records.append({'path':name,'sha256':sha256(raw).hexdigest()})
        abi={'status':'passed','libraries':{name:{'status':'passed'} for name in V.CALLERS}}
        regressions={'compiled_contracts':'passed','mountfd_enabled_full_build':'passed',
                     'mountfd_disabled_build_and_loader':'passed','security_accepted':False,'production_installed':False}
        (source/'abi-tests.json').write_text(json.dumps(abi));(source/'source-tests.json').write_text(json.dumps(regressions))
        (source/'toolchain-packages.txt').write_bytes(b'public-toolchain=1\n')
        (source/'primary-config.h').write_bytes(b'#define USE_LIBMOUNT_MOUNTFD_SUPPORT 1\n')
        upstream='Public build output\nAll 340 tests PASSED\n'+'suite: SKIPPED\n'*162
        (source/'upstream-build.log').write_text(upstream)
        receipt={'schema':'trixie_util_linux_source_candidate/1','status':'candidate_built_not_accepted',
                 'builder_image_id':'sha256:'+'a'*64,'version':B.VERSION,'selected_runtime_packages':sorted(B.SELECTED),
                 'all_family_packages':packages,'build_records':records,'recipe_sha256':recipe,'abi':abi,
                 'source_regressions':regressions,'security_accepted':False,'raw_scanner_gate_waived':False,
                 'production_installed':False,'services_changed':False,
                 'toolchain_inventory_sha256':sha256((source/'toolchain-packages.txt').read_bytes()).hexdigest(),
                 'primary_configuration_sha256':sha256((source/'primary-config.h').read_bytes()).hexdigest(),
                 'upstream_suite':{'status':'passed','reported_suite_script_counts':[340],'skip_status_lines':162,
                                   'skips_are_not_privileged_coverage':True}}
        self.write_reuse_receipt(source,receipt)
        return namespace['reuse_packages'],root,source,receipt

    def write_reuse_receipt(self, source, receipt):
        raw=(json.dumps(receipt,sort_keys=True)+'\n').encode();(source/'build-receipt.json').write_bytes(raw)
        return sha256(raw).hexdigest()

    def test_verified_reuse_copies_only_seventeen_artifacts_and_records_original_build(self):
        reuse,root,source,receipt=self.reuse_packages_fixture()
        expected=self.write_reuse_receipt(source,receipt);before={p.name:p.read_bytes() for p in source.iterdir()}
        observed,provenance=reuse(root,expected)
        self.assertEqual(observed,receipt)
        self.assertEqual(len(before),18)
        self.assertEqual({p.name:p.read_bytes() for p in (root/'output/artifacts').iterdir()},
                         {name:raw for name,raw in before.items() if name!='recipe-identity.json'})
        self.assertEqual({p.name:p.read_bytes() for p in source.iterdir()},before)
        self.assertFalse(provenance['compiled_in_this_run'])
        self.assertEqual(provenance['reused_from_run'],40108449213)
        self.assertEqual(provenance['reused_from_artifact'],12565781476)
        self.assertEqual(provenance['reused_receipt_sha256'],expected)
        self.assertEqual(provenance['original_source_commit'],'e9cff872fe9d07718f50ef84eb8fb9362a085694')

    def test_reuse_refuses_changed_missing_or_extra_download_before_copying(self):
        for failure in ('receipt','package','record','toolchain','config','abi','source','suite','missing','extra','recipe','context'):
            with self.subTest(failure=failure):
                reuse,root,source,receipt=self.reuse_packages_fixture();expected=self.write_reuse_receipt(source,receipt)
                name={'receipt':'build-receipt.json','package':receipt['all_family_packages'][0]['path'],
                      'record':receipt['build_records'][0]['path'],'toolchain':'toolchain-packages.txt',
                      'config':'primary-config.h','abi':'abi-tests.json','source':'source-tests.json',
                      'suite':'upstream-build.log'}.get(failure)
                if name:(source/name).write_bytes(b'changed-public-evidence')
                elif failure=='missing':(source/'abi-tests.json').unlink()
                elif failure=='extra':(source/'unexpected-public-file').write_bytes(b'extra')
                elif failure=='recipe':
                    value=json.loads((source/'recipe-identity.json').read_bytes());value['recipe_sha256']['build.py']='0'*64
                    (source/'recipe-identity.json').write_text(json.dumps(value))
                else:(root/'context/build.py').write_bytes(b'drifted-public-recipe')
                with self.assertRaises((AssertionError,ValueError,FileNotFoundError)):reuse(root,expected)
                self.assertFalse((root/'output/artifacts').exists())

    def test_reuse_receipt_pin_does_not_replace_semantic_admission(self):
        for field,value in (('schema','other/1'),('status','security_accepted'),('security_accepted',True),
                            ('raw_scanner_gate_waived',True),('production_installed',True),('services_changed',True)):
            with self.subTest(field=field):
                reuse,root,source,receipt=self.reuse_packages_fixture();receipt[field]=value
                expected=self.write_reuse_receipt(source,receipt)
                with self.assertRaises(AssertionError):reuse(root,expected)
                self.assertFalse((root/'output/artifacts').exists())

    def test_reuse_rejects_download_links_and_oversize_receipt_before_read(self):
        for failure in ('symlink','hardlink','oversize'):
            with self.subTest(failure=failure):
                reuse,root,source,receipt=self.reuse_packages_fixture();expected=self.write_reuse_receipt(source,receipt)
                target=source/'build-receipt.json'
                if failure=='symlink':
                    target.rename(root/'outside-receipt');target.symlink_to(root/'outside-receipt')
                elif failure=='hardlink':os.link(target,root/'receipt-alias')
                else:
                    with target.open('r+b') as stream:stream.truncate(1024**2+1)
                target_info=target.stat();original_read=os.read
                def guarded_read(fd,maximum):
                    info=os.fstat(fd)
                    if (info.st_dev,info.st_ino)==(target_info.st_dev,target_info.st_ino):
                        raise AssertionError('body_read_before_metadata_gate')
                    return original_read(fd,maximum)
                with patch.object(os,'read',side_effect=guarded_read):
                    with self.assertRaises((AssertionError,OSError)) as error:reuse(root,expected)
                self.assertNotEqual(str(error.exception),'body_read_before_metadata_gate')
                self.assertFalse((root/'output/artifacts').exists())

    def diagnostic_functions(self):
        block=next(value for value in blocks() if 'def public_log_tail(' in value)
        nodes=[node for node in ast.parse(block).body if isinstance(node,ast.FunctionDef)]
        namespace={'os':os,'stat':__import__('stat'),'json':json,'re':re}
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'<actual-public-diagnostics>','exec'),namespace)
        return namespace['public_log_tail'],namespace['failure_record']

    def test_small_install_log_retains_the_explanatory_continuation_in_full(self):
        tail,record=self.diagnostic_functions();path=self.root/'candidate-install.log'
        explanation=b' unable to install public file: operation not permitted\n'
        beginning=b'dpkg: error processing archive public-package.deb (--install):\n'+explanation
        ending=b'\nTraceback: disposable candidate install failed\n'
        raw=beginning+b'x'*(3343-len(beginning)-len(ending))+ending;path.write_bytes(raw)
        size,window=tail(path);value=json.loads(record(path.name,size,window))
        self.assertEqual(size,3343);self.assertEqual(window,raw)
        self.assertEqual(value['tail'].encode(),raw)
        self.assertIn(explanation.decode(),value['tail'])
        self.assertEqual(path.read_bytes(),raw)

    def test_large_and_non_ascii_public_diagnostics_keep_byte_and_encoded_bounds(self):
        tail,record=self.diagnostic_functions();path=self.root/'candidate-install.log'
        raw=(b'error '+b'\xff'*600+b'\n')*150;path.write_bytes(raw)
        size,window=tail(path);encoded=record(path.name,size,window);value=json.loads(encoded)
        self.assertEqual(size,len(raw));self.assertEqual(window,raw[-65536:])
        self.assertEqual(value['tail'],window[-4096:].decode(errors='replace'))
        self.assertEqual(len(value['failure_lines']),4)
        self.assertTrue(all(len(line)<=300 for line in value['failure_lines']))
        self.assertLessEqual(len(encoded.encode()),32768)
        self.assertLessEqual(sum(len(record('public-'+str(i),size,window).encode()) for i in range(16)),524288)

    def test_public_log_tail_rejects_links_writable_or_oversize_before_body_read(self):
        tail,_=self.diagnostic_functions()
        for failure in ('symlink','hardlink','writable','oversize'):
            with self.subTest(failure=failure):
                path=self.root/('public-'+failure);path.write_bytes(b'public-log')
                if failure=='symlink':
                    path.rename(self.root/'public-link-target');path.symlink_to(self.root/'public-link-target')
                elif failure=='hardlink':os.link(path,self.root/'public-hardlink-alias')
                elif failure=='writable':path.chmod(0o666)
                else:
                    with path.open('r+b') as stream:stream.truncate(128*1024**2+1)
                with patch.object(os,'read',side_effect=AssertionError('log_body_read_before_metadata_gate')):
                    with self.assertRaises((AssertionError,OSError)) as error:tail(path)
                self.assertNotEqual(str(error.exception),'log_body_read_before_metadata_gate')

    def test_diagnostic_artifact_preserves_only_fixed_install_log_even_without_failure(self):
        block=next(value for value in blocks() if 'def public_log_tail(' in value)
        root=self.root/'diagnostic-ci';root.mkdir(mode=0o700)
        raw=b'dpkg: error processing archive public.deb\n explanatory continuation\n'
        (root/'candidate-install.log').write_bytes(raw)
        (root/'unrelated-public.log').write_bytes(b'not an admitted diagnostic input')
        output=root/'github-output';output.write_text('')
        printed=io.StringIO()
        with patch.dict(os.environ,{'CANDIDATE_ROOT':str(root),'GITHUB_OUTPUT':str(output),'PRINT_FAILURE_TAILS':'false'}), \
             patch('sys.stdout',printed):
            exec(compile(block,'<actual-public-diagnostic-step>','exec'),{})
        self.assertEqual(printed.getvalue(),'')
        target=root/'diagnostics'
        self.assertEqual({p.name for p in target.iterdir()},{'candidate-install.log','install-log-metadata.json'})
        self.assertEqual((target/'candidate-install.log').read_bytes(),raw)
        self.assertEqual(json.loads((target/'install-log-metadata.json').read_bytes()),
                         {'schema':'trixie_util_linux_install_log/1','name':'candidate-install.log','bytes':len(raw),
                          'retained_bytes':len(raw),'truncated':False,'public_inputs_only':True})
        self.assertIn('install_log=true\n',output.read_text())

    def nested_installer_fixture(self, *, simulation=None, audit=b'', versions=None):
        tree = next(ast.parse(block) for block in blocks() if 'def install_capsule(' in block)
        program = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id == 'install' for target in node.targets))
        folder = Path(self.stack.enter_context(tempfile.TemporaryDirectory(dir=self.root)))
        packages = folder / 'packages'; packages.mkdir()
        rows = [{'name': name, 'path': name + '.deb', 'version': B.expected_version(name)}
                for name in sorted(B.SELECTED)]
        (packages / 'build-receipt.json').write_text(json.dumps({
            'selected_runtime_packages': sorted(B.SELECTED), 'all_family_packages': rows}))
        paths = [str(packages / row['path']) for row in rows]
        if simulation is None:
            simulation = ''.join('Inst ' + row['name'] + ':amd64 (' + row['version'] + ')\n' for row in rows)
        installed = versions if versions is not None else {row['name']: row['version'] for row in rows}
        def run(arguments, **options):
            if arguments[:5] == ['/usr/bin/apt-get', '--simulate', '--no-remove', '--no-download', 'install']:
                options['stdout'].write(simulation.encode())
                return types.SimpleNamespace(returncode=0)
            if arguments[:4] == ['/usr/bin/apt-get', '--yes', '--no-remove', 'install']:
                return types.SimpleNamespace(returncode=0)
            if arguments == ['/usr/bin/dpkg', '--audit']:
                return types.SimpleNamespace(returncode=0, stdout=audit)
            if arguments[:3] == ['/usr/bin/dpkg-query', '-W', '-f=${Package}=${Version}\n']:
                return types.SimpleNamespace(returncode=0, stdout=''.join(
                    name + '=' + version + '\n' for name, version in installed.items()).encode())
            raise AssertionError('unexpected_installer_command')
        command = Mock(side_effect=run)
        modules = {'pathlib': types.SimpleNamespace(Path=lambda value: packages if value == '/packages' else Path(value)),
                   'subprocess': types.SimpleNamespace(run=command), 'json': json, 'os': os}
        def import_module(name, *args, **kwargs):
            if name not in modules:
                raise AssertionError('unexpected_installer_import')
            return modules[name]
        def simulation_file(path, mode):
            self.assertEqual(path, '/tmp/simulation')
            return builtins.open(folder / 'simulation', mode)
        def execute():
            exec(compile(program, '<actual-disposable-installer>', 'exec'), {
                '__builtins__': {**vars(builtins), '__import__': import_module}, 'open': simulation_file})
        return execute, command, paths, rows

    def test_actual_installer_uses_offline_apt_after_exact_nine_simulation_then_audits_versions(self):
        execute, command, paths, rows = self.nested_installer_fixture()
        execute()
        self.assertEqual([call.args[0] for call in command.call_args_list], [
            ['/usr/bin/apt-get', '--simulate', '--no-remove', '--no-download', 'install', *paths],
            ['/usr/bin/apt-get', '--yes', '--no-remove', 'install', *paths],
            ['/usr/bin/dpkg', '--audit'],
            ['/usr/bin/dpkg-query', '-W', '-f=${Package}=${Version}\n', *sorted(B.SELECTED)]])
        self.assertTrue(all(call.kwargs['check'] for call in command.call_args_list))
        self.assertEqual(command.call_args_list[1].kwargs['timeout'], 120)
        self.assertEqual(len(paths), 9)

    def test_actual_installer_container_has_no_network_and_only_existing_metadata_capabilities(self):
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

    def test_actual_installer_refuses_removal_unrelated_or_incomplete_simulation_before_install(self):
        names = sorted(B.SELECTED)
        normal = ['Inst ' + name + ':amd64 (candidate)\n' for name in names]
        for simulation in (''.join(normal) + 'Remv unrelated [1]\n',
                           ''.join(normal) + 'Inst unrelated (1)\n',
                           ''.join(normal[:-1]) + 'Inst unrelated (1)\n', ''.join(normal[:-1])):
            with self.subTest(simulation=simulation):
                execute, command, _, _ = self.nested_installer_fixture(simulation=simulation)
                with self.assertRaises(AssertionError):
                    execute()
                self.assertEqual(command.call_count, 1)

    def test_actual_installer_refuses_dirty_audit_or_wrong_installed_version(self):
        execute, command, _, _ = self.nested_installer_fixture(audit=b'libblkid1 is not configured\n')
        with self.assertRaises(AssertionError):
            execute()
        self.assertEqual(command.call_count, 3)
        versions = {name: B.expected_version(name) for name in B.SELECTED}
        versions['libblkid1'] = '2.41.5-0+deb13u1'
        execute, command, _, _ = self.nested_installer_fixture(versions=versions)
        with self.assertRaises(AssertionError):
            execute()
        self.assertEqual(command.call_count, 4)

    def test_negative_control_that_unexpectedly_passes_is_a_failure(self):
        with patch.object(S.B, 'command'), patch.object(S.subprocess, 'run', return_value=types.SimpleNamespace(returncode=0)):
            with self.assertRaisesRegex(RuntimeError, 'compiled_negative_control_failed'):
                S.compile_run(self.root, 'old-probe', 'int main(void){return 0;}', [], False)

    def test_kernel_coverage_is_explicitly_not_tested_and_never_accepted(self):
        source = (RECIPE / 'source_regressions.py').read_text()
        for name in ('privileged_failed_helper_ownership', 'privileged_cgroup_namespace_credential_drop',
                     'restricted_symlink_procfs_mount_races'):
            self.assertIn("'" + name + "': 'NOT_TESTED'", source)
        self.assertIn("'security_accepted': False", source)
        self.assertIn("'CVE-2026-78409_vendor_binary_reconciliation': 'OPEN'", source)

    def test_every_workflow_python_program_and_nested_installer_parses(self):
        values = blocks()
        self.assertEqual(len(values), 6)
        for value in values:
            tree = ast.parse(value)
            for node in tree.body:
                if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'install' for t in node.targets):
                    ast.parse(ast.literal_eval(node.value))

    def actionlint_result(self, *, returncode=0, errors=None):
        return types.SimpleNamespace(returncode=returncode, stdout=json.dumps(errors or []), stderr='')

    def actionlint_context_error(self):
        line = next(index for index, value in enumerate(WORKFLOW.read_text().splitlines(), 1)
                    if 'PRINT_FAILURE_TAILS:' in value)
        return {'kind': 'expression', 'line': line,
                'message': 'calling function "failure" is not allowed here'}

    def test_explicit_actionlint_preflight_checks_real_workflow_and_old_invalid_environment(self):
        with patch.object(subprocess, 'run', side_effect=[self.actionlint_result(),
                self.actionlint_result(returncode=1, errors=[self.actionlint_context_error()])]) as run:
            result = validate_actionlint_step_environment('/explicit/local/actionlint')
        self.assertEqual(result, {'status': 'passed', 'corrected_workflow_valid': True,
                                 'old_failure_function_environment_rejected': True})
        self.assertEqual(run.call_count, 2)
        valid, invalid = run.call_args_list
        self.assertEqual(valid.kwargs['input'], WORKFLOW.read_text())
        self.assertNotEqual(valid.kwargs['input'], invalid.kwargs['input'])
        self.assertEqual(valid.args[0][0], '/explicit/local/actionlint')
        self.assertIn('-shellcheck=', valid.args[0]); self.assertIn('-pyflakes=', valid.args[0])
        self.assertEqual(valid.args[0][-1], '-')

    def test_actionlint_preflight_cannot_pass_missing_tool_or_false_negative_control(self):
        with patch.object(subprocess, 'run', side_effect=FileNotFoundError('actionlint')):
            with self.assertRaises(FileNotFoundError):
                validate_actionlint_step_environment('/missing/actionlint')
        for rejected in (self.actionlint_result(), self.actionlint_result(returncode=2),
                         self.actionlint_result(returncode=1, errors=[{'kind': 'syntax-check',
                             'line': 1, 'message': 'unrelated YAML failure'}])):
            with self.subTest(rejected=rejected.stdout), patch.object(subprocess, 'run',
                    side_effect=[self.actionlint_result(), rejected]):
                with self.assertRaisesRegex(AssertionError, 'not_semantically_rejected'):
                    validate_actionlint_step_environment('/explicit/local/actionlint')
        with patch.object(subprocess, 'run', return_value=self.actionlint_result(returncode=1)):
            with self.assertRaisesRegex(AssertionError, 'corrected_workflow_actionlint_failed'):
                validate_actionlint_step_environment('/explicit/local/actionlint')

    def test_workflow_compilation_is_offline_nonroot_and_not_privileged(self):
        text = WORKFLOW.read_text()
        self.assertIn('workflow_dispatch:', text)
        self.assertNotIn('pull_request:', text)
        self.assertNotIn('secrets.', text)
        self.assertNotIn('--privileged', text)
        self.assertNotIn('SYS_ADMIN', text.replace('SYS_ADMIN, networking', ''))
        self.assertIn("'--user=1001:1001','--network=none','--read-only'", text)
        self.assertIn("'--cap-drop=ALL'", text)
        self.assertIn("'--cpus=2','--memory=4g'", text)
        self.assertIn("'--pids-limit=512'", text)
        self.assertIn('timeout-minutes: 150', text)
        self.assertIn("'--no-optional-locks','cat-file','-s'", text)

    def test_raw_scans_keep_all_severities_unfixed_rows_and_separate_package_export(self):
        text = WORKFLOW.read_text()
        self.assertIn('severity: UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL', text)
        self.assertIn("ignore-unfixed: 'false'", text)
        self.assertIn('TRIVY_IGNOREFILE: /dev/null', text)
        self.assertIn('32*1024**2', text)
        self.assertIn('8*1024**2', text)
        self.assertIn("steps.export.outcome == 'success'", text)
        self.assertIn("steps.reports.outcome == 'success'", text)
        self.assertEqual(text.count('retention-days: 7'), 4)

    def subdir_workflow_functions(self):
        tree=ast.parse(next(value for value in blocks() if 'def seal_contract(' in value))
        nodes=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name in
               {'seal_contract','fetch_input','read_result','export_contract','admit_contract'}]
        pin=next(ast.literal_eval(node.value) for node in tree.body if isinstance(node,ast.Assign)
                 and any(isinstance(target,ast.Name) and target.id=='pin' for target in node.targets))
        namespace={'pin':pin,'os':os,'pathlib':__import__('pathlib'),'sha256':sha256,'stat':__import__('stat'),
                   'subprocess':subprocess,'re':re,'time':__import__('time'),'json':json,'urllib':__import__('urllib.request')}
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'<actual-subdir-workflow-functions>','exec'),namespace)
        return namespace

    def test_opt_in_contract_requires_reuse_before_any_recipe_or_install_work(self):
        import yaml
        workflow=yaml.safe_load(WORKFLOW.read_text());inputs=workflow.get('on',workflow.get(True))['workflow_dispatch']['inputs']
        self.assertEqual(inputs['verify_restricted_subdir']['type'],'boolean');self.assertIs(inputs['verify_restricted_subdir']['default'],False)
        steps=workflow['jobs']['candidate']['steps'];guard=next(step for step in steps if step.get('run')=='exit 1')
        self.assertEqual(guard['if'],'inputs.verify_restricted_subdir && !inputs.reuse_verified_packages')
        self.assertLess(steps.index(guard),next(index for index,step in enumerate(steps) if step.get('id')=='recipe'))
        contract=next(step for step in steps if step.get('id')=='restricted_subdir')
        self.assertEqual(contract['if'],"inputs.verify_restricted_subdir && inputs.reuse_verified_packages && steps.build.outcome == 'success'")
        self.assertEqual(contract['timeout-minutes'],5)
        self.assertGreater(steps.index(contract),next(index for index,step in enumerate(steps) if step.get('id')=='build'))
        upload=next(step for step in steps if 'restricted-subdir-contract-${{' in step.get('with',{}).get('name',''))
        self.assertIn("steps.restricted_subdir.outputs.artifact_root != ''",upload['if'])
        self.assertIn('always()',upload['if']);self.assertNotIn("steps.restricted_subdir.outcome == 'success'",upload['if'])

    def test_contract_wiring_preserves_the_exact_four_vendor_recipe_bytes(self):
        expected={'Dockerfile':'a368ebab2583fc98f7ddefc44ef47ea21d5e55f3ec0b8c45834db6da7d476090',
            'build.py':'300126114d0a4641acbc861176ffa14db3ffc296949ba5197f6396e345bf6246',
            'verify.py':'247e28bbef656d102a2afc8fe5f4618b34a4ee504a950b8828568ae717361a2b',
            'source_regressions.py':'19dd9dee3ef474e64f202c5756ba08c50b21c159a4f1551a6f6c72902a015f1c'}
        self.assertEqual({name:sha256((RECIPE/name).read_bytes()).hexdigest() for name in expected},expected)
        block=next(value for value in blocks() if 'def seal_contract(' in value)
        self.assertIn("work/'tools/restricted_subdir_contract.py'",block)
        self.assertNotIn("root/'context'",block);self.assertNotIn("root/'output/artifacts'",block.split('subprocess.run([sys.executable',1)[0])

    def test_seal_uses_published_git_blob_and_same_current_tree_fd_before_downloads(self):
        namespace=self.subdir_workflow_functions();raw=(RECIPE/'restricted_subdir_contract.py').read_bytes()
        tree=self.root/'tree';path=tree/'deploy/maintenance/util_linux_build/restricted_subdir_contract.py'
        path.parent.mkdir(parents=True);path.write_bytes(raw)
        replies=[types.SimpleNamespace(stdout=(str(len(raw))+'\n').encode()),types.SimpleNamespace(stdout=raw)]
        with patch.object(subprocess,'run',side_effect=replies) as git:
            self.assertEqual(namespace['seal_contract']('f'*40,tree),raw)
        self.assertEqual(git.call_args.args[0],['git','--no-optional-locks','cat-file','blob','f'*40+':deploy/maintenance/util_linux_build/restricted_subdir_contract.py'])
        path.write_bytes(raw+b'\n# ordinary source drift\n')
        with patch.object(subprocess,'run',side_effect=replies),self.assertRaises(AssertionError):namespace['seal_contract']('f'*40,tree)
        self.assertFalse((self.root/'inputs').exists())

    def test_fetch_input_enforces_size_plus_one_sha_deadline_and_https_redirect(self):
        namespace=self.subdir_workflow_functions();urllib=namespace['urllib'];raw=b'ordinary public source input';url='https://www.kernel.org/ordinary'
        class Response(io.BytesIO):
            def geturl(self):return url
        for content,pin,should_pass in ((raw,sha256(raw).hexdigest(),True),(raw+b'X',sha256(raw).hexdigest(),False),(raw,'0'*64,False)):
            response=Response(content);opener=Mock();opener.open.return_value=response
            with self.subTest(content=content,pin=pin),patch.object(urllib.request,'build_opener',return_value=opener),patch.object(namespace['time'],'monotonic',return_value=10):
                if should_pass:self.assertEqual(namespace['fetch_input'](url,len(raw),pin,20),raw)
                else:
                    with self.assertRaises(AssertionError):namespace['fetch_input'](url,len(raw),pin,20)
        with patch.object(urllib.request,'build_opener') as opener,patch.object(namespace['time'],'monotonic',return_value=20):
            with self.assertRaises(AssertionError):namespace['fetch_input'](url,len(raw),sha256(raw).hexdigest(),20)
        opener.assert_not_called()
        response=Response(raw);response.geturl=lambda:'http://www.kernel.org/ordinary';response.read=Mock(wraps=response.read)
        opener=Mock();opener.open.return_value=response
        with patch.object(urllib.request,'build_opener',return_value=opener),patch.object(namespace['time'],'monotonic',return_value=10):
            with self.assertRaises(AssertionError):namespace['fetch_input'](url,len(raw),sha256(raw).hexdigest(),20)
        response.read.assert_not_called()

    def contract_proof(self):
        value={'schema':'util_linux_restricted_subdir_contract/1','status':'source_contract_passed_not_accepted',
            'source_commit':'f'*40,'verifier_sha256':'61a9d508b092fcfd6174c12d0ea398b5a2cf7f03f0aed137b9b195c933081bde',
            'historical_build_receipt_sha256':'3a799ad92549c9d3c5e18013463743d9bc2b721eb83557e5ee3a0273d4514dde',
            'libmount_package':{'sha256':'111baa6313bd1e72002d99725bf2f82e07d8dafd282269e005d5600aa616aaca'},
            'restricted_refusal_before_stub_side_effects':True,'compiled_contracts':{}}
        for key in ('maintainer_signatures_authenticated','privileged_or_kernel_mount_operations_executed','historical_receipts_modified',
                'vendor_database_reconciliation_completed','scanner_findings_modified','production_installed','security_accepted'):value[key]=False
        for mode in ('enabled','disabled'):
            for variant in ('original','missing-guard'):
                value['compiled_contracts']['mountfd-'+mode+'-'+variant]={'compile':{'exit_code':0},'run':{'exit_code':10 if variant=='missing-guard' else 0}}
        return value

    def test_contract_admission_requires_real_compile_and_exact_negative_exit_and_lineage(self):
        from copy import deepcopy
        admit=self.subdir_workflow_functions()['admit_contract'];value=self.contract_proof();admit(json.dumps(value).encode(),'f'*40)
        for change in (lambda v:v.update(security_accepted=True),lambda v:v.update(source_commit='e'*40),
                lambda v:v.update(historical_build_receipt_sha256='0'*64),lambda v:v['libmount_package'].update(sha256='0'*64),
                lambda v:v['compiled_contracts']['mountfd-enabled-original']['compile'].update(exit_code=False),
                lambda v:v['compiled_contracts']['mountfd-enabled-missing-guard']['run'].update(exit_code=0)):
            wrong=deepcopy(value);change(wrong)
            with self.assertRaises(AssertionError):admit(json.dumps(wrong).encode(),'f'*40)

    def test_contract_export_retains_only_bounded_source_json_logs_and_no_package_binary(self):
        export=self.subdir_workflow_functions()['export_contract'];result=self.root/'result';result.mkdir();public=self.root/'public';public.mkdir()
        expected={'mountfd-enabled-original.c':b'ordinary source','mountfd-enabled-original-compile.log':b'',
                  'proof.json':json.dumps(self.contract_proof()).encode()}
        for name,raw in expected.items():(result/name).write_bytes(raw)
        (result/'mountfd-enabled-original').write_bytes(b'inert binary output not uploaded')
        self.assertEqual(export(result,public),sum(map(len,expected.values())))
        self.assertEqual({p.name:p.read_bytes() for p in public.iterdir()},expected)
        (result/'unexpected.log').write_bytes(b'public but outside fixed inventory')
        other=self.root/'other-public';other.mkdir()
        with self.assertRaises(AssertionError):export(result,other)

    def test_reuse_is_explicit_default_off_and_downloads_only_the_fixed_public_artifact(self):
        text=WORKFLOW.read_text()
        inputs=text.split('reuse_verified_packages:',1)[1].split('\npermissions:',1)[0]
        self.assertIn('type: boolean',inputs);self.assertIn('default: false',inputs)
        self.assertIn('actions: read',text)
        start=text.index('uses: actions/download-artifact@v4')
        step=text[text.rfind('      - name:',0,start):text.index('\n      - name:',start)]
        for expected in ('if: inputs.reuse_verified_packages','github-token: ${{ github.token }}',
                         'repository: mohsenbarari/trading_bot','run-id: 40108449213','artifact-ids: 12565781476',
                         'path: ${{ steps.recipe.outputs.root }}/reuse-download'):
            self.assertIn(expected,step)
        self.assertNotIn('continue-on-error',step)
        self.assertIn("steps.diagnostics.outputs.install_log == 'true'",text)
        self.assertIn('name: trixie-util-linux-public-install-diagnostics',text)

    def test_reuse_validation_precedes_docker_and_skips_only_builder_and_compile(self):
        block=next(value for value in blocks() if 'def reuse_packages(' in value);tree=ast.parse(block)
        branches={ast.literal_eval(node.test.comparators[0]):node for node in tree.body
                  if isinstance(node,ast.If) and isinstance(node.test,ast.Compare)
                  and isinstance(node.test.left,ast.Name) and node.test.left.id=='reuse'}
        self.assertEqual(set(branches),{'true','false'})
        self.assertFalse(branches['true'].orelse);self.assertFalse(branches['false'].orelse)
        validate=next(node for node in ast.walk(branches['true']) if isinstance(node,ast.Call)
                      and isinstance(node.func,ast.Name) and node.func.id=='reuse_packages')
        self.assertEqual(ast.literal_eval(validate.args[1]),'3a799ad92549c9d3c5e18013463743d9bc2b721eb83557e5ee3a0273d4514dde')
        def stages(node):
            return {ast.literal_eval(call.args[1]) for call in ast.walk(node)
                    if isinstance(call,ast.Call) and isinstance(call.func,ast.Name) and call.func.id=='command'
                    and len(call.args)>1 and isinstance(call.args[1],ast.Constant)}
        self.assertEqual(stages(branches['false']),{'builder-prepare','compile-tests'})
        self.assertEqual(stages(branches['true']),set())
        top_commands=[node.value for node in tree.body if isinstance(node,ast.Expr) and isinstance(node.value,ast.Call)
                      and isinstance(node.value.func,ast.Name) and node.value.func.id=='command']
        runtime=next(call for call in top_commands if ast.literal_eval(call.args[1])=='runtime-prepare')
        self.assertLess(branches['true'].lineno,runtime.lineno)
        install=next(node for node in tree.body if isinstance(node,ast.Try) and 'candidate-install' in stages(node))
        self.assertLess(branches['false'].lineno,install.lineno)
        self.assertLessEqual({'candidate-create','candidate-install','candidate-exit','candidate-image'},stages(install))
        self.assertIn("'build_provenance':provenance",block)
        self.assertIn("root/'build-provenance.json'",block)

    def test_candidate_entrypoint_cannot_be_used_as_a_host_build(self):
        with self.assertRaisesRegex(RuntimeError, 'immutable_builder_required'):
            B.run('mutable-tag')
        with patch.object(B.os, 'geteuid', return_value=0), self.assertRaisesRegex(RuntimeError, 'nonroot_container_required'):
            B.run('sha256:' + 'a' * 64)

    def test_input_preparation_refuses_host_checkout_without_network(self):
        with patch.object(B.os, 'geteuid', return_value=0), self.assertRaisesRegex(RuntimeError, 'fetch_builder_only'):
            B.fetch_inputs()
        source = (RECIPE / 'build.py').read_text().split('def fetch_inputs():',1)[1].split('def unpack(',1)[0]
        self.assertNotIn("Path('/.dockerenv').is_file()", source)
        self.assertIn("Path('/recipe/build.py')", source)

    def test_report_admission_refuses_oversize_before_body_and_hardlink(self):
        block = next(value for value in blocks() if 'rows=[r for r in receipt' in value and "root/'reports'/name" in value)
        tree = ast.parse(block)
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'read')
        namespace = {'os': os, 'stat': __import__('stat'), 'json': __import__('json')}
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<reviewed-report-read>', 'exec'), namespace)
        path = self.root / 'report'; path.write_bytes(b'{}')
        self.assertEqual(namespace['read'](path, 2), b'{}')
        with patch.object(os, 'read', side_effect=AssertionError('late_bound')):
            with self.assertRaises(AssertionError) as error: namespace['read'](path, 1)
        self.assertNotEqual(str(error.exception), 'late_bound')
        os.link(path, self.root / 'alias')
        with self.assertRaises(AssertionError): namespace['read'](path, 2)

    def test_actual_raw_scanner_identity_and_schema_are_bound_to_the_candidate(self):
        block = next(value for value in blocks() if 'def verify_report_identity' in value)
        tree = ast.parse(block)
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'verify_report_identity')
        namespace = {}
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<reviewed-report-identity>', 'exec'), namespace)
        value = {'SchemaVersion': 2, 'ArtifactType': 'container_image', 'Metadata': {'ImageID': 'sha256:' + 'a'*64}, 'Results': [{'Class': 'os-pkgs'}]}
        namespace['verify_report_identity'](value, 'sha256:' + 'a'*64)
        for bad in ({}, {**value, 'SchemaVersion': 1}, {**value, 'Results': []}, {**value, 'Metadata': {'ImageID': 'sha256:'+'b'*64}}):
            with self.assertRaises(AssertionError): namespace['verify_report_identity'](bad, 'sha256:'+'a'*64)


if __name__ == '__main__':
    if sys.argv[1:2] == ['--actionlint']:
        if len(sys.argv) != 3:
            raise SystemExit('usage: test_trixie_util_linux_build.py --actionlint /path/to/actionlint')
        print(json.dumps(validate_actionlint_step_environment(sys.argv[2]), sort_keys=True))
    else:
        unittest.main()
