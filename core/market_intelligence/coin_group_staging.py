"""Bounded private staging for messages from the two coin trading groups.

This database is deliberately separate from the normalized Market Store.  It
temporarily retains the text and reply graph necessary to evaluate informal
offers, but only outside the repository checkout and for at most three days.
The final Market Store receives only opaque, privacy-minimized facts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import blake2b
from pathlib import Path
import re
import sqlite3

from .market_contracts import MarketStoreContractError, normalize_utc


COIN_GROUP_STAGING_SCHEMA_VERSION = 2
COIN_GROUP_STAGING_RETENTION = timedelta(days=3)
_MAX_MESSAGE_TEXT_BYTES = 32 * 1024
_MAX_DISPLAY_NAME_BYTES = 512
_TELEGRAM_ID = re.compile(r"^[1-9][0-9]{0,19}$")


class CoinGroupStagingError(RuntimeError):
    """Raised when ephemeral private staging cannot safely retain a message."""


@dataclass(frozen=True, slots=True)
class CoinGroupStagingMessage:
    """Transient private input; ``text`` is never sent to Market Store."""

    group_number: int
    message_id: int
    event_time_utc: datetime | str
    available_at_utc: datetime | str
    text: str
    reply_to_message_id: int | None = None
    sender_identity: str | None = None
    sender_telegram_id: str | None = None
    sender_display_name: str | None = None
    edited_at_utc: datetime | str | None = None


@dataclass(frozen=True, slots=True)
class StagedCoinGroupMessage:
    """A current temporary message version for local parsing/reconciliation."""

    group_number: int
    message_id: int
    event_time_utc: str
    available_at_utc: str
    text: str
    reply_to_message_id: int | None
    sender_digest: bytes | None
    edited_at_utc: str | None
    revision: int
    expires_at_utc: str
    # Research identity is additive metadata.  Keep it optional and append-only
    # in the constructor contract so older parser/linker callers remain valid.
    sender_telegram_id: str | None = None
    sender_display_name: str | None = None


_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS coin_group_staging_metadata (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    schema_version INTEGER NOT NULL,
    initialized_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS coin_group_staged_messages (
    group_number INTEGER NOT NULL CHECK(group_number IN (1, 2)),
    message_id INTEGER NOT NULL CHECK(message_id > 0),
    event_time_utc TEXT NOT NULL,
    available_at_utc TEXT NOT NULL,
    edited_at_utc TEXT,
    reply_to_message_id INTEGER CHECK(reply_to_message_id IS NULL OR reply_to_message_id > 0),
    sender_digest BLOB CHECK(sender_digest IS NULL OR length(sender_digest) = 32),
    sender_telegram_id TEXT,
    sender_display_name TEXT,
    message_text TEXT NOT NULL,
    content_digest BLOB NOT NULL CHECK(length(content_digest) = 32),
    revision INTEGER NOT NULL CHECK(revision > 0),
    first_staged_at_utc TEXT NOT NULL,
    last_staged_at_utc TEXT NOT NULL,
    expires_at_utc TEXT NOT NULL,
    PRIMARY KEY(group_number, message_id)
);

CREATE INDEX IF NOT EXISTS idx_coin_group_staged_messages_expiry
    ON coin_group_staged_messages(expires_at_utc);
CREATE INDEX IF NOT EXISTS idx_coin_group_staged_messages_reply
    ON coin_group_staged_messages(group_number, reply_to_message_id);

CREATE TABLE IF NOT EXISTS coin_group_fact_research_context (
    event_key BLOB PRIMARY KEY CHECK(length(event_key) BETWEEN 16 AND 64),
    group_number INTEGER NOT NULL CHECK(group_number IN (1,2)),
    root_message_id INTEGER NOT NULL CHECK(root_message_id > 0),
    requester_message_id INTEGER CHECK(
        requester_message_id IS NULL OR requester_message_id > 0
    ),
    expires_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_coin_group_fact_research_expiry
    ON coin_group_fact_research_context(expires_at_utc);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _as_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _sender_digest(value: str | None) -> bytes | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    return blake2b(
        normalized.encode("utf-8"), digest_size=32, person=b"coin-group-usr1"
    ).digest()


def _content_digest(
    *,
    group_number: int,
    message_id: int,
    event_time_utc: str,
    available_at_utc: str,
    edited_at_utc: str | None,
    reply_to_message_id: int | None,
    sender_digest: bytes | None,
    sender_telegram_id: str | None,
    sender_display_name: str | None,
    text: str,
) -> bytes:
    digest = blake2b(digest_size=32, person=b"coin-group-stg1")
    for value in (
        str(group_number),
        str(message_id),
        event_time_utc,
        available_at_utc,
        edited_at_utc or "",
        str(reply_to_message_id or ""),
        sender_digest or b"",
        sender_telegram_id or "",
        sender_display_name or "",
        text,
    ):
        encoded = value if isinstance(value, bytes) else str(value).encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    return digest.digest()


def assert_staging_path_outside_repository(
    path: Path | str,
    *,
    repository_root: Path | str,
) -> Path:
    """Reject raw private text storage in a Git checkout before opening it."""

    target = Path(path).expanduser().resolve()
    root = Path(repository_root).expanduser().resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return target
    raise CoinGroupStagingError("coin_group_staging_path_inside_repository")


def connect_coin_group_staging(
    path: Path | str,
    *,
    repository_root: Path | str | None = None,
) -> sqlite3.Connection:
    """Open a local staging DB; callers may enforce an external runtime path."""

    database = (
        assert_staging_path_outside_repository(path, repository_root=repository_root)
        if repository_root is not None
        else Path(path).expanduser().resolve()
    )
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = FULL")
    return connection


def initialize_coin_group_staging(connection: sqlite3.Connection) -> None:
    """Create/verify the short-lived schema without implicit migrations."""

    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'coin_group_staging_metadata'"
    ).fetchone()
    if row is None:
        connection.executescript(_SCHEMA)
        connection.execute(
            "INSERT INTO coin_group_staging_metadata(singleton, schema_version, initialized_at_utc) VALUES(1, ?, ?)",
            (COIN_GROUP_STAGING_SCHEMA_VERSION, _utc_now()),
        )
        connection.commit()
        return
    metadata = connection.execute(
        "SELECT schema_version FROM coin_group_staging_metadata WHERE singleton = 1"
    ).fetchone()
    if metadata is not None and int(metadata["schema_version"]) == 1:
        connection.executescript(
            """
            ALTER TABLE coin_group_staged_messages
              ADD COLUMN sender_telegram_id TEXT;
            ALTER TABLE coin_group_staged_messages
              ADD COLUMN sender_display_name TEXT;
            CREATE TABLE coin_group_fact_research_context (
                event_key BLOB PRIMARY KEY CHECK(length(event_key) BETWEEN 16 AND 64),
                group_number INTEGER NOT NULL CHECK(group_number IN (1,2)),
                root_message_id INTEGER NOT NULL CHECK(root_message_id > 0),
                requester_message_id INTEGER CHECK(
                    requester_message_id IS NULL OR requester_message_id > 0
                ),
                expires_at_utc TEXT NOT NULL
            );
            CREATE INDEX idx_coin_group_fact_research_expiry
                ON coin_group_fact_research_context(expires_at_utc);
            UPDATE coin_group_staging_metadata
               SET schema_version=2
             WHERE singleton=1;
            """
        )
        connection.commit()
        metadata = connection.execute(
            "SELECT schema_version FROM coin_group_staging_metadata WHERE singleton = 1"
        ).fetchone()
    if metadata is None or int(metadata["schema_version"]) != COIN_GROUP_STAGING_SCHEMA_VERSION:
        raise CoinGroupStagingError("coin_group_staging_schema_upgrade_required")


def _normalized_input(
    message: CoinGroupStagingMessage,
) -> tuple[
    int,
    int,
    str,
    str,
    str | None,
    int | None,
    bytes | None,
    str | None,
    str | None,
    str,
    bytes,
    str,
]:
    try:
        group_number = int(message.group_number)
        message_id = int(message.message_id)
    except (TypeError, ValueError) as exc:
        raise CoinGroupStagingError("coin_group_staging_message_identity_invalid") from exc
    if group_number not in {1, 2} or message_id <= 0:
        raise CoinGroupStagingError("coin_group_staging_message_identity_invalid")
    try:
        event_time = normalize_utc(message.event_time_utc, field_name="coin_group_event_time_utc")
        available_at = normalize_utc(
            message.available_at_utc, field_name="coin_group_available_at_utc"
        )
        edited_at = (
            normalize_utc(message.edited_at_utc, field_name="coin_group_edited_at_utc")
            if message.edited_at_utc is not None
            else None
        )
    except MarketStoreContractError as exc:
        raise CoinGroupStagingError(str(exc)) from exc
    if available_at < event_time or (edited_at is not None and edited_at < event_time):
        raise CoinGroupStagingError("coin_group_staging_timestamp_order_invalid")
    reply = message.reply_to_message_id
    if reply is not None:
        try:
            reply = int(reply)
        except (TypeError, ValueError) as exc:
            raise CoinGroupStagingError("coin_group_staging_reply_identity_invalid") from exc
        if reply <= 0:
            raise CoinGroupStagingError("coin_group_staging_reply_identity_invalid")
    text = str(message.text or "").strip()
    if not text or len(text.encode("utf-8")) > _MAX_MESSAGE_TEXT_BYTES:
        raise CoinGroupStagingError("coin_group_staging_text_invalid")
    sender = _sender_digest(message.sender_identity)
    telegram_id = str(message.sender_telegram_id or "").strip() or None
    if telegram_id is not None and not _TELEGRAM_ID.fullmatch(telegram_id):
        raise CoinGroupStagingError("coin_group_staging_sender_telegram_id_invalid")
    display_name = " ".join(str(message.sender_display_name or "").split()) or None
    if (
        display_name is not None
        and len(display_name.encode("utf-8")) > _MAX_DISPLAY_NAME_BYTES
    ):
        raise CoinGroupStagingError("coin_group_staging_sender_name_invalid")
    digest = _content_digest(
        group_number=group_number,
        message_id=message_id,
        event_time_utc=event_time,
        available_at_utc=available_at,
        edited_at_utc=edited_at,
        reply_to_message_id=reply,
        sender_digest=sender,
        sender_telegram_id=telegram_id,
        sender_display_name=display_name,
        text=text,
    )
    expires = (_as_datetime(available_at) + COIN_GROUP_STAGING_RETENTION).replace(
        microsecond=0
    ).isoformat().replace("+00:00", "Z")
    return (
        group_number,
        message_id,
        event_time,
        available_at,
        edited_at,
        reply,
        sender,
        telegram_id,
        display_name,
        text,
        digest,
        expires,
    )


def stage_coin_group_message(
    connection: sqlite3.Connection,
    message: CoinGroupStagingMessage,
    *,
    staged_at_utc: datetime | str | None = None,
) -> bool:
    """Insert a message or atomically replace it only when its source changed.

    Returns ``True`` for an inserted/edited source message and ``False`` for an
    idempotent replay.  The caller owns the transaction boundary.
    """

    (
        group_number,
        message_id,
        event_time,
        available_at,
        edited_at,
        reply,
        sender,
        telegram_id,
        display_name,
        text,
        digest,
        expires,
    ) = _normalized_input(message)
    staged_at = normalize_utc(
        staged_at_utc or _utc_now(), field_name="coin_group_staged_at_utc"
    )
    existing = connection.execute(
        "SELECT content_digest, revision FROM coin_group_staged_messages WHERE group_number = ? AND message_id = ?",
        (group_number, message_id),
    ).fetchone()
    if existing is not None and bytes(existing["content_digest"]) == digest:
        connection.execute(
            "UPDATE coin_group_staged_messages SET last_staged_at_utc = ? WHERE group_number = ? AND message_id = ?",
            (staged_at, group_number, message_id),
        )
        return False
    revision = int(existing["revision"]) + 1 if existing is not None else 1
    connection.execute(
        """
        INSERT INTO coin_group_staged_messages(
            group_number, message_id, event_time_utc, available_at_utc,
            edited_at_utc, reply_to_message_id, sender_digest,
            sender_telegram_id, sender_display_name, message_text,
            content_digest, revision, first_staged_at_utc, last_staged_at_utc,
            expires_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(group_number, message_id) DO UPDATE SET
            event_time_utc = excluded.event_time_utc,
            available_at_utc = excluded.available_at_utc,
            edited_at_utc = excluded.edited_at_utc,
            reply_to_message_id = excluded.reply_to_message_id,
            sender_digest = excluded.sender_digest,
            sender_telegram_id = excluded.sender_telegram_id,
            sender_display_name = excluded.sender_display_name,
            message_text = excluded.message_text,
            content_digest = excluded.content_digest,
            revision = excluded.revision,
            last_staged_at_utc = excluded.last_staged_at_utc,
            expires_at_utc = excluded.expires_at_utc
        """,
        (
            group_number, message_id, event_time, available_at, edited_at,
            reply, sender, telegram_id, display_name, text, digest, revision,
            staged_at, staged_at, expires,
        ),
    )
    return True


def list_current_staged_coin_group_messages(
    connection: sqlite3.Connection,
    *,
    as_of_utc: datetime | str,
    group_number: int | None = None,
) -> list[StagedCoinGroupMessage]:
    """Read unexpired staged rows in causal order for local reconciliation."""

    as_of = normalize_utc(as_of_utc, field_name="coin_group_staging_as_of_utc")
    clauses = ["expires_at_utc > ?", "available_at_utc <= ?"]
    params: list[object] = [as_of, as_of]
    if group_number is not None:
        if int(group_number) not in {1, 2}:
            raise CoinGroupStagingError("coin_group_staging_group_unsupported")
        clauses.append("group_number = ?")
        params.append(int(group_number))
    rows = connection.execute(
        f"""
        SELECT group_number, message_id, event_time_utc, available_at_utc,
               message_text, reply_to_message_id, sender_digest, edited_at_utc,
               sender_telegram_id, sender_display_name, revision, expires_at_utc
        FROM coin_group_staged_messages
        WHERE {' AND '.join(clauses)}
        ORDER BY event_time_utc ASC, message_id ASC
        """,
        params,
    ).fetchall()
    return [
        StagedCoinGroupMessage(
            group_number=int(row["group_number"]),
            message_id=int(row["message_id"]),
            event_time_utc=str(row["event_time_utc"]),
            available_at_utc=str(row["available_at_utc"]),
            text=str(row["message_text"]),
            reply_to_message_id=(int(row["reply_to_message_id"]) if row["reply_to_message_id"] is not None else None),
            sender_digest=(bytes(row["sender_digest"]) if row["sender_digest"] is not None else None),
            sender_telegram_id=(
                str(row["sender_telegram_id"])
                if row["sender_telegram_id"] is not None
                else None
            ),
            sender_display_name=(
                str(row["sender_display_name"])
                if row["sender_display_name"] is not None
                else None
            ),
            edited_at_utc=(str(row["edited_at_utc"]) if row["edited_at_utc"] is not None else None),
            revision=int(row["revision"]),
            expires_at_utc=str(row["expires_at_utc"]),
        )
        for row in rows
    ]


def delete_coin_group_staged_message(
    connection: sqlite3.Connection,
    *,
    group_number: int,
    message_id: int,
) -> bool:
    """Apply one source tombstone to the current private reply graph.

    The durable capture adapter keeps the tombstone itself.  This bounded
    staging database only needs to remove the current raw node; the next
    pipeline reconciliation rejects any offer/trade facts that depended on
    that node.  The caller owns the transaction boundary.
    """

    try:
        group = int(group_number)
        message = int(message_id)
    except (TypeError, ValueError) as exc:
        raise CoinGroupStagingError("coin_group_staging_message_identity_invalid") from exc
    if group not in {1, 2} or message <= 0:
        raise CoinGroupStagingError("coin_group_staging_message_identity_invalid")
    result = connection.execute(
        "DELETE FROM coin_group_staged_messages WHERE group_number = ? AND message_id = ?",
        (group, message),
    )
    return bool(result.rowcount)


def purge_expired_coin_group_staging(
    connection: sqlite3.Connection,
    *,
    as_of_utc: datetime | str,
) -> int:
    """Purge private text after the fixed retention horizon; caller commits."""

    as_of = normalize_utc(as_of_utc, field_name="coin_group_staging_as_of_utc")
    context = connection.execute(
        "DELETE FROM coin_group_fact_research_context WHERE expires_at_utc <= ?",
        (as_of,),
    )
    cursor = connection.execute(
        "DELETE FROM coin_group_staged_messages WHERE expires_at_utc <= ?", (as_of,)
    )
    return max(0, int(context.rowcount)) + max(0, int(cursor.rowcount))
