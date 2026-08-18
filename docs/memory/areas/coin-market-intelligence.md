# Coin Market Intelligence

- USD/Herat restores omitted leading digits from 3 same-book/source facts/15m;
  never use constants/future replay.
- `خ ن ف`/`ف ن ف`, `خ ف`/`ف ف`, or no marker mean tomorrow; `خ ن`/`ف ن`
  and `نق` mean cash. Future wins; exclude registration.
- Prices accept project-thousand, one full-Toman /1,000 conversion, or bounded
  extra zeros; reject quantities/scripts/years. `رب` is quarter; `پ`/`ت پ`/
  `پایین`/`بالا 80` mark low-date.
- Named offers need no anchors; decisive evidence may reject. Unnamed use
  unit-safe `MAIN_ONLINE` ranges plus 2h same-book anchors. Contradictions fail;
  overlaps need margin. Bootstrap: 30m/3 messages/2 senders/1 nonconditional/
  1.5% spread. Conditional only supports. Never default Imam/context.
- Trades require isolated reply branch, oldest root and reciprocal offerer;
  cancellation/rejection gates fills. Keep negotiated values and opaque root.
  Only explicit reciprocal first fill may amend quantity; ambiguous/cumulative
  overfills are audit-only.
- Trade feedback hashes root-to-confirmation and cannot rewrite root semantics;
  mismatches are audit-only. With 3 causal same-instrument/5m offers, prices
  beyond max(5%, 6 robust deviations) are audit-only. Prefer same settlement;
  use the other physical settlement only when thin. Gate historical anchors
  before weighting.
- Term structure/residual cannot override <=30m consistent evidence (>=1 trade,
  >=3 offers, or <=1% two-sided book); cap unsupported cash.
- Reconciliation rejects invalid reply graphs; unchanged facts keep first
  availability. Projection removes absent/rejected facts; pending, conditional
  or >5m-late are audit-only. Models use `available_at_utc`; reports source time.
- Reject malformed envelopes; inverted times cannot advance checkpoints.
  Health separates heartbeat, event and eligible input.
- Private text stays in bounded/authenticated review; Store/projection stay
  opaque. Live jobs use `main`; retarget systemd before removal.
- Estimator: `estimator-live`; home CASH/TOMORROW; `/shadow` shadow/realised.
- Web UI shows parser/estimator events, status and recorded values—never recompute.
- Staging imports production names/aliases only via Iran authority and atomically
  relays a fresh read-only Snapshot to both peers. Preview/selection on, auto off;
  omitted names require confirmation.
- Reviews use opaque keys/digests, never raw text/identity. Revisions correct
  facts; redacted-number syntax calibrates grammar. Review anchors never affect
  prior input.
- Index group anchors by book/time; never rescan per message. Fetch Telegram
  deltas newest-first then sort, but backlog oldest-first to preserve checkpoints.
