# WA-FI PostgreSQL Object-Storage handoff runtime contract

`core.physical_wa_fi_postgres_object_storage_handoff_runtime` is the
root-only, default-disabled bridge from the existing PostgreSQL recovery
spools to the existing encrypted immutable Object-Storage uploader.

It closes one narrow data-plane gap only:

```text
typed WA-FI helper capture ─┐
                            ├─> local immutable spool snapshot/descriptor
WA-FI WAL spool ────────────┘        │
                                      ▼
                         exact age-v1 encryption
                                      │
                                      ▼
                     FI-only create-only S3 PUT + exact VersionId readback
                                      │
                                      ▼
                  existing immutable local spool completion record/manifest
                                      │
                                      ▼
                       WA-IR private Object-Storage exact pull (separate)
```

It is not a release, deployment, writer, promotion, remote-apply, strict
acknowledgement, failover, Full-Matrix, Docker, SSH, PostgreSQL-control, or
direct FI-to-IR transport authority.

## Entry points

`RootOwnedWaFiPostgresObjectStorageHandoff` is constructed from an explicit
`RootOwnedWaFiPostgresObjectStorageHandoffConfig` and a root-runtime clock.
Construction is inert: it opens no credential file, SDK client, socket, age
binary, Docker/SSH connection, or PostgreSQL connection.

The only execution entry points are:

- `wal_uploader()` supplies the narrow uploader protocol expected by
  `physical_wal_archive_spool` / the already-reviewed WA-FI archive-command
  boundary.  The caller supplies a canonical WAL snapshot/descriptor only
  through that existing spool protocol.
- `base_backup_uploader()` supplies the corresponding narrow protocol for
  `physical_wal_base_backup_spool`.
- `publish_helper_base_backup()` accepts only an opaque,
  verified `PhysicalWaFiPostgresHelperCaptureBridgeHandoff`, revalidates it,
  takes its fixed capture root and verified base-backup binding, then invokes
  the existing base-backup spool.  It has no arbitrary source-path argument.

The policy contains local workspace/spool roots, the already pinned WA-IR age
recipient, and an object-kind byte bound.  It deliberately has no endpoint,
bucket, key, URL, host, access key, secret key, session, proxy, direct-site
control, or release selector.  Source/destination are hard-pinned to
`webapp_fi → webapp_ir`; direct control is `forbidden`; destination ingest is
`pull-only`.

## Credential and route boundary

The runtime accepts only the exact
`RootOwnedArvanS3SeparatedClientFactory`.  Before each upload it asks that
factory to admit a fresh opaque paired immutability preflight.  The preflight
must still bind the exact FI→IR route, factory-owned endpoint/region/bucket,
private/versioned retention posture, and distinct FI-publisher / IR-receiver
public identity fingerprints.

The subsequent factory callback:

1. rechecks that admission and its freshness;
2. opens **only** the fixed root-owned FI publisher credential file;
3. checks the current FI fingerprint against the preflight FI pin and rejects
   equality with the independently verified IR pin;
4. creates one transient S3v4 path-style client inside the factory; and
5. passes a private wrapper to the local uploader operation.

The normal publication path does not open the IR receiver secret.  The
separate paired preflight remains the mechanism that proves the identities
are distinct.  The wrapper has only the object-storage methods required by
the existing uploader: private-bucket checks, exact-key version history,
conditional create-only put, and exact-version HEAD/GET.  It exposes no
delete, broad list, generic session, raw client, endpoint switch, or client
factory.

The factory owns region/bucket injection inside its synchronous callback.  A
spool descriptor, not a runtime caller, derives the immutable object key.  A
foreign/historical key is rejected before PUT; a pre-existing version or
delete marker causes a fail-closed refusal rather than an overwrite.

## Encryption and receipt invariants

The runtime does not reimplement cryptography or S3 receipt validation.  It
instantiates the existing root-controlled `PhysicalAgeV1Encryptor` (or an
explicit root test/runtime seam) and delegates to exactly one of:

- `PhysicalWalObjectStorageUploader` for WAL; or
- `PhysicalWalBaseBackupObjectStorageUploader` for a base backup.

Those contracts enforce, in order:

1. a root-owned immutable local snapshot under the fixed spool root;
2. one pinned recipient and age-v1 ciphertext header;
3. private canonical-owner-only bucket and enabled versioning;
4. exact-key history absence;
5. `IfNoneMatch="*"` create-only encrypted PUT with a returned VersionId;
6. exact single-version history; and
7. exact-version HEAD and streamed GET hash/byte/metadata readback.

For a successful WAL handoff, `physical_wal_archive_spool` writes its
immutable upload manifest.  For a successful typed-helper base backup,
`physical_wal_base_backup_spool` writes its immutable completed record.  The
returned receipt/result is recovery-material evidence only; it does not prove
remote replay or permit any role transition.

The paired preflight admission is rechecked after upload.  A stale preflight,
clock regression, recipient mismatch, typed-helper revalidation failure,
credential drift, or any provider/upload/readback error returns a fixed local
failure and never upgrades authority.

## Required live gates before use

This code is intentionally not a deployment command.  A real run still needs
separately provisioned root-owned local directories and credentials,
root-controlled `/usr/bin/age`, a fresh real paired Arvan immutability
preflight, real FI publisher IAM enforcement, the WA-IR exact-pull receiver
boundary, Witness/term validity, clean release/deployment gates, and the
separate executable Full-Matrix driver plus destructive-oracle evidence.

The focused tests use only in-memory S3-shaped doubles and a fake age adapter;
they make no network, Docker, SSH, PostgreSQL, or Object-Storage call.
