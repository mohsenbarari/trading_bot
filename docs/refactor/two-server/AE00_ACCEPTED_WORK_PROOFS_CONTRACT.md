# AE00 private retained proof join — 740R3

## Boundary and resources

Sole private entry: two_site_accepted_work_proofs._collect_accepted_work_proofs(snapshot).
Exact LIVE _ProofSnapshot required; no new owner/root/I/O/constructor/CLI,
caller path/cap/callback, discovery, storage, historical app/test execution or
Provider. Attestation helpers resolve data only. Reuse PURE semantic validators,
physical-line splitter, canonical selector bindings/resolution and credential
regex, not live builders/Runner validation.
No public denominator/cost/report, backfill/replay/startup/adoption, savings/value,
Stage or checkpoint authority. Existing AE00_WORK_LINEAGE_METRICS_CONTRACT.md
remains normative except the explicit metrics-only node amendment below;
accepted normalizer/selector units are not reopened.

ONE fixed internal Snapshot._invoke encloses all parsing/AST/hash/serialization;
cheap ticks and metadata charges stay within SAME live owner. Existing limits
8M actual bytes/120s/128 evidence-reference paths/65536 metadata bytes/256 Git
objects/512 commands/artifact caps are conjunctive, never reset. Historical Git
receipt paths use SAME _observe accounting. Additional bounds: reference
traversals256/depth8; pointer1024 UTF-8 bytes/32 tokens; JSON depth32;
nonmetrics attestation JSON nodes100000; index64 rows/manifest64/metrics2000;
output2M UTF-8. Charge before growth.

Metrics-only node amendment: BOTH lexical and parsed-node checks in Evidence
_json_list use the fixed internal _LIMITS['metrics']//2+1 =1000001 bound.
Valid N-node JSON (including containers and object keys) requires at least2N-1
wire bytes, so this derives an envelope from the unchanged2000000-byte metrics
cap instead of imposing an unrelated100000-node rejection on valid retained
telemetry. No caller parameter or adaptive retry. This supersedes ONLY the
100000-node metrics rule in AE00_WORK_LINEAGE_METRICS_CONTRACT.md section7,
including its accepted738L reference. The attestation _json guard stays100000;
metrics2000rows/depth32, strict UTF8/unique keys/finite numbers, SAME8M/120s/
128 observations/65536 metadata and all custody/cache/poison rules are unchanged.
Dense valid metrics beyond100000 nodes must pass within wire/row/depth bounds;
exact2M wire and2M+1 rejection, and unchanged nonmetrics100000/+1, are tested.
Result validity requires normal enclosing context exit; detachment is not revocation.

Root-family-only extension: require secure ancestors THROUGH existing
.local/two-site-refactor/autopilot. Below it, genuine first absent component
gets normal negative record under FULL root_result/status artifact path,
pinned safe parent fd/missing name. Normal128/path-key charges, zero read/hash
for absence; repeat/final verification catches appearance. Permission/link/
file-in-directory/race/I/O stay unsafe. Other families' parent rules unchanged;
no auxiliary path reader.

## Canonical data

WHOLE ledger physical bytes equal pinned HEAD blob. Exactly one existing
"Reviewed current seven-scenario Source index (data):" heading and JSON fence.
Label is locator, not count. Index exact keys common_receipt_pointers,field_order,
rows; common pointers /source,/actor,/scope,/git; ordered columns:
seed_id,canonical_symbol,scenario_key,selector,source_commit,source_file_sha256,
receipt_path,receipt_sha256,handler_pointer,evidence_limits_path,
evidence_limits_sha256,evidence_limits_pointer. Validate complete row/handler/
limits agreement; membership/counters/prose alone grant no acceptance.

Current receipt physical/HEAD equality is checked on cache hits too.
Historical inventory anchors require declared path/commit/SHA, SHA match and
ancestry to pinned HEAD. Exact RFC6901 ~0/~1/canonical array indices; bounded
cycles, no arbitrary requested reads. Three actual routes:
- inline source.generation_source_manifest and actor Worker/Reviewer/Supervisor;
- recovery relative /source,/actor,/scope,/git via ONLY provenance.registration;
- login path#/pointer via ONLY provenance.accepted_login_actions_registration.
Known same-target routes agree; arbitrary provenance keys cannot authorize.
Recovery source.generation_custody is an explicit nested anchor agreeing with
provenance.custody when both declared. accepted_commit is login's specific alias.
Inline/resolved authority bindings agree. Accounting PENDING/PARTIAL is not
Source rejection; incomplete group accounting cannot erase old proof.

## Task and Source

Task exact task_id,goal,context_files,allowed_files,requirements,test_selectors:
ASCII ID<=80, nonblank bounded goal, ALL4 nonempty unique lists<=32, safe paths/
selectors and pure SECRET refusal. Worker snapshot exact task,source_head,sources;
Reviewer adds proposal string,test_evidence dict. Original parsed Task equals
snapshot; sources exactly context/allowed union, entries exact content,sha256.

Hash domains: generation_task_sha256=ORIGINAL raw Task; source_task_snapshot_sha256=
WORKER raw snapshot; metrics.task_sha256=json.dumps(task,sort_keys=True).encode()
with default spaces/ASCII. Never conflate domains or demand undeclared hashes.
One canonical newline marker; envelope exact head,gap_reference,excerpts,
semantic_identity. Recompute complete union snapshot paths+DISJOINT excerpt
paths+gap; null only allowed-only absent new files, not context/excerpt/gap.
Verify all parent blob SHA/content/null, semantic/source/work hashes. Registration
manifest subsets must agree pairwise, not equal full union (c0649+5+gap=15).

Excerpt exact path,sha256,symbols,ranges: producer's ALL top-level imports and
matched functions/classes/Assign/AnnAssign stored names, multi-targets/decorators.
Physical CRLF/CR/LF only, not U+2028/U+2029. Each range exact start,end,text;
bounds exact nonbool ints within physical lines, text str; full ordered
regenerated ranges/symbols/blob hash agree. No text-only checksum substitute.

Gap JSON schema two-server-refactor.scenario-records.v2; exactly one
blocker_registry.items matching fragment, resolution_stage P1-00b/severity HIGH/
owner CODEX_FINAL_REVIEWER. No stale coverage/observed-handler requirement or
Stage credit. Actual recovery ledger#gap has no unique anchor/owner mapping:
UNKNOWN UNSUPPORTED_GAP_SCHEMA, original identity/hash retained, never rewritten.

## Proposal, native, Reviewer

Canonical result.json (not proposal.json) exact changes,covered_requirements,
findings,status,summary. Worker PROPOSAL_READY/nonempty changes; Reviewer
VERIFIED/changes[]; nonblank summary/findings[] and exact unique nonbool
coverage1..N. Change exact path,original_sha256,content: allowed unique path,
nonempty changed UTF-8/no NUL, parent original SHA/null and full AFTER agree.
No READY/raw-four-key aliases. Accepted Git sole parent/source_head, ancestry,
exact changed paths/regular modes/OIDs/bytes and selector AST agree. Bind
handler/scenario/scope; doc-only changes grant no behavior proof.

Statuses bind role/state/authority/head/task/controller/run/result raw SHA,
inline actor IDs and distinct nonempty conversations. reviewer-0 role verifier.
Optional PRESENT metadata is typed/bounded, never an I/O path/current policy:
scalar strings authority/controller_run_id/conversation_id/effort/head/model/
response_schema_sha256/result_sha256/role/run_id/state/task_id; finite numeric
duration_seconds/retain_until/started_at; nonbool int cli_pid.
Known profiles: cli(path:str,sha256:hex,version:str);
cli_source(sha256:hex,source_path:str); runtime_profile(actual_executor_checked:bool,
command_policy:str,conversation_id:str,name:str,native_tools:nonnegative int);
session_gate(activation_reason:str,query_operation_count:int,required_symbol_count:int,
root_count:int,state:str); usage exact nonnegative ints cache_read_tokens,
input_tokens,output_tokens,thinking_tokens,total_tokens. All ints nonbool;
profile CID equals OWN status CID. Nullable usage/runtime_profile is telemetry,
not authority. Unsupported mandatory versions remain UNKNOWN; no invented exitcode.

Worker <prefix>-worker-0 derives ONLY pipeline/<prefix>/pre-review-tests-0.json.
Native exact state,proposal_sha256,selectors,unsupported_specs,returncode,
stdout_tail,stderr_tail,snapshot_files,snapshot_bytes,isolation: PASSED/nonbool
rc0/unsupported[], exact proposal/selectors, nonnegative nonbool counts,
tails<=8192 UTF-8, isolation bwrap:ro-source,empty-home,clearenv,unshare-all.
Reviewer input_proposal_sha256/input_test_evidence_sha256/input_worker_run_id/
input_worker_conversation_id bind actual originals; review_transport_sha256
is hex only (unretained transport not reconstructed). Embedded proposal string
hash/parsed value and native equal independently read originals.

## Root tiers

HISTORICAL_ATTESTED_CORROBORATED requires committed canonical source-acceptance
attestation PLUS complete actual Task/Source/proposal/Git/native/separate
Reviewer/accepted-metrics tuple. Bare accepted:true/CONTINUE/Git/prose insufficient.
Registration needs no invented inline Supervisor. Declared Supervisor controller/
state/parent/commit agrees. Tier proves historical corroboration, NOT original
Root reread/fresh test/Reviewer/new acceptance/full accounting or full history.

Declared Root result/status SHA attestations select BOTH fixed runs/flat layouts
by bound controller. Validate EVERY present artifact including partial/second
route; malformed/conflicting originals quarantine, never downgrade. Only guarded
absence permits historical tier+MISSING_ORIGINAL_ROOT_RECEIPTS. No declared hashes:
no speculative probing, ORIGINAL_ROOT_NOT_DECLARED (not filesystem absence).
No original reconstruction. ORIGINAL_ROOT_RECEIPT_CORROBORATED additionally
requires a complete valid pair, not a new acceptance event.

Root raw result EXACT11 schema_version,state,disposition,category,summary,commit,
next_action,blockers,gemini_worker_run_id,gemini_reviewer_run_id,proposal_sha256:
version"1.0", CONTINUE/COMPLETE,NONE disposition/category,blockers[], exact commit/
Worker/Reviewer/proposal, nonblank summary, CONTINUE concrete next_action.
No in-memory head fields demanded. Root status minimum state/head_before/
head_after/Worker/Reviewer/proposal/blockers. Present version is exactly "1.0";
present summary/next_action are typed even without result, CONTINUE action nonnull,
and match any present result. protected_refs exact3 names/40-hex values NOT today's refs; other declared
bindings cannot contradict. No mandatory nonexistent run_id/task_id/result hash.

## Aggregation and exact output

Rolling metrics list is RETAINED_LEDGER_ONLY/NOT_FULL_HISTORY, not records envelope.
Every ordinal belongs once to a binding; ordinals local to SAME snapshot.
Safe run aliases retain all ordinals; unsafe/missing IDs=>null per ordinal.
Malformed rows explicit UNBOUND/CONFLICT, never discarded/free history.

Work hash exact stage/gap/Task scenario/symbol_groups excludes Source revisions/
aliases/retries/run IDs. Handler key separately gap/symbol/scenario, not necessarily
Task scenario. Revisions/selectors are witnesses, not extra credit. Connected
credible alias/identity/reference/scenario conflicts quarantine BEFORE output;
missing/incomplete later proof alone cannot erase prior valid work.

EXACT top: schema_version(int1),state(OBSERVATIONAL_PROOF_JOIN),snapshot,works,
handler_scenarios,run_bindings,unknown,retention_limits; no public accepted count/cost.
- snapshot EXACT actual detached _metadata(): schema_version,head,protected_refs,
  files,git_objects,bytes_read with existing complete nested schemas.
- works sorted work_fingerprint: EXACT work_fingerprint,semantic_fingerprints,
  source_revisions,evidence_level,proof_refs,retained_run_ids. Fingerprints/IDs
  sorted unique; revision EXACT semantic_fingerprint,source_sha256,source_head,
  accepted_source_commit, canonical-JSON-sorted unique; strongest proven tier.
- handler_scenarios sorted gap_reference/canonical_symbol/scenario_key: EXACT
  work_fingerprint,gap_reference,canonical_symbol,scenario_key,witnesses.
  Witness EXACT selector,source_commit,source_file_sha256; canonical-JSON-sorted
  unique. Incompatible ownership conflicts, not second credit.
- proof_refs EXACT path,sha256,pointer; tuple-sorted unique VERIFIED artifact/
  Source blobs/resolved pointers, never absent/unverified claims/null hashes/text.
  Chain-causal refs isolated; bound alias refs union into that work. Fixed phases
  locator,attestation,Task,Source,proposal,native,Reviewer,metrics,Root.
- run_bindings sorted first ordinal then null-first run_id: EXACT run_id(safe|null),
  metrics_ordinals,semantic_fingerprint(nullable),work_fingerprint(nullable),
  state(BOUND|UNBOUND|CONFLICT).
- unknown EXACT work_fingerprint,semantic_fingerprint(nullable),reason_code,
  proof_refs,retained_run_ids,metrics_ordinals,index_ordinals; unique canonical-JSON
  sorted; ordinal lists sorted unique; no unsafe ID echo.
- retention_limits EXACT index_row_count,metrics_record_count,proved_index_ordinals,
  unresolved_index_ordinals,limitations; ordinal lists partition ALL index rows.
  Fixed sorted RETAINED_HISTORY_ONLY,RETAINED_LEDGER_ONLY,NOT_FULL_HISTORY,
  LIFETIME_UNVERIFIED,PRIVATE_PROOF_PHASE_ONLY,PUBLIC_COST_REPORT_PENDING,
  NO_BILLING_VALUE_STAGE_AUTHORITY; conditional MISSING_ORIGINAL_ROOT_RECEIPTS,
  ORIGINAL_ROOT_NOT_DECLARED,INCOMPLETE_ACCEPTANCE,UNKNOWN_RETAINED_ROWS.

UNKNOWN reasons EXACT MISSING_ARTIFACT,MALFORMED_EVIDENCE,HASH_MISMATCH,
REFERENCE_CONFLICT,IDENTITY_CONFLICT,SOURCE_MISMATCH,PROPOSAL_MISMATCH,NATIVE_MISMATCH,
REVIEWER_MISMATCH,ACCEPTANCE_MISMATCH,UNSUPPORTED_SCHEMA,UNSUPPORTED_GAP_SCHEMA,
UNBOUND_METRICS. Typed first failing phase wins. Zero proven work is not zero history.
Fatal _AcceptedWorkProofError code/string: Snapshot enums plus INVALID_CANONICAL_INDEX,
INVALID_METRICS_LEDGER. Whole locator/metrics envelopes fail; bounded individual
rows retain ordinals. Unsafe/custody/poison/resource NEVER UNKNOWN. Only fixed enum
notes cross translations, rendered chaining suppressed; no raw source/error/tails.

## Gates and remaining work

Named E25000/proofs44000/governing-metrics-contract25000;
attestation/ALLtests24000/proof-contract16000; ALL10<=241000 under fresh
MANUAL_AE00_PROOFS_SOURCE10_V2, domain MANUAL_AFTERS_UTF8, exact
ten paths/base9a807f33e6626fbe0b0fbf23b4a0c5050f299c65. Whole-byte size must
be measured before Scope issuance/admission; old file-only4 aggregate<=96000
also applies. This NEW manual Source-only bound is NOT the Gemini Role/Task/
proposal/Root187K default or AE04 exception. All default SDK/Runner/Provider
caps stay unchanged; wire geometry is NOT_USED, never inferred Task readiness.
Ten selectors old file2+Snapshot3+new attestation/proofs/fault/bindings4+work_cost_report.
Independent real ordinary/linked temporary Git, full literal/SAME mutation/restore
oracles for3forms/hash/excerpts, Root unsafe/absence/appearance/present conflict,
aliases/revisions/null ordinals/connected quarantine, malformed schema/status/
range and shared resource/time/custody, detached nonleakage. Environment/network
fence BEFORE project imports; no historical app execution/live Git mutation.

Fixtures prove implementation only. Root separately runs bounded actual retained
witness after physical POST native/exact Source checks under proper holds BEFORE
Git commit, reports actual tiers/scenarios/
missing joins (including unsupported recovery), never hardcoded5/7/fabricated Roots.
Phase2 report/windows/direct/shared exact rational allocation/conservation/null
coverage, timing/cohorts/value/AE03live remain OPEN. No bookkeeping commit,
checkpoint reset, activation/adoption/Stage credit.
