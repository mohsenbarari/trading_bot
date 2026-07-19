# Telegram Queue Remediation Handoff — Review Successor to `a1a94c1a`

## Purpose and review boundary

This document describes the source-local remediation of findings `TQ-R001` through `TQ-R008` raised against commit `a1a94c1a3a4e81276e59e82bcbd1b8f99b1ca977` on `candidate/telegram-offer-publication-queue`.

The successor remains a candidate. `TELEGRAM_DELIVERY_QUEUE_IMPLEMENTATION_READY` remains code-owned `False`. This work does not authorize a merge, a staging provider call, the final `1,800 valid + 400 invalid` run, a production deployment, or use of production data/credentials. Live-only findings `TQ-F011` and `TQ-F012` remain gates after independent source review.

## Architecture being protected

The system persists immutable Telegram delivery intent in PostgreSQL and hands it to one priority queue with source-specific subordinate feeders. The primary bot owns publications, private messages, callback answers, trade notifications, administration and market notices. A separately credentialed `channel_editor` owns only allowlisted channel edits. The two roles have independent worker capacity while destination limits remain shared only where the actual Telegram destination requires it.

Provider I/O occurs only after a fenced dispatch marker. A definitive provider response is persisted as a separate durable provider fact before queue/domain feedback. Redis provides atomic cadence, cooldown and probe admission using Redis time; PostgreSQL time is authoritative for durable scheduling, claims and transitions. Runtime ownership remains disabled until Stage 4 approval.

## Remediation disposition

| Finding | Source-local result | Principal implementation evidence | Principal adversarial evidence |
| --- | --- | --- | --- |
| `TQ-R001` | Remediated | Preflight and delivery share the strict non-boolean signed-32-bit `Integer` parser. Every malformed `429` remains rate-limited and uses a positive fallback capped at 300 seconds with an explicit durable deadline source. | Boolean, string, fraction, zero, negative, max+1, huge, missing and unsafe caller-fallback matrices; PostgreSQL durable cooldown. |
| `TQ-R002` | Remediated | Offer publication/edit feeder cycles sample PostgreSQL `clock_timestamp()` and propagate the same sample through selection and handoff. Service defaults no longer use host time. | Host skew of ±24 hours, real-PostgreSQL edit selection, and publication deadlines one second before/after DB time. |
| `TQ-R003` | Remediated | Same-role provider admission is linearized on the owning event loop, checked before claim, after claim and at final dispatch entry. A retained fact defers an unstarted lease without recording a worker error. | Adversarial unit interleaving and real PostgreSQL two-slot interleaving prove zero second gateway/marker after gate closure; cancellation/fact persistence tests remain covered. |
| `TQ-R004` | Remediated | The plan contains a code-owned delivery-obligation oracle with source version, causal dependencies, role/method/destination, receiver policy and allowed outcomes. It covers publications, private confirmation, callbacks, both trade parties, partial/terminal/manual/automatic-expiry edits, administration and market open/close notices. The verifier recomputes obligations, receipts, side effects, race outcomes, SLO percentiles, deadline ratio, reconciliation, acceptance, cleanup and artifact hashes. | The former all-private fixture, missing publication/trade-party/terminal delivery, false receiver override, duplicate ID/effect, route mutation, race skew, forged SLO/reconciliation/cleanup/fault state and fabricated scan hash all fail closed. |
| `TQ-R005` | Remediated | Scanner schemas expose a filename list, count and content manifest separately from total release-surface counts. The live verifier recomputes the artifact manifest from bytes. | The actual schema-3 `scan_release_surfaces` report passes directly; a well-formed fabricated hash fails. |
| `TQ-R006` | Remediated locally; infrastructure attestation still required live | Sender and observer receive independent allowlisted environments built from scratch. Parent generic/production variables, `PYTHONPATH` and parent `PATH` are not inherited. Every real staging child input is rebound to a signed config fingerprint. Absolute regular executable digests, commands, exact fields, short validity, Ed25519 signature and one-use authorization are enforced. | Production sentinel environment, endpoint-fingerprint mismatch, sender/observer separation, signature/tamper/expiry/extra-field and binary-binding tests. Actual network egress enforcement remains an external Stage 4 preflight requirement. |
| `TQ-R007` | Remediated | All 18 trade/expiry races use one common barrier with at least two trades, planned skew at most 25 ms, and a deterministic 9/9 trade/expiry winner mix. Live evidence must show release skew at most 100 ms and exactly one authoritative winner. | Twenty-five seeds, outcome/skew mutations and cross-process `PYTHONHASHSEED` reproducibility. |
| `TQ-R008` | Remediated | The fault catalog separates every valid/malformed `429` shape and binds shape hash, retry source, provider-call count, duplicate-effect count, allowed durable state, applied delay and deadline source. | Catalog equality, strict parser matrices, PostgreSQL persistence, and rejection of a driver-authored arbitrary durable state. |

## Additional defects found and closed during remediation

The remediation self-review found and closed several proof-quality gaps beyond the literal report:

- a scanner manifest was previously checked only for hash shape; the verifier now recomputes it from every declared artifact byte;
- signed live authorization previously allowed signed but unknown fields and trusted the key copied into its own config; its field set is now exact and its verification key must match an independently provisioned trust anchor outside the run directory;
- Stage 4 generation iterated one Python set, making trace hashes dependent on process hash seed; generation is now canonical across processes;
- callback effects were not receiver-required in the new oracle; trade and manual-expiry callbacks now require independent receiver evidence;
- fault durable state was only required to be non-empty; it is now checked against a code-owned per-case allowlist;
- provider-fact barrier deferral was initially represented as a pre-dispatch worker error; it is now a short controlled retry with no `last_error`.

## Evidence contract

The external review package must be produced from the clean, pushed successor SHA and must include:

- raw and structured full `test_telegram*.py` evidence from the guarded local runner, including real scratch PostgreSQL and Redis tests and zero skip/resource/loop/unraisable/FD-growth results;
- raw and structured `test_bot*.py` regression evidence;
- focused request/logging regression evidence;
- exact tracked-source and evidence-package security scans;
- call-site ownership audit, Git/main patch-equivalence reconciliation and GitHub Actions availability status;
- a manifest, internal `SHA256SUMS`, ZIP archive and external ZIP SHA-256 sidecar;
- retained pre-remediation and failed diagnostic reports rather than deletion of inconvenient failures.

The package manifest and reviewer prompt, not this committed document, bind the final successor SHA and final test counts. This avoids changing the reviewed SHA merely to write its own hash into Git.

## Required independent-review questions

The reviewer must independently inspect code and raw evidence rather than accept this handoff's conclusions. At minimum, it must answer:

1. Are all eight findings actually closed on the exact successor SHA?
2. Can any same-role slot start a new provider call after a definitive result has entered volatile persistence?
3. Can any malformed preflight `429` overflow storage, become terminal, or trigger an early second call?
4. Can a fabricated driver omit or relabel a required delivery, receiver receipt, race result, cleanup effect, fault state or SLO and still pass?
5. Can either child inherit or substitute a production-capable endpoint, token, module path or unsigned policy field?
6. Is the scanner report produced by the shipped scanner directly consumable and independently content-bound?
7. Is the Stage 4 trace deterministic, causal and complete for `1,800 + 400`, market notices, trades, manual/automatic expiry and administration?
8. Do any source-local P0/P1/P2 blockers remain before authorization-bound Stage 4 preflight?

The reviewer must keep live capacity, real Telegram permissions, network-policy enforcement, cleanup against staging and production authorization explicitly open unless corresponding live evidence is supplied later.
