# Dedicated-host preflight runtime transport

`core.dedicated_host_preflight_runtime_transport` is the concrete,
default-off runtime seam for the four-role read-only preflight. It does not
deploy a release, mutate a provider resource, reload a service, authorize a
writer, promote WA-IR, or start Full Matrix.

The controller only starts concrete transports when it is root, the fixed
root-owned `0600` runtime file is explicitly enabled, and all four required
routes can be assembled before the first ECC request or SSH process.

```json
{"direct_finland_to_iran":"forbidden","enabled":true,"fi_receipt_transport":"pinned-ssh-readonly-agent","ir_receipt_transport":"pinned-ssh-witness-evidence-agent","mode":"read-only","provider_transport":"fixed-https-get-only","schema":"three-site-dedicated-host-preflight-runtime-transport-config-v2"}
```

The fixed runtime file is:

```text
/etc/trading-bot/security/dedicated-host-preflight/runtime-transport.json
```

## FI and Witness own receipts

Bot-FI, WA-FI, and Witness use the existing root-only pinned SSH receipt
adapter. Its process runner accepts only the immutable argv emitted by that
adapter, uses no shell and a clean environment, pins the source-known host key
and fixed controller identity, caps stdout/stderr and the total exchange, and
does not release stdout on a non-zero exit.

## WA-IR evidence is Witness-mediated

`webapp_ir` is no longer provisioned by the central runtime through Object
Storage. The old central preflight Object-Storage branch is not imported or
called here.

Instead the runtime requires two independent components before any transport:

1. a validated source-pinned `witness` controller target; and
2. the fixed root-owned public verifier policy at:

   ```text
   /etc/trading-bot/security/dedicated-host-preflight/
     wa-ir-witness-evidence-verifier.json  # root:root 0600
   ```

That policy contains only one canonical WA-IR attestation request and the
WA-IR/Witness public keys. It contains no WA-IR private key, age identity,
Object-Storage credential, locator, or ingress capability.

The distinct `RootOwnedWitnessEvidenceSshProcessRunner` sends no stdin and can
run only this literal command against the Witness source-pinned IP and host
key:

```text
preflight-witness-evidence@<Witness-IP> \
  collect-wa-ir-witness-preflight-evidence
```

The adapter validates the returned canonical evidence with both independently
pinned signatures, request hash, campaign/operation/release/manifest,
instance identity, nonce, and expiry. Only then does it return the exact inner
`three-site-dedicated-host-preflight-receipt-v2` for `webapp_ir` to the
existing aggregate. No controller process opens a WA-IR SSH session.

The delivery provenance's WA-IR host-key field continues to bind the
source-pinned WA-IR controller target. The separate Witness SSH host key is
verified before the process starts and is never misrepresented as a WA-IR
transport hop.

## Separate WA-IR ingress

The WA-IR attester and the delivery of its signed envelope to Witness are a
separate, default-off ingress architecture. This central read path neither
implements nor selects that ingress. It simply requires that the local Witness
ledger has already durably admitted exactly the request-pinned envelope.

No result from this module authorizes writing, promotion, execution, or Full
Matrix completion.

## Focused verification

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -q \
  tests.test_dedicated_host_preflight_runtime_transport \
  tests.test_dedicated_host_preflight_witness_evidence_pinned_ssh_delivery \
  tests.test_dedicated_host_preflight_witness_evidence_runtime \
  tests.test_run_dedicated_host_readonly_preflight_controller
git diff --check
```

All transport tests inject process objects or local temporary files; they do
not open a socket, execute SSH, access Object Storage, or use production
credentials.
