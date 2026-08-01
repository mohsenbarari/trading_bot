# V4 predecessor completion-anchor bridge

`PhysicalFullMatrixV4PredecessorPhaseCompletionAnchorProof` is the typed,
non-authorizing bridge from a completed V4 phase to the next phase's already
journaled `effect-started` transition.

It is minted only by
`RootOwnedPhysicalFullMatrixV4ReceiptJournal.project_predecessor_phase_completion_anchor_proof`.
That method reopens the fixed root, validates the create-only local chain,
re-reads the current authenticated Witness head, and fails if a pending,
rollback, divergence, missing predecessor, or mismatched anchor is observed.
It does not call an adapter, host, provider, or transport.

The proof binds, field by field:

- the predecessor phase/effect/request/claim and derived effect-start identity;
- the predecessor's full Witness-anchored start pins;
- the exact completed receipt digest and full completion-anchor pins;
- the common journal, baseline, and Witness genesis pins; and
- the successor phase/effect/request/claim/start identity and its anchor pins.

The critical fence is exact: predecessor completion
`anchor_sequence`/`anchor_head_sha256` must equal the successor start
`anchor_previous_sequence`/`anchor_previous_head_sha256`. The predecessor
completion must in turn directly follow that predecessor's effect-start
anchor.

The Python object is process-local and nonserializable, but it is not derived
from a live-only completion cache. After a restart, it can be freshly minted
only after the new successor start is durable and the current external head is
read again. It has no raw receipt bytes, authority to write/promote/execute,
or Full-Matrix-completed flag; every authorization flag remains false.
