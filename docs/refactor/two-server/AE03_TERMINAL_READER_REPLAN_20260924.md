# AE03 terminal receipt reader: bounded restart plan (2026-09-24)

Status: **R1 BYTE READER ACCEPTED; R2/R3 AND RUNTIME UNWIRED**. The R1 implementation is `85552cdc09c4d1a94b47e2e10d09889f2b56e0c6`; `fd990e177ff4012a9408833d427694a63bf40377` below is historical task-102 context. Supervisor remains STOPPED; no runtime quota recovery, response custody, wait or replay is authorized.

## Why task 102 stopped

The Gemini Worker returned three proposals, but each pre-review test pass failed; the Pipeline then enforced its two-correction limit. Reviewer did not run, Codex applied zero proposal bytes, and Git stayed clean. None of the three Runner statuses contained `quota_evidence`. The user's report that the broader weekly Gemini credit is exhausted is therefore an external constraint, not a quota conclusion inferred from this unit.

| Attempt | Immediate test failure | Fixture lesson |
| --- | --- | --- |
| Worker 0 | A hardlink was created *inside* the scanned directory under an invalid filename, so filename rejection correctly happened before link-count rejection. A global `lstat` mock tripped the existing scanner rather than the later reader check. | Put the second hardlink outside the scanned directory. Target the intended phase with a scoped hook, not an unqualified call count. |
| Worker 1 | A global `fstat` fault fired in the scanner/directory phase while the test asserted a file-phase error. | Match the exact opened file FD or a narrow pathname/phase; never assume the Nth global call belongs to a new helper. |
| Worker 2 | The test referred to undefined `failing_dir_fstat` instead of its defined `failing_fstat_dir`. | Run local syntax/name checks and split fault cases into separate small tests before expensive full-suite execution. |

Static Codex inspection of the unapplied Worker 2 code found another gap: file post-read comparison checked inode/size/mode/owner/link count but not `st_mtime_ns` and `st_ctime_ns` (or an equivalent full fingerprint). Passing the visible tests after fixing the typo would not alone justify acceptance.

## Smaller independent units after Gemini credit returns

1. **R1: descriptor-bound bounded bytes only.** Implement one read-only helper for the exact existing `terminal-quota/<32-hex>.json` path. The directory must already exist. Use the accepted 64-entry scanner, then pin a no-follow `O_DIRECTORY` FD and open the exact basename with `dir_fd`. Before/after `fstat` and directory-relative `stat(follow_symlinks=False)` must agree on device, inode, size, mode, owner, link count, `mtime_ns` and `ctime_ns`; enforce 0700 directory, 0600 regular single-link file and <=32768 bytes. Read at most 32769 bytes, close **both** FDs on every exit, reject any path replacement. Return bytes only; no JSON or quota interpretation.
2. **R2: strict payload validation only.** Given already bounded bytes and the expected request ID, parse UTF-8 with unique keys and require byte-for-byte canonical JSON (`allow_nan=False`). Verify exact receipt/request/response keys, schema, filename/request ID, finite timestamps, canonical SHA256 of full request and response, `COMPLETED` exit 1, and exact `detect_quota_response` evidence. This is still observation, not replay authority. Test pure corruptions separately from filesystem faults.
3. **R3: one composition test.** A thin `read_terminal_quota_record` composes R1 and R2; prove real publisher-to-reader roundtrip and one missing/current-task-drift case. Do not retest every R1/R2 fault in a monolithic method.

Each unit gets a fresh, narrower semantic identity and assignment **only after** the failed task 102 is explicitly recorded as exhausted and no code applied; it must not be a renamed replay of task 102. Gemini Worker and independent Reviewer must both finish, then Codex checks semantic safety, exact bytes and independent regression tests before commit. No provider call is needed while weekly credit is exhausted.

## Negative tests that must be unambiguous

Use one fixture and one scoped fault per test. Place extra hardlinks outside the scanned directory. For a post-scan directory replacement, inject after the scanner returns; for a post-read replacement, inject at the file read; do not disturb the scanner itself when asserting the reader's later error. A targeted `os.fstat` fault should distinguish directory FD from file FD by `stat.S_ISDIR` and the exact FD captured from the intended `os.open`. Restore mocks via context managers, not `monkeypatch.undo()` mid-test. For FD closure assertions, restore the real `os.fstat` before checking `EBADF`, or spy on exact close calls. Under root, mode 000 is not proof of unreadability; assert mode bits explicitly.

Until R1–R3 and later serve/recovery tests pass, the existing publisher remains unwired and no terminal receipt may be treated as a resume permit.

## R1 handoff contract (prepared offline; do not submit during credit exhaustion)

The next Gemini assignment is **R1 only**, not a corrected replay of task 102. Bind it to the integration HEAD and current maintenance assignment at dispatch time; the historical `fd990e17` base above is diagnostic context, not a reusable SHA. The only permitted product change is an isolated byte reader in `scripts/two_site_provider_broker.py`, with focused tests in a new `tests/test_two_site_provider_broker_terminal_read_bytes.py`. Do not edit `serve`, `_submit_request`, Monitor, Supervisor, quota detection, the publisher, or inventory. The result must be bytes or a fail-closed `BrokerError`; it must not parse JSON, create the terminal directory, remove files, or grant retry/resume authority.

1. Accept only a full-match 32-lowercase-hex request ID and derive exactly `terminal-quota/<id>.json` under `broker_paths(canonical_root(root))[0]`. Reject absent/unsafe directories; never call `_terminal_quota_directory`, because that helper creates one. Reuse `scan_private_record_directory` as the bounded whole-directory admission gate and require the target basename in its result. Preserve its 64-entry and 32,768-byte caps.
2. After the scan, open the existing directory with `O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC`, verify its owner/mode and `(st_dev, st_ino)` against no-follow path observations, and hold that FD until the file read finishes. Open the exact basename relative to that FD using `dir_fd` and `O_RDONLY|O_NOFOLLOW|O_CLOEXEC`. `fstat` and `stat(..., dir_fd=directory_fd, follow_symlinks=False)` must agree on file identity, owner, regular type, mode `0600`, single link, size, `st_mtime_ns` and `st_ctime_ns` before **and** after the bounded read. Reject directory/path replacement, including a replacement between scan and open.
3. Read at most `MAX_RECORD_BYTES + 1` bytes; reject a byte over the cap or a short read that contradicts the pinned file size. Close both FDs on every success and failure path, including `open`, `fstat`, `stat`, `read` and final path-validation faults. Do not broaden generic exception handling so programming errors look like ordinary rejected records.
4. Give the Worker the existing scanner/fsync/publisher implementation and the three focused test files, plus a test-design checklist before it edits. Each negative test must target **one phase**: invalid ID, missing/unsafe directory, invalid extra entry, file symlink, external hardlink, oversized file, post-scan directory swap, post-open file swap, post-read metadata drift, exact file-FD `fstat`/`read` fault, and both-FD closure. A second hardlink goes **outside** the scanned directory. Inject only after the existing scanner returns when testing reader-specific races. For closure, restore the real `fstat` before checking `EBADF` or spy on exact descriptors. No global call-order mocks and no `monkeypatch.undo()` halfway through a test.
5. Before a Provider call, locally validate task JSON, allowed paths, exact HEAD/assignment binding, and that the requested test scenarios are reachable under root permissions. After Worker output, the standard independent Gemini Reviewer must inspect the full materialized diff; Codex then checks the complete fingerprint, test reachability and no runtime wiring, runs focused and existing Broker suites, and commits only accepted bytes. Any correction must address the *specific* failed assertion; a fourth try disguised as a new task is forbidden.

R2 and R3 are separately dispatched only after R1 acceptance. This handoff is preparation, **not** acceptance or evidence that Gemini credit has reset.

## Task 103 Codex rejection (2026-09-24 UTC)

The new R1 task `AE03-TERMINAL-BYTES-20260924-103` was genuinely distinct from exhausted task 102. Gemini credit was confirmed with one bounded pin-verified probe. Worker needed two corrections for fixture failures, then its third proposal passed 88 pre-review tests; the independent Gemini Reviewer returned `VERIFIED`. Codex validated the exact Worker/Reviewer/proposal bindings, but **rejected the proposed code before application**. No tracked source/test bytes were applied and the integration tree stayed clean.

Two isolated fault injections against the proposed module, executed in memory with temporary root-owned files, contradicted R1's own safety requirements:

1. Raise `OSError(EIO)` on the file descriptor's `os.close` before it closes. The helper suppresses that error in `finally`, returns successful bytes, and leaves the descriptor open. This is not proof of closure on every path; the close failure must fail closed and the other descriptor must still be closed.
2. Let the existing scanner finish, then atomically replace the target file with a different valid `0600` single-link file before directory-relative open. The helper returns the replacement bytes. Its first target-file fingerprint is taken only after opening, so it cannot detect this scan-to-open swap. Latch the target's no-follow identity before the scan and compare it with the opened descriptor and final path, or use an equivalent bounded design that demonstrably rejects the same test.

## Task 104 Codex rejection (2026-09-24 UTC)

Task `AE03-TERMINAL-BYTES-REPLAN-20260924-104` reused rejected Gemini helper as reference, not accepted code. One fixture correction was needed; 85 tests passed, Gemini Reviewer returned `VERIFIED`, and formal bindings passed. Codex repeated both task-103 counterexamples in memory: scan-to-open replacement now raises; a file-close failure raises and still attempts directory close. No proposal bytes were applied.

Final review found that post-read directory-relative `os.stat` compares `file_stat_after.st_mtime_ns` and `st_ctime_ns` to themselves, not to `file_fstat_after`. A scoped fault changing only the second directory-relative stat's `st_mtime_ns` returned successful bytes. This does not claim an ordinary filesystem exploit; it disproves the promised post-read comparison. Task 104 is rejected. Next unit must add a focused negative test, compare both timestamps to `file_fstat_after`, preserve the two earlier counterexamples, and avoid runtime wiring or aliases. R2 and R3 remain unopened.

## Task 105 R1 accepted (2026-09-24 UTC)

Task `AE03-TERMINAL-BYTES-STAT-20260924-105` used task 104's rejected patch as labeled context, not approved code. Worker 0 and independent Reviewer 0 passed without correction; formal bindings and applied bytes matched proposal SHA `21a51923257b36008edfe49a47115d401c2b54eb5d0c387a9de710caae43d428`. Codex reviewed the complete diff, ran all five Broker suites (87 passed), and independently faulted the second directory-relative `stat` for both nanosecond timestamps; each raised `BrokerError`, while normal bytes read succeeded. Accepted as isolated R1 in `85552cdc09c4d1a94b47e2e10d09889f2b56e0c6`. No runtime call site, receipt JSON validation, wait, or replay was added. R2 strict payload validation and R3 composition remain open.
