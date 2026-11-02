#!/usr/bin/env python3
"""Offline gate: patched psycopg2, matched libpq, system dynamic TLS libraries."""
import argparse
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import re
import ssl
import sys
import sysconfig
from packaging.requirements import Requirement
from packaging.version import Version

RUNTIME_BIN_DIRS = (Path('/usr/local/bin'), Path('/usr/bin'))


def require(ok):
    if not ok: raise ValueError('psycopg2_runtime_security_gate_failed')


def system_libraries(raw):
    require(len(raw) <= 2 * 1024 * 1024)
    found = set()
    for line in raw.splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) != 6: continue
        path = fields[5]
        name = Path(path).name
        family = next((family for family in ('libpq', 'libssl', 'libcrypto')
                       if re.match(r'^' + family + r'[-.]', name)), None)
        if family is None: continue
        require(not path.endswith(' (deleted)') and '.libs/' not in path and
                Path(path).is_absolute() and Path(path).resolve(strict=True).is_relative_to(Path('/usr/lib')))
        if family == 'libpq': require(name.startswith('libpq.so.5'))
        if family == 'libssl': require(name == 'libssl.so.3')
        if family == 'libcrypto': require(name == 'libcrypto.so.3')
        found.add(family)
    require(found == {'libpq', 'libssl', 'libcrypto'})
    return sorted(found)


def final_installer_absent():
    require(importlib.util.find_spec('pip') is None)
    for distribution in importlib.metadata.distributions():
        require(re.sub(r'[-_.]+', '-', distribution.metadata.get('Name', '')).lower() != 'pip')
        for raw in distribution.requires or ():
            dependency = Requirement(raw)
            require(dependency.name.lower() != 'pip' or
                    (dependency.marker is not None and not dependency.marker.evaluate({'extra': ''})))
    folders = {*RUNTIME_BIN_DIRS, Path(sysconfig.get_path('scripts'))}
    for folder in folders:
        count = 0
        for path in folder.iterdir():
            count += 1
            require(count <= 4096 and re.fullmatch(r'pip(?:[0-9]+(?:\.[0-9]+)?)?', path.name, re.I) is None)
    require(sysconfig.get_config_var('WHEEL_PKG_DIR') in (None, ''))
    for key in ('purelib', 'platlib'):
        for count, path in enumerate(Path(sysconfig.get_path(key)).iterdir(), 1):
            require(count <= 4096 and not path.name.lower().startswith(('pip-', '~ip'))
                    and path.name.lower() != 'pip')
    ensure = Path(sysconfig.get_path('stdlib')) / 'ensurepip'
    require(not ensure.is_symlink())
    if ensure.exists(): require(ensure.resolve(strict=True) == ensure)
    bundle = ensure / '_bundled'
    require(not bundle.is_symlink())
    if bundle.exists():
        require(bundle.resolve(strict=True) == bundle)
        # The intentional empty archive directory keeps unrelated stdlib intact.
        require(not any(bundle.iterdir()))


def verify(*, final_runtime=False):
    require(sys.version_info[:2] == (3, 11))
    if final_runtime: final_installer_absent()
    else: require(Version(importlib.metadata.version('pip')) >= Version('26.2.2'))
    distribution = importlib.metadata.distribution('psycopg2-binary')
    require(distribution.version == '2.9.13')
    require(not any(part.endswith('.libs') for path in distribution.files or [] for part in str(path).split('/')))
    import psycopg2
    from psycopg2 import extensions
    require(psycopg2.__version__.split()[0] == '2.9.13' and
            psycopg2.__libpq_version__ == extensions.libpq_version() >= 150000)
    require(ssl.OPENSSL_VERSION_INFO >= (3, 0, 0))
    with open('/proc/self/maps', encoding='ascii') as stream:
        raw = stream.read(2 * 1024 * 1024 + 1)
    libraries = system_libraries(raw)
    return {'status': 'PASS', 'distribution': 'psycopg2-binary', 'version': '2.9.13',
            'libpq_compile_runtime': extensions.libpq_version(), 'system_dynamic_libraries': libraries,
            'vendored_libraries': False, 'pip_policy': 'absent' if final_runtime else 'patched_build_tool'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package-root', choices=('/opt/market-python',))
    parser.add_argument('--final-runtime', action='store_true')
    args = parser.parse_args()
    if args.package_root: sys.path.insert(0, args.package_root)
    try: result = verify(final_runtime=args.final_runtime)
    except (ValueError, OSError, ImportError, importlib.metadata.PackageNotFoundError):
        print('{"status":"FAIL","reason":"psycopg2_runtime_security_gate_failed"}')
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__': raise SystemExit(main())
