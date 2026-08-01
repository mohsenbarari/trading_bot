# WA-IR PostgreSQL recovery materialization runtime contract

`core.physical_wa_ir_postgres_recovery_materialization_runtime` is the
root-only, default-disabled phase-3 bridge between an already staged exact
WA-IR recovery bundle and a deliberately narrow local PostgreSQL recovery
runner.

```text
sealed exact release + live FI Witness term + staged exact FI→IR bundle
        + current route binding + current replay-observed admission evidence
                                      │
                                      ▼
                 detached FD-only PGDATA materialization
                                      │
                                      ▼
     network-none / Unix-socket-only local standby recovery inspection
                                      │
                                      ▼
       fresh recovery evidence + immutable redacted phase-3 receipt
```

It accepts only the normal `webapp_fi → webapp_ir` recovery direction.  It
does not expose an FI-to-IR direct transport, a source or destination chosen
by a caller, an endpoint, a bucket, an object selector, a path, a Docker
command, an environment, a credential, or a PostgreSQL SQL surface.

## Admission and materialization sequence

Construction is inert.  A run needs an explicitly enabled root-owned policy,
a fresh sealed release descriptor, the staged result from
`physical_wa_ir_postgres_recovery_pull_runtime`, a verified signed physical
bundle, a currently live FI Witness term, and a root-private `0700` durable
receipt root.

The policy fixes PostgreSQL 15 to the exact image in the sealed release and
renders only this local recovery profile:

- `network_mode=none`
- TCP listener disabled
- Unix socket directory `/var/run/postgresql`, port `5432`
- peer-local-only socket authentication
- standby replay only
- direct site control forbidden and Object-Storage ingest pull-only

Before the injected materializer becomes reachable, the runtime binds the
release, bundle, stage receipt, route digest, exact staging candidate,
Witness epoch/lease/proof, and a current replay-observed recovery evidence.
The pre-existing FD-only bootstrap boundary deliberately requires that last
admission evidence before it creates a candidate.  This is not a claim that a
new restore has already replayed: after materialization, the runtime invokes
the independent readback collector to mint a **new** recovery evidence.

The term, release, bundle, staged pull result, route pins, and admission
evidence are checked again before the only inspection call, and the final
term/bundle/route plus detached candidate identity are rechecked before any
phase-3 receipt is written.  A changed or stale term therefore blocks rather
than allowing a stale candidate to be inspected or receipted.

## Runner boundary

The injected `PhysicalWaIrPostgresSocketOnlyRecoveryRunner` has exactly two
methods:

1. materialize the prevalidated detached candidate using the fixed source,
   target, and recovery-signal file descriptors; and
2. inspect that same detached candidate using the rendered socket-only
   invocation and collector request.

The runtime imports no Docker, subprocess, socket, PostgreSQL, SSH, HTTP, or
Object-Storage client.  A separately reviewed local adapter may implement the
fixed runner with Docker/PostgreSQL, but it receives no caller-selected
execution input and is reached only after the above checks.  Tests use an
FD-only in-process fake; they never start Docker or PostgreSQL and never make
a network, S3, or SSH call.

## Evidence and explicit non-authority

On a successful fresh local inspection, the runtime writes an `O_EXCL`,
fsynced, root-owned `0400` receipt below its private receipt root.  It records
only hashes, release/bundle/route facts, the full non-secret Witness
epoch/lease/transition/proof binding, bootstrap hashes, and the readback
observation time.  It intentionally excludes
credentials, object keys, object version IDs, local candidate paths, payload
contents, endpoint/bucket data, and runner details.

Every result and durable receipt explicitly keeps all of these false:

- `promotion_authorized`
- `writer_authorized`
- `traffic_switch_authorized`
- `full_matrix_authorized`

The result is suitable for a later phase-3 adapter to envelope as a fresh
`PhysicalFullMatrixRecoveryObservation`; it is not itself an authorization to
promote WA-IR, change writer ownership, switch traffic, or execute Full
Matrix.  In particular, it is intentionally not added to the WA-FI writer
strict-runtime gate: receiver recovery evidence must not be confused with the
writer-side strict durable-replay response requirement.

## Focused verification

```bash
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -B -m unittest -v \
  tests.test_physical_wa_ir_postgres_recovery_materialization_runtime \
  tests.test_physical_wa_ir_postgres_recovery_pull_runtime \
  tests.test_physical_postgres_standby_bootstrap_materialization \
  tests.test_physical_postgres_recovery_readback_collector \
  tests.test_physical_postgres_recovery_preflight \
  tests.test_physical_release_seal_admission
git diff --check
```
