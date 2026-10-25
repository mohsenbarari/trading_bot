# Failed App attempt: terminal reconciliation and guard cleanup

The first Web App activation failed its unchanged public-code COW check. Its
owned rollback restored the old Web image and API200. Historical failure,
rollback intents, results and journals remain unchanged; neither the candidate
nor that failed activation is accepted. The active rollback override stays at
its original path because current Compose labels still reference it.

The original activation/recovery window expired. A new, independently reviewed
cleanup-only packet used a fresh bounded deadline and explicit historical
provenance checks. It did not alter old timestamps, monkeypatch old clock gates
or invoke activation/rollback entrypoints. Nineteen author and nineteen Root
tests passed, including exact canonical-policy AST comparisons and rejection of
the expired window by the unchanged original validator.

Root ran a real read-only preflight, then one successful exact-owned cleanup.
Fresh full Config/Env/mount/network/restart/role comparisons, imports, both APIs
and eight datastore readiness checks passed. Private configuration stayed in RAM.
Only the old Web site-operation guard and Native reservation were unlinked,
with retained-FD checks and parent fsync. The permanent empty SOURCE mutex and
permanent markers retained their identities and were not read, hashed or removed.
No container, image or application data was changed by this cleanup.

The [curated completion](evidence/2026-10-25-app-attempt-cleanup.json) binds the
actual Root receipt and source/observation proofs. Capture's preexisting identity
conflict remains OPEN; generated endpoint changes were freshly observed and
stable during the operation, with all persistent policy fields still checked.

This closes only the failed attempt's owned temporary guards. The old App is
still serving, the patched App is not activated, global security acceptance is
false, and refactor automation remains stopped with no clean cycles counted.
