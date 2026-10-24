# Immutable-image component evidence

The manual `security-component-presence-reuse.yml` now offers a CI-only way to
inspect the already-built19d App without rebuilding or downloading its image
onto a production host. It admits only run40075556242/artifact12552928309 and
the exact ZIP180449870 bytes/SHA256
`a737731a99ddf2d8cb43bcfaeb0e8490c56cdb7ac751c4ea5fbdf1cde327959b`.
One authenticated download is bounded on disk; its redirect URL/token is never
logged. Exact export provenance, closed archive members, configuration digest,
RootFS layer identities and complete Docker/OCI inventory are verified before
loading. The same retained, rehashed tar descriptor feeds Docker. Even an
all-zero growing trailer cannot outlive its advertised file bound.

Only exact image
`sha256:3b4f58b071f3a7c51778e1f6a1ae66d03beaa401fdefee6f8fd2af69999512cf`
may be probed. The current Git-sealed probe is unchanged; its isolated container
has no network or production mounts. Admission/execution/component receipts
retain scope limitations, original source identity, `image_rebuilt:false` and
`security_accepted:false`. No scanner finding or policy is changed. The job has
repository/actions read access only and retains small public receipts for30days.
Source review and44 focused hermetic tests passed, including independent review
and real actionlint validation; actual CI image/probe execution remains pending.

This probe supplies evidence for two source-package-wide scanner reports. It
does not modify the raw scan, dismiss a vulnerability, or accept a release.

The actual 19d candidate scan names `libsystemd0` and `libudev1` for
CVE-2026-16742, and `perl-base` for CVE-2026-9538. The affected code is respectively
the homed/homework service and Archive::Tar, rather than every binary from those
source packages. The SBOM alone does not prove component absence.

The existing payload CI seals this public verifier from the published Git
revision before npm runs. After building, it verifies the sealed bytes and
executes that same buffer inside the exact immutable image ID. The disposable
container has a read-only root, no network, no capabilities, and no production
environment or mounts. It cannot pull a missing image. A unique name/label and
bounded cleanup identify the actual container ID before removing only that
owned container. Cleanup after a Docker-client timeout is best effort: an
absent immediate inventory cannot prove a delayed daemon request finished.
The original timeout always fails the probe and cannot produce a receipt.
Its package metadata, bounded vendor-component name/path
inventory and actual sanitized Perl `@INC` lookup produce
`component-presence.json`. The receipt records the image, source and verifier
digest; it remains an artifact even when the unchanged raw HIGH/CRITICAL scan
blocks the job.

Finding a binary, service/activation file, module, or corresponding symlink
prevents an absence assertion. Unknown, unreadable, over-budget or changing
inputs fail the probe. The inventory does not traverse directory symlinks or
claim to detect renamed/obfuscated code, future runtime downloads, or a
subsequently mounted module. Relevant image or runtime changes require fresh
assessment. No actual 19d image proof is claimed by the synthetic tests.

An eventual component-not-present assessment must cite the actual image receipt
and advisory and be reviewed separately. It is factually different from
accepting an exploitable vulnerability. It still cannot turn a failing raw-scan
gate into a pass. No VEX or ignore rule is introduced here.

Validation uses disposable filesystem and pipe fixtures: present/absent code,
vendored modules, actual Perl lookup paths, usr-merge aliases, symlink loops,
unreadable/changing/oversized inputs, mismatched identities, and output caps.
Actual image execution remains a CI evidence step.

Continuation review also covered root admission: inventory roots use `lstat`
and only ENOENT permits skipping a root. Permission errors and symlink loops
refuse the probe rather than being converted by `exists` into false absence;
both paths have isolated regression fixtures.

Primary references:

- [systemd homed advisory](https://github.com/systemd/systemd/security/advisories/GHSA-jm29-p7hh-vjhv)
- [Debian Perl advisory](https://security-tracker.debian.org/tracker/CVE-2026-9538)
- [Debian perl-base files](https://packages.debian.org/trixie/amd64/perl-base/filelist)
- [Debian perl-modules files](https://packages.debian.org/trixie/all/perl-modules-5.40/filelist)
- [OpenVEX status justifications](https://github.com/openvex/spec/blob/main/OPENVEX-SPEC.md#status-justifications)
- [Trivy local VEX support](https://trivy.dev/docs/latest/supply-chain/vex/file/)
