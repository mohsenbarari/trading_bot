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


## Finite Source07: evidenced deep-path capability, tail cause still open

Authentic CI40491755157 ran the unchanged658-case Coreutils suite:521 PASS,
135 SKIP,1 FAIL (`tail/inotify-dir-recreate`) and1 ERROR (`rm/deep-2` setup).
Full gnulib was not reached. That failure remains preserved; Source07 does not
claim either full-suite pass, a package, a current image or security acceptance.

Root's actual controlled comparison is retained at
`.local/security-coreutils-filesystem-probe-actual-20261027-03/result.json`.
It used four real disposable cached-image observations, with actual applied
profiles AFTER START and all actual removals/CID/name absences. With both
OverlayFS and tmpfs, default docker-default made the two unchanged upstream
Perl setup expressions fail at40/52 levels with ENAMETOOLONG. Actual unconfined
made all52 levels succeed on both filesystems. Public host path_max was8192.
This proves the scoped deep-path/profile effect on that comparison host; it
never retroactively proves the future hosted runner or a full GNU test.

Only this fresh isolated hosted package-builder container now declares
`--security-opt apparmor=unconfined`. Its8GiB memory,4 CPUs,512 PIDs,48GiB growth
allowance,8GiB disk floor, exact capability set, no-new-privileges, original
network cutoff, source binds,256MiB /tmp and output/identity/finalizer guards
remain unchanged. There is no new bind/tmpfs/VM, production profile change,
global AppArmor path_max change, privileged mode, extra capability or retry.
Normal product image/runtime AppArmor policies are outside this exception.

Docker lifecycle matters: a genuinely created/never-STARTed container reports
empty AppArmorProfile even if HostConfig declares an option. That empty value
is explicitly UNKNOWN/applied-not-proven. Complete created-state metadata may
be checked only with PID0 and the zero StartedAt, retaining every other
isolation check. Running or previously started/exited observations require
actual `unconfined`; null/default/empty cannot establish an applied profile.
Source tests cover the distinction and resource/mount/capability negatives.

After creating numeric UID/GID10001 and BEFORE downloading/building vendor
source, configure now runs a finite environment preflight. A genuine10001
child reports only fixed public /proc/self/attr/current plus its actual
real/effective IDs. Source08 reads the optional numeric path_max separately as
Root, as detailed below; that Root0400 sysparam is not sent to the10001 reader.
Another genuine10001 child executes the TWO UNCHANGED upstream9.7 deep-2 Perl
setup expressions once. It must actually return0, produce no error output and
create all52 successive200-byte components. Its original bounded output/status,
physical pipe closure/reaping, held directory identities, exact reverse cleanup,
final FD closes and root absence are checked. Failures remain failures and
bounded public evidence; no ready flag, filtered case, fallback or retry grants
pass. The result remains in normal preparation/provenance. It is an environment
capability gate, not the rm test, complete GNU suite or a security proof.

The tail FAIL remains independently OPEN. Corrected direct syscall probes saw
IN_DELETE_SELF on all four profile/filesystem cases after matching relevant FD
lifetimes; that does not execute or certify GNU tail's full recheck loop. In
upstream9.7 the test waits for initial content, while tail installs parent/file
watches later, so timing/recheck is a possible cause, not a proven diagnosis of
this particular CI failure. Source07 changes no tail/test body, expected output,
wait, retry, default TESTS/TEST_LOGS or genuine platform SKIP reporting. Fresh
unfiltered dh_auto_test must still pass the complete default Coreutils AND
gnulib suites; any FAIL/XPASS/ERROR blocks export. A surviving real tail failure
needs a separately evidenced Source08 investigation, never a waiver.

Source-only QA:41 recipe/protocol/real bounded-inert-child tests plus27 unchanged
vendor parser/lifetime tests,68 PASS, zero skips. The author ran no C/autotools,
Perl52 setup, unprivileged environment preflight, GNU suite, Docker, Git, CI, SSH,
product installation or live action. Default workflow/CLI remain unarmed.

Primary references:
https://raw.githubusercontent.com/coreutils/coreutils/v9.7/tests/rm/deep-2.sh
https://raw.githubusercontent.com/coreutils/coreutils/v9.7/tests/tail/inotify-dir-recreate.sh
https://raw.githubusercontent.com/coreutils/coreutils/v9.7/src/tail.c
https://raw.githubusercontent.com/torvalds/linux/v6.17/security/apparmor/path.c
https://lists.ubuntu.com/archives/apparmor/2016-June/009779.html
https://docs.docker.com/engine/security/apparmor/


## Source08 supersedes07 before dispatch: respect public metadata permissions

Root's pre-dispatch review found that the public AppArmor path_max sysparam is
Root0400, not readable by10001. Source07 was NOT dispatched. Its unprivileged
reader would have raised PermissionError before the actual capability check.
This successor changes only that diagnostic read and its exact source pins.

The UID10001 child now reads ONLY its own profile and real/effective IDs; its
closed schema rejects extra path_max fields. A separate Root coordinator reads
ONLY `/sys/module/apparmor/parameters/path_max` with a32-byte numeric bound,
original nofollow FD, full9 before/after/read and post-close named check. The
readable value, actual Root reader and source frame are separately recorded as
`Root_public_path_max` observational metadata. A truly missing parameter is
explicit `state=not_present,value=null`; no guessed8192 or false readability is
substituted. Permission failures are not silently relabelled missing. There is
no chmod, UID/stat mask, privilege change, arbitrary-path API or sysparam write.
The actual10001 profile and unchanged52-level Perl capability remain mandatory.
All AppArmor lifecycle guards, other isolation/resources, vendor bodies/default
selections, truthful skip handling and full fresh GNU/gnulib gates are unchanged.
Tail's actual CI FAIL cause remains OPEN and is neither waived nor retried.

A new owned permission test creates only a fresh /tmp public0755 parent and a
5-byte Root0400 fixture. It retains the actual original FD/frame, reads it as
Root and launches genuine10001 children: first actual read denial, then the
corrected profile/ID-only program with no protected-file access. It uses no
mocked identity/stat/result. The author's managed environment rejected the
credential transition with real EPERM before a child started. That is recorded
as a real ERROR, not PASS or SKIP. Root must run this exact test on the actual
host after reviewing the frozen packet before dispatch.

Remaining Source QA:42 tests passed, zero skips; the one genuine permission test
is explicitly pending Root host execution. The vendor parser and its27 tests
are byte-identical to already-tested07 and were not needlessly repeated. The
author ran no actual preflight/Perl52/GNU/Docker/Git/CI/SSH/live operation. Root's
separate host-test result must not be silently folded into author evidence.

## Source09: evidenced native build workspace, unchanged full GNU gates

Actual CI08 run40497508702 reached the unfiltered658 Coreutils cases:
524PASS/133SKIP/1FAIL/0ERROR. Deep2 now passed; the remaining failure was the
unchanged `tests/tail/inotify-dir-recreate.sh`. Full gnulib was not reached.
That failure and the earlier failed observations remain preserved.

Official v9.7 `src/stat.c`456–460 deliberately marks OverlayFS0x794c7630 remote
so tail uses polling; generated `is_local_fs_type` returns0 and `tail.c`963 plus
2514–2525 silently disable inotify. The test's different `df --local` precondition
still accepts OverlayFS. Its3.1-second wait expires before the polling reopen
cadence. A direct kernel inotify probe therefore could not diagnose this case.
Exact gnulib41e7b7e... `tests/init.sh`424–430 creates its private test directory
under INITIAL_CWD and mktempd_ unsets TMPDIR, so a TMPDIR tweak is insufficient.

Root's actual two-cached-container comparison is retained at
`.local/security-coreutils-tail-policy-observation-20261027-02/result.json`,
5360/SHA2565480bf14218173d975048dca98778e1425aa7c2d32576d2affd9ffdeaaa918b2.
Actual10001/defaultAppArmor, same9.7 tail80360/ca27a636..., hard8192-byte writer
cap: OverlayFS polling/appeared tokens bothfalse, tmpfs bothtrue. Both actual
container exits0, first removals and CID/name absences completed; outer exit0
was independently observed by Root. This is a bounded semantic comparison,
not the unchanged GNU suite or proof of the future hosted job.

09 creates ONE fresh `native-build` directory inside the exact ephemeral hosted
run root, using finite sudo/mkdir --mode=0755 as genuineRoot. mkdir refuses an
existing path instead of install-d silently adopting it. An actual original
directoryFD binds dev/inode/mode0755/UID0/GID0; first5 is immutable while directory
nlink/size/times legitimately change. Exact ext-family fstatfs magic0xef53 is
required. Initial available capacity remains48GiB growth plus8GiB floor; both
floor and physical growth are checked throughout. No new tmpfs limit or reduced
large/sparse test coverage is introduced. Failed fixed public Root creation
retains bounded actual returncode/output/physical-close evidence.

The builder gets exactly ONE additional RW bind, from that held host inode to
the WHOLE `/build`. The public context remains RO; all private siblings remain
0700. Mount source/destination/RW and complete original policy stay exact. A
bounded child observes actual `/build` dev/inode/mode/UID/GID/magic before any
configure/compile and after the build; it must equal the original hostFD.
Sourcebuild already gives10001 ownership only to nested vendor directories;
the `/build` root remainsRoot0755. Both GNU suites thus use native working
directories without changed test bodies/defaults/selections or skip outcomes.

The original directoryFD survives all build/export/stop, package/source/log
physical finalizers and the final result writer; then original close and named
checks must pass. The owned ephemeral directory and stopped builder remain until
VM teardown; no recursive or production cleanup is added. Sourcebuild, vendor
parser, C patches/fixtures, AppArmor, resources,48GiB/8GiB policy, network cutoff,
source/output pins and all existing GNU/gnulib gates are byte-exact or unchanged.
No early modified test is substituted for full genuine GNU+gnulib success.

Author QA:52 selected owned Python protocol tests passed, zero skips. New cases
include genuine original nativeFD, real mode/replacement/post-close mutations,
and a real impossible-capacity refusal. Only these tiny fixtures use a declared
smaller budget; no UID/stat/magic result is masked and no hosted capacity is
claimed. The unchanged genuineRoot0400→actual10001 test still cannot run in the
managed environment (real EPERM, not PASS/SKIP); Root's fresh full53 actual-host
tests are required before dispatch. The unchanged27 parser tests were not rerun.
No sudo/Docker/build/compiler/Git/CI/SSH or live operation was executed by author.

## Source10: declared qcopy ACL transitive link dependency

Actual CI09 run40504182125 proved Source09's native workspace: all658 ordinary
Coreutils cases completed525PASS/133SKIP/0FAIL/0ERROR, including the unchanged
tail/inotify-dir-recreate case. The newly reached unfiltered gnulib build then
failed to link test-copy-acl. The authenticated log's actual command linked
libcoreutils.a and `-lacl -lattr`, with no `-lselinux`; undefined symbols came
from file-has-acl/getfilecon (freecon and getfilecon variants). This remains a
failed full build, not a GNU+gnulib success or security acceptance.

The exact official9.7 source and Debian9.7-3 source patch establish the cause:
Debian's gnulib-nfs-acl.patch adds qcopy_acl's EOPNOTSUPP fallback through
fdfile_has_aclinfo/aclinfo_free. Those functions carry configured SELinux/SMACK/
ACL library dependencies in FILE_HAS_ACL_LIB, but gl_QCOPY_ACL still substituted
only LIB_XATTR (or LIB_ACL) into QCOPY_ACL_LIB. The missing transitive dependency
is broader than a single failing executable.

All four exact gnulib ACL test link definitions were audited:

| Target | Existing dependencies after common LDADD | Dependency use |
| --- | --- | --- |
| test-set-mode-acl | LIB_ACL plus conversion/intl flags | qset/set ACL; already actually linked |
| test-copy-acl | LIB_ACL + QCOPY_ACL_LIB plus conversion/intl flags | Debian qcopy fallback now pulls file-has-acl |
| test-sameacls | LIB_ACL plus intl/conversion flags | Direct ACL APIs; no file-has-acl call |
| test-file-has-acl | FILE_HAS_ACL_LIB | Already declares its correct dependency |

New separate538-byte patch0005 modifies ONLY m4/acl.m4. gl_QCOPY_ACL now
AC_REQUIREs gl_FILE_HAS_ACL and appends the CONFIGURED FILE_HAS_ACL_LIB to
QCOPY_ACL_LIB. No hardcoded-lselinux workaround is put into a test or product
LDADD. Every existing qcopy consumer automatically inherits its real dependency.
All four prior patches, every C/test body, gnulib.mk/Makefile.am, default test
selection, parser, genuine skip handling, resource/network/AppArmor/native-FS
policy and SDK/runtime promotion boundary remain unchanged.

The exact official m4/acl.m4 original8723/fc2bbf5d... is required before patching,
and declared8955/5ad6011f... afterward. Existing original infrastructure pins
and full post-patch m4/po equality across autoreconf/build remain mandatory;
only this explicitly declared m4 metadata delta is added to source provenance.
Actual configured gnulib Makefile variables and ALL FOUR LDADD definitions are
checked/recorded before unfiltered dh_auto_test and in generated-build-proof.
The configured QCOPY library set must really carry FILE_HAS_ACL_LIB, which must
contain the detected Trixie SELinux library. An unexpanded or missing dependency
refuses early; this precheck never replaces GNU/gnulib tests or evaluates Make.

Primary inputs were bounded official public sources, with no compilation:
coreutils9.7 orig6158960/e8bb26ad... and Debian9.7-3 patch archive26820/483f7787...
are exactly the existing source recipe pins. Original gnulib.mk120565/10acc320...
and Makefile.in753410/a353ef52... carry the four unchanged link stanzas;
Debian gnulib-nfs-acl.patch18341/5b8d055c... adds the fallback. Gnulib's exact
41e7b7e... m4/acl.m4 specifies FILE_HAS_ACL_LIB += LIB_SELINUX. The source-only
cohort PRIMARY_FINDINGS.md has full pins and paths for independent review.

Author55 selected owned Python cases passed; the unchanged genuineRoot0400→
actual10001 credential case remains separately Root-only (full56 host tests
required). New tests reject the observed old missing dependency/unexpanded
flags/duplicate assignments and inspect real owned configured Makefile bytes.
The unchanged27 vendor-parser tests were not repeated. No compiler/autoreconf/
sudo/Docker/Git/CI/SSH/live operation was executed by author. Root must review,
run the genuine host tests and dispatch a NEW full build; no old retry/resume.

## Source11: use the extracted-source ownership contract for m4 pins

Actual CI10 run40508250677 refused BEFORE Coreutils configure at the new
m4/acl.m4 original check: digest() correctly requires a Root-owned public input,
whereas dpkg-source extraction and the patch child genuinely own files10001.
That was an introduced helper misuse; the authenticated failure is preserved.

11 changes ONLY the two new m4 production read/check calls to observed(), which
uses existing owned_read(): admitted0/10001 UID/GID, WORK/canonical scope, regular
nlink1, non-writable mode, expected byte bound and original full9/read/close/name
guards. Each caller still compares the exact declared bytes+SHA before/after
patching and emits a specific refusal on mismatch. Root-only digest() is entirely
unchanged. No source owner, C code, patch, default, parser, native workspace,
library dependency or genuine vendor gate changes.

The AST regression inspects the ACTUAL build_vendor function: both m4 checks
must use observed() with their expected byte bounds; no Root digest may read
anything directly derived from its extracted-source path. Tests execute those
actual check statements, not replacement copies, on owned files. WrongSHA,
group-write permission, symlink and genuine close-time timestamp mutation must
still refuse. A separate mandatory Root HOST case genuinely fchowns the m4
fixture10001:10001, validates both before/after checks, then genuinely assigns
10002:10002 and proves refusal. No UID/stat/flag result is masked.

Author's managed environment cannot perform the two genuine credential/owner
transitions. Both are explicitly omitted from the selected author run, not
PASS/SKIP and not attempted again.57 other owned Python tests passed; Root must
run all59 actual HOST tests before fresh CI. The unchanged27 parser tests were
not repeated. No compiler, autotools, sudo, Docker, Git, CI, SSH or live operation
was executed by author; full Coreutils+gnulib success remains pending.


## Source12: use the actual canonical generated config header

Coreutils9.7 configures `lib/config.h`, as authenticated configure.ac's
`AC_CONFIG_HEADERS([lib/config.h:lib/config.hin])`, the gnulib Makefile template,
and actual CI11 configure output all agree. The previously requested root-level
`config.h` is not that generated header. Source12 corrects exactly three path
references, preserving the full eight-file generated provenance and the actual
GOLD_DESCRIPTOR_ACL=1 feature checks. No cleanup behavior, lifecycle exception,
rehydrated file, fallback path, test body/filter or reader guard is introduced.

Three new owned regression methods run the actual build_vendor header-check
AST and actual generated_proof on the canonical path. A root/config.h decoy
cannot replace a missing or invalid lib/config.h. Actual bad modes, hardlinks,
symlinks and a timestamp mutation at physical close still refuse. These are
explicit source fixtures, not an actual compile/build claim.

The author ran60 selected Python methods, zero skips, excluding both genuine
Root HOST credential/chown cases. Root must run all62. Prior Source11's full
Coreutils and gnulib success is retained truthfully, but no new completed build,
export/native/ABI/image/security acceptance is inferred from it. Root source
review and a fresh ordinary CI run remain required. PRIMARY_FINDINGS.md binds
the exact public primary files and actual failed-CI log that establish this
path correction.


## Source13: preserve the exact lintian package leaf

The slim base excludes /usr/share/lintian/overrides/*, although Coreutils DEB
contains /usr/share/lintian/overrides/coreutils. Complete Coreutils installation
now includes exactly that leaf in addition to the existing doc/locale/man/info
roots. SDK/noncomplete calls retain their old arguments, and global dpkg policy
is unchanged. All-member regular_payload remains mandatory and byte-identical:
unknown/missing paths and changed bytes/modes/owners/links still refuse.

Two captured-command SOURCE methods verify the exact narrow argv, absence of
broader lintian/subtree/force flags, unchanged SDK command and mandatory failing
dpkg-audit refusal. No package is installed by these tests. The author selected
62 Python tests, all pass without skips; Root must still run all64 genuine HOST
cases. No actual new build/native/ABI/export/security acceptance is claimed.
The actual candidate inventory was not exported by the failed prior CI, so
POLICY_COVERAGE_SOURCE.json records remaining unknown classes honestly and does
not grant absence waivers. A fresh full real build remains required.
