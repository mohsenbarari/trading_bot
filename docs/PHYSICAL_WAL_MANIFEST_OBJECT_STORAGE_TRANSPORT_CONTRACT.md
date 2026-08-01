# Physical WAL signed-manifest Object Storage transport contract

`core.physical_wal_manifest_object_storage_transport` is the narrow,
default-disabled transport boundary for one **finite, already verified**
physical PostgreSQL base/WAL/blob manifest bundle.  It does not replace the
physical object uploader, the receiver's recovery staging boundary, a
PostgreSQL replication stream, or a Writer/Witness transition controller.

## Exact source publication

The source accepts only an opaque
`VerifiedPhysicalWalObjectStorageBundle` capability from
`physical_wal_object_manifest`, then re-verifies it and binds it to explicit
non-secret route facts:

- source and destination site;
- source Ed25519 public key;
- campaign, release, Writer/Witness term, baseline geometry, and destination
  age recipient; and
- a separately pinned `route_binding_sha256`.

It never accepts a filename, a mutable Object key, a pointer, or a `latest`
selector from its caller.  The only package Object key is derived from the
directed route digest, base-manifest digest, and deterministic digest of the
complete signed base/WAL/blob manifest set:

```text
physical-wal-manifests/v1/<campaign>/<release>/<source>-to-<destination>/
  route-<route-sha256>/
  baseline-<generation>-<base-manifest-sha256>/
  bundle-<complete-manifest-set-sha256>.age
```

Before anything is encrypted, the module domain-signs a canonical package
with the same source public key that signs the contained manifests, then
locally verifies that outer signature and the full inner base/WAL/blob chain.
The package is only metadata; its maximum plaintext size is 16 MiB.

The source requires an explicitly enabled root-owned 0700 workspace, an
injected age encryptor, injected Ed25519 signer/verifier, and an injected
S3-compatible client.  Importing the module does not construct a client,
load credentials, or contact Object Storage.

The bucket preflight is intentionally compatible with the provider's
S3-style primitives: versioning must report `Enabled`, and `get_bucket_acl`
must prove a nonempty grant list containing only the bucket's canonical owner
with `FULL_CONTROL`.  The adapter performs a conditional
`IfNoneMatch="*"` PUT, rejects visible provider-side encryption fields,
requires a non-null returned VersionId, and proves that exact version by
HEAD and GET hash/byte/metadata readback.  It never calls an Object Storage
listing API.  The provider must therefore preserve the standard conditional
PUT semantics; a provider that cannot do so is rejected as unsuitable for
this contract rather than being approximated with an unsafe `latest` lookup.
Because this boundary deliberately does not list historical versions or delete
markers, deployment policy must separately forbid deletion/overwrite of this
private prefix and retain returned versions for the recovery window.  The
exact VersionId and readback prove what was published and later fetched; they
cannot themselves prove that an administrator or provider will never delete a
historical version.

The returned `PhysicalWalManifestPublicationReceipt` is canonical typed,
non-secret evidence.  It pins the route/term/baseline/recipient, bucket and
region, deterministic bundle digest, full plaintext package digest, Object
key, returned VersionId, ciphertext hash/size, and plaintext byte count.  A
receipt is not a remote replay acknowledgement, a Writer permit, or a
promotion proof.

## Exact receiver fetch and metadata stage

The receiver takes a canonical receipt **and** a
`PhysicalWalManifestReceiverPin`.  The pin independently names exactly one:

- receipt SHA-256;
- deterministic Object key;
- immutable VersionId;
- finite bundle digest; and
- full encrypted-package plaintext SHA-256.

It rejects a receipt that differs in any route, Writer/Witness term,
baseline, recipient, bucket/region, key, version, or package digest.  It
does not list a bucket, follow a pointer, discover a newest version, or trust
a current alias.  After the same private/versioned bucket preflight, it HEADs
and GETs only `Bucket + Key + VersionId`, checks the exact source metadata and
ciphertext hash/size, decrypts through an injected adapter, checks the
plaintext hash/size, verifies the package's domain signature, and invokes the
existing full base/WAL/blob bundle verifier under the root-pinned facts.

Only then does it create a root-owned 0600 local metadata package beneath the
configured 0700 staging root.  Repeating the same receipt is idempotent only
when the existing staged file is itself root-owned, non-symlink, single-link,
0600, and byte-identical.  The result status is
`staged-verified-not-consumed` deliberately: this is neither database replay
nor a durable receive/consume record.

## Explicit non-goals and remaining gates

This v1 component deliberately models one finite, exact bundle.  It does
**not** implement an unbounded WAL feed, a `latest` manifest, an append
cursor, or recovery of a previously staged package.  A future incremental
path must be a separately named contract with a durable pinned predecessor
and source/receiver cursor; it must not be inferred from this bootstrap
bundle.

The caller must distribute and durably authorize receipt pins through a
separate authenticated control plane.  Before recovery or promotion, the
system still needs all of the following outside this module:

- durable receiver consumption/CAS records for manifest hashes and immutable
  object versions;
- Object Storage transport of base/WAL/blob payloads and verified blob
  inventory restoration;
- actual PostgreSQL recovery/replay evidence and a live, durable remote
  acknowledgement;
- an active Witness term recheck, former-writer fencing, and application write
  admission; and
- explicit Full Matrix orchestration and fault testing.

No direct FI-to-IR control channel, database operation, restore, replay,
promotion, lease issuance, or authority exists in this transport module.
