#!/usr/bin/env python3
"""Admit one official source archive, never a same-name cached binary wheel.

Other dependencies retain the existing wheel-cache workflow. Native compilation
belongs to the matching Python311 Bookworm Docker builder, not this host.
"""
import argparse
from hashlib import sha256
import os
from pathlib import Path
import resource
import stat
import subprocess
import tempfile

FILENAME = 'psycopg2_binary-2.9.13.tar.gz'
SIZE = 378112
SHA256 = 'e324ecf60f952d21dd11413b8bbed0951bbd99579a06fd06f28bfc37737cd373'
URL = 'https://files.pythonhosted.org/packages/ed/76/7b4383014be0fcc6c1c0e24292845a14e1672cf17fca62ca0a2bd5f4563d/' + FILENAME
FILTERED = '.requirements-without-psycopg2.txt'


def require(ok):
    if not ok: raise ValueError('psycopg2_source_admission_failed')


def read(path, maximum):
    require(path.resolve(strict=True) == path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid() and before.st_nlink == 1 and
                not before.st_mode & 0o022 and before.st_size <= maximum)
        raw = os.read(fd, maximum + 1)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mode, s.st_uid, s.st_nlink, s.st_mtime_ns, s.st_ctime_ns)
        require(len(raw) == before.st_size and identity(before) == identity(os.fstat(fd)) == identity(path.lstat()))
        return raw
    finally: os.close(fd)


def atomic(path, raw):
    fd, temporary = tempfile.mkstemp(prefix='.psycopg-source-', dir=path.parent)
    try:
        while raw:
            count = os.write(fd, raw); require(count > 0); raw = raw[count:]
        os.fsync(fd)
        if os.path.lexists(path): read(path, 1024 * 1024)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        os.close(fd)
        Path(temporary).unlink(missing_ok=True)


def prepare(cache, requirements, download=False):
    require(cache.is_absolute() and cache.resolve(strict=True) == cache)
    info = cache.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and not info.st_mode & 0o022)
    for parent in cache.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid in {0, os.geteuid()} and not info.st_mode & 0o022)
    for path in cache.iterdir():
        require(not (path.name.lower().startswith('psycopg2') and path.suffix.lower() == '.whl'))
    lines = read(requirements, 131072).splitlines(keepends=True)
    selected = [line for line in lines if line.strip() == b'psycopg2-binary==2.9.13']
    require(len(selected) == 1)
    filtered = b''.join(line for line in lines if line not in selected)
    source = cache / FILENAME
    if not os.path.lexists(source):
        require(download)
        # Fixed official URL, no redirects; hard byte/wall bounds apply before
        # output accumulates, not after a generic pip/network fetch completes.
        with tempfile.TemporaryFile(dir=cache) as output:
            def limits():
                resource.setrlimit(resource.RLIMIT_FSIZE, (SIZE, SIZE))
                resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            subprocess.run(['/usr/bin/curl', '--proto', '=https', '--fail', '--silent', '--show-error',
                            '--max-filesize', str(SIZE), '--max-time', '60', URL],
                           stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.DEVNULL,
                           timeout=65, check=True, preexec_fn=limits,
                           env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8'})
            require(output.tell() == SIZE)
            output.seek(0)
            raw = output.read(SIZE + 1)
        require(len(raw) == SIZE and sha256(raw).hexdigest() == SHA256)
        atomic(source, raw)
    raw = read(source, SIZE)
    require(len(raw) == SIZE and sha256(raw).hexdigest() == SHA256)
    target = cache / FILTERED
    if download: atomic(target, filtered)
    else: require(read(target, 131072) == filtered)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'prepare'))
    parser.add_argument('--cache-dir', type=Path, required=True)
    parser.add_argument('--requirements', type=Path, required=True)
    args = parser.parse_args()
    try:
        prepare(args.cache_dir, args.requirements, download=args.action == 'prepare')
    except (OSError, ValueError, subprocess.SubprocessError):
        print('psycopg2 source admission failed')
        return 1
    return 0


if __name__ == '__main__': raise SystemExit(main())
