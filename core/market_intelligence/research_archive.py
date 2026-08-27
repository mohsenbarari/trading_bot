"""Encrypted, privacy-bounded research material for selected Telegram facts.

The bot transport receives normalized Market Facts only.  Raw offer text and
group participant identities remain on the web archive, encrypted with a
dedicated key that is never reused for capture HMAC or network transport.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import base64
import binascii
import hmac
from pathlib import Path
import re
import secrets
import sqlite3
from typing import Iterable, Mapping


_KEY_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{2,95}$")
_RAW_SOURCES = frozenset(
    {
        "GROUP_1",
        "GROUP_2",
        "PRIVATE_GOLD_CHANNEL",
        "MELTED_AGGREGATE",
        "MELTED_FLOW",
    }
)
_PRIMARY_CAPTURE_SOURCE = "MELTED_PRIMARY_FLOW"
_ENVELOPE_MAGIC = b"research-archive-v1\0"
_MAX_RAW_TEXT_BYTES = 32 * 1024
_MAX_IDENTITY_BYTES = 1024


class ResearchArchiveError(RuntimeError):
    """A content-free research archive failure."""


@dataclass(frozen=True, slots=True)
class ResearchActor:
    telegram_id: str
    display_name: str | None


@dataclass(frozen=True, slots=True)
class ResearchFactContext:
    source_code: str
    source_message_id: int
    occurred_at_utc: str
    available_at_utc: str
    raw_text: str
    raw_kind: str
    actors: Mapping[str, ResearchActor]


class ResearchArchiveKey:
    """Encrypt-then-MAC envelope derived from one dedicated master secret."""

    def __init__(self, master_key: bytes, *, key_id: str) -> None:
        if len(master_key) < 32:
            raise ResearchArchiveError("research_archive_key_too_short")
        if not _KEY_ID.fullmatch(str(key_id)):
            raise ResearchArchiveError("research_archive_key_id_invalid")
        self.key_id = str(key_id)
        self._encryption_key = hmac.new(
            master_key, b"research-archive/encryption/v1", sha256
        ).digest()
        self._authentication_key = hmac.new(
            master_key, b"research-archive/authentication/v1", sha256
        ).digest()
        self._lookup_key = hmac.new(
            master_key, b"research-archive/lookup/v1", sha256
        ).digest()

    @classmethod
    def from_file(cls, path: Path, *, key_id: str) -> "ResearchArchiveKey":
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
                raise ResearchArchiveError("research_archive_key_unavailable")
            supplied = path.read_bytes().strip()
        except OSError as exc:
            raise ResearchArchiveError("research_archive_key_unavailable") from exc
        candidates = [supplied]
        try:
            candidates.insert(0, base64.b64decode(supplied, validate=True))
        except (ValueError, binascii.Error):
            pass
        key = next((item for item in candidates if 32 <= len(item) <= 128), None)
        if key is None:
            raise ResearchArchiveError("research_archive_key_invalid")
        return cls(key, key_id=key_id)

    def lookup_hmac(self, *, purpose: str, value: str) -> bytes:
        material = f"{purpose}\0{value}".encode("utf-8")
        return hmac.new(self._lookup_key, material, sha256).digest()

    def seal(self, plaintext: str, *, purpose: str) -> bytes:
        encoded = plaintext.encode("utf-8")
        limit = _MAX_RAW_TEXT_BYTES if purpose == "RAW_TEXT" else _MAX_IDENTITY_BYTES
        if not encoded or len(encoded) > limit:
            raise ResearchArchiveError("research_archive_plaintext_invalid")
        try:
            import pyaes
        except ImportError as exc:  # pragma: no cover - image dependency gate
            raise ResearchArchiveError("research_archive_cipher_unavailable") from exc
        nonce = secrets.token_bytes(16)
        cipher = pyaes.AESModeOfOperationCTR(
            self._encryption_key,
            counter=pyaes.Counter(int.from_bytes(nonce, "big")),
        )
        ciphertext = cipher.encrypt(encoded)
        aad = (
            _ENVELOPE_MAGIC
            + purpose.encode("ascii")
            + b"\0"
            + self.key_id.encode("ascii")
            + b"\0"
            + nonce
            + ciphertext
        )
        tag = hmac.new(self._authentication_key, aad, sha256).digest()
        return _ENVELOPE_MAGIC + nonce + ciphertext + tag

    def open(self, envelope: bytes, *, purpose: str) -> str:
        minimum = len(_ENVELOPE_MAGIC) + 16 + 32 + 1
        if len(envelope) < minimum or not envelope.startswith(_ENVELOPE_MAGIC):
            raise ResearchArchiveError("research_archive_ciphertext_invalid")
        payload = envelope[len(_ENVELOPE_MAGIC) :]
        nonce, body = payload[:16], payload[16:]
        ciphertext, supplied_tag = body[:-32], body[-32:]
        aad = (
            _ENVELOPE_MAGIC
            + purpose.encode("ascii")
            + b"\0"
            + self.key_id.encode("ascii")
            + b"\0"
            + nonce
            + ciphertext
        )
        expected_tag = hmac.new(self._authentication_key, aad, sha256).digest()
        if not hmac.compare_digest(supplied_tag, expected_tag):
            raise ResearchArchiveError("research_archive_authentication_failed")
        try:
            import pyaes
        except ImportError as exc:  # pragma: no cover - image dependency gate
            raise ResearchArchiveError("research_archive_cipher_unavailable") from exc
        cipher = pyaes.AESModeOfOperationCTR(
            self._encryption_key,
            counter=pyaes.Counter(int.from_bytes(nonce, "big")),
        )
        try:
            return cipher.decrypt(ciphertext).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ResearchArchiveError("research_archive_plaintext_invalid") from exc


def _context_rows(
    staging: sqlite3.Connection,
    event_keys: frozenset[bytes],
) -> dict[bytes, ResearchFactContext]:
    if not event_keys:
        return {}
    result: dict[bytes, ResearchFactContext] = {}
    # The tables contain at most the bounded three-day staging horizon.  Read
    # once per export cycle instead of issuing thousands of point queries.
    group_rows = staging.execute(
        """
        SELECT c.event_key,c.group_number,c.root_message_id,
               root.event_time_utc,root.available_at_utc,root.message_text,
               root.sender_telegram_id AS offerer_id,
               root.sender_display_name AS offerer_name,
               requester.sender_telegram_id AS requester_id,
               requester.sender_display_name AS requester_name
        FROM coin_group_fact_research_context AS c
        JOIN coin_group_staged_messages AS root
          ON root.group_number=c.group_number
         AND root.message_id=c.root_message_id
        LEFT JOIN coin_group_staged_messages AS requester
          ON requester.group_number=c.group_number
         AND requester.message_id=c.requester_message_id
        """
    ).fetchall()
    for row in group_rows:
        event_key = bytes(row["event_key"])
        if event_key not in event_keys:
            continue
        actors: dict[str, ResearchActor] = {}
        if row["offerer_id"] is not None:
            actors["OFFERER"] = ResearchActor(
                str(row["offerer_id"]),
                str(row["offerer_name"]) if row["offerer_name"] is not None else None,
            )
        if row["requester_id"] is not None:
            actors["REQUESTER"] = ResearchActor(
                str(row["requester_id"]),
                (
                    str(row["requester_name"])
                    if row["requester_name"] is not None
                    else None
                ),
            )
        result[event_key] = ResearchFactContext(
            source_code=f"GROUP_{int(row['group_number'])}",
            source_message_id=int(row["root_message_id"]),
            occurred_at_utc=str(row["event_time_utc"]),
            available_at_utc=str(row["available_at_utc"]),
            raw_text=str(row["message_text"]),
            raw_kind="OFFER_TEXT",
            actors=actors,
        )

    has_channel_projection = staging.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='capture_projection_keys'"
    ).fetchone()
    channel_rows = (
        staging.execute(
            """
            SELECT p.event_key,p.source_id,p.message_id,current.event_time_utc,
                   current.available_at_utc,current.message_text
            FROM capture_projection_keys AS p
            JOIN capture_market_messages AS current
              ON current.source_id=p.source_id
             AND current.message_id=p.message_id
            WHERE p.source_id IN (?,?,?)
            """,
            (_PRIMARY_CAPTURE_SOURCE, "MELTED_AGGREGATE", "MELTED_FLOW"),
        ).fetchall()
        if has_channel_projection is not None
        else []
    )
    for row in channel_rows:
        event_key = bytes(row["event_key"])
        if event_key not in event_keys:
            continue
        capture_source = str(row["source_id"])
        source_code = (
            "PRIVATE_GOLD_CHANNEL"
            if capture_source == _PRIMARY_CAPTURE_SOURCE
            else capture_source
        )
        text = str(row["message_text"])
        available = str(row["available_at_utc"])
        if capture_source == _PRIMARY_CAPTURE_SOURCE:
            original = staging.execute(
                """
                SELECT event_time_utc,available_at_utc,message_text
                FROM capture_market_message_revisions
                WHERE source_id=? AND message_id=?
                ORDER BY event_time_utc,available_at_utc,event_id
                LIMIT 1
                """,
                (capture_source, int(row["message_id"])),
            ).fetchone()
            if original is not None:
                text = str(original["message_text"])
                available = str(original["available_at_utc"])
        result[event_key] = ResearchFactContext(
            source_code=source_code,
            source_message_id=int(row["message_id"]),
            occurred_at_utc=str(row["event_time_utc"]),
            available_at_utc=available,
            raw_text=text,
            raw_kind=("OFFER_TEXT" if capture_source == _PRIMARY_CAPTURE_SOURCE else "SOURCE_TEXT"),
            actors={},
        )
    return result


def research_contexts_for_rows(
    staging: sqlite3.Connection,
    rows: Iterable[sqlite3.Row],
) -> dict[bytes, ResearchFactContext]:
    selected = {
        bytes(row["event_key"])
        for row in rows
        if str(row["source_code"]) in _RAW_SOURCES
    }
    return _context_rows(staging, frozenset(selected))


def archive_fact_research_context(
    cursor,
    *,
    fact_id: str,
    fact_revision: int,
    context: ResearchFactContext,
    key: ResearchArchiveKey,
) -> None:
    if context.source_code not in _RAW_SOURCES:
        raise ResearchArchiveError("research_archive_source_unsupported")
    message_key, plaintext_hash = archive_research_message(
        cursor,
        context=context,
        key=key,
    )
    link_research_message_to_fact(
        cursor,
        fact_id=fact_id,
        fact_revision=fact_revision,
        raw_message_key=message_key,
        plaintext_hash=plaintext_hash,
        raw_role=context.raw_kind,
    )
    archive_research_actors(
        cursor,
        fact_id=fact_id,
        actors=context.actors,
        key=key,
    )


def archive_research_message(
    cursor,
    *,
    context: ResearchFactContext,
    key: ResearchArchiveKey,
) -> tuple[bytes, bytes]:
    if context.source_code not in _RAW_SOURCES:
        raise ResearchArchiveError("research_archive_source_unsupported")
    raw_text = context.raw_text.strip()
    plaintext_hash = sha256(raw_text.encode("utf-8")).digest()
    message_key = key.lookup_hmac(
        purpose="RAW_MESSAGE",
        value=f"{context.source_code}:{context.source_message_id}",
    )
    cursor.execute(
        """
        INSERT INTO market_data.research_raw_messages(
          raw_message_key,plaintext_hash,source_code,occurred_at_utc,
          available_at_utc,raw_kind,ciphertext,encryption_key_id
        ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(raw_message_key,plaintext_hash) DO NOTHING
        """,
        (
            message_key,
            plaintext_hash,
            context.source_code,
            context.occurred_at_utc,
            context.available_at_utc,
            context.raw_kind,
            key.seal(raw_text, purpose="RAW_TEXT"),
            key.key_id,
        ),
    )
    return message_key, plaintext_hash


def link_research_message_to_fact(
    cursor,
    *,
    fact_id: str,
    fact_revision: int,
    raw_message_key: bytes,
    plaintext_hash: bytes,
    raw_role: str,
) -> None:
    cursor.execute(
        """
        INSERT INTO market_data.research_fact_raw_messages(
          fact_id,fact_revision,raw_message_key,plaintext_hash,raw_role
        ) VALUES(decode(%s,'hex'),%s,%s,%s,%s)
        ON CONFLICT(fact_id,fact_revision,raw_role) DO NOTHING
        """,
        (fact_id, fact_revision, raw_message_key, plaintext_hash, raw_role),
    )


def archive_research_actors(
    cursor,
    *,
    fact_id: str,
    actors: Mapping[str, ResearchActor],
    key: ResearchArchiveKey,
) -> None:
    for role, actor in actors.items():
        lookup = key.lookup_hmac(purpose="TELEGRAM_ID", value=actor.telegram_id)
        cursor.execute(
            """
            INSERT INTO market_data.market_actor_identities(
              fact_id,actor_role,telegram_id_ciphertext,
              telegram_id_lookup_hmac,display_name_ciphertext,encryption_key_id
            ) VALUES(decode(%s,'hex'),%s,%s,%s,%s,%s)
            ON CONFLICT(fact_id,actor_role) DO NOTHING
            """,
            (
                fact_id,
                role,
                key.seal(actor.telegram_id, purpose="TELEGRAM_ID"),
                lookup,
                (
                    key.seal(actor.display_name, purpose="DISPLAY_NAME")
                    if actor.display_name
                    else None
                ),
                key.key_id,
            ),
        )


__all__ = [
    "ResearchArchiveError",
    "ResearchArchiveKey",
    "ResearchFactContext",
    "archive_research_actors",
    "archive_research_message",
    "archive_fact_research_context",
    "link_research_message_to_fact",
    "research_contexts_for_rows",
]
