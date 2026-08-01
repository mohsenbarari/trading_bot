# Physical Blob receiver promotion-evidence contract

`core.physical_blob_receiver_promotion_evidence` is the mandatory narrow
bridge between a receiver-verified v2 Blob mapping and any future
blob-frontier/promotion-evidence coordinator.  It exists because a v1 Blob
inventory Object-Storage receipt proves only a source publication; it does
not prove that WA-IR can resolve the final v2 Object versions or that the
receiver's Blob set is restorable.

The adapter is default-disabled, pure, and local-only.  It opens no network
connection, creates no Object-Storage client, reads no file, and starts no
restore/replay action.  Enabling it requires two explicit public-key pins:
the mapping signer and the v2 Blob-receipt signer.

## Required inputs

`verify_physical_blob_receiver_promotion_evidence(...)` requires all of:

- an opaque `VerifiedPhysicalBlobReceiverInventoryMapping`, which could only
  have been minted after the receiver compared a decrypted mapping with the
  original decrypted v1 inventory;
- the exact typed
  `PhysicalBlobReceiverInventoryMappingReceipt` pinned by that package;
- a locally fresh, unexpired `VerifiedPhysicalBlobObjectStorageBinding` and
  clock; and
- a requested replay LSN exactly equal to the mapping's signed baseline WAL
  LSN.

It rejects raw bytes, a typed v1 inventory receipt, or a typed v2 Blob receipt
in place of the mapping receipt.  Thus a future coordinator cannot silently
substitute old v1 source-publication evidence for receiver-ready mapping
evidence.

The verifier reparses canonical mapping bytes and all embedded signed
receipts.  It binds and rechecks:

- FI/IR source and destination, campaign, release, baseline generation,
  baseline-manifest hash, baseline WAL LSN, and destination age recipient;
- route-binding digest, timeline, exact Writer-Witness epoch/lease/proof, and
  an unexpired locally verified binding;
- mapping plaintext hash/size and the exact immutable mapping Object
  key/version/ciphertext descriptor from the pinned mapping receipt;
- original v1 inventory hash/size, v1 inventory receipt hash and immutable
  Object descriptor; and
- every ordered v2 Blob receipt hash plus its source record, plaintext,
  handoff hash, Object key/version, and ciphertext descriptor.

Re-signed reordered entries, omitted records, a substituted final descriptor,
an altered mapping receipt wrapper, a foreign timeline/route, a stale term,
or any replay LSN other than the signed mapping baseline fail closed.

## Output and promotion-gate boundary

Success returns only an opaque
`VerifiedPhysicalBlobReceiverPromotionEvidence`.  It contains the exact
typed mapping, original-v1-inventory, and ordered v2-Blob receipts (including
their exact Object descriptors) and a `mapping_eligible_replay_wal_lsn`, but that LSN
means **only** that the mapping's Blob scope does not extend beyond its signed
baseline.  It is not an assertion that WA-IR replayed PostgreSQL to that LSN.

Before a future coordinator signs or supplies the Blob-frontier portion of
physical-WAL promotion evidence, it must call:

```python
require_verified_physical_blob_receiver_promotion_evidence(
    evidence,
    config=approved_public_key_pins,
    verified_binding=live_binding,
    now=now,
)
```

and use this opaque capability as a required input.  The recheck requires the
same explicit enabled public-key configuration, so it never trusts signer
keys carried only inside a prior capability.  The current
`physical_wal_promotion_gate` API is intentionally unchanged: its legacy
signed `blob_object_receipt` has no field for the mapping receipt lineage, so
passing a v1 receipt to that API remains insufficient for this new bridge.  A
future gate/evidence schema revision must consume this capability before it
can assert `objects_complete`; it must not synthesize that assertion from the
mapping bridge.

For that explicit separation, the module also provides the opaque,
versioned `VerifiedPhysicalWalPromotionV2BlobRequirement`:

```python
requirement = build_physical_wal_promotion_v2_blob_requirement(
    receiver_promotion_evidence=evidence,
    config=approved_public_key_pins,
    verified_binding=locally_unexpired_binding,
    now=now,
)
```

Only a `VerifiedPhysicalBlobReceiverPromotionEvidence` can mint this
promotion-v2 requirement; a raw or typed v1 inventory receipt and the legacy
`build_physical_wal_blob_inventory_shard_from_receipt(...)` projection cannot
enter this API.  A future promotion-v2 coordinator must require
`require_physical_wal_promotion_v2_blob_requirement(...)` with the same
explicit pins before it signs any v2 Blob-frontier evidence.  This quarantines
the legacy v1 bridge without changing its existing recovery-oriented API.

## Explicit non-functions

This adapter does **not**:

- fetch or read an Arvan Object key/version;
- verify a receiver-side ciphertext download or decrypt an age payload;
- materialize or hash recovered Blob plaintext;
- persist a receiver acceptance ledger, perform PostgreSQL replay, or issue
  a remote durable/replay acknowledgement;
- query Witness for a newer term, revocation, or successor during the proof
  TTL; or
- set `objects_complete`, sign a physical-WAL blob receipt, invoke the legacy
  promotion gate, CAS a Witness term, fence a former writer, or start a
  writer.

It therefore reduces an evidence-substitution risk, but it is deliberately
not a recovery claim, remote-apply proof, no-loss acknowledgement, or
promotion permit.  Before any real promotion a root-only coordinator must
query/recheck current Witness state immediately; an unexpired local proof is
not global successor-term discovery.
