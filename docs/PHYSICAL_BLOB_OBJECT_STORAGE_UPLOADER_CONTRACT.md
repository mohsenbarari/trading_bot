# Physical blob Object-Storage uploader contract

`core.physical_blob_object_storage_uploader` is a default-disabled,
root-only, local adapter for publishing already-frozen blob artifacts from
`core.physical_blob_artifact_spool`.  Importing it creates no client, reads no
credential, and opens no network connection.  The caller must explicitly
inject an age encryptor, S3-compatible client, and Ed25519 receipt signer.

## Admission boundary

Every upload requires an opaque
`VerifiedPhysicalBlobObjectStorageBinding`.  It can only be minted from the
Blob-spool's live Writer-Witness capability plus one positive PostgreSQL
timeline ID.  On each call the adapter revalidates the live source writer term
and pins all of the following:

- ordered `webapp_fi ↔ webapp_ir` source/destination route;
- campaign, release, baseline generation, baseline-manifest SHA-256, and
  baseline WAL LSN;
- destination age recipient;
- Writer-Witness epoch, lease ID, and witnessed-term proof SHA-256; and
- the separately supplied exact timeline ID.

The uploader accepts no generic path, raw byte string, database row, or
untyped manifest.  `upload_blob` accepts only a
`PhysicalBlobArtifactHandoffResult`; it securely rereads the exact canonical
handoff under the protected spool root and accepts only an immutable, frozen,
database-visible, finalized Blob-spool descriptor.  It verifies the local
snapshot's protected path, owner/mode/link count, stable inode metadata,
hash, and byte count.  `upload_inventory_shard` accepts only a
`PhysicalBlobInventoryShardPlaintext` and only after it has verified exactly
one typed, signed blob receipt for every inventory entry.

All integer checks use exact `int` type checks: booleans, negative values,
zero where not allowed, oversized values, non-canonical JSON, duplicate JSON
keys, symlinks, unsafe file modes, URLs, secret-shaped values, and malformed
receipt signatures fail closed.

## Storage coordinate and publication behavior

The published v2 keys are deterministic and include the route, baseline,
timeline, route-binding digest, epoch/lease/proof term component, blob-record
ID digest (or inventory shard ordinal), and plaintext SHA-256.  The original
Blob-spool v1 handoff key is verified but deliberately is **not** reused as a
published storage coordinate: it lacks the timeline and exact Writer-Witness
term.  This prevents a source handoff from selecting a weaker key.

For each object the adapter:

1. verifies bucket versioning is enabled and bucket ACL grants `FULL_CONTROL`
   to one canonical owner only (the same strict Arvan-compatible preflight as
   the WAL/base uploader);
2. encrypts the immutable plaintext to the pinned age recipient in a private
   root-owned temporary workspace;
3. performs an `IfNoneMatch="*"` conditional PUT, so a retry cannot overwrite
   or reuse a storage key;
4. checks that the post-PUT version history contains exactly the returned live
   version and no delete marker; and
5. heads and streams back that exact version, checking metadata, byte count,
   and ciphertext SHA-256.

Version-history enumeration is never used to authorize absence before a
write; the conditional PUT is the create-only authority.  Any ambiguous
history, provider-side encryption field, missing VersionId, body failure, or
readback mismatch fails without a receipt.  An upload that succeeds remotely
but fails afterward is an unreferenced orphan, never an acknowledged artifact.

## Typed signed receipts and frontier bridge

`upload_blob` returns `PhysicalBlobObjectStorageReceipt` and
`upload_inventory_shard` returns
`PhysicalBlobInventoryShardObjectStorageReceipt`.  Both contain canonical
Ed25519-signed receipt bytes, their SHA-256, exact Object key/version,
ciphertext hash/size, plaintext hash/size, timeline, and route binding.  The
inventory receipt additionally commits to a canonical digest of the exact
ordered set of Blob receipts it covers.

`build_physical_wal_blob_inventory_shard_from_receipt(...)` verifies the
inventory receipt again, checks the current live route/term/baseline/timeline,
rederives its storage key, and requires the exact ordered typed Blob-receipt
set whose signed digest the inventory receipt commits to.  It returns exactly
one mapping suitable for an item in:

```python
build_physical_wal_blob_frontier_manifest(inventory_shards=[mapping])
```

It is a bridge only: it does not sign or publish a blob-frontier manifest.
The future assembler must retain and independently validate the individual
Blob receipt set when it decides that a frontier's objects are complete.

This v1 bridge is a recovery/frontier projection only.  It is deliberately
**not** an input to the versioned physical-promotion-v2 path: that path must
require the opaque receiver mapping and promotion-evidence capabilities in
`physical_blob_receiver_promotion_evidence`, rather than inferring
receiver-restorability from this source-side v1 inventory receipt.

## Explicit non-functions

This module does **not**:

- extract transaction-consistent database descriptors or decide whether an
  upload is database-visible;
- create or publish base/WAL/blob-frontier source manifests;
- discover, download, decrypt, validate, or restore objects at WA-IR/WA-FL;
- prove receiver replay, remote durability, zero-loss acknowledgement, or
  Blob completeness beyond the explicit inventory receipt set;
- manage Object-Storage credentials, bucket policy, key rotation, deletion,
  lifecycle, or disaster recovery; or
- fence writers, perform Witness CAS, choose a promotion target, start
  PostgreSQL, or make an application write/promotion decision.

Accordingly, a successful receipt is immutable Object-Storage evidence only;
it is not a database-consistency proof, remote-apply proof, strict
acknowledgement, writer permit, or promotion authority.
