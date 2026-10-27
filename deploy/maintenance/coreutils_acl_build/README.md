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
