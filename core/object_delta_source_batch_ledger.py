"""Pure source-side ledger and acknowledgement cursor contracts.

This module describes the durable decisions a future Object-Storage delta
publisher must make.  It deliberately does not open a database connection,
touch a filesystem, read credentials, contact Object Storage, or start a
worker.  In particular, an acknowledgement passed here is only structurally
valid; the future adapter must authenticate the receiver acknowledgement and
load all rows under transaction-scoped locks before calling these functions.

The ledger is append-only.  A retry can only replay an identical batch record;
it cannot replace its object receipt, writer term, range, or chain identity.
The acknowledgement cursor is the sole mutable source-side state and can only
advance over the next contiguous ledger batch.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


GENESIS_PRIOR_CHAIN_SHA256 = "0" * 64
SOURCE_BATCH_APPEND_ACTION_APPEND = "append"
SOURCE_BATCH_APPEND_ACTION_REPLAY = "replay"
OUTBOUND_ACK_ACTION_ADVANCE = "advance"
OUTBOUND_ACK_ACTION_REPLAY = "replay"

MAX_STREAM_SEQUENCE_IDS = 100_000
MAX_PAYLOAD_BYTES = 100 * 1024 * 1024 * 1024
WEBAPP_SITES = frozenset({"webapp_fi", "webapp_ir"})
_CAMPAIGN_OR_GENERATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")
_RELEASE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_LEASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_OBJECT_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/=-]{2,1023}$")
_VERSION_ID_RE = re.compile(r"^[A-Za-z0-9._~+/=-]{1,1024}$")


class ObjectDeltaSourceLedgerError(ValueError):
    """Raised when a source batch ledger or acknowledgement is unsafe."""


def _require_site(value: object, *, label: str) -> str:
    if not isinstance(value, str) or value not in WEBAPP_SITES:
        raise ObjectDeltaSourceLedgerError(f"{label} is invalid")
    return value


def _require_match(value: object, pattern: re.Pattern[str], *, label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ObjectDeltaSourceLedgerError(f"{label} is invalid")
    return value


def _require_positive_int(value: object, *, label: str, maximum: int | None = None) -> int:
    if type(value) is not int or value < 1 or (maximum is not None and value > maximum):
        raise ObjectDeltaSourceLedgerError(f"{label} is invalid")
    return value


def _require_nonnegative_int(value: object, *, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ObjectDeltaSourceLedgerError(f"{label} is invalid")
    return value


@dataclass(frozen=True)
class SourceStreamIdentity:
    """The source-owned identity of one append-only logical stream."""

    source_site: str
    destination_site: str
    campaign_id: str
    release_sha: str
    stream_generation_id: str

    def __post_init__(self) -> None:
        source_site = _require_site(self.source_site, label="source site")
        destination_site = _require_site(self.destination_site, label="destination site")
        if source_site == destination_site:
            raise ObjectDeltaSourceLedgerError("source and destination sites must differ")
        _require_match(self.campaign_id, _CAMPAIGN_OR_GENERATION_RE, label="campaign")
        _require_match(self.release_sha, _RELEASE_SHA_RE, label="release")
        _require_match(
            self.stream_generation_id,
            _CAMPAIGN_OR_GENERATION_RE,
            label="stream generation",
        )


@dataclass(frozen=True)
class SourceBatchLedgerEntry:
    """One immutable source batch plus the verified encrypted Object receipt.

    The future publisher must insert this only after it knows the exact Object
    key, VersionId, ciphertext digest, and byte size represented by the batch.
    On restart it must reuse the recorded entry instead of constructing a new
    batch for the same logical range.
    """

    stream: SourceStreamIdentity
    first_sequence: int
    last_sequence: int
    writer_epoch: int
    writer_lease_id: str
    prior_chain_sha256: str
    batch_sha256: str
    payload_sha256: str
    payload_bytes: int
    object_key: str
    object_version_id: str
    ciphertext_sha256: str
    ciphertext_bytes: int

    def __post_init__(self) -> None:
        if not isinstance(self.stream, SourceStreamIdentity):
            raise ObjectDeltaSourceLedgerError("source batch stream is invalid")
        first = _require_positive_int(self.first_sequence, label="source batch first sequence")
        last = _require_positive_int(self.last_sequence, label="source batch last sequence")
        if last < first or last - first + 1 > MAX_STREAM_SEQUENCE_IDS:
            raise ObjectDeltaSourceLedgerError("source batch sequence range is invalid")
        _require_positive_int(self.writer_epoch, label="source batch writer epoch")
        _require_match(self.writer_lease_id, _LEASE_ID_RE, label="source batch writer lease")
        _require_match(self.prior_chain_sha256, _SHA256_RE, label="source batch prior chain hash")
        _require_match(self.batch_sha256, _SHA256_RE, label="source batch hash")
        _require_match(self.payload_sha256, _SHA256_RE, label="source batch payload hash")
        _require_positive_int(
            self.payload_bytes,
            label="source batch payload bytes",
            maximum=MAX_PAYLOAD_BYTES,
        )
        _require_match(self.object_key, _OBJECT_KEY_RE, label="source batch object key")
        _require_match(self.object_version_id, _VERSION_ID_RE, label="source batch object version")
        _require_match(
            self.ciphertext_sha256,
            _SHA256_RE,
            label="source batch ciphertext hash",
        )
        _require_positive_int(
            self.ciphertext_bytes,
            label="source batch ciphertext bytes",
        )


@dataclass(frozen=True)
class OutboundAckCursor:
    """The one mutable, contiguous acknowledgement frontier for a stream."""

    stream: SourceStreamIdentity
    last_acknowledged_sequence: int
    last_acknowledged_batch_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.stream, SourceStreamIdentity):
            raise ObjectDeltaSourceLedgerError("outbound acknowledgement stream is invalid")
        sequence = _require_nonnegative_int(
            self.last_acknowledged_sequence,
            label="outbound acknowledgement sequence",
        )
        digest = _require_match(
            self.last_acknowledged_batch_sha256,
            _SHA256_RE,
            label="outbound acknowledgement batch hash",
        )
        if sequence == 0 and digest != GENESIS_PRIOR_CHAIN_SHA256:
            raise ObjectDeltaSourceLedgerError("genesis acknowledgement cursor hash is invalid")


@dataclass(frozen=True)
class SourceBatchAcknowledgement:
    """A structurally bound acknowledgement for exactly one ledger entry.

    It intentionally does not carry a signature.  Signature verification and
    fixed Object Storage endpoint/bucket binding belong to the future adapter,
    before this pure contract is used.
    """

    stream: SourceStreamIdentity
    first_sequence: int
    last_sequence: int
    batch_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.stream, SourceStreamIdentity):
            raise ObjectDeltaSourceLedgerError("outbound acknowledgement stream is invalid")
        first = _require_positive_int(self.first_sequence, label="outbound acknowledgement first sequence")
        last = _require_positive_int(self.last_sequence, label="outbound acknowledgement last sequence")
        if last < first:
            raise ObjectDeltaSourceLedgerError("outbound acknowledgement sequence range is invalid")
        _require_match(
            self.batch_sha256,
            _SHA256_RE,
            label="outbound acknowledgement batch hash",
        )


@dataclass(frozen=True)
class SourceBatchLedgerAppendPlan:
    """An append or exact replay decision with no persistence side effect."""

    action: str
    entry_to_insert: SourceBatchLedgerEntry | None


@dataclass(frozen=True)
class OutboundAckPlan:
    """A cursor advance or replay decision with no persistence side effect."""

    action: str
    cursor_to_write: OutboundAckCursor | None


REQUIRED_SOURCE_LEDGER_TRANSACTION_STEPS = (
    "authenticate the local writer term and construct canonical payload bytes before database work",
    "begin one caller-owned database transaction",
    "acquire a transaction-scoped advisory lock for the source stream in deterministic order",
    "select the terminal source ledger row and any same-range, same-batch, and same-object rows for update",
    "re-run plan_source_batch_ledger_append with the lock-scoped rows",
    "insert only the returned immutable ledger row; never update an existing row",
    "commit once with the source outbox state needed to reproduce the batch",
)

REQUIRED_OUTBOUND_ACK_TRANSACTION_STEPS = (
    "authenticate the receiver acknowledgement and bind it to the fixed receiver identity before database work",
    "begin one caller-owned database transaction",
    "acquire a transaction-scoped advisory lock for the source stream in deterministic order",
    "select the acknowledgement cursor and referenced immutable source ledger entry for update",
    "re-run plan_outbound_ack_cursor with the lock-scoped rows",
    "upsert only the returned forward cursor; do not delete or mutate source ledger rows",
    "commit once after the cursor succeeds",
)


def _require_same_stream(
    entry: SourceBatchLedgerEntry,
    stream: SourceStreamIdentity,
    *,
    label: str,
) -> None:
    if not isinstance(entry, SourceBatchLedgerEntry) or entry.stream != stream:
        raise ObjectDeltaSourceLedgerError(f"{label} does not match the source stream")


def plan_source_batch_ledger_append(
    *,
    candidate: SourceBatchLedgerEntry,
    previous_entry: SourceBatchLedgerEntry | None,
    existing_by_first_sequence: SourceBatchLedgerEntry | None,
    existing_by_batch_sha256: SourceBatchLedgerEntry | None,
    existing_by_object_version: SourceBatchLedgerEntry | None,
) -> SourceBatchLedgerAppendPlan:
    """Return a safe immutable ledger append or exact retry replay decision.

    ``previous_entry`` must be the stream's terminal row, and all existing-row
    parameters must have been obtained in one transaction after the source
    stream lock.  This function cannot discover omitted rows itself.
    """

    if not isinstance(candidate, SourceBatchLedgerEntry):
        raise ObjectDeltaSourceLedgerError("source batch candidate is invalid")
    existing = (
        existing_by_first_sequence,
        existing_by_batch_sha256,
        existing_by_object_version,
    )
    for value in existing:
        if value is not None:
            _require_same_stream(value, candidate.stream, label="existing source batch")

    present = tuple(value for value in existing if value is not None)
    if present:
        if any(value != candidate for value in present):
            raise ObjectDeltaSourceLedgerError("existing source batch conflicts with immutable retry")
        return SourceBatchLedgerAppendPlan(
            action=SOURCE_BATCH_APPEND_ACTION_REPLAY,
            entry_to_insert=None,
        )

    if previous_entry is None:
        if (
            candidate.first_sequence != 1
            or candidate.prior_chain_sha256 != GENESIS_PRIOR_CHAIN_SHA256
        ):
            raise ObjectDeltaSourceLedgerError(
                "new source stream must begin at genesis sequence one"
            )
    else:
        _require_same_stream(previous_entry, candidate.stream, label="source batch predecessor")
        if candidate.first_sequence != previous_entry.last_sequence + 1:
            raise ObjectDeltaSourceLedgerError("source batch is not the next logical sequence")
        if candidate.prior_chain_sha256 != previous_entry.batch_sha256:
            raise ObjectDeltaSourceLedgerError("source batch predecessor does not match chain")

    return SourceBatchLedgerAppendPlan(
        action=SOURCE_BATCH_APPEND_ACTION_APPEND,
        entry_to_insert=candidate,
    )


def plan_outbound_ack_cursor(
    *,
    cursor: OutboundAckCursor | None,
    acknowledgement: SourceBatchAcknowledgement,
    ledger_entry: SourceBatchLedgerEntry,
) -> OutboundAckPlan:
    """Advance an acknowledgement cursor only over one contiguous ledger row.

    A valid acknowledgement for a batch already below the frontier is a
    harmless replay.  A gap, overlap, different stream, or changed batch hash
    fails closed.  The returned plan has no I/O or database mutation.
    """

    if not isinstance(acknowledgement, SourceBatchAcknowledgement):
        raise ObjectDeltaSourceLedgerError("outbound acknowledgement is invalid")
    _require_same_stream(ledger_entry, acknowledgement.stream, label="acknowledged source batch")
    if (
        acknowledgement.first_sequence,
        acknowledgement.last_sequence,
        acknowledgement.batch_sha256,
    ) != (
        ledger_entry.first_sequence,
        ledger_entry.last_sequence,
        ledger_entry.batch_sha256,
    ):
        raise ObjectDeltaSourceLedgerError("outbound acknowledgement does not match immutable source batch")

    if cursor is None:
        current = OutboundAckCursor(
            stream=acknowledgement.stream,
            last_acknowledged_sequence=0,
            last_acknowledged_batch_sha256=GENESIS_PRIOR_CHAIN_SHA256,
        )
    else:
        if not isinstance(cursor, OutboundAckCursor) or cursor.stream != acknowledgement.stream:
            raise ObjectDeltaSourceLedgerError("outbound acknowledgement cursor does not match stream")
        current = cursor

    if ledger_entry.last_sequence <= current.last_acknowledged_sequence:
        if (
            ledger_entry.last_sequence == current.last_acknowledged_sequence
            and ledger_entry.batch_sha256 != current.last_acknowledged_batch_sha256
        ):
            raise ObjectDeltaSourceLedgerError("outbound acknowledgement terminal batch conflicts with cursor")
        return OutboundAckPlan(action=OUTBOUND_ACK_ACTION_REPLAY, cursor_to_write=None)

    if ledger_entry.first_sequence != current.last_acknowledged_sequence + 1:
        raise ObjectDeltaSourceLedgerError("outbound acknowledgement is not the next logical sequence")
    if ledger_entry.prior_chain_sha256 != current.last_acknowledged_batch_sha256:
        raise ObjectDeltaSourceLedgerError("outbound acknowledgement predecessor does not match cursor")
    return OutboundAckPlan(
        action=OUTBOUND_ACK_ACTION_ADVANCE,
        cursor_to_write=OutboundAckCursor(
            stream=acknowledgement.stream,
            last_acknowledged_sequence=ledger_entry.last_sequence,
            last_acknowledged_batch_sha256=ledger_entry.batch_sha256,
        ),
    )
