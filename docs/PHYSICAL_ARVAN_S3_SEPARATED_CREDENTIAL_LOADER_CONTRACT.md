# Physical Arvan S3 separated-credential admission contract

> **Full-Matrix status: retired compatibility boundary.** This two-identity
> FI-to-IR contract is retained only for forensic reading/migration.  It must
> not be provisioned, activated, or treated as readiness evidence for the
> reversible three-site architecture, which requires four independently
> scoped directional identities and a fresh four-role immutable-storage
> admission.

`core.physical_arvan_s3_separated_credential_loader` is a narrow,
root-only, default-disabled admission boundary for the two Object-Storage
machine users required by the physical FI-to-IR route:

- FI publisher (`fi-publisher`); and
- WA-IR exact receiver (`ir-receiver`).

It is not an SDK client factory, an Object-Storage probe, a provider IAM
manager, an artifact publisher, or a Full-Matrix runner.  Importing or
validating its config does not read a credential, contact Arvan, create an S3
client, open a socket, or run a command.

## Fixed files and exact shape

The two file locations are constants, not config values or CLI inputs:

```text
/etc/trading-bot/security/arvan-s3-fi-publisher-credentials.json
/etc/trading-bot/security/arvan-s3-ir-receiver-credentials.json
```

Each immediate parent must be root-owned, non-symlinked, and exact `0700`.
Each credential file must be a root-owned, single-link, non-symlinked regular
file with exact `0600` mode and a bounded size.  The loader checks path and
parent resolution, opens with `O_NOFOLLOW`, checks the pre-open/fd/post-read
inode tuple, and rejects any mismatch.  Thus a symlink, unsafe mode, extra
hard link, race, missing file, duplicate JSON field, non-UTF-8 payload, or
oversized file fails closed.

The JSON shape has no optional or extra field:

```json
{
  "schema": "gold-trade-physical-arvan-s3-machine-user-credential-v1",
  "role": "fi-publisher",
  "action_profile": "fi-publisher-immutable-create-only-v1",
  "access_key": "…",
  "secret_key": "…"
}
```

The IR file instead has `role="ir-receiver"` and
`action_profile="ir-receiver-exact-readonly-v1"`.  The profile is an
admission declaration, not evidence of provider IAM: the separate live probe
must still exercise the permitted and denied provider operations.

The former FI profile value `fi-publisher-immutable-preflight-v1` is retired.
It is rejected rather than aliased so a stale deployment cannot silently enter
the four-role Full-Matrix path with a weaker or ambiguous policy declaration.

## Fixed route and action boundary

`RootOwnedArvanS3SeparatedCredentialLoaderConfig` is non-secret and defaults
to `enabled=False`.  It requires the same canonical endpoint/region/bucket
normalization as `physical_arvan_s3_client_factory`, plus these fixed values:

```text
source_site                = webapp_fi
destination_site           = webapp_ir
direct_site_control        = forbidden
destination_object_ingest  = pull-only
```

It rejects any route reversal, permissive direct-control mode, receiver push
mode, or altered action profile before either file is opened.

The public projection gives the existing injected immutability probe exactly
these expected operation tuples:

| Role | Expected live-probe operations |
| --- | --- |
| FI publisher | `GetBucketAcl`, `GetBucketVersioning`, `GetObjectLockConfiguration`, `PutObject:create-only`, `ListObjectVersions:exact-key`, `GetObjectRetention:exact-version`, `GetObject:exact-version`, `HeadObject:exact-version` |
| WA-IR receiver | `GetObject:exact-version`, `HeadObject:exact-version` |

These tuples intentionally match
`physical_arvan_s3_immutability_live_probe`.  The live probe remains the
authority that actually proves deletion, overwrite, and receiver list/write
operations are denied.

## Separation and non-disclosure

On an explicit enabled root call,
`load_root_owned_arvan_s3_separated_credential_pair(...)` reads both files,
derives a domain-separated SHA-256 fingerprint of each access-key identity,
checks the role/action profile, and then discards both access/secret values.
It fails if:

- the configured file constants collide;
- the files resolve to the same inode;
- either identity fingerprint or access key is the same; or
- either secret key is the same.

The opaque result and its
`ArvanS3ImmutabilityProbeCredentialProjection` contain only route/policy
facts and the two SHA-256 identity fingerprints.  They contain no raw key,
secret, credential path, provider URL, client, or token.  The opaque pair
explicitly rejects pickling; this module provides no serialization, logging,
environment fallback, or error path that includes credential material.

For the receiver-local dedicated-host preflight pull, a private reviewed
helper may admit **only** the fixed WA-IR receiver file after validating this
same route/action policy. It is intentionally not public API and does not
open, copy, or require the FI publisher secret on WA-IR. The concrete WA-IR
runtime separately pins the expected FI and IR public identity fingerprints
and rejects an admitted receiver identity that does not match its IR pin or
equals its FI pin. The paired loader remains mandatory for the full
immutability probe, where actual two-user separation must be established.

For the reviewed WA-FI encrypted PostgreSQL recovery-material publication
boundary, the symmetric private helper admits **only** the fixed FI publisher
file after the separated client factory has rechecked a fresh paired
immutability-preflight admission. The factory compares the current FI public
fingerprint with the FI pin in that evidence and rejects equality with the IR
pin. Thus the normal FI publisher path neither opens nor copies the WA-IR
receiver secret; it also does not alter the fixed role/action profile or make
the private helper a public credential API.

## Safe integration sequence

1. Create **two distinct** Arvan machine users/HMAC credentials with the
   intended least-privilege policies.  The Witness/controller must receive no
   Object-Storage credential.
2. Install the two files at the exact fixed locations with the exact ownership
   and modes above.  Do not replace the files with a shared legacy credential
   file, symlink, or copy of the same machine user.
3. Root constructs the explicitly enabled exact route config and uses
   `RootOwnedArvanS3SeparatedClientFactory` to keep the two independently
   constructed clients inside one reviewed wrapper.  Its public projection
   releases only role/action/profile/fingerprint facts; it wires the two
   transient clients into `PhysicalArvanS3ImmutabilityLiveProbe` internally.
4. Run the separately authorized disposable live probe.  Its existing
   create-only/exact-version/denied-operation checks remain mandatory before
   the outer immutability preflight can satisfy Full-Matrix readiness.

`RootOwnedArvanS3ClientFactory` currently reads one legacy fixed credential
file, so it must **not** be used twice as a substitute for this pair.  The
separate reviewed per-role construction seam is
`physical_arvan_s3_separated_client_factory`; this loader remains intentionally
limited to secure file admission and public fingerprint projection.

## Focused verification

```bash
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -B -m unittest \
  tests.test_physical_arvan_s3_separated_credential_loader \
  tests.test_physical_arvan_s3_client_factory \
  tests.test_physical_arvan_s3_immutability_live_probe -v
```

The new focused tests use only temporary files and synthetic credentials. They
cover default-off/no-read behavior, root and mode/owner/symlink protection,
duplicate JSON and scope refusal, equal identity/secret rejection, route and
action-profile denial, fingerprint-only public projection, serialization
refusal, tamper detection, and absence of SDK/network/probe/command imports.
