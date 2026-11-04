"""CODEX_AUTHORED DRAFT: hermetic V2 tests, not live bridge authority.

Future destination: tests/test_two_site_root_maintenance_bridge_external_main_git.py.
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


from tests.test_two_site_root_maintenance_bridge_external_main import (
    MAIN, ORIGIN, ERROR, lab, _external_chain, _publish, _record, _pure,
    _assert_live_proof_witness,
)


def test_same_actual_witness_detects_used_proof_omission_then_restores(lab, monkeypatch):
    _assert_live_proof_witness(lab)
    actual = subject._verify_external_main_git
    try:
        monkeypatch.setattr(subject, '_verify_external_main_git', lambda root, record: None)
        with pytest.raises(pytest.fail.Exception, match='DID NOT RAISE'):
            _assert_live_proof_witness(lab)
    finally:
        monkeypatch.setattr(subject, '_verify_external_main_git', actual)
    _assert_live_proof_witness(lab)


@pytest.mark.parametrize('ref', ['refs/heads/candidate/wa-ir-standby-v1'])
def test_unrelated_protected_refs_still_block(lab, ref):
    helpers.git(lab['root'], 'update-ref', ref, lab['external']['new_main'])
    record = copy.deepcopy(lab['v2_record'])
    record['repository']['protected_refs'][ref] = lab['external']['new_main']
    _publish(lab, record)
    helpers.reject_read(lab)


@pytest.mark.parametrize('fault', ['missing_object', 'non_fast_forward'])
def test_structural_rows_do_not_substitute_for_real_git_objects_or_ff(lab, fault):
    old = lab['external']['old_main']
    record = copy.deepcopy(lab['v2_record'])
    if fault == 'missing_object':
        record['external_main_transition']['commits'][0]['head'] = '0' * 40
        record['external_main_transition']['commits'][1]['parent'] = '0' * 40
    else:
        # An actual unrelated root commit can be a branch tip, but not an old->new FF.
        tree = helpers.git(lab['root'], 'rev-parse', old + '^{tree}')
        new = helpers.git(lab['root'], 'commit-tree', tree, '-m', 'LAB unrelated external root')
        helpers.git(lab['root'], 'update-ref', MAIN, new)
        record['repository']['protected_refs'][MAIN] = new
        record['external_main_transition'] = {
            'old_main': old, 'new_main': new, 'commits': [{'head': new, 'parent': old}]}
    raw, _ = _publish(lab, record)
    assert _pure(lab, raw) == record
    helpers.reject_read(lab)


@pytest.mark.parametrize('kind', ['missing', 'tree', 'tag'])
def test_actual_noncommit_objects_cannot_serve_as_external_endpoints(lab, monkeypatch, kind):
    root = lab['root']
    record = copy.deepcopy(lab['v2_record'])
    old = record['external_main_transition']['old_main']
    if kind == 'missing':
        target = '0' * 40
    elif kind == 'tree':
        target = helpers.git(root, 'rev-parse', old + '^{tree}')
    else:
        helpers.git(root, 'tag', '-a', 'lab-external-object', '-m', 'LAB annotated tag', old)
        target = helpers.git(root, 'rev-parse', 'refs/tags/lab-external-object')
    record['external_main_transition']['new_main'] = target
    # Git itself forbids tree/tag refs/heads tips; test actual object checks directly.
    actual = subject._run_git_query
    calls = []
    def observed(root_arg, arguments, **kwargs):
        calls.append(list(arguments))
        return actual(root_arg, arguments, **kwargs)
    monkeypatch.setattr(subject, '_run_git_query', observed)
    with pytest.raises(subject.RootMaintenanceBridgeError, match=ERROR):
        subject._verify_external_main_git(root, record)
    assert calls == [['cat-file', '-t', old], ['cat-file', '-t', target]]
    assert helpers.git(root, 'rev-parse', MAIN) == lab['external']['new_main']
    assert helpers.git(root, 'rev-parse', 'HEAD') == lab['record']['repository']['head']


def test_actual_main_overlap_reaches_and_fails_only_common_base_gate(lab, monkeypatch):
    root = lab['root']
    old, healthy = lab['external']['old_main'], lab['external']['new_main']
    integration = lab['record']['repository']['head']
    assert helpers.git(root, 'rev-parse', integration + '^') == old
    tree = helpers.git(root, 'rev-parse', integration + '^{tree}')
    new = helpers.git(root, 'commit-tree', tree, '-p', integration,
                      '-m', 'LAB main overlap with integration')
    helpers.git(root, 'update-ref', MAIN, new, healthy)
    assert helpers.git(root, 'rev-parse', 'HEAD') == integration
    record = _record(lab, {
        'old_main': old, 'new_main': new,
        'commits': [{'head': integration, 'parent': old}, {'head': new, 'parent': integration}],
    })
    raw, _ = _publish(lab, record)
    assert _pure(lab, raw) == record
    # Three real heads imply two complete rows: the old range base is excluded.
    rows = (integration + ' ' + old + '\n' + new + ' ' + integration + '\n').encode()
    expected = [
        (['cat-file', '-t', old], b'commit\n'),
        (['cat-file', '-t', new], b'commit\n'),
        (['rev-parse', '--verify', MAIN], (new + '\n').encode()),
        (['rev-parse', '--verify', ORIGIN],
         (record['repository']['protected_refs'][ORIGIN] + '\n').encode()),
        (['rev-list', '--reverse', '--parents', old + '..' + new], rows),
        (['merge-base', '--is-ancestor', old, new], b''),
        (['merge-base', '--all', integration, new], (integration + '\n').encode()),
    ]
    actual = subject._run_git_query
    seen = []
    def observed(root_arg, arguments, **kwargs):
        result = actual(root_arg, arguments, **kwargs)
        seen.append((list(arguments), result))
        return result
    before = helpers.custody(lab)
    try:
        monkeypatch.setattr(subject, '_run_git_query', observed)
        with pytest.raises(subject.RootMaintenanceBridgeError, match=ERROR):
            helpers.run(lab)
        assert seen[:7] == expected
        assert helpers.custody(lab) == before
    finally:
        monkeypatch.setattr(subject, '_run_git_query', actual)
        helpers.git(root, 'update-ref', MAIN, healthy, new)
        _publish(lab, lab['v2_record'])
    assert helpers.run(lab) == {**copy.deepcopy(lab['v2_record']),
        'new_repository': ap.validate_accepted_repository(lab['repository'])}


@pytest.mark.parametrize('count', [32, 33])
def test_complete_actual_external_chain_boundary_32_not_33(lab, count):
    transition = _external_chain(lab, count)
    assert len(transition['commits']) == count
    record = _record(lab, transition)
    _publish(lab, record)
    if count == 32:
        before = helpers.custody(lab)
        assert helpers.run(lab) == {**record,
            'new_repository': ap.validate_accepted_repository(lab['repository'])}
        assert helpers.custody(lab) == before
    else:
        helpers.reject_read(lab)


@pytest.mark.parametrize('fault', ['timeout', 'overflow'])
def test_v2_fixed_git_executor_failure_is_bounded_no_shell_and_rejects(lab, monkeypatch, fault):
    actual_query = subject._run_git_query
    actual_process = gitproof._run_bounded_process
    seen = []
    def bounded(argv, **kwargs):
        assert argv[:2] == ['/usr/bin/git', '--no-replace-objects']
        assert argv[2:4] == ['-C', str(lab['root'])]
        assert kwargs['cwd'] == lab['root'] and 'shell' not in kwargs
        assert 0 < kwargs['timeout'] <= 10 and 0 < kwargs['output_limit'] <= 65536
        assert kwargs['env']['GIT_NO_LAZY_FETCH'] == '1'
        assert kwargs['env']['GIT_CONFIG_NOSYSTEM'] == '1'
        assert kwargs['env']['GIT_OPTIONAL_LOCKS'] == '0'
        assert kwargs['env']['GIT_TERMINAL_PROMPT'] == '0'
        seen.append(argv[4:])
        if fault == 'timeout':
            # Rejection-only injection at the actual bounded executor boundary.
            raise gitproof.SandboxError('LAB bounded process timed out')
        return actual_process(argv, **kwargs)
    def small(root, arguments, **kwargs):
        if arguments[:2] == ['cat-file', '-t']:
            kwargs['output_limit'] = 1  # Actual Git emits b'commit\n': real overflow.
        return actual_query(root, arguments, **kwargs)
    monkeypatch.setattr(gitproof, '_run_bounded_process', bounded)
    monkeypatch.setattr(subject, '_run_git_query', small)
    helpers.reject_read(lab)
    assert seen and seen[0] == ['cat-file', '-t', lab['external']['old_main']]
    monkeypatch.setattr(gitproof, '_run_bounded_process', actual_process)
    monkeypatch.setattr(subject, '_run_git_query', actual_query)
    assert helpers.run(lab)['external_main_transition'] == lab['external']
