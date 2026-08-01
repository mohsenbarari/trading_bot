# Three-site data-plane decision: exact standby before Full Matrix

## Status

This document records a blocking architecture decision made during the local
three-site readiness work.  It is not a deployment instruction, does not
enable a service, and does not authorize a remote action.

The target contract is stricter than the existing Object-delta MVP:

1. WebApp-FI and WebApp-IR must have the same recoverable application data.
2. Exactly one site may accept authoritative writes at a time.
3. A promotion must not acknowledge data that cannot be recovered by the
   promoted writer.
4. Finland-to-Iran data movement must not require a direct FI-to-IR control
   connection; a private, versioned Object Storage route is the intended data
   path.
5. A complete Iran external-network partition must fence FI before IR is
   allowed to become writer.  An ambiguous partition must fail closed.

## Evidence that the current MVP cannot meet the contract

The current repository has 58 ORM tables.  The legacy sync registry classifies
23 as `SYNC`, 19 as `NO_SYNC`, and 3 as internal bookkeeping; the remaining
tables are Object-delta implementation state.  `OBJECT_DELTA_SYNC_TABLES`
contains the 23 `SYNC` tables, but the Object-delta receiver registry marks all
23 as `UNAVAILABLE`.  The only executable receiver handler is the deliberately
narrow `commodities` / `INSERT` natural-key ensure operation.

Therefore the existing Object-delta work is useful safety scaffolding, but it
is **not** a full mirror and must not be represented as one in preflight,
promotion, Full Matrix evidence, or operations documentation.

The 19 `NO_SYNC` tables also require an explicit continuity decision.  Some
are safely reconstructible local caches/receipts; others contain user-facing
messages, uploads, sessions, or side-effect state.  Leaving that decision
implicit would create a standby that is not equivalent to the writer.

The release-pinned inventory and its three unresolved promotion decisions are
frozen in [THREE_SITE_CONTINUITY_MATRIX.md](THREE_SITE_CONTINUITY_MATRIX.md).
That matrix is a blocker record, not an authorization to omit a legacy
`NO_SYNC` table from the physical standby.

## Decision

Do not extend the MVP one semantic table handler at a time as the path to an
exact active/standby pair.  That would require portable identities, reference
resolution, operation semantics, deletion policy, and side-effect isolation
for every persistent model; it also leaves non-SYNC state outside the mirror.

The replacement data plane is a **database-level physical PostgreSQL
baseline + ordered WAL design**, with a separately versioned content store for
uploads.  It must be designed as a new, default-off deployment unit rather
than retrofitted into the legacy direct-sync worker.

### PostgreSQL data plane

* A one-time, transaction-consistent PostgreSQL base backup establishes the
  standby.  It must be tied to a durable generation identifier, release,
  schema revision, and a source write fence.
* All later database state is represented by the single physical WAL order,
  rather than by per-table ORM translations.  This covers all database tables,
  sequences, indexes, and transactional metadata together.
* WAL and base-backup artifacts are encrypted before upload, immutable,
  versioned, content-hashed, retention-managed, and pulled by WA-IR from the
  private Object Storage endpoint.  No FI-to-IR SSH/SCP data path is allowed.
* WA-IR may serve only reads while it is a standby.  It must prove a durable
  replay position and the matching base/WAL generation before any promotion
  request can be considered.
* Reverse failback is the same protocol in the IR-to-FI direction; it is not a
  file copy or a reuse of an old FI volume.

Object Storage archival by itself is asynchronous.  It does **not** establish
zero RPO merely because WAL is uploaded often.  If the product requirement is
that every acknowledged FI write already exists at IR, the write admission
path must wait for a verified remote durable/replay acknowledgement through a
reviewed transport.  If that acknowledgement is unavailable, FI must fence
writes rather than silently continue.  Any looser acknowledgement policy is
an explicit bounded-RPO product decision and cannot be called "exact at the
moment".

### Blob, Redis, and session continuity

* User uploads and message files need immutable, content-addressed Object
  records referenced by the database.  A promotion gate must verify that all
  database-visible blobs at or before the eligible replay position are
  available through the Iran route.
* Audit-trail files are a separate append-only evidence stream.  They need a
  durable generation/cursor and immutable retention policy; copying a live
  Docker volume is not a failover protocol.
* Redis queues, locks, websocket fan-out, and rate limits are not authoritative
  data.  They must be reconstructed from PostgreSQL/outbox state after
  promotion, with idempotent workers and explicit duplicate suppression.
* Session, login, and one-time-token records need a written continuity policy:
  either they are intentionally invalidated at promotion or they move into the
  physical database state.  Neither choice may be implicit.
* External side-effect receipts remain append-only/idempotent; promotion must
  never re-send an already durably recorded effect merely because a worker
  restarted.

### Writer fencing and the third site

No two-site protocol can both preserve single-writer safety and automatically
decide a Finland/Iran partition.  The third control component must be an
independent **Iran-reachable witness/quorum member**, not merely Object
Storage or a host-lifecycle script.

For the desired national-cutoff behaviour, the intended quorum rule is:

* normal FI writer: FI plus the witness form the active quorum;
* Iran external cutoff: FI loses witness quorum and self-fences; IR plus the
  witness may issue a strictly newer fencing term;
* any state without a provable quorum, a current term, and an eligible replay
  position is read-only/unavailable.

The fencing term must be enforced at the application/database write boundary,
not only by stopping containers.  Promotion requires both a newer witnessed
term and proof that IR has replayed all writes that were eligible to be
acknowledged under the old term.

This requires a fresh topology decision if the current third server cannot be
reached from Iran during the national cutoff.  It is a correctness requirement,
not an availability optimization.

## Required gates before a Full Matrix can start

1. A release-pinned, destructive-test-only physical baseline/WAL deployment
   exists and has an independently verified pull-only Object Storage path.
2. A tested remote-ack or explicit bounded-RPO policy is encoded in writer
   admission; the selected policy is visible in campaign evidence.
3. All database state, uploads, Redis reconstruction, sessions, and external
   effects have an approved continuity classification and acceptance tests.
4. A quorum witness that is independently reachable from WA-IR during the
   intended Iran partition is proven by fresh read-only preflight.
5. FI and IR application writes are term-fenced before transaction commit;
   raw SQL, workers, WebSockets, migrations, and bot paths have their own
   covered boundaries or are disabled during transition.
6. Promotion and failback prove WAL/replay/base generation, blob availability,
   writer term, route state, and recovery idempotency before traffic changes.
7. A new non-retired Full Matrix driver has independent oracles for these
   invariants.  It may not use the retired historical runner.

Until every gate is satisfied, Full Matrix status is **not runnable**.  The
existing source/receiver Object-delta code remains default-off scaffolding and
must not be enabled as a substitute for this decision.
