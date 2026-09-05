"""Protected runtime identity and canonical context for the typed gateway.

No authority comes from request JSON. A protected installation manifest, real CLI
metadata, actual Git and controller-written receipt attestations supply the context.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Callable

import yaml
import jsonschema

from scripts import two_site_antigravity_session as sessions
from scripts import two_site_orchestrator_fence as fence_api
from scripts.two_site_agent_gateway import (
    ActorBinding, ActorRole, CandidateBinding, CommitGuard, GatewayContext,
    ReceiptBinding, ReceiptKind, ReviewBinding,
)
from scripts.two_site_agent_transport import (
    MODEL, UUID, TransportError, digest, read_bounded, safe_path, unique_json, write_new,
)

ROLE_NAMES = {'COORDINATOR': 'two-site-coordinator', 'WORKER': 'two-site-stage-worker',
              'VERIFIER': 'two-site-stage-verifier'}
ROLE_LABELS = {'WORKER': 'Stage Worker', 'VERIFIER': 'Stage Verifier'}
PROTECTED_SOURCE = (
    '.agents/hooks.json',
    '.agents/agents/two-site-coordinator/agent.md',
    '.agents/agents/two-site-stage-worker/agent.md',
    '.agents/agents/two-site-stage-verifier/agent.md',
    '.agents/skills/two-site-refactor/SKILL.md',
    '.agents/skills/two-site-refactor/resources/TYPED_GATEWAY.md',
    'scripts/two_site_agent_transport.py', 'scripts/two_site_agent_gateway.py',
    'scripts/two_site_agent_context.py', 'scripts/two_site_agent_executor.py',
    'scripts/two_site_agent_controller.py', 'scripts/two_site_agent_host.py',
    'scripts/two_site_agent_recovery.py',
    'scripts/install_two_site_agent_gateway.py', 'scripts/two_site_agent_sandbox.py',
    'scripts/two_site_antigravity_session.py', 'scripts/antigravity_cli_1_1_26_profile.json',
    'scripts/two_site_orchestrator_fence.py', 'scripts/validate_antigravity_two_site_adapter.py',
    'scripts/two_site_codex_final_review.sh', 'scripts/two_site_codex_closeout_review.sh',
    'scripts/two_site_codex_governance_review.sh',
    'docs/refactor/two-server/templates/final-review.schema.json',
    'docs/refactor/two-server/templates/closeout-review.schema.json',
    'docs/refactor/two-server/templates/governance-review.schema.json',
)

AUTHORITY_TYPES = {
    'TEST', 'WORKER_CANDIDATE', 'VERIFIER', 'INTEGRATION',
    'CODEX_FINAL', 'CODEX_CLOSEOUT_PREACTION', 'CLOSEOUT_COMMIT',
    'NEXT_STAGE_PREPARE', 'NEXT_STAGE_COMMIT',
}
AUTHORITY_COMMON_KEYS = {
    'schema_version', 'record_type', 'operation', 'assignment_id', 'stage_id', 'epoch',
    'request_id', 'actor_id', 'role', 'integration_head', 'decision',
}
AUTHORITY_EXTRA_KEYS = {
    'TEST': {'stage_head', 'worktree_patch_sha256', 'selectors'},
    'WORKER_CANDIDATE': {'parent_sha', 'candidate_sha', 'changed_paths',
                         'integration_patch_sha256', 'test_selectors', 'test_authority_sha256'},
    'VERIFIER': {'candidate_sha', 'test_selectors', 'test_authority_sha256',
                 'worker_authority_sha256', 'findings'},
    'INTEGRATION': {'base_sha', 'prior_head_sha', 'head_sha', 'candidate_sha', 'patch_sha256',
                    'verifier_authority_sha256'},
    'CODEX_FINAL': {'base_sha', 'head_sha', 'receipt_path', 'receipt_file_sha256'},
    'CODEX_CLOSEOUT_PREACTION': {'base_sha', 'head_sha', 'patch_sha256',
                                 'receipt_path', 'receipt_file_sha256'},
    'CLOSEOUT_COMMIT': {'base_sha', 'head_sha', 'patch_sha256',
                        'closeout_authority_sha256'},
    'NEXT_STAGE_PREPARE': {'head_sha', 'next_stage_id', 'patch_sha256',
                           'closeout_authority_sha256'},
    'NEXT_STAGE_COMMIT': {'base_sha', 'head_sha', 'next_stage_id', 'patch_sha256',
                          'prepare_authority_sha256'},
}
AUTHORITY_OPERATION = {
    'TEST': 'test', 'WORKER_CANDIDATE': 'worker-commit', 'VERIFIER': 'verifier-submit',
    'INTEGRATION': 'integrate', 'CODEX_FINAL': 'review-request',
    'CODEX_CLOSEOUT_PREACTION': 'closeout-review', 'CLOSEOUT_COMMIT': 'closeout-commit',
    'NEXT_STAGE_PREPARE': 'next-stage-prepare', 'NEXT_STAGE_COMMIT': 'next-stage-commit',
}


class ContextBuilder:
    def __init__(self, root: Path, state: Path, manifest: dict, observe: Callable,
                 *, runtime_binding=sessions.binding, clock=time.time):
        self.root, self.state = safe_path(root), safe_path(state)
        self.manifest, self.observe = manifest, observe
        self.runtime_binding, self.clock = runtime_binding, clock

    def validate_installation(self) -> None:
        expected = {'schema_version', 'root', 'stage_workspace_root', 'workspaces',
                    'coordinator_id', 'accepted_sha',
                    'source_sha256', 'governance_receipt_sha256', 'pre_commit_sha256',
                    'main_memory_guard_sha256', 'binary_sha256', 'mode'}
        m = self.manifest
        if (not isinstance(m, dict) or set(m) != expected or m['schema_version'] != '1.0'
                or m['root'] != str(self.root) or m['mode'] != 'ACTIVE'
                or not isinstance(m['coordinator_id'], str) or not UUID.fullmatch(m['coordinator_id'])
                or not isinstance(m['stage_workspace_root'], str)
                or not isinstance(m['workspaces'], list)
                or m['workspaces'] != [str(self.root), m['stage_workspace_root']]
                or not isinstance(m['source_sha256'], dict) or set(m['source_sha256']) != set(PROTECTED_SOURCE)
                or not isinstance(m['binary_sha256'], dict)
                or set(m['binary_sha256']) != {'git', 'python', 'bwrap', 'prlimit', 'codex', 'agy'}):
            raise TransportError('installation is not an active reviewed configuration')
        if len(set(m['workspaces'])) != len(m['workspaces']):
            raise TransportError('duplicate configured workspace')
        for value in m['workspaces']:
            if not isinstance(value, str):
                raise TransportError('invalid workspace path type')
            safe_path(Path(value))
        stage_workspace = safe_path(Path(m['stage_workspace_root']))
        if stage_workspace == self.root or stage_workspace.is_relative_to(self.root):
            raise TransportError('dynamic Stage workspace root overlaps the integration repository')
        for key in ('accepted_sha', 'governance_receipt_sha256', 'pre_commit_sha256',
                    'main_memory_guard_sha256'):
            length = 40 if key == 'accepted_sha' else 64
            if not isinstance(m[key], str) or not re.fullmatch('[0-9a-f]{' + str(length) + '}', m[key]):
                raise TransportError('invalid installation hash')
        for name, record in m['binary_sha256'].items():
            if (not isinstance(record, dict) or set(record) != {'path', 'sha256'}
                    or not isinstance(record['path'], str) or not Path(record['path']).is_absolute()
                    or not isinstance(record['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', record['sha256'])):
                raise TransportError('invalid protected binary pin: ' + name)
            binary = safe_path(Path(record['path']))
            if not binary.is_file():
                raise TransportError('protected binary missing: ' + name)
        for relative, expected_digest in m['source_sha256'].items():
            actual = hashlib.sha256(read_bounded(self.root / relative, 2_000_000)).hexdigest()
            if actual != expected_digest:
                raise TransportError('reviewed controller/role source has changed')
        receipt_bytes = read_bounded(self.state / 'governance-receipt.json', 1_000_000)
        if hashlib.sha256(receipt_bytes).hexdigest() != m['governance_receipt_sha256']:
            raise TransportError('protected governance receipt changed')
        receipt = unique_json(receipt_bytes)
        schema = unique_json(read_bounded(self.root / 'docs/refactor/two-server/templates/governance-review.schema.json', 65536))
        try:
            jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(receipt)
        except jsonschema.ValidationError as error:
            raise TransportError('invalid governance receipt schema') from error
        if (receipt.get('reviewer_authority') != 'CODEX_FINAL_REVIEWER'
                or receipt.get('receipt_type') != 'EXECUTION_GOVERNANCE_AMENDMENT'
                or receipt.get('verdict') != 'APPROVE'
                or receipt.get('reviewed_head_sha') != m['accepted_sha']
                or receipt.get('decision', {}).get('accepted_commit') != m['accepted_sha']
                or receipt.get('scope', {}).get('unauthorized_external_mutation') != 'NONE'
                or receipt['scope']['changed_paths_within_scope'] is not True
                or receipt['scope']['stage_state_unchanged'] is not True
                or receipt['decision']['stage_completion_granted'] is not False
                or receipt['decision']['conditions']
                or not receipt['verification']
                or any(item['result'] != 'PASS' for item in receipt['verification'])
                or any(item.get('severity') in {'HIGH', 'CRITICAL'} for item in receipt.get('findings', []))):
            raise TransportError('installation lacks exact-SHA Codex acceptance')

    def active_assignment(self) -> tuple[dict, dict, dict]:
        local = self.root / '.local/two-site-refactor'
        fence = unique_json(read_bounded(local / 'orchestrator-fence.json', 65536))
        fence_api.validate_fence(fence)
        if (fence['status'] != 'ACTIVE' or fence['platform'] != 'ANTIGRAVITY'
                or fence['coordinator_instance_id'] != self.manifest['coordinator_id']):
            raise TransportError('canonical fence is not owned by this Coordinator')
        registry = yaml.safe_load(read_bounded(local / 'worktrees.yaml', 1_000_000))
        assignment_id = fence['active_assignment_id']
        if not isinstance(assignment_id, str) or not re.fullmatch(fence_api.ASSIGNMENT_ID_PATTERN, assignment_id):
            raise TransportError('invalid active Assignment ID')
        # Parse the same canonical YAML block, then use the shared contract validator.
        raw = read_bounded(local / 'assignments' / (assignment_id + '.md'), 1_000_000).decode()
        blocks = re.findall(r'```yaml\n(.*?)\n```', raw, re.S)
        if len(blocks) != 1:
            raise TransportError('ambiguous Assignment contract')
        assignment = yaml.safe_load(blocks[0])
        fence_api.validate_canonical_assignment(assignment)
        if (assignment.get('assignment_id') != assignment_id or assignment.get('external_access') != 'NONE'
                or fence_api.immutable_contract_sha256(assignment) != fence['immutable_contract_sha256']):
            raise TransportError('Assignment scope/authority changed')
        return fence, registry, assignment

    def _binding(self, actor: str, role: str) -> dict:
        name = ROLE_NAMES[role]
        definition = read_bounded(self.root / '.agents/agents' / name / 'agent.md', 150_000).decode()
        metadata = yaml.safe_load(definition.split('---', 2)[1])
        return self.runtime_binding(actor, name, self.root, set(metadata['tools']),
                                    parent_id=None if role == 'COORDINATOR' else self.manifest['coordinator_id'],
                                    history_policy='ACTIVE_EXECUTION')

    def _first_prompt(self, actor: str) -> str:
        if not UUID.fullmatch(actor):
            raise TransportError('invalid child conversation ID')
        database = Path.home() / '.gemini/antigravity-cli/conversations' / (actor + '.db')
        safe_path(database)
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=5)) as connection:
            connection.execute('PRAGMA query_only=ON')
            connection.execute('BEGIN')
            rows = connection.execute('SELECT step_type,step_payload FROM steps WHERE idx=0').fetchall()
        if len(rows) != 1 or rows[0][0] != 14 or not isinstance(rows[0][1], bytes):
            raise TransportError('unknown first-child-input layout')
        raw = rows[0][1]
        first = sessions.one(raw, 19, 2)
        repeated = sessions.one(raw, 19, 3, 1)
        if not isinstance(first, bytes) or first != repeated or len(first) > 150_000:
            raise TransportError('child prompt copies differ')
        return hashlib.sha256(first).hexdigest()

    def actor(self, event: dict, assignment: dict, fence: dict) -> ActorBinding:
        actor = event.get('conversationId')
        if (not isinstance(actor, str) or not UUID.fullmatch(actor) or event.get('modelName') != MODEL
                or event.get('workspacePaths') != self.manifest['workspaces']):
            raise TransportError('native actor/model/workspaces mismatch')
        assignment_id = assignment['assignment_id']
        if actor == self.manifest['coordinator_id']:
            runtime_registry = unique_json(read_bounded(self.root / '.local/two-site-refactor/antigravity-runtime-sessions.json', 65536))
            if runtime_registry.get('execution_coordinator') != actor or actor in [runtime_registry.get(k) for k in ('coordinator', 'worker', 'verifier')]:
                raise TransportError('actual Coordinator differs from the registered execution UUID')
            self._binding(actor, 'COORDINATOR')
            return ActorBinding(actor, ActorRole.COORDINATOR, assignment_id)
        actor_file = self.state / 'actors' / (actor + '.json')
        if actor_file.exists():
            entry = unique_json(read_bounded(actor_file, 65536))
            if (entry.get('assignment') != assignment_id or entry.get('epoch') != fence['epoch']
                    or entry.get('parent') != self.manifest['coordinator_id']
                    or entry.get('role') not in ROLE_LABELS):
                raise TransportError('child belongs to another Assignment/epoch')
            self._binding(actor, entry['role'])
            if self._first_prompt(actor) != entry['prompt_sha256']:
                raise TransportError('enrolled child input changed')
            return ActorBinding(actor, ActorRole(entry['role']), assignment_id)
        prompt_digest = self._first_prompt(actor)
        candidates = []
        files = list((self.state / 'delegations').glob('*.json'))
        if len(files) > 4096:
            raise TransportError('delegation retention budget exceeded')
        for path in files:
            intent = unique_json(read_bounded(path, 65536))
            if (intent.get('assignment') == assignment_id and intent.get('epoch') == fence['epoch']
                    and intent.get('parent') == self.manifest['coordinator_id']
                    and intent.get('prompt_sha256') == prompt_digest and intent.get('role') in ROLE_LABELS
                    and 0 <= self.clock() - intent.get('issued_at', 0) <= 120):
                candidates.append(intent)
        if len(candidates) != 1:
            raise TransportError('child lacks unique native Coordinator delegation')
        entry = candidates[0]
        self._binding(actor, entry['role'])
        # One enrolled child per exact one-use delegation, even under parallel hooks.
        claims = self.state / 'delegation-claims'
        claims.mkdir(mode=0o700, exist_ok=True)
        write_new(claims / (prompt_digest + '.json'), {'actor': actor, 'assignment': assignment_id})
        actor_file.parent.mkdir(mode=0o700, exist_ok=True)
        write_new(actor_file, {**entry, 'actor': actor})
        return ActorBinding(actor, ActorRole(entry['role']), assignment_id)

    def delegate(self, event: dict) -> dict:
        self.validate_installation()
        fence, _, assignment = self.active_assignment()
        actor = self.actor(event, assignment, fence)
        if actor.role is not ActorRole.COORDINATOR:
            raise TransportError('only Coordinator may delegate')
        args = event.get('toolCall', {}).get('args', {})
        if set(args) - {'Subagents', 'toolAction', 'toolSummary'}:
            raise TransportError('unknown child invocation argument')
        specs = args.get('Subagents')
        if not isinstance(specs, list) or len(specs) != 1:
            raise TransportError('delegate exactly one Worker or Verifier at a time')
        spec = specs[0]
        if (not isinstance(spec, dict) or set(spec) - {'Prompt', 'Role', 'TypeName', 'Workspace', 'Model'}
                or spec.get('Workspace') != 'inherit' or spec.get('Model', 'inherit') != 'inherit'):
            raise TransportError('unknown/unregistered child workspace or model')
        role = next((key for key, value in ROLE_LABELS.items()
                     if value == spec.get('Role') and ROLE_NAMES[key] == spec.get('TypeName')), None)
        prompt = spec.get('Prompt')
        if (not role or not isinstance(prompt, str) or not 1 <= len(prompt.encode()) <= 150_000
                or assignment['assignment_id'] not in prompt or assignment['worktree_path'] not in prompt):
            raise TransportError('child invocation lacks exact role/Assignment/worktree context')
        delegation_dir = self.state / 'delegations'
        safe_path(delegation_dir, exists=False)
        delegation_dir.mkdir(mode=0o700, exist_ok=True)
        # A native invocation authorizes a single persistent role slot for this
        # Assignment/epoch. Corrections use send_message to the existing child.
        # A crashed/lost invocation requires controller recovery, never a second
        # potentially concurrent writing Worker.
        intents = list(delegation_dir.glob('*.json'))
        if len(intents) >= 4096:
            raise TransportError('delegation retention budget exceeded')
        for path in intents:
            entry = unique_json(read_bounded(path, 65536))
            if (entry.get('assignment') == assignment['assignment_id']
                    and entry.get('epoch') == fence['epoch'] and entry.get('role') == role):
                raise TransportError('role already delegated; address the existing child')
        prompt_sha = hashlib.sha256(prompt.encode()).hexdigest()
        write_new(delegation_dir / (prompt_sha + '.json'), {
            'assignment': assignment['assignment_id'], 'epoch': fence['epoch'],
            'parent': actor.actor_id, 'role': role, 'prompt_sha256': prompt_sha, 'issued_at': self.clock(),
        })
        return {'decision': 'allow', 'reason': 'one native role-bound delegation'}

    def _transport_authorities(self, assignment: dict, fence: dict) -> list[dict]:
        """Read only controller-issued immutable receipts, mirrored byte-for-byte publicly."""

        results = self.root / '.local/two-site-refactor/gateway/results'
        records = []
        receipts = list(self.state.glob('*.receipt.json'))
        if len(receipts) > 4096:
            raise TransportError('transport receipt retention budget exceeded')
        for path in receipts:
            outer_bytes = read_bounded(path, 8_000_000)
            outer = unique_json(outer_bytes)
            outcome = outer.get('outcome') if isinstance(outer, dict) else None
            record = outcome.get('authority_record') if isinstance(outcome, dict) else None
            authority_sha = outcome.get('authority_sha256') if isinstance(outcome, dict) else None
            if record is None and authority_sha is None:
                continue
            if (not isinstance(record, dict) or record.get('record_type') not in AUTHORITY_TYPES
                    or not isinstance(authority_sha, str) or not re.fullmatch('[0-9a-f]{64}', authority_sha)
                    or digest(record) != authority_sha
                    or outer.get('authority') != 'PROTECTED_CONTROLLER_RECEIPT'
                    or outcome.get('status') != 'SUCCESS'
                    or outer.get('request_id') != record.get('request_id')
                    or outer.get('actor') != record.get('actor_id')
                    or outer.get('request', {}).get('operation') != record.get('operation')
                    or record.get('schema_version') != '1.0'
                    or record.get('integration_head') != outer.get('request', {}).get('integration_head')):
                raise TransportError('protected operation authority is malformed')
            self._validate_authority_record(record)
            public = results / (record['request_id'] + '.json')
            public_bytes = read_bounded(public, 8_000_000)
            if public_bytes != outer_bytes:
                raise TransportError('public operation receipt differs from protected authority')
            if (record.get('assignment_id') != assignment['assignment_id']
                    or record.get('stage_id') != assignment['stage_id']
                    or record.get('epoch') != fence['epoch']):
                continue
            records.append({
                'record': record,
                'authority_sha256': authority_sha,
                'public_path': public.relative_to(self.root).as_posix(),
                'public_file_sha256': hashlib.sha256(public_bytes).hexdigest(),
            })
        return records

    @staticmethod
    def _validate_authority_record(record: dict) -> None:
        kind = record['record_type']
        if set(record) != AUTHORITY_COMMON_KEYS | AUTHORITY_EXTRA_KEYS[kind]:
            raise TransportError('protected authority keys differ from the fixed schema')
        if (record['operation'] != AUTHORITY_OPERATION[kind]
                or record['role'] not in {'COORDINATOR', 'WORKER', 'VERIFIER'}
                or not UUID.fullmatch(str(record['request_id']))
                or not UUID.fullmatch(str(record['actor_id']))
                or type(record['epoch']) is not int or record['epoch'] < 1):
            raise TransportError('protected authority identity is invalid')
        sha40_keys = {'integration_head', 'stage_head', 'parent_sha', 'candidate_sha', 'prior_head_sha',
                      'base_sha', 'head_sha'} & set(record)
        sha256_keys = {'integration_patch_sha256', 'test_authority_sha256',
                       'worker_authority_sha256', 'patch_sha256',
                       'verifier_authority_sha256', 'receipt_file_sha256',
                       'closeout_authority_sha256', 'prepare_authority_sha256'} & set(record)
        if any(not isinstance(record[key], str) or not re.fullmatch('[0-9a-f]{40}', record[key])
               for key in sha40_keys):
            raise TransportError('protected authority Git SHA is malformed')
        if any(not isinstance(record[key], str) or not re.fullmatch('[0-9a-f]{64}', record[key])
               for key in sha256_keys):
            raise TransportError('protected authority SHA-256 is malformed')
        if 'worktree_patch_sha256' in record and record['worktree_patch_sha256'] is not None:
            if (not isinstance(record['worktree_patch_sha256'], str)
                    or not re.fullmatch('[0-9a-f]{64}', record['worktree_patch_sha256'])):
                raise TransportError('protected test patch digest is malformed')
        for key in {'selectors', 'test_selectors', 'changed_paths', 'findings'} & set(record):
            value = record[key]
            if (not isinstance(value, list) or len(value) > 1000
                    or len(value) != len(set(value))
                    or any(not isinstance(item, str) or not 1 <= len(item.encode()) <= 4096
                           for item in value)):
                raise TransportError('protected authority list is malformed')
        decisions = {
            'TEST': {'PASS'}, 'WORKER_CANDIDATE': {'CANDIDATE'},
            'VERIFIER': {'VERIFIED', 'CORRECTIONS_REQUIRED'},
            'INTEGRATION': {'INTEGRATED'},
            'CODEX_FINAL': {'APPROVE', 'CHANGES_REQUIRED', 'REJECT', 'BLOCKED'},
            'CODEX_CLOSEOUT_PREACTION': {'APPROVE', 'CHANGES_REQUIRED', 'REJECT', 'BLOCKED'},
            'CLOSEOUT_COMMIT': {'VERIFIED'},
            'NEXT_STAGE_PREPARE': {'PREPARED'},
            'NEXT_STAGE_COMMIT': {'VERIFIED'},
        }
        if record['decision'] not in decisions[kind]:
            raise TransportError('protected authority decision is invalid')
        if 'receipt_path' in record and (not isinstance(record['receipt_path'], str)
                                         or record['receipt_path'].startswith('/')
                                         or '..' in Path(record['receipt_path']).parts):
            raise TransportError('protected authority receipt path is invalid')
        if ('next_stage_id' in record and
                (not isinstance(record['next_stage_id'], str)
                 or not re.fullmatch(r'[A-Z][A-Z0-9]*-[0-9]+[a-z]?', record['next_stage_id']))):
            raise TransportError('protected authority next Stage ID is invalid')

    @staticmethod
    def _unique_authority(records: list[dict], label: str, predicate) -> dict:
        matches = [item for item in records if predicate(item['record'], item['authority_sha256'])]
        if len(matches) != 1:
            raise TransportError(label + ' authority is absent or ambiguous')
        return matches[0]

    def build(self, event: dict, request: dict) -> GatewayContext:
        self.validate_installation()
        fence, registry, assignment = self.active_assignment()
        actor = self.actor(event, assignment, fence)
        stage_path = safe_path(Path(assignment['worktree_path']))
        stage_workspace = safe_path(Path(self.manifest['stage_workspace_root']))
        if stage_path == stage_workspace or not stage_path.is_relative_to(stage_workspace):
            raise TransportError('registered Stage worktree is outside the mounted Stage workspace root')
        snapshots = (self.observe(self.root), self.observe(stage_path))
        coordinator = ActorBinding(self.manifest['coordinator_id'], ActorRole.COORDINATOR, assignment['assignment_id'])
        bindings = (actor,) if actor.role is ActorRole.COORDINATOR else (coordinator, actor)
        receipts, candidates, reviews, integrations = [], [], [], []
        native = self._transport_authorities(assignment, fence)
        operation = request.get('operation')
        if operation == 'worker-commit':
            expected = request.get('test_receipt_sha256')
            self._unique_authority(
                native, 'Worker test',
                lambda record, authority: record.get('record_type') == 'TEST'
                and record.get('role') == 'WORKER' and record.get('actor_id') == actor.actor_id
                and authority == expected and record.get('decision') == 'PASS'
                and record.get('stage_head') == snapshots[1].head_sha
                and record.get('worktree_patch_sha256') == request.get('expected_patch_sha256'),
            )
        if operation == 'stage-advance':
            self._unique_authority(
                native, 'Worker candidate',
                lambda record, authority: record.get('record_type') == 'WORKER_CANDIDATE'
                and authority == request.get('worker_receipt_sha256')
                and record.get('candidate_sha') == request.get('stage_head')
                and record.get('decision') == 'CANDIDATE',
            )
        if operation == 'verifier-submit':
            candidate_sha = request.get('candidate_sha')
            self._unique_authority(
                native, 'Verifier test',
                lambda record, authority: record.get('record_type') == 'TEST'
                and record.get('role') == 'VERIFIER' and record.get('actor_id') == actor.actor_id
                and authority == request.get('test_receipt_sha256') and record.get('decision') == 'PASS'
                and record.get('stage_head') == candidate_sha
                and record.get('selectors') == request.get('test_selectors'),
            )
            self._unique_authority(
                native, 'Worker candidate',
                lambda record, authority: record.get('record_type') == 'WORKER_CANDIDATE'
                and record.get('candidate_sha') == candidate_sha and record.get('decision') == 'CANDIDATE',
            )
        worker_by_candidate = {}
        for item in native:
            if item['record'].get('record_type') != 'WORKER_CANDIDATE':
                continue
            candidate = item['record']['candidate_sha']
            if candidate in worker_by_candidate:
                raise TransportError('Worker candidate authority is ambiguous')
            worker_by_candidate[candidate] = item
        for item in native:
            record, authority = item['record'], item['authority_sha256']
            kind = record.get('record_type')
            if kind == 'VERIFIER' and record.get('decision') == 'VERIFIED':
                worker = worker_by_candidate.get(record.get('candidate_sha'))
                if worker is None:
                    raise TransportError('Verifier receipt lacks its exact Worker candidate')
                value = worker['record']
                if (record.get('worker_authority_sha256') != worker['authority_sha256']
                        or record.get('test_selectors') != value.get('test_selectors')):
                    raise TransportError('Verifier/Worker candidate authority mismatch')
                receipts.append(ReceiptBinding(
                    ReceiptKind.VERIFIER, item['public_path'], assignment['stage_id'],
                    assignment['assignment_id'], 'VERIFIED', authority,
                    value['parent_sha'], value['candidate_sha'], value['integration_patch_sha256'],
                    item['public_file_sha256'],
                ))
                candidates.append(CandidateBinding(
                    assignment['assignment_id'], value['candidate_sha'], value['parent_sha'],
                    tuple(value['changed_paths']), value['integration_patch_sha256'],
                    tuple(value['test_selectors']), authority,
                ))
            elif kind == 'INTEGRATION' and record.get('decision') == 'INTEGRATED':
                integrations.append({'assignment_id': assignment['assignment_id'],
                                     'base_sha': record.get('base_sha'), 'head_sha': record.get('head_sha')})
            elif kind in {'CODEX_FINAL', 'CODEX_CLOSEOUT_PREACTION'}:
                receipt_kind = (ReceiptKind.CODEX_FINAL if kind == 'CODEX_FINAL'
                                else ReceiptKind.CODEX_CLOSEOUT_PREACTION)
                expected_decision = 'APPROVE'
                if record.get('decision') == expected_decision:
                    receipts.append(ReceiptBinding(
                        receipt_kind, record['receipt_path'], assignment['stage_id'],
                        assignment['assignment_id'], expected_decision, authority,
                        record.get('base_sha'), record['head_sha'], record.get('patch_sha256'),
                        record['receipt_file_sha256'],
                    ))
            elif kind == 'CLOSEOUT_COMMIT' and record.get('decision') == 'VERIFIED':
                receipts.append(ReceiptBinding(
                    ReceiptKind.CLOSEOUT_COMMIT, item['public_path'], assignment['stage_id'],
                    assignment['assignment_id'], 'VERIFIED', authority,
                    record.get('base_sha'), record['head_sha'], record.get('patch_sha256'),
                    item['public_file_sha256'],
                ))
            elif kind == 'NEXT_STAGE_PREPARE' and record.get('decision') == 'PREPARED':
                receipts.append(ReceiptBinding(
                    ReceiptKind.NEXT_STAGE_PREPARE, item['public_path'], assignment['stage_id'],
                    assignment['assignment_id'], 'PREPARED', authority,
                    record['head_sha'], record['head_sha'], record['patch_sha256'],
                    item['public_file_sha256'],
                ))
            elif kind == 'NEXT_STAGE_COMMIT' and record.get('decision') == 'VERIFIED':
                receipts.append(ReceiptBinding(
                    ReceiptKind.NEXT_STAGE_COMMIT, item['public_path'], assignment['stage_id'],
                    assignment['assignment_id'], 'VERIFIED', authority,
                    record['base_sha'], record['head_sha'], record['patch_sha256'],
                    item['public_file_sha256'],
                ))
        if request.get('operation') in {'bundle-prepare', 'review-request'}:
            expected_bundle = f".local/two-site-refactor/review-bundles/{assignment['stage_id']}-{assignment['assignment_id']}"
            matches = [item for item in integrations
                       if item.get('assignment_id') == assignment['assignment_id']
                       and item.get('base_sha') == request.get('base_sha')
                       and item.get('head_sha') == request.get('head_sha')
                       and (request.get('bundle', expected_bundle) == expected_bundle)]
            if len(matches) == 1:
                reviews.append(ReviewBinding(assignment['assignment_id'], expected_bundle,
                                             request['base_sha'], request['head_sha']))
        authorization = read_bounded(self.root / '.local/two-site-refactor/authorizations/CODEX_SOURCE_REVIEW.md', 65536).decode().splitlines()
        source_scope = 'TWO_SITE_REFACTOR_SOURCE_AND_EVIDENCE_TO_OPENAI_CODEX'
        authorized = 'authorization: APPROVED' in authorization and 'scope: ' + source_scope in authorization
        guard = CommitGuard(self.root / '.git/hooks/pre-commit', self.manifest['pre_commit_sha256'],
                            self.manifest['main_memory_guard_sha256'], snapshots[0].head_sha, None, True)
        # Effective Git config and main guard bytes are separately rechecked by executor.
        return GatewayContext(self.root, actor.actor_id, bindings,
                              (self.root, stage_path), snapshots,
                              tuple(receipts), tuple(candidates), tuple(reviews), fence_api,
                              source_scope if authorized else None, guard)
