# WA-IR physical PostgreSQL recovery pull runtime contract

`core.physical_wa_ir_postgres_recovery_pull_runtime` is the concrete
receiver-side assembly boundary for normal physical PostgreSQL recovery
material:

```text
verified FI→IR base/WAL/blob bundle + fresh exact metadata locator
    + fresh paired Arvan immutability preflight + live FI Witness term
                              │
                              ▼
               WA-IR credential only / exact Key + VersionId GET
                              │
                              ▼
                    age-v1 FD decrypt / private receiver staging
                              │
                              ▼
             redacted local receipt + typed recovery/bootstrap inputs
```

It is default-disabled and root-only.  Construction is inert.  A staging call
requires an explicitly enabled root policy, root-owned `0700` age workspace,
receiver/state/receipt roots, a root-private WA-IR age identity, a fresh
paired provider preflight, and a current live Witness term whose holder is
`webapp_fi`.

## Exact inputs and route policy

The only accepted normal route is `webapp_fi → webapp_ir` with direct site
control `forbidden` and Object-Storage ingest `pull-only`.  It does not
support a caller-selected source, destination, endpoint, bucket, path, URL,
proxy, latest pointer, list operation, or legacy object.

The signed `VerifiedPhysicalWalObjectStorageBundle` and
`PhysicalWalReceiverStagingPin` must agree exactly on source signing key,
campaign/release, Writer-Witness epoch/lease/proof, baseline, geometry,
recipient, route digest, and every immutable object coordinate.  The live
Witness term is checked both before and after the potentially long pull.

`PhysicalWaIrPostgresRecoveryExactObjectLocator` is a fresh, root-pinned,
public metadata sidecar.  Its SHA-256 is fixed in the root runtime policy.
It cannot substitute an object: its ordered exact expectations must equal the
already signed bundle's `(key, version, ciphertext hash, ciphertext size)`
set.  It supplies the complete expected S3 metadata map because the generic
exact-version reader intentionally requires exact metadata read-back too.
The locator has no credential, private age identity, URL, generic selector,
or client capability.

The paired Arvan preflight independently pins the endpoint/region/bucket and
the distinct FI publisher and IR receiver public credential fingerprints.  At
each actual GET, the runtime opens **only** the fixed WA-IR receiver
credential via the dedicated private receiver loader, verifies its public
fingerprint against that preflight, builds one transient SigV4 path-style
client, and exposes only `get_object(Bucket, Key, VersionId)`.  It cannot
list, head, put, delete, return a raw client, or open the FI credential.

## Staging and outputs

The existing `ArvanExactVersionPullReader` performs the exact version-bound
GET, provider response/metadata checks, streamed ciphertext hash/size check,
and bounded write.  The existing `PhysicalAgeV1FdDecryptor` (or an explicit
root test/runtime seam) decrypts the local FD only.  The existing
`physical_wal_receiver_staging` boundary then checks all plaintext geometry
and hashes and creates its frozen receipt plus durable per-manifest and
per-object consume records.

On success the runtime produces a root-owned frozen redacted receipt.  It
contains only non-secret hashes/term facts and these explicit negative facts:

- `standby_bootstrap_materialization_authorized=false`
- `promotion_authorized=false`
- `full_matrix_authorized=false`

It deliberately omits endpoint, bucket, credentials, identity path, object
key, VersionId, candidate path, plaintext, ciphertext, and client details.
The receipt is deterministic and `O_EXCL`/fsync/frozen; an existing unequal
receipt is a replay conflict, not an overwrite.

The in-memory result also gives the next local boundaries exactly the typed
inputs they need:

- `PhysicalPostgresRecoveryPreflightBinding`, which may later receive a real
  bounded PostgreSQL recovery readback; and
- `PhysicalPostgresStandbyBootstrapStageEvidence`, which may later be passed
  to the separately guarded detached-PGDATA materializer.

Neither output says PostgreSQL has been restored, started, replayed, or made
standby-ready.  They are not remote acknowledgement, writer, traffic,
promotion, deployment, release, or Full-Matrix authority.

## Explicit exclusions

This module performs no S3 list/head/write/delete, FI→IR SSH/SCP/rsync,
PostgreSQL connection/SQL/restore/start/stop/replay, Docker action, Witness
transition, promotion, traffic change, or Full-Matrix execution.  Reverse
IR→FI recovery remains a separate fresh-term route and must not be inferred
from this normal-route runtime.

## Focused verification

```bash
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -B -m unittest -v \
  tests.test_physical_wa_ir_postgres_recovery_pull_runtime \
  tests.test_physical_wal_receiver_staging \
  tests.test_physical_arvan_exact_version_pull \
  tests.test_physical_postgres_recovery_preflight \
  tests.test_physical_postgres_standby_bootstrap_materialization
git diff --check
```

The focused runtime test uses a synthetic signed bundle, live synthetic
Witness term, in-memory S3-shaped exact GET client, and FD decryptor double.
It makes no network, age, Docker, SSH, PostgreSQL, or provider call.
