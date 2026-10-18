"""CODEX_AUTHORED DRAFT: hermetic V2 tests, not live bridge authority.

Future destination: tests/test_two_site_root_maintenance_bridge_external_main.py.
Reuse the complete unchanged LAB fixture; preserve all existing V1 tests.
Draft preparation is compile/static only, not a native or fixture PASS claim.
"""
from __future__ import annotations

import os
os.environ['APP_ENV_FILE'] = 'config/unit-test.env.example'
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

from contextlib import contextmanager
import copy

import pytest
from scripts import two_site_root_maintenance_bridge as subject
from scripts import two_site_maintenance_transition_startup as startup
from scripts import two_site_codex_autopilot as ap
from scripts import two_site_quarantine_recovery_storage as quarantine
from scripts import two_site_maintenance_transition_git as gitproof
from tests import two_site_root_maintenance_bridge_fixtures as helpers

V2_MARKER = b'ROOT_MAINTENANCE_BRIDGE_EXTERNAL_MAIN_V2: '
V2_KIND = 'ROOT_MAINTENANCE_BRIDGE_EXTERNAL_MAIN_V2'
V2_POST = 'ROOT_CURRENT_SOURCE_MAINTENANCE_HANDOFF_NORMAL_POSTHOLD_V2'
MAIN = 'refs/heads/main'
ERROR = '^root maintenance bridge verification failed$'


def _external_chain(lab, count):
    """Actual LAB Git objects, not a fabricated ancestry oracle or checkout."""
    root = lab['root']
    integration = helpers.git(root, 'rev-parse', 'HEAD')
    index = helpers.git(root, 'ls-files', '--stage')
    old = lab['prior']['protected_refs'][MAIN]
    tree = helpers.git(root, 'rev-parse', old + '^{tree}')
    parent, rows = old, []
    for number in range(count):
        head = helpers.git(root, 'commit-tree', tree, '-p', parent,
                           '-m', 'LAB external main commit ' + str(number))
        rows.append({'head': head, 'parent': parent})
        parent = head
    previous = helpers.git(root, 'rev-parse', MAIN)
    helpers.git(root, 'update-ref', MAIN, parent, previous)
    assert helpers.git(root, 'rev-parse', 'HEAD') == integration
    assert helpers.git(root, 'ls-files', '--stage') == index
    assert helpers.git(root, 'status', '--porcelain=v1', '--untracked-files=all') == ''
    for ref, head in lab['prior']['protected_refs'].items():
        if ref != MAIN:
            assert helpers.git(root, 'rev-parse', ref) == head
    return {'old_main': old, 'new_main': parent, 'commits': rows}


def _publish(lab, record, *, post_edit=None, marker_prefix=V2_MARKER):
    """Owned LAB artifacts only; no production publisher exists in these tests."""
    record = copy.deepcopy(record)
    raw = helpers.canonical(record)
    post = copy.deepcopy(lab['post'])
    if marker_prefix == V2_MARKER:
        post.update(schema_version='2.0', kind=V2_POST)
    post['record_sha256'] = helpers.sha(raw)
    if post_edit is not None:
        post_edit(post)
    post_raw = helpers.canonical(post)
    marker = dict(record_sha256=helpers.sha(raw), posthold_sha256=helpers.sha(post_raw),
                  head=lab['record']['repository']['head'],
                  checkpoint_sha256=lab['record']['prior_checkpoint_sha256'])
    helpers.private(lab['directory'] / (helpers.sha(raw) + '.json'), raw)
    helpers.private(lab['directory'] / (helpers.sha(post_raw) + '.json'), post_raw)
    lab['active_path'].write_bytes(helpers.document(helpers.DESTINATION)
                                   + b'\n' + marker_prefix + helpers.canonical(marker))
    lab['repository'] = lab['repository_oracle'](lab['root'])
    lab['marker'] = marker
    return raw, marker


def _record(lab, transition):
    record = copy.deepcopy(lab['record'])
    record.update(schema_version='2.0', kind=V2_KIND,
                  external_main_transition=copy.deepcopy(transition))
    record['repository']['protected_refs'][MAIN] = transition['new_main']
    return record


@pytest.fixture
def lab(tmp_path, monkeypatch):
    value = helpers.build_lab(tmp_path, monkeypatch)
    value['external'] = _external_chain(value, 2)
    value['v2_record'] = _record(value, value['external'])
    _publish(value, value['v2_record'])
    return value


def _pure(lab, raw):
    marker = subject.parse_scope_bridge_marker(lab['active_path'].read_bytes())
    return subject.parse_root_maintenance_bridge_bytes(raw, marker=marker,
        prior_repository=lab['prior'],
        current_repository=ap.validate_accepted_repository(lab['repository']),
        destination_assignment_id=helpers.DESTINATION)


def _assert_live_proof_witness(lab):
    """Same unconditional scenario for healthy, omitted-proof and restored code."""
    good = copy.deepcopy(lab['v2_record'])
    _publish(lab, good)
    before = helpers.custody(lab)
    expected = {**copy.deepcopy(good),
                'new_repository': ap.validate_accepted_repository(lab['repository'])}
    assert helpers.run(lab) == expected
    assert helpers.custody(lab) == before
    # Real integration commit is deliberately falsely claimed as an external one.
    # Both endpoints and all schema bindings remain valid; actual Git must refute it.
    false = copy.deepcopy(good)
    integration = lab['record']['repository']['head']
    false['external_main_transition']['commits'] = [
        {'head': integration, 'parent': good['external_main_transition']['old_main']},
        {'head': good['external_main_transition']['new_main'], 'parent': integration},
    ]
    try:
        raw, _ = _publish(lab, false)
        assert _pure(lab, raw) == false  # Syntax is explicitly not Git authority.
        before = helpers.custody(lab)
        with pytest.raises(subject.RootMaintenanceBridgeError, match=ERROR):
            helpers.run(lab)
        assert helpers.custody(lab) == before
    finally:
        _publish(lab, good)
    assert helpers.run(lab) == {
        **copy.deepcopy(good),
        'new_repository': ap.validate_accepted_repository(lab['repository'])}


def test_real_v2_main_ff_detached_result_and_no_read_side_effects(lab):
    before = helpers.custody(lab)
    expected = {**copy.deepcopy(lab['v2_record']),
                'new_repository': ap.validate_accepted_repository(lab['repository'])}
    result = helpers.run(lab)
    assert result == expected
    assert result['schema_version'] == '2.0' and result['kind'] == V2_KIND
    assert set(result) == set(lab['v2_record']) | {'new_repository'}
    assert result['external_main_transition'] == lab['external']
    result['external_main_transition']['commits'].clear()
    result['new_repository']['control_blobs'].clear()
    assert helpers.run(lab) == expected and helpers.custody(lab) == before


def test_full_v1_reader_still_rejects_real_external_main_ref_drift(lab):
    v1 = copy.deepcopy(lab['record'])
    # Match actual current bare repository so this reaches V1's old-ref gate.
    v1['repository']['protected_refs'][MAIN] = lab['external']['new_main']
    _publish(lab, v1, marker_prefix=b'ROOT_MAINTENANCE_BRIDGE_V1: ')
    helpers.reject_read(lab)


def test_v2_marker_public_shape_and_pure_parser_do_not_run_git(lab, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('pure V2 parsing cannot establish Git authority')
    monkeypatch.setattr(subject, '_run_git_query', forbidden)
    expected = {**lab['marker'], 'schema_version': '2.0'}
    marker = subject.parse_scope_bridge_marker(lab['active_path'].read_bytes())
    assert marker == expected and set(marker) == set(lab['marker']) | {'schema_version'}
    marker['head'] = '0' * 40
    assert subject.parse_scope_bridge_marker(lab['active_path'].read_bytes()) == expected
    raw = helpers.canonical(lab['v2_record'])
    assert _pure(lab, raw) == lab['v2_record']


@pytest.mark.parametrize('fault', [
    'missing_transition', 'unknown_record', 'downgraded_record', 'wrong_kind',
    'missing_old', 'missing_new', 'missing_commits', 'unknown_transition',
    'wrong_old', 'wrong_new', 'same_endpoints', 'empty_rows', 'none_rows',
    'missing_head', 'missing_parent', 'unknown_row', 'wrong_parent',
    'wrong_tail', 'duplicate_head', 'missing_ref', 'extra_ref',
])
def test_closed_v2_record_fields_bind_exact_checkpoint_current_refs_and_rows(lab, fault):
    record = copy.deepcopy(lab['v2_record'])
    external = record['external_main_transition']
    if fault == 'missing_transition':
        del record['external_main_transition']
    elif fault == 'unknown_record':
        record['unknown'] = True
    elif fault == 'downgraded_record':
        record['schema_version'] = '1.0'
    elif fault == 'wrong_kind':
        record['kind'] = 'ROOT_MAINTENANCE_BRIDGE_V1'
    elif fault.startswith('missing_') and fault.split('_', 1)[1] in ('old', 'new', 'commits'):
        del external[{'old': 'old_main', 'new': 'new_main', 'commits': 'commits'}[fault.split('_', 1)[1]]]
    elif fault == 'unknown_transition':
        external['approved'] = True
    elif fault in ('wrong_old', 'wrong_new'):
        external['old_main' if fault == 'wrong_old' else 'new_main'] = '0' * 40
    elif fault == 'same_endpoints':
        external['new_main'] = external['old_main']
    elif fault in ('empty_rows', 'none_rows'):
        external['commits'] = [] if fault == 'empty_rows' else None
    elif fault in ('missing_head', 'missing_parent'):
        del external['commits'][0][fault.split('_', 1)[1]]
    elif fault == 'unknown_row':
        external['commits'][0]['approved'] = True
    elif fault == 'wrong_parent':
        external['commits'][0]['parent'] = '0' * 40
    elif fault == 'wrong_tail':
        external['commits'][-1]['head'] = '0' * 40
    elif fault == 'duplicate_head':
        external['commits'][1]['head'] = external['commits'][0]['head']
    elif fault == 'missing_ref':
        del record['repository']['protected_refs'][MAIN]
    elif fault == 'extra_ref':
        record['repository']['protected_refs']['refs/heads/unscoped'] = external['old_main']
    else:
        raise AssertionError('unhandled test case')
    _publish(lab, record)
    helpers.reject_read(lab)


@pytest.mark.parametrize('fault', ['schema', 'kind', 'extra', 'posthold', 'error',
                                  'record_hash', 'checkpoint', 'reservations', 'started'])
def test_v2_requires_separately_pinned_exact_normal_posthold_record(lab, fault):
    edits = {
        'schema': {'schema_version': '1.0'},
        'kind': {'kind': 'ROOT_CURRENT_SOURCE_MAINTENANCE_HANDOFF_NORMAL_POSTHOLD_V1'},
        'extra': {'approved': True}, 'posthold': {'broker_posthold_completed': False},
        'error': {'operation_error_type': 'OSError'}, 'record_hash': {'record_sha256': '0' * 64},
        'checkpoint': {'checkpoint_changed': True}, 'reservations': {'reservations_changed': True},
        'started': {'Supervisor_started': True},
    }
    _publish(lab, lab['v2_record'], post_edit=lambda post: post.update(edits[fault]))
    helpers.reject_read(lab)


@pytest.mark.parametrize('fault', ['duplicate_v2', 'mixed_v1', 'unknown_v3',
                                  'unknown_old_family', 'serialized_discriminator'])
def test_mixed_duplicate_unknown_and_downgraded_marker_families_fail_closed(lab, fault):
    line = V2_MARKER + helpers.canonical(lab['marker'])
    variants = {
        'duplicate_v2': line + line,
        'mixed_v1': line + b'ROOT_MAINTENANCE_BRIDGE_V1: ' + helpers.canonical(lab['marker']),
        'unknown_v3': b'ROOT_MAINTENANCE_BRIDGE_EXTERNAL_MAIN_V3: ' + helpers.canonical(lab['marker']),
        'unknown_old_family': b'ROOT_MAINTENANCE_BRIDGE_V2: ' + helpers.canonical(lab['marker']),
        'serialized_discriminator': V2_MARKER + helpers.canonical({**lab['marker'], 'schema_version': '2.0'}),
    }
    raw = helpers.document(helpers.DESTINATION) + b'\n' + variants[fault]
    with pytest.raises(subject.RootMaintenanceBridgeError, match=ERROR):
        subject.parse_scope_bridge_marker(raw)
    lab['active_path'].write_bytes(raw)
    lab['repository'] = lab['repository_oracle'](lab['root'])
    helpers.reject_read(lab)


@pytest.mark.parametrize('fault', ['checkpoint', 'root_stop', 'state_stop', 'pending'])
def test_v2_initial_checkpoint_stop_and_pending_custody_still_blocks(lab, fault):
    if fault == 'checkpoint':
        (lab['state'] / 'accepted_checkpoint.json').write_bytes(b'{}')
    elif fault in ('root_stop', 'state_stop'):
        (lab['root'] if fault == 'root_stop' else lab['state']).joinpath('STOP').write_bytes(b'')
    else:
        helpers.private(lab['state'] / 'runs/cycle-0001-20261017T000000Z-1/quota-resume-claim.json', b'{}')
    helpers.reject_read(lab)


@pytest.mark.parametrize('fault', ['main', 'source', 'checkpoint', 'stop', 'pending', 'broker_exit'])
def test_v2_late_boundary_drift_and_failed_actual_broker_exit_never_return(lab, monkeypatch, fault):
    actual = subject.hold_idle_broker_client_lock_for_cold_adoption
    events = []
    if fault == 'main':
        tree = helpers.git(lab['root'], 'rev-parse', lab['external']['old_main'] + '^{tree}')
        late = helpers.git(lab['root'], 'commit-tree', tree, '-p', lab['external']['new_main'],
                           '-m', 'LAB late external main commit')
    @contextmanager
    def disrupted(root):
        with actual(root):
            yield
            events.append('body-returned')
        events.append('actual-posthold')
        if fault == 'main':
            helpers.git(root, 'update-ref', MAIN, late)
        elif fault == 'source':
            (root / ap.CONTROL_PATHS[0]).write_bytes(b'LAB late Source drift')
        elif fault == 'checkpoint':
            (lab['state'] / 'accepted_checkpoint.json').write_bytes(b'{}')
        elif fault == 'stop':
            (root / 'STOP').write_bytes(b'')
        elif fault == 'pending':
            helpers.private(lab['state'] / 'runs/cycle-0001-20261017T000000Z-1/quota-resume-claim.json', b'{}')
        else:
            raise OSError('LAB failure after actual Broker exit')
    monkeypatch.setattr(subject, 'hold_idle_broker_client_lock_for_cold_adoption', disrupted)
    with pytest.raises(subject.RootMaintenanceBridgeError, match=ERROR):
        helpers.run(lab)
    assert events == ['body-returned', 'actual-posthold']
    if fault == 'pending':
        (lab['state'] / 'runs/cycle-0001-20261017T000000Z-1/quota-resume-claim.json').unlink()
        monkeypatch.setattr(subject, 'hold_idle_broker_client_lock_for_cold_adoption', actual)
        assert helpers.run(lab)['external_main_transition'] == lab['external']


@pytest.mark.parametrize('error', [KeyboardInterrupt('LAB first'), SystemExit('LAB first')])
def test_v2_first_error_survives_rechecks_closes_and_actual_normal_broker_posthold(lab, monkeypatch, error):
    actual_proof = subject._verify_external_main_git
    actual_close = subject._close
    actual_broker = subject.hold_idle_broker_client_lock_for_cold_adoption
    events = []
    before = helpers.custody(lab)
    def interrupted(root, record):
        raise error
    def close(descriptor):
        actual_close(descriptor)
        events.append('closed')
        raise OSError('LAB later close error')
    @contextmanager
    def observed(root):
        with actual_broker(root):
            yield
            events.append('body-returned')
        events.append('actual-posthold')
        raise OSError('LAB later wrapper exit error')
    monkeypatch.setattr(subject, '_verify_external_main_git', interrupted)
    monkeypatch.setattr(subject, '_close', close)
    monkeypatch.setattr(subject, 'hold_idle_broker_client_lock_for_cold_adoption', observed)
    try:
        with pytest.raises(type(error)) as caught:
            helpers.run(lab)
        assert caught.value is error
        assert events == ['closed', 'closed', 'body-returned', 'actual-posthold', 'closed', 'closed']
        assert helpers.custody(lab) == before
    finally:
        monkeypatch.setattr(subject, '_verify_external_main_git', actual_proof)
        monkeypatch.setattr(subject, '_close', actual_close)
        monkeypatch.setattr(subject, 'hold_idle_broker_client_lock_for_cold_adoption', actual_broker)
    assert helpers.run(lab)['external_main_transition'] == lab['external']


@pytest.mark.parametrize('fault', ['posthold', 'missing_colon', 'spaced_colon', 'unknown_no_colon'])
def test_v2_real_startup_autopilot_monitor_routes_never_legacy_fallback(lab, monkeypatch, fault):
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(args)
        raise AssertionError('marked V2 success/failure cannot use legacy provenance')
    monkeypatch.setattr(startup, 'read_maintenance_transition_locked', forbidden)
    monkeypatch.setattr(quarantine, 'read_quarantine_recovery_locked', forbidden)
    with ap.exclusive_supervisor_lock(lab['state'] / 'autopilot.lock'):
        startup.require_reviewed_maintenance_transition_locked(lab['root'], lab['state'], lab['repository'])
        for token in ('1', '2'):
            (lab['state'] / ('rescope-' + token * 64)).mkdir(mode=0o700)
        ap.validate_rescope_epoch_startup(lab['state'], lab['repository'], root=lab['root'])
    from scripts import two_site_autopilot_monitor_preflight as monitor
    monkeypatch.setattr(monitor, 'validate_repository', lab['repository_oracle'])
    helpers.private(lab['state'] / 'status.json',
        ap.serialize_json(dict(head=lab['record']['repository']['head'], state='CONTINUE')))
    assert monitor.run_preflight(lab['root']) == (True, 'READY')
    if fault == 'posthold':
        _publish(lab, lab['v2_record'], post_edit=lambda post: post.update(broker_posthold_completed=False))
    else:
        prefix = {
            'missing_colon': b'ROOT_MAINTENANCE_BRIDGE_EXTERNAL_MAIN_V2 ',
            'spaced_colon': b'ROOT_MAINTENANCE_BRIDGE_EXTERNAL_MAIN_V2 : ',
            'unknown_no_colon': b'ROOT_MAINTENANCE_BRIDGE_EXTERNAL_MAIN_V3 ',
        }[fault]
        raw = helpers.document(helpers.DESTINATION) + b'\n' + prefix + helpers.canonical(lab['marker'])
        lab['active_path'].write_bytes(raw)
        lab['repository'] = lab['repository_oracle'](lab['root'])
    with ap.exclusive_supervisor_lock(lab['state'] / 'autopilot.lock'):
        with pytest.raises(startup.MaintenanceTransitionStartupError):
            startup.require_reviewed_maintenance_transition_locked(lab['root'], lab['state'], lab['repository'])
        with pytest.raises(ap.AutopilotError):
            ap.validate_rescope_epoch_startup(lab['state'], lab['repository'], root=lab['root'])
    assert monitor.run_preflight(lab['root']) == (False, 'STALE_CHECKPOINT')
    assert calls == []
    if fault != 'posthold':
        with pytest.raises(subject.RootMaintenanceBridgeError, match=ERROR):
            subject.parse_scope_bridge_marker(raw)
