"""Hermetic daemon-maintenance contracts; no services, environment or network."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import fcntl
import os
from pathlib import Path
import selectors
import signal
import tempfile
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

import yaml

from scripts import validate_docker_daemon_maintenance as guard

REPO = Path(__file__).resolve().parents[1]
PLAY = REPO / 'deploy/maintenance/docker_daemon_security.yml'
IDENT = 'a' * 64


def fixture():
    mount = {'Destination': '/data', 'Source': '/owned/data', 'RW': True,
             'Mode': 'rw', 'Propagation': 'rprivate', 'Type': 'volume'}
    before = {'id': IDENT, 'name': '/owned-db', 'image': 'sha256:' + 'b' * 64,
              'running': True, 'restart_policy': {'Name': 'unless-stopped', 'MaximumRetryCount': 0},
              'project': 'owned', 'mounts': [mount], 'started_at': 'prior-time'}
    plan = {'runtime_snapshot': '/snapshot', 'managed_project_labels': ['owned'],
            'docker_root': '/owned/docker',
            'rollback_packages': [{'name': name, 'version': 'old'} for name in guard.PACKAGES],
            'datastore_ids': [IDENT], 'datastore_probes': [{'id': IDENT, 'kind': 'redis'}], 'units': [
                {'name': 'owned.timer', 'kind': 'timer', 'enabled': 'enabled', 'active': True},
                {'name': 'owned.service', 'kind': 'oneshot', 'enabled': 'static', 'active': False}]}
    return plan, {'schema': 'security_runtime_snapshot/1.0', 'containers': [before]}


class RuntimeGuardTests(unittest.TestCase):
    def invoke(self, phase, mutate=None, ids=None, unit_active='active', docker='29.9.2', containerd='2.4.2', installed=None, pong='PONG', recovery=False):
        plan, snapshot = fixture()
        current = deepcopy(snapshot['containers'][0])
        current['health'] = 'healthy'
        current['mounts'][0].update(Name='owned-volume', Driver='local')
        if phase not in {'preflight', 'complete'}:
            current['restart_policy'] = {'Name': 'no', 'MaximumRetryCount': 0}
        if phase == 'quiesced':
            current['running'] = False
        if mutate:
            mutate(current)

        def native(argv):
            if argv[0] == '/usr/bin/dpkg-query':
                if installed is not None:
                    return installed
                return '\n'.join(f'{name}={"old" if phase == "preflight" else version}' for name, version in guard.PACKAGES.items())
            if argv[0] == '/usr/bin/ctr':
                return f'Client:\n Version: v2.4.2\nServer:\n Version: v{containerd}\n Revision: test'
            if argv[1:3] == ['container', 'ls']:
                return '\n'.join(ids if ids is not None else [IDENT])
            if argv[1] == 'inspect':
                return json.dumps(current)
            if argv[1] == 'exec':
                return pong
            if argv[1] == 'version':
                return docker
            if argv[1] == 'info':
                return plan['docker_root']
            if argv[1] == 'show':
                return ('static' if argv[2] == 'owned.service' else 'enabled') if argv[4] == 'UnitFileState' else unit_active
            self.fail('Unexpected native command: ' + argv[0])

        with patch.object(guard, 'bound_document', return_value=snapshot), patch.object(guard, 'command', side_effect=native):
            return guard.runtime(plan, phase, recovery=recovery)

    def test_exact_baseline_and_real_docker_volume_extra_metadata(self):
        self.invoke('preflight')
        self.invoke('complete')

    def test_oneshot_transient_state_does_not_create_false_drift(self):
        self.invoke('complete')  # oneshot reports active; intended timer is active.

    def test_quiescence_requires_stopped_containers_and_disabled_restart(self):
        self.invoke('quiesced')
        with self.assertRaisesRegex(guard.Refusal, 'running_state'):
            self.invoke('quiesced', lambda c: c.update(running=True))
        with self.assertRaisesRegex(guard.Refusal, 'restart_policy'):
            self.invoke('quiesced', lambda c: c.update(restart_policy={'Name': 'always', 'MaximumRetryCount': 0}))

    def test_new_missing_or_replaced_container_refused(self):
        for ids in ([], [IDENT, 'c' * 64], ['c' * 64]):
            with self.subTest(ids=ids), self.assertRaisesRegex(guard.Refusal, 'container_set'):
                self.invoke('preflight', ids=ids)

    def test_image_project_and_bind_source_drift_refused(self):
        mutations = [lambda c: c.update(image='sha256:' + 'd' * 64),
                     lambda c: c.update(project='unowned'),
                     lambda c: c['mounts'][0].update(Source='/wrong')]
        for mutate in mutations:
            with self.subTest(mutate=mutate), self.assertRaisesRegex(guard.Refusal, 'identity_changed'):
                self.invoke('preflight', mutate)

    def test_recovery_allows_only_original_or_disabled_policy(self):
        self.invoke('recoverable', lambda c: c.update(restart_policy={'Name': 'unless-stopped', 'MaximumRetryCount': 0}))
        with self.assertRaisesRegex(guard.Refusal, 'restart_policy'):
            self.invoke('recoverable', lambda c: c.update(restart_policy={'Name': 'always', 'MaximumRetryCount': 0}))

    def test_health_is_mandatory_before_restoration(self):
        with self.assertRaisesRegex(guard.Refusal, 'unhealthy'):
            self.invoke('healthy', lambda c: c.update(health='unhealthy'))

    def test_timer_intent_drift_refused(self):
        with self.assertRaisesRegex(guard.Refusal, 'intent_changed'):
            self.invoke('complete', unit_active='inactive')

    def test_preflight_old_package_inventory_and_actual_new_daemon_versions_required(self):
        with self.assertRaisesRegex(guard.Refusal, 'installed_package_versions'):
            self.invoke('preflight', installed='docker-ce=wrong')
        with self.assertRaisesRegex(guard.Refusal, 'docker_daemon_version'):
            self.invoke('healthy', docker='29.7.2')
        with self.assertRaisesRegex(guard.Refusal, 'containerd_server_version'):
            self.invoke('complete', containerd='2.3.3')

    def test_recovery_of_old_runtime_is_distinct_from_successful_security_patch(self):
        self.invoke('complete', docker='29.7.2', containerd='2.3.3', installed='old', recovery=True)

    def test_real_redis_pong_contract_is_not_just_container_running(self):
        with self.assertRaisesRegex(guard.Refusal, 'redis_not_ready'):
            self.invoke('healthy', pong='NOAUTH')


class FileAndAtomicGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        # The real native invariant requires root. Unit fixtures deliberately
        # exercise file rules as their actual CI uid, without sudo or skips.
        self.parent = patch.object(guard, 'private_parent')
        self.parent.start()
        self.addCleanup(self.parent.stop)

    def write(self, name, payload):
        path = self.base / name
        path.write_bytes(payload)
        path.chmod(0o600)
        return path

    def test_bounded_owned_regular_file_and_atime_safe_read(self):
        path = self.write('owned', b'hello')
        self.assertEqual(guard.read(path, maximum=5), b'hello')
        with self.assertRaisesRegex(guard.Refusal, 'file_mode_or_bound'):
            guard.read(path, maximum=4)

    def test_symlink_hardlink_public_mode_and_fifo_refused(self):
        target = self.write('owned', b'hello')
        link = self.base / 'symlink'
        link.symlink_to(target)
        with self.assertRaises(OSError):
            guard.read(link)
        hard = self.base / 'hard'
        os.link(target, hard)
        with self.assertRaisesRegex(guard.Refusal, 'unsafe_file'):
            guard.read(hard)
        hard.unlink()
        target.chmod(0o644)
        with self.assertRaisesRegex(guard.Refusal, 'file_mode_or_bound'):
            guard.read(target)
        fifo = self.base / 'fifo'
        os.mkfifo(fifo, 0o600)
        with self.assertRaisesRegex(guard.Refusal, 'unsafe_file'):
            guard.read(fifo)

    def test_manifest_hash_and_duplicate_keys_refused(self):
        path = self.write('manifest', b'{"schema":"docker_daemon_security/1","operation_id":"test"}')
        self.assertEqual(guard.load(path, sha256(path.read_bytes()).hexdigest())['operation_id'], 'test')
        with self.assertRaisesRegex(guard.Refusal, 'manifest_changed'):
            guard.load(path, 'a' * 64)
        path.write_bytes(b'{"schema":"x","schema":"y"}')
        with self.assertRaisesRegex(guard.Refusal, 'duplicate_key'):
            guard.document(path)

    def test_manifest_parse_uses_exact_verified_buffer_without_second_read(self):
        raw = b'{"schema":"docker_daemon_security/1","operation_id":"verified"}'
        changed = b'{"schema":"docker_daemon_security/1","operation_id":"changed"}'
        with patch.object(guard, 'read', side_effect=[raw, changed]) as reader:
            self.assertEqual(guard.load(self.base / 'manifest', sha256(raw).hexdigest())['operation_id'], 'verified')
            self.assertEqual(reader.call_count, 1)

    def test_atomic_noreplace_never_overwrites_race_winner(self):
        source = self.write('temporary', b'complete')
        target = self.write('canonical', b'foreign')
        with self.assertRaisesRegex(guard.Refusal, 'reservation_already_exists'):
            guard.publish_noreplace(source, target)
        self.assertEqual(target.read_bytes(), b'foreign')
        self.assertEqual(source.read_bytes(), b'complete')

    def test_atomic_noreplace_publishes_whole_inode(self):
        source = self.write('temporary', b'complete')
        target = self.base / 'canonical'
        before = source.stat().st_ino
        guard.publish_noreplace(source, target)
        self.assertEqual(target.read_bytes(), b'complete')
        self.assertEqual(target.stat().st_ino, before)
        self.assertFalse(source.exists())

    def test_write_all_retries_short_writes_and_refuses_zero_progress(self):
        with patch.object(guard.os, 'write', side_effect=[2, 1]) as writer:
            guard.write_all(12, b'abc')
            self.assertEqual(writer.call_args_list[1].args, (12, b'c'))
        with patch.object(guard.os, 'write', return_value=0), self.assertRaisesRegex(guard.Refusal, 'no_progress'):
            guard.write_all(12, b'x')

    def test_required_mount_coverage_cannot_be_empty_incomplete_or_duplicated(self):
        plan = {'source_root': '/source', 'docker_root': '/docker', 'mounts': []}
        for mounts in ([], [{'path': '/source'}], [{'path': '/source'}, {'path': '/source'}]):
            plan['mounts'] = mounts
            with self.subTest(mounts=mounts), patch.object(guard.os, 'stat', side_effect=AssertionError('must reject before native probe')), self.assertRaisesRegex(guard.Refusal, 'required_mount'):
                guard.mount_fence(plan)

    def test_real_bounded_owned_subprocess_timeout_terminates_and_reaps_it(self):
        created = []
        native = subprocess.Popen

        def spawn(*args, **kwargs):
            child = native(*args, **kwargs)
            created.append(child)
            return child

        deadline = (datetime.now(timezone.utc) + timedelta(seconds=0.2)).isoformat().replace('+00:00', 'Z')
        with patch.object(guard.subprocess, 'Popen', side_effect=spawn), self.assertRaises(subprocess.TimeoutExpired):
            guard.execute_ansible([sys.executable, '-I', '-B', '-c', 'import time; time.sleep(30)'], {'PATH': os.defpath}, deadline)
        self.assertEqual(len(created), 1)
        self.assertIsNotNone(created[0].poll())
        with self.assertRaises(ProcessLookupError):
            os.killpg(created[0].pid, 0)

    def test_real_parent_sigkill_does_not_release_inherited_operation_flock(self):
        lock = self.write('guarded-lock', b'unchanged')
        script = (
            'import importlib.util,os,fcntl,sys; '
            's=importlib.util.spec_from_file_location("fixture_guard",sys.argv[1]); '
            'm=importlib.util.module_from_spec(s);s.loader.exec_module(m); '
            'fd=os.open(sys.argv[2],os.O_RDWR);fcntl.flock(fd,fcntl.LOCK_EX); '
            'm.execute_ansible([sys.executable,"-I","-B","-c",'
            '"import os,time;print(os.getpid(),flush=True);time.sleep(0.8)"],'
            '{"PATH":os.defpath},"2099-01-01T00:00:00Z",lock_fds=(fd,))'
        )
        parent = subprocess.Popen([sys.executable, '-I', '-B', '-c', script, guard.__file__, str(lock)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        descendant = None
        try:
            with selectors.DefaultSelector() as poll:
                poll.register(parent.stdout, selectors.EVENT_READ)
                self.assertTrue(poll.select(timeout=3), 'Owned fixture failed to start')
                line = parent.stdout.readline(32)
            self.assertTrue(line.strip().isdigit(), 'Owned fixture produced no public PID')
            descendant = int(line)
            parent.kill()
            parent.wait(timeout=3)
            fd = os.open(lock, os.O_RDWR)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                deadline = time.monotonic() + 4
                while True:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        self.assertLess(time.monotonic(), deadline, 'Owned child did not release fence')
                        time.sleep(0.02)
            finally:
                os.close(fd)
            self.assertEqual(lock.read_bytes(), b'unchanged')
        finally:
            if parent.poll() is None:
                parent.kill()
                parent.wait(timeout=3)
            if descendant is not None:
                try:
                    os.killpg(descendant, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            parent.stdout.close()
            parent.stderr.close()


class PlaybookContractTests(unittest.TestCase):
    def setUp(self):
        self.play = yaml.safe_load(PLAY.read_text())[0]

    def tasks(self, nodes=None):
        for item in self.play['tasks'] if nodes is None else nodes:
            yield item
            for key in ('block', 'rescue', 'always'):
                yield from self.tasks(item.get(key, []))

    def test_only_local_builtins_and_no_reboot_release_or_prune(self):
        self.assertFalse(self.play['gather_facts'])
        self.assertFalse(self.play['vars']['maintenance_apply'])
        for task in self.tasks():
            actions = [key for key in task if key.startswith('ansible.')]
            self.assertTrue(all(action.startswith('ansible.builtin.') for action in actions))
            self.assertNotIn('ansible.builtin.reboot', actions)
            if 'ansible.builtin.command' in task:
                args = task['ansible.builtin.command']['argv']
                if isinstance(args, list) and args[:2] == ['/usr/bin/docker', 'inspect']:
                    self.assertIn('--format', args)
        source = PLAY.read_text()
        for forbidden in ('compose up', 'docker rm', 'system prune', 'inode apply', 'allow_unauthenticated: true'):
            self.assertNotIn(forbidden, source)

    def test_default_preflight_ends_before_any_write_or_download(self):
        tasks = self.play['tasks']
        stop = next(i for i, task in enumerate(tasks) if task.get('ansible.builtin.meta') == 'end_play')
        self.assertIn('ansible_check_mode', tasks[stop]['when'])
        self.assertTrue(all(not any(k in task for k in ('ansible.builtin.apt', 'ansible.builtin.systemd_service')) for task in tasks[:stop]))

    def test_pins_and_package_postinst_restart_protection(self):
        self.assertEqual(set(self.play['vars']['package_pins']), {f'{k}={v}' for k, v in guard.PACKAGES.items()})
        installs = [task['ansible.builtin.apt'] for task in self.tasks() if 'ansible.builtin.apt' in task]
        self.assertEqual(len(installs), 1)
        self.assertEqual(installs[0]['policy_rc_d'], 101)
        self.assertTrue(installs[0]['fail_on_autoremove'])
        self.assertFalse(installs[0]['allow_downgrade'])
        self.assertFalse(installs[0]['allow_unauthenticated'])

    def test_timers_stop_first_and_oneshot_is_never_manually_started(self):
        block = next(task['block'] for task in self.play['tasks'] if 'block' in task)
        self.assertIn("'timer'", block[0]['loop'])
        self.assertEqual(block[0]['ansible.builtin.systemd_service']['state'], 'stopped')
        starts = [task for task in self.tasks() if task.get('ansible.builtin.systemd_service', {}).get('state') == 'started' and 'plan.units' in task.get('loop', '')]
        self.assertEqual(len(starts), 2)
        self.assertTrue(all("'active', 'equalto', true" in task['loop'] and "'oneshot'" not in task['loop'] for task in starts))


class ParentLifetimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.marker, self.source, self.reservation = (self.base / n for n in ('marker', 'source', 'reservation'))
        self.marker.write_bytes(b'unchanged-stale-identity-marker')
        self.source.write_bytes(b'')
        self.marker.chmod(0o600)
        self.source.chmod(0o600)
        for name, value in [('MARKER', self.marker), ('SOURCE', self.source), ('RESERVATION', self.reservation)]:
            patcher = patch.object(guard, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.plan = {'operation_id': 'isolated-test', 'source_root': str(self.base),
                     'deadline_utc': '2099-01-01T00:00:00Z',
                     'files': [{'path': '/root/trading-bot/trading_bot/.local/security-maintenance-ansible-venv/bin/ansible-playbook'}]}
        for name in ('private_parent', 'invariants', 'runtime', 'validate'):
            patcher = patch.object(guard, name)
            patcher.start()
            self.addCleanup(patcher.stop)

    def result(self):
        return json.loads(self.reservation.with_name('isolated-test.daemon-maintenance-result.json').read_text())

    def assert_locked(self, path):
        fd = os.open(path, os.O_RDONLY)
        try:
            with self.assertRaises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)

    def test_default_probe_has_no_file_creation_or_ansible(self):
        with patch.object(guard.tempfile, 'mkstemp', side_effect=AssertionError('default write')), patch.object(guard, 'execute_ansible') as child:
            guard.run(self.plan, self.base / 'plan', 'a' * 64, False)
        child.assert_not_called()
        self.assertFalse(self.reservation.exists())
        self.assertEqual(sorted(p.name for p in self.base.iterdir()), ['marker', 'source'])

    def test_entire_child_lifetime_holds_all_fences_and_preserves_marker_bytes(self):
        def child(argv, env, deadline, **kwargs):
            self.assertEqual(len(kwargs['lock_fds']), 3)
            for path in (self.marker, self.source, self.reservation):
                self.assert_locked(path)
            payload = json.loads(self.reservation.read_text())
            self.assertEqual(payload['manifest_sha256'], 'a' * 64)
            self.assertEqual(payload['pid'], os.getpid())
            self.assertEqual(env['DOCKER_HOST'], 'unix:///var/run/docker.sock')
            self.assertIn('ANSIBLE_HOME', env)
            self.assertIn('ANSIBLE_LOCAL_TEMP', env)
            self.assertNotIn('HOME', env)
            return 0
        with patch.object(guard, 'execute_ansible', side_effect=child):
            guard.run(self.plan, self.base / 'plan', 'a' * 64, True)
        self.assertEqual(self.marker.read_bytes(), b'unchanged-stale-identity-marker')
        self.assertEqual(self.source.read_bytes(), b'')
        self.assertFalse(self.reservation.exists())
        self.assertEqual(self.result()['status'], 'completed')

    def test_failed_but_verified_recovery_is_never_reported_as_patched(self):
        with patch.object(guard, 'execute_ansible', return_value=2), self.assertRaisesRegex(guard.Refusal, 'failed_runtime_restored'):
            guard.run(self.plan, self.base / 'plan', 'a' * 64, True)
        self.assertFalse(self.reservation.exists())
        self.assertEqual(self.result()['status'], 'failed_recovered')
        self.assertEqual(guard.validate.call_args.kwargs, {'recovery': True})

    def test_failed_final_parity_retains_guard_and_explicit_failure_receipt(self):
        def validate(plan, digest, phase, **kwargs):
            if phase == 'complete':
                raise guard.Refusal('test_restoration_failed')
        with patch.object(guard, 'validate', side_effect=validate), patch.object(guard, 'execute_ansible', return_value=1), self.assertRaises(guard.Refusal):
            guard.run(self.plan, self.base / 'plan', 'a' * 64, True)
        self.assertTrue(self.reservation.exists())
        self.assertEqual(self.result()['status'], 'failed_closed')
        self.assertEqual(self.marker.read_bytes(), b'unchanged-stale-identity-marker')

    def test_child_timeout_retains_guard_without_false_recovery(self):
        with patch.object(guard, 'execute_ansible', side_effect=subprocess.TimeoutExpired('public-fixed-command', 1)), self.assertRaises(subprocess.TimeoutExpired):
            guard.run(self.plan, self.base / 'plan', 'a' * 64, True)
        self.assertTrue(self.reservation.exists())
        self.assertEqual(self.result()['status'], 'failed_closed')

    def test_reservation_first_write_failure_never_publishes_empty_canonical(self):
        with patch.object(guard, 'write_all', side_effect=OSError('injected')), patch.object(guard, 'execute_ansible') as child, self.assertRaises(OSError):
            guard.run(self.plan, self.base / 'plan', 'a' * 64, True)
        self.assertFalse(self.reservation.exists())
        self.assertEqual(sorted(p.name for p in self.base.iterdir()), ['marker', 'source'])
        child.assert_not_called()


if __name__ == '__main__':
    unittest.main()
