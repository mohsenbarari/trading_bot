# Bot maintenance-lock filesystem identity recovery

Scope: this incident only, parent release `4ef8e6dc`. This is not a deployment,
capture promotion, stale-lock deletion, or a change of Product/Queue/Writer/DNS
authority. Normal handoff and deployment validators are unchanged.

## Independent retained evidence

- The local retained authorized Web journal is
  `/root/secure-envs/trading-bot/release-control/production-4ef8e6dc-cutover/web-bluegreen.json`,
  SHA-256 `330f8efac0efdff959d6c47ee104729fc7170752b33f16800abe11ac0a674271`.
- The authentic pre-transfer Bot receipt exists on the SSH-verified peer
  `65.109.220.59:37067`, at the same release-control directory with basename
  `web-mirror-bot-legacy-authority.json`, SHA-256
  `ead6a73d2b1bd2050757ac0c8ce8def3b4d293d713576d50628448f69e803d4a`.
- That receipt must be retrieved **byte-for-byte**, with bounded SSH output,
  `BatchMode=yes`, `ForwardAgent=no`, and `StrictHostKeyChecking=yes`. Validate
  its pinned hash before exclusive atomic publication as a root-owned `0600`
  regular single-link JSON file beneath the private `release-control` directory.
  Never overwrite an existing file or recreate JSON from a summary. Retrieval
  and operational execution are separate steps; this source patch performs neither.
- The `--pretransfer` option selects that exact staged receipt. Its basename and
  private-root containment are checked; the authentic digest is hard-pinned.
  A missing local copy blocks even the default check truthfully.
- The older supersession receipt's `3ac5…` digest is **not** an authorization
  source for this recovery.

## Check before mutation

Run only through the trusted native server administration channel, not Docker,
a PID/user namespace, or an IDE sandbox. Root, systemd PID1, matching self/PID1
namespaces, absence of container context, and complete `/proc` inspection are
required; these checks supplement the trusted execution channel, not attest an
arbitrary hostile host. Incomplete visibility, an old/current inode still open
by another process, or pending controller/Queue/two-host operations blocks work.

The default mode is read-only. Supply independently observed predecessor
marker/journal digests and the current filesystem device/inode; never substitute
the stale identity stored inside the marker. The canonical source fence is
`/root/secure-envs/trading-bot/.production-runtime-source.lock`.

```sh
python3 scripts/recover_market_bot_lock_identity.py \
  --expected-lock-sha <verified-predecessor-marker-sha256> \
  --expected-journal-sha <verified-predecessor-journal-sha256> \
  --device <current-device> --inode <current-inode> \
  --source-lock /root/secure-envs/trading-bot/.production-runtime-source.lock \
  --pretransfer <exact-staged-authentic-receipt-path>
```

No live recovery was executed as part of authoring these files. `RECOVERABLE`
means eligibility only; it is not a receipt that production has been repaired.

Fresh operator inventory on 2026-10-23 found active/enabled Bot legacy capture
units despite the historical `AUTHORITY_TRANSFERRED` receipt. This is an
independent **live authority-drift blocker**, not an inode-only problem. The tool
must refuse through `_assert_quiesced` before reservation/WAL/metadata writes.
Do not stop collectors, refresh the receipt, or clear evidence merely to make
this check pass; diagnose actual ownership and obtain the appropriate scoped
operational decision separately.

## Deliberate apply and interrupted execution

After independent source/test review and a fresh successful check, the same
arguments plus `--mode apply --confirm repair-production-bot-lock-filesystem-identity`
request only this metadata transaction. The owned controller reservation is
prepared, locked and fsynced before atomic Linux `renameat2(RENAME_NOREPLACE)`
publication. A competing controller or unsupported primitive fails closed;
neither its file nor the maintenance lock is overwritten or deleted.

The exact predecessor and target public control bytes are recorded in a durable
`0600` WAL. The journal is atomically replaced and directory-fsynced **first**;
only then is the maintenance marker truncated and written/fsynced through the
same held inode. Only `device/inode` in the marker and its nested journal binding
change. Nonce, status, timestamps, authority hashes and unit inventory stay exact.
The legacy services are observed, never stopped, enabled, restarted or replayed.

After interruption, use the original arguments plus
`--mode resume --confirm resume-production-bot-lock-filesystem-identity`.
Resume accepts only the exact owned reservation/WAL and inode, authentic retained
proofs, fresh quiescence, recognized predecessor/target journal bytes, and an
old/complete/prefix target marker. Unknown bytes, empty foreign reservations,
foreign WALs or ownership drift require investigation, never forced adoption.
An interrupted unpublished reservation leaves no canonical guard; retry apply.

`COMMITTED` is issued only after exact readback and the unchanged normal guard
passes. Only this transaction's byte- and inode-verified reservation is then
removed. The maintenance marker is never unlinked/renamed. A committed retry is
idempotent. There is no rollback of capture/Writer authority or automatic
restoration of an obsolete filesystem binding. Keep the WAL while the incident
or rollback window remains open; expire it through the normal reviewed cleanup
policy, not automatic deletion inside the recovery command.
