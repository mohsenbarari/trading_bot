# Coin Market Intelligence

- USD/Herat repair is causal at ingest: reconstruct omitted leading digits from
  three strictly-prior same-source/book facts within 15 minutes; never add a
  fixed constant. Replay chronologically.
- In the private group feed, `خ ن ف`/`ف ن ف`, `خ ف`/`ف ف`, and marker-less
  side posts mean tomorrow; `خ ن`/`ف ن` and `نق`/cash words mean cash. Future
  wins. Do not apply this feed default to user registration syntax.
- Coin-group prices may be exact project-thousand values, exact full-Toman
  values divided by 1,000, or a bounded redundant-zero candidate; never add a
  constant. Quantities, scripts and mint years must not corrupt fields. `رب`
  means quarter; `پ`/`ت پ` and `پایین` spelling variants mark low-date.
- Commodity resolution never defaults to Imam or restores
  `group_commodity_context`. Use prior same-book anchors no older than two hours;
  overlapping full-coin bands require coverage for every plausible book. Regime
  bootstrap requires a coherent prior 30-minute explicit cluster: three
  messages, two senders, one nonconditional claim, and 1.5% spread. Conditional
  claims are support-only; eligible facts propagate forward, and only canonical
  evidence may reject an explicit name.
- A trade requires one complete, structurally linked reply branch. Isolate
  users/siblings, choose the oldest ancestral offer, and require reciprocal
  offerer evidence for model eligibility. Cancellation breaks inherited terms;
  a later participant rejection gates the fill. Use the latest negotiated
  quantity/exact normalized price, deduplicate one fill, and link the projected
  trade to its opaque root offer. Ambiguous, overfilled, or unresolved-root
  trades stay out of the model.
- Reconciliation rejects facts invalidated by edits/current reply graph;
  unchanged decisions retain first availability. Projection removes absent or
  ineligible rows, excludes conditional/over-five-minute-late facts, and uses
  `available_at_utc` while retaining source event time as metadata.
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
- Input health separates heartbeat from market-hours freshness, excludes stale
  inputs, prefers normalized sources, and keeps live-only proxies out of training.
