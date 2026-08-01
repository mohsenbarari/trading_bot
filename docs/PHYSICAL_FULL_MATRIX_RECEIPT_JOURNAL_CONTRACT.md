# Physical Full-Matrix receipt-journal contract

`core.physical_full_matrix_receipt_journal` is the concrete local receipt
journal required by the default-off physical Full-Matrix execution driver. It
does not implement a phase adapter and cannot connect to a host, network,
Object Storage, PostgreSQL, Docker, SSH, SCP, rsync, or a shell. Constructing
the journal is inert.

Its only purpose is to make the driver's phase claim, receipt append, and
ordered reread operations crash-safe. A receipt is evidence of one phase only;
it is never a deployment, promotion, writer, external-effect, or
Full-Matrix-completion authorization.

## Fixed local boundary

The journal has no caller-selectable state path. Its one fixed root is:

```text
/var/lib/trading-bot/physical-full-matrix-receipt-journal
```

The root must already exist as a root-owned, non-symlink `0700` directory
under root-owned ancestors that are not writable by group or other. The module
does not create it or any ancestor.

Within that root, the module may create only its fixed `0600`, root-owned,
single-link regular lock and state files:

```text
receipt-journal.lock
receipt-journal.json
```

Every open uses `O_NOFOLLOW`; every accepted lock/state file is checked as a
regular single-link `0600` file before use. A missing root, unsafe mode,
symlink, hard link, path race, malformed state, or unsupported platform fails
closed with a fixed redacted code.

The non-secret
`RootOwnedPhysicalFullMatrixReceiptJournalConfig` defaults to
`enabled=False`. An enabled root call additionally requires the exact journal
schema and mode. There is no environment fallback, arbitrary state root,
implicit enablement, or phase-adapter construction.

## Claim and append protocol

The concrete object implements exactly the driver protocol:

1. `read_receipts(run_id=...)` returns only the complete canonical receipt
   chain for that run.
2. `claim_phase(...)` atomically either returns the matching durable receipt,
   returns a fresh opaque in-process claim, or returns the driver's explicit
   busy claim when an unresolved durable claim already exists.
3. `append_claimed(...)` accepts only the exact claim object minted by that
   journal instance and one immutable canonical driver receipt bound to the
   claim.

The state records a run UUID, one plan digest, ordered receipt strings, and at
most one pending claim. Each receipt is re-parsed through the driver's
canonical receipt validator and must repeat the run, plan, contiguous
sequence, unique phase-request digest, and previous-receipt digest. The
journal therefore rejects a foreign run/plan, reordered receipt, mutable
alias, duplicate request, mismatched predecessor, forged claim, or different
receipt for a spent claim.

An exact existing receipt is returned idempotently. If an append crosses the
atomic replacement boundary but its caller observes an error, retrying the
same live claim returns the exact persisted receipt rather than appending a
second entry.

## Crash and concurrency behavior

For every claim or append mutation the journal:

1. takes an exclusive `flock` on the fixed protected lock file;
2. rereads and validates the complete canonical chain;
3. writes a new canonical state image to a new protected temporary file;
4. fsyncs the temporary file, atomically renames it into place, fsyncs the
   resulting state file, and fsyncs the state directory; and
5. rereads and validates the durable result while still under the lock.

The journal is semantically append-only: an API mutation can add exactly the
next receipt or clear its matching pending claim, never alter an accepted
receipt or reorder a chain.

A crash after durable claim creation but before receipt append is deliberately
a blocker. A later process sees the pending claim and returns the driver's
busy result; it must not retry a possibly destructive phase. A crash during
the append leaves either the already-durable pending claim or the exact
receipt, both of which prevent duplicate phase execution. Resolving a stranded
claim is an external reviewed recovery decision, not a timer, lease takeover,
or automatic journal action.

The state schema always fixes all of these to false:

```text
completion_authorized
promotion_authorized
full_matrix_executed
```

No method can change them.

## Explicit integration

A root-side runtime may explicitly construct this journal with an enabled
config and inject it into
`PhysicalFullMatrixExecutionAdapters(receipt_journal=...)`. The driver still
requires every separately reviewed phase adapter and all fresh readiness
evidence. This journal does not make Full Matrix runnable by itself and does
not authorize any phase.

## Focused verification

```bash
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -B -m unittest -q \
  tests.test_physical_full_matrix_receipt_journal \
  tests.test_physical_full_matrix_execution_driver
```

The tests use temporary local roots, synthetic canonical receipts, injected
rename/fsync failures, threads for competing claimers, and deliberate
tampering. They open no network connection or real phase adapter.
