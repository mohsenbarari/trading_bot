# Dual-Platform Registration Stage 8 Final Review Remediation

Date: 2026-07-12

## Scope

This remediation responds to the independent review of source commit `4983bef8`, including the
Stage 8 implementation introduced by `fe35285e`. The controlling plan remains
`docs/DUAL_PLATFORM_REGISTRATION_AND_SYNCHRONIZED_OTP_ROADMAP_20260710.md`.

This work authorizes no migration, feature enablement, staging deployment, provider test, secret
rotation, production deployment, push, merge, or Stage 9 source work. Stage 9 remains blocked until
an independent reviewer evaluates the exact remediation commit and returns GO.

## Accepted Findings And Corrections

### H-1: malformed observability snapshot

Accepted. Snapshot normalization now strictly type-checks `last_result` and treats normalization as
one fail-safe unit. Valid JSON containing nested lists, objects, booleans, invalid numbers, malformed
timestamps, or unsupported values cannot make `/metrics`, `/api/sync/health`, or snapshot recording
raise. A healthy owner cycle can overwrite the poisoned snapshot.

Heartbeats more than five seconds in the future are now stale rather than healthy.

### M-1: flags-off legacy resend recovery

Accepted. A structured flags-off `otp_active` response now carries a backend-authoritative
`legacy_sms_resend_at` derived from the original 120-second OTP lifetime and the unchanged 30-second
manual resend rule. LoginView uses that deadline and explicit manual-resend capability.

For a genuinely old server that returns an unstructured 429, the client enters a bounded 30-second
legacy recovery without parsing localized text. Verification and resend remain server-authoritative;
the compatibility path grants no login authority.

### M-2: malformed structured state during verification

Accepted. The fallback selector and verification endpoint now share the same bounded state-decode
exception contract. Request-ID corruption returns the existing no-store invalid/expired response and
emits a PII-free denied audit. Corrupt structured metadata does not override an otherwise valid
mobile-owned legacy OTP; no decode exception becomes HTTP 500.

### M-3 and L-3: stale alert and connectivity semantics

Accepted. Stage 8 Loki rules now evaluate the latest role-specific health sample. Heartbeat rules
fail closed on no data. Alert inputs are derived numeric fields, so historical healthy samples cannot
keep a pending alert firing during a current outage.

Registration connectivity no longer comes from any pending row's historical error. The foreign
worker records connectivity from transport responses in the current reconciliation cycle and keeps
the previous observation only when no transport attempt occurs. A successful peer response therefore
clears prior outage suppression immediately.

When Redis is unavailable, health preserves feature and role expectation: an enabled job on its
owning server is `unavailable` and emits heartbeat-unhealthy `1`, while disabled and not-expected
jobs remain non-alerting. Redis loss is never flattened into a healthy zero sample.

Real Loki/Grafana provisioning and temporal evaluation remain mandatory Stage 10 staging evidence.

### M-4: multi-process audit chain

Accepted. The existing audit sink now uses an OS file lock around read-last-hash, record construction,
append, and durability synchronization, while retaining its existing in-process lock and fail-safe
fallback. The supported deployment is Linux/POSIX because this uses `flock`. Before extending the
chain, the sink requires a newline-terminated, parseable final record whose event hash verifies. A
partial, invalid, or tampered tail fails closed without appending or silently restarting the chain.
Successful append requires complete writes plus file `fsync`; initial file creation also synchronizes
the parent directory. Failed append/fsync attempts return non-durable evidence and roll back their
new bytes where the filesystem permits. A four-process test writes 80 events and verifies one complete
linear chain with unique hashes; corruption and fsync-failure tests verify the fail-closed boundary.

The sink does not automatically truncate or quarantine pre-existing corruption because that could
hide tampering. Operators must preserve the damaged artifact and restore or rotate it under the
existing incident process before durable appends can resume.

### L-2: evidence mobile identity

Accepted as evidence hygiene. The review package contains text logs and source diffs only; it contains
no screenshots. Artifact scanning and redaction are required before the package is shared.

## Operational Finding

### M-5: state-secret rotation gate

Accepted as a Stage 10 rollout blocker, not a current source-retention defect. A second key and a new
runtime authority are not introduced. Before any rotation or production rollout, repository-owned
operations must block only new structured OTP creation, leave verification and fallback processing
active, report due/live-claim/oldest-age state, drain for at least 150 seconds, rotate, run one real
accepted delivery probe, and restore traffic. Until that control exists and is proven, secret rotation
and production rollout are NO-GO.

## Verification Contract

Required evidence for this remediation includes:

- focused Stage 4/6/8, auth, health, metrics, alert, and audit tests;
- full frontend unit suite plus focused LoginView tests;
- frontend production build;
- alert YAML parsing and Python compilation;
- PostgreSQL integration suite result, with unavailable external integration explicitly reported as
  skipped rather than passed;
- `git diff --check`;
- a redacted review archive generated from the exact remediation commit.

No successful local test substitutes for real Redis/PostgreSQL restart, real Loki/Grafana temporal
evaluation, two-server staging, provider delivery, secret-drain, or owner acceptance gates.
