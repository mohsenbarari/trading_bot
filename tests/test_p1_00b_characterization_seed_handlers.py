"""P1-00b: one-by-one handler assertions for the 465-seed inventory."""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import ast
import json
import unittest
from functools import lru_cache
from pathlib import Path

from core.background_job_authority import BACKGROUND_JOB_AUTHORITY, REQUIRED_BACKGROUND_JOBS

REPO_ROOT = Path(__file__).resolve().parents[1]
INV_DIR = REPO_ROOT / "docs/refactor/two-server/section-1/inventory"
PRODUCT_KIND = {
    "CRUD_MUTATION",
    "BROADCAST",
    "TOGGLE_MUTATION",
    "ACCOUNT_STATUS_TRANSITION",
    "SESSION_MUTATION",
    "DELIVERY_COMMAND",
    "SYNC_APPLY",
    "SCHEDULED_JOB",
}
NON_PRODUCT_KIND = {
    "FSM_PROMPT",
    "MENU_NAV",
    "ACK_ONLY",
    "READ_QUERY",
    "PROVIDER_REPLY",
    "EPHEMERAL_ACTIVITY",
    "PARSE_NO_COMMIT",
    "SHADOW_OBSERVATION",
    "STREAM_SUBSCRIBE",
}
HTTP_MUTATE = {"post", "put", "patch", "delete"}
RPC_MUTATE = {
    "forward_standard_invitation_to_iran",
    "create_telegram_admin_broadcast",
}
RETRY_TOKENS = {"retry", "retries", "idempotency_key", "duplicate_replay"}
OUTBOX_TOKENS = {"outbox", "publish", "enqueue", "delivery"}
FAILURE_TOKENS = {"rollback", "HTTPException", "IntegrityError", "fail_closed", "raise"}


def _load_json(name: str) -> dict:
    return json.loads((INV_DIR / name).read_text(encoding="utf-8"))


def _all_seeds() -> list[dict]:
    seeds = []
    for name, section in (
        ("surface-api.json", "api"),
        ("surface-bot.json", "bot"),
        ("surface-web.json", "web"),
        ("surface-jobs.json", "jobs"),
    ):
        for entry in _load_json(name)["entries"]:
            item = dict(entry)
            item["_section"] = section
            seeds.append(item)
    return seeds


@lru_cache(maxsize=None)
def _parse_python(path: str) -> ast.AST:
    return ast.parse((REPO_ROOT / path).read_text(encoding="utf-8"), filename=path)


def _node_span(node: ast.AST) -> tuple[int, int]:
    starts = [node.lineno]
    for deco in getattr(node, "decorator_list", []) or []:
        starts.append(getattr(deco, "lineno", node.lineno))
    start = min(starts)
    end = getattr(node, "end_lineno", node.lineno)
    return start, end


def _function_covering(path: str, func_name: str, source_line: int):
    tree = _parse_python(path)
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name
    ]
    if not matches:
        raise AssertionError(f"missing function {func_name} in {path}")
    covering = [
        node
        for node in matches
        if _node_span(node)[0] - 1 <= source_line <= _node_span(node)[1]
    ]
    if len(covering) == 1:
        return covering[0]
    if not covering and len(matches) == 1:
        return matches[0]
    if covering:
        return covering[0]
    raise AssertionError(f"{path}:{source_line} does not cover {func_name}")


def _iter_body(node: ast.AST):
    for child in getattr(node, "body", []) or []:
        yield from ast.walk(child)


def _walk_tokens(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in _iter_body(node):
        if isinstance(child, ast.Name):
            names.add(child.id)
        elif isinstance(child, ast.Attribute):
            names.add(child.attr)
        elif isinstance(child, ast.Constant) and isinstance(child.value, str):
            names.add(child.value)
    return names


def _classify_bot_handler(node: ast.AST) -> str:
    commits = False
    http_mutate = False
    rpc = False
    for child in _iter_body(node):
        if isinstance(child, ast.Attribute) and child.attr == "commit":
            commits = True
        if isinstance(child, ast.Attribute) and child.attr in {"post", "put", "patch"}:
            owner = child.value
            owner_name = owner.id if isinstance(owner, ast.Name) else ""
            if owner_name in {"client", "http"}:
                http_mutate = True
        if isinstance(child, ast.Attribute) and child.attr == "delete":
            owner = child.value
            owner_name = owner.id if isinstance(owner, ast.Name) else ""
            if owner_name == "client":
                http_mutate = True
        if isinstance(child, ast.Name) and child.id in RPC_MUTATE:
            rpc = True
        if isinstance(child, ast.Attribute) and child.attr in RPC_MUTATE:
            rpc = True
    return "PRODUCT_COMMIT" if commits or http_mutate or rpc else "FSM_OR_UI_ONLY"


def _split_source(source: str) -> tuple[str, int]:
    path, _, line = source.partition(":")
    return path, int(line)


def _web_component_exists(component: str) -> bool:
    if component.startswith("REDIRECT:"):
        return True
    names = [component]
    if not component.endswith(".vue"):
        names.append(f"{component}.vue")
    for name in names:
        for folder in ("frontend/src/views", "frontend/src/components"):
            if (REPO_ROOT / folder / name).exists():
                return True
            if (REPO_ROOT / folder / Path(name).name).exists():
                return True
    return (REPO_ROOT / "frontend/src/views" / component).exists()


class P100bSeedHandlerCharacterizationTests(unittest.TestCase):
    def test_every_seed_handler_matches_source_and_mapped_contract(self):
        payload = _load_json("scenario-records.json")
        rules = {
            (rule["family"], rule["operation_kind"]): rule["contract_id"]
            for rule in payload["seed_mapping"]["rules"]
        }
        contracts = {item["contract_id"]: item for item in payload["behavior_contracts"]}
        seeds = _all_seeds()
        self.assertEqual(len(seeds), 465)
        mismatches: list[str] = []
        job_names = []
        for seed in seeds:
            behavior_id = seed["behavior_id"]
            family = seed["family"]
            kind = seed["operation_kind"]
            contract_id = rules[(family, kind)]
            contract = contracts[contract_id]
            self.assertEqual(contract["family"], family, behavior_id)
            self.assertEqual(contract["operation_kind"], kind, behavior_id)
            self.assertTrue(contract["failure_cases"], behavior_id)
            self.assertTrue(contract["timeouts_retries"], behavior_id)
            self.assertTrue(contract["events_outbox"], behavior_id)

            section = seed["_section"]
            if section in {"api", "bot"}:
                path, line = _split_source(seed["source"])
                func_name = seed["handler"].split(".")[-1]
                node = _function_covering(path, func_name, line)
                tokens = _walk_tokens(node)
                if kind in {"ACK_ONLY", "MENU_NAV", "READ_QUERY", "PARSE_NO_COMMIT", "EPHEMERAL_ACTIVITY"}:
                    if "commit" in tokens:
                        mismatches.append(f"{behavior_id} {kind} handler still names commit")
                if section == "bot" and family == "ADMIN":
                    classified = _classify_bot_handler(node)
                    if classified == "PRODUCT_COMMIT" and kind in {
                        "ACK_ONLY",
                        "MENU_NAV",
                        "FSM_PROMPT",
                        "READ_QUERY",
                    }:
                        mismatches.append(f"{behavior_id} commits but is {kind}")
                    if classified == "FSM_OR_UI_ONLY" and kind in {
                        "CRUD_MUTATION",
                        "BROADCAST",
                        "TOGGLE_MUTATION",
                        "ACCOUNT_STATUS_TRANSITION",
                    }:
                        mismatches.append(f"{behavior_id} is UI-only but is {kind}")
                if section == "api":
                    method = seed["method"].upper()
                    if method in {"GET", "HEAD"}:
                        self.assertIn(kind, {"READ_QUERY", "STREAM_SUBSCRIBE"}, behavior_id)
                    elif method == "WEBSOCKET":
                        self.assertEqual(kind, "STREAM_SUBSCRIBE", behavior_id)
                    else:
                        self.assertNotIn(kind, {"READ_QUERY", "STREAM_SUBSCRIBE"}, behavior_id)
                retry_status = contract["timeouts_retries"]["status"]
                outbox_status = contract["events_outbox"]["status"]
                failure_status = contract["failure_cases"]["status"]
                self.assertIn(retry_status, {"APPLICABLE", "NOT_APPLICABLE"}, behavior_id)
                self.assertIn(outbox_status, {"APPLICABLE", "NOT_APPLICABLE"}, behavior_id)
                self.assertIn(failure_status, {"APPLICABLE", "NOT_APPLICABLE"}, behavior_id)
                if retry_status == "NOT_APPLICABLE":
                    self.assertTrue(contract["timeouts_retries"].get("reason"), behavior_id)
                if outbox_status == "NOT_APPLICABLE":
                    self.assertTrue(contract["events_outbox"].get("reason"), behavior_id)
                if failure_status == "NOT_APPLICABLE":
                    self.assertTrue(contract["failure_cases"].get("reason"), behavior_id)
                if failure_status == "APPLICABLE":
                    self.assertTrue(
                        contract["failure_cases"].get("rules") or tokens & FAILURE_TOKENS,
                        behavior_id,
                    )
            elif section == "web":
                path, line = _split_source(seed["source"])
                text = (REPO_ROOT / path).read_text(encoding="utf-8").splitlines()
                window = "\n".join(text[max(0, line - 8) : line + 12])
                self.assertIn(seed["path"], window, behavior_id)
                self.assertIn(seed["name"], window, behavior_id)
                self.assertTrue(_web_component_exists(seed["component"]), seed["component"])
                self.assertEqual(kind, seed["operation_kind"])
                self.assertEqual(contract["db_mutations"]["status"], "NOT_APPLICABLE", behavior_id)
            else:
                job_name = seed["job_name"]
                job_names.append(job_name)
                self.assertIn(job_name, BACKGROUND_JOB_AUTHORITY, behavior_id)
                entry = BACKGROUND_JOB_AUTHORITY[job_name]
                self.assertEqual(entry.job_name, job_name, behavior_id)
                self.assertEqual(kind, "SCHEDULED_JOB", behavior_id)
                self.assertEqual(contract["operation_kind"], "SCHEDULED_JOB", behavior_id)
                if seed["required_registry_member"]:
                    self.assertIn(job_name, REQUIRED_BACKGROUND_JOBS, behavior_id)

        self.assertEqual(mismatches, [])
        self.assertEqual(len(job_names), 15)
        self.assertEqual(len(set(job_names)), 15)

    def test_seed_handler_blocker_stays_open_without_executed_partitions(self):
        payload = _load_json("scenario-records.json")
        registry = {item["blocker_id"]: item for item in payload["blocker_registry"]["items"]}
        self.assertIn("P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE", registry)
        item = registry["P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE"]
        self.assertEqual(item["severity"], "HIGH")
        self.assertEqual(item["disposition"], "BLOCKED_EXPLICIT")
        self.assertEqual(item["resolution_stage"], "P1-00b")
        self.assertEqual(item.get("evidence_cases") or [], [])


if __name__ == "__main__":
    unittest.main()
