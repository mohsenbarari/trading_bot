#!/usr/bin/env python3
"""Native-hook transport for typed operations, without Agent shell permissions.

Only a protected controller may instantiate this transport. Its policy and executor
are trusted installed code, not values supplied by an Agent or a request file.
Public result files are caches; protected receipts remain the authoritative copy.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import time
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Callable

UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z')
MODEL = 'gemini-3.8-flash-high'
MAX_REQUEST = 65536
MAX_EVENT = 2_000_000
MAX_RESULT = 8_000_000
MAX_STATE_FILES = 4096
MAX_STATE_BYTES = 256_000_000
MAX_ARCHIVE_ENTRIES = 2048


class TransportError(ValueError):
    pass


def unique_json(raw: str | bytes):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise TransportError('duplicate JSON key')
            value[key] = item
        return value

    def constant(_):
        raise TransportError('non-finite JSON number')

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise TransportError('invalid bounded JSON') from error


def encoded(value) -> bytes:
    if is_dataclass(value):
        value = asdict(value)
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=False, allow_nan=False).encode()
    except (ValueError, TypeError, RecursionError) as error:
        raise TransportError('noncanonical JSON value') from error


def digest(value) -> str:
    return hashlib.sha256(encoded(value)).hexdigest()


def safe_path(path: Path, *, exists: bool = True) -> Path:
    if not path.is_absolute() or path != Path(os.path.normpath(str(path))):
        raise TransportError('path is not canonical absolute')
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            if exists:
                raise TransportError('required path is absent')
            continue
        if stat.S_ISLNK(mode):
            raise TransportError('symlink path is forbidden')
    return path


def read_bounded(path: Path, limit: int) -> bytes:
    safe_path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise TransportError('not a regular file')
        with os.fdopen(descriptor, 'rb', closefd=False) as source:
            value = source.read(limit + 1)
        if len(value) > limit:
            raise TransportError('file exceeds size limit')
        return value
    finally:
        os.close(descriptor)


def write_new(path: Path, value, limit: int = MAX_RESULT) -> None:
    content = encoded(value) + b'\n'
    if len(content) > limit:
        raise TransportError('result exceeds size limit')
    safe_path(path, exists=False)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, 'wb', closefd=False) as output:
            output.write(content)
            output.flush()
            os.fsync(descriptor)
    finally:
        os.close(descriptor)
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


class NativeTransport:
    """A request is one-use even after failure or ambiguous interruption.

    policy(native_event, request) must derive role and authority from protected
    configuration and canonical Git/fence state. It returns a deterministic plan.
    execute(plan, request) executes fixed operations and independently verifies
    postconditions. Neither callback may execute arbitrary request-provided argv.
    """

    def __init__(self, root: Path, state: Path, workspaces: tuple[Path, ...],
                 policy: Callable, execute: Callable, *, clock=time.time):
        self.root = safe_path(root)
        self.state = safe_path(state)
        self.workspaces = tuple(str(safe_path(item)) for item in workspaces)
        if str(root) not in self.workspaces or len(set(self.workspaces)) != len(self.workspaces):
            raise TransportError('invalid mounted workspace set')
        if any(state.is_relative_to(Path(item)) for item in self.workspaces):
            raise TransportError('protected state must be outside Agent workspaces')
        state_stat = state.stat()
        if not state.is_dir() or state_stat.st_uid != os.geteuid() or state_stat.st_mode & 0o077:
            raise TransportError('controller state is not private')
        self.requests = root / '.local/two-site-refactor/gateway/requests'
        self.results = root / '.local/two-site-refactor/gateway/results'
        for directory in (self.requests, self.results):
            safe_path(directory, exists=False)
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            safe_path(directory)
        self.policy, self.execute, self.clock = policy, execute, clock

    @contextmanager
    def locked(self):
        path = self.state / 'transport.lock'
        safe_path(path, exists=False)
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise TransportError('controller lock is not regular')
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)

    def admission_check(self) -> None:
        """Bound total retained state and freeze after any ambiguous prior effect.

        A different UUID does not authorize replay after a crash. A protected
        supervisor must reconcile the actual Git/receipt/operation state before
        retiring an unresolved record. Neither the Agent nor this transport clears
        evidence automatically. Call under locked() for every mutation admission.
        """
        count, total = 0, 0
        directories = [self.state]
        running = []
        while directories:
            directory = directories.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    count += 1
                    if count >= MAX_STATE_FILES:
                        raise TransportError('protected retention file budget exhausted')
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
                        directories.append(Path(entry.path))
                    elif stat.S_ISREG(info.st_mode):
                        total += info.st_size
                        if total > MAX_STATE_BYTES - MAX_RESULT - 2 * MAX_REQUEST:
                            raise TransportError('protected retention byte budget exhausted')
                        if entry.name.endswith('.running.json'):
                            running.append(Path(entry.path))
                    else:
                        raise TransportError('unsupported object in protected state')
        for path in running:
            request_id = path.name.removesuffix('.running.json')
            if path.parent != self.state or not UUID.fullmatch(request_id):
                raise TransportError('unknown running-operation record')
            receipt = self.state / (request_id + '.receipt.json')
            saved = unique_json(read_bounded(receipt, MAX_RESULT)) if receipt.exists() else None
            terminal = (isinstance(saved, dict) and saved.get('request_id') == request_id
                        and saved.get('authority') == 'PROTECTED_CONTROLLER_RECEIPT'
                        and saved.get('outcome', {}).get('status') in {'SUCCESS', 'FAILED', 'BLOCKED'})
            if terminal:
                continue
            reconciliation = self.state / (request_id + '.reconciliation.json')
            if not reconciliation.exists():
                raise TransportError('an indeterminate interrupted operation needs protected reconciliation')
            record = unique_json(read_bounded(reconciliation, MAX_REQUEST))
            running_sha = hashlib.sha256(read_bounded(path, MAX_REQUEST)).hexdigest()
            receipt_sha = (hashlib.sha256(read_bounded(receipt, MAX_RESULT)).hexdigest()
                           if receipt.exists() else None)
            if (not isinstance(record, dict) or set(record) != {
                    'schema_version', 'request_id', 'operation', 'decision', 'running_sha256',
                    'receipt_sha256', 'reconciled_at', 'authority'}
                    or record.get('schema_version') != '1.0'
                    or record.get('request_id') != request_id
                    or record.get('decision') != 'RETRY_SAFE_NO_AUTHORITY'
                    or record.get('running_sha256') != running_sha
                    or record.get('receipt_sha256') != receipt_sha
                    or type(record.get('reconciled_at')) not in (int, float)
                    or record.get('authority') != 'PROTECTED_ROOT_RECOVERY'):
                raise TransportError('indeterminate operation lacks exact protected reconciliation')

    def compact_completed(self, active_assignment: str) -> None:
        """Replace closed-Assignment protected receipts with one immutable digest index.

        Public result evidence is retained at its stable project-local path. Only raw
        protected transport envelopes that can no longer authorize the active
        Assignment are removed, and only after durable exact-SHA Stage review exists.
        """
        if not isinstance(active_assignment, str) or not active_assignment:
            raise TransportError('active Assignment is invalid for compaction')
        groups: dict[tuple[str, str], list[tuple[Path, dict, bytes]]] = {}
        for path in self.state.glob('*.receipt.json'):
            raw = read_bounded(path, MAX_RESULT)
            receipt = unique_json(raw)
            request = receipt.get('request') if isinstance(receipt, dict) else None
            outcome = receipt.get('outcome') if isinstance(receipt, dict) else None
            assignment = request.get('assignment') if isinstance(request, dict) else None
            stage = (outcome.get('authority_record', {}).get('stage_id')
                     if isinstance(outcome, dict) else None)
            if isinstance(assignment, str) and not isinstance(stage, str):
                assignment_path = self.root / '.local/two-site-refactor/assignments' / (assignment + '.md')
                if assignment_path.is_file():
                    match = re.search(r'^stage_id: ([A-Z][A-Z0-9]*-[0-9]+[a-z]?)$',
                                      read_bounded(assignment_path, 1_000_000).decode(), re.M)
                    stage = match.group(1) if match else None
            if assignment == active_assignment:
                continue
            if (not isinstance(assignment, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', assignment)
                    or not isinstance(stage, str) or not re.fullmatch(r'[A-Z][A-Z0-9]*-[0-9]+[a-z]?', stage)
                    or receipt.get('authority') != 'PROTECTED_CONTROLLER_RECEIPT'
                    or outcome.get('status') not in {'SUCCESS', 'FAILED', 'BLOCKED'}):
                continue
            groups.setdefault((assignment, stage), []).append((path, receipt, raw))
        archive_dir = self.state / 'archive'
        for (assignment, stage), entries in groups.items():
            if len(entries) > MAX_ARCHIVE_ENTRIES:
                raise TransportError('closed Assignment exceeds protected compaction budget')
            durable = self.root / 'docs/refactor/two-server/reviews' / (stage + '-' + assignment + '.json')
            order = self.root / 'docs/refactor/two-server/execution-order.yaml'
            if (not durable.is_file() or not re.search(
                    rf'^  {re.escape(stage)}: \{{state: COMPLETE,',
                    read_bounded(order, 2_000_000).decode(), re.M)):
                continue
            summary = {
                'schema_version': '1.0', 'assignment_id': assignment, 'stage_id': stage,
                'entries': sorted(({
                    'request_id': item[1]['request_id'],
                    'operation': item[1]['request']['operation'],
                    'status': item[1]['outcome']['status'],
                    'protected_receipt_sha256': hashlib.sha256(item[2]).hexdigest(),
                    'authority_sha256': item[1]['outcome'].get('authority_sha256'),
                } for item in entries), key=lambda value: value['request_id']),
                'durable_review_sha256': hashlib.sha256(read_bounded(durable, 2_000_000)).hexdigest(),
                'authority': 'PROTECTED_CONTROLLER_COMPACTION',
            }
            archive_dir.mkdir(mode=0o700, exist_ok=True)
            archive = archive_dir / (assignment + '.json')
            if archive.exists():
                existing = unique_json(read_bounded(archive, MAX_RESULT))
                expected = {item['request_id']: item for item in existing.get('entries', [])}
                if (existing.get('schema_version') != '1.0'
                        or existing.get('assignment_id') != assignment
                        or existing.get('stage_id') != stage
                        or existing.get('durable_review_sha256') != summary['durable_review_sha256']
                        or existing.get('authority') != 'PROTECTED_CONTROLLER_COMPACTION'
                        or any(expected.get(item['request_id']) != item for item in summary['entries'])):
                    raise TransportError('closed Assignment archive differs from protected receipts')
            else:
                write_new(archive, summary, MAX_RESULT)
            for receipt_path, receipt, _ in entries:
                request_id = receipt['request_id']
                for suffix in ('.permit.json', '.running.json', '.receipt.json', '.reconciliation.json'):
                    target = self.state / (request_id + suffix)
                    if target.exists():
                        safe_path(target).unlink()
            descriptor = os.open(self.state, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def request_from_event(self, event: dict) -> tuple[dict, dict]:
        if not isinstance(event, dict) or len(encoded(event)) > MAX_EVENT:
            raise TransportError('invalid native hook event')
        actor, step = event.get('conversationId'), event.get('stepIdx')
        if (not isinstance(actor, str) or not UUID.fullmatch(actor)
                or type(step) is not int or step < 0 or event.get('modelName') != MODEL
                or event.get('workspacePaths') != list(self.workspaces)):
            raise TransportError('native actor/model/workspace mismatch')
        call = event.get('toolCall')
        if not isinstance(call, dict) or set(call) != {'name', 'args'} or call['name'] != 'write_to_file':
            raise TransportError('only a native request-file write is accepted')
        args = call['args']
        allowed = {'TargetFile', 'CodeContent', 'Overwrite', 'Description', 'IsArtifact',
                   'toolAction', 'toolSummary'}
        if not isinstance(args, dict) or set(args) - allowed:
            raise TransportError('unknown request-write argument')
        if args.get('Overwrite') is not False or args.get('IsArtifact', False) is not False:
            raise TransportError('request must be a new non-artifact file')
        for field in ('Description', 'toolAction', 'toolSummary'):
            if not isinstance(args.get(field, ''), str) or len(args.get(field, '')) > 4096:
                raise TransportError('invalid descriptive metadata')
        content = args.get('CodeContent')
        if not isinstance(content, str) or len(content.encode()) > MAX_REQUEST:
            raise TransportError('request body exceeds limit')
        request = unique_json(content)
        if not isinstance(request, dict):
            raise TransportError('request must be a JSON object')
        request_id = request.get('id')
        if not isinstance(request_id, str) or not UUID.fullmatch(request_id) or request.get('actor_id') != actor:
            raise TransportError('request cannot supply another native identity')
        path = self.requests / (request_id + '.json')
        if args.get('TargetFile') != str(path):
            raise TransportError('request path/ID mismatch')
        safe_path(path, exists=False)
        return request, {'actor': actor, 'step': step, 'request_id': request_id,
                         'request_sha256': digest(request)}

    def pre(self, event: dict) -> dict:
        request, identity = self.request_from_event(event)
        request_id = identity['request_id']
        with self.locked():
            self.admission_check()
            if (self.requests / (request_id + '.json')).exists() or any(
                    (self.state / (request_id + suffix)).exists()
                    for suffix in ('.permit.json', '.running.json', '.receipt.json')):
                raise TransportError('request ID has already been used')
            plan = self.policy(event, request)
            write_new(self.state / (request_id + '.permit.json'),
                      {**identity, 'plan_sha256': digest(plan), 'issued_at': self.clock()}, MAX_REQUEST)
        # No command/unsandboxed grant. Only the already-allowed project-local write.
        return {'decision': 'allow', 'reason': 'native-identity-bound typed request'}

    def post(self, event: dict) -> dict:
        request, identity = self.request_from_event(event)
        request_id = identity['request_id']
        permit_path = self.state / (request_id + '.permit.json')
        running = self.state / (request_id + '.running.json')
        protected_result = self.state / (request_id + '.receipt.json')
        public_result = self.results / (request_id + '.json')
        with self.locked():
            permit = unique_json(read_bounded(permit_path, MAX_REQUEST))
            if (not isinstance(permit, dict) or set(permit) != {*identity, 'plan_sha256', 'issued_at'}
                    or any(permit.get(key) != value for key, value in identity.items())
                    or type(permit['issued_at']) not in (float, int)
                    or not 0 <= self.clock() - permit['issued_at'] <= 120):
                raise TransportError('pre/post identity or permit lifetime mismatch')
            if event.get('error'):
                raise TransportError('native request write failed; no operation is permitted')
            written = unique_json(read_bounded(self.requests / (request_id + '.json'), MAX_REQUEST))
            if digest(written) != identity['request_sha256']:
                raise TransportError('written request differs from pre-authorized content')
            plan = self.policy(event, request)
            if digest(plan) != permit['plan_sha256']:
                raise TransportError('authority or operation plan changed since pre-write')
            # O_EXCL via link makes duplicate deliveries safe across processes/crashes.
            os.link(permit_path, running, follow_symlinks=False)
            permit_path.unlink()
            descriptor = os.open(self.state, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            try:
                outcome = self.execute(plan, request)
                if not isinstance(outcome, dict) or outcome.get('status') not in {'SUCCESS', 'FAILED', 'BLOCKED'}:
                    raise TransportError('executor did not return a verified outcome')
                result = {**identity, 'request': request, 'plan_sha256': permit['plan_sha256'], 'completed_at': self.clock(),
                          'outcome': outcome, 'authority': 'PROTECTED_CONTROLLER_RECEIPT'}
            except Exception as error:
                result = {**identity, 'request': request, 'plan_sha256': permit['plan_sha256'], 'completed_at': self.clock(),
                          'outcome': {'status': 'INDETERMINATE', 'error_class': type(error).__name__},
                          'authority': 'PROTECTED_CONTROLLER_RECEIPT'}
            write_new(protected_result, result)
            # Public cache cannot overwrite another file or become receipt authority.
            write_new(public_result, result)
        return {}
