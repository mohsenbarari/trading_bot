# Normal container activation

The maintenance entrypoints in `deploy/maintenance/normal_activation/` replace a
finite, explicitly qualified set of existing containers. They preserve existing
business configuration, storage and network membership. They do not build images,
run migrations, seed data, change DNS or Writer ownership, or certify security
closure. Normal application release continues through the existing release path.

## Source and regression checks

`activate_services.py` uses the adjacent, SHA-pinned `datastore_primitives.py`.
`activate_stores.py` is the byte-identical standalone datastore entrypoint from the
same reviewed snapshot. Keep the two copies identical when changing shared code,
and update the service pin after review. This snapshot intentionally retains the
as-used files rather than refactoring the recovery path during security closure.

Run the finite synthetic regression suite from the repository root:

```sh
python3 -m unittest discover -s deploy/maintenance/normal_activation/tests -p 'test*.py'
```

The tests exercise a synthetic Docker Engine and disposable UID/GID 10001 files with
real flock/CAS checks. Run the latter in the real host UID namespace; an isolated
sandbox cannot prove the required ownership semantics. They neither access the Docker socket
nor invoke systemd, operational databases, Telegram or OTP delivery.

## Before interruption

1. Qualify the exact loaded daemon image and canonical Config identity; accept the
   retained business profile for the correct site. A passing helper is not image,
   business or vulnerability acceptance.
2. Collect the exact existing container IDs, Compose owner, images, mounts and
   network endpoints. Keep operational plans and runtime receipts outside Git.
   Never publish private Env, configuration, authentication values or user data.
3. Establish the existing owned Host timer/watchdog fence. Generate a fresh
   operation ID, fence and state directory, with an absolute deadline and rollback
   reserve. The outer runner must contain that entire deadline and finalizers.
4. Baseline each existing functional readiness contract before stopping anything.
   Copy declared Docker `CMD`/`CMD-SHELL` exactly. Additional probes must be genuine
   existing functional checks; do not substitute a guessed generic command.
   App/Bot/Sync require their accepted functional probes and site business profile.
   A stale cached preflight health status cannot override a successful fresh
   configured test; a failed fresh test refuses the operation before interruption.

## Activation and failure handling

Invoke the appropriate reviewed entrypoint using `--plan`, a fresh `--state-dir`,
and `--deadline-epoch`. These are privileged mutation tools; run only the exact
qualified operation. They carry private runtime values in RAM, emit bounded public
technical events, and retain old containers and data for rollback.

The controller predicts Docker image-default provenance labels before create,
keeps business/Compose labels and private values, and checks exact post-create
configuration. Only the observed Docker29 `OomKillDisable: null` to `false`
representation is equivalent; other HostConfig changes refuse acceptance.
A never-started, unattached operation-owned candidate retains its pending network
intent instead of requesting an invalid disconnect. Started candidates retain
strict endpoint checks.

After datastore replacement, **all originally running dependent clients start and
recover their original restart policies before any client health wait**. The
controller then validates the whole original cohort. Dependent clients must not
remain stopped because a preceding client waits for them. Postactivation readiness
still requires the declared Docker healthy state and the same functional probes.

A known failure attempts restoration of the exact original containers. An unknown
Docker outcome is `NEEDS_RECONCILIATION`, with its pending action retained: do not
blindly retry, create another candidate, or start the retained old writer. Inspect
actual owned state first, and create a fresh operation only after reconciliation.

## Capture release binding

Both capture roles require explicit `capture_authority_release_handover=true` in
the approved item. Their existing `authority-container.json` is release-bound;
changing only image/Env stamps leaves the old marker and causes exit 78.

The finite handover checks the actual mounted session/state paths, bounded marker,
unchanged container authority/role and physical single owner. After the old
container is stopped/PID0 and the created candidate is verified, it acquires both
existing owner locks without truncating them. It CAS-updates only `release_sha`,
preserving authorization time, extra fields, UID/GID10001 and mode0600. It releases
locks before starting the candidate. An equal release is a validated exact no-op.
A known rollback stops the candidate and restores the exact original marker before
starting the old capture. An unknown write/start outcome remains reconciliation;
never start the old capture against an unresolved new marker. Session databases,
keys and replay-pause controls remain unchanged.

## Acceptance and durable restart behavior

`APPLIED_RUNTIME_ONLY` means runtime replacement passed, not overall acceptance.
The responsible reviewer must independently confirm actual role readiness, single
owner, data preservation, provenance and the qualified runtime library identity.
Update only the existing final image-reference override fields with the reviewed
CAS updater; preserve every private/nonimage field and unrelated datastore image.
Do not copy incident-specific paths or receipts into a generic script. Release
only the fence owned by this operation after accepted readiness and durable refs.
Record the final result in the normal release/security evidence location, retaining
only required evidence under the existing retention policy.
