#!/usr/bin/env python3
"""Ordinary positive SQLite compatibility in a proper installed Debian image.

Uses only new synthetic files and sequential normal transactions. Package source
provenance is separate; this does not reproduce or certify the WAL-reset race.
"""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import resource
import signal
import sqlite3
import stat
import subprocess
import sys
import tempfile

BASE_PACKAGE = '3.46.1-7+deb13u2'
FIXED_PACKAGE = BASE_PACKAGE + '+gold1'
ALLOWED_PACKAGES = (BASE_PACKAGE, FIXED_PACKAGE)
PUBLIC_FAILURE_CODES = frozenset(('backup_integrity', 'backup_query_drift', 'binary_bound', 'binary_changed', 'binary_identity', 'binary_mapping', 'binary_parent', 'compile_options_bound', 'fts_contract', 'isolated_python_required', 'json_contract', 'loader_override', 'lookup_index_not_used', 'mapping_bound', 'non_system_library', 'os_metadata', 'os_metadata_bound', 'package_library_path', 'package_metadata_bound', 'package_paths_bound', 'package_payload_changed', 'package_scope', 'package_version', 'package_version_changed', 'python_customization', 'python_version_changed', 'readonly_reopen_drift', 'scratch_not_private', 'sqlite_runtime_package_mismatch', 'stdlib_extension_required', 'stdlib_sqlite_required', 'system_library_missing_or_ambiguous', 'transaction_or_query_drift', 'trixie_required', 'wal_mode', 'wal_reader', 'wal_refresh', 'wal_snapshot_drift'))
QUERY = ('SELECT source,event_id,commodity,price FROM quotes WHERE commodity=? '
         'ORDER BY seen_at DESC,event_id DESC,source')
EXPECTED = [('g2', 3, 'emami', 62000000), ('g1', 1, 'emami', 61500000)]


def require(ok, code):
    if not ok:
        raise ValueError(code)


def command(argv):
    # These fixed public dpkg queries write no package/artifact files. A small
    # regular-file limit is therefore appropriate here, unlike backup/install.
    def limits():
        resource.setrlimit(resource.RLIMIT_FSIZE, (4096, 4096))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    with tempfile.TemporaryFile() as output:
        subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=output,
                       stderr=subprocess.DEVNULL, check=True, timeout=5,
                       preexec_fn=limits, env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'})
        require(output.tell() < 4096, 'package_metadata_bound')
        output.seek(0)
        return output.read(4096).decode('ascii').strip()


def platform_package(expected):
    require(expected in ALLOWED_PACKAGES, 'package_scope')
    path = Path('/etc/os-release').resolve(strict=True)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and
                not info.st_mode & 0o022 and info.st_size <= 65536, 'os_metadata')
        raw = os.read(fd, 65537)
        require(len(raw) == info.st_size, 'os_metadata_bound')
    finally:
        os.close(fd)
    release = dict(line.split('=', 1) for line in raw.decode().splitlines() if '=' in line)
    require(release.get('ID', '').strip('"') == 'debian' and
            release.get('VERSION_ID', '').strip('"') == '13' and
            release.get('VERSION_CODENAME', '').strip('"') == 'trixie', 'trixie_required')
    version = command(['/usr/bin/dpkg-query', '-W', '-f=${Version}\n', 'libsqlite3-0'])
    require(version and '\n' not in version and len(version) <= 100, 'package_version')
    require(version == expected, 'package_version_changed')
    require(command(['/usr/bin/dpkg', '--verify', 'libsqlite3-0']) == '', 'package_payload_changed')
    require(version.rsplit(':', 1)[-1].split('-', 1)[0] == sqlite3.sqlite_version and
            sqlite3.sqlite_version_info == (3, 46, 1), 'sqlite_runtime_package_mismatch')
    return version


def system_library(raw, family):
    require(len(raw) <= 2 * 1024 * 1024, 'mapping_bound')
    found = set()
    for line in raw.splitlines():
        parts = line.split(maxsplit=5)
        if len(parts) != 6 or not Path(parts[5]).name.startswith(family + '.so.'):
            continue
        path = parts[5]
        require(not path.endswith(' (deleted)') and '.libs/' not in path and
                Path(path).is_absolute() and
                Path(path).resolve(strict=True).is_relative_to(Path('/usr/lib')), 'non_system_library')
        found.add(Path(path).resolve(strict=True))
    require(len(found) == 1, 'system_library_missing_or_ambiguous')
    return str(next(iter(found)))




def mapped_binary_identity(path, maps):
    """Bind one public immutable system binary to this process's real mapping."""
    path = Path(path)
    require(type(maps) is str and len(maps) <= 2*1024**2, 'mapping_bound')
    parents = []
    for parent in path.parents:
        row = parent.lstat()
        require(parent.resolve(strict=True) == parent and stat.S_ISDIR(row.st_mode)
                and row.st_uid == row.st_gid == 0 and not row.st_mode & 0o022,
                'binary_parent')
        parents.append((parent, (row.st_dev,row.st_ino,row.st_mode,row.st_uid,row.st_gid)))
    ident = lambda row: (row.st_dev,row.st_ino,row.st_mode,row.st_uid,row.st_gid,
                         row.st_nlink,row.st_size,row.st_mtime_ns,row.st_ctime_ns)
    before = ident(path.lstat())
    require(path.resolve(strict=True) == path and stat.S_ISREG(before[2])
            and before[3:6] == (0,0,1) and not before[2] & 0o022
            and 0 < before[6] <= 16*1024**2, 'binary_identity')
    selected = []
    for line in maps.splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) == 6 and fields[5] == str(path):
            require(fields[4].isdigit(), 'binary_mapping')
            parts = fields[3].split(':')
            require(len(parts) == 2 and all(c and all(x in '0123456789abcdefABCDEF' for x in c) for c in parts),
                    'binary_mapping')
            selected.append((int(parts[0],16),int(parts[1],16),int(fields[4])))
    require(selected and all(item == (os.major(before[0]),os.minor(before[0]),before[1]) for item in selected),
            'binary_mapping')
    fd = os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    try:
        require(ident(os.fstat(fd)) == before, 'binary_identity')
        digest = sha256();count = 0
        while True:
            block = os.read(fd,min(65536,before[6]-count+1))
            if not block:break
            count += len(block);require(count <= before[6], 'binary_bound');digest.update(block)
        require(count == before[6] and ident(os.fstat(fd)) == before == ident(path.lstat()),
                'binary_changed')
    finally:
        os.close(fd)
    require(ident(path.lstat()) == before and all(parent.resolve(strict=True) == parent
            and (lambda row:(row.st_dev,row.st_ino,row.st_mode,row.st_uid,row.st_gid))(parent.lstat()) == old
            for parent,old in parents), 'binary_changed')
    return {'path':str(path),'bytes':before[6],'sha256':digest.hexdigest(),'mapped_inode_verified':True}


def installed_sqlite_identity(maps):
    """The mapped object must be a payload of the verified installed package."""
    selected = Path(system_library(maps,'libsqlite3'))
    raw = command(['/usr/bin/dpkg-query','-L','libsqlite3-0'])
    require(type(raw) is str and len(raw) < 4096, 'package_paths_bound')
    files = set()
    for line in raw.splitlines():
        path = Path(line)
        if path.name.startswith('libsqlite3.so.'):
            require(path.is_absolute(), 'package_library_path')
            resolved = path.resolve(strict=True)
            require(resolved.is_relative_to(Path('/usr/lib')), 'package_library_path')
            files.add(resolved)
    require(files == {selected}, 'package_library_path')
    return mapped_binary_identity(selected,maps)


def loader_identity():
    """Bind isolated stdlib code; ignored business PYTHONPATH is not a loader."""
    import _sqlite3
    import importlib.machinery
    require(sys.flags.isolated == sys.flags.ignore_environment == sys.flags.no_user_site == 1,
            'isolated_python_required')
    require(not any(name.startswith('LD_') for name in os.environ), 'loader_override')
    require('sitecustomize' not in sys.modules and 'usercustomize' not in sys.modules,
            'python_customization')
    stdlib = Path(sys.base_prefix) / 'lib/python3.11'
    package = stdlib/'sqlite3/__init__.py'
    require(Path(sqlite3.__file__) == package and sqlite3.__spec__ is not None
            and sqlite3.__spec__.origin == str(package)
            and isinstance(sqlite3.__spec__.loader,importlib.machinery.SourceFileLoader)
            and isinstance(sqlite3.__loader__,importlib.machinery.SourceFileLoader)
            and sqlite3.connect is _sqlite3.connect and sqlite3.Connection is _sqlite3.Connection,
            'stdlib_sqlite_required')
    path = Path(_sqlite3.__file__)
    spec = _sqlite3.__spec__
    require(path.parent == stdlib/'lib-dynload'
            and path.name in {'_sqlite3'+suffix for suffix in importlib.machinery.EXTENSION_SUFFIXES}
            and spec is not None and spec.origin == str(path)
            and isinstance(spec.loader, importlib.machinery.ExtensionFileLoader)
            and isinstance(_sqlite3.__loader__, importlib.machinery.ExtensionFileLoader),
            'stdlib_extension_required')
    with open('/proc/self/maps',encoding='ascii') as source:
        maps = source.read(2*1024**2+1)
    require(len(maps) <= 2*1024**2, 'mapping_bound')
    return mapped_binary_identity(path,maps)


def compatibility():
    """Bounded examples of the existing WAL/query/backup storage contract."""
    with tempfile.TemporaryDirectory(prefix='sqlite-compat-') as directory:
        root = Path(directory)
        require(stat.S_IMODE(root.stat().st_mode) == 0o700, 'scratch_not_private')
        path, backup = root / 'source.sqlite3', root / 'backup.sqlite3'
        writer = sqlite3.connect(path, isolation_level=None, timeout=2)
        try:
            require(writer.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal', 'wal_mode')
            writer.execute('PRAGMA foreign_keys=ON')
            writer.executescript('''
                CREATE TABLE quotes(source TEXT,event_id INTEGER,commodity TEXT,
                    price INTEGER CHECK(price>0),seen_at TEXT,PRIMARY KEY(source,event_id));
                CREATE INDEX quote_lookup ON quotes(commodity,seen_at DESC,event_id DESC);
                CREATE VIRTUAL TABLE words USING fts5(body);
            ''')
            writer.executemany('INSERT INTO quotes VALUES(?,?,?,?,?)', [
                ('g1', 1, 'emami', 61000000, '2026-10-23T10:00:00Z'),
                ('g2', 3, 'emami', 62000000, '2026-10-23T10:01:00Z'),
                ('g1', 2, 'bahar', 59000000, '2026-10-23T10:02:00Z'),
            ])
            writer.execute('INSERT INTO quotes VALUES(?,?,?,?,?) ON CONFLICT(source,event_id) '
                           'DO UPDATE SET price=excluded.price',
                           ('g1', 1, 'emami', 61500000, '2026-10-23T10:00:00Z'))
            writer.execute('BEGIN IMMEDIATE')
            writer.execute('UPDATE quotes SET price=1 WHERE source=?', ('g1',))
            writer.execute('ROLLBACK')
            require(writer.execute(QUERY, ('emami',)).fetchall() == EXPECTED, 'transaction_or_query_drift')
            require(writer.execute('SELECT json_extract(?, ?)', ('{"price":61500000}', '$.price')).fetchone()[0]
                    == 61500000, 'json_contract')
            writer.execute('INSERT INTO words(body) VALUES(?)', ('سکه امام نقدی coin quote',))
            require(writer.execute('SELECT rowid FROM words WHERE words MATCH ?', ('"سکه"',)).fetchall()
                    == [(1,)], 'fts_contract')
            plan = [row[3] for row in writer.execute('EXPLAIN QUERY PLAN ' + QUERY, ('emami',))]
            require(any('quote_lookup' in detail for detail in plan), 'lookup_index_not_used')
            reader = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, isolation_level=None, timeout=2)
            try:
                reader.execute('BEGIN')
                require(reader.execute(QUERY, ('emami',)).fetchall() == EXPECTED, 'wal_reader')
                writer.execute('UPDATE quotes SET price=price+1 WHERE source=? AND event_id=?', ('g2', 3))
                require(reader.execute(QUERY, ('emami',)).fetchall() == EXPECTED, 'wal_snapshot_drift')
                reader.execute('COMMIT')
                require(reader.execute(QUERY, ('emami',)).fetchall()[0][3] == 62000001, 'wal_refresh')
                writer.execute('UPDATE quotes SET price=price-1 WHERE source=? AND event_id=?', ('g2', 3))
            finally:
                reader.close()
            target = sqlite3.connect(backup, timeout=2)
            try:
                writer.backup(target, pages=32, sleep=0.01)
                require(target.execute('PRAGMA quick_check').fetchone() == ('ok',), 'backup_integrity')
                require(target.execute(QUERY, ('emami',)).fetchall() == EXPECTED, 'backup_query_drift')
            finally:
                target.close()
        finally:
            writer.close()
        for candidate in (path, backup):
            connection = sqlite3.connect(candidate.as_uri() + '?mode=ro', uri=True, timeout=2)
            try:
                require(connection.execute(QUERY, ('emami',)).fetchall() == EXPECTED, 'readonly_reopen_drift')
            finally:
                connection.close()
        return {'wal_snapshot': True, 'rollback_upsert': True, 'json_fts': True,
                'backup_readonly_reopen': True, 'lookup_index': True,
                'query_result_sha256': sha256(json.dumps(EXPECTED, ensure_ascii=False).encode()).hexdigest(),
                'query_plan': plan}


def verify(expected):
    require(sys.version_info[:3] == (3, 11, 17), 'python_version_changed')
    extension = loader_identity()
    version = platform_package(expected)
    with open('/proc/self/maps', encoding='ascii') as stream:
        maps = stream.read(2 * 1024 * 1024 + 1)
    library = installed_sqlite_identity(maps)
    connection = sqlite3.connect(':memory:')
    try:
        options = sorted(row[0] for row in connection.execute('PRAGMA compile_options'))
    finally:
        connection.close()
    require(options and len(options) <= 256 and all(type(v) is str and 0 < len(v) <= 256 for v in options), 'compile_options_bound')
    body = (json.dumps(options, separators=(',', ':'))+'\n').encode()
    return {'schema':'sqlite_ordinary_compatibility/1', 'status':'PASS',
            'python_version':'.'.join(map(str,sys.version_info[:3])),
            'sqlite_package':version, 'sqlite_runtime':sqlite3.sqlite_version,
            'system_library':library['path'], 'sqlite_system_library':library, 'sqlite_stdlib_extension':extension, 'python_environment_ignored':True, 'compile_options':options,
            'compile_options_sha256':sha256(body).hexdigest(),
            'compatibility':compatibility(), 'production_data_accessed':False,
            'wal_reset_reproduction_performed':False, 'all_cves_attested':False,
            'security_accepted':False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expect-package-version', choices=ALLOWED_PACKAGES, required=True)
    args = parser.parse_args()
    def expired(*_):
        raise TimeoutError('sqlite_probe_deadline')
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(20)
    try:
        value = verify(args.expect_package_version)
    except Exception as error:
        code = error.args[0] if type(error) is ValueError and len(error.args) == 1 and type(error.args[0]) is str and error.args[0] in PUBLIC_FAILURE_CODES else 'unclassified'
        kind = type(error).__name__ if type(error).__name__ in {'ValueError','OSError','FileNotFoundError','PermissionError','CalledProcessError','TimeoutExpired','TimeoutError'} else 'unclassified'
        print(json.dumps({'schema':'sqlite_ordinary_compatibility/1','status':'FAIL','reason':'sqlite_compatibility_gate_failed','known_code':code,'exception_class':kind,'security_accepted':False},sort_keys=True))
        return 1
    finally:
        signal.alarm(0)
    print(json.dumps(value, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
