# Arvan Object Storage immutability preflight contract

> **Full-Matrix status: retired normal-direction contract.** Its two-role
> FI-to-IR proof is insufficient for the reversible three-site design and is
> explicitly rejected by current campaign readiness.  It remains here only as
> historical/migration documentation; do not use it to create, adopt, or
> validate a campaign bucket.

`core.physical_arvan_immutability_preflight` makes the provider-side retention
assumption an explicit physical Full-Matrix gate. It is deliberately
default-off and does not import an S3 SDK, read a credential, contact Arvan,
or execute a destructive test by itself.

## Why this is required

Bucket versioning alone is not an immutable recovery promise. A credential
that can create a delete marker, delete an old version, or overwrite an
existing recovery key can invalidate an otherwise correct physical
base/WAL/blob chain. The normal FI-to-IR Object Storage route therefore needs
fresh, disposable-bucket evidence before it is considered by the local
readiness oracle.

## Bound evidence

One observation is pinned to the exact campaign, release, FI-to-IR route hash,
canonical Arvan endpoint/region/bucket, and a minimum retention period. It
requires all of the following:

- `Enabled` bucket versioning and the canonical private-owner-only ACL posture;
- either a compliance Object Lock mode or a provider-verified immutable
  retention equivalent, plus a non-secret hash of the provider policy evidence;
- separately scoped FI publisher and IR receiver credentials, with attempted
  delete and overwrite operations returning `access-denied`; and
- a Witness/controller posture that has **no** Object Storage credential at
  all, rather than an untested broad credential.

The disposable object must be under the fixed campaign-scoped prefix. Its
immutable version, ciphertext hash and byte count are recorded. Both a
version-delete attempt and a delete-marker attempt must be denied; an exact
`Key + VersionId` read after those attempts must return the same version,
hash, and byte count.

The raw observation is canonical-hashed, freshness-bound, and converted to an
opaque `VerifiedPhysicalArvanImmutabilityPreflight`. Directly constructing a
data class or replacing its fields does not satisfy the capability check.

## Collection boundary

`collect_physical_arvan_immutability_preflight` accepts only an enabled typed
policy, runs as root, and calls a narrow injected probe after all local checks
pass. The probe is the future owner of live S3 operations and credentials. It
must use a disposable, campaign-scoped object and separately provisioned
least-privilege identities; its result is immediately revalidated here.

This repository has **not** invoked that probe, created a disposable object,
attempted a delete, read any credential, or contacted Arvan. A later live
execution needs a fresh, narrowly authorized provider action and must retain
the resulting evidence without exposing credential material.

## Full-Matrix use

The readiness oracle takes both the opaque verified evidence and its typed
non-secret binding. It compares campaign/release/source/destination/route
facts with the physical campaign. Missing or mismatched evidence emits one of:

```text
missing-arvan-object-storage-immutability-preflight
arvan-object-storage-immutability-preflight-mismatch
```

Even a fully observed local result remains non-authorizing. It cannot launch
the matrix, upload recovery material, change a route, fence FI, promote IR, or
run a destructive provider test.
