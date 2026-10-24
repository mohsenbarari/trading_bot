"""Ordinary Debian vendor update, built offline against Trixie dependencies."""
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import time

BASE = '6.5+20250216-2'
VENDOR = '6.6+20260608-2'
VERSION = VENDOR + '~trixie+gold1'
SELECTED = frozenset(('ncurses-base', 'ncurses-bin', 'libtinfo6', 'libncursesw6'))
BASELINE = SELECTED | {'libncurses6', 'libncurses-dev'}
FAMILY = BASELINE | {'ncurses-term', 'ncurses-doc', 'ncurses-examples', 'lib32ncurses6',
                     'lib32ncursesw6', 'lib32tinfo6', 'lib32ncurses-dev'}
INPUTS = {
    'ncurses_6.6+20260608-2.dsc': (4163, 'd12b31c11fad1c5baf42e36d643edd72cecacec67c2d14e759da42a61759582a'),
    'ncurses_6.6+20260608.orig.tar.gz': (3806095, '3b91eb714ba61b9ebfcfe09cf8e7c1c45cf2e8a6282f5887fb942db514ae93bd'),
    'ncurses_6.6+20260608.orig.tar.gz.asc': (729, '46de216bd447b5855b7df45d3da5a85f382aa1fea778180459049b2d6ee3c9b9'),
    'ncurses_6.6+20260608-2.debian.tar.xz': (50996, '52a49c453121bd90d21edef53903beac9d9d8229e0ef4f49d3b0252e49932f4c'),
}
RECIPE = frozenset(('Dockerfile', 'build.py', 'verify.py'))
LOG_LIMIT = 32 * 1024**2


def require(value, code):
    if not value:
        raise RuntimeError(code)


def read(path, maximum=16 * 1024**2):
    path = Path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid in {0, os.geteuid()} and before.st_nlink == 1
                and not before.st_mode & 0o022 and 0 <= before.st_size <= maximum, 'input_metadata')
        raw = bytearray()
        while block := os.read(fd, min(65536, maximum + 1 - len(raw))):
            raw.extend(block)
            require(len(raw) <= maximum, 'input_bound')
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mode, s.st_uid, s.st_nlink, s.st_mtime_ns, s.st_ctime_ns)
        require(len(raw) == before.st_size and identity(before) == identity(os.fstat(fd)) == identity(path.lstat()), 'input_changed')
        return bytes(raw)
    finally:
        os.close(fd)


def environment():
    return {'PATH': '/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C.UTF-8',
            'HOME': '/work/home', 'SOURCE_DATE_EPOCH': '1792800000',
            'DEB_BUILD_OPTIONS': 'parallel=2', 'DEB_BUILD_PROFILES': 'noudeb',
            'PYTHONDONTWRITEBYTECODE': '1'}


def command(argv, cwd, log, timeout=120, extra=None):
    """Bound ordinary public tool output before persisting it."""
    child = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, env={**environment(), **(extra or {})}, start_new_session=True)
    deadline = time.monotonic() + timeout
    complete = False
    try:
        with Path(log).open('xb') as out, selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            total = 0
            while selector.get_map():
                require(time.monotonic() < deadline, 'command_timeout')
                for key, _ in selector.select(0.1):
                    block = os.read(key.fileobj.fileno(), min(65536, LOG_LIMIT + 1 - total))
                    if not block:
                        selector.unregister(key.fileobj)
                    else:
                        total += len(block)
                        require(total <= LOG_LIMIT, 'command_log_bound')
                        out.write(block)
            require(child.wait(timeout=max(0.1, deadline - time.monotonic())) == 0, 'command_status')
            complete = True
    finally:
        if not complete or child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        child.wait(timeout=3)
        child.stdout.close()


def validate_inputs(folder, *, toolchain=False):
    require({p.name for p in folder.iterdir()} == set(INPUTS) | ({'toolchain-packages.txt'} if toolchain else set()),
            'vendor_input_inventory')
    for name, (size, digest) in INPUTS.items():
        raw = read(folder / name, size)
        require(len(raw) == size and sha256(raw).hexdigest() == digest, 'vendor_input_identity')


def verify_prepared_inputs():
    # BuildKit preparation has no runtime-only /.dockerenv marker.
    require(os.geteuid() == 0 and Path(__file__).resolve() == Path('/recipe/build.py'), 'preparation_container_only')
    validate_inputs(Path('/inputs'))


def fields(path, work, name):
    log = work / ('fields-' + name + '.log')
    command(['/usr/bin/dpkg-deb', '-f', str(path), 'Package', 'Version', 'Architecture'], work, log, 20)
    return dict(line.split(': ', 1) for line in read(log, 65536).decode().splitlines())


def run(builder):
    require(re.fullmatch('sha256:[a-f0-9]{64}', builder) and os.geteuid() == 1001
            and Path('/.dockerenv').is_file(), 'nonroot_immutable_container_required')
    release = read(Path('/etc/os-release').resolve(), 65536).decode()
    require('ID=debian\n' in release and 'VERSION_CODENAME=trixie\n' in release, 'trixie_required')
    work, inputs, recipe = Path('/work'), Path('/inputs'), Path('/recipe')
    require(not tuple(work.iterdir()) and work.stat().st_uid == 1001, 'fresh_owned_work_required')
    require({p.name for p in recipe.iterdir()} == RECIPE, 'recipe_inventory')
    validate_inputs(inputs, toolchain=True)
    os.umask(0o077)
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError('build_deadline')))
    signal.alarm(4800)
    (work / 'home').mkdir()
    source = work / 'source'
    command(['/usr/bin/dpkg-source', '-x', str(inputs / 'ncurses_6.6+20260608-2.dsc'), str(source)],
            work, work / 'source-extract.log', 120)
    changelog = source / 'debian/changelog'
    original = read(changelog)
    require(original.startswith(('ncurses (' + VENDOR + ')').encode()), 'vendor_version_changed')
    packaging = {name: sha256(read(source / 'debian' / name)).hexdigest() for name in ('rules', 'control')}
    prefix = (f'ncurses ({VERSION}) trixie; urgency=medium\n\n'
              '  * Local Trixie rebuild of the unchanged Debian vendor release.\n'
              '  * No security acceptance or runtime activation is asserted.\n\n'
              ' -- Project local build <local-build@invalid>  Sat, 24 Oct 2026 00:00:00 +0000\n\n')
    changelog.write_bytes(prefix.encode() + original)
    command(['/usr/bin/dpkg-buildpackage', '-us', '-uc', '-b', '-Pnoudeb'], source, work / 'build.log', 3600)
    require(all(sha256(read(source / 'debian' / name)).hexdigest() == digest for name, digest in packaging.items()),
            'vendor_packaging_changed')
    artifacts, candidate, baseline = (work / name for name in ('artifacts', 'candidate', 'baseline'))
    for path in (artifacts, candidate, baseline):
        path.mkdir()
    records, seen = [], set()
    packages = sorted(work.glob('*.deb'))
    require(13 <= len(packages) <= 40 and not tuple(work.glob('*.udeb')), 'family_output_bound')
    for index, path in enumerate(packages):
        value = fields(path, work, str(index))
        name = value['Package']
        require(name not in seen and name.removesuffix('-dbgsym') in FAMILY and value['Version'] == VERSION
                and value['Architecture'] in {'all', 'amd64'}, 'family_identity')
        seen.add(name)
        raw = read(path, 32 * 1024**2)
        records.append({'name': name, 'path': path.name, 'version': VERSION, 'bytes': len(raw), 'sha256': sha256(raw).hexdigest()})
        command(['/usr/bin/dpkg-deb', '-x', str(path), str(candidate)], work, work / f'extract-{index}.log', 30)
        if name in SELECTED:
            (artifacts / path.name).write_bytes(raw)
    require({name for name in seen if not name.endswith('-dbgsym')} == FAMILY, 'full_family_missing')
    old_seen, old_records = set(), []
    for index, path in enumerate(sorted(Path('/baseline').glob('*.deb'))):
        value = fields(path, work, 'old-' + str(index))
        name = value['Package']
        require(name in BASELINE and name not in old_seen and value['Version'] == BASE, 'baseline_identity')
        old_seen.add(name)
        old_records.append({'name': name, 'version': BASE, 'sha256': sha256(read(path, 32 * 1024**2)).hexdigest()})
        command(['/usr/bin/dpkg-deb', '-x', str(path), str(baseline)], work, work / f'extract-old-{index}.log', 30)
    require(old_seen == BASELINE, 'baseline_missing')
    command(['/usr/local/bin/python3', '-I', '-B', str(recipe / 'verify.py'), str(baseline), str(candidate)],
            work, work / 'abi-tests.json', 240)
    abi = json.loads(read(work / 'abi-tests.json'))
    require(abi.get('status') == 'passed' and abi.get('ordinary_functional_compatibility') == 'passed'
            and set(abi.get('libraries', {})) == {'ncurses', 'ncursesw', 'tinfo'}
            and abi.get('interactive_upstream_suite') == 'NOT_TESTED' and abi.get('security_accepted') is False,
            'ordinary_compatibility_not_proven')
    build_records = []
    for pattern in ('*.changes', '*.buildinfo'):
        paths = list(work.glob(pattern))
        require(len(paths) == 1, 'build_record_missing')
        raw = read(paths[0])
        (artifacts / paths[0].name).write_bytes(raw)
        build_records.append({'path': paths[0].name, 'sha256': sha256(raw).hexdigest()})
    for name, origin, limit in (
        ('abi-tests.json', work / 'abi-tests.json', 1024**2),
        ('upstream-build.log', work / 'build.log', LOG_LIMIT),
        ('toolchain-packages.txt', inputs / 'toolchain-packages.txt', 1024**2),
    ):
        (artifacts / name).write_bytes(read(origin, limit))
    receipt = {'schema': 'trixie_ncurses_vendor_candidate/1', 'status': 'candidate_built_not_accepted',
               'builder_image_id': builder, 'vendor_source_version': VENDOR, 'version': VERSION,
               'all_family_packages': records, 'baseline_packages': old_records,
               'selected_runtime_packages': sorted(SELECTED),
               'inputs_sha256': {name: row[1] for name, row in INPUTS.items()}, 'build_records': build_records,
               'recipe_sha256': {p.name: sha256(read(p)).hexdigest() for p in recipe.iterdir()},
               'toolchain_inventory_sha256': sha256(read(inputs / 'toolchain-packages.txt')).hexdigest(),
               'vendor_packaging_sha256': packaging,
               'source_authentication': 'signed_debian_snapshot_sources_index_and_pinned_archive_bytes',
               'maintainer_signatures_authenticated': False, 'abi': abi,
               'vendor_package_build': 'passed', 'interactive_upstream_suite': 'NOT_TESTED',
               'security_accepted': False, 'raw_scanner_gate_waived': False,
               'remaining_gates': ['actual_App_same_payload_tests', 'unfiltered_HIGH_CRITICAL_scan', 'runtime_activation'],
               'production_installed': False}
    (artifacts / 'build-receipt.json').write_text(json.dumps(receipt, sort_keys=True) + '\n')
    for path in artifacts.iterdir():
        path.chmod(0o644)
    signal.alarm(0)
    print(json.dumps({'status': receipt['status'], 'security_accepted': False}))


if __name__ == '__main__':
    try:
        verify_prepared_inputs() if sys.argv[1:] == ['--verify-inputs'] else run(sys.argv[1])
    except Exception as error:
        code = error.args[0] if type(error) is RuntimeError and error.args else type(error).__name__
        print(json.dumps({'status': 'failed', 'code': code if isinstance(code, str) and re.fullmatch('[a-zA-Z0-9_]{1,80}', code) else 'unexpected_failure', 'security_accepted': False}))
        raise SystemExit(1) from None
