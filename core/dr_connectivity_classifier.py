"""Authenticated multi-vantage Iran connectivity classification."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import secrets
from typing import Any
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from core.dr_event_protocol import canonical_json_bytes
from core.secure_file_io import read_secure_text


PINNED_CONNECTIVITY_POLICY_PATH = Path("/etc/trading-bot/security/dr-connectivity-vantages.json")
POLICY_SCHEMA = "three-site-connectivity-vantage-policy-v1"
TARGETS = frozenset({"webapp_fi", "webapp_ir", "witness"})
SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class ConnectivityEvidenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class VantageIdentity:
    vantage_id: str
    physical_probe_id: str
    region: str
    key_id: str
    public_key: bytes


@dataclass(frozen=True)
class ConnectivityEvidencePolicy:
    campaign_id: str
    controller_id: str
    controller_key_id: str
    controller_public_key: bytes
    targets: dict[str, dict[str, str]]
    vantages: dict[str, VantageIdentity]
    policy_hash: str


@dataclass(frozen=True)
class ConnectivityClassification:
    mode: str
    confidence: str
    consecutive_rounds: int
    evidence_hash: str
    campaign_id: str
    policy_hash: str


def _public_key(value: Any, label: str) -> bytes:
    try:
        decoded = base64.b64decode(str(value), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ConnectivityEvidenceError(f"{label} public key encoding is invalid") from exc
    if len(decoded) != 32:
        raise ConnectivityEvidenceError(f"{label} public key must be Ed25519")
    return decoded


def load_connectivity_policy(
    path: Path = PINNED_CONNECTIVITY_POLICY_PATH,
) -> ConnectivityEvidencePolicy:
    try:
        payload = json.loads(
            read_secure_text(path, label="connectivity vantage policy", max_size=128 * 1024)
        )
    except Exception as exc:
        raise ConnectivityEvidenceError("connectivity vantage policy is invalid") from exc
    required = {"schema", "campaign_id", "controller", "targets", "vantages"}
    if not isinstance(payload, dict) or set(payload) != required or payload["schema"] != POLICY_SCHEMA:
        raise ConnectivityEvidenceError("connectivity vantage policy fields/schema are invalid")
    try:
        campaign_id = str(UUID(str(payload["campaign_id"])))
    except ValueError as exc:
        raise ConnectivityEvidenceError("connectivity campaign id is invalid") from exc
    controller = payload["controller"]
    if not isinstance(controller, dict) or set(controller) != {"controller_id", "key_id", "public_key"}:
        raise ConnectivityEvidenceError("connectivity controller identity is invalid")
    targets_payload = payload["targets"]
    if not isinstance(targets_payload, dict) or set(targets_payload) != TARGETS:
        raise ConnectivityEvidenceError("connectivity target policy is incomplete")
    targets: dict[str, dict[str, str]] = {}
    for target, identity in targets_payload.items():
        if not isinstance(identity, dict) or set(identity) != {"ip", "tls_server_name"}:
            raise ConnectivityEvidenceError("connectivity target identity fields are invalid")
        ip = str(identity["ip"])
        tls = str(identity["tls_server_name"])
        if not ip or not tls or "://" in tls:
            raise ConnectivityEvidenceError("connectivity target identity is invalid")
        targets[target] = {"ip": ip, "tls_server_name": tls}
    raw_vantages = payload["vantages"]
    if not isinstance(raw_vantages, list) or len(raw_vantages) < 3:
        raise ConnectivityEvidenceError("connectivity policy lacks independent vantages")
    vantages: dict[str, VantageIdentity] = {}
    physical_ids: set[str] = set()
    key_ids: set[str] = {str(controller["key_id"])}
    for raw in raw_vantages:
        if not isinstance(raw, dict) or set(raw) != {
            "vantage_id", "physical_probe_id", "region", "key_id", "public_key"
        }:
            raise ConnectivityEvidenceError("connectivity vantage identity fields are invalid")
        vantage_id = str(raw["vantage_id"])
        physical_id = str(raw["physical_probe_id"])
        key_id = str(raw["key_id"])
        region = str(raw["region"])
        if (
            not vantage_id or vantage_id in vantages
            or not physical_id or physical_id in physical_ids
            or not key_id or key_id in key_ids
            or region not in {"iran", "global"}
        ):
            raise ConnectivityEvidenceError("connectivity vantages are not independently identified")
        vantages[vantage_id] = VantageIdentity(
            vantage_id=vantage_id,
            physical_probe_id=physical_id,
            region=region,
            key_id=key_id,
            public_key=_public_key(raw["public_key"], vantage_id),
        )
        physical_ids.add(physical_id)
        key_ids.add(key_id)
    if sum(item.region == "iran" for item in vantages.values()) < 2 or not any(
        item.region == "global" for item in vantages.values()
    ):
        raise ConnectivityEvidenceError("connectivity policy lacks Iran/global independence")
    policy_hash = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return ConnectivityEvidencePolicy(
        campaign_id=campaign_id,
        controller_id=str(controller["controller_id"]),
        controller_key_id=str(controller["key_id"]),
        controller_public_key=_public_key(controller["public_key"], "controller"),
        targets=targets,
        vantages=vantages,
        policy_hash=policy_hash,
    )


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


def _verify_signature(public_key: bytes, payload: dict[str, Any], signature: Any, label: str) -> None:
    try:
        decoded = base64.b64decode(str(signature), validate=True)
        Ed25519PublicKey.from_public_bytes(public_key).verify(decoded, canonical_json_bytes(payload))
    except (ValueError, binascii.Error, InvalidSignature) as exc:
        raise ConnectivityEvidenceError(f"{label} signature is invalid") from exc


def classify_connectivity(
    rounds: list[dict[str, Any]],
    *,
    policy: ConnectivityEvidencePolicy,
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
    round_ids: set[str] = set()
    nonces: set[str] = set()
    for round_payload in rounds:
        required_round = {"campaign_id", "round_id", "observed_at", "probes", "collector_receipt"}
        if not isinstance(round_payload, dict) or set(round_payload) != required_round:
            raise ConnectivityEvidenceError("connectivity round fields are invalid")
        if round_payload["campaign_id"] != policy.campaign_id:
            raise ConnectivityEvidenceError("connectivity evidence belongs to another campaign")
        try:
            round_id = str(UUID(str(round_payload["round_id"])))
        except ValueError as exc:
            raise ConnectivityEvidenceError("connectivity round id is invalid") from exc
        if round_id in round_ids:
            raise ConnectivityEvidenceError("connectivity round was replayed")
        round_ids.add(round_id)
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
        seen_targets: set[tuple[str, str]] = set()
        for probe in probes:
            required_probe = {
                "vantage_id", "physical_probe_id", "vantage_region", "target",
                "target_ip", "tls_server_name", "reachable", "observed_at",
                "nonce", "key_id", "signature",
            }
            if not isinstance(probe, dict) or set(probe) != required_probe:
                raise ConnectivityEvidenceError("connectivity probe fields are invalid")
            unsigned = {key: value for key, value in probe.items() if key != "signature"}
            vantage_id = str(probe["vantage_id"])
            identity = policy.vantages.get(vantage_id)
            target = str(probe["target"])
            target_policy = policy.targets.get(target)
            nonce = str(probe["nonce"])
            probe_time = _timestamp(probe["observed_at"])
            if (
                identity is None or target_policy is None
                or probe["physical_probe_id"] != identity.physical_probe_id
                or probe["vantage_region"] != identity.region
                or probe["key_id"] != identity.key_id
                or probe["target_ip"] != target_policy["ip"]
                or probe["tls_server_name"] != target_policy["tls_server_name"]
                or type(probe["reachable"]) is not bool
                or not nonce or nonce in nonces
                or abs((probe_time - observed_at).total_seconds()) > 30
                or (vantage_id, target) in seen_targets
            ):
                raise ConnectivityEvidenceError("connectivity probe identity/context is invalid or replayed")
            _verify_signature(identity.public_key, unsigned, probe["signature"], "connectivity probe")
            nonces.add(nonce)
            seen_targets.add((vantage_id, target))
            if identity.region == "iran":
                domestic.setdefault(identity.physical_probe_id, {})[target] = probe["reachable"]
            elif target == "webapp_fi" and probe["reachable"]:
                global_ok = True
        receipt = round_payload["collector_receipt"]
        if not isinstance(receipt, dict) or set(receipt) != {
            "controller_id", "key_id", "round_hash", "signature"
        }:
            raise ConnectivityEvidenceError("connectivity collector receipt fields are invalid")
        unsigned_round = {key: value for key, value in round_payload.items() if key != "collector_receipt"}
        round_hash = hashlib.sha256(canonical_json_bytes(unsigned_round)).hexdigest()
        unsigned_receipt = {key: value for key, value in receipt.items() if key != "signature"}
        if (
            receipt["controller_id"] != policy.controller_id
            or receipt["key_id"] != policy.controller_key_id
            or receipt["round_hash"] != round_hash
        ):
            raise ConnectivityEvidenceError("connectivity collector receipt identity is invalid")
        _verify_signature(
            policy.controller_public_key,
            unsigned_receipt,
            receipt["signature"],
            "connectivity collector receipt",
        )
        usable = [result for result in domestic.values() if TARGETS.issubset(result)]
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
    return ConnectivityClassification(
        mode, confidence, len(tail), evidence_hash, policy.campaign_id, policy.policy_hash
    )
