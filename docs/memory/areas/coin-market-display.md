# Estimator display

- 2026-11-23 | Native dashboard falls back to `MAIN_ONLINE` for stale or `SAFE_NO_DATA` Primary snapshots. Incident ledger: Imam tomorrow 244.35m at 10:00:04Z, upper 245.8m. Read-only replay was within 0.10m; ~5.65d-old group anchor had 12% weight, structural model 88%. Audit outage bias and interval coverage separately by settlement/anchor age before changing fallback.
- Snapshot receiver imports the engine version and rejects unsupported models.
  Deploy the compatible reader first; v9 explicitly also accepts unchanged-wire
  v8 for producer rollback. Accept activation only after actual Web ACK/freshness.
  Then explicitly select the fully staged prediction epoch for the parser;
  receiving snapshots alone never changes its authority. Verify that selected
  epoch's fresh anchors and an actual unnamed cash offer through the pipeline.
- Recently resolved historical group facts need a bounded export share by their
  new availability time, using the existing source/availability index. Source
  event time stays historical; cyclic history discovery alone delays corrected
  review tags. Reconcile via the normal dirty-group queue. Conditional/late and
  genuinely ambiguous older facts must keep their actual exclusion reason.

- Engine v9: a fresh private normal-paper minute outranks unconfirmed physical
  offers; preserve fresh verified physical trades. Never use unsettled public
  physical quotes as cash authority. Query bounded causal windows, never mix a
  completed minute with its raw constituents, and reserve HIGH for fresh anchors.
- Keep the private normal-paper channel reference separate from the selected
  model underlying. The latter may be a public fallback or a physical offer;
  label its source/form for each settlement. Never present either as a private
  paper rate under a shared channel heading.
- Private references use the same immutable snapshot's
  `PRIVATE_GOLD_PAPER_NORMAL_TODAY/TOMORROW` weighted-minute traces. Missing or
  stale cash stays missing; never substitute tomorrow/public prices. These
  references are display-only; model selection uses the separate engine policy.
