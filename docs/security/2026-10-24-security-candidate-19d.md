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

## Actual component evidence from the unchanged exported image

[Run40118540234](https://github.com/mohsenbarari/trading_bot/actions/runs/40118540234),
job120944882945, successfully admitted and loaded the original exported image,
then ran the unchanged sealed component verifier in a disposable container with
no network, production mounts or runtime data. The recipe commit is
`76f1ca4f0aac5d44639383aa291d043fc6db607d`; the original image/source/payload
identities above remain unchanged. No application or OS package was rebuilt.

The image archive has31 ordered layer references, including repeated empty
layers, and31 legacy Moby v28 compatibility configuration blobs. Each legacy
blob is reconstructed byte-for-byte from the already-pinned image config and
ordered RootFS chain. Its content digest, position-dependent ID, parent and
final configuration must match exactly. Unknown extras, physical duplicate
entries, missing chain nodes and changed bytes still refuse admission. The
earlier diagnostic jobs remain failed historical evidence; this result does
not reinterpret those failures.

Public artifact12570268374 is2295 bytes, ZIP SHA256
`cfbdfdfe0a2ad75f07b6df42b6a4afb935e2e582af7c946c3a92503a92f74437`.
Root independently verified its exact three-member inventory and receipt chain:

| Receipt | SHA256 |
|---|---|
| image-admission.json |9f62e3edbb625ec2cd07a5896da3d939bf1b12fe015ea680824a3eed576759b6|
| component-presence.json |6dd1135d6cbc39e2790e0cf84018d3cd6ccaffcb8fa65428eb4f1ccad7d766a5|
| execution.json |bf31b10195a856fb29e1911eb1e7a20e1804ca60b4dedcacbdc5d80cb5626077|

The verifier examined17567 entries under the resolved roots `/app`, `/etc`,
`/opt` and `/usr`, plus all nine actual taint-mode Perl `@INC` paths. No known
systemd-homed/homework component or Archive::Tar module matched. The shared
systemd libraries and perl-base remain installed at the scanner-reported
versions; component absence was measured rather than inferred from package
names. The verifier SHA256 is
`36e92fdfd3105a9db6bc20fd75aade1238a5e0b053a581bfb24c7b5e1bf6a6d3`.

This is bounded component-absence evidence for this exact immutable image.
Renamed/obfuscated embedded copies, dynamic runtime downloads and future
images were not assessed. All raw44 HIGH rows remain retained; none was
filtered or waived. Acceptance of a composed successor needs its own image
checks and an explicit finding-by-finding disposition. This run reports
`security_accepted:false`, `deployment_performed:false` and
`scanner_findings_modified:false`.
