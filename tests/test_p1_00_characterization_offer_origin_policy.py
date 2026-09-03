"""HIGH-3: offer_origin is a single composed key and changes home, not admission."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from core.enums import UserRole
from core.offer_source import OfferSourceSurface, offer_home_server_for_source
from core.services.bot_access_policy import evaluate_invitation_bot_access
from core.services.offer_request_ledger_service import OfferRequestLedgerCommand
from models.customer_relation import CustomerTier
from models.invitation import InvitationKind

REPO_ROOT = Path(__file__).resolve().parents[1]
INV_DIR = REPO_ROOT / "docs/refactor/two-server/section-1/inventory"
ORIGIN_PAIRS = {
    ("WEBAPP", "WEBAPP"),
    ("WEBAPP", "TELEGRAM_BOT"),
    ("TELEGRAM_BOT", "WEBAPP"),
    ("TELEGRAM_BOT", "TELEGRAM_BOT"),
    ("INTERNAL", "WEBAPP"),
    ("INTERNAL", "TELEGRAM_BOT"),
}
MUTATING_EFFECTS = {"CONFIRMATION", "QUOTA", "COMMISSION", "PUBLICATION", "NOTIFICATION"}
DENIAL_DECISIONS = {"INELIGIBLE", "INELIGIBLE_FOR_NEW_MARKET_MUTATION", "NOT_APPLICABLE"}


def _load_json(name: str) -> dict:
    return json.loads((INV_DIR / name).read_text(encoding="utf-8"))


def _home(origin: str) -> str:
    if origin == "TELEGRAM_BOT":
        return offer_home_server_for_source(OfferSourceSurface.TELEGRAM_BOT)
    if origin == "WEBAPP":
        return offer_home_server_for_source(OfferSourceSurface.WEBAPP)
    return offer_home_server_for_source(OfferSourceSurface.INTERNAL_SYNC, incoming_home_server="iran")


class P100OfferOriginPolicyCharacterizationTests(unittest.TestCase):
    def test_composed_origin_matrix_has_single_origin_keys(self):
        payload = _load_json("scenario-records.json")
        records = payload["composed_origin_matrix"]
        self.assertEqual(len(records), 180)
        self.assertEqual(payload["coverage"]["composed_origin_matrix"], 180)
        self.assertEqual(len(payload["offer_origin_interaction"]), 180)
        seen = set()
        pairs = set()
        for record in records:
            self.assertIsInstance(record["offer_origin"], str)
            self.assertNotIsInstance(record["offer_origin"], list)
            self.assertIn(record["offer_origin"], {"WEBAPP", "TELEGRAM_BOT", "INTERNAL"})
            key = (
                record["offer_origin"],
                record["request_origin"],
                record["persona_variant_id"],
                record["time_variant_id"],
                record["outcome_variant_id"],
            )
            self.assertNotIn(key, seen, record["variant_id"])
            seen.add(key)
            pairs.add((record["offer_origin"], record["request_origin"]))
            self.assertIn(f"OFFER-ORIGIN-{record['offer_origin']}", record["variant_id"])
            self.assertEqual(record["offer_home"], _home(record["offer_origin"]))
            self.assertEqual(record["publication_home"], record["offer_home"])
            self.assertEqual(
                record["request_source_surface"],
                "telegram_bot" if record["request_origin"] == "TELEGRAM_BOT" else "webapp",
            )
            if (
                record["outcome_variant_id"] == "OUTCOME-SUCCESS"
                and record["eligibility"]["decision"] in DENIAL_DECISIONS
            ):
                self.assertEqual(record["policy_effects"], [])
                self.assertFalse(MUTATING_EFFECTS & set(record["policy_effects"]))
        self.assertEqual(pairs, ORIGIN_PAIRS)
        self.assertEqual(len(seen), 180)

    def test_origin_pairs_apply_home_and_request_surfaces(self):
        payload = _load_json("scenario-records.json")
        for record in payload["composed_origin_matrix"]:
            command = OfferRequestLedgerCommand(
                local_offer_id=1,
                offer_public_id="ofr_origin",
                requested_quantity=1,
                request_home_server="iran" if record["request_origin"] == "WEBAPP" else "foreign",
                requester_user_id=1,
                actor_user_id=1,
                request_source_surface=record["request_source_surface"],
                request_source_server="iran" if record["request_origin"] == "WEBAPP" else "foreign",
                idempotency_key=f"origin-{record['variant_id']}",
            )
            self.assertEqual(command.request_source_surface, record["request_source_surface"])
            if record["offer_origin"] != record["request_origin"]:
                self.assertNotEqual(
                    str(command.request_source_surface).lower(),
                    record["offer_origin"].lower(),
                )
            self.assertEqual(record["publication_home"], _home(record["offer_origin"]))
            if record["offer_origin"] == "TELEGRAM_BOT" and record["request_origin"] == "WEBAPP":
                self.assertEqual(record["offer_home"], "foreign")
                self.assertEqual(command.request_source_surface, "webapp")
                self.assertNotEqual(record["offer_home"], command.request_home_server)
            if record["offer_origin"] == "WEBAPP" and record["request_origin"] == "TELEGRAM_BOT":
                self.assertEqual(record["offer_home"], "iran")
                self.assertEqual(command.request_source_surface, "telegram_bot")

    def test_bot_admission_ignores_offer_origin(self):
        origins = (
            OfferSourceSurface.WEBAPP,
            OfferSourceSurface.TELEGRAM_BOT,
            OfferSourceSurface.INTERNAL_SYNC,
        )
        accountant = [
            evaluate_invitation_bot_access(
                role=UserRole.STANDARD,
                invitation_kind=InvitationKind.ACCOUNTANT,
                customer_tier=None,
            )
            for _ in origins
        ]
        tier1 = [
            evaluate_invitation_bot_access(
                role=UserRole.STANDARD,
                invitation_kind=InvitationKind.CUSTOMER,
                customer_tier=CustomerTier.TIER_1,
            )
            for _ in origins
        ]
        tier2 = [
            evaluate_invitation_bot_access(
                role=UserRole.STANDARD,
                invitation_kind=InvitationKind.CUSTOMER,
                customer_tier=CustomerTier.TIER_2,
            )
            for _ in origins
        ]
        self.assertEqual({item.allowed for item in accountant}, {False})
        self.assertEqual({item.allowed for item in tier1}, {True})
        self.assertEqual({item.allowed for item in tier2}, {False})
        payload = _load_json("scenario-records.json")
        grouped = {}
        for record in payload["composed_origin_matrix"]:
            if record["outcome_variant_id"] != "OUTCOME-SUCCESS":
                continue
            key = (
                record["persona_variant_id"],
                record["request_origin"],
                record["time_variant_id"],
            )
            grouped.setdefault(key, set()).add(record["admission_decision"])
        for decisions in grouped.values():
            self.assertEqual(len(decisions), 1)

    def test_admission_cells_do_not_use_a_three_value_origin_array(self):
        payload = _load_json("scenario-records.json")
        for cell in payload["composed_eligibility"]:
            self.assertNotIn("offer_origin", cell)
            self.assertEqual(cell["offer_origin_rule"]["status"], "INDEPENDENT_OF_ADMISSION")


if __name__ == "__main__":
    unittest.main()
