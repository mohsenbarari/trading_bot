# AE00 retained accepted-work cost report (741 proposal)

CODEX-authored proposal, not Source acceptance or AE00 completion. The public
`scripts/two_site_accepted_work_cost_report.py::summarize_accepted_work(root, *,
window=None)` composes the accepted private file/Git owner and complete740 proof
join. It grants no startup, replay, reservation, Stage, billing, savings or
Provider authority. Existing normalizer, selector and legacy19 report remain
unchanged; accepted source prerequisites are not reopened.

## Owner and history boundary

One `_proof_snapshot(root)` and one outer `_invoke` cover the complete proof
join, full retained metrics, normalization, optional membership, windowing,
allocation, hashing and output. No second reader, constructor, callback, path
discovery, write, clock reset, Git command or cap override is added. A private
`_Collector` subclass calls the original `aggregate` unchanged, then retains only
sanitized original proof/controller/ordinal/revision associations whose logical
work survived its full connected-conflict quarantine. A `BOUND` alias alone is
not an acceptance event. The740 private facade and exact output remain unchanged.
All history is proved/normalized and all membership declarations validated before
window filtering. Return values are detached, source-free observations valid
only after normal context exit; the owner still performs its final verification.
Output canonical JSON is at most2,000,000 bytes, reserving the owner's maximum
seven-digit byte counter before successful close. Memory/recursion exhaustion is
fatal `COUNT_LIMIT`, not an UNKNOWN partial report.

`window=None` means the complete **retained** ledger, not lifetime history.
Otherwise window is an exact builtin dict `{start_utc:int,end_utc:int}`: nonbool
Unix UTC epoch seconds `0 <= start_utc < end_utc <= 1000000000000`, half-open.
Invalid input raises `AcceptedWorkCostReportError('INVALID_WINDOW')` before I/O.
Finished timestamps accept finite nonnegative numeric values at most1e12 OR
exact ASCII `YYYY-MM-DDTHH:MM:SS[.1..6digits](Z|+00:00)`. Pure datetime validation
checks the actual date/time, then exact whole epoch seconds plus microseconds
become a rational; there is no local timezone, float timestamp or wallclock.
Naive/nonUTC/whitespace/malformed/boolean/pre-epoch/out-of-range timestamps are
unknown. This does not parse duration fields. Arbitrary printable legacy strings
are not timestamps. Nonfinite JSON still fails the strict whole-ledger parser
before windowing and cannot be downgraded to unknown time.
The window never controls which proof/conflict records are validated.

An acceptance event comes only from a genuine surviving chain's original
controller and its exact matched metrics ordinals. All those originals must
agree on a valid supported UTC timestamp, otherwise event time is null. Alias times,
commit author/committer clocks and proof-file times confer no acceptance time.
Full-retained mode counts even a genuinely proved event with unknown time.
Finite work placement uses FIRST_RETAINED_CORROBORATED_ACCEPTANCE: the earliest
genuine original-controller event of that logical work, never any later source
revision. If ANY genuine event of that work has unknown time, finite placement
is unknown rather than assuming the earliest known event. This is not lifetime
first acceptance. Each selected logical work counts once across all revisions.
Handler/scenario coverage is separate: finite mode counts only distinct attested
scenarios witnessed at a selected first-event commit; full mode retains all.
No expected five-work/seven-scenario constant or synthetic historical proof is
used. Historical evidence tiers and precise UNKNOWN reasons remain those of740.

## Exact wire

Top-level exact16 keys:
`schema_version,state,snapshot,window,proofs,accepted_behavior_count,
handler_scenario_count,denominator_state,acceptance_events,costs,timing,
token_per_accepted_behavior,billing_savings,value_state,cohort_comparability,
limitations`.
Version1; state `OBSERVATIONAL_ACCEPTED_WORK_COSTS`; billing null; value/cohort
`NOT_ESTABLISHED`. Count is observed corroborated retained logical work, not a
new Root acceptance or full-history denominator. Private proof snapshot metadata
remains ATCALL. Public `snapshot` retains those exact files/objects/identity,
but updates ONLY `bytes_read` after NORMAL owner exit from the SAME retained
plain-int counter: it includes final identity verification stdout/stderr.
This is O(1), no I/O/query/reset/new owner/deadline; final custody stabilizes the
other metadata and any abnormal/poisoned exit returns nothing. `proofs` contains
the unchanged full-history740 fields
`works,handler_scenarios,run_bindings,unknown,retention_limits` (not a filtered or
weakened join). `window` is exactly `{kind,acceptance_rule,start_utc,end_utc,selected_ordinals,
unknown_time_ordinals}`, with kind `RETAINED_FULL` or `UTC_HALF_OPEN`; full mode
has null bounds and no unknown-time bucket. `acceptance_rule` is always
FIRST_RETAINED_CORROBORATED_ACCEPTANCE.

`acceptance_events` is sorted by work/commit/controller/semantic and contains
exact `{work_fingerprint,semantic_fingerprint,accepted_source_commit,
controller_run_id,metrics_ordinals,accepted_at_utc,evidence_level}`. It records
all genuine retained events, even those not selected. Ordinals are sorted unique
snapshot-relative positions, not stable global IDs. Repeated handler proofs of
one work/revision/controller deduplicate. `accepted_at_utc` is rational or null.

Every rational is exactly `{numerator:int,denominator:int}`, reduced,
nonnegative, denominator positive, zero0/1. Integer inputs are exact; finite
Python floats use their exact binary value (`Fraction(value)`), not decimal
rounding. No floating-point allocation or tariff conversion occurs.

`costs` is exactly `{input_record_count,distinct_record_count,
duplicate_record_count,rows,totals,direct,shared,unallocated,per_work,membership,
allocation_state,legacy_observation,retained_totals,excluded,unknown_time}`.
`legacy_observation` is the exact unchanged19-field
`summarize_work_lineage_ledger(ALL original retained records)` result. It is not
rewritten with denominator authority or windowed. All other cost calculations
use canonical normalized rows directly, never legacy float totals.

`rows` covers every full-history distinct cost row, in first-ordinal order:
exact `{metrics_ordinals,run_id,cost_row_sha256,window_state,allocation,
work_fingerprints}`. Safe ID or null; canonical normalized-row SHA or null for
malformed row; window state `SELECTED|EXCLUDED|UNKNOWN_TIME`; allocation
`DIRECT|SHARED|UNALLOCATED`. Unselected rows have UNALLOCATED/empty members.
Only identical normalized variants of the same valid run ID deduplicate;
missing/unsafe IDs and malformed rows never deduplicate. All raw ordinals are
retained exactly once. Normalize negative-zero duration and numeric finish to
positive zero exactly as legacy19 does before equality/hash.

Common cost object C is exactly `{row_weight,codex,provider,
reported_cycle_seconds}`. Every amount/weight below is rational.
Codex is `{observed_weight,unknown_weight,observed_subtotal,full_usage}`; its
token maps have exact `input_tokens,cached_input_tokens,uncached_input_tokens,
output_tokens`. Provider has `{observed_weight,unknown_weight,
observed_subtotal,full_usage,attempt_coverage_weight,
unknown_attempt_coverage_weight,reported_attempt_count,observed_attempt_count,
unknown_attempt_count}`; maps have `input_tokens,output_tokens,total_tokens`.
Reported-cycle is `{observed_weight,unknown_weight,observed_subtotal,full_total}`.
Unknown usage contributes unknown coverage, never fabricated zero known usage;
its observed subtotal is still conserved. Provider full usage requires every
positive-weight row's full usage, not merely an observed subtotal. Attempt
coverage is independent; failed and partial attempts retain costs and unknowns.
All full fields are null for empty C or any missing relevant positive-weight
coverage, otherwise their complete totals. Full/null gates are not additive.

`totals,direct,shared,unallocated,retained_totals,excluded,unknown_time` are C;
`per_work` is sorted `{work_fingerprint,costs:C}` for every selected work,
including zero-row (full=null) work. For each observed amount/coverage mass:
retained_totals = totals + excluded + unknown_time;
totals = direct + shared + unallocated;
direct + shared = sum(per_work).
Codex input=cached+uncached, Provider total=input+output, and
reported_attempts=observed_attempts+unknown_attempts remain exact. Windowunknown
observed costs remain explicit, not silently dropped or allocated outside the
denominator. Full mode selects all rows; excluded/unknown_time are empty C.

DIRECT requires a surviving740 BOUND association to one selected proved work and
no distinct normalized variant of that run. Failed rows can qualify; a failed
run is not free. Shared declarations cannot launder CONFLICT rows or normalized
run conflicts. Unknown/unallocated rows remain in selected totals. Valid shared
membership distributes a whole selected row by exact1/N among all members;
if any member is outside the selected denominator it stays unallocated, rather
than reallocating the missing share. SHARED overrides DIRECT only when its
valid declaration includes the directly associated work. No double counting.

## Optional closed shared membership

Only existing receipt family key
`accepted-work-shared-cost-membership-v1.json` is read, at the fixed inventory
path. There is no new family/writer or scan. Current HEAD Git blob must equal
the owned live bytes; both absent means `NO_RETAINED_MEMBERSHIP`. A missing live
leaf with committed bytes, or uncommitted live bytes, is a mismatch. Only genuine
MISSING_FILE is absence; permission, links, unsafe parent, drift, I/O and all
owner resource failures are fatal, never membership UNKNOWN.
HEAD symlink/tree/gitlink or other unsupported Source mode propagates the fixed
UNSUPPORTED_SOURCE error and aborts/closes the public invocation even when the
live leaf is regular. It is NOT an unsupported JSON schema or a partial report.

Exact declaration:
`{schema_version,anchor_commit,canonical_ledger_sha256,metrics_sha256,memberships}`,
schema string `two-site.accepted-work-shared-cost-membership.v1`.
`anchor_commit` is an explicit strict ancestor of current HEAD, not a
self-referential HEAD placeholder. Ledger SHA hashes the complete current raw
canonical ledger; metrics SHA hashes complete SAME raw metrics. Each of at most
2,000 memberships is exactly `{cost_row_sha256,metrics_ordinals,
work_fingerprints}`. Ordinals and members are sorted unique/nonempty; at least2
and at most512 distinct genuinely proved work members. Every member has an
independently surviving accepted commit at/before anchor. No `accepted:true`
flag, alias ID or scope name substitutes for proof.

Cost-row SHA is SHA256 of UTF8 `json.dumps(canonical_normalized_row,
sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False)`.
It must identify exactly one valid full-history distinct row and EXACT all its
ordinals, before windowing. One row cannot appear twice; duplicate/conflicting
associations invalidate the entire declaration, with no partial shared credit.
Typed metadata is charged to the SAME65536 budget before mapping growth; all
JSON parsing/depth/node/time and existing2M receipt/8M owner limits remain.

`costs.membership` is exactly `{state,reason_code,receipt_sha256,anchor_commit,
membership_count}`. States VERIFIED, NO_RETAINED_MEMBERSHIP,
INVALID_MEMBERSHIP, UNSUPPORTED_MEMBERSHIP. Reason is null or fixed
MALFORMED_MEMBERSHIP, HASH_MISMATCH, ANCHOR_MISMATCH, MEMBER_MISMATCH,
ROW_MISMATCH, CONFLICTING_MEMBERSHIP, UNSUPPORTED_SCHEMA. Unknown explicit
schema is unsupported, not a version alias. Present malformed/unsupported/
conflicting declarations confer NO shared credit; independently proved DIRECT
credit survives. Receipt SHA is actual raw SHA when present; anchor is null
unless independently verified. Current real retained membership availability is
unproved; synthetic positive fixtures establish behavior, not real allocation.

## Coverage and timing

`denominator_state` is COMPLETE_RETAINED_WINDOW only if every canonical index
row is proved, every retained740 run binding is BOUND, and finite-window event
and row timestamps are all located; otherwise PARTIAL_RETAINED_WINDOW. This is
not lifetime completeness. `allocation_state` is COMPLETE only for nonempty
selected rows all allocated, with no invalid/unsupported membership; otherwise
PARTIAL. Zero denominator, incomplete proof/window/allocation or missing full
channel usage keeps that channel ratio null. `token_per_accepted_behavior` is
exact `{codex,provider}`, each its token rational map divided by positive
selected logical-work count, or null. No observed lower-bound subtotal is
presented as a complete ratio.

`timing` is exactly `{reported_cycle_seconds,active_seconds,quota_wait_seconds,
calendar_seconds,state}`. Reported-cycle equals selected C's corresponding
field, explicitly nonadditive operational telemetry; other seconds are null,
state HISTORICAL_TIMING_UNVERIFIED. Existing coordinator/provider phase fields
do not establish complete active/quota/calendar intervals or nonoverlap.
No absent timing is zero, no timestamps are manufactured, no billing/prices.

`limitations` is sorted union of740 limitations minus only
PRIVATE_PROOF_PHASE_ONLY/PUBLIC_COST_REPORT_PENDING, always including
RETAINED_LEDGER_ONLY, NOT_FULL_HISTORY, LIFETIME_UNVERIFIED,
HISTORICAL_TIMING_UNVERIFIED, NO_BILLING_VALUE_STAGE_AUTHORITY. Add
NO_RETAINED_MEMBERSHIP when absent, SHARED_MEMBERSHIP_UNVERIFIED when bad,
UNKNOWN_WINDOW_TIMESTAMPS when any row/event is unlocatable in a finite window,
WINDOW_MEMBERSHIP_OUTSIDE_DENOMINATOR when relevant. Historic incomplete
acceptance, missing original Root and unsupported gap limits stay explicit.

Public fixed ValueError facade `AcceptedWorkCostReportError` exposes only `.code`
and the same fixed string; original proof/snapshot fixed errors and fixed cleanup
notes are preserved without payload/chaining. Invalid outer window is not
silently coerced. Unsafe/resource/poison errors cannot become partial reports.
The new report has no CLI and does not modify telemetry, evidence or input maps.
All default Runner/Provider/Root budgets and policies remain unchanged. Manual
Source geometry will be measured and scoped independently; no Task/Provider
packet readiness or new acceptance follows from this document.

## Required verification

Two new hermetic test files use complete real-Git740 fixture chains, not trusted
accepted flags or collector mocks: real positive1/N membership committed after
anchor; normal and linked worktrees; full independent output oracles; retained,
selected, excluded and unknown-time conservation including failed/partial/
malformed/null-ID/conflicting/duplicate rows; every raw ordinal; alias timestamps
cannot create events; revisions count once. Window boundary/bool/string errors,
unknown event time, all membership hash/anchor/member/ordinal/schema/conflict
faults, physical-vs-Git custody, declared missing leaf, same-owner byte/count/
deadline poisoning and detached/source-free outputs are required. A reached
allocation/denominator implementation mutation must make the SAME whole oracle
fail, then restore. Legacy19 entire output and its existing tests stay unchanged.
Finite native selectors include new2, all740 proof4, old file2, snapshot3 and
legacy report plus scoped regressions. Actual retained-data reporting, membership
availability, timing coverage and comparable cohort value require separate
truthful observed evidence; fixture success alone does not close them.


## Schema2 prospective measurements extension (767 CODEX proposal)

This additive extension is unapplied CODEX-authored Source, not accepted Source,
AE00 closure, a measured improvement or activation. All preceding v1 clauses,
exact16 keys, legacy19 wire, proof/custody/limits and historical results stand.
Actual767 component authorization, independent nonauthor review, Root native
PRE+POST and exact current-Scope/HEAD/bytes/dependency acceptance must precede
Source effects. No Provider execution or replay constructs missing observations.

New public API: summarize_accepted_work_measurements(root, *, window=None).
It has the SAME strict window and fixed error facade. One _proof_snapshot,
one outer _invoke and one existing _Proofs collection own all input/normalization/
membership/proof/measurement/output work. No extra readers, collectors, callback,
cache, receipt family or CLI. Final verification, normal close and whole2MB limit
with final seven-digit counter reservation remain mandatory. No I/O after close.

Schema2 returns the complete preceding report, changes schema_version to2 and
state to OBSERVATIONAL_ACCEPTED_WORK_MEASUREMENTS, and adds exactly measurements
(17 top-level keys). Old timing remains the exact historical v1 observation;
prospective timing is explicitly measurements.timing. Base proof/conflict/work/
window/allocation/cost/legacy fields are unchanged, not filtered into authority.

### Persisted optional execution_observation v1

Normal in-flight finalization may write the active observation to its existing
run status and bounded metrics/ledger. Later boundary hooks may update ONLY the
per-run metrics and mutable bounded ledger: never completed-run/global status,
result, receipt, ACK, claim, Task or Scope. Authoritative evidence bytes, inode
and mode remain unchanged. The current proof artifact vocabulary does not admit
per-run metrics; the global ledger hash is captured anew by each live proof owner,
not an immutable original Root acceptance hash. No historical proof is rewritten.

The optional execution_observation contains exactly:
schema_version(int1), clock_domain(exact safe run_id), active, quota_wait,
planned_stop. Each interval is null or exactly {start_utc_ns,start_monotonic_ns,
end_utc_ns,end_monotonic_ns}. Starts are nonbool integers0..1e21; ends are both
null (open), or both such integers >= their corresponding starts. Unknown
version, extra keys, malformed pair, wrong domain, backwards clock or partial end
never provides a known interval. Absence/null is not a measured zero.

The fixed AP boundary observes time.time_ns/time.monotonic_ns before preparation/
repository validation and closes after child/heartbeat cleanup, before metrics
and pruning. Original run_cycle signature, authority/intent/STOP/checkpoint/
finalization, old clocks/duration/status and exception flow stay. Early failures
persist once a safe run directory exists; missing prompt hashes are null and do
not discard the row. Before safe identity there is no fabricated metrics record.
Telemetry failure cannot accept, block, retry or replace the original exception.

Only the existing loop's actual admitted WAITING_QUOTA or observed STOPPED
boundary appends an open interval to that same current-process run, through
existing bounded metrics persistence, without touching immutable acceptance
artifacts, Provider rereads or wait-authority changes. Repeated boundary
publication preserves the first sample and both telemetry leaves' bytes/inodes. No old process,
reset schedule, absence/idle period or restart supplies a closing monotonic point.
Cross-domain/restart/open intervals remain UNKNOWN.8KB per-run,2MB ledger and2000
record caps and existing replacement/retention semantics stay unchanged.

### Exact measurement wire and coverage

measurements is exactly {timing,provider,rates,rows,coverage,limitations}.
rows contains EVERY full-history distinct v1 cost group, in the same order:
{metrics_ordinals,window_state,state,timing_states,timing_seconds,provider}.
state is OBSERVATIONAL or CONFLICT. Complete canonical typed raw observation
presence/value and relevant provider-field presence/values must agree across ALL
raw ordinals of a deduplicated group. bool/int, absent/null and unsupported-version
differences are not equal. Conflict grants no measurement credit; no ordinal0 pick.
This does not alter v1 grouping or proof quarantine, and no alias/revision creates
progress. Full history also participates in overlap detection before windowing.

timing_states has active/quota_wait/planned_stop, each fixed OBSERVED, UNOBSERVED,
INVALID_OBSERVATION, OPEN_INTERVAL, UNLOCATABLE_INTERVAL,
WINDOW_BOUNDARY_UNVERIFIED, OVERLAPPING_INTERVALS or CONFLICT. timing_seconds
has those exact keys with rational observed monotonic seconds or null. Zero-UTC
width with positive monotonic duration is unlocatable. Positive-width UTC spans
overlapping any other run/kind cannot be summed. A finite window must wholly
contain the interval: no proportional clipping or inferred boundary. Intervals
are observations, not a new clock authority.

timing is {active_seconds,quota_wait_seconds,planned_stop_seconds,calendar_seconds}.
The first3 are coverage objects C={observed_group_count,unknown_group_count,
conflict_group_count,observed_subtotal,full_total}; counts partition SELECTED
distinct groups. Subtotal is exact known nonconflicting mass; full_total is null
for empty/unknown/conflicting coverage. All amounts use reduced nonnegative
{numerator,denominator}, monotonic ns/1e9, never a provider/controller sum or
residual-as-idle/active/waste. Active is operational elapsed including actual
model/review/test/validation, not CPU. Calendar is explicit finite window end-start
(including quota/planned stops); retained-full calendar remains null.

provider contains C for attempts,worker_attempts,reviewer_attempts,failed_attempts,
successful_correction_rounds,first_pass,repeated_blocked, plus rework_paths.
Only complete observed Provider states with positive attempt count<=32, bounded
nonbool counters, attempts=workers+reviewers, failed<=attempts and successful
corrections<=workers provide counters. first_pass is bool/null; true additionally
requires exact(2,1,1,0,0). Successful corrections exclude failed corrections;
first-pass is Provider-chain, not whole-Root quality. repeated_blocked is the
structural failed_attempts>=2 flag, not a repeated same-cause finding.
rework_paths is {observed_group_count,unknown_group_count,counts}; counts uses
exact fixed NONE,REVIEWER_CHANGES_REQUIRED,ATTEMPT_BLOCKED,POST_REVIEW_CORRECTION,
MIXED,UNCLASSIFIED_REWORK,UNKNOWN. Invalid/absent/conflicting taxonomy stays unknown;
no raw finding/error/prose appears.

rates is exactly {active_seconds_per_accepted_behavior,
accepted_behaviors_per_elapsed_day,provider_chain_first_pass_rate,
successful_corrections_per_accepted_behavior,blocked_attempt_rate,
repeated_blocked_chain_rate,retry_rate,same_cause_failure_rate,review_escape_rate}.
Known ratios require the unchanged COMPLETE_RETAINED_WINDOW proof and COMPLETE
allocation gates, positive accepted denominator, complete relevant coverage and
no full-history measurement conflict. Chain rates divide by complete observed
chain groups; blocked rate divides by positive full attempts. Accepted/day uses
the same corroborated first-retained work count and explicit calendar/86400.
No partial subtotal becomes a complete ratio. Exact retry/same-cause/review escape
remain null: current producer has no supported typed SHA-bound evidence for them.
No attempts arithmetic, Reviewer VERIFIED=>escape0, unsupported raw claims or
historical prose supplies that evidence.

coverage is {selected_group_count,selected_raw_ordinal_count,conflict_group_count,
unsupported_rates}; the last maps each unsupported rate to observed_group_count0
and unknown_group_count equal selected groups. limitations is sorted fixed labels:
RETAINED_WINDOW_OBSERVATIONS_ONLY,UTC_ENDPOINTS_NOT_CLOCK_AUTHORITY,
NO_TYPED_RETRY_CAUSE_ESCAPE_EVIDENCE, plus INCOMPLETE_MEASUREMENT_COVERAGE,
CONFLICTING_MEASUREMENT_ORDINALS,OVERLAPPING_OBSERVATIONS when applicable.
No source/prompt/commands/errors, billing, saving, Stage or runtime authority.

### Required extension verification

Three NEW <=24KB hermetic suites cover actual AP preparation/success/failure/
interruption/cleanup/boundary hooks, strict clocks/schema/open/restart/zero-vs-
unknown/privacy/persistence/nonblocking faults; real-Git normal/recovery/linked
whole independent v1 and v2 oracles, real-Git Root-chain survival across both
boundary kinds with all authority leaf bytes/inodes/modes stable, failed/
correction/repeated-blocked/unknown/
shared/all-ordinal/revision/window cases; SAME reached arithmetic mutant fails
the SAME whole oracle and restores. All raw projection conflicts, nonadditive
overlap, unsafe metrics/strict JSON, same owner/time/byte/count/metadata/final-close
and2MB +/-1/final-counter boundaries remain mandatory. The complete authorized40
module PRE+POST union includes unchanged legacy/proof/file/snapshot/AP/preparation/
STOP/quota regressions. No actual native/Source acceptance follows from this text.
Historical unavailable timing and comparable economics remain explicitly unknown;
future real measurement evidence is separate from hermetic fixture success.

### Same-domain monotonic overlap correction (767 R2)

In addition to the unchanged full-history UTC overlap sweep, the fixed three
kinds within ONE validated run observation are checked pairwise for positive
monotonic intersection: max(starts) < min(ends), with both pairs closed. Both
involved kinds become OVERLAPPING_INTERVALS/null and the report carries
OVERLAPPING_OBSERVATIONS, even when their UTC spans are disjoint. This does not
compare monotonic values across runs/groups or infer cross-process chronology.
Adjacent endpoints and zero-width monotonic pairs do not intersect; closed zero
observations remain known zero. Open intervals retain UNKNOWN coverage. Real-Git
whole declared-output oracles cover all three kind pairs, UTC-disjoint positive
monotonic overlap, valid adjacency/zero and separate-run epoch non-comparability.
No v1/legacy/proof/allocation/rate gate, clock authority or Source cap changes.
