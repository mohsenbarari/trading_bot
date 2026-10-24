# Immutable-image component evidence

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
