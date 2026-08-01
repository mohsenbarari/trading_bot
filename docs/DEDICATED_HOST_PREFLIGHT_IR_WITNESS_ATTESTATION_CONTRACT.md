# WA-IR to Witness read-only preflight attestation

`core.dedicated_host_preflight_ir_witness_attestation` and
`core.dedicated_host_preflight_ir_witness_attestation_runtime` define the
WA-IR-originated evidence half of dedicated-host preflight. They are
default-off and local-only. They do not open a network connection, use Object
Storage, age, SSH, Docker, a Writer Witness term, a database, or a service.

The purpose is narrowly to let WA-IR attest its own existing canonical
`three-site-dedicated-host-preflight-receipt-v2` observation to a
separately reachable Witness. It is not a deployment, writer, promotion,
external-effect, or Full-Matrix authorization.

## Fixed local WA-IR inputs

Only a root runtime with explicit enablement may read these paths:

```text
/etc/trading-bot/security/dedicated-host-preflight/
  wa-ir-witness-attestation-request.json  # root:root 0600
  wa-ir-witness-attestation-key.json      # root:root 0400
```

Both paths and every ancestor must be canonical, root-owned, non-symlink and
not writable by group or other. The request is a bounded canonical campaign
document. It fixes the existing v2 read-only request, its exact raw SHA-256,
campaign, operation, release, manifest, `webapp_ir` identity, a single use
attestation UUID, nonce, short validity, and the expected WA-IR key ID.

The key file is a distinct canonical Ed25519 key-record schema with the sole
purpose `dedicated-host-preflight-wa-ir-witness-attestation-key`. It is not a
raw key file and is deliberately at a different path from WA-IR age identity,
Object-Storage credentials, and every Writer/Writer-Witness key. Operators
must generate unique key material for this purpose and pin only its public key
and key ID at Witness and the central verifier. The attester never reads any
of the other credential files, so it cannot use them as a fallback.

## WA-IR envelope

The root attester accepts exactly one caller-supplied canonical v2 WA-IR
receipt. It first verifies the receipt against the pinned local request, then
creates the bounded canonical envelope schema
`three-site-dedicated-host-preflight-wa-ir-witness-attestation-envelope-v1`.
The Ed25519 signature is domain separated and binds all of the following:

- the raw request and root-pinned request hash;
- campaign, operation, release, manifest, WA-IR role and provider identity;
- single-use attestation ID, nonce, issuance time and short expiry;
- the exact canonical inner receipt and its SHA-256; and
- the dedicated WA-IR signer key ID and purpose.

The attester has no submit method. A later, explicitly reviewed injected
WA-IR-to-Witness transport may carry only these bytes. There is no FI-to-IR
channel, Object-Storage preflight branch, or permission for a controller to
open the WA-IR request, key, age identity, or Object-Storage credential.

## Request provisioning boundary

The former request-provisioning P0 is implemented separately by
`core.dedicated_host_preflight_ir_request_provisioning`, its FI publisher
runtime, and its WA-IR receiver runtime. The enforced route is:

```text
FI publisher → private versioned Object Storage → WA-IR exact-version pull
    → root-pinned local request file
```

It binds the dedicated FI signature, immutable key and VersionId, exact
ciphertext/plaintext hashes, locator hash, short freshness window, paired
Object-Storage identity binding, replay state, and atomic root-owned
replacement. It has no direct FI-to-IR channel, mutable `latest` object,
bucket list, controller write, or `AgentDelivery` integration. The detailed
contract and the remaining live operator prerequisites are in
[`DEDICATED_HOST_PREFLIGHT_REQUEST_PROVISIONING_CONTRACT.md`](DEDICATED_HOST_PREFLIGHT_REQUEST_PROVISIONING_CONTRACT.md).

## Dual-signature verification

The central side must not treat a WA-IR envelope as host evidence by itself.
It consumes only a Witness evidence envelope and independently verifies both
the Witness signature and the nested WA-IR signature, request binding,
identity, nonce, and freshness. Only then does it return the exact canonical
v2 receipt to the existing four-role aggregate. An expired envelope, wrong
public key/key ID, wrong purpose, malformed/noncanonical JSON, modified inner
receipt, or altered hash fails closed.

No evidence produced here authorizes writing, promotion, execution, or Full
Matrix completion; each such boolean is explicitly false in Witness evidence.

## Focused verification

```bash
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -B -m unittest -q \
  tests.test_dedicated_host_preflight_ir_witness_attestation \
  tests.test_dedicated_host_preflight_ir_witness_attestation_runtime
```
