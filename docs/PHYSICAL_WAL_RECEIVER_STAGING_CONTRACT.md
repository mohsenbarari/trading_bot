# Physical WAL receiver staging contract

`core.physical_wal_receiver_staging` is a local, default-off receive/staging
boundary.  It accepts only an already verified
`VerifiedPhysicalWalObjectStorageBundle`, an exact route/baseline/term pin,
and two mandatory injected adapters: an exact Object-version reader and a
decryptor.  It ships no Object Storage, HTTP, `age`, shell, PostgreSQL,
container, SSH, restore, recovery, or promotion implementation.

The signed bundle destination determines the local receiver role.  Therefore
the same boundary supports normal `webapp_fi` → `webapp_ir` continuity and
witnessed `webapp_ir` → `webapp_fi` failback, while preserving Object-Storage
pull only: it never opens a direct source-server control channel.

Both local roots and all created directories must be absolute, non-symlink,
exact-mode `0700`, and owned by the executing service identity.  Material is
placed only beneath a fresh deterministic candidate.  For every signed
`(object_key, version_id)`, the reader writes to a supplied local FD and the
boundary hashes and sizes those bytes before decryption.  The decryptor then
writes to another supplied local FD.  Staged WAL names/ranges/sizes must match
the pinned 16 MiB geometry; every blob-inventory plaintext must match its
signed hash and size.

After validation, the receiver writes a canonical `O_EXCL` stage receipt and
canonical `O_EXCL` durable consume records keyed by every manifest hash and
every exact Object-version pair.  A crash before the receipt causes the
partial candidate to be quarantined before a new read.  A valid receipt can
be resumed idempotently without another source read or decryption; a foreign,
tampered, aliased, or replayed record fails closed.

Success is only `staged-not-replay-verified`.  It is not a PostgreSQL replay
receipt, remote-apply acknowledgement, live Witness decision, route switch,
or writer/promotion authorization.  Those remain separate guarded steps.
