"""Finite public controls only; no host, Docker, SSH, reboot or package execution."""
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import resource
import signal
import socket
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import yaml

FENCE_TARGETS = ('subprocess.Popen', 'subprocess.run', 'subprocess.call', 'subprocess.check_call', 'subprocess.check_output', 'socket.socket', 'socket.create_connection', 'socket.getaddrinfo', 'ctypes.CDLL', 'os.system', 'os.popen', 'os.fork', 'os.posix_spawn', 'os.kill', 'os.killpg', 'signal.alarm', 'signal.pause', 'resource.setrlimit')
FENCE_ERROR = 'ambient_execution_denied'
def fenced(stack):
    for target in FENCE_TARGETS:
        stack.enter_context(patch(target, side_effect=AssertionError(FENCE_ERROR)))
    return stack

with ExitStack() as _import_fence:
    fenced(_import_fence)
    REPO = Path(__file__).resolve().parents[1]
    SPEC = importlib.util.spec_from_file_location('_web_kernel_fixture', REPO/'deploy/maintenance/web_kernel_reboot_guard.py')
    M = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(M)
    PLAY = yaml.safe_load((REPO/'deploy/maintenance/web_kernel_reboot.yml').read_bytes())[0]


def raw(value):
    return (json.dumps(value, sort_keys=True)+'\n').encode()


class WebKernelRebootTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        fenced(self.stack)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.web-kernel-test-', dir=REPO)))
        self.root.chmod(0o700)
        control = self.root/'controls'; control.mkdir(mode=0o700)
        source = self.root/'source'; source.mkdir(mode=0o700)
        for name, value in {'OWNER': os.geteuid(), 'ROOT': self.root, 'SOURCE': source,
                'WINDOW': self.root/'window.json', 'STATE': self.root/'request.json', 'RESULT': self.root/'postboot-result.json',
                'COMPLETE': self.root/'complete.json', 'SOURCE_LOCK': control/'kernel.source.lock',
                'RESERVATION': control/'common.lock'}.items():
            self.stack.enter_context(patch.object(M, name, value))
        pins = {}
        for name in M.SOURCE_NAMES:
            value = (REPO/'deploy/maintenance'/name).read_bytes()
            path = source/name; path.write_bytes(value); path.chmod(0o600)
            pins[name] = sha256(value).hexdigest()
        now = datetime.now(timezone.utc)
        self.window = {'schema': 'web_kernel_reboot_window/1', 'operation_id': M.OP, 'nonce': '9'*32,
            'machine_id': '8'*32, 'before_boot_id': '11111111-1111-1111-1111-111111111111',
            'not_before_utc': (now-timedelta(seconds=5)).isoformat().replace('+00:00', 'Z'),
            'deadline_utc': (now+timedelta(minutes=50)).isoformat().replace('+00:00', 'Z'),
            'target_kernel': M.TARGET, 'completed_manifest_sha256': 'a'*64, 'completed_journal_sha256': 'b'*64,
            'completed_cleanup_sha256': 'c'*64, 'approved_sources_sha256': pins,
            'reboot_allowed': True, 'kernel_packages_allowed': False, 'engine_packages_allowed': False,
            'container_recreation_allowed': False, 'writer_dns_changes_allowed': False,
            'capture_owner_changes_allowed': False, 'automatic_downgrade_allowed': False}
        self.pin_window()

    def pin_window(self):
        M.WINDOW.write_bytes(raw(self.window)); M.WINDOW.chmod(0o600)
        self.digest = sha256(raw(self.window)).hexdigest()

    def runtime(self):
        return M.Runtime(self.digest)

    def fake_runtime(self):
        marker = self.root/'permanent.marker'; marker.write_bytes(b'unchanged public marker\n'); marker.chmod(0o600)
        @contextmanager
        def context(value):
            yield value
        rt = SimpleNamespace(window=self.window, digest=self.digest, B=SimpleNamespace(
            locked_marker=lambda: context(1), pinned=lambda: context(object()), MARKER=marker,
            equal=lambda a,b: type(a) is type(b) and a==b), host=Mock(), remaining=Mock(return_value=2000),
            completion=Mock(return_value={'marker': {'sha256': sha256(marker.read_bytes()).hexdigest()}}),
            readiness=Mock(return_value={'containers': 27, 'prior_running': 16, 'parked_qa': 10}),
            kernel=Mock(return_value={'grub_cfg_sha256': 'd'*64, 'artifacts': {}}), snapshots=Mock(return_value={}),
            command=Mock(return_value=''), bind_authority=Mock(), check_authority=Mock(), authority_files={})
        return rt

    def test_valid_separate_window_loads_only_sealed_public_boot_source(self):
        rt = self.runtime()
        self.assertEqual(rt.B.OP, 'security-web-engine-20261024-08')
        self.assertEqual(rt.B.C_SHA, '77ca1a9dc477d9a2057e6b33c61871bba529a986ceeb1bdd16a0da5f56a56f9e')
        self.assertEqual(rt.B.run, rt.command)
        self.assertNotEqual(rt.window['nonce'], rt.B.NONCE)

    def test_expired_expanded_future_wronghost_and_false_integer_windows_refuse(self):
        original = deepcopy(self.window)
        now = datetime.now(timezone.utc)
        changes = ({'deadline_utc': (now-timedelta(seconds=1)).isoformat().replace('+00:00','Z')},
                   {'deadline_utc': (now+timedelta(hours=2)).isoformat().replace('+00:00','Z')},
                   {'not_before_utc': (now+timedelta(seconds=30)).isoformat().replace('+00:00','Z')},
                   {'machine_id': True}, {'reboot_allowed': 1}, {'kernel_packages_allowed': 0},
                   {'operation_id': 'security-kernel-reboot-20261023-02'}, {'target_kernel': M.OLD_KERNEL})
        for change in changes:
            self.window = {**original, **change}; self.pin_window()
            with self.subTest(change=change), self.assertRaises(M.Refusal): self.runtime()

    def test_same_engine_nonce_missing_completion_pin_and_unknown_source_refuse(self):
        self.window['nonce'] = 'a872a41985d8f931cd856f50068c75f2'; self.pin_window()
        with self.assertRaisesRegex(M.Refusal, 'kernel_nonce_not_fresh'): self.runtime()
        self.window['nonce'] = '9'*32; self.window['completed_journal_sha256'] = None; self.pin_window()
        with self.assertRaisesRegex(M.Refusal, 'kernel_completion_pin'): self.runtime()
        self.window['completed_journal_sha256'] = 'b'*64
        self.window['approved_sources_sha256']['unreviewed.py'] = 'd'*64; self.pin_window()
        with self.assertRaisesRegex(M.Refusal, 'kernel_source_inventory'): self.runtime()

    def test_only_exact_owned_refusal_types_emit_static_content_free_reasons(self):
        self.assertEqual(M.safe_reason(M.Refusal('kernel_active_version')),'kernel_active_version')
        for error in (RuntimeError('private_value'),ValueError('private value'),M.Refusal('/private/path'),M.Refusal('a'*81)):
            self.assertEqual(M.safe_reason(error),'web_kernel_reboot_refused')

    def test_source_drift_refuses_before_module_compilation(self):
        with (M.SOURCE/'web_boot_guard.py').open('ab') as stream: stream.write(b'\n')
        with patch.object(M, 'ModuleType') as module, self.assertRaisesRegex(M.Refusal, 'kernel_control_changed'):
            self.runtime()
        module.assert_not_called()

    def test_compiles_exact_first_sealed_buffer_then_refuses_changed_source_at_fd_binding(self):
        actual_seal=M.seal; actual_compile=compile; compiled=[]
        original=(M.SOURCE/'web_boot_guard.py').read_bytes()
        def sealing(path,*args,**kwargs):
            result=actual_seal(path,*args,**kwargs)
            if path==M.SOURCE/'web_boot_guard.py':
                with path.open('ab') as stream:stream.write(b'\n# ordinary public comment drift\n')
            return result
        def compiling(value,*args,**kwargs):
            compiled.append(value);return actual_compile(value,*args,**kwargs)
        with patch.object(M,'seal',side_effect=sealing),patch.object(M,'compile',side_effect=compiling,create=True):
            rt=self.runtime()
        self.assertEqual(compiled,[original])
        with ExitStack() as stack,self.assertRaisesRegex(M.Refusal,'kernel_authority_changed'):
            rt.bind_authority(stack)

    def test_retained_window_fd_rejects_identical_byte_path_aba(self):
        rt=self.runtime()
        with ExitStack() as stack:
            rt.bind_authority(stack)
            replacement=self.root/'same-window';replacement.write_bytes(M.WINDOW.read_bytes());replacement.chmod(0o600)
            os.replace(replacement,M.WINDOW)
            with self.assertRaisesRegex(M.Refusal,'kernel_authority_changed'):rt.check_authority()

    def test_control_symlink_and_duplicate_keys_refuse_without_following_contents(self):
        original = M.WINDOW.read_bytes(); M.WINDOW.unlink()
        other = self.root/'ordinary-public.json'; other.write_bytes(original); other.chmod(0o600)
        M.WINDOW.symlink_to(other)
        with self.assertRaisesRegex(M.Refusal, 'kernel_control_path'): self.runtime()
        M.WINDOW.unlink(); M.WINDOW.write_bytes(b'{"same":1,"same":2}\n'); M.WINDOW.chmod(0o600)
        with self.assertRaisesRegex(M.Refusal, 'kernel_duplicate_key'): M.document(M.WINDOW)

    def test_default_check_never_publishes_fences_request_or_reboot(self):
        rt = self.fake_runtime()
        with patch.object(M, 'Runtime', return_value=rt), patch.object(M, 'publish') as publish, \
                patch.object(M, 'create_fences') as fences:
            result = M.execute('check', self.digest)
        self.assertEqual(result['status'], 'checked'); self.assertFalse(result['reboot_requested'])
        publish.assert_not_called(); fences.assert_not_called(); rt.command.assert_not_called()

    def test_durable_unknown_request_precedes_exactly_one_reboot_rpc_and_keeps_fences(self):
        rt = self.fake_runtime(); events = []
        actual_publish = M.publish
        def publish(path, value):
            events.append(('publish', path))
            return actual_publish(path, value)
        def command(argv):
            self.assertTrue(M.STATE.exists())
            state = M.document(M.STATE)
            self.assertEqual(state['phase'], 'reboot_requested_outcome_unknown')
            self.assertEqual([r['path'] for r in state['fences']], [str(M.SOURCE_LOCK),str(M.RESERVATION)])
            events.append(('command', argv)); raise M.Refusal('kernel_command_outcome_unknown')
        rt.command.side_effect = command
        with patch.object(M, 'Runtime', return_value=rt), patch.object(M, 'publish', side_effect=publish):
            with self.assertRaisesRegex(M.Refusal, 'kernel_command_outcome_unknown'): M.execute('request', self.digest)
            with self.assertRaisesRegex(M.Refusal, 'kernel_attempt_exists'): M.execute('request', self.digest)
        self.assertEqual(events[-1], ('command', ['/usr/bin/systemctl','reboot']))
        self.assertEqual(sum(e[0]=='command' for e in events), 1)
        self.assertTrue(M.SOURCE_LOCK.exists()); self.assertTrue(M.RESERVATION.exists())
        self.assertFalse(M.RESULT.exists()); self.assertFalse(M.COMPLETE.exists())

    def test_initial_foreign_second_fence_is_never_removed_or_adopted(self):
        rt = self.fake_runtime(); M.RESERVATION.write_bytes(b'foreign'); M.RESERVATION.chmod(0o600)
        # Inject the race only after the initial absence check.
        actual_open = M.os.open
        def open_file(path, flags, *args, **kwargs):
            if Path(path) == M.SOURCE_LOCK and flags & os.O_CREAT:
                M.RESERVATION.write_bytes(b'foreign'); M.RESERVATION.chmod(0o600)
            return actual_open(path, flags, *args, **kwargs)
        M.RESERVATION.unlink()
        with ExitStack() as stack, patch.object(M.os, 'open', side_effect=open_file):
            with self.assertRaises(FileExistsError): M.create_fences(rt, stack)
        self.assertEqual(M.RESERVATION.read_bytes(), b'foreign')
        self.assertFalse(M.SOURCE_LOCK.exists()); self.assertFalse(M.STATE.exists())

    def test_request_publication_failure_before_intent_rolls_back_only_own_fences(self):
        rt = self.fake_runtime()
        with patch.object(M, 'Runtime', return_value=rt), patch.object(M, 'publish', side_effect=OSError('fixture write failure')):
            with self.assertRaises(OSError): M.execute('request', self.digest)
        self.assertFalse(M.STATE.exists()); self.assertFalse(M.SOURCE_LOCK.exists()); self.assertFalse(M.RESERVATION.exists())
        rt.command.assert_not_called()

    def test_existing_durable_request_is_never_rolled_back_when_publish_ack_is_unknown(self):
        rt = self.fake_runtime(); actual = M.publish
        def publish_then_fail(path, value):
            actual(path, value); raise OSError('fixture fsync acknowledgement unavailable')
        with patch.object(M, 'Runtime', return_value=rt), patch.object(M, 'publish', side_effect=publish_then_fail):
            with self.assertRaises(OSError): M.execute('request', self.digest)
        self.assertTrue(M.STATE.exists()); self.assertTrue(M.RESERVATION.exists()); self.assertTrue(M.SOURCE_LOCK.exists())
        rt.command.assert_not_called()

    def test_request_start_reserve_refuses_before_any_fence_or_intent(self):
        rt=self.fake_runtime()
        def remaining(reserve=0):
            if reserve:raise M.Refusal('kernel_window_expired_or_not_started')
            return 10
        rt.remaining.side_effect=remaining
        with patch.object(M,'Runtime',return_value=rt):
            with self.assertRaisesRegex(M.Refusal,'kernel_window_expired_or_not_started'):M.execute('request',self.digest)
        self.assertFalse(M.SOURCE_LOCK.exists());self.assertFalse(M.RESERVATION.exists());self.assertFalse(M.STATE.exists())
        rt.command.assert_not_called()

    def test_partial_initialization_never_removes_a_replaced_first_fence(self):
        rt=self.fake_runtime();actual_write=M.write_all;calls=[0]
        def writing(fd,value):
            calls[0]+=1
            if calls[0]==2:
                replacement=self.root/'foreign';replacement.write_bytes(b'foreign');replacement.chmod(0o600)
                os.replace(replacement,M.SOURCE_LOCK)
                raise OSError('fixture write failure')
            return actual_write(fd,value)
        with ExitStack() as stack,patch.object(M,'write_all',side_effect=writing):
            with self.assertRaises(OSError):M.create_fences(rt,stack)
        self.assertEqual(M.SOURCE_LOCK.read_bytes(),b'foreign');self.assertFalse(M.RESERVATION.exists())
        self.assertFalse(M.STATE.exists())

    def test_same_boot_or_wrong_kernel_cannot_enter_postboot_path(self):
        rt = self.runtime()
        with patch.object(rt.B, 'native'), patch.object(M, 'read', return_value=(self.window['machine_id']+'\n').encode()), \
             patch.object(M, 'boot_id', return_value=self.window['before_boot_id']):
            with self.assertRaisesRegex(M.Refusal, 'kernel_boot_identity_mismatch'): rt.host(after=True)
        with patch.object(rt.B, 'native'), patch.object(M, 'read', return_value=(self.window['machine_id']+'\n').encode()), \
             patch.object(M, 'boot_id', return_value='22222222-2222-2222-2222-222222222222'), \
             patch.object(rt, 'command', return_value=M.OLD_KERNEL):
            with self.assertRaisesRegex(M.Refusal, 'kernel_active_version'): rt.host(after=True)

    def kernel_fixture(self):
        boot = self.root/'boot'; boot.mkdir(mode=0o700)
        grub = boot/'grub'; grub.mkdir(mode=0o700)
        defaults = self.root/'grub-default'; defaults.write_bytes(b'GRUB_DEFAULT=0\n'); defaults.chmod(0o600)
        cfg = grub/'grub.cfg'; cfg.write_text('menuentry normal {\n linux /boot/vmlinuz-'+M.TARGET+' root=UUID=public ro\n initrd /boot/initrd.img-'+M.TARGET+'\n}\n'); cfg.chmod(0o600)
        for kernel in (M.OLD_KERNEL,M.TARGET):
            for prefix in ('vmlinuz-','initrd.img-'):
                path = boot/(prefix+kernel); path.write_bytes(b'ordinary public kernel artifact fixture'); path.chmod(0o600)
        for name,value in (('BOOT_ROOT',boot),('GRUB_DEFAULT',defaults),('GRUB_CFG',cfg),('GRUB_ENV',grub/'grubenv')):
            self.stack.enter_context(patch.object(M,name,value))
        rt = self.runtime(); output={}
        def command(argv):
            if argv[0]=='/usr/bin/dpkg-query':return output.get('installed','installed')
            self.assertEqual(argv,['/usr/bin/grub-editenv',str(M.GRUB_ENV),'list'])
            return output.get('environment','saved_entry=0')
        self.stack.enter_context(patch.object(rt,'command',side_effect=command))
        return rt,defaults,cfg,output

    def test_normal_installed_kernel_first_grub_entry_and_all_four_artifacts_are_bound(self):
        rt,defaults,cfg,output = self.kernel_fixture()
        proof = rt.kernel()
        self.assertEqual(len(proof['artifacts']),4)
        self.assertEqual(proof['grub_cfg_sha256'],sha256(cfg.read_bytes()).hexdigest())
        self.assertEqual(proof['grub_default_sha256'],sha256(defaults.read_bytes()).hexdigest())
        self.assertTrue(all(len(row)==9 for row in proof['artifacts'].values()))

    def test_missing_target_saved_or_one_time_boot_wrong_first_kernel_and_symlink_artifact_refuse(self):
        rt,defaults,cfg,output = self.kernel_fixture()
        output['installed']='not-installed'
        with self.assertRaisesRegex(M.Refusal,'kernel_target_not_installed'):rt.kernel()
        output.clear(); defaults.write_bytes(b'GRUB_DEFAULT=saved\n')
        with self.assertRaisesRegex(M.Refusal,'kernel_grub_default'):rt.kernel()
        defaults.write_bytes(b'GRUB_DEFAULT=0\n'); output['environment']='next_entry=other'
        with self.assertRaisesRegex(M.Refusal,'kernel_grub_next_entry'):rt.kernel()
        output.clear(); original=cfg.read_bytes(); cfg.write_bytes(original.replace(M.TARGET.encode(),M.OLD_KERNEL.encode()))
        with self.assertRaisesRegex(M.Refusal,'kernel_grub_first_entry'):rt.kernel()
        cfg.write_bytes(original); target=M.BOOT_ROOT/('vmlinuz-'+M.TARGET); target.unlink(); target.symlink_to(M.BOOT_ROOT/('vmlinuz-'+M.OLD_KERNEL))
        with self.assertRaisesRegex(M.Refusal,'kernel_artifact_redirected'):rt.kernel()

    def test_postboot_rechecks_pinned_completion_sources_before_any_readiness_or_cleanup(self):
        rt=self.runtime(); marker=self.root/'permanent';marker.write_bytes(b'permanent');marker.chmod(0o600)
        rt.B.MARKER=marker
        plan={'marker':{'sha256':sha256(marker.read_bytes()).hexdigest()}}
        state={'authority_files':rt.authority_files,'completed_files':{'known-public-source':{'sha256':'a'*64}},'kernel_selection':{},
               'marker_identity':M.identity(marker.lstat())}
        with patch.object(rt,'host'),patch.object(rt,'no_foreign_maintenance'),patch.object(rt,'state',return_value=state), \
             patch.object(rt,'own_fences'),patch.object(M,'document',return_value=plan),patch.object(rt,'kernel',return_value={}), \
             patch.object(rt,'snapshots',return_value={'known-public-source':{'sha256':'b'*64}}), \
             patch.object(rt,'readiness') as ready:
            with ExitStack() as stack:
                rt.bind_authority(stack)
                with self.assertRaisesRegex(M.Refusal,'kernel_completed_evidence_changed'):rt.prepared_after(stack,object())
            ready.assert_not_called()
        self.assertFalse(M.RESULT.exists());self.assertFalse(M.COMPLETE.exists())

    def test_owned_common_reservation_does_not_call_default_boot_completed_absence_gate_postboot(self):
        rt=self.runtime();marker=self.root/'permanent';marker.write_bytes(b'permanent');marker.chmod(0o600)
        rt.B.MARKER=marker
        plan={'marker':{'sha256':sha256(marker.read_bytes()).hexdigest()}}
        with ExitStack() as stack:rows=M.create_fences(rt,stack)
        state={'authority_files':rt.authority_files,'fences':rows,'completed_files':{},'kernel_selection':{},'marker_identity':M.identity(marker.lstat())}
        with patch.object(rt,'host'),patch.object(rt,'no_foreign_maintenance'),patch.object(rt,'state',return_value=state), \
             patch.object(M,'document',side_effect=lambda p,*_:plan if p==rt.B.WORK/'plan.json' else json.loads(M.read(p,private=True))), \
             patch.object(rt,'snapshots',return_value={}),patch.object(rt,'kernel',return_value={}), \
             patch.object(rt,'readiness',return_value={'containers':27}), \
             patch.object(rt.B,'completed',side_effect=AssertionError('default no-maintenance gate must remain unchanged')):
            with ExitStack() as stack:
                rt.bind_authority(stack)
                self.assertEqual(rt.prepared_after(stack,object())[1],{'containers':27})
        self.assertTrue(M.RESERVATION.exists());self.assertFalse(M.RESULT.exists())

    def test_deadline_is_checked_before_commands_and_timeout_has_only_a_bounded_child_wait(self):
        rt = self.runtime(); rt.deadline = datetime.now(timezone.utc)-timedelta(seconds=1)
        with patch.object(M.subprocess,'Popen') as popen:
            with self.assertRaisesRegex(M.Refusal,'kernel_window_expired_or_not_started'):
                rt.command(['/usr/bin/true'])
            popen.assert_not_called()
        rt.deadline = datetime.now(timezone.utc)+timedelta(seconds=20)
        child=Mock(pid=123,wait=Mock(side_effect=[subprocess.TimeoutExpired('public-test',15),
                                               subprocess.TimeoutExpired('public-test',5)]))
        with patch.object(M.subprocess,'Popen',return_value=child) as popen,patch.object(M.os,'killpg') as kill:
            with self.assertRaisesRegex(M.Refusal,'kernel_command_outcome_unknown'):rt.command(['/usr/bin/true'])
        self.assertLessEqual(child.wait.call_args_list[0].kwargs['timeout'],15)
        self.assertEqual(child.wait.call_args_list[1].kwargs['timeout'],5)
        self.assertEqual(child.wait.call_count,2);kill.assert_called_once_with(123,signal.SIGKILL)
        self.assertTrue(popen.call_args.kwargs['start_new_session'])
        self.assertEqual(popen.call_args.kwargs['env'],{'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LC_ALL':'C'})

    def test_public_snapshot_refuses_path_replacement_between_read_and_metadata_capture(self):
        rt=self.runtime(); engine=self.root/'engine';engine.mkdir(mode=0o700)
        systemd=self.root/'systemd';systemd.mkdir(mode=0o700)
        rt.B.WORK=engine;rt.B.CONTROL=M.SOURCE_LOCK.parent;rt.B.SYSTEMD=systemd
        rt.B.desired=lambda:{'docker.service':'ordinary public guard'}
        # Every fixture is local. Replacement is detected at the first plan
        # read, before the fixed production archive path can be visited.
        archive=self.root/'srv';archive.mkdir(mode=0o700)
        paths=[engine/'plan.json',rt.B.CONTROL/(rt.B.OP+'.journal.json'),engine/'window.json',
               archive/(rt.B.OP+'-'+rt.B.NONCE)/'watch-cleanup.json',
               engine/'source/deploy/maintenance/web_engine_current_controller.py',
               engine/'source/deploy/maintenance/web_engine_current_profile.py',engine/'evidence/c07_plan.json',
               systemd/('docker.service.d')/rt.B.NAME]
        for path in paths:
            path.parent.mkdir(parents=True,exist_ok=True,mode=0o700);path.write_bytes(b'ordinary public control');path.chmod(0o600)
        actual_read=M.read
        def replacing(path,*args,**kwargs):
            value=actual_read(path,*args,**kwargs)
            if path==engine/'plan.json':
                replacement=engine/'replacement';replacement.write_bytes(value);replacement.chmod(0o600);os.replace(replacement,path)
            return value
        plan={'current_profile':{'source_control_sha256':{'c07_plan':'a'*64}}}
        # Replacement is detected at the first plan read, before any fixed
        # production archive path could be touched.
        with patch.object(M,'read',side_effect=replacing):
            with self.assertRaisesRegex(M.Refusal,'kernel_control_changed'):rt.snapshots(plan)

    def test_exact_owned_fence_fd_replacement_or_bytes_change_refuse(self):
        rt = self.runtime()
        with ExitStack() as stack:
            rows = M.create_fences(rt, stack)
        state = {'fences': rows}
        with ExitStack() as stack: rt.own_fences(state, stack)
        path = M.SOURCE_LOCK; old = path.read_bytes()
        replacement = self.root/'replacement'; replacement.write_bytes(old); replacement.chmod(0o600); os.replace(replacement,path)
        with ExitStack() as stack, self.assertRaisesRegex(M.Refusal, 'kernel_fence_changed'): rt.own_fences(state, stack)

    def test_post_check_is_readonly_and_finalize_releases_only_validated_own_fences(self):
        rt = self.fake_runtime()
        with ExitStack() as stack: rows = M.create_fences(rt, stack)
        ready = {'containers':27,'prior_running':16,'parked_qa':10,'datastores_verified':6,'api_probes_verified':2}
        rt.prepared_after = Mock(return_value=({'fences': rows},ready))
        with patch.object(M, 'Runtime', return_value=rt), patch.object(M, 'boot_id', return_value='22222222-2222-2222-2222-222222222222'):
            result = M.execute('post-check',self.digest)
            self.assertEqual(result['status'],'postboot_checked'); self.assertFalse(M.RESULT.exists())
            self.assertTrue(M.RESERVATION.exists())
            result = M.execute('finalize',self.digest)
        self.assertEqual(result['status'],'completed'); self.assertFalse(M.RESERVATION.exists()); self.assertFalse(M.SOURCE_LOCK.exists())
        complete = M.document(M.COMPLETE)
        self.assertEqual(complete['postboot_result_sha256'],sha256(M.RESULT.read_bytes()).hexdigest())
        self.assertTrue(complete['original_marker_retained']); self.assertFalse(complete['security_accepted'])
        rt.command.assert_not_called()

    def test_foreign_replacement_at_final_cleanup_is_retained_without_complete_receipt(self):
        rt = self.fake_runtime()
        with ExitStack() as stack: rows = M.create_fences(rt,stack)
        def prepared(stack,bus):
            M.SOURCE_LOCK.unlink(); M.SOURCE_LOCK.write_bytes(b'foreign'); M.SOURCE_LOCK.chmod(0o600)
            return {'fences':rows},{'containers':27}
        rt.prepared_after = prepared
        with patch.object(M,'Runtime',return_value=rt), patch.object(M,'boot_id',return_value='22222222-2222-2222-2222-222222222222'):
            with self.assertRaisesRegex(M.Refusal,'kernel_fence_changed'):M.execute('finalize',self.digest)
        self.assertEqual(M.SOURCE_LOCK.read_bytes(),b'foreign'); self.assertTrue(M.RESERVATION.exists())
        self.assertTrue(M.RESULT.exists()); self.assertFalse(M.COMPLETE.exists())

    def test_only_exact_ephemeral_collector_body_may_disappear_after_reboot(self):
        rt = self.runtime(); B = rt.B
        ephemeral = str(B.RUNTIME/(B.COLLECTORS[0]+'.d')/'market-container-authority.conf')
        plan = {'units':[{'name':B.COLLECTORS[0],'dropins':[{'path':ephemeral,'sha256':sha256(B.BODY.encode()).hexdigest()}]},
                         {'name':'other.service','dropins':[{'path':'/run/other.conf','sha256':'a'*64}]}]}
        with patch.object(B,'current_profile'),patch.object(B,'primary'),patch.object(B,'entries') as entries, \
             patch.object(B,'runtime',return_value={'containers':27}) as runtime,patch.object(rt,'probes',return_value={}), \
             patch.object(M.os.path,'lexists',return_value=False):
            rt.readiness(plan,object(),after=True)
            actual = runtime.call_args.args[0]
            self.assertEqual(actual['units'][0]['dropins'],[])
            self.assertEqual(actual['units'][1]['dropins'],plan['units'][1]['dropins'])
            self.assertEqual(len(plan['units'][0]['dropins']),1)
            self.assertEqual(entries.call_args.args[:2],('setup',True))
            plan['units'][0]['dropins'][0]['sha256']='0'*64
            with self.assertRaisesRegex(M.Refusal,'kernel_ephemeral_inhibitor_changed'):rt.readiness(plan,object(),after=True)

    def test_six_id_bound_datastores_api200_and_engine_enablement_are_required(self):
        rt = self.runtime(); probes=[{'id':str(i),'kind':'redis' if i<2 else 'postgres',
                     **({} if i<2 else {'user':'market_data','database':'market_archive'})} for i in range(6)]
        profile={'datastore_probes':probes,'source_control_sha256':{'c07_plan':'a'*64}}
        plan={'current_profile':profile,'health_probes':[{'id':'api','url':'http://127.0.0.1:8000/health'}],
              'engine_intent':[{'name':'docker.service','enabled':'enabled','active':True}]}
        calls=[]; bad={}
        def command(argv):
            calls.append(argv)
            if argv[0]=='/usr/bin/systemctl':return 'UnitFileState=enabled\nActiveState=active'
            if 'redis-cli' in argv:return bad.get('redis','PONG')
            if 'pg_isready' in argv:return 'accepting connections'
            return bad.get('api','200')
        with patch.object(M,'document',return_value={'health_probes':plan['health_probes']}),patch.object(rt,'command',side_effect=command):
            self.assertEqual(rt.probes(plan),{'datastores_verified':6,'api_probes_verified':1})
            self.assertEqual(sum('pg_isready'in a or 'redis-cli'in a for a in calls),6)
            bad['redis']='NOAUTH'
            with self.assertRaisesRegex(M.Refusal,'kernel_datastore_not_ready'):rt.probes(plan)
            bad.clear();bad['api']='503'
            with self.assertRaisesRegex(M.Refusal,'kernel_api_not_ready'):rt.probes(plan)

    def test_yaml_defaults_check_has_one_reboot_no_package_or_service_restoration_actions(self):
        self.assertEqual(PLAY['vars']['web_kernel_action'],'check')
        reboots=[t for t in PLAY['tasks'] if 'ansible.builtin.reboot'in t]
        self.assertEqual(len(reboots),1)
        reboot=reboots[0]
        self.assertEqual(reboot['ansible.builtin.reboot']['reboot_timeout'],300)
        self.assertEqual(reboot['timeout'],600)
        self.assertEqual(reboot['ansible.builtin.reboot']['test_command'],'/usr/bin/true')
        self.assertNotIn('retries',reboot)
        self.assertEqual(reboot['when'],"web_kernel_action == 'apply' and not ansible_check_mode")
        self.assertEqual(PLAY['tasks'][1]['check_mode'],False)
        for task in PLAY['tasks']:
            for module in ('ansible.builtin.apt','ansible.builtin.shell','ansible.builtin.systemd_service'):
                self.assertNotIn(module,task)
        source=(REPO/'deploy/maintenance/web_kernel_reboot_guard.py').read_text()
        for forbidden in ('/deploy/maintenance/reboot_guard.py','security-kernel-reboot-20261023-02',
                          '15 containers','apt-get','docker start'):
            self.assertNotIn(forbidden,source)


if __name__=='__main__':
    unittest.main()
