# Four-role Arvan S3 role-local collector

`core/physical_arvan_s3_four_role_immutability_role_local_collector.py` is the root-owned S3 execution boundary for the pure four-role immutability runtime. It does not replace durable live-IAM admission, the Witness ledger, or the four-role preflight verifier. It only turns one bounded runtime request into one redacted semantic readback.

The boundary is default-off. It does not accept an endpoint, credential path, bucket, direct site address, generic S3 client, or a second role credential from a caller.

## Required placement

| Host | Local role | Fixed collector config | Fixed credential |
| --- | --- | --- | --- |
| WA-FI | `fi-publisher` | `/etc/trading-bot/security/arvan-s3-four-role-immutability-fi-publisher.json` | `/etc/trading-bot/security/arvan-s3-fi-publisher-credentials.json` |
| WA-FI | `fi-receiver` | `/etc/trading-bot/security/arvan-s3-four-role-immutability-fi-receiver.json` | `/etc/trading-bot/security/arvan-s3-fi-receiver-credentials.json` |
| WA-IR | `ir-receiver` | `/etc/trading-bot/security/arvan-s3-four-role-immutability-ir-receiver.json` | `/etc/trading-bot/security/arvan-s3-ir-receiver-credentials.json` |
| WA-IR | `ir-publisher` | `/etc/trading-bot/security/arvan-s3-four-role-immutability-ir-publisher.json` | `/etc/trading-bot/security/arvan-s3-ir-publisher-credentials.json` |

Nothing is installed on Witness for this collector. Witness remains responsible for its separate durable live-IAM admission/ledger boundary.

Every local config and credential file must be a non-symlink regular file, owned by UID 0, mode `0600`, and single hard link. Its parent security directory must be UID 0 and mode `0700`. The collector uses `O_NOFOLLOW` and rechecks metadata before and after reading. It never accepts a path from a CLI argument or environment variable.

## Exact non-secret config grammar

Each of the four fixed config files has exactly these fields and no others:

```json
{
  "schema": "gold-trade-physical-arvan-s3-four-role-immutability-role-local-collector-config-v1",
  "role": "the fixed local role for this file",
  "endpoint": "the approved Arvan S3 endpoint from the four-role binding",
  "region": "the approved binding region",
  "bucket": "the approved private binding bucket",
  "retention_days": 90,
  "enabled": true
}
```

The literal role determines route direction and namespace; they are not configurable fields:

| Role | Direction | Source/destination | Namespace |
| --- | --- | --- | --- |
| `fi-publisher` | FI publisher → IR receiver | `webapp_fi` → `webapp_ir` | `physical-wal` |
| `ir-receiver` | FI publisher → IR receiver | `webapp_fi` → `webapp_ir` | `physical-wal` |
| `ir-publisher` | IR publisher → FI receiver | `webapp_ir` → `webapp_fi` | `physical-failback` |
| `fi-receiver` | IR publisher → FI receiver | `webapp_ir` → `webapp_fi` | `physical-failback` |

All four files must pin the same approved endpoint, region, private bucket, and retention-days value. For `fi-publisher`, live `GetObjectLockConfiguration` must show exactly that many default `COMPLIANCE` days; drift fails closed. `retention_days` must be 7–3650 and cannot be below the runtime binding's requested minimum.

The four existing credential files use the exact `gold-trade-physical-arvan-s3-machine-user-credential-v1` reader schema and are opened only for their matching role/profile. Credential values themselves must never be put into a config file, command line, log, receipt, or this document.

## Bounded S3 proof sequence

The collector validates the live runtime's exact role, direction, identity digest, campaign, release, endpoint, region, bucket, namespace, nonce-derived key, timestamp, retention floor, and (for a receiver) exact immutable version before opening a credential or importing the SDK.

`fi-publisher` proves private canonical-owner ACL, enabled versioning, default COMPLIANCE Object Lock, create-only `PutObject` with `IfNoneMatch='*'`, exact-key version listing, exact retention, exact `GetObject`/`HeadObject`, and denied unconditioned overwrite/delete/delete-version.

`ir-publisher` proves private canonical-owner ACL, enabled versioning, create-only immutable publication, exact-key version listing, exact `GetObject`/`HeadObject`, and denied unconditioned overwrite/delete/delete-version. It intentionally does not ask for Object Lock configuration or object-retention capability, because that is not part of its narrowly scoped profile.

Each receiver proves only exact-version `GetObject`/`HeadObject` and denied put/delete/delete-version/bucket-list/version-list. No receiver is permitted a bucket posture read, broad key enumeration, or publisher operation.

All outbound provider traffic is local host → the binding-pinned Arvan S3 endpoint. The module has no SSH, HTTP peer, socket, subprocess, or direct WA-FI ↔ WA-IR transport path.

## Read-only distinction

Loading a fixed config and producing an identity projection are local filesystem-only operations. They make no provider call. The complete immutability proof is deliberately not read-only: it must create a bounded immutable probe object and test that unsafe overwrite/delete/list operations are denied. A read-only bucket posture check can be useful diagnostically, but cannot establish create-only authority or denied mutation and therefore must never satisfy the Full-Matrix preflight gate.

## Wiring boundary

After the four role-local identity projections have been bound and a durable live-IAM admission exists, each host constructs its own collector from its local fixed config and supplies only `collector.live_probe_adapter()` to the existing four-role live-probe runtime. The runtime creates the fresh nonces, checks the durable admission first, and then invokes the four adapters in order. The collector returns only the runtime's publisher/receiver semantic readback dataclasses; raw SDK clients, raw provider responses, access keys, and secret keys never cross that seam.
