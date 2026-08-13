from pathlib import Path


SYSTEMD_ROOT = (
    Path(__file__).resolve().parents[1] / "deploy" / "coin_intelligence" / "systemd"
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
