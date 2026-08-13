from pathlib import Path


SYSTEMD_ROOT = (
    Path(__file__).resolve().parents[1] / "deploy" / "coin_intelligence" / "systemd"
)
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ESTIMATOR_RUNTIME_SURFACES = (
    REPOSITORY_ROOT / "apps" / "coin_rate_estimator" / "README.md",
    REPOSITORY_ROOT / "scripts" / "calibrate_morning_reopen_anchor.py",
    REPOSITORY_ROOT / "scripts" / "fair_coin_model_bakeoff_after_unit_fix.py",
    REPOSITORY_ROOT / "scripts" / "run_staging_coin_intelligence_gate.py",
    REPOSITORY_ROOT / "scripts" / "train_and_compare_coin_shadow_ml.py",
    REPOSITORY_ROOT / "scripts" / "train_residual_shadow_and_calibrate.py",
)


def test_live_units_do_not_reference_retired_worktrees() -> None:
    retired_paths = (
        "/root/trading-bot/coin-commodity-inference-promotion",
        "/root/trading-bot/combined-staging-overtime-coin",
        "/srv/trading-bot-three-site-staging-data",
        "/srv/trading-bot-three-site",
    )
    for path in SYSTEMD_ROOT.rglob("*"):
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8")
        for retired_path in retired_paths:
            assert retired_path not in content, f"{path} references {retired_path}"


def test_estimator_runtime_surfaces_do_not_reference_retired_data_plane() -> None:
    retired_paths = (
        "/srv/trading-bot-three-site-staging-data",
        "/srv/trading-bot-three-site",
    )
    for path in ESTIMATOR_RUNTIME_SURFACES:
        content = path.read_text(encoding="utf-8")
        for retired_path in retired_paths:
            assert retired_path not in content, f"{path} references {retired_path}"
