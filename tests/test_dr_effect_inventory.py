from __future__ import annotations

from pathlib import Path
import re
import unittest

from core.dr_effect_inventory import build_three_site_effect_inventory


ROOT = Path(__file__).resolve().parents[1]
PROVIDER_SCANS = {
    "telegram": re.compile(
        r"telegram_gateway\.(?:send_message|edit_message_text|edit_message_reply_markup)"
        r"|\b(?:bot|message\.bot|join_request\.bot)\."
        r"(?:send_message|send_document|send_photo|send_video|edit_message_text|"
        r"edit_message_reply_markup|delete_message)"
        r"|\bsend_telegram_notification\(|\bsend_telegram_message\("
        r"|\bsend_offer_to_channel(?:_with_result)?\("
        r"|\bsend_or_update_trade_suggestion_message\("
    ),
    "webpush": re.compile(r"\bsend_web_push_to_user\(|\bwebpush\("),
    "redis_websocket": re.compile(r"\.(?:publish)\("),
}


class DrEffectInventoryTests(unittest.TestCase):
    def test_required_provider_surfaces_have_complete_contracts(self):
        inventory = build_three_site_effect_inventory()
        effects = inventory["effects"]
        required_names = {
            "telegram_private_notification_outbox",
            "telegram_offer_publication_and_edit",
            "telegram_trade_delivery",
            "telegram_admin_broadcast",
            "telegram_market_transition_notice",
            "webpush_market_and_notification",
            "sms_otp_and_invitation",
            "sms_account_recovery",
            "encrypted_blob_object_storage",
            "realtime_redis_and_websocket",
            "email",
            "arvan_route_switch",
        }
        self.assertTrue(required_names.issubset({row["name"] for row in effects}))
        required_fields = {
            "ambiguity_policy",
            "authority",
            "causation",
            "claim",
            "destination_identity",
            "durability",
            "idempotency",
            "name",
            "notes",
            "outcomes",
            "provider",
            "readiness",
            "source_files",
        }
        for effect in effects:
            self.assertEqual(set(effect), required_fields, effect["name"])
            for key in required_fields - {"outcomes", "source_files"}:
                self.assertTrue(str(effect[key]).strip(), (effect["name"], key))
            self.assertTrue(effect["outcomes"], effect["name"])

    def test_every_declared_source_exists(self):
        for effect in build_three_site_effect_inventory()["effects"]:
            for source in effect["source_files"]:
                self.assertTrue((ROOT / source).is_file(), (effect["name"], source))

    def test_every_runtime_provider_callsite_is_classified(self):
        inventory = build_three_site_effect_inventory()["effects"]
        for provider, pattern in PROVIDER_SCANS.items():
            classified = {
                source
                for effect in inventory
                if effect["provider"] == provider
                for source in effect["source_files"]
            }
            observed: set[str] = set()
            for top in ("api", "bot", "core"):
                for path in (ROOT / top).rglob("*.py"):
                    relative = path.relative_to(ROOT).as_posix()
                    if pattern.search(path.read_text(encoding="utf-8")):
                        observed.add(relative)
            self.assertEqual(observed - classified, set(), (provider, sorted(observed - classified)))

    def test_no_runtime_email_provider_was_added_silently(self):
        marker = re.compile(r"\b(?:smtplib|aiosmtplib|sendgrid|mailgun|send_email)\b", re.I)
        observed = []
        for top in ("api", "bot", "core"):
            for path in (ROOT / top).rglob("*.py"):
                if marker.search(path.read_text(encoding="utf-8")):
                    observed.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(observed, [])


if __name__ == "__main__":
    unittest.main()
