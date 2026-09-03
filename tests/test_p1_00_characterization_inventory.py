"""Refresh-gate characterization for P1-00 inventories, scenarios and checksums."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
import unittest
from collections import Counter
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
INV_DIR = REPO_ROOT / "docs/refactor/two-server/section-1/inventory"
EVIDENCE_INDEX = REPO_ROOT / "docs/refactor/two-server/section-1/evidence-index.md"
EXPECTED_COMMIT = "aa2c06a573dfd60051e142146f55d7965f2242a7"
SIX_BEHAVIOR_IDS = (
    "API-NOTIFY-POST-NOTIFICATIONS-SEND-TEST-PUSH-NOTIFICATION-L201",
    "BOT-ADMIN-CALLBACK-QUERY-ADMIN-BROADCAST-TOGGLE-GROUP-RECIPIENT-L240",
    "BOT-OFFER-MESSAGE-OFFER-OVERTIME-PREFERENCE-START-OFFER-OVERTIME-PREFERENCE-L116",
    "BOT-TG-CALLBACK-QUERY-TELEGRAM-PUBLISHER-CHANNEL-CALLBACKS-PUBLIC-TRADE-CALLBACK-L46",
    "BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-BACK-TO-LOT-TYPE-L2221",
    "BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-BACK-TO-PRICE-L2234",
)
FAMILIES = {
    "AUTH",
    "IDENTITY",
    "OFFER",
    "REQUEST",
    "TRADE",
    "MARKET",
    "MSG",
    "NOTIFY",
    "TG",
    "ADMIN",
    "SYNC",
    "OPS",
}
FAILURE_BOUNDARIES = {
    "failure_before_db_commit",
    "commit_before_event_side_effect",
    "side_effect_before_ack",
    "two_concurrent_commands",
    "process_restart",
    "unknown_authority_schema_hash",
}
ORIGIN_ROWS = {
    ("WEBAPP", "WEBAPP"),
    ("WEBAPP", "TELEGRAM_BOT"),
    ("TELEGRAM_BOT", "WEBAPP"),
    ("TELEGRAM_BOT", "TELEGRAM_BOT"),
    ("INTERNAL", "WEBAPP"),
    ("INTERNAL", "TELEGRAM_BOT"),
}
SECRET_MARKERS = (
    "-----BEGIN RSA PRIVATE KEY-----",
    "-----BEGIN OPENSSH PRIVATE KEY-----",
    "-----BEGIN EC PRIVATE KEY-----",
    "-----BEGIN PRIVATE KEY-----",
    "sk_live_",
    "rk_live_",
    "xoxb-",
    "xoxp-",
)
CHECKSUM_FILES = {
    "inventory/current-finland-runtime.json": "current-finland-runtime.json",
    "inventory/surface-behavior-inventory.json": "surface-behavior-inventory.json",
    "inventory/surface-api.json": "surface-api.json",
    "inventory/surface-bot.json": "surface-bot.json",
    "inventory/surface-web.json": "surface-web.json",
    "inventory/surface-jobs.json": "surface-jobs.json",
    "inventory/runtime-task-ownership.json": "runtime-task-ownership.json",
    "inventory/scenario-records.json": "scenario-records.json",
}


def _load_json(name: str) -> dict:
    return json.loads((INV_DIR / name).read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _fastapi_names() -> list[str]:
    names = []
    for path in sorted((REPO_ROOT / "api/routers").glob("*.py")):
        if path.name == "__init__.py":
            continue
        names.extend(_decorator_names(path, {"get", "post", "put", "patch", "delete", "websocket"}))
    names.extend(_decorator_names(REPO_ROOT / "main.py", {"get", "post", "put", "patch", "delete", "websocket"}))
    return names


def _bot_names() -> list[str]:
    names = []
    kinds = {
        "message",
        "callback_query",
        "chat_join_request",
        "edited_message",
        "my_chat_member",
        "chat_member",
        "inline_query",
        "chosen_inline_result",
        "pre_checkout_query",
        "shipping_query",
        "error",
    }
    for path in sorted((REPO_ROOT / "bot/handlers").glob("*.py")):
        if path.name == "__init__.py":
            continue
        names.extend(_decorator_names(path, kinds))
    return names


def _decorator_names(path: Path, attrs: set[str]) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr in attrs:
                found.append(node.name)
    return found


def _web_names() -> set[str]:
    text = (REPO_ROOT / "frontend/src/router/index.ts").read_text(encoding="utf-8")
    return set(re.findall(r"name:\s*'([^']+)'", text))


class P100InventoryRefreshCharacterizationTests(unittest.TestCase):
    def test_assignment_base_is_an_ancestor_of_this_worktree(self):
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", EXPECTED_COMMIT, "HEAD"],
            cwd=REPO_ROOT,
            check=False,
        )
        self.assertEqual(result.returncode, 0, EXPECTED_COMMIT)

    def test_live_surface_counts_still_match_refreshed_inventory(self):
        api_inv = _load_json("surface-api.json")
        bot_inv = _load_json("surface-bot.json")
        web_inv = _load_json("surface-web.json")
        jobs_inv = _load_json("surface-jobs.json")
        index = _load_json("surface-behavior-inventory.json")

        live_api = _fastapi_names()
        live_bot = _bot_names()
        live_web = _web_names()
        dormant = _decorator_names(
            REPO_ROOT / "src/interfaces/http_api/routers/user_router.py",
            {"get", "post", "put", "patch", "delete", "websocket"},
        )

        self.assertEqual(len(live_api), 217)
        self.assertEqual(len(live_bot), 203)
        self.assertEqual(len(live_web), 30)
        self.assertEqual(len(dormant), 4)
        self.assertEqual(api_inv["coverage"]["total"], 217)
        self.assertEqual(bot_inv["coverage"]["total"], 203)
        self.assertEqual(web_inv["coverage"]["total"], 30)
        self.assertEqual(jobs_inv["coverage"]["total"], 15)
        self.assertEqual(index["counts"]["live_api_decorators_including_main"], 217)
        self.assertEqual(Counter(live_api), Counter(e["handler"].split(".")[-1] for e in api_inv["entries"]))
        self.assertEqual(Counter(live_bot), Counter(e["handler"].split(".")[-1] for e in bot_inv["entries"]))
        self.assertEqual(live_web, {e["name"] for e in web_inv["entries"]})

    def test_inventories_are_stamped_to_assignment_base(self):
        for name in (
            "surface-api.json",
            "surface-bot.json",
            "surface-web.json",
            "surface-jobs.json",
            "surface-behavior-inventory.json",
            "scenario-records.json",
        ):
            payload = _load_json(name)
            self.assertEqual(payload["generated_from_commit"], EXPECTED_COMMIT)
            self.assertNotEqual(payload["generated_from_commit"], "19087ff0686c9260b55f1c6c35782b758be6673e")

    def test_no_unclassified_or_empty_required_test_refs(self):
        empty = []
        unclassified = []
        for name in ("surface-api.json", "surface-bot.json", "surface-web.json", "surface-jobs.json"):
            payload = _load_json(name)
            self.assertEqual(payload["coverage"]["unclassified"], 0)
            self.assertEqual(payload["coverage"]["without_direct_static_test_reference"], 0)
            for entry in payload["entries"]:
                if entry.get("family") not in FAMILIES:
                    unclassified.append(entry["behavior_id"])
                if not entry.get("test_refs"):
                    empty.append(entry["behavior_id"])
        self.assertEqual(unclassified, [])
        self.assertEqual(empty, [])

    def test_six_review_items_now_have_direct_characterization_refs(self):
        by_id = {}
        for name in ("surface-api.json", "surface-bot.json"):
            for entry in _load_json(name)["entries"]:
                by_id[entry["behavior_id"]] = entry
        for behavior_id in SIX_BEHAVIOR_IDS:
            refs = by_id[behavior_id]["test_refs"]
            self.assertTrue(refs, behavior_id)
            self.assertIn("tests/test_p1_00_characterization_six_gaps.py", refs)

    def test_scenario_records_cover_families_matrix_and_failure_timeline(self):
        payload = _load_json("scenario-records.json")
        records = payload["records"]
        families = {item["family"] for item in records}
        self.assertEqual(FAMILIES, families)
        matrix = {
            (item["offer_origin"], item["request_origin"])
            for item in records
            if item.get("record_class") == "web_bot_combination_matrix"
        }
        self.assertEqual(ORIGIN_ROWS, matrix)
        for offer_origin, request_origin in ORIGIN_ROWS:
            row = [
                item
                for item in records
                if item.get("record_class") == "web_bot_combination_matrix"
                and item["offer_origin"] == offer_origin
                and item["request_origin"] == request_origin
            ]
            personas = {item["persona_role"] for item in row}
            tiers = {item.get("customer_tier") for item in row}
            times = {item["time_context"] for item in row}
            outcomes = {item["outcome"] for item in row}
            self.assertTrue({"owner", "customer", "accountant", "admin"} <= personas)
            self.assertTrue({1, 2} <= tiers)
            self.assertEqual({"normal", "overtime", "closed"}, times)
            self.assertEqual({"success", "reject", "retry", "concurrent"}, outcomes)
        failures = {
            (item["family"], item["failure_boundary"])
            for item in records
            if item.get("record_class") == "failure_timeline"
        }
        self.assertEqual({(family, boundary) for family in FAMILIES for boundary in FAILURE_BOUNDARIES}, failures)
        timing = next(item for item in records if item["behavior_id"] == "OPS-DR015-DEPLOY-RESTART-BACKUP-RESTORE-DURATION")
        self.assertEqual(timing["classification"], "CODEX_REVIEW_BLOCKER")
        self.assertEqual(timing["evidence_status"], "NOT_OBSERVED")

    def test_runtime_facts_are_marked_not_reobserved(self):
        runtime = _load_json("current-finland-runtime.json")
        self.assertEqual(runtime["refresh"]["assignment_id"], "WA-P1-00-REFRESH-20260903-001")
        self.assertEqual(runtime["refresh"]["live_host_reobservation"], "NOT_REOBSERVED")
        self.assertEqual(runtime["refresh"]["base_sha"], EXPECTED_COMMIT)
        self.assertEqual(runtime["static_surfaces"]["live_fastapi_decorators"], 217)

    def test_sanitised_checksums_match_evidence_index(self):
        index_text = EVIDENCE_INDEX.read_text(encoding="utf-8")
        for label, filename in CHECKSUM_FILES.items():
            digest = _sha256(INV_DIR / filename)
            self.assertIn(digest, index_text, label)
            self.assertRegex(index_text, rf"`{re.escape(label)}`.*SHA-256 `{digest}`")

    def test_tracked_refresh_artifacts_have_no_secret_material(self):
        paths = list((REPO_ROOT / "docs/refactor/two-server/section-1").rglob("*"))
        leaks = []
        for path in paths:
            if not path.is_file() or path.suffix not in {".md", ".json", ".py"}:
                continue
            text = path.read_text(encoding="utf-8")
            if any(marker in text for marker in SECRET_MARKERS):
                leaks.append(str(path.relative_to(REPO_ROOT)))
        self.assertEqual(leaks, [])


if __name__ == "__main__":
    unittest.main()
