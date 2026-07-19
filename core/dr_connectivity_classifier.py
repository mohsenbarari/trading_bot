"""Multi-vantage, hysteresis-based Iran connectivity classification."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from typing import Any

from core.dr_event_protocol import canonical_json_bytes


class ConnectivityEvidenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class ConnectivityClassification:
    mode: str
    confidence: str
    consecutive_rounds: int
    evidence_hash: str


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ConnectivityEvidenceError("probe timestamp is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConnectivityEvidenceError("probe timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise ConnectivityEvidenceError("probe timestamp lacks timezone")
    return parsed.astimezone(timezone.utc)


def classify_connectivity(
    rounds: list[dict[str, Any]],
    *,
    minimum_rounds: int = 3,
    minimum_domestic_vantages: int = 2,
    now: datetime | None = None,
    max_age_seconds: int = 300,
) -> ConnectivityClassification:
    if len(rounds) < minimum_rounds:
        raise ConnectivityEvidenceError("connectivity evidence lacks hysteresis rounds")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    classified: list[str] = []
    previous_time: datetime | None = None
    for round_payload in rounds:
        if not isinstance(round_payload, dict) or set(round_payload) != {"observed_at", "probes"}:
            raise ConnectivityEvidenceError("connectivity round fields are invalid")
        observed_at = _timestamp(round_payload["observed_at"])
        if observed_at > current or (current - observed_at).total_seconds() > max_age_seconds:
            raise ConnectivityEvidenceError("connectivity round is future-dated or stale")
        if previous_time is not None and observed_at <= previous_time:
            raise ConnectivityEvidenceError("connectivity rounds are not strictly ordered")
        previous_time = observed_at
        probes = round_payload["probes"]
        if not isinstance(probes, list):
            raise ConnectivityEvidenceError("connectivity probes must be a list")
        domestic: dict[str, dict[str, bool]] = {}
        global_ok = False
        for probe in probes:
            if not isinstance(probe, dict) or set(probe) != {
                "vantage_id", "vantage_region", "target", "reachable"
            }:
                raise ConnectivityEvidenceError("connectivity probe fields are invalid")
            vantage_id = str(probe["vantage_id"])
            region = str(probe["vantage_region"])
            target = str(probe["target"])
            if type(probe["reachable"]) is not bool or region not in {"iran", "global"}:
                raise ConnectivityEvidenceError("connectivity probe value/region is invalid")
            if region == "iran":
                domestic.setdefault(vantage_id, {})[target] = probe["reachable"]
            elif target == "webapp_fi" and probe["reachable"]:
                global_ok = True
        usable = [
            result for result in domestic.values()
            if {"webapp_fi", "webapp_ir", "witness"}.issubset(result)
        ]
        if len(usable) < minimum_domestic_vantages:
            classified.append("ambiguous")
            continue
        online_votes = sum(1 for result in usable if result["webapp_fi"])
        isolated_votes = sum(
            1 for result in usable
            if not result["webapp_fi"] and result["webapp_ir"] and result["witness"]
        )
        if online_votes >= minimum_domestic_vantages:
            classified.append("online")
        elif isolated_votes >= minimum_domestic_vantages and global_ok:
            classified.append("isolated")
        else:
            classified.append("ambiguous")
    tail = classified[-minimum_rounds:]
    mode = tail[0] if len(set(tail)) == 1 else "ambiguous"
    confidence = "high" if mode in {"online", "isolated"} else "low"
    evidence_hash = hashlib.sha256(canonical_json_bytes(rounds)).hexdigest()
    return ConnectivityClassification(mode, confidence, len(tail), evidence_hash)
