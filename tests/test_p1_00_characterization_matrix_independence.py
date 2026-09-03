"""HIGH-4: composed eligibility includes outcome/link and proves offer_origin independence."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from core.offer_source import OfferSourceSurface, offer_home_server_for_source

REPO_ROOT = Path(__file__).resolve().parents[1]
INV_DIR = REPO_ROOT / "docs/refactor/two-server/section-1/inventory"

ADMISSION_DECISIONS = {
    "ELIGIBLE",
    "ELIGIBLE_FOR_REQUEST_ONLY",
    "ELIGIBLE_FOR_READ_OR_TERMINAL_REPLAY",
}
DENIAL_DECISIONS = {"INELIGIBLE", "INELIGIBLE_FOR_NEW_MARKET_MUTATION"}


def _load_json(name: str) -> dict:
    return json.loads((INV_DIR / name).read_text(encoding="utf-8"))


class P100MatrixIndependenceCharacterizationTests(unittest.TestCase):
    def test_composed_decisions_include_origin_link_and_outcome(self):
        payload = _load_json("scenario-records.json")
        cells = payload["composed_eligibility"]
        self.assertEqual(len(cells), 180)
        self.assertEqual(payload["coverage"]["composed_eligibility_cells"], 180)
        self.assertEqual(payload["matrix"]["expanded_variant_count"], 180)
        self.assertNotEqual(payload["matrix"]["expanded_variant_count"], 360)
        personas = {item["variant_id"] for item in payload["matrix"]["persona_variants"]}
        times = {item["variant_id"] for item in payload["matrix"]["time_variants"]}
        outcomes = {item["variant_id"] for item in payload["matrix"]["outcome_variants"]}
        seen = set()
        for cell in cells:
            self.assertIn(cell["persona_variant_id"], personas)
            self.assertIn(cell["request_origin"], {"WEBAPP", "TELEGRAM_BOT"})
            self.assertIn(cell["time_variant_id"], times)
            self.assertIn(cell["outcome_variant_id"], outcomes)
            self.assertIn(cell["telegram_link_variant_id"], {
                "TELEGRAM-LINK-NOT_APPLICABLE",
                "TELEGRAM-LINK-PRESENT",
                "TELEGRAM-LINK-ABSENT",
            })
            self.assertEqual(cell["offer_origin_rule"]["status"], "INDEPENDENT_OF_ADMISSION")
            self.assertEqual(cell["offer_origin"], ["WEBAPP", "TELEGRAM_BOT", "INTERNAL"])
            self.assertTrue(cell["expected_outcome"])
            if cell["outcome_variant_id"] == "OUTCOME-SUCCESS" and cell["eligibility"]["decision"] in DENIAL_DECISIONS | {"NOT_APPLICABLE"}:
                self.assertEqual(cell["policy_effects"], [])
                self.assertFalse(
                    any(
                        effect in {"CONFIRMATION", "QUOTA", "COMMISSION", "PUBLICATION", "NOTIFICATION"}
                        for effect in cell["policy_effects"]
                    )
                )
            else:
                self.assertTrue(cell["policy_effects"])
            if cell["request_origin"] == "WEBAPP":
                self.assertEqual(cell["telegram_link_variant_id"], "TELEGRAM-LINK-NOT_APPLICABLE")
            else:
                self.assertIn(cell["telegram_link_variant_id"], {"TELEGRAM-LINK-PRESENT", "TELEGRAM-LINK-ABSENT"})
            seen.add((
                cell["persona_variant_id"],
                cell["request_origin"],
                cell["time_variant_id"],
                cell["telegram_link_variant_id"],
                cell["outcome_variant_id"],
            ))
        self.assertEqual(len(seen), 180)
        independence = payload["axis_independence"]["offer_origin"]
        self.assertEqual(independence["status"], "INDEPENDENT_OF_ADMISSION")
        self.assertEqual(set(independence["surfaces"]), {"WEBAPP", "TELEGRAM_BOT", "INTERNAL"})

    def test_offer_origin_holds_admission_and_effects_constant(self):
        payload = _load_json("scenario-records.json")
        for cell in payload["composed_eligibility"]:
            decisions = []
            effects = []
            for origin in ("WEBAPP", "TELEGRAM_BOT", "INTERNAL"):
                self.assertIn(origin, cell["offer_origin"])
                decisions.append(cell["eligibility"]["decision"])
                effects.append(list(cell["policy_effects"]))
            self.assertEqual(len(set(decisions)), 1, cell["variant_id"])
            self.assertEqual(effects[0], effects[1], cell["variant_id"])
            self.assertEqual(effects[1], effects[2], cell["variant_id"])
            self.assertEqual(cell["admission_decision"], cell["admission_decision"])
        self.assertEqual(offer_home_server_for_source(OfferSourceSurface.WEBAPP), "iran")
        self.assertEqual(offer_home_server_for_source(OfferSourceSurface.TELEGRAM_BOT), "foreign")
        self.assertEqual(
            offer_home_server_for_source(OfferSourceSurface.INTERNAL_SYNC, incoming_home_server="iran"),
            "iran",
        )

    def test_offer_origin_does_not_change_admission_decision(self):
        payload = _load_json("scenario-records.json")
        admissions = {}
        for cell in payload["composed_eligibility"]:
            if cell["outcome_variant_id"] != "OUTCOME-SUCCESS":
                continue
            if cell["request_origin"] == "TELEGRAM_BOT" and cell["telegram_link_variant_id"] != "TELEGRAM-LINK-PRESENT":
                continue
            key = (cell["persona_variant_id"], cell["request_origin"], cell["time_variant_id"])
            admissions[key] = cell["admission_decision"]
        self.assertEqual(len(admissions), 30)
        for origin in payload["matrix"]["origin_variants"]:
            request = origin["request_origin"]
            for (persona, surface, time), decision in admissions.items():
                if surface != request:
                    continue
                self.assertIn(decision, ADMISSION_DECISIONS | DENIAL_DECISIONS)
        self.assertEqual(offer_home_server_for_source(OfferSourceSurface.WEBAPP), "iran")
        self.assertEqual(offer_home_server_for_source(OfferSourceSurface.TELEGRAM_BOT), "foreign")
        self.assertEqual(
            offer_home_server_for_source(OfferSourceSurface.INTERNAL_SYNC, incoming_home_server="iran"),
            "iran",
        )

    def test_absent_telegram_link_denies_bot_without_changing_web(self):
        payload = _load_json("scenario-records.json")
        for cell in payload["composed_eligibility"]:
            if cell["telegram_link_variant_id"] != "TELEGRAM-LINK-ABSENT":
                continue
            self.assertEqual(cell["request_origin"], "TELEGRAM_BOT")
            if cell["outcome_variant_id"] == "OUTCOME-SUCCESS":
                self.assertIn(cell["eligibility"]["decision"], DENIAL_DECISIONS | {"NOT_APPLICABLE"})
            if cell["outcome_variant_id"] == "OUTCOME-REJECT":
                self.assertEqual(cell["eligibility"]["decision"], "ELIGIBLE")
        web_success = [
            cell
            for cell in payload["composed_eligibility"]
            if cell["request_origin"] == "WEBAPP" and cell["outcome_variant_id"] == "OUTCOME-SUCCESS"
        ]
        self.assertEqual(len(web_success), 15)
        for cell in web_success:
            self.assertEqual(cell["telegram_link_variant_id"], "TELEGRAM-LINK-NOT_APPLICABLE")


if __name__ == "__main__":
    unittest.main()
