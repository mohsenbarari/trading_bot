# Fresh V4R Phase-5 reverse strict-ACK migration

## Status

This is an implementation map only.  It starts no campaign, changes no
current V4 behavior, and grants no deployment, writer, promotion, or
Full-Matrix authority.

The current V4 Phase 5 is deliberately frozen as:

```text
name:              ir-writer-v2-witness-roundtrip-strict-ack-matrix
oracle:            ir-writer-v2-witness-roundtrip-strict-ack-oracle-v1
transport profile: ir-v2-witness-roundtrip-strict-ack-v1
```

That label is not an ABI for the separately versioned reverse carrier.  The
existing `physical_wal_v2r_witness_roundtrip_contract` is an isolated,
evidence-only route (`WA-IR -> Witness -> WA-FI -> Witness -> WA-IR`); its FI
acknowledgement says `fi-recovery-evidence-observed`.  It is not a durable
IR-writer acknowledgement, does not bind a V4 effect start, and cannot be
relabelled as a successful Phase-5 oracle.

## Decision: a fresh catalog/anchor generation, not an in-place rename

Do **not** mutate the current V4 catalog or teach it a compatibility alias.
The current phase tuple is included in the V4 plan hash and root-composition
policy digest; sequence-to-name is also signed in every Witness-anchor
commitment.  Relabelling sequence 5 in place would make an old receipt or
immutable anchor either parse under changed semantics or fail ambiguously.

Introduce an isolated **V4R** generation for a completely fresh campaign,
with no import, adapter, parser, journal, anchor, or resume path for V4
plans/receipts/anchors.  The V4R Phase-5 canonical tuple is:

```text
sequence:          5
name:              ir-writer-v2r-witness-roundtrip-strict-ack-matrix
oracle:            ir-writer-v2r-witness-roundtrip-strict-ack-oracle-v1
transport profile: ir-v2r-witness-roundtrip-strict-ack-v1
```

`ir-writer-v2-strict-ack-matrix`, the current V4 Phase-5 name, and any
informal `reverse` spelling are rejection cases in V4R.  A campaign that has
any V4 journal/anchor record is not migrated or resumed; it is terminally
separate from a newly initialized V4R campaign and genesis.

## Required atomic implementation set

1. Add a new, isolated execution driver (for example
   `physical_full_matrix_execution_driver_v4r.py`) with distinct driver,
   plan, and receipt schemas.  Copy the eight-phase graph only after review,
   change Phase 5 to the tuple above, change its exact reverse-successor
   check to the new name, and make every public parser reject V4 schemas and
   all old Phase-5 labels.  Do not import or adapt the V4 driver.

2. Add a matching V4R root-composition module and policy schema.  Its policy
   digest must cover the new full catalog, including all five Phase-5 fields
   (sequence, name, oracle, destructive flag, and transport profile).  Fresh
   V4R phase bindings must be an exact eight-name set; a V4 adapter bag is
   invalid.

3. Add a V4R materialization-preflight module.  It must consume only the
   V4R composition, V4R binding types, and a V4R Witness-anchor identity.
   Its phase-material loop must compare the exact new Phase-5 tuple and must
   reject a current V4 identity, plan hash, or adapter binding.

4. Add a separate V4R Witness-anchor wire, ledger, adapter, and receipt
   journal with distinct schemas, signing domains, fixed state roots, and
   sequence-to-name map.  The new wire must derive Phase 5's label from
   sequence, never accept it from a caller, and must not dual-parse V4
   anchors.  A new V4R genesis binds the new V4R plan and journal hashes;
   reusing a V4 genesis/head is forbidden.

5. Add V4R installation-provenance schemas and issuer policy.  Its
   phase-to-issuer mapping still assigns Phase 5 to `webapp_ir`, but its
   phase-binding and installation-binding hashes must cover the V4R tuple.
   Existing V4 signatures are not a substitute for fresh V4R host
   attestations.

6. Implement the reverse strict-ACK ABI before any Phase-5 adapter.  It
   needs a new process-local Phase-5 provenance boundary that requires:

   - an exact V4R Phase-5 request and its root-journal effect-start
     correlation;
   - a fresh IR writer term/lease and the post-promotion reverse readiness;
   - a separately signed V2R four-hop result with all four role-local durable
     anti-replay reservations and the exact FI recovery frontier; and
   - a post-effect, transactionally bound IR durable-write response that
     carries the V4R effect/request keys or a verified one-to-one bridge.

   A bare V2R return envelope, a normal V2/Gen2 strict response, a local
   test double, or a pre-effect observation must fail closed.  The provenance
   result remains non-authorizing until a root-owned coordinator provides
   capture/checkpoint/reconciliation and returns an exact fresh V4R oracle.

7. Only after the above contracts and fresh signed installation attestations
   exist may a root-owned Phase-5 adapter be designed.  It must use the
   Witness-journal effect-start authority, preserve the Object-Storage-only
   cross-site route, and return an oracle with the V4R Phase-5 tuple exactly.
   This document does not define such an adapter.

## Source trace

The migration must be reviewed as one semantic change across these current
V4 locations:

| Surface | Current pin that makes an in-place rename unsafe |
| --- | --- |
| `core/physical_full_matrix_execution_driver_v4.py` | `_PHASE_CATALOG`, `_PHASES_BY_NAME`, request/receipt hashes, and `_require_reverse_successor` all bind the current Phase-5 name/oracle/profile. |
| `core/physical_full_matrix_v4_root_composition.py` | `_phase_payload()` places the exact catalog in `policy_sha256`; phase bindings compare the tuple field-for-field. |
| `core/physical_full_matrix_v4_materialization_preflight.py` | Each materialized binding is checked against the composition and current catalog by name, sequence, oracle, profile, and policy digest. |
| `core/physical_full_matrix_v4_phase_installation_provenance.py` | Required phase names, `_PHASE_ISSUER_SITE`, and signed phase/installation bindings are catalog-bound. |
| `core/physical_full_matrix_v4_witness_anchor_wire.py` | `_PHASES` maps sequence 5 to the signed commitment label; commitment construction derives it and parsing requires the exact pair. |
| `core/physical_full_matrix_v4_witness_anchor_ledger.py` and `core/physical_full_matrix_v4_receipt_journal.py` | They persist/verify V4 wire commitments and bind phase sequence, request hash, plan, and anchor lineage. |
| `scripts/run_physical_full_matrix_v4.py` | It remains deliberately non-operational and must not acquire a V4R execution CLI or deserialize typed capabilities. |

## Test gates for the migration branch

- The old V4 root composition and old V4 Witness-anchor wire must reject both
  the legacy V3 Phase-5 alias and the V4R Phase-5 tuple.  The focused pure
  tests added with this map lock that current fail-closed property.
- New V4R tests must prove the converse: only the V4R tuple is accepted;
  every V3/V4 alias and V4 anchor/receipt/policy/attestation is rejected.
- Re-run V4, V4R, V2R, root-composition, materialization, installation,
  journal, and anchor suites together.  A passing local suite is still not
  live evidence and is not permission to execute Full Matrix.
