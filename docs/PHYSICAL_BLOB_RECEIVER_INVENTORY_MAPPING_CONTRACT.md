# Physical Blob receiver inventory mapping contract

`core.physical_blob_receiver_inventory_mapping` closes the vocabulary gap
between the immutable v1 Blob-spool inventory and the final v2 Object-Storage
coordinates.  It leaves the v1 handoff and v1 inventory byte-for-byte
unchanged.  Instead, it creates a separate canonical, Ed25519-signed,
age-encrypted v2 mapping that a receiver can compare to the original v1
plaintext after decryption.

The module is default-disabled and root-only for publication.  Importing it
does no I/O and creates no Object-Storage or age client.  The caller must
inject those adapters and the mapping signer explicitly.

## Inputs and immutable binding

`PhysicalBlobReceiverInventoryMappingPublisher.build_artifact(...)` accepts
only:

- a typed `PhysicalBlobInventoryShardPlaintext` emitted by the v1 spool;
- its typed, signed v1 inventory Object-Storage receipt;
- the exact ordered typed v2 Blob Object-Storage receipts; and
- a live `VerifiedPhysicalBlobObjectStorageBinding`.

It securely rereads the v1 plaintext under the protected spool root, parses
it canonically, and requires the exact v1 inventory receipt identity and
exact ordered Blob-receipt digest.  Each receipt must bind the same FI→IR
route, campaign/release/baseline, destination age recipient, PostgreSQL
timeline, and live Writer-Witness epoch/lease/proof.  An omitted, added,
reordered, stale, route-swapped, term-swapped, or descriptor-swapped record
fails closed.

The mapping commits to all of the following:

- original v1 inventory plaintext SHA-256, byte count, shard ordinal, and
  entry count;
- the full original signed v1 inventory upload receipt and its SHA-256;
- every v1 entry's ordinal, source-record identity, content identity,
  handoff descriptor hash, and v1 spool key;
- the full signed v2 Blob receipt for that exact ordered entry, its SHA-256,
  and its final version-bound Object descriptor;
- the canonical digest of the exact ordered Blob-receipt set; and
- route/baseline/recipient, timeline, and exact Writer-Witness term.

The receiver mapping key has its own noncolliding namespace:

```text
physical-blob-receiver-mappings-v2/<campaign>/<release>/<baseline>/
  <source>-to-<destination>/timeline-<id>/route-<sha>/
  term-<epoch>-<lease-hash>-<proof-hash>/
  v1-inventory-<v1-sha>/mapping-<mapping-sha>.age
```

It never reuses a v1 spool key, which intentionally lacks the timeline and
live term identity.

## Publication and receiver verifier

`publish_artifact(...)` first reparses the source-signed mapping and derives
the key again.  It encrypts only the canonical mapping plaintext to the
pinned recipient, performs the same versioned conditional create-only upload
and exact-version readback as the Blob uploader, rechecks the live term after
publication, then returns a separate typed signed mapping receipt.  A remote
write that cannot be fully attested yields no mapping receipt.

At the receiver, the required pure admission call is:

```python
verified = verify_physical_blob_receiver_inventory_mapping_plaintext(
    mapping_plaintext=decrypted_mapping,
    mapping_receipt=pinned_mapping_receipt,
    original_v1_inventory_plaintext=decrypted_original_v1_inventory,
    mapping_signer_public_key=pinned_mapping_key,
    blob_receipt_signer_public_key=pinned_blob_receipt_key,
    verified_binding=live_binding,
    now=now,
)
```

It verifies both signatures, every receipt identity, the pinned mapping
receipt, the deterministic mapping key, live route/baseline/timeline/term,
and a positional comparison against the independently obtained original v1
inventory.  A source-signed replacement list is therefore not sufficient to
hide an omission or reorder.

`build_physical_wal_blob_inventory_shard_from_receiver_mapping(...)` accepts
only that opaque verified capability plus the explicit pinned mapping and Blob
receipt public keys, a freshly revalidated locally unexpired binding, and a
clock, then returns one strict projection for
`build_physical_wal_blob_frontier_manifest(inventory_shards=[...])`.  The
projection names the receiver-ready mapping object; it does not relabel or
rewrite the v1 inventory.

The companion
`require_verified_physical_blob_receiver_inventory_mapping(...)` performs the
same strict recheck before any later use.  It rejects a capability whose
dataclass projection was altered, including a Python boolean substituted for
an integer field; signer keys embedded in a prior mapping are never accepted
as a substitute for the caller's current configured pins.  The unexpired
binding is still a local proof, not a network query for a successor/revoked
Witness term; a real promotion coordinator must query Witness immediately
before acting.

## Explicit remaining fetch/replay boundary

This module deliberately does **not** fetch from Arvan, decrypt age payloads,
persist receiver acceptance, materialize Blob files, replay PostgreSQL,
declare Object completeness, issue a remote acknowledgement, or promote a
writer.  A receiver adapter still must:

1. fetch the exact mapping Object key and `VersionId` pinned by its signed
   mapping receipt, verify ciphertext hash/bytes, and decrypt it;
2. fetch the exact original v1 inventory Object key and `VersionId` embedded
   in the mapping's signed v1 receipt, verify/decrypt it, and call the pure
   verifier above;
3. fetch each final Blob Object at its mapping-pinned key and `VersionId`,
   verify ciphertext hash/bytes before decryption, and verify the recovered
   plaintext against the mapping entry; and
4. durably record the accepted mapping/receipt/version pairs before any
   replay, acknowledgement, or promotion decision.

Accordingly, a mapping receipt is immutable publication evidence and a
receiver verifier is an admission boundary only.  Neither is a database
snapshot-consistency proof, remote-apply proof, zero-loss acknowledgement,
writer permit, or promotion authority.
