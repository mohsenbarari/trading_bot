#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


MATRIX_SCHEMA_VERSION = "trade_notification_delivery_matrix_v1"
SURFACES = ("webapp", "telegram")
OUTAGE_CLASSES = ("stable", "short_under_2m", "medium_around_60m")
OFFER_HOME_BY_SURFACE = {
    "webapp": "iran",
    "telegram": "foreign",
}


@dataclass(frozen=True)
class SurfacePair:
    offer_surface: str
    request_surface: str

    @property
    def name(self) -> str:
        return f"{self.offer_surface}_offer__{self.request_surface}_request"

    @property
    def offer_home_server(self) -> str:
        return OFFER_HOME_BY_SURFACE[self.offer_surface]


@dataclass(frozen=True)
class ActorPair:
    pair_id: str
    title: str
    source_kind: str
    responder_kind: str
    group_relation: str


@dataclass(frozen=True)
class OutageClass:
    outage_id: str
    title: str
    remote_delivery_policy: str


@dataclass(frozen=True)
class DeliveryScenario:
    scenario_id: str
    actor_pair_id: str
    surface_pair: str
    outage_id: str
    offer_home_server: str
    expected_remote_delivery_policy: str


def build_surface_pairs() -> list[SurfacePair]:
    return [
        SurfacePair(offer_surface=offer_surface, request_surface=request_surface)
        for offer_surface in SURFACES
        for request_surface in SURFACES
    ]


def build_actor_pairs() -> list[ActorPair]:
    return [
        ActorPair("user__user", "standard user with standard user", "user", "user", "none"),
        ActorPair("user__tier1_same_owner", "user with same-group tier1 customer", "user", "tier1", "same_owner"),
        ActorPair("user__tier2_same_owner", "user with same-group tier2 customer", "user", "tier2", "same_owner"),
        ActorPair("user__tier1_other_owner", "user with other-owner tier1 customer", "user", "tier1", "other_owner"),
        ActorPair("user__tier2_other_owner", "user with other-owner tier2 customer", "user", "tier2", "other_owner"),
        ActorPair("tier1__user_same_owner", "tier1 customer with same-group user", "tier1", "user", "same_owner"),
        ActorPair("tier2__user_same_owner", "tier2 customer with same-group user", "tier2", "user", "same_owner"),
        ActorPair("tier1__user_other_owner", "tier1 customer with other user", "tier1", "user", "other_owner"),
        ActorPair("tier2__user_other_owner", "tier2 customer with other user", "tier2", "user", "other_owner"),
        ActorPair("tier1__tier1_same_owner", "tier1 customer with same-group tier1 customer", "tier1", "tier1", "same_owner"),
        ActorPair("tier1__tier2_same_owner", "tier1 customer with same-group tier2 customer", "tier1", "tier2", "same_owner"),
        ActorPair("tier2__tier1_same_owner", "tier2 customer with same-group tier1 customer", "tier2", "tier1", "same_owner"),
        ActorPair("tier2__tier2_same_owner", "tier2 customer with same-group tier2 customer", "tier2", "tier2", "same_owner"),
        ActorPair("tier1__tier1_other_owner", "tier1 customer with other-owner tier1 customer", "tier1", "tier1", "other_owner"),
        ActorPair("tier1__tier2_other_owner", "tier1 customer with other-owner tier2 customer", "tier1", "tier2", "other_owner"),
        ActorPair("tier2__tier1_other_owner", "tier2 customer with other-owner tier1 customer", "tier2", "tier1", "other_owner"),
        ActorPair("tier2__tier2_other_owner", "tier2 customer with other-owner tier2 customer", "tier2", "tier2", "other_owner"),
    ]


def build_outage_classes() -> list[OutageClass]:
    return [
        OutageClass(
            "stable",
            "stable Iran/foreign connectivity",
            "all required local and remote WebApp/Telegram deliveries must be sent",
        ),
        OutageClass(
            "short_under_2m",
            "short cross-server outage under two minutes",
            "remote delivery may wait for sync visibility but must still be sent",
        ),
        OutageClass(
            "medium_around_60m",
            "medium cross-server outage around one hour",
            "old opposite-server delivery is skipped without stale user-facing messages",
        ),
    ]


def build_delivery_scenarios() -> list[DeliveryScenario]:
    scenarios: list[DeliveryScenario] = []
    counter = 1
    for actor_pair in build_actor_pairs():
        for surface_pair in build_surface_pairs():
            for outage in build_outage_classes():
                scenarios.append(
                    DeliveryScenario(
                        scenario_id=f"TDN-{counter:03d}",
                        actor_pair_id=actor_pair.pair_id,
                        surface_pair=surface_pair.name,
                        outage_id=outage.outage_id,
                        offer_home_server=surface_pair.offer_home_server,
                        expected_remote_delivery_policy=outage.remote_delivery_policy,
                    )
                )
                counter += 1
    return scenarios


def filter_delivery_scenarios(
    scenarios: list[DeliveryScenario],
    *,
    scenario_ids: set[str],
    outage_ids: set[str],
    max_scenarios: int | None,
) -> list[DeliveryScenario]:
    selected = [
        scenario
        for scenario in scenarios
        if (not scenario_ids or scenario.scenario_id in scenario_ids)
        and (not outage_ids or scenario.outage_id in outage_ids)
    ]
    if max_scenarios is not None:
        selected = selected[: max(0, max_scenarios)]
    return selected


def build_matrix_payload(
    *,
    scenario_ids: set[str] | None = None,
    outage_ids: set[str] | None = None,
    max_scenarios: int | None = None,
) -> dict[str, Any]:
    actor_pairs = build_actor_pairs()
    surface_pairs = build_surface_pairs()
    outage_classes = build_outage_classes()
    all_scenarios = build_delivery_scenarios()
    scenarios = filter_delivery_scenarios(
        all_scenarios,
        scenario_ids=scenario_ids or set(),
        outage_ids=outage_ids or set(),
        max_scenarios=max_scenarios,
    )
    return {
        "schema_version": MATRIX_SCHEMA_VERSION,
        "actor_pair_count": len(actor_pairs),
        "surface_pair_count": len(surface_pairs),
        "outage_class_count": len(outage_classes),
        "total_scenario_count": len(all_scenarios),
        "scenario_count": len(scenarios),
        "actor_pairs": [asdict(item) for item in actor_pairs],
        "surface_pairs": [
            {
                **asdict(item),
                "name": item.name,
                "offer_home_server": item.offer_home_server,
            }
            for item in surface_pairs
        ],
        "outage_classes": [asdict(item) for item in outage_classes],
        "scenarios": [asdict(item) for item in scenarios],
        "production_gate": {
            "status": "blocked_until_owner_staging_validation",
            "reason": "Notification-delivery scenario evidence is staging-only and does not authorize production by itself.",
        },
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Report the trade notification delivery scenario matrix.")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--scenario", action="append", default=[])
    parser.add_argument("--outage", action="append", default=[])
    parser.add_argument("--max-scenarios", type=int)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    scenario_ids = set(args.scenario or [])
    payload = build_matrix_payload(
        scenario_ids=scenario_ids,
        outage_ids=set(args.outage or []),
        max_scenarios=args.max_scenarios,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    if args.check:
        expected = len(build_actor_pairs()) * len(build_surface_pairs()) * len(build_outage_classes())
        if scenario_ids:
            return 0 if payload["scenario_count"] == len(scenario_ids) else 1
        return 0 if payload["scenario_count"] == expected else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
