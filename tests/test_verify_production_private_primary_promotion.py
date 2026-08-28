from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path

import pytest

from core.market_intelligence.coin_rate_engine import COIN_RATE_ENGINE_VERSION
from core.market_intelligence.private_pipeline_contracts import (
    ESTIMATOR_RATE_GRID_V1,
    content_hash,
    estimator_snapshot_id,
)
from scripts import verify_production_private_primary_promotion as verifier
from scripts import audit_production_market_catchup as catchup_audit


NOW = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)
RELEASE_SHA = "a" * 40
RELEASE_TREE = "b" * 40
BOT_IMAGE = "sha256:" + "c" * 64
WEB_IMAGE = "sha256:" + "d" * 64
PROJECT = "market-private-pipeline-primary"
SEQUENCES = {"coin-group-1": 91, "coin-group-2": 57, "private-gold": 42}


def test_catchup_receipt_contract_matches_the_authoritative_auditor() -> None:
    assert verifier.CATCHUP_RECEIPT_SCHEMA == catchup_audit.VERIFICATION_SCHEMA
    assert verifier.AUTHORIZED_BACKFILL_NOT_BEFORE_UTC == catchup_audit.CUTOFF_UTC
    assert verifier.AUTHORIZED_CATCHUP_BACKFILL_SOURCES == tuple(
        sorted(catchup_audit.BACKFILL_SOURCES)
    )
    assert verifier.AUTHORIZED_CATCHUP_SOURCE_INVENTORY == tuple(
        sorted(catchup_audit.LIVE_CAPTURE_SOURCES)
    )


def _snapshot_document(
    *,
    lane: str,
    version: int,
    generated_at: str,
    no_data: bool = False,
) -> dict[str, object]:
    rates: list[dict[str, object]] = []
    for index, (instrument, settlement) in enumerate(ESTIMATOR_RATE_GRID_V1):
        center = 190_000 - index * 5_000
        rates.append(
            {
                "instrument": instrument,
                "settlement": settlement,
                "status": "NO_DATA" if no_data else "ESTIMATED",
                "value": None if no_data else str(center),
                "unit": "PROJECT_THOUSAND_TOMAN",
                "lower_bound": None if no_data else str(center - 1_000),
                "upper_bound": None if no_data else str(center + 1_000),
                "confidence": "NONE" if no_data else "HIGH",
                "method": (
                    "ABSTAIN_NO_FRESH_MELTED"
                    if no_data
                    else "SAME_SETTLEMENT_COIN_ANCHOR_TRANSFER"
                ),
                "reason_code": "NO_FRESH_MELTED" if no_data else None,
                "underlying_source": None if no_data else "PRIVATE_PHYSICAL_TODAY",
                "underlying_age_seconds": None if no_data else 5.0,
                "anchor_age_seconds": None if no_data else 30.0,
                "market_regime": "RANGE",
            }
        )
    payload: dict[str, object] = {
        "contract": "estimator_snapshot/2.0",
        "snapshot_version": version,
        "generated_at_utc": generated_at,
        "input_snapshot_hash": content_hash([]),
        "model_version": COIN_RATE_ENGINE_VERSION,
        "feed_mode": lane,
        "status": "SAFE_NO_DATA" if no_data else "OK",
        "rates": rates,
        "health": [],
        "inputs": [],
        "reason_codes": ["NO_ESTIMATED_COIN_RATES"] if no_data else [],
    }
    payload["snapshot_id"] = estimator_snapshot_id(payload)
    return payload


def _write(path: Path, value: object, *, compact: bool = True) -> None:
    if compact:
        text = json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    else:
        text = json.dumps(value, sort_keys=True, indent=2) + "\n"
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)


def _write_env(path: Path, *, image: str, role: str) -> None:
    values = {
        "MARKET_HMAC_ACTIVE_FILE": "/root/protected/never-emit-this-marker",
        "MARKET_PIPELINE_ALLOW_PRIVATE_PRIMARY": "1",
        "MARKET_PIPELINE_EXPECTED_SNAPSHOT_LANE": "PRIVATE_PRIMARY",
        "MARKET_PIPELINE_FEED_MODE": "PRIVATE_PRIMARY",
        "MARKET_PIPELINE_IMAGE": image,
        "MARKET_PIPELINE_PROJECT_NAME": PROJECT,
        "MARKET_PIPELINE_RELEASE_SHA": RELEASE_SHA,
    }
    if role == "web":
        values.update(
            {
                "MARKET_CAPTURE_BACKFILL_NOT_BEFORE_UTC": verifier.AUTHORIZED_BACKFILL_NOT_BEFORE_UTC,
                "MARKET_CAPTURE_BACKFILL_SOURCE_CODES": verifier.AUTHORIZED_BACKFILL_SOURCE_CODES,
                "MARKET_CAPTURE_BACKFILL_MAX_MESSAGES": "100000",
            }
        )
    path.write_text(
        "".join(f"{key}={values[key]}\n" for key in sorted(values)),
        encoding="utf-8",
    )
    path.chmod(0o600)


def _view(
    *,
    generated_at: datetime = NOW - timedelta(seconds=10),
    version: int = 7,
    no_data: bool = False,
    partial: bool = False,
    sparse_one_gram: bool = False,
) -> dict[str, object]:
    document = _snapshot_document(
        lane="PRIVATE_PRIMARY",
        version=version,
        generated_at=generated_at.isoformat().replace("+00:00", "Z"),
        no_data=no_data,
    )
    if partial:
        rate = document["rates"][0]  # type: ignore[index]
        rate.update(  # type: ignore[union-attr]
            {
                "status": "NO_DATA",
                "value": None,
                "lower_bound": None,
                "upper_bound": None,
                "confidence": "NONE",
                "method": "ABSTAIN_NO_FRESH_MELTED",
                "reason_code": "NO_FRESH_MELTED",
                "underlying_source": None,
                "underlying_age_seconds": None,
                "anchor_age_seconds": None,
            }
        )
        document["snapshot_id"] = estimator_snapshot_id(document)
    if sparse_one_gram:
        rate = next(
            item
            for item in document["rates"]  # type: ignore[union-attr]
            if item["instrument"] == "COIN_ONE_GRAM"  # type: ignore[index]
            and item["settlement"] == "CASH"  # type: ignore[index]
        )
        rate.update(
            {
                "status": "NO_DATA",
                "value": None,
                "lower_bound": None,
                "upper_bound": None,
                "confidence": "NONE",
                "method": verifier.SAFE_SPARSE_NO_DATA_METHOD,
                "reason_code": verifier.SAFE_SPARSE_NO_DATA_REASON,
                "anchor_age_seconds": None,
            }
        )
        document["snapshot_id"] = estimator_snapshot_id(document)
    return {
        "contract": verifier.WEB_VIEW_CONTRACT,
        "snapshot_hash": document["snapshot_id"],
        "snapshot_version": document["snapshot_version"],
        "feed_mode": "PRIVATE_PRIMARY",
        "received_at_utc": (
            generated_at + timedelta(seconds=1)
        ).isoformat().replace("+00:00", "Z"),
        "published_at_utc": (
            generated_at + timedelta(seconds=2)
        ).isoformat().replace("+00:00", "Z"),
        "transport_state": "FRESH",
        "stale_after_seconds": 120,
        "snapshot": document,
    }


def _snapshot_identity(path: Path, view: dict[str, object]) -> dict[str, object]:
    snapshot = view["snapshot"]
    assert isinstance(snapshot, dict)
    rates = snapshot["rates"]
    assert isinstance(rates, list)
    return {
        "contract": verifier.WEB_VIEW_CONTRACT,
        "snapshot_hash": view["snapshot_hash"],
        "snapshot_version": view["snapshot_version"],
        "feed_mode": "PRIVATE_PRIMARY",
        "snapshot_status": snapshot["status"],
        "estimated_rate_count": sum(
            isinstance(rate, dict) and rate.get("status") == "ESTIMATED"
            for rate in rates
        ),
        "file_sha256": sha256(path.read_bytes()).hexdigest(),
    }


def _owners(role: str, image: str) -> dict[str, dict[str, object]]:
    services = verifier.BOT_SERVICES if role == "bot" else verifier.WEB_SERVICES
    values = {
        service: {
            "count": 1,
            "release_sha": RELEASE_SHA,
            "release_tree": RELEASE_TREE,
            "project_name": PROJECT,
            "image_id": image,
            "healthy": True,
        }
        for service in services
    }
    if role == "web":
        values["market-database"].update(
            {
                "release_sha": None,
                "release_tree": None,
                "image_id": "sha256:" + "f" * 64,
            }
        )
    return values


def _health(
    *, role: str, image: str, snapshot: dict[str, object]
) -> dict[str, object]:
    sequences = (
        {"receiver": dict(SEQUENCES), "adapter": dict(SEQUENCES)}
        if role == "bot"
        else {"producer": dict(SEQUENCES), "acknowledged": dict(SEQUENCES)}
    )
    return {
        "schema": verifier.OBSERVATION_SCHEMA,
        "role": role,
        "observed_at_utc": (NOW - timedelta(seconds=1)).isoformat().replace(
            "+00:00", "Z"
        ),
        "release_sha": RELEASE_SHA,
        "release_tree": RELEASE_TREE,
        "project_name": PROJECT,
        "image_id": image,
        "owners": _owners(role, image),
        "legacy_owner_count": 0,
        "unexpected_owner_count": 0,
        "sequences": sequences,
        "counts": {
            "duplicate": 0,
            "rejected": 0,
            "dead_letter": 0,
            "open_outbox": 0,
            "receiver_publication_pending": 0,
        },
        "snapshot": snapshot,
        "secrets_disclosed": False,
    }


class Evidence:
    def __init__(self, root: Path) -> None:
        root.chmod(0o700)
        self.root = root
        self.bot_env = root / "bot.env"
        self.web_env = root / "web.env"
        self.bot_journal = root / "bot-journal.json"
        self.web_journal = root / "web-journal.json"
        self.bot_health = root / "bot-health.json"
        self.web_health = root / "web-health.json"
        self.bot_snapshot = root / "bot-snapshot.json"
        self.web_snapshot = root / "web-snapshot.json"
        self.catchup_receipt = root / "catchup-verification.json"
        _write_env(self.bot_env, image=BOT_IMAGE, role="bot")
        _write_env(self.web_env, image=WEB_IMAGE, role="web")
        self.install_view(_view())
        self.install_journals()
        self.install_catchup_receipt()

    def install_view(
        self,
        view: dict[str, object],
        *,
        web_compact: bool = True,
        web_view: dict[str, object] | None = None,
    ) -> None:
        _write(self.bot_snapshot, view)
        selected_web = web_view or view
        _write(self.web_snapshot, selected_web, compact=web_compact)
        _write(
            self.bot_health,
            _health(
                role="bot",
                image=BOT_IMAGE,
                snapshot=_snapshot_identity(self.bot_snapshot, view),
            ),
        )
        _write(
            self.web_health,
            _health(
                role="web",
                image=WEB_IMAGE,
                snapshot=_snapshot_identity(self.web_snapshot, selected_web),
            ),
        )

    def install_journals(self) -> None:
        for role, env_path, image, output in (
            ("bot", self.bot_env, BOT_IMAGE, self.bot_journal),
            ("web", self.web_env, WEB_IMAGE, self.web_journal),
        ):
            _write(
                output,
                {
                    "schema": verifier.JOURNAL_SCHEMA,
                    "status": "PASS",
                    "role": role,
                    "release_sha": RELEASE_SHA,
                    "old_project": "market-private-pipeline-shadow",
                    "new_project": PROJECT,
                    "new_image_id": image,
                    "new_env": str(env_path),
                    "new_env_sha256": sha256(env_path.read_bytes()).hexdigest(),
                    "state_deleted": False,
                    "product_authority_changed": False,
                    "secrets_disclosed": False,
                },
            )

    def install_catchup_receipt(
        self, *, verified_at: datetime = NOW - timedelta(seconds=1)
    ) -> None:
        evidence = {
            label: {
                "sha256": character * 64,
                "observed_at_utc": (
                    verified_at - timedelta(seconds=30 if label.startswith("previous") else 1)
                ).isoformat().replace("+00:00", "Z"),
            }
            for label, character in (
                ("previous_web", "1"),
                ("previous_bot", "2"),
                ("web", "3"),
                ("bot", "4"),
            )
        }
        evidence_binding = sha256(
            (
                json.dumps(
                    evidence,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("ascii")
        ).hexdigest()
        _write(
            self.catchup_receipt,
            {
                "schema": verifier.CATCHUP_RECEIPT_SCHEMA,
                "status": "PASS",
                "verified_at_utc": verified_at.isoformat().replace(
                    "+00:00", "Z"
                ),
                "release_sha": RELEASE_SHA,
                "cutoff_utc": verifier.AUTHORIZED_BACKFILL_NOT_BEFORE_UTC,
                "backfill_sources": list(
                    verifier.AUTHORIZED_CATCHUP_BACKFILL_SOURCES
                ),
                "live_source_inventory": list(
                    verifier.AUTHORIZED_CATCHUP_SOURCE_INVENTORY
                ),
                "live_tail_observed": True,
                "live_advanced_sources": ["GROUP_1"],
                "live_parser_output_advanced_sources": ["GROUP_1"],
                "evidence_artifacts": evidence,
                "evidence_binding_sha256": evidence_binding,
                "upstream_time_gaps_allowed": True,
                "internal_sequence_gaps": 0,
                "unresolved_quarantines": 0,
                "unresolved_rejections": 0,
                "secrets_disclosed": False,
            },
        )

    def verify(
        self,
        receipt_name: str,
        *,
        catchup_digest: str | None = None,
    ) -> dict[str, object]:
        return verifier.verify_to_receipt(
            receipt=self.root / receipt_name,
            confirmation=verifier.CONFIRMATION,
            release_sha=RELEASE_SHA,
            release_tree=RELEASE_TREE,
            bot_image_id=BOT_IMAGE,
            web_image_id=WEB_IMAGE,
            bot_env=self.bot_env,
            web_env=self.web_env,
            bot_journal=self.bot_journal,
            web_journal=self.web_journal,
            bot_health=self.bot_health,
            web_health=self.web_health,
            bot_snapshot=self.bot_snapshot,
            web_snapshot=self.web_snapshot,
            catchup_receipt=self.catchup_receipt,
            expected_catchup_receipt_sha256=(
                catchup_digest
                or sha256(self.catchup_receipt.read_bytes()).hexdigest()
            ),
            now=NOW,
        )


@pytest.fixture
def evidence(tmp_path: Path) -> Evidence:
    return Evidence(tmp_path)


def test_pass_receipt_is_value_free_bound_and_owner_only(evidence: Evidence) -> None:
    receipt = evidence.verify("pass.json")

    assert receipt["status"] == "PASS"
    assert receipt["stream_count"] == len(SEQUENCES)
    assert receipt["snapshot"]["estimated_rate_count"] == 14  # type: ignore[index]
    assert receipt["snapshot"]["safe_no_data_rate_count"] == 0  # type: ignore[index]
    assert receipt["snapshot"]["safe_no_data_cells"] == []  # type: ignore[index]
    assert receipt["catchup_verification"] == {
        "receipt_sha256": sha256(
            evidence.catchup_receipt.read_bytes()
        ).hexdigest(),
        "age_seconds": 1.0,
    }
    assert "catchup_complete_and_live_tail_verified" in receipt["checks"]
    output = evidence.root / "pass.json"
    assert output.stat().st_mode & 0o777 == 0o600
    text = output.read_text(encoding="utf-8")
    assert "never-emit-this-marker" not in text
    assert '"payload_values_included":false' in text
    assert '"pii_included":false' in text
    assert '"secrets_disclosed":false' in text


def test_missing_catchup_receipt_fails_closed(evidence: Evidence) -> None:
    evidence.catchup_receipt.unlink()
    receipt = evidence.verify("missing-catchup.json", catchup_digest="0" * 64)
    assert receipt["status"] == "FAILED"
    assert receipt["reason_code"] == "artifact_unavailable"


def test_tampered_catchup_digest_fails_closed(evidence: Evidence) -> None:
    receipt = evidence.verify("tampered-catchup.json", catchup_digest="0" * 64)
    assert receipt["status"] == "FAILED"
    assert receipt["reason_code"] == "catchup_receipt_cas_mismatch"


def test_stale_catchup_receipt_fails_closed(evidence: Evidence) -> None:
    evidence.install_catchup_receipt(
        verified_at=NOW - timedelta(seconds=121)
    )
    receipt = evidence.verify("stale-catchup.json")
    assert receipt["status"] == "FAILED"
    assert receipt["reason_code"] == "catchup_receipt_stale_or_future"


def test_wrong_catchup_source_inventory_fails_closed(evidence: Evidence) -> None:
    value = json.loads(evidence.catchup_receipt.read_text(encoding="utf-8"))
    value["live_source_inventory"] = value["live_source_inventory"][:-1]
    _write(evidence.catchup_receipt, value)
    receipt = evidence.verify("wrong-catchup-source.json")
    assert receipt["status"] == "FAILED"
    assert receipt["reason_code"] == "catchup_receipt_contract_invalid"


def test_catchup_receipt_requires_exact_owner_only_mode(evidence: Evidence) -> None:
    evidence.catchup_receipt.chmod(0o400)
    receipt = evidence.verify("catchup-mode.json")
    assert receipt["status"] == "FAILED"
    assert receipt["reason_code"] == "catchup_receipt_owner_mode_invalid"


def test_tampered_snapshot_fails_closed(evidence: Evidence) -> None:
    value = json.loads(evidence.bot_snapshot.read_text(encoding="utf-8"))
    value["snapshot"]["rates"][0]["value"] = "999999"
    _write(evidence.bot_snapshot, value)

    receipt = evidence.verify("tampered.json")

    assert receipt["status"] == "FAILED"
    assert receipt["reason_code"] == "private_primary_snapshot_contract_invalid"


def test_sequence_or_ack_gap_fails_closed(evidence: Evidence) -> None:
    value = json.loads(evidence.web_health.read_text(encoding="utf-8"))
    value["sequences"]["acknowledged"]["coin-group-2"] -= 1
    _write(evidence.web_health, value)

    receipt = evidence.verify("gap.json")

    assert receipt["status"] == "FAILED"
    assert receipt["reason_code"] == "sequence_or_ack_gap_detected"


def test_partial_rate_grid_is_rejected(evidence: Evidence) -> None:
    evidence.install_view(_view(partial=True))

    receipt = evidence.verify("partial.json")

    assert receipt["status"] == "FAILED"
    assert receipt["reason_code"] in {
        "health_snapshot_identity_invalid",
        "private_primary_estimated_rate_coverage_invalid",
    }


def test_sparse_one_gram_anchor_absence_is_promotable(evidence: Evidence) -> None:
    evidence.install_view(_view(sparse_one_gram=True))

    receipt = evidence.verify("sparse-one-gram.json")

    assert receipt["status"] == "PASS"
    assert receipt["snapshot"]["estimated_rate_count"] == 13  # type: ignore[index]
    assert receipt["snapshot"]["safe_no_data_rate_count"] == 1  # type: ignore[index]
    assert receipt["snapshot"]["safe_no_data_cells"] == [  # type: ignore[index]
        "COIN_ONE_GRAM:CASH"
    ]


def test_sparse_one_gram_without_fresh_underlying_is_rejected(
    evidence: Evidence,
) -> None:
    view = _view(sparse_one_gram=True)
    rate = next(
        item
        for item in view["snapshot"]["rates"]  # type: ignore[index]
        if item["instrument"] == "COIN_ONE_GRAM"  # type: ignore[index]
        and item["settlement"] == "CASH"  # type: ignore[index]
    )
    rate["underlying_source"] = None  # type: ignore[index]
    rate["underlying_age_seconds"] = None  # type: ignore[index]
    view["snapshot"]["snapshot_id"] = estimator_snapshot_id(  # type: ignore[index]
        view["snapshot"]  # type: ignore[index]
    )
    view["snapshot_hash"] = view["snapshot"]["snapshot_id"]  # type: ignore[index]
    evidence.install_view(view)

    receipt = evidence.verify("sparse-one-gram-no-underlying.json")

    assert receipt["status"] == "FAILED"
    assert receipt["reason_code"] == "private_primary_estimated_rate_coverage_invalid"


def test_safe_no_data_is_not_promotable(evidence: Evidence) -> None:
    evidence.install_view(_view(no_data=True))

    receipt = evidence.verify("no-data.json")

    assert receipt["status"] == "FAILED"
    assert receipt["reason_code"] == "health_snapshot_identity_invalid"


def test_stale_snapshot_is_rejected(evidence: Evidence) -> None:
    evidence.install_view(_view(generated_at=NOW - timedelta(seconds=180)))

    receipt = evidence.verify("stale.json")

    assert receipt["status"] == "FAILED"
    assert receipt["reason_code"] == "private_primary_snapshot_stale_or_future"


def test_bot_web_file_digest_mismatch_is_rejected(evidence: Evidence) -> None:
    view = _view()
    evidence.install_view(view, web_compact=False)

    receipt = evidence.verify("digest-mismatch.json")

    assert receipt["status"] == "FAILED"
    assert receipt["reason_code"] == "bot_web_snapshot_identity_or_digest_mismatch"


def test_release_mismatch_is_rejected(evidence: Evidence) -> None:
    value = json.loads(evidence.bot_health.read_text(encoding="utf-8"))
    value["release_sha"] = "e" * 40
    _write(evidence.bot_health, value)

    receipt = evidence.verify("release-mismatch.json")

    assert receipt["status"] == "FAILED"
    assert receipt["reason_code"] == "health_observation_binding_invalid"


@pytest.mark.parametrize(
    "field",
    (
        "rejected",
        "dead_letter",
        "open_outbox",
        "receiver_publication_pending",
    ),
)
def test_nonzero_transport_or_publication_counter_is_rejected(
    evidence: Evidence,
    field: str,
) -> None:
    value = json.loads(evidence.web_health.read_text(encoding="utf-8"))
    value["counts"][field] = 1
    _write(evidence.web_health, value)

    receipt = evidence.verify(f"nonzero-{field}.json")

    assert receipt["status"] == "FAILED"
    assert receipt["reason_code"] == "nonzero_transport_or_publication_counter"


def test_idempotent_duplicate_delivery_counter_is_allowed(evidence: Evidence) -> None:
    value = json.loads(evidence.web_health.read_text(encoding="utf-8"))
    value["counts"]["duplicate"] = 4
    _write(evidence.web_health, value)
    assert evidence.verify("duplicates-accounted.json")["status"] == "PASS"


def test_health_payload_or_secret_extension_is_rejected_without_leak(
    evidence: Evidence,
) -> None:
    marker = "highly-sensitive-test-marker"
    value = json.loads(evidence.bot_health.read_text(encoding="utf-8"))
    value["token"] = marker
    _write(evidence.bot_health, value)

    receipt = evidence.verify("redacted-failure.json")

    assert receipt["status"] == "FAILED"
    output = evidence.root / "redacted-failure.json"
    assert output.stat().st_mode & 0o777 == 0o600
    assert marker not in output.read_text(encoding="utf-8")


def test_receipt_is_exclusive(evidence: Evidence) -> None:
    evidence.verify("exclusive.json")

    with pytest.raises(
        verifier.PromotionVerificationError,
        match="receipt_exists",
    ):
        evidence.verify("exclusive.json")


def test_secure_reader_rejects_path_inode_swap_during_fd_bound_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "artifact.json"
    replacement = tmp_path / "replacement.json"
    _write(artifact, {"value": "a"})
    _write(replacement, {"value": "b"})
    real_read = os.read
    swapped = False

    def swapping_read(descriptor: int, maximum: int) -> bytes:
        nonlocal swapped
        payload = real_read(descriptor, maximum)
        if payload and not swapped:
            swapped = True
            replacement.replace(artifact)
        return payload

    monkeypatch.setattr(os, "read", swapping_read)
    with pytest.raises(
        verifier.PromotionVerificationError,
        match="artifact_changed_during_read",
    ):
        verifier._read_secure_bytes(
            artifact, root_owned=True, maximum_bytes=1024
        )
