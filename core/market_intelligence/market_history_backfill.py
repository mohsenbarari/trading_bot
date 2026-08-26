"""Idempotent, privacy-bounded import of normalized historical Market Facts.

The importer accepts one source per bundle.  Telegram envelopes, plaintext raw
messages, URLs, credentials and direct identities are not part of the contract.
If historical raw text or participant identity was selected for permanent web
archive, the protected export job must encrypt it before this boundary.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from typing import Any, Literal, Mapping, Sequence

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from .market_fact_archive import (
    MarketFactArchiveError,
    build_and_publish_fact,
    stable_fact_id,
)
from .private_pipeline_contracts import (
    Code,
    FactPayload,
    Hex64,
    QualityState,
    content_hash,
    load_source_registry,
)


class HistoryBackfillError(RuntimeError):
    """A content-free history import failure."""


_ENCRYPTION_KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,95}$")
_FORBIDDEN_KEY_PARTS = frozenset(
    {
        "access_hash",
        "api_key",
        "channel_name",
        "channel_title",
        "chat_title",
        "credential",
        "invite",
        "link",
        "message_envelope",
        "message_link",
        "peer_title",
        "phone",
        "raw_payload",
        "session",
        "token",
        "url",
        "username",
    }
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone_required")
    return value.astimezone(timezone.utc)


def _decode_ciphertext(value: str, *, field_name: str) -> bytes:
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError) as exc:
        raise ValueError(f"{field_name}_invalid") from exc
    if not 16 <= len(decoded) <= 256_000:
        raise ValueError(f"{field_name}_size_invalid")
    return decoded


def _validate_key_id(value: str) -> str:
    if not _ENCRYPTION_KEY_ID.fullmatch(value):
        raise ValueError("encryption_key_id_invalid")
    return value


class _Contract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        protected_namespaces=(),
    )


class EncryptedParticipantV1(_Contract):
    actor_role: Literal["OFFERER", "REQUESTER"]
    telegram_id_ciphertext_b64: str = Field(min_length=24, max_length=4000)
    telegram_id_lookup_hmac: Hex64
    display_name_ciphertext_b64: str | None = Field(
        default=None, min_length=24, max_length=16_000
    )
    encryption_key_id: str

    @field_validator("telegram_id_ciphertext_b64")
    @classmethod
    def validate_telegram_ciphertext(cls, value: str) -> str:
        _decode_ciphertext(value, field_name="telegram_id_ciphertext")
        return value

    @field_validator("display_name_ciphertext_b64")
    @classmethod
    def validate_name_ciphertext(cls, value: str | None) -> str | None:
        if value is not None:
            _decode_ciphertext(value, field_name="display_name_ciphertext")
        return value

    @field_validator("encryption_key_id")
    @classmethod
    def validate_encryption_key_id(cls, value: str) -> str:
        return _validate_key_id(value)


class EncryptedRawTextV1(_Contract):
    ciphertext_b64: str = Field(min_length=24, max_length=350_000)
    plaintext_hash: Hex64
    encryption_key_id: str

    @field_validator("ciphertext_b64")
    @classmethod
    def validate_ciphertext(cls, value: str) -> str:
        _decode_ciphertext(value, field_name="raw_text_ciphertext")
        return value

    @field_validator("encryption_key_id")
    @classmethod
    def validate_encryption_key_id(cls, value: str) -> str:
        return _validate_key_id(value)


class HistoryLineageV1(_Contract):
    source_record_id_hash: Hex64
    source_revision: int = Field(ge=1)


class HistoryFactRecordV1(_Contract):
    contract: Literal["market_history_fact/1.0"]
    lineage: HistoryLineageV1
    event_key: Hex64
    origin_event_key: Hex64
    source_code: Code
    occurred_at_utc: AwareDatetime
    available_at_utc: AwareDatetime
    parser_version: str = Field(min_length=1, max_length=96)
    quality_state: QualityState
    quality_reason_codes: tuple[str, ...] = ()
    payload: FactPayload
    encrypted_raw_text: EncryptedRawTextV1 | None = None
    encrypted_participants: tuple[EncryptedParticipantV1, ...] = ()

    @field_validator("occurred_at_utc", "available_at_utc")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)

    @field_validator("quality_reason_codes")
    @classmethod
    def validate_reason_codes(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,95}", value):
                raise ValueError("quality_reason_code_invalid")
        return values

    @model_validator(mode="after")
    def validate_semantics(self) -> "HistoryFactRecordV1":
        if self.available_at_utc < self.occurred_at_utc:
            raise ValueError("availability_before_occurrence")
        source = load_source_registry().by_code().get(self.source_code)
        if source is None or not source.permanent_archive or not source.transfer_to_bot:
            raise ValueError("history_source_not_permanent_and_transferable")
        if self.payload.kind not in source.allowed_fact_kinds:
            raise ValueError("fact_kind_not_allowed_for_source")
        roles = [item.actor_role for item in self.encrypted_participants]
        if len(roles) != len(set(roles)):
            raise ValueError("duplicate_participant_role")
        if self.source_code not in {"GROUP_1", "GROUP_2"} and self.encrypted_participants:
            raise ValueError("participants_only_allowed_for_coin_groups")
        return self

    @property
    def logical_identity_hash(self) -> str:
        return stable_fact_id(
            source_code=self.source_code,
            event_key=self.event_key,
            fact_kind=self.payload.kind,
        )

    @property
    def record_hash(self) -> str:
        return content_hash(self)


class HistoryImportBundleV1(_Contract):
    contract: Literal["market_history_bundle/1.0"]
    source_code: Code
    source_system: Code
    source_artifact_hash: Hex64
    records: tuple[dict[str, Any], ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_artifact_hash(self) -> "HistoryImportBundleV1":
        if self.source_artifact_hash != content_hash(self.records):
            raise ValueError("source_artifact_hash_mismatch")
        source = load_source_registry().by_code().get(self.source_code)
        if source is None or not source.permanent_archive or not source.transfer_to_bot:
            raise ValueError("history_bundle_source_not_permanent_and_transferable")
        return self


def build_bundle(
    *, source_code: str, source_system: str, records: Sequence[Mapping[str, Any]]
) -> HistoryImportBundleV1:
    copied = tuple(dict(item) for item in records)
    return HistoryImportBundleV1(
        contract="market_history_bundle/1.0",
        source_code=source_code,
        source_system=source_system,
        source_artifact_hash=content_hash(copied),
        records=copied,
    )


def _scan_forbidden(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if any(token in normalized for token in _FORBIDDEN_KEY_PARTS):
                raise HistoryBackfillError("FORBIDDEN_FIELD")
            _scan_forbidden(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _scan_forbidden(item)
    elif isinstance(value, str):
        lowered = value.strip().lower()
        if lowered.startswith(("http://", "https://", "tg://")) or "t.me/" in lowered:
            raise HistoryBackfillError("FORBIDDEN_VALUE")


def _safe_record_hash(value: Mapping[str, Any]) -> str:
    try:
        return content_hash(value)
    except (TypeError, ValueError):
        return sha256(repr(type(value)).encode("ascii")).hexdigest()


def _reason_from_exception(exc: Exception) -> str:
    if isinstance(exc, HistoryBackfillError):
        return str(exc)
    name = type(exc).__name__.upper()
    if name == "VALIDATIONERROR":
        return "CONTRACT_INVALID"
    if isinstance(exc, MarketFactArchiveError):
        return "ARCHIVE_CONTRACT_REJECTED"
    return "ARCHIVE_WRITE_REJECTED"


def _batch_id(bundle: HistoryImportBundleV1) -> str:
    return sha256(
        b"market-history-batch/1.0\0"
        + bundle.source_code.encode("ascii")
        + b"\0"
        + bundle.source_system.encode("ascii")
        + b"\0"
        + bytes.fromhex(bundle.source_artifact_hash)
    ).hexdigest()


def _fact_material(record: HistoryFactRecordV1) -> dict[str, Any]:
    return {
        "fact_id": record.logical_identity_hash,
        "event_key": record.event_key,
        "origin_event_key": record.origin_event_key,
        "source_code": record.source_code,
        "occurred_at_utc": record.occurred_at_utc.isoformat(),
        "available_at_utc": record.available_at_utc.isoformat(),
        "parser_version": record.parser_version,
        "quality_state": record.quality_state,
        "quality_reason_codes": list(record.quality_reason_codes),
        "payload_hash": content_hash(record.payload),
    }


def _archive_material(connection, fact_ids: Sequence[str]) -> list[dict[str, Any]]:
    if not fact_ids:
        return []
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT encode(fact_id,'hex'),encode(event_key,'hex'),
                   encode(origin_event_key,'hex'),source_code,occurred_at_utc,
                   available_at_utc,parser_version,quality_state,
                   quality_reason_codes,encode(payload_hash,'hex')
            FROM market_data.market_facts
            WHERE fact_id = ANY(%s::bytea[])
            ORDER BY fact_id
            """,
            ([bytes.fromhex(item) for item in fact_ids],),
        )
        return [
            {
                "fact_id": row[0],
                "event_key": row[1],
                "origin_event_key": row[2],
                "source_code": row[3],
                "occurred_at_utc": row[4].astimezone(timezone.utc).isoformat(),
                "available_at_utc": row[5].astimezone(timezone.utc).isoformat(),
                "parser_version": row[6],
                "quality_state": row[7],
                "quality_reason_codes": list(row[8]),
                "payload_hash": row[9],
            }
            for row in cursor.fetchall()
        ]


def _write_sensitive_archive(connection, record: HistoryFactRecordV1) -> None:
    from psycopg2.extras import execute_values

    fact_id = bytes.fromhex(record.logical_identity_hash)
    with connection.cursor() as cursor:
        if record.encrypted_raw_text is not None:
            raw = record.encrypted_raw_text
            cursor.execute(
                """
                INSERT INTO market_data.curated_raw_texts(
                    fact_id,ciphertext,encryption_key_id,plaintext_hash
                ) VALUES(%s,%s,%s,%s)
                ON CONFLICT(fact_id) DO NOTHING
                """,
                (
                    fact_id,
                    _decode_ciphertext(raw.ciphertext_b64, field_name="raw_text_ciphertext"),
                    raw.encryption_key_id,
                    bytes.fromhex(raw.plaintext_hash),
                ),
            )
            cursor.execute(
                "SELECT encode(plaintext_hash,'hex') FROM market_data.curated_raw_texts "
                "WHERE fact_id=%s",
                (fact_id,),
            )
            if cursor.fetchone()[0] != raw.plaintext_hash:
                raise HistoryBackfillError("RAW_TEXT_IDENTITY_CONFLICT")
        if record.encrypted_participants:
            rows = [
                (
                    fact_id,
                    participant.actor_role,
                    _decode_ciphertext(
                        participant.telegram_id_ciphertext_b64,
                        field_name="telegram_id_ciphertext",
                    ),
                    bytes.fromhex(participant.telegram_id_lookup_hmac),
                    _decode_ciphertext(
                        participant.display_name_ciphertext_b64,
                        field_name="display_name_ciphertext",
                    )
                    if participant.display_name_ciphertext_b64 is not None
                    else None,
                    participant.encryption_key_id,
                )
                for participant in record.encrypted_participants
            ]
            execute_values(
                cursor,
                """
                INSERT INTO market_data.market_actor_identities(
                    fact_id,actor_role,telegram_id_ciphertext,
                    telegram_id_lookup_hmac,display_name_ciphertext,encryption_key_id
                ) VALUES %s ON CONFLICT(fact_id,actor_role) DO NOTHING
                """,
                rows,
            )
            cursor.execute(
                """
                SELECT actor_role,encode(telegram_id_lookup_hmac,'hex')
                FROM market_data.market_actor_identities
                WHERE fact_id=%s
                """,
                (fact_id,),
            )
            stored = {str(row[0]): str(row[1]) for row in cursor.fetchall()}
            expected = {
                item.actor_role: item.telegram_id_lookup_hmac
                for item in record.encrypted_participants
            }
            if any(stored.get(role) != digest for role, digest in expected.items()):
                raise HistoryBackfillError("PARTICIPANT_IDENTITY_CONFLICT")


def _quarantine(
    connection,
    *,
    batch_id: str,
    position: int,
    source_code: str,
    record_hash: str,
    reason_code: str,
    logical_identity_hash: str | None = None,
    source_revision: int | None = None,
) -> None:
    with connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO market_data.history_import_quarantine(
                    import_batch_id,source_position,source_code,record_hash,
                    reason_code,logical_identity_hash,source_revision
                ) VALUES(decode(%s,'hex'),%s,%s,decode(%s,'hex'),%s,
                         CASE WHEN %s IS NULL THEN NULL ELSE decode(%s,'hex') END,%s)
                ON CONFLICT(import_batch_id,source_position) DO NOTHING
                """,
                (
                    batch_id,
                    position,
                    source_code,
                    record_hash,
                    reason_code,
                    logical_identity_hash,
                    logical_identity_hash,
                    source_revision,
                ),
            )


def import_history_bundle(connection, bundle_value: Mapping[str, Any]) -> dict[str, Any]:
    """Import one source bundle; invalid rows are digest-only quarantined."""

    bundle = HistoryImportBundleV1.model_validate(bundle_value)
    batch_id = _batch_id(bundle)
    parsed: list[tuple[int, str, HistoryFactRecordV1]] = []
    invalid: list[tuple[int, str, str]] = []
    for position, raw in enumerate(bundle.records):
        record_hash = _safe_record_hash(raw)
        try:
            _scan_forbidden(raw)
            record = HistoryFactRecordV1.model_validate(raw)
            if record.source_code != bundle.source_code:
                raise HistoryBackfillError("SOURCE_MISMATCH")
            parsed.append((position, record_hash, record))
        except Exception as exc:  # row isolation is intentional
            invalid.append((position, record_hash, _reason_from_exception(exc)))

    source_times = [item[2].occurred_at_utc for item in parsed]
    source_min = min(source_times) if source_times else None
    source_max = max(source_times) if source_times else None
    with connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT status,imported_revision_count,duplicate_revision_count,
                       quarantined_revision_count,archive_fact_count,
                       encode(archive_reconciliation_hash,'hex')
                FROM market_data.history_import_batches
                WHERE import_batch_id=decode(%s,'hex')
                """,
                (batch_id,),
            )
            existing_batch = cursor.fetchone()
            if existing_batch is not None and existing_batch[0] == "RECONCILED":
                return {
                    "status": "pass",
                    "batch_id": batch_id,
                    "source_code": bundle.source_code,
                    "source_record_count": len(bundle.records),
                    "imported_revision_count": int(existing_batch[1]),
                    "duplicate_revision_count": int(existing_batch[2]),
                    "quarantined_revision_count": int(existing_batch[3]),
                    "archive_fact_count": int(existing_batch[4]),
                    "reconciliation_hash": existing_batch[5],
                    "no_op": True,
                }
            cursor.execute(
                """
                INSERT INTO market_data.history_import_batches(
                    import_batch_id,source_code,source_system,source_artifact_hash,
                    source_record_count,source_min_occurred_at_utc,
                    source_max_occurred_at_utc,status
                ) VALUES(decode(%s,'hex'),%s,%s,decode(%s,'hex'),%s,%s,%s,'RUNNING')
                ON CONFLICT(import_batch_id) DO UPDATE SET
                    status='RUNNING',completed_at_utc=NULL
                """,
                (
                    batch_id,
                    bundle.source_code,
                    bundle.source_system,
                    bundle.source_artifact_hash,
                    len(bundle.records),
                    source_min,
                    source_max,
                ),
            )

    for position, record_hash, reason in invalid:
        _quarantine(
            connection,
            batch_id=batch_id,
            position=position,
            source_code=bundle.source_code,
            record_hash=record_hash,
            reason_code=reason,
        )

    parsed.sort(
        key=lambda item: (
            item[2].logical_identity_hash,
            item[2].lineage.source_revision,
            item[2].occurred_at_utc,
        )
    )
    # Offers must exist before their outcome foreign keys are projected.
    parsed.sort(
        key=lambda item: item[2].payload.kind.endswith("OUTCOME")
        or item[2].payload.kind == "COIN_TRADE"
    )
    imported = 0
    duplicates = 0
    quarantined = len(invalid)
    accepted_latest: dict[str, HistoryFactRecordV1] = {}
    for position, record_hash, record in parsed:
        identity = record.logical_identity_hash
        try:
            with connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT source_revision,encode(record_hash,'hex')
                        FROM market_data.history_import_items
                        WHERE source_code=%s AND logical_identity_hash=decode(%s,'hex')
                        ORDER BY source_revision DESC
                        """,
                        (record.source_code, identity),
                    )
                    prior = cursor.fetchall()
                    same = next(
                        (row for row in prior if int(row[0]) == record.lineage.source_revision),
                        None,
                    )
                    if same is not None:
                        if same[1] != record_hash:
                            raise HistoryBackfillError("REVISION_CONTENT_CONFLICT")
                        duplicates += 1
                        previous = accepted_latest.get(identity)
                        if (
                            previous is None
                            or previous.lineage.source_revision
                            < record.lineage.source_revision
                        ):
                            accepted_latest[identity] = record
                        continue
                    if prior and record.lineage.source_revision < int(prior[0][0]):
                        raise HistoryBackfillError("REVISION_REGRESSION")
                published = build_and_publish_fact(
                    connection,
                    event_key=record.event_key,
                    origin_event_key=record.origin_event_key,
                    source_code=record.source_code,
                    occurred_at_utc=record.occurred_at_utc,
                    available_at_utc=record.available_at_utc,
                    parser_version=record.parser_version,
                    quality_state=record.quality_state,
                    quality_reason_codes=record.quality_reason_codes,
                    payload=record.payload,
                )
                _write_sensitive_archive(connection, record)
                disposition = "IMPORTED" if published.changed else "DUPLICATE"
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO market_data.history_import_items(
                            import_batch_id,source_position,source_code,
                            logical_identity_hash,source_revision,record_hash,
                            fact_id,archive_fact_revision,import_disposition
                        ) VALUES(decode(%s,'hex'),%s,%s,decode(%s,'hex'),%s,
                                 decode(%s,'hex'),decode(%s,'hex'),%s,%s)
                        """,
                        (
                            batch_id,
                            position,
                            record.source_code,
                            identity,
                            record.lineage.source_revision,
                            record_hash,
                            published.fact.fact_id,
                            published.fact.fact_revision,
                            disposition,
                        ),
                    )
                if published.changed:
                    imported += 1
                else:
                    duplicates += 1
            accepted_latest[identity] = record
        except Exception as exc:  # preserve the rest of the batch
            connection.rollback()
            try:
                import psycopg2

                fatal_database_errors = (
                    psycopg2.InterfaceError,
                    psycopg2.InternalError,
                    psycopg2.OperationalError,
                    psycopg2.ProgrammingError,
                )
            except ImportError:  # pragma: no cover - archive runtime always pins psycopg2
                fatal_database_errors = ()
            if isinstance(exc, fatal_database_errors):
                raise HistoryBackfillError("HISTORY_IMPORT_STORAGE_FAILURE") from exc
            quarantined += 1
            _quarantine(
                connection,
                batch_id=batch_id,
                position=position,
                source_code=bundle.source_code,
                record_hash=record_hash,
                reason_code=_reason_from_exception(exc),
                logical_identity_hash=identity,
                source_revision=record.lineage.source_revision,
            )

    source_material = sorted(
        (_fact_material(item) for item in accepted_latest.values()),
        key=lambda item: item["fact_id"],
    )
    archive_material = _archive_material(
        connection, [item["fact_id"] for item in source_material]
    )
    source_hash = content_hash(source_material)
    archive_hash = content_hash(archive_material)
    archive_times = [
        datetime.fromisoformat(item["occurred_at_utc"]) for item in archive_material
    ]
    reconciled = source_material == archive_material
    status = "RECONCILED" if reconciled else "FAILED"
    with connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE market_data.history_import_batches SET
                    source_reconciliation_hash=decode(%s,'hex'),
                    archive_fact_count=%s,
                    archive_min_occurred_at_utc=%s,
                    archive_max_occurred_at_utc=%s,
                    archive_reconciliation_hash=decode(%s,'hex'),
                    imported_revision_count=%s,
                    duplicate_revision_count=%s,
                    quarantined_revision_count=%s,
                    status=%s,completed_at_utc=clock_timestamp()
                WHERE import_batch_id=decode(%s,'hex')
                """,
                (
                    source_hash,
                    len(archive_material),
                    min(archive_times) if archive_times else None,
                    max(archive_times) if archive_times else None,
                    archive_hash,
                    imported,
                    duplicates,
                    quarantined,
                    status,
                    batch_id,
                ),
            )
    if not reconciled:
        raise HistoryBackfillError("HISTORY_RECONCILIATION_MISMATCH")
    return {
        "status": "pass",
        "batch_id": batch_id,
        "source_code": bundle.source_code,
        "source_record_count": len(bundle.records),
        "imported_revision_count": imported,
        "duplicate_revision_count": duplicates,
        "quarantined_revision_count": quarantined,
        "archive_fact_count": len(archive_material),
        "source_min_occurred_at_utc": source_min.isoformat() if source_min else None,
        "source_max_occurred_at_utc": source_max.isoformat() if source_max else None,
        "reconciliation_hash": archive_hash,
        "no_op": False,
    }


def export_bot_seed(connection, output_path: Path) -> dict[str, Any]:
    """Write a fact-only JSONL seed; raw text and identities never join this query."""

    if output_path.exists():
        raise HistoryBackfillError("bot_seed_target_exists")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT o.envelope
            FROM market_data.market_facts f
            JOIN market_data.market_fact_outbox o
              ON o.fact_id=f.fact_id AND o.fact_revision=f.fact_revision
            WHERE f.retention_class='PERMANENT'
            ORDER BY f.stream_id,f.source_sequence
            """
        )
        facts = [row[0] for row in cursor.fetchall()]
    for fact in facts:
        _scan_forbidden(fact)
    facts_hash = content_hash(facts)
    manifest = {
        "contract": "market_history_bot_seed/1.0",
        "fact_count": len(facts),
        "facts_hash": facts_hash,
        "contains_raw_telegram_history": False,
        "contains_participant_identity": False,
    }
    with output_path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n")
        for fact in facts:
            handle.write(
                json.dumps(fact, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                + "\n"
            )
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(output_path, 0o600)
    return manifest


def load_bundle(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
