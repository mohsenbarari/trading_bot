"""HIGH-1: Bot admin seeds must match FSM-only vs product-commit behavior."""

from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INV_DIR = REPO_ROOT / "docs/refactor/two-server/section-1/inventory"
ADMIN_SCAN_MODULES = ("admin", "admin_users", "admin_commodities")
PRODUCT_KIND = {"CRUD_MUTATION", "BROADCAST", "TOGGLE_MUTATION"}
NON_PRODUCT_KIND = {
    "FSM_PROMPT",
    "MENU_NAV",
    "ACK_ONLY",
    "READ_QUERY",
    "PROVIDER_REPLY",
}
HTTP_MUTATE = {"post", "put", "patch", "delete"}
RPC_MUTATE = {
    "forward_standard_invitation_to_iran",
    "create_telegram_admin_broadcast",
}
CODEX_EXACT_EXAMPLES = {
    "BOT-ADMIN-MESSAGE-ADMIN-PROCESS-INVITATION-ACCOUNT-NAME-L183": {
        "handler": "admin.process_invitation_account_name",
        "operation_kind": "FSM_PROMPT",
        "source": "bot/handlers/admin.py",
        "class": "FSM_OR_UI_ONLY",
    },
    "BOT-ADMIN-MESSAGE-ADMIN-PROCESS-INVITATION-MOBILE-L230": {
        "handler": "admin.process_invitation_mobile",
        "operation_kind": "FSM_PROMPT",
        "source": "bot/handlers/admin.py",
        "class": "FSM_OR_UI_ONLY",
    },
    "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-USER-EDIT-ROLE-L1078": {
        "handler": "admin_users.handle_user_edit_role",
        "operation_kind": "FSM_PROMPT",
        "source": "bot/handlers/admin_users.py",
        "class": "FSM_OR_UI_ONLY",
    },
    "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-SET-TRADES-L1312": {
        "handler": "admin_users.handle_set_trades",
        "operation_kind": "FSM_PROMPT",
        "source": "bot/handlers/admin_users.py",
        "class": "FSM_OR_UI_ONLY",
    },
    "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-SET-COMMODITIES-L1336": {
        "handler": "admin_users.handle_set_commodities",
        "operation_kind": "FSM_PROMPT",
        "source": "bot/handlers/admin_users.py",
        "class": "FSM_OR_UI_ONLY",
    },
    "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-USER-BLOCK-SETTINGS-L1607": {
        "handler": "admin_users.handle_user_block_settings",
        "operation_kind": "FSM_PROMPT",
        "source": "bot/handlers/admin_users.py",
        "class": "FSM_OR_UI_ONLY",
    },
    "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-ADMIN-SET-MAX-BLOCK-L1698": {
        "handler": "admin_users.handle_admin_set_max_block",
        "operation_kind": "FSM_PROMPT",
        "source": "bot/handlers/admin_users.py",
        "class": "FSM_OR_UI_ONLY",
    },
    "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-ADMIN-TOGGLE-BLOCK-L1649": {
        "handler": "admin_users.handle_admin_toggle_block",
        "operation_kind": "TOGGLE_MUTATION",
        "source": "bot/handlers/admin_users.py",
        "class": "PRODUCT_COMMIT",
    },
}


def _load_json(name: str) -> dict:
    return json.loads((INV_DIR / name).read_text(encoding="utf-8"))


def _function_node(path: Path, func_name: str):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return node
    raise AssertionError(f"missing function {func_name} in {path}")


def classify_admin_handler(path: Path, func_name: str) -> str:
    node = _function_node(path, func_name)
    commits = False
    http_mutate = False
    rpc = False
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute) and child.attr == "commit":
            commits = True
        if isinstance(child, ast.Attribute) and child.attr in {"post", "put", "patch"}:
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


class P100AdminSeedSemanticsCharacterizationTests(unittest.TestCase):
    def test_codex_examples_fail_when_inverted(self):
        seeds = {item["behavior_id"]: item for item in _load_json("surface-bot.json")["entries"]}
        for behavior_id, expected in CODEX_EXACT_EXAMPLES.items():
            seed = seeds[behavior_id]
            self.assertEqual(seed["handler"], expected["handler"], behavior_id)
            self.assertEqual(seed["operation_kind"], expected["operation_kind"], behavior_id)
            if expected["class"] == "FSM_OR_UI_ONLY":
                self.assertNotIn(seed["operation_kind"], PRODUCT_KIND, behavior_id)
            else:
                self.assertIn(seed["operation_kind"], PRODUCT_KIND, behavior_id)
            classified = classify_admin_handler(
                REPO_ROOT / expected["source"],
                expected["handler"].split(".", 1)[1],
            )
            self.assertEqual(classified, expected["class"], behavior_id)

    def test_scanned_admin_handlers_match_product_commit_or_fsm(self):
        seeds = [
            item
            for item in _load_json("surface-bot.json")["entries"]
            if item["family"] == "ADMIN"
            and item["handler"].split(".", 1)[0] in ADMIN_SCAN_MODULES
        ]
        self.assertGreaterEqual(len(seeds), 40)
        mismatches = []
        for seed in seeds:
            module, func_name = seed["handler"].split(".", 1)
            path = REPO_ROOT / "bot/handlers" / f"{module}.py"
            classified = classify_admin_handler(path, func_name)
            kind = seed["operation_kind"]
            if classified == "PRODUCT_COMMIT" and kind not in PRODUCT_KIND:
                mismatches.append(f"{seed['handler']} commits product rows but is {kind}")
            if classified == "FSM_OR_UI_ONLY" and kind in PRODUCT_KIND:
                mismatches.append(f"{seed['handler']} is FSM/UI-only but is {kind}")
            if classified == "FSM_OR_UI_ONLY":
                self.assertIn(kind, NON_PRODUCT_KIND, seed["handler"])
        self.assertEqual(mismatches, [])


if __name__ == "__main__":
    unittest.main()
