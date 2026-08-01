# Physical WA-IR fresh bootstrap descriptor contract

`core.physical_wa_ir_bootstrap_bundle_builder` is a deliberately narrow,
default-disabled, root-only preparation boundary for the **first fresh**
encrypted WA-IR stage-bootstrap artifact.  It produces public canonical
descriptor metadata only.  It is not the legacy bootstrap packer, a release
archive reader, an encryption adapter, an Object-Storage client, a host
delivery adapter, or a bootstrap executor.

The boundary exists to prevent an emergency rollout from accidentally
reusing a historical WA-IR bootstrap, a historic recipient, a mutable Object
selector, or an unsealed release projection.

## Required inputs

`prepare_fresh_wa_ir_bootstrap_descriptor(...)` accepts only these opaque
local capabilities:

1. `SealedWaIrBootstrapExactReleaseBinding`, minted from an explicit
   `WaIrBootstrapExactReleaseBinding` with an exact campaign ID, release SHA,
   control release SHA, release-bundle SHA-256, image-set SHA-256,
   release-provenance SHA-256, seal UUID, and UTC seal time.  Its route is
   fixed to `webapp_fi → webapp_ir`.
2. `VerifiedWaIrBootstrapFreshAgeRecipient`, minted from a public
   campaign-scoped `age1...` recipient, the SHA-256 of that exact public
   recipient, a fresh generation UUID, and an issue time.  There is no field
   for an age private identity, identity-file path, secret, token, or URL.
3. `VerifiedWaIrBootstrapImmutableObjectLocatorExpectation`, minted from one
   fresh exact expected Object coordinate: deterministic Object key, exact
   non-mutable version ID, ciphertext and plaintext hashes/sizes, the exact
   recipient, `age-v1`, and `versioned-create-only-readback-v1`.

The release seal, recipient, and locator must all be fresh at preparation
time (180 seconds by default, never more than 300 seconds), agree on the
same campaign/release/binding/recipient, and use four distinct UUIDs.  The
locator key is deterministically constrained to:

```text
physical-wa-ir-bootstrap/v1/<campaign>/<release>/<sealed-binding-sha256>/<bootstrap-uuid>/stage-bootstrap.tar.age
```

Wildcard keys/versions, aliases such as `latest`/`current`, URL-shaped or
secret-shaped values, stale timestamps, wrong route, unmatched hashes, and
raw/unsealed input types fail with fixed error codes before any marker is
created.

## Default-off state and non-reuse rule

The config defaults to `enabled=False`.  A caller must explicitly provide a
root-owned, non-symlink, exact-`0700` state directory and the fixed policy:

```text
direct_fi_to_ir_control = forbidden
operation_mode          = prepare-review-only
```

After every local validation succeeds, the builder atomically creates and
fsyncs five root-only (`0600`) hash-only markers below
`physical-wa-ir-bootstrap-bundle-builder/`:

- campaign + release;
- recipient public-key SHA-256;
- recipient generation UUID digest;
- locator digest; and
- bootstrap UUID digest.

Any later reuse is refused, including a retry with a different recipient and
locator but the same campaign/release.  This is intentional: an interruption
after preparation requires a new campaign rather than a silent reuse of old
bootstrap material.  A deny-list of historical recipient public-key hashes
is also supported by the root config.

The descriptor has fixed `status="prepared-local-only"`,
`publish_authorized=false`, `execution_authorized=false`, and
`direct_fi_to_ir_control="forbidden"`.  It contains no endpoint, bucket,
credential, Object URL, private identity, filesystem source path, plaintext,
or command.  Reviewing the descriptor revalidates it without creating any
additional marker and conveys no authority to publish or execute.

## Relationship to the existing scripts and private pull seam

The existing `scripts/prepare_webapp_ir_stage_bootstrap.py` and
`scripts/render_webapp_ir_stage_bootstrap_receive.py` are legacy operational
helpers.  This module neither imports nor invokes them because their archive
and receiver concerns are outside this metadata-only gate.

Likewise, this module does not invoke the retired
`core.dedicated_host_preflight_ir_object_storage_pull_delivery` tombstone.
That former receipt route has no active runtime surface.  The independently
scoped FI-to-WA-IR request-provisioning protocol is the only Object-Storage
path relevant to a later receiver, and this builder still makes no network
client or claim that an Object exists.  The shared principle is opaque exact
version pins with no direct FI-to-IR control transport.

## Required future integration sequence

A separately reviewed root-owned orchestration layer must do all of the
following; none is implemented here:

1. Produce and review a new exact release admission/seal with the six bound
   release facts required above.  Do not translate an old bootstrap receipt
   into a new seal.
2. Generate a fresh WA-IR public age recipient for this campaign in the
   identity boundary.  Pass only its public recipient and public digest to
   this builder; never copy or serialize the private identity here.
3. Use a separately reviewed create-only encrypted publisher to create the
   actual artifact, read back its immutable exact version, and obtain its
   exact ciphertext/plaintext evidence.  It must provide a fresh immutable
   locator expectation matching the builder's deterministic key and release
   binding.  This builder cannot mint or verify provider-side evidence.
4. Prepare and independently review this descriptor while all three inputs
   remain fresh.  A crash, expiry, or reuse refusal means start a new
   campaign; do not delete or alter its local markers to retry.
5. Only a later separately authorized consumer may map the reviewed exact
   descriptor into the appropriate private pull/receive contract.  That
   consumer must independently root-pin the exact immutable version and use
   its own credentials and private identity boundary.

In particular, this descriptor is not sufficient to publish the 295 MB
release image artifact, to pull it to WA-IR or Witness, to alter any server,
or to begin Full Matrix.

## Focused verification

```bash
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -B -m unittest \
  tests.test_physical_wa_ir_bootstrap_bundle_builder -v
```

The focused suite uses only synthetic public metadata and a temporary local
root-owned state directory.  It covers default-off policy, fresh sealed
release/recipient/locator requirements, exact Object coordinate checks,
private/historic identity denial, durable non-reuse, descriptor tamper
refusal, and the absence of network, Object-Storage, SSH, Docker, or command
execution imports/calls.
