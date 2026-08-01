# Signed FI-to-WA-IR preflight-request provisioning

`core.dedicated_host_preflight_ir_request_provisioning` plus the two separate
root-only runtimes close the narrow request-provisioning path needed by the
WA-IR → Witness attester:

```text
FI typed request + fresh paired preflight
  -> FI-only signature + age encryption
  -> private versioned Object Storage (create-only)
  -> WA-IR exact Key + VersionId GET
  -> age decrypt + two signature/hash/freshness checks
  -> atomic root-owned WA-IR attestation-request file
```

It is local/default-off code. Importing or constructing either runtime does
not contact Arvan, run `age`, read a credential, or modify a host. No live
host, bucket, Object Storage policy, key, identity, or service was changed by
this implementation.

## Strict separation

The FI runtime reads only these FI-owned fixed inputs:

```text
/etc/trading-bot/security/dedicated-host-preflight/
  fi-wa-ir-request-provisioning-key.json             # root:root 0400
/etc/trading-bot/security/arvan-s3-fi-publisher-credentials.json  # existing FI-only file
```

It emits only this non-secret, redacted local locator after a successful
create-only PUT and exact-version readback:

```text
/var/lib/trading-bot/dedicated-host-preflight/
  fi-wa-ir-attestation-request-locator.json          # root:root 0600
```

The WA-IR receiver reads only these WA-IR-owned fixed inputs:

```text
/etc/trading-bot/security/dedicated-host-preflight/
  wa-ir-witness-attestation-request-locator.json     # root:root 0600
  wa-ir-preflight-request-age-identity.txt           # root:root 0400
/etc/trading-bot/security/arvan-s3-ir-receiver-credentials.json  # existing IR-only file
/var/lib/trading-bot/dedicated-host-preflight/
  wa-ir-preflight-request-age/                       # root:root 0700
  wa-ir-preflight-request-replay/                    # root:root 0700
```

Its only successful install destination is the existing attester input:

```text
/etc/trading-bot/security/dedicated-host-preflight/
  wa-ir-witness-attestation-request.json             # root:root 0600
```

The FI signing key is a separate canonical Ed25519 key record with purpose
`dedicated-host-preflight-fi-wa-ir-request-provisioning-key`. It must not be
the WA-IR attestation key, a Witness key, a writer/lease key, an age identity,
or an Object Storage credential. The receiver pins its public key. The age
identity is separate from every signing key.

## Immutable delivery rules

The FI payload is short-lived (at most 300 seconds and no longer than the
typed WA-IR request). It binds the canonical request hash, campaign,
operation, release, manifest, unique attestation ID, nonce, FI/IR Object
Storage identity fingerprints, route-binding hash, recipient, issuance, and
expiry. The FI publisher accepts only that typed signed payload, not caller
selected JSON or object selectors.

The generated object key is in the dedicated immutable namespace
`dedicated-host-preflight/v1/.../wa-ir-witness-request/...`. FI performs only
`PutObject` with `IfNoneMatch: *`, then an exact `GetObject` for the returned
version. The locator contains no endpoint, bucket, URL, credential, or
plaintext; it fixes the object key, VersionId, ciphertext hash/size, exact
metadata, payload/request hashes, bindings, freshness window, and a second
FI signature.

WA-IR accepts only a root-pinned locator whose SHA-256 matches its campaign
policy. It never lists, discovers a VersionId, follows a URL, writes Object
Storage, opens the FI credential, or calls FI. Its receiver client exposes
only one pre-approved `GetObject(Bucket, Key, VersionId)`. It decrypts only
with the dedicated WA-IR age identity and compares the inner FI signed payload
to every locator and fresh preflight binding before the atomic replacement.

The receiver replay ledger holds the locator/payload/request hashes plus
attestation ID and nonce. A repeated locator, payload, ID, or nonce is
rejected before another Object Storage GET. No successful result grants
writer, promotion, execution, controller, or Full-Matrix authority.

## Locator relay is intentionally external and non-controlling

There is intentionally no FI→WA-IR socket, SSH action, controller write, or
`AgentDelivery` integration in these runtimes. An operator must arrange a
reviewed, unidirectional **non-secret locator relay** that atomically places
the exact FI-generated locator at WA-IR's fixed input and separately pins its
SHA-256 in the enabled receiver policy. The Object Storage object itself is
the only payload transfer. A mutable `latest` locator, controller delivery,
or direct FI connection is not an acceptable substitute.

## Live prerequisites before use

1. Complete a fresh paired FI-publisher/IR-receiver Arvan immutability
   preflight bound to the exact campaign, release, endpoint, region, bucket,
   and distinct identities.
2. Confirm the private versioned bucket retains immutable exact versions and
   permits the FI account only create-only PUT plus exact readback in the
   dedicated prefix; confirm the WA-IR account permits only exact GET/HEAD in
   that prefix, with no list, PUT, or delete.
3. Provision the fixed root-owned paths and modes above, a unique FI signing
   key/public pin, and a unique WA-IR age identity/recipient. Do not reuse any
   pre-existing signing key.
4. Install a reviewed locator relay with no direct FI control of WA-IR and no
   controller access to WA-IR secrets. Pin the received locator SHA-256 for
   the active campaign.
5. Run the fresh FI publish and WA-IR receiver preflight locally, then let the
   existing WA-IR attester and Witness path generate evidence. That evidence
   still must pass the independent campaign readiness and Full-Matrix gates.

## Focused verification

```bash
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -B -m unittest -q \
  tests.test_dedicated_host_preflight_ir_request_provisioning \
  tests.test_dedicated_host_preflight_request_provisioning_runtime
```

The suite uses only in-memory fake S3/age adapters. It covers signature and
locator tampering, expiry, create-only FI readback, exact receiver pull,
atomic request installation, replay rejection, and the absence of a
controller/`AgentDelivery` surface.
