#!/usr/bin/env python3
"""Offline candidate gate; uses only new synthetic SQLite files, never app data.

The vendor package floor covers the three identified SQLite findings. It is not
an all-CVE attestation and does not replace scanning the actual built image.
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

MINIMUM_PACKAGE = '3.46.1-7+deb13u2'
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


def platform_package():
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
    command(['/usr/bin/dpkg', '--compare-versions', version, 'ge', MINIMUM_PACKAGE])
    require(version.rsplit(':', 1)[-1].split('-', 1)[0] == sqlite3.sqlite_version and
            sqlite3.sqlite_version_info >= (3, 46, 1), 'sqlite_runtime_package_mismatch')
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
                try:
                    reader.execute('UPDATE quotes SET price=1')
                except sqlite3.OperationalError:
                    pass
                else:
                    raise ValueError('readonly_write_accepted')
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


def verify(require_libmagic=False):
    require(sys.version_info[:3] == (3, 11, 17), 'python_version_changed')
    version = platform_package()
    if require_libmagic:
        import magic
        require(magic.from_buffer(b'plain text compatibility\n', mime=True) == 'text/plain', 'libmagic_contract')
    with open('/proc/self/maps', encoding='ascii') as stream:
        maps = stream.read(2 * 1024 * 1024 + 1)
    system_library(maps, 'libsqlite3')
    if require_libmagic:
        system_library(maps, 'libmagic')
    return {'status': 'PASS', 'sqlite_package': version, 'sqlite_runtime': sqlite3.sqlite_version,
            'libmagic_checked': require_libmagic, 'compatibility': compatibility(),
            'production_data_accessed': False, 'all_cves_attested': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--require-libmagic', action='store_true')
    args = parser.parse_args()
    def expired(*_):
        raise TimeoutError('sqlite_probe_deadline')
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(20)
    try:
        value = verify(args.require_libmagic)
    except Exception:
        print('{"status":"FAIL","reason":"sqlite_runtime_security_gate_failed"}')
        return 1
    finally:
        signal.alarm(0)
    print(json.dumps(value, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
