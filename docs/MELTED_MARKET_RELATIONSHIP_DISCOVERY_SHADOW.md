# Melted Market Relationship Discovery — Shadow Contract

## Purpose

This is a research layer for discovering relationships, not a replacement
price formula.  It has two independent jobs:

1. model the behaviour of each melted-gold segment (paper normal, reverse,
   swim; today/tomorrow; physical; conditional); and
2. test whether information available in those segments before a cutoff helps
   explain a *later* movement in another melted segment or a coin reference.

The coin estimator must eventually consume only evidence that survives this
research process.  It must not merely copy the most recent coin quote, and a
high-volume melted-paper signal must not reduce the direct evidential weight of
a fresh, confirmed coin trade.

Every result is `SHADOW_RESEARCH_NOT_PROMOTED`.  It cannot change a runtime
price, range, parser result, source weight, or activation flag.

## Market contract

The source contract preserves rather than averages away these dimensions:

| Dimension | Values currently kept separate |
| --- | --- |
| paper variant | normal, reverse, swim, generic/unknown |
| settlement | today, tomorrow, unknown |
| delivery form | paper, physical |
| condition | conditional versus non-conditional |
| event kind | offer versus confirmed trade |
| direction | buy, sell, unknown |
| size | confirmed quantity when present |

Conditional rows are excluded by default.  They remain useful historical
evidence, but cannot silently teach an unconditional price relation.  The
explicit `--include-conditional` experiment is separate and must be reported
as such.

The current input adapter also emits strictly source-specific support features
for melted reference quotes, paper/physical Herat, XAUUSD, union gold and AED.
A missing source is absent from the row; it is never replaced by another
source.  In particular, USDT must not be substituted for Herat.  When a
durable USDT/IME history is available under the normalized contract, it must be
added as its own source key with the same timestamp and missingness rules.

The present public coin input is labelled `COIN_REFERENCE:*`: it is an external
generic cash/paper series only.  It is not an Imam/half/quarter/one-gram offer
and cannot prove a product-specific relationship.  A later adapter must use
canonical, independently confirmed project coin trades as the target for each
commodity and settlement.

The current confirmed-trade adapter already builds that product-specific
research label.  It applies the explicit methqal multiples (`2.253`, half,
quarter and one-gram divisions) to a strictly prior, non-conditional melted
anchor after converting its rial observation to the project toman unit.  Its
label is the observed coin bubble relative to that intrinsic value, alongside
the anchor kind and age.  This makes the learning target interpretable: a
future challenger learns the residual/bubble, not the entire coin price from
scratch.  Cash first seeks a current physical anchor and only then follows the
explicit current-paper fallback; tomorrow seeks its own normal paper anchor
first.  A same-timestamp or future melted row is forbidden.

## Point-in-time feature graph

For each Tehran-minute cutoff and each source segment, the feature builder
creates independent 1, 3, 5, 10 and 15 minute windows.  Each window includes:

- offer count, confirmed-trade count and trade share;
- buy/sell offer and trade imbalance;
- confirmed trade quantity;
- sequence length and direction for all order flow and for trades alone;
- within-window price change and source staleness;
- directional spreads between same-unit melted segments; and
- Tehran clock features, weekday, and Jalali year/month/day derived from the
  same cutoff.

Support feeds contribute only their own count, return and staleness.  Raw price
levels from incompatible units are not mixed into a synthetic spread.

The discovery target is a future price return at a configurable horizon
(initially five minutes).  Its future observation must be strictly later than
the feature cutoff.  The current target anchor and realization both have a
bounded freshness limit.  Thus a late delivery timestamp or a future trade
cannot leak into a historical feature row.

## Evaluation and output

`scripts/discover_melted_market_relationships_shadow.py` requires
`--acknowledge-shadow-only`; it reads the normalized SQLite input read-only and
requires report/dataset paths outside the repository.  Its optional JSONL
dataset holds aggregate numeric features, cutoff/realization timestamps and
future return only—never raw messages, post IDs, channel names, participant
names or account identifiers.

The report evaluates every candidate in chronological fit/validation/test
partitions (60/20/20 by feature availability time).  A candidate is only marked
cross-split stable when each partition clears all of the following research
filters:

- enough samples in that partition;
- a configurable minimum absolute correlation;
- a configurable minimum directional agreement; and
- the same non-zero correlation sign in all three partitions.

This is a research triage label, not an approval.  It deliberately removes
near-zero correlations that merely share a sign by chance.

## Required next evidence before any model use

1. Run multi-origin walk-forward evaluation across enough distinct Tehran days,
   including openings after overnight and holiday gaps.
2. Compare a relationship-informed candidate against the current structural
   estimator under both ordinary online anchors and frozen coin anchors.
3. Slice error, range coverage and false direction by market regime,
   settlement, physical/paper condition, Tehran time, weekday and Jalali
   calendar cohorts.
4. Require independent evidence for each product/settlement before a discovered
   relation can inform Imam, half, quarter, one-gram or low-date logic.
5. Keep confirmed coin trades as the strongest local evidence.  Use melted
   relations primarily for sparse/closed/reopening periods, and only after the
   preceding gates and a versioned human approval.

No automatic promotion, online self-training, or automatic coefficient update
is allowed in this subsystem.

## Non-linear bubble challenger

`scripts/train_coin_bubble_relationship_challenger_shadow.py` is the next,
still-disabled research layer.  It can evaluate an optional CatBoost model only
after the intrinsic-label dataset has enough chronological evidence.  The
candidate learns the bubble residual, never the full coin price.  Its numeric
input is the aggregate feature graph only; it does not load raw messages or
live application data.

Before the optional dependency is even imported, it requires at least 250 fit
rows, 60 validation rows, 60 untouched test rows, seven distinct Tehran-market
fit days and at least one commodity/settlement/form cell with 30 fit rows.  A
15-minute purge band separates each chronological 60/20/20 split.  It reports
a fitted-market median-bubble baseline on the untouched test cohort.  Failure
of any gate is a successful safe abstention, not a reason to lower the gate.

CatBoost remains optional in `requirements-market-intelligence.txt`; it is not
part of the application image and its execution requires the explicit
`--execute-catboost` switch even after the data gates pass.  A completed
experiment writes a report only.  It neither saves an active runtime model nor
alters a source weight or range.

## Durable collection and readiness cycle

`run_coin_relationship_shadow_cycle.py` runs the bounded research sequence
under a non-blocking local lock: regenerate the current privacy-minimized coin
labels, upsert them to the durable ledger, compact only labels older than the
configured retention period (initially 180 days), then evaluate the challenger
from that ledger.  It is an explicit operator command, not an application job
or an active-inference dependency.

The ledger stores only timestamps, canonical commodity/settlement/form,
strictly-prior melted-anchor metadata, numeric intrinsic/actual/bubble values
and numeric feature maps.  It rejects raw offer text, message IDs, sender or
counterparty fields.  An economic label key is a one-way digest of its market
time/dimensions and anchor; parser corrections update the existing aggregate
label instead of silently accumulating two contradictory versions.

The same ledger keeps the independent melted-market feature/target rows.  They
retain the future return only after it is observed, with an aggregate
point-in-time feature map for every target segment.  This is what will give the
future melted challenger enough chronological history to discover interactions
between normal/reverse/swim, today/tomorrow, flow, support markets and calendar
state without reconstructing raw Telegram traffic.

`train_melted_relationship_challenger_shadow.py` is the corresponding optional
non-linear challenger.  It predicts the later return of a melted target
segment, never an active coin quote.  Before CatBoost may even be imported, it
requires 1,500 fit rows, 350 validation rows, 350 untouched test rows, seven
distinct fit days and a target segment with at least 300 fit rows.  The split
is chronological with a 15-minute purge band.  Its baseline is the prior
median return for that exact target segment.  This keeps an abundant but short
three-day sequence from being mistaken for reliable market knowledge.
