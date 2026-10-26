# PostgreSQL candidate: scoped Windows advisory assessment

The existing public build evidence supports **affected code absent** for
CVE-2026-39824 / GO-2026-5024 in the Linux/amd64 gosu binary inside PostgreSQL
candidate `sha256:49a811b28ed9289d4e981c8f6cd36e1cf05259653a858e40df0cb6d6413c9aa1`.
This conclusion is specific to that binary and candidate. The original scanner
row remains unchanged: `golang.org/x/sys`, installed `v0.1.0`, severity `UNKNOWN`,
`FixedVersion: 0.44.0`, and `Status: fixed`. The module version is not fixed.
This assessment grants no scanner waiver, global security acceptance or serving
activation authority.

## Upstream scope and source guards

The [official Go advisory](https://pkg.go.dev/vuln/GO-2026-5024) identifies an
overflow in `golang.org/x/sys/windows.NewNTUnicodeString`, fixed in v0.44.0.
Its [official OSV record](https://raw.githubusercontent.com/golang/vulndb/master/data/osv/GO-2026-5024.json)
explicitly limits the affected import to `golang.org/x/sys/windows`, with
`goos: windows` and that symbol. These public sources were checked on
2026-10-26; the raw scanner result was not rewritten.

The installed module version's
[tagged source](https://github.com/golang/sys/blob/v0.1.0/windows/syscall_windows.go#L1554-L1562)
places the affected function in `syscall_windows.go`. The exact compiler's
[Go 1.26.10 build-constraint rules](https://github.com/golang/go/blob/go1.26.10/src/cmd/go/internal/help/helpdoc.go#L861-L869)
make the `_windows.go` suffix an implicit Windows constraint. The
[pinned gosu source](https://github.com/tianon/gosu/blob/6456aaa0f3c854d199d0f037f068eb97515b7513/setup-user.go#L2-L7)
imports `golang.org/x/sys/unix`. Its
[original module lock](https://github.com/tianon/gosu/blob/6456aaa0f3c854d199d0f037f068eb97515b7513/go.mod)
declares x/sys v0.1.0. The reviewed build changes only the moby/sys/user lock
to v0.4.1; all three gosu Go code files remain unchanged.

## Existing authenticated candidate evidence

CI run `40142076769`, recipe commit
`ecfb667ee1583168f2777a6fa5f54766a63ef787`, produced the public evidence ZIP:
529854 bytes, SHA256
`5a56ff8c901fb7553b596cbab6d706360163e1de07b895a61a5baa4b964b361d`.
`datastore_candidate_contract.PROFILES['postgres']` pins this ZIP, candidate,
export, raw report, SBOM and ordered twelve-layer RootFS. The original image
artifact is `12578982553`: 114543515 bytes, SHA256
`7d22f13f6cb225efe325c297e777536973f7de1082da12629ca2df44a1f601da`.

| Evidence ZIP member | Bytes | SHA256 |
|---|---:|---|
| `image-export.json` | 5302 | `6e68ee177520cd4f1e2a7f1bab2c91ed8f9017d44f3a5e4d477cfc056f208e45` |
| `reports/gosu-windows-scope.json` | 494 | `2edf6524b02352aea00557898c9d329f5af29dbfda9255e1c104a6414a1481e0` |
| `reports/build.log` | 4897 | `3754711c155bf344d4e3ca987e09e0904a18f7d88136da5e3305f083a0f489e7` |
| `reports/candidate-inventory.json` | 508420 | `c73b795d770173ed9618e947fb913892632566bbaf816cbf7b14ea187bdb4d65` |
| `reports/vulnerabilities.json` | 102049 | `7ea249beb0c1d242257798b28b1d5bf12eb4796a869d37d1168f31a02a8f6891` |
| `source/identity.json` | 340 | `77f7b0f65ed0cfddc91874629f4d19f0cc2e826b753eea78989a3c37927aa90a` |

The source identity binds `compose_alpine_datastore_security.py`, 55418 bytes,
SHA256 `95e356a9f77a4377fbab137d34945a162e6faf8e1401a813c2099f23adff085e`.
Its offline readonly-module build verifies Linux/amd64 and x/sys v0.1.0,
requires the Unix package in the complete dependency list, rejects the Windows
package there, and rejects Windows-package symbols in `go tool nm /gosu`.
Only after those commands succeed does it emit the fixed scope marker.
The existing parser admits exactly one executed marker line, excluding a
command echo.

The scope record is byte-bound by the export and equals the candidate's record.
The installed candidate inventory binds the same Linux/amd64 gosu 1.19 binary,
compiled with Go 1.26.10, SHA256
`077de39980df05ec3897fbadf39a3105ddbd430e8fb714e9eb97b5bc57a8f094`.
Together, the tagged platform guard and these actual compile measurements
support the scoped code-absence conclusion. No new binary analysis, probe,
container execution or production observation was performed for this note.

The evidence continues to state `module_version_fixed: false`,
`raw_finding_modified: false`, and `security_accepted: false`. Existing role,
restore, compatibility and runtime activation gates remain independent.
