# WA-FI physical PostgreSQL base-backup capture-command contract

`core.physical_wa_fi_postgres_base_backup_capture_command` is the
default-disabled, root-owned command boundary that makes the existing
completed-artifact/base-backup spool reachable from a future installed
`pg_basebackup` adapter on WA-FI.  Importing the module, constructing fakes,
and running its contract tests do not invoke PostgreSQL, a subprocess, Docker,
SSH, Object Storage, a network connection, deployment, or a credential read.

## Fixed local invocation only

The effectful entrypoint accepts an empty argument list only.  It has no
caller-selectable command, host, socket, port, output path, environment,
credential, URL, config pathname, shell string, or peer-control argument.
It loads only:

```text
/etc/trading-bot/physical-postgres/primary/base-backup-capture.json
```

The file and every ancestor must be root-owned/non-symlinked; the file must
be a single-link `0600` regular file containing bounded canonical JSON.  The
policy is explicitly enabled only with `enabled: true`; the module default is
off.  Its canonical SHA-256 pin covers the source/destination route, capture
roots, base-backup lineage, Witness proof, uploader policy, and command
digest.  Unknown fields—including host, password, proxy, URL, command, or
environment fields—are rejected.

The sole command identity is the module constant:

```text
/usr/lib/postgresql/15/bin/pg_basebackup
```

Before a runner can be called, that exact path and its ancestors must be
non-symlinked/root-controlled; the file must be root-owned, non-writable by
group/world, executable, single-link, bounded, and hash-match the pinned
`pg_basebackup_sha256` from the fixed policy.  Missing, replaced, or
mismatched binaries fail closed.

The fixed argv uses only a policy-pinned local Unix-socket directory, port
`5432`, role `replication`, `--no-password`, tar output, no streamed WAL, a
fast checkpoint, and a newly created private output directory.  The supplied
invocation carries an empty environment.  A future installed runner must exec
that exact absolute argv directly, with no shell or inherited credential
environment; this module deliberately does not implement process execution.

## Capture, completion, and spool handoff

The runtime policy permits only the FI → IR route with
`direct_site_control=forbidden` and `destination_object_ingest=pull-only`.
All capture, completed-source, spool, and uploader-workspace roots are
separate root-owned `0700` non-symlink directories.  The runner receives only
one newly created private child directory and must leave exactly these
root-owned `0600` regular files there:

- `base.tar` — the completed physical base-backup artifact;
- `completion.attestation` — a bounded non-empty local completion attestation;
- `completion.json` — canonical metadata binding the artifact/attestation
  hashes and sizes, policy hash, command digest, route, and Witness term.

The boundary hashes and verifies every fixed file itself.  It then copies the
artifact into the fixed completed-source root with no caller-controlled name,
authorizes a `VerifiedPhysicalWalBaseBackupBinding`, and calls the existing
`capture_physical_wal_base_backup` flow.  That existing flow takes the
immutable local snapshot, invokes the injected base-backup uploader, proves
its encrypted create-only Object-Storage receipt, and records the completed
handoff.

Runner, uploader factory, age factory, and Object-Storage client factory are
injected seams.  The boundary does not construct an SDK client or discover a
bucket.  A malformed policy, wrong route, unsafe root, unavailable command,
non-zero runner result, missing/corrupt completion, or stale/different Witness
binding fails before an uploader call.  The output staging directory is kept
as private local failure evidence rather than being deleted by a broad cleanup
operation.

## Witness timing and non-goals

The fixed Witness proof is verified before the runner, immediately after it,
again after the potentially long local copy but before Object-Storage handoff,
and once more inside the existing spool completion recheck.  The fixed runtime
policy/binary/term must remain exactly equal at each recheck.  This detects a
term expiry or local policy rotation during capture; a future live Witness
adapter must supply fresh evidence rather than treating this static boundary
as a lease authority.

A successful result is archive/recovery evidence only.  It is not native
`remote_apply`, a strict remote acknowledgement, a Writer permit, a promotion
right, a database restore, a replay proof, or a complete Full Matrix result.
Installing the root-controlled runner and policy, arranging local PostgreSQL
authentication, scheduling retries, delivering Witness evidence, and making
any deployment or promotion decision are separate future runtime work.
