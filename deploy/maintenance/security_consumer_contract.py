"""Pure fail-closed contracts for the five approved security consumers only.

Runtime callers may compare resolved configuration/Env in memory. Never emit,
persist, hash or include those values in an exception or public receipt.
"""
from pathlib import Path
from datetime import datetime, timezone
from hashlib import sha256
import json
import re

SUCCESSOR_SCHEMA = 'security_consumer_window/2'
SUCCESSOR_OPERATION = 'security-consumers-20261024-02'
CANDIDATE = 'sha256:8da3e404d01cc239606d1c79a30c4701c5a2ef2d103787b68b9ec221ae723672'
CANDIDATE_ROOTFS_SHA256 = '6762bf592f90b011d8c356fa58051654234aeff72b33f0d2d825f525c3d4c203'
PAYLOAD_SHA256 = 'cd2e64577576b9053b926d2e65620a98ca77b0ad71cc3100d9145b9409077c7b'
PAYLOAD_OVERLAY = '19d4c2da778f8f1edc567db4c9616c2d153980c9'
PAYLOAD_OVERLAY_TREE = '7a45e7ed73bcaeccc5172152c116a415b630e7fb'
PAYLOAD_TREE = '02553c4ba0b6ee8032e6f1ad1083c9df6cebb70c'
RAW_REPORT_SHA256 = '77e85632e55ded98366e799c0f166a0b6ab434a9e71f2b728aac5e452a9b7192'
SBOM_SHA256 = '5f05e26c9dc2d5d406398979d750ce71177dbde85033356ac70f186631ab6f80'
COMPOSITION_SHA256 = '8fbcad3c416a179984ff744cd166f1279cd1af97cb85892ce99c58f8e1b74797'
EVIDENCE_ZIP_SHA256 = 'd05da4a9c4d1c82b37e1fc0ac9e861762c88be862872ea767a8b561db2982716'
EVIDENCE_ZIP_BYTES = 248816
DISPOSITION_SHA256 = 'dc0da8ce33421795798de342edb8a27080995d2e5fb86072c75981e1694d17d4'
SOURCE_CLOSURE_SHA256 = 'b10a85dffd4b4c5eba649953556db2571621fae9c2c3c9c5839b50fcfb26f18d'
WEB_COMPLETED_PLAN_SHA256 = '7d55ccacd99a0f6fd0a4d93ee493a7193a9dde027b4e47bc73b2ef0af63e6216'
WEB_PROFILE_SHA256 = 'c8f95e308624660c31e18863dc4b4f4151dd525096e154564bddc17ea2c917e4'
WEB_ENGINE_COMPLETION_SHA256 = 'bf805d5de6c8705b4ba1b02593b8c9827c8448d80c47273e4c2d82a541b6cf21'
WEB_KERNEL_POSTBOOT_SHA256 = 'caf747e2ac9418b52154583a7bf9af6a70cd75ccddc94ec1703c567216376bdd'
WEB_KERNEL_COMPLETION_SHA256 = '42331354b02d5922fec5ee25ca5c56fe6a0b1ee084e1062da4925f8a030ef41b'
NATIVE_PUBLIC_BASELINE_SHA256 = 'ee9f8fe071e9257e2eba5d9d710d8429f71282e8fd6dbb9c1ade071393371984'
IMAGE_ZIP_SHA256 = '3424f8f038817e42fd7992761a588039c79bab5a6542571ee73ef2d3338cb807'
IMAGE_ZIP_BYTES = 183811369
WEB_ARTIFACT_ADMISSION_SHA256 = '515d583a175a1b3fc07ccc59f329053936af741ef240d5f12921e1f7a19144fe'
SOURCE_TAR_SHA256 = '7337d4730d2a99edb9c62d45e499afcb3654c16eba20582318ceb1866515e786'
SOURCE_TAR_BYTES = 451747328
PUBLIC_PATH = '/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
RUNTIME_ENV_DELTA = {'PYTHONDONTWRITEBYTECODE': '1'}
USER_DOCUMENT_EXCEPTION = {'path': 'docs/memory/areas/coin-market-retention.md', 'status': ' M',
    'sha256': '5fbbf84dfec99a504a048a5c1df81e353417f57cdd7b2d7281c8a3aac28ec4e3'}
OLD_IMAGES = {'bot': 'sha256:bb639822737a9eef992b9ff14095bf3abd6d5e927c16e5fdeb0386190d1d6291',
              'web': 'sha256:3ec6bb0297739409c996c3b08ade6ebab203583870a5e755572a58dfc1e80cec'}
ROLE_ORDER = (('web', 'app'), ('web', 'sync_worker'),
              ('bot', 'app'), ('bot', 'bot'), ('bot', 'sync_worker'))
EVIDENCE_NAMES = {'image_security', 'dependency_compatibility', 'producer_import_boundaries',
                  'frontend', 'migration_tree_unchanged', 'backup_restore', 'bot_host_maintenance',
                  'web_host_maintenance', 'finding_dispositions', 'source_closures',
                  'payload_manifest', 'candidate_admission', 'web_current_profile',
                  'web_engine_completion', 'web_kernel_postboot', 'native_public_baseline', 'web_artifact_admission'}

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
    successor = record.get('schema') == SUCCESSOR_SCHEMA
    if successor:
        require(payload == {'baseline_commit': BASELINE, 'baseline_tree': BASELINE_TREE,
                            'payload_tree': PAYLOAD_TREE, 'manifest_sha256': PAYLOAD_SHA256},
                'successor_payload_changed')
    expected = {'org.opencontainers.image.revision': BASELINE,
                'org.opencontainers.image.source': 'https://github.com/mohsenbarari/trading_bot',
                'io.gold-trade.release.tree': payload['payload_tree'],
                'io.gold-trade.security.baseline.tree': BASELINE_TREE,
                'io.gold-trade.security.overlay.revision': PAYLOAD_OVERLAY if successor else record['source_commit'],
                'io.gold-trade.security.overlay.tree': PAYLOAD_OVERLAY_TREE if successor else record['source_tree'],
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


def assert_config_parity(site, service_name, actual, rendered, old_image, new_image, managed_override=None, running=True,
                         strict_serving=False):
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
    if strict_serving:
        for name, settings in networks.items():
            network = actual['NetworkSettings']['Networks'][rendered['networks'][name]['name']]
            expected_aliases = {actual['Name'].lstrip('/'), service_name, *((settings or {}).get('aliases') or [])}
            actual_aliases = {alias for alias in network.get('Aliases') or []
                              if alias not in {actual['Id'], actual['Id'][:12]}}
            require(actual_aliases == expected_aliases and not network.get('IPAMConfig')
                    and not network.get('DriverOpts'), 'unreviewed_endpoint_policy')
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


def utc(value):
    require(type(value) is str and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z', value),
            'successor_window_timestamp')
    try: return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError: raise Refusal('successor_window_timestamp') from None


def assert_window(record, now, *, activating=False, recovering=False):
    """Explicit fresh authority; historical receipts and default dates grant none."""
    require(type(record) is dict and record.get('schema') == SUCCESSOR_SCHEMA
            and record.get('operation_id') == SUCCESSOR_OPERATION
            and record.get('status') == 'approved_for_finite_conditional_activation', 'successor_authority_required')
    require(set(record) == {'schema', 'operation_id', 'status', 'nonce', 'source_commit', 'source_tree',
            'not_before_utc', 'cutoff_utc', 'deadline_utc', 'images', 'baseline_images', 'permissions',
            'runtime_env_delta', 'checkout_exception', 'global_security_eligible', 'raw_gate_passed',
            'raw_gate_waived', 'acl_global_library_fixed', 'evidence', 'control_sources', 'payload', 'control_files',
            'control_identities', 'source_mutex', 'recovery', 'candidate_source_image_id'},
            'successor_window_fields')
    require(type(record.get('nonce')) is str and re.fullmatch('[a-f0-9]{32}', record['nonce'])
            and all(type(record.get(k)) is str and re.fullmatch('[a-f0-9]{40}', record[k])
                    for k in ('source_commit', 'source_tree')), 'successor_identity')
    start, cutoff, end = (utc(record.get(k)) for k in ('not_before_utc', 'cutoff_utc', 'deadline_utc'))
    recovery = record.get('recovery')
    require(type(recovery) is dict and set(recovery) == {'purpose', 'not_before_utc', 'deadline_utc',
            'owned_targets_only', 'new_forward_actions_allowed', 'unknown_rpc_observation_only'}
            and recovery['purpose'] == 'owned_rollback_only' and recovery['owned_targets_only'] is True
            and recovery['new_forward_actions_allowed'] is False and recovery['unknown_rpc_observation_only'] is True,
            'successor_recovery_scope')
    recovery_start, recovery_end = utc(recovery['not_before_utc']), utc(recovery['deadline_utc'])
    require(recovery_start == start and 0 < (recovery_end-end).total_seconds() <= 1800,
            'successor_recovery_window')
    require(type(now) is datetime and now.tzinfo is not None
            and (start <= now < recovery_end if recovering else start <= now < end)
            and start < cutoff <= end and 1200 <= (end-cutoff).total_seconds()
            and 0 < (end-start).total_seconds() <= 5400
            and not (activating and recovering) and (not activating or now < cutoff), 'successor_window_closed')
    require(record.get('candidate_source_image_id') == CANDIDATE and type(record.get('images')) is dict
            and set(record['images']) == {'bot', 'web'}
            and all(type(v) is str and re.fullmatch('sha256:[a-f0-9]{64}', v)
                    and v not in OLD_IMAGES.values() for v in record['images'].values())
            and record.get('baseline_images') == OLD_IMAGES, 'successor_image_scope')
    require(type(record.get('permissions')) is dict and all(type(v) is bool for v in record['permissions'].values())
            and record['permissions'] == {'five_consumer_images': True, 'migrations': False,
            'capture': False, 'writer_dns': False, 'engine_packages': False, 'reboot': False}, 'successor_permission_scope')
    require(record.get('runtime_env_delta') == RUNTIME_ENV_DELTA, 'successor_runtime_env_delta')
    require(record.get('checkout_exception') == USER_DOCUMENT_EXCEPTION, 'successor_checkout_exception')
    require(record.get('payload') == {'baseline_commit': BASELINE, 'baseline_tree': BASELINE_TREE,
            'payload_tree': PAYLOAD_TREE, 'manifest_sha256': PAYLOAD_SHA256}, 'successor_payload_changed')
    require(type(record.get('control_files')) is dict and len(record['control_files']) == 3
            and all(type(k) is str and Path(k).is_absolute() and type(v) is str and re.fullmatch('[a-f0-9]{64}', v)
                    for k, v in record['control_files'].items()), 'successor_control_binding')
    identities = record.get('control_identities')
    require(type(identities) is dict and set(identities) == set(record['control_files'])
            and all(type(v) is list and len(v) == 9 and all(type(n) is int for n in v)
                    and v[0] > 0 and v[1] > 0 and v[3] == 0 for v in identities.values()),
            'successor_control_identity')
    source = record.get('source_mutex')
    require(type(source) is list and len(source) == 9 and all(type(n) is int for n in source)
            and source[0] > 0 and source[1] > 0 and source[2:7] == [0o100600, 0, 0, 1, 0]
            and source[7] >= 0 and source[8] >= 0, 'successor_source_mutex_identity')
    require(record.get('global_security_eligible') is False and record.get('raw_gate_passed') is False
            and record.get('raw_gate_waived') is False and record.get('acl_global_library_fixed') is False,
            'successor_acceptance_forbidden')
    require(type(record.get('evidence')) is dict and set(record['evidence']) == EVIDENCE_NAMES,
            'successor_evidence_inventory')
    require(type(record.get('control_sources')) is dict and set(record['control_sources']) == CONTROL_SOURCES
            and all(type(v) is str and re.fullmatch('[a-f0-9]{64}', v)
                    for v in record['control_sources'].values()), 'successor_control_inventory')
    for binding in record['evidence'].values():
        require(type(binding) is dict and set(binding) == {'path', 'sha256'}
                and type(binding['path']) is str and Path(binding['path']).is_absolute()
                and type(binding['sha256']) is str and re.fullmatch('[a-f0-9]{64}', binding['sha256']),
                'successor_evidence_binding')
    for name, pin in (('finding_dispositions', DISPOSITION_SHA256), ('source_closures', SOURCE_CLOSURE_SHA256),
                      ('payload_manifest', PAYLOAD_SHA256), ('web_current_profile', WEB_COMPLETED_PLAN_SHA256),
                      ('web_engine_completion', WEB_ENGINE_COMPLETION_SHA256),
                      ('web_kernel_postboot', WEB_KERNEL_POSTBOOT_SHA256),
                      ('web_host_maintenance', WEB_KERNEL_COMPLETION_SHA256),
                      ('native_public_baseline', NATIVE_PUBLIC_BASELINE_SHA256)):
        require(record['evidence'][name]['sha256'] == pin, 'successor_static_evidence_changed')
    require(record['evidence']['web_artifact_admission']['sha256'] == WEB_ARTIFACT_ADMISSION_SHA256,
            'successor_artifact_admission_changed')
    return recovery_end if recovering else end


def assert_candidate(record, image, *, site, admission):
    """Public immutable identity/whole RootFS; never hash Config or Env values."""
    assert_loaded_candidate(record, admission)
    require(type(image) is dict and image.get('Id') == record['images'][site]
            and image.get('Os') == 'linux' and image.get('Architecture') == 'amd64', 'successor_candidate_identity')
    rootfs = image.get('RootFS')
    require(type(rootfs) is dict and set(rootfs) == {'Type', 'Layers'} and rootfs['Type'] == 'layers'
            and type(rootfs['Layers']) is list and len(rootfs['Layers']) == 32
            and all(type(v) is str and re.fullmatch('sha256:[a-f0-9]{64}', v) for v in rootfs['Layers']),
            'successor_candidate_rootfs')
    raw = json.dumps(rootfs['Layers'], sort_keys=True, separators=(',', ':')).encode()
    require(sha256(raw).hexdigest() == CANDIDATE_ROOTFS_SHA256, 'successor_candidate_rootfs')
    require(type(image.get('Config')) is dict, 'successor_candidate_config')
    assert_payload_provenance(record, image['Config'].get('Labels') or {})
    config = image['Config']
    require(command(config.get('Entrypoint')) == [] and command(config.get('Cmd')) == ['python3']
            and config.get('WorkingDir') == '/app' and (config.get('User') or '') == '',
            'successor_candidate_execution_defaults')


def assert_loaded_candidate(record, admission):
    """Root-reviewed post-LOAD facts; artifact admission alone is insufficient."""
    require(type(admission) is dict and admission.get('schema') == 'security_consumer_loaded_candidate/2'
            and admission.get('source_image_id') == CANDIDATE and admission.get('config_sha256') == CANDIDATE[7:]
            and admission.get('artifact_zip_sha256') == IMAGE_ZIP_SHA256
            and admission.get('artifact_zip_bytes') == IMAGE_ZIP_BYTES
            and admission.get('artifact_admission_sha256') == WEB_ARTIFACT_ADMISSION_SHA256
            and admission.get('source_tar_sha256') == SOURCE_TAR_SHA256
            and admission.get('source_tar_bytes') == SOURCE_TAR_BYTES
            and admission.get('rootfs_layers_sha256') == CANDIDATE_ROOTFS_SHA256
            and admission.get('artifact_bytes_verified') is True and admission.get('security_accepted') is False
            and type(admission.get('sites')) is dict and set(admission['sites']) == {'bot', 'web'},
            'successor_loaded_candidate_required')
    for site, observed in admission['sites'].items():
        require(type(observed) is dict and set(observed) == {'host', 'port', 'image_loaded', 'stable_before_after',
                'daemon_image_id', 'configuration_values_disclosed', 'binding'}
                and observed.get('host') == SITES[site]['host']
                and observed.get('port') == SITES[site]['port']
                and observed.get('image_loaded') is True and observed.get('stable_before_after') is True
                and observed.get('daemon_image_id') == record['images'][site]
                and observed.get('configuration_values_disclosed') is False,
                'successor_load_measurement_missing')
        proof = observed.get('binding')
        require(type(proof) is dict and proof.get('docker_image_id') == record['images'][site]
                and proof.get('config_sha256') == CANDIDATE[7:]
                and proof.get('security_accepted') is False and type(proof.get('rootfs_layers')) is list,
                'successor_loaded_identity')
        layers = proof['rootfs_layers']
        require(len(layers) == 32 and all(type(v) is str and re.fullmatch('sha256:[a-f0-9]{64}', v) for v in layers)
                and sha256(json.dumps(layers, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
                == CANDIDATE_ROOTFS_SHA256, 'successor_loaded_rootfs')
        kind = proof.get('kind')
        if kind == 'Docker_config_identity':
            require(record['images'][site] == CANDIDATE, 'successor_loaded_identity')
        else:
            require(proof.get('schema') == 'runtime_image_archive_binding/1'
                    and proof.get('scanner_config_id') == CANDIDATE and proof.get('findings_waived') is False
                    and type(proof.get('archive_sha256')) is str
                    and re.fullmatch('[a-f0-9]{64}', proof['archive_sha256'])
                    and type(proof.get('archive_bytes')) is int and proof['archive_bytes'] > 0,
                    'successor_loaded_archive_proof')
            if kind in {'OCI_manifest_to_config', 'Docker_manifest_to_config'}:
                require(proof.get('manifest_sha256') == record['images'][site][7:], 'successor_loaded_identity')
            else:
                indexed = proof.get('index_binding')
                require(kind == 'OCI_index_to_manifest_to_config' and type(indexed) is dict
                        and indexed.get('root_index_sha256') == record['images'][site][7:]
                        and all(indexed.get(k) is False for k in ('statement_semantics_validated',
                                'signatures_verified', 'provenance_trusted')), 'successor_loaded_identity')


def assert_loader_defaults(old_image, new_image):
    """Only the measured Python release metadata delta is permitted."""
    old, new = environment(old_image['Config'].get('Env') or []), environment(new_image['Config'].get('Env') or [])
    expected = {'PATH', 'LANG', 'GPG_KEY', 'PYTHON_VERSION', 'PYTHON_SHA256'}
    require(set(old) == set(new) == expected and old['PATH'] == new['PATH'] == PUBLIC_PATH
            and old['LANG'] == new['LANG'] == 'C.UTF-8' and old['GPG_KEY'] == new['GPG_KEY']
            and new['PYTHON_VERSION'] == '3.11.17'
            and re.fullmatch(r'3\.11\.[0-9]+', old['PYTHON_VERSION'])
            and all(re.fullmatch('[a-f0-9]{64}', value) for value in (old['PYTHON_SHA256'], new['PYTHON_SHA256'])),
            'successor_unreviewed_image_environment_delta')


def assert_role_launch(site, service, actual, *, candidate=False):
    require((site, service) in ROLE_ORDER, 'successor_role_scope')
    config, host = actual['Config'], actual['HostConfig']
    workers = '4' if site == 'web' else '2'
    expected = (['sh', '-c', 'exec uvicorn main:app --host 0.0.0.0 --port 8000 --workers ' + workers]
                if service == 'app' else ['python', 'run_bot.py'] if service == 'bot'
                else ['python', '-m', 'core.sync_worker'])
    require(command(config.get('Cmd')) == expected and not command(config.get('Entrypoint'))
            and config.get('WorkingDir') == '/app' and (config.get('User') or '') == ''
            and host.get('PidMode') in {None, ''} and host.get('Privileged') is False,
            'successor_role_launch_drift')
    values = environment(config.get('Env') or [])
    allowed = {'PYTHON_VERSION', 'PYTHON_SHA256', 'PYTHONDONTWRITEBYTECODE'}
    require(values.get('PATH') == PUBLIC_PATH
            and all(not key.startswith('LD_') and (not key.startswith('PYTHON') or key in allowed) for key in values)
            and (not candidate or values.get('PYTHONDONTWRITEBYTECODE') == '1'), 'successor_loader_override')


def target_override(site, service, image, restart, *, rollback=False, candidate_image=CANDIDATE):
    require((site, service) in ROLE_ORDER and image == (OLD_IMAGES[site] if rollback else candidate_image),
            'successor_override_scope')
    require(type(restart) is str and restart in {'no', 'always', 'unless-stopped'}, 'successor_restart_intent')
    item = {'image': image, 'pull_policy': 'never', 'restart': restart}
    if not rollback: item['environment'] = dict(RUNTIME_ENV_DELTA)
    return {'services': {service: item}}


def target_apply_argv(site, service, override):
    require((site, service) in ROLE_ORDER, 'successor_role_scope')
    argv = compose_apply_argv(site, override)
    return argv[:-len(SITES[site]['services'])] + [service]


def assert_public_diff(raw, *, verified_app_root=False):
    """Inspect COW metadata only in RAM; no public code/cache change is allowed."""
    require(type(raw) is str and len(raw.encode()) <= 1024**2, 'successor_cow_bound')
    # These writable data locations are never read, copied, hashed or reported.
    writable = ('/tmp', '/var/tmp', '/run', '/app/app_logs', '/app/audit_trail', '/app/uploads')
    count = 0
    for line in raw.splitlines():
        count += 1
        require(count <= 10000 and len(line) <= 4096 and len(line) > 2
                and line[0] in 'ACD' and line[1] == ' ', 'successor_cow_shape')
        path = line[2:]
        require(path.startswith('/') and str(Path(path)) == path and '..' not in Path(path).parts
                and (any(path == root or path.startswith(root + '/') for root in writable)
                     or line[0] == 'C' and path == '/app' and verified_app_root is True),
                'successor_public_code_cow_changed')


def mount_projection(values):
    require(type(values) is list and len(values) <= 256, 'successor_mount_shape')
    fields = ('Destination', 'Mode', 'Propagation', 'RW', 'Source', 'Type')
    result = []
    for row in values:
        require(type(row) is dict and all(k in row for k in fields) and type(row['RW']) is bool,
                'successor_mount_shape')
        result.append({k: row[k] for k in fields})
    require(len({r['Destination'] for r in result}) == len(result), 'successor_mount_shape')
    return sorted(result, key=lambda r: r['Destination'])


def assert_fact_profile(site, document, actual):
    """Bind historical completion FACTS to a fresh complete initial inventory."""
    require(type(actual) is list and len(actual) == (27 if site == 'web' else 15)
            and len({r['id'] for r in actual}) == len(actual), 'successor_initial_inventory')
    if site == 'web':
        require(type(document) is dict and type(document.get('current_profile')) is dict,
                'successor_current_profile')
        profile = document['current_profile']
        raw = json.dumps(profile, sort_keys=True, separators=(',', ':')).encode()
        require(sha256(raw).hexdigest() == WEB_PROFILE_SHA256, 'successor_current_profile')
        expected = profile['containers']
        require(profile['expected_running'] == 16 and len(profile['parked_ids']) == 10
                and len(profile['datastore_probes']) == 6, 'successor_current_profile')
    else:
        require(document.get('schema') == 'security_native_current_public_profile/1'
                and document.get('stable_before_after') is True and document.get('container_count') == 15
                and document.get('runtime_changed') is False, 'successor_native_profile')
        expected = document['containers']
    by_id = {row['id']: row for row in actual}
    require(set(by_id) == {row['id'] for row in expected}, 'successor_initial_inventory')
    for row in expected:
        observed = by_id[row['id']]
        for key in ('id', 'name', 'image', 'running', 'restart_policy', 'project', 'network_names',
                    'paused', 'restarting', 'pid_mode', 'privileged', 'read_only', 'service', 'network_mode'):
            if key in row: require(observed.get(key) == row[key], 'successor_initial_profile_drift')
        require(mount_projection(observed['mounts']) == mount_projection(row['mounts'])
                and (observed['ports'] or {}) == (row.get('ports', row.get('port_bindings')) or {}),
                'successor_initial_profile_drift')


def assert_phase_inventory(site, initial, current, projection, *, recovering_service=None, candidate_images=None):
    """Only explicitly owned target identities may change; peers stay exact."""
    require(type(projection) is dict and set(projection) <= {s + '/' + n for s, n in ROLE_ORDER},
            'successor_phase_projection')
    require(type(initial) is list and type(current) is list and len(initial) == len(current)
            and len({r['name'] for r in current}) == len(current), 'successor_phase_inventory')
    by_name = {row['name']: row for row in current}
    require(set(by_name) == {row['name'] for row in initial}, 'successor_phase_inventory')
    services = {name: service for service, name in SITES[site]['services'].items()}
    candidate = CANDIDATE if candidate_images is None else candidate_images[site]
    require(recovering_service is None or (site, recovering_service) in ROLE_ORDER, 'successor_recovery_scope')
    for old in initial:
        new = by_name[old['name']];service = services.get(old['name']);key = site + '/' + str(service)
        if key not in projection:
            require(new == old, 'successor_nonselected_runtime_drift')
            continue
        owned = projection[key]
        require(type(owned) is dict and set(owned) == {'id', 'image'}
                and type(owned['id']) is str and re.fullmatch('[a-f0-9]{64}', owned['id'])
                and owned['image'] in {candidate, OLD_IMAGES[site]}
                and new['id'] == owned['id'] and new['image'] == owned['image']
                and (service == recovering_service or new['running'] is True and new['restarting'] is False)
                and new['paused'] is False,
                'successor_owned_target_identity')
        for field in ('name', 'project', 'service', 'mounts', 'restart_policy', 'ports', 'network_names',
                      'network_mode', 'pid_mode', 'privileged', 'read_only'):
            require(new.get(field) == old.get(field), 'successor_owned_target_policy_drift')
        old_network, new_network = old.get('network_policy', {}), new.get('network_policy', {})
        require(set(old_network) == set(new_network), 'successor_network_alias_drift')
        for network in old_network:
            def aliases(row, cid):
                return sorted(x for x in row.get('Aliases') or [] if x not in {cid, cid[:12]})
            require(aliases(old_network[network], old['id']) == aliases(new_network[network], new['id'])
                    and not new_network[network].get('IPAMConfig'), 'successor_network_alias_drift')
