#!/usr/bin/env python3
"""Pure fail-closed bridge for a future exact-release Witness status exporter.

This module deliberately does not run an exporter or contact Witness.  It
accepts one canonical, redacted exporter record, binds it to a controller
supplied exact-release/exporter/key policy, and reduces it to the existing
``witness_live`` input contract.  It cannot publish evidence or make a
convergence source-set ready.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Mapping

from scripts import production_shadow_convergence_witness_live as WITNESS


EXPORTER_RECORD_SCHEMA = "production-shadow-witness-live-exporter-record-v1"
EXPORTER_POLICY_SCHEMA = "production-shadow-witness-live-exporter-policy-v1"
MAX_RECORD_BYTES = WITNESS.MAX_INPUT_BYTES

IDENTITY_FIELDS = WITNESS.IDENTITY_FIELDS
POLICY_FIELDS = frozenset(
    {
        "schema",
        "exporter_relative_path",
        "exporter_sha256",
        "witness_public_key",
        "witness_public_key_sha256",
    }
)
RECORD_FIELDS = frozenset(
    {
        "schema",
        "status",
        *IDENTITY_FIELDS,
        "journal_started_at",
        "observed_at",
        "exporter_relative_path",
        "exporter_sha256",
        "exporter_release_sha",
        "exporter_release_tree_sha",
        "witness_public_key",
        "witness_public_key_sha256",
        "signed_proof",
        "signed_proof_sha256",
        "witness_status_receipt_sha256",
        "exporter_record_sha256",
    }
)


class WitnessLiveExporterError(ValueError):
    """A future Witness exporter record is not safely bound to this release."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WitnessLiveExporterError("Witness exporter JSON has duplicate fields")
        result[key] = value
    return result


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value, allow_nan=False, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise WitnessLiveExporterError("Witness exporter value is not canonical JSON") from exc


def _sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _nonzero_sha256(value: Any, *, label: str) -> str:
    try:
        return WITNESS._nonzero_sha256(value, label=label)  # noqa: SLF001
    except WITNESS.WitnessLiveContractError as exc:
        raise WitnessLiveExporterError(f"{label} is invalid") from exc


def _identity(value: Any) -> dict[str, str]:
    try:
        return WITNESS._identity_from_mapping(value)  # noqa: SLF001
    except WITNESS.WitnessLiveContractError as exc:
        raise WitnessLiveExporterError("Witness exporter identity fields differ") from exc


def _relative_path(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.startswith("/")
        or value.startswith("./")
        or "//" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise WitnessLiveExporterError("Witness exporter relative path is invalid")
    return value


def _timestamp_text(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise WitnessLiveExporterError("journal start time is invalid")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _record_digest(document: Mapping[str, Any]) -> str:
    return _sha256({key: value for key, value in document.items() if key != "exporter_record_sha256"})


def parse_exporter_record_payload(payload: bytes) -> dict[str, Any]:
    """Decode exactly one bounded canonical record without filesystem or network I/O."""

    if not isinstance(payload, bytes) or not 1 <= len(payload) <= MAX_RECORD_BYTES:
        raise WitnessLiveExporterError("Witness exporter record payload is invalid")
    try:
        document = json.loads(payload.decode("ascii"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WitnessLiveExporterError("Witness exporter record payload is invalid") from exc
    if not isinstance(document, dict) or payload != _canonical_json(document) + b"\n":
        raise WitnessLiveExporterError("Witness exporter record payload is not canonical")
    return document


def validate_exporter_policy(value: Any) -> dict[str, str]:
    """Validate the controller-pinned policy; the record never supplies its policy."""

    if not isinstance(value, Mapping) or set(value) != POLICY_FIELDS:
        raise WitnessLiveExporterError("Witness exporter policy fields differ")
    public_key = value.get("witness_public_key")
    if (
        not isinstance(public_key, str)
        or not WITNESS.witness_public_key_is_valid(public_key)
        or value.get("witness_public_key_sha256")
        != hashlib.sha256(public_key.encode("ascii")).hexdigest()
    ):
        raise WitnessLiveExporterError("Witness exporter policy public key is invalid")
    if value.get("schema") != EXPORTER_POLICY_SCHEMA:
        raise WitnessLiveExporterError("Witness exporter policy schema differs")
    return {
        "schema": EXPORTER_POLICY_SCHEMA,
        "exporter_relative_path": _relative_path(value.get("exporter_relative_path")),
        "exporter_sha256": _nonzero_sha256(value.get("exporter_sha256"), label="exporter SHA-256"),
        "witness_public_key": public_key,
        "witness_public_key_sha256": value["witness_public_key_sha256"],
    }


def reduce_exporter_record(
    record: Any,
    *,
    identity: Mapping[str, Any],
    journal_started_at: datetime,
    exporter_policy: Mapping[str, Any],
    now: datetime,
) -> dict[str, Any]:
    """Bind an exporter record, then return validated existing Witness input.

    The caller must obtain ``exporter_policy`` from an independently pinned
    exact-release material/manifest.  This function intentionally has no path
    parameter and therefore cannot discover or trust a policy from the record.
    """

    expected_identity = _identity(identity)
    policy = validate_exporter_policy(exporter_policy)
    if not isinstance(journal_started_at, datetime) or journal_started_at.tzinfo is None:
        raise WitnessLiveExporterError("journal start time is invalid")
    if not isinstance(record, Mapping) or set(record) != RECORD_FIELDS:
        raise WitnessLiveExporterError("Witness exporter record fields differ")
    document = dict(record)
    expected = {
        "schema": EXPORTER_RECORD_SCHEMA,
        "status": "observed",
        **expected_identity,
        "journal_started_at": _timestamp_text(journal_started_at),
        "exporter_relative_path": policy["exporter_relative_path"],
        "exporter_sha256": policy["exporter_sha256"],
        "exporter_release_sha": expected_identity["release_sha"],
        "exporter_release_tree_sha": expected_identity["release_tree_sha"],
        "witness_public_key": policy["witness_public_key"],
        "witness_public_key_sha256": policy["witness_public_key_sha256"],
    }
    if any(document.get(key) != item for key, item in expected.items()):
        raise WitnessLiveExporterError("Witness exporter record binding differs")
    if document.get("exporter_record_sha256") != _record_digest(document):
        raise WitnessLiveExporterError("Witness exporter record digest differs")
    candidate = {
        "schema": WITNESS.INPUT_SCHEMA,
        "status": "observed",
        **expected_identity,
        "journal_started_at": expected["journal_started_at"],
        "observed_at": document.get("observed_at"),
        "witness_public_key": policy["witness_public_key"],
        "witness_public_key_sha256": policy["witness_public_key_sha256"],
        "signed_proof": document.get("signed_proof"),
        "signed_proof_sha256": document.get("signed_proof_sha256"),
        "witness_status_receipt_sha256": document.get("witness_status_receipt_sha256"),
        "input_sha256": WITNESS.ZERO_SHA256,
    }
    candidate["input_sha256"] = WITNESS._input_digest(candidate)  # noqa: SLF001
    try:
        checked, _observed_at = WITNESS.validate_input(
            candidate,
            identity=expected_identity,
            journal_started_at=journal_started_at,
            now=now,
        )
    except WITNESS.WitnessLiveContractError as exc:
        raise WitnessLiveExporterError("Witness exporter signed lease record is invalid") from exc
    return checked
