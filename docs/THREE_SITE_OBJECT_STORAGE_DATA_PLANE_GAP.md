# Three-site Object Storage data-plane audit

## Status and scope

**Status: `blocked / live preflight, installation, and destructive validation required`.**
This started as a local, read-only repository audit.  It did not contact Object Storage,
Arvan, a VPS, SSH, a token service, or production files, and it does not
authorize any such action.

The target is an FI-writer / IR-standby physical PostgreSQL data plane with
an Object-Storage-only FI-to-IR data route.  In particular, no step below
permits an FI-to-IR SSH, SCP, rsync, database connection, or control channel.
The desired contract remains:

1. one witnessed writer term at a time;
2. a physical PostgreSQL baseline plus one ordered WAL history for all DB
   state;
3. separately verifiable blobs for every database-visible upload; and
4. no claim of zero RPO unless the writer admission path has a real durable
   remote/replay acknowledgement.

This audit supplements
[THREE_SITE_DATA_PLANE_DECISION.md](THREE_SITE_DATA_PLANE_DECISION.md).  It
does not make the existing snapshot, Object-delta, or release-staging paths
ready for Full Matrix.

## Executive result

There are strong local primitives for encrypted, hash-bound, version-bound
Object Storage artifacts.  The original audit correctly found no live
physical base backup, WAL archiver, pull/replay loop, replay receipt publisher,
blob frontier, or promotion installation.  The old snapshot path remains a
logical `pg_dump` plus `uploads` archive path and must not be relabelled as
physical replication.

The shortest principled path is therefore a new, default-off
`physical-postgres-object-store` deployment unit that reuses the audited
security *properties* below, but has its own typed base/WAL/blob manifest and
receiver state machine.  It must not extend the 23-table Object-delta MVP or
the legacy snapshot CLI one artifact kind at a time.

## Subsequent local implementation status

The current branch now contains default-off, fail-closed **local boundaries**
for much of the protocol shape. They are deliberately not evidence of live
replication or readiness:

| Boundary now present | What it closes locally | What is still not proven |
| --- | --- | --- |
| `physical_wa_fi_postgres_archive_command` and the base/WAL spool/uploader adapters | A fixed root-only FI `archive_command` shape, safe local WAL snapshot, age encryption, create-only exact-version upload/readback, and fresh term recheck. | No adapter binary/config has been installed; no PostgreSQL or Object Storage operation has run. |
| `physical_wal_receiver_staging`, age-v1 FD bridge, and recovery readback collector | Exact encrypted pulls, private staging, and canonical recovery evidence for a future detached-PGDATA materialization step. | No base restore, WAL replay, recovery-signal installation, or standby startup has run. |
| `physical_wal_remote_ack_object_storage_transport` plus strict writer-response boundary | Separate encrypted request/receipt objects with Witness locators and a local durable-response coupling contract. | No live locator delivery/polling, receiver replay, database transaction integration, or real FI write acknowledgement has run. |
| Blob v2 spool/upload/exact-pull/pre-CAS acceptance and promotion coordinator contracts | Immutable Blob object/mapping pins survive the term handoff ordering problem. | No live database-visible Blob frontier or promotion CAS has run. |
| `physical_arvan_immutability_preflight` | Makes versioning, retention, credential separation, and denied-delete/exact-readback evidence a mandatory oracle slot. | The disposable provider probe has not contacted Arvan and no provider retention capability has been demonstrated. |

The Full-Matrix readiness oracle remains intentionally blocked until fresh
typed evidence from those **live** steps exists. In particular, no local test
may be translated into a claim that WA-IR is a standby or that strict zero RPO
is active.

## Reusable local components

| Component | Reusable property | Boundary that must remain explicit |
| --- | --- | --- |
| `scripts/manage_webapp_ir_snapshot.py` | Root-only inputs/workspace, age encryption, pinned Ed25519 manifest signatures, exact VersionId read-back, content hashes, conditional create, manifest-last commit, and private/versioned bucket checks.  See lines 1-19, 439-466, 651-690, and 826-947. | Its manifest is hard-coded to `pg_dump`/`uploads`/optional audit and its consumer selects a newest logical snapshot.  Reuse only extracted low-level behavior or a reviewed copy with a new schema. |
| `scripts/manage_webapp_ir_artifact_stage.py` | Immutable root-only input snapshot before encryption, signed release manifest, path-style S3 client, exact version-bound presigned GET validation, no redirects/proxies, bounded download, and no-replace candidate promotion.  See lines 643-666, 1018-1069, 1288-1310, 1335-1418, and 1721-1900. | It is a release-artifact staging protocol: one detached bundle is driven by a short-lived manifest URL and release/campaign bindings.  It is not a continuous WAL receiver. |
| `scripts/manage_webapp_fi_source_transport.py` and `scripts/webapp_fi_source_transport_contract.py` | A controller can issue an FI direct Object-Storage PUT without distributing S3 credentials; the controller later performs exact read-back.  It supports a 100-GiB upper bound and keeps URLs transient.  See source transport lines 991-1038 and 1382-1434. | The allowed directions/object kinds are a closed source-phase allowlist (`bootstrap`, static assets, image, evidence) at contract lines 138-155.  It currently cannot carry base/WAL/blob data and must not be widened implicitly. |
| `scripts/prepare_webapp_ir_artifact_bundle.py` and `scripts/webapp_ir_image_archive_contract.py` | Exact release/Docker image archive provenance and isolated Docker tags. | Release images are control-plane artifacts, not database or user-data replication. |
| `core/physical_wal_promotion_gate.py` | A local pure admission contract binds signed source durability, receiver replay, blob frontier, continuity artifact, prior activation, a newer witnessed term, and—on the strict path—a separately verified signed pull-plane request/receipt pair for the exact route, prior term, baseline, acknowledged WAL frontier, and blob frontier. | It performs no I/O or promotion by design.  The signed pair is evidence only: it does not durably record replay, delay an application commit, fence the former writer, or consume a Witness term.  It is not an uploader, receiver, archive command, or writer-start capability. |

The snapshot primitive also now rejects all SDK-visible SSE/KMS/SSE-C/bucket-key
response fields at both PUT and GET, not only one `ServerSideEncryption`
field.  This aligns its no-provider-encryption contract with the stronger
artifact-stage primitive; it is covered by the focused snapshot suite.

## Existing paths that are not the physical data plane

### Logical snapshot path

`scripts/create_webapp_fi_snapshot_artifacts.py` explicitly captures a
read-only custom-format `pg_dump` and tarred uploads (lines 1-35 and 362-452).
`scripts/manage_webapp_ir_snapshot.py` validates the `PGDMP` header and fixed
archive formats before publishing (lines 1218-1315).  The resulting old
standby timer restores a new candidate from that snapshot; it has no WAL
receiver.  Its 15--30 second freshness policy is a bounded-RPO snapshot
policy, not an acknowledgement that IR has replayed each acknowledged write.

The repository has no tracked configuration or executable use of
`wal_level`, `archive_mode`, `archive_command`, `restore_command`,
`pg_basebackup`, `pg_receivewal`, replication slots, `recovery.signal`, or a
synchronous standby setting.  The only current matches are snapshot service
names and the new pure promotion-admission vocabulary.

### Release artifact stage

The artifact-stage consumer validates one exact, encrypted release bundle and
then stages it in a detached directory.  Its signed manifest contains
short-lived, version-bound artifact URLs (artifact-stage lines 1721-1847).
That is appropriate for a one-off release/image bootstrap.  It is not an
always-on pull protocol: a continuously generated WAL stream cannot depend on
an old one-shot presigned manifest URL or on release-specific artifact names.

Its publisher first makes an entire immutable plaintext workspace snapshot,
then age-encrypts it and read-backs the ciphertext.  That is excellent for
small-to-medium release artifacts, but an unchunked physical base backup
would require substantial coexisting disk space.  The physical path must use
measured capacity admission and chunk/segment-aware transfer rather than
assuming a nominal data volume is sufficient.

### Source-phase FI transport

The FI source transport is deliberately narrow.  Its contract permits only
five source-phase routes and the FI-originated large object is an application
image sent to the controller, not a database/WAL stream.  Although its
transient presigned PUT pattern is useful reference material, widening that
allowlist would be a security-sensitive new protocol, not configuration.

### Operational backup

`scripts/run_production_backup.py` produces a logical PostgreSQL dump plus
Redis/uploads/audit archives (lines 1-8 and 154-279).  Its optional transfer
helper uses SCP (lines 346-368).  It is a backup/recovery tool only and is
explicitly excluded from the FI-to-IR data plane.

## Object Storage guarantees and missing guarantees

### What current code verifies locally

Before upload/consume, the reusable primitives verify:

- bucket versioning is enabled and ACL grants only the canonical owner;
- a newly allocated object key has no prior version or delete marker;
- creation uses `If-None-Match: *`, returns a non-null VersionId, and leaves
  exactly one object version with no delete marker;
- ciphertext is age-encrypted, content-hashed, then immediately read back by
  exact VersionId;
- a signed manifest binds plaintext and ciphertext hashes, byte counts,
  object keys, exact versions, source/destination/release identity; and
- the consumer validates the source key pin, exact version, hash and size
  before decrypting a fresh root-only candidate.

This gives useful corruption/replay/reused-key defenses and permits a true
**pull** from the Object Storage origin without an FI-to-IR host connection.

### P0: versioning is not proven retention/immutability

The code checks versioning and ACL, but it does not prove provider-side
retention/object-lock semantics, a delete-deny policy, or the separation of
writer/uploader/receiver credentials.  Versioning alone does not establish
that a privileged credential cannot create a delete marker or permanently
remove an old version.

Before physical data is entrusted to this path, a fresh disposable-bucket
preflight must produce non-secret evidence for all of the following:

1. versioning and private ACL as above;
2. retention/object-lock capability **or** a provider-verified immutable
   retention equivalent;
3. no-delete/no-overwrite credentials for FI writer, IR reader, and any
   controller separately; and
4. exact-version retrieval after a deliberately attempted disallowed delete
   in the disposable test bucket.

If the provider cannot offer retention/object-lock equivalence, the design
must call the resulting recovery promise *versioned best effort*, not
immutable archival.

### P0: archive-only is not zero RPO

An uploaded WAL segment proves neither that WA-IR has replayed it nor that a
write was held until it became recoverable remotely.  Upload cadence, a short
snapshot age, or a frequent Object Storage poll therefore cannot be called
zero RPO.

For an exact acknowledged-write contract, the writer admission boundary needs
a reviewed remote durable/replay acknowledgement tied to the same witnessed
term and base/WAL generation.  If that acknowledgement disappears, FI must
fence writes before acknowledging more work.  A native PostgreSQL
`remote_apply` setup requires a live standby connection; an Object-Storage
route instead needs a separately designed, signed replay-receipt/admission
protocol.  It must not borrow the native name without proving equivalent
semantics.  Otherwise the only honest policy is explicitly bounded RPO.

The local promotion gate now rejects a strict generic claim unless it is also
given a verified, fresh signed source-request/destination-receipt pair bound
to that exact active route, previous Witness term, baseline, target WAL LSN,
blob frontier, manifest set, and immutable Object versions.  This closes a
local evidence-substitution gap; it still is **not** the runtime replay ledger
or the writer admission implementation.  Those two durable boundaries remain
mandatory before a strict claim can be made.

### P0: blobs are outside PostgreSQL WAL

The existing logical snapshot archives uploads, but there is no
content-addressed Object Storage blob publisher/receiver or database-to-blob
frontier proof.  A physical DB replay can therefore point at a file which IR
cannot fetch.  The continuity matrix's in-flight upload decision remains a
promotion blocker.  The blob route needs independent immutable manifests,
hashes, availability receipts, retention, and an LSN/cursor frontier no
earlier than the proposed promotion point.

## Required physical Object Storage protocol

The following is an implementation order, not a deployment runbook.

1. **Typed local contracts and fixture tests.**  Define a versioned physical
   baseline manifest, ordered WAL-segment manifest, replay receipt, blob
   object manifest, and continuity bundle.  Bind each to campaign/release,
   base generation, timeline, LSN range, exact Object Storage version/hash,
   source key, receiver/controller key, and witnessed term.  Reject duplicate
   JSON keys, mutable paths, holes, timeline regressions, and receipt replay.

2. **FI local physical producer.**  Add a default-off deployment unit which
   establishes a transaction-consistent physical base backup locally and
   records its start/stop/recovery WAL positions.  Enable a reviewed local
   WAL archive spool with bounded backpressure, ordered segment inventory,
   timeline handling, and a fence when its safety budget is exceeded.  The
   producer uploads only to Object Storage; it never connects to WA-IR.

3. **New neutral Object Storage adapter.**  Extract or reimplement the
   reviewed create-only/versioned/age/hash/read-back behavior with a physical
   schema.  Use immutable batch manifests uploaded last.  For WAL, make each
   segment/manifest independently resumable and verify a contiguous ordered
   chain before any receiver state advances.  Do not invoke the old snapshot
   or release-stage CLI as an adapter.

4. **WA-IR pull-only receiver.**  Preposition a root-owned IR pull agent with
   the pinned endpoint/key/identity and minimally scoped Object Storage read
   capability (or an equally bounded fresh capability mechanism).  It polls
   Object Storage directly, restores the physical base into a new detached
   PostgreSQL data directory, applies only contiguous verified WAL, and
   records signed replay evidence.  It must not start an application writer
   or use FI SSH.

5. **Blob plane.**  Store finalized blobs by content hash and publish an
   append-only manifest bound to the DB/WAL frontier.  IR must verify
   availability for every DB-visible blob at the eligible frontier.  Adopt
   an explicit policy for in-flight upload sessions before any promotion
   claim.

6. **Acknowledgement and writer fencing.**  Implement the selected strict or
   bounded-RPO policy at every application/worker write acknowledgement
   boundary.  Feed the signed durability/replay/blob receipts to the physical
   promotion gate only after live verification.  A local `eligible` result is
   followed by Witness recheck, durable compare-and-swap/term consumption,
   old-writer fence, and atomic installation; it is never itself permission
   to start a writer.

7. **Fresh read-only preflight, destructive staging, then Full Matrix.**
   Verify the provider retention policy, all local image/tool versions,
   credentials scopes, Object Storage reachability from IR, base restore,
   WAL gap/reorder/corruption behavior, blob holes, delayed receipts, FI
   fencing, IR promotion, and reverse failback.  Only then can a new,
   non-retired Full Matrix driver start.

## Explicit non-goals and guards

- Do not enable the Object-delta receiver as a full-mirror substitute.
- Do not alter legacy snapshot timers into a physical replication system.
- Do not use direct FI-to-IR SSH/SCP/rsync, a direct PostgreSQL replication
  connection, or a controller URL copied into durable state as a shortcut.
- Do not delete old base/WAL/blob objects until the new retention and
  failback policy has been independently tested.
- Do not treat Redis as authoritative; rebuild it from physical PostgreSQL
  state/outbox under the new term.
- Do not mark Object Storage, standby, promotion, preflight, or Full Matrix
  as ready from this audit.

## Local audit evidence

The audit ran a repository-wide search for physical PostgreSQL/WAL tooling,
reviewed the paths named above, and added the no-provider-encryption response
hardening to the legacy snapshot primitive.  The focused command was:

```text
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -B -m unittest tests.test_manage_webapp_ir_snapshot -v
```

It passed **43 tests**.  No network, SSH, token, Object Storage, VPS, Docker,
or production-file action occurred.
