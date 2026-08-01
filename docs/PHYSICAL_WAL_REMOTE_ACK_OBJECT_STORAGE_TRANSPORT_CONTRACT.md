# Physical-WAL remote-ack encrypted Object-Storage transport contract

`core.physical_wal_remote_ack_object_storage_transport` is the narrow,
default-disabled transfer boundary for the existing signed physical-WAL
remote-ack request and the receiver ledger's already durable signed receipt.
It provides no replication, recovery, Witness, deployment, polling, proxy,
or promotion implementation.

## One directed, immutable round trip

For one exact `webapp_fi -> webapp_ir` binding, the only supported sequence
is:

1. FI re-verifies its signed remote-ack request, encrypts its canonical bytes
   to IR's pinned `age` recipient, and conditionally creates exactly one
   request Object.
2. A separate trusted Witness/control-plane boundary builds a canonical,
   Ed25519-signed **request locator**.  IR verifies that locator before it
   performs an Object-Storage call, then obtains only its embedded
   `Bucket + Key + VersionId`, decrypts it locally, and re-verifies the
   signed request.
3. A trusted local PostgreSQL recovery adapter and the existing receiver
   ledger produce the durable receipt.  IR accepts no raw receipt bytes for
   publication: it requires a `PhysicalWalRemoteAckReceiverLedgerResult`,
   re-verifies its signed evidence against the published request, encrypts it
   to FI's pinned `age` recipient, and conditionally creates exactly one
   receipt Object.
4. Witness/control plane independently builds a canonical signed **receipt
   locator**.  FI verifies it before an Object-Storage call and consumes only
   that exact receipt `Key + VersionId`.

The module deliberately has no method to list a bucket, HEAD an object,
follow an alias, use a `latest`/`current`/`null` version, construct a
presigned URL, contact the opposite site, or carry plaintext through Object
Storage.  It does not accept a raw receipt as an alternative to the durable
ledger-result type.  A caller must not interpret a successful receipt pull as
a Writer permit, a promotion authorization, or a complete recovery proof.

## Pins and locators

Each object key is deterministic but never mutable:

```text
physical-wal-remote-ack-transport-v1/
  <source>/<destination>/<campaign>/<release>/<baseline>/<writer-term>/
  requests/<request-sha256>.age

physical-wal-remote-ack-transport-v1/
  <source>/<destination>/<campaign>/<release>/<baseline>/<writer-term>/
  receipts/<request-sha256>/<receipt-sha256>.age
```

The canonical Witness-signed locator binds the full existing remote-ack
binding (including campaign, release, directed route, destination recipient,
baseline generation/manifest, Writer epoch/holder/lease, Witness term proof,
frontier and manifest/object-version set), plus the request and, when present,
receipt plaintext SHA-256, deterministic key, returned immutable VersionId,
ciphertext hash/size, plaintext size, and `age-v1` recipient.  The verifier
rejects malformed JSON, duplicate keys, noncanonical encoding, wrong locator
role, signer mismatch, invalid/future/stale lifetime, replayed locator
identity or nonce, boolean-as-integer pins, mutable selectors, and a key that
does not re-derive from the signed pins.

The locator's short issuance/expiry window is checked before any pull.  The
request and receipt's own signed timestamps are rechecked after exact
decryption: a request may not postdate its request locator, and a receipt may
not postdate its receipt locator.  A receipt locator cannot be made before
its contained durable receipt acknowledgement.

## Encryption, publication, and exact readback

All network-facing behavior is injected through a two-method
`put_object`/`get_object` client and separate `age` encrypt/decrypt protocols.
Importing and constructing the transport performs no credential read, SDK
import, network action, bucket preflight, or Object-Storage operation.  The
new `RootOwnedArvanS3ClientFactory.physical_publish_client_factory()` may be
supplied as that injected client source; this module never constructs it,
selects an endpoint, or reads its credentials itself.

Every PUT uses `IfNoneMatch="*"`; a missing or unsafe returned VersionId is
rejected.  Immediately after publication the transport GETs the same exact
Key and returned VersionId, requiring exact identity and metadata plus
ciphertext byte/hash readback.  Pulls derive their expected metadata from the
locator and binding, GET only the exact version, write the ciphertext to a
new root-owned `0600` file below a root-owned `0700` workspace, require the
`age-encryption.org/v1` header, decrypt locally through the injected adapter,
and recheck plaintext hash/size and signed request/receipt evidence.

The Object-Storage policy must keep this prefix private, versioned,
create-only, and retained for the campaign recovery window.  A VersionId and
publication readback prove the object that was addressed; they cannot prevent
a storage administrator or provider from later deleting an otherwise pinned
version.

## Explicit runtime boundary

This is intentionally not a hidden polling loop.  Live delivery of a fresh
Witness-signed locator, durable replay observation, ledger invocation,
replay-consumption tracking, retry scheduling, and campaign-level timeout or
escalation must be supplied by a later named runtime adapter.  That adapter
must persist locator replay state and must not replace this contract with
bucket discovery or a direct FI-to-IR control connection.
