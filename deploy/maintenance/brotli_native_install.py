#!/usr/bin/env python3
"""Finite two-package Native Brotli transaction; consumer refresh is separate."""
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import argparse
import fcntl
import io
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import time
import zipfile

ROOT = Path('/srv/trading-bot/backups/security-brotli-native-install-20261024-01')
CONTROL = Path('/root/secure-envs/trading-bot')
MARKER = CONTROL / 'queue-cutover-artifacts/production-release.lock'
LEGACY_SOURCE = CONTROL / '.production-runtime-source.lock'
ABSENT = (CONTROL / 'release-control/private-primary-controller.lock',
          Path('/var/lib/trading-bot/production-release/two-host-release-state.json'))
OLD, NEW = '1.1.0-2build2', '1.1.0-2build2+security20261024.1'
PACKAGES = ('libbrotli1', 'python3-brotli')
UNITS = ('coin-public-market-telegram.service', 'golden_bot.service')
BUNDLE_SHA = '1972bc04862562082112fe27fabc0dd74cea7701e862be9887bd78218efc9944'
BUNDLE_BYTES = 1098270
INPUTS = {
    'libbrotli1': (330894, 'ac9ee18779db1c023ad3f76ae4581c7e97958eded07d8296bed033e5ccad2921'),
    'python3-brotli': (332512, 'bc3001e3f52491dd258ff6aab94a06f053d92614e425c51d05204dc197a24399'),
}
BASELINE = {
    'libbrotli1': (330730, '74492419b8fda803774b8c9acef6afc5d2f9ff31782635aae212906adae7b277'),
    'python3-brotli': (332292, 'f42bb87f98c01017c291cb0cfce99c92c1c91fd38e798f978e4fd10ee98601fc'),
}
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C.UTF-8',
       'DEBIAN_FRONTEND': 'noninteractive', 'NEEDRESTART_MODE': 'l', 'APT_LISTCHANGES_FRONTEND': 'none',
       'DOCKER_HOST': 'unix:///var/run/docker.sock', 'PYTHONDONTWRITEBYTECODE': '1'}
POLICY = Path('/usr/sbin/policy-rc.d')
POLICY_BODY = b'#!/bin/sh\n# Owned two-package Brotli transaction 20261024-01\nexit 101\n'
DEADLINE = None
MODULE = Path('/usr/lib/python3/dist-packages/_brotli.cpython-312-x86_64-linux-gnu.so')
INSPECT = ('{"id":{{json .Id}},"image":{{json .Image}},"running":{{json .State.Running}},'
           '"policy":{{json .HostConfig.RestartPolicy}},"mounts":{{json .Mounts}}}')


def require(ok, code):
    if not ok:
        raise RuntimeError(code)


def identity(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid,
            value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def safe_parent(path):
    require(path.is_absolute() and path.resolve(strict=False) == path, 'redirected_path')
    for parent in path.parents:
        value = parent.lstat()
        require(stat.S_ISDIR(value.st_mode) and value.st_uid == 0 and not value.st_mode & 0o022, 'unsafe_parent')


def read(path, maximum=65536):
    safe_parent(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
                and not before.st_mode & 0o022 and 0 < before.st_size <= maximum, 'unsafe_input')
        chunks = bytearray()
        while block := os.read(fd, min(65536, maximum + 1 - len(chunks))):
            chunks.extend(block)
            require(len(chunks) <= maximum, 'input_bound')
        require(len(chunks) == before.st_size and identity(before) == identity(os.fstat(fd)) == identity(path.lstat()), 'input_changed')
        return bytes(chunks)
    finally:
        os.close(fd)


def write(path, raw, *, exclusive):
    original = None if exclusive else path.lstat()
    if original is not None:
        require(stat.S_ISREG(original.st_mode) and original.st_uid == 0 and original.st_nlink == 1
                and stat.S_IMODE(original.st_mode) == 0o600, 'unsafe_output')
    target = path if exclusive else path.with_name('.' + path.name + '.next')
    fd = os.open(target, os.O_WRONLY | os.O_NOFOLLOW | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600, 'unsafe_output')
        require(identity(info) == identity(target.lstat()), 'output_identity_changed')
        view = memoryview(raw)
        while view:
            count = os.write(fd, view)
            require(count > 0, 'short_output')
            view = view[count:]
        os.fsync(fd)
        if not exclusive:
            require(identity(path.lstat()) == identity(original), 'output_identity_changed')
            os.replace(target, path)
    finally:
        if not exclusive and os.path.lexists(target) and identity(target.lstat()) == identity(os.fstat(fd)):
            target.unlink()
        os.close(fd)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def command(argv, *, seconds=30, cwd=None):
    binary = Path(argv[0]).resolve(strict=True)
    info = binary.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022, 'untrusted_binary')
    if DEADLINE is not None:
        seconds = min(seconds, DEADLINE - time.monotonic())
    require(seconds > 0, 'native_brotli_deadline')
    child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             env=ENV, cwd=cwd, start_new_session=True)
    output, selector = bytearray(), selectors.DefaultSelector()
    deadline = time.monotonic() + seconds
    try:
        selector.register(child.stdout, selectors.EVENT_READ)
        while selector.get_map():
            require(time.monotonic() < deadline, 'command_timeout')
            for key, _ in selector.select(0.1):
                block = os.read(key.fileobj.fileno(), 65536)
                if not block:
                    selector.unregister(key.fileobj)
                else:
                    require(len(output) + len(block) < 2 * 1024 * 1024, 'command_output_bound')
                    output.extend(block)
        status = child.wait(timeout=max(0.1, deadline - time.monotonic()))
        require(status == 0, 'native_command_failed_' + binary.name.replace('-', '_') + '_' + str(abs(status)))
        return bytes(output).decode()
    finally:
        selector.close()
        if child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait(timeout=5)
        child.stdout.close()


def versions():
    raw = command(['/usr/bin/dpkg-query', '-W', '-f=${Package}=${Version}\n', *PACKAGES])
    result = dict(line.split('=', 1) for line in raw.splitlines())
    require(set(result) == set(PACKAGES), 'installed_package_inventory_changed')
    return result


def dirty_packages():
    raw = command(['/usr/bin/dpkg-query', '-W', '-f=${Package}\t${db:Status-Status}\n'])
    rows = [line.split('\t') for line in raw.splitlines()]
    require(all(len(row) == 2 for row in rows), 'dpkg_status_schema')
    return {name for name, status in rows if status not in {'installed', 'not-installed', 'config-files'}}


def boot_id():
    with Path('/proc/sys/kernel/random/boot_id').open('rb') as handle:
        value = handle.read(65).decode().strip()
    require(re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', value), 'invalid_boot_identity')
    return value


def package_locks_available():
    # A read-only admission probe of existing POSIX locks. Release before the
    # ordinary package tool obtains its own locks; never kill another owner.
    opened = []
    try:
        for path in map(Path, ('/var/lib/dpkg/lock-frontend', '/var/lib/dpkg/lock', '/var/cache/apt/archives/lock')):
            safe_parent(path)
            fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
            opened.append(fd)
            value = os.fstat(fd)
            require(stat.S_ISREG(value.st_mode) and value.st_uid == 0 and value.st_nlink == 1
                    and identity(value) == identity(path.lstat()), 'unsafe_package_lock')
            try:
                fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('package_manager_busy') from None
    finally:
        for fd in reversed(opened):
            os.close(fd)


@contextmanager
def legacy_source_guard():
    # This is a permanent, empty flock file, not a durable operation record.
    # Never create, delete, replace or write it; prove and lock the same inode.
    safe_parent(LEGACY_SOURCE)
    before = LEGACY_SOURCE.lstat()
    fd = os.open(LEGACY_SOURCE, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        value = os.fstat(fd)
        require(stat.S_ISREG(value.st_mode) and value.st_uid == 0 and value.st_nlink == 1
                and stat.S_IMODE(value.st_mode) == 0o600 and value.st_size == 0
                and identity(before) == identity(value) == identity(LEGACY_SOURCE.lstat()), 'native_source_lock_changed')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('native_source_lock_busy') from None
        yield fd
        require(identity(value) == identity(os.fstat(fd)) == identity(LEGACY_SOURCE.lstat()), 'native_source_lock_changed')
    finally:
        os.close(fd)


def native(*, recovery=False, source_held=False):
    require(os.geteuid() == 0 and Path('/proc/1/exe').readlink().name == 'systemd', 'native_root_required')
    require(not any(Path(v).exists() for v in ('/.dockerenv', '/run/.containerenv', '/run/systemd/container')), 'container_refused')
    for ns in ('pid', 'mnt', 'user'):
        require(os.readlink('/proc/self/ns/' + ns) == os.readlink('/proc/1/ns/' + ns), 'namespace_mismatch')
    release = dict(line.split('=', 1) for line in read(Path('/etc/os-release').resolve(strict=True)).decode().splitlines() if '=' in line)
    require(release.get('ID') == 'ubuntu' and release.get('VERSION_ID', '').strip('"') == '24.04'
            and os.uname().machine == 'x86_64', 'wrong_native_distribution')
    require(not any(os.path.lexists(path) for path in ABSENT), 'production_operation_present')
    if not source_held:
        with legacy_source_guard():
            pass
    require(not os.path.lexists(POLICY), 'preexisting_service_policy_refused')
    package_locks_available()
    if not recovery:
        require(not command(['/usr/bin/dpkg', '--audit']).strip() and not dirty_packages(), 'dpkg_not_clean')
    for path in (Path('/'), ROOT.parent):
        available = os.statvfs(path)
        require(available.f_bavail * available.f_frsize >= 1024**3, 'package_capacity_insufficient')


def runtime():
    identifiers = command(['/usr/bin/docker', 'ps', '-aq', '--no-trunc']).splitlines()
    require(len(set(identifiers)) == len(identifiers) == 15 and all(re.fullmatch('[0-9a-f]{64}', v) for v in identifiers), 'native_container_inventory_changed')
    rows = []
    for ident in sorted(identifiers):
        row = json.loads(command(['/usr/bin/docker', 'inspect', '--format', INSPECT, ident]))
        require(row['id'] == ident and len({m['Destination'] for m in row['mounts']}) == len(row['mounts']), 'container_metadata_invalid')
        row['mounts'] = sorted(row['mounts'], key=lambda value: value['Destination'])
        rows.append(row)
    return rows


def units():
    result = {}
    for unit in UNITS:
        raw = command(['/usr/bin/systemctl', 'show', unit, '--property=LoadState,ActiveState,UnitFileState,MainPID,KillMode'])
        value = dict(line.split('=', 1) for line in raw.splitlines())
        require(value['LoadState'] == 'loaded' and value['ActiveState'] == 'active' and value['UnitFileState'] == 'enabled'
                and int(value['MainPID']) > 1, 'native_consumer_not_running')
        result[unit] = value
    return result


def installed_api(version):
    # A fresh, isolated interpreter proves installed C/Python bytes without
    # importing either live service or touching application/session data.
    script = '''import ctypes,sys
sys.path.insert(0,'/usr/lib/python3/dist-packages')
import brotli,_brotli
assert _brotli.__file__=='/usr/lib/python3/dist-packages/_brotli.cpython-312-x86_64-linux-gnu.so'
value='آزمون Brotli'.encode()*32
assert brotli.decompress(brotli.compress(value))==value
decoder=ctypes.CDLL('/usr/lib/x86_64-linux-gnu/libbrotlidec.so.1')
decoder.BrotliDecoderErrorString.argtypes=[ctypes.c_int]
decoder.BrotliDecoderErrorString.restype=ctypes.c_char_p
if sys.argv[1]=='patched':assert decoder.BrotliDecoderErrorString(-17)==b'_ERROR_FORMAT_BLOCK_SWITCH'
print('installed_brotli_api_passed')
'''
    require(command(['/usr/bin/python3', '-I', '-B', '-S', '-c', script, 'patched' if version == NEW else 'rollback'], seconds=15).strip()
            == 'installed_brotli_api_passed', 'installed_api_not_proven')


def bundle(path):
    raw = read(path, 2 * 1024 * 1024)
    require(len(raw) == BUNDLE_BYTES and sha256(raw).hexdigest() == BUNDLE_SHA, 'candidate_bundle_changed')
    selected = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)) == 9 and all('/' not in n and '\\' not in n for n in names), 'candidate_archive_inventory')
        for package, (size, digest) in INPUTS.items():
            name = package + '_' + NEW + '_amd64.deb'
            entry = archive.getinfo(name)
            require(entry.file_size == size, 'candidate_package_size')
            data = archive.read(entry)
            require(len(data) == size and sha256(data).hexdigest() == digest, 'candidate_package_digest')
            selected[name] = data
        entry = archive.getinfo('build-receipt.json')
        require(entry.file_size < 65536, 'build_receipt_bound')
        receipt = json.loads(archive.read(entry))
        require(receipt['schema'] == 'noble_brotli_source_build/1' and receipt['status'] == 'passed' and receipt['version'] == NEW
                and receipt['tests']['status'] == 'passed' and receipt['tests']['python_embedded_decoder_rebuilt'] is True,
                'candidate_build_proof_failed')
    return selected


def package_paths(folder, version):
    return [str(folder / (name + '_' + version + '_amd64.deb')) for name in PACKAGES]


def verify_debs(folder, version, expected):
    for name, (size, digest) in expected.items():
        path = folder / (name + '_' + version + '_amd64.deb')
        raw = read(path, 1024 * 1024)
        require(len(raw) == size and sha256(raw).hexdigest() == digest, 'package_digest_changed')
        value = command(['/usr/bin/dpkg-deb', '-f', str(path), 'Package', 'Version', 'Architecture'])
        fields = dict(line.split(': ', 1) for line in value.splitlines())
        require(fields == {'Package': name, 'Version': version, 'Architecture': 'amd64'}, 'package_metadata_changed')


def simulate(folder, version):
    raw = command(['/usr/bin/apt-get', '--simulate', '--reinstall', '--no-remove', '--no-download', 'install', *package_paths(folder, version)])
    names = []
    for line in raw.splitlines():
        require(not line.startswith('Remv '), 'package_removal_refused')
        if line.startswith('Inst '):
            match = re.match(r'Inst ([^ :]+)(?::amd64)? .*\(' + re.escape(version) + r'\b', line)
            require(match is not None, 'unexpected_install_plan')
            names.append(match[1])
    require(set(names) == set(PACKAGES) and len(names) == 2, 'unexpected_install_closure')


@contextmanager
def no_restarts():
    require(not os.path.lexists(POLICY), 'preexisting_service_policy_refused')
    fd = os.open(POLICY, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o755)
    try:
        require(os.write(fd, POLICY_BODY) == len(POLICY_BODY), 'policy_write_incomplete')
        os.fchmod(fd, 0o755)
        os.fsync(fd)
        owned = identity(os.fstat(fd))
    finally:
        os.close(fd)
    try:
        yield
    finally:
        require(identity(POLICY.lstat()) == owned and read(POLICY) == POLICY_BODY, 'owned_service_policy_changed')
        POLICY.unlink()


def action(mode, source):
    native(recovery=mode == 'rollback')
    initial = versions()
    require(initial == {n: OLD for n in PACKAGES} if mode in {'check', 'stage', 'install'} else all(v in {OLD, NEW} for v in initial.values()), 'wrong_baseline_versions')
    selected = bundle(source)
    original_runtime, original_units = runtime(), units()
    if mode == 'check':
        return {'status': 'native_brotli_checked', 'host_installed': False, 'consumer_refresh_required': list(UNITS)}
    safe_parent(MARKER)
    old_marker = read(MARKER, 8192)
    fd = os.open(MARKER, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    stack = ExitStack()
    stack.callback(os.close, fd)
    try:
        marker_identity = identity(os.fstat(fd))
        require(marker_identity == identity(MARKER.lstat()), 'production_marker_changed')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stack.enter_context(legacy_source_guard())
        native(recovery=mode == 'rollback', source_held=True)
        if mode == 'stage':
            safe_parent(ROOT)
            os.mkdir(ROOT, 0o700)
            for name in ('candidate', 'rollback'):
                os.mkdir(ROOT / name, 0o700)
            for name, raw in selected.items():
                write(ROOT / 'candidate' / name, raw, exclusive=True)
            command(['/usr/bin/apt-get', 'download', *[n + '=' + OLD for n in PACKAGES]], cwd=ROOT / 'rollback', seconds=120)
            verify_debs(ROOT / 'rollback', OLD, BASELINE)
            verify_debs(ROOT / 'candidate', NEW, INPUTS)
            simulate(ROOT / 'candidate', NEW)
            receipt = {'schema': 'native_brotli_install/1', 'phase': 'staged_not_installed', 'boot_id': boot_id(),
                       'runtime': original_runtime, 'units': original_units, 'candidate_bundle_sha256': BUNDLE_SHA,
                       'candidate_packages': INPUTS, 'rollback_packages': BASELINE,
                       'cleanup_review_due_utc': (datetime.now(timezone.utc) + timedelta(days=10)).isoformat(),
                       'security_transition_completed': False, 'consumer_refresh_required': list(UNITS)}
            write(ROOT / 'receipt.json', (json.dumps(receipt, sort_keys=True) + '\n').encode(), exclusive=True)
        else:
            receipt = json.loads(read(ROOT / 'receipt.json'))
            require(receipt['schema'] == 'native_brotli_install/1' and receipt['candidate_bundle_sha256'] == BUNDLE_SHA
                    and receipt['boot_id'] == boot_id()
                    and receipt['runtime'] == original_runtime, 'native_staged_baseline_changed')
            verify_debs(ROOT / 'rollback', OLD, BASELINE)
            verify_debs(ROOT / 'candidate', NEW, INPUTS)
            target, folder = (OLD, 'rollback') if mode == 'rollback' else (NEW, 'candidate')
            require(receipt['phase'] in ({'on_disk_patched_consumers_pending', 'install_started'} if mode == 'rollback' else {'staged_not_installed'}), 'wrong_install_phase')
            if mode == 'rollback':
                require(dirty_packages() <= set(PACKAGES), 'unowned_dpkg_recovery_refused')
            simulate(ROOT / folder, target)
            receipt.update(phase='rollback_started' if mode == 'rollback' else 'install_started', completion_unverified=True)
            write(ROOT / 'receipt.json', (json.dumps(receipt, sort_keys=True) + '\n').encode(), exclusive=False)
            with no_restarts():
                # The real CI installed these exact local files with dpkg.
                # No apt install hooks, dependency upgrade or package removal.
                command(['/usr/bin/dpkg', '--install', *package_paths(ROOT / folder, target)], seconds=120)
            require(versions() == {n: target for n in PACKAGES} and not command(['/usr/bin/dpkg', '--audit']).strip()
                    and not command(['/usr/bin/dpkg', '--verify', *PACKAGES]).strip(), 'installed_package_verification_failed')
            installed_api(target)
            require(runtime() == original_runtime and units() == original_units, 'native_runtime_changed')
            receipt.update(phase='rolled_back_security_open' if mode == 'rollback' else 'on_disk_patched_consumers_pending',
                           completion_unverified=True, security_transition_completed=False, services_restarted=False)
            write(ROOT / 'receipt.json', (json.dumps(receipt, sort_keys=True) + '\n').encode(), exclusive=False)
        require(read(MARKER, 8192) == old_marker and identity(os.fstat(fd)) == identity(MARKER.lstat()) == marker_identity,
                'production_marker_changed')
        return {'status': receipt['phase'], 'security_transition_completed': False, 'services_restarted': False,
                'consumer_refresh_required': list(UNITS), 'completion_unverified': True}
    finally:
        stack.close()


def main():
    global DEADLINE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path)
    parser.add_argument('action', choices=('check', 'stage', 'install', 'rollback'), nargs='?', default='check')
    args = parser.parse_args()
    DEADLINE = time.monotonic() + 210
    def expired(*_):
        raise RuntimeError('native_brotli_deadline')
    for signum in (signal.SIGALRM, signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, expired)
    signal.alarm(210)
    print(json.dumps(action(args.action, args.bundle), sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        value = str(error) if isinstance(error, RuntimeError) else ''
        print(json.dumps({'status': 'native_brotli_refused', 'reason_code': value if re.fullmatch('[a-z0-9_]{1,96}', value) else type(error).__name__,
                          'security_transition_completed': False, 'completion_unverified': True}))
        raise SystemExit(1)
