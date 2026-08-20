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

- Character-hash logistic baseline: privacy-safe and fast, but weak on payment-deadline transfer.
- Frozen Persian DistilBERT plus trained span/family heads: selected neural research baseline.

The selected encoder is pinned to `HooshvareLab/distilbert-fa-zwnj-base` revision `e8b934b8c81b17c5e4a1a90325f5f25ced94e8d6`. Encoder fine-tuning is deliberately deferred until owner-reviewed labels exist; the current run trains only downstream heads.

The research environment is reproducible from `apps/coin_rate_estimator/requirements-condition-research.txt`. It is intentionally separate from production dependencies.

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

Rows are ordered by event time and split once into `3,395` training, `727` calibration, and `729` sealed evaluation rows. Models fit only the training partition. Thresholds are selected only on calibration, then locked before evaluation. In cross-group checks, thresholds are calibrated only on the source group; the target group is never used for fitting or threshold selection.

Sealed temporal results against weak labels:

| Task | Character baseline P / R / F1 | Frozen DistilBERT P / R / F1 |
| --- | ---: | ---: |
| Condition-span tokens | 0.865 / 0.951 / 0.906 | 0.891 / 0.940 / 0.914 |
| Exact row boundary | 0.956 | 0.968 |
| Any explicit condition | 0.941 / 0.736 / 0.826 | 0.952 / 0.908 / 0.929 |
| Payment deadline | 0.440 / 0.440 / 0.440 | 0.786 / 0.440 / 0.564 |

Five of six trainable neural heads nominally clear the `0.90` weak-label precision threshold. That is not a promotion gate: three of those heads have only `4`, `9`, or `10` positive evaluation examples. `PAYMENT_DEADLINE` is the important blocked head (`0.786` precision), and four sparse families remain untrained. Human ground truth and substantially larger rare-family support are required.

With source-only calibration, neural cross-group span F1 is `0.878` from Group 1 to Group 2 and `0.929` in reverse. Family macro F1 is `0.807` and `0.896`; only `1/4` and `4/5` trained heads respectively clear the nominal precision threshold. The direction gap is evidence of group drift, not a reason to merge both groups blindly.

## CPU result

Single-offer PyTorch FP32 inference on the current 8-core host measured:

| Encoder runtime | p50 | p95 |
| --- | ---: | ---: |
| Persian DistilBERT FP32 | 437 ms | 511 ms |

The model is suitable for offline research only. It is not approved for synchronous offer handling or a shadow path. Quantized/ONNX benchmarking and concurrency/load testing must be rerun in a pinned environment after the label-quality gate.

## Evidence boundary

These scores measure agreement with deterministic weak labels, not owner-reviewed truth. They show that semantic embeddings improve important categories such as payment deadlines; they do not prove production accuracy or justify dynamic tolerance. Models stored in the artifacts are the exact training-partition models used for the reported evaluation; they are not silently refit on the sealed evaluation rows.

The artifacts contain only fitted head coefficients, thresholds, pinned metadata, implementation hashes, dependency versions, aggregate metadata, and a source fingerprint. Encoder weights are not embedded. External evidence digests:

- character report: `96a2565780bb52d2f578522473982893bca462afb5e69d7d03de2726681ea4f2`
- character artifact: `245db7b3847bcb57cacecfa012acf86de9992831a8c8ab1e5475e415faada26b`
- privacy-safe review queue: `6f1168e3b6e04d060683c9ab38539a1f2255f902c209d9a6391b3228ee86437b`
- neural report: `1f3c2d16de6a277bad82e5dfd0e14897687d4ffafde5a7b9c711db9c736b8438`
- neural trained-head artifact: `28c808db9ca66f5d425c7fa7db4924b75faddf97f5925c2e75b73537612dfb26`

The earlier neural report `283582ed…` and artifact `de5e79ca…` are superseded and non-promotable because threshold selection and reporting used the same holdout, and cross-group thresholds were selected on the target group. Their scores must not be cited as current evidence.

## Required next gate

1. Build an authenticated, non-exporting review flow for stratified samples from both groups, both settlements, all session phases, and rare families.
2. Human-label condition boundaries, multi-label families, deadline interpretation, and ambiguous/no-condition cases against a written guide.
3. Freeze a new temporal evaluation set before any fine-tuning; use a separate calibration partition and source-only calibration for cross-group evaluation.
4. Calibrate probabilities and define abstention. Low-confidence or unseen families must not affect tolerance.
5. Benchmark an ONNX INT8 build under realistic concurrent load.
6. Only then study outcome-conditioned tolerance offline with point-in-time market features; future trade outcomes must never become input features.

Promotion requires a separate explicit decision, a versioned self-contained artifact, rollback, shadow comparison, and staging evidence.
