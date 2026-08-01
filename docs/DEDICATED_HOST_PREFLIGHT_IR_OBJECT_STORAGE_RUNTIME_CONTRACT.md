# Retired WA-IR Object-Storage preflight receipt runtime

`core.dedicated_host_preflight_ir_object_storage_runtime` is permanently
retired.  It no longer reads a policy, locator, age identity, credential, or
replay state; it cannot provision an S3 client and it is not imported by the
controller or `core.dedicated_host_preflight_runtime_transport`.

This matters because the old runtime represented a generic WA-IR preflight
receipt-pull bridge.  The three-site architecture requires a different trust
direction:

```text
FI signed request -> private versioned Object Storage -> WA-IR attester
WA-IR signed envelope -> Witness durable ledger -> central Witness query
```

The first line is the separately scoped request-provisioning protocol.  The
second is the sole active receipt-evidence path.  Neither line permits a
central Object-Storage receipt pull, direct Finland-to-Iran receipt control,
or a bypass around Witness.

## Fixed outcome

The retired module provides only a redacted audit result:

```json
{
  "error": "IR_OBJECT_STORAGE_RUNTIME_RETIRED_NO_DIRECT_OR_BYPASS_ROUTE",
  "reason": "no-direct-or-bypass-route",
  "status": "blocked"
}
```

There is no configuration that changes this outcome.  In particular, the
former `provision_root_owned_ir_object_storage_pull_delivery` and
`load_and_provision_root_owned_ir_object_storage_pull_delivery` interfaces do
not exist.  This is intentional removal of the generic compatibility bridge,
not a missing deployment input.

## Verification

The focused tombstone tests assert both fixed blocked outcomes, the absence of
a provisioning/`AgentDelivery` API, and controller rejection of the historical
route before an observer is invoked.
