# Account1 live-capture incident, 2026-09-05

## Runtime outcome

Live input recovery and the permanent Account1 code fix are applied.
Product remains LEGACY and Queue-v1 is unchanged.

Account1 on wa-fi repeatedly exited with `capture_replay_source_incomplete`.
One unavailable point-in-time historical MELTED_FLOW revision remained quarantined.
The replay completion refusal disconnected all Account1 live subscriptions.
Over 2,580 restarts were observed; a recent live-starting heartbeat incorrectly
allowed Docker health to pass despite the unresolved replay quarantine.

The existing a972e389 capture release was recreated ONLY for Account1 with
`account1-live-recovery-20260905.yml`. This clears the explicit historical backfill
cutoff and source selection for this runtime, retaining normal recent catch-up
and live subscriptions. It does not delete state, quarantine, manifests, session
files, facts, or acknowledge the unfinished historical replay.

Capture started at 07:01:37 UTC. At 07:18:05 UTC it was running, restart count 0,
with a recent heartbeat and capture sequence 2544645. Melted and Herat streams
resumed. XAU had no recent upstream events; it was not artificially refreshed.

Production's actual coin-rates snapshot at 07:17:36 UTC inferred IMAM for both
previously rejected TOMORROW prices, 233000 and 233400, via the existing ranker.
These were read-only inference probes, not published offers or trades. Price
guards, user confirmation, source-quality rules, and estimator authority were
not bypassed.

## Permanent fix activated with explicit owner approval

- Code commit: 0c953335.
- Tested image source: 1f972a48ade3037d8455d574d14a598851ef5640.
- Source tree: f3f3ffcdc39bb608fb6448c5b814c155f9c73ce5.
- Image: sha256:3d8c72539565c70b416db01cc7dd9db43f5d776e90c20abf451f766745ccc6c7.
- wa-fi imported image ID: sha256:c0fd39806381256712719e1ae6be06cba0b8f710934140244ae9398e5e32196f.
- Portable content digest on BOTH hosts: c1eaefa0361ca93980a2a7dd692477ef25863b0c85689975763a51c23b9153ea.
- Image input signature: 362c29d6aade7aff1102177535913528ea1ef87dc225ff6088cf0ed2d22378b3.

Only an incomplete replay COMPLETION is isolated from live capture. Source-stage,
integrity, and storage failures still fail closed. Quarantine keeps readiness
degraded. Replay records are not reset or falsely certified.

The focused capture/foundation/snapshot suite passed 100 tests. After additional
health validation, 18 focused tests passed again. The exact runtime image also
passed those 18 tests in an isolated, network-disabled container. The file-owner
contract fixture requires root; the first non-root test invocation failed that
fixture and was not reported as a passing run.

At 07:28:14 UTC the owner-approved, scoped Account1 handoff completed with status
`APPLIED_LIVE_HISTORICAL_REVIEW_RETAINED`. Account1's marker now binds 1f972a48.
The existing production-maintenance record remains bound to 4ef8e6dc, byte-for-byte
unchanged. The operation held that exact lock inode for serialization; its
subordinate incident journal binds the parent digest and prior/target Account1
markers. This is not a whole-pipeline release supersession.

The prior owner stopped before both session/state owner locks were acquired.
Marker intent was journaled before atomic marker replacement. Compose then
recreated ONLY Account1 with the digest-pinned image, unchanged mounts, and the
same live-recovery configuration. Every other running container retained its ID,
image and start time. No overlapping Account1 session owner was observed.

At 07:30:25 UTC the new runtime had zero restarts, a fresh heartbeat and sequence
2546422 (2546179 at its first successful live probe). Melted/Herat continued to
advance. Product's 07:28:35 snapshot still inferred IMAM for 233000 and 233400.
No real offer/trade or Telegram message was created by these probes.

Docker health now truthfully reports `unhealthy` because one historical replay
record remains quarantined, even though live capture is ready and advancing.
This historical readiness failure is NOT a full acceptance PASS and was not
cleared to produce green health. Missing upstream XAU events were not synthesized.

The scoped tool is `scripts/incident_account1_handoff_20260905.py`; it refuses
blind re-execution if its journal exists. Its six guard tests plus the existing
18 capture/foundation tests passed (24 total). The first read-only Compose
preflight omitted the web profile and failed before stopping anything; this was
corrected and the real preflight passed. Automatic rollback was prepared but not
needed or claimed as exercised against production.

Operational receipt: `incident-recovery/20260905-account1/handoff.json` on wa-fi.
Future broader upgrades must account for this scoped Account1 marker/image and
must not blindly replace it with the parent pipeline's older release.

No Product deployment, Queue-v1 change, authority switch to PRIVATE_PRIMARY,
Git push, database migration, or staging runtime change was performed.
