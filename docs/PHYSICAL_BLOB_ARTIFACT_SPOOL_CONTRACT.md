# Physical blob artifact spool contract

`core.physical_blob_artifact_spool` is a default-disabled, root-only local
boundary for already-finalized upload blobs.  It does not query the database
or open any network, Object Storage, encryption, restore, PostgreSQL, or
promotion path.

A trusted external database snapshot extractor supplies explicit
`PhysicalBlobFrozenDescriptor` values.  Each one is bound to the ordered
`webapp_fi ↔ webapp_ir` route, campaign/release, active Writer-Witness term,
baseline generation/manifest/LSN, destination age recipient, immutable source
record ID, declared content hash/size, and a non-path identity for the fixed
protected uploads root.  The descriptor is accepted only when it states
frozen database visibility and finalization, with both temporary and in-flight
flags false.  Temporary/in-flight path forms are also rejected fail-closed.

The spool securely opens the exact source file below a root-owned 0700 uploads
root with `openat`/no-follow semantics.  It requires root ownership, mode
0600, one link, declared size, stable metadata, and declared SHA-256.  It
copies into a separately protected root-owned spool; it never modifies the
source.  Snapshot, handoff, source-record index, inventory, and inventory
ordinal records are immutable create-only local artifacts.  A changed replay
of a source record or shard ordinal is rejected; byte-identical retry is
idempotent.

For each snapshot, the module writes a canonical handoff record.  It also
writes one canonical **plaintext** inventory shard with its SHA-256, byte
count, and entry count.  Those three facts can later be paired with a verified
encrypted Object Storage receipt to form a `PhysicalWalBlobInventoryShard`.
No signed blob-frontier manifest is created here.

The remaining external stages are intentionally separate:

- a transaction-consistent, frozen database descriptor extractor;
- an injected encrypted, create-only Object Storage uploader plus exact
  readback receipt; and
- a receiver-side verified blob download/decryption/restoration stage.

No result from this module is a database snapshot-consistency proof,
Object-Storage receipt, remote-apply proof, strict acknowledgement, writer
permit, or promotion authority.
