# Estimator display

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
