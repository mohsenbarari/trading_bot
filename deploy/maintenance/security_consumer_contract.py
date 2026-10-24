"""Pure fail-closed contracts for the five approved security consumers only.

Runtime callers may compare resolved configuration/Env in memory. Never emit,
persist, hash or include those values in an exception or public receipt.
"""
from pathlib import Path
import re

SITES = {
    'bot': {'host': '65.109.216.187', 'port': 22, 'project': 'trading_bot',
            'workdir': '/root/trading-bot/trading_bot',
            'files': ['/root/trading-bot/trading_bot/docker-compose.yml',
                      '/root/trading-bot/trading_bot/tmp/production-release/artifacts/writer-restart-disabled.foreign.override.yml'],
            'services': {'app': '/trading_bot_app', 'bot': '/trading_bot_bot', 'sync_worker': '/trading_bot_sync_worker'}},
    'web': {'host': '65.109.220.59', 'port': 37067, 'project': 'current',
            'workdir': '/srv/trading-bot/current',
            'files': ['/srv/trading-bot/current/docker-compose.iran.yml',
                      '/srv/trading-bot/releases/state/writer-restart-disabled.override.yml'],
            'services': {'app': '/trading_bot_app', 'sync_worker': '/trading_bot_sync_worker'}},
}
SNAPSHOTS = {'/app/runtime/coin-inference', '/app/runtime/product-estimator'}
CONTROL_SOURCES = {'security_consumer_contract.py', 'security_consumer_site.py',
                   'security_consumer_release.py', 'security_consumers.yml',
                   'security_consumers_inventory.yml', 'security_consumers_ansible.cfg'}
BASELINE = 'ba1f7616ade57558f51bb3a7fbb903243a33aab4'
BASELINE_TREE = '5d2ff35831696a3886372e4388f2a769a3594a8a'


class Refusal(RuntimeError):
    """Static code only: resolved configuration is never diagnostic text."""


def require(ok, code):
    if not ok: raise Refusal(code)


def environment(values):
    require(isinstance(values, list) and all(isinstance(item, str) and '=' in item for item in values), 'invalid_environment_shape')
    result = {}
    for item in values:
        key, value = item.split('=', 1)
        require(key not in result, 'duplicate_environment_key')
        result[key] = value
    return result


def command(value):
    require(value is None or isinstance(value, list) and all(isinstance(item, str) for item in value), 'command_not_explicit_argv')
    return value or []


def assert_payload_provenance(record, labels):
    """Recipe/control main is NOT the mixed product's source revision."""
    payload = record.get('payload')
    require(isinstance(payload, dict) and set(payload) == {
        'baseline_commit', 'baseline_tree', 'payload_tree', 'manifest_sha256'}, 'security_payload_binding_missing')
    require(payload['baseline_commit'] == BASELINE and payload['baseline_tree'] == BASELINE_TREE and
            re.fullmatch('[a-f0-9]{40}', payload['payload_tree']) and
            re.fullmatch('[a-f0-9]{64}', payload['manifest_sha256']), 'security_payload_identity_invalid')
    expected = {'org.opencontainers.image.revision': BASELINE,
                'org.opencontainers.image.source': 'https://github.com/mohsenbarari/trading_bot',
                'io.gold-trade.release.tree': payload['payload_tree'],
                'io.gold-trade.security.baseline.tree': BASELINE_TREE,
                'io.gold-trade.security.overlay.revision': record['source_commit'],
                'io.gold-trade.security.overlay.tree': record['source_tree'],
                'io.gold-trade.security.payload.sha256': payload['manifest_sha256']}
    require(all(labels.get(name) == value for name, value in expected.items()), 'candidate_image_provenance_mismatch')


def public_identity(actual):
    labels = actual['Config']['Labels']
    return {'id': actual['Id'], 'name': actual['Name'], 'image': actual['Image'],
            'project': labels.get('com.docker.compose.project'), 'service': labels.get('com.docker.compose.service')}


def assert_target(site, actual, managed_override=None, running=True):
    scope = SITES[site]
    identity = public_identity(actual)
    service = identity['service']
    require(service in scope['services'] and identity['name'] == scope['services'][service] and
            identity['project'] == scope['project'], 'consumer_outside_exact_whitelist')
    labels = actual['Config']['Labels']
    files = scope['files'] + ([str(managed_override)] if managed_override else [])
    require(labels.get('com.docker.compose.project.config_files') == ','.join(files) and
            labels.get('com.docker.compose.project.working_dir') == scope['workdir'], 'active_compose_provenance_changed')
    require(re.fullmatch('sha256:[0-9a-f]{64}', identity['image']) and
            re.fullmatch('[0-9a-f]{64}', identity['id']), 'invalid_consumer_identity')
    require(not running or actual['State']['Running'] is True, 'consumer_not_baseline_running')
    return service


def ports_from_compose(rows):
    result = []
    for item in rows:
        require(isinstance(item, dict) and str(item.get('published', '')).isdigit() and
                isinstance(item.get('target'), int) and item.get('protocol', 'tcp') in {'tcp', 'udp'}, 'unsupported_port_shape')
        result.append((str(item['target']) + '/' + item.get('protocol', 'tcp'),
                       item.get('host_ip', '0.0.0.0'), str(item['published'])))
    return sorted(result)


def ports_from_inspect(bindings):
    return sorted((port, value.get('HostIp') or '0.0.0.0', value['HostPort'])
                  for port, values in (bindings or {}).items() for value in (values or []))


def assert_config_parity(site, service_name, actual, rendered, old_image, new_image, managed_override=None, running=True):
    """No hashing of Env. Fail before stopping anything on unresolved drift."""
    require(assert_target(site, actual, managed_override=managed_override, running=running) == service_name, 'service_identity_mismatch')
    service = rendered['services'][service_name]
    config, host = actual['Config'], actual['HostConfig']
    old, new = old_image['Config'], new_image['Config']
    require(service.get('container_name') == actual['Name'].lstrip('/'), 'container_name_drift')
    expected_env = environment(old.get('Env') or [])
    explicit_env = service.get('environment') or {}
    require(isinstance(explicit_env, dict) and all(isinstance(key, str) and isinstance(value, str)
                                               for key, value in explicit_env.items()), 'unresolved_compose_environment')
    expected_env.update(explicit_env)
    actual_env = environment(config.get('Env') or [])
    require(expected_env == actual_env, 'application_environment_drift')
    old_defaults, new_defaults = environment(old.get('Env') or []), environment(new.get('Env') or [])
    python_base = {'PATH', 'LANG', 'GPG_KEY', 'PYTHON_VERSION', 'PYTHON_SHA256', 'PYTHON_PIP_VERSION',
                   'PYTHON_SETUPTOOLS_VERSION', 'PYTHON_GET_PIP_URL', 'PYTHON_GET_PIP_SHA256'}
    require({key: value for key, value in old_defaults.items() if key not in python_base} ==
            {key: value for key, value in new_defaults.items() if key not in python_base}, 'image_application_defaults_drift')
    if service_name in {'app', 'bot'}:
        require(actual_env.get('PRODUCT_ESTIMATOR_SNAPSHOT_MODE') == 'LEGACY' and
                actual_env.get('COIN_INTELLIGENCE_INFERENCE_AUTO_SELECTION_ENABLED') == 'false', 'market_behavior_not_frozen')
    for field, docker_key in (('command', 'Cmd'), ('entrypoint', 'Entrypoint')):
        expected = command(service.get(field, old.get(docker_key)))
        require(command(config.get(docker_key)) == expected, 'consumer_command_drift')
        if field not in service:
            require(command(new.get(docker_key)) == command(old.get(docker_key)), 'image_default_command_drift')
    for field, docker_key, default in (('working_dir', 'WorkingDir', ''), ('user', 'User', ''), ('stop_signal', 'StopSignal', '')):
        expected = service.get(field, old.get(docker_key, default)) or default
        require((config.get(docker_key) or default) == expected, 'consumer_execution_identity_drift')
        if field not in service:
            require((new.get(docker_key) or default) == (old.get(docker_key) or default), 'image_default_identity_drift')
    for key in ('secrets', 'configs', 'volumes_from', 'post_start', 'pre_stop', 'network_mode', 'pid', 'ipc'):
        require(not service.get(key), 'unsupported_consumer_feature')
    require(not host.get('Privileged') and not service.get('privileged') and
            host.get('ReadonlyRootfs', False) == service.get('read_only', False), 'consumer_privilege_drift')
    for key, docker_key in (('cap_add', 'CapAdd'), ('cap_drop', 'CapDrop'), ('security_opt', 'SecurityOpt'), ('extra_hosts', 'ExtraHosts')):
        expected = service.get(key) or []
        if isinstance(expected, dict): expected = [name + ':' + str(value) for name, value in expected.items()]
        require(sorted(host.get(docker_key) or []) == sorted(expected), 'consumer_host_policy_drift')
    limits = []
    for name, value in (service.get('ulimits') or {}).items():
        limits.append((name, value['soft'], value['hard']) if isinstance(value, dict) else (name, value, value))
    actual_limits = [(value['Name'], value['Soft'], value['Hard']) for value in host.get('Ulimits') or []]
    require(sorted(limits) == sorted(actual_limits), 'consumer_ulimit_drift')
    logging = service.get('logging') or {}
    actual_logging = host.get('LogConfig') or {}
    require(logging.get('driver', 'json-file') == actual_logging.get('Type', 'json-file') and
            (logging.get('options') or {}) == (actual_logging.get('Config') or {}), 'consumer_logging_drift')
    for key in ('cpus', 'cpu_quota', 'cpu_period', 'cpuset', 'mem_limit', 'mem_reservation', 'memswap_limit',
                'pids_limit', 'devices', 'device_cgroup_rules', 'sysctls', 'dns', 'dns_search', 'dns_opt', 'runtime'):
        require(not service.get(key), 'resource_override_requires_separate_review')
    for key in ('Memory', 'MemoryReservation', 'MemorySwap', 'NanoCpus', 'CpuQuota', 'CpuPeriod',
                'CpusetCpus', 'Devices', 'DeviceCgroupRules', 'Sysctls', 'Dns', 'DnsSearch', 'DnsOptions'):
        require(not host.get(key), 'captured_resource_override_requires_separate_review')
    require(host.get('PidsLimit') in {None, 0} and host.get('Runtime', 'runc') == 'runc', 'captured_runtime_override_requires_separate_review')
    policy = service.get('restart', 'no')
    require(host['RestartPolicy']['Name'] == policy and policy in {'no', 'always', 'unless-stopped'}, 'consumer_restart_policy_drift')
    require(ports_from_compose(service.get('ports') or []) == ports_from_inspect(host.get('PortBindings')), 'consumer_port_drift')
    expected_mounts = []
    for mount in service.get('volumes') or []:
        require(mount['type'] in {'bind', 'volume'} and not mount.get('consistency') and not mount.get('subpath'), 'unsupported_volume_shape')
        require(not (mount.get('volume') or {}).get('subpath') and
                not (mount.get('volume') or {}).get('nocopy') and
                not (mount.get('bind') or {}).get('selinux'), 'unsupported_volume_options')
        source = mount['source']
        if mount['type'] == 'volume': source = rendered['volumes'][source]['name']
        expected_mounts.append((mount['type'], source, mount['target'], not mount.get('read_only', False)))
    actual_mounts = [(mount['Type'], mount.get('Name') if mount['Type'] == 'volume' else mount['Source'],
                      mount['Destination'], mount['RW']) for mount in actual['Mounts']]
    require(sorted(expected_mounts) == sorted(actual_mounts), 'consumer_mount_drift')
    for mount in service.get('volumes') or []:
        if mount['type'] == 'bind':
            actual_mount = next(item for item in actual['Mounts'] if item['Destination'] == mount['target'])
            require((mount.get('bind') or {}).get('propagation', 'rprivate') ==
                    (actual_mount.get('Propagation') or 'rprivate'), 'consumer_bind_propagation_drift')
    require(not host.get('Tmpfs') and not service.get('tmpfs'), 'unexpected_tmpfs')
    if service_name in {'app', 'bot'}:
        require(SNAPSHOTS <= {mount['Destination'] for mount in actual['Mounts'] if mount['RW'] is False}, 'snapshot_not_readonly')
    networks = service.get('networks') or {}
    require(isinstance(networks, dict), 'unresolved_network_shape')
    expected_networks = {rendered['networks'][name]['name'] for name in networks}
    require(expected_networks == set(actual['NetworkSettings']['Networks']), 'consumer_network_drift')
    require(all(not value.get('ipv4_address') and not value.get('ipv6_address') and not value.get('mac_address')
                for value in networks.values() if value), 'static_endpoint_requires_separate_review')
    # The caller keeps these objects in memory only; the result has no Env data.
    return public_identity(actual)


def image_only_override(site, image):
    require(re.fullmatch('sha256:[0-9a-f]{64}', image), 'image_not_immutable_digest')
    return {'services': {name: {'image': image, 'pull_policy': 'never'} for name in SITES[site]['services']}}


def compose_apply_argv(site, override):
    scope = SITES[site]
    require(Path(override).is_absolute(), 'override_not_absolute')
    argv = ['/usr/bin/docker', 'compose', '--project-name', scope['project'], '--project-directory', scope['workdir']]
    for path in (*scope['files'], str(override)):
        argv += ['-f', path]
    return [*argv, 'up', '-d', '--no-deps', '--no-build', '--pull', 'never', '--wait', '--wait-timeout', '180',
            '--force-recreate', *scope['services']]
