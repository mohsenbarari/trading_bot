# Physical Blob receiver exact-pull staging contract

`core.physical_blob_receiver_exact_pull_staging` is the receiver-side,
default-disabled boundary for obtaining encrypted physical Blob objects from
private versioned Object Storage.  It is not a synchronizer and it has no
default client, credentials, URL input, bucket listing, `latest` selector,
direct FI-to-IR connection, decryption, restore, replay, acknowledgement, or
promotion action.

## Admission

The stager accepts only these typed, signed receipt inputs together with a
locally fresh `VerifiedPhysicalBlobObjectStorageBinding`:

- `PhysicalBlobObjectStorageReceipt` for one finalized Blob object;
- `PhysicalBlobReceiverInventoryMappingReceipt` for the current v2 receiver
  mapping; and
- `PhysicalBlobInventoryShardObjectStorageReceipt` only as an inventory
  anchor explicitly paired with its current v2 mapping receipt.

Raw receipt bytes, spool handoffs, plaintext inventories, mutable object
selectors, and unpaired inventory receipts are rejected.  The Object-Storage
receipt envelope currently has a version-one grammar; that is not a legacy
data-plane path.  Every accepted GET must instead match the current v2
transport metadata (`physical-blob-object-storage-uploader-v2` or
`physical-blob-receiver-inventory-mapping-v2`) exactly.  This distinction keeps
the mapping's necessary original-inventory anchor available for comparison
without allowing a raw v1 artifact to stand in for receiver evidence.

Before any GET, the adapter re-verifies the signer pins and rechecks exact
source/destination route, campaign, release, baseline, writer epoch/lease,
Witness proof, timeline, recipient, deterministic object key, immutable
VersionId, ciphertext hash, and ciphertext size.  The mapping-bound anchor
also has to match the mapping receipt's original inventory hash, byte size,
ordinal, and entry count.  Boolean values cannot satisfy any integer pin.

## Exact pull and secure staging

The root-owned staging policy pins receiver site, receiver `age` recipient,
private `0700` staging root, and a finite byte ceiling.  The separately
injected `RootOwnedArvanExactVersionPullConfig` pins only the canonical Arvan
endpoint, region, private bucket, and maximum size.  The injected client
factory receives no caller-selected host, key, version, credentials, or URL.

For each object the stager derives the complete expected metadata map from the
signed receipt and live binding, builds an `ArvanExactVersionPullReader` with
one exact `Key + VersionId` expectation, and performs exactly that GET.  It
never invokes a listing or a mutable selector.  The response has to match
identity, metadata, size, and SHA-256; provider-side encryption/redirect
fields are rejected by the reader.

The ciphertext is written to a newly created `0600` regular file in a newly
created root-owned `0700` child directory.  The adapter fsyncs it, rehashes it
through the opened descriptor, verifies its byte size and `age-encryption.org`
v1 header, fsyncs the directory, and only then returns an opaque observation.
Failures discard only the exact new candidate path; the adapter never
overwrites an existing staged file.

## Reuse boundary

Call
`require_verified_physical_blob_receiver_exact_pull_observation(...)` before a
future decrypt/mapping/evidence step.  It repeats the signature, live-term,
route, baseline, recipient, deterministic-key, local file/hash/size, and
age-v1 checks.  A staged observation therefore cannot survive a stale term,
receipt wrapper mutation, modified local ciphertext, changed receiver policy,
or changed signer pin.

Successful output proves only one exact encrypted local copy.  A later
receiver must still decrypt with the separately root-controlled age adapter,
verify mapping plaintext against the paired inventory anchor, and then use the
existing Blob promotion-evidence contract.  It is never a Blob-frontier,
remote-apply, strict-acknowledgement, Writer-Witness, traffic-fence, or
promotion capability.
