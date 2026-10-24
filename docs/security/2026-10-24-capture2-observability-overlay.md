# Capture2 observability candidate — source only

The exact existing image is
`sha256:2239469ab3dd3e291e16b4571af84d446f776ade05d48552f150b7ec2670fcc6`.
Its 35,143-byte foundation matches public Git commit
`e6283962b57de53bbb4f69dc4ffa72ed67b21885`, SHA-256
`2b9bcc1b92626cbc1378003bad75f68fb55826cacc45c46e31aadf07be92965f`.

`scripts/prepare_capture2_observability.py` reads only that public Git object.
Default is read-only; `--output` exclusively creates three public build inputs:
the patched foundation, a two-instruction Dockerfile, and an identity manifest.
The source file is generated, not duplicated in Git. An exact byte replacement
changes only the generic `OSError`/`ValueError`/SQLite error handler. Exit status,
reason code, other handlers, parsing, capture and model behavior remain intact.

The report adds a static error-type enum, closest recognized static origin enum
and public code line number. Origin lookup walks at most 64 frames and prints
no traceback, filename, function, exception message, payload, credential or
locals. Unknown types/origins use fixed fallback values. These fields diagnose
the crash; they do not fix or restart it.

## Build admission — not performed by this packet

1. Independently review/test/publish the preparer and retain its output manifest.
   Never use the current main foundation instead of the exact historical blob.
2. On the actual Docker daemon, require the exact existing base image ID above
   and the recorded original public foundation hash/UID10001:GID10001:0644.
   Bind the manifest's fixed local base tag to this ID, and verify it again
   immediately before building. A Docker image config ID is not a registry
   manifest digest; do not invent a registry reference or let a builder pull a
   different image. Use the native Docker driver with this verified local image.
3. After explicit build admission, use the generated context only, no pull,
   no RUN network, and record the actual immutable candidate image ID. The recipe
   has one COPY with UID/GID10001, no RUN/dependency/install step. The public
   context foundation is explicitly mode0644, even under umask077, inside its
   private0700 parent; legacy COPY preserves that mode. The actual Web daemon
   has no Buildx plugin, so no `--chmod` or new builder installation is needed;
   use its existing legacy builder for this one COPY.
4. Require candidate rootfs layers to equal the exact base prefix plus the one
   reviewed file-copy layer; the only filesystem difference is the foundation.
   Prove its actual hash and metadata without any operational session mounts.
   Preserve image Env, User, Entrypoint, Cmd, Healthcheck, workdir and other
   runtime defaults. Do not label this mixed artifact as a whole-main release.
5. Run synthetic tests of the actual candidate handler in an isolated container
   with no application config, Telegram session, network or capture entrypoint.
   Validate its manifest/source hash before compiling the same public bytes.
   Passing source tests alone is not an actual image/runtime result.

## Standard image-only rollout admission — blocked until recovered

The old container's Compose labels refer to three files which are absent,
including its runtime env. This packet does not pretend those files exist and
does not authorize a stop, duplicate capture, PID repair or live code edit.

Recover the historical **public** Compose sources from their authoritative
revision/artifacts. Obtain the protected role env through a legitimate existing
configuration/provisioning path. Do not dump, hash, log, back up or reconstruct
private Env as a diagnostic. Never inspect/copy/hash the Telegram session or
private configuration merely to discover the error.

Render through the normal trusted Compose operation in memory, with logging
disabled for private material. Before a stop, require full parity against the
current container: complete resolved Env, command/entrypoint/user/workdir,
healthcheck, restart policy, capabilities/security options/read-only root,
resource limits, networks/aliases, ports and every mount/source/mode. Only the
explicitly reviewed image may differ. Neither Env values nor a digest of them
may enter evidence. Emit a Boolean parity result only. Missing authoritative
configuration or any mismatch blocks activation; do not bypass the gate.

Once root approves that concrete parity result, perform a normal Compose
replacement of **only** account2. Confirm old capture/process descendants are
gone and there is exactly one session owner before starting the replacement.
Do not start a second capture container or manipulate a live process/lock PID.
Keep all data, mounts, paths, original image and recovered config for a bounded
rollback window. Require exact unchanged non-target identities and actual
capture heartbeat/readiness, then inspect only the new static error fields if
the ordinary role fails. A repeat failure remains failed; do not call it fixed.

Retain required public provenance and protected rollback references through
review, with a cleanup review due within 10 days. No artifact in this packet is
a production backup, secret snapshot, security remediation or activation proof.
# Finite activation after authentic QA parking

The reviewed operation is `deploy/maintenance/web_capture2_observability.py`.
Its default `check` acquires the original maintenance/source/reservation locks
without rewriting their payloads or the expired C07 transaction. Activation is
an explicit separate action, bound to the completed QA-parking audit and a new
15-minute repair clock. The first nine minutes admit work; six remain for
rollback. It never releases the original fences or declares security closure.

Use only the recovered public 4ef8 Compose context at
`/root/.local/security-capture2-compose-parity-20261024-04` and the legitimate
protected f3be `--env-file`. The narrow overlay retains the proven live public
release revision and `unless-stopped` policy. Full resolved environment and
Config/HostConfig comparisons remain in bounded RAM. Actual runtime collector
inhibitors must remain intact; this operation does not install persistent boot
guards or authorize reboot.

The operation stops only account2 with normal Compose, proves its PID is zero
and no other running container owns an overlapping account2 session bind, then
uses `up --detach --no-deps --no-build --pull never --force-recreate` for that
service. It verifies all other 26 identities/images/configurations unchanged.
Only process-state changes of the two previously bound Market incident IDs are
recorded as still open. Post-start output contains only the fixed bounded
exception-type/origin schema; an absent diagnostic remains explicitly pending.

Unexpected replacement/configuration failures trigger the same normal Compose
operation with the retained immutable base image. Rollback also handles an
absent account2 container after a failed create, provided the exact other 26
remain and no competing session owner exists. The new audit truthfully records
the replacement CID and the superseded capture-role anchor; the original C07
manifest and evidence remain unchanged. Public incident artifacts have a
10-day cleanup-review deadline; sessions, application data and images are not
deleted by this operation.
