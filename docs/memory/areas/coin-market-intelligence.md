# Coin Market Intelligence

- USD/Herat abbreviation repair is ingest-time and causal: reconstruct omitted
  leading digits from at least three strictly-prior same-source/book facts in a
  bounded 15-minute range; never add a fixed constant. Replay chronologically.
- Coin-group settlement supports both generations: old `خ ن ف`/`ف ن ف` and
  current `خ ف`/`ف ف` mean tomorrow; old `خ ن`/`ف ن`, current single `خ`/`ف`,
  and standalone `نق` mean cash. Explicit future delivery wins over `ن`.
- Coin-group number parsing is contextual: attached comma/dot/slash thousands,
  glued/bare/word quantities, Persian/Arabic letters and mint-year metadata must
  not corrupt price or quantity. Payment timing/account terms are conditional.
- Commodity resolution never defaults an omitted coin to Imam and never restores
  `group_commodity_context`. Use strictly-prior same-book anchors no older than
  two hours. A new regime may bootstrap only from a coherent prior 30-minute
  explicit cluster with at least three messages, two senders and 1.5% spread.
  Group-derived consensus may resolve an unnamed offer or validate a matching
  name, but only independent canonical evidence may reject an explicit name.
- A trade requires one structurally linked reply branch. Keep users/siblings
  isolated, choose the oldest offer on that ancestry, prefer attributable owner
  confirmation, use the latest negotiated quantity/price, deduplicate one fill,
  and keep ambiguous/overfilled facts out of model eligibility. Detect confirmed
  trades even when the root commodity is unresolved, but retain them only as
  pending audit facts until the root is eligible.
- Reconciliation rejects derived offer/trade facts invalidated by edits or the
  current reply graph. Idempotent unchanged decisions retain first availability.
  Estimator projection is authoritative: remove missing/ineligible prior rows,
  exclude conditional or over-five-minute-late facts, and use `available_at_utc`
  as the compatibility timeline while retaining source event time as metadata.
- Reject malformed group envelopes individually; inverted timestamps must not
  poison siblings or freeze checkpoints. Health separates heartbeat, latest
  canonical event, and actual model-eligible input; historical rows are not live.
- Private group text stays only in bounded external staging. Market Store and
  estimator projection keep opaque normalized facts; never revive the legacy
  parser/data plane. Live jobs execute from canonical `main`; verify and retarget
  every systemd reference before removing a worktree.
- Estimator runtime state lives under
  `/srv/trading-bot/production-data/coin-intelligence/estimator-live`. The home
  page shows only primary output and its exact CASH/TOMORROW inputs; shadow and
  realised outcomes stay on `/shadow`.
- Input health is data-driven: expose heartbeat separately from market-hours-aware
  freshness, exclude stale/invalid inputs, prefer direct normalized sources, and
  label any corroborated live-only proxy as degraded and never training-eligible.
