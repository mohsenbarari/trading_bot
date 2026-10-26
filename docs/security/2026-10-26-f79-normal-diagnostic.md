# F79 current-normal diagnostic packet

`capture_conflict_normal.py` is the coexisting `/4` consumer for the one approved
read-only diagnostic source bind. It uses current normal M/C/S validators and
preserves the sealed H9d stopped-first replacement, cgroup/owner checks, known
rollback and unknown-outcome rules. H9d, R2d, Wff, the original e91 packet and
all earlier observations remain unchanged. The rebase proof still names its
original M24f producer; current normal admission does not relabel that evidence.

The target remains the original `6a16…` container and F79 image. The normal
four-file prefix, working directory and explicit Env reference remain fixed.
Current CLI 5.6.1 is independent of the historical creation label 2.40.3.
New and rollback containers must carry the current creation version, exact
normal name, normal prefix and present-empty `--no-deps` creation label.
`pull_policy`, when present, is exactly `never`.

Normal Compose resolves private configuration only in trusted RAM. Every
service's no-env-resolution references must be covered before full rendering.
Both secret references and Env references are held by metadata without reading
or hashing their payloads. Complete business Env, Config, Host and mounts remain
comparison operands in RAM. The release comparison comes from the admitted
normal model, without inferring an Env value from an OCI revision or pathname.

The old runtime has five mounts and baked capture/foundation code. Only the
approved capture source gains one read-only bind. The normal validators compare
a clone with exactly that bind removed, while the actual render/mount delta is
independently checked. Rollback recreates the old image with five mounts and
baked foundation/capture code. No session, event data, key or Env diagnostic is
provided. Only the existing closed conflict-field record may leave RAM.

Before stop, the worker rechecks the complete captured runtime and normal
parity. Before start, it proves the exact created container never started.
Permanent fence FDs survive all source finalizers. A known closed temporary
guard loses only its own live-name checker; a duplicate FD checks zero links and
named absence through the final outer checkpoint. Unknown requests retain
ownership/history for manual reconciliation. Native keeps the actual bounded
SSH return code and admits only a strict closed peer refusal, so printed output
from a later-failing bootstrap cannot count as success.

## Source and staging

The Root-only operational packet is Native
`.local/security-f79-normal-diagnostic-plan-20261026-04/`:
`prepare_normal_control.py`, `one_normal_observation.py`, `stage-f79-normal.yml`
and their owned fixtures.
It stages exactly seventeen public assets into a fresh nonce beneath the
existing capture diagnostic roots. Those assets include current M/C/S and the
bounded capture helper under separate names, the original H/R/W/M24f helpers,
the original authenticated catalog/LOAD/control/archive/profile evidence, the
actual rebase proof, the reviewed public overlay and the fresh `/4` control.
Publication is exclusive with file and parent fsync plus identity/byte checks.
Ansible cannot overwrite an existing destination. No image is fetched or loaded.

Root first verifies the selected committed source blobs and published commit.
The new files are not operationally admitted merely because this document or a
unit fixture passes. `packet-pins.json` records the exact reviewed source pins.
The original readonly rebase result remains proof of public code lineage, not
permission to replace a runtime.

## Root execution sequence after review

Use a fresh observation nonce and the published source commit. The preparer's
own hash and worker hash come from the final packet manifest. The preparer has
no client or serving API:

```sh
/usr/bin/python3 -I -B "$F79_PLAN/prepare_normal_control.py" \
  --mode observe --nonce "$F79_OBSERVE_NONCE" \
  --source-commit "$F79_PUBLISHED_COMMIT" --source-sha256 "$F79_PREPARER_SHA"
```

Stage that nonce using the existing maintenance Ansible installation and fixed
inventory, with `ANSIBLE_CONFIG` set to the existing
`deploy/maintenance/security_legacy_parking_ansible.cfg`:

```sh
/root/trading-bot/trading_bot/.local/security-maintenance-ansible-venv/bin/ansible-playbook \
  -i /root/trading-bot/trading_bot/deploy/maintenance/security_consumers_inventory.yml \
  "$F79_PLAN/stage-f79-normal.yml" \
  --extra-vars "@$F79_PLAN/observe-$F79_OBSERVE_NONCE-stage.json"
```

The fixed Root observation caller runs the Native worker in a finite scope: one CPU, 1 GiB RAM, swap zero,
64 tasks, 900 seconds, five-second stop, control-group kill and SendSIGKILL.
There is no address-space limit for Go clients. The coordinator itself creates
the matching finite Web scope and enters the exact normal working directory.

```sh
/usr/bin/python3 -I -B "$F79_PLAN/one_normal_observation.py" \
  --nonce "$F79_OBSERVE_NONCE" --control-sha256 "$F79_OBSERVE_CONTROL_SHA" \
  --source-sha256 "$F79_OBSERVER_SHA"
```

This operation can only return a closed readonly refusal or an observation with
`diagnostic_applied: false`, `activation_authority: false` and actual public
reference/lifecycle metadata. The caller retains the actual final Native scope
return code and admits only the strict public observation. It closes all its
source holders before publishing `observe-NONCE-completion.json`. A worker's
earlier `native-observe-outcome.json` alone is never accepted by the next
producer: a late finalizer could still make that worker refuse. Nonzero or
unclassified stdout is neither retained nor hashed as an observation.

The final preparer consumes only the named caller completion with actual zero
return code, fixed caller/worker pins, exact public stdout SHA and its original
control. It refuses changed source/profile/version, the old nonce, malformed
metadata or an observation older than 120 seconds. Caller intent/completion and
Native once-only request/outcome files remain immutable.

Root then uses another fresh nonce with `--mode diagnostic --normal-proof
"$F79_PLAN/observe-$F79_OBSERVE_NONCE-completion.json" --normal-proof-sha256 SHA`,
stages the corresponding manifest, and runs the
Native command with neither `--observe` nor `--apply`. This is the default
NoApply gate. Its once-only check request and outcome remain immutable.

Actual diagnostic apply remains held until Root resolves fresh App artifact
preloads and establishes a fresh finite maintenance window. It requires the
explicit token `ADD-ONLY-CAPTURE2-CURRENT-NORMAL-DIAGNOSTIC-SOURCE-20261026-04`.
The apply invocation performs all admission again under held fences; a prior
NoApply result grants no substitute runtime authority. Each mode permits one
request per nonce, with no automatic retry. Capture, functional-fix, security
and full-matrix acceptance remain open/false throughout.

## Current reboot-safe admission

The first actual readonly observation b83fed1f9caa4a11b21fa9d03b0259a9
refused with FileNotFoundError before any diagnostic apply. Root verified by
metadata that both runtime legacy inhibitors were absent after reboot, while
both persistent inhibitors existed as root-owned0644/nlink1/170-byte files.
The original consumer required the runtime file unconditionally. Its source,
staged request and closed refusal remain immutable.

The current canonical worker accepts persistent-only ownership authority after
reboot, or both files when present, only after exact sealed-body and full9/FD
admission. Each legacy unit must have at least one authoritative file and its
actual DropInPaths must equal those admitted files. Disabled/inactive state,
RefuseManualStart, exact token condition, zero service PIDs, empty cgroup and
absent token remain mandatory. Initially absent runtime files and every held
file are rechecked through closure; foreign, conflicting, missing or changed
authority refuses. The worker does not recreate a runtime file to satisfy a
guard. Its normal loader also admits the fixed public machine-id leaf before
opening/reading it, with exact0444/0644 modes, root ownership, one link,33 bytes
and held final checks.

Root passed all30 current public fixtures in the genuine Root/CAP_CHOWN-only,
NNP, separate-init/network-none/readonly own-COPY lane. Manifest
`a09c5619146fe7266f1229fd3a5c876603fda8093b4b9f5c43cba82fee6f1571`,
result `ce1fb1fb78e57506df5a392ccad87fabdebb5708f1756cdd3e8b61ab4cb8470b`.
The ignored coexisting prepare_normal_control_v2.py and
one_normal_observation_v2.py bind the new worker; fresh controls/nonces are
required. Old control/preparer/caller files remain unchanged. No previous
request grants retry or apply authority.
