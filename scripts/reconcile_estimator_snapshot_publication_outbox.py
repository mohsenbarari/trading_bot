#!/usr/bin/env python3
"""Close evidenced legacy estimator publication markers crash-safely.

The operator is not a replay path.  Its durable journal binds one release,
database inode, preimage and receipt target and advances monotonically through
``PREPARED -> DB_COMMITTED -> RECEIPT_COMMITTED``.  Recovery proves the exact
preimage rows against the live database and never guesses, waives, deletes or
reapplies a committed mutation.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
from typing import Iterator, Sequence
from urllib.parse import quote


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.market_intelligence.estimator_snapshot_receiver import (  # noqa: E402
    EstimatorSnapshotReceiverError,
    inspect_snapshot_publication_reconciliation_recovery,
    reconcile_snapshot_publication_outbox,
)


PRODUCTION_CONFIRMATION = (
    "RECONCILE PRODUCTION ESTIMATOR SNAPSHOT PUBLICATION OUTBOX"
)
JOURNAL_SCHEMA = "estimator_snapshot_publication_reconciliation_journal/1.0"
JOURNAL_SCHEMA_VERSION = 1
JOURNAL_STATES = ("PREPARED", "DB_COMMITTED", "RECEIPT_COMMITTED")


class SnapshotPublicationReconciliationError(RuntimeError):
    """Content-free operator contract failure."""


def _canonical_bytes(document: dict[str, object]) -> bytes:
    return (
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _path_sha256(path: Path) -> str:
    return _sha256(os.fsencode(str(path)))


def _validate_absolute(path: Path, *, label: str) -> None:
    if (
        not path.is_absolute()
        or path.name in {"", ".", ".."}
        or any(part in {".", ".."} for part in path.parts[1:])
    ):
        raise SnapshotPublicationReconciliationError(f"{label}_invalid")


def _directory_flags() -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return flags


def _open_parent(
    path: Path,
    *,
    label: str,
    create: bool,
) -> tuple[int, str]:
    """Walk every directory component with openat/O_NOFOLLOW."""

    _validate_absolute(path, label=label)
    descriptor = os.open("/", _directory_flags())
    try:
        for component in path.parent.parts[1:]:
            try:
                child = os.open(component, _directory_flags(), dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise SnapshotPublicationReconciliationError(
                        f"{label}_parent_invalid"
                    )
                try:
                    os.mkdir(component, 0o700, dir_fd=descriptor)
                    child = os.open(component, _directory_flags(), dir_fd=descriptor)
                except OSError as exc:
                    raise SnapshotPublicationReconciliationError(
                        f"{label}_parent_invalid"
                    ) from exc
            except OSError as exc:
                raise SnapshotPublicationReconciliationError(
                    f"{label}_parent_invalid"
                ) from exc
            os.close(descriptor)
            descriptor = child
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid not in {0, os.geteuid()}
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise SnapshotPublicationReconciliationError(
                f"{label}_parent_invalid"
            )
        return descriptor, path.name
    except BaseException:
        os.close(descriptor)
        raise


def _existing_file_descriptor(
    path: Path,
    *,
    label: str,
    writable: bool = False,
) -> tuple[int, int, str, os.stat_result]:
    parent, name = _open_parent(path, label=label, create=False)
    flags = os.O_RDWR if writable else os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(name, flags, dir_fd=parent)
        opened = os.fstat(descriptor)
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid not in {0, os.geteuid()}
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) & 0o022
            or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise SnapshotPublicationReconciliationError(f"{label}_invalid")
        return descriptor, parent, name, opened
    except OSError as exc:
        os.close(parent)
        raise SnapshotPublicationReconciliationError(f"{label}_invalid") from exc
    except BaseException:
        os.close(parent)
        raise


def _read_descriptor(descriptor: int, *, maximum: int = 16 * 1024 * 1024) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    payload = bytearray()
    while True:
        chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - len(payload)))
        if not chunk:
            return bytes(payload)
        payload.extend(chunk)
        if len(payload) > maximum:
            raise SnapshotPublicationReconciliationError("secure_file_too_large")


def _read_secure(path: Path, *, label: str) -> bytes:
    descriptor, parent, _name, opened = _existing_file_descriptor(
        path, label=label
    )
    try:
        if stat.S_IMODE(opened.st_mode) != 0o600:
            raise SnapshotPublicationReconciliationError(f"{label}_mode_invalid")
        return _read_descriptor(descriptor)
    finally:
        os.close(descriptor)
        os.close(parent)


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise SnapshotPublicationReconciliationError("secure_file_short_write")
        view = view[written:]
    os.fsync(descriptor)


def _create_exact_file(path: Path, payload: bytes, *, label: str) -> None:
    parent, name = _open_parent(path, label=label, create=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(name, flags, 0o600, dir_fd=parent)
        try:
            os.fchmod(descriptor, 0o600)
            _write_all(descriptor, payload)
            opened = os.fstat(descriptor)
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (
                opened.st_nlink != 1
                or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
            ):
                raise SnapshotPublicationReconciliationError(f"{label}_changed")
        finally:
            os.close(descriptor)
        os.fsync(parent)
    except OSError as exc:
        raise SnapshotPublicationReconciliationError(f"{label}_invalid") from exc
    finally:
        os.close(parent)


def _replace_exact_file(
    path: Path,
    payload: bytes,
    *,
    label: str,
    expected_previous_sha256: str,
) -> None:
    parent, name = _open_parent(path, label=label, create=False)
    temporary = f".{name}.next.{os.getpid()}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    temp_descriptor: int | None = None
    try:
        current_descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent,
        )
        try:
            current = _read_descriptor(current_descriptor)
        finally:
            os.close(current_descriptor)
        if _sha256(current) != expected_previous_sha256:
            raise SnapshotPublicationReconciliationError(f"{label}_changed")
        temp_descriptor = os.open(temporary, flags, 0o600, dir_fd=parent)
        os.fchmod(temp_descriptor, 0o600)
        _write_all(temp_descriptor, payload)
        os.close(temp_descriptor)
        temp_descriptor = None
        # Recheck immediately before the atomic rename.
        current_descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent,
        )
        try:
            current = _read_descriptor(current_descriptor)
        finally:
            os.close(current_descriptor)
        if _sha256(current) != expected_previous_sha256:
            raise SnapshotPublicationReconciliationError(f"{label}_changed")
        os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    except OSError as exc:
        raise SnapshotPublicationReconciliationError(f"{label}_invalid") from exc
    finally:
        if temp_descriptor is not None:
            os.close(temp_descriptor)
        # A handled failure may leave only the private next-state file.  It is
        # not a receipt or waiver and is safe to remove before returning.
        try:
            os.unlink(temporary, dir_fd=parent)
        except OSError:
            pass
        os.close(parent)


def _schema_identity(connection: sqlite3.Connection) -> dict[str, object]:
    rows = [
        tuple(row)
        for row in connection.execute(
            "SELECT type,name,tbl_name,COALESCE(sql,'') FROM sqlite_master "
            "ORDER BY type,name,tbl_name"
        ).fetchall()
    ]
    return {
        "schema_sha256": _sha256(
            json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ),
        "user_version": int(connection.execute("PRAGMA user_version").fetchone()[0]),
        "schema_version": int(
            connection.execute("PRAGMA schema_version").fetchone()[0]
        ),
    }


@contextmanager
def _connect_existing(
    path: Path,
) -> Iterator[tuple[sqlite3.Connection, dict[str, object]]]:
    descriptor, parent, name, opened = _existing_file_descriptor(
        path, label="receiver_database", writable=True
    )
    connection: sqlite3.Connection | None = None
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise SnapshotPublicationReconciliationError(
                "receiver_database_operator_locked"
            ) from exc
        proc_path = f"/proc/self/fd/{parent}/{quote(name, safe='')}"
        connection = sqlite3.connect(
            f"file:{proc_path}?mode=rw",
            uri=True,
            timeout=30,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        required = {
            "estimator_snapshot_receipts",
            "estimator_snapshot_publication_outbox",
        }
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (
            not required.issubset(tables)
            or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise SnapshotPublicationReconciliationError(
                "receiver_database_contract_invalid"
            )
        identity = {
            "path_sha256": _path_sha256(path),
            "device": int(opened.st_dev),
            "inode": int(opened.st_ino),
            "owner": int(opened.st_uid),
            "mode": f"{stat.S_IMODE(opened.st_mode):04o}",
            "nlink": int(opened.st_nlink),
            **_schema_identity(connection),
        }
        yield connection, identity
        final = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (final.st_dev, final.st_ino) != (opened.st_dev, opened.st_ino):
            raise SnapshotPublicationReconciliationError(
                "receiver_database_changed"
            )
    except sqlite3.Error as exc:
        raise SnapshotPublicationReconciliationError(
            "receiver_database_invalid"
        ) from exc
    finally:
        if connection is not None:
            connection.close()
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(descriptor)
        os.close(parent)


def _lanes(value: str) -> tuple[str, ...]:
    if value == "BOTH":
        return ("PRIVATE_PRIMARY", "PRIVATE_SHADOW")
    return (value,)


def _receipt_document(report: dict[str, object]) -> dict[str, object]:
    return {
        **report,
        "action": "RECONCILE_PUBLICATION_OUTBOX",
        "database_path_disclosed": False,
        "snapshot_path_disclosed": False,
        "publication_event_path_disclosed": False,
        "raw_payload_disclosed": False,
    }


def _completed_report(report: dict[str, object]) -> dict[str, object]:
    repaired = int(report["pending_before"])
    return {
        **report,
        "status": "APPLIED" if repaired else "ALREADY_RECONCILED",
        "repaired_count": repaired,
        "pending_after": 0,
    }


def _binding(args: argparse.Namespace, database_identity: dict[str, object]) -> dict[str, object]:
    return {
        "release_sha": args.expected_release_sha,
        "release_tree": args.expected_release_tree,
        "feed_modes": list(_lanes(args.lane)),
        "expected_plan_sha256": args.expected_plan_sha256,
        "database_identity": database_identity,
        "snapshot_root_sha256": _path_sha256(args.snapshot_root),
        "publication_events_path_sha256": _path_sha256(args.publication_events),
        "preimage_backup_path_sha256": _path_sha256(args.preimage_backup),
        "receipt_path_sha256": _path_sha256(args.receipt),
        "journal_path_sha256": _path_sha256(args.journal),
    }


def _preimage_identity(report: dict[str, object]) -> dict[str, object]:
    keys = (
        "preimage_backup_sha256",
        "preimage_backup_size_bytes",
        "preimage_backup_integrity",
        "preimage_backup_mode",
        "preimage_backup_device",
        "preimage_backup_inode",
        "preimage_plan_sha256",
    )
    try:
        return {key: report[key] for key in keys}
    except KeyError as exc:
        raise SnapshotPublicationReconciliationError(
            "journal_preimage_contract_invalid"
        ) from exc


def _verify_preimage_file(path: Path, expected: object) -> None:
    if not isinstance(expected, dict):
        raise SnapshotPublicationReconciliationError(
            "journal_preimage_contract_invalid"
        )
    descriptor, parent, _name, opened = _existing_file_descriptor(
        path, label="preimage_backup"
    )
    try:
        if stat.S_IMODE(opened.st_mode) != 0o600:
            raise SnapshotPublicationReconciliationError(
                "journal_preimage_mismatch"
            )
        payload = _read_descriptor(descriptor, maximum=1024 * 1024 * 1024)
    finally:
        os.close(descriptor)
        os.close(parent)
    actual = {
        "preimage_backup_sha256": _sha256(payload),
        "preimage_backup_size_bytes": len(payload),
        "preimage_backup_device": int(opened.st_dev),
        "preimage_backup_inode": int(opened.st_ino),
    }
    if any(expected.get(key) != value for key, value in actual.items()):
        raise SnapshotPublicationReconciliationError("journal_preimage_mismatch")


def _journal_document(
    *,
    state: str,
    binding: dict[str, object],
    preimage: dict[str, object],
    result: dict[str, object] | None,
    receipt_sha256: str | None,
) -> dict[str, object]:
    return {
        "schema": JOURNAL_SCHEMA,
        "schema_version": JOURNAL_SCHEMA_VERSION,
        "state": state,
        "binding": binding,
        "preimage": preimage,
        "result": result,
        "receipt_sha256": receipt_sha256,
        "secrets_disclosed": False,
        "key_material_disclosed": False,
        "market_values_disclosed": False,
    }


def _load_journal(path: Path) -> tuple[dict[str, object], str]:
    payload = _read_secure(path, label="journal")
    try:
        document = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SnapshotPublicationReconciliationError("journal_malformed") from exc
    if (
        not isinstance(document, dict)
        or set(document)
        != {
            "schema",
            "schema_version",
            "state",
            "binding",
            "preimage",
            "result",
            "receipt_sha256",
            "secrets_disclosed",
            "key_material_disclosed",
            "market_values_disclosed",
        }
        or document.get("schema") != JOURNAL_SCHEMA
        or document.get("schema_version") != JOURNAL_SCHEMA_VERSION
        or document.get("state") not in JOURNAL_STATES
        or document.get("secrets_disclosed") is not False
        or document.get("key_material_disclosed") is not False
        or document.get("market_values_disclosed") is not False
    ):
        raise SnapshotPublicationReconciliationError(
            "journal_schema_or_compatibility_invalid"
        )
    state = str(document["state"])
    if (
        state == "PREPARED"
        and (document.get("result") is not None or document.get("receipt_sha256") is not None)
    ) or (
        state == "DB_COMMITTED"
        and (
            not isinstance(document.get("result"), dict)
            or document.get("receipt_sha256") is not None
        )
    ) or (
        state == "RECEIPT_COMMITTED"
        and (
            not isinstance(document.get("result"), dict)
            or not isinstance(document.get("receipt_sha256"), str)
            or len(str(document.get("receipt_sha256"))) != 64
        )
    ):
        raise SnapshotPublicationReconciliationError("journal_state_invalid")
    return document, _sha256(payload)


def _write_new_journal(path: Path, document: dict[str, object]) -> str:
    payload = _canonical_bytes(document)
    _create_exact_file(path, payload, label="journal")
    return _sha256(payload)


def _advance_journal(
    path: Path,
    document: dict[str, object],
    *,
    previous_sha256: str,
) -> str:
    payload = _canonical_bytes(document)
    _replace_exact_file(
        path,
        payload,
        label="journal",
        expected_previous_sha256=previous_sha256,
    )
    return _sha256(payload)


def _write_or_verify_receipt(path: Path, document: dict[str, object]) -> str:
    payload = _canonical_bytes(document)
    try:
        existing = _read_secure(path, label="receipt")
    except SnapshotPublicationReconciliationError as exc:
        if str(exc) not in {"receipt_parent_invalid", "receipt_invalid"}:
            raise
        _create_exact_file(path, payload, label="receipt")
        return _sha256(payload)
    if existing != payload:
        raise SnapshotPublicationReconciliationError("receipt_existing_mismatch")
    return _sha256(existing)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "apply"):
        command = commands.add_parser(name)
        command.add_argument("--database", type=Path, required=True)
        command.add_argument("--snapshot-root", type=Path, required=True)
        command.add_argument("--publication-events", type=Path, required=True)
        command.add_argument(
            "--lane",
            choices=("BOTH", "PRIVATE_SHADOW", "PRIVATE_PRIMARY"),
            default="BOTH",
        )
        command.add_argument("--expected-release-sha", required=True)
        command.add_argument("--expected-release-tree", required=True)
        command.add_argument("--receipt", type=Path, required=True)
        if name == "apply":
            command.add_argument("--expected-plan-sha256", required=True)
            command.add_argument("--preimage-backup", type=Path, required=True)
            command.add_argument("--journal", type=Path)
            command.add_argument("--confirm", required=True)
    return parser


def _validate_operator_paths(args: argparse.Namespace) -> None:
    for name, label in (
        ("database", "receiver_database"),
        ("snapshot_root", "snapshot_root"),
        ("publication_events", "publication_events_path"),
        ("receipt", "receipt_target"),
    ):
        _validate_absolute(getattr(args, name), label=label)
    if args.command == "apply":
        _validate_absolute(args.preimage_backup, label="preimage_backup")
        if args.journal is None:
            args.journal = Path(str(args.receipt) + ".journal.json")
        _validate_absolute(args.journal, label="journal")
        if len({args.receipt, args.preimage_backup, args.journal}) != 3:
            raise SnapshotPublicationReconciliationError(
                "operator_artifact_paths_not_distinct"
            )


def _finish_receipt(
    args: argparse.Namespace,
    journal: dict[str, object],
    journal_sha: str,
) -> tuple[dict[str, object], dict[str, object], str]:
    result = journal.get("result")
    if not isinstance(result, dict):
        raise SnapshotPublicationReconciliationError("journal_result_invalid")
    receipt_sha = _write_or_verify_receipt(args.receipt, result)
    if journal["state"] == "RECEIPT_COMMITTED":
        if journal.get("receipt_sha256") != receipt_sha:
            raise SnapshotPublicationReconciliationError(
                "journal_receipt_digest_mismatch"
            )
        return result, journal, journal_sha
    terminal = _journal_document(
        state="RECEIPT_COMMITTED",
        binding=journal["binding"],
        preimage=journal["preimage"],
        result=result,
        receipt_sha256=receipt_sha,
    )
    return result, terminal, _advance_journal(
        args.journal, terminal, previous_sha256=journal_sha
    )


def _run_apply(
    args: argparse.Namespace,
    connection: sqlite3.Connection,
    database_identity: dict[str, object],
) -> dict[str, object]:
    binding = _binding(args, database_identity)
    journal: dict[str, object] | None = None
    journal_sha: str | None = None
    try:
        journal, journal_sha = _load_journal(args.journal)
    except SnapshotPublicationReconciliationError as exc:
        if str(exc) not in {"journal_parent_invalid", "journal_invalid"}:
            raise

    if journal is not None:
        if journal.get("binding") != binding:
            raise SnapshotPublicationReconciliationError("journal_binding_mismatch")
        _verify_preimage_file(args.preimage_backup, journal.get("preimage"))
        recovery = inspect_snapshot_publication_reconciliation_recovery(
            connection,
            snapshot_root=args.snapshot_root,
            publication_events_path=args.publication_events,
            feed_modes=_lanes(args.lane),
            release_sha=args.expected_release_sha,
            release_tree=args.expected_release_tree,
            expected_plan_sha256=args.expected_plan_sha256,
            preimage_backup_path=args.preimage_backup,
        )
        if journal.get("preimage") != _preimage_identity(recovery):
            raise SnapshotPublicationReconciliationError(
                "journal_preimage_mismatch"
            )
        if journal["state"] in {"DB_COMMITTED", "RECEIPT_COMMITTED"}:
            if recovery["status"] != "DB_COMMITTED":
                raise SnapshotPublicationReconciliationError(
                    "journal_database_state_mismatch"
                )
            expected_result = _receipt_document(_completed_report(recovery))
            if journal.get("result") != expected_result:
                raise SnapshotPublicationReconciliationError(
                    "journal_result_mismatch"
                )
            result, _journal, _sha = _finish_receipt(args, journal, journal_sha)
            return result
        if recovery["status"] == "DB_COMMITTED":
            result = _receipt_document(_completed_report(recovery))
            journal = _journal_document(
                state="DB_COMMITTED",
                binding=binding,
                preimage=journal["preimage"],
                result=result,
                receipt_sha256=None,
            )
            journal_sha = _advance_journal(
                args.journal, journal, previous_sha256=journal_sha
            )
            result, _journal, _sha = _finish_receipt(args, journal, journal_sha)
            return result
        reuse_preimage = True
    else:
        reuse_preimage = args.preimage_backup.exists() or args.preimage_backup.is_symlink()
        if reuse_preimage:
            recovery = inspect_snapshot_publication_reconciliation_recovery(
                connection,
                snapshot_root=args.snapshot_root,
                publication_events_path=args.publication_events,
                feed_modes=_lanes(args.lane),
                release_sha=args.expected_release_sha,
                release_tree=args.expected_release_tree,
                expected_plan_sha256=args.expected_plan_sha256,
                preimage_backup_path=args.preimage_backup,
            )
            if recovery["status"] != "PENDING":
                raise SnapshotPublicationReconciliationError(
                    "journal_missing_after_database_commit"
                )

    def before_mutation(prepared_report: dict[str, object]) -> None:
        nonlocal journal, journal_sha
        prepared = _journal_document(
            state="PREPARED",
            binding=binding,
            preimage=_preimage_identity(prepared_report),
            result=None,
            receipt_sha256=None,
        )
        if journal is None:
            journal_sha = _write_new_journal(args.journal, prepared)
            journal = prepared
        elif journal != prepared:
            raise SnapshotPublicationReconciliationError(
                "journal_prepared_state_mismatch"
            )

    def after_commit(applied_report: dict[str, object]) -> None:
        nonlocal journal, journal_sha
        if journal is None or journal_sha is None:
            raise SnapshotPublicationReconciliationError(
                "journal_prepared_state_missing"
            )
        result = _receipt_document(applied_report)
        committed = _journal_document(
            state="DB_COMMITTED",
            binding=binding,
            preimage=journal["preimage"],
            result=result,
            receipt_sha256=None,
        )
        journal_sha = _advance_journal(
            args.journal, committed, previous_sha256=journal_sha
        )
        journal = committed

    report = reconcile_snapshot_publication_outbox(
        connection,
        snapshot_root=args.snapshot_root,
        publication_events_path=args.publication_events,
        feed_modes=_lanes(args.lane),
        release_sha=args.expected_release_sha,
        release_tree=args.expected_release_tree,
        apply=True,
        expected_plan_sha256=args.expected_plan_sha256,
        preimage_backup_path=args.preimage_backup,
        reuse_preimage_backup=reuse_preimage,
        before_mutation=before_mutation,
        after_commit=after_commit,
    )
    if journal is None or journal_sha is None:
        raise SnapshotPublicationReconciliationError("journal_commit_missing")
    expected_result = _receipt_document(report)
    if journal.get("result") != expected_result:
        raise SnapshotPublicationReconciliationError("journal_result_mismatch")
    result, _journal, _sha = _finish_receipt(args, journal, journal_sha)
    return result


def run(args: argparse.Namespace) -> dict[str, object]:
    if args.command == "apply" and args.confirm != PRODUCTION_CONFIRMATION:
        raise SnapshotPublicationReconciliationError(
            "production_confirmation_required"
        )
    _validate_operator_paths(args)
    with _connect_existing(args.database) as (connection, database_identity):
        if args.command == "apply":
            return _run_apply(args, connection, database_identity)
        report = reconcile_snapshot_publication_outbox(
            connection,
            snapshot_root=args.snapshot_root,
            publication_events_path=args.publication_events,
            feed_modes=_lanes(args.lane),
            release_sha=args.expected_release_sha,
            release_tree=args.expected_release_tree,
            apply=False,
        )
    receipt = _receipt_document(report)
    _write_or_verify_receipt(args.receipt, receipt)
    return receipt


def main(argv: Sequence[str] | None = None) -> int:
    try:
        report = run(build_parser().parse_args(argv))
        print(json.dumps(report, sort_keys=True))
        return 0
    except (
        EstimatorSnapshotReceiverError,
        SnapshotPublicationReconciliationError,
    ) as exc:
        print(
            json.dumps(
                {
                    "status": "FAIL",
                    "reason_code": str(exc),
                    "secrets_disclosed": False,
                    "market_values_disclosed": False,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    except (OSError, sqlite3.Error, TypeError, ValueError):
        print(
            json.dumps(
                {
                    "status": "FAIL",
                    "reason_code": "snapshot_reconciliation_operation_failed",
                    "secrets_disclosed": False,
                    "market_values_disclosed": False,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
