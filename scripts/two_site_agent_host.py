#!/usr/bin/env python3
"""Protected native entrypoint for the project-scoped Antigravity gateway.

The installed copy of this module is root-owned and exact-SHA reviewed.  It turns
native hook metadata into the fixed planner/executor grammar; it is not a shell,
provider, deployment, DNS, Writer, secret, or arbitrary-filesystem gateway.
"""
from __future__ import annotations

import hashlib
import fcntl
import json
import os
import re
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

import jsonschema
import yaml

from scripts import two_site_orchestrator_fence as fence_api
from scripts.two_site_agent_context import ContextBuilder
from scripts.two_site_agent_controller import NativeController, hook_response
from scripts.two_site_agent_executor import (
    LocalOperationRunner, MutationBoundary, PinnedPaths, execute_plan, observe_git,
)
from scripts.two_site_agent_gateway import (
    ActorRole, EnvClass, OperationPlan, RESULTING_HEAD, REVIEW_BUNDLE_FILES,
)
from scripts.two_site_agent_sandbox import (
    GitAncestryEvidence,
    _run_bounded_process,
    hook_deadline_seconds,
    operation_deadline,
)
from scripts.two_site_agent_transport import (
    MAX_EVENT, MAX_REQUEST, MAX_RESULT, NativeTransport, TransportError, digest, read_bounded,
    safe_path, unique_json, write_new,
)

STATE_ROOT = Path('/var/lib/two-site-agent-gateway/controller')
MANIFEST_PATH = Path('/var/lib/two-site-agent-gateway/manifest.json')
MAX_REVIEW_OUTPUT = 2_000_000
REVIEW_TIMEOUT = 1300


def file_sha256(path: Path) -> str:
    safe_path(path)
    value = hashlib.sha256()
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        with os.fdopen(descriptor, 'rb', closefd=False) as source:
            while chunk := source.read(1024 * 1024):
                value.update(chunk)
    finally:
        os.close(descriptor)
    return value.hexdigest()


class ProtectedGatewayHost:
    def __init__(self, manifest: dict, state: Path = STATE_ROOT):
        self.manifest = manifest
        self.root = safe_path(Path(manifest.get('root', '')))
        self.state = safe_path(state)
        self.builder = ContextBuilder(self.root, self.state, manifest, observe_git)
        workspaces = tuple(safe_path(Path(value)) for value in manifest.get('workspaces', ()))
        self.transport = NativeTransport(
            self.root, self.state, workspaces, self._policy, self._execute,
        )
        self.controller = NativeController(self.builder, self.transport)
        self.builder.validate_installation()
        with self.transport.locked():
            self._recover_stage_bind_transaction()
            _, _, assignment = self.builder.active_assignment()
            self.transport.compact_completed(assignment['assignment_id'])

    def _policy(self, event: dict, request: dict) -> OperationPlan:
        return self.controller.policy(event, request)

    def _binary(self, name: str, *, verify: bool = True) -> Path:
        record = self.manifest['binary_sha256'][name]
        path = safe_path(Path(record['path']))
        if verify and file_sha256(path) != record['sha256']:
            raise TransportError('protected binary pin changed: ' + name)
        return path

    def _pins(self) -> tuple[PinnedPaths, dict, dict]:
        fence, _, assignment = self.builder.active_assignment()
        stage = safe_path(Path(assignment['worktree_path']))
        pins = PinnedPaths(
            self.root,
            stage,
            'refactor/two-site-architecture',
            assignment['branch'],
            self._binary('git'),
            self.manifest['binary_sha256']['git']['sha256'],
            self._binary('python'),
            self.manifest['binary_sha256']['python']['sha256'],
            self.root / 'scripts/two_site_orchestrator_fence.py',
            self.manifest['source_sha256']['scripts/two_site_orchestrator_fence.py'],
            self.root / 'scripts/two_site_codex_final_review.sh',
            self.manifest['source_sha256']['scripts/two_site_codex_final_review.sh'],
            self.root / 'scripts/two_site_codex_closeout_review.sh',
            self.manifest['source_sha256']['scripts/two_site_codex_closeout_review.sh'],
        )
        return pins, fence, assignment

    def _before_mutation(self, boundary: MutationBoundary, plan: OperationPlan,
                         request: Mapping[str, Any], pins: PinnedPaths) -> None:
        self.builder.validate_installation()
        fence, _, assignment = self.builder.active_assignment()
        bindings = dict(plan.bindings)
        if (boundary.request_id != plan.request_id or boundary.operation != plan.operation
                or boundary.phase not in {'command', 'followup'}):
            raise TransportError('mutation callback is not bound to the protected plan')
        planned = plan.commands if boundary.phase == 'command' else plan.followups
        if (boundary.command_index < 0 or boundary.command_index >= len(planned)
                or boundary.command != planned[boundary.command_index]):
            raise TransportError('mutation callback command differs from the protected plan')

        post_commit_followup = (
            boundary.phase == 'followup'
            and plan.operation in {'integrate', 'next-stage-commit'}
        )
        if post_commit_followup:
            boundary_head_valid = (
                boundary.command.observed_value == RESULTING_HEAD
                and boundary.integration.clean
                and boundary.integration.head_sha != request['integration_head']
                and boundary.integration.parent_sha == request['integration_head']
            )
        else:
            boundary_head = (request.get('closeout_head')
                             if plan.operation == 'closeout-advance' else request['integration_head'])
            boundary_head_valid = boundary.integration.head_sha == boundary_head
        if (fence['active_assignment_id'] != request['assignment']
                or assignment['assignment_id'] != request['assignment']
                or fence['epoch'] != request['epoch']
                or fence['coordinator_instance_id'] != self.manifest['coordinator_id']
                or fence['integration_head_sha'] != request['integration_head']
                or not boundary_head_valid
                or assignment['worktree_path'] != str(pins.stage_worktree)
                or assignment['branch'] != pins.stage_branch
                or bindings.get('assignment') != request['assignment']
                or bindings.get('epoch') != str(request['epoch'])
                or self.controller.reservation.exists()):
            raise TransportError('mutation boundary lost its protected fence/source lock')

    def _runner(self, pins: PinnedPaths, assignment: dict) -> LocalOperationRunner:
        stage = observe_git(pins.stage_worktree)
        root = observe_git(pins.root)
        ancestry = (
            (pins.stage_worktree, GitAncestryEvidence(assignment['base_sha'], stage.head_sha)),
            (pins.root, GitAncestryEvidence(assignment['integration_head_at_issue'], root.head_sha)),
        )
        return LocalOperationRunner(ancestry)

    @contextmanager
    def _fence_locked(self):
        path = self.root / '.local/two-site-refactor/orchestrator-fence.lock'
        safe_path(path, exists=False)
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)

    def _validate_host_prepare(self, plan: OperationPlan, request: Mapping[str, Any],
                               pins: PinnedPaths) -> dict:
        self.builder.validate_installation()
        fence, _, assignment = self.builder.active_assignment()
        bindings = dict(plan.bindings)
        snapshot = observe_git(self.root)
        if (fence['coordinator_instance_id'] != self.manifest['coordinator_id']
                or fence['active_assignment_id'] != request['assignment']
                or fence['epoch'] != request['epoch']
                or fence['integration_head_sha'] != request['integration_head']
                or assignment['assignment_id'] != request['assignment']
                or assignment['worktree_path'] != str(pins.stage_worktree)
                or bindings.get('assignment') != request['assignment']
                or bindings.get('epoch') != str(request['epoch'])
                or snapshot.head_sha != request['integration_head']):
            raise TransportError('host preparation lost its exact plan/fence binding')
        return assignment

    def _review_supervisor(self, command, request: Mapping[str, Any],
                           bindings: Mapping[str, str]) -> Mapping[str, Any]:
        if command.env_class is not EnvClass.EXTERNAL_CODEX_REVIEW:
            return {'status': 'BLOCKED', 'reason': 'unknown external environment class'}
        codex = self._binary('codex')
        bridge = safe_path(Path(command.argv[0]))
        if bridge not in {
            self.root / 'scripts/two_site_codex_final_review.sh',
            self.root / 'scripts/two_site_codex_closeout_review.sh',
        }:
            return {'status': 'BLOCKED', 'reason': 'review bridge is outside the fixed catalog'}
        bundle_relative = bindings.get('bundle')
        expected_bundle = (
            f".local/two-site-refactor/review-bundles/"
            f"{bindings.get('stage')}-{request.get('assignment')}"
        )
        if not isinstance(bundle_relative, str) or bundle_relative != expected_bundle:
            raise TransportError('review plan lacks its canonical bundle binding')
        bundle = safe_path(self.root / bundle_relative)
        def validate_inputs() -> None:
            for name in REVIEW_BUNDLE_FILES:
                key = 'review_input_' + name.replace('.', '_').lower() + '_sha256'
                if file_sha256(bundle / name) != bindings.get(key):
                    raise TransportError('review bundle changed after protected planning')

        validate_inputs()
        if command.argv[0] == str(self.root / 'scripts/two_site_codex_closeout_review.sh'):
            final_path = safe_path(self.root / str(bindings.get('final_receipt', '')))
            if file_sha256(final_path) != bindings.get('final_receipt_file_sha256'):
                raise TransportError('accepted final receipt changed before closeout review')
            snapshot = observe_git(self.root)
            if snapshot.staged_patch_sha256 != bindings.get('closeout_patch'):
                raise TransportError('closeout patch changed before protected review')
        environment = {
            'PATH': str(codex.parent) + ':/usr/local/bin:/usr/bin:/bin',
            'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
            'PYTHONDONTWRITEBYTECODE': '1', 'GIT_TERMINAL_PROMPT': '0',
        }
        for key in ('HOME', 'CODEX_HOME', 'XDG_CONFIG_HOME', 'SSL_CERT_FILE', 'SSL_CERT_DIR'):
            value = os.environ.get(key)
            if value:
                environment[key] = value
        result = _run_bounded_process(
            command.argv, cwd=self.root, env=environment, timeout=REVIEW_TIMEOUT,
            output_limit=MAX_REVIEW_OUTPUT,
        )
        validate_inputs()
        if command.argv[0] == str(self.root / 'scripts/two_site_codex_closeout_review.sh'):
            if (file_sha256(final_path) != bindings.get('final_receipt_file_sha256')
                    or observe_git(self.root).staged_patch_sha256 != bindings.get('closeout_patch')):
                raise TransportError('closeout review inputs changed during protected review')
        response = {
            'status': 'SUCCESS' if result.returncode == 0 else 'FAILED',
            'returncode': result.returncode,
            'stdout_sha256': hashlib.sha256(result.stdout).hexdigest(),
            'stderr_sha256': hashlib.sha256(result.stderr).hexdigest(),
            'stdout': result.stdout[-64_000:].decode('utf-8', errors='replace'),
            'stderr': result.stderr[-64_000:].decode('utf-8', errors='replace'),
        }
        if result.returncode == 0:
            if command.argv[0] == str(self.root / 'scripts/two_site_codex_final_review.sh'):
                receipt_name, markdown_name = 'CODEX_FINAL_REVIEW.json', 'CODEX_FINAL_REVIEW.md'
            else:
                receipt_name, markdown_name = 'CODEX_CLOSEOUT_PREACTION.json', None
            receipt = safe_path(bundle / receipt_name)
            response['receipt_path'] = receipt.relative_to(self.root).as_posix()
            response['receipt_file_sha256'] = file_sha256(receipt)
            if markdown_name is not None:
                markdown = safe_path(bundle / markdown_name)
                response['markdown_path'] = markdown.relative_to(self.root).as_posix()
                response['markdown_file_sha256'] = file_sha256(markdown)
        return response

    @staticmethod
    def _snapshot(outcome: dict, side: str, index: int) -> dict:
        values = outcome.get(side)
        if not isinstance(values, list) or len(values) != 2 or not isinstance(values[index], dict):
            raise TransportError('executor authority lacks an exact Git snapshot pair')
        return values[index]

    def _validated_review_receipt(self, relative: str, schema_relative: str) -> tuple[dict, str]:
        path = safe_path(self.root / relative)
        receipt = unique_json(read_bounded(path, 1_000_000))
        schema = unique_json(read_bounded(self.root / schema_relative, 128_000))
        try:
            jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(receipt)
        except jsonschema.ValidationError as error:
            raise TransportError('review bridge produced a schema-invalid receipt') from error
        return receipt, file_sha256(path)

    @staticmethod
    def _validate_review_decision(receipt: Mapping[str, Any], *, closeout: bool) -> None:
        verdict = receipt.get('verdict')
        decision = receipt.get('decision', {})
        if verdict != 'APPROVE':
            if ((closeout and decision.get('commit_authorized') is not False)
                    or (not closeout and decision.get('accepted_commit') is not None)):
                raise TransportError('non-APPROVE review contradicts its decision')
            return
        findings = receipt.get('findings', [])
        scope = receipt.get('scope', {})
        if closeout:
            scope_ok = (scope.get('stage_receipt_valid') is True
                        and scope.get('changed_paths_closeout_only') is True
                        and scope.get('state_transition_valid') is True
                        and decision.get('commit_authorized') is True)
        else:
            verification = receipt.get('verification', [])
            scope_ok = (scope.get('dependency_receipts_valid') is True
                        and scope.get('changed_paths_within_assignment') is True
                        and scope.get('locks_and_parallel_peer_valid') is True
                        and isinstance(verification, list) and bool(verification)
                        and all(item.get('result') == 'PASS' for item in verification))
        if (not scope_ok or scope.get('unauthorized_external_mutation') != 'NONE'
                or decision.get('conditions')
                or any(item.get('severity') in {'HIGH', 'CRITICAL'} for item in findings)):
            raise TransportError('APPROVE review contradicts mandatory protected gates')

    @staticmethod
    def _atomic_project_write(path: Path, content: bytes) -> None:
        safe_path(path, exists=False)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        safe_path(path.parent)
        if path.exists():
            safe_path(path)
            if not path.is_file():
                raise TransportError('prepared target is not a regular file')
        descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
        try:
            with os.fdopen(descriptor, 'wb') as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
            parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def _prepare_bundle(self, plan: OperationPlan, request: Mapping[str, Any], assignment: dict) -> dict:
        bundle = self.root / dict(plan.bindings)['bundle']
        safe_path(bundle, exists=False)
        bundle.mkdir(parents=True, exist_ok=True, mode=0o700)
        safe_path(bundle)
        assignment_source = read_bounded(
            self.root / '.local/two-site-refactor/assignments' / (assignment['assignment_id'] + '.md'),
            1_000_000,
        )
        values = {
            'assignment.md': assignment_source,
            'STAGE_REPORT.md': request['stage_report'].encode(),
            'FINAL_REVIEW_REQUEST.md': request['final_review_request'].encode(),
            'HANDOFF.md': request['handoff'].encode(),
            'commit.txt': (
                f"stage={assignment['stage_id']}\nassignment={assignment['assignment_id']}\n"
                f"base_sha={request['base_sha']}\nhead_sha={request['head_sha']}\n"
            ).encode(),
        }
        observed = {}
        for name, content in values.items():
            self._atomic_project_write(bundle / name, content)
            observed[name] = file_sha256(bundle / name)
        bindings = dict(plan.bindings)
        if (observed['STAGE_REPORT.md'] != bindings['stage_report_sha256']
                or observed['FINAL_REVIEW_REQUEST.md'] != bindings['final_request_sha256']
                or observed['HANDOFF.md'] != bindings['handoff_sha256']):
            raise TransportError('prepared review bundle differs from the authorized request')
        return {'bundle': bundle.relative_to(self.root).as_posix(), 'files_sha256': observed}

    def _prepare_closeout(self, plan: OperationPlan, request: Mapping[str, Any], assignment: dict,
                          pins: PinnedPaths) -> dict:
        accepted = request['accepted_head']
        initial = observe_git(self.root)
        if initial.head_sha != accepted or not initial.clean:
            raise TransportError('closeout preparation lost the exact clean accepted HEAD')
        bundle_rel = f".local/two-site-refactor/review-bundles/{assignment['stage_id']}-{assignment['assignment_id']}"
        final_json_rel = bundle_rel + '/CODEX_FINAL_REVIEW.json'
        final_json, final_file_sha = self._validated_review_receipt(
            final_json_rel, 'docs/refactor/two-server/templates/final-review.schema.json')
        if final_file_sha != dict(plan.bindings).get('final_receipt_file_sha256'):
            raise TransportError('accepted final receipt changed after protected planning')
        self._validate_review_decision(final_json, closeout=False)
        if final_json.get('verdict') != 'APPROVE' or final_json.get('reviewed_head_sha') != accepted:
            raise TransportError('closeout preparation requires exact-head Codex APPROVE')
        final_md = read_bounded(self.root / (bundle_rel + '/CODEX_FINAL_REVIEW.md'), 1_000_000)
        durable = f"docs/refactor/two-server/reviews/{assignment['stage_id']}-{assignment['assignment_id']}"
        order_path = self.root / 'docs/refactor/two-server/execution-order.yaml'
        order = read_bounded(order_path, 2_000_000).decode()
        pattern = re.compile(rf"^(  {re.escape(assignment['stage_id'])}: \{{state: )IN_PROGRESS(,.*)$", re.M)
        updated_order, count = pattern.subn(r'\1COMPLETE\2', order)
        if count != 1:
            raise TransportError('closeout preparation cannot identify one IN_PROGRESS Stage line')
        reviewed_date = str(final_json['reviewed_at_utc'])[:10]
        record_id = f"{assignment['stage_id']}-COMPLETE-{reviewed_date.replace('-', '')}"
        ledger_path = self.root / 'docs/refactor/two-server/CHANGE_LEDGER.md'
        ledger = read_bounded(ledger_path, 2_000_000).decode()
        if record_id in ledger:
            raise TransportError('closeout ledger record already exists')
        ledger += (
            f"\n### Record `{record_id}`\n\n"
            f"- date: `{reviewed_date}`\n- stage_id: `{assignment['stage_id']}`\n"
            "- from_state: `IN_PROGRESS`\n- to_state: `COMPLETE`\n"
            "- actor: `ANTIGRAVITY_COORDINATOR` after `CODEX_FINAL_REVIEWER` `APPROVE`\n"
            f"- commit: `{accepted}`\n- environment: integration `{pins.integration_branch}`; no external mutation\n"
            f"- evidence_uri: `{durable}.md`\n- decision_or_gap_ids: `NONE`\n"
            "- notes: exact-SHA implementation acceptance; closeout remains subject to patch-bound pre-action review\n"
        )
        outputs = {
            'docs/refactor/two-server/CHANGE_LEDGER.md': ledger.encode(),
            'docs/refactor/two-server/execution-order.yaml': updated_order.encode(),
            durable + '.json': json.dumps(final_json, ensure_ascii=False, sort_keys=True, indent=2).encode() + b'\n',
            durable + '.md': final_md,
        }
        for relative, content in outputs.items():
            self._atomic_project_write(self.root / relative, content)
        result = _run_bounded_process(
            [str(pins.git_binary), '-C', str(self.root), 'add', '--', *sorted(outputs)],
            cwd=self.root,
            env={'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8',
                 'GIT_TERMINAL_PROMPT': '0'}, timeout=60, output_limit=64_000,
        )
        if result.returncode != 0:
            raise TransportError('fixed closeout staging failed')
        snapshot = observe_git(self.root)
        if (snapshot.head_sha != accepted or snapshot.unstaged_paths or snapshot.untracked_paths
                or snapshot.staged_paths != tuple(sorted(outputs))
                or snapshot.staged_patch_sha256 is None):
            raise TransportError('prepared closeout index differs from the exact four-path contract')
        return {'staged_paths': list(snapshot.staged_paths),
                'patch_sha256': snapshot.staged_patch_sha256}

    def _prepare_next_stage_transition(self, request: Mapping[str, Any], assignment: dict,
                                       pins: PinnedPaths) -> dict:
        snapshot = observe_git(self.root)
        if snapshot.head_sha != request['integration_head'] or not snapshot.clean:
            raise TransportError('next Stage transition requires the exact clean integration HEAD')
        order_path = self.root / 'docs/refactor/two-server/execution-order.yaml'
        order = read_bounded(order_path, 2_000_000).decode()
        next_stage = str(request['next_stage'])
        pattern = re.compile(rf"^(  {re.escape(next_stage)}: \{{state: )PROPOSED(,.*)$", re.M)
        updated, count = pattern.subn(r'\1IN_PROGRESS\2', order)
        if count != 1:
            raise TransportError('next Stage transition cannot identify one canonical PROPOSED line')
        self._atomic_project_write(order_path, updated.encode())
        result = _run_bounded_process(
            [str(pins.git_binary), '-C', str(self.root), 'add', '--',
             'docs/refactor/two-server/execution-order.yaml'],
            cwd=self.root, env={'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8',
                                'GIT_TERMINAL_PROMPT': '0'}, timeout=60, output_limit=64_000,
        )
        if result.returncode != 0:
            raise TransportError('fixed next Stage transition staging failed')
        after = observe_git(self.root)
        if (after.head_sha != request['integration_head']
                or after.staged_paths != ('docs/refactor/two-server/execution-order.yaml',)
                or after.unstaged_paths or after.untracked_paths
                or after.staged_patch_sha256 is None):
            raise TransportError('next Stage transition differs from the exact one-file contract')
        return {'next_stage': next_stage, 'patch_sha256': after.staged_patch_sha256,
                'staged_paths': list(after.staged_paths)}

    @staticmethod
    def _contract_digest(contract: Mapping[str, Any]) -> str:
        return hashlib.sha256(json.dumps(
            contract, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
        ).encode()).hexdigest()

    @property
    def _stage_bind_journal_path(self) -> Path:
        return self.state / 'stage-bind-transaction.json'

    def _write_stage_bind_journal(self, record: Mapping[str, Any], phase: str) -> dict:
        updated = {**record, 'phase': phase}
        self._atomic_project_write(
            self._stage_bind_journal_path,
            json.dumps(updated, sort_keys=True, separators=(',', ':')).encode() + b'\n',
        )
        return updated

    def _git_common_dir(self, pins: PinnedPaths, worktree: Path) -> Path:
        result = _run_bounded_process(
            [str(pins.git_binary), '-C', str(worktree), 'rev-parse',
             '--path-format=absolute', '--git-common-dir'],
            cwd=self.root, env={'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8',
                                'GIT_TERMINAL_PROMPT': '0'}, timeout=60, output_limit=64_000,
        )
        if result.returncode != 0:
            raise TransportError('cannot prove worktree repository identity')
        raw = result.stdout.decode('utf-8', errors='strict').strip()
        return safe_path(Path(raw))

    def _worktree_registered(self, pins: PinnedPaths, worktree: Path) -> bool:
        result = _run_bounded_process(
            [str(pins.git_binary), '-C', str(self.root), 'worktree', 'list', '--porcelain'],
            cwd=self.root, env={'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8',
                                'GIT_TERMINAL_PROMPT': '0'}, timeout=60, output_limit=256_000,
        )
        if result.returncode != 0:
            raise TransportError('cannot inspect registered Git worktrees')
        entries = [line.removeprefix('worktree ') for line in
                   result.stdout.decode('utf-8', errors='strict').splitlines()
                   if line.startswith('worktree ')]
        return str(worktree) in entries

    def _stage_is_complete(self, stage_id: str) -> bool:
        order = read_bounded(
            self.root / 'docs/refactor/two-server/execution-order.yaml', 2_000_000,
        ).decode()
        return bool(re.search(
            rf'^  {re.escape(stage_id)}: \{{state: COMPLETE,', order, re.M,
        ))

    def _validate_retirement_candidate(self, record: Mapping[str, Any], pins: PinnedPaths,
                                       registry: Mapping[str, Any]) -> dict:
        matches = [item for item in registry.get('stage_worktrees', [])
                   if isinstance(item, dict)
                   and item.get('assignment_id') == record['old_assignment']]
        if len(matches) != 1:
            raise TransportError('retirement Assignment is not uniquely registered')
        item = matches[0]
        path = safe_path(Path(str(item.get('path', ''))), exists=False)
        workspace_root = safe_path(Path(self.manifest['stage_workspace_root']))
        if (path == workspace_root or not path.is_relative_to(workspace_root)
                or str(path) != record['old_worktree']
                or item.get('branch') != record['old_branch']
                or item.get('head_sha') != record['old_head']
                or item.get('stage_id') != record['old_stage']
                or item.get('status') not in {'COMPLETE_CANDIDATE', 'CLOSED', 'REMOVED'}
                or not self._stage_is_complete(str(record['old_stage']))):
            raise TransportError('retirement registry/Stage identity is not exact and COMPLETE')
        assignment_path = self.root / '.local/two-site-refactor/assignments' / (
            str(record['old_assignment']) + '.md')
        raw = read_bounded(assignment_path, 1_000_000).decode()
        blocks = re.findall(r'```yaml\n(.*?)\n```', raw, re.S)
        payload = yaml.safe_load(blocks[0]) if len(blocks) == 1 else None
        if (not isinstance(payload, dict)
                or payload.get('assignment_id') != record['old_assignment']
                or payload.get('stage_id') != record['old_stage']
                or payload.get('branch') != record['old_branch']
                or payload.get('worktree_path') != record['old_worktree']):
            raise TransportError('retirement Assignment document differs from registry identity')
        if path.exists():
            snapshot = observe_git(path)
            if (not snapshot.clean or snapshot.branch != record['old_branch']
                    or snapshot.head_sha != record['old_head']
                    or self._git_common_dir(pins, path) != self._git_common_dir(pins, self.root)):
                raise TransportError('retirement worktree branch/HEAD/repository is not exact')
        elif item.get('status') == 'CLOSED':
            if self._worktree_registered(pins, path):
                raise TransportError('CLOSED retirement worktree vanished but remains Git-registered')
        elif item.get('status') != 'REMOVED':
            raise TransportError('retirement path vanished without a closed/removed registry record')
        return item

    def _retire_bound_worktree(self, record: Mapping[str, Any], pins: PinnedPaths) -> None:
        registry_path = self.root / '.local/two-site-refactor/worktrees.yaml'
        registry = yaml.safe_load(read_bounded(registry_path, 1_000_000))
        item = self._validate_retirement_candidate(record, pins, registry)
        path = Path(record['old_worktree'])
        if path.exists():
            if item.get('status') != 'CLOSED':
                raise TransportError('only the newly CLOSED prior worktree may be retired')
            result = _run_bounded_process(
                [str(pins.git_binary), '-C', str(self.root), 'worktree', 'remove', str(path)],
                cwd=self.root, env={'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8',
                                    'GIT_TERMINAL_PROMPT': '0'}, timeout=120,
                output_limit=256_000,
            )
            if result.returncode != 0 or path.exists():
                raise TransportError('fixed prior Stage worktree retirement failed')
            item['status'] = 'REMOVED'
            item['removed_at'] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
            registry['updated_at'] = item['removed_at']
            self._atomic_project_write(
                registry_path, yaml.safe_dump(registry, sort_keys=False).encode())
        elif item.get('status') == 'CLOSED':
            item['status'] = 'REMOVED'
            item['removed_at'] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
            registry['updated_at'] = item['removed_at']
            self._atomic_project_write(
                registry_path, yaml.safe_dump(registry, sort_keys=False).encode())
        self._validate_retirement_candidate(record, pins, registry)

    def _journal_pins(self, record: Mapping[str, Any]) -> PinnedPaths:
        return PinnedPaths(
            self.root, Path(str(record['old_worktree'])), 'refactor/two-site-architecture',
            str(record['old_branch']), self._binary('git'),
            self.manifest['binary_sha256']['git']['sha256'], self._binary('python'),
            self.manifest['binary_sha256']['python']['sha256'],
            self.root / 'scripts/two_site_orchestrator_fence.py',
            self.manifest['source_sha256']['scripts/two_site_orchestrator_fence.py'],
            self.root / 'scripts/two_site_codex_final_review.sh',
            self.manifest['source_sha256']['scripts/two_site_codex_final_review.sh'],
            self.root / 'scripts/two_site_codex_closeout_review.sh',
            self.manifest['source_sha256']['scripts/two_site_codex_closeout_review.sh'],
        )

    def _stage_bind_inputs(self, record: Mapping[str, Any]) -> tuple[dict, dict, Path]:
        expected = {
            'schema_version', 'phase', 'request_id', 'request_sha256', 'plan_sha256',
            'old_assignment', 'old_stage', 'old_worktree', 'old_branch', 'old_head',
            'next_assignment', 'next_stage', 'next_worktree', 'next_branch',
            'integration_head', 'epoch', 'assignment_path', 'assignment_sha256',
            'registry_before', 'registry_before_sha256', 'registry_after',
            'registry_after_sha256', 'created_at',
        }
        if (not isinstance(record, dict) or set(record) != expected
                or record.get('schema_version') != '1.0'
                or record.get('phase') not in {
                    'STARTED', 'WORKTREE_CREATED', 'ASSIGNMENT_WRITTEN',
                    'REGISTRY_WRITTEN', 'FENCE_BOUND', 'COMPLETE',
                }):
            raise TransportError('Stage-bind recovery journal schema is invalid')
        request_id = record['request_id']
        running_path = self.state / (str(request_id) + '.running.json')
        running = unique_json(read_bounded(running_path, MAX_REQUEST))
        request_path = self.root / '.local/two-site-refactor/gateway/requests' / (
            str(request_id) + '.json')
        request = unique_json(read_bounded(request_path, MAX_REQUEST))
        if (not isinstance(running, dict) or not isinstance(request, dict)
                or request.get('id') != request_id or request.get('operation') != 'next-stage-bind'
                or digest(request) != record['request_sha256']
                or running.get('request_id') != request_id
                or running.get('request_sha256') != record['request_sha256']
                or running.get('plan_sha256') != record['plan_sha256']
                or running.get('actor') != request.get('actor_id')
                or request.get('assignment') != record['old_assignment']
                or request.get('next_assignment') != record['next_assignment']
                or request.get('next_stage') != record['next_stage']
                or request.get('integration_head') != record['integration_head']
                or request.get('epoch') != record['epoch']):
            raise TransportError('Stage-bind journal differs from protected request/running identity')
        return running, request, running_path

    def _finish_recovered_stage_bind(self, record: Mapping[str, Any], running: Mapping[str, Any],
                                     request: Mapping[str, Any], status: str, reason: str) -> None:
        if status not in {'SUCCESS', 'FAILED'}:
            raise TransportError('unsupported Stage-bind recovery result')
        request_id = str(record['request_id'])
        receipt_path = self.state / (request_id + '.receipt.json')
        if receipt_path.exists():
            raise TransportError('an existing nonterminal Stage-bind receipt cannot be replaced')
        result = {
            'actor': running['actor'], 'step': running['step'], 'request_id': request_id,
            'request_sha256': running['request_sha256'], 'request': request,
            'plan_sha256': running['plan_sha256'],
            'completed_at': datetime.now(timezone.utc).timestamp(),
            'outcome': {
                'status': status, 'request_id': request_id, 'operation': 'next-stage-bind',
                'reason': reason, 'authority': 'EXECUTOR_VERIFIED_NO_APPROVAL',
            },
            'authority': 'PROTECTED_CONTROLLER_RECEIPT',
        }
        if status == 'SUCCESS':
            result['outcome']['prepared'] = {
                'next_assignment': record['next_assignment'],
                'next_stage': record['next_stage'], 'worktree_path': record['next_worktree'],
                'branch': record['next_branch'],
            }
        write_new(receipt_path, result, MAX_RESULT)
        public = self.root / '.local/two-site-refactor/gateway/results' / (request_id + '.json')
        if public.exists():
            if unique_json(read_bounded(public, MAX_RESULT)) != result:
                raise TransportError('public Stage-bind recovery result differs')
        else:
            write_new(public, result, MAX_RESULT)

    def _validate_bound_stage(self, record: Mapping[str, Any], pins: PinnedPaths) -> None:
        fence = unique_json(read_bounded(
            self.root / '.local/two-site-refactor/orchestrator-fence.json', 65536))
        self._validate_stage_bind_fence_identity(record, pins, fence)
        if (fence.get('status') != 'ACTIVE' or fence.get('binding_kind') != 'STAGE'
                or fence.get('platform') != 'ANTIGRAVITY'
                or fence.get('integration_branch') != pins.integration_branch
                or fence.get('active_assignment_id') != record['next_assignment']
                or fence.get('previous_assignment_id') != record['old_assignment']
                or fence.get('integration_head_sha') != record['integration_head']
                or fence.get('epoch') != record['epoch']
                or fence.get('coordinator_instance_id') != self.manifest['coordinator_id']):
            raise TransportError('recovered Stage-bind fence is not exact and ACTIVE')

        _, request, _ = self._stage_bind_inputs(record)
        contract = request.get('contract')
        slug = contract.get('slug') if isinstance(contract, dict) else None
        if not isinstance(slug, str):
            raise TransportError('recovered Stage-bind request contract is malformed')
        workspace_root = safe_path(Path(self.manifest['stage_workspace_root']))
        expected_worktree = safe_path(
            workspace_root / f"{record['next_stage']}-{slug}", exists=False)
        expected_branch = f"refactor/stage/{record['next_stage']}-{slug}"
        expected_assignment_path = self.root / '.local/two-site-refactor/assignments' / (
            str(record['next_assignment']) + '.md')
        if (record.get('next_worktree') != str(expected_worktree)
                or record.get('next_branch') != expected_branch
                or record.get('assignment_path') != str(expected_assignment_path)):
            raise TransportError('recovered Stage-bind targets are not request-derived')

        assignment_path = safe_path(Path(str(record['assignment_path'])))
        if file_sha256(assignment_path) != record['assignment_sha256']:
            raise TransportError('recovered next Assignment content differs from journal')
        blocks = re.findall(r'```yaml\n(.*?)\n```', read_bounded(
            assignment_path, 1_000_000).decode(), re.S)
        assignment = yaml.safe_load(blocks[0]) if len(blocks) == 1 else None
        try:
            fence_api.validate_canonical_assignment(assignment)
            assignment_contract_sha = fence_api.immutable_contract_sha256(assignment)
        except Exception as error:
            raise TransportError('recovered next Assignment contract is invalid') from error
        if (assignment.get('assignment_id') != record['next_assignment']
                or assignment.get('stage_id') != record['next_stage']
                or assignment.get('role') != 'WORKER'
                or assignment.get('status') != 'READY'
                or assignment.get('orchestrator') != 'ANTIGRAVITY'
                or assignment.get('orchestration_epoch') != record['epoch']
                or assignment.get('worktree_path') != record['next_worktree']
                or assignment.get('branch') != record['next_branch']
                or assignment.get('base_sha') != record['integration_head']
                or assignment.get('integration_branch') != pins.integration_branch
                or assignment.get('integration_head_at_issue') != record['integration_head']
                or assignment.get('external_access') != 'NONE'
                or assignment.get('issued_by') != 'EXECUTION_COORDINATOR'
                or fence.get('assignment_base_sha') != assignment.get('base_sha')
                or fence.get('assignment_integration_head_at_issue')
                != assignment.get('integration_head_at_issue')
                or fence.get('immutable_contract_sha256') != assignment_contract_sha):
            raise TransportError('recovered next Assignment/fence owner contract differs')

        registry = yaml.safe_load(read_bounded(
            self.root / '.local/two-site-refactor/worktrees.yaml', 1_000_000))
        matches = [item for item in registry.get('stage_worktrees', [])
                   if isinstance(item, dict)
                   and item.get('assignment_id') == record['next_assignment']]
        integration = registry.get('integration') if isinstance(registry, dict) else None
        if (not isinstance(integration, dict)
                or integration.get('path') != str(self.root)
                or integration.get('branch') != pins.integration_branch
                or integration.get('head_sha') != record['integration_head']
                or integration.get('owner_role') != 'EXECUTION_COORDINATOR'
                or integration.get('active_assignment_id') != record['next_assignment']
                or integration.get('orchestrator') != 'ANTIGRAVITY'
                or integration.get('orchestration_epoch') != record['epoch']):
            raise TransportError('recovered next Stage integration owner differs')
        expected_stage = {
            'registry_id': assignment['worktree_registry_id'],
            'stage_id': record['next_stage'], 'assignment_id': record['next_assignment'],
            'owner_role': 'EXECUTION_WORKER', 'path': record['next_worktree'],
            'branch': record['next_branch'], 'base_sha': record['integration_head'],
            'head_sha': record['integration_head'],
            'dirty_uncommitted_candidate': False, 'locks': assignment['locks'],
            'status': 'READY', 'orchestrator': 'ANTIGRAVITY',
            'orchestration_epoch': record['epoch'],
            'created_at': record['created_at'], 'expires_at': assignment['expires_at'],
            'remove_within_hours_after_close': 24,
        }
        if len(matches) != 1 or matches[0] != expected_stage:
            raise TransportError('recovered next Stage registry identity differs')
        old = self._validate_retirement_candidate(record, pins, registry)
        if (old.get('status') != 'CLOSED'
                or old.get('owner_role') != 'EXECUTION_WORKER'
                or old.get('orchestrator') != 'ANTIGRAVITY'
                or old.get('orchestration_epoch') != record['epoch']):
            raise TransportError('recovered prior Stage retirement owner differs')
        worktree = safe_path(Path(str(record['next_worktree'])))
        snapshot = observe_git(worktree)
        if (not snapshot.clean or snapshot.branch != record['next_branch']
                or snapshot.head_sha != record['integration_head']
                or self._git_common_dir(pins, worktree) != self._git_common_dir(pins, self.root)):
            raise TransportError('recovered next Stage worktree is not exact')

    def _validate_stage_bind_fence_identity(
            self, record: Mapping[str, Any], pins: PinnedPaths,
            fence: Mapping[str, Any]) -> None:
        try:
            fence_api.validate_fence(fence)
        except Exception as error:
            raise TransportError('Stage-bind fence schema is invalid') from error
        if (fence.get('platform') != 'ANTIGRAVITY'
                or fence.get('coordinator_instance_id') != self.manifest['coordinator_id']
                or fence.get('epoch') != record['epoch']
                or fence.get('integration_branch') != pins.integration_branch
                or fence.get('integration_head_sha') != record['integration_head']):
            raise TransportError('Stage-bind fence ownership/integration identity drifted')
        snapshot = observe_git(self.root)
        if (not snapshot.clean or snapshot.branch != pins.integration_branch
                or snapshot.head_sha != record['integration_head']):
            raise TransportError('Stage-bind actual integration Git identity drifted')

    def _validate_stage_bind_rollback_authority(
            self, record: Mapping[str, Any], pins: PinnedPaths,
            fence: Mapping[str, Any]) -> None:
        """Prove the complete old owner/state before any rollback side effect.

        The caller holds the canonical fence lock.  Journal hashes alone are not
        authority: the current fence, Assignment, registry and actual Git state
        must all still describe the exact old Antigravity Stage.
        """
        self._validate_stage_bind_fence_identity(record, pins, fence)
        if (fence.get('status') != 'ACTIVE'
                or fence.get('active_assignment_id') != record['old_assignment']):
            raise TransportError('Stage-bind rollback is not owned by the exact old ACTIVE fence')

        _, request, _ = self._stage_bind_inputs(record)
        contract = request.get('contract')
        slug = contract.get('slug') if isinstance(contract, dict) else None
        if not isinstance(slug, str):
            raise TransportError('Stage-bind rollback request contract is malformed')
        workspace_root = safe_path(Path(self.manifest['stage_workspace_root']))
        derived_worktree = safe_path(
            workspace_root / f"{record['next_stage']}-{slug}", exists=False)
        derived_branch = f"refactor/stage/{record['next_stage']}-{slug}"
        derived_assignment = self.root / '.local/two-site-refactor/assignments' / (
            str(record['next_assignment']) + '.md')
        if (record.get('next_worktree') != str(derived_worktree)
                or record.get('next_branch') != derived_branch
                or record.get('assignment_path') != str(derived_assignment)):
            raise TransportError('Stage-bind rollback journal targets are not request-derived')

        assignment_path = self.root / '.local/two-site-refactor/assignments' / (
            str(record['old_assignment']) + '.md')
        raw = read_bounded(assignment_path, 1_000_000).decode()
        blocks = re.findall(r'```yaml\n(.*?)\n```', raw, re.S)
        assignment = yaml.safe_load(blocks[0]) if len(blocks) == 1 else None
        try:
            fence_api.validate_canonical_assignment(assignment)
            contract_digest = fence_api.immutable_contract_sha256(assignment)
        except Exception as error:
            raise TransportError('Stage-bind old Assignment contract is invalid') from error
        if (assignment.get('assignment_id') != record['old_assignment']
                or assignment.get('stage_id') != record['old_stage']
                or assignment.get('worktree_path') != record['old_worktree']
                or assignment.get('branch') != record['old_branch']
                or assignment.get('orchestrator') != 'ANTIGRAVITY'
                or assignment.get('orchestration_epoch') != record['epoch']
                or assignment.get('integration_branch') != pins.integration_branch
                or assignment.get('base_sha') != fence.get('assignment_base_sha')
                or assignment.get('integration_head_at_issue')
                != fence.get('assignment_integration_head_at_issue')
                or contract_digest != fence.get('immutable_contract_sha256')):
            raise TransportError('Stage-bind old Assignment/fence contract drifted')

        registry_path = self.root / '.local/two-site-refactor/worktrees.yaml'
        before_bytes = str(record['registry_before']).encode()
        after_bytes = str(record['registry_after']).encode()
        if (hashlib.sha256(before_bytes).hexdigest() != record['registry_before_sha256']
                or hashlib.sha256(after_bytes).hexdigest() != record['registry_after_sha256']):
            raise TransportError('Stage-bind rollback journal registry digests are invalid')
        before_registry = yaml.safe_load(before_bytes)
        after_registry = yaml.safe_load(after_bytes)
        if not isinstance(before_registry, dict) or not isinstance(after_registry, dict):
            raise TransportError('Stage-bind rollback journal registry snapshots are malformed')
        before_copy = json.loads(json.dumps(before_registry))
        after_copy = json.loads(json.dumps(after_registry))
        before_stages = before_copy.pop('stage_worktrees', None)
        after_stages = after_copy.pop('stage_worktrees', None)
        before_copy.pop('updated_at', None)
        after_updated = after_copy.pop('updated_at', None)
        if (before_copy != after_copy or not isinstance(before_stages, list)
                or not isinstance(after_stages, list)
                or after_stages[:-1] != before_stages
                or len(after_stages) != len(before_stages) + 1
                or after_updated != record['created_at']):
            raise TransportError('Stage-bind rollback registry snapshots have an invalid semantic delta')
        derived_record = after_stages[-1]
        expected_derived = {
            'registry_id': f"stage-{record['next_stage']}-{slug}",
            'stage_id': record['next_stage'], 'assignment_id': record['next_assignment'],
            'owner_role': 'EXECUTION_WORKER', 'path': str(derived_worktree),
            'branch': derived_branch, 'base_sha': record['integration_head'],
            'head_sha': record['integration_head'], 'locks': list(contract.get('locks', [])),
            'status': 'READY', 'orchestrator': 'ANTIGRAVITY',
            'orchestration_epoch': record['epoch'], 'created_at': record['created_at'],
        }
        if (not isinstance(derived_record, dict)
                or any(derived_record.get(key) != value
                       for key, value in expected_derived.items())):
            raise TransportError('Stage-bind rollback derived registry record is invalid')

        registry_bytes = read_bounded(registry_path, 1_000_000)
        if registry_bytes not in {before_bytes, after_bytes}:
            raise TransportError('Stage-bind rollback registry bytes differ from its journal')
        registry = yaml.safe_load(registry_bytes)
        integration = registry.get('integration') if isinstance(registry, dict) else None
        if (not isinstance(integration, dict)
                or integration.get('path') != str(self.root)
                or integration.get('branch') != pins.integration_branch
                or integration.get('head_sha') != record['integration_head']
                or integration.get('active_assignment_id') != record['old_assignment']
                or integration.get('owner_role') != 'EXECUTION_COORDINATOR'
                or integration.get('orchestrator') != 'ANTIGRAVITY'
                or integration.get('orchestration_epoch') != record['epoch']):
            raise TransportError('Stage-bind rollback registry integration identity drifted')
        old = self._validate_retirement_candidate(record, pins, registry)
        if (old.get('status') != 'COMPLETE_CANDIDATE'
                or old.get('owner_role') != 'EXECUTION_WORKER'
                or old.get('orchestrator') != 'ANTIGRAVITY'
                or old.get('orchestration_epoch') != record['epoch']
                or old.get('registry_id') != assignment.get('worktree_registry_id')
                or old.get('base_sha') != assignment.get('base_sha')
                or old.get('locks') != assignment.get('locks')):
            raise TransportError('Stage-bind rollback registry Stage ownership drifted')

    def _rollback_stage_bind_effects(
            self, record: Mapping[str, Any], pins: PinnedPaths,
            fence: Mapping[str, Any]) -> None:
        self._validate_stage_bind_rollback_authority(record, pins, fence)
        registry_path = self.root / '.local/two-site-refactor/worktrees.yaml'
        registry_bytes = read_bounded(registry_path, 1_000_000)
        registry_sha = hashlib.sha256(registry_bytes).hexdigest()
        if registry_sha not in {record['registry_before_sha256'], record['registry_after_sha256']}:
            raise TransportError('cannot rollback a Stage-bind registry with unknown drift')
        worktree = Path(str(record['next_worktree']))
        if worktree.exists():
            snapshot = observe_git(safe_path(worktree))
            if (not snapshot.clean or snapshot.branch != record['next_branch']
                    or snapshot.head_sha != record['integration_head']
                    or self._git_common_dir(pins, worktree) != self._git_common_dir(pins, self.root)):
                raise TransportError('cannot rollback a changed next Stage worktree')
            result = _run_bounded_process(
                [str(pins.git_binary), '-C', str(self.root), 'worktree', 'remove', str(worktree)],
                cwd=self.root, env={'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8',
                                    'GIT_TERMINAL_PROMPT': '0'}, timeout=120,
                output_limit=256_000,
            )
            if result.returncode != 0 or worktree.exists():
                raise TransportError('cannot rollback derived next Stage worktree')
        elif self._worktree_registered(pins, worktree):
            raise TransportError('missing next Stage path remains Git-registered')
        assignment_path = Path(str(record['assignment_path']))
        if assignment_path.exists():
            if file_sha256(safe_path(assignment_path)) != record['assignment_sha256']:
                raise TransportError('cannot rollback a changed next Assignment document')
            assignment_path.unlink()
        branch_check = _run_bounded_process(
            [str(pins.git_binary), '-C', str(self.root), 'rev-parse', '--verify',
             'refs/heads/' + str(record['next_branch'])],
            cwd=self.root, env={'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8',
                                'GIT_TERMINAL_PROMPT': '0'}, timeout=60, output_limit=64_000,
        )
        if branch_check.returncode == 0:
            if branch_check.stdout.decode().strip() != record['integration_head']:
                raise TransportError('cannot rollback a changed next Stage branch')
            deleted = _run_bounded_process(
                [str(pins.git_binary), '-C', str(self.root), 'branch', '-D',
                 str(record['next_branch'])], cwd=self.root,
                env={'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8',
                     'GIT_TERMINAL_PROMPT': '0'}, timeout=60, output_limit=64_000,
            )
            if deleted.returncode != 0:
                raise TransportError('cannot rollback derived next Stage branch')
        if registry_sha == record['registry_after_sha256']:
            before = str(record['registry_before']).encode()
            if hashlib.sha256(before).hexdigest() != record['registry_before_sha256']:
                raise TransportError('Stage-bind journal registry backup is invalid')
            self._atomic_project_write(registry_path, before)

    def _rollback_stage_bind(self, record: Mapping[str, Any], pins: PinnedPaths) -> None:
        """Rollback only while holding and revalidating the canonical fence lock."""
        with self._fence_locked():
            fence = unique_json(read_bounded(
                self.root / '.local/two-site-refactor/orchestrator-fence.json', 65536))
            self._rollback_stage_bind_effects(record, pins, fence)

    def _recover_stage_bind_transaction(self) -> None:
        journal_path = self._stage_bind_journal_path
        if not journal_path.exists():
            return
        record = unique_json(read_bounded(journal_path, 4_000_000))
        running, request, _ = self._stage_bind_inputs(record)
        receipt_path = self.state / (str(record['request_id']) + '.receipt.json')
        if receipt_path.exists():
            receipt = unique_json(read_bounded(receipt_path, MAX_RESULT))
            if (receipt.get('request_id') != record['request_id']
                    or receipt.get('authority') != 'PROTECTED_CONTROLLER_RECEIPT'
                    or receipt.get('outcome', {}).get('status') not in {'SUCCESS', 'FAILED', 'BLOCKED'}):
                raise TransportError('Stage-bind journal has a nonterminal protected receipt')
            public = self.root / '.local/two-site-refactor/gateway/results' / (
                str(record['request_id']) + '.json')
            if public.exists():
                if unique_json(read_bounded(public, MAX_RESULT)) != receipt:
                    raise TransportError('terminal Stage-bind public/protected receipts differ')
            else:
                write_new(public, receipt, MAX_RESULT)
            journal_path.unlink()
            return
        pins = self._journal_pins(record)
        with self._fence_locked():
            fence = unique_json(read_bounded(
                self.root / '.local/two-site-refactor/orchestrator-fence.json', 65536))
            self._validate_stage_bind_fence_identity(record, pins, fence)
            old_active = (fence.get('status') == 'ACTIVE'
                          and fence.get('active_assignment_id') == record['old_assignment'])
            new_active = (fence.get('status') == 'ACTIVE'
                          and fence.get('binding_kind') == 'STAGE'
                          and fence.get('previous_assignment_id') == record['old_assignment']
                          and fence.get('active_assignment_id') == record['next_assignment'])
            reconciling = (fence.get('status') == 'RECONCILING'
                           and fence.get('binding_kind') == 'STAGE'
                           and fence.get('previous_assignment_id') == record['old_assignment']
                           and fence.get('active_assignment_id') == record['next_assignment'])
            if old_active:
                self._rollback_stage_bind_effects(record, pins, fence)
                self._finish_recovered_stage_bind(
                    record, running, request, 'FAILED',
                    'protected recovery rolled back an interrupted pre-bind transaction')
                journal_path.unlink()
                return
            if new_active:
                self._validate_bound_stage(record, pins)
                self._retire_bound_worktree(record, pins)
                self._finish_recovered_stage_bind(
                    record, running, request, 'SUCCESS',
                    'protected recovery verified an already-completed exact Stage bind')
                journal_path.unlink()
                return
            if not reconciling:
                raise TransportError('Stage-bind recovery fence is outside rollback/roll-forward states')

        # The canonical fence program acquires the same lock and independently
        # revalidates the complete RECONCILING state before any roll-forward write.
        result = _run_bounded_process(
            [str(pins.python_binary), '-I', str(pins.fence_script), 'stage-bind',
             '--platform', 'ANTIGRAVITY', '--instance', self.manifest['coordinator_id'],
             '--epoch', str(record['epoch']), '--integration-head', record['integration_head'],
             '--assignment', record['old_assignment'], '--next-assignment', record['next_assignment']],
            cwd=self.root, env={'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8',
                                'GIT_TERMINAL_PROMPT': '0'}, timeout=120,
            output_limit=256_000,
        )
        if result.returncode != 0:
            raise TransportError('protected Stage-bind reconciliation did not complete')
        with self._fence_locked():
            fence = unique_json(read_bounded(
                self.root / '.local/two-site-refactor/orchestrator-fence.json', 65536))
            self._validate_stage_bind_fence_identity(record, pins, fence)
            if (fence.get('status') != 'ACTIVE' or fence.get('binding_kind') != 'STAGE'
                    or fence.get('previous_assignment_id') != record['old_assignment']
                    or fence.get('active_assignment_id') != record['next_assignment']):
                raise TransportError('reconciled Stage-bind fence is not the exact new owner')
            self._validate_bound_stage(record, pins)
            self._retire_bound_worktree(record, pins)
            self._finish_recovered_stage_bind(
                record, running, request, 'SUCCESS',
                'protected recovery completed an exact RECONCILING Stage bind')
            journal_path.unlink()

    def _prepare_stage_bind(self, plan: OperationPlan, request: Mapping[str, Any],
                            assignment: dict, pins: PinnedPaths) -> dict:
        bindings = dict(plan.bindings)
        contract = request['contract']
        if (not isinstance(contract, dict)
                or self._contract_digest(contract) != bindings.get('contract_sha256')):
            raise TransportError('next Assignment contract changed after protected planning')
        next_stage, next_assignment = str(request['next_stage']), str(request['next_assignment'])
        slug = str(contract['slug'])
        if not next_assignment.startswith('WA-' + next_stage.upper() + '-'):
            raise TransportError('next Assignment ID is not namespaced to its Stage')
        workspace_root = safe_path(Path(self.manifest['stage_workspace_root']))
        worktree = safe_path(workspace_root / f'{next_stage}-{slug}', exists=False)
        branch = f'refactor/stage/{next_stage}-{slug}'
        registry_id = f'stage-{next_stage}-{slug}'
        assignment_path = self.root / '.local/two-site-refactor/assignments' / (next_assignment + '.md')
        if self._stage_bind_journal_path.exists():
            raise TransportError('another Stage-bind transaction requires protected recovery')
        if assignment_path.exists() or worktree.exists():
            raise TransportError('next Assignment or worktree already exists; protected recovery is required')
        registry_path = self.root / '.local/two-site-refactor/worktrees.yaml'
        registry_original_bytes = read_bounded(registry_path, 1_000_000)
        registry = yaml.safe_load(registry_original_bytes)
        if not isinstance(registry, dict) or not isinstance(registry.get('stage_worktrees'), list):
            raise TransportError('worktree registry is malformed')
        if any(item.get('assignment_id') == next_assignment or item.get('path') == str(worktree)
               or item.get('branch') == branch for item in registry['stage_worktrees']
               if isinstance(item, dict)):
            raise TransportError('next Assignment identifiers already exist in registry')
        if any(item.get('status') not in {'CLOSED'} and item.get('path') == str(worktree)
               for item in registry['stage_worktrees'] if isinstance(item, dict)):
            raise TransportError('next Stage worktree collides with an active record')
        branch_check = _run_bounded_process(
            [str(pins.git_binary), '-C', str(self.root), 'show-ref', '--verify', '--quiet',
             'refs/heads/' + branch], cwd=self.root,
            env={'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8',
                 'GIT_TERMINAL_PROMPT': '0'}, timeout=60, output_limit=64_000,
        )
        if branch_check.returncode == 0:
            raise TransportError('next Stage branch already exists')
        if branch_check.returncode != 1:
            raise TransportError('cannot prove next Stage branch absence')
        created_at = datetime.now(timezone.utc)
        limits = registry.get('limits', {})
        maximum = limits.get('max_ephemeral_stage_worktrees', 2) if isinstance(limits, dict) else None
        if type(maximum) is not int or not 1 <= maximum <= 2:
            raise TransportError('worktree registry has an invalid ephemeral limit')
        present = [item for item in registry['stage_worktrees']
                   if isinstance(item, dict) and isinstance(item.get('path'), str)
                   and Path(item['path']).is_dir()]
        if len(present) >= maximum:
            # A temporary +1 is allowed only while this journaled bind immediately
            # replaces and retires the exact current COMPLETE Stage worktree.
            if len(present) > maximum:
                raise TransportError('ephemeral worktree registry already exceeds its limit')
        canonical = {
            'assignment_id': next_assignment, 'stage_id': next_stage, 'role': 'WORKER',
            'status': 'READY', 'orchestrator': 'ANTIGRAVITY',
            'orchestration_epoch': int(request['epoch']), 'supersedes': None,
            'base_sha': request['integration_head'], 'branch': branch,
            'worktree_path': str(worktree), 'worktree_registry_id': registry_id,
            'allowed_paths': list(contract['allowed_paths']),
            'forbidden_paths': list(contract['forbidden_paths']),
            'locks': list(contract['locks']), 'parallel_peer': None,
            'deliverables': list(contract['deliverables']), 'tests': list(contract['tests']),
            'failure_tests': list(contract['failure_tests']),
            'rollback_test': contract['rollback_test'], 'external_access': 'NONE',
            'expires_at': (created_at + timedelta(days=7)).isoformat().replace('+00:00', 'Z'),
            'issued_by': 'EXECUTION_COORDINATOR',
            'issued_at': created_at.isoformat().replace('+00:00', 'Z'),
            'issued_from_worktree': str(self.root),
            'integration_branch': pins.integration_branch,
            'integration_head_at_issue': request['integration_head'],
        }
        try:
            from scripts import two_site_orchestrator_fence as fence_api
            fence_api.validate_canonical_assignment(canonical)
        except Exception as error:
            raise TransportError('generated next Assignment is not canonical') from error
        document = (
            f"# Work Assignment — `{next_assignment}`\n\n"
            f"```yaml\n{yaml.safe_dump(canonical, sort_keys=False).rstrip()}\n```\n\n"
            "## Scope notes\n\n" + str(contract['scope_notes']).rstrip() + "\n\n"
            "Generated by the protected two-site Coordinator gateway. External access is NONE.\n"
        )
        new_record = {
            'registry_id': registry_id, 'stage_id': next_stage,
            'assignment_id': next_assignment, 'owner_role': 'EXECUTION_WORKER',
            'path': str(worktree), 'branch': branch, 'base_sha': request['integration_head'],
            'head_sha': request['integration_head'], 'dirty_uncommitted_candidate': False,
            'locks': list(contract['locks']), 'status': 'READY', 'orchestrator': 'ANTIGRAVITY',
            'orchestration_epoch': int(request['epoch']),
            'created_at': created_at.isoformat().replace('+00:00', 'Z'),
            'expires_at': canonical['expires_at'], 'remove_within_hours_after_close': 24,
        }
        registry_after = json.loads(json.dumps(registry))
        registry_after['stage_worktrees'].append(new_record)
        registry_after['updated_at'] = created_at.isoformat().replace('+00:00', 'Z')
        registry_before_bytes = registry_original_bytes
        registry_after_bytes = yaml.safe_dump(registry_after, sort_keys=False).encode()
        journal = {
            'schema_version': '1.0', 'phase': 'STARTED',
            'request_id': plan.request_id, 'request_sha256': digest(request),
            'plan_sha256': digest(plan), 'old_assignment': assignment['assignment_id'],
            'old_stage': assignment['stage_id'], 'old_worktree': assignment['worktree_path'],
            'old_branch': assignment['branch'], 'old_head': observe_git(pins.stage_worktree).head_sha,
            'next_assignment': next_assignment, 'next_stage': next_stage,
            'next_worktree': str(worktree), 'next_branch': branch,
            'integration_head': request['integration_head'], 'epoch': int(request['epoch']),
            'assignment_path': str(assignment_path),
            'assignment_sha256': hashlib.sha256(document.encode()).hexdigest(),
            'registry_before': registry_before_bytes.decode(),
            'registry_before_sha256': hashlib.sha256(registry_before_bytes).hexdigest(),
            'registry_after': registry_after_bytes.decode(),
            'registry_after_sha256': hashlib.sha256(registry_after_bytes).hexdigest(),
            'created_at': created_at.isoformat().replace('+00:00', 'Z'),
        }
        self._validate_retirement_candidate(journal, pins, registry)
        write_new(self._stage_bind_journal_path, journal, 4_000_000)
        result = _run_bounded_process(
            [str(pins.git_binary), '-C', str(self.root), 'worktree', 'add', '-b', branch,
             str(worktree), request['integration_head']],
            cwd=self.root, env={'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8',
                                'GIT_TERMINAL_PROMPT': '0'}, timeout=120, output_limit=256_000,
        )
        if result.returncode != 0:
            raise TransportError('fixed next Stage worktree creation failed')
        journal = self._write_stage_bind_journal(journal, 'WORKTREE_CREATED')
        self._atomic_project_write(assignment_path, document.encode())
        journal = self._write_stage_bind_journal(journal, 'ASSIGNMENT_WRITTEN')
        self._atomic_project_write(registry_path, registry_after_bytes)
        self._write_stage_bind_journal(journal, 'REGISTRY_WRITTEN')
        if observe_git(worktree).head_sha != request['integration_head'] or not observe_git(worktree).clean:
            raise TransportError('new registered Stage worktree does not match exact integration HEAD')
        return {'next_assignment': next_assignment, 'next_stage': next_stage,
                'worktree_path': str(worktree), 'branch': branch,
                'assignment_contract_sha256': self._contract_digest(canonical)}

    def _authority_record(self, plan: OperationPlan, request: Mapping[str, Any], outcome: dict,
                          assignment: dict, fence: dict) -> dict | None:
        if outcome.get('status') != 'SUCCESS':
            return None
        common = {
            'schema_version': '1.0', 'operation': plan.operation,
            'assignment_id': assignment['assignment_id'], 'stage_id': assignment['stage_id'],
            'epoch': fence['epoch'], 'request_id': plan.request_id,
            'actor_id': plan.actor_id, 'role': plan.role.value,
            'integration_head': request['integration_head'],
        }
        if plan.operation == 'test':
            stage = self._snapshot(outcome, 'pre_git', 1)
            return {**common, 'record_type': 'TEST', 'decision': 'PASS',
                    'stage_head': stage['head_sha'],
                    'worktree_patch_sha256': stage.get('worktree_patch_sha256'),
                    'selectors': list(request['selectors'])}
        if plan.operation == 'worker-commit':
            before = self._snapshot(outcome, 'pre_git', 1)
            changed = sorted(set(before['staged_paths'] + before['unstaged_paths'] + before['untracked_paths']))
            return {**common, 'record_type': 'WORKER_CANDIDATE', 'decision': 'CANDIDATE',
                    'parent_sha': before['head_sha'], 'candidate_sha': outcome['resulting_head'],
                    'changed_paths': changed, 'integration_patch_sha256': request['expected_patch_sha256'],
                    'test_selectors': self._selectors_for_authority(request['test_receipt_sha256'], 'WORKER'),
                    'test_authority_sha256': request['test_receipt_sha256']}
        if plan.operation == 'verifier-submit':
            worker = self._worker_for_candidate(request['candidate_sha'])
            selectors = self._selectors_for_authority(
                request['test_receipt_sha256'], 'VERIFIER')
            if selectors != list(request['test_selectors']):
                raise TransportError('Verifier submission selectors differ from its passing test authority')
            return {**common, 'record_type': 'VERIFIER', 'decision': request['decision'],
                    'candidate_sha': request['candidate_sha'],
                    'test_selectors': selectors,
                    'test_authority_sha256': request['test_receipt_sha256'],
                    'worker_authority_sha256': worker['authority_sha256'],
                    'findings': list(request['findings'])}
        if plan.operation == 'integrate':
            before = self._snapshot(outcome, 'pre_git', 0)
            return {**common, 'record_type': 'INTEGRATION', 'decision': 'INTEGRATED',
                    'base_sha': assignment['integration_head_at_issue'],
                    'prior_head_sha': before['head_sha'], 'head_sha': outcome['resulting_head'],
                    'candidate_sha': request['candidate_sha'],
                    'patch_sha256': request['expected_patch_sha256'],
                    'verifier_authority_sha256': request['verifier_receipt_sha256']}
        bundle = f".local/two-site-refactor/review-bundles/{assignment['stage_id']}-{assignment['assignment_id']}"
        if plan.operation == 'review-request':
            relative = bundle + '/CODEX_FINAL_REVIEW.json'
            receipt, receipt_sha = self._validated_review_receipt(
                relative, 'docs/refactor/two-server/templates/final-review.schema.json')
            review_output = outcome.get('review_output')
            self._validate_review_decision(receipt, closeout=False)
            if (not isinstance(review_output, dict)
                    or review_output.get('receipt_path') != relative
                    or review_output.get('receipt_file_sha256') != receipt_sha
                    or receipt.get('stage_id') != assignment['stage_id']
                    or receipt.get('assignment_id') != assignment['assignment_id']
                    or receipt.get('integration_base_sha') != request['base_sha']
                    or receipt.get('reviewed_head_sha') != request['head_sha']):
                raise TransportError('Codex final receipt identity differs from the request')
            return {**common, 'record_type': 'CODEX_FINAL', 'decision': receipt['verdict'],
                    'base_sha': request['base_sha'], 'head_sha': request['head_sha'],
                    'receipt_path': relative, 'receipt_file_sha256': receipt_sha}
        if plan.operation == 'closeout-review':
            relative = bundle + '/CODEX_CLOSEOUT_PREACTION.json'
            receipt, receipt_sha = self._validated_review_receipt(
                relative, 'docs/refactor/two-server/templates/closeout-review.schema.json')
            review_output = outcome.get('review_output')
            self._validate_review_decision(receipt, closeout=True)
            if (not isinstance(review_output, dict)
                    or review_output.get('receipt_path') != relative
                    or review_output.get('receipt_file_sha256') != receipt_sha
                    or receipt.get('stage_id') != assignment['stage_id']
                    or receipt.get('assignment_id') != assignment['assignment_id']
                    or receipt.get('accepted_stage_sha') != request['accepted_head']):
                raise TransportError('Codex closeout receipt identity differs from the request')
            return {**common, 'record_type': 'CODEX_CLOSEOUT_PREACTION',
                    'decision': receipt['verdict'], 'base_sha': assignment['base_sha'],
                    'head_sha': request['accepted_head'],
                    'patch_sha256': receipt['proposed_patch_sha256'],
                    'receipt_path': relative, 'receipt_file_sha256': receipt_sha}
        if plan.operation == 'closeout-commit':
            return {**common, 'record_type': 'CLOSEOUT_COMMIT', 'decision': 'VERIFIED',
                    'base_sha': request['accepted_head'], 'head_sha': outcome['resulting_head'],
                    'patch_sha256': request['expected_patch_sha256'],
                    'closeout_authority_sha256': request['closeout_receipt_sha256']}
        if plan.operation == 'next-stage-prepare':
            prepared = outcome.get('prepared')
            if not isinstance(prepared, dict):
                raise TransportError('next Stage prepare lacks protected patch evidence')
            return {**common, 'record_type': 'NEXT_STAGE_PREPARE', 'decision': 'PREPARED',
                    'head_sha': request['integration_head'], 'next_stage_id': request['next_stage'],
                    'patch_sha256': prepared['patch_sha256'],
                    'closeout_authority_sha256': request['closeout_commit_receipt_sha256']}
        if plan.operation == 'next-stage-commit':
            return {**common, 'record_type': 'NEXT_STAGE_COMMIT', 'decision': 'VERIFIED',
                    'base_sha': request['integration_head'], 'head_sha': outcome['resulting_head'],
                    'next_stage_id': request['next_stage'],
                    'patch_sha256': request['expected_patch_sha256'],
                    'prepare_authority_sha256': request['prepare_receipt_sha256']}
        return None

    def _records(self) -> list[dict]:
        fence, _, assignment = self.builder.active_assignment()
        return self.builder._transport_authorities(assignment, fence)

    def _worker_for_candidate(self, candidate_sha: str) -> dict:
        matches = [item for item in self._records()
                   if item['record'].get('record_type') == 'WORKER_CANDIDATE'
                   and item['record'].get('candidate_sha') == candidate_sha]
        if len(matches) != 1:
            raise TransportError('Verifier submission lacks one exact Worker candidate')
        return matches[0]

    def _selectors_for_authority(self, authority_sha: str, role: str) -> list[str]:
        matches = [item['record'] for item in self._records()
                   if item['authority_sha256'] == authority_sha
                   and item['record'].get('record_type') == 'TEST'
                   and item['record'].get('role') == role]
        if len(matches) != 1:
            raise TransportError(role + ' commit lacks one exact passing test authority')
        return list(matches[0]['selectors'])

    def _execute(self, plan: OperationPlan, request: Mapping[str, Any]) -> dict:
        self.builder.validate_installation()
        pins, fence, assignment = self._pins()
        if any(command.env_class is EnvClass.HERMETIC_TEST for command in plan.commands):
            self._binary('bwrap')
            self._binary('prlimit')
        if plan.operation == 'next-stage-bind':
            try:
                with self._fence_locked():
                    assignment = self._validate_host_prepare(plan, request, pins)
                    prepared_bind = self._prepare_stage_bind(plan, request, assignment, pins)
            except Exception:
                if self._stage_bind_journal_path.exists():
                    journal = unique_json(read_bounded(self._stage_bind_journal_path, 4_000_000))
                    self._stage_bind_inputs(journal)
                    self._rollback_stage_bind(journal, pins)
                    self._stage_bind_journal_path.unlink()
                    return {
                        'status': 'FAILED', 'request_id': plan.request_id,
                        'operation': plan.operation,
                        'reason': 'journaled Stage-bind preparation was rolled back',
                        'authority': 'EXECUTOR_VERIFIED_NO_APPROVAL',
                    }
                # Every Stage-bind side effect is journal-after-write forbidden:
                # absence of the journal proves this rejection occurred entirely
                # in read-only prevalidation, so it is terminal, not indeterminate.
                return {
                    'status': 'BLOCKED', 'request_id': plan.request_id,
                    'operation': plan.operation,
                    'reason': 'Stage-bind read-only prevalidation rejected the request',
                    'authority': 'EXECUTOR_VERIFIED_NO_APPROVAL',
                }
        else:
            prepared_bind = None
        before = lambda boundary: self._before_mutation(boundary, plan, request, pins)
        outcome = execute_plan(
            plan, request, runner=self._runner(pins, assignment), pinned_paths=pins,
            before_mutation=before, review_supervisor=self._review_supervisor,
        )
        if outcome.get('status') == 'SUCCESS' and plan.operation == 'bundle-prepare':
            with self._fence_locked():
                assignment = self._validate_host_prepare(plan, request, pins)
                prepared = self._prepare_bundle(plan, request, assignment)
                if observe_git(self.root).head_sha != request['integration_head']:
                    raise TransportError('integration HEAD changed while preparing the review bundle')
            outcome = {**outcome, 'prepared': prepared}
        if outcome.get('status') == 'SUCCESS' and plan.operation == 'closeout-prepare':
            with self._fence_locked():
                assignment = self._validate_host_prepare(plan, request, pins)
                prepared = self._prepare_closeout(plan, request, assignment, pins)
            outcome = {**outcome, 'prepared': prepared}
        if outcome.get('status') == 'SUCCESS' and plan.operation == 'next-stage-prepare':
            with self._fence_locked():
                assignment = self._validate_host_prepare(plan, request, pins)
                prepared = self._prepare_next_stage_transition(request, assignment, pins)
            outcome = {**outcome, 'prepared': prepared}
        if plan.operation == 'next-stage-bind':
            journal = unique_json(read_bounded(self._stage_bind_journal_path, 4_000_000))
            self._stage_bind_inputs(journal)
            current_fence = unique_json(read_bounded(
                self.root / '.local/two-site-refactor/orchestrator-fence.json', 65536))
            if outcome.get('status') != 'SUCCESS':
                if (current_fence.get('status') == 'ACTIVE'
                        and current_fence.get('active_assignment_id') == journal['old_assignment']):
                    # The helper reacquires the canonical fence lock and revalidates
                    # fence, Coordinator, Assignment, registry and actual Git before
                    # performing any removal or restoration.
                    self._rollback_stage_bind(journal, pins)
                    self._stage_bind_journal_path.unlink()
                    return outcome
                if (current_fence.get('status') == 'RECONCILING'
                        and current_fence.get('binding_kind') == 'STAGE'):
                    retry = _run_bounded_process(
                        [str(pins.python_binary), '-I', str(pins.fence_script), 'stage-bind',
                         '--platform', 'ANTIGRAVITY', '--instance', self.manifest['coordinator_id'],
                         '--epoch', str(journal['epoch']), '--integration-head', journal['integration_head'],
                         '--assignment', journal['old_assignment'],
                         '--next-assignment', journal['next_assignment']],
                        cwd=self.root,
                        env={'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8',
                             'GIT_TERMINAL_PROMPT': '0'}, timeout=120, output_limit=256_000,
                    )
                    if retry.returncode != 0:
                        raise TransportError('journaled Stage-bind retry did not reconcile')
                outcome = {
                    'status': 'SUCCESS', 'request_id': plan.request_id,
                    'operation': plan.operation,
                    'reason': 'journaled Stage bind completed by exact recovery',
                    'authority': 'EXECUTOR_VERIFIED_NO_APPROVAL',
                }
            with self._fence_locked():
                self._validate_bound_stage(journal, pins)
                journal = self._write_stage_bind_journal(journal, 'FENCE_BOUND')
                self._retire_bound_worktree(journal, pins)
                self._write_stage_bind_journal(journal, 'COMPLETE')
            outcome = {**outcome, 'prepared': prepared_bind}
        record = self._authority_record(plan, request, outcome, assignment, fence)
        if record is not None:
            outcome = {**outcome, 'authority_record': record, 'authority_sha256': digest(record)}
        return outcome

    def stop(self, event: dict) -> dict:
        self.builder.validate_installation()
        fence, _, assignment = self.builder.active_assignment()
        self.builder.actor(event, assignment, fence)
        with self.transport.locked():
            self.transport.admission_check()
            if self.controller.reservation.exists():
                return {'continue': False, 'stopReason': 'typed gateway has a pending source mutation'}
        return {}


def load_manifest(path: Path = MANIFEST_PATH) -> dict:
    return unique_json(read_bounded(safe_path(path), 1_000_000))


def main(argv: list[str] | None = None) -> int:
    values = sys.argv[1:] if argv is None else argv
    if len(values) != 1 or values[0] not in {'pre', 'post', 'stop'}:
        return 64
    phase = values[0]
    try:
        with operation_deadline(hook_deadline_seconds(phase)):
            raw = sys.stdin.buffer.read(MAX_EVENT + 1)
            if len(raw) > MAX_EVENT:
                raise TransportError('native hook event exceeds its budget')
            event = unique_json(raw)
            if not isinstance(event, dict):
                raise TransportError('native hook event must be an object')
            host = ProtectedGatewayHost(load_manifest())
            if phase == 'stop':
                response = host.stop(event)
            else:
                response = hook_response(host.controller, phase, event)
    except Exception as error:
        response = ({'decision': 'deny', 'reason': 'typed gateway unavailable: ' + type(error).__name__}
                    if phase == 'pre' else
                    ({'continue': False, 'stopReason': 'typed gateway cannot prove a safe stop'}
                     if phase == 'stop' else {}))
    sys.stdout.write(json.dumps(response, sort_keys=True, separators=(',', ':')) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
