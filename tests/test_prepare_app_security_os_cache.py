"""Hermetic public cache fixtures; no network or host package commands run."""
from contextlib import ExitStack,redirect_stderr,redirect_stdout
from copy import deepcopy
from hashlib import sha256
import ast
import base64
import errno
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import resource
import signal
import stat
import subprocess
import sys
import tempfile
from types import ModuleType,SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from zipfile import ZipFile
import zlib

ROOT=Path(__file__).resolve().parents[1]
# Saved before the ambient fences. Runner tests permit only their own isolated
# Python children and process IDs, never package commands or external services.
REAL_POPEN=subprocess.Popen
REAL_FORK=os.fork
REAL_KILL=os.kill
REAL_KILLPG=os.killpg
REAL_SETRLIMIT=resource.setrlimit
REAL_SIGNAL=signal.signal
REAL_PREAD=os.pread
TARGETS=('subprocess.Popen','subprocess.run','subprocess.call','subprocess.check_call','subprocess.check_output',
    'os.system','os.popen','os.kill','os.killpg','socket.socket','socket.create_connection','socket.getaddrinfo',
    'urllib.request.urlopen','signal.signal','signal.alarm','resource.setrlimit')

def fence(stack):
    for target in TARGETS:stack.enter_context(patch(target,side_effect=AssertionError('ambient operation denied')))

with ExitStack() as importing:
    fence(importing)
    spec=importlib.util.spec_from_file_location('_app_security_cache',ROOT/'scripts/prepare_app_security_os_cache.py')
    M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)

# Exact public compiler receipt, not runtime/host data. The unchanged pure
# acl-tar admission hardpins this receipt in addition to each test ZIP's pin.
PUBLIC_ACL_RECEIPT=zlib.decompress(base64.b64decode(
    'eNrNV02PG0cOve+vMHRNrNT3R87Jdfewvi2CBovFmmlE0y20pLFnDe9v31ct2SPZk8ADZIE1jBlNd4lF8j0+kh83VMbNz28+bsbpKAvxcXyUYV6G/TI+jju5kzrw/Ig3d4Jjm7//493w7td/vvv1l82PbzZ4T/w0NBwcaD8OB+HTMh6fBjocBP8rvtJod5B+diwLLaMc1tuId+tvpqmOlY4ynN8/DYd7Mj70u6ypWpsqvhUnbLVxHMmV5HOJNhXnxbGjXIstVmvfdIjB+KaSySrXqruH8w7+024ny5XhzGxEvMrBtVizKVlMLtbWnFMlUzmn5J1VvhTv2dtq2DSjY5KicrL02bB82M/L8TAscqRxWqN1+vLuhXBY2RhdDtpYXZNLnlwtZANi85myzzZTclHnmDP7xNUEHa04V0qVrPqth3mihxUI2EcOt4d5u8Z5ONLx1FO72dOa+E+f8HSalwfaXfBZM/75NV7u7+mwmupv+t/LXE/Af56GcYI9JO0av2dsmWV/vH339e14dqROornffYa8Z6U/nOT98Bn+3UwVDMOffzHul8hp4ftOaLAMaB1lutx26+ofuX/lUqLaPG7wLUZbLTgRSiVNkWtUbExLKcZkOlrJaNbVlKrIm1rZpqg3ny606Gb77z9PwF/AlP+7BKyE7HGW04gELMIy7o/PV0yn3e5ygueH/dhLFvx5lIkmlqsDOxCaHsbd07An/h261B3/18dNeTquH6ON3vQMXCoFFt/Wcnd4elhZTsf726eD2bqteqv/c1zGD6P8cAd89EAPNbhtlbLm50sehCyg8L6xVxK56SKks2bjKTiiopCcEkyo4oKvKSkGQKW2nJpSjk23BjU9oMq6uZeu7lx5jibElNxtNF+F8Sr/UxWVxccWOObqQJ9AIXnNSmmdTAHr8EFAuKCFcMQG9tpbnzSXUP2r/fdOh2v/z7qlX0Dk9s2rokJ2S4qBKyc21bhiKFnvjI+imrJKNSdW6ajA0lZqDDEy61ZLzV4V9fqoNAo0vRiWPL4Ykzy+KqAGlDgYgMCBFUhmozP4gVpLDEhqhfJpkC3pZsg1BKitjqG4llsw5dUB2ahi+DaeF2J5VRylFG1EZ1UafnaMWrQUQoaoSampKmey7SgoVag0rxXkLEFLcg6h6O+K4zcc+iIq81IvevCdJcL3NHUNuS1zCSgMcATOM8WkXAues/JRl5IAhU6EgNCDEgXTQglkTSSq8F5KyefkfqcHq+/j1OZbH5R3EPqiISUW9HZKjIkSMjdbKTRjvXHWIWWuaResgPMuAcYWC1Q5uKvEQErHByjlMPaufbnjZwh2yx4mig6ZJHNkSwpohN5WM6Dh7A1SUHV2Dnpuiiq2MqYi4Bdyd/dZqvs0Mcyn4/50XAG4ptB1dXSnqjQ67Y4DxL1i1lx9vO0+49TtPHeHj9dJ7DwbadqiRW0//Lt/MSBTEOFWrKD7oDGF2BLoFU2NUXImDtorE12qXBuGiJCUb5KytVklL5sf39xecOCVvjlFwCmI2uFjdoWqyVyDh5oUNMLkMnQADAazwY/epRPOpAY6+BJuzG7nZby78llC0Cha50M1KGtSqHXtCKWslGhdY9WlkZTQrERXIqbRnFSJpikdNNk/NL6ls/e+QRgKhqaEnETMuhJKSmINScMo4XUOJnmD4VZriEpUoTAOV1wI0Qzl8xSgt9b/UNvh7q3/NvNeN+FGpBUj8KKpoM4l9XGhWkehZyZoNEFF+Ndq6P2l1mLEJFuievGSs/+iTJCI9spm5WFjRlaVWA3nNIxDJGGbPQbDBuIXSEqrSD4me4xQ9I3prxEA6dEhMlqblohviBQ2xQTORbEDtA13KWm6eh2EsBFIK95FgKBbpLROdf/rvekBq8W6XmAcG+8mDGqLHAY6He9lOo5Mt5P4K4f6hd4PB+xg3fpd38LeE+K4OSE87uW6EH+Z+XdZuu9riVQkAywFx3CgFOQIfGOjuPkuUnhlgYhGH6BKjG2L2QFG8MQZld3ms0Zt90/rgJJKDAARjMUUnFHHFtWGxg52dhKHRAaKLGBpqwbQBVeVsQz0POpfLh1jbE8Xg1Avw0FJ0tGExMpJQvc0WCpcUNHbaLAEBsomEOS9xmwj7PuMoUeXIKuUbw58Lw/UzZ31ex3YO7cuCvZljf3pvI392a4kO2E8GpYT8HuQmyn2t3XHOy0s1whful+HH987l+BwmGh/uJ8xQa/nD8C3yod10N+PUz/4efY/9/ibMf957e65Pw4T7HzxtZfNPO/QGMfOGoR4nG82WZMoRuMzS5bcBNmzVlDujOUMtdmbR2vGOYgrtDdLKsgmQNVo9IzR5ozRmrhz8ON0d80wnqfjMq87ilLGgQ3Bp+pUQdvowo7hO6EkMdMZq7CRWBUlihOFNVA3X7yAYwYKrVcFXk7nFXTTsmNM505jFvcYbiGrDfRCp8N6qX0C4yoVi7ndSArwHGuNkqAwCTtiESwxf/sv/P8vwg=='))

class AppSecurityOSCacheTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close);fence(self.stack)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='app-vendor-public-')))
        self.root.chmod(0o700);previous=os.umask(0o077);self.addCleanup(os.umask,previous)

    def private(self,name,raw,mode=0o600):
        path=self.root/name;path.write_bytes(raw);path.chmod(mode);return path

    def fixture(self):
        # Each fixture owns its recipe namespace; global published pins are
        # never patched or reused as synthetic acceptance evidence.
        raw=(ROOT/'scripts/compose_security_os_candidate.py').read_bytes()
        module=ModuleType('_public_fixture_recipe');module.__file__=str(ROOT/'scripts/compose_security_os_candidate.py')
        exec(compile(raw,module.__file__,'exec'),module.__dict__)
        lock=json.loads((ROOT/'scripts/app_security_os_packages.lock.json').read_bytes())
        rows=[];contents={}
        for original in lock['packages']:
            row={**original,'version':'1.0+public-fixture','path':original['name']+'_1.0_amd64.deb'}
            contents[row['name']]=('ordinary public package fixture '+row['name']).encode()
            row.update(bytes=len(contents[row['name']]),sha256=sha256(contents[row['name']]).hexdigest());rows.append(row)
        rows.sort(key=lambda row:row['name']);module.PACKAGES={r['name']:(r['version'],r['bytes'],r['sha256']) for r in rows}
        module.GROUPS={};artifacts={}
        for group,spec in lock['artifacts'].items():
            selected=[r for r in rows if r['name'] in spec['packages']]
            receipt={'status':'candidate_built_not_accepted','security_accepted':False,'production_installed':False,
                'abi':{'status':'passed'},'selected_runtime_packages':spec['packages'],'all_family_packages':selected}
            if group=='acl-tar':receipt['acl_build_receipt_sha256']=sha256(PUBLIC_ACL_RECEIPT).hexdigest()
            receipt_raw=M.encode(receipt);path=self.root/(group+'.zip')
            with ZipFile(path,'w') as archive:
                archive.writestr('build-receipt.json',receipt_raw)
                for row in selected:archive.writestr(row['path'],contents[row['name']])
                if group=='acl-tar':archive.writestr('acl-build-receipt.json',PUBLIC_ACL_RECEIPT)
            path.chmod(0o600);data=path.read_bytes();members=len(selected)+1+int(group=='acl-tar')
            artifact={**spec,'bytes':len(data),'sha256':sha256(data).hexdigest(),
                'receipt_sha256':sha256(receipt_raw).hexdigest(),'members':members}
            lock['artifacts'][group]=artifact;artifacts[group]=path
            module.GROUPS[group]=(spec['run_id'],spec['artifact_id'],len(data),artifact['sha256'],
                artifact['receipt_sha256'],set(spec['packages']),members)
        lock['packages']=rows;manifest=module.encoded({'schema':lock['manifest']['schema'],'packages':rows})
        lock['manifest'].update(bytes=len(manifest),sha256=sha256(manifest).hexdigest())
        recipe=SimpleNamespace(module=module,lock=lock,manifest=manifest)
        source=self.private('retained-public-source',b'ordinary source admission fixture')
        def load(held):
            held.read(source,1024,sha256(b'ordinary source admission fixture').hexdigest(),0o600,os.geteuid())
            return recipe
        self.stack.enter_context(patch.object(M,'load_recipe',side_effect=load))
        return recipe,artifacts,source

    def cache(self,recipe=None,artifacts=None):
        if recipe is None:recipe,artifacts,_=self.fixture()
        path=self.root/'security-os-capsule';value=M.prepare_cache(path,artifacts)
        self.assertEqual(value['status'],'cache_prepared');return path

    def verify(self,path,recipe):
        with ExitStack() as stack:
            held=M.Held(stack);M.verify_cache(path,recipe,held,os.geteuid());held.check()

    def inventory(self,path):
        return {p.name:M.identity(p.lstat()) for p in path.iterdir()}

    def test_tracked_contract_matches_exact_published_recipe_and_installer(self):
        self.assertEqual(len(PUBLIC_ACL_RECEIPT),4401)
        self.assertEqual(sha256(PUBLIC_ACL_RECEIPT).hexdigest(),'04a7e8f89a037d5a471c46d604d796b63f98ce9b6a0e0a01b450dc3af977e029')
        with ExitStack() as stack:
            held=M.Held(stack);recipe=M.load_recipe(held);held.check()
            self.assertEqual(len(recipe.lock['packages']),15)
            self.assertEqual([recipe.lock['artifacts'][k]['artifact_id'] for k in M.GROUPS],[12567076825,12567123180,12567979094])
            self.assertEqual(len(recipe.manifest),3266)
            self.assertEqual(sha256(recipe.manifest).hexdigest(),'11f0b90693a7349db9ce977ebd1eebcf5ce690166960c6e9fba2ff1e25984b81')
            installer=recipe.module.INSTALLER.encode();self.assertEqual(len(installer),3467)
            self.assertEqual(sha256(installer).hexdigest(),'1e01e6c524e95ed626a57cf87b51d75730fc70444f9419fc2cf3a93d01928215')

    def test_real_owner_prepare_publishes_exact_pins_and_public_modes_under_umask077(self):
        recipe,artifacts,_=self.fixture();cache=self.cache(recipe,artifacts)
        self.assertEqual(stat.S_IMODE(cache.stat().st_mode),0o755);self.assertEqual(cache.stat().st_uid,os.geteuid())
        self.assertEqual({p.name for p in cache.iterdir()},{r['path'] for r in recipe.lock['packages']}|{'manifest.json'})
        self.assertTrue(all(stat.S_IMODE(p.stat().st_mode)==0o644 and p.stat().st_uid==os.geteuid() for p in cache.iterdir()))
        self.assertEqual((cache/'manifest.json').read_bytes(),recipe.manifest);self.verify(cache,recipe)
        self.assertFalse(any(p.name.startswith('.security-os-capsule-') for p in self.root.iterdir()))

    def test_existing_valid_cache_and_check_are_read_only_without_download(self):
        recipe,artifacts,_=self.fixture();cache=self.cache(recipe,artifacts)
        before=self.inventory(cache);parent=set(self.root.iterdir());inode=M.identity(cache.stat())
        with patch.object(M,'download_artifact',side_effect=AssertionError('offline check must not download')):
            for check in (False,True):self.assertEqual(M.prepare_cache(cache,check_only=check)['status'],'cache_verified')
        self.assertEqual(before,self.inventory(cache));self.assertEqual(inode,M.identity(cache.stat()));self.assertEqual(parent,set(self.root.iterdir()))

    def test_absent_check_cannot_create_lock_stage_or_download(self):
        self.fixture();cache=self.root/'security-os-capsule';before=set(self.root.iterdir())
        with patch.object(M,'download_artifact',side_effect=AssertionError('must remain offline')),self.assertRaises(FileNotFoundError):
            M.prepare_cache(cache,check_only=True)
        self.assertEqual(before,set(self.root.iterdir()))

    def test_corrupt_existing_cache_is_preserved_and_never_replaced(self):
        recipe,artifacts,_=self.fixture();cache=self.cache(recipe,artifacts);before=cache.stat().st_ino
        member=cache/recipe.lock['packages'][0]['path'];member.write_bytes(b'ordinary corrupt fixture');member.chmod(0o644)
        with self.assertRaisesRegex(RuntimeError,'^file_pin$'):M.prepare_cache(cache,artifacts)
        self.assertEqual(cache.stat().st_ino,before);self.assertEqual(member.read_bytes(),b'ordinary corrupt fixture')

    def test_manifest_bytes_and_extra_inventory_are_required(self):
        recipe,artifacts,_=self.fixture();cache=self.cache(recipe,artifacts)
        manifest=cache/'manifest.json';manifest.write_bytes(M.encode({'schema':'ordinary-forged'}));manifest.chmod(0o644)
        with self.assertRaisesRegex(RuntimeError,'^file_pin$'):self.verify(cache,recipe)
        manifest.write_bytes(recipe.manifest);manifest.chmod(0o644);extra=cache/'ordinary-extra';extra.write_bytes(b'public');extra.chmod(0o644)
        with self.assertRaisesRegex(RuntimeError,'^cache_inventory$'):self.verify(cache,recipe)

    def test_cache_directory_and_file_modes_remain_strict(self):
        recipe,artifacts,_=self.fixture();cache=self.cache(recipe,artifacts)
        cache.chmod(0o777)
        with self.assertRaisesRegex(RuntimeError,'^cache_metadata$'):self.verify(cache,recipe)
        cache.chmod(0o755);member=cache/recipe.lock['packages'][0]['path'];member.chmod(0o666)
        with self.assertRaisesRegex(RuntimeError,'^file_metadata$'):self.verify(cache,recipe)

    def test_symlink_and_hardlink_members_are_refused_using_real_metadata(self):
        recipe,artifacts,_=self.fixture();cache=self.cache(recipe,artifacts);row=recipe.lock['packages'][0]
        member=cache/row['path'];original=member.read_bytes();member.unlink();other=self.private('other-member',original,0o644)
        member.symlink_to(other)
        with self.assertRaisesRegex(RuntimeError,'^path$'):self.verify(cache,recipe)
        member.unlink();os.link(other,member)
        with self.assertRaisesRegex(RuntimeError,'^file_metadata$'):self.verify(cache,recipe)

    def test_retained_file_identity_refuses_same_bytes_replacement(self):
        path=self.private('held-public',b'ordinary public fixture')
        with ExitStack() as stack:
            held=M.Held(stack);held.read(path,1024,sha256(path.read_bytes()).hexdigest(),0o600,os.geteuid())
            replacement=self.private('replacement',path.read_bytes());os.replace(replacement,path)
            with self.assertRaisesRegex(RuntimeError,'^file_changed$'):held.check()

    def test_concurrent_real_flock_refuses_without_altering_valid_cache(self):
        recipe,artifacts,_=self.fixture();cache=self.cache(recipe,artifacts);before=self.inventory(cache)
        with (self.root/'.security-os-capsule.lock').open('r+b') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError,'^cache_busy$'):M.prepare_cache(cache,artifacts)
        self.assertEqual(before,self.inventory(cache))

    def test_lock_payload_or_hardlink_never_becomes_authority(self):
        self.fixture();cache=self.root/'security-os-capsule';lock=self.private('.security-os-capsule.lock',b'ordinary nonempty fixture')
        with self.assertRaisesRegex(RuntimeError,'^lock_metadata$'):M.prepare_cache(cache)
        self.assertFalse(cache.exists());lock.write_bytes(b'');lock.chmod(0o600);os.link(lock,self.root/'lock-alias')
        with self.assertRaisesRegex(RuntimeError,'^lock_metadata$'):M.prepare_cache(cache)

    def test_partial_or_corrupt_offline_zip_set_never_publishes(self):
        recipe,artifacts,_=self.fixture();cache=self.root/'security-os-capsule'
        with self.assertRaisesRegex(RuntimeError,'^artifact_arguments$'):M.prepare_cache(cache,{'util':artifacts['util']})
        bad=artifacts['util'];bad.write_bytes(b'ordinary corrupt archive');bad.chmod(0o600)
        with self.assertRaisesRegex(RuntimeError,'^file_pin$'):M.prepare_cache(cache,artifacts)
        self.assertFalse(cache.exists());self.assertFalse(any(p.name.startswith('.security-os-capsule-') for p in self.root.iterdir()))

    def test_atomic_noreplace_refuses_an_empty_directory_created_at_publication(self):
        recipe,artifacts,_=self.fixture();cache=self.root/'security-os-capsule';publish=M.rename_noreplace;created=[]
        def race(parent,stage,name):
            cache.mkdir(mode=0o755);created.append(cache.stat().st_ino);return publish(parent,stage,name)
        with patch.object(M,'rename_noreplace',side_effect=race),self.assertRaisesRegex(RuntimeError,'^cache_exists$'):
            M.prepare_cache(cache,artifacts)
        self.assertEqual(cache.stat().st_ino,created[0]);self.assertEqual(list(cache.iterdir()),[])

    def test_source_and_artifact_checks_survive_successful_directory_publication(self):
        for kind in ('source','artifact'):
            with self.subTest(kind=kind),ExitStack() as isolated:
                parent=self.root;self.root=Path(isolated.enter_context(tempfile.TemporaryDirectory(prefix='retained-public-')));self.root.chmod(0o700)
                try:
                    recipe,artifacts,source=self.fixture();cache=self.root/'security-os-capsule';publish=M.rename_noreplace
                    def replace(parent_fd,stage,name):
                        publish(parent_fd,stage,name);target=source if kind=='source' else artifacts['util']
                        replacement=self.private('late-replacement',target.read_bytes());os.replace(replacement,target)
                    with patch.object(M,'rename_noreplace',side_effect=replace),self.assertRaisesRegex(RuntimeError,'^file_changed$'):
                        M.prepare_cache(cache,artifacts)
                    self.assertTrue(cache.is_dir());self.assertEqual(len(list(cache.iterdir())),16)
                finally:self.root=parent

    def test_download_delegate_uses_only_the_fixed_published_artifact_identity(self):
        recipe,_,_=self.fixture();recipe.module.fetch_artifact=Mock()
        for group in M.GROUPS:
            path=self.root/(group+'-download.zip');M.download_artifact(recipe,group,path);row=recipe.lock['artifacts'][group]
            recipe.module.fetch_artifact.assert_called_with(row['artifact_id'],path,row['bytes'],row['sha256'])
        source=ast.parse((ROOT/'scripts/compose_security_os_candidate.py').read_bytes())
        fetch=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='fetch_artifact')
        text=ast.unparse(fetch)
        self.assertIn("'--method', 'GET'",text);self.assertIn('actions/artifacts/',text)
        self.assertIn('RLIMIT_FSIZE',text);self.assertNotIn('RLIMIT_AS',text)

    def test_existing_fetcher_bounds_official_get_before_start_without_go_address_limit(self):
        recipe,artifacts,_=self.fixture();group='util';row=recipe.lock['artifacts'][group]
        payload=artifacts[group].read_bytes();starts=[]
        def process(argv,**kwargs):
            starts.append((argv,kwargs));kwargs['preexec_fn']()
            kwargs['stdout'].write(payload)
            return SimpleNamespace(pid=123456,wait=Mock(return_value=0))
        path=self.root/'downloaded.zip'
        with patch.dict(os.environ,{'GH_TOKEN':'ordinary-public-auth-fixture'}), \
             patch('subprocess.Popen',side_effect=process),patch('os.killpg',side_effect=ProcessLookupError), \
             patch('resource.setrlimit') as limits:
            M.download_artifact(recipe,group,path)
        self.assertEqual(path.read_bytes(),payload);self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)
        argv,kwargs=starts[0]
        self.assertEqual(argv,['gh','api','--method','GET',
            'repos/mohsenbarari/trading_bot/actions/artifacts/'+str(row['artifact_id'])+'/zip'])
        self.assertEqual(kwargs['env'],{'PATH':'/usr/bin:/bin','GH_TOKEN':'ordinary-public-auth-fixture'})
        self.assertIs(kwargs['start_new_session'],True)
        import resource
        self.assertEqual(limits.call_args_list[0].args,(resource.RLIMIT_FSIZE,(row['bytes']+1,)*2))
        self.assertEqual(limits.call_args_list[1].args,(resource.RLIMIT_CORE,(0,0)))
        self.assertFalse(any(call.args[0]==resource.RLIMIT_AS for call in limits.call_args_list))
        payload+=b'x'
        with patch.dict(os.environ,{'GH_TOKEN':'ordinary-public-auth-fixture'}), \
             patch('subprocess.Popen',side_effect=process),patch('os.killpg',side_effect=ProcessLookupError), \
             patch('resource.setrlimit'),self.assertRaisesRegex(RuntimeError,'^input_metadata$'):
            M.download_artifact(recipe,group,self.root/'oversized-download.zip')

    def test_readonly_capsule_owner_exception_cannot_widen_to_an_arbitrary_directory(self):
        with ExitStack() as stack,self.assertRaisesRegex(RuntimeError,'^image_readonly$'):
            M.Held(stack,readonly_root=self.root)

    def test_image_command_requires_real_root_and_exact_readonly_build_boundary(self):
        with patch.object(M,'load_recipe',side_effect=AssertionError('must reject before source/installer')), \
             self.assertRaisesRegex(RuntimeError,'^root_required$' if os.geteuid()!=0 else '^image_cache_path$'):
            M.install_image_cache(self.root/'security-os-capsule')
        with patch.object(M.platform,'machine',return_value='aarch64'),self.assertRaisesRegex(RuntimeError,'^image_architecture$'):
            M.image_boundary(M.CAPSULE)
        with patch.object(M.platform,'machine',return_value='x86_64'),patch.object(M.os,'statvfs',return_value=SimpleNamespace(f_flag=0)),self.assertRaisesRegex(RuntimeError,'^image_readonly$'):
            M.image_boundary(M.CAPSULE)
        with patch.object(M.platform,'machine',return_value='x86_64'),patch.object(M.os,'statvfs',return_value=SimpleNamespace(f_flag=M.os.ST_RDONLY)), \
             patch.object(M.os,'listdir',return_value=['lo','eth0']),self.assertRaisesRegex(RuntimeError,'^image_network$'):
            M.image_boundary(M.CAPSULE)

    def test_closed_failure_and_cli_do_not_echo_paths_args_or_authentication(self):
        errors=[RuntimeError('ordinary-private-path'),OSError(errno.EACCES,'ordinary-private-token','ordinary-private-path'),
            ValueError({'GH_TOKEN':'ordinary-private-token'})]
        for error in errors:
            value=M.failure(error);self.assertNotIn('ordinary-private',M.encode(value).decode());self.assertFalse(value['security_accepted'])
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(M.main(['--cache-dir',str(self.root/'security-os-capsule'),'--util-zip','ordinary-private-input']),1)
        value=json.loads(output.getvalue());self.assertEqual(value['code'],'artifact_arguments');self.assertNotIn('ordinary-private',output.getvalue())

    def test_malformed_cli_errors_are_closed_before_argparse_can_echo_values(self):
        prefix=['--cache-dir',str(self.root/'ordinary-private-cache')]
        variants=[['--unrecognized','ordinary-private-token'],['--check=ordinary-private-value'],
            ['--check','--install-image-cache'],['--util-zip','ordinary-private-zip']]
        for arguments in variants:
            with self.subTest(arguments=arguments),redirect_stdout(io.StringIO()) as output,redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(M.main(prefix+arguments),1)
            value=json.loads(output.getvalue());self.assertEqual(value['code'],'artifact_arguments')
            self.assertNotIn('ordinary-private',output.getvalue());self.assertEqual(errors.getvalue(),'')
        with redirect_stdout(io.StringIO()) as output,redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(M.main(['--help']),0)
        self.assertIn('--install-image-cache',output.getvalue());self.assertEqual(errors.getvalue(),'')

class InstallerRunnerTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close);fence(self.stack)
        self.children=[];self.forks=[];self.parent=os.getpid();self.read_sizes=[]
        self.addCleanup(self.reap_owned)
        self.stack.enter_context(patch.object(M.subprocess,'Popen',side_effect=self.spawn_python))
        self.stack.enter_context(patch.object(M.os,'fork',side_effect=self.fork_python))
        self.stack.enter_context(patch.object(M.os,'kill',side_effect=self.kill_owned))
        self.stack.enter_context(patch.object(M.os,'killpg',side_effect=self.kill_group))
        self.stack.enter_context(patch.object(M.resource,'setrlimit',side_effect=self.child_limit))
        self.stack.enter_context(patch.object(M.signal,'signal',side_effect=self.child_signal))
        self.stack.enter_context(patch.object(M.os,'pread',side_effect=self.read_bounded))

    def spawn_python(self,argv,**kwargs):
        self.assertIsInstance(argv,(list,tuple));self.assertGreaterEqual(len(argv),5)
        self.assertEqual(argv[:4],[sys.executable,'-I','-B','-c'])
        self.assertFalse(kwargs.get('shell',False));self.assertTrue(kwargs.get('close_fds',True))
        process=REAL_POPEN(argv,**kwargs);self.children.append(process);return process

    def fork_python(self):
        pid=REAL_FORK()
        if pid:self.forks.append(pid)
        return pid

    def kill_owned(self,pid,number):
        self.assertIn(pid,self.forks+[p.pid for p in self.children]);self.assertIn(number,(signal.SIGKILL,signal.SIGTERM))
        return REAL_KILL(pid,number)

    def kill_group(self,pid,number):
        self.assertIn(pid,self.forks);self.assertIn(number,(signal.SIGKILL,signal.SIGTERM))
        return REAL_KILLPG(pid,number)

    def child_limit(self,kind,pair):
        if os.getpid()==self.parent:raise AssertionError('parent resource mutation denied')
        return REAL_SETRLIMIT(kind,pair)

    def child_signal(self,*args):
        if os.getpid()==self.parent:raise AssertionError('parent signal mutation denied')
        return REAL_SIGNAL(*args)

    def read_bounded(self,fd,size,offset):
        amount=M.output_size(fd);self.read_sizes.append(amount)
        self.assertLessEqual(os.fstat(fd).st_size,M.MAX_INSTALL_OUTPUT+1)
        self.assertTrue(fcntl.fcntl(fd,fcntl.F_GET_SEALS)&fcntl.F_SEAL_GROW)
        self.assertLessEqual(amount,M.MAX_INSTALL_OUTPUT);self.assertLessEqual(size,M.MAX_INSTALL_OUTPUT)
        return REAL_PREAD(fd,size,offset)

    def reap_owned(self):
        for child in self.children:
            if child.poll() is None:
                try:REAL_KILL(child.pid,signal.SIGKILL)
                except ProcessLookupError:pass
            child.wait(timeout=2)
        for pid in self.forks:
            try:REAL_KILLPG(pid,signal.SIGKILL)
            except ProcessLookupError:pass
            try:os.waitpid(pid,0)
            except ChildProcessError:pass

    def python(self,source):return [sys.executable,'-I','-B','-c',source]

    def command(self,source):
        argv=self.python(source)
        return M.InstallerCommands(lambda value:tuple(value)==tuple(argv)),argv

    def assert_reaped(self):
        for child in self.children:
            self.assertIsNotNone(child.returncode)
            with self.assertRaises(ChildProcessError):os.waitpid(child.pid,os.WNOHANG)
        for pid in self.forks:
            with self.assertRaises(ChildProcessError):os.waitpid(pid,os.WNOHANG)

    def test_adapter_real_python_binary_text_and_check_output_are_bounded(self):
        runner,argv=self.command("import os;os.write(1,b'ordinary\\xff');os.write(2,b'public-error')")
        result=runner.run(argv,capture_output=True,check=True)
        self.assertEqual((result.returncode,result.stdout,result.stderr),(0,b'ordinary\xff',b'public-error'))
        result=runner.run(argv,capture_output=True,encoding='utf-8',errors='replace',check=True)
        self.assertEqual((result.stdout,result.stderr),('ordinary\ufffd','public-error'))
        runner,argv=self.command("print('ordinary-public-result')")
        self.assertEqual(runner.check_output(argv,text=True,stderr=subprocess.STDOUT),'ordinary-public-result\n')
        self.assertTrue(self.read_sizes);self.assert_reaped()

    def test_private_stream_has_kernel_enforced_finite_capacity(self):
        with patch.object(M,'MAX_INSTALL_OUTPUT',1024):
            fd=M.private_output()
            try:
                self.assertEqual((os.fstat(fd).st_size,M.output_size(fd)),(1025,0))
                self.assertEqual(os.write(fd,b'x'*1025),1025)
                self.assertEqual(M.output_size(fd),1025)
                with self.assertRaises(OSError):os.write(fd,b'x')
                with self.assertRaises(OSError):os.ftruncate(fd,2048)
                self.assertEqual((os.fstat(fd).st_size,M.output_size(fd)),(1025,1025))
            finally:os.close(fd)

    def test_actual_installer_commands_allow_only_the_exact_offline_contract(self):
        lock=json.loads((ROOT/'scripts/app_security_os_packages.lock.json').read_bytes())
        paths=tuple(str(M.CAPSULE/row['path']) for row in lock['packages']);allowed=M.approved_installer_commands(paths)
        query=['/usr/bin/dpkg-query','-W','-f=${Package}\t${Version}\t${db:Status-Status}\n']
        for path in paths:self.assertTrue(allowed(['/usr/bin/dpkg-deb','-f',path,'Package','Version','Architecture']))
        self.assertTrue(allowed(query));self.assertTrue(allowed(['/usr/bin/dpkg','--audit']))
        self.assertTrue(allowed(['/usr/bin/apt-get','--simulate','--no-remove','--no-download','install',*paths]))
        self.assertTrue(allowed(['/usr/bin/apt-get','--yes','--no-remove','install',*paths]))
        for argv in (['/usr/bin/apt-get','update'],['/usr/bin/apt-get','--yes','install',*paths],
                ['/usr/bin/apt-get','--yes','--no-remove','install',*reversed(paths)],
                ['/bin/sh','-c','ordinary-private-command'],query+['ordinary-private-option']):
            self.assertFalse(allowed(argv))
        for invalid in (paths[:-1],paths[:-1]+paths[:1],paths[:-1]+('/tmp/ordinary.deb',)):
            with self.assertRaisesRegex(RuntimeError,'^installer_contract$'):M.approved_installer_commands(invalid)
        self.assertEqual(self.children,[]);self.assertEqual(self.forks,[])

    def test_adapter_denies_unapproved_argv_and_ambiguous_capture_before_spawn(self):
        runner=M.InstallerCommands(lambda _:False)
        with self.assertRaisesRegex(RuntimeError,'^installer_command$'):
            runner.run(['/usr/bin/apt-get','ordinary-private-argument'],capture_output=True)
        runner,argv=self.command('pass')
        for kwargs in ({'capture_output':True,'stdout':subprocess.PIPE},{'stdin':subprocess.PIPE},
                {'timeout':True},{'timeout':0}):
            with self.subTest(kwargs=kwargs),self.assertRaisesRegex(RuntimeError,'^installer_command$'):
                runner.run(argv,**kwargs)
        self.assertEqual(self.children,[])

    def test_adapter_nonzero_preserves_bounded_private_output_and_reaps(self):
        runner,argv=self.command("import os;os.write(1,b'ordinary-private-command-output');raise SystemExit(7)")
        with self.assertRaises(subprocess.CalledProcessError) as caught:
            runner.run(argv,capture_output=True,check=True)
        self.assertEqual(caught.exception.returncode,7)
        self.assertEqual(caught.exception.stdout,b'ordinary-private-command-output')
        self.assertNotIn('ordinary-private',M.encode(M.failure(caught.exception)).decode());self.assert_reaped()

    def test_adapter_stdout_and_stderr_overflow_are_refused_before_any_read(self):
        for descriptor in (1,2):
            runner,argv=self.command('import os;os.write('+str(descriptor)+",b'x'*1025)")
            with self.subTest(descriptor=descriptor),patch.object(M,'MAX_INSTALL_OUTPUT',1024),\
                    self.assertRaisesRegex(RuntimeError,'^installer_output_bound$'):
                runner.run(argv,capture_output=True,check=True)
            self.assertEqual(self.read_sizes,[]);self.assert_reaped()

    def test_adapter_timeout_kills_and_reaps_its_real_child(self):
        runner,argv=self.command('import time;time.sleep(10)')
        with self.assertRaisesRegex(RuntimeError,'^installer_timeout$'):
            runner.run(argv,capture_output=True,check=True,timeout=0.05)
        self.assertEqual(self.read_sizes,[]);self.assert_reaped()

    def test_adapter_primary_timeout_survives_cleanup_kill_and_wait_faults(self):
        runner,argv=self.command('import time;time.sleep(10)');cleanup=[]
        def spawn(argv,**kwargs):
            process=self.spawn_python(argv,**kwargs);kill=process.kill;wait=process.wait
            self.addCleanup(setattr,process,'wait',wait)
            def failed_kill():
                cleanup.append('kill');kill();raise RuntimeError('ordinary-private-cleanup-kill')
            def failed_wait(*args,**kw):
                cleanup.append('wait');wait(*args,**kw);raise RuntimeError('ordinary-private-cleanup-wait')
            process.kill=failed_kill;process.wait=failed_wait
            return process
        with patch.object(M.subprocess,'Popen',side_effect=spawn),self.assertRaisesRegex(RuntimeError,'^installer_timeout$'):
            runner.run(argv,capture_output=True,timeout=0.05)
        self.assertEqual(cleanup,['kill','wait']);self.assert_reaped()

    def test_execute_isolates_stdio_subprocess_limits_and_hard_child_budgets(self):
        kinds=(resource.RLIMIT_CORE,resource.RLIMIT_FSIZE,resource.RLIMIT_CPU,resource.RLIMIT_AS)
        before={kind:resource.getrlimit(kind) for kind in kinds};run=M.subprocess.run;check_output=M.subprocess.check_output
        term=signal.getsignal(signal.SIGTERM)
        raw=("import os,resource\n"
            "assert __name__=='__main__'\n"
            "assert resource.getrlimit(resource.RLIMIT_CORE)==(0,0)\n"
            "for kind in (resource.RLIMIT_FSIZE,resource.RLIMIT_CPU,resource.RLIMIT_AS):\n"
            " assert 0<resource.getrlimit(kind)[0]==resource.getrlimit(kind)[1]\n"
            "os.write(1,b'ordinary-private-installer-output')\n"
            "os.write(2,b'ordinary-private-installer-error')\n").encode()
        with redirect_stdout(io.StringIO()) as out,redirect_stderr(io.StringIO()) as errors:
            M.execute_installer(raw)
        self.assertEqual((out.getvalue(),errors.getvalue()),('',''))
        self.assertIs(M.subprocess.run,run);self.assertIs(M.subprocess.check_output,check_output)
        self.assertEqual({kind:resource.getrlimit(kind) for kind in kinds},before)
        self.assertEqual(signal.getsignal(signal.SIGTERM),term);self.assert_reaped()

    def test_execute_maps_private_child_exception_and_nonzero_to_closed_failure(self):
        raw=b"import os;os.write(2,b'ordinary-private-output');raise ValueError('ordinary-private-path-token')"
        with redirect_stdout(io.StringIO()) as out,redirect_stderr(io.StringIO()) as errors,\
                self.assertRaisesRegex(RuntimeError,'^installer_failed$') as caught:
            M.execute_installer(raw)
        self.assertEqual((out.getvalue(),errors.getvalue()),('',''))
        self.assertNotIn('ordinary-private',M.encode(M.failure(caught.exception)).decode());self.assert_reaped()

    def test_execute_private_output_overflow_and_wall_timeout_reap(self):
        variants=((b"import os;os.write(1,b'x'*1025)",'installer_output_bound'),
            (b'import time;time.sleep(10)','installer_timeout'))
        for raw,code in variants:
            with self.subTest(code=code),patch.object(M,'MAX_INSTALL_OUTPUT',1024),\
                    patch.object(M,'INSTALL_WALL_SECONDS',0.1),self.assertRaisesRegex(RuntimeError,'^'+code+'$'):
                M.execute_installer(raw)
            self.assert_reaped()

    def test_execute_primary_or_success_never_signals_a_reaped_group(self):
        with patch.object(M.os,'killpg',side_effect=AssertionError('reaped group must not be signalled')) as cleanup:
            with self.assertRaisesRegex(RuntimeError,'^installer_failed$') as caught:
                M.execute_installer(b"raise ValueError('ordinary-private-primary')")
            self.assertNotIn('ordinary-private',M.encode(M.failure(caught.exception)).decode())
            M.execute_installer(b'pass')
        cleanup.assert_not_called();self.assert_reaped()

    def test_execute_timeout_primary_survives_owned_group_cleanup_fault(self):
        cleanup=[]
        def failed_group(pid,number):
            cleanup.append(pid)
            try:self.kill_group(pid,number)
            except ProcessLookupError:pass
            raise RuntimeError('ordinary-private-cleanup-error')
        raw=b'import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(10)'
        with patch.object(M.os,'killpg',side_effect=failed_group),patch.object(M,'INSTALL_WALL_SECONDS',0.1),\
                self.assertRaisesRegex(RuntimeError,'^installer_timeout$') as caught:
            M.execute_installer(raw)
        self.assertNotIn('ordinary-private',M.encode(M.failure(caught.exception)).decode());self.assert_reaped()
        self.assertEqual(cleanup,self.forks)

    def test_execute_reaps_leftover_descendant_and_refuses_cleanup_only_success(self):
        with tempfile.TemporaryDirectory(prefix='app-vendor-public-orphan-') as temporary:
            path=Path(temporary)/'public-child-pid'
            raw=("import os,time\n"
                "pid=os.fork()\n"
                "if pid==0:\n"
                " time.sleep(10)\n"
                " os._exit(0)\n"
                "with open("+repr(str(path))+",'x') as stream:stream.write(str(pid))\n").encode()
            pid=None
            try:
                with self.assertRaisesRegex(RuntimeError,'^installer_cleanup$'):M.execute_installer(raw)
                pid=int(path.read_text());self.assertGreater(pid,1);self.assertNotIn(pid,self.forks)
                with self.assertRaises(ProcessLookupError):REAL_KILL(pid,0)
                self.assert_reaped()
            finally:
                if pid is None and path.exists():pid=int(path.read_text())
                if pid is not None:
                    try:REAL_KILL(pid,signal.SIGKILL)
                    except ProcessLookupError:pass

    def test_image_checks_held_cache_after_success_and_preserves_primary_failure(self):
        calls=[];held=SimpleNamespace(check=Mock(side_effect=lambda:calls.append('held')))
        recipe=SimpleNamespace(module=SimpleNamespace(INSTALLER='ordinary harmless synthetic body'),
            lock={'packages':[{'path':'ordinary.deb'}]})
        with patch.object(M,'image_boundary',side_effect=lambda _:calls.append('boundary')),\
                patch.object(M,'Held',return_value=held),patch.object(M,'load_recipe',return_value=recipe),\
                patch.object(M,'verify_cache',side_effect=lambda *_:calls.append('verify')),\
                patch.object(M,'execute_installer',side_effect=lambda *_:calls.append('execute')) as execute:
            if os.geteuid()!=0:
                with self.assertRaisesRegex(RuntimeError,'^root_required$'):M.install_image_cache(M.CAPSULE)
                self.assertEqual(calls,[]);execute.assert_not_called();return
            value=M.install_image_cache(M.CAPSULE)
            self.assertEqual(value['status'],'image_packages_installed');self.assertEqual(calls,['boundary','verify','execute','held'])
            calls.clear();execute.side_effect=RuntimeError('installer_failed');held.check.side_effect=RuntimeError('file_changed')
            with self.assertRaisesRegex(RuntimeError,'^installer_failed$'):M.install_image_cache(M.CAPSULE)
            self.assertEqual(held.check.call_count,2)
            execute.side_effect=None
            with self.assertRaisesRegex(RuntimeError,'^file_changed$'):M.install_image_cache(M.CAPSULE)
            self.assertEqual(held.check.call_count,3)

if __name__=='__main__':unittest.main()
