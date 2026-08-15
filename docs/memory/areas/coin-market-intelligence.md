# Coin Market Intelligence

- USD/Herat abbreviation repair is an ingest-time causal decision: use at
  least three strictly-prior observations from the same source/book in a
  bounded 15-minute range, reconstruct omitted leading digits from that range,
  and never add a fixed price constant. Historical replay must be chronological.
- Coin-group settlement supports both generations: old `خ ن ف`/`ف ن ف` and
  current `خ ف`/`ف ف` mean tomorrow; old `خ ن`/`ف ن`, current single `خ`/`ف`,
  and standalone `نق` mean cash. Explicit future delivery wins over `ن`.
- Reject a malformed coin-group envelope individually; inverted edit/create
  times must not poison valid siblings or freeze the checkpoint. The dashboard
  keeps collector heartbeat, latest canonical group event, latest eligible event and
  the rate's selected anchor distinct; offer and trade freshness are computed
  independently and historical accepted rows are never labelled as live intake.
- A group trade requires one structurally linked reply branch. Keep users and
  sibling branches isolated, prefer attributable owner confirmation, use the
  latest negotiated quantity/price on that branch, deduplicate declarations
  from one fill, and keep ambiguous/overfilled facts out of model eligibility.
- Do not restore `group_commodity_context` or silently default an omitted coin
  to Imam. Commodity resolution uses strictly-prior same-book Market Store
  anchors and fails closed when context is insufficient or conflicting.
- Live coin-intelligence systemd jobs must execute from the canonical checkout;
  before removing a worktree, retarget and verify every timer/service that
  references it so snapshots and Market Store inputs cannot silently stall.
- The operator estimator runtime is a sidecar of canonical `main`; its model,
  analytics, sessions and SQLite stores live only under
  `/srv/trading-bot/production-data/coin-intelligence/estimator-live`.
- Current private-group Market Store facts may be projected into the estimator's
  compatibility conversation store using opaque identifiers and normalized
  fields; do not revive the retired legacy group parser or its data plane.
- The estimator home dashboard shows only primary-model output plus the exact
  CASH/TOMORROW input snapshot consumed by it: point-before-mean values,
  explicit proxy/estimate/exclusion provenance, and per-rate live/historical
  coin-group anchors. Collector heartbeat, stored activity and actual model
  eligibility/effect are separate; shadow and realised-outcome data remain
  exclusively on `/shadow`.
- Estimator health is input-driven, not process-driven. Every required source
  must expose a collector heartbeat separately from market-hours-aware data
  freshness; stale or invalid inputs stay excluded, and aggregate health must
  degrade with explicit reason codes instead of reporting a bare `RUNNING`.
  Direct normalized sources always win. A live-only fallback must remain a
  separately stored, corroborated and explicitly labelled proxy, degrade health,
  fail closed on disagreement, and never enter historical model training.
