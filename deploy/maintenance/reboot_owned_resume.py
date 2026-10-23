#!/usr/bin/env python3
"""Continue exactly one proven reserved reboot; never adopt or retry another.

Inactive systemd units may be garbage-collected. Persistent private-bus RefUnit
references keep their definitions available to the UNCHANGED canonical checks;
they never Start, enable, modify, or replace a unit.
"""
import argparse
from contextlib import contextmanager, ExitStack, redirect_stdout
from hashlib import sha256
import fcntl
import json
import os
from pathlib import Path
import signal
import stat
import sys
from types import ModuleType

PROJECT = Path('/root/trading-bot/trading_bot')
GUARD = PROJECT / 'deploy/maintenance/reboot_guard.py'
GUARD_SHA = '0a2af0aa7842fbc79c977fe0ddebc30d9f9b5b732b5e2438628c41392b0e676b'
SOURCE = '25ac5849eaa36164f4d8f9639dd5e7cb96d3305b'
NONCE = '672348cb-0415-47f4-95f2-a4702478451d'
DIGEST = '7807c3a490002b0bc932d114bb4a6fc4e3ed8464b070c3ee5db5b12739e419ed'
CONFIRM = 'RESUME-OWNED-KERNEL-20261023-02'


def require(ok, code):
    if not ok: raise RuntimeError(code)


def guard_module():
    fd = os.open(GUARD, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and info.st_nlink == 1 and
                not info.st_mode & 0o022 and info.st_size <= 65536, 'unsafe_canonical_guard')
        raw = os.read(fd, 65537)
        key = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        require(len(raw) == info.st_size and key(info) == key(os.fstat(fd)) == key(GUARD.lstat()) and
                sha256(raw).hexdigest() == GUARD_SHA, 'canonical_guard_changed')
    finally: os.close(fd)
    result = ModuleType('_exact_canonical_reboot_guard')
    result.__file__ = str(GUARD)
    exec(compile(raw, str(GUARD), 'exec'), result.__dict__)
    return result


@contextmanager
def pinned_units(g):
    import dbus
    bus = dbus.SystemBus(private=True)
    acquired = []
    try:
        manager = dbus.Interface(bus.get_object('org.freedesktop.systemd1', '/org/freedesktop/systemd1'),
                                 'org.freedesktop.systemd1.Manager')
        for unit in sorted(g.UNITS | {'docker.service'}):
            manager.RefUnit(unit, timeout=5)
            acquired.append(unit)
        yield
    finally:
        for unit in reversed(acquired):
            try: manager.UnrefUnit(unit, timeout=5)
            except Exception: pass
        bus.close()  # Closing the private connection releases any remaining refs.


def reserved_proof(g, plan):
    require(plan['nonce'] == NONCE and plan['source_revision'] == SOURCE, 'wrong_owned_operation')
    g.invariants(plan, before=True)
    status = g.state(plan, DIGEST)
    require(status['phase'] == 'reserved' and status['current_boot_id'] == plan['before_boot_id'] == g.boot_id(),
            'not_original_reserved_boot')
    require(type(status.get('owner_pid')) is int and status['owner_pid'] > 1 and
            not os.path.lexists(Path('/proc') / str(status['owner_pid'])), 'previous_owner_not_proven_dead')
    g.own_reservation(plan, DIGEST)
    require(not os.path.lexists(g.RESULT) and not os.path.lexists(g.B.PENDING), 'result_or_other_release_exists')
    require(g.B.document(g.TOKEN) == g.token_payload(plan, DIGEST) and
            g.B.read(g.GOLDEN_INHIBITOR, private=False) == g.GOLDEN_INHIBITOR_BYTES, 'owned_quiescence_gate_changed')
    for unit in plan['units']:
        actual = g.properties(unit['name'])
        expected = 'disabled' if unit['enabled'] == 'enabled' else unit['enabled']
        require(actual['UnitFileState'] == expected and actual['ActiveState'] in {'inactive', 'failed'}, 'unit_not_quiescent')
    g.B.runtime(plan, 'quiesced')
    require(not os.path.lexists(continuation_path(g)), 'continuation_already_attempted')
    return status


def continuation_path(g):
    return g.STATE.with_name(g.OPERATION + '.owned-continuation.json')


def bind(g, plan):
    require(plan['nonce'] == NONCE and plan['source_revision'] == SOURCE and
            plan['operation_id'] == 'security-kernel-reboot-20261023-02', 'wrong_fixed_manifest')


def run(g, mode, confirmation=None):
    plan = g.load(g.MANIFEST, DIGEST)
    bind(g, plan)
    with pinned_units(g):
        if mode in {'restore', 'cleanup'}:
            require(confirmation == CONFIRM, 'explicit_owned_apply_required')
            intent = g.B.document(continuation_path(g))
            require(intent['schema'] == 'security_kernel_reboot_owned_continuation/1' and
                    intent['operation_id'] == g.OPERATION and intent['nonce'] == NONCE and intent['manifest_sha256'] == DIGEST and
                    intent['source_revision'] == SOURCE and intent['guard_sha256'] == GUARD_SHA and
                    intent['before_boot_id'] == plan['before_boot_id'] != g.boot_id() and
                    intent['reservation_inode'] > 0, 'continuation_or_new_boot_invalid')
            if mode == 'restore':
                require(g.own_reservation(plan, DIGEST)['inode'] == intent['reservation_inode'], 'restore_reservation_changed')
                require(g.state(plan, DIGEST)['phase'] == 'reboot_requested', 'restore_phase_invalid')
                g.execute(plan, DIGEST, before=False)
            else:
                result = g.B.document(g.RESULT)
                require(result['status'] == 'completed' and result['nonce'] == NONCE and
                        result['manifest_sha256'] == DIGEST and not os.path.lexists(g.B.RESERVATION), 'cleanup_not_completed')
                argv = sys.argv
                try:
                    sys.argv = [str(GUARD), 'cleanup-controller', '--manifest', str(g.MANIFEST),
                                '--manifest-sha256', DIGEST, '--apply']
                    require(g.main() == 0, 'original_cleanup_failed')
                finally: sys.argv = argv
            return
        require(mode in {'check', 'resume'}, 'unknown_mode')
        reserved_proof(g, plan)
        with ExitStack() as stack:
            g.lock_current(stack)
            g.B.read(g.B.RESERVATION, maximum=8192)
            fd = os.open(g.B.RESERVATION, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
            stack.callback(os.close, fd)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            require(g.B.identity(os.fstat(fd)) == g.B.identity(g.B.RESERVATION.lstat()) and
                    os.fstat(fd).st_ino == g.own_reservation(plan, DIGEST)['inode'], 'reservation_inode_changed')
            old = reserved_proof(g, plan)
            if mode == 'check': return
            require(confirmation == CONFIRM and g.now() < g.deadline(g.CUTOFF), 'explicit_apply_or_cutoff_invalid')
            require(not os.path.lexists(continuation_path(g)), 'continuation_already_attempted')
            g.atomic(continuation_path(g), {'schema': 'security_kernel_reboot_owned_continuation/1',
                     'operation_id': g.OPERATION, 'nonce': NONCE, 'manifest_sha256': DIGEST,
                     'source_revision': SOURCE, 'guard_sha256': GUARD_SHA,
                     'before_boot_id': plan['before_boot_id'], 'previous_owner_pid': old['owner_pid'],
                     'owner_pid': os.getpid(), 'reservation_device': os.fstat(fd).st_dev,
                     'reservation_inode': os.fstat(fd).st_ino}, exclusive=True)
            # Take responsibility only AFTER both complete quiescent proofs.
            # No timestamp/freshness, phase, inventory or authority is refreshed.
            current = g.state(plan, DIGEST)
            require(current == old, 'reserved_state_changed_before_owner_transfer')
            current['owner_pid'] = os.getpid()
            g.atomic(g.STATE, current)
            with open(os.devnull, 'w') as output, redirect_stdout(output):
                g.phase_check(plan, DIGEST, 'quiesced')  # Original full check, no skip/patch.
            g.advance(plan, DIGEST, 'reboot_requested')
            g.B.command(['/usr/bin/systemctl', 'reboot'])  # Exactly one, never retry.
            signal.alarm(60)
            signal.pause()
            raise RuntimeError('reboot_did_not_terminate_owned_continuation')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('check', 'resume', 'restore', 'cleanup'), nargs='?', default='check')
    parser.add_argument('--apply')
    args = parser.parse_args()
    try: run(guard_module(), args.mode, args.apply)
    except Exception:
        print('{"status":"refused","code":"owned_continuation_refused"}')
        return 1
    print(json.dumps({'status': 'passed', 'mode': args.mode, 'nonce': NONCE, 'manifest_sha256': DIGEST}))
    return 0


if __name__ == '__main__': raise SystemExit(main())
