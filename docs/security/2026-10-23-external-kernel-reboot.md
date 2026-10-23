# One native Bot kernel reboot, controlled independently from Web

This is a **single-window, single-attempt** maintenance package, not a new
deployment framework. The native Bot is `65.109.216.187:22`; the independent
controller is existing Web `65.109.220.59:37067`. A source review or successful
syntax/unit test is **not** evidence that a live reboot has happened.

Mutation cutoff: **2026-10-24 00:30 UTC**. All restoration and credential cleanup
must finish before **01:30 UTC**, when the temporary SSH authorization expires.
Both projects' downtime is authorized, but authority, images, DNS and business
data must not be changed. Starting the refactor automation is a separate action
and is deliberately absent from this package.

## Scenario and fixed sequence

1. Publish/review the exact source first. Use existing `controller_access.yml`
   with **new task ID `host-patch-02`**. Old `host-patch-01` was cleaned and is
   not reusable. Its private key is generated and stays **on Web only**; only
   SSH itself uses it. Agents/Ansible never read, checksum or transfer its bytes.
   The opposite native Bot→Web lane explicitly uses the existing persistent
   `/root/.ssh/id_ed25519_iran` identity (the name is historical, not the target).
   Metadata-only admission checks it; minimal-environment, config-free strict SSH
   was proved before setup. The durable runner intentionally ignores SSH config;
   an inherited agent or ordinary interactive success is not cleanup readiness.
2. Default `reboot_bootstrap.yml` is read-only: check exact existing hosts,
   native Web identity, Python **3.14.4 x86_64, GIL enabled**, and workspace
   metadata. `setup` explicitly provisions only a private task-scoped Web venv.
   Web lacks `ensurepip`, so venv uses `--without-pip`. Ten exact official
   SHA-pinned wheels (pip **26.2.2**, Ansible-core **2.21.5**, eight dependencies)
   install offline with no builds, dependency resolution, cache or global pip.
   Actual installed versions, `pip check` and strict Web→Bot SSH must pass.
   Download is bounded **before writing** by exact official wheel size, curl
   max-filesize, child file-size limit and60-second transfer timeout; then
   exact size/SHA are streamed and fsynced before atomic no-replace publication.
3. Native `reboot_guard.py snapshot` freezes a new public control manifest:
   current source HEAD and public assets; exact15 container identity/image/
   mount/policy baseline;21 native unit definitions and original enablement;
   historical marker/journal bytes and inode; machine/oldboot identity;
   required seven filesystem paths' UUID/source-root/inode (including the
   separate staging bind); verified backup/restore/offhost
   receipts; STOP/BLOCKED-cycle8/monitor-source proof; controller-ready proof.
   It does not change any service, historical authority receipt or Writer.
4. `start` revalidates that manifest and launches one **Web systemd-run
   oneshot** with `Restart=no` and a one-hour hard limit. It survives this
   conversation disconnecting and the native Bot rebooting. Its own exclusive
   intent file prevents a second attempt or blind reboot retry.
5. Before the reboot, native guard holds the **current** historical marker
   inode and runtime-source flocks. It atomically publishes a new independent
   private-primary reservation and fsynced state/token; it never repairs or
   adopts `AUTHORITY_TRANSFERRED`. The reservation persists through reboot.
   Native Ansible disables only previously enabled recorded units, stops
   recorded timers/consumers, records all15 restart policies as `no`, then
   gracefully stops only the existing containers. Docker/socket may autostart
   after boot, but those containers must remain stopped until controlled restore.
6. Root cron has an explicit Golden `@reboot systemctl restart golden_bot`.
   Therefore one owned temporary **Golden-only** systemd condition blocks it
   while the task token exists. This drop-in also requires/asserts the secure
   bind mount, so a missing bind cannot masquerade as an absent token. Existing
   cron, original units, unrelated relay, SSH and agents are untouched. The
   automation monitor remains blocked by its already-created durable STOP.
   Its cron is matched to the exact public tmux-PYTHONPATH setup followed by
   the direct monitor command and `/dev/null` redirects; extra shell commands
   are refused, not normalized away. Private cron bytes are neither retained
   nor hashed in the manifest.
   Its existing separate `PYTHONPATH` assignment is admitted only as one exact
   canonical-path token and separately bound; chained/extra/duplicate assignments
   are refused. A real preflight caught this missing environment-line contract
   before any runtime writes; the cron itself was not changed to pass the guard.
7. Web's built-in Ansible reboot action requests exactly one native reboot and
   waits for a different boot ID. That is only connectivity evidence. Native
   postcheck separately requires the same machine, new boot, **active
   `6.8.0-147-generic`**, unchanged source/control bytes, actual attached UUID/
   bind source/inode and loaded11 boot-gate assertions. Old mount IDs/PIDs are
   not accepted as cross-boot proof. Kernel138 remains installed as fallback;
   booting138 is a failure, not a reason to retry automatically.
   Read-only GRUB admission also requires `GRUB_DEFAULT=0`, no saved-default
   setting, first configured Linux entry147 and no `next_entry` override. It
   never edits GRUB or treats a merely installed kernel as an active fix.
   Both147 target and138 fallback kernel/initrd must exist as nonempty owned
   regular files. The first kernel's paired initrd must also be147; their
   inode/size/mode/timestamps are bound without hashing full boot images.
8. Native reacquires the three current-inode ownership flocks, verifies all15
   still stopped and all recorded native consumers quiescent, then starts
   existing datastore IDs first. Real PostgreSQL/Redis probes must pass before
   existing application IDs start; the actual API must then return200. Only
   afterward is Golden's exact token removed immediately before its intended
   start, original enabled/active unit intent and exact container policies
   restored, and Golden SQLite `quick_check` verified read-only. No rows are read.
9. Only full success produces the native completed receipt and releases the
   **owned new** reservation. Web retrieves only that public receipt, makes its
   final SSH request to existing exact task02 cleanup, checks the private key
   and pin are gone, and publishes its own completed receipt. There are no
   further SSH requests using that removed key.

## Commands for the reviewed operator

Use the existing scoped Ansible executable, never global Ansible. Set
`ANSIBLE_HOME`, `ANSIBLE_LOCAL_TEMP` to the existing task-private local cache
paths and `ANSIBLE_CONFIG=deploy/maintenance/reboot_ansible.cfg`. Run from the
published canonical repository, **not** an unreviewed candidate worktree.

```sh
.local/security-maintenance-ansible-venv/bin/ansible-playbook -i deploy/maintenance/controller_inventory.example.yml deploy/maintenance/controller_access.yml -e controller_access_id=host-patch-02 -e controller_access_action=setup -e controller_access_confirmation=TEMPORARY-ROOT-SSH-20261024T013000Z
.local/security-maintenance-ansible-venv/bin/ansible-playbook -i deploy/maintenance/controller_inventory.example.yml deploy/maintenance/reboot_bootstrap.yml
.local/security-maintenance-ansible-venv/bin/ansible-playbook -i deploy/maintenance/controller_inventory.example.yml deploy/maintenance/reboot_bootstrap.yml -e reboot_bootstrap_action=setup -e reboot_bootstrap_confirmation=EXTERNAL-BOT-REBOOT-20261023
/usr/bin/python3 -I -B deploy/maintenance/reboot_guard.py snapshot
```

Read only the emitted manifest SHA and nonce. Then pass **those exact values**:

```sh
.local/security-maintenance-ansible-venv/bin/ansible-playbook -i deploy/maintenance/controller_inventory.example.yml deploy/maintenance/reboot_bootstrap.yml -e reboot_bootstrap_action=start -e reboot_bootstrap_confirmation=EXTERNAL-BOT-REBOOT-20261023 -e reboot_manifest_sha256=EXACT_EMITTED_SHA -e reboot_nonce=EXACT_EMITTED_NONCE
```

Default bootstrap/check creates no workspace, venv, wheel, key or running unit.
Snapshot creates only its exclusive control manifest. Never call the native
quiesce/restore playbook directly: its owned-info probe requires genuine
current native flocks, reservation and boot-bound state.

## Failure and recovery boundaries

- Unknown container/unit/source/cron/STOP/receipt/mount change, another operation,
  expired deadline, wrong active kernel or ownership mismatch refuses safely.
- A crash before full reservation publication cannot expose a partial canonical
  reservation. Once intent/reservation exists, any ambiguous or failed phase
  remains blocked for exact-state operator review. The tool does **not** delete
  foreign locks, fabricate handoff evidence, infer ownership from PID alone,
  automatically restart a partially restored system, or retry reboot.
- If a preboot cut fails, some approved runtime may be stopped/disabled. Its
  bound manifest and reservation remain; inspect/recover exact owned intent
  instead of rerunning setup/snapshot or removing locks. Likewise a postboot
  failure deliberately retains the reservation and Golden guard if still set.
- Expiry only revokes authentication; it does not delete Web files. If success
  cleanup fails, use the existing task02 exact cleanup command separately while
  ordinary Bot→Web SSH remains available. Do not reuse old01 or broad-delete
  `.ssh`, control directories, historical receipts or backup bind mounts.
- The Web workspace contains only public assets, wheels and capped8MiB logs;
  no env/private key bytes. Preserve it for review of a failed operation; after
  success retain the public receipts per the10-day maintenance evidence policy
  and remove only this task's verified owned artifacts, never unrelated files.

## Verification evidence

`tests/test_security_kernel_reboot.py` exercises real synthetic-file flocks,
atomic no-replace publication, short-write/crash/race refusals, cross-boot
ownership and complete/failed restore paths, active-kernel and bound-evidence
drift, bounded controller reads and exclusive intent, exact dependency locks,
read-only defaults, durable runner, credential/source scope and phase ordering.
Tests unconditionally deny subprocesses, TCP/DNS/urllib and control-file opens
outside their temporary fixture. Ansible syntax checks are separate. These
tests do not reboot a host or prove a live maintenance success.
