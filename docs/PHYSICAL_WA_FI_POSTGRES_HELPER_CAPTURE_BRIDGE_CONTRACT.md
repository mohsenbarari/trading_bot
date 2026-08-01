# WA-FI PostgreSQL helper capture bridge

`core.physical_wa_fi_postgres_helper_capture_bridge` is the independent,
default-disabled local bridge from the reviewed PostgreSQL 15 helper container
to the existing physical base-backup *handoff types*.  It intentionally does
not call, modify, or provide compatibility for
`physical_wa_fi_postgres_base_backup_capture_command`: that older boundary
pins a host `pg_basebackup` binary and is not part of this architecture.

The bridge imports no Docker SDK, subprocess runner, PostgreSQL client, TCP
client, SSH/SCP client, Object-Storage SDK, uploader, credential loader, or
release controller.  It invokes only
`execute_wa_fi_postgres_helper_container_capture` and only with a mandatory
injected helper runner.  The reviewed helper remains responsible for the
digest-pinned PostgreSQL 15 image, non-root attested UID/GID, Unix socket,
`--network=none`, and `--pull=never` Docker argv.  The bridge creates no root
container and never uses a host `pg_basebackup` package.

## Inputs and route fence

Before it can call the helper, a caller must build an opaque
`PhysicalWaFiPostgresHelperCaptureBridgeControl`.  The builder requires all of
the following to be currently valid:

- a strict-runtime installation request and its fresh verified local
  observation;
- a live, signature-verified WA-FI Witness term;
- a valid `PhysicalWalBaseBackupManifestBinding` for exactly
  `webapp_fi -> webapp_ir`;
- the helper capture-policy SHA-256.

The builder compares campaign and release, checks the strict manifest's Writer
term hash against the live Witness proof, and normalizes the base-backup
binding with the existing handoff authority.  It retains capabilities only for
later revalidation.  The helper is called only after all of those checks, and
the strict observation and Witness term are rechecked immediately after helper
completion.  Expiry, rotation, a changed term, a changed capture policy, a
non-FI source, or any direct-FI-to-IR route fails closed.

This does not issue or replace a release seal.  It only consumes an already
attested strict-manifest identity; release admission, deployment, Writer
admission, promotion, and Full Matrix remain separate gates.

## Fixed local roots and artifact collection

The runtime has no caller-provided file paths.  Its two fixed, separately
root-owned `0700` roots are:

```text
/var/lib/trading-bot/physical-postgres/primary/helper-base-backup-captures
/var/lib/trading-bot/physical-postgres/primary/helper-base-backup-evidence
```

An installer must create them in advance.  The bridge verifies every ancestor
is root-controlled/non-symlinked, requires both roots to be disjoint, and
creates one fresh root-owned `0700` capture child.  That child is the only
capture-output root passed to the helper.  The helper's final `base.tar` must
be a single-link `root:root 0600` regular file directly in that child; its
SHA-256 and byte count are re-read by the bridge.  No cleanup removes a failed
capture child or a prior artifact.

The resulting `PhysicalWaFiPostgresHelperCaptureBridgeHandoff` exposes that
capture child plus a `PhysicalWalBaseBackupCompletedArtifact` named
`base.tar` and an already verified
`VerifiedPhysicalWalBaseBackupBinding`.  A future reviewed spool coordinator
can use those existing control types with its own independently validated
spool configuration.  This bridge never invokes the spool or an uploader.

## Canonical completion evidence

After the post-helper recheck, the bridge creates a non-self-referential
capture attestation hash first, uses it as the completed artifact's
`completion_attestation_sha256`, then authorizes the existing base-backup
route binding.  It writes one newline-terminated canonical JSON receipt named
by its SHA-256 under the evidence root.  Writes use a private temporary file,
`fsync`, create-only hard-link finalization, and directory `fsync`; an existing
receipt is accepted only when its exact root-owned single-link `0600` payload
matches.  It is never overwritten.

The receipt contains only redacted metadata:

- campaign/release and deployment/base-manifest hashes;
- strict-runtime request/installation identities;
- Writer/Witness epoch, lease/transition IDs, and proof hash;
- helper configuration/installation/preflight/invocation hashes;
- artifact filename, plaintext hash, and byte count;
- the derived base-backup route-binding hash.

It deliberately excludes endpoint names, IP addresses, Object-Storage bucket
or object values, recipients, paths, credentials, tokens, passwords, private
keys, and payload bytes.  It explicitly records that no Object-Storage handoff
has happened and that it is not a release, launch, Writer, promotion, or Full
Matrix authorization.

`require_physical_wa_fi_postgres_helper_capture_bridge_handoff` revalidates
the still-live strict/Witness inputs, artifact bytes, verified handoff binding,
fixed receipt path, canonical receipt, and full expected evidence before a
future coordinator may consume the handoff.  It performs no upload, launch,
promotion, or remote action.

## Remaining gates

This closes only the local host-`pg_basebackup` architectural dependency for
the capture boundary.  It does not install the helper image/configuration,
provide an actual root-controlled Docker runner, provision Object-Storage
identities/policy, publish an encrypted base backup, pull/recover it on WA-IR,
prove durable replay, authorize release/deployment, or start Full Matrix.
