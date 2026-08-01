# Physical WAL incremental receiver chain contract

`core.physical_wal_incremental_receiver_chain` is the local, default-off
continuity boundary after a receiver has verified one finite physical
base/WAL/blob bootstrap bundle.  It exists because
`verify_physical_wal_object_storage_bundle` is intentionally a bootstrap/full
history verifier; treating that API as a runtime cursor would either resend an
unbounded history or lose the signed predecessor that makes the next update
safe.

The component's sole successful status is
`metadata-staged-not-replay-verified`.  That means signed metadata has been
checked and durably recorded locally.  It does **not** mean that the encrypted
objects were downloaded, decrypted, restored, replayed by PostgreSQL,
remotely acknowledged, witnessed, or made eligible for promotion.

## Root-owned local state

The component requires an explicitly enabled
`PhysicalWalIncrementalReceiverConfig` and an existing, root-owned `0700`
state root.  It refuses to run outside root.  Beneath that root it creates:

```
physical-wal-incremental-receiver-v1/
  cursor.lock                 # root-owned 0600 serialization lock
  records/
    00000000000000000001.json # root-owned frozen 0400 bootstrap record
    00000000000000000002.json # root-owned frozen 0400 append record
    ...
```

Every record is canonical JSON, written with `O_EXCL`, file-synced, directory
synced, then frozen to `0400`.  A record contains a hash of its unsigned
canonical form and the previous record hash.  On every read the entire local
history is checked again: sequence, record predecessor, route pin, base,
signatures and WAL/blob continuity all have to agree.  A damaged, mutable,
symlinked, non-root-owned, reordered, or unknown local state entry fails
closed.

The bounded bootstrap record preserves canonical signed base/WAL/blob bytes.
Each incremental record preserves the canonical signed WAL and blob-frontier
bytes, not a mutable object key or a `latest` pointer.  This is necessary to
reconstruct the signed predecessor after a receiver restart.

## Required call order

1. Build a `PhysicalWalIncrementalReceiverPin` with
   `build_physical_wal_incremental_receiver_pin`.  Its route digest covers the
   source/destination, source Ed25519 key, destination age recipient,
   campaign/release, exact Writer-Witness term, baseline hash and physical
   PostgreSQL geometry.
2. Pass an already verified `VerifiedPhysicalWalObjectStorageBundle` to
   `bootstrap_physical_wal_incremental_receiver_chain`.  The bundle is
   reverified against that pin and must contain a complete blob frontier.
3. For each later continuity point, pass exactly one signed WAL manifest and
   one signed complete blob-frontier manifest to
   `stage_physical_wal_incremental_receiver_append`.

The append must bind all of the following to the durable current cursor:

- the exact prior WAL manifest hash, end LSN and last segment ordinal;
- the exact prior blob-frontier manifest hash and frontier LSN;
- the same signed base backup, directed route, source key, age recipient and
  Writer-Witness term; and
- a blob frontier exactly equal to the new WAL terminal LSN.

The initial physical bundle may contain several WAL manifests.  Thereafter the
API accepts one WAL/blob pair at a time, deliberately making the receiver's
expected predecessor explicit rather than inferring a range from a listing.

## Retry and rejection semantics

An idempotent retry is accepted only when both supplied signed canonical
manifest bytes and both manifest hashes equal the **current** append record.
It adds no record.  A bootstrap can be retried only while it is still the only
record and its full bytes match exactly.

The following fail closed:

- an append before bootstrap;
- an old, non-current WAL/blob pair after progress;
- a WAL ordinal/LSN hole or a mismatched predecessor hash;
- a second blob frontier for a consumed WAL, or a second WAL for a consumed
  blob frontier;
- a changed base manifest, route, source signing key, recipient, campaign,
  release, Writer-Witness term or physical geometry; and
- an incomplete blob frontier.

There is intentionally no conflict-resolution policy.  Once one valid next
pair is committed, a competing pair at that predecessor is a fork and must be
resolved outside this local staging boundary with the appropriate witnessed
writer authority.

## Explicit non-goals and remaining boundary

This module has no Object Storage, age, PostgreSQL, network, SSH, SCP, Docker,
Witness or promotion adapter.  It does not consume encrypted objects or prove
that a receiver can replay them.  A future execution coordinator must still
combine this cursor with exact-version Object Storage fetch/decrypt, durable
receiver inventory/replay evidence, Writer-Witness CAS/fencing, application
writer admission and a destructive Full Matrix campaign.  None of those
claims may be inferred from this metadata cursor.
