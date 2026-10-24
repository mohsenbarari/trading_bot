#!/usr/bin/env python3
"""Finite managed backup configuration staging; no backup or runtime mutation."""
from contextlib import ExitStack
from datetime import datetime, timezone
from hashlib import sha256
import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import sys
import time
from types import ModuleType

PLAN = Path('/root/.local/security-web-engine-20261024-task07/plan.json')
PLAN_SHA = 'f8b4759ea648ec0d8b8528f43c74ecc281e68b2d2fd94431398923c62e0f16c5'
CONTROL = Path('/root/secure-envs/trading-bot/release-control')
MARKER = Path('/root/secure-envs/trading-bot/queue-cutover-artifacts/production-release.lock')
MARKER_DEVICE, MARKER_INODE = 2049, 648637
MARKER_SHA = '0fcd5b5165f31e77665fb56a140ed0a1ad61f0098b359730b26f89ec81a074f5'
SOURCE = CONTROL / 'security-web-engine-20261023-07.source.lock'
RESERVATION = CONTROL / 'private-primary-controller.lock'
ENV = Path('/srv/trading-bot/market-pipeline-releases/f3be9ae2c4e9df0da31abaa41e91f327eb9573a2/web.release.env')
MANAGED = ENV.parent.parent / 'security-backup-managed-20261024-03'
STAGE = Path('/root/.local/security-web-market-managed-config-20261024-03')
CONTROLS = {
    PLAN: PLAN_SHA,
    CONTROL / 'security-web-c07-terminal-closure-20261024-01.json': '712af9788ef73e3934252d7b7e50bd0681bbfea13c25088da59a123efabb5dc0',
    CONTROL / 'security-web-engine-20261023-07.qa-parking.json': 'd9ddd4d3c1c5771f20014d1d986bae720e82439ff07a1869653c1174ea465e9e',
    CONTROL / 'security-capture2-observability-20261024-06.activation.json': '22923a5f68f415e86ffd7f1db0563ed8973d7bc3dd6fbce39537e17dcf810dbf',
}
PINS = {
    'web_engine_preflight.py': 'cbadbc1085dd5e351510ed719a9e6c779523c4d644f53198d4f9c0c1d7750679',
    'backup_market_pipeline_archive.py': 'e2083dcedef0c149f0ffd1f21a7855dd4db5ccaba9ea3fe136b8400661678a6c',
    'prepare_market_pipeline_primary_release.py': '9e9179f5276ff33a5e7da8b378f917c2f0525e314c0311ca02b0a2d5ef45591b',
    'prepare_market_pipeline_release.py': '6355aafa369b3abd652ff251a19fa9e329a8104bf9781ba197b1dc5e113677d5',
}
OLD_CAPTURE = '9870311065b13646767dfad1e48214d6a231e3898e1f248bb9559edd8926966c'
CAPTURE = '6a16c8a1b4cdab3b4bcd2a0d4d4998ced7a455cc6c364255e5e0e3dacd9c2752'
BASE = 'sha256:2239469ab3dd3e291e16b4571af84d446f776ade05d48552f150b7ec2670fcc6'
FUNCTIONAL = 'sha256:f79cdda21c192c52536f4ccfecadc7f724453a9ea4abfae96b1efe540ebbc61b'
ANCHOR = 'sha256:e46aedbe135117301fb19183ff39587174162ca4a454ac60c7fccb2b302033cd'
PROVENANCE = {'image_id': ANCHOR, 'release_sha': 'a6dcd636a1525a3a3d47e2a6083ca887b16080db',
              'release_tree': '259c9c4964864b7acfd92f94c9856e664130d082',
              'image_input_signature': '669ee2267889d54bb36ef4cd8184d7a02c10fadeb4f39609e60ddce9f6f62a69'}
FUNCTIONAL_LABELS = {
    'io.gold-trade.capture.replay.baseline': 'd8f15f8212b9510e3abb5301d0201b2ba1eca13224f5a01c682d1de6716cb7d5',
    'io.gold-trade.capture.replay.fix-commit': 'adca507b261d3e139befc67b4a937c48c96bb949',
    'io.gold-trade.capture.replay.source-sha256': '1b9c611692978edec4c37de217e77abf00f5a0e456006060c936d40c53978929',
    'io.gold-trade.capture.replay.status': 'functional_fix_not_security_acceptance',
}
LABEL_TEMPLATE = '{' + ','.join(json.dumps(key) + ':{{json (index .Config.Labels ' + json.dumps(key) + ')}}'
                              for key in FUNCTIONAL_LABELS) + '}'
INSPECT = ('{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Image}},'
           '"running":{{json .State.Running}},"restarting":{{json .State.Restarting}},'
           '"restart_policy":{{json .HostConfig.RestartPolicy}},"mounts":{{json .Mounts}},'
           '"project":{{json (index .Config.Labels "com.docker.compose.project")}},'
           '"network_mode":{{json .HostConfig.NetworkMode}},"port_bindings":{{json .HostConfig.PortBindings}},'
           '"network_names":{{json .NetworkSettings.Networks}},"functional":' + LABEL_TEMPLATE + '}')
IMAGE = ('{"id":{{json .Id}},"layers":{{json .RootFS.Layers}},'
         '"release_sha":{{json (index .Config.Labels "org.opencontainers.image.revision")}},'
         '"release_tree":{{json (index .Config.Labels "io.gold-trade.release.tree")}},'
         '"image_input_signature":{{json (index .Config.Labels "io.gold-trade.release.input-signature")}},'
         '"functional":' + LABEL_TEMPLATE + '}')


class Refusal(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise Refusal(code)


class Clock:
    def __init__(self, deadline):
        require(type(deadline) is str and deadline.endswith('Z'), 'managed_deadline_invalid')
        self.deadline = datetime.fromisoformat(deadline.replace('Z', '+00:00'))
        require(0 < self.remaining() <= 600, 'managed_deadline_invalid')

    def remaining(self):
        return (self.deadline - datetime.now(timezone.utc)).total_seconds()

    def check(self):
        require(self.remaining() > 0, 'managed_deadline_expired')


def command(P, clock, arguments):
    clock.check()
    value = P.command(arguments)
    clock.check()
    return value


def public_profile(P, clock, plan, parking):
    rows = plan['containers']
    require(len(rows) == 27 and len({row['id'] for row in rows}) == 27, 'managed_baseline_invalid')
    parked = parking['parked_ids']
    require(isinstance(parked, list) and len(parked) == len(set(parked)) == 10
            and set(parked) == {row['id'] for row in rows if row['project'] == 'three-site-dc32d90392b0-webapp-fi'},
            'managed_parking_scope_changed')
    expected = {}
    for row in rows:
        row = dict(row)
        if row['id'] == OLD_CAPTURE:
            require(row['image'] == BASE, 'managed_original_capture_changed')
            row.update(id=CAPTURE, image=FUNCTIONAL)
        if row['id'] in parked:
            row.update(running=False, restart_policy={'Name': 'no', 'MaximumRetryCount': 0})
        expected[row['id']] = row
    require(CAPTURE in expected and sum(row['running'] for ident, row in expected.items() if ident not in parked) == 16,
            'managed_retained_intent_changed')
    ids = command(P, clock, ['/usr/bin/docker', 'ps', '-aq', '--no-trunc']).splitlines()
    require(len(ids) == len(set(ids)) == 27 and set(ids) == set(expected), 'managed_runtime_inventory_changed')
    result = []
    for ident in sorted(expected):
        row = json.loads(command(P, clock, ['/usr/bin/docker', 'inspect', '--format', INSPECT, ident]))
        before = expected[ident]
        keys = ('id', 'name', 'image', 'running', 'restart_policy', 'project', 'network_mode', 'port_bindings')
        require(all(type(row[key]) is type(before[key]) and row[key] == before[key] for key in keys)
                and row['restarting'] is False
                and sorted(row['network_names'] or {}) == sorted(before['network_names'])
                and P.canonical_mounts(row['mounts']) == P.canonical_mounts(before['mounts']), 'managed_runtime_profile_changed')
        if ident == CAPTURE:
            require(row['functional'] == FUNCTIONAL_LABELS, 'managed_capture_functional_labels_changed')
        result.append({**{key: row[key] for key in keys}, 'restarting': False,
                       'network_names': sorted(row['network_names'] or {}), 'mounts': P.canonical_mounts(row['mounts'])})
    anchors = [row for row in result if row['name'] == '/market-private-pipeline-primary-market-capture-account1-1']
    require(len(anchors) == 1 and anchors[0]['image'] == ANCHOR and anchors[0]['running'] is True,
            'managed_source_anchor_changed')
    images = {image: json.loads(command(P, clock, ['/usr/bin/docker', 'image', 'inspect', '--format', IMAGE, image]))
              for image in (ANCHOR, BASE, FUNCTIONAL)}
    require(all(value['id'] == image for image, value in images.items()), 'managed_image_identity_changed')
    require({key: images[ANCHOR][key] for key in PROVENANCE if key != 'image_id'}
            == {key: value for key, value in PROVENANCE.items() if key != 'image_id'}, 'managed_source_provenance_changed')
    layers, derived = images[BASE]['layers'], images[FUNCTIONAL]['layers']
    require(isinstance(layers, list) and layers and isinstance(derived, list)
            and len(derived) == len(layers) + 2 and derived[:len(layers)] == layers
            and images[FUNCTIONAL]['functional'] == FUNCTIONAL_LABELS, 'managed_capture_lineage_changed')
    return {'containers': result, 'source': PROVENANCE, 'capture_lineage': images}


def controls(P):
    documents = [P.document(path, digest) for path, digest in CONTROLS.items()]
    plan, closure, parking, capture = documents
    require(closure['phase'] == 'aborted_before_install_fences_released'
            and closure['security_transition_completed'] is False, 'managed_closure_not_complete')
    require(parking['phase'] == 'qa_parked_production_incident_open' and parking['qa_parking_verified'] is True
            and parking['parked_count'] == 10 and parking['retained_count'] == 17
            and parking['security_transition_completed'] is False, 'managed_parking_evidence_changed')
    require(capture['schema'] == 'capture2_observability_activation/1'
            and capture['phase'] == 'diagnostic_image_active_incident_open'
            and capture['new_capture_id'] == CAPTURE and capture['candidate_image'] == FUNCTIONAL
            and capture['candidate_configuration_accepted'] is True
            and capture['all_other_26_configuration_unchanged'] is True
            and capture['original_plan_sha256'] == PLAN_SHA
            and capture['security_transition_completed'] is False, 'managed_capture_evidence_changed')
    options = plan['market_backup']
    require(options['env_file'] == str(ENV) and all(options[key] == value for key, value in PROVENANCE.items()),
            'managed_original_source_binding_changed')
    require(plan['marker']['path'] == str(MARKER) and plan['marker']['device'] == MARKER_DEVICE
            and plan['marker']['inode'] == MARKER_INODE
            and plan['marker']['sha256'] == MARKER_SHA, 'managed_marker_plan_changed')
    return plan, parking, {path: P.identity(path.lstat()) for path in CONTROLS}


def boundaries(P, clock, plan, identities, marker, marker_identity):
    clock.check()
    require(not any(os.path.lexists(path) for path in (SOURCE, RESERVATION, P.PENDING)), 'managed_foreign_operation')
    require(not any(os.path.lexists(path) for path in plan['absent_control_paths']), 'managed_foreign_operation')
    require(P.identity(os.fstat(marker)) == marker_identity == P.identity(MARKER.lstat()), 'managed_marker_changed')
    require(sha256(os.pread(marker, 8193, 0)).hexdigest() == plan['marker']['sha256'], 'managed_marker_changed')
    for path, expected in identities.items():
        require(P.identity(path.lstat()) == expected, 'managed_control_changed')
        P.document(path, CONTROLS[path])
    for path, expected in getattr(P, '_managed_public_files', {}).items():
        require(P.identity(path.lstat()) == expected[0]
                and P.file_digest(path, maximum=131072, private=True) == expected[1], 'managed_public_source_changed')


def provision(P, backup, primary, clock):
    require(os.geteuid() == 0, 'managed_native_root_required')
    plan, parking, identities = controls(P)
    require(Path('/etc/machine-id').read_text().strip() == plan['machine_id'], 'managed_host_changed')
    P.safe_parent(MARKER)
    with ExitStack() as stack:
        marker = os.open(MARKER, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        stack.callback(os.close, marker)
        info = os.fstat(marker)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= 8192
                and (info.st_dev, info.st_ino) == (MARKER_DEVICE, MARKER_INODE), 'managed_marker_changed')
        marker_identity = P.identity(info)
        wait_until = time.monotonic() + min(5, clock.remaining())
        while True:
            try:
                fcntl.flock(marker, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                clock.check(); require(time.monotonic() < wait_until, 'managed_marker_busy'); time.sleep(.1)
        boundaries(P, clock, plan, identities, marker, marker_identity)
        with backup.BoundRoleEnv(ENV) as original:
            require(list(P.identity(os.fstat(original.descriptor))) == plan['market_backup']['env_file_identity'],
                    'managed_original_env_metadata_changed')
            before = public_profile(P, clock, plan, parking)
            public_bytes = json.dumps(before, sort_keys=True, separators=(',', ':')).encode()
            evidence = sha256(public_bytes).hexdigest()
            options = dict(source_image_id=ANCHOR, source_release_sha=PROVENANCE['release_sha'],
                           source_release_tree=PROVENANCE['release_tree'],
                           source_image_input_signature=PROVENANCE['image_input_signature'], source_evidence_sha256=evidence)
            P.safe_parent(MANAGED)
            require(not os.path.lexists(MANAGED), 'managed_destination_exists')
            boundaries(P, clock, plan, identities, marker, marker_identity); original.check()
            MANAGED.mkdir(mode=0o700)
            arguments = dict(rendered_env=ENV, web_env=MANAGED/'web.release.env', receipt=MANAGED/'web-managed-receipt.json', **options)
            primary.render_web_metadata(**arguments)
            evidence_path = MANAGED/'public-source-evidence.json'
            primary._atomic_write(evidence_path, public_bytes, exclusive=True, _metadata=True)
            primary.verify_web_metadata(**arguments)
            values = backup.validate_release_env(arguments['web_env'], release_sha=PROVENANCE['release_sha'],
                                                 image_id=ANCHOR, allow_target_identity_mismatch=True)
            del values
            require(public_profile(P, clock, plan, parking) == before, 'managed_public_source_changed')
            boundaries(P, clock, plan, identities, marker, marker_identity); original.check()
            require(P.document(evidence_path, evidence) == before, 'managed_public_evidence_changed')
            primary.verify_web_metadata(**arguments)
            clock.check()
    return {'status': 'PASS', 'reason_code': None, 'managed_configuration_staged': True,
            'validation_passed': True, 'original_retained': True, 'runtime_changed': False,
            'backup_created': False, 'deployment_eligible': False, 'protected_env_content_hashed': False,
            'secret_or_session_contents_read': False, 'secrets_disclosed': False}


def load_sources(sources):
    require(set(sources) == set(PINS), 'managed_public_source_inventory_invalid')
    for name, data in sources.items():
        require(0 < len(data) <= 131072 and sha256(data).hexdigest() == PINS[name], 'managed_public_source_changed')
    P = ModuleType('_managed_preflight')
    exec(compile(sources['web_engine_preflight.py'], '<reviewed_preflight>', 'exec'), P.__dict__)
    P.safe_parent(STAGE)
    require(not os.path.lexists(STAGE), 'managed_public_stage_exists')
    STAGE.mkdir(mode=0o700)
    package = STAGE/'scripts'; package.mkdir(mode=0o700)
    public = {**sources, '__init__.py': b''}
    for name, data in public.items():
        descriptor = os.open(package/name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'wb') as output:
            output.write(data); output.flush(); os.fsync(output.fileno())
    require(not any(name == 'scripts' or name.startswith('scripts.') for name in sys.modules), 'managed_import_namespace_occupied')
    P._managed_public_files = {package/name: (P.identity((package/name).lstat()), (len(data), sha256(data).hexdigest()))
                               for name, data in public.items()}
    # Execute the already pinned public buffers, never reopen staged Python by
    # pathname for import. Staged bytes remain independently checked evidence.
    scripts = ModuleType('scripts'); scripts.__path__ = []
    sys.modules['scripts'] = scripts
    for name in ('prepare_market_pipeline_release.py', 'backup_market_pipeline_archive.py', 'prepare_market_pipeline_primary_release.py'):
        fullname = 'scripts.' + name.removesuffix('.py')
        module = ModuleType(fullname); module.__package__ = 'scripts'; module.__file__ = str(package/name)
        sys.modules[fullname] = module
        exec(compile(sources[name], str(package/name), 'exec'), module.__dict__)
    return P, sys.modules['scripts.backup_market_pipeline_archive'], sys.modules['scripts.prepare_market_pipeline_primary_release']


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deadline-utc', required=True)
    for name in PINS:
        parser.add_argument('--' + name.removesuffix('.py').replace('_', '-'), required=True)
    args = parser.parse_args(argv)
    try:
        clock = Clock(args.deadline_utc)
        signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(Refusal('managed_deadline_expired')))
        signal.alarm(max(1, math.ceil(clock.remaining())))
        os.umask(0o077)
        modules = load_sources({name: getattr(args, name.removesuffix('.py')).encode() for name in PINS})
        result = provision(*modules, clock)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as error:
        code = str(error)
        allowed = {'primary_managed_source_release_mismatch', 'backup_source_release_sha_invalid', 'backup_source_image_identity_invalid'}
        reason = code if ((isinstance(error, Refusal) and re.fullmatch('managed_[a-z0-9_]{1,80}', code)) or code in allowed) else 'managed_configuration_refused'
        print(json.dumps({'status': 'FAIL', 'reason_code': reason, 'managed_configuration_staged': False,
                          'validation_passed': False, 'runtime_changed': False, 'backup_created': False,
                          'deployment_eligible': False, 'secrets_disclosed': False}, sort_keys=True))
        return 1
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    raise SystemExit(main())
