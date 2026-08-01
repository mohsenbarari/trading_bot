# Retired WA-IR Object-Storage preflight receipt route

`core.dedicated_host_preflight_ir_object_storage_pull_delivery` is a
**tombstone**, not a delivery implementation.  The former route
`private-versioned-object-storage-pull-agent` /
`object-storage-pull-readonly-receipt` is permanently retired from the
three-site dedicated-host preflight.

The active WA-IR receipt route is exclusively:

```text
witness-dual-signed-preflight-evidence /
collect-wa-ir-witness-preflight-evidence
```

The controller reads a source-pinned Witness endpoint, verifies the WA-IR and
Witness signatures, and then admits the inner WA-IR receipt.  It never uses
an Object-Storage pull, opens a WA-IR SSH session, or treats an FI observation
as a substitute for that evidence.

## Hard fence

The tombstone deliberately has no `AgentDelivery`, target, request,
controller, manifest, S3 client, credential, age, filesystem, subprocess, or
network surface.  The only public artifact is a fixed redacted record:

```json
{
  "error": "IR_OBJECT_STORAGE_PULL_RETIRED_NO_DIRECT_OR_BYPASS_ROUTE",
  "reason": "no-direct-or-bypass-route",
  "status": "blocked"
}
```

It contains the former route and phase only for audit correlation.  It grants
no permission to retrieve an object or to resume the former implementation.
The default gate is permanently false, rather than an operator-enabled
feature flag.

`core.dedicated_host_preflight_controller` carries the former route/phase in
a distinct retired-contract map solely to reject a submitted controller
configuration before any provider or delivery observer runs.  A candidate
preflight manifest has no delivery-route fields, so it cannot select the
former route.  Rejection is explicit: the legacy route is retired and **no
direct or bypass route exists**.

FI-to-WA-IR Object-Storage is still used only by the separate signed request
provisioning protocol.  That protocol is not a controller `AgentDelivery`
and does not turn its request receiver into a receipt path.

## Focused verification

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -q \
  tests.test_dedicated_host_preflight_ir_object_storage_pull_delivery \
  tests.test_dedicated_host_preflight_ir_object_storage_runtime \
  tests.test_dedicated_host_preflight_controller
```
