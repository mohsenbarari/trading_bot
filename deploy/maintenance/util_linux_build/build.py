#!/usr/bin/env python3
"""Finite offline full-family util-linux build; never a host apply command."""
from hashlib import sha256
import io
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import resource
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import time
import urllib.request

VERSION = '2.41.6-0+deb13u0+gold1'
BASE_VERSION = '2.41.5-0+deb13u1'
SELECTED = frozenset(('bsdutils', 'libblkid1', 'liblastlog2-2', 'libmount1',
                      'libsmartcols1', 'libuuid1', 'login', 'mount', 'util-linux'))
DEVEL = frozenset(('libblkid-dev', 'libfdisk-dev', 'liblastlog2-dev',
                   'libmount-dev', 'libsmartcols-dev', 'uuid-dev'))
BASELINE = SELECTED | DEVEL | {'libfdisk1'}
FULL_FAMILY = BASELINE | {'bsdextrautils', 'eject', 'fdisk', 'lastlog2', 'libpam-lastlog2',
                         'rfkill', 'util-linux-extra', 'util-linux-locales', 'uuid-runtime'}
DEBIAN_URL = 'https://deb.debian.org/debian/pool/main/u/util-linux/'
KERNEL_URL = 'https://www.kernel.org/pub/linux/utils/util-linux/v2.41/'
PATCH_URL = 'https://github.com/util-linux/util-linux/commit/'
INPUTS = {
    'util-linux-2.41.6.tar.xz': (KERNEL_URL + 'util-linux-2.41.6.tar.xz', 9470680,
        'e596083744e746be7d2823b62b43f4418dd7bf56303b4dc09e6fe8112fe3d7ed'),
    'util-linux_2.41.5.orig.tar.xz': (DEBIAN_URL + 'util-linux_2.41.5.orig.tar.xz', 9474992,
        'f586e35d320ff537aab3ffeca37e9ecd482ccbe013590db4429a414d8aa6a728'),
    'util-linux_2.41.5-0+deb13u1.debian.tar.xz':
        (DEBIAN_URL + 'util-linux_2.41.5-0+deb13u1.debian.tar.xz', 107604,
         '5b327ccd22f0f4ed28a389870aa51d04ecedb8693e52a1d122850f2b3188cbf6'),
    'util-linux_2.41.5-0+deb13u1.dsc': (DEBIAN_URL + 'util-linux_2.41.5-0+deb13u1.dsc', 4988,
        '43e9b2cbebd10fdc598c4ad10217c8202c28de53af6eafc892c8e9b5cbf3a3a5'),
    '79c2881c.patch': (PATCH_URL + '79c2881c27a0b40889cd5433d9f125689826d1d2.patch', 1271,
        'ad095c0456e71636c2b9fd9c41fb24031ad5ab66dfac9e3fd6a9f40cfcecf0ab'),
    '485dbb67.patch': (PATCH_URL + '485dbb67f1b6bb18e08b1b77f4aa2373ff3a705b.patch', 2620,
        '0b30248db96c77926ab9ad33474003bb1b13d652368dc754a84ac51ec61f7220'),
    '233cf732.patch': (PATCH_URL + '233cf7321e9d0fd2cea901d0a97e565c725640ad.patch', 4383,
        '6f98c72584bbc602869f663b4e1f1394cd45f8c086199241c3e2a4293f401b4e'),
}
ORIGINAL_SERIES = (
    'debian/man-getopt-examples.patch', 'debian/verbose-tests.patch',
    'debian/hardlink-tests-known-failed.patch', 'debian/lsfd-usrbin.patch',
    'debian/sensible-pager.patch', 'debian/login-support-LOG_OK_LOGINS.patch',
    'debian/login-do-not-print-motd-done-by-PAM.patch',
    'debian/login-turn-off-btmp-utmp-lastlog-writing.patch', 'debian/salsa-ci-skip-tests.patch',
    'upstream/loopdev-use-openat2-RESOLVE_NO_SYMLINKS-for-backing-file.patch',
    'upstream/libmount-restrict-source-path-canonicalization-for-non-ro.patch',
)
FOLLOWUPS = ('79c2881c.patch', '485dbb67.patch', '233cf732.patch')
LOG_LIMIT = 32 * 1024**2
ERROR_CODES = frozenset(('actual_upstream_test_count_missing', 'archive_member', 'archive_total_bound',
    'baseline_family_missing', 'baseline_identity', 'build_record_missing', 'candidate_tests_failed',
    'command_deadline', 'command_failed', 'command_log_bound', 'disabled_mountfd_enabled',
    'empty_owned_work_required', 'family_identity', 'fetch_builder_only', 'full_family_missing_or_extra',
    'full_family_output_bound', 'immutable_builder_required', 'input_bound', 'input_changed',
    'input_metadata', 'inputs_not_empty', 'main_config_identity', 'main_mountfd_not_enabled',
    'nonroot_container_required', 'offline_input_digest', 'packaging_series_changed',
    'public_input_digest', 'trixie_only', 'upstream_suite_skipped_as_root',
    'archive_symlink_prefix', 'primary_distclean_incomplete'))


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def refusal_code(error):
    value = error.args[0] if type(error) is RuntimeError and error.args else None
    return value if isinstance(value, str) and value in ERROR_CODES else type(error).__name__


def read(path, maximum=16 * 1024**2):
    path = Path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid in (0, os.geteuid())
                and not before.st_mode & 0o022 and before.st_nlink == 1
                and 0 <= before.st_size <= maximum, 'input_metadata')
        data = bytearray()
        while chunk := os.read(fd, min(65536, maximum + 1 - len(data))):
            data.extend(chunk)
            require(len(data) <= maximum, 'input_bound')
        identity = lambda s: (s.st_dev, s.st_ino, s.st_uid, s.st_mode, s.st_nlink,
                              s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        require(len(data) == before.st_size and identity(before) == identity(os.fstat(fd))
                == identity(path.lstat()), 'input_changed')
        return bytes(data)
    finally:
        os.close(fd)


def expected_version(name, version=VERSION):
    return ('1:' if name == 'bsdutils' else '1:4.16.0-2+really' if name == 'login' else '') + version


def fetch_inputs():
    # BuildKit RUN deliberately lacks Docker's runtime-only /.dockerenv.
    # This preparation only downloads pinned public inputs into /inputs; bind
    # the sealed recipe location instead of misclassifying a normal build.
    require(os.geteuid() == 0 and Path(__file__).resolve(strict=True) == Path('/recipe/build.py'),
            'fetch_builder_only')
    require(not tuple(Path('/inputs').iterdir()), 'inputs_not_empty')
    for name, (url, size, digest) in INPUTS.items():
        with urllib.request.urlopen(url, timeout=45) as response:
            raw = response.read(size + 1)
        require(len(raw) == size and sha256(raw).hexdigest() == digest, 'public_input_digest')
        fd = os.open(Path('/inputs') / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, 'wb') as output:
            output.write(raw)


def unpack(raw, target, prefix):
    """Bounded public source extraction; reject special nodes and escaping links."""
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:xz') as archive:
        members = archive.getmembers()
        require(len(members) <= 20000 and sum(m.size for m in members) <= 256 * 1024**2,
                'archive_total_bound')
        for member in members:
            parts = PurePosixPath(member.name).parts
            require(parts and parts[0] == prefix and not PurePosixPath(member.name).is_absolute()
                    and '..' not in parts and not member.isdev() and not member.isfifo()
                    and not member.islnk() and member.size <= 16 * 1024**2, 'archive_member')
            if member.issym():
                link = PurePosixPath(member.linkname)
                resolved = PurePosixPath(posixpath.normpath(str(PurePosixPath(member.name).parent / link)))
                require(member.linkname and not link.is_absolute() and resolved.parts
                        and resolved.parts[0] == prefix and '..' not in resolved.parts, 'archive_symlink_prefix')
        archive.extractall(target, filter='data')
    return target / prefix


def environment():
    return {'PATH': '/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C.UTF-8',
            'HOME': '/work/home', 'SOURCE_DATE_EPOCH': '1792800000',
            'DEB_BUILD_OPTIONS': 'parallel=2', 'DEB_BUILD_PROFILES': 'noudeb',
            'QUILT_PATCHES': 'debian/patches', 'QUILT_PATCH_OPTS': '--fuzz=0',
            'PYTHONDONTWRITEBYTECODE': '1'}


def command(argv, directory, log, timeout=120, extra=None):
    def limits():
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    # Sparse upstream disk fixtures may legitimately exceed an arbitrary
    # RLIMIT_FSIZE. Bound the LOG while streaming instead of changing tests.
    with Path(log).open('xb') as output:
        child = subprocess.Popen(argv, cwd=directory, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, start_new_session=True,
                                 env={**environment(), **(extra or {})}, preexec_fn=limits)
        selector = selectors.DefaultSelector(); deadline = time.monotonic() + timeout; total = 0
        try:
            selector.register(child.stdout, selectors.EVENT_READ)
            while selector.get_map():
                require(time.monotonic() < deadline, 'command_deadline')
                for key, _ in selector.select(0.1):
                    block = os.read(key.fileobj.fileno(), 65536)
                    if not block:
                        selector.unregister(key.fileobj)
                    else:
                        total += len(block); require(total <= LOG_LIMIT, 'command_log_bound')
                        output.write(block)
            status = child.wait(timeout=max(0.1, deadline - time.monotonic()))
            if status:
                name = Path(log).name
                require(re.fullmatch(r'[a-zA-Z0-9._-]{1,100}', name), 'command_failed')
                print(json.dumps({'schema': 'trixie_util_linux_failed_command/1',
                                  'public_log': name, 'exit_code': status}), flush=True)
            require(status == 0, 'command_failed')
        finally:
            selector.close()
            if child.poll() is None:
                try: os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError: pass
                try: child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    try: os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                    child.wait(timeout=3)
            child.stdout.close()


def suite_evidence(log):
    raw = read(log, 128 * 1024**2).decode(errors='replace')
    require('Ignore util-linux test suite' not in raw, 'upstream_suite_skipped_as_root')
    counts = re.findall(r'All ([1-9][0-9]*) tests PASSED', raw)
    require(counts, 'actual_upstream_test_count_missing')
    return {'status': 'passed', 'reported_suite_script_counts': [int(n) for n in counts],
            'skip_status_lines': len(re.findall(r'(?im)^.*\bSKIPPED\b.*$', raw)),
            'skips_are_not_privileged_coverage': True}


def fields(package, work, index):
    log = work / f'fields-{index}.log'
    command(['/usr/bin/dpkg-deb', '-f', str(package), 'Package', 'Version', 'Architecture'], work, log, 20)
    return dict(line.split(': ', 1) for line in read(log, 65536).decode().splitlines())


def run(builder_image):
    require(re.fullmatch(r'sha256:[a-f0-9]{64}', builder_image), 'immutable_builder_required')
    require(os.geteuid() == 1001 and Path('/.dockerenv').is_file(), 'nonroot_container_required')
    release = read(Path('/etc/os-release').resolve(strict=True), 65536).decode()
    require('ID=debian\n' in release and 'VERSION_CODENAME=trixie\n' in release, 'trixie_only')
    work, inputs, recipe = Path('/work'), Path('/inputs'), Path('/recipe')
    require(not tuple(work.iterdir()) and work.stat().st_uid == 1001, 'empty_owned_work_required')
    os.umask(0o077)
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError('build_total_deadline')))
    signal.alarm(3600)
    (work / 'home').mkdir(mode=0o700)
    for name, (_, size, digest) in INPUTS.items():
        raw = read(inputs / name)
        require(len(raw) == size and sha256(raw).hexdigest() == digest, 'offline_input_digest')
    source = unpack(read(inputs / 'util-linux-2.41.6.tar.xz'), work, 'util-linux-2.41.6')
    oldsource = unpack(read(inputs / 'util-linux_2.41.5.orig.tar.xz'), work, 'util-linux-2.41.5')
    unpack(read(inputs / 'util-linux_2.41.5-0+deb13u1.debian.tar.xz'), source, 'debian')
    series = source / 'debian/patches/series'
    require(tuple(read(series).decode().splitlines()) == ORIGINAL_SERIES, 'packaging_series_changed')
    # Exactly two already-integrated patches leave the ACTIVE series. Their
    # original public files remain untouched for review; no behavior patch drops.
    series.write_text('\n'.join((*ORIGINAL_SERIES[:-2], *FOLLOWUPS)) + '\n')
    for name in FOLLOWUPS:
        (source / 'debian/patches' / name).write_bytes(read(inputs / name, 65536))
    command(['/usr/bin/quilt', '--quiltrc', '/dev/null', 'push', '-a'], source, work / 'quilt.log', 120)
    prefix = (f'util-linux ({VERSION}) trixie; urgency=medium\n\n'
              '  * Local stable 2.41.6 candidate with three pinned upstream follow-ups.\n'
              '  * CVE closure and final runtime activation are NOT asserted.\n\n'
              ' -- Project local security build <local-build@invalid>  Sat, 24 Oct 2026 00:00:00 +0000\n\n')
    changelog = source / 'debian/changelog'
    changelog.write_bytes(prefix.encode() + read(changelog))
    command(['/usr/bin/dpkg-buildpackage', '-us', '-uc', '-b', '-Pnoudeb'], source, work / 'build.log', 2700)
    suite = suite_evidence(work / 'build.log')
    artifacts = work / 'artifacts'; artifacts.mkdir(mode=0o755)
    candidate, old = work / 'candidate', work / 'old'
    candidate.mkdir(); old.mkdir()
    observed, package_records = {}, []
    packages = sorted(work.glob('*.deb'))
    require(0 < len(packages) <= 64 and not tuple(work.glob('*.udeb')), 'full_family_output_bound')
    for index, package in enumerate(packages):
        value = fields(package, work, str(index))
        name = value['Package']
        base_name = name.removesuffix('-dbgsym')
        require(base_name in FULL_FAMILY and name not in observed and value['Architecture'] in ('amd64', 'all')
                and value['Version'] == expected_version(base_name), 'family_identity')
        observed[name] = value
        command(['/usr/bin/dpkg-deb', '-x', str(package), str(candidate)], work, work / f'extract-new-{index}.log', 30)
        package_records.append({'name': name, 'path': package.name, 'version': value['Version'], 'bytes': package.stat().st_size,
                                'sha256': sha256(read(package, 64 * 1024**2)).hexdigest()})
        if name in SELECTED:
            dest = artifacts / package.name
            shutil.copyfile(package, dest); dest.chmod(0o644)
    require({n for n in observed if not n.endswith('-dbgsym')} == FULL_FAMILY, 'full_family_missing_or_extra')
    old_names, old_records = set(), []
    for index, package in enumerate(sorted(Path('/baseline').glob('*.deb'))):
        value = fields(package, work, 'old-' + str(index)); name = value['Package']
        require(name in BASELINE and name not in old_names and value['Version'] == expected_version(name, BASE_VERSION)
                and value['Architecture'] == 'amd64', 'baseline_identity')
        old_names.add(name)
        old_records.append({'name': name, 'version': value['Version'], 'path': package.name,
                            'bytes': package.stat().st_size,
                            'sha256': sha256(read(package, 64 * 1024**2)).hexdigest()})
        command(['/usr/bin/dpkg-deb', '-x', str(package), str(old)], work, work / f'extract-old-{index}.log', 30)
    require(old_names == BASELINE, 'baseline_family_missing')
    command(['/usr/local/bin/python3', '-I', '-B', str(recipe / 'verify.py'), str(old), str(candidate)],
            work, work / 'abi-tests.json', 150)
    # The main Debian build is full-featured. A separate out-of-tree target
    # actually compiles libmount with mountfd support DISABLED as well.
    statuses = list(source.rglob('config.status')); require(len(statuses) == 1, 'main_config_identity')
    config = read(statuses[0].parent / 'config.h').decode()
    require('#define USE_LIBMOUNT_MOUNTFD_SUPPORT 1' in config, 'main_mountfd_not_enabled')
    (artifacts / 'primary-config.h').write_text(config)
    (artifacts / 'primary-config.h').chmod(0o644)
    # Debian configures in-source. Respect Autoconf's source-state guard: the
    # completed primary build is cleaned normally before the separate variant.
    # Package/ABI/upstream evidence above is already captured outside the tree.
    command(['/usr/bin/make', 'distclean'], statuses[0].parent, work / 'primary-distclean.log', 180)
    require(not statuses[0].exists(), 'primary_distclean_incomplete')
    disabled = work / 'mountfd-disabled'; disabled.mkdir()
    command([str(source / 'configure'), '--disable-all-programs', '--enable-libmount',
             '--disable-libmount-mountfd-support', '--without-python'], disabled, work / 'disabled-configure.log', 120)
    require('#define USE_LIBMOUNT_MOUNTFD_SUPPORT 1' not in read(disabled / 'config.h').decode(), 'disabled_mountfd_enabled')
    command(['/usr/bin/make', '-j2', 'libmount.la'], disabled, work / 'disabled-build.log', 240)
    command(['/usr/local/bin/python3', '-I', '-B', str(recipe / 'source_regressions.py'),
             str(oldsource), str(source), str(disabled)], work, work / 'source-tests.json', 120)
    abi = json.loads(read(work / 'abi-tests.json')); regressions = json.loads(read(work / 'source-tests.json'))
    require(abi.get('status') == 'passed' and regressions.get('compiled_contracts') == 'passed', 'candidate_tests_failed')
    build_records = []
    for pattern in ('*.buildinfo', '*.changes'):
        outputs = list(work.glob(pattern)); require(len(outputs) == 1, 'build_record_missing')
        raw = read(outputs[0]); build_records.append({'path': outputs[0].name, 'sha256': sha256(raw).hexdigest()})
        shutil.copyfile(outputs[0], artifacts / outputs[0].name); (artifacts / outputs[0].name).chmod(0o644)
    toolchain = read(inputs / 'toolchain-packages.txt', 1024**2)
    (artifacts / 'toolchain-packages.txt').write_bytes(toolchain)
    (artifacts / 'toolchain-packages.txt').chmod(0o644)
    receipt = {'schema': 'trixie_util_linux_source_candidate/1', 'status': 'candidate_built_not_accepted',
               'builder_image_id': builder_image, 'version': VERSION, 'upstream_suite': suite,
               'all_family_packages': package_records, 'baseline_packages': old_records,
               'selected_runtime_packages': sorted(SELECTED), 'build_records': build_records,
               'toolchain_inventory_sha256': sha256(toolchain).hexdigest(),
               'primary_configuration_sha256': sha256(config.encode()).hexdigest(),
               'inputs_sha256': {n: row[2] for n, row in INPUTS.items()},
               'recipe_sha256': {p.name: sha256(read(p)).hexdigest() for p in recipe.iterdir()},
               'maintainer_signatures_authenticated': False, 'abi': abi, 'source_regressions': regressions,
               'security_accepted': False, 'raw_scanner_gate_waived': False,
               'remaining_gates': ['privileged_negative_controls', '78409_vendor_reconciliation',
                                   'actual_app_image_regressions', 'unfiltered_HIGH_CRITICAL_scan'],
               'production_installed': False, 'services_changed': False}
    for name in ('abi-tests.json', 'source-tests.json'):
        shutil.copyfile(work / name, artifacts / name); (artifacts / name).chmod(0o644)
    shutil.copyfile(work / 'build.log', artifacts / 'upstream-build.log')
    (artifacts / 'upstream-build.log').chmod(0o644)
    (artifacts / 'build-receipt.json').write_text(json.dumps(receipt, sort_keys=True) + '\n')
    (artifacts / 'build-receipt.json').chmod(0o644)
    signal.alarm(0)
    print(json.dumps({'status': receipt['status'], 'security_accepted': False, 'runtime_packages': 9}))


if __name__ == '__main__':
    try:
        fetch_inputs() if sys.argv[1:] == ['--fetch-inputs'] else run(sys.argv[1])
    except Exception as error:
        print(json.dumps({'status': 'failed', 'code': refusal_code(error), 'security_accepted': False}))
        raise SystemExit(1)
