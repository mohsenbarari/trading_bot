from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import uuid


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


RUNNER = load_module(
    "run_repository_unittest_shard_under_test",
    "scripts/run_repository_unittest_shard.py",
)
VERIFIER = load_module(
    "verify_repository_unittest_shards_under_test",
    "scripts/verify_repository_unittest_shards.py",
)


def make_case(module_name: str, case_name: str) -> unittest.TestCase:
    def run_test(self) -> None:
        return None

    case_type = type(case_name, (unittest.TestCase,), {"runTest": run_test})
    case_type.__module__ = module_name
    return case_type("runTest")


def successful_result(cases: list[unittest.TestCase]) -> unittest.TestResult:
    return unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(
        unittest.TestSuite(cases)
    )


def shard_document(
    records: list[dict[str, str]],
    *,
    index: int,
    count: int,
    successful: bool = True,
) -> dict[str, object]:
    selected = [
        record["id"]
        for record in records
        if VERIFIER._shard_index(record["module"], count) == index
    ]
    return {
        "schema": VERIFIER.SHARD_SCHEMA,
        "shard_count": count,
        "shard_index": index,
        "tests": records,
        "tests_sha256": VERIFIER.sha256_hex(records),
        "selected_test_ids": selected,
        "selected_test_ids_sha256": VERIFIER.sha256_hex(selected),
        "result": {
            "errors": 0,
            "failures": 0,
            "skipped": 0,
            "successful": successful,
            "tests_run": len(selected),
            "unexpected_successes": 0,
        },
    }


def write_document(path: Path, document: dict[str, object]) -> None:
    path.write_bytes(VERIFIER.canonical_json_bytes(document))


class RepositoryUnittestShardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_argv = list(sys.argv)
        self.original_path = list(sys.path)
        self.addCleanup(setattr, sys, "argv", self.original_argv)
        self.addCleanup(setattr, sys, "path", self.original_path)

    def write_discovery_module(
        self,
        root: Path,
        *,
        assertion: str = "self.assertEqual(sys.argv[1:], [])",
    ) -> str:
        module_name = f"test_shard_generated_{uuid.uuid4().hex}"
        (root / "__init__.py").touch()
        (root / f"{module_name}.py").write_text(
            "import sys\n"
            "import unittest\n\n"
            "class Generated(unittest.TestCase):\n"
            "    def test_case(self):\n"
            f"        {assertion}\n",
            encoding="utf-8",
        )
        self.addCleanup(sys.modules.pop, module_name, None)
        return module_name

    def run_runner(self, argv: list[str]) -> int:
        with contextlib.redirect_stderr(io.StringIO()):
            return RUNNER.main(argv)

    def run_verifier(self, argv: list[str]) -> int:
        with contextlib.redirect_stderr(io.StringIO()):
            return VERIFIER.main(argv)

    def test_module_assignment_is_stable_and_bounded(self) -> None:
        observed = [RUNNER.shard_index_for_module("tests.test_alpha", 3) for _ in range(3)]

        self.assertEqual(observed, [observed[0]] * 3)
        self.assertGreaterEqual(observed[0], 0)
        self.assertLess(observed[0], 3)

    def test_partition_is_exact_and_keeps_each_module_together(self) -> None:
        tests = [
            make_case("tests.test_alpha", "AlphaOne"),
            make_case("tests.test_alpha", "AlphaTwo"),
            make_case("tests.test_bravo", "BravoOne"),
            make_case("tests.test_charlie", "CharlieOne"),
            make_case("tests.test_delta", "DeltaOne"),
        ]
        selections = [
            RUNNER.select_shard(tests, shard_index=index, shard_count=3)
            for index in range(3)
        ]
        selected_ids = [case.id() for selection in selections for case in selection]
        self.assertEqual(set(selected_ids), {case.id() for case in tests})
        self.assertEqual(len(selected_ids), len(set(selected_ids)))
        alpha_shards = {
            index
            for index, selection in enumerate(selections)
            if any(case.__class__.__module__ == "tests.test_alpha" for case in selection)
        }
        self.assertEqual(len(alpha_shards), 1)

    def test_build_manifest_binds_the_selected_inventory(self) -> None:
        tests = [
            make_case("tests.test_alpha", "Alpha"),
            make_case("tests.test_bravo", "Bravo"),
        ]
        selected = RUNNER.select_shard(tests, shard_index=0, shard_count=1)
        result = successful_result(selected)

        manifest = RUNNER.build_manifest(
            tests,
            selected,
            shard_index=0,
            shard_count=1,
            result=result,
        )

        self.assertEqual(manifest["schema"], RUNNER.SHARD_SCHEMA)
        self.assertTrue(manifest["result"]["successful"])
        self.assertEqual(manifest["selected_test_ids"], [case.id() for case in tests])

    def test_invalid_shard_selection_is_rejected(self) -> None:
        case = make_case("tests.test_alpha", "Alpha")

        with self.assertRaisesRegex(RUNNER.UnittestShardError, "outside"):
            RUNNER.select_shard([case], shard_index=3, shard_count=3)
        with self.assertRaisesRegex(RUNNER.UnittestShardError, "positive"):
            RUNNER.shard_index_for_module("tests.test_alpha", 0)

    def test_runner_rejects_invalid_serialization_and_suite_entries(self) -> None:
        with self.assertRaisesRegex(RUNNER.UnittestShardError, "canonical"):
            RUNNER.canonical_json_bytes({"unsupported": object()})
        with self.assertRaisesRegex(RUNNER.UnittestShardError, "unsupported suite item"):
            list(RUNNER.flatten_suite([object()]))

    def test_runner_rejects_invalid_test_identity(self) -> None:
        invalid_module = make_case("tests.test_alpha", "InvalidModule")
        invalid_module.__class__.__module__ = ""
        with self.assertRaisesRegex(RUNNER.UnittestShardError, "invalid module"):
            RUNNER.module_name(invalid_module)

        invalid_identifier = make_case("tests.test_bravo", "InvalidIdentifier")
        invalid_identifier.id = lambda: ""  # type: ignore[method-assign]
        with self.assertRaisesRegex(RUNNER.UnittestShardError, "invalid identifier"):
            RUNNER.test_record(invalid_identifier)
        with self.assertRaisesRegex(RUNNER.UnittestShardError, "test module"):
            RUNNER.shard_index_for_module("", 1)

    def test_runner_rejects_duplicate_or_mismatched_manifest_selection(self) -> None:
        duplicate_one = make_case("tests.test_alpha", "Duplicate")
        duplicate_two = make_case("tests.test_alpha", "Duplicate")
        duplicate_result = successful_result([duplicate_one, duplicate_two])
        with self.assertRaisesRegex(RUNNER.UnittestShardError, "duplicate"):
            RUNNER.build_manifest(
                [duplicate_one, duplicate_two],
                [duplicate_one, duplicate_two],
                shard_index=0,
                shard_count=1,
                result=duplicate_result,
            )

        case = make_case("tests.test_bravo", "Selected")
        with self.assertRaisesRegex(RUNNER.UnittestShardError, "selected tests"):
            RUNNER.build_manifest(
                [case],
                [],
                shard_index=0,
                shard_count=1,
                result=successful_result([]),
            )

    def test_runner_discovery_and_main_cover_success_failure_and_empty_shards(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module_name = self.write_discovery_module(root)
            manifest = root / "manifest.json"
            self.assertEqual(
                self.run_runner(
                    [
                        "--start-directory", str(root),
                        "--pattern", f"{module_name}.py",
                        "--shard-index", "0",
                        "--shard-count", "1",
                        "--manifest", str(manifest),
                        "--verbosity", "0",
                    ]
                ),
                0,
            )
            self.assertTrue(manifest.is_file())
            self.assertEqual(json.loads(manifest.read_text(encoding="ascii"))["shard_count"], 1)

            module = f"{module_name}.Generated"
            empty_index = 1 - RUNNER.shard_index_for_module(module_name, 2)
            self.assertEqual(
                self.run_runner(
                    [
                        "--start-directory", str(root),
                        "--pattern", f"{module_name}.py",
                        "--shard-index", str(empty_index),
                        "--shard-count", "2",
                        "--manifest", str(root / "empty.json"),
                    ]
                ),
                2,
            )
            self.assertTrue(module.startswith(module_name))

            failing_module = self.write_discovery_module(
                root,
                assertion="self.fail('expected shard failure')",
            )
            failure_manifest = root / "failure.json"
            self.assertEqual(
                self.run_runner(
                    [
                        "--start-directory", str(root),
                        "--pattern", f"{failing_module}.py",
                        "--shard-index", "0",
                        "--shard-count", "1",
                        "--manifest", str(failure_manifest),
                        "--verbosity", "0",
                    ]
                ),
                1,
            )
            self.assertFalse(json.loads(failure_manifest.read_text(encoding="ascii"))["result"]["successful"])

    def test_runner_rejects_missing_or_empty_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "__init__.py").touch()
            with self.assertRaisesRegex(RUNNER.UnittestShardError, "no tests"):
                RUNNER.discover_tests(root, "test_*.py")
            with self.assertRaisesRegex(RUNNER.UnittestShardError, "does not exist"):
                RUNNER.discover_tests(root / "missing", "test_*.py")
            self.assertEqual(
                self.run_runner(
                    [
                        "--start-directory", str(root / "missing"),
                        "--shard-index", "0",
                        "--shard-count", "1",
                        "--manifest", str(root / "missing.json"),
                    ]
                ),
                2,
            )
            with mock.patch.object(
                unittest.TestLoader,
                "discover",
                side_effect=ImportError("synthetic discovery failure"),
            ):
                with self.assertRaisesRegex(RUNNER.UnittestShardError, "discovery failed"):
                    RUNNER.discover_tests(root, "test_*.py")

    def test_runner_discovery_restores_the_project_import_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module_name = self.write_discovery_module(root)
            repository_root = str(ROOT)
            sys.path[:] = [entry for entry in sys.path if entry != repository_root]
            discovered = RUNNER.discover_tests(root, f"{module_name}.py")

        self.assertEqual(len(discovered), 1)
        self.assertIn(repository_root, sys.path)

    def test_verifier_requires_exact_union_of_every_shard(self) -> None:
        records = [
            {"id": f"tests.test_case.Case{index}.runTest", "module": f"tests.test_case_{index}"}
            for index in range(24)
        ]
        count = 3
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(count):
                selected = [
                    record["id"]
                    for record in records
                    if VERIFIER._shard_index(record["module"], count) == index
                ]
                document = {
                    "schema": VERIFIER.SHARD_SCHEMA,
                    "shard_count": count,
                    "shard_index": index,
                    "tests": records,
                    "tests_sha256": VERIFIER.sha256_hex(records),
                    "selected_test_ids": selected,
                    "selected_test_ids_sha256": VERIFIER.sha256_hex(selected),
                    "result": {
                        "errors": 0,
                        "failures": 0,
                        "skipped": 0,
                        "successful": True,
                        "tests_run": len(selected),
                        "unexpected_successes": 0,
                    },
                }
                (root / f"backend-shard-{index}.json").write_bytes(
                    VERIFIER.canonical_json_bytes(document)
                )

            verified = VERIFIER.verify_manifest_directory(root, shard_count=count)

        self.assertTrue(verified["passed"])
        self.assertEqual(verified["test_count"], len(records))

    def test_verifier_rejects_malformed_json_and_inventory_values(self) -> None:
        with self.assertRaisesRegex(VERIFIER.ShardVerificationError, "duplicate"):
            VERIFIER._strict_object([("field", 1), ("field", 2)])
        with self.assertRaisesRegex(VERIFIER.ShardVerificationError, "canonicalized"):
            VERIFIER.canonical_json_bytes({"unsupported": object()})
        with self.assertRaisesRegex(VERIFIER.ShardVerificationError, "strict ASCII"):
            VERIFIER._strict_json(b"{")
        with self.assertRaisesRegex(VERIFIER.ShardVerificationError, "canonical JSON"):
            VERIFIER._strict_json(b'{"z":1,"a":2}')
        with self.assertRaisesRegex(VERIFIER.ShardVerificationError, "invalid"):
            VERIFIER._require_integer(True, label="integer")
        with self.assertRaisesRegex(VERIFIER.ShardVerificationError, "inventory is invalid"):
            VERIFIER._require_records([])
        with self.assertRaisesRegex(VERIFIER.ShardVerificationError, "inventory is invalid"):
            VERIFIER._require_records([{"id": "test", "module": ""}])
        with self.assertRaisesRegex(VERIFIER.ShardVerificationError, "duplicate IDs"):
            VERIFIER._require_records(
                [
                    {"id": "duplicate", "module": "tests.test_one"},
                    {"id": "duplicate", "module": "tests.test_two"},
                ]
            )

    def test_parse_manifest_rejects_each_bound_section(self) -> None:
        records = [{"id": "tests.test_case.Case.runTest", "module": "tests.test_case"}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "backend-shard-0.json"

            def parse_mutation(mutator, message: str) -> None:
                document = shard_document(records, index=0, count=1)
                mutator(document)
                write_document(path, document)
                with self.assertRaisesRegex(VERIFIER.ShardVerificationError, message):
                    VERIFIER.parse_manifest(path, expected_index=0, expected_count=1)

            with self.assertRaisesRegex(VERIFIER.ShardVerificationError, "cannot read"):
                VERIFIER.parse_manifest(path, expected_index=0, expected_count=1)
            parse_mutation(lambda document: document.__setitem__("schema", "wrong"), "schema")
            parse_mutation(lambda document: document.__setitem__("shard_index", 1), "binding")
            parse_mutation(lambda document: document.__setitem__("tests_sha256", "0" * 64), "digest")
            parse_mutation(lambda document: document.__setitem__("selected_test_ids", "wrong"), "selected tests")
            parse_mutation(
                lambda document: document.__setitem__("selected_test_ids_sha256", "0" * 64),
                "selected digest",
            )
            parse_mutation(
                lambda document: document.update(
                    {
                        "selected_test_ids": ["wrong"],
                        "selected_test_ids_sha256": VERIFIER.sha256_hex(["wrong"]),
                    }
                ),
                "membership",
            )
            parse_mutation(lambda document: document.__setitem__("result", {}), "result")
            parse_mutation(
                lambda document: document["result"].__setitem__("errors", True),
                "result errors",
            )
            parse_mutation(
                lambda document: document["result"].__setitem__("tests_run", 99),
                "result differs",
            )
            parse_mutation(
                lambda document: document["result"].__setitem__("successful", False),
                "test execution failed",
            )

    def test_verifier_rejects_mismatched_inventories_and_union(self) -> None:
        records = [
            {"id": f"tests.test_case.Case{index}.runTest", "module": f"tests.test_case_{index}"}
            for index in range(24)
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(2):
                write_document(root / f"backend-shard-{index}.json", shard_document(records, index=index, count=2))
            changed_records = [*records, {"id": "tests.extra.Case.runTest", "module": "tests.extra"}]
            write_document(root / "backend-shard-1.json", shard_document(changed_records, index=1, count=2))
            with self.assertRaisesRegex(VERIFIER.ShardVerificationError, "inventories differ"):
                VERIFIER.verify_manifest_directory(root, shard_count=2)

        with mock.patch.object(
            VERIFIER,
            "parse_manifest",
            side_effect=[
                {"records": records, "selected": [records[0]["id"]]},
                {"records": records, "selected": [records[0]["id"]]},
            ],
        ):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "backend-shard-0.json").touch()
                (root / "backend-shard-1.json").touch()
                with self.assertRaisesRegex(VERIFIER.ShardVerificationError, "union"):
                    VERIFIER.verify_manifest_directory(root, shard_count=2)

    def test_verifier_main_success_and_error_paths(self) -> None:
        records = [
            {"id": f"tests.test_case.Case{index}.runTest", "module": f"tests.test_case_{index}"}
            for index in range(24)
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(3):
                write_document(root / f"backend-shard-{index}.json", shard_document(records, index=index, count=3))
            (root / "ignored.txt").touch()
            output = root / "verification.json"
            self.assertEqual(
                self.run_verifier(
                    [
                        "--manifest-directory", str(root),
                        "--shard-count", "3",
                        "--output", str(output),
                    ]
                ),
                0,
            )
            self.assertTrue(output.is_file())
            self.assertEqual(
                self.run_verifier(
                    [
                        "--manifest-directory", str(root / "missing"),
                        "--shard-count", "3",
                        "--output", str(root / "missing.json"),
                    ]
                ),
                2,
            )
            with self.assertRaisesRegex(VERIFIER.ShardVerificationError, "invalid"):
                VERIFIER.verify_manifest_directory(root, shard_count=0)

    def test_verifier_rejects_an_incomplete_manifest_set(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(VERIFIER.ShardVerificationError, "incomplete"):
                VERIFIER.verify_manifest_directory(Path(directory), shard_count=3)


if __name__ == "__main__":
    unittest.main()
