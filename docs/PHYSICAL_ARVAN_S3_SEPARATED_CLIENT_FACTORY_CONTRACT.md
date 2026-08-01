# Physical Arvan S3 separated-client factory contract

> **Full-Matrix status: retired compatibility boundary.** The paired
> FI-publisher/IR-receiver factory below cannot serve the reversible three-site
> architecture.  It is retained for forensic reading only; Full Matrix
> requires four independently scoped role-local identities and a fresh
> four-role immutable-storage admission for both directions.

`core.physical_arvan_s3_separated_client_factory` is the root-only,
default-disabled credential-to-client bridge for exactly two independently
admitted Arvan machine users: FI publisher and WA-IR receiver.  It leaves the
legacy single-credential `RootOwnedArvanS3ClientFactory` unchanged and never
uses that factory as a substitute for two identities.

Importing, validating, or constructing
`RootOwnedArvanS3SeparatedClientFactory` is inert: no credential file, SDK,
network connection, Object-Storage operation, SSH, Docker, or command is
opened or run.

## Required admission and fixed policy

The factory receives only an explicitly enabled
`RootOwnedArvanS3SeparatedCredentialLoaderConfig`.  It requires the loader
and factory enabled flags to agree and delegates secure file admission to
`physical_arvan_s3_separated_credential_loader`:

- exact FI and IR fixed paths, root-owned `0700` parents, root-owned `0600`
  single-link files, anti-symlink and read-race checks;
- distinct FI publisher and IR receiver role/action-profile files;
- distinct machine-user identity fingerprints and distinct access/secret
  material; and
- the fixed `webapp_fi → webapp_ir`, no-direct-control, pull-only route.

All of those gates run before the SDK import.  A disabled mode, non-root
runtime, malformed clock, binding drift, equal identity pair, unsafe file, or
credential admission failure terminates with a fixed non-sensitive code.

## Two clients, one wrapper

`collect_immutability_preflight(binding=..., observed_at=...)` is the paired
two-client method. Before it reads credentials or loads the SDK it validates
the exact typed FI-to-IR immutability binding against the configured endpoint,
region, and bucket.

It then creates two independent SigV4 path-style SDK sessions internally:

- one with the transient FI access/secret pair; and
- one with the transient IR access/secret pair.

Both use the canonical Arvan HTTPS endpoint, matching region, TLS
verification, five-second connect timeout, sixty-second read timeout, at most
two standard retries, and an explicit empty proxy map.  No profile, session
token, environment provider, metadata provider, caller endpoint, bucket, or
proxy is accepted.  If the SDK returns the same raw client object for both
roles, the call stops before either client is used.

The two raw clients are wrapped only in local variables with an exact-bucket
probe method surface and passed directly to
`PhysicalArvanS3ImmutabilityLiveProbe`.  Neither client, session, SDK object,
credential file path, access key, secret key, or client-containing probe is
returned or retained by the factory after the call.  Provider `AccessDenied`
errors are deliberately preserved only inside that existing live probe so its
denied-operation oracle can prove the actual IAM boundary.

## FI-only recovery publication callback

The only other client-construction path is
`execute_fi_publisher_recovery_handoff(...)`. It is not a generic S3 factory:
before it can construct one transient FI client, root must provide a fresh
factory-minted `ArvanS3FiPublisherRecoveryHandoffAdmission`. The admission is
derived from an opaque verified paired immutability preflight and rechecked at
both boundaries of the local uploader call.

For this path the factory opens only the fixed FI publisher file. It compares
the resulting public FI fingerprint against the preflight's FI fingerprint
and rejects equality with the separately verified IR fingerprint. Therefore a
normal WA-FI recovery-material publication does not open the WA-IR receiver
secret merely to upload a private encrypted object.

The callback receives a private bucket-scoped wrapper with only the exact
methods needed by the existing physical WAL/base-backup uploader:
private-bucket checks, exact-key version history, `IfNoneMatch="*"` put, and
exact-version head/get. It has no delete, broad-list, generic client/session,
endpoint/bucket selector, or credential escape. The factory-owned route is
injected only inside the synchronous callback; the caller cannot choose an
endpoint, bucket, or object key. The reviewed
`physical_wa_fi_postgres_object_storage_handoff_runtime` is the only current
consumer of this callback.

## Public output boundary

`credential_projection()` can read/admit the two files but returns only:

- FI role, action profile, expected action tuple, and SHA-256 identity
  fingerprint; and
- IR role, action profile, expected action tuple, and SHA-256 identity
  fingerprint.

It contains no endpoint, bucket, credential, path, token, client, or generic
object handle.  The live-preflight observation returned by explicit collection
is the pre-existing public evidence type; it contains typed provider evidence
but no client or credential material.

The action tuples exactly match the existing live probe: FI receives the
create-only/exact-readback preflight surface and IR only the exact
get/head-readback surface.  The live probe still actively attempts the
provider-denied operations; a local action profile never substitutes for live
IAM evidence.

## Safe live sequence

1. Provision two distinct Arvan machine users/HMAC pairs and install them at
   the loader's fixed protected locations.
2. Root creates the matching enabled loader and paired-factory configs.
3. Root may call `credential_projection()` to record only the non-secret
   role/action/fingerprint facts.
4. Under a separately authorized disposable preflight window, root calls
   `collect_immutability_preflight(...)`.  This one explicit call creates the
   two transient clients and invokes the existing immutable-retention probe.
5. Feed the resulting observation into the existing outer preflight verifier.
   A separately enabled FI recovery handoff may use its fresh opaque verified
   result only to establish the narrowed FI-only callback above. A successful
   observation is still evidence only, never permission to release, deploy,
   promote a writer, or start Full Matrix.

## Focused verification

```bash
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -B -m unittest \
  tests.test_physical_arvan_s3_separated_credential_loader \
  tests.test_physical_arvan_s3_separated_client_factory \
  tests.test_physical_wa_fi_postgres_object_storage_handoff_runtime \
  tests.test_physical_arvan_s3_immutability_live_probe \
  tests.test_physical_arvan_immutability_preflight -v
```

The paired-factory test injects an in-memory SDK and two in-memory
S3-shaped clients. It proves two different credential sessions, no client
escape from the wrapper, projection redaction, disabled/root/binding/file
failure ordering, same-client refusal, and successful integration with the
existing live probe—without a real SDK import, network request, or provider
operation.
