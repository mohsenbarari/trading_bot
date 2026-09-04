"""HIGH-3: origin is applied to live home/request functions; other cells are blockers or equivalence."""

from __future__ import annotations

import asyncio
import inspect
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from core.enums import UserRole
from core.offer_source import OfferSourceSurface, offer_home_server_for_source
from core.services.bot_access_policy import evaluate_invitation_bot_access
from core.services.offer_expiry_service import OfferExpiryCommand, OfferNotAuthoritativeError, ensure_offer_expiry_authority
from core.services.offer_publication_state_service import (
    publication_dedupe_key,
    publication_owner_server_for_surface,
)
from core.services.offer_request_ledger_service import (
    OfferRequestLedgerCommand,
    create_offer_request_ledger_entry,
    load_offer_request_by_idempotency,
)
from models.offer_publication_state import OfferPublicationSurface
from models.customer_relation import CustomerTier
from models.invitation import InvitationKind
from tests.test_p1_00_characterization_concurrent_restart import (
    _AsyncSessionAdapter,
    _make_engine,
)

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
REQUIRED_INTERACTIONS = {
    "OUTCOME-SUCCESS",
    "OUTCOME-REJECT",
    "PERMISSION-DENIED",
    "RETRY",
    "CONCURRENT",
    "UNLINKED-BOT",
    "EXPIRY-CANCEL-REPUBLISH-TERMINAL",
    "POLICY-SNAPSHOT",
    "NOTIFICATION",
    "FAILURE-BEFORE-COMMIT",
    "FAILURE-AFTER-COMMIT-BEFORE-SIDE-EFFECT",
    "SIDE-EFFECT-RETRY",
}


def _load_json(name: str) -> dict:
    return json.loads((INV_DIR / name).read_text(encoding="utf-8"))


def _surface(origin: str) -> OfferSourceSurface:
    if origin == "TELEGRAM_BOT":
        return OfferSourceSurface.TELEGRAM_BOT
    if origin == "WEBAPP":
        return OfferSourceSurface.WEBAPP
    return OfferSourceSurface.INTERNAL_SYNC


def _home(origin: str) -> str:
    if origin == "INTERNAL":
        return offer_home_server_for_source(OfferSourceSurface.INTERNAL_SYNC, incoming_home_server="iran")
    return offer_home_server_for_source(_surface(origin))


class P100OfferOriginPolicyCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    def test_composed_origin_matrix_has_single_origin_keys(self):
        payload = _load_json("scenario-records.json")
        records = payload["composed_origin_matrix"]
        self.assertEqual(len(records), 180)
        self.assertEqual(payload["coverage"]["composed_origin_matrix"], 180)
        seen = set()
        pairs = set()
        for record in records:
            self.assertIsInstance(record["offer_origin"], str)
            self.assertNotIsInstance(record["offer_origin"], list)
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
            self.assertEqual(record["offer_home"], _home(record["offer_origin"]))
            self.assertEqual(record["publication_home"], _home(record["offer_origin"]))
        self.assertEqual(pairs, ORIGIN_PAIRS)

    def test_live_home_and_ledger_functions_consume_origin(self):
        self.assertEqual(offer_home_server_for_source(OfferSourceSurface.WEBAPP), "iran")
        self.assertEqual(offer_home_server_for_source(OfferSourceSurface.TELEGRAM_BOT), "foreign")
        self.assertEqual(
            offer_home_server_for_source(OfferSourceSurface.INTERNAL_SYNC, incoming_home_server="iran"),
            "iran",
        )
        self.assertNotEqual(
            offer_home_server_for_source(OfferSourceSurface.WEBAPP),
            offer_home_server_for_source(OfferSourceSurface.TELEGRAM_BOT),
        )
        home_params = inspect.signature(offer_home_server_for_source).parameters
        self.assertIn("source_surface", home_params)
        web = OfferRequestLedgerCommand(
            local_offer_id=1,
            offer_public_id="ofr_origin",
            requested_quantity=1,
            request_home_server="iran",
            requester_user_id=1,
            actor_user_id=1,
            request_source_surface="webapp",
            request_source_server="iran",
            idempotency_key="origin-web",
        )
        bot = OfferRequestLedgerCommand(
            local_offer_id=1,
            offer_public_id="ofr_origin",
            requested_quantity=1,
            request_home_server="foreign",
            requester_user_id=1,
            actor_user_id=1,
            request_source_surface="telegram_bot",
            request_source_server="foreign",
            idempotency_key="origin-bot",
        )
        self.assertEqual(web.request_source_surface, "webapp")
        self.assertEqual(bot.request_source_surface, "telegram_bot")
        self.assertNotEqual(web.request_source_surface, bot.request_source_surface)
        self.assertNotEqual(web.request_home_server, bot.request_home_server)

    def test_publication_and_expiry_consume_surface_or_home(self):
        self.assertEqual(
            publication_owner_server_for_surface(OfferPublicationSurface.WEBAPP_MARKET),
            "iran",
        )
        self.assertEqual(
            publication_owner_server_for_surface(OfferPublicationSurface.TELEGRAM_CHANNEL),
            "foreign",
        )
        web_key = publication_dedupe_key("ofr_origin", OfferPublicationSurface.WEBAPP_MARKET)
        bot_key = publication_dedupe_key("ofr_origin", OfferPublicationSurface.TELEGRAM_CHANNEL)
        self.assertNotEqual(web_key, bot_key)
        self.assertEqual(publication_dedupe_key("ofr_origin", OfferPublicationSurface.WEBAPP_MARKET), web_key)
        web_offer = SimpleNamespace(home_server=offer_home_server_for_source(OfferSourceSurface.WEBAPP))
        bot_offer = SimpleNamespace(home_server=offer_home_server_for_source(OfferSourceSurface.TELEGRAM_BOT))
        with patch("core.services.offer_expiry_service.current_server", return_value="iran"):
            ensure_offer_expiry_authority(web_offer)
            with self.assertRaises(OfferNotAuthoritativeError):
                ensure_offer_expiry_authority(bot_offer)
        command = OfferExpiryCommand(reason="closed", source_surface="webapp", source_server="iran")
        self.assertEqual(command.source_surface, "webapp")

    def test_bot_access_api_has_no_origin_parameter(self):
        params = inspect.signature(evaluate_invitation_bot_access).parameters
        self.assertNotIn("origin", params)
        self.assertNotIn("offer_origin", params)
        self.assertNotIn("source_surface", params)
        accountant = evaluate_invitation_bot_access(
            role=UserRole.STANDARD,
            invitation_kind=InvitationKind.ACCOUNTANT,
            customer_tier=None,
        )
        tier1 = evaluate_invitation_bot_access(
            role=UserRole.STANDARD,
            invitation_kind=InvitationKind.CUSTOMER,
            customer_tier=CustomerTier.TIER_1,
        )
        tier2 = evaluate_invitation_bot_access(
            role=UserRole.STANDARD,
            invitation_kind=InvitationKind.CUSTOMER,
            customer_tier=CustomerTier.TIER_2,
        )
        self.assertFalse(accountant.allowed)
        self.assertTrue(tier1.allowed)
        self.assertFalse(tier2.allowed)

    def test_required_origin_interactions_are_executed_or_owned_by_p1_00b(self):
        payload = _load_json("scenario-records.json")
        rows = payload["origin_required_interactions"]
        self.assertEqual(len(rows), len(ORIGIN_PAIRS) * len(REQUIRED_INTERACTIONS))
        seen = set()
        registry = {item["blocker_id"]: item for item in payload["blocker_registry"]["items"]}
        executed = {
            "OUTCOME-SUCCESS",
            "OUTCOME-REJECT",
            "PERMISSION-DENIED",
            "RETRY",
            "CONCURRENT",
        }
        for row in rows:
            key = (row["offer_origin"], row["request_origin"], row["interaction"])
            self.assertNotIn(key, seen, key)
            seen.add(key)
            self.assertIn(row["interaction"], REQUIRED_INTERACTIONS)
            self.assertIn(row["disposition"], {"EQUIVALENT_TO_REPRESENTATIVE", "TRANSFERRED_TO_P1_00B"})
            if row["disposition"] == "EQUIVALENT_TO_REPRESENTATIVE":
                self.assertIn(row["interaction"], executed)
                self.assertTrue(row["representative_id"])
                self.assertTrue(row["live_function"])
                self.assertTrue(row["equivalence_reason"])
                self.assertIn("policy effect", row["equivalence_reason"].lower())
                self.assertNotIn("self-compared", json.dumps(row, ensure_ascii=False))
            else:
                blocker_id = row["named_blocker"]["blocker_id"]
                self.assertIn(blocker_id, registry)
                self.assertEqual(registry[blocker_id]["severity"], "HIGH")
                self.assertEqual(registry[blocker_id]["disposition"], "TRANSFERRED_TO_P1_00B")
                self.assertEqual(registry[blocker_id]["resolution_stage"], "P1-00b")
                self.assertTrue(registry[blocker_id]["observed_fact"])
                self.assertNotEqual(row.get("applicability"), "ELIGIBLE")
        self.assertTrue(any(row["interaction"] == "OUTCOME-SUCCESS" and row["disposition"] == "EQUIVALENT_TO_REPRESENTATIVE" for row in rows))
        self.assertTrue(any(row["interaction"] == "POLICY-SNAPSHOT" and row["disposition"] == "TRANSFERRED_TO_P1_00B" for row in rows))

    async def test_combined_live_policy_effects_for_each_origin_pair(self):
        source = Path(__file__).read_text(encoding="utf-8")
        self.assertIn("asyncio.gather", source)
        self.assertIn("asyncio.Barrier", source)
        self.assertIn("request_home_server=offer_home", source)

        for offer_origin, request_origin in ORIGIN_PAIRS:
            offer_home = _home(offer_origin)
            request_surface = "telegram_bot" if request_origin == "TELEGRAM_BOT" else "webapp"
            request_home = "foreign" if request_origin == "TELEGRAM_BOT" else "iran"
            accountant = evaluate_invitation_bot_access(
                role=UserRole.STANDARD,
                invitation_kind=InvitationKind.ACCOUNTANT,
                customer_tier=None,
            )
            tier2 = evaluate_invitation_bot_access(
                role=UserRole.STANDARD,
                invitation_kind=InvitationKind.CUSTOMER,
                customer_tier=CustomerTier.TIER_2,
            )
            tier1 = evaluate_invitation_bot_access(
                role=UserRole.STANDARD,
                invitation_kind=InvitationKind.CUSTOMER,
                customer_tier=CustomerTier.TIER_1,
            )
            self.assertFalse(accountant.allowed, (offer_origin, request_origin))
            self.assertFalse(tier2.allowed, (offer_origin, request_origin))
            self.assertTrue(tier1.allowed, (offer_origin, request_origin))

            command = OfferRequestLedgerCommand(
                local_offer_id=None,
                offer_public_id=f"ofr_{offer_origin}",
                requested_quantity=1,
                request_home_server=offer_home,
                requester_user_id=None,
                actor_user_id=None,
                request_source_surface=request_surface,
                request_source_server=request_home,
                idempotency_key=f"origin-{offer_origin}-{request_origin}",
            )
            with tempfile.TemporaryDirectory() as tmp:
                success_path = str(Path(tmp) / f"{offer_origin}-{request_origin}-success.sqlite")
                engine = _make_engine(success_path)
                Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
                db = _AsyncSessionAdapter(Session())
                created = await create_offer_request_ledger_entry(db, command)
                await db.commit()
                replay = await create_offer_request_ledger_entry(db, command)
                surface = created.ledger.request_source_surface
                self.assertFalse(created.duplicate_replay, (offer_origin, request_origin))
                self.assertTrue(replay.duplicate_replay, (offer_origin, request_origin))
                self.assertEqual(created.ledger.request_home_server, offer_home)
                self.assertEqual(getattr(surface, "value", surface), request_surface)
                self.assertEqual(created.ledger.request_source_server, request_home)
                self.assertEqual(created.ledger.offer_public_id, f"ofr_{offer_origin}")
                with sqlite3.connect(success_path) as conn:
                    rows = conn.execute(
                        "SELECT request_home_server, request_source_surface, request_source_server FROM offer_requests"
                    ).fetchall()
                self.assertEqual(len(rows), 1, (offer_origin, request_origin))
                self.assertEqual(rows[0][0], offer_home)
                self.assertEqual(rows[0][1], request_surface)
                self.assertEqual(rows[0][2], request_home)
                db.close()
                engine.dispose()

                concurrent_path = str(Path(tmp) / f"{offer_origin}-{request_origin}-concurrent.sqlite")
                concurrent_engine = _make_engine(concurrent_path)
                ConcurrentSession = sessionmaker(
                    bind=concurrent_engine, future=True, expire_on_commit=False
                )
                barrier = asyncio.Barrier(2)

                async def worker():
                    worker_db = _AsyncSessionAdapter(ConcurrentSession())
                    await barrier.wait()
                    try:
                        result = await create_offer_request_ledger_entry(worker_db, command)
                        await worker_db.commit()
                        return "winner" if not result.duplicate_replay else "replay", result
                    except IntegrityError:
                        await worker_db.rollback()
                        existing = await load_offer_request_by_idempotency(
                            worker_db,
                            request_home_server=offer_home,
                            idempotency_key=command.idempotency_key,
                        )
                        return "loser", existing
                    finally:
                        worker_db.close()

                first, second = await asyncio.gather(worker(), worker())
                outcomes = {first[0], second[0]}
                with sqlite3.connect(concurrent_path) as conn:
                    concurrent_rows = conn.execute(
                        "SELECT request_home_server, request_source_surface, request_source_server, idempotency_key FROM offer_requests"
                    ).fetchall()
                self.assertEqual(len(concurrent_rows), 1, (offer_origin, request_origin, outcomes))
                self.assertTrue(outcomes & {"winner"}, (offer_origin, request_origin, outcomes))
                self.assertTrue(outcomes <= {"winner", "loser", "replay"}, outcomes)
                self.assertTrue({"loser", "replay"} & outcomes or outcomes == {"winner", "replay"} or outcomes == {"winner", "loser"}, outcomes)
                if outcomes == {"winner"}:
                    self.fail(f"CONCURRENT produced two winners without a loser/replay: {(offer_origin, request_origin)}")
                self.assertEqual(concurrent_rows[0][0], offer_home)
                self.assertEqual(concurrent_rows[0][1], request_surface)
                self.assertEqual(concurrent_rows[0][2], request_home)
                self.assertEqual(concurrent_rows[0][3], command.idempotency_key)
                concurrent_engine.dispose()

    def test_admission_cells_do_not_use_a_three_value_origin_array(self):
        payload = _load_json("scenario-records.json")
        for cell in payload["composed_eligibility"]:
            self.assertNotIn("offer_origin", cell)
            self.assertEqual(cell["offer_origin_rule"]["status"], "INDEPENDENT_OF_ADMISSION")


if __name__ == "__main__":
    unittest.main()
