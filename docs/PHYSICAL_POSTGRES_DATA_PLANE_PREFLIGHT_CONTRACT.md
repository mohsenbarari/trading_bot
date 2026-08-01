# Physical PostgreSQL data-plane preflight contract

## Scope and status

`core/physical_postgres_data_plane_preflight.py` is a pure, default-off
contract for a future read-only preflight adapter.  It does not open
PostgreSQL, run `SHOW`, read a file, invoke Docker/SSH, access Object Storage,
create a replication slot, start recovery, or change writer authority.

Its only purpose is to turn bounded, canonical, non-secret evidence from a
future adapter into one of two observations:

* `observed`: the supplied FI-primary / IR-standby snapshots match the exact
  selected posture at the caller-supplied clock instant;
* `blocked`: coherent snapshots were supplied, but they do not make the
  requested posture.  Malformed, non-canonical, stale-unusable, or unbound
  raw inputs fail closed before a result is made.

Neither result authorizes a deployment, a writer, a PostgreSQL promotion, or
a traffic change.  A real transition still requires a live Witness recheck,
durable single-use term transition, real replication-health probe, database
write fencing, blob continuity proof, and a promotion-specific gate.

## Evidence boundary

The future read-only adapter supplies one `PhysicalPostgresReadbackEvidence`
per site.  It contains:

* canonical UTF-8 JSON, capped at 32 KiB;
* a SHA-256 that must bind exactly those bytes;
* typed provenance: `site`, the fixed read-only collector identity, and a
  fixed logical evidence path.

Duplicate JSON keys, whitespace/non-canonical encoding, hidden URLs,
credentials, token/secret-shaped values, unapproved collector identities,
and hash/provenance mismatches are rejected.  The schema stores only an
archive/restore command *identity* plus SHA-256.  It never accepts or emits a
command string, URL, password, access key, or Object Storage location.

The raw evidence records, for both sites:

* `wal_level`, `archive_mode`, archive and restore command identity/hash,
  `max_wal_senders`, `max_replication_slots`, `hot_standby`, and
  `synchronous_commit`;
* explicit native PostgreSQL synchronous-receiver state.  It must be
  `disabled` for this Object-Storage-only FI-to-IR route; a named receiver,
  replication slot, or `remote_apply` claim is rejected rather than treated
  as a shortcut;
* timeline, base generation ID, base-backup hash, source/archive/replay/
  acknowledged WAL frontiers;
* release, schema revision, and the current Witness-fenced term projection;
* the non-negotiable route assumptions below.

An independently trusted typed binding supplies the expected release, schema,
term, baseline, and per-site archive/restore hashes.  The future strict
runtime must add its own signed Object-Storage replay-ledger and
writer-admission identity binding; this preflight intentionally has no native
receiver identity.  Readback values must match the trusted binding; two hosts
merely agreeing with each other is not enough.

## Fixed transport assumptions

Every accepted readback states all of the following:

* archive transport is `private-versioned-object-storage`;
* WA-IR obtains Object Storage artifacts by `pull-only`;
* direct FI-to-IR **control** is `forbidden`.

These are evidence labels, not a transport implementation.  In particular,
they do not create a control connection, a remote filesystem path, or a
permission for FI to SSH/SCP into IR.

## Two intentionally different durability profiles

### `archive-bounded-rpo`

This profile accepts only `wal_delivery_mode=archive-only`, with Object
Storage archival and WA-IR pull recovery.  It requires a physical baseline,
ordered WAL/archive frontiers, an IR replay frontier, and the release/schema/
term/base binding.  It is explicitly a bounded-RPO recovery posture: a write
may be locally acknowledged before it appears in an archive object or has
been replayed by WA-IR.

The profile must not present a synchronous receiver or `remote_apply` as if
Object Storage archive were a PostgreSQL remote acknowledgement.

### `strict-zero-loss`

This profile describes the target strict acknowledgement semantics, but the
current preflight deliberately cannot observe it as ready.  It requires the
Object-Storage route label
`wal_delivery_mode=strict-object-storage-remote-durable-replay`, local WAL
durability (`synchronous_commit=on`), no native PostgreSQL synchronous
receiver, and an IR replay frontier at or beyond FI's recorded acknowledged
WAL frontier.  It still returns `blocked` with
`strict-remote-durable-replay-runtime-not-implemented`.

That is intentional: the missing runtime must prove the exact signed
Object-Storage pull receipt, durable receiver/replay ledger, writer-admission
hook, and loss-of-ack write fence for the same term/base/blob frontier.
`remote_apply`, `primary_conninfo`, a replication slot, or a direct FI-to-IR
database connection are incompatible with this architecture and are rejected.
An archive success report is never a substitute for the durable replay
acknowledgement.

## Normal posture covered today

This first contract covers only the normal role:

* WA-FI is `primary`, holds the expected FI Witness term, has a source and
  acknowledged WAL frontier;
* WA-IR is `standby`, has `hot_standby=on`, `archive_mode=always`, the same
  release/schema/term/timeline/base generation, and a replay frontier;
* both nodes advertise the pull-only Object Storage / no-direct-control
  assumptions.

Promotion and reverse failback require a separate fresh term plus their own
WAL/base/blob continuity gate.  This contract is intentionally not that gate.
