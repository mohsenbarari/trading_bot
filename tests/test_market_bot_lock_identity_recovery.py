"""Hermetic filesystem/control fixtures: never open production configuration."""
from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import fcntl
from hashlib import sha256
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import recover_market_bot_lock_identity as recovery
from scripts import quiesce_production_legacy_market_collectors as handoff


class BotLockIdentityRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bot-lock-recovery-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'secure'
        self.root.mkdir(mode=0o700)
        self.lock = self.root / 'queue-cutover-artifacts/production-release.lock'
        self.journal = self.root / 'market-pipeline-cutover/bot-legacy-handoff-4ef8e6dc.json'
        self.auth = self.root / 'release-control/production-4ef8e6dc-cutover/web-bluegreen.json'
        self.pre = self.auth.with_name('web-mirror-bot-legacy-authority.json')
        self.wal = self.journal.with_name('bot-lock-identity-recovery-4ef8e6dc.json')
        self.source = self.root / '.production-runtime-source.lock'
        self.two_host = self.root / 'two-host-state.json'
        self.write(self.source, b'')
        self.write(self.lock, b'{}\n')
        info = self.lock.stat()
        self.units = {unit: {'unit_sha256': 'a' * 64, 'active': False, 'enabled': False, 'source_codes': sorted(handoff.UNIT_SOURCE_OWNERSHIP[unit])} for unit in handoff.ROLE_UNITS['bot']}
        marker = {'schema': 'market_pipeline_maintenance_lock/1.0', 'environment': 'production', 'host_role': 'bot', 'release_sha': recovery.RELEASE, 'nonce_sha256': 'c' * 64, 'journal_path_sha256': sha256(str(self.journal).encode()).hexdigest(), 'device': info.st_dev + 1, 'inode': info.st_ino + 1}
        entries = {}
        for account in (1, 2):
            role = f'market-capture-account{account}'
            payload = {'contract': 'market_capture_authority/1.0', 'authority': 'container', 'role': role, 'release_sha': recovery.RELEASE, 'authorized_at_utc': '2026-08-31T04:59:00Z'}
            entries[role] = {'path': f'/srv/market/sessions/account{account}/authority-container.json', 'prior_sha256': 'd' * 64, 'target_sha256': sha256(recovery.canonical(payload)).hexdigest(), 'target_payload': payload}
        binding = {'authorized_at_utc': '2026-08-31T04:59:00Z', 'entries': entries}
        now = datetime.now(timezone.utc)
        pre = {'schema': handoff.SCHEMA, 'status': 'AUTHORITY_TRANSFERRING', 'host_role': 'bot', 'release_sha': recovery.RELEASE, 'created_at_utc': '2026-08-31T04:39:00Z', 'verified_at_utc': (now - timedelta(seconds=10)).isoformat(), 'prior_units': self.units, 'current_units': self.units, 'maintenance_lock': marker, 'primary_verification_sha256': None, 'primary_rollback_sha256': None, 'authority_transfer': {'bluegreen_journal_path_sha256': sha256(str(self.auth).encode()).hexdigest(), 'prepared_bluegreen_journal_sha256': 'b' * 64, 'authorization_bluegreen_journal_sha256': None, 'marker_authority_sha256': sha256(recovery.canonical(binding)).hexdigest()}, 'state_deleted': False, 'secrets_disclosed': False}
        self.write(self.pre, pre)
        self.pre_sha = sha256(self.pre.read_bytes()).hexdigest()
        auth = {'schema': 'market_pipeline_bluegreen_upgrade/1.0', 'journal_contract_revision': 2, 'status': 'captures_authorized', 'role': 'web', 'release_sha': recovery.RELEASE, 'bot_legacy_collector_receipt_sha256': self.pre_sha, 'bot_legacy_authority_transfer': pre['authority_transfer'], 'legacy_authority_prepared_journal_sha256': 'b' * 64, 'marker_transition': dict(binding, status='COMPLETE'), 'product_authority_changed': False, 'state_deleted': False, 'secrets_disclosed': False}
        self.write(self.auth, auth)
        self.auth_sha = sha256(self.auth.read_bytes()).hexdigest()
        journal = json.loads(json.dumps(pre))
        journal['status'] = 'AUTHORITY_TRANSFERRED'
        journal['verified_at_utc'] = (now - timedelta(seconds=5)).isoformat()
        journal['authority_transfer']['authorization_bluegreen_journal_sha256'] = self.auth_sha
        self.write(self.journal, journal)
        self.write(self.lock, marker)
        self.before_lock, self.before_journal = self.lock.read_bytes(), self.journal.read_bytes()
        self.args = {'expected_lock_sha': sha256(self.before_lock).hexdigest(), 'expected_journal_sha': sha256(self.before_journal).hexdigest(), 'device': info.st_dev, 'inode': info.st_ino, 'source_lock': self.source}
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in {'ROOT': self.root, 'LOCK': self.lock, 'JOURNAL': self.journal, 'AUTH': self.auth, 'PRETRANSFER': self.pre, 'WAL': self.wal, 'RESERVATION': self.root / 'release-control/private-primary-controller.lock', 'TWO_HOST_STATE': self.two_host, 'AUTH_SHA': self.auth_sha, 'PRETRANSFER_SHA': self.pre_sha}.items():
            self.stack.enter_context(patch.object(recovery, name, value))
        self.stack.enter_context(patch.object(handoff, 'OPERATION_LOCK_PATH', self.lock))
        self.stack.enter_context(patch.object(handoff, 'APPROVED_ROOT', self.journal.parent))
        self.host = self.stack.enter_context(patch.object(recovery, 'native_host'))
        self.owners = self.stack.enter_context(patch.object(recovery, 'no_other_owners'))
        self.quiesced = self.stack.enter_context(patch.object(handoff, '_assert_quiesced', return_value=self.units))

    def write(self, path, value):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_bytes(recovery.canonical(value) if isinstance(value, dict) else value)
        path.chmod(0o600)

    def apply(self, **extra):
        return recovery.recover(mode='apply', confirm=recovery.APPLY_CONFIRM, **dict(self.args, **extra))

    def resume(self):
        return recovery.recover(mode='resume', confirm=recovery.RESUME_CONFIRM, **self.args)

    def assert_original(self):
        self.assertEqual(self.lock.read_bytes(), self.before_lock)
        self.assertEqual(self.journal.read_bytes(), self.before_journal)

    def assert_committed(self):
        self.assertEqual((self.lock.stat().st_dev, self.lock.stat().st_ino), (self.args['device'], self.args['inode']))
        marker = json.loads(self.lock.read_bytes())
        journal = json.loads(self.journal.read_bytes())
        old_marker, old_journal = json.loads(self.before_lock), json.loads(self.before_journal)
        old_marker.update(device=self.args['device'], inode=self.args['inode'])
        old_journal['maintenance_lock'] = old_marker
        self.assertEqual(marker, old_marker)
        self.assertEqual(journal, old_journal)
        self.assertEqual(json.loads(self.wal.read_bytes())['status'], 'COMMITTED')
        with handoff._maintenance_guard(self.journal, recovery.RELEASE, 'bot') as binding:
            self.assertEqual(binding, marker)
        self.assertEqual(handoff.validate_transferred_handoff(journal=self.journal, expected_journal_sha256=sha256(self.journal.read_bytes()).hexdigest(), release_sha=recovery.RELEASE, host_role='bot', expected_maintenance_lock=marker), journal)

    def test_check_is_default_and_does_not_write(self):
        self.assertEqual(recovery.recover(**self.args)['status'], 'RECOVERABLE')
        self.assert_original()
        self.assertFalse(self.wal.exists())

    def test_apply_preserves_all_other_fields_and_normal_guards(self):
        self.assertEqual(self.apply()['status'], 'COMMITTED')
        self.assert_committed()
        before = self.lock.read_bytes(), self.journal.read_bytes()
        self.assertEqual(self.resume()['status'], 'COMMITTED')
        self.assertEqual(before, (self.lock.read_bytes(), self.journal.read_bytes()))

    def test_each_durable_crash_point_resumes(self):
        for phase in ('reservation', 'wal', 'journal', 'truncate', 'marker', 'commit', 'released'):
            with self.subTest(phase=phase):
                # Each scenario has an independent temporary control plane.
                case = type(self)('runTest')
                case.setUp()
                try:
                    def crash(name):
                        if name == phase:
                            raise OSError('synthetic_crash')
                    with patch.object(recovery, 'checkpoint', side_effect=crash):
                        with self.assertRaises(OSError):
                            case.apply()
                    self.assertEqual(case.resume()['status'], 'COMMITTED')
                    case.assert_committed()
                finally:
                    case.doCleanups()

    def test_marker_prefix_short_write_interruption_resumes(self):
        real_write = os.write
        marker_fd = None
        def short(fd, raw):
            nonlocal marker_fd
            info = os.fstat(fd)
            if (info.st_dev, info.st_ino) == (self.args['device'], self.args['inode']):
                if marker_fd is None:
                    marker_fd = fd
                    return real_write(fd, raw[:11])
                raise OSError('interrupted_marker_write')
            return real_write(fd, raw)
        with patch.object(recovery.os, 'write', side_effect=short):
            with self.assertRaises(OSError):
                self.apply()
        self.assertEqual(len(self.lock.read_bytes()), 11)
        self.assertEqual(self.resume()['status'], 'COMMITTED')
        self.assert_committed()

    def test_short_writes_are_completed_and_zero_progress_fails(self):
        real_write = os.write
        with patch.object(recovery.os, 'write', side_effect=lambda fd, data: real_write(fd, data[:7])):
            self.apply()
        self.assert_committed()
        with patch.object(recovery.os, 'write', return_value=0):
            with self.assertRaisesRegex(recovery.RecoveryError, 'no_progress'):
                recovery.write_all(-1, b'x')

    def test_wrong_confirm_digest_inode_or_source_fence_refuses_before_writes(self):
        for extra in ({'confirm': 'wrong'}, {'expected_lock_sha': '0' * 64}, {'expected_journal_sha': '0' * 64}, {'inode': self.args['inode'] + 10}, {'source_lock': self.auth.with_name('.production-runtime-source.lock')}):
            with self.subTest(extra=tuple(extra)):
                args = dict(self.args, mode='apply', confirm=recovery.APPLY_CONFIRM)
                args.update(extra)
                with self.assertRaises((recovery.RecoveryError, OSError)):
                    recovery.recover(**args)
                self.assert_original()
                self.assertFalse(self.wal.exists())

    def test_native_host_failure_refuses(self):
        self.host.side_effect = recovery.RecoveryError('namespace_invalid')
        with self.assertRaisesRegex(recovery.RecoveryError, 'namespace'):
            self.apply()
        self.assert_original()

    def test_active_legacy_owners_refuse_before_reservation_or_wal(self):
        self.quiesced.side_effect = handoff.CollectorHandoffError('collector_handoff_overlap_not_quiesced')
        with self.assertRaisesRegex(handoff.CollectorHandoffError, 'overlap_not_quiesced'):
            self.apply()
        self.assert_original()
        self.assertFalse(self.wal.exists())
        self.assertFalse(recovery.RESERVATION.exists())

    def test_retained_proof_mutation_refuses(self):
        for path in (self.auth, self.pre):
            with self.subTest(path=path.name):
                original = path.read_bytes()
                self.write(path, original + b' ')
                with self.assertRaisesRegex(recovery.RecoveryError, 'proof_digest'):
                    self.apply()
                self.write(path, original)
        self.assert_original()

    def test_nonissuer_authority_and_inventory_drift_refuse(self):
        for key, value in [('nonce_sha256', 'f' * 64), ('status', 'PRIMARY_COMMITTED'), ('verified_at_utc', '2099-01-01T00:00:00Z')]:
            with self.subTest(key=key):
                journal = json.loads(self.before_journal)
                if key == 'nonce_sha256':
                    journal['maintenance_lock'][key] = value
                else:
                    journal[key] = value
                raw = recovery.canonical(journal)
                self.write(self.journal, raw)
                with self.assertRaises((recovery.RecoveryError, handoff.CollectorHandoffError)):
                    self.apply(expected_journal_sha=sha256(raw).hexdigest())
                self.write(self.journal, self.before_journal)
        self.quiesced.return_value = {}
        with self.assertRaisesRegex(recovery.RecoveryError, 'inventory_drift'):
            self.apply()
        self.assert_original()

    def test_pending_controller_queue_and_two_host_operations_refuse(self):
        paths = [self.root / 'release-control/private-primary-controller.lock', self.two_host, self.lock.parent / 'production-queue-phase-pending.json']
        for path in paths:
            with self.subTest(path=path.name):
                self.write(path, {'schema_version': 1, 'environment': 'production', 'secrets_disclosed': False, 'status': 'recovery_failed'})
                with self.assertRaisesRegex(recovery.RecoveryError, 'pending'):
                    self.apply()
                path.unlink()
        self.assert_original()
        for index, status in enumerate(sorted(recovery.TERMINAL)):
            self.write(self.lock.parent / f'production-queue-phase-old-{index}.json', {'schema_version': 1, 'environment': 'production', 'secrets_disclosed': False, 'status': status})
        self.assertEqual(recovery.recover(**self.args)['status'], 'RECOVERABLE')

    def test_owned_reservation_excludes_controller_and_resumes_without_wal(self):
        def interrupt(name):
            if name == 'reservation':
                with recovery.RESERVATION.open('r+b') as contender:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
                raise OSError('interrupted_before_wal')
        with patch.object(recovery, 'checkpoint', side_effect=interrupt):
            with self.assertRaises(OSError):
                self.apply()
        self.assertFalse(self.wal.exists())
        self.assert_original()
        self.resume()
        self.assertFalse(recovery.RESERVATION.exists())
        self.assert_committed()

    def test_foreign_and_unidentifiable_reservations_never_adopted_or_removed(self):
        for raw in (b'', b'{"schema":"production_private_primary_choreography_lock/1.0"}\n', b'{"schema":"production_market_bot_identity_recovery_reservation/1.0"}\n'):
            with self.subTest(raw=raw):
                self.write(recovery.RESERVATION, raw)
                with self.assertRaises((recovery.RecoveryError, ValueError)):
                    self.resume()
                self.assertEqual(recovery.RESERVATION.read_bytes(), raw)
                self.assertFalse(self.wal.exists())
                self.assert_original()
                recovery.RESERVATION.unlink()

    def test_controller_winning_atomic_publication_race_is_not_unlinked(self):
        real_publish = recovery.publish_noreplace
        competitor = []
        payload = b'{"schema":"production_private_primary_choreography_lock/1.0"}\n'
        def race(temporary, path):
            if path == recovery.RESERVATION:
                self.write(path, payload)
                other = os.open(path, os.O_RDWR)
                competitor.append(other)
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return real_publish(temporary, path)
        try:
            with patch.object(recovery, 'publish_noreplace', side_effect=race):
                with self.assertRaises(FileExistsError):
                    self.apply()
            self.assertEqual(recovery.RESERVATION.read_bytes(), payload)
            self.assert_original()
            self.assertFalse(self.wal.exists())
        finally:
            for fd in competitor:
                os.close(fd)

    def test_unpublished_reservation_crash_and_partial_write_leave_no_guard(self):
        real_write = os.write
        for failure in ('zero', 'partial', 'before_publish'):
            with self.subTest(failure=failure):
                calls = []
                def fail_write(fd, raw):
                    calls.append(fd)
                    if failure == 'zero':
                        return 0
                    if failure == 'partial':
                        if len(calls) == 1:
                            return real_write(fd, raw[:9])
                        raise OSError('reservation_partial_write')
                    return real_write(fd, raw)
                def fail_checkpoint(name):
                    if failure == 'before_publish' and name == 'reservation_pre_publish':
                        raise OSError('reservation_before_publish')
                with patch.object(recovery.os, 'write', side_effect=fail_write), patch.object(recovery, 'checkpoint', side_effect=fail_checkpoint):
                    with self.assertRaises((recovery.RecoveryError, OSError)):
                        self.apply()
                self.assertFalse(recovery.RESERVATION.exists())
                self.assertFalse(self.wal.exists())
                self.assert_original()

    def test_new_wal_publication_is_truly_exclusive(self):
        original_publish = recovery.publish_noreplace
        foreign = b'{"foreign":"wal"}\n'
        def collide(temporary, path):
            if path == self.wal:
                self.write(path, foreign)
            return original_publish(temporary, path)
        with patch.object(recovery, 'publish_noreplace', side_effect=collide):
            with self.assertRaises(FileExistsError):
                self.apply()
        self.assertEqual(self.wal.read_bytes(), foreign)
        self.assert_original()

    def test_lock_and_source_flock_exclude_concurrent_operation(self):
        for path in (self.lock, self.source):
            with self.subTest(path=path.name), path.open('r+b') as stream:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaises(BlockingIOError):
                    self.apply()
        self.assert_original()

    def test_symlink_hardlink_fifo_permissions_and_bounded_input_refuse(self):
        original = self.pre.read_bytes()
        for kind in ('symlink', 'hardlink', 'fifo', 'permissions', 'oversize', 'duplicate', 'nonobject'):
            with self.subTest(kind=kind):
                self.pre.unlink()
                if kind == 'symlink':
                    self.pre.symlink_to(self.auth)
                elif kind == 'hardlink':
                    os.link(self.auth, self.pre)
                elif kind == 'fifo':
                    os.mkfifo(self.pre, 0o600)
                elif kind == 'permissions':
                    self.write(self.pre, original)
                    self.pre.chmod(0o644)
                else:
                    self.write(self.pre, b'x' * 65537 if kind == 'oversize' else (b'{"x":1,"x":2}' if kind == 'duplicate' else b'[]'))
                with self.assertRaises((recovery.RecoveryError, OSError)):
                    self.apply()
                self.pre.unlink()
                self.write(self.pre, original)
        self.assert_original()

    def test_unrecognized_resume_bytes_journal_and_tampered_wal_refuse(self):
        with patch.object(recovery, 'checkpoint', side_effect=lambda name: (_ for _ in ()).throw(OSError()) if name == 'truncate' else None):
            with self.assertRaises(OSError):
                self.apply()
        self.write(self.lock, b'not-a-target-prefix')
        with self.assertRaisesRegex(recovery.RecoveryError, 'unrecognized'):
            self.resume()
        self.write(self.lock, b'')
        self.write(self.journal, self.before_journal + b' ')
        with self.assertRaisesRegex(recovery.RecoveryError, 'unrecognized'):
            self.resume()
        wal = json.loads(self.wal.read_bytes())
        wal['target_lock'] = 'eA=='
        self.write(self.wal, wal)
        with self.assertRaisesRegex(recovery.RecoveryError, 'target_drift'):
            self.resume()

    def test_apply_cannot_reuse_wal_and_resume_requires_wal(self):
        with self.assertRaisesRegex(recovery.RecoveryError, 'wal_missing'):
            self.resume()
        self.apply()
        with self.assertRaisesRegex(recovery.RecoveryError, 'requires_resume'):
            self.apply()


class NativeOwnerInventoryTests(unittest.TestCase):
    def test_unprivileged_fixture_roundtrip_without_sudo_or_skipping(self):
        def exercise():
            case = BotLockIdentityRecoveryTests('runTest')
            case.setUp()
            try:
                case.test_apply_preserves_all_other_fields_and_normal_guards()
            finally:
                case.doCleanups()
        if os.geteuid() != 0:
            exercise()  # CI runs this branch with runner-owned secure fixtures.
            return
        def unprivileged_child():
            os.setgroups([])
            os.setgid(65534)
            os.setuid(65534)
            exercise()
        process = multiprocessing.get_context('fork').Process(target=unprivileged_child)
        process.start()
        process.join(timeout=15)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
            self.fail('unprivileged fixture timed out')
        self.assertEqual(process.exitcode, 0)

    def test_real_deleted_old_inode_open_descriptor_is_not_mistaken_for_stale(self):
        with tempfile.TemporaryDirectory(prefix='old-inode-fd-') as directory:
            path = Path(directory) / 'old-lock'
            path.write_bytes(b'public fixture')
            with path.open('rb') as stream:
                info = os.fstat(stream.fileno())
                path.unlink()
                original_iterdir = Path.iterdir
                def own_proc(path):
                    return iter([Path('/proc') / str(os.getpid())]) if path == Path('/proc') else original_iterdir(path)
                with patch.object(recovery.Path, 'iterdir', own_proc), self.assertRaisesRegex(recovery.RecoveryError, 'still_open'):
                    recovery.no_other_owners((info.st_dev, info.st_ino), (-1, -1), set())

    def test_incomplete_proc_visibility_fails_closed(self):
        def denied(path):
            if path == Path('/proc'):
                return iter([Path('/proc/1')])
            raise PermissionError('synthetic_hidepid')
        with patch.object(recovery.Path, 'iterdir', denied), self.assertRaises(PermissionError):
            recovery.no_other_owners((-1, -1), (-2, -2), set())

    def test_namespace_guard_rejects_nested_pid_and_user_namespaces(self):
        for nested in ('pid', 'user'):
            def fake_stat(path):
                return type('Info', (), {'st_dev': 1, 'st_ino': 2 if str(path) == f'/proc/self/ns/{nested}' else 1})()
            with self.subTest(namespace=nested), patch.object(recovery.os, 'stat', side_effect=fake_stat), patch.object(recovery.os, 'geteuid', return_value=0), patch.object(recovery.Path, 'readlink', return_value=Path('/usr/lib/systemd/systemd')):
                with self.assertRaisesRegex(recovery.RecoveryError, 'namespace'):
                    recovery.native_host()


if __name__ == '__main__':
    unittest.main()
