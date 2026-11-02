# Current08 Web boot guards

This source packet prepares four persistent Web systemd drop-ins after a
successfully completed OP08 engine transaction. It performs no reboot, service
start/restart, container change, mount change or kernel operation. It supplies
no authority for a later reboot. That requires its own fresh operation and
recovery/capacity checks.

`web_boot_guard.py` accepts only the explicit `current08` profile for operation
`security-web-engine-20261024-08`, nonce
`a872a41985d8f931cd856f50068c75f2`. It checks the pinned OP08 controller and
profile sources, derives the current profile from all fourteen original public
control buffers, and compares the completed plan to that profile. The exact
27 containers, sixteen intended running containers, seventeen retained
containers, ten parked QA containers, functional f79 Capture2 image, network,
ports, restart policies and mount identities remain separate from the aborted
C07 plan. No old Native fifteen-container or reboot record is adopted.

The caller must provide three independently observed immutable SHA256 values:
the completed OP08 plan, its final `phase=complete` journal and its successful
`watch-cleanup.json`. Incomplete, aborted, changed or wrong-operation evidence
refuses. Quiesced backup/restore cleanup, compared cold archive and verified
offhost receipt must be recorded in that final journal. Both OP08 temporary
fences and the historical C07 source fence must be absent; the original
permanent production-release marker remains the same inode and bytes and is
flocked throughout admission, writes, daemon reload and postchecks. The expired
engine window is read only to authenticate source pins. Its expiry is never
extended or treated as installation/reboot authority.

Run `web_boot_guards.yml` with `web_boot_profile=current08` and
`web_boot_action=check` first. Supply
`web_boot_completed_manifest_sha256`, `web_boot_completed_journal_sha256` and
`web_boot_completed_cleanup_sha256` from the actual completed operation;
none are invented or filled by this source packet. Ansible check mode also
forces this read-only preflight. A passed preflight verifies current patched
Docker/containerd/runc versions, native Web host identity, the exact container
and systemd consumer inventory, attached ext4 UUID, all four parked volume
records/backing directories and unchanged credential-file metadata (lstat only,
no file contents), absence of the legacy collector authority token,
and existing collector inhibitors. It does not rehash the retained backup
artifacts or establish a future reboot recovery budget.

The same two sealed known Market health incidents remain explicitly open,
as in the completed controller contract. Their observed health may fluctuate;
each pre/post observation rejects an unhealthy container outside those exact
two identities. The report retains the currently observed known incident IDs
and does not declare the underlying incidents resolved or waived.

Only a separately requested `setup` action with confirmation
`WEB-BOOT-GUARDS-CURRENT08-20261024` may publish missing exact drop-ins. The two
legacy collectors remain disabled and blocked by the same authority-token
condition and `RefuseManualStart=yes`; Docker requires the real attached
mount, and the mount uses its fixed UUID. A same-name file with different bytes
refuses. Already installed exact content is verified without rewriting it.
Cleanup removes only exact owned content and refuses to remove a collector's
last remaining inhibitor. The helper never starts a unit or container.

This is source validation only until the completed OP08 evidence and an actual
read-only preflight are available. Security acceptance, application rollout
and reboot authorization are not granted by these guards.

Actual OP08 engine maintenance completed and the boot read-only check passed.
The first setup then published all four exact drop-ins and loaded them, but its
final validation refused `uuid_override_not_loaded`: systemd reported the
correct mounted device as `/dev/sdb` instead of retaining the textual UUID
alias. Independent read-only inspection confirmed the exact files, loaded
inhibitors, unchanged runtime and matching mount UUID. The source correction
reuses the full existing primary mount UUID/source/device-resolution proof;
it accepts that canonical spelling and continues rejecting a different device
or UUID. Drop-in contents are unchanged. No successful corrected setup or
reboot is claimed by that source correction alone. Public refusal output exposes
only this helper's own static reason codes, never arbitrary exception text.

## Actual validated setup, 24 October 2026

The corrected helper SHA256
`08963b564e4925ab415359b20e75a73d8302c543706e5d8477c1602ebd506c50`
passed the actual setup gate: Ansible reported two successful tasks, zero changed.
All four previously published files already had exact content, root ownership,
0644 mode and one link; loaded conditions and `NeedDaemonReload=no` were verified.
The canonical `/dev/sdb` source was accepted only through the complete attached
UUID/device proof. No service restart or reboot occurred during this setup.

The completed engine inputs are actual plan SHA256
`7d55ccacd99a0f6fd0a4d93ee493a7193a9dde027b4e47bc73b2ef0af63e6216`,
final journal `ab1b1e85fc017cb2188a499f69ad3806e60f2b8be27e16dccd9dbb5be1050184`
and watcher cleanup
`b4a488bbafd472319f3c4c1dbac7bd10582a285f305b015191af5aae4bf9f511`.
The local public `completion.json` SHA256 is
`bf805d5de6c8705b4ba1b02593b8c9827c8448d80c47273e4c2d82a541b6cf21`.
Direct reconciliation confirms the completed operation/nonce, successful worker,
owned-resource cleanup, retained permanent marker, fresh quiesced dump restore
and cleanup PASS, cold compare, and verified offhost fsync of the same ciphertext.
The dump was 2,341,073,498 bytes; the combined authenticated ciphertext was
6,461,994,096 bytes. These public metadata checks do not reread database/archive
contents or refresh backup age/capacity authority.

The [separately admitted kernel operation](2026-10-24-web-kernel-reboot.md)
subsequently completed. It rechecked these persistent conditions before and after
reboot; this setup did not supply its reboot authority. Global security/deployment
acceptance remains false, App/Market/datastore activation pending, and
autopilot/refactor STOP with clean streak 0.
