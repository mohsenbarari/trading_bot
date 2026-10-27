This is a SOURCE-only, default-unarmed recipe for one isolated Coreutils
9.7-3+gold1 Debian runtime package. It does not build or activate a product
image and does not access production. Root must review this new recipe before
committing or dispatching it. The historical C source packet stays byteexact;
its historical acceptance fields are not rewritten.

The normal GitHub-hosted workflow has a boolean execute input defaulting to
false. It admits only the exact current main or existing security remediation
branch and a fresh Ubuntu24.04 x86_64 UID1001 runner. Recipe blobs are bound to
the exact checked-out Git commit. A digest-pinned Python3.11.17 slim-Trixie
amd64 container provides the build ABI. No Ubuntu headers, compiler or library
enter the package. GH_TOKEN is passed only to the ordinary gh artifact-fetch
child; Docker children receive an explicit token-free environment.

The existing approved public ACL runtime is fetched from its exact original
artifact/run/ZIP pins. That artifact contains no development DEB. The recipe
therefore reuses the already approved Tar compiler-only SDK pattern: obtain
exact signed Debian ACL2.4 sources, build their normal SDK in this disposable
builder, install its fresh libacl1+libacl1-dev, then reinstall and verify the
UNCHANGED approved runtime DEB before Coreutils configure/link. New SDK byte,
control/version, header, library and buildinfo observations are recorded. No
new ACL runtime or SDK DEB is exported or promoted. The final package must
actually link dynamically to the approved libacl.so.1 and its exact versioned
ACL-at ABI; a static SDK archive cannot satisfy this gate.

The authenticated source chain has four exact Coreutils9.7-3 files (DSC,
original tarball, detached signature and Debian tarball), four exact ACL2.4.0-1
files, signed APT InRelease indexes and real gpgv VALIDSIG results for both
DSCs. Fixed snapshot expiry alone is relaxed; signature authentication is not.
Forky supplies source only; binary repositories are Trixie. Maintained attr2.6
private compatibility provenance is separately labelled official HTTPS plus
its exact release SHA, never falsely labelled Debian APT authentication.
All 13 original attr members are checked against the approved source packet.
The complete DSC-extracted Coreutils tree must match every approved original
source pin available in the historical 76-file manifest. Its 23 patch targets
must match exact before/after hashes; new target paths must genuinely be
absent before patching. This is self-contained in the tracked layout and does
not execute the older local-only checker or load any .local sibling source.

Three declared deltas follow the original ordinary Debian package recipe:

1. The unchanged Root-reviewed 60,929-byte C/BuildDepends/dh_autoreconf patch
   performs the private descriptor caller fix and official GNU FD0 correction.
   Actual autoreconf must regenerate configure with the new feature, and the
   generated config.h must enable GOLD_DESCRIPTOR_ACL. Source, configure,
   Makefile.in, both configured Makefiles, config.status and config.log hashes
   connect source to configure, compile and package provenance.
2. Debian9.7-3's original dh_auto_test override is a NO-OP. A separate explicit
   304-byte hook patch invokes sourcebuild.py --vendor-tests as real UID/GID
   10001. It captures actual TESTS, TEST_LOGS, TEST_EXTENSIONS and TEST_SUITE_LOG
   expansions from the two generated Makefiles, then runs unfiltered ordinary
   dh_auto_test. No TESTS override, nocheck or body exclusion is introduced.
   Original vendor test bodies are hashed before build and checked afterwards.
   Each configured .trs/.log pair and both genuine summaries are read through
   the independently reviewed bounded collector, with actual exit/status,
   full metadata/hash and physical-close/named finalization. Genuine stock
   platform/privilege/optional SKIP and XFAIL remain explicit observations;
   FAIL, XPASS, ERROR, missing results and command failure reject the package.
   dpkg success is not substituted for a GNU test result.

Mandatory additional C fixtures are separate from GNU optional tests and
allow NO skips or masks. The unchanged private-helper fixture exercises real
held O_PATH objects, FD0, access/default ACL, explicit MODE default deletion,
renamed original directories, search-only permission and general xattrs.
The new companion links the actual built libcoreutils.a and invokes genuine
GNU qcopy_acl with a real regular source descriptor0 and deliberately absent
diagnostic filenames; it must copy a real extended ACL and mode, then complete
checked descriptor/file/directory cleanup before reporting success. Both run
as real UID/GID10001. No C compiler or even syntax-only compiler was invoked
while preparing this SOURCE packet.

The finite additional CLI fixture uses the installed candidate cp/mv/install
as UID10001, with regular ACL/mode/xattr, -P/-H/-L/-s, recursive directory
access/default ACL and xattr, no-preserve mode, FIFO without opening payload,
same-filesystem rename, genuine cross-device move to /dev/shm, and install
file/directory modes. It requires different actual device IDs, exact zero
commands and checked cleanup. Candidate binary hashes must match the installed
DEB/ABI proof. It does not claim a real NFSv4 filesystem was exercised.
Kernel/libc O_PATH/fchmodat/utimensat behavior, ordinary GNU results and natural
recursion FD pressure remain actual vendor execution gates, not SOURCE proof.
No new global depth limit or fallback to caller pathnames is added.

The hosted controller requires actual capacity before pull: >=4 usable CPUs,
>=12GiB host RAM, 48GiB bounded growth plus an independent 8GiB disk floor on
workspace and Docker filesystems. The disposable builder has 4 CPUs, 8GiB
RAM+swap total, 512 PIDs, 128MiB shm, and a 256MiB executable/nosuid/nodev /tmp.
The existing narrow seven builder capabilities and no-new-privileges are
preserved. Its sole host bind is the allowlisted public context, read-only.
Repositories/source acquisition are online; bridge endpoints are then removed
and actual loopback-only interfaces verified before any SDK/Coreutils build.
Normal complete GNU tests may take time; finite outer/build/test deadlines
are explicit. There is no automatic retry, resume or reused builder.

Command output bounds are checked before list append or log write. JSON
writers incrementally bound encoded output before accumulation. Every output
has a finite explicit size, and all source/log/output descriptor finalizers
and named checks precede the final result. Only the owned stopped disposable
builder is retained on failure; the hosted VM ends afterwards. Partial logs
and a truthful failure phase are retained even if no package is published.
The workflow only uploads the exact completed 14 outputs on success (one
Coreutils runtime DEB, ordinary changes/buildinfos and finite proof JSON).
Package artifacts expire after30 days; bounded failure/capacity/log evidence
after10 days. No Docker image, SDK DEB, business data or arbitrary tree is
uploaded.

SOURCE QA is Python-only. It validates inert defaults, exact input/output
pins, bounded actual inert Python-child output, workflow/ABI scope and complete
self-contained source contracts. The collector's 20 synthetic parser tests
remain parser evidence. None of these proves C compilation, kernel behavior,
a package, a new product image, whole-image ACL closure or global security.
All security acceptance fields remain false until Root obtains and reviews
actual evidence. No Git, CI dispatch, Docker or live action was performed by
the SOURCE preparer.


Successor02 changes only hosted log ownership and the corresponding SOURCE QA.
Frozen01 is preserved as failed source evidence. Each phase now captures its
finite <=128MiB public command output in RAM through actual child/selector/pipe
closure and the post-close source guard. Only accepted known bytes are then
written exclusively; their original post-write creation full9, original
readholder and exact byte/hash binding are established BEFORE the writer closes.
The original reader is immediately registered with the outer owner and retained
through all source/output closes, followed by all named checks. Final success
records these original log creation bindings. There is no filesystem glob or
post-close frame adoption. An actually silent command may create a verified
empty output log; all ordinary input admission still rejects empty files.

A failed command retains only explicitly partial <=16KiB prefix plus <=16KiB
tail, actual return code/observed byte count/pipe closure and acceptance=false,
through the same known-byte writer. It cannot produce a successful phase or
resume/retry. Genuine inert Python-child SOURCE regressions cover failed exit,
source timestamp mutation at actual stdout close, same-byte timestamp mutation
at actual log writer close, and mutation at actual outer reader close. These
remain SOURCE lifetime tests, with no C, package, Git, Docker or GNU execution.


Successor03 responds only to actual failed VendorCI run40479759340. The
unchanged real regeneration was invoked, but a second autopoint import copied
old gettext0.19.2 macros over the release's newer Gnulib imports, then autoconf
lost four existing definitions. Authentic9.7 source threadlib.m4 and wint_t.m4
contain all four. The exact release bootstrap itself runs autopoint before
Gnulib import, then preserves those imports using AUTOPOINT=true during actual
autoreconf. GNU Gnulib manual section3.11 documents that ordering.

The third separately declared486-byte packaging-only patch sets only
AUTOPOINT=true for Coreutils dh_autoreconf. SDK build environments, the original
Cpatch and the GNU test hook remain unchanged. Every original imported .m4
file and po/Makefile.in.in is observed before and after real regeneration;
three exact critical original-source pins join to the authenticated release.
Changed generated configure/config.h/Makefiles and GOLD_DESCRIPTOR_ACL remain
mandatory. No old configure, ignored macro error, m4_pattern_allow, skipped
Autoconf/Automake, changed vendor suite or network bootstrap is permitted.
Actual owned patch-only fuzz0 reconstruction and Python SOURCE QA do not prove
autoreconf/C compilation. The failed authentic CI and all predecessors stay
preserved; Root must review a fresh complete execution before any acceptance.


Successor04 fixes failure observability after authentic CI40481949063 reached
real Coreutils configure (the previous autopoint cause was fixed) and failed
before Coreutils compilation. Its3,479,467-byte command output had been bounded
and fully captured in RAM, but old failure evidence retained only16KiB prefix
and16KiB tail, losing the intervening configure cause. Available partial bytes
show only generic configure exit1; no missing cause is guessed here.

Every failed phase now retains ALL already captured known bytes, bounded to
its unchanged <=128MiB command maximum, in an exclusive .failed.log. A separate
<=4KiB .failed.json records actual returncode, received/captured byte counts,
SHA, bound, EOF and physical pipe closure, plus acceptance=false. Timeout or
overflow remains explicitly incomplete; no beyond-bound byte is appended or
written. Both original creationframes/readers are established before writer
close and registered with the same outer owner through all finalizers. No late
pathname adoption, extra permissions, ignored error, retry/resume or gate change
is introduced. The existing success path is unchanged. Actual inert exit7 with
a middle-only cause marker, outbound overflow, and a timestamp change to an
older log at the newer metadata reader's physical close are SOURCE regressions.
The original Cpatch/SDK/compiler base, full GNU selection and suites remain
byteexact03. Root's clean active controller formatting is inherited. Prior
Root integration/pathspec copies outside the source tree are excluded from04
(their originals remain preserved); they are not new execution evidence.


Successor05 responds only to the fully retained authentic failed CI40486023971.
The real regenerated configure's private ACL AC_LINK_IFELSE test failed under
unchanged -Werror=implicit-function-declaration solely because fchownat's
unistd.h was missing. Earlier real configure already detected fchownat. The
separate279-byte0004 patch adds exactly that declaring header; the original
60929-byte Cpatch remains byteexact. The final configure.ac must match the new
separate33402-byte source hash after all four admitted patches. Official
versioned glibc2.41, authenticated ACL2.4 headers and Debian attr2.5.2 headers
map every other direct call to its existing included declaration. This is a
SOURCE header audit, never an assertion that a new C link/build already passed.

Actual owned source-only patch fuzz0 reconstruction verifies both the complete
final private-probe source and Debian rules. No Werror, typed ACL function
pointer, flag assertion, actual regeneration/GOLD/link test, library/runtime
binding, native fixture or full GNU test gate is disabled or cached away.
Root must run a fresh real CI; the complete failed04 output and all predecessors
remain preserved. No compiler, Autoconf, linker, package, Git, Docker or live
operation was executed by the SOURCE preparer.

Successor06 responds to authentic CI40488040479. Real configure and Coreutils
C compilation passed in that prior run; the additional hook refused the
gnulib selection BEFORE dh_auto_test. The old parser incorrectly required a
nested gnulib name to start test-. Official9.7 Makefile.in declares nested
uniwidth/test-uc_width2.sh, and the same source family includes unictype and
unistr. The new parser accepts only canonical relative names under the fixed
gnulib-tests scope. Absolute, traversal, ambiguous, non-ASCII, symlink, foreign
parent, hardlink, stale or missing outputs still refuse; no unreadable skip or
namespace override exists. The output Scope, canonical, summary and log suffix
implementations are byteexact05, preserving every bound/full9/physicalclose.

Before parsing, each actual <=4MiB configured Make protocol is forwarded to
the existing bounded public build stderr with scope/size/SHA and end markers.
A parser failure can no longer hide the captured selection in RAM. There is
no additional artifact/output family or unbounded accumulator. An actual
inert exit7 fixture proves complete raw bytes remain in failure evidence;
separate excessive/invalid inputs refuse before the first forward.

The primary-source audit also found an independent future SKIP metadata
mismatch. Actual builder package metadata says Automake1:1.17-4, whose basic
driver supports --collect-skipped-logs. Its generated Makefile passes no when
IGNORE_SKIPPED_LOGS is nonempty; original9.7 gnulib.mk sets that variable to1.
The older original9.7 basic driver lacks that option. Rather than assume which
existing script autoreconf replaced, the hook observes the actual generated
build-aux/test-driver before selection, logs its public binding, admits only
the two exact reviewed primary bytes, and reobserves the same hash after the
genuine command. The receipt records the actual selected driver source.

The V2 read-only Make protocol adds actual collect_skipped_logs=yes/no without
assigning or filtering any vendor variable. Parser TRS semantics derive from
the exact observed driver plus that real Make policy: PASS copies no, SKIP
copies yes for original9.7 or the configured option for1.17, and XFAIL copies
yes for BOTH. All skip logs are still independently opened/read/hashed even
when Automake omits their text from its global summary. FAIL/XPASS/ERROR still
refuse. The basic driver's four fields, full configured test/log mapping,
actual suffix/name/exit status, both exact summaries and count agreement stay
mandatory. This is output interpretation, never a test exclusion or success
waiver. Genuine unfiltered dh_auto_test exit0 remains indispensable.

Primary Automake source URLs (exact version matching the actual preparation):
https://sources.debian.org/data/main/a/automake-1.17/1:1.17-4/lib/test-driver
https://sources.debian.org/data/main/a/automake-1.17/1:1.17-4/lib/am/check.am
Original9.7 driver4879/08ff8986c3a0ce8bf32d25ec7e4752ffbce9c89589f018836c97a08b6573ac2c;
Automake1.17-4 driver5215/05d9c1057354db27b965fcd154860f938186324ccd72fdb8c5767083194c8b5a.
Both genuine old parser failures were reproduced separately using immutable05.

The original Cpatch, four source patches, SDK/runtime/ABI preparation, compiler
base, GNU test bodies/default selection, C/CLI fixtures, command/output caps,
physicalclose guards and final artifact list remain unchanged. Fresh current06
C compile, full GNU and new native fixtures/package/image acceptance remain
OPEN. SOURCE QA passes35 recipe/real inert-child tests plus27 owned parser
fixtures (including the prior20 ported to a self-contained tracked path),0
skips. No C, autotools, GNU test, Git, CI, Docker, SSH or live command was run
by this SOURCE preparer; historical real05 C compile is not relabelled as06.
