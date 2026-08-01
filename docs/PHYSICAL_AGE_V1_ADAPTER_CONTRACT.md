# Physical age-v1 adapter contract

`core.physical_age_v1_adapter` provides the concrete local process boundary
for the injected age protocols used by the physical WAL/base-backup/blob and
manifest transports.  It is not an Object Storage client, a secret
distribution system, a PostgreSQL restore tool, or promotion authority.

## Fixed trust boundary

- Both adapters are default-disabled and need a root-owned, mode-`0700`
  workspace root.
- The executable is pinned to `/usr/bin/age`; a receiver additionally pins
  `/usr/bin/age-keygen`.  Both must be root-owned, non-writable, regular
  non-symlink executables.
- The encryptor accepts precisely one configured `age1…` recipient.  A caller
  cannot select a different recipient.
- The decryptor accepts precisely one configured recipient and validates that
  exact recipient by deriving it from its root-private identity via
  `age-keygen -y` before a decrypt operation.
- Identity, plaintext, and ciphertext inputs must be single-link private
  regular files.  New outputs are created once with `O_EXCL|O_NOFOLLOW`,
  mode `0600`, and are never overwritten.

## Operation discipline

Both paths copy the input FD into a fresh private workspace snapshot before
invoking `age`.  The decryptor snapshots the identity as well before deriving
its public recipient and before passing it to `age`. Consequently the
subprocess never reopens a caller-controlled input or identity path. It runs
without a shell, with empty stdin, suppressed output, a restricted environment,
strict `umask`, bounded file size, and a timeout.

Encryption validates the generated `age-encryption.org/v1` header before
making the requested output visible.  Decryption validates that header before
and after its ciphertext snapshot and only then decrypts.  A malformed output,
unsafe mode/owner/link count, recipient drift, identity-recipient mismatch,
existing destination, mutable input, or subprocess failure fails closed.

`PhysicalAgeV1FdDecryptor` is the compatible bridge for
`PhysicalWalDecryptor`.  It accepts only private, single-link ciphertext and
empty destination FDs owned by the receiver staging boundary; snapshots the
ciphertext and identity before calling `age`; then returns the exact
`PhysicalWalDecryptionReadback` hash, byte count, key, version, and recipient.
It never reopens the supplied cipher FD by path and never closes either caller
FD.

The broad adapter caps are only a final local ceiling.  Each physical
WAL/base-backup/blob/manifest caller remains responsible for passing the
tighter object-specific byte bounds and for validating its signed lineage.

## Deliberate non-claims

Successful encryption does not prove Object Storage publication, recipient
receipt, WAL replay, blob inventory application, remote acknowledgement,
Witness CAS, writer fencing, traffic cutover, or promotion.  Successful
decryption does not authorize consuming any result.  Those claims require the
separate exact-version, signed-manifest, remote-ack, recovery, and promotion
contracts.
