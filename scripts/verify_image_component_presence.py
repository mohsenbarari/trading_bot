"""Read-only component evidence, never a vulnerability waiver or acceptance gate.

Run only in a disposable immutable-image container without production mounts or
environment. A bounded package/filesystem inventory corroborates the vendor's
component-level advisory; it cannot rule out renamed/obfuscated embedded code.
"""
import argparse
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
import uuid

MAX_FILES = 100000
MAX_SECONDS = 45
ROOTS = ('/usr', '/lib', '/lib64', '/bin', '/sbin', '/app', '/opt', '/etc')
PACKAGES = ('libsystemd0', 'libudev1', 'perl-base', 'perl', 'perl-modules-5.40',
            'systemd', 'systemd-homed', 'systemd-home', 'libacl1', 'tar', 'coreutils',
            'libncursesw6', 'libtinfo6', 'ncurses-base', 'ncurses-bin')


def require(value, code):
    if not value:
        raise RuntimeError(code)


def read_regular(path, maximum):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= maximum, 'input_metadata')
        raw = bytearray()
        while block := os.read(fd, min(65536, maximum + 1 - len(raw))):
            raw.extend(block)
            require(len(raw) <= maximum, 'input_bound')
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        require(len(raw) == before.st_size and identity(before) == identity(os.fstat(fd)) == identity(path.lstat()), 'input_changed')
        return bytes(raw)
    finally:
        os.close(fd)


def packages(raw):
    found = {}
    for paragraph in raw.decode('utf-8').split('\n\n'):
        fields = {}
        for line in paragraph.splitlines():
            if line and not line[0].isspace() and ': ' in line:
                key, value = line.split(': ', 1)
                require(key not in fields, 'duplicate_package_field')
                fields[key] = value
        name = fields.get('Package')
        if fields.get('Status') == 'install ok installed' and name in PACKAGES:
            require(name not in found and re.fullmatch(r'[A-Za-z0-9.+:~_-]{1,128}', fields.get('Version', '')), 'invalid_package_record')
            found[name] = fields['Version']
    require(all(name in found for name in ('libsystemd0', 'libudev1', 'perl-base')), 'expected_runtime_packages_missing')
    return found


def component(path):
    name = path.name
    if ('systemd-homed' in name or 'systemd-homework' in name or name == 'homectl'
            or name.startswith('org.freedesktop.home1')):
        return 'systemd_homed'
    parts = path.parts
    if (name == 'Tar.pm' and path.parent.name == 'Archive'
            or len(parts) >= 3 and parts[-3:-1] == ('Archive', 'Tar')):
        return 'archive_tar'
    return None


def inventory(roots, *, maximum=MAX_FILES, deadline=None):
    """No directory-symlink traversal; usr-merge aliases are scanned only once."""
    deadline = time.monotonic() + MAX_SECONDS if deadline is None else deadline
    seen_roots, matches, count = set(), {'systemd_homed': [], 'archive_tar': []}, 0
    for root in roots:
        try:
            root.lstat()
        except FileNotFoundError:
            continue
        root = root.resolve(strict=True)
        require(root.is_dir(), 'inventory_root_not_directory')
        if any(root == prior or root.is_relative_to(prior) for prior in seen_roots):
            continue
        seen_roots.add(root)
        def fail(_):
            raise RuntimeError('inventory_unreadable')
        for parent, dirs, files in os.walk(root, followlinks=False, onerror=fail):
            require(time.monotonic() < deadline, 'inventory_timeout')
            for name in (*dirs, *files):
                count += 1
                require(count <= maximum, 'inventory_count_bound')
                path = Path(parent) / name
                kind = component(path)
                if kind:
                    require(len(matches[kind]) < 64 and len(str(path)) <= 4096, 'component_match_bound')
                    matches[kind].append(str(path))
    return {'entries_examined': count, 'roots': sorted(map(str, seen_roots)),
            'matches': {key: sorted(value) for key, value in matches.items()}}


def bounded_command(argv, *, maximum=16384, timeout=10):
    """Cap stdout before accumulation; no shell, inherited env or disk spool."""
    child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C.UTF-8'},
                             start_new_session=True)
    data = bytearray()
    deadline = time.monotonic() + timeout
    completed = False
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            while selector.get_map():
                require(time.monotonic() < deadline, 'command_timeout')
                for key, _ in selector.select(min(0.1, max(0, deadline - time.monotonic()))):
                    block = os.read(key.fileobj.fileno(), min(4096, maximum + 1 - len(data)))
                    if not block:
                        selector.unregister(key.fileobj)
                    else:
                        data.extend(block)
                        require(len(data) <= maximum, 'command_output_bound')
        require(child.wait(timeout=max(0.01, deadline - time.monotonic())) == 0, 'command_failed')
        completed = True
        return bytes(data)
    finally:
        if not completed or child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        child.wait(timeout=2)
        child.stdout.close()


def perl_paths():
    raw = bounded_command(['/usr/bin/perl', '-T', '-e', 'print "$_\\n" for @INC;'])
    values = raw.decode('utf-8').splitlines()
    require(0 < len(values) <= 64 and len(set(values)) == len(values), 'perl_inc_invalid')
    for value in values:
        require(value.startswith('/') and len(value) <= 4096 and all(ord(c) >= 32 for c in value)
                and '..' not in Path(value).parts, 'perl_inc_invalid')
    return values


def module_present(path):
    # lexists() turns unreadable/invalid-path errors into false absence.
    # Only a real ENOENT permits the component-absence conclusion.
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def probe(image, commit, verifier, *, root=Path('/')):
    require(re.fullmatch('sha256:[a-f0-9]{64}', image) and re.fullmatch('[a-f0-9]{40}', commit)
            and re.fullmatch('[a-f0-9]{64}', verifier), 'identity_invalid')
    found = packages(read_regular(root / 'var/lib/dpkg/status', 4 * 1024 * 1024))
    scan = inventory([root / item.lstrip('/') for item in ROOTS])
    inc = perl_paths()
    module_matches = [value for value in inc if module_present(root / value.lstrip('/') / 'Archive/Tar.pm')]
    homed = not scan['matches']['systemd_homed']
    archive = not scan['matches']['archive_tar'] and not module_matches
    return {'schema': 'security_image_component_presence/1', 'image_id': image,
            'source_commit': commit, 'verifier_sha256': verifier, 'packages': found,
            'inventory': scan, 'perl_inc': inc, 'perl_inc_archive_tar_matches': module_matches,
            'components': {
                'systemd_homed': {'advisory': 'CVE-2026-16742', 'component_absent': homed},
                'archive_tar': {'advisory': 'CVE-2026-9538', 'component_absent': archive}},
            'scope': 'known_vendor_component_paths_and_names_plus_actual_perl_inc',
            'directory_symlinks_followed': False, 'dynamic_runtime_code_downloads_analyzed': False,
            'renamed_or_obfuscated_embedded_code_analyzed': False,
            'production_mounts_or_data_used': False, 'scanner_findings_modified': False,
            'security_accepted': False}


def in_container(image, commit, raw, expected):
    """CI-only public buffer: execute exactly the pre-NPM sealed verifier bytes."""
    require(0 < len(raw) <= 32768 and sha256(raw).hexdigest() == expected, 'verifier_mismatch')
    require(re.fullmatch('sha256:[a-f0-9]{64}', image) and re.fullmatch('[a-f0-9]{40}', commit), 'identity_invalid')
    bootstrap = ('import hashlib,sys;raw=bytes.fromhex(sys.argv.pop(1));expected=sys.argv.pop(1);'
                 'assert len(raw)<=32768 and hashlib.sha256(raw).hexdigest()==expected;'
                 'exec(compile(raw,"<sealed-component-probe>","exec"),{"__name__":"__main__"})')
    owner = uuid.uuid4().hex
    name = 'security-component-probe-' + owner
    docker = ['/usr/bin/docker', '--host=unix:///var/run/docker.sock']
    argv = [*docker, 'run', '--rm', '--pull=never', '--name', name,
            '--label', 'io.gold-trade.component-probe=' + owner,
            '--network', 'none', '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
            '--user', '0:0', '--memory', '256m', '--cpus', '1', '--pids-limit', '64',
            '--entrypoint', '/usr/local/bin/python', image, '-I', '-B', '-c', bootstrap,
            raw.hex(), expected, '--image-id', image, '--source-commit', commit, '--verifier-sha256', expected]
    try:
        output = bounded_command(argv, maximum=65536, timeout=75)
    finally:
        cleanup_container(docker, name, owner)
    record = json.loads(output)
    require(record.get('schema') == 'security_image_component_presence/1' and record.get('image_id') == image
            and record.get('source_commit') == commit and record.get('verifier_sha256') == expected
            and record.get('scanner_findings_modified') is False and record.get('security_accepted') is False,
            'receipt_identity_mismatch')
    return output


def cleanup_container(docker, name, owner):
    """A timed-out Docker client is not proof that its daemon work stopped."""
    listed = bounded_command([*docker, 'container', 'ls', '--all', '--no-trunc',
                              '--filter', 'name=^/' + name + '$', '--format', '{{.ID}}'], maximum=256, timeout=5)
    if not listed.strip():
        return
    cid = listed.decode('ascii').strip()
    require(re.fullmatch('[a-f0-9]{64}', cid), 'cleanup_inventory_invalid')
    template = '{"id":{{json .Id}},"name":{{json .Name}},"owner":{{json (index .Config.Labels "io.gold-trade.component-probe")}}}'
    actual = json.loads(bounded_command([*docker, 'inspect', '--type', 'container', '--format', template, cid],
                                       maximum=1024, timeout=5))
    require(actual == {'id': cid, 'name': '/' + name, 'owner': owner}, 'cleanup_owner_mismatch')
    bounded_command([*docker, 'rm', '--force', cid], maximum=256, timeout=10)
    absent = bounded_command([*docker, 'container', 'ls', '--all', '--no-trunc',
                              '--filter', 'name=^/' + name + '$', '--format', '{{.ID}}'], maximum=256, timeout=5)
    require(not absent.strip(), 'cleanup_incomplete')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image-id', required=True)
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--verifier-sha256', required=True)
    args = parser.parse_args()
    receipt = probe(args.image_id, args.source_commit, args.verifier_sha256)
    raw = (json.dumps(receipt, sort_keys=True) + '\n').encode()
    require(len(raw) <= 65536, 'receipt_bound')
    sys.stdout.buffer.write(raw)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('{"status":"refused","code":"component_probe_failed"}', file=sys.stderr)
        raise SystemExit(1) from None
