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
from core.market_intelligence.private_pipeline_contracts import (
    content_hash,
    estimator_snapshot_id,
)
from core.market_intelligence.estimator_snapshot_runtime import (
    build_estimator_snapshot,
)
from scripts import check_production_coin_inference_readiness as readiness
from tests.test_market_pipeline_stage10_snapshot import Stage10SnapshotTests
from tests.test_product_snapshot_reader import (
    _snapshot_document as _private_snapshot_document,
    _write_private_view,
)


NOW = datetime(2026, 8, 21, 9, 30, tzinfo=timezone.utc)


def _with_private_primary_source_inputs(document: dict) -> dict:
    traces = []
    stamp = NOW.isoformat().replace("+00:00", "Z")
    for index, (component, source_code) in enumerate(
        sorted(readiness.PRIVATE_PRIMARY_SOURCE_INPUTS.items()), start=1
    ):
        traces.append(
            {
                "component": component,
                "source_codes": [source_code],
                "source_event_key": f"{index:064x}",
                "source_fact_id": f"{index + 20:064x}",
                "fact_revision": 1,
                "occurred_at_utc": stamp,
                "available_at_utc": stamp,
                "parsed_at_utc": stamp,
                "transferred_at_utc": stamp,
                "point_value": "1",
                "mean_value": "1",
                "unit": "PROJECT_THOUSAND_TOMAN",
                "sample_count": 1,
                "selection_method": "PRIVATE_PRIMARY_SOURCE_READINESS_V1",
                "fallback": False,
                "freshness": "FRESH",
                "age_seconds": 0.0,
            }
        )
    document["inputs"] = traces
    document["input_snapshot_hash"] = content_hash(traces)
    document["snapshot_id"] = estimator_snapshot_id(document)
    return document


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


def test_host_readiness_and_relay_import_without_application_dependencies() -> None:
    """The host relay must not require the container-only application stack."""

    environment = os.environ.copy()
    environment["APP_ENV_FILE"] = str(
        readiness.REPO_ROOT / "config" / "unit-test.env.example"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import builtins

original_import = builtins.__import__

def dependency_guard(name, *args, **kwargs):
    if name.split('.', 1)[0] in {'sqlalchemy', 'pydantic_settings'}:
        raise ModuleNotFoundError(f'blocked container dependency: {name}')
    return original_import(name, *args, **kwargs)

builtins.__import__ = dependency_guard
import scripts.check_production_coin_inference_readiness
import scripts.relay_production_coin_inference_snapshot
""",
        ],
        cwd=readiness.REPO_ROOT,
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


def test_private_primary_consumer_binds_mount_reader_mode_and_exact_grid(
    monkeypatch,
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        snapshot = root / "latest-private-primary.json"
        document = _with_private_primary_source_inputs(_private_snapshot_document(
            lane="PRIVATE_PRIMARY",
            generated_at=NOW.isoformat().replace("+00:00", "Z"),
        ))
        _write_private_view(snapshot, document)
        digest = readiness.sha256(snapshot.read_bytes()).hexdigest()
        mountinfo = root / "mountinfo"
        mountinfo.write_text(
            f"37 28 0:35 / {root} ro,relatime - tmpfs tmpfs ro\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(readiness, "PRIVATE_PRIMARY_CONTAINER_DIR", root)
        monkeypatch.setattr(
            readiness, "PRIVATE_PRIMARY_CONTAINER_SNAPSHOT", snapshot
        )
        monkeypatch.setenv("PRODUCT_ESTIMATOR_SNAPSHOT_MODE", "PRIVATE_PRIMARY")
        monkeypatch.setenv(
            "PRODUCT_ESTIMATOR_PRIVATE_PRIMARY_SNAPSHOT_PATH", str(snapshot)
        )
        monkeypatch.setenv(
            "PRODUCT_ESTIMATOR_SNAPSHOT_MAX_AGE_SECONDS", "120"
        )
        monkeypatch.setenv("COIN_INTELLIGENCE_INFERENCE_PREVIEW_ENABLED", "true")
        monkeypatch.setenv("COIN_INTELLIGENCE_INFERENCE_SELECTION_ENABLED", "true")
        monkeypatch.setenv("OFFER_MODEL_PRICE_GUARD_ENABLED", "true")
        monkeypatch.setenv(
            "COIN_INTELLIGENCE_INFERENCE_AUTO_SELECTION_ENABLED", "false"
        )
        args = type(
            "Args",
            (),
            {
                "snapshot": str(snapshot),
                "mountinfo": str(mountinfo),
                "expected_sha256": digest,
            },
        )()

        report = readiness._private_primary_consumer_probe(args, now=NOW)

        assert report["authority"] == "PRIVATE_PRIMARY"
        assert report["rate_cell_count"] == 14
        assert report["snapshot_digest"] == digest
        assert report["required_source_input_trace_count"] == 9
        assert len(report["source_input_trace_sha256"]) == 64

        missing_trace = json.loads(json.dumps(document))
        missing_trace["inputs"].pop()
        missing_trace["input_snapshot_hash"] = content_hash(missing_trace["inputs"])
        missing_trace["snapshot_id"] = estimator_snapshot_id(missing_trace)
        _write_private_view(snapshot, missing_trace)
        args.expected_sha256 = readiness.sha256(snapshot.read_bytes()).hexdigest()
        with pytest.raises(
            readiness.ProductionInferenceReadinessError,
            match="private_primary_source_input_inventory_invalid",
        ):
            readiness._private_primary_consumer_probe(args, now=NOW)

        duplicate_trace = json.loads(json.dumps(document))
        duplicate_trace["inputs"].append(
            json.loads(json.dumps(duplicate_trace["inputs"][0]))
        )
        duplicate_trace["input_snapshot_hash"] = content_hash(
            duplicate_trace["inputs"]
        )
        duplicate_trace["snapshot_id"] = estimator_snapshot_id(duplicate_trace)
        _write_private_view(snapshot, duplicate_trace)
        args.expected_sha256 = readiness.sha256(snapshot.read_bytes()).hexdigest()
        with pytest.raises(
            readiness.ProductionInferenceReadinessError,
            match="private_primary_source_input_inventory_invalid",
        ):
            readiness._private_primary_consumer_probe(args, now=NOW)

        future = json.loads(json.dumps(document))
        future["generated_at_utc"] = (NOW + timedelta(seconds=1)).isoformat().replace(
            "+00:00", "Z"
        )
        for trace in future["inputs"]:
            trace["transferred_at_utc"] = future["generated_at_utc"]
        future["input_snapshot_hash"] = content_hash(future["inputs"])
        future["snapshot_id"] = estimator_snapshot_id(future)
        _write_private_view(snapshot, future)
        args.expected_sha256 = readiness.sha256(snapshot.read_bytes()).hexdigest()
        with pytest.raises(
            readiness.ProductionInferenceReadinessError,
            match="private_primary_snapshot_stale_or_future",
        ):
            readiness._private_primary_consumer_probe(args, now=NOW)

        _write_private_view(snapshot, document)
        args.expected_sha256 = digest

        monkeypatch.setenv("PRODUCT_ESTIMATOR_SNAPSHOT_MODE", "LEGACY")
        with pytest.raises(
            readiness.ProductionInferenceReadinessError,
            match="private_primary_mode_not_configured",
        ):
            readiness._private_primary_consumer_probe(args, now=NOW)


def test_private_primary_consumer_accepts_real_estimator_snapshot_with_extra_signals(
    monkeypatch,
) -> None:
    """Exercise the gate against the real builder, not a nine-trace fixture.

    The real snapshot contains pricing-signal traces in addition to the nine
    source-inventory proof traces.  Those legitimate extras must remain
    accepted, while every component in the complete input ledger stays unique.
    """

    case = Stage10SnapshotTests(methodName="runTest")
    case.setUp()
    try:
        case._seed_remaining_primary_inventory()
        generated = datetime(2026, 8, 26, 5, 0, 10, tzinfo=timezone.utc)
        private_snapshot = build_estimator_snapshot(
            case.market,
            as_of_utc=generated,
            generated_at_utc=generated,
            snapshot_version=1,
            feed_mode="PRIVATE_PRIMARY",
        )
        document = private_snapshot.model_dump(mode="json")
        required = set(readiness.PRIVATE_PRIMARY_SOURCE_INPUTS)
        components = [item["component"] for item in document["inputs"]]
        assert len(components) > len(required)
        assert len(components) == len(set(components))
        assert required.issubset(components)

        trace_count, trace_digest = (
            readiness._private_primary_source_trace_assessment(
                private_snapshot,
                snapshot_age_seconds=0.0,
            )
        )

        assert trace_count == 9
        assert len(trace_digest) == 64
    finally:
        case.tearDown()


@pytest.mark.parametrize("unsafe_state", ("SAFE_NO_DATA", "STALE_TRANSPORT"))
def test_private_primary_consumer_rejects_non_rate_ready_snapshot(
    monkeypatch,
    unsafe_state: str,
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        snapshot = root / "latest-private-primary.json"
        document = _with_private_primary_source_inputs(_private_snapshot_document(
            lane="PRIVATE_PRIMARY",
            generated_at=NOW.isoformat().replace("+00:00", "Z"),
            no_data=unsafe_state == "SAFE_NO_DATA",
        ))
        _write_private_view(snapshot, document)
        if unsafe_state == "STALE_TRANSPORT":
            wrapped = json.loads(snapshot.read_text(encoding="utf-8"))
            wrapped["transport_state"] = "STALE"
            snapshot.write_text(json.dumps(wrapped), encoding="utf-8")
        digest = readiness.sha256(snapshot.read_bytes()).hexdigest()
        mountinfo = root / "mountinfo"
        mountinfo.write_text(
            f"37 28 0:35 / {root} ro,relatime - tmpfs tmpfs ro\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(readiness, "PRIVATE_PRIMARY_CONTAINER_DIR", root)
        monkeypatch.setattr(
            readiness, "PRIVATE_PRIMARY_CONTAINER_SNAPSHOT", snapshot
        )
        monkeypatch.setenv("PRODUCT_ESTIMATOR_SNAPSHOT_MODE", "PRIVATE_PRIMARY")
        monkeypatch.setenv(
            "PRODUCT_ESTIMATOR_PRIVATE_PRIMARY_SNAPSHOT_PATH", str(snapshot)
        )
        monkeypatch.setenv(
            "PRODUCT_ESTIMATOR_SNAPSHOT_MAX_AGE_SECONDS", "120"
        )
        monkeypatch.setenv("COIN_INTELLIGENCE_INFERENCE_PREVIEW_ENABLED", "true")
        monkeypatch.setenv("COIN_INTELLIGENCE_INFERENCE_SELECTION_ENABLED", "true")
        monkeypatch.setenv("OFFER_MODEL_PRICE_GUARD_ENABLED", "true")
        monkeypatch.setenv(
            "COIN_INTELLIGENCE_INFERENCE_AUTO_SELECTION_ENABLED", "false"
        )
        args = type(
            "Args",
            (),
            {
                "snapshot": str(snapshot),
                "mountinfo": str(mountinfo),
                "expected_sha256": digest,
            },
        )()

        with pytest.raises(
            readiness.ProductionInferenceReadinessError,
            match="private_primary_snapshot_not_rate_ready",
        ):
            readiness._private_primary_consumer_probe(args, now=NOW)


def test_private_primary_consumer_detects_replacement_during_probe(
    monkeypatch,
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        snapshot = root / "latest-private-primary.json"
        document = _with_private_primary_source_inputs(_private_snapshot_document(
            lane="PRIVATE_PRIMARY",
            generated_at=NOW.isoformat().replace("+00:00", "Z"),
        ))
        _write_private_view(snapshot, document)
        original = snapshot.read_bytes()
        digest = readiness.sha256(original).hexdigest()
        mountinfo = root / "mountinfo"
        mountinfo.write_text(
            f"37 28 0:35 / {root} ro,relatime - tmpfs tmpfs ro\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(readiness, "PRIVATE_PRIMARY_CONTAINER_DIR", root)
        monkeypatch.setattr(
            readiness, "PRIVATE_PRIMARY_CONTAINER_SNAPSHOT", snapshot
        )
        for key, value in {
            "PRODUCT_ESTIMATOR_SNAPSHOT_MODE": "PRIVATE_PRIMARY",
            "PRODUCT_ESTIMATOR_PRIVATE_PRIMARY_SNAPSHOT_PATH": str(snapshot),
            "PRODUCT_ESTIMATOR_SNAPSHOT_MAX_AGE_SECONDS": "120",
            "COIN_INTELLIGENCE_INFERENCE_PREVIEW_ENABLED": "true",
            "COIN_INTELLIGENCE_INFERENCE_SELECTION_ENABLED": "true",
            "OFFER_MODEL_PRICE_GUARD_ENABLED": "true",
            "COIN_INTELLIGENCE_INFERENCE_AUTO_SELECTION_ENABLED": "false",
        }.items():
            monkeypatch.setenv(key, value)
        real_read_bytes = Path.read_bytes
        calls = 0

        def changing_read_bytes(path: Path) -> bytes:
            nonlocal calls
            if path == snapshot:
                calls += 1
                if calls > 1:
                    return original + b" "
            return real_read_bytes(path)

        monkeypatch.setattr(Path, "read_bytes", changing_read_bytes)
        args = type(
            "Args",
            (),
            {
                "snapshot": str(snapshot),
                "mountinfo": str(mountinfo),
                "expected_sha256": digest,
            },
        )()

        with pytest.raises(
            readiness.ProductionInferenceReadinessError,
            match="private_primary_snapshot_changed_during_probe",
        ):
            readiness._private_primary_consumer_probe(args, now=NOW)


def test_private_primary_consumer_rejects_thirteen_estimated_plus_one_no_data(
    monkeypatch,
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        snapshot = root / "latest-private-primary.json"
        document = _with_private_primary_source_inputs(_private_snapshot_document(
            lane="PRIVATE_PRIMARY",
            generated_at=NOW.isoformat().replace("+00:00", "Z"),
        ))
        document["rates"][0].update(
            {
                "status": "NO_DATA",
                "value": None,
                "lower_bound": None,
                "upper_bound": None,
                "confidence": "NONE",
                "reason_code": "NO_FRESH_MELTED",
                "underlying_source": None,
                "underlying_age_seconds": None,
                "anchor_age_seconds": None,
            }
        )
        document["snapshot_id"] = estimator_snapshot_id(document)
        _write_private_view(snapshot, document)
        digest = readiness.sha256(snapshot.read_bytes()).hexdigest()
        mountinfo = root / "mountinfo"
        mountinfo.write_text(
            f"37 28 0:35 / {root} ro,relatime - tmpfs tmpfs ro\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(readiness, "PRIVATE_PRIMARY_CONTAINER_DIR", root)
        monkeypatch.setattr(
            readiness, "PRIVATE_PRIMARY_CONTAINER_SNAPSHOT", snapshot
        )
        for key, value in {
            "PRODUCT_ESTIMATOR_SNAPSHOT_MODE": "PRIVATE_PRIMARY",
            "PRODUCT_ESTIMATOR_PRIVATE_PRIMARY_SNAPSHOT_PATH": str(snapshot),
            "PRODUCT_ESTIMATOR_SNAPSHOT_MAX_AGE_SECONDS": "120",
            "COIN_INTELLIGENCE_INFERENCE_PREVIEW_ENABLED": "true",
            "COIN_INTELLIGENCE_INFERENCE_SELECTION_ENABLED": "true",
            "OFFER_MODEL_PRICE_GUARD_ENABLED": "true",
            "COIN_INTELLIGENCE_INFERENCE_AUTO_SELECTION_ENABLED": "false",
        }.items():
            monkeypatch.setenv(key, value)
        args = type(
            "Args",
            (),
            {
                "snapshot": str(snapshot),
                "mountinfo": str(mountinfo),
                "expected_sha256": digest,
            },
        )()

        with pytest.raises(
            readiness.ProductionInferenceReadinessError,
            match="private_primary_snapshot_not_rate_ready",
        ):
            readiness._private_primary_consumer_probe(args, now=NOW)


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
