#!/usr/bin/env python3
"""Run the disposable, network-none Docker gate for Stage 5 coin parsing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from typing import Any, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.market_intelligence.coin_group_feedback import (  # noqa: E402
    ensure_coin_group_feedback_store,
)


DOCKERFILE = REPO_ROOT / "deploy/market-data/Dockerfile"
UTC = timezone.utc


class Stage5RehearsalError(RuntimeError):
    pass


def command(
    arguments: Sequence[str],
    *,
    label: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(arguments),
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode:
        raise Stage5RehearsalError(f"{label}_failed_rc_{result.returncode}")
    return result


def git_release() -> tuple[str, int]:
    if command(["git", "status", "--porcelain=v1"], label="git_status").stdout.strip():
        raise Stage5RehearsalError("git_worktree_must_be_clean")
    sha = command(["git", "rev-parse", "HEAD"], label="git_sha").stdout.strip()
    epoch = command(
        ["git", "show", "-s", "--format=%ct", "HEAD"], label="git_epoch"
    ).stdout.strip()
    if len(sha) != 40 or not epoch.isdigit():
        raise Stage5RehearsalError("git_release_identity_invalid")
    return sha, int(epoch)


def event(
    sequence: int,
    *,
    group: int,
    message_id: int,
    text: str,
    sender: str,
    when: datetime,
    reply_to: int | None = None,
) -> dict[str, Any]:
    published = when.astimezone(UTC).isoformat().replace("+00:00", "Z")
    available = (when + timedelta(seconds=1)).astimezone(UTC).isoformat().replace(
        "+00:00", "Z"
    )
    return {
        "schema": "coin_group_event",
        "schema_version": "2.0",
        "event_id": f"60000000-0000-7000-8000-{sequence:012d}",
        "event_type": "message_created",
        "source": {"market": "coin", "source_id": f"GROUP_{group}"},
        "message": {
            "message_id": str(message_id),
            "published_at_utc": published,
            "edited_at_utc": None,
            "text": text,
            "content_type": "text",
            "is_forwarded": False,
            "is_backfill": False,
            "sender": {
                "peer_id": sender,
                "kind": "user",
                "display_name": None,
            },
            "reply": {
                "status": (
                    "resolved_from_live_stream" if reply_to is not None else "not_reply"
                ),
                "message_id": str(reply_to) if reply_to is not None else None,
            },
        },
        "producer": {"available_at_utc": available},
    }


def prepare_fixture(root: Path) -> tuple[Path, Path, Path, str]:
    state = root / "state"
    spool = root / "capture" / "account2"
    market_spool = root / "capture" / "account1"
    calibration = root / "calibration"
    for path in (state, spool, market_spool, calibration):
        path.mkdir(parents=True, mode=0o700)
        os.chown(path, 10001, 10001)
        os.chmod(path, 0o700)
    at = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=1)
    raw_offer = "امام فروش فردا 190000 / 5 تا"
    records = [
        event(1, group=1, message_id=1, text=raw_offer, sender="a" * 16, when=at),
        event(
            2,
            group=1,
            message_id=2,
            text="ب5 تا189900",
            sender="b" * 16,
            when=at + timedelta(seconds=2),
            reply_to=1,
        ),
        event(
            3,
            group=1,
            message_id=3,
            text="برکت",
            sender="a" * 16,
            when=at + timedelta(seconds=4),
            reply_to=2,
        ),
        # Instrument is deliberately omitted.  Two causal model anchors below
        # make ONE_GRAM the only valid same-book resolution; unlike the two
        # quarter-coin families, this fixture band does not overlap a sibling.
        event(
            4,
            group=2,
            message_id=1,
            text="3 تا نقدی ف 27700",
            sender="c" * 16,
            when=at + timedelta(seconds=6),
        ),
    ]
    partial = event(
        5,
        group=2,
        message_id=2,
        text="نیم بهار نقدی خرید 95000 / 2 تا",
        sender="d" * 16,
        when=at + timedelta(seconds=8),
    )
    partial_line = json.dumps(partial, ensure_ascii=False)
    spool_file = spool / f"events-{at.date().isoformat()}.jsonl"
    spool_file.write_text(
        '{"invalid":"sibling"}\n'
        + "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records)
        + partial_line[:50],
        encoding="utf-8",
    )
    os.chown(spool_file, 10001, 10001)
    os.chmod(spool_file, 0o600)

    feedback = calibration / "review-decisions.sqlite3"
    ensure_coin_group_feedback_store(feedback)
    prediction = calibration / "prediction-ledger.sqlite3"
    connection = sqlite3.connect(prediction)
    try:
        connection.execute(
            """
            CREATE TABLE coin_estimate_predictions(
              id INTEGER PRIMARY KEY,
              prediction_time_utc TEXT NOT NULL,
              created_at_utc TEXT NOT NULL,
              model_id TEXT NOT NULL,
              commodity TEXT NOT NULL,
              settlement TEXT NOT NULL,
              estimated_price_toman INTEGER NOT NULL
            )
            """
        )
        def stamp(value: datetime) -> str:
            return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

        connection.executemany(
            "INSERT INTO coin_estimate_predictions VALUES(?,?,?,?,?,?,?)",
            [
                (
                    1,
                    stamp(at - timedelta(minutes=10)),
                    stamp(at - timedelta(minutes=10) + timedelta(seconds=1)),
                    "MAIN_ONLINE",
                    "یک گرمی",
                    "CASH",
                    27_600_000,
                ),
                (
                    2,
                    stamp(at - timedelta(minutes=5)),
                    stamp(at - timedelta(minutes=5) + timedelta(seconds=1)),
                    "MAIN_ONLINE",
                    "یک گرمی",
                    "CASH",
                    27_700_000,
                ),
            ],
        )
        connection.commit()
    finally:
        connection.close()
    for path in (feedback, prediction):
        os.chown(path, 10001, 10001)
        os.chmod(path, 0o400)
    return spool_file, feedback, prediction, partial_line


def docker_run(
    image: str,
    release_sha: str,
    root: Path,
    *,
    mode: str,
    oneshot: bool,
    name: str | None = None,
    detach: bool = False,
    sidecars: bool = True,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    arguments = [
        "docker",
        "run",
        "--network",
        "none",
        "--read-only",
        "--tmpfs",
        "/tmp:size=16m,mode=1777,noexec,nosuid,nodev",
        "--user",
        "10001:10001",
        "-e",
        f"MARKET_PIPELINE_MODE={mode}",
        "-e",
        f"MARKET_PIPELINE_RELEASE_SHA={release_sha}",
        "-e",
        "MARKET_PIPELINE_STATE_ROOT=/var/lib/market-data/state",
        "-e",
        "MARKET_PIPELINE_CAPTURE_ROOT=/var/lib/market-data/capture",
        "-e",
        "MARKET_PROCESSOR_MARKET_SPOOL_DIR=/var/lib/market-data/capture/account1",
        "-e",
        "MARKET_PROCESSOR_COIN_SPOOL_DIR=/var/lib/market-data/capture/account2",
        "-e",
        "MARKET_PROCESSOR_INTERVAL_SECONDS=0.25",
        "-e",
        f"MARKET_PROCESSOR_ONESHOT={'true' if oneshot else 'false'}",
        "-v",
        f"{root / 'state'}:/var/lib/market-data/state",
        "-v",
        f"{root / 'capture'}:/var/lib/market-data/capture:ro",
    ]
    if sidecars:
        arguments.extend(
            [
                "-e",
                "MARKET_PROCESSOR_FEEDBACK_DB=/var/lib/market-data/calibration/review-decisions.sqlite3",
                "-e",
                "MARKET_PROCESSOR_PREDICTION_DB=/var/lib/market-data/calibration/prediction-ledger.sqlite3",
                "-v",
                f"{root / 'calibration'}:/var/lib/market-data/calibration:ro",
            ]
        )
    if name:
        arguments.extend(["--name", name])
    if detach:
        arguments.append("--detach")
    else:
        arguments.append("--rm")
    arguments.extend([image, "service", "--role", "market-processor"])
    return command(arguments, label="coin_processor_run", check=check)


def inspect_result(root: Path) -> dict[str, Any]:
    role_state = root / "state" / "market-processor"
    health = json.loads((role_state / "health.json").read_text(encoding="utf-8"))
    market = sqlite3.connect(role_state / "shadow-market.sqlite3")
    market.row_factory = sqlite3.Row
    try:
        rows = market.execute(
            "SELECT source_code,event_type,instrument,price_value,quality_state,"
            "parser_version,attributes_json FROM market_observations "
            "ORDER BY source_code,event_type,instrument"
        ).fetchall()
        integrity = market.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        market.close()
    eligible = [row for row in rows if row["quality_state"] == "ELIGIBLE"]
    if integrity != "ok" or len(eligible) != 4:
        raise Stage5RehearsalError("market_projection_gate_failed")
    if not any(
        row["source_code"] == "GROUP_2"
        and row["instrument"] == "COIN_ONE_GRAM"
        for row in eligible
    ):
        raise Stage5RehearsalError("temporal_instrument_resolution_failed")
    trades = [row for row in eligible if row["event_type"] == "TRADE"]
    if len(trades) != 1 or trades[0]["price_value"] != "189900":
        raise Stage5RehearsalError("exact_reply_branch_trade_failed")
    if any("امام فروش" in str(row["attributes_json"]) for row in rows):
        raise Stage5RehearsalError("raw_text_leaked_to_market_store")
    return {
        "facts": len(rows),
        "eligible": len(eligible),
        "trades": len(trades),
        "pending_or_rejected": len(rows) - len(eligible),
        "integrity": integrity,
        "parser_version": health["parser_version"],
        "anchors": health["last_projection_causal_inputs"]["anchors"],
    }


def run_rehearsal() -> dict[str, Any]:
    release_sha, epoch = git_release()
    suffix = secrets.token_hex(5)
    image = f"market-stage5-rehearsal:{release_sha[:12]}-{suffix}"
    owner = f"market-stage5-owner-{suffix}"
    root: Path | None = None
    cleanup = {"container_removed": False, "image_removed": False, "root_removed": False}
    try:
        command(
            [
                "docker",
                "build",
                "--no-cache",
                "--file",
                str(DOCKERFILE),
                "--tag",
                image,
                "--build-arg",
                f"SOURCE_SHA={release_sha}",
                "--build-arg",
                "IMAGE_VERSION=stage5-coin-parser-rehearsal",
                "--build-arg",
                f"SOURCE_DATE_EPOCH={epoch}",
                ".",
            ],
            label="coin_processor_image_build",
        )
        root = Path(tempfile.mkdtemp(prefix="market-stage5-"))
        os.chown(root, 10001, 10001)
        os.chmod(root, 0o700)
        spool_file, _feedback, _prediction, partial_line = prepare_fixture(root)

        missing = docker_run(
            image,
            release_sha,
            root,
            mode="live",
            oneshot=True,
            sidecars=False,
            check=False,
        )
        if missing.returncode != 78:
            raise Stage5RehearsalError("missing_causal_inputs_did_not_fail_closed")

        docker_run(image, release_sha, root, mode="live", oneshot=True)
        first_health = json.loads(
            (root / "state/market-processor/health.json").read_text(encoding="utf-8")
        )
        if first_health["counters"]["records"] != 5:
            raise Stage5RehearsalError("partial_tail_cursor_gate_failed")
        with spool_file.open("a", encoding="utf-8") as stream:
            stream.write(partial_line[50:] + "\n")
        docker_run(image, release_sha, root, mode="live", oneshot=True)
        second_health = json.loads(
            (root / "state/market-processor/health.json").read_text(encoding="utf-8")
        )
        if second_health["counters"]["records"] != 1:
            raise Stage5RehearsalError("partial_tail_resume_gate_failed")
        docker_run(image, release_sha, root, mode="live", oneshot=True)
        replay_health = json.loads(
            (root / "state/market-processor/health.json").read_text(encoding="utf-8")
        )
        if replay_health["counters"]["records"] != 0:
            raise Stage5RehearsalError("replay_idempotency_gate_failed")
        inspected = inspect_result(root)

        docker_run(
            image,
            release_sha,
            root,
            mode="live",
            oneshot=False,
            name=owner,
            detach=True,
        )
        time.sleep(1)
        healthcheck = command(
            [
                "docker",
                "exec",
                owner,
                "python",
                "-m",
                "core.market_intelligence.private_pipeline_foundation",
                "healthcheck",
                "--role",
                "market-processor",
            ],
            label="processor_healthcheck",
            check=False,
        )
        if healthcheck.returncode:
            raise Stage5RehearsalError("processor_healthcheck_failed")
        command(["docker", "stop", "--time", "5", owner], label="owner_stop")
        command(["docker", "rm", owner], label="owner_remove")
        cleanup["container_removed"] = True
        return {
            "status": "pass",
            "release_sha": release_sha,
            "projection": inspected,
            "causal_inputs": {"missing_fail_closed": True},
            "restart": {"partial_tail_resumed": True, "replay_records": 0},
            "isolation": {
                "network": "none",
                "shadow_only": True,
                "telegram_session_used": False,
                "product_database_touched": False,
            },
            "cleanup": cleanup,
        }
    finally:
        command(["docker", "rm", "--force", owner], label="cleanup_owner", check=False)
        cleanup["container_removed"] = True
        removed = command(
            ["docker", "image", "rm", "--force", image],
            label="cleanup_image",
            check=False,
        )
        cleanup["image_removed"] = removed.returncode == 0
        if root is not None and root.exists():
            shutil.rmtree(root)
        cleanup["root_removed"] = not (root is not None and root.exists())


def main() -> int:
    try:
        result = run_rehearsal()
    except Stage5RehearsalError as exc:
        print(
            json.dumps({"status": "fail", "reason_code": str(exc)}, sort_keys=True),
            file=sys.stderr,
        )
        return 1
    if not all(result["cleanup"].values()):
        print(
            json.dumps({"status": "fail", "reason_code": "stage5_cleanup_incomplete"}),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
