#!/usr/bin/env python3
"""Close only the aborted C07 ownership transaction; never claim security completion."""
from contextlib import ExitStack
from datetime import datetime, timezone, timedelta
from hashlib import sha256
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import time
from types import ModuleType

OP = 'security-web-c07-terminal-closure-20261024-01'
CONFIRM = 'CLOSE-ONLY-ABORTED-C07-OWNED-FENCES-20261024-01'
CAPTURE_ID = '6a16c8a1b4cdab3b4bcd2a0d4d4998ced7a455cc6c364255e5e0e3dacd9c2752'
CAPTURE_SHA = '22923a5f68f415e86ffd7f1db0563ed8973d7bc3dd6fbce39537e17dcf810dbf'
PINS = {
    'capture': '696c2a0572de85a06b30fd990197f442e21f395bd1055c92159d0231cacaa709',
    'parking': '83650494adc141234c447f8020f11b0cce4a2a8e419a00ae1141b29e92c4a928',
    'qa': '9703ddf97439fd1578a4137ce3ad0e38d48c2c1acf0cf891b588527494982272',
    'hot': 'cb3e03503a0bddae0d7bd7d31f615606d2645b0f6f9b10dfa7468fef108995aa',
    'parity': 'd5e44f2b35f93e5484ca0c86d749282978f2afba9a5093a5ed9eb282ec71e91e',
}

def require(ok, code):
    if not ok:
        raise RuntimeError(code)

def reviewed(raw, key):
    data = raw.encode()
    require(0 < len(data) <= 65536 and sha256(data).hexdigest() == PINS[key], 'closure_reviewed_source_changed')
    module = ModuleType('_closure_' + key)
    exec(compile(data, '<reviewed_closure_' + key + '>', 'exec'), module.__dict__)
    return module

def current_profile(plan, V):
    anchors = [row for row in plan['containers'] if row['id'] == V.CID]
    require(len(anchors) == 1 and anchors[0]['image'] == V.BASE
            and anchors[0]['name'] == '/' + V.PROJECT + '-' + V.SERVICE + '-1', 'closure_original_capture_changed')
    return {**plan, 'schema': 'c07_terminal_current_inventory/1', 'operation_id': OP,
            'original_operation_id': plan['operation_id'],
            'containers': [{**row, 'id': CAPTURE_ID, 'image': V.FUNCTIONAL_IMAGE}
                           if row['id'] == V.CID else row for row in plan['containers']]}

def terminal_owners(C, H, A):
    units = [
        'security-web-engine-07-' + H.NONCE + '.service',
        'security-web-engine-07-owned-restore-' + H.NONCE + '.service',
        'security-web-engine-07-owned-intent-' + H.NONCE + '.service',
        'security-market-hot-preflight-20261024-01.service',
        'security-web-qa-parking-20261024-01.service',
        *['security-capture2-observability-20261024-0' + str(n) + '.service' for n in range(1, 7)],
    ]
    scopes = ['security-web-engine-' + H.NONCE + '-' + n + '.scope'
              for n in ('pre_install', 'install_restore')]
    for pid in (2110733, 2147364, H.OWNER):
        require(not os.path.lexists('/proc/' + str(pid)), 'closure_previous_owner_alive')
    for name in units:
        state = dict(line.split('=', 1) for line in C.B.command([
            '/usr/bin/systemctl', 'show', name, '--property=LoadState,ActiveState,MainPID']).splitlines())
        require(state.get('MainPID') == '0' and state.get('ActiveState') in {'inactive', 'failed'}
                and state.get('LoadState') in {'loaded', 'not-found'}, 'closure_owned_unit_active')
    for name in [*units, *scopes]:
        require(C.scope_empty(Path('/sys/fs/cgroup/system.slice') / name), 'closure_owned_descendants_present')
    # Do not reject unrelated ordinary watchdog/healthcheck jobs.
    import dbus
    bus = dbus.SystemBus(private=True)
    try:
        manager = dbus.Interface(bus.get_object('org.freedesktop.systemd1', '/org/freedesktop/systemd1'),
                                 'org.freedesktop.systemd1.Manager')
        jobs = manager.ListJobs(timeout=5)
        require(not any(str(row[1]) in set(units + scopes) for row in jobs), 'closure_owned_job_pending')
    finally:
        bus.close()

def evidence(C, H, A):
    parking_path = C.ROOT / (H.OP + '.qa-parking.json')
    capture_path = C.ROOT / (A.OP + '.activation.json')
    parking_raw, capture_raw = (C.B.read(path, maximum=65536) for path in (parking_path, capture_path))
    parking, capture = map(json.loads, (parking_raw, capture_raw))
    require(sha256(parking_raw).hexdigest() == A.PARK_AUDIT_SHA
            and parking['phase'] == 'qa_parked_production_incident_open'
            and parking['qa_parking_verified'] is True and parking['parked_count'] == 10
            and parking['retained_count'] == 17 and parking['old_fences_retained'] is True
            and parking['security_transition_completed'] is False, 'closure_parking_evidence_changed')
    require(sha256(capture_raw).hexdigest() == CAPTURE_SHA and capture['operation_id'] == A.OP
            and capture['schema'] == 'capture2_observability_activation/1'
            and capture['phase'] == 'diagnostic_image_active_incident_open'
            and capture['new_capture_id'] == CAPTURE_ID and capture['candidate_image'].startswith('sha256:')
            and capture['candidate_configuration_accepted'] is True
            and capture['all_other_26_configuration_unchanged'] is True
            and capture['original_operation_id'] == H.OP and capture['original_plan_sha256'] == H.PLAN_SHA
            and capture['security_transition_completed'] is False, 'closure_capture_acceptance_changed')
    originals = {parking_path: parking_raw, capture_path: capture_raw}
    for item in parking['preserved_evidence']:
        path = Path(item['path'])
        require(path in {C.paths({'operation_id': H.OP})[0], C.ROOT / (H.OP + '.owned-restore.json'),
                         C.ROOT / (H.OP + '.owned-intent.json')}, 'closure_original_evidence_inventory')
        raw = C.B.read(path, maximum=65536)
        require(raw.decode() == item['raw_json'] and sha256(raw).hexdigest() == item['sha256'], 'closure_original_evidence_changed')
        originals[path] = raw
    require(len(originals) == 5, 'closure_original_evidence_inventory')
    return parking, capture, originals

def fence_unchanged(C, path, fd, raw, identity):
    require(C.B.identity(os.fstat(fd)) == identity == C.B.identity(path.lstat())
            and C.B.read(path, maximum=8192) == raw, 'closure_owned_fence_changed')

def ready(C, P, R, H, A, V, plan, profile, selected, resources, native):
    R.hot_finished(C, H)
    incidents = P.inventory(C, R, profile, parked=True)
    require(all(row['id'] in R.KNOWN_MARKET_FLAPS for row in incidents), 'closure_new_production_incident')
    # Only health-cadence oscillation remains open; a stopped/restarting
    # retained writer is not evidence of restored runtime intent.
    require(all(row['running'] and not row['restarting'] for row in incidents), 'closure_retained_process_not_ready')
    require(C.B.command(['/usr/bin/docker', 'inspect', '--format',
            '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}', CAPTURE_ID]) == 'healthy',
            'closure_capture_health_not_ready')
    require(P.preserved_resources(C, selected) == resources, 'closure_qa_resource_changed')
    require(not C.B.command(['/usr/bin/ss', '-H', '-ltn', 'sport = :8443']), 'closure_qa_port_open')
    P.web_datastores_native(C, R, profile)
    require(A.legacy(C) == native, 'closure_legacy_guards_changed')
    require(not os.path.lexists(C.P.PENDING) and not any(os.path.lexists(p) for p in plan['absent_control_paths']),
            'closure_foreign_pending_operation')
    return incidents

def capture_current(A, V, rows):
    require(set(rows) and CAPTURE_ID in rows, 'closure_capture_missing')
    row = rows[CAPTURE_ID]
    require(row['image'] == V.FUNCTIONAL_IMAGE and row['running'] and not row['restarting']
            and row['pid'] > 0 and row['name'] == '/' + V.PROJECT + '-' + V.SERVICE + '-1', 'closure_capture_not_running')
    require(all((row['config'].get('Labels') or {}).get(key) == value for key, value in V.FUNCTIONAL_LABELS.items()),
            'closure_capture_functional_provenance_changed')
    A.same_session_owner(rows, CAPTURE_ID)

def release_owned(C, ordered, fds, originals, identities, receipt_path, receipt, final_check):
    # A crash at any point remains ambiguous: never automatically adopt/retry
    # this attempt. The permanent marker is not in this deletion inventory.
    receipt.update(phase='aborted_before_install_fence_release_pending')
    C.write_json(receipt_path, receipt, exclusive=True)
    for path in ordered:
        fence_unchanged(C, path, fds[path], originals[path], identities[path])
        os.unlink(path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    final_check()
    receipt.update(phase='aborted_before_install_fences_released', owned_source_and_reservation_released=True)
    C.write_json(receipt_path, receipt, exclusive=False)

def execute(P, R, H, A, V, action):
    clock = A.Clock()
    clock.deadline_utc = datetime.now(timezone.utc) + timedelta(seconds=600)
    C = H.controller()
    plan = C.P.document(H.OLD_ROOT / 'plan.json', H.PLAN_SHA)
    require(datetime.now(timezone.utc).date().isoformat() == '2026-10-24', 'closure_scope_day_expired')
    selected, _ = R.exact_scope(plan)
    profile = current_profile(plan, V)
    path = C.ROOT / (OP + '.json')
    with ExitStack() as stack, R.pinned(C):
        fds, originals, identities = {}, {}, {}
        for target in (C.P.MARKER, C.paths(plan)[1], C.P.RESERVATION):
            if target != C.P.MARKER and os.path.lexists(path):
                break
            raw = C.B.read(target, maximum=8192)
            prior = C.B.identity(target.lstat())
            fd = os.open(target, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
            stack.callback(os.close, fd)
            A.acquire_marker(fd, clock) if target == C.P.MARKER else fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fence_unchanged(C, target, fd, raw, prior)
            fds[target], originals[target], identities[target] = fd, raw, prior
        if os.path.lexists(path):
            receipt = C.B.document(path)
            require(action == 'check' and receipt['schema'] == 'web_c07_terminal_closure/1'
                    and receipt['operation_id'] == OP and receipt['original_plan_sha256'] == H.PLAN_SHA
                    and receipt['phase'] == 'aborted_before_install_fences_released'
                    and receipt['security_transition_completed'] is False
                    and not os.path.lexists(C.paths(plan)[1]) and not os.path.lexists(C.P.RESERVATION),
                    'closure_partial_or_previous_attempt_requires_review')
            marker = plan['marker']
            require(sha256(originals[C.P.MARKER]).hexdigest() == marker['sha256']
                    and identities[C.P.MARKER][:2] == (marker['device'], marker['inode']), 'closure_marker_changed')
            _, _, preserved = evidence(C, H, A)
            require(receipt['evidence'] == {str(p): sha256(raw).hexdigest() for p, raw in preserved.items()},
                    'closure_completed_evidence_changed')
            return {'status': receipt['phase'], 'historical_closure_verified': True, 'runtime_changed': False,
                    'current_runtime_rechecked': False, 'security_transition_completed': False}
        P.owner_proof(C, H, plan, fds)
        terminal_owners(C, H, A)
        parking, capture, preserved = evidence(C, H, A)
        require(parking['parked_ids'] == sorted(R.IDS) and capture['candidate_image'] == V.FUNCTIONAL_IMAGE,
                'closure_authorized_scope_changed')
        native = A.legacy(C)
        resources = parking['preserved_resources']
        before = A.snapshot(V, clock)
        capture_current(A, V, before)
        all_incidents = []
        # Actual 30-second observation; no healthcheck ExecIDs prohibition.
        require(clock.seconds(31) == 31, 'closure_observation_clock_insufficient')
        for index in range(3):
            if index:
                time.sleep(15)
            all_incidents.extend(ready(C, P, R, H, A, V, plan, profile, selected, resources, native))
            after = A.snapshot(V, clock)
            capture_current(A, V, after)
            A.other_runtime(before, after, None, known_incidents=frozenset(R.KNOWN_MARKET_FLAPS))
        terminal_owners(C, H, A)
        P.owner_proof(C, H, plan, fds)
        require(all(C.B.read(p, maximum=65536) == raw for p, raw in preserved.items()), 'closure_historical_evidence_changed')
        for target in originals:
            fence_unchanged(C, target, fds[target], originals[target], identities[target])
        receipt = {'schema': 'web_c07_terminal_closure/1', 'operation_id': OP, 'pid': os.getpid(),
            'original_operation_id': H.OP, 'original_nonce': H.NONCE, 'original_plan_sha256': H.PLAN_SHA,
            'original_deadline_utc': plan['deadline_utc'], 'closure_deadline_utc': clock.deadline_utc.isoformat(),
            'original_phase': 'writers_quiesced', 'original_package_install_started': False,
            'security_transition_completed': False, 'all_prior_runtime_healthy': False,
            'known_market_incident_ids': sorted(R.KNOWN_MARKET_FLAPS), 'known_market_incidents_status': 'open',
            'observed_known_health_incidents': all_incidents,
            'known_market_state_deltas': A.incident_deltas(before, after, R.KNOWN_MARKET_FLAPS),
            'current_runtime_intent_verified': True, 'retained_count': 17, 'retained_expected_running': 16,
            'parked_count': 10, 'qa_port_8443_closed': True, 'qa_resources_preserved': True,
            'capture_identity_delta': {'historical_id': V.CID, 'current_id': CAPTURE_ID, 'current_image': V.FUNCTIONAL_IMAGE},
            'accepted_capture06_audit_sha256': CAPTURE_SHA, 'qa_parking_audit_sha256': A.PARK_AUDIT_SHA,
            'current_capture_single_owner_stable': True, 'retained_configuration_preserved': True,
            'evidence': {str(p): sha256(raw).hexdigest() for p, raw in preserved.items()},
            'owned_fences': [{'path': str(p), 'identity': list(identities[p]), 'sha256': sha256(originals[p]).hexdigest()}
                             for p in (C.paths(plan)[1], C.P.RESERVATION)],
            'permanent_marker_removed': False, 'old_manifest_or_journal_modified': False,
            'persistent_boot_guard_verified': native['persistent_boot_guard_verified'], 'web_reboot_authorized': False,
            'cleanup_review_due_utc': (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()}
        if action == 'check':
            return {'status': 'aborted_c07_closure_checked', 'runtime_changed': False, 'owned_fences_released': False,
                    'security_transition_completed': False, 'known_market_incidents_status': 'open'}
        def final_check():
            fence_unchanged(C, C.P.MARKER, fds[C.P.MARKER], originals[C.P.MARKER], identities[C.P.MARKER])
            require(all(C.B.read(p, maximum=65536) == raw for p, raw in preserved.items()), 'closure_postrelease_evidence_changed')
        release_owned(C, [C.paths(plan)[1], C.P.RESERVATION], fds, originals, identities, path, receipt, final_check)
        return {'status': receipt['phase'], 'runtime_changed': False, 'owned_fences_released': True,
                'security_transition_completed': False, 'known_market_incidents_status': 'open'}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('capture', 'parking', 'qa', 'hot', 'parity'):
        parser.add_argument(name + '_source')
    parser.add_argument('action', choices=('check', 'close'), nargs='?', default='check')
    parser.add_argument('--confirmation', default='')
    args = parser.parse_args()
    require(args.action == 'check' or args.confirmation == CONFIRM, 'closure_explicit_confirmation_required')
    A, P, R, H, V = [reviewed(getattr(args, name + '_source'), name) for name in ('capture', 'parking', 'qa', 'hot', 'parity')]
    def expired(*_):
        raise RuntimeError('closure_own_clock_expired')
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(600)
    print(json.dumps(execute(P, R, H, A, V, args.action), sort_keys=True))
    return 0

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        code = str(error) if isinstance(error, RuntimeError) else ''
        if not re.fullmatch(r'[a-z0-9_]{1,96}', code):
            code = 'closure_refused'
        print(json.dumps({'status': 'closure_refused', 'reason_code': code, 'security_transition_completed': False}))
        raise SystemExit(1)
