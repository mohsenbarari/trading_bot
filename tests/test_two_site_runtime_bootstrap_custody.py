"""Independent full-record and real custody proofs for the one-off bootstrap."""
from __future__ import annotations

import contextlib
import copy
import importlib
import json
import unittest
from unittest import mock

from scripts.two_site_assignment_scope import AssignmentScope
from scripts.two_site_codex_autopilot import rescope_epoch_basename
import scripts.two_site_maintenance_transition_record as codec
import scripts.two_site_maintenance_transition_publish as pub
import scripts.two_site_maintenance_transition_storage as reader
from scripts.two_site_maintenance_transition_record import (
    validate_maintenance_transition_record as validate,
    build_maintenance_transition_record as build,
    parse_maintenance_transition_record as parse,
    serialize_maintenance_transition_record as serialize,
)

# Independent fixed oracle DATA; never derived from the codec.
DEFAULT = frozenset({
    "docs/refactor/two-server/templates/CODEX_AUTOPILOT_PROMPT.md",
    "docs/refactor/two-server/templates/GEMINI_TASK_PROMPT.md",
    "scripts/two_site_autopilot_monitor.sh",
    "scripts/two_site_autopilot_tmux.sh",
    "scripts/two_site_codex_autopilot.py",
    "scripts/two_site_cycle_metrics.py",
    "scripts/two_site_cycle_tools.py",
})
RUNNER = "scripts/two_site_proposal_runner.py"
EXPANDED = DEFAULT | {RUNNER}
PREFIX = "ae00-runtime-tool-bootstrap-20261005-330"
FROZEN = {
    "schema_version": "1.0",
    "kind": "REVIEWED_MAINTENANCE_TRANSITION",
    "prior_checkpoint_sha256": "4d0b2482a47809827db196a22e05840a3884bc23bb02bb43887f3b6cf8e80cba",
    "prior_head": "3a0f5db6d948c3aa17797e1cf5d771b76145e4e1",
    "new_repository": {
        "head": "2" * 40,
        "status": "",
        "protected_refs": {
            "refs/heads/main": "3" * 40,
            "refs/remotes/origin/main": "3" * 40,
            "refs/heads/candidate/wa-ir-standby-v1": "3" * 40,
        },
        "control_blobs": {
            **dict.fromkeys(sorted(DEFAULT), "4" * 40),
            "scripts/two_site_cycle_packet.py": "4" * 40,
            "scripts/two_site_proposal_runner.py": "b" * 40,
            "scripts/two_site_provider_broker.py": "4" * 40,
            "scripts/two_site_assignment_scope.py": "4" * 40,
            "scripts/two_site_preparation_contract.py": "4" * 40,
            "scripts/two_site_preparation_io.py": "4" * 40,
            "scripts/two_site_preparation_boundary.py": "4" * 40,
            "scripts/two_site_preparation_runtime_gate.py": "4" * 40,
            "docs/refactor/two-server/templates/gemini-runtime-contract.json": "4" * 40,
            "docs/refactor/two-server/templates/codex-autopilot-cycle.schema.json": "4" * 40,
            ".agents/skills/two-site-refactor/SKILL.md": "4" * 40,
            ".agents/rules/two-site-refactor-safety.md": "4" * 40,
        },
        "assignment_scope": {
            "assignment_id": "bootstrap-fixture",
            "stage_id": "P1-00b",
            "sha256": "5" * 64,
            "expires_at": "2026-10-30T23:59:00Z",
            "allowed_paths": ["scripts/two_site_proposal_runner.py"],
            "forbidden_paths": ["api/**"],
        },
    },
    "maintenance_commits": [
        {
            "head": "1" * 40,
            "parent": "3a0f5db6d948c3aa17797e1cf5d771b76145e4e1",
            "paths": [
                "scripts/two_site_maintenance_transition_publish.py",
                "scripts/two_site_maintenance_transition_record.py",
                "scripts/two_site_maintenance_transition_storage.py",
                "tests/test_two_site_runtime_bootstrap_custody.py",
            ],
            "worker_run_id": PREFIX + "-custody-author-0",
            "reviewer_run_id": PREFIX + "-custody-reviewer-0",
            "proposal_sha256": "6" * 64,
            "test_evidence_sha256": "7" * 64,
        },
        {
            "head": "2" * 40,
            "parent": "1" * 40,
            "paths": [
                "scripts/two_site_proposal_runner.py",
                "scripts/two_site_proposal_runtime_profile.py",
                "tests/test_two_site_proposal_runner.py",
                "tests/test_two_site_runtime_proposal_profile.py",
            ],
            "worker_run_id": PREFIX + "-runtime-author-0",
            "reviewer_run_id": PREFIX + "-runtime-reviewer-0",
            "proposal_sha256": "8" * 64,
            "test_evidence_sha256": "9" * 64,
        },
    ],
}


def raw_literal(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode()


FROZEN_BYTES = raw_literal(FROZEN)


def fresh():
    return copy.deepcopy(FROZEN)


def prior_repository():
    repo = copy.deepcopy(FROZEN["new_repository"])
    repo["head"] = FROZEN["prior_head"]
    repo["control_blobs"][RUNNER] = "4" * 40
    repo["assignment_scope"]["assignment_id"] = "prior-fixture"
    return repo


def typed(value: object, ordered=True) -> tuple:
    """Whole typed snapshot; foreign hooks never run."""
    kind = type(value)
    if kind is dict:
        items = value.items() if ordered else ((k, value[k]) for k in sorted(value))
        return ("dict", tuple((typed(k, ordered), typed(v, ordered)) for k, v in items))
    if kind is list:
        return ("list", tuple(typed(v, ordered) for v in value))
    if kind is str or kind is bool or kind is int or kind is float or kind is bytes:
        return (kind.__name__, value)
    return ("foreign", id(value))


def whole(value: object) -> object:
    return typed(value, ordered=False)


def at(value, path):
    for key in path:
        value = value[key]
    return value


def change(record, path, value):
    parent = at(record, path[:-1])
    if type(parent[path[-1]]) is list and type(value) is list:
        parent[path[-1]][:] = value
    else:
        parent[path[-1]] = value


class HookString(str):
    armed = False
    hashes = 0
    equalities = 0

    def __hash__(self):
        if self.armed:
            type(self).hashes += 1
            raise AssertionError("foreign hash executed")
        return str.__hash__(self)

    def __eq__(self, other):
        if self.armed:
            type(self).equalities += 1
            raise AssertionError("foreign equality executed")
        return str.__eq__(self, other)


class DictSubclass(dict):
    def __len__(self):
        raise AssertionError("foreign dict length executed")

    def __iter__(self):
        raise AssertionError("foreign dict iteration executed")


class ListSubclass(list):
    def __len__(self):
        raise AssertionError("foreign list length executed")

    def __iter__(self):
        raise AssertionError("foreign list iteration executed")


class TestBootstrapWholeCodec(unittest.TestCase):
    eq = unittest.TestCase.assertEqual

    def positive(self, caller: dict) -> None:
        oracle = fresh()
        before, expected = typed(caller), whole(oracle)
        oracle_state = typed(oracle)
        self.eq(codec.maintenance_control_paths_for_record(caller), EXPANDED)
        for operation in (
            lambda: validate(caller),
            lambda: build(caller["prior_checkpoint_sha256"], caller["prior_head"], caller["new_repository"], caller["maintenance_commits"]),
            lambda: parse(FROZEN_BYTES),
        ):
            result = operation()
            self.eq(whole(result), expected)
            self.eq(typed(caller), before)
            self.eq(typed(oracle), oracle_state)
        self.eq(serialize(caller), FROZEN_BYTES)
        self.eq(typed(caller), before)
        self.eq(typed(oracle), oracle_state)

    def rejecting_codec_witness(self, caller: object) -> None:
        before = typed(caller)
        with self.assertRaises(codec.MaintenanceTransitionRecordError):
            validate(caller)
        self.eq(typed(caller), before)

    def negative(self, caller: object) -> None:
        before = typed(caller)
        self.eq(codec.maintenance_control_paths_for_record(caller), DEFAULT)
        self.eq(typed(caller), before)
        self.rejecting_codec_witness(caller)

    def test_complete_independent_oracle_and_default_constants(self):
        self.eq(codec.ALLOWED_CONTROL_PATHS, DEFAULT)
        self.eq(codec.ALLOWED_MAINTENANCE_CONTROL_PATHS, DEFAULT)
        self.positive(fresh())

    def test_each_sealed_condition_positive_fault_same_positive(self):
        cases = [
            (("schema_version",), "2.0"),
            (("kind",), "UNREVIEWED"),
            (("prior_checkpoint_sha256",), "a" * 64),
            (("prior_head",), "a" * 40),
            (("new_repository", "assignment_scope", "stage_id"), "P1-01"),
            (("new_repository", "status"), False),
            (("new_repository", "head"), "a" * 40),
            (("maintenance_commits", 1, "parent"), "a" * 40),
            (("maintenance_commits", 0, "proposal_sha256"), "bad"),
            (("maintenance_commits", 1, "test_evidence_sha256"), "bad"),
        ]
        for index in (0, 1):
            for key in ("worker_run_id", "reviewer_run_id"):
                original = FROZEN["maintenance_commits"][index][key]
                for bad in (original + "\n", original + "-other", original.replace("330", "329"), original.replace("20261005", "20261004"), "x" * 129, True):
                    cases.append((("maintenance_commits", index, key), bad))
            cases.append((("maintenance_commits", index, "worker_run_id"), FROZEN["maintenance_commits"][index]["reviewer_run_id"]))
            cases.append((("maintenance_commits", index, "reviewer_run_id"), FROZEN["maintenance_commits"][index]["worker_run_id"]))
            paths = FROZEN["maintenance_commits"][index]["paths"]
            cases.extend([
                (("maintenance_commits", index, "paths"), paths + ["tests/test_two_site_other.py"]),
                (("maintenance_commits", index, "paths"), paths[:-1]),
                (("maintenance_commits", index, "paths"), list(reversed(paths))),
                (("maintenance_commits", index, "paths"), [*paths[:-1], "../escape.py"]),
            ])
        frozen = typed(FROZEN)
        for path, value in cases:
            with self.subTest(path=path, kind=type(value).__name__):
                positive = fresh()
                faulty = fresh()
                change(faulty, path, copy.deepcopy(value))
                self.positive(positive)
                self.negative(faulty)
                self.positive(positive)
                self.eq(typed(FROZEN), frozen)

    def test_extra_missing_keys_and_third_commit(self):
        for path in ((), ("new_repository",), ("new_repository", "assignment_scope"), ("maintenance_commits", 0), ("maintenance_commits", 1)):
            for action in ("extra", "missing"):
                positive, bad = fresh(), fresh()
                obj = at(bad, path)
                if action == "extra":
                    obj["unapproved"] = "x"
                else:
                    del obj[next(iter(obj))]
                self.positive(positive)
                self.negative(bad)
                self.positive(positive)
        bad = fresh()
        bad["maintenance_commits"].append(copy.deepcopy(bad["maintenance_commits"][1]))
        self.negative(bad)
        self.positive(fresh())
        for bad in (None, [], DictSubclass(FROZEN)):
            self.negative(bad)

    def test_foreign_keys_values_containers_never_execute_hooks(self):
        locations = ((), ("new_repository",), ("new_repository", "assignment_scope"), ("new_repository", "protected_refs"), ("new_repository", "control_blobs"), ("maintenance_commits", 0), ("maintenance_commits", 1))
        for path in locations:
            bad = fresh()
            HookString.armed = False
            obj = at(bad, path)
            foreign = {HookString(k): v for k, v in obj.items()}
            if path:
                change(bad, path, foreign)
            else:
                bad = foreign
            HookString.hashes = HookString.equalities = 0
            HookString.armed = True
            try:
                self.negative(bad)
                self.eq((HookString.hashes, HookString.equalities), (0, 0))
            finally:
                HookString.armed = False
        for path in (("maintenance_commits", 0, "paths", 0), ("maintenance_commits", 1, "paths", 0), ("maintenance_commits", 0, "worker_run_id")):
            bad = fresh()
            change(bad, path, HookString(at(bad, path)))
            HookString.hashes = HookString.equalities = 0
            HookString.armed = True
            try:
                self.negative(bad)
                self.eq((HookString.hashes, HookString.equalities), (0, 0))
            finally:
                HookString.armed = False
        for path in (("new_repository",), ("new_repository", "assignment_scope")):
            bad = fresh()
            change(bad, path, DictSubclass(at(bad, path)))
            self.negative(bad)
        for path in (("maintenance_commits",), ("maintenance_commits", 0, "paths"), ("new_repository", "assignment_scope", "allowed_paths")):
            bad = fresh()
            change(bad, path, ListSubclass(at(bad, path)))
            self.negative(bad)

    def test_detachment_every_nested_branch_whole_sibling_custody(self):
        branches = [
            (("new_repository", "head"), "a" * 40),
            (("new_repository", "protected_refs", "refs/heads/main"), "a" * 40),
            (("new_repository", "control_blobs", RUNNER), "a" * 40),
            (("new_repository", "assignment_scope", "assignment_id"), "mutated"),
            (("new_repository", "assignment_scope", "allowed_paths"), ["mutated"]),
            (("new_repository", "assignment_scope", "forbidden_paths"), ["mutated"]),
        ]
        for index in (0, 1):
            branches.extend([(("maintenance_commits", index, "paths"), ["mutated"]), (("maintenance_commits", index, "worker_run_id"), "mutated")])
        for path, value in branches:
            caller = fresh()
            oracle = fresh()
            before, oracle_state = typed(caller), typed(oracle)
            outputs = [validate(caller), build(caller["prior_checkpoint_sha256"], caller["prior_head"], caller["new_repository"], caller["maintenance_commits"]), parse(FROZEN_BYTES)]
            for result in outputs:
                expected = copy.deepcopy(oracle)
                change(expected, path, value)
                change(result, path, value)
                self.eq(whole(result), whole(expected))
                self.eq(typed(caller), before)
                self.eq(typed(oracle), oracle_state)

    def test_actual_policy_mutant_breaks_same_public_witness_then_restored(self):
        original = codec.maintenance_control_paths_for_record
        caller = fresh()
        bad = fresh()
        bad["prior_checkpoint_sha256"] = "a" * 64
        self.positive(caller)
        self.rejecting_codec_witness(bad)
        try:
            with mock.patch.object(codec, "maintenance_control_paths_for_record", return_value=EXPANDED):
                with self.assertRaises(AssertionError):
                    self.rejecting_codec_witness(bad)
        finally:
            self.assertIs(codec.maintenance_control_paths_for_record, original)
            self.rejecting_codec_witness(bad)
            self.positive(caller)


class TestBootstrapRealCustody(unittest.TestCase):
    eq = unittest.TestCase.assertEqual

    @contextlib.contextmanager
    def env(self, *, record=None, checkpoint=None, cp_sha=None):
        legacy = importlib.import_module("tests.test_two_site_maintenance_transition_publish")
        env = legacy.TestMaintenanceTransitionPublish("runTest")
        env.setUp()
        try:
            frozen = copy.deepcopy(FROZEN if record is None else record)
            norm = copy.deepcopy(frozen["new_repository"])
            raw_repo = copy.deepcopy(norm)
            raw_repo["assignment_scope"] = AssignmentScope(**raw_repo["assignment_scope"])
            cp = prior_repository()
            if checkpoint is not None:
                cp = checkpoint
            sha = FROZEN["prior_checkpoint_sha256"] if cp_sha is None else cp_sha
            with env._mock_env(raw_repo=raw_repo, chk_repo=cp, chk_sha=sha):
                yield env, frozen
        finally:
            env.tearDown()

    def leaf(self, env, record):
        directory = env.state_root / "maintenance-transitions"
        directory.mkdir(mode=0o700, exist_ok=True)
        return directory / (rescope_epoch_basename(record["new_repository"]) + ".json")

    def write_leaf(self, env, record, raw=None):
        leaf = self.leaf(env, record)
        leaf.write_bytes(raw_literal(record) if raw is None else raw)
        leaf.chmod(0o600)
        return leaf

    def assert_read_whole(self, env, record):
        before = typed(record)
        result = reader.read_maintenance_transition_locked(env.root, env.state_root)
        self.eq(whole(result), whole(record))
        self.eq(typed(record), before)
        self.assertTrue(env.broker_entered and env.broker_exited)
        result["new_repository"]["control_blobs"][RUNNER] = "mutated"
        self.eq(typed(record), before)
        again = reader.read_maintenance_transition_locked(env.root, env.state_root)
        self.eq(whole(again), whole(record))

    def test_actual_publisher_and_reader_consult_complete_bound_policy(self):
        with self.env() as (env, record):
            before = typed(record)
            with mock.patch.object(pub, "maintenance_control_paths_for_record", wraps=codec.maintenance_control_paths_for_record) as pub_spy, mock.patch.object(reader, "maintenance_control_paths_for_record", wraps=codec.maintenance_control_paths_for_record) as read_spy:
                result = pub.publish_maintenance_transition_locked(env.root, env.state_root, record["maintenance_commits"])
                self.eq(whole(result), whole(record))
                self.eq(self.leaf(env, record).read_bytes(), FROZEN_BYTES)
                self.eq(pub_spy.call_count, 1)
                self.eq(whole(pub_spy.call_args.args[0]), whole(record))
                self.assert_read_whole(env, record)
                self.eq(read_spy.call_count, 2)
                for call in read_spy.call_args_list:
                    self.eq(whole(call.args[0]), whole(record))
            self.eq(typed(record), before)

    def test_bad_publish_has_no_leaf_then_same_good_publish(self):
        cases = ("checkpoint", "head", "actor", "path", "undeclared-runner", "foreign-control")
        for case in cases:
            with self.subTest(case=case), self.env() as (env, record):
                bad_chain = copy.deepcopy(record["maintenance_commits"])
                prior = prior_repository()
                if case == "actor":
                    bad_chain[0]["worker_run_id"] += "-other"
                if case == "path":
                    bad_chain[1]["paths"].append("tests/test_two_site_other.py")
                if case == "undeclared-runner":
                    bad_chain[1]["paths"].remove(RUNNER)
                before = typed(bad_chain)
                extra = contextlib.ExitStack()
                with extra:
                    if case == "checkpoint":
                        extra.enter_context(mock.patch.object(pub, "_load_and_audit_accepted_checkpoint", return_value=({"repository": prior}, "a" * 64)))
                    if case == "head":
                        prior["head"] = "a" * 40
                        extra.enter_context(mock.patch.object(pub, "_load_and_audit_accepted_checkpoint", return_value=({"repository": prior}, record["prior_checkpoint_sha256"])))
                    if case == "foreign-control":
                        changed = copy.deepcopy(record["new_repository"])
                        changed["control_blobs"]["scripts/two_site_provider_broker.py"] = "c" * 40
                        changed["assignment_scope"] = AssignmentScope(**changed["assignment_scope"])
                        extra.enter_context(mock.patch.object(pub, "validate_repository", return_value=changed))
                    with self.assertRaises(pub.MaintenanceTransitionPublishError):
                        pub.publish_maintenance_transition_locked(env.root, env.state_root, bad_chain)
                self.eq(typed(bad_chain), before)
                self.assertFalse(self.leaf(env, record).exists())
                good = pub.publish_maintenance_transition_locked(env.root, env.state_root, record["maintenance_commits"])
                self.eq(whole(good), whole(record))
                self.assert_read_whole(env, record)

    def test_reader_missing_foreign_malformed_and_bad_binding_then_same_good(self):
        for case in ("missing", "foreign-leaf", "malformed", "checkpoint", "head", "actor", "path", "undeclared-runner", "foreign-control"):
            with self.subTest(case=case), self.env() as (env, record):
                bad = copy.deepcopy(record)
                extra = contextlib.ExitStack()
                foreign = None
                leaf = self.leaf(env, record)
                if case == "foreign-leaf":
                    foreign = leaf.parent / "foreign.json"
                    foreign.write_bytes(b"{}\n")
                    foreign.chmod(0o600)
                elif case != "missing":
                    if case == "checkpoint":
                        bad["prior_checkpoint_sha256"] = "a" * 64
                    if case == "head":
                        bad["prior_head"] = "a" * 40
                    if case == "actor":
                        bad["maintenance_commits"][0]["worker_run_id"] += "-other"
                    if case == "path":
                        bad["maintenance_commits"][1]["paths"].append("tests/test_two_site_other.py")
                    if case == "undeclared-runner":
                        bad["maintenance_commits"][1]["paths"].remove(RUNNER)
                    if case == "foreign-control":
                        bad["new_repository"]["control_blobs"]["scripts/two_site_provider_broker.py"] = "c" * 40
                    self.write_leaf(env, bad, b"not JSON\n" if case == "malformed" else None)
                retained = {p.name: p.read_bytes() for p in leaf.parent.iterdir()}
                with extra:
                    if case == "foreign-control":
                        changed = copy.deepcopy(bad["new_repository"])
                        changed["assignment_scope"] = AssignmentScope(**changed["assignment_scope"])
                        extra.enter_context(mock.patch.object(reader, "validate_repository", return_value=changed))
                    with self.assertRaises(reader.MaintenanceTransitionStorageError):
                        reader.read_maintenance_transition_locked(env.root, env.state_root)
                self.eq({p.name: p.read_bytes() for p in leaf.parent.iterdir()}, retained)
                if foreign is not None:
                    foreign.unlink()  # This test's own disposable foreign fixture only.
                if case == "foreign-control":
                    self.leaf(env, bad).unlink()
                self.write_leaf(env, record)
                self.assert_read_whole(env, record)

    def test_unsealed_missing_runner_declaration_is_not_control_permission(self):
        record = fresh()
        record["maintenance_commits"][1]["paths"].remove(RUNNER)
        self.eq(codec.maintenance_control_paths_for_record(record), DEFAULT)
        # Only actual custody knows observed control drift.
        self.eq(whole(validate(record)), whole(record))
        with self.env(record=record) as (env, record):
            with self.assertRaises(pub.MaintenanceTransitionPublishError):
                pub.publish_maintenance_transition_locked(env.root, env.state_root, record["maintenance_commits"])
            self.write_leaf(env, record)
            with self.assertRaises(reader.MaintenanceTransitionStorageError):
                reader.read_maintenance_transition_locked(env.root, env.state_root)
