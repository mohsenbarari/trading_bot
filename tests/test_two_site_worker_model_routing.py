"""Unit tests for pure disabled-by-default worker model routing."""

from __future__ import annotations

import json
import os
from pathlib import Path
import random
import tempfile
import unittest

from scripts.two_site_worker_model_routing import (
    DEFAULT_POLICY_PATH,
    MAX_POLICY_FILE_BYTES,
    OPUS_EFFORT,
    OPUS_MODEL,
    REQUIRED_PERCENT,
    PolicyValidationError,
    WorkerRoute,
    WorkerRoutingPolicy,
    compute_task_bucket,
    load_worker_routing_policy,
    select_worker_model_route,
    validate_worker_routing_policy,
)

RUNTIME_CONTRACT_PATH = Path("docs/refactor/two-server/templates/gemini-runtime-contract.json")
with open(RUNTIME_CONTRACT_PATH, "r", encoding="utf-8") as f:
    _runtime_contract = json.load(f)
TEST_DEFAULT_MODEL = _runtime_contract["worker_model"]
TEST_DEFAULT_EFFORT = _runtime_contract["worker_effort"]


class TestWorkerModelRouting(unittest.TestCase):
    """Test suite verifying strict policy validation, deterministic routing, and inactive defaults."""

    def test_r1_active_template_matches_schema_and_routes_models(self) -> None:
        template_path = Path(DEFAULT_POLICY_PATH)
        self.assertTrue(template_path.is_file(), f"Template missing at {DEFAULT_POLICY_PATH}")

        policy = load_worker_routing_policy(template_path)
        self.assertEqual(policy.schema_version, "1.0")
        self.assertFalse(policy.enabled, "Template must be disabled fail-closed for safety stabilization")
        self.assertEqual(policy.percent, REQUIRED_PERCENT)
        self.assertEqual(policy.percent, 22)
        self.assertEqual(policy.model, OPUS_MODEL)
        self.assertEqual(policy.effort, OPUS_EFFORT)
        self.assertEqual(policy.effort, "model-default")

        opus_task_id = next(
            f"task-opus-{i}" for i in range(100) if compute_task_bucket(f"task-opus-{i}") < 22
        )
        self.assertLess(compute_task_bucket(opus_task_id), 22)
        opus_route = select_worker_model_route(
            opus_task_id,
            policy=policy,
            default_model=TEST_DEFAULT_MODEL,
            default_effort=TEST_DEFAULT_EFFORT,
        )
        self.assertEqual(opus_route.model, TEST_DEFAULT_MODEL)
        self.assertEqual(opus_route.effort, TEST_DEFAULT_EFFORT)
        self.assertEqual(opus_route, (TEST_DEFAULT_MODEL, TEST_DEFAULT_EFFORT))

        auto_opus_route = select_worker_model_route(
            opus_task_id,
            default_model=TEST_DEFAULT_MODEL,
            default_effort=TEST_DEFAULT_EFFORT,
        )
        self.assertEqual(auto_opus_route.model, TEST_DEFAULT_MODEL)
        self.assertEqual(auto_opus_route.effort, TEST_DEFAULT_EFFORT)
        self.assertEqual(auto_opus_route, (TEST_DEFAULT_MODEL, TEST_DEFAULT_EFFORT))

        gemini_task_id = "OPUS-POLICY-ACTIVATE-20260926-380"
        self.assertEqual(compute_task_bucket(gemini_task_id), 85)
        gemini_route = select_worker_model_route(
            gemini_task_id,
            policy=policy,
            default_model=TEST_DEFAULT_MODEL,
            default_effort=TEST_DEFAULT_EFFORT,
        )
        self.assertEqual(gemini_route.model, TEST_DEFAULT_MODEL)
        self.assertEqual(gemini_route.effort, TEST_DEFAULT_EFFORT)
        self.assertEqual(gemini_route, (TEST_DEFAULT_MODEL, TEST_DEFAULT_EFFORT))

        auto_gemini_route = select_worker_model_route(
            gemini_task_id,
            default_model=TEST_DEFAULT_MODEL,
            default_effort=TEST_DEFAULT_EFFORT,
        )
        self.assertEqual(auto_gemini_route.model, TEST_DEFAULT_MODEL)
        self.assertEqual(auto_gemini_route.effort, TEST_DEFAULT_EFFORT)

    def test_r2_deterministic_and_retry_stable(self) -> None:
        sample_task_ids = [f"retry-task-{idx:04d}" for idx in range(25)]
        enabled_policy = WorkerRoutingPolicy(
            schema_version="1.0",
            enabled=True,
            percent=22,
            model=OPUS_MODEL,
            effort=OPUS_EFFORT,
        )

        first_buckets = [compute_task_bucket(t_id) for t_id in sample_task_ids]
        first_routes = [
            select_worker_model_route(
                t_id,
                policy=enabled_policy,
                default_model=TEST_DEFAULT_MODEL,
                default_effort=TEST_DEFAULT_EFFORT,
            )
            for t_id in sample_task_ids
        ]

        for _ in range(5):
            random.seed(random.randint(1, 1000000))
            re_buckets = [compute_task_bucket(t_id) for t_id in sample_task_ids]
            re_routes = [
                select_worker_model_route(
                    t_id,
                    policy=enabled_policy,
                    default_model=TEST_DEFAULT_MODEL,
                    default_effort=TEST_DEFAULT_EFFORT,
                )
                for t_id in sample_task_ids
            ]
            self.assertEqual(first_buckets, re_buckets)
            self.assertEqual(first_routes, re_routes)

    def test_r2_r4_distribution_across_10000_synthetic_ids(self) -> None:
        enabled_policy = WorkerRoutingPolicy(
            schema_version="1.0",
            enabled=True,
            percent=22,
            model=OPUS_MODEL,
            effort=OPUS_EFFORT,
        )
        total_samples = 10000
        opus_count = 0

        for i in range(total_samples):
            t_id = f"synthetic-task-20260926-{i:05d}"
            bucket = compute_task_bucket(t_id)
            self.assertTrue(0 <= bucket <= 99)
            route = select_worker_model_route(
                t_id,
                policy=enabled_policy,
                default_model=TEST_DEFAULT_MODEL,
                default_effort=TEST_DEFAULT_EFFORT,
            )
            if bucket < 22:
                self.assertEqual(route.model, OPUS_MODEL)
                self.assertEqual(route.effort, OPUS_EFFORT)
                opus_count += 1
            else:
                self.assertEqual(route.model, TEST_DEFAULT_MODEL)
                self.assertEqual(route.effort, TEST_DEFAULT_EFFORT)

        percentage = (opus_count / total_samples) * 100.0
        self.assertTrue(
            20.0 <= percentage <= 25.0,
            f"Expected Opus routing between 20% and 25%, got {percentage:.2f}% ({opus_count}/{total_samples})",
        )

    def test_r3_fail_closed_on_invalid_task_id_and_caller_defaults(self) -> None:
        with self.assertRaises(TypeError):
            select_worker_model_route(
                None,  # type: ignore[arg-type]
                default_model=TEST_DEFAULT_MODEL,
                default_effort=TEST_DEFAULT_EFFORT,
            )
        with self.assertRaises(TypeError):
            select_worker_model_route(
                1234,  # type: ignore[arg-type]
                default_model=TEST_DEFAULT_MODEL,
                default_effort=TEST_DEFAULT_EFFORT,
            )
        with self.assertRaises(ValueError):
            select_worker_model_route(
                "",
                default_model=TEST_DEFAULT_MODEL,
                default_effort=TEST_DEFAULT_EFFORT,
            )
        with self.assertRaises(ValueError):
            select_worker_model_route(
                "   ",
                default_model=TEST_DEFAULT_MODEL,
                default_effort=TEST_DEFAULT_EFFORT,
            )

        with self.assertRaises(TypeError):
            select_worker_model_route("task-1")  # type: ignore[call-arg]
        with self.assertRaises(TypeError):
            select_worker_model_route("task-1", default_model=TEST_DEFAULT_MODEL)  # type: ignore[call-arg]
        with self.assertRaises(TypeError):
            select_worker_model_route("task-1", default_effort=TEST_DEFAULT_EFFORT)  # type: ignore[call-arg]

        with self.assertRaises(ValueError):
            select_worker_model_route(
                "task-1",
                default_model="",
                default_effort=TEST_DEFAULT_EFFORT,
            )
        with self.assertRaises(ValueError):
            select_worker_model_route(
                "task-1",
                default_model="   ",
                default_effort=TEST_DEFAULT_EFFORT,
            )
        with self.assertRaises(ValueError):
            select_worker_model_route(
                "task-1",
                default_model=TEST_DEFAULT_MODEL,
                default_effort="",
            )
        with self.assertRaises(ValueError):
            select_worker_model_route(
                "task-1",
                default_model=TEST_DEFAULT_MODEL,
                default_effort="   ",
            )

        with self.assertRaises(TypeError):
            compute_task_bucket(None)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            compute_task_bucket("")
        with self.assertRaises(ValueError):
            compute_task_bucket("   ")

    def test_r3_fail_closed_on_policy_schema_and_percentage_violations(self) -> None:
        valid_dict = {
            "schema_version": "1.0",
            "enabled": False,
            "percent": 22,
            "model": OPUS_MODEL,
            "effort": OPUS_EFFORT,
        }
        self.assertIsInstance(validate_worker_routing_policy(valid_dict), WorkerRoutingPolicy)

        with self.assertRaises(PolicyValidationError):
            validate_worker_routing_policy({**valid_dict, "extra": "surprise"})

        with self.assertRaises(PolicyValidationError):
            incomplete = dict(valid_dict)
            incomplete.pop("model")
            validate_worker_routing_policy(incomplete)

        with self.assertRaises(PolicyValidationError):
            validate_worker_routing_policy({**valid_dict, "schema_version": "2.0"})

        with self.assertRaises(PolicyValidationError):
            validate_worker_routing_policy({**valid_dict, "enabled": "false"})

        for invalid_percent in [0, 21, 23, 100, -1, 101]:
            with self.assertRaises(PolicyValidationError):
                validate_worker_routing_policy({**valid_dict, "percent": invalid_percent})
            with self.assertRaises(PolicyValidationError):
                validate_worker_routing_policy({**valid_dict, "enabled": True, "percent": invalid_percent})

        with self.assertRaises(PolicyValidationError):
            validate_worker_routing_policy({**valid_dict, "percent": True})
        with self.assertRaises(PolicyValidationError):
            validate_worker_routing_policy({**valid_dict, "percent": "22"})

        with self.assertRaises(PolicyValidationError):
            validate_worker_routing_policy({**valid_dict, "model": "claude-sonnet-4-5"})
        with self.assertRaises(PolicyValidationError):
            validate_worker_routing_policy({**valid_dict, "effort": "high"})
        with self.assertRaises(PolicyValidationError):
            validate_worker_routing_policy({**valid_dict, "effort": "medium"})

    def test_r3_reject_unchecked_worker_routing_policy_instances(self) -> None:
        invalid_policies = [
            WorkerRoutingPolicy("1.0", True, 99, OPUS_MODEL, OPUS_EFFORT),
            WorkerRoutingPolicy("1.0", True, 0, OPUS_MODEL, OPUS_EFFORT),
            WorkerRoutingPolicy("1.0", True, 21, OPUS_MODEL, OPUS_EFFORT),
            WorkerRoutingPolicy("1.0", True, 23, OPUS_MODEL, OPUS_EFFORT),
            WorkerRoutingPolicy("1.0", True, 100, OPUS_MODEL, OPUS_EFFORT),
            WorkerRoutingPolicy("2.0", True, 22, OPUS_MODEL, OPUS_EFFORT),
            WorkerRoutingPolicy("1.0", True, 22, "claude-sonnet-4-5", OPUS_EFFORT),
            WorkerRoutingPolicy("1.0", True, 22, OPUS_MODEL, "high"),
            WorkerRoutingPolicy("1.0", True, 22, OPUS_MODEL, "medium"),
            WorkerRoutingPolicy("1.0", "not-bool", 22, OPUS_MODEL, OPUS_EFFORT),  # type: ignore[arg-type]
        ]
        for bad_policy in invalid_policies:
            with self.assertRaises(PolicyValidationError):
                validate_worker_routing_policy(bad_policy)
            with self.assertRaises(PolicyValidationError):
                select_worker_model_route(
                    "task-123",
                    policy=bad_policy,
                    default_model=TEST_DEFAULT_MODEL,
                    default_effort=TEST_DEFAULT_EFFORT,
                )

    def test_r3_no_pinned_gemini_constants_and_no_unwanted_exports(self) -> None:
        import scripts.two_site_worker_model_routing as routing_module

        self.assertFalse(hasattr(routing_module, "DEFAULT_GEMINI_MODEL"))
        self.assertFalse(hasattr(routing_module, "DEFAULT_GEMINI_EFFORT"))

        for alias in [
            "validate_policy",
            "load_policy",
            "get_task_bucket",
            "select_worker_route",
            "select_worker_model",
        ]:
            self.assertFalse(hasattr(routing_module, alias), f"Alias {alias} should be removed")

        with self.assertRaises(TypeError):
            compute_task_bucket("task-123", domain_separator="other:")  # type: ignore[call-arg]

        custom_route = select_worker_model_route(
            "task-custom",
            policy={"schema_version": "1.0", "enabled": False, "percent": 22, "model": OPUS_MODEL, "effort": OPUS_EFFORT},
            default_model="custom-worker-model",
            default_effort="custom-effort",
        )
        self.assertEqual(custom_route.model, "custom-worker-model")
        self.assertEqual(custom_route.effort, "custom-effort")

    def test_r3_fail_closed_on_duplicate_keys_and_malformed_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            dup_file = Path(tmp_dir) / "duplicate.json"
            dup_content = (
                '{"schema_version": "1.0", "enabled": false, "enabled": true, '
                '"percent": 22, "model": "claude-opus-4-6-thinking", "effort": "model-default"}'
            )
            dup_file.write_text(dup_content, encoding="utf-8")
            with self.assertRaises(PolicyValidationError):
                load_worker_routing_policy(dup_file)

            malformed_file = Path(tmp_dir) / "malformed.json"
            malformed_file.write_text('{"schema_version": "1.0", broken', encoding="utf-8")
            with self.assertRaises(PolicyValidationError):
                load_worker_routing_policy(malformed_file)

            empty_file = Path(tmp_dir) / "empty.json"
            empty_file.write_text("", encoding="utf-8")
            with self.assertRaises(PolicyValidationError):
                load_worker_routing_policy(empty_file)

            bad_utf8 = Path(tmp_dir) / "bad_utf8.json"
            bad_utf8.write_bytes(b"\x80\x81\x82")
            with self.assertRaises(PolicyValidationError):
                load_worker_routing_policy(bad_utf8)

    def test_r3_fail_closed_on_file_security_hardlinks_symlinks_oversize_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base_path = Path(tmp_dir)
            valid_content = (
                '{"schema_version": "1.0", "enabled": false, "percent": 22, '
                '"model": "claude-opus-4-6-thinking", "effort": "model-default"}'
            )
            valid_file = base_path / "valid.json"
            valid_file.write_text(valid_content, encoding="utf-8")

            oversize_file = base_path / "oversize.json"
            oversize_bytes = (
                b'{"schema_version": "1.0", "enabled": false, "percent": 22, '
                b'"model": "claude-opus-4-6-thinking", "effort": "model-default", "padding": "'
                + b"a" * 4100
                + b'"}'
            )
            oversize_file.write_bytes(oversize_bytes)
            self.assertGreater(len(oversize_bytes), MAX_POLICY_FILE_BYTES)
            with self.assertRaises(PolicyValidationError):
                load_worker_routing_policy(oversize_file)

            symlink_file = base_path / "symlink.json"
            os.symlink(valid_file, symlink_file)
            with self.assertRaises(PolicyValidationError):
                load_worker_routing_policy(symlink_file)

            broken_symlink = base_path / "broken_symlink.json"
            os.symlink(base_path / "nonexistent.json", broken_symlink)
            with self.assertRaises(PolicyValidationError):
                load_worker_routing_policy(broken_symlink)

            hardlink_file = base_path / "hardlink.json"
            os.link(valid_file, hardlink_file)
            with self.assertRaises(PolicyValidationError):
                load_worker_routing_policy(hardlink_file)
            with self.assertRaises(PolicyValidationError):
                load_worker_routing_policy(valid_file)

            clean_file = base_path / "clean.json"
            clean_file.write_text(valid_content, encoding="utf-8")
            loaded = load_worker_routing_policy(clean_file)
            self.assertEqual(loaded.percent, 22)
            self.assertEqual(loaded.effort, "model-default")

            dir_target = base_path / "policy_dir"
            dir_target.mkdir()
            with self.assertRaises(PolicyValidationError):
                load_worker_routing_policy(dir_target)

            sandbox_dir = base_path / "sandbox"
            sandbox_dir.mkdir()
            outside_file = base_path / "outside.json"
            outside_file.write_text(valid_content, encoding="utf-8")
            with self.assertRaises(PolicyValidationError):
                load_worker_routing_policy(outside_file, base_dir=sandbox_dir)

    def test_r4_opus_remains_strictly_inactive_when_disabled(self) -> None:
        disabled_policy = WorkerRoutingPolicy(
            schema_version="1.0",
            enabled=False,
            percent=22,
            model=OPUS_MODEL,
            effort=OPUS_EFFORT,
        )
        for i in range(100):
            task_id = f"production-worker-task-{i}"
            route = select_worker_model_route(
                task_id,
                policy=disabled_policy,
                default_model=TEST_DEFAULT_MODEL,
                default_effort=TEST_DEFAULT_EFFORT,
            )
            self.assertEqual(route.model, TEST_DEFAULT_MODEL)
            self.assertEqual(route.effort, TEST_DEFAULT_EFFORT)
            self.assertNotEqual(route.model, OPUS_MODEL)


if __name__ == "__main__":
    unittest.main()
