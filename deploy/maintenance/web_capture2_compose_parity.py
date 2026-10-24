"""One config-only probe; private rendered configuration stays in bounded RAM."""
import base64
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import time

DOCKER = '/usr/bin/docker'
ROOT = Path('/root/.local/security-capture2-compose-parity-20261024-04')
ROLE_ENV = Path('/srv/trading-bot/market-pipeline-releases/f3be9ae2c4e9df0da31abaa41e91f327eb9573a2/web.release.env')
CID = '9870311065b13646767dfad1e48214d6a231e3898e1f248bb9559edd8926966c'
AFTER_CAPTURE02_ID = '9d427b7bf4c89f954040cad09e591bbbe01eca6c0e935abbbab87dcd9a8d5538'
AFTER_CAPTURE04_ID = 'b29cc1b589fd5f255dd8086419cb16a155769667618f2859cca6702a05377a76'
BASE = 'sha256:2239469ab3dd3e291e16b4571af84d446f776ade05d48552f150b7ec2670fcc6'
CANDIDATE = 'sha256:a96439e0366c40b8aca012d2a0b4c3254e2c8257880d67f6d6f0d53361ad924c'
FUNCTIONAL_IMAGE = 'sha256:f79cdda21c192c52536f4ccfecadc7f724453a9ea4abfae96b1efe540ebbc61b'
FUNCTIONAL_OVERLAY = Path('/root/.local/security-capture2-replay-fix-20261024-01/capture2-functional-image.yml')
FUNCTIONAL_LABELS = {'io.gold-trade.capture.replay.baseline':'d8f15f8212b9510e3abb5301d0201b2ba1eca13224f5a01c682d1de6716cb7d5',
    'io.gold-trade.capture.replay.fix-commit':'adca507b261d3e139befc67b4a937c48c96bb949',
    'io.gold-trade.capture.replay.source-sha256':'1b9c611692978edec4c37de217e77abf00f5a0e456006060c936d40c53978929',
    'io.gold-trade.capture.replay.status':'functional_fix_not_security_acceptance'}
PUBLIC_REVISION = '4ef8e6dcb2d361b763bd8b72dc730c1f978f564a'
PATCHED_SOURCE = 'f9d2b55921431e069b06e7de463d8b1d36ecbc01a94d5d0d98cd59f1dc9260da'
PROJECT = 'market-private-pipeline-primary'
SERVICE = 'market-capture-account2'
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C.UTF-8', 'DOCKER_HOST': 'unix:///var/run/docker.sock'}

def require(ok, code):
    if not ok:
        raise RuntimeError(code)

def safe(path, *, private=False):
    require(path.is_absolute(), 'unsafe_path')
    for parent in reversed(path.parents):
        value = parent.lstat()
        require(stat.S_ISDIR(value.st_mode) and value.st_uid == 0 and not value.st_mode & 0o022, 'unsafe_parent')
    value = path.lstat()
    require(stat.S_ISREG(value.st_mode) and value.st_uid == 0 and value.st_nlink == 1
            and not value.st_mode & (0o077 if private else 0o022), 'unsafe_file')
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)

def bounded(argv, *, seconds=30, maximum=2097152):
    child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, env=ENV, cwd=ROOT,
                             start_new_session=True)
    buffers = {'stdout': bytearray(), 'stderr': bytearray()}
    selector = selectors.DefaultSelector()
    deadline = time.monotonic() + seconds
    try:
        for name in buffers:
            selector.register(getattr(child, name), selectors.EVENT_READ, name)
        while selector.get_map():
            require(time.monotonic() < deadline, 'command_timeout')
            for key, _ in selector.select(0.25):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                else:
                    require(sum(map(len, buffers.values())) + len(chunk) <= maximum, 'command_output_bound')
                    buffers[key.data].extend(chunk)
        code = child.wait(timeout=max(0.1, min(3, deadline - time.monotonic())))
        return code, buffers['stdout'], buffers['stderr']
    finally:
        selector.close()
        if child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        child.wait(timeout=3)
        child.stdout.close()
        child.stderr.close()

def query(argv):
    code, output, _ = bounded(argv)
    require(code == 0, 'metadata_command_failed')
    return json.loads(output)

def environment(items):
    result = {}
    for item in items or []:
        require(isinstance(item, str) and '=' in item, 'environment_schema_invalid')
        key, value = item.split('=', 1)
        require(key not in result, 'duplicate_environment_key')
        result[key] = value
    return result

def duration(value):
    match = re.fullmatch(r'(\d+)(ns|us|ms|s|m|h)', value)
    require(match is not None, 'duration_schema_invalid')
    return int(match[1]) * {'ns': 1, 'us': 1000, 'ms': 1000000, 's': 1000000000, 'm': 60000000000, 'h': 3600000000000}[match[2]]

def main(*, existing=False, incident_profile='original'):
    require(incident_profile in {'original','after_capture02','after_capture04'}, 'incident_profile_invalid')
    current_id = {'original':CID,'after_capture02':AFTER_CAPTURE02_ID,'after_capture04':AFTER_CAPTURE04_ID}[incident_profile]
    require(incident_profile != 'after_capture04' or existing,'functional_profile_requires_existing_public_context')
    candidate_image = FUNCTIONAL_IMAGE if incident_profile == 'after_capture04' else CANDIDATE
    require(os.geteuid() == 0, 'native_root_required')
    safe(Path(DOCKER))
    before_env = safe(ROLE_ENV, private=True)
    if existing:
        value = ROOT.lstat()
        require(stat.S_ISDIR(value.st_mode) and value.st_uid == 0 and stat.S_IMODE(value.st_mode) == 0o700
                and ROOT.resolve(strict=True) == ROOT, 'unsafe_existing_public_root')
    else:
        require(not os.path.lexists(ROOT), 'fresh_public_root_required')
        ROOT.mkdir(mode=0o700)
    public_variables = set()
    expected_files = {}
    for name, encoded in PUBLIC_COMPOSE.items():
        data = base64.b64decode(encoded, validate=True)
        public_variables.update(re.findall(rb'\$\{([A-Z][A-Z0-9_]+)', data))
        expected_files[name] = data
    expected_files['capture2-image-only.yml'] = ('services:\n  ' + SERVICE + ':\n    image: ' + CANDIDATE + '\n'
        '    restart: unless-stopped\n    environment:\n      MARKET_PIPELINE_RELEASE_SHA: ' + PUBLIC_REVISION + '\n').encode()
    require(set(expected_files) == {'compose.yml','compose.web.yml','capture2-image-only.yml'}, 'public_file_inventory_invalid')
    if incident_profile == 'after_capture04':
        expected_files[str(FUNCTIONAL_OVERLAY)] = ('services:\n  '+SERVICE+':\n    image: '+FUNCTIONAL_IMAGE+'\n').encode()
    for name, data in expected_files.items():
        path = ROOT / name
        if existing:
            before = safe(path, private=True)
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                raw = os.read(fd,65537)
                info = os.fstat(fd)
                require(raw == data and (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns) == before
                        and safe(path,private=True) == before, 'existing_public_source_changed')
            finally:
                os.close(fd)
        else:
            with path.open('xb') as handle:
                os.fchmod(handle.fileno(), 0o600)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
    args = [DOCKER, 'compose', '--project-name', PROJECT, '--project-directory', str(ROOT),
            '--env-file', str(ROLE_ENV), '-f', str(ROOT / 'compose.yml'),
            '-f', str(ROOT / 'compose.web.yml'), '-f', str(ROOT / 'capture2-image-only.yml'),
            '--profile', 'web', 'config', '--format', 'json']
    if incident_profile == 'after_capture04':
        at = args.index('--profile')
        args[at:at] = ['-f',str(FUNCTIONAL_OVERLAY)]
    code, raw, error = bounded(args)
    require(safe(ROLE_ENV, private=True) == before_env, 'role_env_metadata_changed')
    if code:
        missing = sorted(variable.decode() for variable in public_variables
                         if re.search(rb'\b' + re.escape(variable) + rb'\b[^\n]{0,256}(?:required|missing|unset)', error, re.I))
        print(json.dumps({'status': 'compose_render_refused', 'missing_public_variable_names': missing,
                          'private_values_emitted': False, 'full_runtime_parity': False}))
        return 2
    rendered = json.loads(raw)
    service = rendered['services'][SERVICE]
    live = query([DOCKER, 'inspect', '--format',
        '{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Image}},"config":{{json .Config}},"host":{{json .HostConfig}},"mounts":{{json .Mounts}},"networks":{{json .NetworkSettings.Networks}}}', current_id])
    image = query([DOCKER, 'image', 'inspect', '--format', '{"id":{{json .Id}},"config":{{json .Config}}}', service['image']])
    base = query([DOCKER, 'image', 'inspect', '--format', '{"id":{{json .Id}},"config":{{json .Config}}}', BASE])
    require(live['id'] == current_id and live['name'] == '/' + PROJECT + '-' + SERVICE + '-1', 'target_identity_changed')
    config, host, defaults = live['config'], live['host'], image['config']
    expected_env = environment(defaults.get('Env'))
    expected_env.update(service.get('environment', {}))
    health = service.get('healthcheck', {})
    expected_health = {'Test': health['test'], 'Interval': duration(health['interval']),
                       'Timeout': duration(health['timeout']), 'StartPeriod': duration(health['start_period']), 'Retries': health['retries']}
    expected_mounts = {(item['type'], item['source'], item['target'], not item.get('read_only', False)) for item in service.get('volumes', [])}
    for secret in service.get('secrets', []):
        require(isinstance(secret, dict) and set(secret) <= {'source', 'target', 'uid', 'gid', 'mode'}, 'secret_schema_invalid')
        require(not any(key in secret for key in ('uid', 'gid', 'mode')), 'secret_override_requires_review')
        target = secret.get('target', secret['source'])
        expected_mounts.add(('bind', rendered['secrets'][secret['source']]['file'], target if target.startswith('/') else '/run/secrets/' + target, False))
    actual_mounts = {(item['Type'], item['Source'], item['Destination'], item['RW']) for item in live['mounts']}
    expected_networks = {rendered['networks'][name]['name'] for name in service['networks']}
    aliases_ok = all(set(item.get('Aliases') or []) == {PROJECT + '-' + SERVICE + '-1', SERVICE}
                     for item in live['networks'].values())
    expected_labels = dict(defaults.get('Labels') or {})
    expected_labels.update(service.get('labels', {}))
    non_compose_labels = {key: value for key, value in (config.get('Labels') or {}).items() if not key.startswith('com.docker.compose.')}
    policy = service.get('restart', 'no').split(':')
    diagnostic_labels = {'io.gold-trade.observability.base-image': BASE,
                         'io.gold-trade.observability.source-sha256': PATCHED_SOURCE,
                         'io.gold-trade.observability.status': 'diagnostic_not_security_acceptance'}
    if incident_profile == 'after_capture04':
        diagnostic_labels.update(FUNCTIONAL_LABELS)
    old_labels = dict(base['config'].get('Labels') or {})
    old_env = environment(config.get('Env'))
    checks = {
        'source_image_identity': image['id'] == candidate_image and live['image'] == base['id'] == BASE,
        'complete_image_default_parity': {k:v for k,v in defaults.items() if k != 'Labels'} == {k:v for k,v in base['config'].items() if k != 'Labels'},
        'exact_diagnostic_image_labels': defaults.get('Labels') == {**old_labels, **diagnostic_labels},
        'public_release_retained': old_env.get('MARKET_PIPELINE_RELEASE_SHA') == PUBLIC_REVISION == expected_env.get('MARKET_PIPELINE_RELEASE_SHA'),
        'complete_environment': expected_env == environment(config.get('Env')),
        'command': service.get('command', defaults.get('Cmd')) == config.get('Cmd'),
        'entrypoint': (service.get('entrypoint') if service.get('entrypoint') is not None else defaults.get('Entrypoint')) == config.get('Entrypoint'),
        'user': service.get('user', defaults.get('User')) == config.get('User'),
        'working_directory': service.get('working_dir', defaults.get('WorkingDir')) == config.get('WorkingDir'),
        'healthcheck': expected_health == config.get('Healthcheck'),
        'read_only': bool(service.get('read_only', False)) == host.get('ReadonlyRootfs'),
        'init': bool(service.get('init', False)) == host.get('Init'),
        'capabilities': service.get('cap_drop') == host.get('CapDrop') and not host.get('CapAdd'),
        'security_options': service.get('security_opt') == host.get('SecurityOpt') and not host.get('Privileged'),
        'pids_limit': service.get('pids_limit') == host.get('PidsLimit'),
        'memory_limit': int(service.get('mem_limit', 0)) == host.get('Memory'),
        'cpu_limit': round(float(service.get('cpus', 0)) * 1000000000) == host.get('NanoCpus'),
        'restart_policy': {'Name': policy[0], 'MaximumRetryCount': int(policy[1]) if len(policy) == 2 else 0} == host.get('RestartPolicy'),
        'logging': service.get('logging') == {'driver': host.get('LogConfig', {}).get('Type'), 'options': host.get('LogConfig', {}).get('Config')},
        'tmpfs': dict(item.split(':', 1) for item in service.get('tmpfs', [])) == host.get('Tmpfs'),
        'all_mounts': expected_mounts == actual_mounts and all(item.get('Propagation') == 'rprivate' for item in live['mounts']),
        'network_membership': expected_networks == set(live['networks']),
        'network_aliases': aliases_ok,
        'published_ports': not service.get('ports') and not host.get('PortBindings'),
        'application_labels': old_labels == non_compose_labels and expected_labels == {**non_compose_labels, **diagnostic_labels},
    }
    # Full semantic Config contract. Docker-generated identity/provenance may
    # change; all actual application configuration must otherwise be identical.
    fixed_config = {'AttachStdin': False, 'AttachStdout': True, 'AttachStderr': True,
                    'OpenStdin': False, 'StdinOnce': False, 'Tty': False,
                    'Domainname': '', 'Hostname': current_id[:12]}
    config_accounted = set(fixed_config) | {'Cmd','Entrypoint','Env','Healthcheck','Image','Labels','User','Volumes','WorkingDir'}
    checks['full_config_schema'] = set(config) == config_accounted
    checks['full_config_fixed_defaults'] = all(config.get(k) == v for k,v in fixed_config.items())
    checks['image_volume_defaults'] = config.get('Volumes') == defaults.get('Volumes') == base['config'].get('Volumes')
    compose_labels = {k:v for k,v in (config.get('Labels') or {}).items() if k.startswith('com.docker.compose.')}
    compose_fixed = {'com.docker.compose.project': PROJECT, 'com.docker.compose.service': SERVICE,
                     'com.docker.compose.container-number': '1', 'com.docker.compose.oneoff': 'False',
                     'com.docker.compose.depends_on': ''}
    if incident_profile != 'original':
        compose_fixed['com.docker.compose.replace'] = SERVICE+'-1'
    compose_provenance = {'com.docker.compose.config-hash','com.docker.compose.image',
                          'com.docker.compose.project.config_files','com.docker.compose.project.working_dir','com.docker.compose.project.environment_file',
                          'com.docker.compose.version'}
    checks['compose_fixed_labels'] = all(compose_labels.get(k) == v for k,v in compose_fixed.items())
    checks['compose_provenance_schema'] = set(compose_labels) == set(compose_fixed) | compose_provenance
    checks['compose_image_provenance'] = compose_labels.get('com.docker.compose.image') == BASE
    checks['compose_hash_provenance_format'] = bool(re.fullmatch(r'[0-9a-f]{64}', compose_labels.get('com.docker.compose.config-hash', '')))
    old_working = '/root/secure-envs/trading-bot/release-control/production-4ef8e6dc-cutover/.web-bluegreen.json.compose.242959.6db610ac44418756'
    if incident_profile != 'original':
        old_working = str(ROOT)
        suffix = '04' if incident_profile == 'after_capture04' else '02'
        expected_config_files = ','.join(str(ROOT/name) for name in ('compose.yml','compose.web.yml','capture2-image-only.yml'))+',/root/.local/security-capture2-observability-20261024-'+suffix+'-activation/rollback.yml'
        expected_environment_file = str(ROLE_ENV)
    else:
        expected_config_files = old_working+'/compose.yml,'+old_working+'/compose.web.yml'
        expected_environment_file = old_working+'/runtime.env'
    checks['compose_old_file_provenance'] = compose_labels.get('com.docker.compose.project.working_dir') == old_working and compose_labels.get('com.docker.compose.project.config_files') == expected_config_files and compose_labels.get('com.docker.compose.project.environment_file') == expected_environment_file
    checks['compose_cli_provenance_format'] = bool(re.fullmatch(r'\d+\.\d+\.\d+(?:[-+][A-Za-z0-9._-]+)?', compose_labels.get('com.docker.compose.version','')))
    if incident_profile != 'original':
        checks['compose_cli_provenance_format'] = compose_labels.get('com.docker.compose.version') == '2.40.3'
    checks['old_image_reference_schema'] = isinstance(config.get('Image'),str) and bool(re.fullmatch(r'[A-Za-z0-9._:/@-]{1,256}',config['Image']))
    checks['dynamic_network_configuration'] = all(not (item or {}).keys() - {'aliases'} for item in service['networks'].values()) and all(not item.get('IPAMConfig') and not item.get('DriverOpts') and not item.get('Links') for item in live['networks'].values())
    # Every HostConfig field is handled, including inactive defaults. Empty
    # collection vs null is the only normalization for documented empty lists.
    empty_fields = {'BlkioDeviceReadBps','BlkioDeviceReadIOps','BlkioDeviceWriteBps','BlkioDeviceWriteIOps',
                    'BlkioWeightDevice','CapAdd','DeviceCgroupRules','DeviceRequests','Devices','Dns',
                    'DnsOptions','DnsSearch','ExtraHosts','GroupAdd','Links','PortBindings','Ulimits','VolumesFrom'}
    zero_fields = {'BlkioWeight','CpuCount','CpuPercent','CpuPeriod','CpuQuota','CpuRealtimePeriod',
                   'CpuRealtimeRuntime','CpuShares','IOMaximumBandwidth','IOMaximumIOps','MemoryReservation','OomScoreAdj'}
    text_fields = {'Cgroup','CgroupParent','ContainerIDFile','CpusetCpus','CpusetMems','Isolation',
                   'PidMode','UTSMode','UsernsMode','VolumeDriver'}
    host_fixed = {'AutoRemove':False,'CgroupnsMode':'private','ConsoleSize':[0,0],'Init':True,'IpcMode':'private',
                  'MemorySwappiness':None,'OomKillDisable':None,'Privileged':False,'PublishAllPorts':False,
                  'ReadonlyRootfs':True,'Runtime':'runc','ShmSize':67108864,
                  'MemorySwap':int(service['mem_limit'])*2,
                  'NetworkMode':rendered['networks']['market-backend']['name']}
    masked = {'/proc/acpi','/proc/asound','/proc/interrupts','/proc/kcore','/proc/keys','/proc/latency_stats',
              '/proc/sched_debug','/proc/scsi','/proc/timer_list','/proc/timer_stats','/sys/devices/virtual/powercap','/sys/firmware'}
    readonly = {'/proc/bus','/proc/fs','/proc/irq','/proc/sys','/proc/sysrq-trigger'}
    host_accounted = empty_fields | zero_fields | text_fields | set(host_fixed) | {
        'Binds','CapDrop','LogConfig','MaskedPaths','Memory','Mounts','NanoCpus','PidsLimit','ReadonlyPaths',
        'RestartPolicy','SecurityOpt','Tmpfs'}
    checks['full_host_schema'] = set(host) == host_accounted
    checks['full_host_empty_defaults'] = all(host.get(k) is None or host.get(k) == [] for k in empty_fields - {'PortBindings'}) and host.get('PortBindings') in (None,{})
    checks['full_host_zero_defaults'] = all(type(host.get(k)) is int and host[k] == 0 for k in zero_fields)
    checks['full_host_text_defaults'] = all(host.get(k) == '' for k in text_fields)
    checks['full_host_fixed_defaults'] = all(host.get(k) == v for k,v in host_fixed.items())
    checks['kernel_masked_paths'] = set(host.get('MaskedPaths') or []) == masked
    checks['kernel_readonly_paths'] = set(host.get('ReadonlyPaths') or []) == readonly
    expected_binds = sorted(item['source'] + ':' + item['target'] + (':ro' if item.get('read_only') else ':rw') for item in service.get('volumes',[]))
    expected_secret_mounts = []
    for item in service.get('secrets',[]):
        target = item.get('target',item['source'])
        expected_secret_mounts.append({'Type':'bind','Source':rendered['secrets'][item['source']]['file'],
            'Target':target if target.startswith('/') else '/run/secrets/'+target,'ReadOnly':True,'BindOptions':{}})
    checks['native_bind_configuration'] = sorted(host.get('Binds') or []) == expected_binds
    checks['native_secret_mount_configuration'] = sorted(host.get('Mounts') or [],key=lambda x:x['Target']) == sorted(expected_secret_mounts,key=lambda x:x['Target'])
    allowed_service = {'image','profiles','user','read_only','init','restart','cap_drop','security_opt','pids_limit',
                       'mem_limit','cpus','tmpfs','logging','environment','command','entrypoint','secrets','volumes','depends_on','healthcheck','networks'}
    checks['resolved_service_schema'] = set(service) <= allowed_service
    # This proof never performs rollout or grants operational authorization.
    public_env_names = set(re.findall(rb'^  ([A-Z][A-Z0-9_]+):', base64.b64decode(PUBLIC_COMPOSE['compose.yml']), re.M))
    public_env_names.update(re.findall(rb'^      ([A-Z][A-Z0-9_]+):', base64.b64decode(PUBLIC_COMPOSE['compose.web.yml']), re.M))
    actual_env = environment(config.get('Env'))
    changed_public_env_names = sorted(name.decode() for name in public_env_names
                                    if expected_env.get(name.decode()) != actual_env.get(name.decode()))
    revision = (base['config'].get('Labels') or {}).get('org.opencontainers.image.revision')
    public_revision = revision if isinstance(revision, str) and re.fullmatch(r'[0-9a-f]{40}', revision) else None
    print(json.dumps({'status': 'initial_config_comparison_complete', 'checks': checks,
                      'base_image_public_revision': public_revision,
                      'changed_public_environment_names': changed_public_env_names,
                      'remaining_environment_key_difference_count': len(set(expected_env) ^ set(actual_env)),
                      'non_compose_label_difference_count': len(set(expected_labels.items()) ^ set(non_compose_labels.items())),
                      'private_values_emitted': False, 'role_env_metadata_unchanged': True,
                      'full_runtime_parity': all(checks.values()), 'activation_requires_no_deps': True,
                      'incident_profile': incident_profile,
                      'rollout_authorized': False}, sort_keys=True))
    return 0 if all(checks.values()) else 3

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, KeyError, TypeError):
        print(json.dumps({'status': 'config_parity_probe_refused', 'full_runtime_parity': False, 'private_values_emitted': False}))
        raise SystemExit(1)
