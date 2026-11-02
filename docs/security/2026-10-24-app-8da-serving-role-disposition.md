# App 8da: per-finding evidence and conditional serving-role assessment

This dossier reconciles the unchanged 44 HIGH rows for
`sha256:8da3e404d01cc239606d1c79a30c4701c5a2ef2d103787b68b9ec221ae723672`.
It creates no scanner exclusion, acceptance receipt, deployment permission or
runtime eligibility. The raw gate failed. The candidate is not bound to the
observed serving containers, and the ACL row remains **under investigation**.

The immutable data is in
[finding-dispositions.json](evidence/2026-10-24-app-8da-finding-dispositions.json)
and [source-closures.json](evidence/2026-10-24-app-8da-source-closures.json).
These are evidence records, not an executable admission policy. Every required
condition in the record is currently `verified_for_actual8da_runtime: false`.

| Advisory | Raw HIGH rows | Exact-image evidence assessment |
| --- | ---: | --- |
| CVE-2026-76642 | 9 | Fixed upstream source: selected packages match the verified 2.41.6 build and recorded follow-ups. |
| CVE-2026-78408 | 9 | Fixed source, including stable follow-up `485dbb67` closing both cgroup descriptors. Privileged kernel coverage remains NOT_TESTED. |
| CVE-2026-78409 | 9 | Source-specific not affected: the affected fast path starts in 2.42; the exact 2.41.6 functions retain restricted refusal. |
| CVE-2026-78410 | 9 | Fixed 2.41.6 restricted-source binding path; privileged kernel coverage remains NOT_TESTED. |
| CVE-2025-69720 | 4 | Fixed vendor source: ncurses 6.6+20260608-2 contains the earlier infocmp bound fix. |
| CVE-2026-16742 | 2 | Required homed/homework components absent in the measured exact-image paths; shared libraries remain installed. |
| CVE-2026-9538 | 1 | Archive::Tar absent in the measured known paths and actual Perl INC scope; perl-base remains installed. |
| CVE-2026-54369 | 1 | Under investigation. Only a conditional assessment of the five serving roles below is proposed. |

Upstream identifies 2.41.6 as a patched release for the
[failed-helper issue](https://github.com/util-linux/util-linux/security/advisories/GHSA-m25x-3hj9-m26f)
and [restricted-source issue](https://github.com/util-linux/util-linux/security/advisories/GHSA-rh77-686x-2f2m).
The separate [cgroup follow-up](https://github.com/util-linux/util-linux/commit/485dbb67f1b6bb18e08b1b77f4aa2373ff3a705b)
is pinned in the actual build. The
[78409 advisory](https://github.com/util-linux/util-linux/security/advisories/GHSA-8f2p-47x3-43mv)
defines the affected version range. The actual benign compiled contract from
run `40132051114` binds the old `3a799ad9…` build receipt and `111baa63…`
libmount DEB: both original macro variants compile/run 0/0, and both deliberately
missing-guard controls compile/run 0/10. These harmless stubs perform no mount,
namespace or privileged kernel operation; they do not replace that untested
coverage or rewrite the old OPEN receipt.

The component scopes follow the
[systemd advisory](https://github.com/systemd/systemd/security/advisories/GHSA-jm29-p7hh-vjhv)
and [Perl tracker](https://security-tracker.debian.org/tracker/CVE-2026-9538).
The [ncurses tracker](https://security-tracker.debian.org/tracker/CVE-2025-69720)
records the earlier fixed source. The JSON preserves the exact versions,
per-row package/PURL/digest, source inputs and original build limitations.

All 44 raw rows have an empty `FixedVersion`. Trivy 0.76.1's Debian detector
[reports that branch before comparing the installed version](https://github.com/aquasecurity/trivy/blob/v0.76.1/pkg/detector/ospkg/debian/debian.go).
That explains why the source evidence and the raw rows can coexist; it does not
authorize suppression or make the raw gate pass.

The ACL update supplies new APIs; it does not repair legacy caller behavior.
The [maintainer's advisory](https://www.openwall.com/lists/oss-security/2026/06/29/1)
explains that the old pathname APIs retain their compatibility behavior and
callers must adopt the new interfaces. The
[Debian tracker](https://security-tracker.debian.org/tracker/CVE-2026-54369)
marks the 2.4 source update; this is not evidence that every old caller migrated.
The tar packaging fix addresses a
[symbol-name collision](https://bugs.debian.org/1141146), not adoption of those APIs.

The actual sealed ELF receipt records legacy imports in `cp`, `install`, `mv`,
`sed` and `tar`; `useradd` and `usermod` also depend on libacl. Those consumers
remain present. Static symbol inventory proves neither safe call-site flags nor
the absence of dlopen, ctypes, static embedding or obfuscated code. No whole-library
FIXED or removed-consumer claim is made.

The owned-source evidence checks all 857 SHA-verified public Python files from
payload manifest `cd2e6457…`, without importing or executing application code.
Conservative import closures, including parent packages and all owned branches,
contain 274 modules for `main.py`, 326 for `run_bot.py`, and 100 for
`core/sync_worker.py`. No owned ACL/xattr/native/process/shell/file-copy dispatch
or symlink/hardlink creation was found in those closures. Eleven dynamic imports
in the API/Bot closures are literal reporting packages, listed in the record;
their external internals are not a whole-program proof. Public upload code uses
generated UUID paths and ordinary file writes; it does not invoke those CLI tools.
The exact image/source binding is inherited from the composition's unchanged
App/Python checks, and must be verified again at actual admission.

The proposed ACL scope contains exactly these launch roles:

| Role | Fixed command |
| --- | --- |
| Native App | `sh -c 'exec uvicorn main:app --host 0.0.0.0 --port 8000 --workers 2'` |
| Native Bot | `python run_bot.py` |
| Native sync worker | `python -m core.sync_worker` |
| Web App | `sh -c 'exec uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4'` |
| Web sync worker | `python -m core.sync_worker` |

The proposed contracts require the recorded service/project identities,
`/app` working directory, empty entrypoint, private PID namespace and
`Privileged=false`. Configured user is empty, meaning the image defaults to root;
absence of privilege is **not** a justification. Current boundary receipts concern
old `bb6398…` Native and `3ec6bb…` Web images, not candidate 8da.

Admission has three distinct phases. Before each target handoff, under fresh held
fences, the validator checks the admitted candidate and planned known command,
Config/mount, RAM-only loader allowlist, path and complete writer contracts. Peers
remain on explicitly verified baseline or candidate contracts for that phase;
they do not all need to be 8da before the first handoff. Immediately after each
handoff, the validator checks the actual changed target and role behavior before
advancing. Failure requires the separately authorized owned rollback while fences
remain held. Only final global completion requires all five actual candidate
roles and the complete matrix. This dossier grants none of these authorities or
per-target eligibility; one target's success cannot create global acceptance.

An existing candidate activation validator must enforce the following fresh
predicates at the applicable phase. The JSON names each predicate and preserves
its unresolved state.

1. Admit the exact exported 8da artifact and verify immutable image/config,
   ordered RootFS, source provenance and unchanged App/Python bytes.
2. Check each target's planned exact role and launch tuple before handoff, then
   its actual candidate contract immediately afterward. Require all five actual
   candidate roles at global completion; reject a missing role, changed command,
   unknown project or phase-inconsistent image.
3. Compare complete mount inventories. Reject code/library/interpreter overlays,
   host-root/daemon exposure and unknown mounts; both inference snapshots stay
   read-only.
4. Use a trusted RAM-only configuration validator to reject unapproved interpreter,
   loader or plugin overrides, including PYTHONPATH/PYTHONHOME/PYTHONSTARTUP/
   PYTHONINSPECT and LD_PRELOAD/LD_LIBRARY_PATH. Private values are neither output
   nor hashed. The current parity helper alone is not an implemented allowlist.
5. Obtain complete writer inventories before/after each handoff. Only declared
   App/Bot roles on explicitly verified baseline/candidate contracts for that phase
   may write uploads/audit; unknown writers or incomplete observations invalidate scope.
6. Verify root-owned, non-symlink, non-022 path ancestors and stable retained-FD/
   named identities. Upload trees must have no symlink, hardlink, nonregular entry
   or untrusted writable directory. Recheck before/after; do not expose user paths
   or contents in diagnostics.
7. Verify fresh candidate public-source parity, including no working-layer shadow,
   new owned dispatch, configurable dynamic import or link-creation path.
8. Reconcile every fresh raw row and all static/component/vendor/contract pins.
   A new HIGH/CRITICAL row or changed input requires a new assessment.
9. Complete the actual API/Bot/browser/import/database/backup matrix and existing
   host gates. The 56 focused composition tests are not that complete matrix.
10. Obtain a fresh independently reviewed acceptance/window/fences. The old consumer
    deadline `2026-10-24T01:30:00Z` stays expired and immutable.
11. Obtain root review of the full evidence and actual conditions. A proof hash,
    successful export or proposed disposition does not create acceptance.
12. Restrict the assessment to these serving roles. Administrative shells/CLI,
    ACL metadata work and untrusted-tree operations with the installed consumers
    require separate safe-caller assessment or remediation.

The existing `security_consumer_contract.py` target/provenance/configuration
checks are useful seams for a separately reviewed successor. This dossier changes
none of those validators, deadlines, raw gates or role controllers. Checks above
are required future enforcement, not a claim that the old validators already
implement them. Source, mount, writer, interpreter, library or operator-use drift
invalidates the conditional assessment. Third-party native internals, arbitrary
runtime code and operator routes remain outside the measured owned-source proof.

Exact controls are preserved in the JSON; the principal pins are:

| Control | SHA256 |
| --- | --- |
| Raw Trivy report | `77e85632e55ded98366e799c0f166a0b6ab434a9e71f2b728aac5e452a9b7192` |
| SBOM | `5f05e26c9dc2d5d406398979d750ce71177dbde85033356ac70f186631ab6f80` |
| Composition receipt | `8fbcad3c416a179984ff744cd166f1279cd1af97cb85892ce99c58f8e1b74797` |
| util-linux build receipt | `3a799ad92549c9d3c5e18013463743d9bc2b721eb83557e5ee3a0273d4514dde` |
| ncurses build receipt | `0903c9fbcd69d5dbc6e381f2c84d2718f85899e23c375de2e1c8ec57e81483fd` |
| ACL/tar combined receipt | `c0114c344ec6103c1d8053421f5e63dc06721b16d8e318f21cde95608d9b465c` |
| Compiled benign util contract | `dd5cad2d7e9d6ef6f29ebf0951aa2e622c1bb5f8e3e326bc15bf8b2b21bdd170` |
| Static ACL consumer receipt | `ffa950a05c00f512872e9fd54048f9f7aa37bc2a7308600454b6d55c3ff1c5dc` |
| Component presence receipt | `6567331b1110a359c9dfbea1b274b524c7e8675e5a471d88fe07f0b1d9dd21ba` |

The record retains original OPEN, NOT_TESTED and false values for signatures,
privileged/kernel/interactive coverage and full-runtime gates. It performs no
private Env/data read, image download, activation or production change. All raw
findings and the failed raw gate remain intact; global security eligibility and
deployment eligibility remain false.
