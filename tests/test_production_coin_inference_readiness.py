from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest

from core.market_intelligence.coin_rate_engine import COIN_RATE_ENGINE_VERSION, COIN_SPECS
from core.market_intelligence.market_contracts import (
    MARKET_STORE_CONTRACT_VERSION,
    MarketObservation,
    derive_event_key,
)
from core.market_intelligence.market_snapshot import (
    MARKET_SNAPSHOT_SCHEMA_VERSION,
    publish_market_snapshot_atomically,
)
from core.market_intelligence.market_store import (
    advance_source_checkpoint,
    connect_market_store,
    initialize_market_store,
    upsert_observation,
)
from scripts import check_production_coin_inference_readiness as readiness


NOW = datetime(2026, 8, 21, 9, 30, tzinfo=timezone.utc)


def test_cli_does_not_parse_operational_compose_dotenv(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "PRODUCTION_ORCHESTRATION_ONLY_KEY=true\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment.pop("APP_ENV_FILE", None)
    result = subprocess.run(
        [
            sys.executable,
            str(readiness.REPO_ROOT / "scripts" / "check_production_coin_inference_readiness.py"),
            "--help",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def _snapshot(
    *,
    underlying_age: float = 20.0,
    omit_code: str | None = None,
    all_low: bool = False,
) -> dict:
    items = []
    for code in COIN_SPECS:
        for settlement in ("CASH", "TOMORROW"):
            confidence = (
                "LOW_PAPER_FALLBACK"
                if all_low or code == omit_code
                else "MEDIUM"
            )
            items.append(
                {
                    "commodity_code": code,
                    "settlement_term": settlement,
                    "status": "ESTIMATED",
                    "estimated_project_price": 100_000,
                    "lower_project_price": 99_000,
                    "upper_project_price": 101_000,
                    "confidence": confidence,
                    "method": "readiness-test",
                    "underlying_source": "PRIVATE_PHYSICAL_TODAY",
                    "underlying_age_seconds": underlying_age,
                    "anchor_age_seconds": None,
                    "market_regime": "OSCILLATING",
                    "reason": None,
                    "herat_source": None,
                    "herat_basis_relative": None,
                }
            )
    return {
        "schema_version": MARKET_SNAPSHOT_SCHEMA_VERSION,
        "market_store_contract_version": MARKET_STORE_CONTRACT_VERSION,
        "builder_version": "readiness-test",
        "generated_at_utc": NOW.isoformat().replace("+00:00", "Z"),
        "snapshot_status": "PARTIAL_COIN_RATE_STATE",
        "signals": {},
        "rates": {
            "engine_version": COIN_RATE_ENGINE_VERSION,
            "items": items,
            "estimated_count": len(items),
            "no_data_count": 0,
        },
    }


def _no_data_snapshot(*, with_safe_context: bool) -> dict:
    snapshot = _snapshot()
    for item in snapshot["rates"]["items"]:
        item.update(
            {
                "status": "NO_DATA",
                "estimated_project_price": None,
                "lower_project_price": None,
                "upper_project_price": None,
                "confidence": "NONE",
                "underlying_source": None,
                "underlying_age_seconds": None,
                "anchor_age_seconds": None,
            }
        )
    snapshot["snapshot_status"] = "NO_DATA_COIN_RATE_STATE"
    snapshot["rates"]["estimated_count"] = 0
    snapshot["rates"]["no_data_count"] = len(snapshot["rates"]["items"])
    if with_safe_context:
        snapshot[readiness.SAFE_NO_DATA_CONTEXT_KEY] = {
            "contract_version": readiness.SAFE_NO_DATA_CONTEXT_VERSION,
            "source_status": "DEGRADED_GUARD_FAIL_OPEN",
            "source_reason": readiness.SAFE_NO_DATA_SOURCE_REASON,
            "group_inputs_within_hot_retention": True,
            "private_input_within_hot_retention": True,
            "collector_checkpoint_count": 3,
            "price_authority": False,
        }
    return snapshot


def _publish(root: Path, **overrides: object) -> tuple[Path, str]:
    path = root / "coin-rates.json"
    digest = publish_market_snapshot_atomically(path, _snapshot(**overrides))
    path.chmod(0o600)
    return path, digest


def _publish_no_data(root: Path, *, with_safe_context: bool) -> tuple[Path, str]:
    path = root / "coin-rates.json"
    digest = publish_market_snapshot_atomically(
        path,
        _no_data_snapshot(with_safe_context=with_safe_context),
    )
    path.chmod(0o600)
    return path, digest


def _observation(source: str, *, at: datetime, private: bool = False) -> MarketObservation:
    return MarketObservation(
        event_key=derive_event_key("production-readiness", source),
        source_code=source,
        source_family="TELEGRAM_PRIVATE" if private else "GROUP",
        event_time_utc=at,
        available_at_utc=at,
        instrument="MELTED_GOLD_PRIVATE" if private else "COIN_IMAM",
        market_label="PRIVATE_GOLD_PHYSICAL" if private else "COIN_MARKET",
        settlement_term="TODAY" if private else "CASH",
        trade_form="PHYSICAL",
        event_type="OFFER",
        side="SELL",
        price=80_000_000 if private else 190_000,
        price_unit=("TOMAN_PER_MESGHAL_750" if private else "PROJECT_THOUSAND_TOMAN"),
        currency="TOMAN" if private else "IRT",
        quantity=1,
        quantity_unit="PIECE",
        parse_confidence=1.0,
        parser_version="production-readiness-test",
        quality_state="ELIGIBLE",
        quality_policy_version="production-readiness-test",
    )


def _market_store(root: Path, *, private_age_seconds: int = 20) -> Path:
    production = root / "production-market-store"
    production.mkdir()
    path = production / "market.sqlite3"
    connection = connect_market_store(path)
    initialize_market_store(connection)
    upsert_observation(connection, _observation("GROUP_1", at=NOW - timedelta(minutes=10)))
    upsert_observation(connection, _observation("GROUP_2", at=NOW - timedelta(minutes=8)))
    upsert_observation(
        connection,
        _observation(
            "PRIVATE_GOLD_CHANNEL",
            at=NOW - timedelta(seconds=private_age_seconds),
            private=True,
        ),
    )
    for index, source in enumerate(
        (
            "COIN_GROUP_EVENT_CHANNEL",
            "PRIVATE_GOLD_EVENT_OFFER",
            "PRIVATE_GOLD_EVENT_TRADE",
        ),
        start=1,
    ):
        advance_source_checkpoint(
            connection,
            source_code=source,
            message_id=index,
            event_time_utc=NOW.isoformat().replace("+00:00", "Z"),
        )
    connection.commit()
    connection.close()
    path.chmod(0o600)
    for sidecar in production.glob("market.sqlite3-*"):
        sidecar.chmod(0o600)
    return path


def test_snapshot_gate_requires_fresh_effective_underlying_and_useful_coverage() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        path, digest = _publish(root)
        _loaded, report = readiness._snapshot_assessment(path, expected_sha256=digest, now=NOW)
        assert report["hard_reject_eligible_count"] == len(COIN_SPECS) * 2
        assert report["pure_guard_probe"] == "ALLOWED"

        stale, stale_digest = _publish(root, underlying_age=121.0)
        with pytest.raises(
            readiness.ProductionInferenceReadinessError,
            match="hard_reject_coverage_insufficient",
        ):
            readiness._snapshot_assessment(
                stale,
                expected_sha256=stale_digest,
                now=NOW,
                require_hard_reject_coverage=True,
            )

        incomplete, incomplete_digest = _publish(root, omit_code="ONE_GRAM")
        with pytest.raises(
            readiness.ProductionInferenceReadinessError,
            match="hard_reject_coverage_insufficient",
        ):
            readiness._snapshot_assessment(
                incomplete,
                expected_sha256=incomplete_digest,
                now=NOW,
                require_hard_reject_coverage=True,
            )


def test_low_only_snapshot_passes_transport_and_reports_guard_fail_open() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        path, digest = _publish(root, all_low=True)
        _loaded, report = readiness._snapshot_assessment(
            path,
            expected_sha256=digest,
            now=NOW,
        )
        assert report["status"] == "DEGRADED_GUARD_FAIL_OPEN"
        assert report["hard_reject_coverage_ready"] is False
        assert report["pure_guard_probe"] == "ABSTAINED_FAIL_OPEN"
        with pytest.raises(
            readiness.ProductionInferenceReadinessError,
            match="hard_reject_coverage_insufficient",
        ):
            readiness._snapshot_assessment(
                path,
                expected_sha256=digest,
                now=NOW,
                require_hard_reject_coverage=True,
            )


def test_safe_no_data_snapshot_is_degraded_and_guard_abstains() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        path, digest = _publish_no_data(root, with_safe_context=True)

        _loaded, report = readiness._snapshot_assessment(
            path,
            expected_sha256=digest,
            now=NOW,
        )

        assert report["status"] == "DEGRADED_GUARD_FAIL_OPEN"
        assert report["snapshot_mode"] == "SAFE_NO_DATA"
        assert report["estimated_rate_count"] == 0
        assert report["hard_reject_coverage_ready"] is False
        assert report["pure_guard_probe"] == "ABSTAINED_FAIL_OPEN"
        with pytest.raises(
            readiness.ProductionInferenceReadinessError,
            match="hard_reject_coverage_insufficient",
        ):
            readiness._snapshot_assessment(
                path,
                expected_sha256=digest,
                now=NOW,
                require_hard_reject_coverage=True,
            )


def test_unbound_no_data_snapshot_remains_blocked() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        path, digest = _publish_no_data(root, with_safe_context=False)

        with pytest.raises(
            readiness.ProductionInferenceReadinessError,
            match="estimated_rate_coverage_unavailable",
        ):
            readiness._snapshot_assessment(
                path,
                expected_sha256=digest,
                now=NOW,
            )


def test_source_gate_requires_both_groups_private_input_and_all_checkpoints() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        path = _market_store(root)
        report = readiness._source_probe(path, now=NOW)
        assert report["collector_checkpoint_count"] == 3
        assert report["private_gold_age_seconds"] == 20.0

        connection = connect_market_store(path)
        connection.execute(
            "DELETE FROM market_source_checkpoints WHERE source_code = ?",
            ("PRIVATE_GOLD_EVENT_TRADE",),
        )
        connection.commit()
        connection.close()
        with pytest.raises(
            readiness.ProductionInferenceReadinessError,
            match="upstream_input_freshness_or_coverage_failed",
        ):
            readiness._source_probe(path, now=NOW)


def test_source_gate_rejects_stale_private_input_and_insecure_store() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        stale = _market_store(root, private_age_seconds=121)
        report = readiness._source_probe(stale, now=NOW)
        assert report["status"] == "DEGRADED_GUARD_FAIL_OPEN"
        assert report["freshness_basis"] == "ECONOMIC_EVENT_TIME"
        assert report["private_gold_hard_authority_fresh"] is False
        assert report["private_gold_engine_age_supported"] is True
        assert report["degradation_reason"] == readiness.SAFE_NO_DATA_SOURCE_REASON
        assert report["safe_no_data_snapshot_allowed"] is True
        assert readiness.safe_no_data_source_assessment(report) is True
        stale.chmod(0o644)
        with pytest.raises(
            readiness.ProductionInferenceReadinessError,
            match="market_store_metadata_invalid",
        ):
            readiness._source_probe(stale, now=NOW)


def test_stale_group_input_is_degraded_but_cannot_authorize_no_data() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        path = _market_store(root, private_age_seconds=121)
        connection = connect_market_store(path)
        connection.execute(
            """
            UPDATE market_observations
            SET event_time_utc = ?, available_at_utc = ?
            WHERE source_code = 'GROUP_1'
            """,
            (
                (NOW - timedelta(days=4)).isoformat().replace("+00:00", "Z"),
                (NOW - timedelta(days=4)).isoformat().replace("+00:00", "Z"),
            ),
        )
        connection.commit()
        connection.close()

        report = readiness._source_probe(path, now=NOW)

        assert report["status"] == "DEGRADED_GUARD_FAIL_OPEN"
        assert report["degradation_reason"] == "GROUP_INPUTS_OUTSIDE_HOT_RETENTION"
        assert report["safe_no_data_snapshot_allowed"] is False
        assert readiness.safe_no_data_source_assessment(report) is False


def test_source_freshness_uses_event_time_not_recent_backfill_availability() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        path = _market_store(root)
        connection = connect_market_store(path)
        connection.execute(
            """
            UPDATE market_observations
            SET event_time_utc = ?, available_at_utc = ?
            WHERE source_code = 'PRIVATE_GOLD_CHANNEL'
            """,
            (
                (NOW - timedelta(hours=2)).isoformat().replace("+00:00", "Z"),
                NOW.isoformat().replace("+00:00", "Z"),
            ),
        )
        connection.commit()
        connection.close()

        report = readiness._source_probe(path, now=NOW)

        assert report["private_gold_age_seconds"] == 7200.0
        assert report["private_gold_engine_age_supported"] is False
        assert report["status"] == "DEGRADED_GUARD_FAIL_OPEN"


def test_fresh_private_paper_is_not_reported_as_hard_price_authority() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        path = _market_store(root)
        connection = connect_market_store(path)
        connection.execute(
            """
            UPDATE market_observations
            SET trade_form = 'PAPER_NORMAL', settlement_term = 'TOMORROW'
            WHERE source_code = 'PRIVATE_GOLD_CHANNEL'
            """
        )
        connection.commit()
        connection.close()

        report = readiness._source_probe(path, now=NOW)

        assert report["private_gold_paper_age_seconds"] == 20.0
        assert report["private_gold_physical_age_seconds"] is None
        assert report["private_gold_engine_age_supported"] is True
        assert report["private_gold_hard_authority_fresh"] is False
        assert report["status"] == "DEGRADED_GUARD_FAIL_OPEN"


def test_consumer_gate_binds_canonical_read_only_mount_digest_and_flags(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        snapshot, digest = _publish(root)
        mountinfo = root / "mountinfo"
        mountinfo.write_text(
            "37 28 0:35 / /app/runtime/coin-inference ro,relatime - tmpfs tmpfs ro\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(readiness, "CANONICAL_CONTAINER_DIR", root)
        monkeypatch.setattr(readiness, "CANONICAL_CONTAINER_SNAPSHOT", snapshot)
        monkeypatch.setenv("COIN_INTELLIGENCE_INFERENCE_PREVIEW_ENABLED", "true")
        monkeypatch.setenv("COIN_INTELLIGENCE_INFERENCE_SELECTION_ENABLED", "true")
        monkeypatch.setenv("OFFER_MODEL_PRICE_GUARD_ENABLED", "true")
        monkeypatch.setenv("COIN_INTELLIGENCE_INFERENCE_AUTO_SELECTION_ENABLED", "false")
        mountinfo.write_text(
            f"37 28 0:35 / {root} ro,relatime - tmpfs tmpfs ro\n",
            encoding="utf-8",
        )
        args = type(
            "Args",
            (),
            {
                "snapshot": str(snapshot),
                "mountinfo": str(mountinfo),
                "expected_sha256": digest,
                "expect_enabled": True,
            },
        )()
        report = readiness._consumer_probe(args, now=NOW)
        assert report["mount_read_only"] is True

        mountinfo.write_text(
            f"37 28 0:35 / {root} rw,relatime - tmpfs tmpfs rw\n",
            encoding="utf-8",
        )
        with pytest.raises(
            readiness.ProductionInferenceReadinessError,
            match="consumer_snapshot_mount_not_read_only",
        ):
            readiness._consumer_probe(args, now=NOW)


def test_cli_is_redacted_and_requires_exact_production_confirmation(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = readiness.main(
        [
            "--environment",
            "production",
            "--production-confirmation",
            "wrong",
            "snapshot",
            "--snapshot",
            "/secret/snapshot.json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert result == 2
    assert payload == {
        "reason": "production_confirmation_required",
        "secrets_disclosed": False,
        "status": "BLOCKED",
    }
    assert "/secret" not in json.dumps(payload)
