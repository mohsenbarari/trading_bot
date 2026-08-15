from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from core.market_intelligence.input_health import (
    InputHealthConfig,
    build_estimator_input_health,
    update_probe_state,
)


NOW = datetime(2026, 8, 13, 17, 0, tzinfo=timezone.utc)


def _observed(at: datetime = NOW) -> dict[str, object]:
    return {
        "status": "OBSERVED",
        "latest_event_utc": at.isoformat().replace("+00:00", "Z"),
        "average_window_seconds": 90,
    }


def _estimate(*, missing: str | None = None) -> dict[str, object]:
    settlements: dict[str, object] = {}
    for settlement in ("CASH", "TOMORROW"):
        inputs: dict[str, object] = {
            "melted_gold": _observed(),
            "xauusd": _observed(),
            "usd": {
                "status": "ESTIMATED",
                "anchor_event_utc": "2026-08-13T08:30:00Z",
                "price_source": "MARKET_HOURS_AWARE_ESTIMATE",
            },
            "usdt": _observed(),
            "generic_coin": {"status": "NO_DATA"},
            "order_flow": {"status": "NO_DATA", "window_seconds": 600},
        }
        if missing:
            inputs[missing] = {"status": "NO_DATA"}
        settlements[settlement] = {
            "inputs": inputs,
            "market_regime": {
                "status": "OBSERVED",
                "window_seconds": 600,
                "components": [
                    {
                        "name": "MELTED_GOLD",
                        "last_observed_utc": NOW.isoformat().replace("+00:00", "Z"),
                    }
                ],
            },
        }
    return {"settlements": settlements}


def _config(root: Path) -> InputHealthConfig:
    return InputHealthConfig(
        public_telegram_state=root / "public.json",
        external_market_state=root / "external.json",
        group_projection_state=root / "group.json",
        public_telegram_max_age_seconds=60,
        wallex_max_age_seconds=45,
        binance_paxg_max_age_seconds=45,
        group_projection_max_age_seconds=90,
    )


def _write_healthy_probes(config: InputHealthConfig) -> None:
    update_probe_state(
        config.public_telegram_state,
        source="PUBLIC_MARKET_TELEGRAM",
        status="HEALTHY",
        successful=True,
        now=NOW,
    )
    update_probe_state(
        config.external_market_state,
        source="WALLEX_PUBLIC_API",
        status="HEALTHY",
        successful=True,
        now=NOW,
    )
    update_probe_state(
        config.external_market_state,
        source="BINANCE_PAXG_PUBLIC_API",
        status="HEALTHY",
        successful=True,
        now=NOW,
    )
    update_probe_state(
        config.group_projection_state,
        source="COIN_GROUP_PROJECTION",
        status="HEALTHY",
        successful=True,
        now=NOW,
        details={"eligible_offers": 0, "eligible_trades": 0},
    )


def test_quiet_group_is_healthy_when_projection_heartbeat_is_fresh() -> None:
    with TemporaryDirectory() as directory:
        config = _config(Path(directory))
        _write_healthy_probes(config)
        result = build_estimator_input_health(_estimate(), as_of=NOW, config=config)

    assert result["status"] == "HEALTHY"
    assert result["collectors"]["coin_group_projection"]["status"] == "HEALTHY"
    assert result["model_inputs"]["generic_coin"]["status"] == "QUIET_OR_NO_DATA"
    assert result["reason_codes"] == []


def test_stale_public_collector_is_critical_even_while_inputs_are_recent() -> None:
    with TemporaryDirectory() as directory:
        config = _config(Path(directory))
        _write_healthy_probes(config)
        update_probe_state(
            config.public_telegram_state,
            source="PUBLIC_MARKET_TELEGRAM",
            status="HEALTHY",
            successful=True,
            now=NOW - timedelta(minutes=2),
        )
        result = build_estimator_input_health(_estimate(), as_of=NOW, config=config)

    assert result["status"] == "CRITICAL"
    assert result["collectors"]["public_market_telegram"]["reason_code"] == "COLLECTOR_HEARTBEAT_STALE"


def test_failed_supporting_collector_degrades_without_hiding_model_output() -> None:
    with TemporaryDirectory() as directory:
        config = _config(Path(directory))
        _write_healthy_probes(config)
        update_probe_state(
            config.external_market_state,
            source="WALLEX_PUBLIC_API",
            status="FAILED",
            successful=False,
            error_code="WALLEX_TIMEOUTERROR",
            now=NOW,
        )
        result = build_estimator_input_health(_estimate(), as_of=NOW, config=config)

    assert result["status"] == "DEGRADED"
    assert result["collectors"]["wallex_public_api"]["error_code"] == "WALLEX_TIMEOUTERROR"


def test_missing_critical_model_input_is_critical() -> None:
    with TemporaryDirectory() as directory:
        config = _config(Path(directory))
        _write_healthy_probes(config)
        result = build_estimator_input_health(
            _estimate(missing="xauusd"), as_of=NOW, config=config
        )

    assert result["status"] == "CRITICAL"
    assert "MODEL_INPUT_XAUUSD_NO_DATA" in result["reason_codes"]


def test_explicit_xau_proxy_degrades_but_keeps_input_available() -> None:
    with TemporaryDirectory() as directory:
        config = _config(Path(directory))
        _write_healthy_probes(config)
        estimate = _estimate()
        for settlement in ("CASH", "TOMORROW"):
            estimate["settlements"][settlement]["inputs"]["xauusd"] = {
                "status": "ESTIMATED",
                "is_proxy": True,
                "latest_event_utc": NOW.isoformat().replace("+00:00", "Z"),
                "average_window_seconds": 90,
            }
        result = build_estimator_input_health(estimate, as_of=NOW, config=config)

    assert result["status"] == "DEGRADED"
    assert result["model_inputs"]["xauusd"]["status"] == "AVAILABLE_PROXY"
    assert "MODEL_INPUT_XAUUSD_PROXY_ACTIVE" in result["reason_codes"]


def test_failure_heartbeat_preserves_last_success_timestamp() -> None:
    with TemporaryDirectory() as directory:
        path = Path(directory) / "health.json"
        successful = update_probe_state(
            path,
            source="SOURCE",
            status="HEALTHY",
            successful=True,
            now=NOW - timedelta(seconds=10),
        )
        failed = update_probe_state(
            path,
            source="SOURCE",
            status="FAILED",
            successful=False,
            error_code="TIMEOUT",
            now=NOW,
        )

    assert failed["last_success_at_utc"] == successful["last_success_at_utc"]
    assert failed["error_code"] == "TIMEOUT"


def test_coin_group_health_is_derived_from_selected_rate_anchors() -> None:
    with TemporaryDirectory() as directory:
        config = _config(Path(directory))
        _write_healthy_probes(config)
        estimate = _estimate()
        estimate["settlements"]["CASH"]["rates"] = [
            {
                "commodity_name": "امام",
                "group_offer_anchor": {"status": "NO_DATA"},
                "historical_group_anchor": {
                    "status": "OBSERVED",
                    "event_time_utc": "2026-08-13T16:55:00Z",
                },
            }
        ]
        estimate["settlements"]["TOMORROW"]["rates"] = [
            {
                "commodity_name": "امام",
                "group_offer_anchor": {"status": "NO_DATA"},
            }
        ]
        result = build_estimator_input_health(estimate, as_of=NOW, config=config)

    group = result["model_inputs"]["coin_groups"]
    assert group["status"] == "HISTORICAL_ONLY"
    assert group["settlements"] == {"CASH": "HISTORICAL", "TOMORROW": "NO_DATA"}
    assert group["live_commodity_count"] == 0
    assert group["historical_commodity_count"] == 1
    assert group["latest_observation_age_seconds"] == 300.0
    assert result["status"] == "HEALTHY"


def test_live_coin_group_anchor_is_visible_without_masquerading_as_generic_coin() -> None:
    with TemporaryDirectory() as directory:
        config = _config(Path(directory))
        _write_healthy_probes(config)
        estimate = _estimate()
        estimate["settlements"]["CASH"]["rates"] = [
            {
                "commodity_name": "امام",
                "group_offer_anchor": {
                    "status": "OBSERVED",
                    "latest_event_utc": "2026-08-13T16:59:30Z",
                },
            }
        ]
        estimate["settlements"]["TOMORROW"]["rates"] = []
        result = build_estimator_input_health(estimate, as_of=NOW, config=config)

    assert result["model_inputs"]["coin_groups"]["status"] == "PARTIAL"
    assert result["model_inputs"]["coin_groups"]["settlements"]["CASH"] == "OBSERVED"
    assert result["model_inputs"]["generic_coin"]["status"] == "QUIET_OR_NO_DATA"


def test_ambiguous_public_coin_quote_is_excluded_not_reported_as_quiet() -> None:
    with TemporaryDirectory() as directory:
        config = _config(Path(directory))
        _write_healthy_probes(config)
        estimate = _estimate()
        for settlement in ("CASH", "TOMORROW"):
            estimate["settlements"][settlement]["inputs"]["generic_coin"] = {
                "status": "NO_DATA",
                "excluded_input_reason": "AMBIGUOUS_SETTLEMENT_NOT_MODEL_ELIGIBLE",
                "excluded_observations": [
                    {
                        "status": "OBSERVED",
                        "latest_event_utc": "2026-08-13T16:59:45Z",
                    }
                ],
            }
        result = build_estimator_input_health(estimate, as_of=NOW, config=config)

    generic = result["model_inputs"]["generic_coin"]
    assert generic["status"] == "EXCLUDED_BY_CONTRACT"
    assert generic["settlements"] == {"CASH": "EXCLUDED", "TOMORROW": "EXCLUDED"}
    assert generic["latest_observation_age_seconds"] == 15.0
    assert result["status"] == "HEALTHY"
