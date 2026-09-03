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

from core.background_job_authority import (
    REQUIRED_BACKGROUND_JOBS,
    background_job_authority_entries,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
INV_DIR = REPO_ROOT / "docs/refactor/two-server/section-1/inventory"
EVIDENCE_INDEX = REPO_ROOT / "docs/refactor/two-server/section-1/evidence-index.md"
EXPECTED_COMMIT = "aa2c06a573dfd60051e142146f55d7965f2242a7"
ORIGINAL_INVENTORY_COMMIT = "19087ff0686c9260b55f1c6c35782b758be6673e"
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


def classify_failure_test_family(path: str) -> str | None:
    name = Path(path).name
    hits: list[tuple[int, str, str]] = []
    for family, tokens in FAMILY_TEST_TOKENS.items():
        for token in tokens:
            if token in name:
                hits.append((len(token), family, token))
    if not hits:
        return None
    hits.sort(reverse=True)
    best_len, best_family, _ = hits[0]
    if any(length == best_len and family != best_family for length, family, _ in hits[1:]):
        return None
    return best_family


def failure_test_covers_boundary(path: str, boundary: str) -> bool:
    name = Path(path).name
    return any(token in name for token in BOUNDARY_NAME_TOKENS[boundary])


def is_family_boundary_evidence(path: str, family: str, boundary: str) -> bool:
    return classify_failure_test_family(path) == family and failure_test_covers_boundary(path, boundary)


def parse_pytest_node_id(node_id: str) -> tuple[str, str | None, str]:
    path, separator, rest = node_id.partition("::")
    if not separator or not rest:
        raise AssertionError(f"evidence must be a pytest node id, not a filename: {node_id}")
    if "." in rest:
        class_name, _, func_name = rest.partition(".")
        return path, class_name, func_name
    return path, None, rest


def find_test_function(path: Path, class_name: str | None, func_name: str):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if class_name and isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == func_name:
                    return item
        if class_name is None and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return node
    return None


def function_source_talk(fn) -> str:
    parts = [fn.name, ast.get_docstring(fn) or ""]
    for node in ast.walk(fn):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            parts.append(node.value)
    return " ".join(parts)


CORE_CONTRACT_FIELDS = {
    "preconditions",
    "input",
    "response",
    "db_mutations",
    "events_outbox",
    "side_effects",
    "ordering",
    "timeouts_retries",
    "idempotency_key",
    "failure_cases",
    "reference_evidence",
    "regression_tests",
}
GENERIC_META_TESTS = {"tests/test_p1_00_characterization_inventory.py"}
AMBIGUOUS_POLICY_PHRASES = {
    "where current policy allows",
    "where allowed",
    "if allowed",
    "probably",
}
DEFERRAL_TOKENS = {
    "READ_ONLY_VARIANTS_RESOLVE",
    "OTHERWISE_NOT_APPLICABLE",
}
INHERIT_PREDICATE_PHRASE = "persona×surface is not ineligible"
INHERIT_SEMANTIC_PATTERNS = (
    r"not\s+ineligible",
    r"is not ineligible",
    r"\binherit\b",
    r"where current policy allows",
    r"where allowed",
    r"read_only_variants_resolve",
    r"otherwise_not_applicable",
)
MUTATION_CLASSES = {"READ_OR_STREAM", "MUTATION_OR_COMMAND", "SCHEDULED_JOB"}
OPERATION_KINDS = {
    "READ_QUERY",
    "STREAM_SUBSCRIBE",
    "MENU_NAV",
    "FSM_PROMPT",
    "ACK_ONLY",
    "EPHEMERAL_ACTIVITY",
    "PARSE_NO_COMMIT",
    "SHADOW_OBSERVATION",
    "CRUD_MUTATION",
    "BROADCAST",
    "SESSION_MUTATION",
    "DELIVERY_COMMAND",
    "DOCUMENT_SEND",
    "PROVIDER_REPLY",
    "SYNC_APPLY",
    "SCHEDULED_JOB",
    "TOGGLE_MUTATION",
}
NON_PRODUCT_MUTATION_KINDS = {
    "READ_QUERY",
    "STREAM_SUBSCRIBE",
    "MENU_NAV",
    "FSM_PROMPT",
    "ACK_ONLY",
    "EPHEMERAL_ACTIVITY",
    "PARSE_NO_COMMIT",
}
FALSE_CRUD_EXAMPLES = (
    "BOT-TG-CALLBACK-QUERY-DEFAULT-ACKNOWLEDGE-NOOP-CALLBACK-L17",
    "BOT-TG-CALLBACK-QUERY-DEFAULT-HANDLE-UNMATCHED-CALLBACK-L24",
    "BOT-TG-MESSAGE-DEFAULT-HANDLE-UNAUTHORIZED-MESSAGES-L43",
    "API-MSG-POST-CHAT-SEND-TYPING-SIGNAL-L918",
    "API-MSG-POST-CHAT-SEND-DIRECT-ACTIVITY-SIGNAL-L939",
    "API-MSG-POST-CHAT-SEND-ROOM-ACTIVITY-SIGNAL-L962",
    "API-OFFER-POST-OFFERS-PARSE-OFFER-TEXT-L3184",
    "API-OFFER-POST-OFFERS-PREVIEW-COIN-COMMODITY-INFERENCE-L1592",
)
BLOCKER_REGISTRY_FIELDS = {
    "blocker_id",
    "severity",
    "owner",
    "resolution_stage",
    "blocked_dependents",
    "required_evidence",
    "forbidden_shortcuts",
    "disposition",
    "status",
    "reason",
}
MENU_NAV_EXAMPLE = "BOT-ADMIN-MESSAGE-ADMIN-COMMODITIES-HANDLE-MANAGE-COMMODITIES-L305"
BROADCAST_EXAMPLE = "API-ADMIN-POST-ADMIN-MESSAGES-CREATE-BROADCAST-L261"
DOCUMENT_SEND_EXAMPLE = "BOT-TG-MESSAGE-PANEL-SHOW-RECENT-TRADES-PDF-L534"
CRUD_BROADCAST_PHRASES = (
    "broadcast payload",
    "committed broadcast identity",
    "broadcast fanout",
    "broadcast receipt",
    "broadcast audience",
)
FAMILY_TEST_TOKENS = {
    "AUTH": ("test_auth_", "test_session_", "test_stage6_otp"),
    "IDENTITY": (
        "test_bot_link",
        "test_users_router",
        "test_users_public",
        "test_invitation",
        "test_user_account",
    ),
    "OFFER": ("test_offers_", "test_offer_publication", "test_offer_source", "test_offer_price"),
    "REQUEST": ("test_offer_request", "test_offer_overtime", "test_overtime_request"),
    "TRADE": ("test_trade_", "test_trades_", "test_bot_trade_"),
    "MARKET": ("test_market_", "test_estimator", "test_coin_market"),
    "MSG": ("test_chat_", "test_messages_", "test_realtime_", "test_messenger_", "test_album_"),
    "NOTIFY": ("test_notification", "test_web_push"),
    "TG": (
        "test_telegram_delivery",
        "test_telegram_publisher",
        "test_telegram_notification",
        "test_telegram_queue",
        "test_telegram_otp",
        "test_telegram_dispatch",
        "test_telegram_gateway",
        "test_telegram_interaction",
        "test_telegram_multi_publisher",
        "test_telegram_split_runtime",
    ),
    "ADMIN": ("test_admin_", "test_bot_admin_", "test_commodities_", "test_telegram_admin_"),
    "SYNC": ("test_sync_",),
    "OPS": (
        "test_deployment",
        "test_main_lifespan",
        "test_main_metrics",
        "test_main_foreign",
        "test_background_job_authority",
        "test_telegram_bot_runtime_role",
        "test_server_routing",
    ),
}
BOUNDARY_NAME_TOKENS = {
    "failure_before_db_commit": (
        "_guards",
        "_guard",
        "_mutations",
        "_mutation",
        "_login",
        "_session_flows",
        "_create",
        "_delete",
        "_atomicity",
        "direct_mutations",
    ),
    "commit_before_event_side_effect": (
        "outbox",
        "publication_worker",
        "delivery_worker",
        "delivery_service",
        "delivery_receipt",
        "otp_delivery",
        "sync_worker",
    ),
    "side_effect_before_ack": (
        "otp_delivery",
        "web_push",
        "idempotenc",
        "outbox",
        "delivery_queue",
        "delivery_contract",
        "trade_telegram_delivery",
        "trade_webapp_delivery",
    ),
    "two_concurrent_commands": (
        "idempotenc",
        "concurrent",
        "claim_index",
        "contention",
    ),
    "process_restart": (
        "lifespan",
        "resume",
        "restart",
    ),
    "unknown_authority_schema_hash": (
        "authority",
        "signature",
        "foreign_surface",
        "field_policy",
        "schema_hash",
        "metrics_guard",
    ),
}
CROSS_FAMILY_FORBIDDEN_EXAMPLES = (
    ("IDENTITY", "commit_before_event_side_effect", "tests/test_offer_publication_worker.py"),
    ("IDENTITY", "commit_before_event_side_effect", "tests/test_trade_delivery_reconciliation_service.py"),
    ("MARKET", "failure_before_db_commit", "tests/test_offers_router_create_success.py"),
    ("MSG", "side_effect_before_ack", "tests/test_telegram_admin_broadcast_confirm_idempotency.py"),
    ("MSG", "side_effect_before_ack", "tests/test_telegram_delivery_queue_production_cutover.py"),
)
HUMAN_MATRIX = REPO_ROOT / "docs/refactor/two-server/section-1/04-surface-policy-matrix.md"
DENIAL_DECISIONS = {"INELIGIBLE", "INELIGIBLE_FOR_NEW_MARKET_MUTATION", "NOT_APPLICABLE"}
ADMISSION_DECISIONS = {"ELIGIBLE", "ELIGIBLE_FOR_REQUEST_ONLY", "ELIGIBLE_FOR_READ_OR_TERMINAL_REPLAY"}
FORBIDDEN_ELIGIBILITY_DECISIONS = {
    "INHERIT_PERSONA_ELIGIBILITY",
    "INHERIT",
    "WHERE_POLICY_ALLOWS",
    "ELIGIBLE_IF",
}
DEFINITE_ELIGIBILITY_DECISIONS = {
    "ELIGIBLE",
    "ELIGIBLE_FOR_REQUEST_ONLY",
    "ELIGIBLE_FOR_READ_OR_TERMINAL_REPLAY",
    "INELIGIBLE",
    "INELIGIBLE_FOR_NEW_MARKET_MUTATION",
    "NOT_APPLICABLE",
    "UNRESOLVED",
}
SECRET_SCAN_RESULT = "NO_SECRET_VALUE / DETECTION_SIGNATURES_PRESENT"


def _literal_string(node: ast.AST | None) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    raise AssertionError(f"expected static string literal, got {ast.dump(node) if node else None}")


def _api_mount_prefixes() -> dict[str, str]:
    tree = ast.parse((REPO_ROOT / "main.py").read_text(encoding="utf-8"), filename="main.py")
    prefixes: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "include_router" or not isinstance(node.func.value, ast.Name):
            continue
        if node.func.value.id != "api_router" or not node.args:
            continue
        router_ref = node.args[0]
        if not (
            isinstance(router_ref, ast.Attribute)
            and router_ref.attr == "router"
            and isinstance(router_ref.value, ast.Name)
        ):
            continue
        prefix_node = next((kw.value for kw in node.keywords if kw.arg == "prefix"), None)
        prefixes[router_ref.value.id] = "/api" + _literal_string(prefix_node)
    return prefixes


def _fastapi_contracts() -> list[tuple[str, str, str, str, str]]:
    contracts: list[tuple[str, str, str, str, str]] = []
    prefixes = _api_mount_prefixes()
    attrs = {"get", "post", "put", "patch", "delete", "websocket"}
    for path in sorted((REPO_ROOT / "api/routers").glob("*.py")):
        if path.name == "__init__.py":
            continue
        module = path.stem
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not (
                    isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr in attrs
                    and isinstance(dec.func.value, ast.Name)
                    and dec.func.value.id == "router"
                ):
                    continue
                method = dec.func.attr.upper()
                full_path = prefixes[module] + _literal_string(dec.args[0])
                mutation_class = (
                    "READ_OR_STREAM" if method in {"GET", "WEBSOCKET"} else "MUTATION_OR_COMMAND"
                )
                source = f"{path.relative_to(REPO_ROOT)}:{dec.lineno}"
                contracts.append((method, full_path, f"{module}.{node.name}", source, mutation_class))

    path = REPO_ROOT / "main.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not (
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Attribute)
                and dec.func.attr in attrs
                and isinstance(dec.func.value, ast.Name)
                and dec.func.value.id == "app"
            ):
                continue
            method = dec.func.attr.upper()
            mutation_class = (
                "READ_OR_STREAM" if method in {"GET", "WEBSOCKET"} else "MUTATION_OR_COMMAND"
            )
            contracts.append(
                (
                    method,
                    _literal_string(dec.args[0]),
                    f"main.{node.name}",
                    f"main.py:{dec.lineno}",
                    mutation_class,
                )
            )
    return contracts


def _bot_contracts() -> list[tuple[str, str, str, str]]:
    contracts: list[tuple[str, str, str, str]] = []
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
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not (
                    isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr in kinds
                    and isinstance(dec.func.value, ast.Name)
                    and dec.func.value.id == "router"
                ):
                    continue
                filter_expr = ", ".join(ast.unparse(item) for item in dec.args)
                contracts.append(
                    (
                        dec.func.attr,
                        filter_expr,
                        f"{path.stem}.{node.name}",
                        f"{path.relative_to(REPO_ROOT)}:{dec.lineno}",
                    )
                )
    return contracts


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


def _web_contracts() -> list[tuple[str, str, str, tuple[str, ...], str]]:
    path = REPO_ROOT / "frontend/src/router/index.ts"
    lines = path.read_text(encoding="utf-8").splitlines()
    contracts: list[tuple[str, str, str, tuple[str, ...], str]] = []
    for line_no, line in enumerate(lines, start=1):
        path_match = re.match(r"\s{6}path:\s*'([^']+)',", line)
        if not path_match:
            continue
        block = [line]
        for later in lines[line_no:]:
            if later == "    },":
                break
            block.append(later)
        block_text = "\n".join(block)
        name_match = re.search(r"\bname:\s*'([^']+)'", block_text)
        if not name_match:
            raise AssertionError(f"route at line {line_no} has no static name")
        import_component = re.search(r"component:\s*\(\)\s*=>\s*import\('\.\./views/([^']+)'\)", block_text)
        named_component = re.search(r"component:\s*([A-Za-z][A-Za-z0-9_]*)", block_text)
        redirect = re.search(r"redirect:\s*\{\s*name:\s*'([^']+)'", block_text)
        if import_component:
            component = import_component.group(1)
        elif named_component:
            component = named_component.group(1)
        elif redirect:
            component = f"REDIRECT:{redirect.group(1)}"
        else:
            raise AssertionError(f"route {name_match.group(1)} has no component/redirect")
        guards = tuple(sorted(set(re.findall(r"(requires[A-Za-z]+):\s*true", block_text))))
        contracts.append(
            (
                path_match.group(1),
                name_match.group(1),
                component,
                guards,
                f"frontend/src/router/index.ts:{line_no}",
            )
        )
    return contracts


def _nonempty_contract_value(value: object) -> bool:
    if value is None or value == "" or value == [] or value == {}:
        return False
    if isinstance(value, dict):
        if value.get("status") == "NOT_APPLICABLE":
            return bool(value.get("reason"))
        if value.get("status") == "APPLICABLE":
            return bool(value.get("rules"))
        if value.get("decision") == "NOT_APPLICABLE":
            return bool(value.get("reason"))
        if value.get("decision") in DEFINITE_ELIGIBILITY_DECISIONS:
            return bool(value.get("predicate") or value.get("reason"))
    return True


def _iter_eligibility_nodes(value: object):
    if isinstance(value, dict):
        if "decision" in value:
            yield value
        for child in value.values():
            yield from _iter_eligibility_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_eligibility_nodes(child)


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text).lower()


def _assert_no_deferred_or_ambiguous_text(test, text: str, label: str) -> None:
    compact = _normalized(text)
    test.assertFalse(any(phrase in compact for phrase in AMBIGUOUS_POLICY_PHRASES), label)
    test.assertFalse(any(token.lower() in compact for token in DEFERRAL_TOKENS), label)
    test.assertNotIn(INHERIT_PREDICATE_PHRASE, compact, label)
    test.assertNotIn("inherit_persona_eligibility", compact, label)
    for pattern in INHERIT_SEMANTIC_PATTERNS:
        test.assertIsNone(re.search(pattern, compact), f"{label}: {pattern}")


def _assert_predicate_matches_decision(test, decision: str, node: dict, label: str) -> None:
    predicate = _normalized(str(node.get("predicate") or ""))
    reason = _normalized(str(node.get("reason") or ""))
    if decision in DENIAL_DECISIONS:
        test.assertNotIn("is not ineligible", predicate, label)
        test.assertFalse(predicate.startswith("eligible"), label)
        if decision == "NOT_APPLICABLE":
            test.assertTrue(node.get("reason"), label)
        else:
            test.assertTrue(predicate or reason, label)
    if decision in ADMISSION_DECISIONS:
        test.assertTrue(node.get("predicate"), label)
        test.assertNotIn("botaccessdecision(false", predicate.replace(" ", ""), label)


def _assert_definite_variant(test, variant: dict, *, require_rule_sources: bool = True) -> None:
    test.assertTrue(variant.get("contract_id"), variant)
    applicability = variant.get("applicability")
    test.assertIn(
        applicability,
        {"ELIGIBLE", "NOT_APPLICABLE", "APPLICABLE", "SHARED_EVIDENCE_TEMPLATE", "HIGH_BLOCKER"},
        variant,
    )
    if applicability in {"NOT_APPLICABLE", "HIGH_BLOCKER"}:
        test.assertTrue(variant.get("reason") or (variant.get("eligibility") or {}).get("reason") or variant.get("named_blocker"), variant)
    test.assertTrue(variant.get("expected_outcome"), variant.get("variant_id") or variant)
    _assert_no_deferred_or_ambiguous_text(test, json.dumps(variant, ensure_ascii=False), variant.get("variant_id") or "variant")
    if require_rule_sources:
        sources = variant.get("rule_sources") or variant.get("evidence_refs") or []
        test.assertTrue(sources, variant.get("variant_id") or variant)
        for ref in sources:
            test.assertTrue((REPO_ROOT / ref).exists(), ref)
    for node in _iter_eligibility_nodes(variant.get("eligibility")):
        decision = node["decision"]
        test.assertNotIn(decision, FORBIDDEN_ELIGIBILITY_DECISIONS, variant)
        test.assertIn(decision, DEFINITE_ELIGIBILITY_DECISIONS, variant)
        _assert_predicate_matches_decision(test, decision, node, variant.get("variant_id") or decision)
        if decision in {"NOT_APPLICABLE", "UNRESOLVED"}:
            test.assertTrue(node.get("reason"), variant)
        else:
            test.assertTrue(node.get("predicate") or node.get("expected_outcome"), variant)


class P100InventoryRefreshCharacterizationTests(unittest.TestCase):
    def test_assignment_base_is_an_ancestor_of_this_worktree(self):
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", EXPECTED_COMMIT, "HEAD"],
            cwd=REPO_ROOT,
            check=False,
        )
        self.assertEqual(result.returncode, 0, EXPECTED_COMMIT)

    def test_live_surface_semantics_match_verified_inventory(self):
        api_inv = _load_json("surface-api.json")
        bot_inv = _load_json("surface-bot.json")
        web_inv = _load_json("surface-web.json")
        jobs_inv = _load_json("surface-jobs.json")
        index = _load_json("surface-behavior-inventory.json")

        live_api = _fastapi_contracts()
        live_bot = _bot_contracts()
        live_web = _web_contracts()
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
        self.assertEqual(
            Counter(live_api),
            Counter(
                (
                    item["method"],
                    item["path"],
                    item["handler"],
                    item["source"],
                    item["mutation_class"],
                )
                for item in api_inv["entries"]
            ),
        )
        self.assertEqual(
            Counter(live_bot),
            Counter(
                (item["kind"], item["filter"], item["handler"], item["source"])
                for item in bot_inv["entries"]
            ),
        )
        self.assertEqual(
            Counter(live_web),
            Counter(
                (
                    item["path"],
                    item["name"],
                    item["component"],
                    tuple(sorted(item["guards"])),
                    item["source"],
                )
                for item in web_inv["entries"]
            ),
        )

        authority = background_job_authority_entries()
        by_name = {item["job_name"]: item for item in jobs_inv["entries"]}
        self.assertEqual(set(by_name), set(authority))
        for job_name, actual in authority.items():
            recorded = by_name[job_name]
            self.assertEqual(recorded["required_registry_member"], job_name in REQUIRED_BACKGROUND_JOBS)
            self.assertEqual(recorded["current_allowed_servers"], list(actual.allowed_servers))
            self.assertEqual(recorded["mutated_tables"], list(actual.mutated_tables))
            self.assertEqual(recorded["local_runtime"], actual.local_runtime)
            self.assertEqual(recorded["external_state"], list(actual.external_state))
            self.assertEqual(recorded["side_effects"], list(actual.side_effects))
            self.assertTrue(recorded["target_authority_candidates"])

    def test_inventory_provenance_preserves_origin_and_records_verification(self):
        for name in (
            "surface-api.json",
            "surface-bot.json",
            "surface-web.json",
            "surface-jobs.json",
            "surface-behavior-inventory.json",
        ):
            payload = _load_json(name)
            self.assertEqual(payload["generated_from_commit"], ORIGINAL_INVENTORY_COMMIT)
            self.assertEqual(payload["verified_against_commit"], EXPECTED_COMMIT)
        scenarios = _load_json("scenario-records.json")
        self.assertEqual(scenarios["generated_from_commit"], EXPECTED_COMMIT)

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

    def test_normalized_behavior_contracts_are_real_and_complete(self):
        payload = _load_json("scenario-records.json")
        contracts = payload["behavior_contracts"]
        self.assertEqual(len(contracts), payload["coverage"]["behavior_contracts"])
        ids = {item["contract_id"] for item in contracts}
        self.assertEqual(len(ids), len(contracts))
        self.assertEqual(FAMILIES, {item["family"] for item in contracts if item["family"] in FAMILIES})
        for contract in contracts:
            self.assertEqual(CORE_CONTRACT_FIELDS - set(contract), set(), contract["contract_id"])
            for field in CORE_CONTRACT_FIELDS:
                self.assertTrue(
                    _nonempty_contract_value(contract[field]),
                    f"{contract['contract_id']}:{field}",
                )
            self.assertTrue(all((REPO_ROOT / ref).exists() for ref in contract["regression_tests"]))
            self.assertTrue(set(contract["regression_tests"]) - GENERIC_META_TESTS, contract["contract_id"])
            for source in contract["reference_evidence"]["sources"]:
                self.assertTrue((REPO_ROOT / source).exists(), f"{contract['contract_id']} -> {source}")
            contract_text = json.dumps(contract, ensure_ascii=False).lower()
            self.assertFalse(any(phrase in contract_text for phrase in AMBIGUOUS_POLICY_PHRASES))
            self.assertNotIn("inherit_persona_eligibility", contract_text)
            if contract.get("mutation_class") != "SCHEDULED_JOB" or contract["family"] != "OPS":
                self.assertNotIn("this assignment authorizes no", contract_text)
            if contract.get("mutation_class") == "READ_OR_STREAM" or contract.get("operation_kind") in NON_PRODUCT_MUTATION_KINDS:
                self.assertEqual(contract["db_mutations"]["status"], "NOT_APPLICABLE", contract["contract_id"])
                self.assertTrue(contract["db_mutations"].get("reason"), contract["contract_id"])
            if contract.get("operation_kind"):
                self.assertIn(contract["operation_kind"], OPERATION_KINDS, contract["contract_id"])
            if contract.get("operation_kind") == "READ_QUERY":
                self.assertEqual(contract["side_effects"]["status"], "NOT_APPLICABLE", contract["contract_id"])
                self.assertTrue(contract["side_effects"].get("reason"), contract["contract_id"])
                self.assertNotIn("handler does send", contract_text)
            if contract.get("operation_kind") == "CRUD_MUTATION":
                self.assertFalse(
                    any(phrase in contract_text for phrase in CRUD_BROADCAST_PHRASES),
                    contract["contract_id"],
                )
                self.assertEqual(contract["db_mutations"]["status"], "APPLICABLE", contract["contract_id"])
            if contract.get("operation_kind") in {"ACK_ONLY", "EPHEMERAL_ACTIVITY", "PARSE_NO_COMMIT"}:
                self.assertEqual(contract["db_mutations"]["status"], "NOT_APPLICABLE", contract["contract_id"])
                self.assertEqual(contract["events_outbox"]["status"], "NOT_APPLICABLE", contract["contract_id"])

    def test_every_surface_seed_maps_once_with_seed_specific_behavioral_evidence(self):
        payload = _load_json("scenario-records.json")
        contract_ids = {item["contract_id"] for item in payload["behavior_contracts"]}
        rules = payload["seed_mapping"]["rules"]
        seeds = []
        for name in ("surface-api.json", "surface-bot.json", "surface-web.json", "surface-jobs.json"):
            seeds.extend(_load_json(name)["entries"])

        self.assertEqual(len(seeds), 465)
        self.assertEqual(len({item["behavior_id"] for item in seeds}), 465)
        mapped_lines = []
        family_counts = Counter(item["family"] for item in seeds)
        self.assertEqual(dict(family_counts), payload["seed_mapping"]["expected_family_counts"])
        for seed in seeds:
            self.assertIn(seed.get("mutation_class"), MUTATION_CLASSES, seed["behavior_id"])
            self.assertIn(seed.get("operation_kind"), OPERATION_KINDS, seed["behavior_id"])
            matches = [
                rule
                for rule in rules
                if rule["family"] == seed["family"] and rule["operation_kind"] == seed["operation_kind"]
            ]
            self.assertEqual(len(matches), 1, seed["behavior_id"])
            contract_id = matches[0]["contract_id"]
            self.assertIn(contract_id, contract_ids)
            contract = next(item for item in payload["behavior_contracts"] if item["contract_id"] == contract_id)
            self.assertEqual(contract.get("operation_kind"), seed["operation_kind"], seed["behavior_id"])
            if seed["operation_kind"] in NON_PRODUCT_MUTATION_KINDS:
                self.assertEqual(
                    contract["db_mutations"]["status"],
                    "NOT_APPLICABLE",
                    f"{seed['operation_kind']} seed mapped to mutation contract: {seed['behavior_id']} -> {contract_id}",
                )
            self.assertTrue(seed["test_refs"], seed["behavior_id"])
            self.assertTrue(
                set(seed["test_refs"]) - GENERIC_META_TESTS,
                f"generic-only evidence: {seed['behavior_id']}",
            )
            for ref in seed["test_refs"]:
                self.assertTrue((REPO_ROOT / ref).exists(), f"{seed['behavior_id']} -> {ref}")
            mapped_lines.append(f"{seed['behavior_id']}={contract_id}")

        resolved = ("\n".join(sorted(mapped_lines)) + "\n").encode()
        self.assertEqual(
            hashlib.sha256(resolved).hexdigest(),
            payload["seed_mapping"]["resolved_index_sha256"],
        )
        for behavior_id in FALSE_CRUD_EXAMPLES:
            seed = next(item for item in seeds if item["behavior_id"] == behavior_id)
            self.assertNotEqual(seed["operation_kind"], "CRUD_MUTATION", behavior_id)

    def test_bot_menu_and_api_broadcast_do_not_share_a_mutation_contract(self):
        payload = _load_json("scenario-records.json")
        seeds = {}
        for name in ("surface-api.json", "surface-bot.json", "surface-web.json", "surface-jobs.json"):
            for entry in _load_json(name)["entries"]:
                seeds[entry["behavior_id"]] = entry
        menu = seeds[MENU_NAV_EXAMPLE]
        broadcast = seeds[BROADCAST_EXAMPLE]
        self.assertEqual(menu["operation_kind"], "MENU_NAV")
        self.assertEqual(broadcast["operation_kind"], "BROADCAST")
        rules = payload["seed_mapping"]["rules"]
        menu_contract_id = next(
            rule["contract_id"]
            for rule in rules
            if rule["family"] == menu["family"] and rule["operation_kind"] == menu["operation_kind"]
        )
        broadcast_contract_id = next(
            rule["contract_id"]
            for rule in rules
            if rule["family"] == broadcast["family"] and rule["operation_kind"] == broadcast["operation_kind"]
        )
        self.assertNotEqual(menu_contract_id, broadcast_contract_id)
        menu_contract = next(item for item in payload["behavior_contracts"] if item["contract_id"] == menu_contract_id)
        broadcast_contract = next(
            item for item in payload["behavior_contracts"] if item["contract_id"] == broadcast_contract_id
        )
        self.assertEqual(menu_contract["db_mutations"]["status"], "NOT_APPLICABLE")
        self.assertEqual(broadcast_contract["db_mutations"]["status"], "APPLICABLE")
        self.assertEqual(broadcast_contract["events_outbox"]["status"], "APPLICABLE")
        self.assertEqual(broadcast_contract["side_effects"]["status"], "APPLICABLE")

    def test_pdf_export_seed_is_not_mapped_to_a_no_send_read_contract(self):
        payload = _load_json("scenario-records.json")
        seeds = {
            entry["behavior_id"]: entry
            for name in ("surface-api.json", "surface-bot.json", "surface-web.json", "surface-jobs.json")
            for entry in _load_json(name)["entries"]
        }
        seed = seeds[DOCUMENT_SEND_EXAMPLE]
        self.assertEqual(seed["handler"], "panel.show_recent_trades_pdf")
        self.assertEqual(seed["source"], "bot/handlers/panel.py:534")
        self.assertEqual(seed["operation_kind"], "DOCUMENT_SEND")
        contract_id = next(
            rule["contract_id"]
            for rule in payload["seed_mapping"]["rules"]
            if rule["family"] == seed["family"] and rule["operation_kind"] == seed["operation_kind"]
        )
        self.assertNotEqual(contract_id, "BC-TG-READ_QUERY-V1")
        contract = next(item for item in payload["behavior_contracts"] if item["contract_id"] == contract_id)
        self.assertEqual(contract["operation_kind"], "DOCUMENT_SEND")
        self.assertEqual(contract["side_effects"]["status"], "APPLICABLE")
        self.assertEqual(contract["response"]["status"], "APPLICABLE")
        talk = json.dumps(contract, ensure_ascii=False).lower()
        self.assertTrue("send_private_document_via_runtime" in talk or "document" in talk)
        self.assertNotIn("this read does not send", talk)
        read_contract = next(item for item in payload["behavior_contracts"] if item["contract_id"] == "BC-TG-READ_QUERY-V1")
        self.assertEqual(read_contract["side_effects"]["status"], "NOT_APPLICABLE")

    def test_matrix_axes_resolve_definite_eligibility_and_outcome(self):
        payload = _load_json("scenario-records.json")
        matrix = payload["matrix"]
        origin_variants = matrix["origin_variants"]
        persona_variants = matrix["persona_variants"]
        time_variants = matrix["time_variants"]
        outcome_variants = matrix["outcome_variants"]
        self.assertEqual(
            {(item["offer_origin"], item["request_origin"]) for item in origin_variants},
            ORIGIN_ROWS,
        )
        self.assertEqual(matrix["expanded_variant_count"], len(payload["composed_eligibility"]))
        self.assertEqual(matrix["expanded_variant_count"], 180)
        self.assertNotEqual(matrix["expanded_variant_count"], 360)
        self.assertEqual(len(origin_variants), 6)
        self.assertEqual(len(persona_variants) * 2 * len(time_variants), 30)

        for variant in [*origin_variants, *persona_variants, *time_variants, *outcome_variants]:
            self.assertEqual(variant["contract_id"], matrix["contract_id"])
            _assert_definite_variant(self, variant)

        for persona in persona_variants:
            tier = persona["customer_tier"]
            if tier["value"] == "NOT_APPLICABLE":
                self.assertTrue(tier["reason"])
            for surface in ("WEBAPP", "TELEGRAM_BOT"):
                decision = persona["eligibility"][surface]
                self.assertIn(decision["decision"], DEFINITE_ELIGIBILITY_DECISIONS)
                self.assertNotIn(decision["decision"], FORBIDDEN_ELIGIBILITY_DECISIONS)
                self.assertTrue(decision["predicate"])
                self.assertTrue(decision["expected_outcome"])
        self.assertEqual(
            next(item for item in time_variants if item["variant_id"] == "TIME-NORMAL")["eligibility"]["decision"],
            "ELIGIBLE",
        )
        tier2 = next(item for item in persona_variants if item["variant_id"] == "PERSONA-CUSTOMER-TIER2")
        self.assertEqual(tier2["eligibility"]["TELEGRAM_BOT"]["decision"], "INELIGIBLE")
        accountant = next(item for item in persona_variants if item["variant_id"] == "PERSONA-ACCOUNTANT")
        self.assertEqual(
            {item["decision"] for item in accountant["eligibility"].values()},
            {"INELIGIBLE"},
        )

    def test_composed_eligibility_covers_every_persona_surface_time_cell(self):
        payload = _load_json("scenario-records.json")
        personas = [item["variant_id"] for item in payload["matrix"]["persona_variants"]]
        times = [item["variant_id"] for item in payload["matrix"]["time_variants"]]
        outcomes = [item["variant_id"] for item in payload["matrix"]["outcome_variants"]]
        cells = payload["composed_eligibility"]
        self.assertEqual(len(cells), 180)
        self.assertEqual(payload["coverage"]["composed_eligibility_cells"], 180)
        seen = set()
        for cell in cells:
            _assert_definite_variant(self, cell)
            decision = cell["eligibility"]["decision"]
            _assert_predicate_matches_decision(self, decision, cell["eligibility"], cell["variant_id"])
            self.assertIn(cell["offer_origin_rule"]["status"], {"INDEPENDENT_OF_ADMISSION"})
            self.assertIn(cell["outcome_variant_id"], outcomes)
            seen.add(
                (
                    cell["persona_variant_id"],
                    cell["request_origin"],
                    cell["time_variant_id"],
                    cell["telegram_link_variant_id"],
                    cell["outcome_variant_id"],
                )
            )
        self.assertEqual(len(seen), 180)
        self.assertEqual({item[0] for item in seen}, set(personas))
        self.assertEqual({item[2] for item in seen}, set(times))
        accountant_reject = next(
            item
            for item in cells
            if item["persona_variant_id"] == "PERSONA-ACCOUNTANT"
            and item["request_origin"] == "TELEGRAM_BOT"
            and item["time_variant_id"] == "TIME-NORMAL"
            and item["telegram_link_variant_id"] == "TELEGRAM-LINK-PRESENT"
            and item["outcome_variant_id"] == "OUTCOME-REJECT"
        )
        self.assertEqual(accountant_reject["admission_decision"], "INELIGIBLE")
        self.assertEqual(accountant_reject["eligibility"]["decision"], "ELIGIBLE")
        tier2_bot_success = next(
            item
            for item in cells
            if item["persona_variant_id"] == "PERSONA-CUSTOMER-TIER2"
            and item["request_origin"] == "TELEGRAM_BOT"
            and item["time_variant_id"] == "TIME-NORMAL"
            and item["telegram_link_variant_id"] == "TELEGRAM-LINK-PRESENT"
            and item["outcome_variant_id"] == "OUTCOME-SUCCESS"
        )
        self.assertEqual(tier2_bot_success["admission_decision"], "INELIGIBLE")
        self.assertEqual(tier2_bot_success["eligibility"]["decision"], "NOT_APPLICABLE")

    def test_invalid_combinations_and_policy_effects_are_explicit(self):
        payload = _load_json("scenario-records.json")
        combinations = payload["invalid_combinations"]
        self.assertEqual(len(combinations), payload["coverage"]["invalid_combinations"])
        self.assertEqual(len(combinations), 6)
        for item in combinations:
            _assert_definite_variant(self, item)
            self.assertEqual(item["applicability"], "NOT_APPLICABLE")
            self.assertTrue(item["reason"])

        effects = payload["policy_effects"]
        self.assertEqual({item["effect_id"] for item in effects}, {
            "CONFIRMATION",
            "QUOTA",
            "COMMISSION",
            "PUBLICATION",
            "NOTIFICATION",
            "TIMEOUT",
            "FAILURE",
        })
        for item in effects:
            _assert_definite_variant(self, item)

        links = payload["telegram_link_variants"]
        self.assertEqual(len(links), 3)
        absent = next(item for item in links if item["variant_id"] == "TELEGRAM-LINK-ABSENT")
        self.assertEqual(absent["eligibility"]["TELEGRAM_BOT"]["decision"], "INELIGIBLE")
        self.assertEqual(absent["eligibility"]["WEBAPP"]["decision"], "NOT_APPLICABLE")
        self.assertTrue(absent["eligibility"]["WEBAPP"]["reason"])
        for item in links:
            _assert_definite_variant(self, item)

    def test_failure_axes_and_dr015_deferral_are_explicit(self):
        payload = _load_json("scenario-records.json")
        timeline = payload["failure_timeline"]
        self.assertEqual(set(timeline["families"]), FAMILIES)
        self.assertEqual(
            {item["failure_boundary"] for item in timeline["boundaries"]},
            FAILURE_BOUNDARIES,
        )
        self.assertEqual(
            len(timeline["families"]) * len(timeline["boundaries"]),
            timeline["expanded_variant_count"],
        )
        scenario_text = json.dumps(payload, ensure_ascii=False)
        _assert_no_deferred_or_ambiguous_text(self, scenario_text, "scenario-records.json")
        for boundary in timeline["boundaries"]:
            self.assertEqual(boundary["contract_id"], timeline["contract_id"])
            self.assertEqual(boundary["applicability"], "SHARED_EVIDENCE_TEMPLATE")
            self.assertNotIn("eligibility", boundary)
            self.assertTrue(boundary["expected_outcome"])
            self.assertTrue(boundary["evidence_refs"])
            _assert_no_deferred_or_ambiguous_text(self, json.dumps(boundary, ensure_ascii=False), boundary["variant_id"])
            for ref in boundary["evidence_refs"]:
                self.assertTrue((REPO_ROOT / ref).exists(), ref)

        cells = payload["family_boundary_variants"]
        self.assertEqual(len(cells), 72)
        self.assertEqual(payload["coverage"]["family_boundary_variants"], 72)
        expected_pairs = {
            (family, boundary)
            for family in timeline["families"]
            for boundary in FAILURE_BOUNDARIES
        }
        seen_pairs = set()
        for cell in cells:
            _assert_definite_variant(self, cell)
            self.assertIn(cell["applicability"], {"ELIGIBLE", "NOT_APPLICABLE", "HIGH_BLOCKER"})
            if cell["applicability"] == "NOT_APPLICABLE":
                self.assertEqual(cell["eligibility"]["decision"], "NOT_APPLICABLE")
                self.assertTrue(cell["eligibility"]["reason"])
            elif cell["applicability"] == "HIGH_BLOCKER":
                self.assertEqual(cell["eligibility"]["decision"], "UNRESOLVED")
                self.assertTrue(cell["eligibility"]["reason"])
                self.assertTrue(cell.get("named_blocker"))
                self.assertEqual(cell.get("evidence_cases") or [], [])
            else:
                self.assertEqual(cell["eligibility"]["decision"], "ELIGIBLE")
                self.assertTrue(cell["eligibility"]["predicate"])
            seen_pairs.add((cell["family"], cell["failure_boundary"]))
        self.assertEqual(seen_pairs, expected_pairs)
        ops = {item["failure_boundary"]: item["applicability"] for item in cells if item["family"] == "OPS"}
        self.assertEqual(
            ops,
            {
                "failure_before_db_commit": "NOT_APPLICABLE",
                "commit_before_event_side_effect": "NOT_APPLICABLE",
                "side_effect_before_ack": "NOT_APPLICABLE",
                "two_concurrent_commands": "NOT_APPLICABLE",
                "process_restart": "NOT_APPLICABLE",
                "unknown_authority_schema_hash": "ELIGIBLE",
            },
        )
        registry = payload["blocker_registry"]
        self.assertEqual(registry["disposition_request"], "OPEN_HIGH_BLOCKERS_FOR_CODEX")
        self.assertNotEqual(registry["disposition_request"], "DEFERRED_TO_P1_06")
        self.assertNotEqual(registry["disposition_request"], "NONE_P1_00_HIGH_BLOCKERS_REMAIN")
        self.assertGreaterEqual(len(registry["items"]), 1)
        self.assertEqual(set(registry["required_fields"]), BLOCKER_REGISTRY_FIELDS)
        registry_ids = {item["blocker_id"] for item in registry["items"]}
        filename_only = 0
        unrouted = 0
        for cell in cells:
            if cell["applicability"] == "HIGH_BLOCKER":
                blocker = cell.get("named_blocker") or {}
                self.assertTrue(blocker, cell["variant_id"])
                self.assertIn(blocker["blocker_id"], registry_ids, cell["variant_id"])
                self.assertEqual(cell.get("evidence_cases") or [], [])
                continue
            if cell["applicability"] != "ELIGIBLE":
                continue
            refs = cell.get("evidence_refs") or []
            cases = cell.get("evidence_cases") or []
            self.assertFalse(cell.get("named_blocker"), cell["variant_id"])
            self.assertTrue(cases, cell["variant_id"])
            self.assertEqual(refs, [item["node_id"] for item in cases], cell["variant_id"])
            for case in cases:
                node_id = case["node_id"]
                talk = case["boundary_talk"]
                self.assertTrue(talk, cell["variant_id"])
                self.assertTrue(case.get("injects_named_boundary"), cell["variant_id"])
                path, class_name, func_name = parse_pytest_node_id(node_id)
                self.assertTrue((REPO_ROOT / path).exists(), node_id)
                fn = find_test_function(REPO_ROOT / path, class_name, func_name)
                self.assertIsNotNone(fn, node_id)
                blob = function_source_talk(fn)
                self.assertIn(talk, blob, node_id)
                self.assertIn("injects", fn.name, node_id)
                if "::" not in node_id:
                    filename_only += 1
            self.assertNotEqual(
                {Path(case["node_id"].split("::", 1)[0]).name for case in cases},
                set(),
            )
        self.assertEqual(filename_only, payload["closure"]["filename_only_failure_evidence"])
        self.assertEqual(payload["closure"]["filename_only_failure_evidence"], 0)
        for item in registry["items"]:
            missing = BLOCKER_REGISTRY_FIELDS - set(item)
            self.assertEqual(missing, set(), item.get("blocker_id"))
            if any(not item.get(field) for field in BLOCKER_REGISTRY_FIELDS):
                unrouted += 1
        self.assertEqual(unrouted, payload["closure"]["unrouted_named_blockers"])
        self.assertEqual(payload["closure"]["unrouted_named_blockers"], 0)
        self.assertEqual(len(registry["items"]), payload["closure"]["named_high_blockers"])
        self.assertEqual(
            sum(1 for cell in cells if cell["applicability"] == "HIGH_BLOCKER"),
            payload["closure"]["family_boundary_named_blockers"],
        )
        notify_side = next(item for item in cells if item["variant_id"] == "NOTIFY__SIDE-EFFECT-BEFORE-ACK")
        self.assertEqual(notify_side["applicability"], "NOT_APPLICABLE")
        self.assertEqual(notify_side["eligibility"]["decision"], "NOT_APPLICABLE")
        self.assertIn("no provider-send-before-ack", notify_side["eligibility"]["reason"])
        self.assertFalse(notify_side.get("named_blocker"), "NOTIFY side-effect must not be a High blocker")
        self.assertEqual(notify_side.get("evidence_cases") or [], [])
        from tests.test_p1_00_characterization_failure_boundaries import assert_cited_node_uses_family_path

        for cell in cells:
            if cell["applicability"] != "ELIGIBLE":
                continue
            for case in cell.get("evidence_cases") or []:
                assert_cited_node_uses_family_path(cell["family"], case["node_id"])

        self.assertEqual(len(payload["deferred_blockers"]), 1)
        timing = payload["deferred_blockers"][0]
        self.assertEqual(timing["blocker_id"], "DR-015")
        self.assertEqual(timing["status"], "DEFERRED_BLOCKER")
        self.assertEqual(timing["resolution_stage"], "DPL-1")
        self.assertEqual(timing["evidence_status"], "NOT_OBSERVED")
        self.assertIn("P1-02", timing["blocks_before_resolution"])

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

    def test_tracked_refresh_artifacts_have_no_secret_values(self):
        paths = list((REPO_ROOT / "docs/refactor/two-server/section-1").rglob("*"))
        leaks = []
        for path in paths:
            if not path.is_file() or path.suffix not in {".md", ".json", ".py"}:
                continue
            text = path.read_text(encoding="utf-8")
            if any(marker in text for marker in SECRET_MARKERS):
                leaks.append(str(path.relative_to(REPO_ROOT)))
        self.assertEqual(leaks, [])
        self.assertTrue(all(marker in Path(__file__).read_text(encoding="utf-8") for marker in SECRET_MARKERS))
        self.assertIn(SECRET_SCAN_RESULT, EVIDENCE_INDEX.read_text(encoding="utf-8"))

    def test_human_matrix_and_scenario_source_have_no_ambiguous_eligibility(self):
        matrix_text = HUMAN_MATRIX.read_text(encoding="utf-8")
        _assert_no_deferred_or_ambiguous_text(self, matrix_text, "04-surface-policy-matrix.md")
        self.assertIn("accountant INELIGIBLE", matrix_text)
        self.assertIn("tier-2 Bot INELIGIBLE", matrix_text)
        self.assertIn("admin ELIGIBLE with command", matrix_text)
        scenario_text = json.dumps(_load_json("scenario-records.json"), ensure_ascii=False)
        _assert_no_deferred_or_ambiguous_text(self, scenario_text, "scenario-records.json")


if __name__ == "__main__":
    unittest.main()
