#!/usr/bin/env python3
"""Native one-phase coordinator; preserves the existing empty source mutex."""
from contextlib import ExitStack
import argparse
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
from types import ModuleType, SimpleNamespace

HERE = Path(__file__).resolve().parent
CONTRACT = None


class CleanupUnknown(RuntimeError):
    def __init__(self, primary_code):
        self.primary_code = primary_code
        super().__init__('reservation_cleanup_unknown')


def need(ok, code):
    if not ok: raise RuntimeError(code)


def identity(s):
    return (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid, s.st_nlink,
            s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def read_held(stack, path, pin, maximum, *, expected_uid=0, expected_gid=0):
    need(path.is_absolute() and path.resolve(strict=True) == path, 'control_path')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK); stack.callback(os.close, fd)
    before = os.fstat(fd)
    need(stat.S_ISREG(before.st_mode) and before.st_uid == expected_uid and before.st_gid == expected_gid and before.st_nlink == 1
         and stat.S_IMODE(before.st_mode) == 0o600 and 0 < before.st_size <= maximum, 'control_metadata')
    raw = os.pread(fd, before.st_size + 1, 0)
    def check():
        need(identity(before) == identity(os.fstat(fd)) == identity(path.lstat())
             and sha256(os.pread(fd, before.st_size + 1, 0)).hexdigest() == pin, 'control_changed')
    need(len(raw) == before.st_size and sha256(raw).hexdigest() == pin, 'control_sha256'); check()
    return raw, check


def module(stack, path, pin, *, expected_uid=0, expected_gid=0):
    raw, check = read_held(stack, path, pin, 131072, expected_uid=expected_uid, expected_gid=expected_gid)
    value = ModuleType('_datastore_native_' + path.stem); value.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), value.__dict__)
    return value, check


def launch(args, *, expected_uid=0, expected_gid=0):
    global CONTRACT
    # Explicit owner operands let ordinary filesystem tests use their real UID;
    # the executable entrypoint exposes no owner override and always requires 0.
    need(type(expected_uid) is int and expected_uid >= 0 and type(expected_gid) is int and expected_gid >= 0
         and os.geteuid() == expected_uid and args.apply == 'DATASTORE-CANDIDATE-IMAGES-ONLY-20261025-01', 'native_root_required')
    os.umask(0o077)
    with ExitStack() as stack:
        need(Path(args.control).parent == HERE/'controls'
             and re.fullmatch(r'[a-f0-9]{32}\.json', Path(args.control).name), 'operation_path')
        owners = {'expected_uid': expected_uid, 'expected_gid': expected_gid}
        raw, control_check = read_held(stack, Path(args.control), args.control_sha256, 2*1024**2, **owners)
        def unique(rows):
            result = {}
            for key, value in rows:
                need(key not in result, 'duplicate_control_key'); result[key] = value
            return result
        early = json.loads(raw, object_pairs_hook=unique, parse_constant=lambda _: need(False, 'control_constant'))
        W, worker_check = module(stack, HERE/'datastore_candidate_stage.py', early['source_sha256']['datastore_candidate_stage.py'], **owners)
        C, contract_check = module(stack, HERE/'datastore_candidate_contract.py', early['source_sha256']['datastore_candidate_contract.py'], **owners)
        CONTRACT = C
        op, _ = W.control(raw, args.control_sha256, early['phase'], 'bot', C)
        need(Path(args.control).stem == op['nonce'], 'operation_path')
        _, own_check = read_held(stack, HERE/'datastore_candidate_launch.py',
                                  op['source_sha256']['datastore_candidate_launch.py'], 131072, **owners)
        fences = op['fences']
        need(type(fences) is dict and set(fences) == {'marker', 'source_mutex'}, 'fence_fields')
        for key, value in fences.items():
            need(type(value) is list and len(value) == 9 and all(type(v) is int for v in value)
                 and value[0] > 0 and value[1] > 0 and value[2] == 0o100600
                 and value[3:6] == [expected_uid, expected_gid, 1] and value[6] >= 0 and value[7] >= 0 and value[8] >= 0
                 and (key != 'source_mutex' or value[6] == 0), 'fence_identity')
        need(not os.path.lexists(W.RESERVATION), 'reservation_preexisting')
        work = W.ROOTS['bot'] / (op['profile'] + '-' + op['nonce'])
        need(not os.path.lexists(work), 'attempt_preexisting')
        held = []
        for path, expected in ((W.MARKER, fences['marker']), (W.SOURCE_LOCK, fences['source_mutex'])):
            fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK); stack.callback(os.close, fd)
            need(list(identity(os.fstat(fd))) == expected == list(identity(path.lstat())), 'fence_changed')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB); held.append((path, fd, expected))
        reservation = None; payload = None; requested = [False]; known = None; cleanup = False; reservation_locked = False
        def fixed():
            control_check(); worker_check(); contract_check(); own_check()
            for path, fd, expected in held:
                need(list(identity(os.fstat(fd))) == expected == list(identity(path.lstat())), 'fence_changed')
        def owned():
            fixed(); reservation.check()
            need(os.pread(reservation.fd, len(payload)+1, 0) == payload, 'reservation_changed')
        def remove(*, complete):
            fixed(); reservation.check(); length = reservation.before[6]
            need((length == len(payload) if complete else 0 <= length <= len(payload))
                 and os.pread(reservation.fd, length+1, 0) == payload[:length], 'reservation_changed')
            os.unlink(W.RESERVATION)
            parent = os.open(W.RESERVATION.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try: os.fsync(parent)
            finally: os.close(parent)
            need(not os.path.lexists(W.RESERVATION), 'reservation_cleanup_unknown')
        try:
            fixed()
            payload = C.encode({'schema': 'datastore_candidate_reservation/1', 'operation_id': W.OPERATION,
                'nonce': op['nonce'], 'profile': op['profile'], 'phase': op['phase'], 'pid': os.getpid(),
                'operation_sha256': args.control_sha256, 'source_mutex': fences['source_mutex']})
            reservation = C.Output(W.RESERVATION, 2*1024**2, **owners)
            stack.callback(reservation.close)
            fcntl.flock(reservation.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            reservation_locked = True
            reservation.write(payload); reservation.finish()
            parent = os.open(W.RESERVATION.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try: os.fsync(parent)
            finally: os.close(parent)
            owned()
            call = SimpleNamespace(site='bot', phase=op['phase'], control=args.control,
                control_sha256=args.control_sha256, apply=args.apply)
            def mark_requested(): requested[0] = True
            known = W.execute(call, reservation_owned=True, fence_check=owned, on_rpc=mark_requested)
            expected_status = {'stage-web': 'archive_admitted_not_loaded', 'load-web': 'loaded_measured_not_accepted',
                               'load-native': 'both_sites_loaded_measured_not_accepted'}[op['phase']]
            need(type(known) is dict and known.get('security_accepted') is False and known.get('status') == expected_status
                 and type(known.get('receipt_sha256')) is str and C.HEX.fullmatch(known['receipt_sha256']), 'phase_not_verified')
            owned(); remove(complete=True); cleanup = True
        except Exception as error:
            if reservation is not None and payload is not None and not requested[0]:
                if not reservation_locked: raise CleanupUnknown(C.error_code(error)) from None
                try: remove(complete=False); cleanup = True
                except Exception: raise CleanupUnknown(C.error_code(error)) from None
            raise
        # Unknown RPCs retain the exact reservation. ExitStack closing FDs
        # releases only kernel flocks, never asserts daemon cancellation.
        result = {'schema': 'datastore_candidate_controller_result/1', 'status': known['status'],
            'profile': op['profile'], 'nonce': op['nonce'], 'operation_sha256': args.control_sha256,
            'source_mutex_preserved': True, 'reservation_removed': cleanup,
            'phase_receipt_sha256': known['receipt_sha256'], 'datastore_activation_performed': False,
            'security_accepted': False}
        try:
            raw = C.encode(result); output = C.Output(work/'controller-result.json', 2*1024**2, **owners)
            try: output.write(raw); output.finish()
            finally: output.close()
            directory = os.open(work, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try: os.fsync(directory)
            finally: os.close(directory)
        except Exception:
            return {'status': 'known_outcome_final_receipt_pending', 'verified_outcome': known['status'],
                    'source_mutex_preserved': True, 'reservation_removed': True, 'security_accepted': False}, 1
        fixed()
        return result, 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--control', required=True); parser.add_argument('--control-sha256', required=True)
    parser.add_argument('--apply', required=True); args = parser.parse_args()
    try: value, status = launch(args)
    except Exception as error:
        value, status = {'status': 'incomplete', 'code': 'datastore_phase_not_verified',
            'primary_failure': CONTRACT.error_code(error) if CONTRACT is not None else 'entry_not_verified',
            'retry_load_allowed': False, 'daemon_cancellation_proven': False, 'security_accepted': False}, 1
        if isinstance(error, CleanupUnknown): value['primary_failure'] = error.primary_code; value['code'] = 'reservation_cleanup_unknown'
    print(json.dumps(value, sort_keys=True)); return status


if __name__ == '__main__': raise SystemExit(main())
