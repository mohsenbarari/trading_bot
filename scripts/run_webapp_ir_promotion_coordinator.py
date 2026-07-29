#!/usr/bin/env python3
"""Run the fixed local WA-IR promotion sequence exactly once.

The coordinator has no SSH, Object Storage, or arbitrary-command interface.
It serially invokes only the three pinned scripts from the exact 2c08 release:
the Writer Witness promotion watch, the local Nginx listener gate, and the
route bridge with the listener receipt produced by that gate.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from typing import Any, Callable, Sequence


RELEASE_SHA = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
RELEASE_ROOT = Path(f"/srv/trading-bot-three-site/releases/{RELEASE_SHA}")
PYTHON = Path("/usr/bin/python3")
SCHEMA = "gold-trade-wa-ir-promotion-coordinator-v1"
MAX_CONFIG_BYTES = 32 * 1024
PATH_RE = re.compile(r"^/[A-Za-z0-9._/-]+$")
CONFIG_KEYS = frozenset(
    {
        "schema",
        "writer_agent_config",
        "restore_receipt",
        "active_snapshot",
        "proof_directory",
        "listener_config",
        "listener_receipt",
        "route_token_file",
        "route_audit_log",
        "poll_seconds",
    }
)


class PromotionCoordinatorError(RuntimeError):
    """Raised when a promotion stage cannot safely proceed."""


CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class CoordinatorConfig:
    writer_agent_config: Path
    restore_receipt: Path
    active_snapshot: Path
    proof_directory: Path
    listener_config: Path
    listener_receipt: Path
    route_token_file: Path
    route_audit_log: Path
    poll_seconds: int


def _require_root() -> None:
    if os.geteuid() != 0:
        raise PromotionCoordinatorError("this command must run as root")


def _safe_path(value: object, *, label: str) -> Path:
    if not isinstance(value, str) or not PATH_RE.fullmatch(value):
        raise PromotionCoordinatorError(f"{label} must be a safe absolute path")
    return Path(value)


def _root_owned_directory(path: Path, *, label: str, private: bool) -> Path:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise PromotionCoordinatorError(f"{label} does not exist") from exc
    disallowed = 0o077 if private else 0o022
    if (
        not path.is_absolute()
        or not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_mode & disallowed
        or path.resolve(strict=True) != path
    ):
        qualifier = "root-only" if private else "root-owned and not group/world writable"
        raise PromotionCoordinatorError(f"{label} must be an absolute {qualifier} directory")
    return path


def _read_root_file(
    path: Path,
    *,
    label: str,
    maximum: int = MAX_CONFIG_BYTES,
    private: bool,
) -> bytes:
    try:
        before = path.lstat()
    except OSError as exc:
        raise PromotionCoordinatorError(f"{label} does not exist") from exc
    disallowed = 0o077 if private else 0o022
    if (
        not path.is_absolute()
        or not stat.S_ISREG(before.st_mode)
        or stat.S_ISLNK(before.st_mode)
        or before.st_uid != 0
        or before.st_mode & disallowed
        or before.st_nlink != 1
        or before.st_size > maximum
    ):
        qualifier = "root-only" if private else "root-owned and not group/world writable"
        raise PromotionCoordinatorError(f"{label} must be an absolute {qualifier} regular file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise PromotionCoordinatorError(f"cannot securely open {label}") from exc
    try:
        after = os.fstat(descriptor)
        if (
            not stat.S_ISREG(after.st_mode)
            or after.st_uid != 0
            or after.st_mode & disallowed
            or after.st_nlink != 1
            or after.st_size > maximum
            or after.st_ino != before.st_ino
            or after.st_dev != before.st_dev
        ):
            raise PromotionCoordinatorError(f"{label} changed while being opened")
        payload = os.read(descriptor, maximum + 1)
        if len(payload) > maximum:
            raise PromotionCoordinatorError(f"{label} exceeds its size limit")
        return payload
    finally:
        os.close(descriptor)


def _read_private_file(path: Path, *, label: str, maximum: int = MAX_CONFIG_BYTES) -> bytes:
    return _read_root_file(path, label=label, maximum=maximum, private=True)


def _require_private_regular_file(path: Path, *, label: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise PromotionCoordinatorError(f"{label} does not exist") from exc
    if (
        not path.is_absolute()
        or not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_mode & 0o077
        or metadata.st_nlink != 1
    ):
        raise PromotionCoordinatorError(f"{label} must be an absolute root-only regular file")


def _private_file_or_private_parent(path: Path, *, label: str) -> None:
    _root_owned_directory(path.parent, label=f"{label} parent", private=True)
    try:
        path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise PromotionCoordinatorError(f"cannot inspect {label}") from exc
    _require_private_regular_file(path, label=label)


def _strict_json(raw: bytes, *, label: str) -> dict[str, Any]:
    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise PromotionCoordinatorError(f"{label} contains duplicate JSON key {key}")
            result[key] = value
        return result

    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PromotionCoordinatorError(f"{label} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise PromotionCoordinatorError(f"{label} must be a JSON object")
    return payload


def load_config(path: Path) -> CoordinatorConfig:
    payload = _strict_json(_read_private_file(path, label="promotion coordinator config"), label="promotion coordinator config")
    if set(payload) != CONFIG_KEYS:
        missing = sorted(CONFIG_KEYS - set(payload))
        unexpected = sorted(set(payload) - CONFIG_KEYS)
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unexpected:
            details.append("unexpected " + ", ".join(unexpected))
        raise PromotionCoordinatorError("promotion coordinator config keys are invalid: " + "; ".join(details))
    if payload["schema"] != SCHEMA:
        raise PromotionCoordinatorError("promotion coordinator config schema is invalid")
    poll_seconds = payload["poll_seconds"]
    if isinstance(poll_seconds, bool) or not isinstance(poll_seconds, int) or not 1 <= poll_seconds <= 30:
        raise PromotionCoordinatorError("promotion coordinator poll_seconds must be between 1 and 30")
    config = CoordinatorConfig(
        writer_agent_config=_safe_path(payload["writer_agent_config"], label="writer_agent_config"),
        restore_receipt=_safe_path(payload["restore_receipt"], label="restore_receipt"),
        active_snapshot=_safe_path(payload["active_snapshot"], label="active_snapshot"),
        proof_directory=_safe_path(payload["proof_directory"], label="proof_directory"),
        listener_config=_safe_path(payload["listener_config"], label="listener_config"),
        listener_receipt=_safe_path(payload["listener_receipt"], label="listener_receipt"),
        route_token_file=_safe_path(payload["route_token_file"], label="route_token_file"),
        route_audit_log=_safe_path(payload["route_audit_log"], label="route_audit_log"),
        poll_seconds=poll_seconds,
    )
    for file_path, label in (
        (config.writer_agent_config, "writer agent config"),
        (config.restore_receipt, "snapshot restore receipt"),
        (config.active_snapshot, "active snapshot pointer"),
        (config.listener_config, "listener config"),
        (config.route_token_file, "route token file"),
    ):
        _read_private_file(file_path, label=label)
    _root_owned_directory(config.proof_directory, label="promotion proof directory", private=True)
    _private_file_or_private_parent(config.listener_receipt, label="listener receipt")
    _private_file_or_private_parent(config.route_audit_log, label="route audit log")
    return config


def _release_scripts() -> tuple[Path, Path, Path]:
    _root_owned_directory(RELEASE_ROOT, label="exact release root", private=False)
    if RELEASE_ROOT.name != RELEASE_SHA:
        raise PromotionCoordinatorError("release root is not the exact 2c08 release")
    paths = tuple(
        RELEASE_ROOT / "scripts" / name
        for name in (
            "production_writer_lease_agent.py",
            "activate_webapp_ir_promoted_listener.py",
            "route_webapp_ir_from_promotion_proof.py",
        )
    )
    for path in paths:
        _read_root_file(
            path,
            label=f"fixed in-release script {path.name}",
            maximum=1024 * 1024,
            private=False,
        )
    return paths


def _python() -> Path:
    if PYTHON != Path("/usr/bin/python3") or not os.access(PYTHON, os.X_OK):
        raise PromotionCoordinatorError("fixed python interpreter is invalid")
    return PYTHON


def _run_fixed(
    command: Sequence[str],
    *,
    timeout_seconds: float | None,
    capture_stderr: bool,
) -> subprocess.CompletedProcess[str]:
    try:
        options: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "text": True,
            "check": False,
            "env": {
                "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                "LANG": "C",
                "TZ": "UTC",
            },
        }
        if timeout_seconds is not None:
            options["timeout"] = timeout_seconds
        if capture_stderr:
            options["stderr"] = subprocess.PIPE
        return subprocess.run(
            list(command),
            **options,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PromotionCoordinatorError("cannot start fixed local promotion stage") from exc


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Run a bounded local activation or route stage."""

    return _run_fixed(command, timeout_seconds=300, capture_stderr=True)


def _run_watch(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Wait for a safe promotion without killing its persistent watcher.

    The writer agent emits wait diagnostics to stderr, which is deliberately
    inherited by systemd here instead of accumulating in a pipe.  Its single
    stdout JSON result is retained for the coordinator once promotion ends.
    """

    return _run_fixed(command, timeout_seconds=None, capture_stderr=False)


def _last_json(result: subprocess.CompletedProcess[str], *, stage: str) -> dict[str, Any]:
    if result.returncode != 0:
        raise PromotionCoordinatorError(f"{stage} exited with {result.returncode}")
    for line in reversed(result.stdout.splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise PromotionCoordinatorError(f"{stage} did not return a JSON object")


def _run_stage(
    command_runner: CommandRunner,
    command: Sequence[str],
    *,
    stage: str,
) -> dict[str, Any]:
    try:
        result = command_runner(command)
    except (OSError, subprocess.SubprocessError) as exc:
        raise PromotionCoordinatorError(f"cannot start {stage}") from exc
    return _last_json(result, stage=stage)


def run_coordinator(
    config_path: Path,
    *,
    apply: bool,
    command_runner: CommandRunner | None = None,
    watch_command_runner: CommandRunner | None = None,
) -> dict[str, Any]:
    config = load_config(config_path)
    writer_agent, listener, router = _release_scripts()
    python = _python()
    stage_runner = command_runner or _run
    watch_runner = watch_command_runner or (_run_watch if command_runner is None else stage_runner)
    status: dict[str, Any] = {
        "schema": SCHEMA,
        "status": "planned",
        "external_route_changed": False,
        "stages": {},
    }
    if not apply:
        return status

    promotion = _run_stage(
        watch_runner,
        (
            str(python),
            str(writer_agent),
            "--config",
            str(config.writer_agent_config),
            "promote-watch",
            "--restore-receipt",
            str(config.restore_receipt),
            "--active-snapshot",
            str(config.active_snapshot),
            "--proof-directory",
            str(config.proof_directory),
            "--poll-seconds",
            str(config.poll_seconds),
        ),
        stage="promote-watch",
    )
    if promotion.get("status") not in {"activated", "already_activated"} or promotion.get("site") != "webapp_ir":
        raise PromotionCoordinatorError("promote-watch did not activate WebApp-IR")
    status["stages"]["promote_watch"] = promotion

    listener_result = _run_stage(
        stage_runner,
        (str(python), str(listener), "--config", str(config.listener_config), "--apply", "--json"),
        stage="listener activation",
    )
    if (
        listener_result.get("status") != "reloaded"
        or listener_result.get("external_route_changed") is not False
        or listener_result.get("receipt_path") != str(config.listener_receipt)
    ):
        raise PromotionCoordinatorError("listener activation did not produce the required local receipt")
    _read_private_file(config.listener_receipt, label="listener receipt")
    status["stages"]["listener_activation"] = listener_result

    route_result = _run_stage(
        stage_runner,
        (
            str(python),
            str(router),
            "--proof-directory",
            str(config.proof_directory),
            "--token-file",
            str(config.route_token_file),
            "--audit-log",
            str(config.route_audit_log),
            "--listener-receipt",
            str(config.listener_receipt),
            "--apply",
        ),
        stage="route bridge",
    )
    status["stages"]["route"] = route_result
    status["status"] = "completed"
    status["external_route_changed"] = route_result.get("applied") is True
    return status


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path, help="Root-only closed coordinator JSON config.")
    parser.add_argument("--apply", action="store_true", help="Required to run the three local stages.")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        _require_root()
        result = run_coordinator(args.config, apply=args.apply)
    except PromotionCoordinatorError as exc:
        print(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "status": "blocked",
                    "error_class": type(exc).__name__,
                    "reason": str(exc),
                    "external_route_changed": "unknown" if args.apply else False,
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    else:
        print(result["status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
