#!/usr/bin/env python3
"""Root-only fail-closed reconciliation for interrupted typed operations.

This command never invents an approval or replays a mutation. It can release the
transport only when the original operation is proven safe to retry without carrying
forward authority. Ambiguous or partially-mutated Git/fence state remains blocked.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

import yaml

from scripts.two_site_agent_executor import observe_git
from scripts.two_site_agent_host import MANIFEST_PATH, load_manifest
from scripts.two_site_agent_transport import (
    MAX_REQUEST, MAX_RESULT, UUID, TransportError, digest, read_bounded, safe_path,
    unique_json, write_new,
)

SAFE_RETRY_OPERATIONS = {
    'inspect', 'status', 'diff', 'worker-check', 'test', 'verifier-submit',
    'bundle-prepare', 'review-request', 'closeout-review', 'receipt-check',
}


@contextmanager
def recovery_lock(state: Path):
    path = safe_path(state / 'transport.lock', exists=False)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise TransportError('original protected operation is still running') from error
        yield
    finally:
        os.close(descriptor)


def _sha(path: Path, limit: int) -> str:
    return hashlib.sha256(read_bounded(path, limit)).hexdigest()


def _request(root: Path, state: Path, request_id: str) -> tuple[dict, Path, Path | None]:
    if not UUID.fullmatch(request_id):
        raise TransportError('recovery request ID is not a canonical UUIDv4')
    running = safe_path(state / (request_id + '.running.json'))
    identity = unique_json(read_bounded(running, MAX_REQUEST))
    request_path = safe_path(root / '.local/two-site-refactor/gateway/requests' / (request_id + '.json'))
    request = unique_json(read_bounded(request_path, MAX_REQUEST))
    if (not isinstance(identity, dict) or not isinstance(request, dict)
            or identity.get('request_id') != request_id or request.get('id') != request_id
            or identity.get('request_sha256') != digest(request)
            or identity.get('actor') != request.get('actor_id')):
        raise TransportError('running/request identity differs during recovery')
    receipt = state / (request_id + '.receipt.json')
    if receipt.exists():
        saved = unique_json(read_bounded(receipt, MAX_RESULT))
        if (not isinstance(saved, dict) or saved.get('request_id') != request_id
                or saved.get('authority') != 'PROTECTED_CONTROLLER_RECEIPT'
                or saved.get('outcome', {}).get('status') != 'INDETERMINATE'):
            raise TransportError('only absent or INDETERMINATE receipts need recovery')
        return request, running, safe_path(receipt)
    return request, running, None


def _stage_advance_retry_safe(root: Path, request: dict) -> bool:
    local = root / '.local/two-site-refactor'
    registry = yaml.safe_load(read_bounded(local / 'worktrees.yaml', 1_000_000))
    matches = [item for item in registry.get('stage_worktrees', [])
               if isinstance(item, dict) and item.get('assignment_id') == request.get('assignment')]
    if len(matches) != 1:
        return False
    stage = matches[0]
    worktree = safe_path(Path(str(stage.get('path', ''))))
    snapshot = observe_git(worktree)
    return (snapshot.clean and snapshot.head_sha == request.get('stage_head')
            and stage.get('head_sha') in {request.get('stage_head'), snapshot.parent_sha}
            and stage.get('status') in {'RUNNING', 'READY', 'COMPLETE_CANDIDATE'})


def reconcile(request_id: str, *, apply: bool, manifest_path: Path = MANIFEST_PATH) -> dict:
    if os.geteuid() != 0:
        raise TransportError('protected recovery requires root ownership')
    manifest = load_manifest(safe_path(manifest_path))
    root = safe_path(Path(manifest['root']))
    state = safe_path(manifest_path.parent / 'controller')
    with recovery_lock(state):
        request, running, receipt = _request(root, state, request_id)
        operation = request.get('operation')
        integration = observe_git(root)
        retry_safe = operation in SAFE_RETRY_OPERATIONS and integration.head_sha == request.get('integration_head')
        if operation == 'stage-advance':
            retry_safe = integration.head_sha == request.get('integration_head') and _stage_advance_retry_safe(root, request)
        if not retry_safe:
            return {'status': 'BLOCKED', 'request_id': request_id, 'operation': operation,
                    'reason': 'canonical mutation state is ambiguous; no authority or rollback was invented'}
        record = {
            'schema_version': '1.0', 'request_id': request_id, 'operation': operation,
            'decision': 'RETRY_SAFE_NO_AUTHORITY', 'running_sha256': _sha(running, MAX_REQUEST),
            'receipt_sha256': _sha(receipt, MAX_RESULT) if receipt is not None else None,
            'reconciled_at': time.time(), 'authority': 'PROTECTED_ROOT_RECOVERY',
        }
        if apply:
            write_new(state / (request_id + '.reconciliation.json'), record, MAX_REQUEST)
        return {'status': 'RECONCILED' if apply else 'RETRY_SAFE', **record}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument('mode', choices=('audit', 'resolve-safe'))
    result.add_argument('--request-id', required=True)
    result.add_argument('--manifest', type=Path, default=MANIFEST_PATH)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        result = reconcile(args.request_id, apply=args.mode == 'resolve-safe',
                           manifest_path=args.manifest)
    except Exception as error:
        result = {'status': 'BLOCKED', 'error_class': type(error).__name__}
    print(json.dumps(result, sort_keys=True, separators=(',', ':')))
    return 0 if result.get('status') in {'RETRY_SAFE', 'RECONCILED'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
