"""HIGH-1: ack, ephemeral activity, and parse seeds are not CRUD_MUTATION."""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import ast
import json
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INV_DIR = REPO_ROOT / "docs/refactor/two-server/section-1/inventory"

CODEX_FALSE_CRUD_EXAMPLES = {
    "BOT-TG-CALLBACK-QUERY-DEFAULT-ACKNOWLEDGE-NOOP-CALLBACK-L17": {
        "handler": "default.acknowledge_noop_callback",
        "operation_kind": "ACK_ONLY",
        "source": "bot/handlers/default.py",
        "forbidden_tokens": ("session.commit", "db.commit", "outbox"),
    },
    "BOT-TG-CALLBACK-QUERY-DEFAULT-HANDLE-UNMATCHED-CALLBACK-L24": {
        "handler": "default.handle_unmatched_callback",
        "operation_kind": "ACK_ONLY",
        "source": "bot/handlers/default.py",
        "forbidden_tokens": ("session.commit", "db.commit", "outbox"),
    },
    "BOT-TG-MESSAGE-DEFAULT-HANDLE-UNAUTHORIZED-MESSAGES-L43": {
        "handler": "default.handle_unauthorized_messages",
        "operation_kind": "ACK_ONLY",
        "source": "bot/handlers/default.py",
        "forbidden_tokens": ("session.commit", "db.commit", "outbox"),
    },
    "API-MSG-POST-CHAT-SEND-TYPING-SIGNAL-L918": {
        "handler": "chat.send_typing_signal",
        "operation_kind": "EPHEMERAL_ACTIVITY",
        "source": "api/routers/chat.py",
        "forbidden_tokens": ("persist_sent_direct_message", "db.add(message)"),
    },
    "API-MSG-POST-CHAT-SEND-DIRECT-ACTIVITY-SIGNAL-L939": {
        "handler": "chat.send_direct_activity_signal",
        "operation_kind": "EPHEMERAL_ACTIVITY",
        "source": "api/routers/chat.py",
        "forbidden_tokens": ("persist_sent_direct_message", "db.add(message)"),
    },
    "API-MSG-POST-CHAT-SEND-ROOM-ACTIVITY-SIGNAL-L962": {
        "handler": "chat.send_room_activity_signal",
        "operation_kind": "EPHEMERAL_ACTIVITY",
        "source": "api/routers/chat.py",
        "forbidden_tokens": ("persist_sent_direct_message", "db.add(message)"),
    },
    "API-OFFER-POST-OFFERS-PARSE-OFFER-TEXT-L3184": {
        "handler": "offers.parse_offer_text",
        "operation_kind": "PARSE_NO_COMMIT",
        "source": "api/routers/offers.py",
        "forbidden_tokens": ("Offer(", "db.add(", "await db.commit()"),
    },
    "API-OFFER-POST-OFFERS-PREVIEW-COIN-COMMODITY-INFERENCE-L1592": {
        "handler": "offers.preview_coin_commodity_inference",
        "operation_kind": "SHADOW_OBSERVATION",
        "source": "api/routers/offers.py",
        "forbidden_tokens": ("Offer(", "OfferCreate", "create_offer"),
    },
    "BOT-ADMIN-CALLBACK-QUERY-ADMIN-START-INVITATION-CREATION-INLINE-L160": {
        "handler": "admin.start_invitation_creation_inline",
        "operation_kind": "FSM_PROMPT",
        "source": "bot/handlers/admin.py",
        "forbidden_tokens": ("Invitation(", "db.commit", "session.commit"),
    },
    "BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-STALE-TRADE-CREATION-CALLBACK-L3504": {
        "handler": "trade_create.handle_stale_trade_creation_callback",
        "operation_kind": "ACK_ONLY",
        "source": "bot/handlers/trade_create.py",
        "forbidden_tokens": ("db.commit", "session.commit", "Offer("),
    },
}


def _load_json(name: str) -> dict:
    return json.loads((INV_DIR / name).read_text(encoding="utf-8"))


def _all_seeds() -> dict[str, dict]:
    seeds = {}
    for name in ("surface-api.json", "surface-bot.json", "surface-web.json", "surface-jobs.json"):
        for entry in _load_json(name)["entries"]:
            seeds[entry["behavior_id"]] = entry
    return seeds


def _function_source(path: Path, func_name: str) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return ast.get_source_segment(path.read_text(encoding="utf-8"), node) or ""
    raise AssertionError(f"missing function {func_name} in {path}")


class P100OperationExceptionCharacterizationTests(unittest.TestCase):
    def test_codex_false_mutation_examples_are_not_on_crud_mutation(self):
        payload = _load_json("scenario-records.json")
        seeds = _all_seeds()
        rules = payload["seed_mapping"]["rules"]
        contracts = {item["contract_id"]: item for item in payload["behavior_contracts"]}
        for behavior_id, expected in CODEX_FALSE_CRUD_EXAMPLES.items():
            seed = seeds[behavior_id]
            self.assertEqual(seed["handler"], expected["handler"], behavior_id)
            self.assertEqual(seed["operation_kind"], expected["operation_kind"], behavior_id)
            self.assertNotEqual(seed["operation_kind"], "CRUD_MUTATION", behavior_id)
            match = next(
                rule
                for rule in rules
                if rule["family"] == seed["family"] and rule["operation_kind"] == seed["operation_kind"]
            )
            self.assertNotEqual(match["contract_id"], "BC-OFFER-CRUD_MUTATION-V1", behavior_id)
            self.assertNotIn("CRUD_MUTATION", match["contract_id"], behavior_id)
            contract = contracts[match["contract_id"]]
            if expected["operation_kind"] == "SHADOW_OBSERVATION":
                self.assertEqual(contract["db_mutations"]["status"], "APPLICABLE", behavior_id)
                self.assertEqual(contract["events_outbox"]["status"], "NOT_APPLICABLE", behavior_id)
                talk = json.dumps(contract, ensure_ascii=False).lower()
                self.assertIn("no offer row", talk, behavior_id)
            else:
                self.assertEqual(contract["db_mutations"]["status"], "NOT_APPLICABLE", behavior_id)
                self.assertEqual(contract["events_outbox"]["status"], "NOT_APPLICABLE", behavior_id)
                self.assertTrue(contract["db_mutations"].get("reason"), behavior_id)

    def test_handler_bodies_do_not_commit_the_claimed_product_rows(self):
        for expected in CODEX_FALSE_CRUD_EXAMPLES.values():
            source = _function_source(REPO_ROOT / expected["source"], expected["handler"].split(".")[-1])
            compact = " ".join(source.split())
            for token in expected["forbidden_tokens"]:
                self.assertNotIn(token, compact, f"{expected['handler']} still contains {token}")
            if expected["operation_kind"] == "ACK_ONLY":
                self.assertTrue(
                    "answer_callback_query_via_runtime" in compact
                    or "answer_pre_auth_message_via_runtime" in compact
                    or "_answer_stale_trade_creation_callback" in compact,
                    expected["handler"],
                )
            if expected["operation_kind"] == "EPHEMERAL_ACTIVITY":
                self.assertIn("publish_", compact, expected["handler"])
            if expected["operation_kind"] == "PARSE_NO_COMMIT":
                self.assertIn("ParseOfferResponse", compact, expected["handler"])
                self.assertIn("return", compact, expected["handler"])
            if expected["operation_kind"] == "SHADOW_OBSERVATION":
                self.assertIn("observe_coin_inference_shadow", compact, expected["handler"])
                self.assertNotIn("create_offer", compact, expected["handler"])
            if expected["operation_kind"] == "FSM_PROMPT":
                self.assertIn("set_state", compact, expected["handler"])


if __name__ == "__main__":
    unittest.main()
