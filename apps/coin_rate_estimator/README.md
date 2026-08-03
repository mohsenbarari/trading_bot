# Coin rate estimator operator application

## Status

This source was migrated from the former external runtime into
`candidate/coin-price-intelligence` for review and version control.  It is an
**operator-facing, non-authoritative application**.  Its runtime model,
SQLite databases, Telegram session, credentials, generated state, logs and
manual-entry token are intentionally excluded from Git.

It is not yet wired into the production WebApp, bot, scheduler, deployment or
three-site data plane.  The current product priority remains the integrated
coin-estimation Shadow work under `core/market_intelligence`; this application
is preserved here so that its page and its supporting estimator source do not
remain an unversioned server-only dependency.

The application contains:

- `coin_estimator.py`: robust hybrid training and one-minute inference
- `live_server.py`: read-only Telegram live listener and HTTP page
- `test_estimator.py`: offline unit tests
- `runtime/`: intentionally ignored local model, data, state and operator
  token directory
- `telegram_price_collector/`: legacy collector implementation required by the
  standalone page's existing normalized SQLite schema

## Runtime configuration

The process reads all mutable paths from environment variables.  A deployed
instance must point them at a protected runtime volume, never this source tree:

```bash
export COIN_RATE_ESTIMATOR_RUNTIME_DIR=/var/lib/trading-bot/coin-rate-estimator
export COIN_RATE_ESTIMATOR_MODEL="$COIN_RATE_ESTIMATOR_RUNTIME_DIR/model.json"
export COIN_RATE_ESTIMATOR_MARKET_DB="$COIN_RATE_ESTIMATOR_RUNTIME_DIR/market_prices.sqlite3"
export COIN_CONVERSATION_DB=/var/lib/trading-bot/coin-intelligence/conversation_events.sqlite3
export TELEGRAM_API_ID='...'
export TELEGRAM_API_HASH='...'
export TELEGRAM_PHONE='...'
```

No credential, session, phone number, public random route, model artifact or
database is committed with this application.

## Temporary manual offer / trade entry

The web page also contains a temporary structured operator form for entering
coin offers and confirmed trades while direct project-offer ingestion is not
available. It accepts the raw group-offer text and produces editable
suggestions for commodity, today/tomorrow settlement, physical/paper form,
buy/sell side, project-unit price and quantity. The parser combines
deterministic market grammar with robust price profiles and token evidence
learned from the operator-reviewed rows in `manual_coin_offers`; it refreshes
when that database changes.

A Tehran clock can be included in the text, preferably as `15:31`. The parser
uses the last offer in operator-entry order as its date anchor, keeps small
out-of-order clock movements on the same date, and advances the date on a
credible overnight/market-session rollover. It unchecks the live-offer box and
fills the editable Tehran datetime field. The inferred date is never hidden:
low-confidence or non-monotonic cases are shown beside the parser result and
must be reviewed. Skipped calendar days cannot be proven from a clock alone,
so the field remains editable. The operator-only clock is removed before
`raw_offer_text` is stored; only the structured occurrence timestamp retains
it.

Buy/sell is populated only when the text contains an explicit side marker or
word. A text-only classifier is not allowed to guess this field because the
audited corpus contains offers whose side can be known only from surrounding
market context. The form therefore has no default side and cannot be submitted
until the operator selects one. The submit button is also disabled after the
first valid submission to prevent browser double-click duplicates.

On first start, the service creates these two tables inside the existing
conversation database, without modifying imported Telegram messages:

- `manual_coin_offers`
- `manual_coin_confirmed_trades`

The latter has the higher training weight and replaces its source offer in the
live five-minute anchor, so an offer and its confirmed trade are never counted
as two independent live references.  Historical manual offers are retained as
historical labels; live entries expire from the live range after five minutes.
The page also lists open manually entered offers in a second small form, so a
later partial or full trade can be attached to the original offer rather than
entering the offer a second time.

Each service start still has an internal write token. Unless
`COIN_ESTIMATOR_WRITE_TOKEN` is set, the token is generated once at
`runtime/manual-entry.token` with mode `0600`. The temporary operator page
does not ask the user to enter or expose this token. A successful form
submission immediately re-runs the estimator, then returns to the page with
the current cash and tomorrow estimates beside the form.

The estimator is not an LLM. It uses the supplied intrinsic-value formulas,
robust weighted bubble calibration from extracted Telegram group offers and
reply-linked confirmed Telegram trades, and a strongly-shrunk
melted-vs-dollar/ounce feature. The experimental project database is excluded
by default. A project completed trade has weight 2.0, a reply-confirmed group
trade 1.5, and a timed-out offer 1/3. When trades exist, the entire offer class
is capped at 40% of effective training weight, so a large number of offers
cannot collectively outvote completed transactions. Missing live minute inputs
stay missing and are rendered as `<NO_DATA_THIS_MINUTE>`.

Offers have separate live and historical lifecycles. For the first five
minutes an active offer can contribute to the observed range. An explicitly
cancelled or early-expired offer receives half weight; after five minutes it
has zero live-range weight and can only contribute a decaying/historical bubble
label. A confirmed trade supersedes its source offer so it is never counted
twice.

The live coin reference is hierarchical: the most recent quality-approved
confirmed trade, then the midpoint/range of an active two-sided book, then a
confidence-weighted offer median. A latest offer is retained for audit but is
never used directly as the estimated price. All live references have a hard
five-minute TTL and are never forward-filled.

Out-of-book offers are quality-gated before either inference or training. In a
normal/range market, a sell below the lowest active buy or a buy above the
highest active sell is excluded, and a reply-linked transaction derived from
that offer is excluded too. A sell merely below the highest/best bid, or a buy
merely above the lowest/best ask, is not excluded. In a directional market an
out-of-book offer survives only when the direction agrees with
an independently detected, sufficiently confident move in melted gold,
dollar/USDT, ounce, or IME gold. Coin offers are not used to declare the market
regime.

Public external features are read from the same compact market database.
Wallex `USDT/IRT` is an explicitly marked proxy only when a matching Herat USD
observation is absent; the Herat cash/paper classification is never overwritten.
The normalized IME `IRT_PER_MESGHAL_750` quote can be a lower-weight intrinsic
fallback, and the IME Imam certificate can be a current Imam anchor when the
Telegram coin quote is absent. Every fallback obeys the same trailing-window
rule: stale API data becomes `<NO_DATA_THIS_MINUTE>` and is never forward-filled.
The IME coin certificate is a cash anchor only: it may directly anchor `CASH`
Imam, but it never directly replaces a `TOMORROW` coin quote. Tomorrow may use
the same-minute exchange gold certificate as an explicitly named underlying
and applies its independently trained tomorrow bubble.

Settlement (`TODAY`/`TOMORROW`) and trade form (`PHYSICAL`/`PAPER`) are stored
as independent axes. Transfer (`حواله`), unofficial (`غیررسمی`), and explicit
paper quotes are classified as paper; they are never silently relabelled as
tomorrow. The physical project estimator can use a same-minute paper melted
quote only as an explicitly named reference fallback when no physical melted
quote is available.

Dollar and melted-gold physical classification follows an explicit-cash rule:
only dollar text containing `نقدی`/`نقد`, and only melted-gold text containing
`نقدی`/`رسمی`, is physical. Herat today/tomorrow and all other melted-gold
subtypes are paper. The live output exposes the selected market label and trade
form so a paper reference cannot look like a physical observation.

The ten-minute order-flow feature is calculated independently per settlement,
trade form, and instrument. It combines recency-weighted buy/sell imbalance,
offer-to-trade share, and the latest consecutive offer streak. A learned,
non-negative and shrunk coefficient may move the bubble estimate. Even when
there is not enough evidence to move the point estimate, positive pressure
expands the upper tolerance and negative pressure expands the lower tolerance.
Paper flow has its own configurable lower weight and remains visible in the
JSON output.

Market regime (`RANGE`, `UP`, `DOWN`, `SHOCK`, or `UNKNOWN`) is detected from
underlyings rather than coin offers. USDT has greater nominal weight than IME
when both are the only usable references. Regime and order flow expand the
appropriate side of the reported range; movement of the point estimate remains
learned and validation-gated.

Cash melted-gold input prefers `آبشده نقدی` and falls back to
`آبشده امروزی` only when it is observed in the same minute. It never carries a
previous minute forward. Historical dollar context uses a trailing observed
ten-minute window because dollar messages are less frequent; live output keeps
the original one-minute missing-value rule. Tomorrow melted input first looks
for a physical tomorrow/today underlying, then may use `آبشده حواله` or
`آبشده غیررسمی` as an explicit paper-reference fallback.

Run tests:

```bash
cd apps/coin_rate_estimator
python3 -m unittest -v test_estimator.py test_offer_text_parser.py
python3 evaluate_offer_text_parser.py
```

Retrain without reading the project database:

```bash
cd apps/coin_rate_estimator
python3 coin_estimator.py train --project-labels disabled
```

Training reads paths supplied through the runtime environment. A chronological
conformal calibration supplies a minimum tolerance floor only after separate
test coverage is recorded in the model artifact.

Start the configured live service:

```bash
export COIN_RATE_ESTIMATOR_PORT=30486
export COIN_RATE_ESTIMATOR_PATH='choose-a-new-operator-route'
./run_live.sh
```

Direct IME polling is disabled in the normal foreign-host deployment path.
Any future Iran relay and Object Storage transfer must follow the repository's
three-site policy and be implemented as a separate reviewed change.  This
application must fail closed when an exchange observation is absent; it must
not manufacture a current value.
