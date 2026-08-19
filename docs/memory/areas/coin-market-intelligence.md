# Coin Market Intelligence

- USD/Herat leading digits require 3 same-book/source facts/15m;
  no constants/future replay.
- `خ ن ف`/`ف ن ف`, `خ ف`/`ف ف`, or no marker mean tomorrow; `خ ن`/`ف ن`
  and `نق` mean cash. Future wins; exclude registration.
- Prices accept project-thousand, full-Toman /1,000 or bounded zeros;
  reject quantities/scripts/years. `رب` is quarter; `پ`/`ت پ`/
  `پایین`/`بالا 80` mark low-date.
- Named offers need no anchors; decisive evidence may reject. Unnamed use
  unit-safe `MAIN_ONLINE` ranges plus 2h same-book anchors; contradictions fail
  and overlaps need margin. Bootstrap needs 3 messages/2 senders/30m/1.5%
  spread and one nonconditional fact. No Imam/context default.
- Trades require isolated replies, oldest root and reciprocal offerer;
  cancellation/rejection gates fills. Only explicit reciprocal first fill
  amends quantity; ambiguous/cumulative overfills stay audit-only.
- Trade feedback hashes root-to-confirmation; mismatches cannot rewrite roots
  and stay audit-only. With 3 causal same-instrument/5m offers, prices
  beyond max(5%, 6 robust deviations) are audit-only. Prefer same settlement;
  use other physical settlement only when thin. Gate historical anchors
  before weighting.
- Term structure cannot override <=30m evidence (trade, 3 offers, or <=1%
  two-sided book); cap unsupported cash.
- Reconciliation rejects invalid reply graphs; unchanged facts keep first
  availability. Projection drops absent/rejected facts; pending/conditional/
  >5m-late stay audit-only. Models use `available_at_utc`; reports source time.
- Reject malformed envelopes; inverted times cannot advance checkpoints.
  Health separates heartbeat/event/eligible input.
- Private text stays in authenticated bounded review; Store/projection
  opaque. Live jobs use `main`; retarget systemd before removal.
- Estimator: `estimator-live`; home CASH/TOMORROW; `/shadow` shadow/realised.
- Web UI shows recorded parser/estimator events/status/values; never recompute.
- Staging catalog mirrors Iran; Snapshot reads only. v3 uses exact bands, else
  confirms one nearest same-family center within ±10%; ties require choice.
  Edits reuse that Snapshot/scope, omit model receipt, and never auto-select.
  Bot edits enter choice state before buttons; stale taps answer.
- Staging bridge/collectors share writer `flock`; ordering bridge `After=`
  recurring collectors starves it and stales Snapshots.
- Reviews use opaque digests, never raw text/identity. Revisions correct facts;
  redacted numbers calibrate grammar. Review anchors never affect prior input.
- Index anchors by book/time; never rescan per message. Fetch deltas newest-first
  then sort; oldest-first backlog preserves checkpoints.
