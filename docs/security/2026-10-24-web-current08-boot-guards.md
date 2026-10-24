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
