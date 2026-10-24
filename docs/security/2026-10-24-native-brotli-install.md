# Native Brotli: stage, two-package install, and deferred consumer refresh

SOURCE ONLY. This packet has not installed packages or restarted anything.

Actual read-only host proof on 2026-10-24 found Ubuntu 24.04 with systemd PID1,
15 retained containers, and only `libbrotli1` / `python3-brotli` installed at
`1.1.0-2build2`. CLI and development packages are absent and will remain absent.
The complete four-package CI build remains input evidence, not host scope.

Candidate CI `40076826664`, control `3eb2e743`, passed the actual C/Python API,
ABI and inventory checks. Its public package ZIP is 1,098,270 bytes, SHA256
`1972bc04862562082112fe27fabc0dd74cea7701e862be9887bd78218efc9944`.
The separate raw scanner ZIP remains retained and unfiltered; no row is hidden.

The two existing native consumers demonstrably map the old embedded decoder:
`coin-public-market-telegram.service` PID524702 and `golden_bot.service` PID524800.
Both had five mappings matching the actual host Python module device/inode and
unchanged process start ticks. The collector has a private systemd mount
namespace; that did not exempt its mapping. The bounded scan covered 332 PIDs
with no incomplete reads. PID values are observations, not future authority.

## Package transaction

Use `brotli_native_install.yml` with the existing Native inventory. Default and
Ansible check mode perform only read-only admission. `stage` creates one new
private directory under `/srv/trading-bot/backups`, extracts only the two pinned
candidate debs, and runs authenticated normal apt download for the exact two old
Noble packages. The old debs are hash/size/Package/Version/Architecture checked
before the transaction is admitted. All paths, files and output sizes are bounded.

`install` rechecks the same boot, frozen 15-container identities, metadata and
mount records, rollback debs, globally clean dpkg/pending-trigger state, package
locks, baseline versions and exact two-package simulation. It holds the existing
production marker flock throughout and preserves its payload and inode; foreign
production/source/reservation fences block the operation. It then invokes normal
`dpkg --install` on only the two verified local debs, matching the actual CI
installation method. No apt install hooks, package removals, toolchain install,
Docker operation, service restart or repository update is requested.

The candidate library has only an ldconfig trigger. Python package scripts only
compile/clean packaged Python files; independently inspected scripts contain no
daemon commands. An exclusively created, exactly owned `policy-rc.d` returns101
and is removed only when its content/inode still match; preexisting foreign
policy is refused. `NEEDRESTART_MODE=l` also remains set. This does not invent a
guarantee about arbitrary ambient dpkg configuration: clean-state admission and
unchanged consumer/container identities are checked afterward. Unknown or failed
states remain incomplete. Nothing kills another package-manager lock owner.

Post-install checks require exact selected versions, clean dpkg audit, package
file verification, unchanged runtime and a fresh isolated Python/C API proof.
The new decoder's error enum is checked, alongside a synthetic round trip.
The receipt still says `on_disk_patched_consumers_pending`, security incomplete,
because the running services retain their pre-install mapped code.

`rollback` uses only the two authenticated old debs. It admits only the matching
owned staged/install receipt and same boot/runtime, allows no unrelated dirty
package recovery, and never reinstalls/removes CLI/dev or restarts a daemon.
Rollback means security remains open. A power loss/SIGKILL or unknown package
state requires reviewed owned recovery; the packet does not adopt such a state
or manufacture a successful result.

## Separate consumer-refresh gate

The old overnight window expired. This packet contains **no refresh action**.
The final reviewer must approve one concrete new window before a separate
reviewed Ansible refresh of only the two identified services. Before that action,
require the valid package receipt, current patched on-disk package/API proof,
fresh package/production locks, exact Native identities and new window bounds.

Refresh one service at a time through systemd, retaining original enablement and
restart policy. Prove old process ownership has ended, new MainPID/start-time is
stable, the service is active, and its `_brotli` mapping matches the new host
package inode. Recheck real collector/Golden readiness and that the original
old decoder inodes are no longer mapped. Unknown additional consumers or failed
readiness keep runtime remediation open. A fresh healthy instant is insufficient
to declare the whole security programme complete.

Until that separate refresh is reviewed and executed in its new window, all
package receipts and reports must explicitly remain partial. Keep rollback
artifacts through the window and set a cleanup review within ten days; nothing
is automatically deleted by this packet.
