# Physical PostgreSQL standby bootstrap materialization contract

`core/physical_postgres_standby_bootstrap_materialization.py` is a
root-owned, default-off local boundary for the **WA-FL → WA-IR standby**
bootstrap path.  It makes already verified staging and recovery evidence
actionable only by a separately installed local materializer.  It is not a
restore, PostgreSQL, or promotion implementation.

## What this boundary admits

Before the injected materializer method is looked up or called, the boundary
requires all of the following to agree exactly:

- a root-owned `0700` fixed root set: source candidates, detached PGDATA
  candidates, receipts, failed candidates, and the recovery-signal seed root;
- an enabled root-only configuration (`owner_uid == 0` and effective UID 0),
  with no overlapping roots;
- the existing opaque verified physical-WAL Object-Storage bundle, with its
  FI-to-IR route and current Writer-Witness term bound together;
- the preflight binding's exact bundle ID, staging-receipt SHA-256,
  route-binding SHA-256, and expected term;
- the existing staged candidate at exactly
  `source_staging_candidates_root / bundle_id`, with no symlink traversal,
  and its exact frozen (`0400`) canonical `stage-receipt.json` read back from
  disk;
- static manifest, immutable object-version, and base/WAL/Blob artifact
  bindings in that receipt;
- fresh replay-observed recovery-readback evidence accepted by the existing
  physical recovery preflight; and
- one empty `0600`, single-link, root-owned `recovery.signal.seed` under the
  fixed seed root.

The PGDATA path is never caller selected.  It is a deterministic SHA-256
bootstrap-intent child of the fixed PGDATA candidate root and must be newly
created as one empty root-owned `0700` directory.  Existing, nonempty,
foreign, symlinked, or receipt-less candidates fail closed.  A repeat is
accepted only if the exact candidate inode and an exact canonical receipt
already match the recomputed plan; in that case no materializer is called.

## Narrow materializer seam

The future adapter receives only:

```python
materialize_standby_bootstrap(
    *,
    plan,
    source_stage_fd,
    target_pgdata_fd,
    recovery_signal_seed_fd,
)
```

All three descriptors are opened by the boundary after the admission checks.
There is no adapter input for a shell command, SQL string, path, environment,
host, URL, credential, Object Storage operation, Docker, SSH, or promotion.
The plan is canonical ASCII JSON and binds the source/target device+inode,
stage receipt, manifests, immutable object versions, terminal WAL LSN, exact
Witness term, recovery evidence, and seed hash.

The boundary rechecks source, target, seed, current term, expected term, and
recovery evidence immediately before the adapter call and again before it
publishes a receipt.  Any inode/symlink/content/term race yields no success
receipt.  On failure it removes only a still-empty candidate; a nonempty
candidate is atomically moved to the fixed failed-candidate root.  It never
performs recursive deletion.

## Receipt semantics

On a matching materializer acknowledgement, the boundary writes a canonical,
hashed `gold-trade-physical-postgres-standby-bootstrap-receipt-v1` receipt.
It proves only the local bootstrap **intent/result** for that detached
candidate.  It is not a PostgreSQL recovery proof, Writer-Witness CAS,
writer-authority permit, traffic switch, failover, or promotion authorization.

## Explicitly outside this boundary

The separately reviewed and explicitly authorized runtime adapter still must
perform, verify, and test all destructive work: immutable object pull and
decryption, `pg_basebackup` extraction or equivalent base restore, ordered WAL
replay, creation of `recovery.signal`, PostgreSQL launch/readback, fencing,
Witness CAS, promotion, traffic switching, and failback.  None of those are
implemented or authorized by this module.

## Focused verification

```bash
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -B -m unittest \
  tests.test_physical_postgres_recovery_preflight \
  tests.test_physical_postgres_recovery_readback_collector \
  tests.test_physical_postgres_standby_bootstrap_materialization -v
git diff --check
```
