#!/usr/bin/env python3
"""Finite cipher-only cold-engine receiver on the pinned final Finland filesystem."""
from contextlib import ExitStack
from datetime import datetime, timezone
from hashlib import sha256
import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys

ROOT = Path('/srv/trading-bot/security-backups')
DEVICE = 2049
OWNER = 0
UUID = 'ce366399-3430-441e-8a6f-6f5621f0c7a2'
MOUNT_SOURCE = '/dev/sda1'
MOUNT_TARGET = '/'
MAXIMUM = 10 * 1024**3
RESERVE = 8 * 1024**3
CONTROL_ALLOWANCE = 1024**2
CONTROL_MAXIMUM = 64 * 1024
BLOCK = 1024**2
CIPHER = 'engine-store-backup.enc'
RECEIPT = 'offhost-receipt.json'
SCHEMA = 'security_web_engine_cold_offhost/1'


class Refusal(RuntimeError):
    pass


def require(ok, code):
    if not ok:
        raise Refusal(code)


def fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def binding(info):
    return dict(zip(('device', 'inode', 'uid', 'gid', 'mode', 'nlink', 'size_bytes', 'mtime_ns', 'ctime_ns'),
                    fingerprint(info)))


def directory_binding(info):
    return {key: binding(info)[key] for key in ('device', 'inode', 'uid', 'gid', 'mode')}


def exact(left, right):
    if type(left) is not type(right):
        return False
    if type(right) is dict:
        return set(left) == set(right) and all(exact(left[key], value) for key, value in right.items())
    if type(right) is list:
        return len(left) == len(right) and all(exact(a, b) for a, b in zip(left, right))
    return left == right


class Clock:
    def __init__(self, deadline):
        require(type(deadline) is str and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z', deadline),
                'offhost_deadline_invalid')
        try:
            self.deadline = datetime.fromisoformat(deadline.replace('Z', '+00:00'))
        except ValueError:
            raise Refusal('offhost_deadline_invalid') from None
        require(0 < self.remaining() <= 7200, 'offhost_deadline_invalid')

    def remaining(self):
        return (self.deadline - datetime.now(timezone.utc)).total_seconds()

    def check(self):
        require(self.remaining() > 0, 'offhost_deadline_expired')


def arguments(operation, nonce, manifest, size=None, digest=None):
    require(type(operation) is str and re.fullmatch(r'[a-z0-9][a-z0-9-]{0,47}', operation), 'offhost_operation_invalid')
    require(type(nonce) is str and re.fullmatch(r'[a-f0-9]{32}', nonce), 'offhost_nonce_invalid')
    require(type(manifest) is str and re.fullmatch(r'[a-f0-9]{64}', manifest), 'offhost_manifest_invalid')
    if size is not None or digest is not None:
        require(type(size) is int and 0 < size <= MAXIMUM, 'offhost_cipher_size_invalid')
        require(type(digest) is str and re.fullmatch(r'[a-f0-9]{64}', digest), 'offhost_cipher_digest_invalid')
    return 'security-web-engine-cold-' + operation + '-' + nonce


def mount_proof():
    result = subprocess.run(['/usr/bin/findmnt', '--json', '--target', str(ROOT),
                             '--output', 'TARGET,SOURCE,FSTYPE,UUID'],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            check=False, timeout=5, env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'})
    require(result.returncode == 0 and len(result.stdout) <= 16384, 'offhost_mount_probe_failed')
    try:
        value = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise Refusal('offhost_mount_probe_failed') from None
    expected = {'filesystems': [{'target': MOUNT_TARGET, 'source': MOUNT_SOURCE, 'fstype': 'ext4', 'uuid': UUID}]}
    require(exact(value, expected), 'offhost_mount_identity_changed')
    return expected['filesystems'][0]


class Destination:
    def __init__(self, name, clock):
        self.name, self.clock = name, clock
        self.stack = ExitStack()
        self.root = self.operation = -1
        self.root_identity = self.operation_identity = None

    def __enter__(self):
        try:
            require(os.geteuid() == OWNER and ROOT.is_absolute() and ROOT.resolve(strict=True) == ROOT,
                    'offhost_root_identity_changed')
            for path in ROOT.parents:
                info = path.lstat()
                require(stat.S_ISDIR(info.st_mode) and info.st_uid in {0, OWNER} and not info.st_mode & 0o022,
                        'offhost_root_parent_invalid')
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
            self.root = os.open(ROOT, flags); self.stack.callback(os.close, self.root)
            self.refresh_root()
            self.mount = mount_proof()
            self.check()
            return self
        except BaseException:
            self.stack.close()
            raise

    def refresh_root(self):
        info = os.fstat(self.root)
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == OWNER and stat.S_IMODE(info.st_mode) == 0o700
                and info.st_dev == DEVICE, 'offhost_root_identity_changed')
        self.root_identity = fingerprint(info)

    def refresh_operation(self):
        info = os.fstat(self.operation)
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == OWNER and stat.S_IMODE(info.st_mode) == 0o700
                and info.st_dev == DEVICE, 'offhost_operation_identity_changed')
        self.operation_identity = fingerprint(info)

    def absent(self):
        try:
            os.stat(self.name, dir_fd=self.root, follow_symlinks=False)
        except FileNotFoundError:
            return
        raise Refusal('offhost_destination_exists')

    def open_operation(self, create=False):
        self.check()
        if create:
            self.absent()
            os.mkdir(self.name, 0o700, dir_fd=self.root)
            os.fsync(self.root)
            self.refresh_root()
        self.operation = os.open(self.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                 dir_fd=self.root)
        self.stack.callback(os.close, self.operation)
        self.refresh_operation()
        self.check()

    def check(self, *, probe_mount=False):
        self.clock.check()
        require(fingerprint(os.fstat(self.root)) == self.root_identity == fingerprint(ROOT.lstat()),
                'offhost_root_identity_changed')
        if self.operation >= 0:
            info = os.stat(self.name, dir_fd=self.root, follow_symlinks=False)
            require(fingerprint(os.fstat(self.operation)) == self.operation_identity == fingerprint(info),
                    'offhost_operation_identity_changed')
        if probe_mount:
            require(exact(mount_proof(), self.mount), 'offhost_mount_identity_changed')

    def free(self):
        value = os.fstatvfs(self.root)
        require(not value.f_flag & os.ST_RDONLY, 'offhost_mount_readonly')
        return value.f_bavail * value.f_frsize

    def __exit__(self, kind, value, traceback):
        try:
            if kind is None:
                self.check(probe_mount=True)
        finally:
            self.stack.close()


def file_check(destination, descriptor, name, expected=None):
    current = os.fstat(descriptor)
    require(stat.S_ISREG(current.st_mode) and current.st_uid == OWNER and current.st_nlink == 1
            and stat.S_IMODE(current.st_mode) == 0o600 and current.st_dev == DEVICE,
            'offhost_file_metadata_invalid')
    require(fingerprint(current) == fingerprint(os.stat(name, dir_fd=destination.operation, follow_symlinks=False)),
            'offhost_file_identity_changed')
    if expected is not None:
        require(fingerprint(current) == expected, 'offhost_file_identity_changed')
    destination.check()
    return current


def write_all(descriptor, data):
    while data:
        written = os.write(descriptor, data)
        require(written > 0, 'offhost_write_failed')
        data = data[written:]


def digest_file(destination, descriptor, size, expected):
    identity = fingerprint(file_check(destination, descriptor, CIPHER))
    result, count = sha256(), 0
    while chunk := os.pread(descriptor, min(BLOCK, size + 1 - count), count):
        count += len(chunk)
        require(count <= size, 'offhost_cipher_stream_excess')
        result.update(chunk)
        file_check(destination, descriptor, CIPHER, identity)
    require(count == size and result.hexdigest() == expected, 'offhost_cipher_integrity_failed')
    return file_check(destination, descriptor, CIPHER, identity)


def payload(destination, operation, nonce, manifest, size, digest, deadline, cipher_info):
    return {'schema': SCHEMA, 'operation_id': operation, 'nonce': nonce, 'manifest_sha256': manifest,
            'deadline_utc': deadline, 'root': str(ROOT), 'directory': destination.name,
            'root_binding': directory_binding(os.fstat(destination.root)),
            'directory_binding': directory_binding(os.fstat(destination.operation)), 'mount': destination.mount,
            'ciphertext_name': CIPHER, 'ciphertext_size_bytes': size, 'ciphertext_sha256': digest,
            'ciphertext_binding': binding(cipher_info), 'fsync_completed': True,
            'plaintext_materialized_offhost': False, 'private_key_contents_read': False,
            'production_mutated': False, 'secrets_disclosed': False}


def capacity(operation, nonce, manifest_sha256, deadline_utc):
    name = arguments(operation, nonce, manifest_sha256)
    with Destination(name, Clock(deadline_utc)) as target:
        target.absent()
        available = target.free()
        result = {'status': 'capacity_verified', 'operation_id': operation, 'nonce': nonce,
                  'manifest_sha256': manifest_sha256, 'deadline_utc': deadline_utc,
                  'root': str(ROOT), 'mount': target.mount, 'device': DEVICE,
                  'free_bytes': available, 'reserve_bytes': RESERVE, 'control_allowance_bytes': CONTROL_ALLOWANCE,
                  'max_cipher_bytes': min(MAXIMUM, max(0, available - RESERVE - CONTROL_ALLOWANCE)),
                  'files_created': False, 'plaintext_materialized_offhost': False}
        target.check(probe_mount=True); target.absent()
        return result


def transfer(operation, nonce, manifest_sha256, cipher_size, cipher_sha256, deadline_utc, source=None, *, verify=False):
    require(cipher_size is not None and cipher_sha256 is not None, 'offhost_cipher_identity_missing')
    name = arguments(operation, nonce, manifest_sha256, cipher_size, cipher_sha256)
    with Destination(name, Clock(deadline_utc)) as target:
        if not verify:
            require(source is not None, 'offhost_cipher_source_missing')
            require(target.free() >= cipher_size + RESERVE + CONTROL_ALLOWANCE, 'offhost_capacity_insufficient')
        target.open_operation(create=not verify)
        receipt_fd = -1
        if verify:
            require(set(os.listdir(target.operation)) == {CIPHER, RECEIPT}, 'offhost_directory_inventory_invalid')
            receipt_fd = os.open(RECEIPT, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                                 dir_fd=target.operation)
            target.stack.callback(os.close, receipt_fd)
            receipt_info = file_check(target, receipt_fd, RECEIPT)
            require(0 < receipt_info.st_size <= CONTROL_MAXIMUM, 'offhost_receipt_invalid')
            raw = os.pread(receipt_fd, CONTROL_MAXIMUM + 1, 0)
            require(len(raw) == receipt_info.st_size, 'offhost_receipt_invalid')
            file_check(target, receipt_fd, RECEIPT, fingerprint(receipt_info))
        flags = os.O_RDONLY if verify else os.O_RDWR | os.O_CREAT | os.O_EXCL
        cipher_fd = os.open(CIPHER, flags | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, 0o600,
                            dir_fd=target.operation)
        target.stack.callback(os.close, cipher_fd)
        if not verify:
            target.refresh_operation()
        initial = file_check(target, cipher_fd, CIPHER)
        if verify:
            require(initial.st_size == cipher_size, 'offhost_cipher_integrity_failed')
        if not verify:
            result, count = sha256(), 0
            while chunk := source.read(min(BLOCK, cipher_size + 1 - count)):
                target.check()
                require(type(chunk) is bytes, 'offhost_cipher_stream_invalid')
                count += len(chunk)
                require(count <= cipher_size, 'offhost_cipher_stream_excess')
                result.update(chunk); write_all(cipher_fd, chunk)
                target.check()
                require(target.free() >= RESERVE, 'offhost_capacity_insufficient')
            require(count == cipher_size and result.hexdigest() == cipher_sha256, 'offhost_cipher_integrity_failed')
            os.fsync(cipher_fd)
        info = digest_file(target, cipher_fd, cipher_size, cipher_sha256)
        expected = payload(target, operation, nonce, manifest_sha256, cipher_size, cipher_sha256, deadline_utc, info)
        target.check(probe_mount=True)
        require(target.free() >= RESERVE, 'offhost_capacity_insufficient')
        if verify:
            try:
                pairs = json.loads(raw, object_pairs_hook=lambda rows: rows)
                def decode(value):
                    if type(value) is list:
                        require(all(type(row) is tuple and len(row) == 2 for row in value), 'offhost_receipt_invalid')
                        require(len({row[0] for row in value}) == len(value), 'offhost_receipt_invalid')
                        return {key: decode(item) for key, item in value}
                    return value
                record = decode(pairs)
            except (ValueError, TypeError):
                raise Refusal('offhost_receipt_invalid') from None
            require(exact(record, expected), 'offhost_receipt_changed')
            file_check(target, receipt_fd, RECEIPT, fingerprint(receipt_info))
            require(os.pread(receipt_fd, CONTROL_MAXIMUM + 1, 0) == raw, 'offhost_receipt_changed')
        else:
            os.fsync(target.operation)
            encoded = (json.dumps(expected, sort_keys=True, separators=(',', ':')) + '\n').encode()
            require(len(encoded) <= CONTROL_MAXIMUM, 'offhost_receipt_bound')
            receipt_fd = os.open(RECEIPT, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                                 0o600, dir_fd=target.operation)
            target.stack.callback(os.close, receipt_fd)
            target.refresh_operation()
            write_all(receipt_fd, encoded); os.fsync(receipt_fd); os.fsync(target.operation)
            receipt_info = file_check(target, receipt_fd, RECEIPT)
            require(os.pread(receipt_fd, CONTROL_MAXIMUM + 1, 0) == encoded, 'offhost_receipt_changed')
            file_check(target, receipt_fd, RECEIPT, fingerprint(receipt_info))
        file_check(target, cipher_fd, CIPHER, fingerprint(info))
        require(set(os.listdir(target.operation)) == {CIPHER, RECEIPT}, 'offhost_directory_inventory_invalid')
        target.check(probe_mount=True)
        require(target.free() >= RESERVE, 'offhost_capacity_insufficient')
        return {'status': 'verified' if verify else 'received', **expected}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('capacity', 'receive', 'verify'))
    parser.add_argument('--operation', required=True)
    parser.add_argument('--nonce', required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--deadline-utc', required=True)
    parser.add_argument('--cipher-size', type=int)
    parser.add_argument('--cipher-sha256')
    args = parser.parse_args(argv)
    try:
        clock = Clock(args.deadline_utc)
        signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(Refusal('offhost_deadline_expired')))
        signal.alarm(max(1, math.ceil(clock.remaining())))
        if args.mode == 'capacity':
            result = capacity(args.operation, args.nonce, args.manifest_sha256, args.deadline_utc)
        else:
            require(args.cipher_size is not None and args.cipher_sha256 is not None, 'offhost_cipher_identity_missing')
            result = transfer(args.operation, args.nonce, args.manifest_sha256, args.cipher_size,
                              args.cipher_sha256, args.deadline_utc,
                              None if args.mode == 'verify' else sys.stdin.buffer, verify=args.mode == 'verify')
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as error:
        reason = str(error) if isinstance(error, Refusal) else 'offhost_backup_refused'
        print(json.dumps({'status': 'refused', 'code': reason, 'plaintext_materialized_offhost': False,
                          'private_key_contents_read': False, 'production_mutated': False,
                          'secrets_disclosed': False}, sort_keys=True), file=sys.stderr)
        return 1
    finally:
        signal.alarm(0)


def guarded_argv(control, original, connection):
    """Parse only the fixed argument grammar allowed by the expiring SSH key."""
    require(type(control) is dict and set(control) == {
        'schema','operation_id','nonce','expires_utc','source_ip','destination_root','lock_identity'},
        'offhost_guard_control_invalid')
    require(control['schema'] == 'web_engine_cold_ssh_guard/1'
            and control['source_ip'] == '65.109.220.59'
            and control['destination_root'] == str(ROOT), 'offhost_guard_control_invalid')
    arguments(control['operation_id'], control['nonce'], '0'*64)
    identity = control['lock_identity']
    require(type(identity) is dict and set(identity) == {'device','inode','uid','gid','mode'}
            and all(type(v) is int for v in identity.values())
            and identity['device'] > 0 and identity['inode'] > 0
            and identity['uid'] == OWNER and identity['gid'] == os.getegid()
            and identity['mode'] == stat.S_IFREG | 0o600, 'offhost_guard_lock_invalid')
    require(type(original) is str and 0 < len(original) <= 4096
            and original.isascii() and all(32 <= ord(c) < 127 for c in original),
            'offhost_guard_command_invalid')
    peer = connection.split() if type(connection) is str else []
    require(len(peer) == 4 and peer[0] == control['source_ip'], 'offhost_guard_peer_invalid')
    tokens = original.split()
    require(len(tokens) in {10,14} and tokens[0] == 'engine-cold'
            and tokens[1] in {'capacity','receive','verify'}
            and tokens[2:10:2] == ['--operation','--nonce','--manifest-sha256','--deadline-utc'],
            'offhost_guard_command_invalid')
    mode, operation, nonce, manifest, deadline = tokens[1],tokens[3],tokens[5],tokens[7],tokens[9]
    require(operation == control['operation_id'] and nonce == control['nonce'],
            'offhost_guard_owner_invalid')
    arguments(operation, nonce, manifest)
    expiry, clock = Clock(control['expires_utc']),Clock(deadline)
    require(clock.deadline <= expiry.deadline, 'offhost_guard_deadline_invalid')
    if mode == 'capacity':
        require(len(tokens) == 10, 'offhost_guard_command_invalid')
    else:
        require(len(tokens) == 14 and tokens[10] == '--cipher-size' and tokens[12] == '--cipher-sha256'
                and tokens[11].isascii() and tokens[11].isdigit() and len(tokens[11]) <= 11,
                'offhost_guard_command_invalid')
        arguments(operation, nonce, manifest, int(tokens[11]), tokens[13])
    return tokens[1:]


def guard_file(path, maximum):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == OWNER and before.st_nlink == 1
                and stat.S_IMODE(before.st_mode) == 0o600 and 0 < before.st_size <= maximum,
                'offhost_guard_file_invalid')
        raw = os.pread(fd, maximum+1, 0)
        require(len(raw) == before.st_size and fingerprint(before) == fingerprint(os.fstat(fd))
                == fingerprint(path.lstat()), 'offhost_guard_file_changed')
        return fd,raw,fingerprint(before)
    except BaseException:
        os.close(fd)
        raise


def guard_bound(fd, path, identity, raw):
    require(fingerprint(os.fstat(fd)) == identity == fingerprint(path.lstat())
            and os.pread(fd,len(raw)+1,0) == raw
            and fingerprint(os.fstat(fd)) == identity == fingerprint(path.lstat()),
            'offhost_guard_file_changed')


def guard_control(path):
    require(path.is_absolute() and path.resolve(strict=True) == path, 'offhost_guard_path_invalid')
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == OWNER and not info.st_mode & 0o022,
                'offhost_guard_path_invalid')
    fd,raw,identity = guard_file(path, CONTROL_MAXIMUM)
    def unique(pairs):
        result = {}
        for key,value in pairs:
            require(key not in result, 'offhost_guard_control_invalid');result[key] = value
        return result
    try:
        try:
            control = json.loads(raw, object_pairs_hook=unique)
        except (ValueError, TypeError):
            raise Refusal('offhost_guard_control_invalid') from None
        return control,sha256(raw).hexdigest(),fd,identity,raw
    except BaseException:
        os.close(fd)
        raise


def entrypoint(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] != '--ssh-guard':
        return main(argv)
    lock = control_fd = -1
    try:
        require(len(argv) == 2, 'offhost_guard_command_invalid')
        path = Path(argv[1])
        control,digest,control_fd,control_identity,control_raw = guard_control(path)
        allowed = guarded_argv(control, os.environ.get('SSH_ORIGINAL_COMMAND',''),
                               os.environ.get('SSH_CONNECTION',''))
        lock_path = path.with_suffix('.lock')
        lock,lock_raw,lock_identity = guard_file(lock_path,128)
        require(lock_raw == (digest+'\n').encode(), 'offhost_guard_lock_invalid')
        require(exact(directory_binding(os.fstat(lock)),control['lock_identity']), 'offhost_guard_lock_invalid')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refusal('offhost_guard_busy') from None
        guard_bound(control_fd,path,control_identity,control_raw)
        guard_bound(lock,lock_path,lock_identity,lock_raw)
        result = main(allowed)
        guard_bound(control_fd,path,control_identity,control_raw)
        guard_bound(lock,lock_path,lock_identity,lock_raw)
        return result
    except Exception as error:
        reason = str(error) if isinstance(error, Refusal) else 'offhost_guard_refused'
        print(json.dumps({'status':'refused','code':reason,'production_mutated':False,
                          'plaintext_materialized_offhost':False}),file=sys.stderr)
        return 1
    finally:
        if lock >= 0:
            os.close(lock)
        if control_fd >= 0:
            os.close(control_fd)


if __name__ == '__main__':
    raise SystemExit(entrypoint())
