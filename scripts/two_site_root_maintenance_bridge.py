"""Read-only Root maintenance bridge explicitly authorized by live Assignment bytes.

Caller holds Supervisor custody. No publisher, checkpoint/epoch edit, replay,
Worker/controller fiction, or permission to start a Supervisor is provided.
Old accepted-code artifacts are hash-custodied, not retrospectively reclassified:
the live Assignment marker authorizes the exact fresh Root bridge and handoff.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from os import close as _close
from pathlib import Path
import re
import stat

from scripts.two_site_codex_autopilot import (
    BRANCH, validate_repository, validate_accepted_repository,
    _validate_persisted_repository,
)
from scripts.two_site_assignment_scope import (
    MAX_CONTROL_FILE_BYTES, load_expected_assignment_scope,
    parse_assignment_scope_bytes, _resolve_shared_checkout,
    _find_assignment_markdown, _safe_read_file_bytes,
)
from scripts.two_site_cold_start_broker_quiescence import (
    hold_idle_broker_client_lock_for_cold_adoption,
)
from scripts.two_site_cold_start_adoption_storage import (
    _open_validated_state_root, _load_and_audit_accepted_checkpoint,
)
from scripts.two_site_maintenance_transition_storage import (
    _pin_validated_root, _check_stop_and_pending, _recheck_repository,
    _recheck_checkpoint,
)
from scripts.two_site_maintenance_transition_git import _run_git_query
from scripts.two_site_proposal_runner import bounded_file_bytes

STATE = Path('.local/two-site-refactor/autopilot')
DIRECTORY = 'root-maintenance-bridges'
MARKER = 'ROOT_MAINTENANCE_BRIDGE_V1:'
KIND = 'ROOT_MAINTENANCE_BRIDGE_V1'
POSTHOLD = 'ROOT_CURRENT_SOURCE_MAINTENANCE_HANDOFF_NORMAL_POSTHOLD_V1'
MARKER_V2 = 'ROOT_MAINTENANCE_BRIDGE_EXTERNAL_MAIN_V2:'
KIND_V2 = 'ROOT_MAINTENANCE_BRIDGE_EXTERNAL_MAIN_V2'
POSTHOLD_V2 = 'ROOT_CURRENT_SOURCE_MAINTENANCE_HANDOFF_NORMAL_POSTHOLD_V2'
MAX_RECORD_BYTES = 32768
MAX_EVIDENCE_BYTES = 200000
MAX_EVIDENCE_TOTAL = 4000000
MAX_COMMITS = 66
ERROR = 'root maintenance bridge verification failed'
RECORD_KEYS = frozenset({
    'schema_version', 'kind', 'prior_checkpoint_sha256', 'prior_head',
    'destination_assignment_id', 'repository', 'commits',
    'history_audit_sha256', 'handoff_assignment_id', 'handoff_scope_sha256',
})
POST_KEYS = frozenset({
    'schema_version', 'kind', 'record_sha256', 'prior_checkpoint_sha256',
    'head', 'destination_assignment_id', 'handoff_assignment_id',
    'handoff_scope_sha256', 'broker_posthold_completed', 'operation_error_type',
    'checkpoint_changed', 'reservations_changed', 'Supervisor_started',
})


class RootMaintenanceBridgeError(ValueError):
    """A marked bridge is invalid: never fallback, retry or infer acceptance."""


def _require(condition):
    if not condition:
        raise RootMaintenanceBridgeError(ERROR)


def _same(a, b):
    if type(a) is not type(b):
        return False
    if type(a) is dict:
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if type(a) in (list, tuple):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return a == b


def _hex(value, length):
    return type(value) is str and re.fullmatch('[0-9a-f]{' + str(length) + '}', value) is not None


def _canonical(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False,
                       separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')


def _pairs(items):
    value = {}
    for key, item in items:
        _require(key not in value)
        value[key] = item
    return value


def _json(raw, limit):
    _require(type(raw) is bytes and 0 < len(raw) <= limit)
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
                           parse_constant=_invalid_constant)
        pending, count = [(value, 0)], 0
        while pending:
            item, depth = pending.pop(); count += 1
            _require(depth <= 32 and count <= 8192)
            if type(item) is dict:
                pending.extend((x, depth + 1) for x in item.values())
            elif type(item) is list:
                pending.extend((x, depth + 1) for x in item)
        _require(type(value) is dict and _canonical(value) == raw)
        return value
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise RootMaintenanceBridgeError(ERROR) from None


def _invalid_constant(value):
    raise ValueError('nonfinite JSON')


def parse_scope_bridge_marker(assignment_bytes):
    """Pure syntax only; caller must authenticate these whole live Scope bytes."""
    _require(type(assignment_bytes) is bytes and 0 < len(assignment_bytes) <= MAX_CONTROL_FILE_BYTES)
    try:
        text = assignment_bytes.decode('utf-8')
    except UnicodeError:
        raise RootMaintenanceBridgeError(ERROR) from None
    family = 'ROOT_MAINTENANCE_BRIDGE_'
    if family not in text:
        return None
    _require(text.count(family) == 1)
    prefixes = re.findall(r'ROOT_MAINTENANCE_BRIDGE_[^\s:]*:', text)
    _require(len(prefixes) == 1 and prefixes[0] in (MARKER, MARKER_V2))
    prefix = prefixes[0]
    matches = re.findall(r'^' + re.escape(prefix) + r' (\{[^\r\n]*\})$', text, re.MULTILINE)
    _require(text.count(prefix) == len(matches) == 1)
    marker = _json((matches[0] + '\n').encode('utf-8'), 1024)
    _require(set(marker) == {'record_sha256', 'posthold_sha256', 'head', 'checkpoint_sha256'})
    _require(_hex(marker['record_sha256'], 64) and _hex(marker['head'], 40)
             and _hex(marker['posthold_sha256'], 64) and _hex(marker['checkpoint_sha256'], 64))
    if prefix == MARKER_V2:
        marker['schema_version'] = '2.0'  # Internal only; wire marker stays four keys.
    return marker


def parse_root_maintenance_bridge_bytes(raw, *, marker, prior_repository,
                                        current_repository, destination_assignment_id):
    """Pure closed record validation, not publication or proof of Root authority."""
    record = _json(raw, MAX_RECORD_BYTES)
    v2 = type(marker) is dict and marker.get('schema_version') == '2.0'
    _require(set(record) == (RECORD_KEYS | {'external_main_transition'} if v2 else RECORD_KEYS)
             and record['schema_version'] == ('2.0' if v2 else '1.0')
             and record['kind'] == (KIND_V2 if v2 else KIND)
             and _hex(record['history_audit_sha256'], 64))
    _require(hashlib.sha256(raw).hexdigest() == marker['record_sha256'])
    _require(record['prior_checkpoint_sha256'] == marker['checkpoint_sha256']
             and record['prior_head'] == prior_repository['head']
             and _hex(record['prior_head'], 40))
    _require(type(destination_assignment_id) is str and record['destination_assignment_id'] == destination_assignment_id)
    bare = {key: value for key, value in current_repository.items() if key != 'assignment_scope'}
    _require(type(record['repository']) is dict
             and set(record['repository']) == {'head', 'status', 'protected_refs', 'control_blobs'}
             and _same(record['repository'], bare) and bare['status'] == ''
             and bare['head'] == marker['head'] and bare['head'] != prior_repository['head'])
    if v2:
        _validate_external_main_transition(record, prior_repository, bare)
    else:
        _require(_same(prior_repository['protected_refs'], bare['protected_refs']))
    _require(set(prior_repository['control_blobs']) == set(bare['control_blobs']))
    _require(type(record['handoff_assignment_id']) is str
             and re.fullmatch('[A-Za-z0-9_-]{1,128}', record['handoff_assignment_id']) is not None
             and record['handoff_assignment_id'] != destination_assignment_id
             and _hex(record['handoff_scope_sha256'], 64))
    rows = record['commits']
    _require(type(rows) is list and 0 < len(rows) <= MAX_COMMITS)
    cursor, heads, paths = prior_repository['head'], set(), set()
    for row in rows:
        _require(type(row) is dict and set(row) == {'head', 'parent', 'evidence_path', 'evidence_sha256'})
        _require(_hex(row['head'], 40) and row['head'] not in heads and row['parent'] == cursor)
        path = row['evidence_path']
        _require(type(path) is str and re.fullmatch(
            r'\.local/two-site-refactor/reviews/[A-Za-z0-9_.-]{1,180}\.json', path) is not None)
        _require(path not in paths and _hex(row['evidence_sha256'], 64))
        paths.add(path); heads.add(row['head']); cursor = row['head']
    _require(cursor == bare['head'])
    return copy.deepcopy(record)


def _validate_external_main_transition(record, prior, current):
    transition = record['external_main_transition']
    _require(type(transition) is dict and set(transition) == {'old_main', 'new_main', 'commits'})
    old, new = transition['old_main'], transition['new_main']
    _require(_hex(old, 40) and _hex(new, 40) and old != new)
    before, after = prior['protected_refs'], current['protected_refs']
    refs = {'refs/heads/main', 'refs/remotes/origin/main', 'refs/heads/candidate/wa-ir-standby-v1'}
    _require(type(before) is dict and type(after) is dict and set(before) == set(after) == refs
             and all(_hex(value, 40) for value in (*before.values(), *after.values()))
             and before['refs/heads/main'] == old and after['refs/heads/main'] == new
             and all(before[ref] == after[ref] for ref in refs if ref != 'refs/heads/main'))
    rows = transition['commits']
    _require(type(rows) is list and 1 <= len(rows) <= 32)
    cursor, heads = old, {old}
    for row in rows:
        _require(type(row) is dict and set(row) == {'head', 'parent'}
                 and _hex(row['head'], 40) and row['head'] not in heads and row['parent'] == cursor)
        heads.add(row['head']); cursor = row['head']
    _require(cursor == new)


def _verify_external_main_git(root, record):
    """Read fixed Git facts only; FF is not approval, merge or Source acceptance."""
    try:
        transition = record['external_main_transition']
        old, new, head = transition['old_main'], transition['new_main'], record['repository']['head']
        _require(_hex(old, 40) and _hex(new, 40) and _hex(head, 40))
        expected = ''.join(row['head'] + ' ' + row['parent'] + '\n'
                           for row in transition['commits']).encode('ascii')
        queries = (
            (['cat-file', '-t', old], b'commit\n', 64),
            (['cat-file', '-t', new], b'commit\n', 64),
            (['rev-parse', '--verify', 'refs/heads/main'], (new + '\n').encode('ascii'), 64),
            (['rev-list', '--reverse', '--parents', old + '..' + new], expected, 4096),
            (['merge-base', '--is-ancestor', old, new], b'', 64),
            (['merge-base', '--all', head, new], (old + '\n').encode('ascii'), 64),
        )
        for args, expected, limit in queries:
            _require(_run_git_query(root, args, output_limit=limit) == expected)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise RootMaintenanceBridgeError(ERROR) from None


def _recheck_external_pending(root, state_root):
    """V2-only fresh descriptor check after Broker exit; Supervisor remains held."""
    root_fd = state_fd = None
    first_error = None
    try:
        root_fd = _pin_validated_root(root)
        state_fd = _open_validated_state_root(state_root)
        _check_stop_and_pending(root_fd, state_fd, state_root)
    except BaseException as exc:
        first_error = exc
    finally:
        for descriptor in (state_fd, root_fd):
            if descriptor is not None:
                try:
                    _close(descriptor)
                except BaseException as exc:
                    if first_error is None:
                        first_error = exc
    if first_error is not None:
        raise first_error


def _identity(path, *, directory=False, private=False):
    info = path.lstat()
    _require(info.st_uid == os.geteuid() and not stat.S_ISLNK(info.st_mode))
    if directory:
        _require(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700)
    else:
        _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1)
        if private:
            _require(stat.S_IMODE(info.st_mode) == 0o600)
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _scope_bytes(root, norm):
    current = norm['assignment_scope']
    checked = load_expected_assignment_scope(root, current['assignment_id'], current['sha256'])
    shared = _resolve_shared_checkout(str(root))
    path = Path(_find_assignment_markdown(shared, checked.assignment_id))
    mark = _identity(path)
    raw = _safe_read_file_bytes(str(path), max_bytes=MAX_CONTROL_FILE_BYTES)
    described = parse_assignment_scope_bytes(raw, expected_assignment_id=checked.assignment_id,
                                             integration_branch=BRANCH)
    _require(described == checked and _identity(path) == mark)
    _require(_same(validate_accepted_repository({**norm, 'assignment_scope': checked}), norm))
    return path, mark, raw


def _handoff(post, record, marker):
    v2 = type(marker) is dict and marker.get('schema_version') == '2.0'
    for key in ('checkpoint_changed', 'reservations_changed', 'Supervisor_started'):
        _require(post.get(key) is False)
    _require(set(post) == POST_KEYS and post['schema_version'] == ('2.0' if v2 else '1.0')
             and post['kind'] == (POSTHOLD_V2 if v2 else POSTHOLD)
             and post['record_sha256'] == marker['record_sha256']
             and post['prior_checkpoint_sha256'] == record['prior_checkpoint_sha256']
             and post['head'] == record['repository']['head']
             and post['broker_posthold_completed'] is True
             and post['operation_error_type'] is None)
    for key in ('destination_assignment_id', 'handoff_assignment_id', 'handoff_scope_sha256'):
        _require(post[key] == record[key])


def read_root_maintenance_bridge_locked(root, state_root, repository):
    """None ONLY for an authenticated live Scope without the explicit marker.

    Marked missing/invalid data always raises; no automatic legacy/quarantine
    fallback follows. One continuous fresh-IDLE Broker hold spans actual Scope,
    checkpoint, record, historical raw hashes, Git ancestry and current Source.
    Successful Broker exit precedes the detached return. No file is written.
    """
    try:
        _require(isinstance(root, Path) and root.is_absolute()
                 and isinstance(state_root, Path) and state_root == root / STATE)
        norm = validate_accepted_repository(repository)
        result, first_error, entered = None, None, False
        root_fd = state_fd = None
        scope_binding = checkpoint_binding = old_binding = None
        directory_binding = state_binding = None
        external_record = None
        custody = {}

        def preserve(action):
            nonlocal first_error
            try:
                action()
            except BaseException as exc:
                if first_error is None:
                    first_error = exc

        def read(path, limit, *, private=False):
            before = _identity(path, private=private)
            raw = bounded_file_bytes(root, path, limit, ERROR)
            _require(_identity(path, private=private) == before)
            custody[path] = (before, raw, limit, private)
            return raw

        def no_stop(path):
            try:
                path.lstat()
            except FileNotFoundError:
                return
            raise RootMaintenanceBridgeError(ERROR)

        def recheck_bound(*, in_hold):
            # Run each established binding independently even after a primary error.
            if external_record is not None:
                preserve(lambda: _verify_external_main_git(root, external_record))
                if not in_hold:
                    preserve(lambda: _recheck_external_pending(root, state_root))
            for path, (mark, body, limit, private) in custody.items():
                preserve(lambda path=path, mark=mark, body=body, limit=limit, private=private:
                    _require(_identity(path, private=private) == mark
                             and bounded_file_bytes(root, path, limit, ERROR) == body
                             and _identity(path, private=private) == mark))
            if scope_binding is not None:
                preserve(lambda: _require(_scope_bytes(root, norm) == scope_binding))
            for binding in (directory_binding, state_binding):
                if binding is not None:
                    path, mark = binding
                    preserve(lambda path=path, mark=mark:
                        _require(_identity(path, directory=True) == mark))
            if old_binding is not None:
                path, mark, body = old_binding
                preserve(lambda: _require(_identity(path) == mark
                    and _safe_read_file_bytes(str(path), max_bytes=MAX_CONTROL_FILE_BYTES) == body
                    and _identity(path) == mark))
            if checkpoint_binding is not None:
                digest, prior, mark = checkpoint_binding
                path = state_root / 'accepted_checkpoint.json'
                preserve(lambda: _require(_identity(path, private=True) == mark
                    and hashlib.sha256(bounded_file_bytes(root, path, 16384, ERROR)).hexdigest() == digest
                    and _identity(path, private=True) == mark))
                if in_hold and state_fd is not None:
                    preserve(lambda: _recheck_checkpoint(state_fd, state_root, digest, prior))
            if in_hold and root_fd is not None and state_fd is not None:
                preserve(lambda: _check_stop_and_pending(root_fd, state_fd, state_root))
            for path in (root / 'STOP', state_root / 'STOP'):
                preserve(lambda path=path: no_stop(path))
            preserve(lambda: _recheck_repository(root, norm))

        try:
            with hold_idle_broker_client_lock_for_cold_adoption(root):
                entered = True
                try:
                    root_fd = _pin_validated_root(root)
                    state_fd = _open_validated_state_root(state_root)
                    state_binding = (state_root, _identity(state_root, directory=True))
                    _check_stop_and_pending(root_fd, state_fd, state_root)
                    _recheck_repository(root, norm)
                    scope_binding = _scope_bytes(root, norm)
                    marker = parse_scope_bridge_marker(scope_binding[2])
                    if marker is not None:
                        checkpoint, checkpoint_sha = _load_and_audit_accepted_checkpoint(state_fd, state_root)
                        prior = _validate_persisted_repository(checkpoint['repository'])
                        checkpoint_binding = (checkpoint_sha, prior,
                            _identity(state_root / 'accepted_checkpoint.json', private=True))
                        _require(marker['checkpoint_sha256'] == checkpoint_sha and marker['head'] == norm['head'])
                        directory = state_root / DIRECTORY
                        directory_binding = (directory, _identity(directory, directory=True))
                        raw = read(directory / (marker['record_sha256'] + '.json'), MAX_RECORD_BYTES, private=True)
                        record = parse_root_maintenance_bridge_bytes(raw, marker=marker,
                            prior_repository=prior, current_repository=norm,
                            destination_assignment_id=norm['assignment_scope']['assignment_id'])
                        if marker.get('schema_version') == '2.0':
                            external_record = record
                            _verify_external_main_git(root, external_record)
                        post_raw = read(directory / (marker['posthold_sha256'] + '.json'), 8192, private=True)
                        _require(hashlib.sha256(post_raw).hexdigest() == marker['posthold_sha256'])
                        _handoff(_json(post_raw, 8192), record, marker)
                        shared = _resolve_shared_checkout(str(root))
                        old_path = Path(_find_assignment_markdown(shared, record['handoff_assignment_id']))
                        old_mark = _identity(old_path)
                        old_raw = _safe_read_file_bytes(str(old_path), max_bytes=MAX_CONTROL_FILE_BYTES)
                        old_binding = (old_path, old_mark, old_raw)
                        described = parse_assignment_scope_bytes(old_raw,
                            expected_assignment_id=record['handoff_assignment_id'], integration_branch=BRANCH)
                        _require(described.sha256 == record['handoff_scope_sha256'] and _identity(old_path) == old_mark)
                        audit_raw = read(directory / (record['history_audit_sha256'] + '.json'), MAX_EVIDENCE_BYTES, private=True)
                        _require(bool(audit_raw) and hashlib.sha256(audit_raw).hexdigest() == record['history_audit_sha256'])
                        total = len(audit_raw)
                        for row in record['commits']:
                            _require(total < MAX_EVIDENCE_TOTAL)
                            evidence = read(root / row['evidence_path'], min(MAX_EVIDENCE_BYTES, MAX_EVIDENCE_TOTAL - total))
                            total += len(evidence)
                            _require(bool(evidence) and hashlib.sha256(evidence).hexdigest() == row['evidence_sha256'])
                        actual = _run_git_query(root, ['rev-list', '--reverse', '--parents',
                            prior['head'] + '..' + norm['head']], output_limit=16384).decode('ascii').splitlines()
                        _require(actual == [row['head'] + ' ' + row['parent'] for row in record['commits']])
                        _run_git_query(root, ['merge-base', '--is-ancestor', prior['head'], norm['head']], output_limit=4096)
                        result = {**record, 'new_repository': copy.deepcopy(norm)}
                except BaseException as exc:
                    first_error = exc
                finally:
                    recheck_bound(in_hold=True)
                    for descriptor in (state_fd, root_fd):
                        if descriptor is not None:
                            preserve(lambda descriptor=descriptor: _close(descriptor))
                    state_fd = root_fd = None
                # No body/finalization exception crosses yield: actual Broker POST runs.
        except BaseException as exc:
            if first_error is None:
                first_error = exc
        if entered:
            recheck_bound(in_hold=False)
        if first_error is not None:
            raise first_error
        return copy.deepcopy(result)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise RootMaintenanceBridgeError(ERROR) from None


__all__ = ['RootMaintenanceBridgeError', 'parse_scope_bridge_marker',
           'parse_root_maintenance_bridge_bytes', 'read_root_maintenance_bridge_locked']
