# Actual App candidate evidence — 24 October 2026

Status: built and tested; **not accepted, transferred or activated**.

[Run 40075556242](https://github.com/mohsenbarari/trading_bot/actions/runs/40075556242)
built source `19d4c2da778f8f1edc567db4c9616c2d153980c9` through the existing
security-only payload job. Product baseline remains
`ba1f7616ade57558f51bb3a7fbb903243a33aab4`; unrelated main Market changes are
excluded. Exact identities:

- Image: `sha256:3b4f58b071f3a7c51778e1f6a1ae66d03beaa401fdefee6f8fd2af69999512cf`.
- Payload tree: `02553c4ba0b6ee8032e6f1ad1083c9df6cebb70c`.
- Public manifest SHA-256: `cd2e64577576b9053b926d2e65620a98ca77b0ad71cc3100d9145b9409077c7b`.
- Public evidence artifact: `12553191740`, 190,111 bytes.
- Exported-image artifact: `12552928309`, 180,449,870 bytes; exported is not accepted.

All 56 tests across five sealed modules passed inside that immutable candidate,
offline and without runtime data. This is focused compatibility evidence, not a
claim that the full browser/API/Bot matrix has finished. Actual PostgreSQL and
observability jobs also passed; the complete workflow was still running when
these image reports were examined.

The unfiltered HIGH/CRITICAL scan changed from 48 HIGH rows in the preceding
`d0257b5a` candidate to 44 HIGH and zero CRITICAL rows. All four pip-vendored
msgpack/setuptools/urllib3 advisory rows disappeared after genuine final-stage
installer removal. This comparison concerns the merged runtime filesystem;
it is not a claim that old build-layer bytes have vanished from the image archive.
Neither scan ignores unfixed findings or uses an exception file.

The remaining 44 rows represent **eight advisory IDs**, not 44 independent bugs:

| Source package | HIGH rows | Remaining advisory IDs |
| --- | ---: | --- |
| util-linux | 36 | CVE-2026-76642, CVE-2026-78408, CVE-2026-78409, CVE-2026-78410 |
| acl | 1 | CVE-2026-54369 |
| ncurses | 4 | CVE-2025-69720 |
| systemd | 2 | CVE-2026-16742 |
| perl | 1 | CVE-2026-9538 |

The OS findings still block acceptance. Debian currently lists the relevant
[ACL](https://security-tracker.debian.org/tracker/CVE-2026-54369) and
[ncurses](https://security-tracker.debian.org/tracker/CVE-2025-69720) Trixie
packages as affected. An ACL version replacement alone does not prove caller
safety: its new no-follow APIs require adoption and have documented tar symbol
compatibility constraints. Do not mix unstable binaries or force-remove
essential libraries to make a scanner quiet.

The upstream
[systemd advisory](https://github.com/systemd/systemd/security/advisories/GHSA-jm29-p7hh-vjhv)
identifies homed/homework, and
[Perl's advisory record](https://security-tracker.debian.org/tracker/CVE-2026-9538)
identifies Archive::Tar. The candidate inventory's shared systemd libraries and
perl-base do not by themselves prove either vulnerable component exists or is
absent. Any component-absence determination must inspect the actual image and
retain raw scanner rows; no absence exception has been accepted here.

No Web/Bot image, configuration, Writer, DNS, capture owner, model or database
was changed by this CI run. Host security maintenance and the live capture2
incident remain separate open work. Refactor automation remains stopped until
security remediation and runtime verification actually finish.
