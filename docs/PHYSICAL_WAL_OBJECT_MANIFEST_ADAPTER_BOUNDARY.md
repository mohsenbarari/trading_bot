# Physical WAL / Object Storage manifest adapter boundary

`core.physical_wal_object_manifest` is the local, pure boundary for the new
physical data plane. It signs and verifies canonical metadata for a physical
PostgreSQL base backup, contiguous WAL ranges, and a complete encrypted blob
inventory frontier. It does not open files, use `age`, connect to PostgreSQL,
or call Object Storage.

The current production contract explicitly pins PostgreSQL 15's 16 MiB WAL
segment geometry. A base manifest carries the actual backup-start LSN plus an
aligned `wal_chain_start_lsn` for the segment containing it. Every WAL object
must name exactly the timeline/log/segment derived from that aligned LSN and
cover exactly one pinned-size segment; duplicates, gaps, reordering, and a
timeline change in one base lineage are rejected.

WAL ordinals are zero-based absolute `start_lsn / 16 MiB` values, never
campaign-relative counters.  The first chain predecessor is derived as
`wal_chain_start_lsn / 16 MiB - 1`; `-1` is valid only for the signed genesis
link at segment zero.  This keeps a later base backup aligned with the exact
ordinal emitted by PostgreSQL archive processing instead of silently resetting
the sequence.

The future root-only uploader must create each encrypted object with
create-only/versioned semantics, read back that exact `VersionId`, hash and
size, then build the matching signed manifest. It must never publish a
`latest`/pointer alias. The receiver downloads only the signed key plus exact
`VersionId`, checks ciphertext hash and size before decryption, then passes
the raw canonical plaintext through the pinned-key verifier.

The manifests bind expected metadata; by themselves they do **not** prove
that an Object Storage provider retained the named bytes or that a prior
receiver consumed them. The uploader/receiver adapters must perform and
durably attest their own read-back and consume steps.

Before restore/apply, the receiver adapter must atomically record the accepted
manifest hashes and `(object_key, version_id)` pairs. The pure replay
arguments only reject supplied already-consumed sets; they cannot make that
durable single-consumption decision. It must also verify every decrypted blob
inventory shard against its signed plaintext hash and size before declaring
`objects_complete`.

The compatible projections for the later promotion admission are
`baseline_generation_id`, `baseline_manifest_sha256`, `baseline_wal_lsn`, and
`blob_object_frontier_wal_lsn`. These manifests bind a Witness-term identity
but do not prove a live Witness decision or synchronous remote apply. A live
Witness recheck, former-writer fence, and transport acknowledgement remain
mandatory before a writer transition or Full Matrix approval.
