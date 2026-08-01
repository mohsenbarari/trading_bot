# Physical Full-Matrix campaign readiness contract

> **Migration status.** The historical V1 single-object base-backup bundle
> described in parts of this document is activation-fenced and cannot produce
> a positive readiness report.  Likewise, the historical two-role Arvan
> immutability evidence is explicitly rejected.  A local V2 chunked
> recovery/coverage bridge now exists, but it is deliberately non-authorizing:
> it has no V2 source request, receiver receipt, durable receiver ledger, or
> strict writer-response coupling.  Therefore it adds an observed diagnostic
> slot and the hard `v2-strict-remote-ack-chain-not-integrated` fence; it does
> not make the campaign runnable or translate any V1 evidence.

`core.physical_full_matrix_campaign_readiness` is the local-only readiness
oracle for one physical direction at a time. The initial direction is:

```text
webapp_fi (sole writer) -> private versioned Object Storage -> webapp_ir (standby)
                                      ^
                                   Witness term
```

It is an input boundary for a future non-retired physical Full-Matrix driver,
not an operational runbook or an execution coordinator.

## Deliberate safety boundary

The oracle accepts only typed/injected evidence. It never opens a path or
contacts a host, PostgreSQL, Object Storage, a Witness, Docker, SSH, a shell,
or a provider. It never starts replay, writes a ledger, performs transport,
changes a route, fences FI, promotes IR, sends an external effect, or runs a
test campaign.

Its only non-blocked status is `all-local-evidence-observed`. That does **not**
mean runnable: `execution_authorized`, `promotion_authorized`, and
`external_execution_authorized` are always `false`. A future root-owned
coordinator must recheck live Witness state, durable ledger/database state,
source fencing, and route state immediately before any effectful action.

## Process-local execution provenance

`PhysicalFullMatrixCampaignReadiness` is a public diagnostic/reporting value,
not a driver input.  The only accepted driver input is the opaque,
non-serializable `VerifiedPhysicalFullMatrixCampaignReadiness` returned by
`mint_verified_physical_full_matrix_campaign_readiness`.  Minting first runs
this assessor and succeeds only for an `all-local-evidence-observed` report
with no reasons and all authorization flags still false.

The wrapper retains the exact local config and injected evidence only in
process-local private state.  Plan construction may check that membership,
but an effectful driver must call
`require_verified_physical_full_matrix_campaign_readiness(..., now=...)` to
run the assessor again at its own clock.  A raw report, a caller-constructed
wrapper, a serialized/replayed wrapper, a changed report, or a newly blocked
reassessment is rejected before journal or adapter handling.  This provenance
mechanism is still not execution, promotion, route, or external-effect
authority.

## Campaign binding

`PhysicalFullMatrixCampaignBinding` pins campaign ID, release SHA, schema
revision, baseline generation and manifest, baseline/timeline/ordered stream,
Object Storage recipient and route hash, Writer term, exact acknowledged WAL
and Blob frontiers, recovery-stage receipt, deployment manifest/operation, and
the selected P0 operation UUID.

It accepts only one of the two explicit directional pairs: FI-to-IR or
IR-to-FI. In each case the source must project as `writer_source` and the
destination as `standby_receiver`. Reverse failback requires its own complete
IR-to-FI binding and fresh evidence; it cannot be satisfied by changing a
site field on normal-direction observations or by reusing FI credentials.

## Required evidence slots

| Slot | Required local check |
| --- | --- |
| V2 chunked recovery coverage | `VerifiedPhysicalFullMatrixV2RecoveryEvidence` revalidates one exact chunked base backup, its handoff/receiver admission, exact WAL and Blob object coverage, target WAL continuity, and signed target-recovery readback.  Its target LSN must equal the campaign target and Blob frontier.  It is observed only; its authorization flags must all be false. |
| Physical base + ordered WAL recovery | The historical V1 single-object WAL bundle is activation-fenced.  The V2 recovery slot does not replace it piecemeal: only an atomic V2 request/receipt/ledger/strict-writer-response integration may retire the V1 slots and fence. |
| Exact remote acknowledgement | Opaque signed FI request/IR receipt, request-bound IR recovery evidence, and typed durable-ledger result with exact request/receipt/hash/nonce/replay identity. The ledger path is never opened. |
| Strict writer response | Opaque `VerifiedPhysicalStrictRemoteAckWriterResponseObservation`, minted only by the root-owned strict ack-to-writer-response boundary after an exact signed FI request/IR durable receipt, request-bound IR recovery, live FI Witness term, active FI fence, local commit receipt, and one-use local receipt ledger all revalidate. The old boolean-shaped injected wrapper is not an oracle input. |
| Arvan immutable retention | Opaque four-role preflight plus its enabled typed configuration, durable live-IAM admission, live-IAM binding, and matching failback binding.  It proves private/versioned posture, retention/object-lock evidence, four separated identities, and exact-version survival after denied disposable delete attempts.  The historical two-role `VerifiedPhysicalArvanImmutabilityPreflight` is explicitly rejected. |
| Arvan reverse-route admission | A fresh opaque `VerifiedPhysicalIrToFiObjectStorageFailbackPreflight` under its enabled typed configuration. It binds the distinct IR-publisher/FI-receiver identities together with the normal pair, proves all four fingerprints differ, and pins `webapp_ir -> private Object Storage -> webapp_fi` plus `physical-failback`. A normal two-role preflight cannot satisfy this slot. |
| Receiver-ready Blob evidence | `VerifiedPhysicalBlobReceiverPromotionEvidence`, enabled current-pinned `PhysicalBlobReceiverPromotionEvidenceConfig`, and current `VerifiedPhysicalBlobObjectStorageBinding`; there is no Blob-config compatibility fallback. |
| Witness and roles | Current opaque Witness term and current opaque role activation, projecting FI writer/source and IR standby/receiver. Signature revalidation is not a live Witness query. |
| Deployment posture | Opaque four-host read-only preflight posture: pinned clean release, no existing matrix process, and writable FI/IR staging mounts. |
| Selected P0 auth/upload | Typed matching `PromotionContinuityParticipantsResult` with selected participants applied. The oracle does not query whether the caller committed its transaction. |
| External-effect reconciliation | Opaque term-bound `complete_no_resend` decision over the explicit effect scopes. The oracle never reads its root-owned file or authorizes a worker. |
| Source fence/recovery route | Opaque source fence observation: term-fenced-before-commit, private versioned Object Storage pull-only route, no direct FI-to-IR control, no legacy compatibility. |

Blob mapping evidence remains scoped to its pinned baseline LSN. It is a
receiver-ready prerequisite, not a claim that Blob coverage automatically
extends beyond that mapping. The exact WAL/Blob frontier is separately bound
by the WAL bundle and remote acknowledgement.

## V2 acknowledgement migration fence

The V2 recovery bridge intentionally stops before acknowledgement semantics.
It does not issue or verify a V2 source request, receiver receipt, durable
ledger entry, or source writer-response coupling.  Readiness therefore emits
the following stable diagnostic whenever valid V2 recovery evidence is
observed:

```text
v2-strict-remote-ack-chain-not-integrated
```

That fence and the V1 fence must remain until a reviewed V2 protocol replaces
the complete V1 acknowledgement chain atomically.  Reusing V1 remote-ack
types, mapping the V2 bridge into a V1 slot, or accepting an independently
valid later Blob frontier would be a false-positive path.

## Strict remote-ack writer-response gap

The signed request/receipt and IR durable ledger prove a narrow continuity
point. They do **not** prove that FI's application response waited for it or
fenced writes on failure. Therefore the oracle has a mandatory hard sub-slot:

```text
missing-strict-remote-ack-writer-response
strict-remote-ack-writer-response-mismatch
```

The oracle now accepts only the opaque observation minted by
`core.physical_strict_remote_ack_writer_response`; a raw data class or caller
booleans cannot satisfy this slot. The owning boundary revalidates the exact
FI-to-IR binding, live FI Witness term, signed remote acknowledgement,
request-bound IR recovery evidence, typed IR durable-ledger receipt, and a
fresh signed FI fence before it invokes its injected local writer transaction
boundary. That callback must atomically persist the local response and unique
remote-receipt consumption before it returns the separately signed durable
local commit receipt.

This is still not a transport implementation or a promotion permit. In
particular, Object Storage request/receipt transport remains a separate
follow-up: no local writer receipt may be presented as evidence that a request
completed a network/Object-Storage roundtrip. The normal direct FI-to-IR
control path remains forbidden.

## Retired runner rejection

The oracle never parses, loads, or translates old runner artifacts. Any
nonempty `legacy_runner_artifacts` input is rejected before other evidence is
inspected as `legacy-runner-artifact-rejected`. This includes the historical
two-server paths/schemas such as:

- `scripts/run_production_full_matrix.py`
- `scripts/run_staging_two_server_full_matrix.py`
- `production_full_matrix_runner_plan_v1`
- `staging_two_server_full_matrix_runner_v1`

The exported `LEGACY_FULL_MATRIX_RUNNER_PATHS` and
`LEGACY_FULL_MATRIX_RUNNER_SCHEMAS` list known IDs; an unknown nonempty
artifact is rejected too, rather than creating a compatibility parser.

## Deterministic blockers

The aggregate emits a stable ordered subset of these codes:

```text
driver-disabled
invalid-campaign-binding
invalid-assessment-clock
invalid-campaign-inputs
legacy-runner-artifact-rejected
missing-physical-wal-recovery-observation
physical-wal-recovery-observation-mismatch
missing-v2-chunked-recovery-evidence
v2-chunked-recovery-evidence-mismatch
v2-strict-remote-ack-chain-not-integrated
missing-physical-wal-bundle
physical-wal-bundle-mismatch
missing-remote-ack-evidence
remote-ack-evidence-mismatch
missing-remote-ack-receiver-recovery
remote-ack-receiver-recovery-mismatch
missing-remote-ack-durable-ledger
remote-ack-durable-ledger-mismatch
missing-strict-remote-ack-writer-response
strict-remote-ack-writer-response-mismatch
missing-arvan-object-storage-immutability-preflight
arvan-object-storage-immutability-preflight-mismatch
missing-arvan-object-storage-failback-preflight
arvan-object-storage-failback-preflight-mismatch
missing-blob-promotion-evidence
blob-promotion-evidence-mismatch
missing-current-witness-term
witness-term-mismatch
missing-current-role-activation
role-activation-mismatch
missing-deployment-preflight-posture
deployment-preflight-posture-mismatch
missing-p0-auth-upload-result
p0-auth-upload-result-mismatch
missing-external-effect-reconciliation-decision
external-effect-reconciliation-mismatch
missing-source-write-fence-recovery-route
source-write-fence-recovery-route-mismatch
```

`scripts/assess_physical_full_matrix_campaign_readiness.py` deliberately does
not deserialize opaque evidence. It only returns a blocked report
(`typed-injected-evidence-required`) or rejects a historical artifact ID.
Production integration must call the Python API with already verified objects
from their owning boundaries.
