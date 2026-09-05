"""Protected host preparation and authority tests; no real installation or review."""
import io
import hashlib
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import yaml

from scripts.install_two_site_agent_gateway import expected_hooks
from scripts.two_site_agent_executor import execute_plan
from scripts.two_site_agent_gateway import (
    ActorRole, CommandPlan, EnvClass, FollowupCommandPlan, GitCheckpoint, GitSnapshot,
    RESULTING_HEAD, plan_operation,
)
from scripts.two_site_agent_host import ProtectedGatewayHost, main as host_main
from scripts.two_site_agent_sandbox import (
    HOOK_TIMEOUT_MARGIN_SECONDS, hook_deadline_seconds, hook_timeout_seconds,
)
from scripts.two_site_agent_transport import MODEL, NativeTransport, TransportError, digest
from tests.test_two_site_agent_gateway import (
    ASSIGNMENT as GATEWAY_ASSIGNMENT,
    CLOSEOUT_PATCH as GATEWAY_CLOSEOUT_PATCH,
    COORDINATOR as GATEWAY_COORDINATOR,
    GatewayFixture,
    INTEGRATION_HEAD as GATEWAY_INTEGRATION_HEAD,
    STAGE as GATEWAY_STAGE,
)
from tests.test_two_site_agent_executor import (
    ASSIGNMENT, COORDINATOR, EPOCH, STAGE, GitFixture, RecordingRunner,
)


class HostTests(unittest.TestCase):
    def setUp(self):
        self.fx = GitFixture()
        self.addCleanup(self.fx.cleanup)
        self.host = ProtectedGatewayHost.__new__(ProtectedGatewayHost)
        self.host.root = self.fx.root
        self.host.state = self.fx.container / 'protected-state'
        self.host.state.mkdir(mode=0o700)
        local = self.fx.root / '.local/two-site-refactor'
        assignment = local / 'assignments' / (ASSIGNMENT + '.md')
        assignment.parent.mkdir(parents=True)
        self.assignment = {'assignment_id': ASSIGNMENT, 'stage_id': STAGE,
                           'worktree_path': str(self.fx.stage),
                           'branch': 'refactor/stage/P1-00b-gateway',
                           'base_sha': self.fx.base,
                           'integration_head_at_issue': self.fx.base}
        assignment.write_text(
            '# Assignment\n\n```yaml\n' + yaml.safe_dump(self.assignment, sort_keys=False).rstrip()
            + '\n```\n')

    def bind_mutation_host(self):
        (self.fx.root / '.gitignore').write_text('.local/\n')
        self.fx.git(self.fx.root, 'add', '.gitignore')
        self.fx.git(self.fx.root, 'commit', '-qm', 'ignore protected runtime state')
        self.fx.integration_head = self.fx.head(self.fx.root)
        self.fx.pins = self.fx.make_pins()
        self.assignment['integration_head_at_issue'] = self.fx.integration_head
        fence = {
            'active_assignment_id': ASSIGNMENT, 'epoch': EPOCH,
            'coordinator_instance_id': COORDINATOR,
            'integration_head_sha': self.fx.integration_head,
        }
        self.host.builder = Mock()
        self.host.builder.active_assignment.return_value = (fence, {}, self.assignment)
        self.host.manifest = {'coordinator_id': COORDINATOR}
        self.host.controller = SimpleNamespace(
            reservation=self.fx.root / '.local/two-site-refactor/no-source-reservation.json')

    def prepare_stage_bind_fixture(self):
        workspace_root = self.fx.container
        self.host.manifest = {
            'stage_workspace_root': str(workspace_root), 'coordinator_id': COORDINATOR,
            'binary_sha256': {
                'git': {'path': str(self.fx.pins.git_binary), 'sha256': self.fx.pins.git_sha256},
                'python': {'path': str(self.fx.pins.python_binary), 'sha256': self.fx.pins.python_sha256},
            },
            'source_sha256': {
                'scripts/two_site_orchestrator_fence.py': self.fx.pins.fence_sha256,
                'scripts/two_site_codex_final_review.sh': self.fx.pins.final_review_sha256,
                'scripts/two_site_codex_closeout_review.sh': self.fx.pins.closeout_review_sha256,
            },
        }
        registry = self.fx.root / '.local/two-site-refactor/worktrees.yaml'
        registry.write_text(yaml.safe_dump({
            'schema_version': '1.0',
            'limits': {'max_ephemeral_stage_worktrees': 2},
            'integration': {'active_assignment_id': ASSIGNMENT},
            'stage_worktrees': [{
                'registry_id': 'old', 'stage_id': STAGE, 'assignment_id': ASSIGNMENT,
                'path': str(self.fx.stage), 'branch': self.assignment['branch'],
                'head_sha': self.fx.head(self.fx.stage), 'status': 'COMPLETE_CANDIDATE',
            }],
        }, sort_keys=False))
        order = self.fx.root / 'docs/refactor/two-server/execution-order.yaml'
        order.parent.mkdir(parents=True, exist_ok=True)
        order.write_text(
            f'stages:\n  {STAGE}: {{state: COMPLETE, section: 1}}\n'
            '  P1-01: {state: IN_PROGRESS, section: 1}\n')
        contract = {
            'slug': 'repo-cleanup',
            'allowed_paths': ['docs/refactor/two-server/section-1/**'],
            'forbidden_paths': [
                '.agents/**', 'docs/refactor/two-server/CHANGE_LEDGER.md',
                'docs/refactor/two-server/execution-order.yaml',
                'docs/refactor/two-server/reviews/**', 'scripts/two_site_agent_*.py',
            ],
            'locks': [], 'deliverables': ['inventory'], 'tests': ['dry run'],
            'failure_tests': ['path escape'], 'rollback_test': 'revert commit',
            'scope_notes': 'Inventory and tooling only.',
        }
        request = self.fx.request(
            'next-stage-bind', actor_id=COORDINATOR, contract=contract,
            next_stage='P1-01', next_assignment='WA-P1-01-REPO-CLEANUP-20260905-001',
            next_stage_commit_receipt_sha256='a' * 64,
        )
        plan = self.fx.plan(
            request, ActorRole.COORDINATOR, (),
            contract_sha256=self.host._contract_digest(contract),
        )
        result = self.host._prepare_stage_bind(plan, request, self.assignment, self.fx.pins)
        return request, plan, result, registry

    def write_stage_bind_transport_state(self, request):
        journal = json.loads(self.host._stage_bind_journal_path.read_text())
        local = self.fx.root / '.local/two-site-refactor/gateway'
        (local / 'requests').mkdir(parents=True, exist_ok=True)
        (local / 'results').mkdir(parents=True, exist_ok=True)
        (local / 'requests' / (request['id'] + '.json')).write_text(json.dumps(request))
        running = {
            'actor': request['actor_id'], 'step': 41, 'request_id': request['id'],
            'request_sha256': digest(request), 'plan_sha256': journal['plan_sha256'],
            'issued_at': 1.0,
        }
        (self.host.state / (request['id'] + '.running.json')).write_text(json.dumps(running))
        return journal

    def mark_stage_bind_state(self, request, registry_path, *, status='ACTIVE'):
        registry = yaml.safe_load(registry_path.read_text())
        registry['integration']['active_assignment_id'] = request['next_assignment']
        for item in registry['stage_worktrees']:
            if item['assignment_id'] == ASSIGNMENT:
                item['status'] = 'CLOSED'
                item['closed_at'] = '2026-09-05T00:00:00Z'
        registry_path.write_text(yaml.safe_dump(registry, sort_keys=False))
        fence = {
            'status': status, 'binding_kind': 'STAGE',
            'previous_assignment_id': ASSIGNMENT,
            'active_assignment_id': request['next_assignment'], 'epoch': EPOCH,
            'integration_head_sha': self.fx.integration_head,
            'coordinator_instance_id': COORDINATOR,
        }
        (self.fx.root / '.local/two-site-refactor/orchestrator-fence.json').write_text(
            json.dumps(fence))

    def test_review_bundle_is_exact_and_hash_verified(self):
        request = {
            'base_sha': self.fx.base, 'head_sha': self.fx.integration_head,
            'stage_report': 'report', 'final_review_request': 'request', 'handoff': 'handoff',
        }
        bundle = f'.local/two-site-refactor/review-bundles/{STAGE}-{ASSIGNMENT}'
        plan = SimpleNamespace(bindings=tuple(sorted({
            'bundle': bundle,
            'stage_report_sha256': hashlib.sha256(b'report').hexdigest(),
            'final_request_sha256': hashlib.sha256(b'request').hexdigest(),
            'handoff_sha256': hashlib.sha256(b'handoff').hexdigest(),
        }.items())))
        evidence = self.host._prepare_bundle(plan, request, self.assignment)
        self.assertEqual(bundle, evidence['bundle'])
        self.assertEqual(b'report', (self.fx.root / bundle / 'STAGE_REPORT.md').read_bytes())
        self.assertIn(self.fx.integration_head,
                      (self.fx.root / bundle / 'commit.txt').read_text())

    def test_closeout_prepare_writes_and_stages_only_four_derived_paths(self):
        bundle = self.fx.root / f'.local/two-site-refactor/review-bundles/{STAGE}-{ASSIGNMENT}'
        bundle.mkdir(parents=True)
        (bundle / 'CODEX_FINAL_REVIEW.md').write_text('accepted review\n')
        (self.fx.root / '.gitignore').write_text('.local/\n')
        order = self.fx.root / 'docs/refactor/two-server/execution-order.yaml'
        order.parent.mkdir(parents=True)
        order.write_text(f'stages:\n  {STAGE}: {{state: IN_PROGRESS, section: 1, depends_on: [], production: false}}\n')
        ledger = self.fx.root / 'docs/refactor/two-server/CHANGE_LEDGER.md'
        ledger.write_text('# Ledger\n')
        self.fx.git(self.fx.root, 'add', '--all')
        self.fx.git(self.fx.root, 'commit', '-qm', 'closeout fixture baseline')
        self.fx.integration_head = self.fx.head(self.fx.root)
        self.fx.pins = self.fx.make_pins()
        self.assertEqual('', self.fx.git(self.fx.root, 'status', '--porcelain=v1',
                                         '--untracked-files=all').stdout)
        receipt = {
            'reviewed_at_utc': '2026-09-05T00:00:00Z', 'verdict': 'APPROVE',
            'reviewed_head_sha': self.fx.integration_head,
            'scope': {'dependency_receipts_valid': True,
                      'changed_paths_within_assignment': True,
                      'locks_and_parallel_peer_valid': True,
                      'unauthorized_external_mutation': 'NONE'},
            'findings': [], 'verification': [{'result': 'PASS'}],
            'decision': {'accepted_commit': self.fx.integration_head, 'conditions': []},
        }
        self.host._validated_review_receipt = Mock(return_value=(receipt, 'a' * 64))
        plan = SimpleNamespace(bindings=(('final_receipt_file_sha256', 'a' * 64),))
        result = self.host._prepare_closeout(
            plan, {'accepted_head': self.fx.integration_head}, self.assignment, self.fx.pins)
        self.assertEqual(4, len(result['staged_paths']))
        self.assertRegex(result['patch_sha256'], r'^[0-9a-f]{64}$')
        self.assertEqual('', self.fx.git(self.fx.root, 'diff', '--name-only').stdout)
        self.assertIn(f'{STAGE}: {{state: COMPLETE', order.read_text())

    def test_test_authority_is_canonical_and_not_generic_success(self):
        plan = SimpleNamespace(operation='test', request_id='44444444-4444-4444-8444-444444444444',
                               actor_id='22222222-2222-4222-8222-222222222222',
                               role=ActorRole.WORKER)
        request = {'integration_head': self.fx.integration_head,
                   'selectors': ['tests/test_sample.py']}
        stage = {'head_sha': self.fx.base, 'worktree_patch_sha256': '1' * 64}
        outcome = {'status': 'SUCCESS', 'pre_git': [{}, stage]}
        record = self.host._authority_record(
            plan, request, outcome, self.assignment, {'epoch': EPOCH})
        self.assertEqual('TEST', record['record_type'])
        self.assertRegex(digest(record), r'^[0-9a-f]{64}$')
        self.assertIsNone(self.host._authority_record(
            plan, request, {'status': 'FAILED'}, self.assignment, {'epoch': EPOCH}))

    def test_verifier_authority_requires_exact_passing_test_receipt_and_selectors(self):
        selectors = ['tests/test_sample.py']
        plan = SimpleNamespace(
            operation='verifier-submit',
            request_id='44444444-4444-4444-8444-444444444444',
            actor_id='22222222-2222-4222-8222-222222222222',
            role=ActorRole.VERIFIER,
        )
        request = {
            'integration_head': self.fx.integration_head,
            'candidate_sha': self.fx.base,
            'test_receipt_sha256': 'a' * 64,
            'test_selectors': selectors,
            'decision': 'VERIFIED',
            'findings': [],
        }
        self.host._worker_for_candidate = Mock(return_value={'authority_sha256': 'b' * 64})
        self.host._selectors_for_authority = Mock(return_value=selectors)
        record = self.host._authority_record(
            plan, request, {'status': 'SUCCESS'}, self.assignment, {'epoch': EPOCH},
        )
        self.assertEqual('VERIFIER', record['record_type'])
        self.assertEqual(selectors, record['test_selectors'])
        self.host._selectors_for_authority.assert_called_once_with('a' * 64, 'VERIFIER')

        self.host._selectors_for_authority = Mock(return_value=['tests/test_other.py'])
        with self.assertRaisesRegex(TransportError, 'selectors differ'):
            self.host._authority_record(
                plan, request, {'status': 'SUCCESS'}, self.assignment, {'epoch': EPOCH},
            )

    def test_complete_hook_entrypoints_enforce_deadline_and_fail_closed(self):
        outputs = {
            'pre': {'decision': 'deny'},
            'post': {},
            'stop': {'continue': False},
        }

        def slow_host(_manifest):
            time.sleep(0.25)
            raise AssertionError('deadline did not interrupt host preparation')

        for phase, expected in outputs.items():
            stdin = SimpleNamespace(buffer=io.BytesIO(b'{}'))
            stdout = io.StringIO()
            started = time.monotonic()
            with patch('scripts.two_site_agent_host.sys.stdin', stdin), patch(
                'scripts.two_site_agent_host.sys.stdout', stdout
            ), patch(
                'scripts.two_site_agent_host.load_manifest', return_value={}
            ), patch(
                'scripts.two_site_agent_host.ProtectedGatewayHost', side_effect=slow_host
            ), patch(
                'scripts.two_site_agent_host.hook_deadline_seconds', return_value=0.05
            ):
                self.assertEqual(0, host_main([phase]))
            response = json.loads(stdout.getvalue())
            for key, value in expected.items():
                self.assertEqual(value, response[key])
            self.assertNotIn('authority', response)
            self.assertLess(time.monotonic() - started, 0.2)

    def test_installer_hook_contract_matches_repository_shape(self):
        actual = json.loads((Path(__file__).resolve().parents[1] / '.agents/hooks.json').read_text())
        self.assertEqual(actual, expected_hooks())

    def test_hook_timeouts_are_derived_from_enforced_end_to_end_deadlines(self):
        hooks = expected_hooks()['two-site-typed-operation-gate']
        values = {
            'pre': hooks['PreToolUse'][0]['hooks'][0]['timeout'],
            'post': hooks['PostToolUse'][0]['hooks'][0]['timeout'],
            'stop': hooks['Stop'][0]['timeout'],
        }
        for phase, outer_timeout in values.items():
            self.assertEqual(hook_timeout_seconds(phase), outer_timeout)
            self.assertEqual(
                hook_deadline_seconds(phase) + HOOK_TIMEOUT_MARGIN_SECONDS,
                outer_timeout,
            )

    def test_all_codex_review_bridges_pin_model_and_reasoning(self):
        root = Path(__file__).resolve().parents[1]
        for relative in (
            'scripts/two_site_codex_final_review.sh',
            'scripts/two_site_codex_closeout_review.sh',
            'scripts/two_site_codex_governance_review.sh',
        ):
            source = (root / relative).read_text()
            self.assertIn('codex_model="gpt-6-astra"', source)
            self.assertIn('codex_reasoning_effort="high"', source)
            self.assertIn('--model "$codex_model"', source)
            self.assertIn('model_reasoning_effort=', source)

    def closeout_review_composition(self):
        fixture = GatewayFixture()
        self.addCleanup(fixture.cleanup)
        fixture.set_stage_flag('state', 'COMPLETE')
        prefix = f'docs/refactor/two-server/reviews/{GATEWAY_STAGE}-{GATEWAY_ASSIGNMENT}'
        snapshot = GitSnapshot(
            fixture.root,
            'refactor/two-site-architecture',
            GATEWAY_INTEGRATION_HEAD,
            staged_paths=tuple(sorted((
                'docs/refactor/two-server/CHANGE_LEDGER.md',
                'docs/refactor/two-server/execution-order.yaml',
                prefix + '.json',
                prefix + '.md',
            ))),
            staged_patch_sha256=GATEWAY_CLOSEOUT_PATCH,
        )
        request = fixture.request(
            'closeout-review', actor=GATEWAY_COORDINATOR,
            accepted_head=GATEWAY_INTEGRATION_HEAD,
            final_receipt_sha256=fixture.digest(fixture.final_path),
        )
        plan = plan_operation(request, fixture.context(
            caller=GATEWAY_COORDINATOR, root_snapshot=snapshot,
        ))
        host = ProtectedGatewayHost.__new__(ProtectedGatewayHost)
        host.root = fixture.root
        host._binary = Mock(return_value=Path('/usr/bin/true'))
        return fixture, snapshot, request, plan, host

    def test_closeout_planner_and_supervisor_compose_successfully(self):
        _, snapshot, request, plan, host = self.closeout_review_composition()
        with patch('scripts.two_site_agent_host.observe_git', return_value=snapshot), patch(
            'scripts.two_site_agent_host._run_bounded_process',
            return_value=subprocess.CompletedProcess(plan.commands[0].argv, 0, b'ok', b''),
        ) as runner:
            result = host._review_supervisor(plan.commands[0], request, dict(plan.bindings))
        self.assertEqual('SUCCESS', result['status'])
        self.assertRegex(result['receipt_file_sha256'], r'^[0-9a-f]{64}$')
        runner.assert_called_once()

    def test_closeout_composition_rejects_missing_binding_and_input_tampering(self):
        fixture, snapshot, request, plan, host = self.closeout_review_composition()
        missing = dict(plan.bindings)
        del missing['bundle']
        with patch('scripts.two_site_agent_host.observe_git', return_value=snapshot), patch(
            'scripts.two_site_agent_host._run_bounded_process'
        ) as runner:
            with self.assertRaisesRegex(TransportError, 'canonical bundle binding'):
                host._review_supervisor(plan.commands[0], request, missing)
        runner.assert_not_called()

        def mutate(*_args, **_kwargs):
            (fixture.final_path.parent / 'HANDOFF.md').write_text('tampered during review\n')
            return subprocess.CompletedProcess(plan.commands[0].argv, 0, b'ok', b'')

        with patch('scripts.two_site_agent_host.observe_git', return_value=snapshot), patch(
            'scripts.two_site_agent_host._run_bounded_process', side_effect=mutate,
        ):
            with self.assertRaisesRegex(TransportError, 'changed after protected planning'):
                host._review_supervisor(plan.commands[0], request, dict(plan.bindings))

    def test_host_and_executor_integrate_commit_then_advance_composition(self):
        self.bind_mutation_host()
        (self.fx.stage / 'app.txt').write_text('verified candidate\n')
        self.fx.git(self.fx.stage, 'add', 'app.txt')
        self.fx.git(self.fx.stage, 'commit', '-qm', f'{STAGE}: candidate')
        candidate = self.fx.head(self.fx.stage)
        candidate_parent = self.fx.git(self.fx.stage, 'rev-parse', 'HEAD^').stdout.strip()
        patch = subprocess.run(
            ['/usr/bin/git', '-C', str(self.fx.stage), 'diff', '--binary', '--full-index',
             '--no-ext-diff', '--no-textconv', '--no-renames', candidate_parent, candidate, '--'],
            check=True, capture_output=True,
        ).stdout
        expected = hashlib.sha256(patch).hexdigest()
        request = self.fx.request(
            'integrate', actor_id=COORDINATOR, candidate_sha=candidate,
            expected_patch_sha256=expected, test_selectors=['tests/test_sample.py'],
            verifier_receipt_sha256='a' * 64, message=f'{STAGE}: integrate candidate',
            allowed_paths=['app.txt'],
        )
        paths = ('app.txt',)
        commands = (
            self.fx.fence('check'),
            CommandPlan(('git', '-C', str(self.fx.root), 'cherry-pick', '--no-commit', candidate),
                        str(self.fx.root), EnvClass.GIT_MUTATION, 'cherry-pick'),
            CommandPlan(('git', '-C', str(self.fx.root), 'diff', '--cached', '--check'),
                        str(self.fx.root), EnvClass.READ_ONLY, 'check'),
            CommandPlan((sys.executable, '-m', 'pytest', '-q', '--disable-warnings', '--maxfail=1',
                         '--', 'tests/test_sample.py'), str(self.fx.root), EnvClass.HERMETIC_TEST, 'test'),
            CommandPlan(('git', '-C', str(self.fx.root), 'diff', '--cached', '--binary',
                         '--full-index', '--'), str(self.fx.root), EnvClass.READ_ONLY, 'patch'),
            CommandPlan(('git', '-C', str(self.fx.root), 'commit', '-m', request['message']),
                        str(self.fx.root), EnvClass.GIT_MUTATION, 'commit'),
        )
        advance = self.fx.fence('advance', integration_head=RESULTING_HEAD)
        plan = self.fx.plan(
            request, ActorRole.COORDINATOR, commands,
            checkpoints=(
                GitCheckpoint(1, str(self.fx.root), self.fx.integration_head, paths, expected, True, True),
                GitCheckpoint(3, str(self.fx.root), self.fx.integration_head, paths, expected, True, True),
            ),
            followups=(FollowupCommandPlan(
                advance.argv, advance.cwd, advance.env_class, RESULTING_HEAD, 'advance'),),
            **self.fx.commit_bindings(),
        )
        outcome = execute_plan(
            plan, request, runner=RecordingRunner(), pinned_paths=self.fx.pins,
            before_mutation=lambda boundary: self.host._before_mutation(
                boundary, plan, request, self.fx.pins),
        )
        self.assertEqual('SUCCESS', outcome['status'], outcome)
        self.assertEqual(request['integration_head'],
                         self.fx.git(self.fx.root, 'rev-parse', 'HEAD^').stdout.strip())

    def test_host_and_executor_next_stage_commit_then_advance_composition(self):
        self.bind_mutation_host()
        order = self.fx.root / 'docs/refactor/two-server/execution-order.yaml'
        order.parent.mkdir(parents=True, exist_ok=True)
        order.write_text('stages:\n  P1-01: {state: IN_PROGRESS}\n')
        self.fx.git(self.fx.root, 'add', str(order.relative_to(self.fx.root)))
        from scripts.two_site_agent_executor import observe_git
        patch_sha = observe_git(self.fx.root).staged_patch_sha256
        request = self.fx.request(
            'next-stage-commit', actor_id=COORDINATOR, next_stage='P1-01',
            expected_patch_sha256=patch_sha, prepare_receipt_sha256='a' * 64,
            message='P1-01: open bounded Stage',
        )
        command = CommandPlan(
            ('git', '-C', str(self.fx.root), 'commit', '-m', request['message']),
            str(self.fx.root), EnvClass.GIT_MUTATION, 'transition',
        )
        advance = self.fx.fence('advance', integration_head=RESULTING_HEAD)
        plan = self.fx.plan(
            request, ActorRole.COORDINATOR, (command,),
            followups=(FollowupCommandPlan(
                advance.argv, advance.cwd, advance.env_class, RESULTING_HEAD, 'advance'),),
            next_stage='P1-01', patch=patch_sha, prepare_receipt='receipt.json',
            **self.fx.commit_bindings(),
        )
        outcome = execute_plan(
            plan, request, runner=RecordingRunner(), pinned_paths=self.fx.pins,
            before_mutation=lambda boundary: self.host._before_mutation(
                boundary, plan, request, self.fx.pins),
        )
        self.assertEqual('SUCCESS', outcome['status'], outcome)
        self.assertEqual(request['integration_head'],
                         self.fx.git(self.fx.root, 'rev-parse', 'HEAD^').stdout.strip())

    def test_review_supervisor_rejects_bundle_swap_before_codex(self):
        bundle = self.fx.root / f'.local/two-site-refactor/review-bundles/{STAGE}-{ASSIGNMENT}'
        bundle.mkdir(parents=True, exist_ok=True)
        names = ('assignment.md', 'STAGE_REPORT.md', 'FINAL_REVIEW_REQUEST.md', 'HANDOFF.md', 'commit.txt')
        bindings = {'bundle': bundle.relative_to(self.fx.root).as_posix()}
        for name in names:
            (bundle / name).write_text(name + '\n')
            key = 'review_input_' + name.replace('.', '_').lower() + '_sha256'
            bindings[key] = hashlib.sha256((bundle / name).read_bytes()).hexdigest()
        (bundle / 'HANDOFF.md').write_text('swapped\n')
        self.host._binary = Mock(return_value=Path('/bin/true'))
        command = CommandPlan(
            (str(self.fx.pins.final_review_bridge),), str(self.fx.root),
            EnvClass.EXTERNAL_CODEX_REVIEW, 'review',
        )
        with patch('scripts.two_site_agent_host._run_bounded_process') as runner:
            with self.assertRaises(TransportError):
                self.host._review_supervisor(command, {}, bindings)
        runner.assert_not_called()

    def test_review_supervisor_rejects_bundle_swap_during_codex(self):
        bundle = self.fx.root / f'.local/two-site-refactor/review-bundles/{STAGE}-{ASSIGNMENT}'
        bundle.mkdir(parents=True, exist_ok=True)
        names = ('assignment.md', 'STAGE_REPORT.md', 'FINAL_REVIEW_REQUEST.md', 'HANDOFF.md', 'commit.txt')
        bindings = {'bundle': bundle.relative_to(self.fx.root).as_posix()}
        for name in names:
            (bundle / name).write_text(name + '\n')
            bindings['review_input_' + name.replace('.', '_').lower() + '_sha256'] = hashlib.sha256(
                (bundle / name).read_bytes()).hexdigest()
        self.host._binary = Mock(return_value=Path('/bin/true'))
        command = CommandPlan(
            (str(self.fx.pins.final_review_bridge),), str(self.fx.root),
            EnvClass.EXTERNAL_CODEX_REVIEW, 'review',
        )

        def mutate(*args, **kwargs):
            (bundle / 'HANDOFF.md').write_text('changed during review\n')
            return subprocess.CompletedProcess(command.argv, 0, b'ok', b'')

        with patch('scripts.two_site_agent_host._run_bounded_process', side_effect=mutate):
            with self.assertRaises(TransportError):
                self.host._review_supervisor(command, {}, bindings)

    def test_closeout_prepare_rejects_final_receipt_swap(self):
        self.host._validated_review_receipt = Mock(return_value=(
            {'reviewed_at_utc': '2026-09-05T00:00:00Z', 'verdict': 'APPROVE',
             'reviewed_head_sha': self.fx.integration_head}, 'b' * 64,
        ))
        plan = SimpleNamespace(bindings=(('final_receipt_file_sha256', 'a' * 64),))
        with self.assertRaises(TransportError):
            self.host._prepare_closeout(
                plan, {'accepted_head': self.fx.integration_head}, self.assignment, self.fx.pins)

    def test_next_stage_transition_stages_only_execution_order(self):
        order = self.fx.root / 'docs/refactor/two-server/execution-order.yaml'
        order.parent.mkdir(parents=True, exist_ok=True)
        order.write_text('stages:\n  P1-01: {state: PROPOSED, section: 1, depends_on: [], production: false}\n')
        self.fx.git(self.fx.root, 'add', '--all')
        self.fx.git(self.fx.root, 'commit', '-qm', 'next stage fixture baseline')
        self.fx.integration_head = self.fx.head(self.fx.root)
        self.fx.pins = self.fx.make_pins()
        result = self.host._prepare_next_stage_transition(
            {'integration_head': self.fx.integration_head, 'next_stage': 'P1-01'},
            self.assignment, self.fx.pins,
        )
        self.assertEqual(['docs/refactor/two-server/execution-order.yaml'], result['staged_paths'])
        self.assertIn('state: IN_PROGRESS', order.read_text())

    def test_stage_bind_preparation_creates_only_derived_assignment_and_worktree(self):
        _, _, result, registry = self.prepare_stage_bind_fixture()
        worktree = Path(result['worktree_path'])
        self.assertTrue(worktree.is_dir())
        self.assertEqual(self.fx.integration_head, self.fx.head(worktree))
        assignment = (self.fx.root / '.local/two-site-refactor/assignments' /
                      'WA-P1-01-REPO-CLEANUP-20260905-001.md')
        self.assertIn('external_access: NONE', assignment.read_text())
        registered = __import__('yaml').safe_load(registry.read_text())['stage_worktrees']
        self.assertEqual('READY', registered[1]['status'])
        journal = json.loads(self.host._stage_bind_journal_path.read_text())
        self.assertEqual('REGISTRY_WRITTEN', journal['phase'])

    def test_interrupted_pre_bind_transaction_rolls_back_with_terminal_failure(self):
        request, _, result, registry = self.prepare_stage_bind_fixture()
        journal = self.write_stage_bind_transport_state(request)
        fence_path = self.fx.root / '.local/two-site-refactor/orchestrator-fence.json'
        fence_path.write_text(json.dumps({
            'status': 'ACTIVE', 'active_assignment_id': ASSIGNMENT, 'epoch': EPOCH,
            'integration_head_sha': self.fx.integration_head,
            'coordinator_instance_id': COORDINATOR,
        }))
        self.host._recover_stage_bind_transaction()
        self.assertFalse(Path(result['worktree_path']).exists())
        self.assertFalse(Path(journal['assignment_path']).exists())
        self.assertFalse(self.host._stage_bind_journal_path.exists())
        restored = yaml.safe_load(registry.read_text())
        self.assertEqual([ASSIGNMENT], [item['assignment_id'] for item in restored['stage_worktrees']])
        receipt = json.loads((self.host.state / (request['id'] + '.receipt.json')).read_text())
        self.assertEqual('FAILED', receipt['outcome']['status'])

    def test_completed_bind_recovery_retires_only_exact_prior_worktree(self):
        request, _, result, registry_path = self.prepare_stage_bind_fixture()
        self.write_stage_bind_transport_state(request)
        self.mark_stage_bind_state(request, registry_path)
        self.host._recover_stage_bind_transaction()
        self.assertFalse(self.fx.stage.exists())
        self.assertTrue(Path(result['worktree_path']).is_dir())
        self.assertFalse(self.host._stage_bind_journal_path.exists())
        recovered = yaml.safe_load(registry_path.read_text())
        old = next(item for item in recovered['stage_worktrees']
                   if item['assignment_id'] == ASSIGNMENT)
        self.assertEqual('REMOVED', old['status'])
        receipt = json.loads((self.host.state / (request['id'] + '.receipt.json')).read_text())
        self.assertEqual('SUCCESS', receipt['outcome']['status'])

    def test_started_journal_with_no_effects_is_idempotently_rolled_back(self):
        request, _, result, registry_path = self.prepare_stage_bind_fixture()
        journal = json.loads(self.host._stage_bind_journal_path.read_text())
        self.host._rollback_stage_bind(journal, self.fx.pins)
        self.host._write_stage_bind_journal(journal, 'STARTED')
        self.write_stage_bind_transport_state(request)
        (self.fx.root / '.local/two-site-refactor/orchestrator-fence.json').write_text(json.dumps({
            'status': 'ACTIVE', 'active_assignment_id': ASSIGNMENT, 'epoch': EPOCH,
            'integration_head_sha': self.fx.integration_head,
            'coordinator_instance_id': COORDINATOR,
        }))
        self.host._recover_stage_bind_transaction()
        self.assertFalse(Path(result['worktree_path']).exists())
        self.assertEqual(1, len(yaml.safe_load(registry_path.read_text())['stage_worktrees']))
        receipt = json.loads((self.host.state / (request['id'] + '.receipt.json')).read_text())
        self.assertEqual('FAILED', receipt['outcome']['status'])

    def test_reconciling_stage_bind_is_rolled_forward_then_retired(self):
        from scripts import two_site_agent_host as host_module

        request, _, result, registry_path = self.prepare_stage_bind_fixture()
        self.write_stage_bind_transport_state(request)
        fence_path = self.fx.root / '.local/two-site-refactor/orchestrator-fence.json'
        fence_path.write_text(json.dumps({
            'status': 'RECONCILING', 'binding_kind': 'STAGE',
            'previous_assignment_id': ASSIGNMENT,
            'active_assignment_id': request['next_assignment'], 'epoch': EPOCH,
            'integration_head_sha': self.fx.integration_head,
            'coordinator_instance_id': COORDINATOR,
        }))
        original = host_module._run_bounded_process

        def finish_fence(argv, **kwargs):
            if 'stage-bind' in argv:
                self.mark_stage_bind_state(request, registry_path)
                return subprocess.CompletedProcess(argv, 0, b'BOUND\n', b'')
            return original(argv, **kwargs)

        with patch.object(host_module, '_run_bounded_process', side_effect=finish_fence):
            self.host._recover_stage_bind_transaction()
        self.assertFalse(self.fx.stage.exists())
        self.assertTrue(Path(result['worktree_path']).is_dir())
        receipt = json.loads((self.host.state / (request['id'] + '.receipt.json')).read_text())
        self.assertEqual('SUCCESS', receipt['outcome']['status'])

    def test_recovery_finishes_registry_after_worktree_removal_crash(self):
        request, _, result, registry_path = self.prepare_stage_bind_fixture()
        self.write_stage_bind_transport_state(request)
        self.mark_stage_bind_state(request, registry_path)
        removed = self.fx.git(self.fx.root, 'worktree', 'remove', str(self.fx.stage))
        self.assertEqual(0, removed.returncode)
        old_before = next(item for item in yaml.safe_load(registry_path.read_text())['stage_worktrees']
                          if item['assignment_id'] == ASSIGNMENT)
        self.assertEqual('CLOSED', old_before['status'])
        self.host._recover_stage_bind_transaction()
        self.assertTrue(Path(result['worktree_path']).is_dir())
        old_after = next(item for item in yaml.safe_load(registry_path.read_text())['stage_worktrees']
                         if item['assignment_id'] == ASSIGNMENT)
        self.assertEqual('REMOVED', old_after['status'])

    def test_retirement_rejects_workspace_root_branch_drift_and_incomplete_stage(self):
        _, _, _, registry_path = self.prepare_stage_bind_fixture()
        journal = json.loads(self.host._stage_bind_journal_path.read_text())
        registry = yaml.safe_load(registry_path.read_text())
        old = next(item for item in registry['stage_worktrees']
                   if item['assignment_id'] == ASSIGNMENT)

        old['branch'] = 'refactor/stage/wrong-branch'
        with self.assertRaisesRegex(TransportError, 'registry/Stage identity'):
            self.host._validate_retirement_candidate(journal, self.fx.pins, registry)
        old['branch'] = journal['old_branch']

        unsafe = {**journal, 'old_worktree': str(self.fx.container)}
        old['path'] = str(self.fx.container)
        with self.assertRaisesRegex(TransportError, 'registry/Stage identity'):
            self.host._validate_retirement_candidate(unsafe, self.fx.pins, registry)
        old['path'] = journal['old_worktree']

        order = self.fx.root / 'docs/refactor/two-server/execution-order.yaml'
        order.write_text(order.read_text().replace('state: COMPLETE', 'state: IN_PROGRESS'))
        with self.assertRaisesRegex(TransportError, 'registry/Stage identity'):
            self.host._validate_retirement_candidate(journal, self.fx.pins, registry)

    def test_prejournal_stage_bind_collision_returns_terminal_blocked(self):
        self.bind_mutation_host()
        self.host.manifest.update({
            'stage_workspace_root': str(self.fx.container),
            'binary_sha256': {
                'git': {'path': str(self.fx.pins.git_binary), 'sha256': self.fx.pins.git_sha256},
                'python': {'path': str(self.fx.pins.python_binary), 'sha256': self.fx.pins.python_sha256},
            },
            'source_sha256': {
                'scripts/two_site_orchestrator_fence.py': self.fx.pins.fence_sha256,
                'scripts/two_site_codex_final_review.sh': self.fx.pins.final_review_sha256,
                'scripts/two_site_codex_closeout_review.sh': self.fx.pins.closeout_review_sha256,
            },
        })
        request = self.fx.request(
            'next-stage-bind', actor_id=COORDINATOR, next_stage='P1-01',
            next_assignment='WA-P1-01-COLLISION-20260905-001',
            next_stage_commit_receipt_sha256='a' * 64, contract={},
        )
        plan = self.fx.plan(request, ActorRole.COORDINATOR, ())
        transport = NativeTransport(
            self.fx.root, self.host.state, (self.fx.root, self.fx.stage),
            lambda event, supplied: plan,
            lambda supplied_plan, supplied_request: self.host._execute(
                supplied_plan, supplied_request),
        )
        event = {
            'conversationId': COORDINATOR, 'stepIdx': 51, 'modelName': MODEL,
            'workspacePaths': [str(self.fx.root), str(self.fx.stage)],
            'toolCall': {'name': 'write_to_file', 'args': {
                'TargetFile': str(transport.requests / (request['id'] + '.json')),
                'CodeContent': json.dumps(request), 'Overwrite': False,
                'IsArtifact': False, 'Description': 'typed collision fixture',
            }},
        }
        with patch.object(self.host, '_prepare_stage_bind',
                          side_effect=TransportError('derived branch already exists')):
            self.assertEqual('allow', transport.pre(event)['decision'])
            Path(event['toolCall']['args']['TargetFile']).write_text(json.dumps(request))
            transport.post(event)
        receipt = json.loads((self.host.state / (request['id'] + '.receipt.json')).read_text())
        outcome = receipt['outcome']
        self.assertEqual('BLOCKED', outcome['status'])
        self.assertEqual('next-stage-bind', outcome['operation'])
        self.assertNotIn('authority_sha256', outcome)
        transport.admission_check()
        self.assertFalse(self.host._stage_bind_journal_path.exists())


if __name__ == '__main__':
    unittest.main()
