#!/usr/bin/env python3
"""Receive/verify ONE encrypted Web-engine backup; no keys or plaintext.

Called over the already configured strict SSH lane. Never overwrites/adopts a
directory, resumes a partial upload, deletes data, or emits exception strings.
"""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import signal
import stat
import sys

ROOT = Path('/srv/trading-bot/backups')
MAXIMUM = 10 * 1024**3
FLOOR = 2 * 1024**3


def require(ok):
    if not ok:
        raise RuntimeError('offhost_backup_refused')


def metadata(path, private=True):
    require(path.is_absolute() and path.resolve(strict=False) == path)
    value = path.lstat()
    require(value.st_uid == os.geteuid() and not value.st_mode & (0o077 if private else 0o022))
    require(stat.S_ISDIR(value.st_mode) or stat.S_ISREG(value.st_mode) and value.st_nlink == 1)
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid in {0, os.geteuid()} and not info.st_mode & 0o022)


def write_all(fd, data):
    while data:
        count = os.write(fd, data)
        require(count > 0)
        data = data[count:]


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def fingerprint(value):
    return (value.st_dev, value.st_ino, value.st_uid, value.st_gid, value.st_mode,
            value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def read_receipt(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid()
                and before.st_nlink == 1 and not before.st_mode & 0o077 and before.st_size <= 4096)
        value = os.read(fd, 4097)
        require(len(value) <= 4096 and fingerprint(before) == fingerprint(os.fstat(fd)) == fingerprint(path.lstat()))
        return json.loads(value)
    finally:
        os.close(fd)


def transfer(operation, nonce, size, digest, manifest_digest, source, *, verify=False, root=ROOT):
    require(re.fullmatch('[A-Za-z0-9._-]{1,80}', operation) is not None)
    require(re.fullmatch('[0-9a-f]{32}', nonce) is not None)
    require(all(re.fullmatch('[0-9a-f]{64}', v) for v in (digest, manifest_digest)))
    require(type(size) is int and 0 < size <= MAXIMUM)
    metadata(root)
    directory = root / ('security-web-engine-' + operation + '-' + nonce)
    artifact, receipt = directory / 'engine.enc', directory / 'received.json'
    expected = {'schema': 'web_engine_offhost/1', 'operation_id': operation, 'nonce': nonce,
                'manifest_sha256': manifest_digest, 'ciphertext_sha256': digest,
                'ciphertext_size_bytes': size, 'fsync_completed': True,
                'plaintext_materialized_offhost': False}
    if verify:
        metadata(directory); metadata(artifact); metadata(receipt)
        require(read_receipt(receipt) == expected)
        fd = os.open(artifact, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    else:
        available = os.statvfs(root)
        require(available.f_bavail * available.f_frsize >= size + FLOOR)
        os.mkdir(directory, 0o700)  # exclusive; an interrupted upload is NOT adopted
        sync_directory(root)
        fd = os.open(artifact, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    result, count = sha256(), 0
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid() and before.st_nlink == 1
                and not before.st_mode & 0o077)
        if verify:
            require(before.st_size == size)
        while chunk := (os.read(fd, min(65536, size + 1 - count)) if verify else source.read(min(65536, size + 1 - count))):
            count += len(chunk)
            require(count <= size)
            result.update(chunk)
            if not verify:
                write_all(fd, chunk)
        require(count == size and result.hexdigest() == digest)
        if not verify:
            os.fsync(fd)
        after = os.fstat(fd)
        current = artifact.lstat()
        require((after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                == (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns))
        if verify:
            require((before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                    == (after.st_size, after.st_mtime_ns, after.st_ctime_ns))
    finally:
        os.close(fd)
    if not verify:
        fd = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            write_all(fd, (json.dumps(expected, sort_keys=True) + '\n').encode())
            os.fsync(fd)
        finally:
            os.close(fd)
        sync_directory(directory)
    return expected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['capacity', 'receive', 'verify'])
    parser.add_argument('--operation', required=True)
    parser.add_argument('--nonce', required=True)
    parser.add_argument('--size', type=int, required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--manifest-sha256', required=True)
    args = parser.parse_args()
    try:
        def expired(_signum, _frame):
            raise RuntimeError('offhost_backup_timeout')
        signal.signal(signal.SIGALRM, expired)
        signal.alarm(900)
        if args.mode == 'capacity':
            metadata(ROOT)
            available = os.statvfs(ROOT)
            require(available.f_bavail * available.f_frsize >= MAXIMUM + FLOOR)
            print(json.dumps({'status': 'capacity_verified', 'operation_id': args.operation, 'nonce': args.nonce,
                              'manifest_sha256': args.manifest_sha256, 'minimum_free_bytes': MAXIMUM + FLOOR}))
            return 0
        value = transfer(args.operation, args.nonce, args.size, args.sha256, args.manifest_sha256,
                         sys.stdin.buffer, verify=args.mode == 'verify')
        print(json.dumps({'status': 'verified', **value}))
        return 0
    except BaseException:
        print('{"status":"refused","code":"offhost_backup_refused"}', file=sys.stderr)
        return 1
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    raise SystemExit(main())
