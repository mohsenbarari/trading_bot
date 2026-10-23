#!/usr/bin/env python3
"""Re-attest one relocated Bot maintenance inode; never transfer authority.

Default: read-only eligibility check. Only the retained 4ef8 authorization and
its exact pre-transfer Bot receipt can authorize this incident-specific repair.
Normal deployment/handoff guards are deliberately unchanged.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import ctypes
from datetime import datetime, timedelta, timezone
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
import stat
import sys
import tempfile
import time

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import quiesce_production_legacy_market_collectors as handoff

ROOT = Path('/root/secure-envs/trading-bot')
RELEASE = '4ef8e6dcb2d361b763bd8b72dc730c1f978f564a'
AUTH_SHA = '330f8efac0efdff959d6c47ee104729fc7170752b33f16800abe11ac0a674271'
PRETRANSFER_SHA = 'ead6a73d2b1bd2050757ac0c8ce8def3b4d293d713576d50628448f69e803d4a'
LOCK = ROOT / 'queue-cutover-artifacts/production-release.lock'
JOURNAL = ROOT / 'market-pipeline-cutover/bot-legacy-handoff-4ef8e6dc.json'
AUTH = ROOT / 'release-control/production-4ef8e6dc-cutover/web-bluegreen.json'
PRETRANSFER = AUTH.with_name('web-mirror-bot-legacy-authority.json')
WAL = JOURNAL.with_name('bot-lock-identity-recovery-4ef8e6dc.json')
RESERVATION = ROOT / 'release-control/private-primary-controller.lock'
TWO_HOST_STATE = Path('/var/lib/trading-bot/production-release/two-host-release-state.json')
WAL_SCHEMA = 'production_market_bot_lock_identity_recovery/1.0'
APPLY_CONFIRM = 'repair-production-bot-lock-filesystem-identity'
RESUME_CONFIRM = 'resume-production-bot-lock-filesystem-identity'
TERMINAL = {'applied', 'redeployed', 'rolled_back', 'failed_recovered', 'interrupted_recovered'}


class RecoveryError(RuntimeError):
    """A stable, content-free refusal; no secret/control payload in errors."""


def require(condition, reason):
    if not condition:
        raise RecoveryError(reason)


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':')) + '\n').encode('ascii')


def document(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'duplicate_document_key')
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=unique)
    require(isinstance(value, dict), 'document_not_object')
    return value


def parents(path):
    require(path.is_absolute() and path.is_relative_to(ROOT), 'path_outside_control_root')
    info = ROOT.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and not info.st_mode & 0o022, 'unsafe_control_root')
    cursor = ROOT
    for name in path.parent.relative_to(ROOT).parts:
        cursor /= name
        info = cursor.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and not info.st_mode & 0o022, 'unsafe_control_parent')
    require(path.parent.resolve(strict=True) == path.parent, 'redirected_control_parent')


def identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink, info.st_mode, info.st_uid, info.st_gid


def read_fd(fd, path, maximum):
    before = os.fstat(fd)
    require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid() and stat.S_IMODE(before.st_mode) == 0o600 and before.st_nlink == 1, 'unsafe_control_file')
    require(before.st_size <= maximum, 'control_file_too_large')
    os.lseek(fd, 0, os.SEEK_SET)
    chunks, length = [], 0
    while True:
        chunk = os.read(fd, min(8192, maximum + 1 - length))
        if not chunk:
            break
        chunks.append(chunk)
        length += len(chunk)
        require(length <= maximum, 'control_file_too_large')
    after, named = os.fstat(fd), path.lstat()
    require(identity(before) == identity(after) == identity(named), 'control_file_changed')
    return b''.join(chunks)


@contextmanager
def opened(path, maximum, *, lock=False):
    parents(path)
    fd = os.open(path, (os.O_RDWR if lock else os.O_RDONLY) | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        if lock:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield fd, read_fd(fd, path, maximum)
    finally:
        os.close(fd)


def read(path, maximum=65536):
    with opened(path, maximum) as (_, raw):
        return raw


def native_host():
    # Call only through the trusted native-host administration channel. These
    # checks reject common containers/namespaces; no magic namespace inode IDs.
    require(sys.platform == 'linux' and os.geteuid() == 0, 'native_root_host_required')
    require(Path('/proc/1/exe').readlink().name == 'systemd', 'native_systemd_host_required')
    for namespace in ('pid', 'user'):
        own, init = os.stat(f'/proc/self/ns/{namespace}'), os.stat(f'/proc/1/ns/{namespace}')
        require((own.st_dev, own.st_ino) == (init.st_dev, init.st_ino), 'host_namespace_mismatch')
    require(not any(os.path.lexists(path) for path in ('/.dockerenv', '/run/.containerenv', '/run/systemd/container')) and not os.environ.get('container'), 'container_context_refused')


def no_other_owners(old, current, allowed_fds):
    """Only inspect FD metadata, never read FD contents or process environments."""
    deadline, count = time.monotonic() + 10, 0
    for process in Path('/proc').iterdir():
        if not process.name.isdecimal():
            continue
        try:
            for entry in (process / 'fd').iterdir():
                count += 1
                require(count <= 100000 and time.monotonic() <= deadline, 'proc_inventory_bound_exceeded')
                try:
                    info = entry.stat()
                except FileNotFoundError:
                    continue  # A process/descriptor exited during the snapshot.
                pair = info.st_dev, info.st_ino
                if pair in {old, current}:
                    require(int(process.name) == os.getpid() and int(entry.name) in allowed_fds and pair == current, 'maintenance_inode_still_open')
        except (FileNotFoundError, ProcessLookupError):
            continue
    with open('/proc/locks', 'rb') as stream:
        raw = stream.read(2 * 1024 * 1024 + 1)
    require(len(raw) <= 2 * 1024 * 1024, 'kernel_lock_inventory_too_large')
    for line in raw.decode('ascii').splitlines():
        fields = line.split()
        for index, field in enumerate(fields):
            if field.count(':') != 2:
                continue
            major, minor, inode = field.split(':')
            pair = os.makedev(int(major, 16), int(minor, 16)), int(inode)
            if pair in {old, current}:
                require(pair == current and fields[index - 1] == str(os.getpid()), 'maintenance_inode_still_locked')


def no_pending_operations(*, owned_reservation=False):
    paths = (AUTH.parent / 'private-primary-controller.lock', TWO_HOST_STATE)
    if not owned_reservation:
        paths += (RESERVATION,)
    for path in paths:
        require(not os.path.lexists(path), 'pending_production_operation')
    for index, path in enumerate(LOCK.parent.glob('production-queue-phase-*.json')):
        require(index < 1000, 'queue_journal_inventory_bound_exceeded')
        phase = document(read(path))
        require(phase.get('schema_version') == 1 and phase.get('environment') == 'production' and phase.get('secrets_disclosed') is False and phase.get('status') in TERMINAL, 'pending_or_invalid_queue_phase')


def prove(marker_raw, journal_raw, pretransfer):
    marker, journal = document(marker_raw), document(journal_raw)
    require(pretransfer.name == 'web-mirror-bot-legacy-authority.json' and pretransfer.is_relative_to(ROOT / 'release-control'), 'pretransfer_evidence_path_invalid')
    auth_raw, pre_raw = read(AUTH), read(pretransfer)
    require(sha256(auth_raw).hexdigest() == AUTH_SHA and sha256(pre_raw).hexdigest() == PRETRANSFER_SHA, 'retained_proof_digest_mismatch')
    auth, pre = document(auth_raw), document(pre_raw)
    require(set(marker) == {'schema', 'environment', 'host_role', 'release_sha', 'nonce_sha256', 'journal_path_sha256', 'device', 'inode'}, 'lock_schema_invalid')
    require(marker['schema'] == 'market_pipeline_maintenance_lock/1.0' and marker['environment'] == 'production' and marker['host_role'] == 'bot' and marker['release_sha'] == RELEASE, 'lock_authority_invalid')
    require(all(type(marker[k]) is int and marker[k] > 0 for k in ('device', 'inode')), 'lock_identity_invalid')
    require(handoff.HEX64.fullmatch(str(marker['nonce_sha256'])) and marker['journal_path_sha256'] == sha256(str(JOURNAL).encode()).hexdigest(), 'lock_binding_invalid')
    handoff._validate_transferred_payload(journal, expected_maintenance_lock=marker)
    require(journal.get('schema') == handoff.SCHEMA and journal.get('host_role') == 'bot' and journal.get('release_sha') == RELEASE, 'journal_authority_invalid')
    require(auth.get('schema') == 'market_pipeline_bluegreen_upgrade/1.0' and auth.get('journal_contract_revision') == 2 and auth.get('status') == 'captures_authorized' and auth.get('role') == 'web' and auth.get('release_sha') == RELEASE, 'authorization_schema_invalid')
    require(auth.get('product_authority_changed') is False and auth.get('state_deleted') is False and auth.get('secrets_disclosed') is False, 'authorization_side_effect_invalid')
    authority = journal['authority_transfer']
    require(auth.get('bot_legacy_collector_receipt_sha256') == PRETRANSFER_SHA and auth.get('bot_legacy_authority_transfer') == pre.get('authority_transfer'), 'copied_receipt_binding_invalid')
    require(pre.get('status') == 'AUTHORITY_TRANSFERRING' and pre['authority_transfer'].get('authorization_bluegreen_journal_sha256') is None, 'pretransfer_state_invalid')
    expected = document(pre_raw)
    expected['status'] = 'AUTHORITY_TRANSFERRED'
    expected['authority_transfer']['authorization_bluegreen_journal_sha256'] = AUTH_SHA
    expected['verified_at_utc'] = journal['verified_at_utc']
    require(expected == journal, 'non_issuer_journal_mutation')
    verified = datetime.fromisoformat(journal['verified_at_utc'].replace('Z', '+00:00'))
    prior = datetime.fromisoformat(pre['verified_at_utc'].replace('Z', '+00:00'))
    require(verified.tzinfo is not None and prior.tzinfo is not None and prior <= verified <= datetime.now(timezone.utc) + timedelta(seconds=30), 'transfer_timestamp_invalid')
    require(authority['bluegreen_journal_path_sha256'] == sha256(str(AUTH).encode()).hexdigest() and authority['prepared_bluegreen_journal_sha256'] == auth.get('legacy_authority_prepared_journal_sha256'), 'authorization_path_or_prepared_binding_invalid')
    transition = auth['marker_transition']
    require(transition.get('status') == 'COMPLETE' and set(transition['entries']) == {'market-capture-account1', 'market-capture-account2'}, 'marker_transition_invalid')
    binding = {'authorized_at_utc': transition['authorized_at_utc'], 'entries': {role: {k: entry[k] for k in ('path', 'prior_sha256', 'target_sha256', 'target_payload')} for role, entry in sorted(transition['entries'].items())}}
    require(sha256(canonical(binding)).hexdigest() == authority['marker_authority_sha256'], 'marker_authority_digest_invalid')
    require(handoff._assert_quiesced('bot') == journal['current_units'], 'legacy_inventory_drift')
    return marker, journal


def write_all(fd, raw):
    cursor = 0
    while cursor < len(raw):
        written = os.write(fd, raw[cursor:])
        require(written > 0, 'short_write_no_progress')
        cursor += written


def atomic(path, raw, *, exclusive=False):
    parents(path)
    require(not exclusive or not os.path.lexists(path), 'recovery_wal_already_exists')
    fd, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        try:
            write_all(fd, raw)
            os.fsync(fd)
        finally:
            os.close(fd)
        if exclusive:
            publish_noreplace(temporary, path)
        else:
            os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def checkpoint(_name):
    """Fault-injection seam; no production behavior."""


def publish_noreplace(temporary, path):
    """Publish a fully fsynced inode without replacing any race winner."""
    library = ctypes.CDLL(None, use_errno=True)
    function = getattr(library, 'renameat2', None)
    require(function is not None, 'atomic_noreplace_unavailable')
    function.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    function.restype = ctypes.c_int
    if function(-100, os.fsencode(temporary), -100, os.fsencode(path), 1) != 0:
        raise OSError(ctypes.get_errno(), 'atomic_noreplace_failed')


@contextmanager
def reserve(expected, *, mode, recorded=None):
    """Use the existing controller path without forging its controller schema."""
    created, temporary = False, None
    parents(RESERVATION)
    if not os.path.lexists(RESERVATION):
        require(recorded is None, 'owned_reservation_missing')
        if mode == 'check':
            yield None
            return
        fd, temporary = tempfile.mkstemp(prefix=f'.{RESERVATION.name}.recovery.', dir=RESERVATION.parent)
        created = True
    else:
        require(mode != 'apply', 'existing_reservation_requires_resume')
        fd = os.open(RESERVATION, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        # A new inode is locked and fully durable BEFORE no-replace publish.
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if created:
            info = os.fstat(fd)
            payload = dict(expected, schema='production_market_bot_identity_recovery_reservation/1.0', nonce=secrets.token_hex(32), guard_device=info.st_dev, guard_inode=info.st_ino)
            write_all(fd, canonical(payload))
            os.fsync(fd)
            checkpoint('reservation_pre_publish')
            publish_noreplace(temporary, RESERVATION)
            directory = os.open(RESERVATION.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        raw = read_fd(fd, RESERVATION, 8192)
        payload = document(raw)
        info = os.fstat(fd)
        require(set(payload) == set(expected) | {'schema', 'nonce', 'guard_device', 'guard_inode'} and all(payload[k] == v for k, v in expected.items()), 'foreign_controller_reservation')
        require(payload['schema'] == 'production_market_bot_identity_recovery_reservation/1.0' and handoff.HEX64.fullmatch(str(payload['nonce'])) and (payload['guard_device'], payload['guard_inode']) == (info.st_dev, info.st_ino), 'reservation_identity_invalid')
        require(recorded is None or raw == base64.b64decode(recorded, validate=True), 'reservation_wal_binding_invalid')
        yield fd, raw
    finally:
        os.close(fd)
        if temporary is not None and os.path.lexists(temporary):
            os.unlink(temporary)


def recover(*, mode='check', expected_lock_sha, expected_journal_sha, device, inode, source_lock, pretransfer=None, confirm=None):
    require(mode in {'check', 'apply', 'resume'}, 'mode_invalid')
    require(mode == 'check' or confirm == (APPLY_CONFIRM if mode == 'apply' else RESUME_CONFIRM), 'confirmation_invalid')
    require(all(handoff.HEX64.fullmatch(value) for value in (expected_lock_sha, expected_journal_sha)) and type(device) is int and type(inode) is int and device > 0 and inode > 0, 'expected_binding_invalid')
    require(source_lock == ROOT / '.production-runtime-source.lock', 'source_fence_path_invalid')
    pretransfer = PRETRANSFER if pretransfer is None else pretransfer
    native_host()
    with opened(LOCK, 8192, lock=True) as (fd, current_raw), opened(source_lock, 8192, lock=True) as (source_fd, _):
        current = os.fstat(fd).st_dev, os.fstat(fd).st_ino
        require(current == (device, inode), 'current_inode_drift')
        wal = None
        if os.path.lexists(WAL):
            require(mode in {'check', 'resume'}, 'existing_wal_requires_resume')
            wal = document(read(WAL, 262144))
            require(set(wal) == {'schema', 'status', 'release_sha', 'device', 'inode', 'before_lock', 'before_journal', 'target_lock', 'target_journal', 'source_lock', 'authorization_sha256', 'pretransfer_sha256', 'pretransfer_path', 'reservation'}, 'recovery_wal_schema_invalid')
            require(wal['schema'] == WAL_SCHEMA and wal['status'] in {'PREPARED', 'COMMITTED'} and wal['release_sha'] == RELEASE and (wal['device'], wal['inode']) == current and wal['source_lock'] == str(source_lock) and wal['authorization_sha256'] == AUTH_SHA and wal['pretransfer_sha256'] == PRETRANSFER_SHA and wal['pretransfer_path'] == str(pretransfer), 'recovery_wal_binding_invalid')
            before_lock, before_journal, target_lock, target_journal = (base64.b64decode(wal[k], validate=True) for k in ('before_lock', 'before_journal', 'target_lock', 'target_journal'))
            require(len(before_lock) <= 8192 and len(before_journal) <= 65536 and len(target_lock) <= 8192 and len(target_journal) <= 65536, 'recovery_wal_payload_too_large')
        else:
            require(mode != 'resume' or os.path.lexists(RESERVATION), 'resume_wal_missing')
            before_lock, before_journal = current_raw, read(JOURNAL)
        require(sha256(before_lock).hexdigest() == expected_lock_sha and sha256(before_journal).hexdigest() == expected_journal_sha, 'predecessor_digest_mismatch')
        marker, journal = prove(before_lock, before_journal, pretransfer)
        old = marker['device'], marker['inode']
        require(old != current, 'identity_recovery_not_needed')
        updated_marker = dict(marker, device=device, inode=inode)
        updated_journal = dict(journal, maintenance_lock=updated_marker)
        expected_targets = canonical(updated_marker), canonical(updated_journal)
        if wal is None:
            target_lock, target_journal = expected_targets
        require((target_lock, target_journal) == expected_targets, 'recovery_target_drift')
        live_journal = read(JOURNAL)
        require((live_journal == before_journal and current_raw == before_lock) or (live_journal == target_journal and (current_raw == before_lock or target_lock.startswith(current_raw))), 'unrecognized_recovery_state')
        if wal and wal['status'] == 'COMMITTED':
            require((live_journal, current_raw) == (target_journal, target_lock), 'committed_recovery_drift')
        no_other_owners(old, current, {fd})
        no_pending_operations(owned_reservation=os.path.lexists(RESERVATION) and (wal is not None or mode == 'resume'))
        if wal and wal['status'] == 'COMMITTED' and not os.path.lexists(RESERVATION):
            no_pending_operations()
            return {'status': 'COMMITTED', 'mode': mode, 'authority_changed': False}
        intent = {'release_sha': RELEASE, 'device': device, 'inode': inode, 'source_lock': str(source_lock), 'pretransfer_path': str(pretransfer), 'expected_lock_sha': expected_lock_sha, 'expected_journal_sha': expected_journal_sha, 'target_lock_sha': sha256(target_lock).hexdigest(), 'target_journal_sha': sha256(target_journal).hexdigest(), 'wal_path_sha': sha256(str(WAL).encode()).hexdigest(), 'authorization_sha256': AUTH_SHA, 'pretransfer_sha256': PRETRANSFER_SHA}
        with reserve(intent, mode=mode, recorded=wal['reservation'] if wal else None) as owned:
            no_pending_operations(owned_reservation=owned is not None)
            if mode == 'check':
                return {'status': 'RECOVERABLE' if wal is None else wal['status'], 'mode': mode, 'authority_changed': False}
            guard_fd, guard_raw = owned
            checkpoint('reservation')
            if wal is None:
                wal = {'schema': WAL_SCHEMA, 'status': 'PREPARED', 'release_sha': RELEASE, 'device': device, 'inode': inode, 'source_lock': str(source_lock), 'authorization_sha256': AUTH_SHA, 'pretransfer_sha256': PRETRANSFER_SHA, 'pretransfer_path': str(pretransfer), 'reservation': base64.b64encode(guard_raw).decode('ascii')}
                wal.update({k: base64.b64encode(v).decode('ascii') for k, v in zip(('before_lock', 'before_journal', 'target_lock', 'target_journal'), (before_lock, before_journal, target_lock, target_journal))})
                atomic(WAL, canonical(wal), exclusive=True)
                checkpoint('wal')
            if live_journal != target_journal:
                no_pending_operations(owned_reservation=True)
                atomic(JOURNAL, target_journal)
                checkpoint('journal')
            prove(before_lock, before_journal, pretransfer)  # Never renew receipt timestamps.
            no_pending_operations(owned_reservation=True)
            no_other_owners(old, current, {fd})
            require((os.fstat(fd).st_dev, os.fstat(fd).st_ino) == current == (LOCK.lstat().st_dev, LOCK.lstat().st_ino), 'current_inode_drift')
            require(read_fd(guard_fd, RESERVATION, 8192) == guard_raw, 'reservation_ownership_lost')
            if current_raw != target_lock:
                os.ftruncate(fd, 0)
                os.fsync(fd)
                checkpoint('truncate')
                os.lseek(fd, 0, os.SEEK_SET)
                write_all(fd, target_lock)
                os.fsync(fd)
                checkpoint('marker')
            require(read_fd(fd, LOCK, 8192) == target_lock and read(JOURNAL) == target_journal, 'recovery_readback_failed')
            handoff._maintenance_lock(JOURNAL, RELEASE, 'bot')
            require(handoff._assert_quiesced('bot') == journal['current_units'], 'legacy_inventory_drift')
            wal['status'] = 'COMMITTED'
            atomic(WAL, canonical(wal))
            checkpoint('commit')
            require(read_fd(guard_fd, RESERVATION, 8192) == guard_raw, 'reservation_ownership_lost')
            os.unlink(RESERVATION)
            directory = os.open(RESERVATION.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            checkpoint('released')
            return {'status': 'COMMITTED', 'mode': mode, 'authority_changed': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('check', 'apply', 'resume'), default='check')
    parser.add_argument('--expected-lock-sha', required=True)
    parser.add_argument('--expected-journal-sha', required=True)
    parser.add_argument('--device', type=int, required=True)
    parser.add_argument('--inode', type=int, required=True)
    parser.add_argument('--source-lock', type=Path, required=True)
    parser.add_argument('--pretransfer', type=Path, help='Exact retained ead6 receipt, staged byte-for-byte under release-control; never synthesized.')
    parser.add_argument('--confirm')
    try:
        print(json.dumps(recover(**vars(parser.parse_args(argv))), sort_keys=True))
        return 0
    except (RecoveryError, handoff.CollectorHandoffError, OSError, ValueError, KeyError, TypeError):
        print('bot_lock_identity_recovery_refused', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
