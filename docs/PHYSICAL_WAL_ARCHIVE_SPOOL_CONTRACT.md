# Physical WAL archive spool

`core/physical_wal_archive_spool.py` is a local-only archive-command handoff
producer.  It does not upload to Object Storage, contact FI/IR, start
PostgreSQL, or implement `remote_apply`.

It accepts only one completed, canonical 16 MiB PostgreSQL WAL segment from a
fixed non-symlink source mount, snapshots it locally with hash/metadata
stability checks, and writes immutable canonical descriptor/manifest records.
The descriptor is bound to one ordered, distinct WA-FI↔WA-IR route, release,
stream, baseline and aligned WAL chain start, timeline/database ID, the
pinned recipient for that route's destination, and a live opaque Witness term
held by that route's source. Normal operation uses FI→IR. After a fenced IR
promotion, the same constrained Object-Storage-only path supports IR→FI
failback; neither direction permits direct site-to-site control.

An uploader is injected; no default exists. Its receipt must bind the exact
descriptor, deterministic object key, pinned destination recipient, versioned
immutable encrypted object metadata, and bounded ciphertext size. A missing
adapter, invalid/expired term, tampered snapshot, malformed receipt, or
unbound manifest fails closed. A completed record keyed by descriptor hash is
validated and returned idempotently without another uploader call.

Archive success is recovery material only.  It never constitutes synchronous
remote durable/replay acknowledgement, zero-loss evidence, a writer permit,
or a promotion decision.
