# Separate Web kernel reboot after current08

This packet uses the existing strict `web_engine_inventory.yml` from an
independent finite Native Ansible runner. It never runs the old Native
fifteen-container reboot guard. Source review and hermetic tests are not a
reboot result. Default action and Ansible check mode perform read-only checks;
neither stages files, creates ownership, writes configuration nor reboots.

OP08 engine maintenance actually completed. Its immutable public evidence is
plan `7d55ccacd99a0f6fd0a4d93ee493a7193a9dde027b4e47bc73b2ef0af63e6216`,
final journal `ab1b1e85fc017cb2188a499f69ad3806e60f2b8be27e16dccd9dbb5be1050184`
and watcher cleanup
`b4a488bbafd472319f3c4c1dbac7bd10582a285f305b015191af5aae4bf9f511`.
These identify completed engine recovery evidence; its expired or expiring
maintenance window grants no kernel or reboot authority. The exact four
persistent Web boot guards must separately pass setup verification first.

The normal operator must stage a fresh root-only
`/root/.local/security-web-kernel-reboot-20261024-01` directory, its public
`source/{web_kernel_reboot_guard.py,web_kernel_reboot.yml,web_boot_guard.py}`,
and a new mode0600 `window.json`. No existing request, result, completed marker
or partial ownership may be adopted. The fresh kernel window has exact schema
`web_kernel_reboot_window/1`, operation ID
`security-web-kernel-reboot-20261024-01`, a new32-hex nonce distinct from OP08,
the actual Web32-hex machine ID and UUID-shaped before-boot ID, UTC
`not_before_utc` and `deadline_utc` at most one hour apart, target
`7.0.0-38-generic`, and the three completed-evidence SHA fields:
`completed_manifest_sha256`, `completed_journal_sha256`,
`completed_cleanup_sha256`. `approved_sources_sha256` contains exactly those
three staged public source filenames and their independently reviewed hashes.
`reboot_allowed` is true; `kernel_packages_allowed`, `engine_packages_allowed`,
`container_recreation_allowed`, `writer_dns_changes_allowed`,
`capture_owner_changes_allowed` and `automatic_downgrade_allowed` are all false.
The caller independently pins this new window's exact bytes. The helper never
mints its own authority. For this first operation the operator keeps the new
deadline no later than22:29:08Z24Oct, preserving the original overall outage
upper bound without extending or reusing the engine window. A later reboot
requires its own fresh operation/recovery assessment.

Invoke the play with `web_kernel_window_sha256` and default
`web_kernel_action=check`. Explicit apply also needs
`web_kernel_confirmation=ONE-WEB-KERNEL-REBOOT-20261024-01`. Initial checks
require old kernel `7.0.0-27-generic`, installed target package, safe existing
old/new kernel/initrd metadata, normal GRUB default0 and first target
kernel/initrd entries, and no one-time next-entry override. No APT, GRUB edit,
package installation, manual service start, Docker start or container
recreation is performed. The full completed current profile, machine identity,
four persistent inhibitors/mount constraints, source/control bytes, parked
storage and metadata-only credential identities are checked. Six exact
PostgreSQL/Redis probes, actual internal API200 probes and native engine
enablement intent must pass. Known two Market health incidents remain open;
no unrelated unhealthy identity is admitted.

The reboot request requires at least1500seconds left. The original permanent
M inode is opened without creation and held with its current flock. Fresh
common reservation/source files are exclusively created, flocked and fsynced.
A durable `request.json` binds their exact identities, window, before-boot ID,
completed public files, persistent guard files and boot selection before the
single normal `systemctl reboot` RPC. Flocks remain held until shutdown or a
finite failure; persistent exact ownership bridges the reboot. Uncertain
request acknowledgement remains an unresolved owned operation, never proof of
cancellation or permission to issue another reboot.

The window and all three source files retain their original descriptors through
each invocation. Compilation uses the already hash-verified first boot-helper
buffer. Path/inode/metadata/byte hashes are rechecked before request and final
writes; the request also binds their identities across reboot. An identical-byte
pathname replacement is not adopted. A read-only preflight may precede source
revision only before any request/fence exists; preserve the never-applied old
window/source and issue the revised source pins without extending its timing.

The play uses exactly one `ansible.builtin.reboot`. Its timeout is300seconds
for each of Ansible's separate boot-ID and test-command phases, with a600second
task bound, as described in the [official reboot documentation](https://docs.ansible.com/projects/ansible/latest/collections/ansible/builtin/reboot_module.html).
Only subsequent read-only readiness observations retry (one initial attempt
plus at most eight retries, five-second delays and sixty-second per-attempt
task limits, matching the [Ansible2.21 executor](https://github.com/ansible/ansible/blob/stable-2.21/lib/ansible/executor/task_executor.py#L462)). There is no automatic reboot
retry, downgrade, repair or adoption. All helper commands also share the fresh
absolute deadline and bounded output/child waits. Use the existing independent
Native durable runner with an outer deadline matching this window; running the
controller on Web would lose the controller during its own reboot.

Postboot checks require the same machine, a different boot ID and exactly
`7.0.0-38-generic`, Docker 29.9.2/containerd 2.4.2/runc 1.5.2, unchanged source and
completion evidence, and the full27-container/sixteen-running/ten-parked
profile including f79 Capture2, networks, mounts, restart policies and native
intent. The default boot helper's no-maintenance gate is unchanged: postboot
composition verifies its own exact kernel reservation while calling the same
read-only mount/runtime/definition probes. Only disappearance of the two
specific old runtime collector inhibitor paths with the exact known body hash
is normalized, and only after persistent inhibitor content and loaded
conditions are verified. All other unit/drop-in constraints remain exact.

Finalization writes `postboot-result.json`, releases only the same owned
temporary fence inodes/bytes (common reservation last), then exclusively
writes `complete.json` binding the result hash. The permanent M bytes/inode
remain intact. Failure leaves existing evidence/fences for explicit diagnosis;
absence is not cleanup proof. The kernel packet does not rehash backup archive
contents or refresh the offhost transport: the separate completed OP08 backup
proof remains its recovery prerequisite. It never reads private Env, session
or key contents. Security acceptance and application rollout remain false.

## Actual separate reboot completed, 24 October 2026, 21:51 UTC

The real operation completed after the successful OP08 transition and separately
validated persistent guards. It used controller SHA256
`d771da36489f085607f23dba4caef0222fd9ff642aa6837168ddf9b71f9b0f7c`,
playbook `3c34c35ae61af7583c59bf02b227379638071490bf8029228d9c8952307eb847`
and boot helper
`08963b564e4925ab415359b20e75a73d8302c543706e5d8477c1602ebd506c50`.
Its final window SHA256 was
`c12ea7dee9a4a6991f16575373f0312585c1dda27bdbd616b1eef2440ebae321`,
nonce `cb6162207c65a131df604041a6771f3e`, with its separate 21:42:22Z not-before
and unchanged 22:29:08Z deadline. Never-applied earlier source/window revisions
remain history; they are not the final request or reusable authority.

Native public evidence under `.local/security-web-kernel-controller-20261024-01/`:

- `completed-postboot-result.json`: SHA256
  `caf747e2ac9418b52154583a7bf9af6a70cd75ccddc94ec1703c567216376bdd`.
- `completed-complete.json`: SHA256
  `42331354b02d5922fec5ee25ca5c56fe6a0b1ee084e1062da4925f8a030ef41b`.

The complete receipt binds the exact postboot-result hash, operation, nonce and
window. Boot ID changed from `71dcaadb-7b92-473a-92ec-cda4ddfe5403` to
`0a16f404-c832-4e26-816e-302a015374d0`; the active kernel is exactly
`7.0.0-38-generic`. Readiness records 27 containers, 16 intended running, 10 parked
QA, six verified datastores and one successful internal API probe. The source's
full Docker 29.9.2/containerd 2.4.2/runc 1.5.2, f79, mount, policy, inhibitor and
native-intent gates passed. Known health incidents remain OPEN with acceptance
unwaived; one was observed unhealthy in this sample.

Ansible's retained log reports `ok=5 changed=2 unreachable=0 failed=0`, with three
readiness retries after the single reboot request. Those were observations,
not additional reboot requests. The completion receipt records owned temporary
fences released and the original permanent marker retained. The postboot result
explicitly records no package changes, application-image change, automatic reboot
retry, Writer/DNS change or capture-owner change. `operation-current.json` is the
earlier staging record and its `reboot_requested=false` does not supersede these
final completion records.

Global security/deployment acceptance remains false; App, Market and datastore
security-image activation is still pending. Autopilot/refactor remains STOP,
clean streak 0. These completed records do not extend either window or authorize
another maintenance operation.
