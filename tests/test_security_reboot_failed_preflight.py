"""Finite pre-reservation retirement tests: synthetic artifacts, no host calls."""
from contextlib import ExitStack
import configparser
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import yaml

REPO = Path(__file__).resolve().parents[1]
HERE = REPO / 'deploy/maintenance'
spec = importlib.util.spec_from_file_location('_reboot_failed_preflight_test', HERE / 'reboot_retire_failed_preflight.py')
R = importlib.util.module_from_spec(spec); spec.loader.exec_module(R)


class FailedPreflightRetirementTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.temp = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.retire-fixture-', dir=REPO)))
        self.web = self.temp / 'web'; self.web.mkdir(mode=0o700)
        self.control = self.temp / 'control'; self.control.mkdir(mode=0o700)
        self.manifest = self.control / (R.OPERATION + '.manifest.json')
        self.stack.enter_context(patch.object(R, 'WEB', self.web))
        self.stack.enter_context(patch.object(R, 'CONTROL', self.control))
        self.stack.enter_context(patch.object(R, 'MANIFEST', self.manifest))
        self.stack.enter_context(patch.object(R, 'parents', side_effect=lambda path:
            R.require(path.is_relative_to(self.temp) and path.resolve(strict=False) == path, 'non_fixture_path')))
        for target in ('subprocess.Popen', 'subprocess.run', 'socket.socket.connect', 'socket.create_connection', 'socket.getaddrinfo'):
            self.stack.enter_context(patch(target, side_effect=AssertionError('no host or network operation')))
        self.options = SimpleNamespace(manifest_sha256='a' * 64, nonce='11111111-1111-4111-8111-111111111111',
            source_revision='b' * 40, intent_sha256='c' * 64, log_sha256='d' * 64, apply=False)
        self.intent = {'schema': 'security_kernel_reboot_controller_intent/1', 'operation_id': R.OPERATION,
            'nonce': self.options.nonce, 'source_revision': self.options.source_revision,
            'status': 'single_attempt_started'}
        manifest = b'{"synthetic_original_manifest":true}\n'
        self.options.manifest_sha256 = sha256(manifest).hexdigest()
        self.intent['manifest_sha256'] = self.options.manifest_sha256
        intent = (json.dumps(self.intent, sort_keys=True) + '\n').encode()
        log = b'owned synthetic SSH PTY failure\n'
        self.options.intent_sha256 = sha256(intent).hexdigest(); self.options.log_sha256 = sha256(log).hexdigest()
        for name, data in [('controller-intent.json', intent), ('ansible.log', log), ('reboot_manifest.json', manifest)]:
            self.file(self.web / name, data)
        self.file(self.manifest, manifest)
        self.show = self.stack.enter_context(patch.object(R, 'run', return_value='ActiveState=failed\nMainPID=0\nExecMainStatus=1'))

    def file(self, path, data=b'fixture'):
        path.write_bytes(data); path.chmod(0o600); return path

    def test_remote_check_is_readonly_and_requires_exact_intent_and_three_hashes(self):
        before = {p.name: p.read_bytes() for p in self.web.iterdir()}
        self.assertEqual(R.remote(self.options)['status'], 'checked')
        self.assertEqual({p.name: p.read_bytes() for p in self.web.iterdir()}, before)
        self.options.log_sha256 = '0' * 64
        with self.assertRaisesRegex(RuntimeError, 'web_artifact_changed'): R.remote(self.options)
        self.assertEqual({p.name: p.read_bytes() for p in self.web.iterdir()}, before)

    def test_remote_archive_preserves_all_three_bytes_and_never_resets_failed_unit(self):
        before = {p.name: p.read_bytes() for p in self.web.iterdir()}
        self.options.apply = True
        self.assertEqual(R.remote(self.options)['status'], 'archived')
        archive = self.web / ('failed-preflight-' + self.options.nonce)
        for name, raw in before.items():
            self.assertFalse((self.web / name).exists()); self.assertEqual((archive / name).read_bytes(), raw)
        self.assertEqual(json.loads((archive / 'retirement.json').read_bytes()), R.expected(self.options))
        self.assertEqual(self.show.call_args.args[0][:2], ['/usr/bin/systemctl', 'show'])
        self.assertEqual(R.remote(self.options)['status'], 'archived')

    def test_partial_remote_archiving_resumes_only_exact_owned_nonce_and_hashes(self):
        self.options.apply = True
        original = R.noreplace
        calls = 0
        def interrupted(source, destination):
            nonlocal calls
            original(source, destination)
            if source.name == 'ansible.log':
                calls += 1
                if calls == 1: raise RuntimeError('injected archive acknowledgement loss')
        with patch.object(R, 'noreplace', side_effect=interrupted), self.assertRaises(RuntimeError):
            R.remote(self.options)
        self.assertTrue((self.web / 'controller-intent.json').exists())
        self.assertEqual(R.remote(self.options)['status'], 'archived')

    def test_short_writes_publish_only_a_complete_receipt_and_cannot_overwrite(self):
        path = self.temp / 'receipt.json'
        original = os.write
        with patch.object(R.os, 'write', side_effect=lambda fd, data: original(fd, data[:3])):
            R.publish(path, {'complete': True})
        self.assertEqual(json.loads(path.read_bytes()), {'complete': True})
        with self.assertRaises(OSError): R.publish(path, {'complete': False})
        self.assertEqual(json.loads(path.read_bytes()), {'complete': True})

    def test_kill_before_receipt_or_directory_publication_leaves_no_canonical_blocker(self):
        path = self.temp / 'receipt.json'
        with patch.object(R.os, 'write', side_effect=RuntimeError('interrupted short receipt')), self.assertRaises(RuntimeError):
            R.publish(path, {'complete': True})
        self.assertFalse(path.exists())
        archive = self.web / ('failed-preflight-' + self.options.nonce)
        original = R.noreplace
        def unpublished(source, destination):
            if destination == archive: raise RuntimeError('interrupted directory publication')
            return original(source, destination)
        with patch.object(R, 'noreplace', side_effect=unpublished), self.assertRaises(RuntimeError):
            R.prepare_archive(archive, R.expected(self.options))
        self.assertFalse(archive.exists())
        self.assertTrue((self.web / 'controller-intent.json').exists())
        R.prepare_archive(archive, R.expected(self.options))
        self.assertEqual(json.loads((archive / 'retirement.json').read_bytes()), R.expected(self.options))

    def test_live_unit_or_any_result_or_wrong_intent_refuses_before_archive(self):
        for metadata in ('ActiveState=active\nMainPID=123\nExecMainStatus=0',
                         'ActiveState=failed\nMainPID=123\nExecMainStatus=1'):
            with self.subTest(metadata=metadata), patch.object(R, 'run', return_value=metadata), self.assertRaises(RuntimeError):
                R.remote(self.options)
        for name in ('native-result.json', 'controller-result.json', 'credential-cleanup.log'):
            path = self.file(self.web / name)
            with self.subTest(name=name), self.assertRaises(RuntimeError): R.remote(self.options)
            path.unlink()
        self.assertTrue((self.web / 'controller-intent.json').exists())
        self.assertEqual(len(list(self.web.iterdir())), 3)

    def test_foreign_archive_receipt_cannot_be_adopted(self):
        archive = self.web / ('failed-preflight-' + self.options.nonce); archive.mkdir(mode=0o700)
        self.file(archive / 'retirement.json', b'{}')
        self.options.apply = True
        with self.assertRaisesRegex(RuntimeError, 'foreign_archive_intent'): R.remote(self.options)
        self.assertTrue((self.web / 'controller-intent.json').exists())

    def test_archives_never_overwrite_existing_file_or_accept_ambiguous_pair(self):
        source = self.file(self.temp / 'source', b'old'); target = self.file(self.temp / 'target', b'keep')
        with self.assertRaises(RuntimeError): R.archive_file(source, target, sha256(b'old').hexdigest())
        with self.assertRaises(OSError): R.noreplace(source, target)
        self.assertEqual(source.read_bytes(), b'old'); self.assertEqual(target.read_bytes(), b'keep')

    def test_read_and_archive_refuse_symlink_hardlink_changed_digest_and_unsafe_mode(self):
        original = self.file(self.temp / 'original', b'public fixture')
        symlink = self.temp / 'symlink'; symlink.symlink_to(original)
        with self.assertRaises((RuntimeError, OSError)): R.read(symlink)
        linked = self.temp / 'linked'; os.link(original, linked)
        with self.assertRaises(RuntimeError): R.read(original)
        linked.unlink(); original.chmod(0o644)
        with self.assertRaises(RuntimeError): R.read(original)
        original.chmod(0o600)
        with self.assertRaises(RuntimeError): R.archive_file(original, self.temp / 'archive', '0' * 64)
        self.assertTrue(original.exists())

    def test_native_any_reservation_state_token_result_inhibitor_refuses(self):
        base = SimpleNamespace(RESERVATION=self.temp / 'reservation', PENDING=self.temp / 'pending', runtime=Mock())
        guard = SimpleNamespace(B=base, STATE=self.temp / 'state', TOKEN=self.temp / 'token', RESULT=self.temp / 'result',
            GOLDEN_INHIBITOR=self.temp / 'golden', invariants=Mock())
        for path in (base.RESERVATION, base.PENDING, guard.STATE, guard.TOKEN, guard.RESULT, guard.GOLDEN_INHIBITOR):
            self.file(path)
            with self.subTest(path=path.name), self.assertRaisesRegex(RuntimeError, 'native_operation_started'):
                R.native_check(guard, {})
            path.unlink()
        base.runtime.assert_not_called()

    def test_native_default_never_archives_or_asks_remote_to_apply(self):
        guard = SimpleNamespace(lock_current=Mock())
        with patch.object(R, 'canonical_guard', return_value=(guard, {})), patch.object(R, 'native_check') as baseline, \
                patch.object(R, 'web', return_value={}) as web, patch.object(R, 'publish') as publish, \
                patch.object(R, 'archive_file') as archive:
            self.assertEqual(R.retire(self.options)['status'], 'checked')
            baseline.assert_called_once(); web.assert_called_once_with(self.options, False)
            publish.assert_not_called(); archive.assert_not_called()
        self.assertTrue(self.manifest.exists())

    def test_native_archive_requires_baseline_both_before_and_after_remote_retirement(self):
        guard = SimpleNamespace(lock_current=Mock())
        self.options.apply = True
        with patch.object(R, 'canonical_guard', return_value=(guard, {})), patch.object(R, 'native_check', side_effect=[None, RuntimeError('baseline drift')]), \
                patch.object(R, 'web', return_value={}) as web:
            with self.assertRaisesRegex(RuntimeError, 'baseline drift'): R.retire(self.options)
        self.assertTrue(self.manifest.exists())
        self.assertEqual([call.args[1] for call in web.call_args_list], [False, True])

    def test_complete_native_archive_is_preserved_and_resumable_without_rearming(self):
        guard = SimpleNamespace(lock_current=Mock())
        self.options.apply = True
        with patch.object(R, 'canonical_guard', return_value=(guard, {})), patch.object(R, 'native_check'), patch.object(R, 'web', return_value={}):
            self.assertEqual(R.retire(self.options)['status'], 'retired_pre_mutation')
            self.assertFalse(self.manifest.exists())
            self.assertEqual(R.retire(self.options)['status'], 'retired_pre_mutation')
        archives = list(self.control.glob('*.manifest.json'))
        self.assertEqual(len(archives), 1)
        self.assertEqual(sha256(archives[0].read_bytes()).hexdigest(), self.options.manifest_sha256)

    def test_actual_ansible_command_preflight_tags_and_no_pty_precede_intent(self):
        config = configparser.ConfigParser(); config.read(HERE / 'reboot_ansible.cfg')
        self.assertFalse(config.getboolean('ssh_connection', 'usetty'))
        tasks = yaml.safe_load((HERE / 'reboot_external.yml').read_text())[0]['tasks']
        tagged = [task for task in tasks if 'controller_preflight' in task.get('tags', [])]
        self.assertEqual(tagged, tasks[:2])
        self.assertIn('ansible.builtin.command', tagged[1])
        self.assertFalse(any('ansible.builtin.reboot' in task for task in tagged))
        controller = (HERE / 'reboot_controller.py').read_text()
        self.assertLess(controller.index("'preflight.log', 180"), controller.index("publish('controller-intent.json'"))
        self.assertIn("command + ['--tags', 'controller_preflight']", controller)
        self.assertIn("'external_command_preflight_failed_no_reboot_intent'", controller)


if __name__ == '__main__':
    unittest.main()
