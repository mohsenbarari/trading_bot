# V4 Phase-8 final-convergence admission boundary

`core.physical_full_matrix_v4_final_convergence_admission` is a default-off,
pure contract for the final read-only Phase-8 observation. It is not a
Phase-8 adapter, live Full-Matrix runner, host probe, signer, trusted clock,
or authorization source.

## Required future evidence

After the V4 driver creates the exact Phase-8 `effect-started` record, the
root adapter receives its private request copy. The contract first requires:

- the exact Phase-8 effect-start authority; and
- its exact Witness-attested anchor proof.

It projects their public redacted pins field by field into
`PhysicalFullMatrixV4Phase8EffectStartAnchorBinding`, not a generic tuple
hash. The projection repeats the final FI successor binding from P7 (holder,
epoch, lease, witnessed term, readiness, route, transition, and roundtrip
pins), the effect-start identity/claim, and every immutable Witness anchor
pin, including the effect-started timestamp.

Four independently verified opaque capabilities are required:

1. FI primary readback.
2. IR standby/replay readback under that final FI successor.
3. Object/blob lineage and version parity.
4. Fresh Witness term and FI-primary route state.

Each claim must repeat the typed Phase-8 binding exactly, have a distinct
content identifier, be observed at or after the exact Phase-8 effect-start
time, and declare a lifetime no longer than five minutes. A stale Phase-5
IR-writer term/readiness/route cannot satisfy the IR-standby slot because it
does not match the final P7 successor binding.

There is one additional predecessor-chain requirement: an explicit typed P7
**completion** provenance/anchor. P7's effect-start anchor is not enough.
P7 completion carries its receipt and completion-anchor pins through the
shared root-journal `PhysicalFullMatrixV4PredecessorPhaseCompletionAnchorProof`.
The journal derives that object only from its durable create-only records after
re-reading the current Witness head. Its completion anchor's sequence/head
must equal the `previous_sequence` and `previous_head` of the subsequent P8
effect-start anchor. The Phase-8 contract projects that exact private bridge
into its own opaque P7 provenance capability; it has no raw-receipt or
P7-start fallback.

## Current status

The four owner-specific live verifiers and root Phase-8 runtime do not exist.
Raw claims, dictionaries, `evidence_sha256`, and the generic V4 phase oracle
are rejected or blocked. Even a test-only structurally cross-pinned opaque
bundle returns `blocked-typed-evidence-cross-pinned-not-final-admission`, with every
authorization and `full_matrix_executed` flag false.

This boundary prevents a false final-convergence claim; it does not make the
Full Matrix runnable or complete.
