# Coin Market Intelligence

- USD/Herat repairs infer omitted leading digits from three prior same-book,
  same-source facts within 15 minutes; never add constants or replay out of order.
- In the private group feed, `خ ن ف`/`ف ن ف`, `خ ف`/`ف ف`, and marker-less
  side posts mean tomorrow; `خ ن`/`ف ن` and `نق`/cash words mean cash. Future
  wins. Do not apply this feed default to user registration syntax.
- Coin-group prices accept project-thousand, full-Toman divided once by 1,000,
  or bounded redundant-zero forms; never constants. Quantities, scripts and mint
  years cannot become prices. `رب` means quarter; `پ`/`ت پ`/`پایین` mark low-date.
- Commodity resolution never defaults to Imam or restores
  `group_commodity_context`. Prior same-book anchors expire after two hours;
  overlapping full-coin bands need every plausible book covered. Bootstrap needs
  a 30-minute cluster: three messages, two senders, one nonconditional claim,
  1.5% spread. Conditional claims only support; only authoritative evidence may
  reject an explicit name.
- Trades require one complete linked reply branch, isolated users/siblings, the
  oldest root, and reciprocal offerer evidence. Cancellation breaks inherited
  terms; later rejection gates the fill. Keep final price/quantity, deduplicate,
  and link the opaque root. Ambiguous/overfilled/unresolved trades stay audit-only.
- Reconciliation rejects edit/reply-graph-invalid facts; unchanged decisions keep
  first availability. Projection removes absent/rejected facts but retains
  pending, conditional, or over-five-minute-late facts audit-only. Models use
  `available_at_utc`; reports retain source time.
- Reject malformed envelopes individually; inverted timestamps cannot advance
  checkpoints. Health separates heartbeat, canonical event, and eligible input.
- Private group text stays only in bounded external staging. Market Store and
  projection keep opaque normalized facts; never revive the legacy data plane.
  Live jobs use canonical `main`; retarget systemd before worktree removal.
- Estimator runtime state lives under
  `/srv/trading-bot/production-data/coin-intelligence/estimator-live`. Home shows
  primary CASH/TOMORROW inputs; shadow/realised detail stays on `/shadow`.
- The web UI is the parser/estimator observable contract. Its ledger shows every
  normalized offer/trade, separates input from audit-only facts, and joins
  eligible facts to recorded main/shadow prices and cycle times—never recomputation.
- Field reviews use a structured sidecar with opaque keys
  and reviewer digests, never raw text/identity. Each revision corrects/rejects
  the exact fact next cycle. A number-redacted syntax digest calibrates matching
  later grammar; price anchors start at review and never affect past live input.
- Input health separates heartbeat from market-hours freshness, rejects stale
  input, prefers normalized sources, and excludes live-only proxies from training.
