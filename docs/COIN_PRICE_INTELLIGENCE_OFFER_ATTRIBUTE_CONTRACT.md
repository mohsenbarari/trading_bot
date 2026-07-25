# Coin Price Intelligence — Offer Attribute Contract

## Purpose and scope

This contract defines structured attributes that belong to an offer in
addition to its canonical commodity, price, quantity, side, settlement, and
trade form. It is introduced for Shadow/research use only; it does not change
the current parser, default-`امام` rule, database schema, or user-facing offer
flow.

The first supported attribute is the coin mint year. A mint year is not a new
commodity. For example, `ربع ۱۴۰۳` and `ربع ۱۴۰۴` both retain the canonical
commodity `ربع بهار`.

## Attribute envelope

Every extracted attribute is represented independently of the canonical
commodity:

| Field | Type | Meaning |
| --- | --- | --- |
| `name` | string | Stable attribute key, initially `mint_year`. |
| `value` | string or null | Normalized value, initially `1403` or `1404`. |
| `status` | enum | `EXPLICIT`, `INFERRED`, or `UNSPECIFIED`. |
| `extraction_method` | enum | `RULE`, `LLM`, or `HUMAN_REVIEW`. |
| `confidence` | number 0–1 | Confidence in the extracted value, not price confidence. |
| `evidence_span` | bounded metadata | Character/field evidence needed for audit; it must not become a model feature containing raw text. |

`UNSPECIFIED` means the offer text did not state the attribute. It never means
the canonical/non-year variant, and no value may be invented to fill it.

An `INFERRED` value is a candidate only. It cannot alter a persisted offer,
become a training label, or be presented as a fact without user confirmation
or a later deterministic/human validation path.

## Mint-year extraction policy

1. Normalize Persian and Arabic digits before parsing.
2. Apply deterministic, contextual rules first for explicit forms such as
   `۱۴۰۳`, `۴۰۳`, `۱۴۰۴`, and `۴۰۴` when they are attached to a coin phrase.
3. Reject ambiguous numeric tokens: a price, quantity, or message containing
   multiple incompatible years must not be silently classified.
4. A local LLM may be used only as a constrained fallback for ambiguous text.
   It must return the attribute envelope, evidence reason, and confidence.
5. LLM output is never itself an authoritative historical label. It must pass
   deterministic validation or enter the human-review queue.
6. The canonical commodity remains the exact name in `commodities.name`.
   `commodity_aliases` remains input-only and does not encode mint year as a
   substitute for a canonical commodity.

## Estimator policy

The numerical estimator receives the canonical commodity and attributes as
separate inputs.

- Explicit-year observations are evaluated in their own conditional cohort;
  they must not be averaged into the central price band for `UNSPECIFIED`
  offers.
- A year-specific observation may later become an auxiliary directional or
  range-tail signal only after a strictly-prior validation proves benefit.
  It must not override a fresh canonical coin anchor.
- `UNSPECIFIED` is a distinct cohort, not a proxy label for a non-year coin.
- If the requested output lacks a stated year, the model may return the
  canonical commodity only. It must not fabricate a year from price.
- If a user explicitly writes a year, the standardized preview preserves it
  in the description/attribute output while retaining the same canonical
  `commodity_id`.

## Evidence from the current research cohort

The 2026-07-24 offline audit found material conditional price differences in
the Telegram group data. Relative to nearby same-commodity observations with
no explicit year, median differences were approximately:

| Canonical commodity | Explicit year | Median difference |
| --- | ---: | ---: |
| `ربع بهار` | 1403 | -4.77% |
| `ربع بهار` | 1404 | -3.00% |
| `نیم بهار` | 1404 | -1.56% |
| `امام` | 1403 / 1404 | below 1% |

This confirms that mint year is a material conditional feature, especially
for quarter coins, while confirming that it must not create a separate
commodity or PostgreSQL identity.

Two point-estimation alternatives were evaluated and rejected in Shadow:

- excluding all explicit-year rows improved online error but degraded the
  frozen-anchor/reopening scenario because it discarded useful market-flow
  evidence;
- converting explicit-year rows into adjusted point anchors did not improve
  both online and frozen-anchor scenarios, and increased interval width.

The active model therefore remains unchanged. A wider 30-minute
strictly-prior discontinuity candidate was positive in research but also
remains unpromoted until a genuinely later holdout confirms it.

## Future implementation gates

Before any runtime or user-facing change:

1. Create a versioned attribute schema and synthetic fixtures covering Persian
   digits, Arabic digits, shorthand years, prices, quantities, and ambiguity.
2. Build deterministic extraction and an explicit review path before enabling
   an LLM fallback.
3. Store the attribute separately from `commodity_id`; retain source text only
   in the protected extraction store, never in a model bundle or public
   snapshot.
4. Evaluate `EXPLICIT`, `INFERRED`, and `UNSPECIFIED` cohorts on global
   chronological folds with no timestamp overlap.
5. Require improvement on a future untouched cohort, 95% audit-envelope
   coverage, and a useful operational-band width before promotion.
6. Keep the feature Shadow-only until an owner explicitly approves a
   user-facing confirmation flow.
