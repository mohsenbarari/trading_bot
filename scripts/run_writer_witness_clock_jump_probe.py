#!/usr/bin/env python3
"""Exercise the production database-clock path on a fake-timed disposable PostgreSQL."""

from __future__ import annotations

import argparse
import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import time
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.writer_witness_control import (
    WriterWitnessError,
    load_witness_snapshot,
    transition_witness_state,
)
from writer_witness_app import database_clock


EXPECTED_PORT = 55440
TAG_PATTERN = re.compile(r"wwm_[0-9a-f]{12}\Z")
ALLOWED_PHASES = ("phase-one", "phase-two")
OFFSET_TOLERANCE_SECONDS = 5.0
TRUSTED_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"


class ClockProbeError(RuntimeError):
    """The isolated clock boundary cannot be proven safely."""


def require_isolated_runtime() -> None:
    if not (
        sys.flags.isolated
        and sys.flags.ignore_environment
        and sys.flags.dont_write_bytecode
        and getattr(sys.flags, "safe_path", False)
        and sys.flags.utf8_mode == 1
        and sys.pycache_prefix == "/dev/null"
    ):
        raise ClockProbeError("clock probe Python startup is not isolated")
    expected_prefix = Path("/opt/trading-bot-witness/active/venv").resolve(strict=True)
    if Path(sys.prefix).resolve(strict=True) != expected_prefix:
        raise ClockProbeError("clock probe is outside the active attested venv")
    allowed = {"PATH": TRUSTED_PATH}
    if os.environ.get("LC_CTYPE") == "C.UTF-8":
        allowed["LC_CTYPE"] = "C.UTF-8"
    if dict(os.environ) != allowed:
        raise ClockProbeError("clock probe environment is not clean")


def private_key_base64() -> str:
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return base64.b64encode(raw).decode("ascii")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _validate_real_directory(path: Path, *, expected_root: Path) -> Path:
    try:
        resolved = path.resolve(strict=True)
        metadata = path.lstat()
    except OSError as exc:
        raise ClockProbeError(f"clock probe directory is unavailable: {path}") from exc
    if (
        resolved != path
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise ClockProbeError(f"clock probe directory is not private and real: {path}")
    try:
        path.relative_to(expected_root)
    except ValueError as exc:
        raise ClockProbeError(f"clock probe directory escapes its tagged root: {path}") from exc
    return path


def _validate_control_file(path: Path, *, expected_root: Path) -> Path:
    try:
        resolved = path.resolve(strict=True)
        metadata = path.lstat()
    except OSError as exc:
        raise ClockProbeError("faketime control file is unavailable") from exc
    if (
        resolved != path
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise ClockProbeError("faketime control file is not one owner-only regular file")
    if path.parent != expected_root:
        raise ClockProbeError("faketime control file escapes its tagged root")
    return path


def _validate_library(path: Path, expected_sha256: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ClockProbeError("libfaketime SHA-256 is invalid")
    try:
        resolved = path.resolve(strict=True)
        metadata = resolved.lstat()
    except OSError as exc:
        raise ClockProbeError("libfaketime is unavailable") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise ClockProbeError("libfaketime is not a root-controlled regular file")
    if not str(resolved).startswith(("/usr/lib/", "/lib/")):
        raise ClockProbeError("libfaketime is outside the system library roots")
    observed = hashlib.sha256(resolved.read_bytes()).hexdigest()
    if observed != expected_sha256:
        raise ClockProbeError("libfaketime hash differs from the pinned host evidence")
    return resolved


def _write_clock_control(path: Path, value: str) -> None:
    if value not in {"+0", "-60s", "+31s"}:
        raise ClockProbeError("unsupported isolated clock offset")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".faketime.rc.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        raw = (value + "\n").encode("ascii")
        if os.write(descriptor, raw) != len(raw):
            raise ClockProbeError("short faketime control write")
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _database_url(socket_dir: Path) -> URL:
    return URL.create(
        "postgresql+asyncpg",
        username="postgres",
        database="postgres",
        query={"host": str(socket_dir), "port": str(EXPECTED_PORT)},
    )


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


async def _clock_evidence(session, expected_offset: int) -> dict[str, Any]:
    deadline = time.monotonic() + 4.0
    while True:
        host_before = datetime.now(timezone.utc)
        sql_clock = (
            await session.execute(text("SELECT clock_timestamp()"))
        ).scalar_one()
        app_clock = await database_clock(session)
        host_after = datetime.now(timezone.utc)
        midpoint = host_before + (host_after - host_before) / 2
        observed_offset = (sql_clock.astimezone(timezone.utc) - midpoint).total_seconds()
        app_delta = abs(
            (app_clock.astimezone(timezone.utc) - sql_clock.astimezone(timezone.utc)).total_seconds()
        )
        if (
            abs(observed_offset - expected_offset) <= OFFSET_TOLERANCE_SECONDS
            and app_delta <= 1.0
        ):
            return {
                "expected_offset_seconds": expected_offset,
                "observed_offset_seconds": round(observed_offset, 6),
                "host_clock": _iso(midpoint),
                "sql_clock_timestamp": _iso(sql_clock),
                "service_database_clock": _iso(app_clock),
                "monotonic_ns": time.monotonic_ns(),
            }
        if time.monotonic() >= deadline:
            raise ClockProbeError(
                f"disposable PostgreSQL clock offset did not reach {expected_offset}: {observed_offset}"
            )
        await asyncio.sleep(0.05)


async def _rejected_transition(sessions, **kwargs: Any) -> str:
    async with sessions() as session:
        try:
            await transition_witness_state(session, **kwargs)
        except WriterWitnessError as exc:
            await session.rollback()
            return str(exc)
        await session.rollback()
    raise ClockProbeError("unsafe isolated clock transition unexpectedly succeeded")


async def _isolation_evidence(
    session,
    *,
    data_dir: Path,
    socket_dir: Path,
    postmaster_pid: int,
    faketime_library: Path,
    production_system_identifier: str,
) -> dict[str, Any]:
    data_directory = str((await session.execute(text("SHOW data_directory"))).scalar_one())
    port = int((await session.execute(text("SHOW port"))).scalar_one())
    listen_addresses = str(
        (await session.execute(text("SHOW listen_addresses"))).scalar_one()
    )
    address_row = (
        await session.execute(
            text("SELECT inet_server_addr()::text, inet_client_addr()::text")
        )
    ).one()
    system_identifier = str(
        (
            await session.execute(
                text("SELECT system_identifier::text FROM pg_control_system()")
            )
        ).scalar_one()
    )
    if (
        data_directory != str(data_dir)
        or port != EXPECTED_PORT
        or listen_addresses != ""
        or address_row[0] is not None
        or address_row[1] is not None
        or system_identifier == production_system_identifier
    ):
        raise ClockProbeError("disposable PostgreSQL failed its socket/data identity boundary")
    maps_path = Path(f"/proc/{postmaster_pid}/maps")
    try:
        mappings = maps_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ClockProbeError("cannot attest the disposable PostgreSQL library map") from exc
    if str(faketime_library) not in mappings:
        raise ClockProbeError("disposable PostgreSQL did not load the pinned libfaketime")
    if str(socket_dir) not in str(_database_url(socket_dir)):
        raise ClockProbeError("clock probe did not construct a Unix-socket database URL")
    return {
        "data_directory": data_directory,
        "port": port,
        "listen_addresses": listen_addresses,
        "unix_socket_transport": True,
        "system_identifier": system_identifier,
        "libfaketime_loaded": True,
        "postmaster_pid": postmaster_pid,
    }


async def run_probe(
    *,
    phase: str,
    socket_dir: Path,
    data_dir: Path,
    control_file: Path,
    postmaster_pid: int,
    faketime_library: Path,
    production_system_identifier: str,
) -> dict[str, Any]:
    engine = create_async_engine(_database_url(socket_dir), pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    key = private_key_base64()
    try:
        async with sessions() as session:
            isolation = await _isolation_evidence(
                session,
                data_dir=data_dir,
                socket_dir=socket_dir,
                postmaster_pid=postmaster_pid,
                faketime_library=faketime_library,
                production_system_identifier=production_system_identifier,
            )

        clock_phases: list[dict[str, Any]] = []
        if phase == "phase-one":
            _write_clock_control(control_file, "+0")
            async with sessions() as session:
                clock_phases.append(await _clock_evidence(session, 0))
                first = await transition_witness_state(
                    session,
                    action="acquire",
                    requester_site="webapp_fi",
                    expected_epoch=0,
                    expected_lease_id=None,
                    request_id="real-clock-acquire-fi",
                    operator="isolated-real-clock-probe",
                    reason="establish the first lease from the real database clock path",
                    private_key_base64=key,
                    lease_duration_seconds=30,
                )
                await session.commit()
            _write_clock_control(control_file, "-60s")
            async with sessions() as session:
                clock_phases.append(await _clock_evidence(session, -60))
            rejection = await _rejected_transition(
                sessions,
                action="acquire",
                requester_site="webapp_ir",
                expected_epoch=1,
                expected_lease_id=first.state.lease_id,
                request_id="real-clock-backward-ir-before-restart",
                operator="isolated-real-clock-probe",
                reason="prove backward database time cannot steal a live lease",
                private_key_base64=key,
                lease_duration_seconds=30,
            )
            if "live witness lease" not in rejection:
                raise ClockProbeError("backward-clock rejection was not caused by the live lease")
            return {
                "status": "phase_one_passed",
                "production_clock_path": "SELECT clock_timestamp()",
                "synthetic_time_argument_used": False,
                "isolation": isolation,
                "clock_phases": clock_phases,
                "writer_epoch": first.state.writer_epoch,
                "holder_site": first.state.holder_site,
                "backward_clock_steal_rejected_before_restart": True,
            }

        async with sessions() as session:
            clock_phases.append(await _clock_evidence(session, -60))
            before = await load_witness_snapshot(session)
        if before.writer_epoch != 1 or before.holder_site != "webapp_fi":
            raise ClockProbeError("disposable lease did not persist across the clocked restart")
        second_backward_rejection = await _rejected_transition(
            sessions,
            action="acquire",
            requester_site="webapp_ir",
            expected_epoch=1,
            expected_lease_id=before.lease_id,
            request_id="real-clock-backward-ir-after-restart",
            operator="isolated-real-clock-probe",
            reason="re-prove live-lease rejection after PostgreSQL restart",
            private_key_base64=key,
            lease_duration_seconds=30,
        )
        if "live witness lease" not in second_backward_rejection:
            raise ClockProbeError("post-restart backward-clock rejection was unsafe")

        _write_clock_control(control_file, "+31s")
        async with sessions() as session:
            clock_phases.append(await _clock_evidence(session, 31))
            second = await transition_witness_state(
                session,
                action="acquire",
                requester_site="webapp_ir",
                expected_epoch=1,
                expected_lease_id=before.lease_id,
                request_id="real-clock-forward-ir",
                operator="isolated-real-clock-probe",
                reason="acquire only after real database clock expiry",
                private_key_base64=key,
                lease_duration_seconds=30,
            )
            await session.commit()

        _write_clock_control(control_file, "-60s")
        async with sessions() as session:
            clock_phases.append(await _clock_evidence(session, -60))
        stale_rejection = await _rejected_transition(
            sessions,
            action="renew",
            requester_site="webapp_fi",
            expected_epoch=1,
            expected_lease_id=before.lease_id,
            request_id="real-clock-stale-renew-fi",
            operator="isolated-real-clock-probe",
            reason="prove backward database time cannot revive a stale epoch",
            private_key_base64=key,
            lease_duration_seconds=30,
        )
        if "stale witness epoch" not in stale_rejection:
            raise ClockProbeError("old epoch was not rejected after the backward jump")

        _write_clock_control(control_file, "+0")
        async with sessions() as session:
            clock_phases.append(await _clock_evidence(session, 0))
            final = await load_witness_snapshot(session)
        if final.writer_epoch != 2 or final.holder_site != "webapp_ir":
            raise ClockProbeError("real clock probe ended with an unsafe writer state")
        return {
            "status": "passed",
            "scenario": "isolated-postgresql-real-database-clock-jump",
            "production_clock_path": "SELECT clock_timestamp()",
            "synthetic_time_argument_used": False,
            "isolation": isolation,
            "clock_phases": clock_phases,
            "backward_clock_steal_rejected": True,
            "postgres_restart_persistence_proved": True,
            "forward_expiry_acquire_epoch": second.state.writer_epoch,
            "old_epoch_revival_rejected": True,
            "final_holder_site": final.holder_site,
            "final_writer_epoch": final.writer_epoch,
        }
    finally:
        await engine.dispose()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=ALLOWED_PHASES, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--socket-dir", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--clock-control-file", type=Path, required=True)
    parser.add_argument("--postmaster-pid", type=int, required=True)
    parser.add_argument("--faketime-library", type=Path, required=True)
    parser.add_argument("--faketime-library-sha256", required=True)
    parser.add_argument("--production-system-identifier", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    require_isolated_runtime()
    args = parse_args(argv)
    if os.geteuid() == 0:
        raise ClockProbeError("clock probe must run as the isolated PostgreSQL OS user")
    if not TAG_PATTERN.fullmatch(args.tag):
        raise ClockProbeError("unsafe Matrix clock tag")
    tagged_root = Path(f"/run/{args.tag}-clock")
    _validate_real_directory(tagged_root, expected_root=Path("/run"))
    socket_dir = _validate_real_directory(args.socket_dir, expected_root=tagged_root)
    data_dir = _validate_real_directory(args.data_dir, expected_root=tagged_root)
    control_file = _validate_control_file(
        args.clock_control_file, expected_root=tagged_root
    )
    faketime_library = _validate_library(
        args.faketime_library, args.faketime_library_sha256
    )
    if args.postmaster_pid <= 1 or not args.production_system_identifier.isdigit():
        raise ClockProbeError("clock probe process/system identity is invalid")
    result = asyncio.run(
        run_probe(
            phase=args.phase,
            socket_dir=socket_dir,
            data_dir=data_dir,
            control_file=control_file,
            postmaster_pid=args.postmaster_pid,
            faketime_library=faketime_library,
            production_system_identifier=args.production_system_identifier,
        )
    )
    result["tag"] = args.tag
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ClockProbeError as exc:
        raise SystemExit(f"writer witness real clock probe failed: {exc}") from exc
