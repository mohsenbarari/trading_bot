# Arvan S3 immutability live-probe adapter

> **Full-Matrix status: retired normal-direction helper.** A two-role
> FI-to-IR probe is not acceptable readiness evidence for a reversible
> three-site campaign.  It remains documented only for forensic/migration
> context; a future live collection must use the four-role, both-direction
> contract and must not reuse this paired adapter.

`core.physical_arvan_s3_immutability_live_probe` is the default-off,
root-only implementation of the injected probe interface consumed by
`physical_arvan_immutability_preflight`.  It has no S3 SDK, credential-file,
network, subprocess, URL-generation, or provider bootstrap code.  Importing
or constructing it is inert; development tests use only in-memory injected
clients.

This adapter does **not** claim that Arvan supports Object Lock or immutable
retention.  That claim remains unavailable until a separately authorized,
root-run live probe produces fresh evidence and the existing preflight
verifies it.

## Activation and roots of trust

The default configuration has no binding, no clients, and `enabled=False`.
`collect()` requires root and an explicitly enabled exact typed configuration
before it inspects either injected client.  Missing config, disabled config,
non-root execution, binding drift, malformed identity hashes, reused identity
hashes, or reuse of the same client object all fail before an S3 method call.

The deployment integration owns the two injected clients and must create them
from separately provisioned, least-privilege FI and IR credentials.  The
adapter receives only non-secret SHA-256 identity digests and client objects;
it cannot load, select, print, or return credentials.  The Witness has no
client and no Object Storage credential.

The configured binding must exactly equal the one supplied by the outer
preflight.  Its endpoint and bucket are only used to validate that existing
typed binding; neither is sent as an endpoint selector to a client and neither
appears in adapter error text.  The returned typed preflight observation keeps
the binding fields required by the pre-existing evidence contract, but the
adapter itself emits no log, command, URL, endpoint, credential, body, header,
owner identity, or SDK exception text.

## Disposable key and fixed request surface

The key is generated internally under the root-pinned template:

```text
physical-preflight/<validated-campaign-id>/arvan-immutability/probe-<UTC>-<random>.age
```

No caller can provide a prefix, object key, version ID, range, header, ACL,
payload, URL, presigned URL, bucket, endpoint, credential, or operation.  The
payload is a bounded non-secret disposable byte sequence; it is never recovery
material.  The initial FI write is exactly create-only:

- exact configured bucket and generated key;
- `IfNoneMatch=*`, `ACL=private`, `ContentType=application/octet-stream`, and
  `CacheControl=no-store`;
- SHA-256 checksum fields; and
- `COMPLIANCE` Object Lock mode with the observed retention deadline.

The returned `VersionId` must be non-null and all readbacks use that exact key
and version.  A full bounded byte range, checksum header, content headers,
byte count, Object Lock mode, and **the exact returned retention deadline**
must match.  A FI exact-key version listing may contain only the created
version and no delete marker.

## Exact credential-operation contract

The FI allowed-operation tuple is an ordered execution whitelist and must
equal the live adapter's non-denial calls exactly:

```text
GetBucketAcl
GetBucketVersioning
GetObjectLockConfiguration
PutObject:create-only
ListObjectVersions:exact-key
GetObjectRetention:exact-version
GetObject:exact-version
HeadObject:exact-version
```

The FI bucket ACL must be private canonical-owner-only.  Versioning must be
`Enabled`; Object Lock must be enabled with a `COMPLIANCE` default retention
in days at or above the campaign floor; the exact created version must return
the same `COMPLIANCE` retention.  These two Object Lock calls were added to
the typed preflight whitelist so the evidence never understates the credential
permissions it actually used.

The IR allowed tuple is only:

```text
GetObject:exact-version
HeadObject:exact-version
```

The only FI list is `ListObjectVersions` for the exact generated key.  There
is no general FI list, no normal IR list, no caller-controlled prefix, and no
presigned operation.  The probe deliberately attempts only these denied
operations, always against the same generated disposable key:

- FI: delete marker, delete exact version, and overwrite.
- IR: delete marker, delete exact version, list bucket with that exact prefix,
  list versions with that exact prefix, and put/overwrite.

Every one must fail with structured `AccessDenied`; an accepted operation, a
different error, a missing method, a malformed response, or a mismatch after
the attempts fails closed.  The post-attempt IR exact get/head readback proves
the original version and payload survived.  The adapter never cleans up or
acts outside the disposable key, because successful immutability tests make
cleanup impossible by design.

## Error handling and verification

Only a structured S3 `Error.Code == AccessDenied` or the explicit
`InjectedS3AccessDenied` test marker is normalized as a denial.  Exception
strings are never inspected or included in errors.  Errors are fixed codes;
raw response mappings are normalized to the existing typed preflight
observation, then hashed and independently verified by the outer preflight.
The result is observation evidence only, never execution authority.

Run the local focused suite with:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.test_physical_arvan_s3_immutability_live_probe \
  tests.test_physical_arvan_immutability_preflight \
  tests.test_physical_full_matrix_campaign_readiness
```
