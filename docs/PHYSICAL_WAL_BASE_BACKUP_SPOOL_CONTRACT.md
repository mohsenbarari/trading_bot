# Physical WAL base-backup spool contract

`core.physical_wal_base_backup_spool` is a local-only, default-off boundary
for one completed physical PostgreSQL base-backup **file**. It is deliberately
not a base-backup producer: it does not invoke PostgreSQL, Docker, a shell,
encryption, Object Storage, or a peer server.

## Admission

The caller first supplies an opaque `VerifiedPhysicalWalBaseBackupBinding`.
It binds a completed artifact name, exact plaintext hash/size and completion
attestation to these lineage facts:

- one ordered distinct route: `webapp_fi → webapp_ir` or
  `webapp_ir → webapp_fi`;
- the source-held, live, signature-verified Witness term;
- campaign, release, baseline generation, system identifier, timeline and
  fixed 16 MiB WAL geometry;
- baseline LSN, aligned WAL-chain start and base-backup end LSN; and
- the age recipient pinned for that route's destination.

The spool accepts neither a `PGDATA` directory nor a generic path. The file is
opened below one fixed, absolute, non-symlink source root. The source root and
artifact must be private and owned by root or the executing trusted producer.
The private spool root and every child directory must be root-owned with exact
mode `0700`.

## Local capture and capacity

Before reading the source artifact, the spool checks `statvfs` free space for
the exact artifact byte count plus a configured nonzero reserve. It copies to
a private temporary file, verifies the source identity/size/hash before and
after capture, fsyncs, and publishes a content-addressed immutable snapshot.
No live PostgreSQL data directory is ever read.

The handoff descriptor and deterministic completion record are immutable local
files. A valid completion record lets a later retry reuse the snapshot without
calling the uploader again. Snapshot integrity and the Witness term are
rechecked before a new completion or successful retry return.

## Uploader boundary and claims

The uploader is mandatory and injected. Its receipt must bind exactly the
descriptor hash, deterministic object key, immutable object version,
ciphertext digest/size, `age-v1`, the pinned destination recipient, and
`versioned_create_only_readback_v1`.

Completion is archive/recovery evidence only. It is explicitly not proof of
PostgreSQL `remote_apply`, a strict acknowledgement, a standby replay state,
a promotion right, or writer authority. Receiver staging and recovery replay
preflight remain separate subsequent boundaries.
