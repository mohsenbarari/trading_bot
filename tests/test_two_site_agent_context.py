"""Protected installation and native delegation authority (disposable fixtures)."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from scripts.two_site_agent_context import ContextBuilder, PROTECTED_SOURCE, ROLE_NAMES
from scripts.two_site_agent_gateway import ActorRole
from scripts.two_site_agent_transport import MODEL, TransportError

COORDINATOR = '11111111-1111-4111-8111-111111111111'
WORKER = '22222222-2222-4222-8222-222222222222'
OTHER = '33333333-3333-4333-8333-333333333333'
SHA = 'a' * 40


class ContextTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='two-site-context-')
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        self.root, self.state = base / 'root', base / 'state'
        self.stage_workspace, self.stage = base / 'worktrees', base / 'worktrees' / 'stage'
        for directory in (self.root, self.state, self.stage_workspace, self.stage):
            directory.mkdir(mode=0o700)
        canonical = Path(__file__).resolve().parents[1]
        for relative in PROTECTED_SOURCE:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if relative.endswith('governance-review.schema.json'):
                path.write_bytes((canonical / relative).read_bytes())
            elif relative.endswith('/agent.md'):
                path.write_text('---\ntools: [view_file, write_to_file]\n---\n')
            else:
                path.write_text('reviewed fixture\n')
        self.receipt = {
            'schema_version': '1.0', 'reviewer_authority': 'CODEX_FINAL_REVIEWER',
            'receipt_type': 'EXECUTION_GOVERNANCE_AMENDMENT', 'amendment_id': 'GATEWAY-TEST',
            'integration_base_sha': 'b' * 40, 'reviewed_head_sha': SHA,
            'reviewed_at_utc': '2026-09-04T23:00:00Z', 'verdict': 'APPROVE',
            'scope': {'changed_paths_within_scope': True, 'stage_state_unchanged': True,
                      'unauthorized_external_mutation': 'NONE'}, 'findings': [],
            'verification': [{'check': 'fixture', 'result': 'PASS', 'evidence_uri': 'fixture'}],
            'decision': {'accepted_commit': SHA, 'stage_completion_granted': False,
                         'residual_risk': 'fixture only', 'conditions': []},
        }
        self.manifest = {
            'schema_version': '1.0', 'root': str(self.root),
            'stage_workspace_root': str(self.stage_workspace),
            'workspaces': [str(self.root), str(self.stage_workspace)], 'coordinator_id': COORDINATOR,
            'accepted_sha': SHA, 'mode': 'ACTIVE',
            'source_sha256': {value: hashlib.sha256((self.root / value).read_bytes()).hexdigest()
                              for value in PROTECTED_SOURCE},
            'governance_receipt_sha256': '0' * 64,
            'pre_commit_sha256': '1' * 64, 'main_memory_guard_sha256': '2' * 64,
            'binary_sha256': self.binary_manifest(base),
        }
        self.save_receipt()
        local = self.root / '.local/two-site-refactor'
        local.mkdir(parents=True)
        (local / 'antigravity-runtime-sessions.json').write_text(json.dumps({
            'execution_coordinator': COORDINATOR, 'coordinator': OTHER,
            'worker': '44444444-4444-4444-8444-444444444444',
            'verifier': '55555555-5555-4555-8555-555555555555',
        }))
        self.runtime = Mock(return_value={'verified': True})
        self.builder = ContextBuilder(self.root, self.state, self.manifest, Mock(),
                                      runtime_binding=self.runtime, clock=lambda: 1000.0)
        self.assignment = {'assignment_id': 'WA-P1-00B-TEST-20260904-001',
                           'stage_id': 'P1-00b',
                           'worktree_path': str(self.stage)}
        self.fence = {'epoch': 2}
        self.builder.active_assignment = lambda: (self.fence, {}, self.assignment)
        self.prompt = self.assignment['assignment_id'] + ' worktree=' + str(self.stage)
        self.prompt_hash = hashlib.sha256(self.prompt.encode()).hexdigest()
        self.builder._first_prompt = lambda actor: self.prompt_hash
        self.event = {'conversationId': COORDINATOR, 'stepIdx': 5, 'modelName': MODEL,
                      'workspacePaths': self.manifest['workspaces'],
                      'toolCall': {'name': 'invoke_subagent', 'args': {'Subagents': [{
                          'TypeName': ROLE_NAMES['WORKER'], 'Role': 'Stage Worker',
                          'Workspace': 'inherit', 'Model': 'inherit', 'Prompt': self.prompt,
                      }]}}}

    @staticmethod
    def binary_manifest(base: Path) -> dict[str, dict[str, str]]:
        directory = base / 'binaries'
        directory.mkdir()
        result = {}
        for name in ('git', 'python', 'bwrap', 'prlimit', 'codex', 'agy'):
            path = directory / name
            path.write_text(name + '\n')
            result[name] = {
                'path': str(path),
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        return result

    def save_receipt(self):
        path = self.state / 'governance-receipt.json'
        path.write_text(json.dumps(self.receipt))
        self.manifest['governance_receipt_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()

    def test_exact_reviewed_installation_and_source_drift(self):
        self.builder.validate_installation()
        (self.root / PROTECTED_SOURCE[0]).write_text('changed unreviewed controller')
        with self.assertRaises(TransportError):
            self.builder.validate_installation()

    def test_receipt_must_be_complete_and_entirely_successful(self):
        initial = copy.deepcopy(self.receipt)
        for mutate in (
            lambda r: r.update(verdict='CHANGES_REQUIRED'),
            lambda r: r['decision'].update(accepted_commit='c' * 40),
            lambda r: r['scope'].update(stage_state_unchanged=False),
            lambda r: r.update(verification=[]),
            lambda r: r['verification'][0].update(result='NOT_RUN'),
            lambda r: r['decision'].update(stage_completion_granted=True),
            lambda r: r['decision'].update(conditions=['not cleared for activation']),
            lambda r: r.update(unrecognized_authority=True),
        ):
            self.receipt = copy.deepcopy(initial)
            mutate(self.receipt)
            self.save_receipt()
            with self.assertRaises(TransportError):
                self.builder.validate_installation()

    def test_registered_execution_actor_not_smoke_and_real_binding_required(self):
        actor = self.builder.actor(self.event, self.assignment, self.fence)
        self.assertEqual(ActorRole.COORDINATOR, actor.role)
        self.assertEqual('ACTIVE_EXECUTION', self.runtime.call_args.kwargs['history_policy'])
        self.runtime.side_effect = ValueError('runtime role drift')
        with self.assertRaises(ValueError):
            self.builder.actor(self.event, self.assignment, self.fence)
        self.runtime.side_effect = None
        registry = self.root / '.local/two-site-refactor/antigravity-runtime-sessions.json'
        registry.write_text(json.dumps({'execution_coordinator': COORDINATOR, 'coordinator': COORDINATOR}))
        with self.assertRaises(TransportError):
            self.builder.actor(self.event, self.assignment, self.fence)

    def test_child_requires_exact_one_use_native_delegation(self):
        child = {**self.event, 'conversationId': WORKER}
        with self.assertRaises(TransportError):
            self.builder.actor(child, self.assignment, self.fence)
        self.assertEqual('allow', self.builder.delegate(self.event)['decision'])
        actor = self.builder.actor(child, self.assignment, self.fence)
        self.assertEqual(ActorRole.WORKER, actor.role)
        self.assertEqual(COORDINATOR, self.runtime.call_args.kwargs['parent_id'])
        self.assertEqual(actor, self.builder.actor(child, self.assignment, self.fence))
        # A second UUID cannot claim even an identical role and first prompt.
        with self.assertRaises(FileExistsError):
            self.builder.actor({**child, 'conversationId': OTHER}, self.assignment, self.fence)
        with self.assertRaises(TransportError):
            self.builder.actor(child, self.assignment, {'epoch': 3})

    def test_second_worker_or_verifier_role_slot_cannot_be_created(self):
        self.builder.delegate(self.event)
        changed = copy.deepcopy(self.event)
        changed['toolCall']['args']['Subagents'][0]['Prompt'] += ' new writing worker'
        with self.assertRaises(TransportError):
            self.builder.delegate(changed)
        changed['toolCall']['args']['Subagents'][0].update(
            TypeName=ROLE_NAMES['VERIFIER'], Role='Stage Verifier')
        self.assertEqual('allow', self.builder.delegate(changed)['decision'])
        changed['toolCall']['args']['Subagents'][0]['Prompt'] += ' second verifier'
        with self.assertRaises(TransportError):
            self.builder.delegate(changed)

    def test_fake_role_new_workspace_or_parallel_invocation_are_rejected(self):
        for update in ({'Role': 'Coordinator'}, {'Workspace': '/tmp/another'},
                       {'Model': 'different'}, {'TypeName': 'general-purpose'}):
            event = copy.deepcopy(self.event)
            event['toolCall']['args']['Subagents'][0].update(update)
            with self.assertRaises(TransportError):
                self.builder.delegate(event)
        event = copy.deepcopy(self.event)
        event['toolCall']['args']['Subagents'] *= 2
        with self.assertRaises(TransportError):
            self.builder.delegate(event)
        self.assertFalse(list((self.state / 'delegations').glob('*.json')))

    def test_only_exact_mirrored_transport_receipt_becomes_authority(self):
        request_id = '66666666-6666-4666-8666-666666666666'
        record = {
            'schema_version': '1.0', 'record_type': 'TEST', 'operation': 'test',
            'assignment_id': self.assignment['assignment_id'], 'stage_id': 'P1-00b',
            'epoch': 2, 'request_id': request_id, 'actor_id': WORKER, 'role': 'WORKER',
            'integration_head': SHA, 'decision': 'PASS', 'stage_head': SHA,
            'worktree_patch_sha256': '7' * 64, 'selectors': ['tests/test_fixture.py'],
        }
        authority_sha = hashlib.sha256(
            json.dumps(record, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        outer = {
            'actor': WORKER, 'step': 1, 'request_id': request_id,
            'request_sha256': '8' * 64,
            'request': {'operation': 'test', 'integration_head': SHA},
            'plan_sha256': '9' * 64, 'completed_at': 1000,
            'outcome': {'status': 'SUCCESS', 'authority_record': record,
                        'authority_sha256': authority_sha},
            'authority': 'PROTECTED_CONTROLLER_RECEIPT',
        }
        content = json.dumps(outer, sort_keys=True, separators=(',', ':')) + '\n'
        (self.state / (request_id + '.receipt.json')).write_text(content)
        public = self.root / '.local/two-site-refactor/gateway/results' / (request_id + '.json')
        public.parent.mkdir(parents=True, exist_ok=True)
        public.write_text(content)
        records = self.builder._transport_authorities(self.assignment, self.fence)
        self.assertEqual(authority_sha, records[0]['authority_sha256'])
        outer['outcome']['authority_record']['unexpected'] = True
        changed = json.dumps(outer, sort_keys=True, separators=(',', ':')) + '\n'
        (self.state / (request_id + '.receipt.json')).write_text(changed)
        public.write_text(changed)
        with self.assertRaises(TransportError):
            self.builder._transport_authorities(self.assignment, self.fence)


if __name__ == '__main__':
    unittest.main()
