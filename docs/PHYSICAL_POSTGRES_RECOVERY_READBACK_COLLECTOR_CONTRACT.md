# Physical PostgreSQL recovery readback collector contract

`core/physical_postgres_recovery_readback_collector.py` is a fail-closed,
default-disabled local observation adapter. It converts one narrowly injected
local inspection into exact canonical
`PhysicalPostgresRecoveryReceiverReadbackEvidence` for the existing recovery
preflight.

It does **not** restore a base backup, replay WAL, start or stop PostgreSQL,
change recovery configuration, promote a standby, fence a writer, switch
traffic, run Docker, SSH, S3/Object Storage, deployment, or network activity.
It is not a writer authorization or a recovery execution boundary.

## Root-pinned input contract

The collector accepts one `PhysicalPostgresRecoveryReadbackRootConfig`. It is
disabled unless `enabled=True`. A production bootstrap must separately load
this policy from a root-owned source; the pure collector validates only its
explicit root marker (`root_owner_uid == 0`) and cannot itself prove filesystem
ownership.

The root policy has no SQL, command, path, environment, URL, credential, or
host field. It pins exactly:

| Pin | Required value |
| --- | --- |
| Collector identity | `root-owned-postgres-recovery-readback-collector-v1` |
| Inspection interface | `fixed-root-owned-postgres-recovery-inspection-v1` |
| Receiver role | `standby` only |
| Route | one source site and one distinct local receiver site |
| Stage receipt | bundle ID SHA-256, stage receipt SHA-256, and route-binding SHA-256 |
| Freshness | bounded evidence age, at most 300 seconds |

Before the inspector is called, the collector independently revalidates the
root policy, the signed current Witness term and the expected binding term,
the verified physical WAL Object-Storage bundle and its writer term, the
preflight binding's local standby and stage binding, and every root-pinned
route and stage-receipt hash. Any failure occurs before the inspector method
is looked up or invoked.

## Narrow local inspector

The only injected interface is:

```python
inspect_bound_recovery_receiver(
    *,
    request: PhysicalPostgresRecoveryReadbackInspectionRequest,
) -> PhysicalPostgresRecoveryLocalInspection
```

The request is created internally after all pin checks. It contains only fixed
non-secret identity, stage, bundle-terminal-LSN, term, baseline, system-ID,
timeline, and WAL-geometry facts. It cannot carry arbitrary SQL, a shell
command, file path, environment, endpoint, credential, or restore action.

The inspector must echo route, stage, terminal LSN, and full Witness-term
facts. It also reports only local PostgreSQL recovery state: `in_recovery`,
role, system identifier, timeline, WAL segment size, baseline generation,
replay LSN, and observation timestamp. The collector rejects wrong route or
stage, wrong terminal LSN, wrong term, stale time, invalid boolean,
non-standby state, incorrect system/timeline/geometry/baseline, and malformed
LSNs. A replay LSN below the exact bundle terminal frontier yields only the
explicit `staged-not-replay-verified` observation; it never becomes a replay
or promotion success claim.

`DisabledPhysicalPostgresRecoveryLocalInspector` is the safe default behavior
for a bootstrap that has not installed a separately reviewed local observer.

## Evidence output

After local inspection passes, the collector builds the exact canonical ASCII
JSON schema already required by the recovery preflight. The raw evidence binds
source, destination, receiver, stage bundle ID, stage receipt hash,
route-binding hash, manifest hash list, immutable object key/version list,
base manifest hash, terminal WAL LSN, the exact current Witness
holder/epoch/lease/transition/proof, and normalized local PostgreSQL readback.

It returns the raw bytes with their SHA-256 in
`PhysicalPostgresRecoveryReceiverReadbackEvidence`, then submits that evidence
to the existing pure preflight as a final local consistency check. A blocked or
mismatched self-check fails closed. This proves compatibility with the existing
admission contract; it does not perform a recovery action.

## Separate authorization still required

A real root-controlled recovery executor must be designed and authorized
separately. It needs explicit destructive-operation authorization, fencing,
process ownership checks, immutable object pull/decryption checks, restore and
ordered replay controls, runtime PostgreSQL checks, Witness CAS sequencing,
and observed destructive failover evidence. None of those capabilities are in
this collector.

## Focused verification

```bash
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -B -m unittest \
  tests.test_physical_postgres_recovery_preflight \
  tests.test_physical_postgres_recovery_readback_collector -v
git diff --check
```
