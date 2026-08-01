# Physical Arvan S3 Immutability Probe Runner Contract

> **Full-Matrix status: retired normal-direction helper.** This paired
> FI-publisher/IR-receiver runner is not a permitted route into the reversible
> three-site Full Matrix.  It is retained solely for forensic/migration
> reading; future live evidence must come from the four-role, both-direction
> immutable-storage boundary.

`core.physical_arvan_s3_immutability_probe_runner` is the narrow local
execution/receipt boundary for a future paired FI-publisher and WA-IR-receiver
Arvan Object-Storage immutability collection.  It is intentionally not a
deployment, promotion, writer-failover, or Full-Matrix runner.

## Admission order

`RootOwnedArvanS3ImmutabilityProbeRunner` is inert at construction and
default-off.  Its only collection method is `run(now=...)`.  Before it can
call the paired client factory, it requires all of the following locally:

1. an explicit `enabled=True` configuration and root effective UID;
2. a fresh, opaque `SealedPhysicalReleaseDescriptor`;
3. an exact campaign/release/FI-to-IR binding matching that seal;
4. the fixed receipt directory
   `/var/lib/trading-bot/physical-arvan-immutability-receipts`, owned by root
   with mode `0700`, with no leaf symlink; and
5. no pre-existing receipt claim for the same seal plus binding digest.

The runner supplies a fresh normalised binding to the exact
`RootOwnedArvanS3SeparatedClientFactory` implementation only after those
checks.  Therefore a disabled runner, stale/tampered seal, invalid/mismatched
binding, unsafe directory, or historic claim cannot open a credential file,
load an SDK, construct a client, or invoke the disposable provider probe.

The runner itself has no S3 SDK, network, subprocess, Docker, SSH, or
credential-file surface.  It does not alter legacy runners or deployment
paths.

## Evidence and redaction

The factory's raw observation is immediately re-verified with
`verify_physical_arvan_immutability_preflight`.  A returned canonical receipt
contains only the campaign/release identity, hashes of the sealed descriptor,
binding, and observation, retention posture, observation time, and one-way
FI/IR machine-user identity fingerprints.

It never contains an endpoint, region, bucket, object key, version ID,
ciphertext, credential, credential path, raw client, or raw provider error.
Any factory or provider exception is collapsed to
`ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_FACTORY_FAILED`; an invalid observation is
collapsed to `ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_OBSERVATION_INVALID`.

## Durable receipt semantics

The result is a new `0600`, root-owned regular file under the fixed directory.
The runner creates a private `O_EXCL|O_NOFOLLOW` temporary leaf, writes and
`fsync`s it, atomically links it to the deterministic final name without
replacement, removes the temporary link, and `fsync`s the anchored receipt
directory.  It then securely reads the final leaf through the directory file
descriptor and requires byte-for-byte canonical readback.  Existing claims
are refused before the provider probe and again at the atomic-link boundary;
the runner never overwrites or reuses one.

The parser rejects duplicate JSON keys, non-ASCII/non-canonical bytes, altered
hashes, altered authorization flags, and extra fields.

## Explicit non-authority

Every receipt has all of these fields set to `false`:

- `deployment_authorized`
- `promotion_authorized`
- `full_matrix_authorized`

It is only durable preflight evidence.  A future campaign gate must still
perform independent, fresh checks for every required approval and readiness
condition before any deployment or Full-Matrix action.

## Test boundary

`tests/test_physical_arvan_s3_immutability_probe_runner.py` injects the factory
result by patching its collection method.  It validates default-off and
seal/binding/root/replay ordering, canonical redaction, root-only modes,
atomic receipt readback, raw-error redaction, observation rejection, and
parser tamper rejection.  It makes no S3, SSH, Docker, subprocess, or network
call.
