# Dedicated-host preflight Arvan ECC readback contract

`core/dedicated_host_preflight_arvan_ecc_readback.py` is the default-off,
root-only implementation of the controller's read-only `ProviderReadback`
interface.  It converts one official Arvan ECC server-detail response into the
canonical raw provider evidence required by
`core/dedicated_host_preflight_controller.py`.

It is not a provisioning, update, deletion, SSH, Docker, host, traffic, or
Full-Matrix execution adapter.

## Fixed authority and request boundary

The adapter has no caller-selected URL, endpoint, region, instance ID, query,
header map, environment variable, command, or credential path.

- The endpoint is fixed to `https://napi.arvancloud.ir/ecc/v1`.
- The only invocation is `GET /regions/{region}/servers/{instance_id}`.
- `region` and `instance_id` are derived only after the supplied
  `DedicatedHostTarget` exactly matches the source-pinned four-host inventory.
- The target's role, public IPv4, region, instance ID, delivery contract, and
  nonzero host-key digest are independently revalidated before the fixed
  root-owned secret file is opened or the injected runner is reached.
- The secret config path is fixed to
  `/etc/trading-bot/security/dedicated-host-preflight/arvan-ecc-readback.json`.
  It must be a canonical ASCII JSON root-owned `0600` regular file under
  root-owned non-writable ancestors.  Its exact schema requires `enabled:true`
  and one opaque API key.  It cannot redirect the endpoint or select a host.

The public adapter switch is also default-off.  Both that switch and the fixed
secret config must be explicitly enabled, and the process must be root, before
any runner can be invoked.

## Bounded runner seam

There is intentionally no HTTP library in the module.  A separately reviewed
runtime may inject only:

```python
run(invocation=ArvanEccGetServerInvocation(...))
```

The immutable invocation contains the fixed endpoint, literal `GET`, exact
source-pinned path, fixed `Apikey` scheme, and opaque key.  It accepts no
general HTTP method, URL, query string, headers, request body, proxy,
environment, or transport option.  The runner returns only an integer status
and bounded bytes; any exception, non-200 response, malformed response, or
oversize response becomes a fixed redacted failure code.

The API key is private implementation state (`repr=False`) and is never copied
to output evidence, a receipt, or an exception.  The provider body itself is
also not returned.

## Response normalization

Only an ECC server-detail response proving the exact target is accepted:

- exact canonical server UUID;
- `status: "ACTIVE"`, normalized to controller `status: "running"`;
- the exact expected public IPv4 as a public IPv4 address; and
- if a response carries `region`, an exact match with the source-pinned target.

The adapter rejects duplicate/malformed JSON, unsupported constants, foreign
ID/IP/region/status, absent or malformed addresses, extra public addresses,
and secret-, URL-, header-, credential-, cookie-, or proxy-shaped response
content.  It emits only the controller's canonical newline-terminated raw
document and the exact SHA-256/provenance wrapper:

```json
{"instance_id":"…","provider":"arvan_ecc","public_ipv4":"…","region":"…","role":"…","schema":"three-site-dedicated-host-provider-readback-v1","status":"running"}
```

Controller validation still re-parses and binds this output to the four-host
campaign.  An accepted readback is only `status=observed`; it is never a
readiness, writer, deployment, or mutation authorization.

## Focused verification

```bash
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -B -m unittest \
  tests.test_dedicated_host_preflight_controller \
  tests.test_dedicated_host_preflight_arvan_ecc_readback -v
git diff --check
```
