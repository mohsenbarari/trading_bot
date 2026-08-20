# Coin Offer Condition Classifier — Research Baseline

Status: `RESEARCH_ONLY_NOT_PROMOTED`

This work starts the condition-aware layer needed before any dynamic fair-price tolerance can be designed. It does not change the live estimator, parser, offer acceptance, tolerance, staging, or production behavior.

## Architecture

The classifier is hierarchical, not a single flat label:

1. A Persian encoder separates condition spans from the offer core.
2. Independent heads assign one or more condition families.
3. Settlement (`CASH` / `TOMORROW`), trade form, Tehran market-session phase, and payment-deadline horizon remain deterministic point-in-time axes.
4. A composite research class is built from those axes.

This means identical wording such as `فیش تا 2` does not collapse cash and tomorrow offers, or opening-hour and midday offers, into the same economic class.

Regex is retained only for high-precision weak labels and deterministic clock interpretation. It is not the intended final language decision layer.

## Models compared

- Character-hash logistic baseline: privacy-safe and fast, but payment-deadline precision was only `0.597` on the temporal holdout.
- Frozen Persian DistilBERT plus trained span/family heads: selected neural baseline.
- Persian ALBERT INT8: rejected for runtime work because its measured single-offer CPU latency was substantially slower.

The selected encoder is pinned to `HooshvareLab/distilbert-fa-zwnj-base` revision `e8b934b8c81b17c5e4a1a90325f5f25ced94e8d6`. Encoder fine-tuning is deliberately deferred until owner-reviewed labels exist; the current run trains only downstream heads.

## Current-data run

Read-only Group 1/2 data was deduplicated once per group/book/day. Raw text, Telegram identities, message IDs, and reversible vocabulary were not retained in artifacts or reports.

| Measure | Result |
| --- | ---: |
| Unique offers | 4,851 |
| Group 1 / Group 2 | 1,794 / 3,057 |
| Cash / tomorrow | 1,493 / 3,358 |
| Explicit-condition / no-explicit-condition | 515 / 4,336 |
| Session phases represented | 6 |
| Composite research classes | 75 |

Temporal holdout results against weak labels:

| Task | Precision | Recall | F1 |
| --- | ---: | ---: | ---: |
| Condition-span tokens | 0.945 | 0.913 | 0.929 |
| Exact row boundary | 0.969 | — | — |
| Any explicit condition | 0.966 | 0.935 | 0.950 |
| Payment deadline | 0.967 | 0.763 | 0.853 |

All six trainable labels cleared the research precision gate in the temporal split. Four sparse families remain rule-only because current support is below the training floor. Cross-group span F1 was `0.907` from Group 1 to Group 2 and `0.930` in the reverse direction. Family macro F1 was `0.887` and `0.945` respectively; not every family cleared the precision gate in cross-group transfer.

## CPU result

Single-offer PyTorch inference on the current 8-core host measured approximately:

| Encoder runtime | p50 | p95 |
| --- | ---: | ---: |
| Persian DistilBERT FP32 | 450 ms | 516 ms |
| Persian DistilBERT dynamic INT8 | 342 ms | 453 ms |
| Persian ALBERT dynamic INT8 | 1,082 ms | 1,304 ms |

The neural model is therefore suitable for offline research and possibly an asynchronous shadow path, but is not yet approved for synchronous offer handling. ONNX Runtime benchmarking and concurrency/load testing are still required before any runtime proposal.

## Evidence boundary

These scores measure agreement with deterministic weak labels, not owner-reviewed truth. They show that semantic embeddings improve important categories such as payment deadlines; they do not prove production accuracy or justify dynamic tolerance.

The neural probe artifact contains only fitted head coefficients, thresholds, pinned encoder metadata, aggregate metadata, and a source fingerprint. Encoder weights are not embedded. External evidence digests:

- report: `283582ed47202f36cf5a06bb552cb85e4469459981d3901da8651a804eb79d91`
- trained-head artifact: `de5e79caedd6ac1ca15eba72a184ce719fdc54fde6d5247aa3cd33e553d5a08f`

## Required next gate

1. Build an authenticated, non-exporting review flow for stratified samples from both groups, both settlements, all session phases, and rare families.
2. Human-label condition boundaries, multi-label families, deadline interpretation, and ambiguous/no-condition cases against a written guide.
3. Fine-tune and compare the Persian encoder on a sealed temporal holdout and cross-group holdouts.
4. Calibrate probabilities and define abstention. Low-confidence or unseen families must not affect tolerance.
5. Benchmark an ONNX INT8 build under realistic concurrent load.
6. Only then study outcome-conditioned tolerance offline with point-in-time market features; future trade outcomes must never become input features.

Promotion requires a separate explicit decision, a versioned self-contained artifact, rollback, shadow comparison, and staging evidence.
