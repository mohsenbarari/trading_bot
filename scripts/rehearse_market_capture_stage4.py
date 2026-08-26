#!/usr/bin/env python3
"""Run the disposable Docker gate for Stage 4 durable market capture.

The rehearsal is network-none and uses synthetic Telegram envelopes only.  It
exercises both account roles, crash windows on both sides of fsynced append,
replay/deduplication, process ownership, exact retention, health, and cleanup.
No live session, credential, source binding, parser, or product database is
opened.
"""

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
from typing import Any, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.market_intelligence.capture_event_adapter import decode_capture_event
from core.market_intelligence.private_capture_telegram import (
    SOURCE_POLICIES,
    TelegramMessageSnapshot,
    build_deleted_event,
    build_group_event,
    build_market_event,
)


DOCKERFILE = REPO_ROOT / "deploy/market-data/Dockerfile"
UTC = timezone.utc


class Stage4RehearsalError(RuntimeError):
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
        raise Stage4RehearsalError(f"{label}_failed_rc_{result.returncode}")
    return result


def git_release_sha() -> str:
    if command(["git", "status", "--porcelain=v1"], label="git_status").stdout.strip():
        raise Stage4RehearsalError("git_worktree_must_be_clean")
    value = command(["git", "rev-parse", "HEAD"], label="git_revision").stdout.strip()
    if len(value) != 40:
        raise Stage4RehearsalError("git_revision_invalid")
    return value


def source_epoch() -> int:
    raw = command(
        ["git", "show", "-s", "--format=%ct", "HEAD"], label="git_source_epoch"
    ).stdout.strip()
    if not raw.isdigit() or int(raw) <= 0:
        raise Stage4RehearsalError("git_source_epoch_invalid")
    return int(raw)


def build_image(tag: str, release_sha: str, epoch: int) -> tuple[str, float]:
    started = time.monotonic()
    command(
        [
            "docker",
            "build",
            "--no-cache",
            "--file",
            str(DOCKERFILE),
            "--tag",
            tag,
            "--build-arg",
            f"SOURCE_SHA={release_sha}",
            "--build-arg",
            "IMAGE_VERSION=stage4-capture-rehearsal",
            "--build-arg",
            f"SOURCE_DATE_EPOCH={epoch}",
            ".",
        ],
        label="capture_image_build",
    )
    image_id = command(
        ["docker", "image", "inspect", tag, "--format", "{{.Id}}"],
        label="capture_image_inspect",
    ).stdout.strip()
    return image_id, time.monotonic() - started


def _snapshot(
    message_id: int,
    *,
    published: datetime,
    text: str,
    edited: datetime | None = None,
    reply_to: int | None = None,
    sender_id: int | None = 501,
) -> TelegramMessageSnapshot:
    return TelegramMessageSnapshot(
        message_id=message_id,
        published_at=published,
        edited_at=edited,
        text=text,
        has_media=False,
        media_type=None,
        action_type=None,
        entities=(),
        reply_to_message_id=reply_to,
        reply_to_top_id=None,
        grouped_id=None,
        sender_id=sender_id,
        sender_kind="user" if sender_id is not None else "unknown",
        is_forwarded=False,
        via_bot=False,
        post=False,
        silent=False,
        pinned=False,
        noforwards=False,
        is_forum=False,
    )


def fixture_documents(now: datetime) -> dict[str, list[dict[str, Any]]]:
    old = now - timedelta(days=3, seconds=1)

    def market(
        source: str,
        message_id: int,
        at: datetime,
        *,
        event_type: str = "message_created",
        backfill: bool = False,
        edited: datetime | None = None,
        received: datetime | None = None,
        text: str | None = None,
    ) -> dict[str, Any]:
        return build_market_event(
            SOURCE_POLICIES[source],
            _snapshot(
                message_id,
                published=at,
                edited=edited,
                text=text or f"fixture-{source}-{message_id}",
            ),
            event_type=event_type,  # type: ignore[arg-type]
            received_at=received or max(at, edited or at) + timedelta(seconds=1),
            backfill=backfill,
        )

    account1 = [
        market("XAUUSD", 1, old, received=old),
        market("XAUUSD", 2, now - timedelta(seconds=20)),
        market("USD_HERAT", 3, now - timedelta(seconds=18)),
        market("MELTED_PRIMARY_FLOW", 4, now - timedelta(seconds=16)),
        market(
            "MELTED_PRIMARY_FLOW",
            4,
            now - timedelta(seconds=16),
            event_type="message_edited",
            edited=now - timedelta(seconds=10),
            text="fixture-private-edited",
        ),
        market("MELTED_AGGREGATE", 5, now - timedelta(seconds=8)),
        market("MELTED_FLOW", 6, now - timedelta(seconds=6)),
    ]
    account1.append(
        market(
            "XAUUSD",
            2,
            now - timedelta(seconds=20),
            event_type="message_snapshot",
            backfill=True,
            received=now,
        )
    )

    key = b"stage4-fixture-hmac-key-material-0001"
    group1_offer = build_group_event(
        SOURCE_POLICIES["GROUP_1"],
        _snapshot(1, published=now - timedelta(seconds=20), text="fixture-group1-offer"),
        event_type="message_created",
        received_at=now - timedelta(seconds=19),
        backfill=False,
        reply_status="not_reply",
        hmac_key=key,
    )
    group1_reply = build_group_event(
        SOURCE_POLICIES["GROUP_1"],
        _snapshot(
            2,
            published=now - timedelta(seconds=18),
            text="fixture-group1-reply",
            reply_to=1,
            sender_id=502,
        ),
        event_type="message_created",
        received_at=now - timedelta(seconds=17),
        backfill=False,
        reply_status="resolved_from_live_stream",
        hmac_key=key,
    )
    group1_edit = build_group_event(
        SOURCE_POLICIES["GROUP_1"],
        _snapshot(
            2,
            published=now - timedelta(seconds=18),
            edited=now - timedelta(seconds=14),
            text="fixture-group1-reply-edited",
            reply_to=1,
            sender_id=502,
        ),
        event_type="message_edited",
        received_at=now - timedelta(seconds=13),
        backfill=False,
        reply_status="resolved_from_live_stream",
        hmac_key=key,
    )
    group2_offer = build_group_event(
        SOURCE_POLICIES["GROUP_2"],
        _snapshot(1, published=now - timedelta(seconds=12), text="fixture-group2-offer"),
        event_type="message_created",
        received_at=now - timedelta(seconds=11),
        backfill=False,
        reply_status="not_reply",
        hmac_key=key,
    )
    group2_reply = build_group_event(
        SOURCE_POLICIES["GROUP_2"],
        _snapshot(
            2,
            published=now - timedelta(seconds=10),
            text="fixture-group2-reply",
            reply_to=1,
            sender_id=503,
        ),
        event_type="message_created",
        received_at=now - timedelta(seconds=9),
        backfill=True,
        reply_status="resolved_from_api",
        hmac_key=key,
    )
    duplicate = build_group_event(
        SOURCE_POLICIES["GROUP_1"],
        _snapshot(1, published=now - timedelta(seconds=20), text="fixture-group1-offer"),
        event_type="message_created",
        received_at=now,
        backfill=True,
        reply_status="not_reply",
        hmac_key=key,
    )
    account2 = [
        group1_offer,
        group1_reply,
        group1_edit,
        build_deleted_event(
            SOURCE_POLICIES["GROUP_1"], message_id=2, received_at=now - timedelta(seconds=12)
        ),
        group2_offer,
        group2_reply,
        duplicate,
    ]
    return {"account1": account1, "account2": account2}


def write_jsonl(path: Path, documents: Sequence[Mapping[str, Any]]) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    try:
        for document in documents:
            payload = (
                json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                + "\n"
            ).encode("utf-8")
            if os.write(descriptor, payload) != len(payload):
                raise Stage4RehearsalError("fixture_input_short_write")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def prepare_account(root: Path, account: str, documents: Sequence[Mapping[str, Any]]) -> dict[str, Path]:
    account_root = root / account
    paths = {
        "root": account_root,
        "state": account_root / "state",
        "capture": account_root / "capture",
        "session": account_root / "session",
        "input": account_root / "fixture-events.jsonl",
    }
    account_root.mkdir(mode=0o700)
    for key in ("state", "capture", "session"):
        paths[key].mkdir(mode=0o700)
    write_jsonl(paths["input"], documents)
    for path in (account_root, paths["state"], paths["capture"], paths["session"]):
        os.chown(path, 10001, 10001)
        os.chmod(path, 0o700)
    return paths


def run_capture(
    image: str,
    release_sha: str,
    *,
    role: str,
    paths: Mapping[str, Path],
    now: datetime,
    input_enabled: bool = True,
    oneshot: bool = True,
    retention: bool = False,
    crash_point: str | None = None,
    crash_sequence: int = 0,
    name: str | None = None,
    detach: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    arguments = ["docker", "run"]
    if detach:
        arguments.append("--detach")
    else:
        arguments.append("--rm")
    if name:
        arguments.extend(["--name", name])
    arguments.extend(
        [
            "--network",
            "none",
            "--read-only",
            "--user",
            "10001:10001",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--tmpfs",
            "/tmp:size=8m,mode=1777,noexec,nosuid,nodev",
            "--env",
            "MARKET_PIPELINE_MODE=fixture",
            "--env",
            f"MARKET_PIPELINE_RELEASE_SHA={release_sha}",
            "--env",
            "MARKET_PIPELINE_STATE_ROOT=/var/lib/market-data/state",
            "--env",
            "MARKET_PIPELINE_CAPTURE_ROOT=/var/lib/market-data/capture",
            "--env",
            "MARKET_PIPELINE_SESSION_ROOT=/var/lib/market-data/session",
            "--env",
            f"MARKET_CAPTURE_FIXTURE_NOW_UTC={now.isoformat().replace('+00:00', 'Z')}",
            "--volume",
            f"{paths['state']}:/var/lib/market-data/state",
            "--volume",
            f"{paths['capture']}:/var/lib/market-data/capture",
            "--volume",
            f"{paths['session']}:/var/lib/market-data/session",
        ]
    )
    if input_enabled:
        arguments.extend(
            [
                "--env",
                "MARKET_CAPTURE_FIXTURE_INPUT=/fixture/events.jsonl",
                "--volume",
                f"{paths['input']}:/fixture/events.jsonl:ro",
            ]
        )
    if oneshot:
        arguments.extend(["--env", "MARKET_CAPTURE_FIXTURE_ONESHOT=1"])
    if retention:
        arguments.extend(["--env", "MARKET_CAPTURE_FIXTURE_RETENTION=1"])
    if crash_point:
        arguments.extend(
            [
                "--env",
                f"MARKET_CAPTURE_FIXTURE_CRASH_POINT={crash_point}",
                "--env",
                f"MARKET_CAPTURE_FIXTURE_CRASH_SEQUENCE={crash_sequence}",
            ]
        )
    arguments.extend([image, "service", "--role", role])
    return command(arguments, label=f"run_{role}", check=check)


def inspect_account(paths: Mapping[str, Path], *, role: str, expected: int) -> dict[str, Any]:
    account = "account1" if role.endswith("account1") else "account2"
    stream = "market" if account == "account1" else "coin"
    state_path = paths["state"] / role / "capture-state.sqlite"
    connection = sqlite3.connect(state_path)
    try:
        seen = int(connection.execute("SELECT COUNT(*) FROM capture_seen").fetchone()[0])
        outbox = int(connection.execute("SELECT COUNT(*) FROM capture_outbox").fetchone()[0])
        duplicates = int(
            connection.execute(
                "SELECT COALESCE(SUM(duplicate),0) FROM capture_source_metrics"
            ).fetchone()[0]
        )
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        connection.close()
    rows: list[dict[str, Any]] = []
    for path in sorted(paths["capture"].glob("events-*.jsonl")):
        for raw in path.read_text(encoding="utf-8").splitlines():
            document = json.loads(raw)
            decode_capture_event(document, stream=stream)
            rows.append(document)
    sequences = [int(row["producer"]["capture_sequence"]) for row in rows]
    if seen != expected or len(rows) != expected or outbox or integrity != "ok":
        raise Stage4RehearsalError(f"{account}_durability_mismatch")
    if sequences != list(range(1, expected + 1)):
        raise Stage4RehearsalError(f"{account}_sequence_gap")
    health = json.loads((paths["state"] / role / "health.json").read_text(encoding="utf-8"))
    if health.get("status") != "fixture-ready" or health.get("outbox") != 0:
        raise Stage4RehearsalError(f"{account}_health_not_ready")
    return {
        "durable_events": seen,
        "duplicates": duplicates,
        "outbox": outbox,
        "sequence_gap_count": 0,
        "integrity": integrity,
        "source_count": len(health["sources"]),
    }


def run_rehearsal() -> dict[str, Any]:
    release_sha = git_release_sha()
    epoch = source_epoch()
    suffix = secrets.token_hex(5)
    image = f"market-stage4-rehearsal:{release_sha[:12]}-{suffix}"
    owner_name = f"market-stage4-owner-{suffix}"
    image_id = ""
    temporary_root: Path | None = None
    cleanup = {
        "container_removed": False,
        "image_removed": False,
        "temporary_root_removed": False,
    }
    try:
        image_id, build_seconds = build_image(image, release_sha, epoch)
        telethon_version = command(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--entrypoint",
                "python",
                image,
                "-c",
                "import telethon; print(telethon.__version__)",
            ],
            label="telethon_version",
        ).stdout.strip()
        if telethon_version != "1.44.0":
            raise Stage4RehearsalError("telethon_version_mismatch")

        temporary_root = Path(tempfile.mkdtemp(prefix="market-stage4-"))
        os.chown(temporary_root, 10001, 10001)
        os.chmod(temporary_root, 0o700)
        now = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
        documents = fixture_documents(now)
        account1 = prepare_account(temporary_root, "account1", documents["account1"])
        account2 = prepare_account(temporary_root, "account2", documents["account2"])

        crashed = run_capture(
            image,
            release_sha,
            role="market-capture-account1",
            paths=account1,
            now=now,
            crash_point="after_append",
            crash_sequence=3,
            check=False,
        )
        if crashed.returncode != 75:
            raise Stage4RehearsalError("after_append_crash_not_observed")
        run_capture(
            image,
            release_sha,
            role="market-capture-account1",
            paths=account1,
            now=now,
        )
        first = inspect_account(
            account1, role="market-capture-account1", expected=7
        )

        crashed = run_capture(
            image,
            release_sha,
            role="market-capture-account2",
            paths=account2,
            now=now,
            crash_point="after_stage",
            crash_sequence=2,
            check=False,
        )
        if crashed.returncode != 75:
            raise Stage4RehearsalError("after_stage_crash_not_observed")
        run_capture(
            image,
            release_sha,
            role="market-capture-account2",
            paths=account2,
            now=now,
        )
        second = inspect_account(
            account2, role="market-capture-account2", expected=6
        )

        run_capture(
            image,
            release_sha,
            role="market-capture-account1",
            paths=account1,
            now=now,
            input_enabled=False,
            oneshot=False,
            name=owner_name,
            detach=True,
        )
        time.sleep(1)
        contender = run_capture(
            image,
            release_sha,
            role="market-capture-account1",
            paths=account1,
            now=now,
            input_enabled=False,
            oneshot=True,
            check=False,
        )
        if contender.returncode != 78:
            raise Stage4RehearsalError("second_capture_owner_did_not_fail_closed")
        command(["docker", "stop", "--time", "5", owner_name], label="owner_stop")
        command(["docker", "rm", owner_name], label="owner_remove")
        cleanup["container_removed"] = True

        run_capture(
            image,
            release_sha,
            role="market-capture-account1",
            paths=account1,
            now=now,
            input_enabled=False,
            retention=True,
        )
        remaining = sum(
            len(path.read_text(encoding="utf-8").splitlines())
            for path in account1["capture"].glob("events-*.jsonl")
        )
        if remaining != 6:
            raise Stage4RehearsalError("exact_retention_gate_failed")
        if not tuple(account1["capture"].glob("retention-audit-*.jsonl")):
            raise Stage4RehearsalError("retention_audit_missing")

        return {
            "status": "pass",
            "release_sha": release_sha,
            "image": {
                "id": image_id,
                "build_seconds": round(build_seconds, 3),
                "telethon_version": telethon_version,
            },
            "account1": first,
            "account2": second,
            "crash_recovery": {
                "after_stage": "pass",
                "after_append": "pass",
                "restart_loss_count": 0,
            },
            "ownership": {"second_owner_failed_closed": True},
            "retention": {
                "expired_raw_removed": 1,
                "remaining_raw": remaining,
                "audit_present": True,
            },
            "isolation": {
                "network": "none",
                "parser_started": False,
                "live_session_used": False,
                "product_database_touched": False,
            },
            "cleanup": cleanup,
        }
    finally:
        command(["docker", "rm", "--force", owner_name], label="cleanup_owner", check=False)
        cleanup["container_removed"] = True
        if image:
            removed = command(
                ["docker", "image", "rm", "--force", image],
                label="cleanup_image",
                check=False,
            )
            cleanup["image_removed"] = removed.returncode == 0
        if temporary_root is not None and temporary_root.exists():
            shutil.rmtree(temporary_root)
        cleanup["temporary_root_removed"] = not (
            temporary_root is not None and temporary_root.exists()
        )


def main() -> int:
    try:
        result = run_rehearsal()
    except Stage4RehearsalError as exc:
        print(
            json.dumps(
                {"status": "fail", "reason_code": str(exc)},
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1
    if not all(result["cleanup"].values()):
        print(
            json.dumps(
                {"status": "fail", "reason_code": "stage4_cleanup_incomplete"},
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
