from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile

import pytest

from core.market_intelligence.estimator_snapshot_receiver import (
    EstimatorSnapshotReceiverError,
    SNAPSHOT_PUBLICATION_EVENT_LOG_MAX_BYTES,
    connect_snapshot_receiver,
    estimator_snapshot_publication_event_id,
    reconcile_snapshot_publication_outbox,
)
from core.market_intelligence.private_pipeline_contracts import estimator_snapshot_id
from core.market_intelligence.private_pipeline_contracts import EstimatorSnapshotV1
from scripts import reconcile_estimator_snapshot_publication_outbox as operator
from tests.test_market_private_pipeline_contracts import (
    estimator_snapshot_fixture,
    fixture,
)


RELEASE_SHA = "a" * 40
RELEASE_TREE = "b" * 40
PUBLISHED_AT = "2026-08-26T05:00:07Z"
RECEIVED_AT = "2026-08-26T05:00:06Z"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    path.chmod(0o600)


def _snapshot(
    lane: str,
    version: int,
    *,
    contract: str = "estimator_snapshot/2.0",
) -> dict[str, object]:
    document = (
        fixture("estimator_snapshot.json")
        if contract == "estimator_snapshot/1.0"
        else estimator_snapshot_fixture()
    )
    document["feed_mode"] = lane
    document["snapshot_version"] = version
    if contract == "estimator_snapshot/1.0":
        document = EstimatorSnapshotV1.model_validate(document).model_dump(mode="json")
    document.pop("snapshot_id", None)
    document["snapshot_id"] = estimator_snapshot_id(document)
    return document


def _add_pending(
    connection,
    *,
    snapshot_root: Path,
    events_path: Path,
    lane: str,
    version: int,
    redacted: bool = False,
    contract: str = "estimator_snapshot/2.0",
) -> dict[str, object]:
    document = _snapshot(lane, version, contract=contract)
    snapshot_id = str(document["snapshot_id"])
    event_id = estimator_snapshot_publication_event_id(lane, snapshot_id)
    connection.execute(
        "INSERT INTO estimator_snapshot_receipts "
        "(feed_mode,snapshot_version,snapshot_id,input_snapshot_hash,payload_json,"
        "received_at_utc,published_at_utc) VALUES(?,?,?,?,?,?,?)",
        (
            lane,
            version,
            snapshot_id,
            document["input_snapshot_hash"],
            "{}" if redacted else json.dumps(document, sort_keys=True),
            RECEIVED_AT,
            PUBLISHED_AT,
        ),
    )
    connection.execute(
        "INSERT INTO estimator_snapshot_publication_outbox "
        "(event_id,feed_mode,snapshot_version,snapshot_id,published_at_utc,"
        "delivered_at_utc) VALUES(?,?,?,?,?,NULL)",
        (event_id, lane, version, snapshot_id, PUBLISHED_AT),
    )
    view = {
        "contract": "estimator_snapshot_web_view/1.0",
        "snapshot_hash": snapshot_id,
        "snapshot_version": version,
        "feed_mode": lane,
        "received_at_utc": RECEIVED_AT,
        "published_at_utc": PUBLISHED_AT,
        "transport_state": "FRESH",
        "stale_after_seconds": 30,
        "snapshot": document,
    }
    _write_json(
        snapshot_root
        / (
            "latest-private-primary.json"
            if lane == "PRIVATE_PRIMARY"
            else "latest-private-shadow.json"
        ),
        view,
    )
    event = {
        "contract": "estimator_snapshot_realtime_event/1.0",
        "event_id": event_id,
        "event_type": "estimator:snapshot-published",
        "feed_mode": lane,
        "snapshot_hash": snapshot_id,
        "snapshot_version": version,
        "published_at_utc": PUBLISHED_AT,
    }
    events_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with events_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
    events_path.chmod(0o600)
    return document


def _add_production_scale_pending(
    connection,
    *,
    snapshot_root: Path,
    events_path: Path,
    count: int = 23_305,
    retained_v1_count: int = 14_941,
    force_rotated_log: bool = False,
) -> None:
    base_v1 = EstimatorSnapshotV1.model_validate(
        fixture("estimator_snapshot.json")
    ).model_dump(mode="json")
    receipts = []
    outbox = []
    event_lines: list[bytes] = []
    latest_document: dict[str, object] | None = None
    for version in range(1, count + 1):
        if version <= retained_v1_count:
            document = dict(base_v1)
            document["feed_mode"] = "PRIVATE_SHADOW"
            document["snapshot_version"] = version
            document.pop("snapshot_id", None)
            document["snapshot_id"] = estimator_snapshot_id(document)
            snapshot_id = str(document["snapshot_id"])
            input_hash = str(document["input_snapshot_hash"])
            payload_json = json.dumps(document, sort_keys=True, separators=(",", ":"))
        elif version == count:
            document = _snapshot("PRIVATE_SHADOW", version)
            latest_document = document
            snapshot_id = str(document["snapshot_id"])
            input_hash = str(document["input_snapshot_hash"])
            payload_json = "{}"
        else:
            snapshot_id = sha256(f"redacted-snapshot:{version}".encode()).hexdigest()
            input_hash = sha256(f"redacted-input:{version}".encode()).hexdigest()
            payload_json = "{}"
        event_id = estimator_snapshot_publication_event_id(
            "PRIVATE_SHADOW", snapshot_id
        )
        receipts.append(
            (
                "PRIVATE_SHADOW",
                version,
                snapshot_id,
                input_hash,
                payload_json,
                RECEIVED_AT,
                PUBLISHED_AT,
            )
        )
        outbox.append(
            (
                event_id,
                "PRIVATE_SHADOW",
                version,
                snapshot_id,
                PUBLISHED_AT,
            )
        )
        event_lines.append(
            (
                json.dumps(
                    {
                        "contract": "estimator_snapshot_realtime_event/1.0",
                        "event_id": event_id,
                        "event_type": "estimator:snapshot-published",
                        "feed_mode": "PRIVATE_SHADOW",
                        "snapshot_hash": snapshot_id,
                        "snapshot_version": version,
                        "published_at_utc": PUBLISHED_AT,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8")
        )
    assert latest_document is not None
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.executemany(
            "INSERT INTO estimator_snapshot_receipts "
            "(feed_mode,snapshot_version,snapshot_id,input_snapshot_hash,payload_json,"
            "received_at_utc,published_at_utc) VALUES(?,?,?,?,?,?,?)",
            receipts,
        )
        connection.executemany(
            "INSERT INTO estimator_snapshot_publication_outbox "
            "(event_id,feed_mode,snapshot_version,snapshot_id,published_at_utc,"
            "delivered_at_utc) VALUES(?,?,?,?,?,NULL)",
            outbox,
        )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    _write_json(
        snapshot_root / "latest-private-shadow.json",
        {
            "contract": "estimator_snapshot_web_view/1.0",
            "snapshot_hash": latest_document["snapshot_id"],
            "snapshot_version": count,
            "feed_mode": "PRIVATE_SHADOW",
            "received_at_utc": RECEIVED_AT,
            "published_at_utc": PUBLISHED_AT,
            "transport_state": "FRESH",
            "stale_after_seconds": 30,
            "snapshot": latest_document,
        },
    )
    payload = b"".join(event_lines)
    events_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if len(payload) <= SNAPSHOT_PUBLICATION_EVENT_LOG_MAX_BYTES and not force_rotated_log:
        events_path.write_bytes(payload)
    else:
        split_limit = (
            len(payload) // 2
            if force_rotated_log
            else SNAPSHOT_PUBLICATION_EVENT_LOG_MAX_BYTES
        )
        split = payload.rfind(b"\n", 0, split_limit + 1) + 1
        assert 0 < split <= SNAPSHOT_PUBLICATION_EVENT_LOG_MAX_BYTES
        rotated = events_path.with_name(events_path.name + ".1")
        rotated.write_bytes(payload[:split])
        rotated.chmod(0o600)
        events_path.write_bytes(payload[split:])
        assert events_path.stat().st_size <= SNAPSHOT_PUBLICATION_EVENT_LOG_MAX_BYTES
    events_path.chmod(0o600)


def _reconcile(
    connection,
    root: Path,
    *,
    lanes: tuple[str, ...],
    apply: bool = False,
    expected_plan: str | None = None,
):
    return reconcile_snapshot_publication_outbox(
        connection,
        snapshot_root=root / "snapshots",
        publication_events_path=root / "events" / "publication.jsonl",
        feed_modes=lanes,
        release_sha=RELEASE_SHA,
        release_tree=RELEASE_TREE,
        apply=apply,
        expected_plan_sha256=expected_plan,
        preimage_backup_path=(
            root / "backups" / f"{expected_plan}.sqlite3" if apply else None
        ),
    )


def test_plan_apply_and_second_apply_are_exact_non_replaying_and_idempotent() -> None:
    with tempfile.TemporaryDirectory(prefix="snapshot-reconcile-") as temporary:
        root = Path(temporary)
        database = root / "receiver.sqlite3"
        connection = connect_snapshot_receiver(database)
        try:
            _add_pending(
                connection,
                snapshot_root=root / "snapshots",
                events_path=root / "events" / "publication.jsonl",
                lane="PRIVATE_PRIMARY",
                version=9,
            )
            event_bytes = (root / "events" / "publication.jsonl").read_bytes()
            counts_before = tuple(
                connection.execute(
                    "SELECT (SELECT COUNT(*) FROM estimator_snapshot_receipts),"
                    "(SELECT COUNT(*) FROM estimator_snapshot_publication_outbox)"
                ).fetchone()
            )

            plan = _reconcile(connection, root, lanes=("PRIVATE_PRIMARY",))
            assert plan["status"] == "PLAN"
            assert plan["pending_before"] == 1
            assert plan["retained_payload_count"] == 1
            applied = _reconcile(
                connection,
                root,
                lanes=("PRIVATE_PRIMARY",),
                apply=True,
                expected_plan=str(plan["plan_sha256"]),
            )
            assert applied["status"] == "APPLIED"
            assert applied["repaired_count"] == 1
            row = connection.execute(
                "SELECT published_at_utc,delivered_at_utc "
                "FROM estimator_snapshot_publication_outbox"
            ).fetchone()
            assert tuple(row) == (PUBLISHED_AT, PUBLISHED_AT)
            assert (root / "events" / "publication.jsonl").read_bytes() == event_bytes
            assert tuple(
                connection.execute(
                    "SELECT (SELECT COUNT(*) FROM estimator_snapshot_receipts),"
                    "(SELECT COUNT(*) FROM estimator_snapshot_publication_outbox)"
                ).fetchone()
            ) == counts_before

            empty_plan = _reconcile(connection, root, lanes=("PRIVATE_PRIMARY",))
            again = _reconcile(
                connection,
                root,
                lanes=("PRIVATE_PRIMARY",),
                apply=True,
                expected_plan=str(empty_plan["plan_sha256"]),
            )
            assert again["status"] == "ALREADY_RECONCILED"
            assert again["repaired_count"] == 0
        finally:
            connection.close()


def test_redacted_receipt_is_allowed_only_with_exact_event_and_latest_view() -> None:
    with tempfile.TemporaryDirectory(prefix="snapshot-reconcile-redacted-") as temporary:
        root = Path(temporary)
        connection = connect_snapshot_receiver(root / "receiver.sqlite3")
        try:
            _add_pending(
                connection,
                snapshot_root=root / "snapshots",
                events_path=root / "events" / "publication.jsonl",
                lane="PRIVATE_SHADOW",
                version=3,
                redacted=True,
            )
            plan = _reconcile(connection, root, lanes=("PRIVATE_SHADOW",))
            assert plan["redacted_payload_count"] == 1
            assert plan["retained_payload_count"] == 0

            view_path = root / "snapshots" / "latest-private-shadow.json"
            view = json.loads(view_path.read_text(encoding="utf-8"))
            view["published_at_utc"] = "2026-08-26T05:00:08Z"
            _write_json(view_path, view)
            with pytest.raises(
                EstimatorSnapshotReceiverError,
                match="snapshot_reconciliation_latest_view_identity_invalid",
            ):
                _reconcile(connection, root, lanes=("PRIVATE_SHADOW",))
            assert connection.execute(
                "SELECT delivered_at_utc FROM estimator_snapshot_publication_outbox"
            ).fetchone()[0] is None
        finally:
            connection.close()


@pytest.mark.parametrize("corruption", ["malformed", "duplicate"])
def test_full_event_log_corruption_or_duplicate_fails_closed(corruption: str) -> None:
    with tempfile.TemporaryDirectory(prefix="snapshot-reconcile-corrupt-") as temporary:
        root = Path(temporary)
        connection = connect_snapshot_receiver(root / "receiver.sqlite3")
        try:
            _add_pending(
                connection,
                snapshot_root=root / "snapshots",
                events_path=root / "events" / "publication.jsonl",
                lane="PRIVATE_PRIMARY",
                version=4,
            )
            events_path = root / "events" / "publication.jsonl"
            if corruption == "malformed":
                with events_path.open("a", encoding="utf-8") as handle:
                    handle.write("{broken\n")
            else:
                event = events_path.read_text(encoding="utf-8")
                rotated = events_path.with_name(events_path.name + ".1")
                rotated.write_text(event, encoding="utf-8")
                rotated.chmod(0o600)
            with pytest.raises(EstimatorSnapshotReceiverError):
                _reconcile(connection, root, lanes=("PRIVATE_PRIMARY",))
            assert connection.execute(
                "SELECT delivered_at_utc FROM estimator_snapshot_publication_outbox"
            ).fetchone()[0] is None
        finally:
            connection.close()


def test_partial_evidence_fails_before_any_lane_is_updated() -> None:
    with tempfile.TemporaryDirectory(prefix="snapshot-reconcile-partial-") as temporary:
        root = Path(temporary)
        connection = connect_snapshot_receiver(root / "receiver.sqlite3")
        try:
            _add_pending(
                connection,
                snapshot_root=root / "snapshots",
                events_path=root / "events" / "publication.jsonl",
                lane="PRIVATE_PRIMARY",
                version=5,
            )
            _add_pending(
                connection,
                snapshot_root=root / "snapshots",
                events_path=root / "events" / "publication.jsonl",
                lane="PRIVATE_SHADOW",
                version=5,
            )
            connection.execute(
                "UPDATE estimator_snapshot_receipts SET published_at_utc=NULL "
                "WHERE feed_mode='PRIVATE_SHADOW'"
            )
            with pytest.raises(
                EstimatorSnapshotReceiverError,
                match="snapshot_reconciliation_receipt_identity_invalid",
            ):
                _reconcile(
                    connection,
                    root,
                    lanes=("PRIVATE_PRIMARY", "PRIVATE_SHADOW"),
                    apply=True,
                    expected_plan="0" * 64,
                )
            assert connection.execute(
                "SELECT COUNT(*) FROM estimator_snapshot_publication_outbox "
                "WHERE delivered_at_utc IS NOT NULL"
            ).fetchone()[0] == 0
        finally:
            connection.close()


def test_apply_rejects_dry_run_race_with_plan_cas_and_changes_nothing() -> None:
    with tempfile.TemporaryDirectory(prefix="snapshot-reconcile-race-") as temporary:
        root = Path(temporary)
        connection = connect_snapshot_receiver(root / "receiver.sqlite3")
        try:
            _add_pending(
                connection,
                snapshot_root=root / "snapshots",
                events_path=root / "events" / "publication.jsonl",
                lane="PRIVATE_PRIMARY",
                version=6,
            )
            plan = _reconcile(
                connection,
                root,
                lanes=("PRIVATE_PRIMARY", "PRIVATE_SHADOW"),
            )
            _add_pending(
                connection,
                snapshot_root=root / "snapshots",
                events_path=root / "events" / "publication.jsonl",
                lane="PRIVATE_SHADOW",
                version=6,
                redacted=True,
            )
            with pytest.raises(
                EstimatorSnapshotReceiverError,
                match="snapshot_reconciliation_plan_cas_mismatch",
            ):
                _reconcile(
                    connection,
                    root,
                    lanes=("PRIVATE_PRIMARY", "PRIVATE_SHADOW"),
                    apply=True,
                    expected_plan=str(plan["plan_sha256"]),
                )
            assert connection.execute(
                "SELECT COUNT(*) FROM estimator_snapshot_publication_outbox "
                "WHERE delivered_at_utc IS NOT NULL"
            ).fetchone()[0] == 0
        finally:
            connection.close()


def test_many_same_lane_pending_rows_support_mixed_v1_v2_and_redaction() -> None:
    with tempfile.TemporaryDirectory(prefix="snapshot-reconcile-many-") as temporary:
        root = Path(temporary)
        connection = connect_snapshot_receiver(root / "receiver.sqlite3")
        try:
            redacted = 0
            for version in range(1, 241):
                is_redacted = version % 11 == 0
                redacted += int(is_redacted)
                _add_pending(
                    connection,
                    snapshot_root=root / "snapshots",
                    events_path=root / "events" / "publication.jsonl",
                    lane="PRIVATE_SHADOW",
                    version=version,
                    redacted=is_redacted,
                    contract=(
                        "estimator_snapshot/1.0"
                        if version <= 120
                        else "estimator_snapshot/2.0"
                    ),
                )
            plan = _reconcile(connection, root, lanes=("PRIVATE_SHADOW",))
            aggregate = plan["lane_aggregates"]["PRIVATE_SHADOW"]
            assert aggregate["pending_count"] == 240
            assert aggregate["minimum_snapshot_version"] == 1
            assert aggregate["maximum_snapshot_version"] == 240
            assert len(aggregate["identity_sha256"]) == 64
            assert plan["redacted_payload_count"] == redacted
            assert plan["retained_payload_count"] == 240 - redacted

            applied = _reconcile(
                connection,
                root,
                lanes=("PRIVATE_SHADOW",),
                apply=True,
                expected_plan=str(plan["plan_sha256"]),
            )
            assert applied["repaired_count"] == 240
            assert applied["preimage_backup_integrity"] == "ok"
            assert applied["publication_event_aggregate_sha256"] == plan[
                "publication_event_aggregate_sha256"
            ]
            assert connection.execute(
                "SELECT COUNT(*) FROM estimator_snapshot_publication_outbox "
                "WHERE delivered_at_utc IS NULL"
            ).fetchone()[0] == 0
        finally:
            connection.close()


def test_corrupt_middle_of_many_row_preimage_causes_atomic_zero_update() -> None:
    with tempfile.TemporaryDirectory(prefix="snapshot-reconcile-many-corrupt-") as temporary:
        root = Path(temporary)
        connection = connect_snapshot_receiver(root / "receiver.sqlite3")
        try:
            for version in range(1, 81):
                _add_pending(
                    connection,
                    snapshot_root=root / "snapshots",
                    events_path=root / "events" / "publication.jsonl",
                    lane="PRIVATE_SHADOW",
                    version=version,
                    redacted=version % 13 == 0,
                    contract=(
                        "estimator_snapshot/1.0"
                        if version <= 40
                        else "estimator_snapshot/2.0"
                    ),
                )
            plan = _reconcile(connection, root, lanes=("PRIVATE_SHADOW",))
            connection.execute(
                "UPDATE estimator_snapshot_receipts SET payload_json=? "
                "WHERE feed_mode='PRIVATE_SHADOW' AND snapshot_version=37",
                ('{"contract":"estimator_snapshot/1.0"}',),
            )
            with pytest.raises(
                EstimatorSnapshotReceiverError,
                match="snapshot_reconciliation_receipt_payload_invalid",
            ):
                _reconcile(
                    connection,
                    root,
                    lanes=("PRIVATE_SHADOW",),
                    apply=True,
                    expected_plan=str(plan["plan_sha256"]),
                )
            assert connection.execute(
                "SELECT COUNT(*) FROM estimator_snapshot_publication_outbox "
                "WHERE delivered_at_utc IS NOT NULL"
            ).fetchone()[0] == 0
        finally:
            connection.close()


def test_production_scale_23305_pending_rows_and_rotated_log_reconcile() -> None:
    with tempfile.TemporaryDirectory(prefix="snapshot-reconcile-production-scale-") as temporary:
        root = Path(temporary)
        connection = connect_snapshot_receiver(root / "receiver.sqlite3")
        try:
            _add_production_scale_pending(
                connection,
                snapshot_root=root / "snapshots",
                events_path=root / "events" / "publication.jsonl",
                force_rotated_log=True,
            )
            plan = _reconcile(connection, root, lanes=("PRIVATE_SHADOW",))
            aggregate = plan["lane_aggregates"]["PRIVATE_SHADOW"]
            assert plan["pending_before"] == 23_305
            assert plan["retained_payload_count"] == 14_941
            assert plan["redacted_payload_count"] == 8_364
            assert plan["event_count"] == 23_305
            assert plan["event_file_count"] == 2
            assert aggregate["pending_count"] == 23_305
            assert aggregate["minimum_snapshot_version"] == 1
            assert aggregate["maximum_snapshot_version"] == 23_305

            applied = _reconcile(
                connection,
                root,
                lanes=("PRIVATE_SHADOW",),
                apply=True,
                expected_plan=str(plan["plan_sha256"]),
            )
            assert applied["repaired_count"] == 23_305
            assert applied["pending_after"] == 0
            assert applied["preimage_plan_sha256"] == plan["plan_sha256"]
            assert connection.execute(
                "SELECT COUNT(*) FROM estimator_snapshot_publication_outbox "
                "WHERE delivered_at_utc=published_at_utc"
            ).fetchone()[0] == 23_305
        finally:
            connection.close()


def test_latest_historical_shadow_v1_view_can_be_reconciled_without_rewrite() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        connection = connect_snapshot_receiver(root / "receiver.sqlite")
        try:
            original = _add_pending(
                connection,
                snapshot_root=root / "snapshots",
                events_path=root / "events" / "publication.jsonl",
                lane="PRIVATE_SHADOW",
                version=7,
                contract="estimator_snapshot/1.0",
            )
            view_path = root / "snapshots" / "latest-private-shadow.json"
            view_digest = sha256(view_path.read_bytes()).hexdigest()
            plan = _reconcile(connection, root, lanes=("PRIVATE_SHADOW",))
            applied = _reconcile(
                connection,
                root,
                lanes=("PRIVATE_SHADOW",),
                apply=True,
                expected_plan=str(plan["plan_sha256"]),
            )
            assert applied["pending_after"] == 0
            assert sha256(view_path.read_bytes()).hexdigest() == view_digest
            assert json.loads(view_path.read_text())["snapshot"] == original
        finally:
            connection.close()


def _run_operator(*arguments: str):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = operator.main(list(arguments))
    return code, stdout.getvalue(), stderr.getvalue()


def _operator_fixture(root: Path) -> tuple[Path, tuple[str, ...], dict[str, object]]:
    database = root / "receiver.sqlite3"
    connection = connect_snapshot_receiver(database)
    try:
        _add_pending(
            connection,
            snapshot_root=root / "snapshots",
            events_path=root / "events" / "publication.jsonl",
            lane="PRIVATE_PRIMARY",
            version=41,
        )
    finally:
        connection.close()
    common = (
        "--database",
        str(database.resolve()),
        "--snapshot-root",
        str((root / "snapshots").resolve()),
        "--publication-events",
        str((root / "events" / "publication.jsonl").resolve()),
        "--lane",
        "PRIVATE_PRIMARY",
        "--expected-release-sha",
        RELEASE_SHA,
        "--expected-release-tree",
        RELEASE_TREE,
    )
    plan_receipt = root / "receipts" / "plan.json"
    code, output, error = _run_operator(
        "plan", *common, "--receipt", str(plan_receipt.resolve())
    )
    assert (code, error) == (0, "")
    plan = json.loads(output)
    apply_arguments = (
        "apply",
        *common,
        "--expected-plan-sha256",
        str(plan["plan_sha256"]),
        "--confirm",
        operator.PRODUCTION_CONFIRMATION,
        "--preimage-backup",
        str((root / "backups" / "preimage.sqlite3").resolve()),
        "--journal",
        str((root / "journal" / "apply.json").resolve()),
        "--receipt",
        str((root / "receipts" / "apply.json").resolve()),
    )
    return database, apply_arguments, plan


def _sigkill_operator(
    boundary: str,
    arguments: tuple[str, ...],
) -> subprocess.CompletedProcess[str]:
    program = r"""
import os
import signal
import sys
from scripts import reconcile_estimator_snapshot_publication_outbox as operator

boundary = sys.argv[1]
if boundary == "AFTER_PREPARED":
    original = operator._write_new_journal
    def kill_after_prepared(*args, **kwargs):
        result = original(*args, **kwargs)
        os.kill(os.getpid(), signal.SIGKILL)
        return result
    operator._write_new_journal = kill_after_prepared
elif boundary == "AFTER_DB_COMMIT":
    original = operator._advance_journal
    def kill_before_db_journal(path, document, **kwargs):
        if document.get("state") == "DB_COMMITTED":
            os.kill(os.getpid(), signal.SIGKILL)
        return original(path, document, **kwargs)
    operator._advance_journal = kill_before_db_journal
elif boundary == "AFTER_DB_JOURNAL":
    original = operator._advance_journal
    def kill_after_db_journal(path, document, **kwargs):
        result = original(path, document, **kwargs)
        if document.get("state") == "DB_COMMITTED":
            os.kill(os.getpid(), signal.SIGKILL)
        return result
    operator._advance_journal = kill_after_db_journal
elif boundary == "AFTER_RECEIPT_COMMIT":
    original = operator._write_or_verify_receipt
    def kill_after_receipt(*args, **kwargs):
        result = original(*args, **kwargs)
        os.kill(os.getpid(), signal.SIGKILL)
        return result
    operator._write_or_verify_receipt = kill_after_receipt
elif boundary == "AFTER_TERMINAL_JOURNAL":
    original = operator._advance_journal
    def kill_after_terminal_journal(path, document, **kwargs):
        result = original(path, document, **kwargs)
        if document.get("state") == "RECEIPT_COMMITTED":
            os.kill(os.getpid(), signal.SIGKILL)
        return result
    operator._advance_journal = kill_after_terminal_journal
else:
    raise AssertionError(boundary)
raise SystemExit(operator.main(sys.argv[2:]))
"""
    environment = os.environ.copy()
    environment["APP_ENV_FILE"] = "config/unit-test.env.example"
    return subprocess.run(
        [sys.executable, "-c", program, boundary, *arguments],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_operator_requires_confirmation_and_writes_value_free_mode_600_receipts() -> None:
    with tempfile.TemporaryDirectory(prefix="snapshot-reconcile-operator-") as temporary:
        root = Path(temporary)
        database = root / "receiver.sqlite3"
        connection = connect_snapshot_receiver(database)
        _add_pending(
            connection,
            snapshot_root=root / "snapshots",
            events_path=root / "events" / "publication.jsonl",
            lane="PRIVATE_PRIMARY",
            version=7,
        )
        connection.close()
        plan_receipt = root / "receipts" / "plan.json"
        common = (
            "--database",
            str(database.resolve()),
            "--snapshot-root",
            str((root / "snapshots").resolve()),
            "--publication-events",
            str((root / "events" / "publication.jsonl").resolve()),
            "--lane",
            "PRIVATE_PRIMARY",
            "--expected-release-sha",
            RELEASE_SHA,
            "--expected-release-tree",
            RELEASE_TREE,
        )
        code, output, error = _run_operator(
            "plan", *common, "--receipt", str(plan_receipt.resolve())
        )
        assert (code, error) == (0, "")
        plan = json.loads(output)
        assert stat.S_IMODE(plan_receipt.stat().st_mode) == 0o600
        assert "187450" not in output
        assert "rates" not in output
        assert plan["raw_payload_disclosed"] is False

        rejected_receipt = root / "receipts" / "rejected.json"
        code, _, error = _run_operator(
            "apply",
            *common,
            "--expected-plan-sha256",
            str(plan["plan_sha256"]),
            "--confirm",
            "wrong",
            "--preimage-backup",
            str((root / "backups" / "rejected.sqlite3").resolve()),
            "--receipt",
            str(rejected_receipt.resolve()),
        )
        assert code == 1
        assert "production_confirmation_required" in error
        assert not rejected_receipt.exists()

        apply_receipt = root / "receipts" / "apply.json"
        code, output, error = _run_operator(
            "apply",
            *common,
            "--expected-plan-sha256",
            str(plan["plan_sha256"]),
            "--confirm",
            operator.PRODUCTION_CONFIRMATION,
            "--preimage-backup",
            str((root / "backups" / "apply.sqlite3").resolve()),
            "--receipt",
            str(apply_receipt.resolve()),
        )
        assert (code, error) == (0, "")
        assert json.loads(output)["repaired_count"] == 1
        assert json.loads(output)["preimage_backup_integrity"] == "ok"
        assert stat.S_IMODE((root / "backups" / "apply.sqlite3").stat().st_mode) == 0o600
        assert stat.S_IMODE(apply_receipt.stat().st_mode) == 0o600
        assert sha256(apply_receipt.read_bytes()).hexdigest()
        assert "187450" not in apply_receipt.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("boundary", "expected_state", "database_committed", "receipt_exists"),
    (
        ("AFTER_PREPARED", "PREPARED", False, False),
        ("AFTER_DB_COMMIT", "PREPARED", True, False),
        ("AFTER_DB_JOURNAL", "DB_COMMITTED", True, False),
        ("AFTER_RECEIPT_COMMIT", "DB_COMMITTED", True, True),
        ("AFTER_TERMINAL_JOURNAL", "RECEIPT_COMMITTED", True, True),
    ),
)
def test_real_sigkill_boundaries_recover_exactly_once_without_replay(
    boundary: str,
    expected_state: str,
    database_committed: bool,
    receipt_exists: bool,
) -> None:
    with tempfile.TemporaryDirectory(prefix="snapshot-reconcile-sigkill-") as temporary:
        root = Path(temporary)
        database, arguments, _plan = _operator_fixture(root)
        killed = _sigkill_operator(boundary, arguments)
        assert killed.returncode == -9
        journal_path = root / "journal" / "apply.json"
        receipt_path = root / "receipts" / "apply.json"
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        assert journal["state"] == expected_state
        assert receipt_path.exists() is receipt_exists
        connection = sqlite3.connect(database)
        try:
            delivered = connection.execute(
                "SELECT delivered_at_utc FROM estimator_snapshot_publication_outbox"
            ).fetchone()[0]
        finally:
            connection.close()
        assert (delivered == PUBLISHED_AT) is database_committed

        code, output, error = _run_operator(*arguments)
        assert (code, error) == (0, "")
        recovered = json.loads(output)
        assert recovered["status"] == "APPLIED"
        assert recovered["repaired_count"] == 1
        terminal_bytes = journal_path.read_bytes()
        receipt_bytes = receipt_path.read_bytes()
        terminal = json.loads(terminal_bytes)
        assert terminal["state"] == "RECEIPT_COMMITTED"
        assert terminal["receipt_sha256"] == sha256(receipt_bytes).hexdigest()

        # A terminal rerun is a byte-for-byte no-op and cannot mutate again.
        code, repeated_output, error = _run_operator(*arguments)
        assert (code, error) == (0, "")
        assert json.loads(repeated_output) == recovered
        assert journal_path.read_bytes() == terminal_bytes
        assert receipt_path.read_bytes() == receipt_bytes
        connection = sqlite3.connect(database)
        try:
            assert connection.execute(
                "SELECT COUNT(*) FROM estimator_snapshot_publication_outbox "
                "WHERE delivered_at_utc=published_at_utc"
            ).fetchone()[0] == 1
        finally:
            connection.close()


def test_receipt_failure_leaves_db_committed_journal_and_retry_only_finishes_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory(prefix="snapshot-reconcile-receipt-fail-") as temporary:
        root = Path(temporary)
        database, arguments, _plan = _operator_fixture(root)
        original = operator._write_or_verify_receipt

        def fail_receipt(*_args, **_kwargs):
            raise operator.SnapshotPublicationReconciliationError(
                "injected_receipt_failure"
            )

        monkeypatch.setattr(operator, "_write_or_verify_receipt", fail_receipt)
        code, _output, error = _run_operator(*arguments)
        assert code == 1
        assert "injected_receipt_failure" in error
        journal_path = root / "journal" / "apply.json"
        assert json.loads(journal_path.read_text())["state"] == "DB_COMMITTED"
        connection = sqlite3.connect(database)
        try:
            assert connection.execute(
                "SELECT delivered_at_utc FROM estimator_snapshot_publication_outbox"
            ).fetchone()[0] == PUBLISHED_AT
        finally:
            connection.close()

        monkeypatch.setattr(operator, "_write_or_verify_receipt", original)
        code, output, error = _run_operator(*arguments)
        assert (code, error) == (0, "")
        assert json.loads(output)["repaired_count"] == 1
        assert json.loads(journal_path.read_text())["state"] == "RECEIPT_COMMITTED"


@pytest.mark.parametrize("tamper", ("journal", "result", "preimage", "receipt"))
def test_recovery_rejects_tampered_journal_preimage_or_receipt(tamper: str) -> None:
    with tempfile.TemporaryDirectory(prefix="snapshot-reconcile-tamper-") as temporary:
        root = Path(temporary)
        database, arguments, _plan = _operator_fixture(root)
        boundary = (
            "AFTER_PREPARED"
            if tamper == "journal"
            else "AFTER_DB_JOURNAL"
            if tamper == "result"
            else "AFTER_DB_COMMIT"
        )
        assert _sigkill_operator(boundary, arguments).returncode == -9
        journal_path = root / "journal" / "apply.json"
        if tamper == "journal":
            document = json.loads(journal_path.read_text())
            document["binding"]["release_sha"] = "c" * 40
            _write_json(journal_path, document)
        elif tamper == "result":
            document = json.loads(journal_path.read_text())
            document["result"]["repaired_count"] = 99
            _write_json(journal_path, document)
        elif tamper == "preimage":
            preimage = root / "backups" / "preimage.sqlite3"
            payload = bytearray(preimage.read_bytes())
            payload[-1] ^= 1
            preimage.write_bytes(payload)
            preimage.chmod(0o600)
        else:
            wrong = root / "receipts" / "apply.json"
            _write_json(wrong, {"status": "wrong"})
        code, _output, error = _run_operator(*arguments)
        assert code == 1
        assert any(
            reason in error
            for reason in (
                "journal_binding_mismatch",
                "journal_result_mismatch",
                "snapshot_reconciliation_backup_integrity_failed",
                "journal_preimage_mismatch",
                "receipt_existing_mismatch",
            )
        ), error
        connection = sqlite3.connect(database)
        try:
            count = connection.execute(
                "SELECT COUNT(*) FROM estimator_snapshot_publication_outbox"
            ).fetchone()[0]
            assert count == 1
        finally:
            connection.close()


def test_recovery_rejects_replaced_database_inode_before_any_mutation() -> None:
    with tempfile.TemporaryDirectory(prefix="snapshot-reconcile-wrong-db-") as temporary:
        root = Path(temporary)
        database, arguments, _plan = _operator_fixture(root)
        assert _sigkill_operator("AFTER_PREPARED", arguments).returncode == -9
        replacement = root / "replacement.sqlite3"
        shutil.copy2(database, replacement)
        original = root / "original.sqlite3"
        database.rename(original)
        replacement.rename(database)
        database.chmod(0o600)
        code, _output, error = _run_operator(*arguments)
        assert code == 1
        assert "journal_binding_mismatch" in error
        connection = sqlite3.connect(database)
        try:
            assert connection.execute(
                "SELECT delivered_at_utc FROM estimator_snapshot_publication_outbox"
            ).fetchone()[0] is None
        finally:
            connection.close()


def test_sqlite_uri_metacharacters_are_literal_and_intermediate_symlinks_fail_closed() -> None:
    with tempfile.TemporaryDirectory(prefix="snapshot-reconcile-uri-") as temporary:
        parent = Path(temporary)
        root = parent / "literal ?#% داده"
        root.mkdir(mode=0o700)
        database, arguments, _plan = _operator_fixture(root)
        code, output, error = _run_operator(*arguments)
        assert (code, error) == (0, "")
        assert json.loads(output)["repaired_count"] == 1
        assert database.exists()

        real = parent / "real"
        real.mkdir(mode=0o700)
        linked = parent / "linked"
        linked.symlink_to(real, target_is_directory=True)
        target = real / "receiver.sqlite3"
        connection = connect_snapshot_receiver(target)
        connection.close()
        with pytest.raises(
            operator.SnapshotPublicationReconciliationError,
            match="receiver_database_parent_invalid",
        ):
            with operator._connect_existing(linked / "receiver.sqlite3"):
                pass


def test_database_path_swap_race_is_detected_by_fd_and_inode_recheck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory(prefix="snapshot-reconcile-race-path-") as temporary:
        root = Path(temporary)
        database, _arguments, _plan = _operator_fixture(root)
        replacement = root / "replacement.sqlite3"
        shutil.copy2(database, replacement)
        original_connect = operator.sqlite3.connect
        swapped = False

        def swap_then_connect(*args, **kwargs):
            nonlocal swapped
            if not swapped:
                swapped = True
                database.rename(root / "original.sqlite3")
                replacement.rename(database)
            return original_connect(*args, **kwargs)

        monkeypatch.setattr(operator.sqlite3, "connect", swap_then_connect)
        with pytest.raises(
            operator.SnapshotPublicationReconciliationError,
            match="receiver_database_contract_invalid",
        ):
            with operator._connect_existing(database):
                pass


def test_unknown_journal_schema_version_fails_closed() -> None:
    with tempfile.TemporaryDirectory(prefix="snapshot-reconcile-schema-") as temporary:
        root = Path(temporary)
        _database, arguments, _plan = _operator_fixture(root)
        assert _sigkill_operator("AFTER_PREPARED", arguments).returncode == -9
        journal_path = root / "journal" / "apply.json"
        document = json.loads(journal_path.read_text())
        document["schema_version"] = 2
        _write_json(journal_path, document)
        code, _output, error = _run_operator(*arguments)
        assert code == 1
        assert "journal_schema_or_compatibility_invalid" in error


def test_hardlinked_database_and_loose_journal_mode_fail_closed() -> None:
    with tempfile.TemporaryDirectory(prefix="snapshot-reconcile-file-contract-") as temporary:
        root = Path(temporary)
        database, arguments, _plan = _operator_fixture(root)
        hardlink = root / "receiver-hardlink.sqlite3"
        os.link(database, hardlink)
        with pytest.raises(
            operator.SnapshotPublicationReconciliationError,
            match="receiver_database_invalid",
        ):
            with operator._connect_existing(database):
                pass
        hardlink.unlink()

        assert _sigkill_operator("AFTER_PREPARED", arguments).returncode == -9
        journal_path = root / "journal" / "apply.json"
        journal_path.chmod(0o644)
        code, _output, error = _run_operator(*arguments)
        assert code == 1
        assert "journal_mode_invalid" in error
