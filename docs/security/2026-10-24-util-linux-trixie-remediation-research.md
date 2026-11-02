# Trixie util-linux remediation: source research, not acceptance

Status: research only, 2026-10-24 UTC. No package was built, installed, removed, or activated. No scanner exception is proposed.

The immutable App candidate `sha256:3b4f58b071f3a7c51778e1f6a1ae66d03beaa401fdefee6f8fd2af69999512cf`, source `19d4c2da778f8f1edc567db4c9616c2d153980c9`, has 36 util-linux HIGH rows: four CVEs repeated across nine installed binary packages. The actual unfiltered candidate evidence, not an inferred package list, identifies `bsdutils`, `libblkid1`, `liblastlog2-2`, `libmount1`, `libsmartcols1`, `libuuid1`, `login`, `mount`, and `util-linux`. These rows remain open.

## Official findings

Debian currently marks Trixie `2.41.5-0+deb13u1` vulnerable for [76642](https://security-tracker.debian.org/tracker/CVE-2026-76642), [78408](https://security-tracker.debian.org/tracker/CVE-2026-78408), [78409](https://security-tracker.debian.org/tracker/CVE-2026-78409), and [78410](https://security-tracker.debian.org/tracker/CVE-2026-78410). Its fixed unstable version for 76642, 78409 and 78410 is `2.42.3-1`; 78408 requires `2.42.4-1`, explicitly linked to the later nsenter descriptor fix. The current fixed source for the complete set is therefore `2.42.4-1`. The Trixie `<no-dsa>` classification does not meet this project's acceptance gate and does not remove those rows.

Upstream [2.41.6](https://github.com/util-linux/util-linux/releases/tag/v2.41.6) is the smaller stable maintenance candidate. Its [release notes](https://www.kernel.org/pub/linux/utils/util-linux/v2.41/v2.41.6-ReleaseNotes) describe fixes for failed-helper posthooks, privileged source-path races, descriptor inheritance, and terminal banner sanitation. However, **unmodified 2.41.6 is insufficient**:

| Follow-up | Why it is needed | Exact upstream revision |
| --- | --- | --- |
| nsenter descriptor lifetime | Close the temporary cgroup FD; initialize it to -1 so stdin is not closed; close the privileged cgroup.procs FD immediately after migration, before credential changes. O_CLOEXEC alone is insufficient. | [485dbb67f1b6bb18e08b1b77f4aa2373ff3a705b](https://github.com/util-linux/util-linux/commit/485dbb67f1b6bb18e08b1b77f4aa2373ff3a705b), the v2.41 equivalent of [286dd3ff41526b582ef48830de239dffbaa61f90](https://github.com/util-linux/util-linux/commit/286dd3ff41526b582ef48830de239dffbaa61f90) |
| Missing header | Supply the openat2 resolve-flag definitions explicitly instead of depending on a transitive glibc include. | [79c2881c27a0b40889cd5433d9f125689826d1d2](https://github.com/util-linux/util-linux/commit/79c2881c27a0b40889cd5433d9f125689826d1d2) |
| Mountfd build regression | Gate idmap on the compiled mountfd feature, avoiding an unavailable helper when that feature is disabled. The disabled configuration must still build and reject unsupported idmap operations. | [233cf7321e9d0fd2cea901d0a97e565c725640ad](https://github.com/util-linux/util-linux/commit/233cf7321e9d0fd2cea901d0a97e565c725640ad) |

The official 2.41.6 tarball was downloaded with a 12 MB bound and checked against the [kernel.org signed checksum listing](https://www.kernel.org/pub/linux/utils/util-linux/v2.41/sha256sums.asc). The bytes actually contain O_CLOEXEC but omit those explicit closes and initialize `cgroup_fd` to zero. All three v2.41 follow-up patches applied to exact matching contexts in memory, without dropping context lines: 1, 3, and 7 hunks respectively. No compiler or installed binary was exercised. The corresponding signed metadata was retrieved; its signature has **not** yet been authenticated against a trusted maintainer key.

For 78409, the [upstream advisory](https://github.com/util-linux/util-linux/security/advisories/GHSA-8f2p-47x3-43mv) says affected versions start at 2.42, while Debian currently flags the older Trixie family. The actual stable tarball retains the explicit restricted-user subdirectory refusal in `hook_subdir.c`, rather than the affected newer detached-tree fast path. This discrepancy requires a binary regression and vendor/scan reconciliation; it is not authority to suppress the Debian row. Do not import the newer fast path as part of a security backport.

## Inputs pinned by actual bounded downloads

All patch URLs below are `https://github.com/util-linux/util-linux/commit/<revision>.patch`; tar URLs are in the linked official directories. Hashes are SHA256 of the exact downloaded files.

| Public input | Bytes | SHA256 |
| --- | ---: | --- |
| [util-linux-2.41.6.tar.xz](https://www.kernel.org/pub/linux/utils/util-linux/v2.41/util-linux-2.41.6.tar.xz) | 9470680 | `e596083744e746be7d2823b62b43f4418dd7bf56303b4dc09e6fe8112fe3d7ed` |
| 79c2881c patch | 1271 | `ad095c0456e71636c2b9fd9c41fb24031ad5ab66dfac9e3fd6a9f40cfcecf0ab` |
| 485dbb67 patch | 2620 | `0b30248db96c77926ab9ad33474003bb1b13d652368dc754a84ac51ec61f7220` |
| 233cf732 patch | 4383 | `6f98c72584bbc602869f663b4e1f1394cd45f8c086199241c3e2a4293f401b4e` |
| 286dd3ff patch, reference only; do not apply in addition to 485dbb67 | 2975 | `bd5b45db9dbfb340622250e92a9f35dbfd9a3574045c4e89e3d034e2391f7697` |
| [Trixie 2.41.5 source descriptor](https://deb.debian.org/debian/pool/main/u/util-linux/util-linux_2.41.5-0+deb13u1.dsc) | 4988 | `43e9b2cbebd10fdc598c4ad10217c8202c28de53af6eafc892c8e9b5cbf3a3a5` |
| Trixie `util-linux_2.41.5-0+deb13u1.debian.tar.xz` | 107604 | `5b327ccd22f0f4ed28a389870aa51d04ecedb8693e52a1d122850f2b3188cbf6` |

## Coherent package and ABI plan

1. Build in a disposable Trixie builder pinned to the same base and Trixie repository inputs. Keep the [Trixie packaging](https://sources.debian.org/src/util-linux/2.41.5-0%2Bdeb13u1/debian/) and full feature set; use only the standard `noudeb` profile to omit installer packages. Do not use `stage1`, `noverity`, `nocheck`, Sid binaries, or essential-package removal as a shortcut.
2. Replace the upstream source with the authenticated 2.41.6 tarball and apply the three pinned follow-ups above. Two Trixie quilt patches, loopdev openat2 and restricted source canonicalization, are already integrated upstream: verify their implementation, then remove only their duplicate series entries. Retain the Debian behavior, PAM, permission and installation patches. Six other Debian patches matched exact source contexts in the research check; the three patches targeting documentation/extensionless shell tests still need a complete packaging application check. That check was not simulated as a successful build.
3. Use an honest local source version, for example `2.41.6-0+deb13u0+gold1`, with a recorded patch manifest. Preserve Debian's binary transformations: `bsdutils` carries epoch `1:`, and `login` retains `1:4.16.0-2+really<source-version>`. The nine installed family packages must all come from this one build. Do not replace library files over old dpkg metadata or install unrelated optional outputs.
4. Run dependency simulation first, require no removals or unrelated upgrades, then install the complete selected family into a disposable candidate layer. Run `dpkg --audit`, package-version inventory and loader checks afterward. Preserve the existing PAM configuration, su/newgrp/mount/umount modes, and maintainer-script behavior. Never run package replacement against production to discover compatibility.

Source comparison found all six library symbol-version maps and their library Make modules unchanged between upstream 2.41.5 and 2.41.6: blkid, fdisk, mount, smartcols, uuid and lastlog2. This is useful evidence of intended ABI continuity, **not** a binary ABI result. Before accepting the rebuilt family, compare SONAME, exported versioned symbols and required GLIBC versions; run Debian's symbol checks; compile small callers against the old Trixie headers/libraries and execute the unchanged caller binaries against the new libraries. Include a read-only libmount table parser, synthetic-file blkid probe, UUID generation/parse round trip, smartcols formatting, and disposable lastlog database access. Debian's developer autopkgtests are additional smoke tests, not a substitute for old-caller compatibility.

## Required real regressions and candidate checks

- Failed external helper: use an isolated fixture and a helper that exits nonzero; assert failure and unchanged target ownership/mode, including owner/idmap options. A string search for the new guard is insufficient.
- nsenter: in a disposable delegated cgroup/namespace fixture, verify self-migration still works, stdin remains open, and neither privileged cgroup descriptor remains open before credential drop or after exec. Observe only descriptor metadata. Require a negative control against the old source; do not target another workload's cgroup.
- Restricted source/subdirectory: fixture-owned intermediate symlinks, source swaps and a synthetic procfs-style escape must not attach or modify the outside sentinel. Test supported modern-kernel behavior and fail-closed unsupported syscall behavior. Record unavailable privileges/kernel features as **not tested**, not PASS.
- Compile both enabled and disabled mountfd configurations to cover the upstream build regression; run the normal full-feature upstream test suite with its actual privileged-test coverage stated separately.
- Rebuild the App candidate with the reviewed product payload, rerun the immutable-image security regression suite, generate a full SBOM and unfiltered vulnerability report, and compare exact package-family versions and application smoke tests. No market/model behavior change is part of this work.

## Feasibility and the remaining scanner decision

A Trixie source build is plausible: this is a C maintenance release and the existing Trixie package supplies the build rules and dependencies. It is heavier than a single-library build because the family includes PAM, systemd/udev, audit, sqlite, crypto and documentation/test tooling. Perform it in CI with bounded parallelism and explicit disk/time budgets, keeping toolchains out of the final image. No measured build duration, binary ABI pass, privileged regression pass, or final image-size result exists yet.

The current Debian-based scan has no Trixie fixed-version entry for these rows. Therefore a truthful custom 2.41.6 backport may remain flagged even after source and binary tests pass. Keep the raw report unchanged. Do not rename 2.41.6 to 2.42.3, alter scanner metadata, or claim zero HIGH findings from patch presence.

If the unchanged acceptance gate requires the vendor-recognized fixed line, evaluate **a coherent 2.42.4 source rebuild against Trixie**, not a Sid binary transplant. Its [official source descriptor](https://deb.debian.org/debian/pool/main/u/util-linux/util-linux_2.42.4-1.dsc) is SHA256 `23501bc9fe62d130aaebc7d75eb9c3c8d0f7284075d1dae743a2889d15d8b692`; upstream tar SHA256 is `fbd62a100ab7bb8746ba0661255c3c48185b1e9021507c624da01fbc696330ec`. That route has a larger functional diff and **is not build-ready**: the current Sid packaging requires debhelper 14.1/compat14, so it cannot simply replace the Trixie recipe. Port the packaging explicitly and repeat full ABI/behavior tests. Do not import a newer libc or weaken build dependencies to force it through.

Next concrete step: choose and review the disposable source-build recipe with the raw-scanner limitation visible, then prove packaging application, old-caller ABI and real regressions before any deployment. This research closes neither the 36 util-linux rows nor the other eight App HIGH rows.
