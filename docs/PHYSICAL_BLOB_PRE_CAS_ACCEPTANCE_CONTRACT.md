# Durable physical Blob pre-CAS acceptance contract

`core/physical_blob_pre_cas_acceptance.py` closes one narrow failover gap: the
physical Blob v2 verifier correctly requires a locally live former
Writer-Witness term, while a safe successor promotion may run only after that
former term is no longer usable. This module records the former-term-bound
Blob evidence *before* successor term issuance/CAS and later rechecks only an
authority-signed record.

It is a pure contract. It does not open a database, filesystem, network,
Object Storage, Docker, SSH, PostgreSQL, Witness, or deployment client.

## One persistence seam

`PhysicalBlobPreCasAcceptanceAuthority.append_and_read_back(...)` is the only
persistence seam:

```python
append_and_read_back(
    *,
    canonical_acceptance: bytes,
    acceptance_sha256: str,
) -> bytes
```

The injected authority implementation is responsible for durable storage. It
must atomically reject reuse of `pre_cas_operation_id`, append exactly the
canonical acceptance after checking its SHA-256, read back the exact stored
bytes, and return a canonical Ed25519-signed receipt only after that readback.

The local contract verifies the pinned authority signature and equality of the
append and readback hashes. It cannot prove a particular database, bucket, or
medium is durable; that trust belongs explicitly to the supplied authority.
There is no alternate local persistence path and no retry path that relaxes
former-term validation.

## Pre-CAS creation

`persist_physical_blob_pre_cas_acceptance(...)` is default-off. With
`PhysicalBlobPreCasAcceptanceConfig(enabled=True)`, it requires all of the
following at one `now` value before it calls the authority:

- verified prior role activation and the active predecessor's still-live
  Witness term;
- verified physical-WAL source evidence;
- the strict v2 Blob requirement, reverified with its existing Blob signer
  configuration and exact Object-Storage binding; and
- an authority implementing the sole append/readback method above.

It cross-binds the records before persistence. The canonical acceptance binds:

| Group | Bound facts |
| --- | --- |
| Identity | source/destination site, campaign, release, stream generation, and destination age recipient |
| Baseline | generation ID, manifest SHA-256, and baseline WAL LSN |
| Former writer | epoch, lease ID, Witness transition ID, and Witness proof SHA-256 |
| Exact WAL source evidence | schema and SHA-256 of the exact signed source durability receipt bytes |
| Blob evidence | timeline, route-binding hash, mapping plaintext/receipt hashes, mapping object key and immutable version ID, ciphertext hash/size, original v1 inventory receipt hash, Blob receipt-set hash, entry count, and mapping replay LSN |
| Ordering | unique operation ID and `accepted_at` |

The mapping replay LSN must equal the baseline WAL LSN. A generic receipt, v1
receipt, wrong direction, wrong baseline, changed former term, wrong recipient,
or boolean substituted for an integer fails closed before the authority call.

## Signed readback and post-CAS use

The authority receipt has its own schema and binds the operation ID, acceptance
SHA-256, readback SHA-256, positive append sequence, `accepted_at`, `issued_at`,
and Ed25519 signature. The public authority key is supplied independently in
the config and is compared to the capability during every recheck.

`verify_physical_blob_pre_cas_acceptance(...)` can mint a new verified
capability from raw canonical acceptance plus raw signed readback. It verifies
canonical encoding, fields, freshness, the pinned signature, exact hashes, and
timestamps; it does not contact the authority or a former writer.

`require_verified_physical_blob_pre_cas_acceptance(...)` accepts only the
opaque capability, repeats the same verification, detects in-process mutation,
and performs no former-term live check and no v2 Blob revalidation. The record
is bounded by the configured maximum age and future skew, so it is not a
perpetual replay authorization.

The post-CAS coordinator consumes this capability with the same pinned config.
It cross-binds the exact source evidence schema/hash again and requires both
`accepted_at` and the authority-signed readback `issued_at` to be no later
than `successor_witness_term.issued_at`. This ordering is tested directly.
There is no fallback that calls the former v2 verifier after that point.

## What this is not

This contract is not a live CAS, writer fence, PostgreSQL recovery, or traffic
switch. It never authorizes a writer to start. A runtime implementation still
needs a root-controlled durable authority, a Witness CAS, former-writer fence,
immutable-object pull/decrypt/restore/replay, and destructive three-server
evidence before Full Matrix completion.

## Focused verification

```bash
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -B -m unittest \
  tests.test_physical_blob_pre_cas_acceptance \
  tests.test_physical_postgres_promotion_coordinator -v
git diff --check
```
