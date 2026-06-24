#!/usr/bin/env python3
"""Build the production full-matrix scenario manifest.

The manifest is intentionally side-effect free. It expands the code-defined
market, delivery, actor, surface, outage, offer-type, and offer-shape axes into
runner-consumable scenario records and keeps policy-unsupported cases visible as
negative assertions.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import report_trade_notification_delivery_matrix as delivery_matrix
from scripts import run_bot_webapp_comprehensive_load_matrix as market_matrix
from scripts import run_trade_delivery_targeted_join_matrix as targeted_join_matrix
from scripts.plan_production_full_matrix import default_prefix, validate_prefix


SCHEMA_VERSION = "production_full_matrix_manifest_v1"
SCENARIO_CATALOG_PATH = "docs/PRODUCTION_FULL_MATRIX_SCENARIO_CATALOG.md"
MANIFEST_DOC_PATH = "docs/PRODUCTION_FULL_MATRIX_MANIFEST.md"

SURFACE_PAIR_QUADRANTS = {
    "webapp_offer__webapp_request": "iran_to_iran",
    "webapp_offer__telegram_request": "iran_to_foreign",
    "telegram_offer__webapp_request": "foreign_to_iran",
    "telegram_offer__telegram_request": "foreign_to_foreign",
}

SERVER_BY_SURFACE = {
    "webapp": "iran",
    "telegram": "foreign",
}

BASE_TRADE_ASSERTION_REFS = [
    "authoritative_single_mutation",
    "offer_request_terminal_ledger",
    "idempotency_no_duplicate_trade",
    "offer_quantity_integrity",
    "cross_server_sync_terminal_state",
    "webapp_notification_required_recipients",
    "telegram_delivery_eligible_recipients",
    "publication_terminal_state",
    "cleanup_prefix_scope",
]

NEGATIVE_POLICY_ASSERTION_REFS = [
    "policy_rejection_is_explicit",
    "no_partial_mutation_on_reject",
    "offer_request_terminal_ledger",
    "cleanup_prefix_scope",
]

STRESS_OVERLAY_FAMILIES = [
    {
        "family": "hot_wholesale_concurrent",
        "selector": {"shapes": ["wholesale_full"]},
        "min_parallel_requests": 36,
        "assertion_refs": [
            "authoritative_single_mutation",
            "offer_quantity_integrity",
            "losing_requests_terminal_rejected",
            "idempotency_no_duplicate_trade",
        ],
    },
    {
        "family": "hot_retail_same_lot_concurrent",
        "selector": {"shapes": ["retail_two_lot", "retail_three_lot"]},
        "min_parallel_requests": 36,
        "assertion_refs": [
            "retail_lot_integrity",
            "losing_requests_terminal_rejected",
            "lot_unavailable_rejection_visible",
            "idempotency_no_duplicate_trade",
        ],
    },
    {
        "family": "hot_retail_mixed_lot_concurrent",
        "selector": {"shapes": ["retail_two_lot", "retail_three_lot"]},
        "min_parallel_requests": 36,
        "assertion_refs": [
            "retail_lot_integrity",
            "offer_quantity_integrity",
            "webapp_notification_required_recipients",
            "telegram_delivery_eligible_recipients",
        ],
    },
    {
        "family": "duplicate_idempotency_replay",
        "selector": {"shapes": ["wholesale_full", "retail_two_lot", "retail_three_lot"]},
        "min_parallel_requests": 2,
        "assertion_refs": ["idempotency_no_duplicate_trade", "offer_request_terminal_ledger"],
    },
    {
        "family": "manual_expire_trade_race",
        "selector": {"shapes": ["wholesale_full", "retail_two_lot", "retail_three_lot"]},
        "min_parallel_requests": 8,
        "assertion_refs": [
            "authoritative_single_mutation",
            "terminal_offer_not_reactivated",
            "offer_request_terminal_ledger",
            "publication_terminal_state",
        ],
    },
    {
        "family": "time_expire_trade_race",
        "selector": {"shapes": ["wholesale_full", "retail_two_lot", "retail_three_lot"]},
        "min_parallel_requests": 8,
        "assertion_refs": [
            "authoritative_single_mutation",
            "terminal_offer_not_reactivated",
            "offer_request_terminal_ledger",
            "publication_terminal_state",
        ],
    },
    {
        "family": "read_during_write",
        "selector": {"shapes": ["wholesale_full", "retail_two_lot", "retail_three_lot"]},
        "min_parallel_requests": 12,
        "assertion_refs": [
            "read_views_consistent",
            "customer_counterparty_privacy",
            "publication_terminal_state",
        ],
    },
]

NEGATIVE_BUSINESS_CASES = [
    "own_offer_request",
    "invalid_request_amount",
    "retail_lot_unavailable",
    "already_completed_offer",
    "manually_expired_offer",
    "time_expired_offer",
    "market_closed",
    "inactive_offer_owner",
    "inactive_requester",
    "watch_role_market_action",
    "accountant_market_action",
    "tier2_offer_creation",
    "tier2_telegram_request",
    "trading_restricted_user",
    "daily_trade_limit_exceeded",
    "daily_request_limit_exceeded",
    "active_commodity_limit_exceeded",
    "remote_authority_unavailable",
    "bad_internal_signature",
    "wrong_authoritative_server",
    "stale_telegram_button",
    "missing_public_offer_id",
    "cleanup_scope_violation",
]

READ_PATHS = [
    "GET /api/offers",
    "GET /api/offers/public/{offer_public_id}",
    "GET /api/offers/public/{offer_public_id}/detail",
    "GET /api/offers/market-history",
    "GET /api/offers/expired",
    "GET /api/offers/my",
    "GET /api/trades/my",
    "GET /api/trades/with-user/{user_id}",
    "GET /api/trading-settings/market-state",
]

SYNC_TABLES = [
    "users",
    "offers",
    "offer_requests",
    "trades",
    "notifications",
    "trade_delivery_receipts",
    "offer_publication_states",
    "customer_relations",
    "accountant_relations",
    "user_blocks",
]

NOTIFICATION_RECIPIENT_GROUPS = [
    "offer_side_principal_user",
    "responder_side_principal_user",
    "customer_owner",
    "active_accountants",
    "telegram_linked_eligible_user",
    "webapp_only_user",
    "broken_telegram_id_user",
    "opposite_server_sync_delayed_user",
]

ASSERTIONS = {
    "authoritative_single_mutation": "Only the offer home server mutates offer quantity, lots, status, and trades.",
    "offer_request_terminal_ledger": "Every request attempt ends in a synced terminal offer_request ledger state.",
    "idempotency_no_duplicate_trade": "Duplicate request or retry cannot create a second completed trade.",
    "offer_quantity_integrity": "Completed quantity never exceeds original quantity and remaining_quantity is correct.",
    "retail_lot_integrity": "Retail lot winners cannot exceed available lots and consumed lots are removed once.",
    "lot_unavailable_rejection_visible": "Unavailable lot requests return an explicit rejected_lot_unavailable result.",
    "losing_requests_terminal_rejected": "Concurrent losing requests receive explicit terminal rejection statuses.",
    "cross_server_sync_terminal_state": "Terminal offer, trade, request, notification, and receipt rows sync on both servers.",
    "webapp_notification_required_recipients": "All required WebApp notification recipients receive receipt-backed notification records.",
    "telegram_delivery_eligible_recipients": "Only eligible linked Telegram recipients receive Telegram delivery attempts on foreign.",
    "publication_terminal_state": "Telegram channel and WebApp publication state match completed, expired, or cancelled offers.",
    "terminal_offer_not_reactivated": "Stale sync or recovery must not reactivate a terminal offer.",
    "read_views_consistent": "Read surfaces remain internally consistent while writes are running.",
    "customer_counterparty_privacy": "Customer-visible messages and views show the owner path, not the real non-owner counterparty.",
    "policy_rejection_is_explicit": "Unsupported business-policy paths are rejected explicitly and recorded as negative evidence.",
    "no_partial_mutation_on_reject": "Rejected paths do not create partial trades, quantity changes, or stale publication changes.",
    "cleanup_prefix_scope": "Cleanup dry-run and hard-delete affect only the exact prefixed synthetic cohort.",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def shape_payload(shape: market_matrix.OfferShape) -> dict[str, Any]:
    return {
        "name": shape.name,
        "quantity": shape.quantity,
        "request_amount": shape.request_amount,
        "expected_winner_count": shape.expected_winner_count,
        "is_wholesale": shape.is_wholesale,
        "lot_sizes": list(shape.lot_sizes),
    }


def surface_pair_payload(surface_pair: delivery_matrix.SurfacePair) -> dict[str, Any]:
    return {
        "name": surface_pair.name,
        "offer_surface": surface_pair.offer_surface,
        "request_surface": surface_pair.request_surface,
        "offer_home_server": surface_pair.offer_home_server,
        "request_source_server": SERVER_BY_SURFACE[surface_pair.request_surface],
        "quadrant": SURFACE_PAIR_QUADRANTS[surface_pair.name],
    }


def actor_pair_payload(actor_pair: delivery_matrix.ActorPair) -> dict[str, Any]:
    return asdict(actor_pair)


def market_behavior_scenario_payloads() -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for scenario in market_matrix.build_comprehensive_scenarios():
        payloads.append(
            {
                "manifest_id": f"MB-{scenario.scenario_id}",
                "kind": "market_behavior",
                "source_catalog": "run_bot_webapp_comprehensive_load_matrix",
                "source_scenario_id": scenario.scenario_id,
                "family": scenario.family,
                "offer_origin": scenario.offer_origin,
                "request_surface": scenario.request_surface,
                "expire_surface": scenario.expire_surface,
                "offer_type": scenario.offer_type,
                "shape": scenario.shape,
                "terminal_state": scenario.terminal_state,
                "min_attempts": market_matrix.DEFAULT_MIN_ATTEMPTS_PER_SCENARIO,
                "assertion_refs": ["cleanup_prefix_scope"],
            }
        )
    return payloads


def delivery_contract_scenario_payloads() -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    actor_pairs = {item.pair_id: item for item in delivery_matrix.build_actor_pairs()}
    surface_pairs = {item.name: item for item in delivery_matrix.build_surface_pairs()}
    for scenario in delivery_matrix.build_delivery_scenarios():
        actor_pair = actor_pairs[scenario.actor_pair_id]
        surface_pair = surface_pairs[scenario.surface_pair]
        payloads.append(
            {
                "manifest_id": f"DC-{scenario.scenario_id}",
                "kind": "delivery_contract",
                "source_catalog": "report_trade_notification_delivery_matrix",
                "source_scenario_id": scenario.scenario_id,
                "actor_pair_id": scenario.actor_pair_id,
                "source_kind": actor_pair.source_kind,
                "responder_kind": actor_pair.responder_kind,
                "group_relation": actor_pair.group_relation,
                "surface_pair": scenario.surface_pair,
                "quadrant": SURFACE_PAIR_QUADRANTS[scenario.surface_pair],
                "offer_surface": surface_pair.offer_surface,
                "request_surface": surface_pair.request_surface,
                "offer_home_server": scenario.offer_home_server,
                "request_source_server": SERVER_BY_SURFACE[surface_pair.request_surface],
                "outage_id": scenario.outage_id,
                "expected_remote_delivery_policy": scenario.expected_remote_delivery_policy,
                "assertion_refs": [
                    "webapp_notification_required_recipients",
                    "telegram_delivery_eligible_recipients",
                    "customer_counterparty_privacy",
                    "cleanup_prefix_scope",
                ],
            }
        )
    return payloads


def targeted_join_scenario_payloads() -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for scenario in targeted_join_matrix.build_targeted_join_scenarios():
        payloads.append(
            {
                "manifest_id": f"TJ-{scenario.scenario_id}",
                "kind": "targeted_trade_delivery_join",
                "source_catalog": "run_trade_delivery_targeted_join_matrix",
                "source_scenario_id": scenario.scenario_id,
                "actor_pair_id": scenario.actor_pair_id,
                "source_kind": scenario.source_kind,
                "responder_kind": scenario.responder_kind,
                "group_relation": scenario.group_relation,
                "offer_surface": scenario.offer_surface,
                "request_surface": scenario.request_surface,
                "surface_pair": scenario.surface_pair,
                "quadrant": SURFACE_PAIR_QUADRANTS[scenario.surface_pair],
                "outage_id": scenario.outage_id,
                "offer_home_server": scenario.offer_home_server,
                "request_source_server": scenario.request_source_server,
                "expected_remote_delivery_policy": scenario.expected_remote_delivery_policy,
                "policy_supported": scenario.policy_supported,
                "unsupported_reasons": list(scenario.unsupported_reasons),
                "assertion_refs": BASE_TRADE_ASSERTION_REFS
                if scenario.policy_supported
                else NEGATIVE_POLICY_ASSERTION_REFS,
            }
        )
    return payloads


def base_trade_shape_scenario_payloads() -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    counter = 1
    for actor_pair in delivery_matrix.build_actor_pairs():
        for surface_pair in delivery_matrix.build_surface_pairs():
            for outage in delivery_matrix.build_outage_classes():
                reasons = targeted_join_matrix.unsupported_reasons_for(
                    actor_pair=actor_pair,
                    offer_surface=surface_pair.offer_surface,
                    request_surface=surface_pair.request_surface,
                )
                for offer_type in market_matrix.OFFER_TYPES:
                    for shape in market_matrix.SHAPES.values():
                        policy_supported = not reasons
                        payloads.append(
                            {
                                "manifest_id": f"PBTS-{counter:04d}",
                                "kind": "production_base_trade_shape",
                                "actor_pair_id": actor_pair.pair_id,
                                "source_kind": actor_pair.source_kind,
                                "responder_kind": actor_pair.responder_kind,
                                "group_relation": actor_pair.group_relation,
                                "offer_surface": surface_pair.offer_surface,
                                "request_surface": surface_pair.request_surface,
                                "surface_pair": surface_pair.name,
                                "quadrant": SURFACE_PAIR_QUADRANTS[surface_pair.name],
                                "offer_home_server": surface_pair.offer_home_server,
                                "request_source_server": SERVER_BY_SURFACE[surface_pair.request_surface],
                                "outage_id": outage.outage_id,
                                "expected_remote_delivery_policy": outage.remote_delivery_policy,
                                "offer_type": offer_type,
                                "shape": shape.name,
                                "policy_supported": policy_supported,
                                "expected_outcome": "trade_completed"
                                if policy_supported
                                else "policy_rejected",
                                "unsupported_reasons": list(reasons),
                                "assertion_refs": BASE_TRADE_ASSERTION_REFS
                                if policy_supported
                                else NEGATIVE_POLICY_ASSERTION_REFS,
                            }
                        )
                        counter += 1
    return payloads


def stress_overlay_payloads(base_scenarios: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    counter = 1
    supported = [scenario for scenario in base_scenarios if scenario["policy_supported"]]
    for overlay in STRESS_OVERLAY_FAMILIES:
        allowed_shapes = set(overlay["selector"]["shapes"])
        for base in supported:
            if base["shape"] not in allowed_shapes:
                continue
            payloads.append(
                {
                    "manifest_id": f"PO-{counter:04d}",
                    "kind": "production_stress_overlay",
                    "family": overlay["family"],
                    "base_manifest_id": base["manifest_id"],
                    "actor_pair_id": base["actor_pair_id"],
                    "surface_pair": base["surface_pair"],
                    "quadrant": base["quadrant"],
                    "outage_id": base["outage_id"],
                    "offer_type": base["offer_type"],
                    "shape": base["shape"],
                    "min_parallel_requests": overlay["min_parallel_requests"],
                    "telegram_ratio": 0.6,
                    "target_rps_floor": 600,
                    "assertion_refs": list(overlay["assertion_refs"]),
                }
            )
            counter += 1
    return payloads


def negative_business_guard_payloads() -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for index, case_id in enumerate(NEGATIVE_BUSINESS_CASES, start=1):
        payloads.append(
            {
                "manifest_id": f"NBG-{index:03d}",
                "kind": "negative_business_guard",
                "case_id": case_id,
                "required": True,
                "assertion_refs": NEGATIVE_POLICY_ASSERTION_REFS,
            }
        )
    return payloads


def build_axes() -> dict[str, Any]:
    return {
        "surface_pairs": [surface_pair_payload(item) for item in delivery_matrix.build_surface_pairs()],
        "outage_classes": [asdict(item) for item in delivery_matrix.build_outage_classes()],
        "actor_pairs": [actor_pair_payload(item) for item in delivery_matrix.build_actor_pairs()],
        "offer_types": list(market_matrix.OFFER_TYPES),
        "offer_shapes": [shape_payload(item) for item in market_matrix.SHAPES.values()],
        "market_action_families": sorted(
            {scenario.family for scenario in market_matrix.build_comprehensive_scenarios()}
        ),
        "negative_business_cases": list(NEGATIVE_BUSINESS_CASES),
        "notification_recipient_groups": list(NOTIFICATION_RECIPIENT_GROUPS),
        "read_paths": list(READ_PATHS),
        "sync_tables": list(SYNC_TABLES),
    }


def summarize_sections(sections: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    base = sections["production_base_trade_shape"]
    targeted = sections["targeted_trade_delivery_join"]
    return {
        "market_behavior_scenarios": len(sections["market_behavior"]),
        "delivery_contract_scenarios": len(sections["delivery_contract"]),
        "targeted_join_scenarios": len(targeted),
        "targeted_join_policy_supported": sum(1 for item in targeted if item["policy_supported"]),
        "targeted_join_policy_unsupported": sum(1 for item in targeted if not item["policy_supported"]),
        "base_trade_shape_scenarios": len(base),
        "base_trade_shape_policy_supported": sum(1 for item in base if item["policy_supported"]),
        "base_trade_shape_policy_unsupported": sum(1 for item in base if not item["policy_supported"]),
        "stress_overlay_scenarios": len(sections["production_stress_overlay"]),
        "negative_business_guard_scenarios": len(sections["negative_business_guard"]),
        "total_manifest_scenarios": sum(len(items) for items in sections.values()),
    }


def build_manifest(*, prefix: str | None = None) -> dict[str, Any]:
    normalized_prefix = validate_prefix(prefix or default_prefix())
    market_behavior = market_behavior_scenario_payloads()
    delivery_contract = delivery_contract_scenario_payloads()
    targeted_join = targeted_join_scenario_payloads()
    base_trade_shape = base_trade_shape_scenario_payloads()
    stress_overlay = stress_overlay_payloads(base_trade_shape)
    negative_business_guard = negative_business_guard_payloads()
    sections = {
        "market_behavior": market_behavior,
        "delivery_contract": delivery_contract,
        "targeted_trade_delivery_join": targeted_join,
        "production_base_trade_shape": base_trade_shape,
        "production_stress_overlay": stress_overlay,
        "negative_business_guard": negative_business_guard,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "prefix": normalized_prefix,
        "environment": "production",
        "mutates_production": False,
        "catalog_docs": {
            "scenario_catalog": SCENARIO_CATALOG_PATH,
            "manifest_contract": MANIFEST_DOC_PATH,
        },
        "runner_contract": {
            "requires_isolation": True,
            "requires_exact_prefix_cleanup": True,
            "requires_two_server_execution": True,
            "high_volume_telegram_transport": "aiogram_dispatcher_or_fake_bot_api",
            "manual_real_telegram_slice_required": True,
            "target_users": 1000,
            "target_rps_floor": 600,
            "telegram_ratio": 0.6,
            "webapp_ratio": 0.4,
        },
        "axes": build_axes(),
        "assertions": ASSERTIONS,
        "summary": summarize_sections(sections),
        "sections": sections,
        "production_gate": {
            "status": "manifest_only",
            "reason": "This manifest is side-effect-free and does not execute production writes.",
        },
    }


def validate_manifest(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    summary = manifest["summary"]
    expected = {
        "market_behavior_scenarios": 228,
        "delivery_contract_scenarios": 204,
        "targeted_join_scenarios": 204,
        "targeted_join_policy_supported": 108,
        "targeted_join_policy_unsupported": 96,
        "base_trade_shape_scenarios": 1224,
        "base_trade_shape_policy_supported": 648,
        "base_trade_shape_policy_unsupported": 576,
        "stress_overlay_scenarios": 3672,
        "negative_business_guard_scenarios": len(NEGATIVE_BUSINESS_CASES),
    }
    for key, value in expected.items():
        if summary.get(key) != value:
            errors.append(f"{key}: expected {value}, got {summary.get(key)}")

    quadrants = {item["quadrant"] for item in manifest["axes"]["surface_pairs"]}
    if quadrants != set(SURFACE_PAIR_QUADRANTS.values()):
        errors.append(f"surface quadrants mismatch: {sorted(quadrants)}")

    base_scenarios = manifest["sections"]["production_base_trade_shape"]
    if {item["offer_type"] for item in base_scenarios} != set(market_matrix.OFFER_TYPES):
        errors.append("base trade scenarios do not cover all offer types")
    if {item["shape"] for item in base_scenarios} != set(market_matrix.SHAPES):
        errors.append("base trade scenarios do not cover all offer shapes")
    if {item["outage_id"] for item in base_scenarios} != {
        item.outage_id for item in delivery_matrix.build_outage_classes()
    }:
        errors.append("base trade scenarios do not cover all outage classes")

    unsupported = [item for item in base_scenarios if not item["policy_supported"]]
    if not any("tier2_cannot_create_offer" in item["unsupported_reasons"] for item in unsupported):
        errors.append("tier2 offer-creation negative cases are missing")
    if not any("tier2_cannot_use_telegram_request" in item["unsupported_reasons"] for item in unsupported):
        errors.append("tier2 Telegram-request negative cases are missing")

    overlay_base_ids = {item["base_manifest_id"] for item in manifest["sections"]["production_stress_overlay"]}
    supported_base_ids = {item["manifest_id"] for item in base_scenarios if item["policy_supported"]}
    if not overlay_base_ids.issubset(supported_base_ids):
        errors.append("stress overlays reference policy-unsupported base scenarios")

    encoded = json.dumps(manifest, ensure_ascii=False)
    if "BOT_TOKEN" in encoded or "895973" in encoded:
        errors.append("manifest appears to contain Telegram token material")
    return errors


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", default=default_prefix())
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--print-full",
        action="store_true",
        help="Print the full manifest to stdout. By default, --output prints only a compact summary.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    manifest = build_manifest(prefix=args.prefix)
    errors = validate_manifest(manifest)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.output and not args.print_full:
        stdout_payload = {
            "schema_version": manifest["schema_version"],
            "status": "ok" if not errors else "error",
            "prefix": manifest["prefix"],
            "output": str(args.output),
            "summary": manifest["summary"],
        }
    else:
        stdout_payload = manifest
    print(json.dumps(stdout_payload, ensure_ascii=False, sort_keys=True))
    if args.check and errors:
        for error in errors:
            print(f"manifest check failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
