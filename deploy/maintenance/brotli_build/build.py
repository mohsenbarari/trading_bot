#!/usr/bin/env python3
"""One offline Noble source backport build. No install or host apply entrypoint."""
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import resource
import shutil
import signal
import stat
import subprocess
import sys

VERSION = '1.1.0-2build2+security20261024.1'
SOURCE = {
    'brotli_1.1.0.orig.tar.gz': (512036, '10973f4b4199eafa1d5735ef661ddb2ec2f97319ee9fd1824d4aabe08cff5265'),
    'brotli_1.1.0-2build2.debian.tar.xz': (5644, '96603222d7b6edb3253a846852972b6bde5b86814907d5540249e6950980a943'),
    'brotli_1.1.0-2build2.dsc': (2401, 'c58421959677a80d942924685007e8d5b5d1160fd7f61a1f0a9062bf08835856'),
}
PATCH_SHA = '3c1e4587947e4c61dd9c7c4619b1ae12d6bedbfd3c33c23981bf31269e18d52c'
BEFORE = {
    'c/dec/decode.c': '4fd20d5f0c6d8afb007ddb90ce4b7015762ff881658798aad83a46766e589268',
    'c/include/brotli/decode.h': '20d0a87a96bc25a3af7557075be87be4393e88a5fb564db08e92884dee17d841',
}
AFTER = {
    'c/dec/decode.c': '79db5a1879540eb7c915ce948e8fb94adc1ff49be87dca01529b9283c6ba343e',
    'c/include/brotli/decode.h': '24809ccba4457752434b0987a9e69ca62791e508cb1aaaec3c8030c9cb86a2af',
}
PACKAGES = {'brotli', 'libbrotli1', 'libbrotli-dev', 'python3-brotli'}


def require(ok, code):
    if not ok: raise RuntimeError(code)


def read(path, maximum=2 * 1024 * 1024):
    path = Path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid() and not before.st_mode & 0o022
                and before.st_nlink == 1 and before.st_size <= maximum, 'input_metadata')
        data = bytearray()
        while chunk := os.read(fd, min(65536, maximum + 1 - len(data))):
            data.extend(chunk); require(len(data) <= maximum, 'input_bound')
        identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        require(identity(before) == identity(os.fstat(fd)) == identity(path.lstat()), 'input_changed')
        return bytes(data)
    finally: os.close(fd)


def pinned_inputs(root, recipe):
    for name, (size, digest) in SOURCE.items():
        raw = read(root / name)
        require(len(raw) == size and sha256(raw).hexdigest() == digest, 'source_digest')
    patch = read(recipe / 'CVE-2026-111497.patch', 65536)
    require(sha256(patch).hexdigest() == PATCH_SHA, 'patch_digest')
    require(set(re.findall(rb'^diff --git a/(\S+) b/\1$', patch, re.MULTILINE))
            == {name.encode() for name in BEFORE}, 'patch_scope')


def command(argv, *, directory, log, timeout=1200):
    def limits():
        resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 * 1024, 16 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    with open(log, 'xb') as output:
        subprocess.run(argv, cwd=directory, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                       timeout=timeout, check=True, preexec_fn=limits,
                       env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C.UTF-8', 'HOME': '/work/home',
                            'SOURCE_DATE_EPOCH': '1792800000', 'DEB_BUILD_OPTIONS': 'parallel=2',
                            'DEB_BUILD_PROFILES': '', 'EDITOR': 'true', 'PYTHONDONTWRITEBYTECODE': '1'})


def hashes(root):
    return {str(p.relative_to(root)): sha256(read(p)).hexdigest()
            for p in root.rglob('*') if '.pc' not in p.relative_to(root).parts and p.is_file() and not p.is_symlink()}


def run(builder_image, work=Path('/work'), inputs=Path('/inputs'), recipe=Path('/recipe'), baseline=Path('/baseline')):
    # These fixed image paths/identity are intentionally not a host-build mode.
    require(work == Path('/work') and inputs == Path('/inputs') and recipe == Path('/recipe'), 'builder_paths_only')
    require(re.fullmatch('sha256:[0-9a-f]{64}', builder_image), 'builder_identity_required')
    release = dict(line.split('=', 1) for line in read(Path('/etc/os-release').resolve(strict=True)).decode().splitlines() if '=' in line)
    require(release.get('ID') == 'ubuntu' and release.get('VERSION_ID', '').strip('"') == '24.04', 'not_noble')
    require(Path('/.dockerenv').is_file() and os.geteuid() == 0, 'not_isolated_builder')
    require(not list(work.iterdir()), 'output_not_empty')
    os.umask(0o077)
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError('build_total_deadline')))
    signal.alarm(1500)
    pinned_inputs(inputs, recipe)
    for name in SOURCE: shutil.copyfile(inputs / name, work / name)
    (work / 'home').mkdir(mode=0o700)
    command(['/usr/bin/dpkg-source', '-x', str(work / 'brotli_1.1.0-2build2.dsc'), str(work / 'source')],
            directory=work, log=work / 'unpack.log', timeout=60)
    source = work / 'source'
    original = hashes(source)
    for name, digest in BEFORE.items(): require(original[name] == digest, 'patch_preimage')
    patch = str(recipe / 'CVE-2026-111497.patch')
    command(['/usr/bin/patch', '--dry-run', '--batch', '--forward', '--fuzz=0', '-p1', '-i', patch], directory=source, log=work / 'patch-check.log', timeout=30)
    command(['/usr/bin/patch', '--batch', '--forward', '--fuzz=0', '-p1', '-i', patch], directory=source, log=work / 'patch.log', timeout=30)
    changed = hashes(source)
    require(set(original) == set(changed) and {p for p in changed if changed[p] != original[p]} == set(AFTER), 'unexpected_source_change')
    for name, digest in AFTER.items(): require(changed[name] == digest, 'patch_postimage')
    command(['/usr/bin/dpkg-source', '--commit', '.', 'CVE-2026-111497.patch'], directory=source, log=work / 'quilt.log', timeout=30)
    changelog = source / 'debian/changelog'
    prefix = (f'brotli ({VERSION}) noble; urgency=medium\n\n'
              '  * Backport Google da9806362874b9bd4b552a3af360b5d6347beb12 (CVE-2026-111497).\n\n'
              ' -- Project local security build <local-build@invalid>  Sat, 24 Oct 2026 00:00:00 +0000\n\n').encode()
    changelog.write_bytes(prefix + read(changelog))
    allowed = {*AFTER, 'debian/changelog', 'debian/patches/series', 'debian/patches/CVE-2026-111497.patch'}
    final = hashes(source)
    require(set(original) <= set(final) and all(final[p] == old for p, old in original.items() if p not in allowed)
            and set(final) - set(original) <= allowed, 'packaging_drift')
    command(['/usr/bin/dpkg-buildpackage', '-us', '-uc', '-b'], directory=source, log=work / 'build.log')
    output = work / 'artifacts'; output.mkdir(mode=0o700)
    for folder in ('candidate', 'old'): (work / folder).mkdir(mode=0o700)
    observed = {}
    for index, package in enumerate(sorted(work.glob('*.deb'))):
        log = work / f'package-{index}.log'
        command(['/usr/bin/dpkg-deb', '-f', str(package), 'Package', 'Version', 'Architecture'], directory=work, log=log, timeout=15)
        fields = dict(line.split(': ', 1) for line in read(log).decode().splitlines())
        require(fields['Package'] in PACKAGES and fields['Package'] not in observed
                and fields['Version'] == VERSION and fields['Architecture'] == 'amd64', 'unexpected_package')
        observed[fields['Package']] = package.name
        command(['/usr/bin/dpkg-deb', '-x', str(package), str(work / 'candidate')], directory=work, log=work / f'extract-new-{index}.log', timeout=30)
        shutil.copyfile(package, output / package.name)
    require(set(observed) == PACKAGES, 'missing_package')
    old_names = set()
    for index, package in enumerate(sorted(baseline.glob('*.deb'))):
        log = work / f'old-package-{index}.log'
        command(['/usr/bin/dpkg-deb', '-f', str(package), 'Package', 'Version', 'Architecture'], directory=work, log=log, timeout=15)
        fields = dict(line.split(': ', 1) for line in read(log).decode().splitlines())
        require(fields['Package'] in PACKAGES and fields['Package'] not in old_names
                and fields['Version'] == '1.1.0-2build2' and fields['Architecture'] == 'amd64', 'old_package_identity')
        old_names.add(fields['Package'])
        command(['/usr/bin/dpkg-deb', '-x', str(package), str(work / 'old')], directory=work, log=work / f'extract-old-{index}.log', timeout=30)
    require(old_names == PACKAGES, 'old_package_missing')
    command(['/usr/bin/python3', '-I', '-B', '-S', str(recipe / 'verify.py'), str(work / 'old'), str(work / 'candidate')],
            directory=work, log=work / 'candidate-tests.json', timeout=45)
    proof = json.loads(read(work / 'candidate-tests.json'))
    require(proof['status'] == 'passed', 'candidate_tests_failed')
    for pattern in ('*.buildinfo', '*.changes'):
        files = list(work.glob(pattern)); require(len(files) == 1, 'build_record_missing')
        shutil.copyfile(files[0], output / files[0].name)
    shutil.copyfile(work / 'candidate-tests.json', output / 'candidate-tests.json')
    receipt = {'schema': 'noble_brotli_source_build/1', 'status': 'passed', 'version': VERSION,
               'builder_image_id': builder_image, 'source_date_epoch': 1792800000,
               'maintainer_dsc_signature_verified': False,
               'source_sha256': {n: v[1] for n, v in SOURCE.items()}, 'patch_sha256': PATCH_SHA,
               'recipe_sha256': {p.name: sha256(read(p)).hexdigest() for p in recipe.iterdir() if p.is_file()},
               'packages': [{'name': n, 'path': f, 'bytes': (output / f).stat().st_size,
                             'sha256': sha256(read(output / f, 16 * 1024 * 1024)).hexdigest()} for n, f in sorted(observed.items())],
               'tests': proof, 'host_installed': False, 'services_changed': False}
    (output / 'build-receipt.json').write_text(json.dumps(receipt, sort_keys=True) + '\n')
    signal.alarm(0)
    print(json.dumps({'status': 'passed', 'packages': 4, 'host_installed': False}))


if __name__ == '__main__':
    try: run(sys.argv[1])
    except Exception as error:
        print(json.dumps({'status': 'failed', 'code': type(error).__name__, 'host_installed': False}))
        raise SystemExit(1)
