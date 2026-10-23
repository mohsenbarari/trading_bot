#!/usr/bin/env python3
"""Read-only Web Docker-CE transition preparation, NOT an apply controller.

There is deliberately no --apply switch. A successful result attests only the
frozen public runtime/package baseline and (optionally) the offline deb set.
It does not attest backups, quiescence, downgrade safety, or remediation.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import resource
import stat
import subprocess
import sys
import tempfile

SCHEMA = 'web_engine_preflight/1'
MARKER = Path('/root/secure-envs/trading-bot/queue-cutover-artifacts/production-release.lock')
RESERVATION = Path('/root/secure-envs/trading-bot/release-control/private-primary-controller.lock')
PENDING = Path('/var/lib/trading-bot/production-release/two-host-release-state.json')
PROJECT_COUNTS = {
    'market-private-pipeline-primary': 7, 'current': 4,
    'trading_bot_staging_iran': 5, 'three-site-727c1f337b75-webapp-fi': 1,
    'three-site-dc32d90392b0-webapp-fi': 10,
}
# Sizes/digests are public package metadata, NOT secret/config fingerprints.
# CE: signed official Docker Resolute index dated 2026-10-23 03:57:03 UTC.
# Ubuntu rollback: native, authenticated Resolute apt package metadata.
NEW_PACKAGES = {
    'docker-ce': ('5:29.9.2-1~ubuntu.26.04~resolute', 24579432, 'd9e8759e6a88a8aee6cf37905f739d252b96f0bbc41a915398e92357ca774535'),
    'docker-ce-cli': ('5:29.9.2-1~ubuntu.26.04~resolute', 17597184, '809f799e7a341c9ef9c2a3dd287da468c21de03745f8f2d080a0904549781542'),
    'containerd.io': ('2.4.2-3~ubuntu.26.04~resolute', 23267328, 'e6197cb4f8b9efb2432e0bf242937e9270d25255a276ff7b810d7a0578782655'),
    'docker-compose-plugin': ('5.6.1-1~ubuntu.26.04~resolute', 8084608, '3dfea1021638130095750f372e98e61d8046f84a805c9fa7e072269894f75add'),
    'docker-buildx-plugin': ('0.38.1-1~ubuntu.26.04~resolute', 17801696, '423e4c264ccd33cd696365d19583b280d5e019ea7f9d00494f749db9b54376f6'),
}
OLD_PACKAGES = {
    'docker.io': ('29.1.3-0ubuntu4.1', 35510476, '7a77548bd669f232ec8852092623303be32c55538401ff047b4d1e0bb5ea721d'),
    'containerd': ('2.2.2-0ubuntu1.1', 28054046, '25f1410e781533bf19d22aa9181c6b57043c005cc30b635edf8a2afebe590fc0'),
    'runc': ('1.4.0-0ubuntu1', 9827920, '7282e0d92acb54eefb57f84849ab8b0102fdc5a64459a95194101718b283ceff'),
    'docker-compose-v2': ('2.40.3+ds1-0ubuntu1', 14760138, 'fb8bbea5614c65cbdf426a7fd13bfeaf0f777a8f4d62758fbbfa4f051be330da'),
}
INSPECT = ('{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Image}},'
           '"running":{{json .State.Running}},"restart_policy":{{json .HostConfig.RestartPolicy}},'
           '"project":{{json (index .Config.Labels "com.docker.compose.project")}},'
           '"mounts":{{json .Mounts}},"health":{{with index .State "Health"}}{{json .Status}}{{else}}null{{end}}}')


class Refusal(RuntimeError):
    """Static diagnostic only; never print exceptions from native tools."""


def require(ok, code):
    if not ok:
        raise Refusal(code)


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def safe_parent(path):
    require(path.is_absolute() and path.resolve(strict=False) == path, 'redirected_path')
    for parent in path.parents:
        info = parent.stat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid in {0, os.geteuid()}
                and not info.st_mode & 0o022, 'unsafe_parent')


def file_digest(path, *, maximum, private):
    path = Path(path)
    safe_parent(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                and before.st_uid == os.geteuid() and before.st_size <= maximum
                and not before.st_mode & (0o077 if private else 0o022), 'unsafe_file')
        result, size = sha256(), 0
        while chunk := os.read(descriptor, 65536):
            size += len(chunk)
            require(size <= maximum, 'file_bound')
            result.update(chunk)
        require(identity(before) == identity(os.fstat(descriptor)) == identity(path.lstat()), 'file_changed')
        return size, result.hexdigest()
    finally:
        os.close(descriptor)


def document(path, expected_digest):
    require(re.fullmatch('[0-9a-f]{64}', expected_digest) is not None, 'invalid_digest')
    # Read and validate the SAME descriptor; never hash then reopen a manifest.
    path = Path(path)
    safe_parent(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid()
                and before.st_nlink == 1 and not before.st_mode & 0o077
                and before.st_size <= 2 * 1024 * 1024, 'unsafe_manifest')
        data = bytearray()
        while chunk := os.read(descriptor, min(65536, 2 * 1024 * 1024 + 1 - len(data))):
            data.extend(chunk)
            require(len(data) <= 2 * 1024 * 1024, 'manifest_bound')
        require(identity(before) == identity(os.fstat(descriptor)) == identity(path.lstat()), 'manifest_changed')
        require(sha256(data).hexdigest() == expected_digest, 'manifest_digest_changed')
        def unique(pairs):
            value = {}
            for key, item in pairs:
                require(key not in value, 'duplicate_key')
                value[key] = item
            return value
        return json.loads(data, object_pairs_hook=unique)
    finally:
        os.close(descriptor)


def command(argv):
    binary = Path(argv[0]).resolve(strict=True)
    info = binary.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022, 'unsafe_binary')
    def bounded_child():
        resource.setrlimit(resource.RLIMIT_FSIZE, (2 * 1024 * 1024, 2 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    with tempfile.TemporaryFile() as output:
        try:
            subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.DEVNULL,
                           timeout=20, check=True, preexec_fn=bounded_child,
                           env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C',
                                'DOCKER_HOST': 'unix:///var/run/docker.sock'})
        except (subprocess.SubprocessError, OSError):
            raise Refusal('native_metadata_command_failed') from None
        require(output.tell() <= 2 * 1024 * 1024, 'native_output_bound')
        output.seek(0)
        return output.read(2 * 1024 * 1024).decode().strip()


def canonical_mounts(rows):
    result = [{key: row[key] for key in ('Destination', 'Mode', 'Propagation', 'RW', 'Source', 'Type')} for row in rows]
    require(len({row['Destination'] for row in result}) == len(result), 'duplicate_mount_destination')
    return sorted(result, key=lambda row: row['Destination'])


def baseline_contract(rows):
    require(isinstance(rows, list) and len(rows) == 27, 'container_count_changed')
    require(len({r['id'] for r in rows}) == 27 and all(re.fullmatch('[0-9a-f]{64}', r['id']) for r in rows), 'container_identity_invalid')
    counts = {project: sum(r['project'] == project for r in rows) for project in PROJECT_COUNTS}
    require(counts == PROJECT_COUNTS and all(r['project'] in PROJECT_COUNTS for r in rows), 'project_inventory_changed')
    stopped = [r for r in rows if r['running'] is False]
    require(all(type(r['running']) is bool for r in rows) and len(stopped) == 1
            and stopped[0]['name'] == '/trading_bot_staging_iran-migration-1'
            and stopped[0]['restart_policy'] == {'Name': 'no', 'MaximumRetryCount': 0}, 'prior_running_intent_changed')
    for row in rows:
        require(re.fullmatch('sha256:[0-9a-f]{64}', row['image']) is not None, 'image_identity_invalid')
        canonical_mounts(row['mounts'])


def store_contract(info):
    require(info['root'] == '/var/lib/docker' and info['driver'] == 'overlayfs'
            and info['driver_status'] == [['driver-type', 'io.containerd.snapshotter.v1']], 'containerd_store_changed')
    require(info['live_restore'] is False, 'live_restore_changed')


def package_contract(entries, pins):
    require(isinstance(entries, list) and len(entries) == len(pins)
            and {e['name'] for e in entries} == set(pins), 'package_inventory_changed')
    require(len({e['path'] for e in entries}) == len(entries), 'duplicate_package_path')
    for entry in entries:
        version, size, digest = pins[entry['name']]
        require((entry['version'], entry['size'], entry['sha256']) == (version, size, digest), 'package_pin_changed')


def validate(plan, *, verify_payloads):
    require(plan['schema'] == SCHEMA, 'schema_invalid')
    require(re.fullmatch('[a-zA-Z0-9._-]{1,80}', plan['operation_id']) is not None, 'operation_invalid')
    require(plan['deadline_utc'].endswith('Z')
            and datetime.now(timezone.utc) < datetime.fromisoformat(plan['deadline_utc'].replace('Z', '+00:00')), 'window_expired')
    require(os.geteuid() == 0 and Path('/proc/1/exe').readlink().name == 'systemd', 'native_root_required')
    require(not any(Path(p).exists() for p in ('/.dockerenv', '/run/.containerenv', '/run/systemd/container')), 'container_refused')
    for namespace in ('pid', 'user', 'mnt'):
        require(os.readlink('/proc/self/ns/' + namespace) == os.readlink('/proc/1/ns/' + namespace), 'namespace_mismatch')
    distro = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    require(distro.get('ID', '').strip('"') == 'ubuntu' and distro.get('VERSION_ID', '').strip('"') == '26.04', 'distro_mismatch')
    require(Path('/etc/machine-id').read_text().strip() == plan['machine_id'], 'host_mismatch')
    addresses = json.loads(command(['/usr/sbin/ip', '-j', 'address']))
    require(any(a.get('local') == '65.109.220.59' for link in addresses for a in link.get('addr_info', [])), 'web_host_required')
    require(not os.path.lexists(RESERVATION) and not os.path.lexists(PENDING), 'operation_present')
    marker = plan['marker']
    require(marker['path'] == str(MARKER), 'marker_path_changed')
    require(file_digest(MARKER, maximum=8192, private=True) == (marker['size'], marker['sha256']), 'marker_changed')
    require((MARKER.lstat().st_dev, MARKER.lstat().st_ino) == (marker['device'], marker['inode']), 'marker_inode_changed')
    require(not any(os.path.lexists(p) for p in ['/etc/docker/daemon.json', '/etc/containerd/config.toml']), 'store_config_changed')
    require({m['path'] for m in plan['store_roots']} == {'/var/lib/docker', '/var/lib/containerd'}
            and len(plan['store_roots']) == 2, 'store_root_inventory_changed')
    for mount in plan['store_roots']:
        require(not Path(mount['path']).is_symlink(), 'store_root_redirected')
        value = Path(mount['path']).stat()
        require((value.st_dev, value.st_ino) == (mount['device'], mount['inode']), 'store_root_changed')
        require(command(['/usr/bin/findmnt', '-n', '-o', 'UUID', '-T', mount['path']]) == mount['uuid'], 'store_filesystem_changed')
    baseline_contract(plan['containers'])
    info = json.loads(command(['/usr/bin/docker', 'info', '--format', '{"root":{{json .DockerRootDir}},"driver":{{json .Driver}},"driver_status":{{json .DriverStatus}},"live_restore":{{json .LiveRestoreEnabled}}}']))
    store_contract(info)
    expected = {r['id']: r for r in plan['containers']}
    require(set(command(['/usr/bin/docker', 'ps', '-aq', '--no-trunc']).splitlines()) == set(expected), 'container_set_changed')
    for ident, before in expected.items():
        actual = json.loads(command(['/usr/bin/docker', 'inspect', '--format', INSPECT, ident]))
        require(all(actual[k] == before[k] for k in ['id', 'name', 'image', 'project', 'running', 'restart_policy']), 'container_baseline_changed')
        require(canonical_mounts(actual['mounts']) == canonical_mounts(before['mounts']), 'container_mount_changed')
        require(not before['running'] or actual['health'] in {None, 'healthy'}, 'baseline_unhealthy')
    old_versions = {name: pin[0] for name, pin in OLD_PACKAGES.items()}
    installed = dict(line.split('=', 1) for line in command(['/usr/bin/dpkg-query', '-W', '-f=${Package}=${Version}\\n', *OLD_PACKAGES]).splitlines())
    require(installed == old_versions, 'installed_package_baseline_changed')
    if verify_payloads:
        for entries, pins in [(plan['old_packages'], OLD_PACKAGES), (plan['new_packages'], NEW_PACKAGES)]:
            package_contract(entries, pins)
            for entry in entries:
                require(file_digest(entry['path'], maximum=64 * 1024 * 1024, private=False)
                        == (entry['size'], entry['sha256']), 'package_payload_changed')
                fields = command(['/usr/bin/dpkg-deb', '-f', entry['path'], 'Package', 'Version', 'Architecture']).splitlines()
                require(fields == ['Package: ' + entry['name'], 'Version: ' + entry['version'], 'Architecture: amd64'], 'package_identity_changed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--verify-offline-packages', action='store_true')
    args = parser.parse_args()
    try:
        validate(document(args.manifest, args.manifest_sha256), verify_payloads=args.verify_offline_packages)
        print(json.dumps({'status': 'preflight_only_passed', 'offline_packages_verified': args.verify_offline_packages,
                          'runtime_changed': False, 'apply_available': False,
                          'backup_restore_verified': False, 'remediation_complete': False}))
        return 0
    except BaseException as error:
        print(json.dumps({'status': 'refused', 'code': str(error) if isinstance(error, Refusal) else 'unexpected_failure'}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
