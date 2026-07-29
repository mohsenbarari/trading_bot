from __future__ import annotations

from datetime import datetime, timezone
import copy
import ast
import json
from pathlib import Path
import unittest

from scripts import production_shadow_queue_state_collector_contract as MODULE
from scripts import production_shadow_queue_state_observation as QUEUE


CAMPAIGN_ID = "11111111-1111-4111-8111-111111111111"
OPERATION_ID = "22222222-2222-4222-8222-222222222222"
RELEASE_SHA = "a" * 40
TREE_SHA = "b" * 40
HASH = "c" * 64
NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)


def identity() -> dict[str, str]:
    return {
        "campaign_id": CAMPAIGN_ID,
        "operation_id": OPERATION_ID,
        "release_sha": RELEASE_SHA,
        "release_tree_sha": TREE_SHA,
        "manifest_sha256": HASH,
        "plan_sha256": "d" * 64,
        "approval_sha256": "e" * 64,
        "phase_started_at": "2026-07-29T11:00:00Z",
    }


def plan() -> dict[str, object]:
    return MODULE.build_plan(
        identity=identity(),
        role="webapp_fi",
        runtime_target_binding_sha256="f" * 64,
        app_image_id="sha256:" + "1" * 64,
        collector_source_manifest_sha256="2" * 64,
    )


def output(value: dict[str, object] | None = None) -> dict[str, object]:
    built = {
        "schema": MODULE.OUTPUT_SCHEMA,
        "status": "observed-redacted",
        **{key: identity()[key] for key in QUEUE.IDENTITY_FIELDS},
        "role": "webapp_fi",
        "queue_collector_plan_sha256": plan()["queue_collector_plan_sha256"],
        "captured_at": "2026-07-29T11:59:00Z",
        "observed_at": "2026-07-29T11:59:30Z",
        "queue_counters": {counter: 0 for counter in QUEUE.QUEUE_COUNTERS},
        "source_proofs": {
            source: {
                "source": source,
                "read_only": True,
                "snapshot_sha256": ("3" if source == "application_database" else "4") * 64,
            }
            for source in MODULE.RUNTIME_INPUTS
        },
        "collector_output_sha256": "0" * 64,
    }
    if value:
        built.update(value)
    built["collector_output_sha256"] = MODULE._output_digest(built)
    return built


class QueueStateCollectorContractTests(unittest.TestCase):
    def test_plan_is_static_and_fully_bound(self) -> None:
        document = plan()
        self.assertEqual(document["status"], "planned-only")
        self.assertEqual(document["runtime_inputs"], list(MODULE.RUNTIME_INPUTS))
        self.assertTrue(document["mutation_forbidden"])
        self.assertEqual(MODULE.validate_plan(document), document)

    def test_collector_output_is_reduced_only_after_exact_binding(self) -> None:
        document = output()
        validated = MODULE.validate_collector_output(document, plan=plan(), now=NOW)
        reduced = MODULE.reduce_to_role_snapshot(document, plan=plan(), now=NOW)
        self.assertEqual(validated["role"], "webapp_fi")
        self.assertEqual(reduced["collector_output_sha256"], document["collector_output_sha256"])
        self.assertEqual(
            QUEUE._role_snapshot_digest(reduced["role_snapshot"]),  # noqa: SLF001
            reduced["role_snapshot"]["queue_state_sha256"],
        )

    def test_rejects_unbound_image_plan_or_non_read_only_sources(self) -> None:
        altered_plan = plan()
        altered_plan["app_image_id"] = "latest"
        with self.assertRaisesRegex(MODULE.QueueStateCollectorContractError, "plan differs"):
            MODULE.validate_plan(altered_plan)
        altered_output = output()
        altered_output["source_proofs"] = copy.deepcopy(altered_output["source_proofs"])
        altered_output["source_proofs"]["application_redis"]["read_only"] = False
        altered_output["collector_output_sha256"] = MODULE._output_digest(altered_output)
        with self.assertRaisesRegex(MODULE.QueueStateCollectorContractError, "source proof"):
            MODULE.validate_collector_output(altered_output, plan=plan(), now=NOW)

    def test_rejects_foreign_plan_digest_and_stale_or_duplicate_json(self) -> None:
        altered = output({"queue_collector_plan_sha256": "9" * 64})
        with self.assertRaisesRegex(MODULE.QueueStateCollectorContractError, "identity differs"):
            MODULE.validate_collector_output(altered, plan=plan(), now=NOW)
        stale = output({"captured_at": "2026-07-29T10:00:00Z"})
        with self.assertRaisesRegex(MODULE.QueueStateCollectorContractError, "freshness differs"):
            MODULE.validate_collector_output(stale, plan=plan(), now=NOW)
        raw = json.dumps(output(), sort_keys=True, separators=(",", ":")).encode("ascii")
        duplicate = raw.replace(b'"schema":', b'"schema":"x","schema":', 1)
        with self.assertRaisesRegex(MODULE.QueueStateCollectorContractError, "duplicate"):
            MODULE.parse_collector_output(duplicate)

    def test_parser_requires_canonical_ascii_payload(self) -> None:
        document = output()
        payload = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("ascii")
        self.assertEqual(MODULE.parse_collector_output(payload), document)
        with self.assertRaisesRegex(MODULE.QueueStateCollectorContractError, "canonical"):
            MODULE.parse_collector_output(payload + b"\n")

    def test_module_does_not_add_live_execution_capabilities(self) -> None:
        source = Path(MODULE.__file__).read_text(encoding="ascii")
        imports = {
            alias.name.split(".", maxsplit=1)[0]
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports.update(
            node.module.split(".", maxsplit=1)[0]
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ImportFrom) and node.module
        )
        self.assertFalse(
            imports & {"subprocess", "socket", "requests", "docker", "redis", "sqlalchemy", "os"}
        )


if __name__ == "__main__":
    unittest.main()
