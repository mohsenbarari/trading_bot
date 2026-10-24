#!/usr/bin/env python3
"""Build-only removal of pip and CPython's alternate installer wheel archives."""
import argparse
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import sysconfig

BUNDLED = {'pip-24.0-py3-none-any.whl', 'setuptools-79.0.1-py3-none-any.whl'}


def require(ok):
    if not ok: raise ValueError('runtime_installer_removal_refused')


def bundled_plan(stdlib):
    """Admit only CPython 3.11.17's two known bootstrap archives, never stdlib."""
    require(stdlib.resolve(strict=True) == stdlib)
    value = stdlib.lstat()
    require(stat.S_ISDIR(value.st_mode) and value.st_uid == os.geteuid() and not value.st_mode & 0o022)
    ensure = stdlib / 'ensurepip'
    if not ensure.exists():
        require(not ensure.is_symlink())
        return []
    value = ensure.lstat()
    require(stat.S_ISDIR(value.st_mode) and value.st_uid == os.geteuid() and
            not value.st_mode & 0o022 and ensure.resolve(strict=True) == ensure)
    folder = stdlib / 'ensurepip' / '_bundled'
    if not folder.exists():
        require(not folder.is_symlink())
        return []
    for directory in (folder,):
        value = directory.lstat()
        require(stat.S_ISDIR(value.st_mode) and value.st_uid == os.geteuid() and
                not value.st_mode & 0o022 and directory.resolve(strict=True) == directory)
    found = list(folder.iterdir())
    require(len(found) <= 2 and {path.name for path in found} <= BUNDLED)
    planned = []
    for path in found:
        value = path.lstat()
        require(stat.S_ISREG(value.st_mode) and value.st_uid == os.geteuid() and value.st_nlink == 1
                and not value.st_mode & 0o022 and 0 < value.st_size <= 8 * 1024 * 1024)
        planned.append((path, (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_nlink,
                               value.st_size, value.st_mtime_ns, value.st_ctime_ns)))
    return planned


def image_layout():
    # No configurable prefix/path, venv, old interpreter or ordinary invocation
    # is a mutation route. Dockerfiles explicitly opt in with isolated Python.
    require(sys.flags.isolated and sys.version_info[:3] == (3, 11, 17) and os.geteuid() == 0)
    require(sys.prefix == sys.base_prefix == '/usr/local')
    require(sysconfig.get_path('stdlib') == '/usr/local/lib/python3.11')
    require(sysconfig.get_config_var('WHEEL_PKG_DIR') in (None, ''))
    return Path('/usr/local/lib/python3.11')


def strip_installer():
    stdlib = image_layout()
    planned = bundled_plan(stdlib)  # Refuse unexpected archives before uninstall.
    subprocess.run([sys.executable, '-I', '-B', '-m', 'pip', '--disable-pip-version-check',
                    'uninstall', '--yes', 'pip'], stdin=subprocess.DEVNULL, check=True, timeout=60,
                   env={'PATH': '/usr/local/bin:/usr/bin:/bin', 'PIP_CONFIG_FILE': os.devnull,
                        'PIP_NO_INPUT': '1', 'PIP_DISABLE_PIP_VERSION_CHECK': '1'})
    for path, before in planned:
        value = path.lstat()
        require(before == (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_nlink,
                           value.st_size, value.st_mtime_ns, value.st_ctime_ns))
        path.unlink()
    require(not bundled_plan(stdlib))
    return {'status': 'PASS', 'pip_uninstalled': True, 'bootstrap_wheels_removed': len(planned),
            'installed_setuptools_and_wheel_retained': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image-build', action='store_true', required=True)
    parser.parse_args()
    try: result = strip_installer()
    except (ValueError, OSError, subprocess.SubprocessError):
        print('{"status":"FAIL","reason":"runtime_installer_removal_refused"}')
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__': raise SystemExit(main())
