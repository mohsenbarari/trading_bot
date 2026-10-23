#!/usr/bin/env python3
"""Fetch one fixed public wheel with an EARLY file-size and wall-time limit."""
import argparse
import ctypes
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import resource
import stat
import subprocess
import tempfile
from urllib.parse import urlsplit

STAGE = Path('/root/trading-bot/trading_bot/.local/security-kernel-reboot-task02-wheels')


def require(ok, code):
    if not ok: raise RuntimeError(code)


def native_root():
    require(os.geteuid() == 0, 'native_root_required')


def verify(path, size, digest):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid() and before.st_nlink == 1 and
                stat.S_IMODE(before.st_mode) == 0o600 and before.st_size == size, 'unsafe_or_wrong_size_wheel')
        result = sha256()
        count = 0
        while raw := os.read(fd, min(65536, size + 1 - count)):
            result.update(raw); count += len(raw)
            require(count <= size, 'wheel_oversize')
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_mode, s.st_nlink, s.st_uid)
        require(count == size and result.hexdigest() == digest and
                identity(before) == identity(os.fstat(fd)) == identity(path.lstat()), 'wheel_changed_or_digest_mismatch')
    finally: os.close(fd)


def stage_guard():
    for path in (STAGE, *STAGE.parents):
        info = path.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid in {0, os.geteuid()} and
                not info.st_mode & 0o022, 'unsafe_stage_parent')


def fetch(url, filename, size, digest):
    native_root()
    parts = urlsplit(url)
    require(parts.scheme == 'https' and parts.netloc == 'files.pythonhosted.org' and
            parts.path.startswith('/packages/') and not parts.query and not parts.fragment and
            Path(parts.path).name == filename and re.fullmatch(r'[A-Za-z0-9_.-]+\.whl', filename), 'official_wheel_scope')
    require(0 < size <= 8 * 1024 * 1024 and re.fullmatch('[a-f0-9]{64}', digest), 'wheel_bound_or_digest_invalid')
    stage_guard()
    destination = STAGE / filename
    if os.path.lexists(destination):
        verify(destination, size, digest)
        return
    binary = Path('/usr/bin/curl').resolve(strict=True)
    info = binary.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022, 'untrusted_curl')
    fd, temporary = tempfile.mkstemp(prefix='.wheel-', dir=STAGE)
    try:
        def bounds():
            resource.setrlimit(resource.RLIMIT_FSIZE, (size, size))
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        subprocess.run([str(binary), '--proto', '=https', '--proto-redir', '=https',
                        '--max-filesize', str(size), '--max-time', '60', '--location', '--silent', '--fail',
                        '--output', temporary, url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=65, check=True, preexec_fn=bounds,
                       env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
        verify(Path(temporary), size, digest)
        require(os.fstat(fd).st_ino == Path(temporary).lstat().st_ino, 'wheel_temporary_inode_changed')
        os.fsync(fd)
        libc = ctypes.CDLL(None, use_errno=True)
        rename = libc.renameat2
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        status = rename(-100, os.fsencode(temporary), -100, os.fsencode(destination), 1)
        if status != 0:
            raise OSError(ctypes.get_errno(), 'wheel_publish_noreplace_failed')
        directory = os.open(STAGE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        os.close(fd)
        Path(temporary).unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--filename', required=True)
    parser.add_argument('--size', type=int, required=True)
    parser.add_argument('--sha256', required=True)
    arguments = parser.parse_args()
    try:
        fetch(arguments.url, arguments.filename, arguments.size, arguments.sha256)
        print(json.dumps({'status': 'verified', 'filename': arguments.filename, 'size': arguments.size}))
    except Exception as error:
        print(json.dumps({'status': 'refused', 'error_class': type(error).__name__}))
        raise SystemExit(1)
