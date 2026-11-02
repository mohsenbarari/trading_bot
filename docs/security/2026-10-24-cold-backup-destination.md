# Cold engine backup destination: scoped ciphertext storage only

The final Finland host65.109.214.203 was reached using the existing explicit
Native identity id_ed25519_finland_primary, strict known-host verification and
no agent forwarding. The pinned ED25519 fingerprint remains
SHA256:bwxz2aeBwy0ZNOMMCVdRhaW//TkeALqt6etTQa3NINs. Root free space was
303641874432 bytes. This authorizes no application migration or Writer/DNS change.

The separately reviewed web_engine_cold_receiver_stage.yml prepares only a new
0700 ciphertext root and public receiver namespace. It asserts fixed host/path
inputs before mutation, uses exclusive mkdir without adopting/chmodding existing
destinations, verifies the same bounded source descriptor and emits the actual
public filesystem identity. A partial attempt remains evidence and cannot be
silently reused. No package installation, production stop, reboot, SSH-key grant
or ciphertext transfer is part of staging.

The receiver has the reviewed final root filesystem identity: device2049,
/dev/sda1, ext4 UUIDce366399-3430-441e-8a6f-6f5621f0c7a2, mounttarget/.
Its root is /srv/trading-bot/security-backups. Only opaque encrypted bytes enter
exclusive operation/nonce directories; no encryption key or plaintext goes there.
The hard cipher maximum is10GiB, with8GiB reserve and1MiB control allowance.
Receive fsyncs and rehashes the actual artifact; independent verify rereads it
and checks the immutable receipt, inode, ownership, size and digest. Unknown,
partial or changed evidence is never adopted, overwritten or deleted.

A future short-lived Web SSH key must be restricted to the verified Web source
IP, an expiry and a forced receiver command using --ssh-guard. Its private key
stays on Web. The public guard contract is web_engine_cold_ssh_guard/1 with exact
operation_id, nonce, expires_utc, source_ip, destination_root and lock_identity
(device/inode/uid/gid/fullmode of the pre-created0600 regular lock). The lock's
contents are the guard JSON's SHA256 plus one newline. Its inode is created
before generating that JSON; its payload is written before any authorized use.
No content from the private SSH key is inspected or hashed diagnostically.

Only the exact engine-cold capacity/receive/verify argument grammar is accepted,
with the bound owner, deadline no later than expiry, and all byte/digest bounds.
The public control and persistent lock retain their file descriptors and full
identities before and after the operation. A replaced lock is refused by later
requests because the original inode is pinned in the guard; concurrent use of
that inode fails immediately. A guard/key must expire within the receiver's
two-hour admission bound. Expired operations need a fresh namespace and grant;
no historical deadline is rewritten. Remove the exact temporary authorization
after its operation, retaining public incident evidence as needed.

Source tests include actual separate-FD flock contention and identical-byte
control/lock replacement during the named mock operation. Both original and
subsequent admission must fail on replacement. This packet grants no fresh
maintenance window, proves no cold archive exists and closes no vulnerability.
Before engine package writes, require fresh runtime/storage checks, owned
quiescence, cold archive compare, normal crypto reconciliation, actual offhost
fsync and independent reread. The verified HOT database backup remains a separate
required artifact; it never substitutes for that cold engine archive.
