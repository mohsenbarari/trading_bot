# Trixie ncurses vendor update: candidate evidence only

The candidate rebuilds Debian source `6.6+20260608-2` against authenticated
Trixie binary dependencies. Its honest local version is
`6.6+20260608-2~trixie+gold1`. Debian identifies the affected `infocmp` issue and
fixed release in its [CVE-2025-69720 tracker](https://security-tracker.debian.org/tracker/CVE-2025-69720).
This is an ordinary vendor update, with no local security patch or malformed
terminal fixtures. Source approval is not a completed build or security closure.

The manual `trixie-ncurses-build.yml` workflow admits published `main` or
`security/remediation-20261023` only. It has read-only repository access and no
deployment action. Exactly three published recipe blobs are sealed, bounded,
hashed and checked against the working checkout before use. No application,
configuration, session, private key or production data enters this job.

The exact amd64 Python 3.11.17/Trixie base and the dated, signed Debian snapshot
indexes are pinned in the Dockerfile. A Forky **source-only** index provides the
vendor descriptor and build-dependency metadata; there is no Forky binary index.
The four source archive sizes and SHA256 values are checked before extraction.
Maintainer signatures have not been separately authenticated; the receipt states
that limitation instead of equating it with signed-index authentication.

Only an additional changelog entry identifies the local rebuild. Debian rules
and control bytes must remain unchanged. Normal `dpkg-source -x` and
`dpkg-buildpackage -b -Pnoudeb` build the complete thirteen-package family and
any generated debug packages. No `nocheck` or alternative feature profile is
introduced. The compiler runs without networking, as UID 1001, with a read-only
root filesystem, no capabilities, two CPUs, 4 GiB RAM, bounded logs and a finite
deadline. There is no compilation on production hosts.

Compatibility checks retain the old `ncurses`, `ncursesw` and `tinfo` SONAMEs,
versioned exports and Trixie GLIBC ceiling. Callers compiled with old headers and
libraries run unchanged against old and candidate libraries; actual loaded
paths are checked. Ordinary Python curses/readline operations and existing
`xterm`/`linux` infocmp/tic round trips run for both profiles. These checks do
not claim interactive upstream-suite or exploit-regression coverage. The
receipt explicitly labels the interactive upstream suite `NOT_TESTED`.

Only the four runtime packages already present in the App base are exported:
`ncurses-base`, `ncurses-bin`, `libtinfo6` and `libncursesw6`. A separately checked
five-file public capsule preserves private artifact-directory permissions.
An isolated candidate container simulates exactly those four upgrades and
rejects removals or unrelated changes. Normal offline APT orders local packages
and their Pre-Depends; no forced installation is permitted. Clean dpkg audit and
all four exact installed versions are required before making the scan image.

The real install uses `apt-get --yes --no-remove install` with exactly those four
absolute local paths; simulation alone retains `--no-download`. Actual util-linux
CI40112121261 demonstrated that `--no-download` on the real command bypassed
local-file acquisition and produced an internal pathname error. The candidate's
mandatory `--network=none`, empty runtime APT lists, exact-four simulation and
archive hash checks preserve network isolation while normal APT acquires the
local files. No force option, ignore-missing, repository or capability is added.
This source correction applies to subsequent installation attempts. Ncurses
CI40112074921 and its evidence remain bound to their originally dispatched source;
that run failed during the vendor package build and produced no accepted package
artifact. A fresh build is required; its outputs must not be relabeled as evidence
for this installation correction.

That first run authenticated the exact vendor archives and installed Trixie
build dependencies successfully. Normal Debian packaging then failed at
`chmod -x .../README.gz`: the inherited 0077 mask excluded group/other execute
bits from a symbolic mode with no explicit owner class. Only the exact
`/usr/bin/dpkg-buildpackage` child now receives its normal public-build mask
0022 through `Popen(umask=...)`. The controller, logs and other children retain
0077. Source/build data are public and remain below the private runner parent;
no production permissions or vendor rules are changed. The mask regression
checks child scope, unchanged controller mask and 0600 log files.

The raw scanner report retains all severities and unfixed findings. Its schema
and image ID, the SBOM versions, source receipt, recipe identities and exported
package hashes must match the actual candidate. Scanner exit zero here means
evidence collection only. The final App HIGH/CRITICAL gate remains unchanged;
there is no custom-version waiver or assertion of production installation.
Public artifacts have seven-day CI retention. Failure diagnostics read only
fixed public log names, with bounded escaped tails.

Before dispatch, run the focused hermetic tests and the actual Actions parser:

```sh
APP_ENV_FILE=config/unit-test.env.example PYTHONDONTWRITEBYTECODE=1 \
  python3 -m pytest -q -p no:cacheprovider \
  tests/test_native_ncurses_build.py tests/test_ncurses_packaging_umask.py
actionlint -shellcheck= -pyflakes= .github/workflows/trixie-ncurses-build.yml
```

Initial source review: 31 hermetic tests passed, an independent reviewer found
no actionable defect, and actionlint accepted the workflow. Actual source
resolution, compilation, compatibility, installation, scan and final App
activation still require their own run-bound evidence. Global security closure
and automation restart remain blocked until the remaining production gates pass.
