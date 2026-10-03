# Section 1 Stage Ledger

Last updated: 2026-09-04 UTC
Complete commit: `a0f22b7d476429974b9d4c8a7ae3d0d5e4a99be9`
Assignment: `WA-P1-00-SCOPE-20260903-002` (supersedes invalidated `WA-P1-00-REFRESH-20260903-001`)
Executing leftover freeze: `WA-P1-00B-FREEZE-20260904-001` (`base_sha` `40228b073882588e452cde6b0e6cb4cb09e7ffbd`; do not mark `P1-00b` COMPLETE)
Historical inventory baseline: `aa2c06a573dfd60051e142146f55d7965f2242a7`
Reissued Assignment base: `492d896d67fe9a3625bd5784b795a4029b92b600`
Worker parent: `c70839a0ece4a87244a54f2ff58e275239232980`
Rejected integration review: `079d5cf0552d4bdc89c0430d90c50226176bc4c3`

| Stage | Status | Gate | Primary output |
| --- | --- | --- | --- |
| `P1-00` | `COMPLETE` | Codex `APPROVE` on `a0f22b7d476429974b9d4c8a7ae3d0d5e4a99be9` | current-state dossier and blocker register |
| `P1-00b` | `IN_PROGRESS` | leftover freeze; Assignment `WA-P1-00B-FREEZE-20260904-001`; blocked until its own Codex `APPROVE` | remaining current-behavior freeze |
| `P1-01` | `PROPOSED` | policy approved; execution blocked by `P1-00`, `P1-00b` and exact batch receipts | cleanup/retention execution |
| `P1-02` | `PROPOSED` | scenario contract approved; blocked by `P1-00`, `P1-00b`, `DPL-1` (including `DR-015` measured durations) and `DOC-3` | topology-neutral configuration |
| `P1-03` | `PROPOSED` | scenario/ownership contract approved; dependencies not complete | target runtime topology |
| `P1-04` | `PROPOSED` | scenario/data contract approved; dependencies not complete | shared Finland data plane |
| `P1-05` | `PROPOSED` | scenario/merge contract approved; dependencies and Assignment open | deterministic data-merge rehearsal |
| `P1-06` | `PROPOSED` | scenario/acceptance contract approved; dependencies and execution evidence open | differential staging acceptance |
| `P1-07` | `PROPOSED` | scenario/cutover contract approved; dependencies, evidence and production authorization open | controlled cutover |
| `P1-08` | `PROPOSED` | scenario/closure contract approved; cutover, Iran standby, evidence and per-resource authorization open | closure/decommission |

## `P1-00` execution receipt

This refresh was documentation and side-effect-free characterization only. It did
not provision, restart, deploy, change DNS, write runtime state, copy production
data or delete anything.

| Field | Value |
| --- | --- |
| Resume / inventory base | `aa2c06a573dfd60051e142146f55d7965f2242a7` |
| Historical plan-base | `b8333ea4` |
| Repository worktree | registered Stage worktree `stage-P1-00-refresh` |
| Last observed production release | `e533d415f1fe085a251d8e6df2016fa775d86702` on both current Finland roles; `NOT_REOBSERVED` |
| Last observed database migration head | `ff6c7d8e9f01` on both current application databases; `NOT_REOBSERVED` |
| Runtime access this Assignment | none; live host facts `NOT_REOBSERVED` |
| Target SSH fingerprint | `SHA256:bwxz2aeBwy0ZNOMMCVdRhaW//TkeALqt6etTQa3NINs`; `NOT_REOBSERVED` |
| External writes | none |
| Secret values collected | none |

Completed technical evidence in this refresh:

- static inventories keep original `generated_from_commit` `19087ff0...` and add
  `verified_against_commit` `aa2c06a5...`; live method/path/filter/guard/job
  semantics still match 217 / 203 / 30 / 15 with zero unclassified items;
- six previously empty `test_refs` closed by
  `tests/test_p1_00_characterization_six_gaps.py`;
- persona/tier/time/failure source compacted to
  `inventory/scenario-records.json` schema `two-server-refactor.scenario-records.v2`
  with real family contracts, definite composed eligibility, seed mapping and
  named invalid combinations; Cartesian expansion is not stored in Git;
- `DR-015` classified `DEFERRED_BLOCKER` / `DPL-1` (`NOT_OBSERVED`); measured
  durations are required later; estimate and production rehearsal remain forbidden;
- sanitised checksums recomputed in `evidence-index.md`.

## Why `P1-00` is `COMPLETE`

Codex Final Reviewer issued `APPROVE` for exact integration commit
`a0f22b7d476429974b9d4c8a7ae3d0d5e4a99be9` on Assignment
`WA-P1-00-SCOPE-20260903-002`. Receipt:
`docs/refactor/two-server/reviews/P1-00-WA-P1-00-SCOPE-20260903-002.md`.

Leftover current-behavior freeze is owned by `P1-00b`. `P1-01` and implementation
Stages remain blocked by `P1-00b`. `DR-015` still blocks `P1-02` until `DPL-1`.
`DR-018` stays `NOT_REINSPECTED`.

Residual items that do not reopen `P1-00`:

1. `DR-015` — deploy/restart/backup/restore duration has no already-observed
   measurement. Codex decided this is `DEFERRED_BLOCKER` with
   `resolution_stage: DPL-1`. Required evidence is measured deploy, independent
   API/Bot restart, backup, and restore+verification durations. Do not estimate
   or rehearse in production. `P1-02` must not start before `DPL-1` completes.
2. Live host capacity/release/schema facts remain `NOT_REOBSERVED` because this
   Assignment has `external_access: NONE`. That is accepted for this snapshot and
   is not enough for later staging/cutover.
3. Supporting human labels in some inventory docs still mention the superseded
   Assignment; Codex recorded this as MEDIUM for the next authorized
   documentation change (`P1-00b`).

## `P1-00b` execution status

### 2026-09-30 UTC — Five-unit recovery characterization evidence checkpoint

Assignment `WA-P1-00B-EVIDENCE-RECONCILE-20260930-051` completes reconciliation for the five accepted recovery behavior units across two continuation packets:
- First two units reconciled in `docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-5.json`:
  - `tests/test_p1_00b_characterization_get_single_session_recovery_status_missing_refresh.py`
  - `tests/test_p1_00b_characterization_pending_recovery_prompts_admin_empty.py`
- Remaining three units reconciled in `docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-6.json`:
  - `tests/test_p1_00b_characterization_request_single_session_recovery_identity.py`
  - `tests/test_p1_00b_characterization_cancel_recovery_malformed_uuid.py`
  - `tests/test_p1_00b_characterization_cancel_recovery_no_active.py`

Current seed coverage stands at 385 of 465 total seeds evidenced (135 baseline + 250 reconciled new), with 80 seeds remaining.

Canonical High blockers remain open:
- `P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE`
- `P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN`

The five-unit reconciliation is complete, but `P1-00b` remains `IN_PROGRESS`; channel rejection, other seed handlers and origin-matrix cells remain pending. Historical Stage entries are preserved and Stage closure is not claimed.

### 2026-10-01 UTC — Five-unit recovery characterization evidence checkpoint

Assignment `WA-P1-00B-EVIDENCE-RECONCILE-20261001-075` completes the non-overlapping five-unit reconciliation across continuations 7 through 10:
- Continuation 7 (`docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-7.json`):
  - `tests/test_p1_00b_characterization_admin_users_toggle_block_unlinked.py` (commit `431a5da5ef1c987c72eabd3571534e7843d1c5fd`, pre-evidenced baseline seed `BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-ADMIN-TOGGLE-BLOCK-L1649`, selector `tests/test_p1_00b_characterization_admin_users_toggle_block_unlinked.py::AdminUsersToggleBlockUnlinkedCharacterizationTests::test_toggle_block_without_user_stops_before_parse_session_or_delivery`)
  - `tests/test_p1_00b_characterization_trade_expire_offer_db_entry_failure.py` (commit `6b76cb3c23b3507e3048d71b6e6b9817ec058a4a`, newly counted seed `BOT-TRADE-CALLBACK-QUERY-TRADE-MANAGE-HANDLE-EXPIRE-OFFER-L118`, selector `tests/test_p1_00b_characterization_trade_expire_offer_db_entry_failure.py::TradeExpireOfferDbEntryFailureCharacterizationTests::test_db_factory_failure_propagates_and_releases_lease_without_delivery`)
- Continuation 8 (`docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-8.json`):
  - `tests/test_p1_00b_characterization_admin_broadcast_search_state.py` (commit `b6ced906adf8a1847bd8962263d1006af55baa62`, pre-evidenced baseline seed `BOT-ADMIN-MESSAGE-ADMIN-BROADCAST-PROCESS-SELECTED-RECIPIENT-SEARCH-L308`, selectors `tests/test_p1_00b_characterization_admin_broadcast_search_state.py::AdminBroadcastSearchStateCharacterizationTests::test_matched_recipients_preserve_selected_marker_and_search_delivery`, `tests/test_p1_00b_characterization_admin_broadcast_search_state.py::AdminBroadcastSearchStateCharacterizationTests::test_empty_results_preserve_selected_count_and_no_match_delivery`, `tests/test_p1_00b_characterization_admin_broadcast_search_state.py::AdminBroadcastSearchStateCharacterizationTests::test_malformed_selected_id_fails_before_session_or_delivery`)
- Continuation 9 (`docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-9.json`):
  - `tests/test_p1_00b_characterization_admin_broadcast_finish_selected_linked.py` (commit `b48ae0bf6ef28731e070a0e016ae40daac6cdffa`, pre-evidenced baseline seed `BOT-ADMIN-CALLBACK-QUERY-ADMIN-BROADCAST-FINISH-SELECTED-RECIPIENTS-L395`, selectors `tests/test_p1_00b_characterization_admin_broadcast_finish_selected_linked.py::AdminBroadcastFinishSelectedLinkedCharacterizationTests::test_empty_selection_alerts_without_transition_or_edit`, `tests/test_p1_00b_characterization_admin_broadcast_finish_selected_linked.py::AdminBroadcastFinishSelectedLinkedCharacterizationTests::test_nonempty_selection_prompts_then_acknowledges`)
- Continuation 10 (`docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-10.json`):
  - `tests/test_p1_00b_characterization_admin_broadcast_selected_user_rejection.py` (commit `3f7933fc134303fdc09837550be931eafac4c0a2`, pre-evidenced baseline seed `BOT-ADMIN-CALLBACK-QUERY-ADMIN-BROADCAST-TOGGLE-SELECTED-USER-L334`, selectors `tests/test_p1_00b_characterization_admin_broadcast_selected_user_rejection.py::AdminBroadcastSelectedUserRejectionCharacterizationTests::test_malformed_target_alerts_without_state_or_delivery`, `tests/test_p1_00b_characterization_admin_broadcast_selected_user_rejection.py::AdminBroadcastSelectedUserRejectionCharacterizationTests::test_new_target_at_cap_alerts_without_mutation_or_delivery`)

Seed coverage distinct accounting distinguishes four pre-evidenced baseline seeds (`BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-ADMIN-TOGGLE-BLOCK-L1649`, `BOT-ADMIN-MESSAGE-ADMIN-BROADCAST-PROCESS-SELECTED-RECIPIENT-SEARCH-L308`, `BOT-ADMIN-CALLBACK-QUERY-ADMIN-BROADCAST-FINISH-SELECTED-RECIPIENTS-L395`, `BOT-ADMIN-CALLBACK-QUERY-ADMIN-BROADCAST-TOGGLE-SELECTED-USER-L334`) from the one newly counted trade expiry seed (`BOT-TRADE-CALLBACK-QUERY-TRADE-MANAGE-HANDLE-EXPIRE-OFFER-L118`). Current seed coverage stands at 386 of 465 total seeds evidenced (135 baseline + 251 reconciled new), with 79 seeds remaining.

Canonical High blockers remain open:
- `P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE`
- `P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN`

Stage `P1-00b` remains `IN_PROGRESS`; origin matrix remains open, and no Stage closure, origin-cell acceptance, product change, or new behavior unit is claimed.

### 2026-10-01 UTC — Terminate session characterization evidence checkpoint

Assignment `WA-P1-00B-EVIDENCE-RECONCILE-20261001-079` reconciles the two existing accepted `terminate_session` characterization units into `docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-11.json`:
- `tests/test_p1_00b_characterization_terminate_session_malformed_guard.py` (commit `e7f957d7900e9cfc5150b6cad83e1daf7c29bb81`, SHA256 `ea75b1cd5f9f341608dd039acc24a81714067bd0fa2e0d2dd1bd47fafa21c4d0`, seed `API-AUTH-DELETE-SESSIONS-TERMINATE-SESSION-L1075`, selectors `tests/test_p1_00b_characterization_terminate_session_malformed_guard.py::TerminateSessionMalformedGuardCharacterizationTests::test_valid_bearer_and_real_guard_precede_malformed_uuid_rejection`, `tests/test_p1_00b_characterization_terminate_session_malformed_guard.py::TerminateSessionMalformedGuardCharacterizationTests::test_accountant_guard_rejects_before_malformed_uuid_path`): validates ordinary guard execution and 400 rejection on malformed UUID, as well as accountant guard 403 before UUID parsing, without claiming mutation or successful termination.
- `tests/test_p1_00b_characterization_terminate_session_missing_target.py` (commit `03659a9ff3af3c1efeb7e97ab1ce9d782109c257`, SHA256 `672a23ece8460a17aa2d9766c472d8fc6035a87e3a2f4e17acccdd6da128eab9`, seed `API-AUTH-DELETE-SESSIONS-TERMINATE-SESSION-L1075`, selector `tests/test_p1_00b_characterization_terminate_session_missing_target.py::TerminateSessionMissingTargetCharacterizationTests::test_valid_uuid_missing_target_returns_404`): validates 404 response on valid UUID with missing target session after exactly one target `UserSession` query through real `AsyncSession` without double-counting the seed.

Both units execute the single seed `API-AUTH-DELETE-SESSIONS-TERMINATE-SESSION-L1075`, which is counted once only. Distinct seed coverage updates to 387 of 465 total seeds evidenced (135 baseline + 252 reconciled new), with 78 seeds remaining.

Canonical High blockers remain open:
- `P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE`
- `P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN`

Stage `P1-00b` remains `IN_PROGRESS`; successful termination, mutation, origin-matrix cells, policy/notification/retry closure, and seed siblings are excluded, and no Stage closure or product change is claimed.

### 2026-10-01 UTC — Partial reconciliation checkpoint (continuations 13 and 14)

Assignment `WA-P1-00B-EVIDENCE-RECONCILE-20261001-116` (parent `WA-P1-00B-FREEZE-20260904-001`, inspected head `67931e960a1361ddb49f1be603eb1553cd1a0687`) records one documentation-only partial reconciliation checkpoint for exactly the two accepted behavior units indexed in continuations 13 and 14:
- Continuation 13 (`docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-13.json`):
  - `tests/test_p1_00b_characterization_trade_export_self_delegation.py` (commit `98c98e93e94aa426ebae7502a67a5d5053a3a31a`, SHA256 `b8db918415014c2fbaec56888bdc37e6be53f329829a6a1af9dd0ba72b5856d1`, seed `API-TRADE-GET-TRADES-EXPORT-TRADES-WITH-USER-L5797`, selector `tests/test_p1_00b_characterization_trade_export_self_delegation.py::TradeExportSelfDelegationCharacterizationTests::test_self_target_delegates_exact_filters_and_returns_export_response`): self-target export forwards filters and exact db/context and returns delegated response with zero DB access. Self-export is one prospective new distinct seed.
- Continuation 14 (`docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-14.json`):
  - `tests/test_p1_00b_characterization_users_read_all_execute_failure_http.py` (commit `d175d215f83631a98a1a9fefea7c27cef6d88936`, SHA256 `1fd0fe0e19768903c6fd2e921b63238c2af0ceba5b6331210ce6e1e1494e0623`, seed `API-IDENTITY-GET-USERS-READ-ALL-USERS-L407`, selector `tests/test_p1_00b_characterization_users_read_all_execute_failure_http.py::UsersReadAllExecuteFailureHttpCharacterizationTests::test_list_query_failure_returns_http_500_before_relation_context`): HTTP list query execute failure returns default 500 after strict pagination/query validation, before enrichment/serialization. Adds zero seeds because the read_all_users seed is already reconciled (in `recent-characterization.json` via direct execute-failure test commit `0d9141891441c5dbacbe66b8dcc480418c21fe0d`). Scenarios or files are not counted as distinct seeds.

Behavior unit tracking:
- `units_since_reconciliation=5`
- `total_behavior_units=5`
- `reconciled_behavior_units=2`
- `remaining_behavior_units=3`

The remaining inventory paths in the five-unit pending group are exactly:
- `docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-15.json` (empty-list HTTP200)
- `docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-16.json` (manager-peer HTTP403)
- `docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-17.json` (missing-target HTTP404)

Reconciliation policy constraints:
- No sixth behavior may be accepted before complete reconciliation.
- Rejected continuation-12 stays deferred.
- Global reconciliation remains incomplete until the remaining three behavior units are actually reconciled.

Canonical coverage and manifest remain unchanged:
- `baseline_evidenced_seed_count`: 135
- `reconciled_new_seed_count`: 252
- `evidenced_seed_count`: 387
- `remaining_seed_count`: 78
- `expected_seed_count`: 465
- `coverage_status`: `PARTIAL`
- Seed acceptance status: `FULL_SEED_ACCEPTANCE_NOT_ASSESSED`

Canonical High blockers retained:
- `P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE`
- `P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN`

Stage status:
- `P1-00`: `COMPLETE`
- `P1-00b`: `IN_PROGRESS`

No Stage/seed/blocker/origin-cell closure is authorized; no product, runtime, retry, or persistence modification is made.

### 2026-10-02 UTC — Continuation 15 documentation linkage checkpoint (empty-list HTTP200)

Assignment `WA-P1-00B-AFTER-AE00-WORK-PRODUCER-20261002-153` (sha256 `42cf318ef79e18fa02c30dc927178832dcd1adb91fbb4cced713cac348f52f86`, inspected HEAD `c1ad7a81f1f3ace1ecd7745be6a735ed2b1b1123`) records one bounded documentation-only continuation-15 evidence-linkage entry for open canonical blocker `P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE`:
- Inventory artifact: `docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-15.json` (SHA256 `11bc150e1d9e1e92fd317b90078a38b6e28819d7f8b8f812ad19399dc91369b5`, last commit `8cabfffc4622d88bf006ed9ec050e0dc0b8e211c`).
- Existing accepted behavior test: `tests/test_p1_00b_characterization_users_read_all_empty_http.py` (commit `f564bf674a6c5f7ab38be28a2ad92f69ff886ab8`, SHA256 `83151fbafaa7953fbad50136ad564a0bdd2f5d39c1e5943c75a40b3364cf7faa`, seed `API-IDENTITY-GET-USERS-READ-ALL-USERS-L407`, selector `tests/test_p1_00b_characterization_users_read_all_empty_http.py::UsersReadAllEmptyHttpCharacterizationTests::test_empty_list_returns_http_200_without_relation_queries`): characterizes observed empty-list boundary with HTTP200 JSON `[]`, one validated list query, counted `scalars().all()`, real relation helper `attach_user_management_relation_context` returning empty list `[]` without relation queries, and suppression of serializer invocations.
- Repaired provenance test: `tests/test_p1_00b_characterization_recent_inventory_continuation_15_empty_list.py` (commit `c1ad7a81f1f3ace1ecd7745be6a735ed2b1b1123`, SHA256 `703bbeaafc74b200570714f811303a2a1d06b9402e4030a582a76fe81b6602ec`, selector `tests/test_p1_00b_characterization_recent_inventory_continuation_15_empty_list.py::ContinuationFifteenEmptyListProvenanceTests::test_schema_version_wrong_types_rejected`): establishes exact-integer `schema_version=1` and rejection of non-integer/wrong JSON types and values. Tests for this cycle are independently run after generation and not claimed as passed.

Reconciliation status and machine aggregate tracking:
- This is documentation linkage for already accepted evidence only, not aggregate reconciliation or a sixth behavior.
- Pending-five total remains 5 (`units_since_reconciliation=5`, `total_behavior_units=5`), with 2 reconciled (`reconciled_behavior_units=2`) and 3 pending (`remaining_behavior_units=3`):
  - `docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-15.json` (empty-list HTTP200)
  - `docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-16.json` (manager-peer HTTP403)
  - `docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-17.json` (missing-target HTTP404)
  as recorded in existing `docs/refactor/two-server/section-1/inventory/recent-characterization-reconciliation-13-14.json`.
- Reconciliation policy: no sixth behavior accepted before full reconciliation; continuation-12 and all prior rejected ledger proposals remain deferred.
- No edits to inventories, provenance sources, behavior tests, or `docs/refactor/two-server/section-1/inventory/seed-handler-coverage.json`.

Seed accounting, blockers, and Stage status:
- Distinct-seed accounting remains unchanged at 387 of 465 total seeds evidenced (135 baseline + 252 reconciled new), 78 remaining; the `API-IDENTITY-GET-USERS-READ-ALL-USERS-L407` seed is already evidenced and adds zero distinct seeds.
- Stage status: `P1-00` remains `COMPLETE`; `P1-00b` remains `IN_PROGRESS`.
- Both canonical High blockers remain open: `P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE` and `P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN`.
- Origin matrix remains open; seed acceptance status remains `FULL_SEED_ACCEPTANCE_NOT_ASSESSED`.
- Explicit exclusions: nonempty lists, search, include_deleted, middle-manager policy, read_user self-success or super-admin rejection, mutation, persistence, retry, reload, origin cells, seed/blocker/Stage closure. No unit, seed, cell, or blocker is newly accepted or marked closed, and full five-unit reconciliation is not claimed.

### 2026-10-02 UTC — Continuation 16 documentation linkage checkpoint (manager-peer HTTP403)

Assignment `WA-P1-00B-AFTER-AE00-WORK-PRODUCER-20261002-153` (digest `42cf318ef79e18fa02c30dc927178832dcd1adb91fbb4cced713cac348f52f86`, inspected HEAD `93c01c59f828808f5f7ed86803643dbdc4e5695c`) records one bounded documentation-only continuation-16 evidence-linkage entry for open canonical blocker `P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE`:
- Inventory artifact: `docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-16.json` (SHA256 `22e2c7c921ee8e20a2335a1ce1f32e0224f4b1403f2597ef6a11e36d4930fe3a`, last commit `0eebdb41c0300ae0d92a16d5cd0aa8db75ad2b9f`).
- Existing accepted behavior test: `tests/test_p1_00b_characterization_users_read_manager_peer_http.py` (commit `05fe0d5c2a5dae727aa7219549843b1ce6235945`, SHA256 `f093210c5dadda895a370ec1427607c3eade7e593e3133d3551881f2e8e20bcd`, seed `API-IDENTITY-GET-USERS-READ-USER-L440`, selector `tests/test_p1_00b_characterization_users_read_manager_peer_http.py::UsersReadManagerPeerHttpCharacterizationTests::test_middle_manager_peer_read_returns_http_403_before_relation_loading`): describes only observed manager-peer HTTP403 before relation loading/serialization, never claim full handler acceptance.
- Repaired provenance source: `tests/test_p1_00b_characterization_recent_inventory_continuation_16_manager_peer.py` at repair commit `93c01c59f828808f5f7ed86803643dbdc4e5695c`, SHA256 `44629dbe8698a1e2ad0d4be3948b16a6e21d72b77fa92b62421cc97d8746aafe`, and exact selector `tests/test_p1_00b_characterization_recent_inventory_continuation_16_manager_peer.py::ContinuationSixteenManagerPeerProvenanceTests::test_schema_version_wrong_types_rejected`: explains exact int schema_version 1 and independently decoded wrong JSON types/values rejection using existing evidence only. Do not state tests for this new proposal have already passed; acceptance/test receipts are separate.

Reconciliation status and machine aggregate tracking:
- This is documentation linkage for already accepted evidence only, not aggregate reconciliation or new behavior acceptance.
- Keep aggregate reconciliation exactly 2/5 with pending-five=5, units_since_reconciliation=5, total_behavior_units=5, reconciled_behavior_units=2, remaining_behavior_units=3; explicitly keep continuations 15 empty-list HTTP200, 16 manager-peer HTTP403, 17 missing-target HTTP404 pending and no sixth behavior before complete reconciliation as recorded in `docs/refactor/two-server/section-1/inventory/recent-characterization-reconciliation-13-14.json`.
- Reconciliation policy: no sixth behavior before complete reconciliation; continuation-12 and all prior rejected ledger proposals remain deferred.
- Do not change inventory, counters, seed-handler coverage, test files, runtime/source, existing accepted evidence, or deferred prior proposals. This linkage adds no distinct seed and does not complete five-unit reconciliation.

Seed accounting, blockers, and Stage status:
- Retain coverage 387/465 (135 baseline + 252 reconciled new), 78 remaining, FULL_SEED_ACCEPTANCE_NOT_ASSESSED, both canonical High blockers open (`P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE` and `P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN`), P1-00 COMPLETE and P1-00b IN_PROGRESS.
- Exclude read_user self-success, super-admin rejection, success/mutation/persistence/retry/reload, origin cells and all seed/cell/blocker/Stage closure.

### 2026-10-02 UTC — Continuation 17 documentation linkage checkpoint (missing-target HTTP404)

Assignment `WA-P1-00B-AFTER-AE00-WORK-PRODUCER-20261002-153` (sha256 `42cf318ef79e18fa02c30dc927178832dcd1adb91fbb4cced713cac348f52f86`, inspected HEAD `13098a05c56241a7a433f5a8f383fe333702cbca`) records one bounded documentation-only continuation-17 evidence-linkage entry for open canonical blocker `P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE`:
- Inventory artifact: `docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-17.json` (SHA256 `f3d6f12ade5bb8933472a727ba3e880070bdca758148b2a98ce81e404c18bd15`, last commit `67931e960a1361ddb49f1be603eb1553cd1a0687`).
- Existing accepted behavior test: `tests/test_p1_00b_characterization_users_read_missing_http.py` (accepted commit `0f3deb613251c7c5f39822b2a1f0e5532e5e140f`, SHA256 `7ae68ea6d3239cf6ab844b3087efe45a56a38b068308316861362b1ff71c8dfb`, seed `API-IDENTITY-GET-USERS-READ-USER-L440`, exact selector `tests/test_p1_00b_characterization_users_read_missing_http.py::UsersReadMissingHttpCharacterizationTests::test_authorized_missing_target_returns_http_404_after_single_get`): describes only observed authorized missing-target HTTP404 JSON detail `User not found` after exactly one target `db.get(models.user.User, 404)` returning `None`. Characterization overrides authorization; do not claim authorization-policy coverage or full handler acceptance.
- Provenance test: `tests/test_p1_00b_characterization_recent_inventory_continuation_17_missing_target.py` (last commit `67931e960a1361ddb49f1be603eb1553cd1a0687`, SHA256 `07d8cb0582b2afa110997a254fd6ed560777ecff1a86224413b03dbb66f97578`, exact selector `tests/test_p1_00b_characterization_recent_inventory_continuation_17_missing_target.py::ContinuationSeventeenMissingTargetProvenanceTests::test_exact_missing_target_provenance`): validates existing provenance schema version `schema_version=1`, stage `P1-00b`, predecessor linkage to continuation-16, single record hash, and AST selector resolution using existing evidence only. Do not invent test receipts or claim proposal tests passed; acceptance and test evidence are separate.

Reconciliation status and machine aggregate tracking:
- This is documentation linkage for already accepted evidence only, not aggregate reconciliation or new behavior acceptance.
- Keep aggregate reconciliation exactly 2/5 with pending-five=5: `units_since_reconciliation=5`, `total_behavior_units=5`, `reconciled_behavior_units=2`, `remaining_behavior_units=3`.
- Explicitly retain continuations 15 empty-list HTTP200, 16 manager-peer HTTP403, and 17 missing-target HTTP404 pending, as recorded in `docs/refactor/two-server/section-1/inventory/recent-characterization-reconciliation-13-14.json`.
- Reconciliation policy: linkage is existing evidence only and does not reconcile the aggregate; no sixth behavior before complete reconciliation; continuation-12 and all prior rejected ledger proposals remain deferred.
- No inventory, counter, seed-coverage, test, source, or previously accepted evidence edits.

Seed accounting, blockers, and Stage status:
- Retain distinct coverage 387/465 (135 baseline + 252 reconciled new), 78 remaining, `FULL_SEED_ACCEPTANCE_NOT_ASSESSED`, `P1-00` `COMPLETE` and `P1-00b` `IN_PROGRESS`.
- Both canonical High blockers remain open: `P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE` and `P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN`.
- Add zero distinct seeds and do not claim complete five-unit reconciliation.
- Explicitly exclude read_user self-success, super-admin rejection, other successful reads, mutation, persistence/retry/reload, origin cells and seed/cell/blocker/Stage closure.

### 2026-10-02 UTC — Continuation 17 partial reconciliation checkpoint (missing-target HTTP404)

Assignment `WA-P1-00B-AFTER-OBSERVER-PROJECTION-20261002-161` (digest `b2dbac223995215c207ba881b3eea2673bb6b428a7962cb9beb9562c69156883`, inspected HEAD `171c3d3cad480cf33bbe62232ac2fff75b21d3ff`) records one bounded partial reconciliation checkpoint for accepted continuation 17 missing-target HTTP404 characterization under open canonical blocker `P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE`:
- Receipt artifact: `docs/refactor/two-server/section-1/inventory/recent-characterization-reconciliation-17.json` as an incremental successor binding prior checkpoint `docs/refactor/two-server/section-1/inventory/recent-characterization-reconciliation-13-14.json` (commit `5cbd4670983759b1f68b5c0fd78b77955b694e1f`, SHA256 `80e23877c849688cddb567b88898ee1440adee0a65a07237b5325d36dcab0a6d`).
- Source inventory artifact: `docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-17.json` (SHA256 `f3d6f12ade5bb8933472a727ba3e880070bdca758148b2a98ce81e404c18bd15`, last commit `67931e960a1361ddb49f1be603eb1553cd1a0687`).
- Accepted C17 behavior test: `tests/test_p1_00b_characterization_users_read_missing_http.py` (accepted commit `0f3deb613251c7c5f39822b2a1f0e5532e5e140f`, SHA256 `7ae68ea6d3239cf6ab844b3087efe45a56a38b068308316861362b1ff71c8dfb`, seed `API-IDENTITY-GET-USERS-READ-USER-L440`, selector `tests/test_p1_00b_characterization_users_read_missing_http.py::UsersReadMissingHttpCharacterizationTests::test_authorized_missing_target_returns_http_404_after_single_get`).

Reconciliation status and unit tracking:
- Incremental C17 reconciliation advances reconciled units from historical prior 2/5 to current 3/5:
  - `units_since_reconciliation`: 5
  - `total_behavior_units`: 5
  - `pending_five`: 5
  - `delta_reconciled_behavior_units`: 1
  - `historical_prior_reconciled_units`: 2
  - `reconciled_behavior_units`: 3
  - `remaining_behavior_units`: 2
- Cumulative reconciled inventories: continuations 13, 14, and 17 (`cumulative_inventory_paths`).
- Remaining inventory paths in pending-five group remain exactly 2:
  - `docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-15.json` (empty-list HTTP200)
  - `docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-16.json` (manager-peer HTTP403)
- Policy: `global_reconciliation_complete=false`; `no_sixth_behavior_accepted_before_complete_reconciliation=true`; continuation-12, 153/154/155/157-C001, normal159 C1/C2 (including rejected C15 machine checkpoint and policy/seed proposals) remain deferred.

Seed accounting, blockers, and Stage status:
- C17 seed `API-IDENTITY-GET-USERS-READ-USER-L440` is prospective distinct (count 1), not in canonical 387 set, and overlaps with pending continuation-16 (overlapping C16 must never increment prospective distinct count again).
- Canonical coverage counts remain unchanged at 387 of 465 total seeds (135 baseline + 252 reconciled new, 78 remaining); seed acceptance status remains `FULL_SEED_ACCEPTANCE_NOT_ASSESSED`.
- Canonical High blockers remain open: `P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE` and `P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN` (24 origin cells unresolved).
- Stage status: `P1-00` `COMPLETE`; `P1-00b` `IN_PROGRESS`; `closures_authorized=false`.
- Exclusions: read_user self-success, super-admin rejection, authorization-policy coverage, mutation, persistence, retry, reload, origin cells, seed/blocker/Stage closure.

### 2026-10-03 UTC — Continuation 16 documentation linkage checkpoint (manager-peer HTTP403)

Assignment `WA-P1-00B-GATE-FIRST-RECONCILIATION-20261002-169` (digest `5f8cbf6411079ac471fbf364568a2f54997abc60253b67ec72aae88eae3cc23e`, inspected HEAD `fcc9ba2ee11ef589dae2216918f43a6aae9bd1e4`) records one bounded documentation-only C16 current evidence-linkage entry for open canonical blocker `P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE`:
- Inventory artifact: `docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-16.json` (SHA256 `22e2c7c921ee8e20a2335a1ce1f32e0224f4b1403f2597ef6a11e36d4930fe3a`, commit `0eebdb41c0300ae0d92a16d5cd0aa8db75ad2b9f`).
- Unchanged behavior test: `tests/test_p1_00b_characterization_users_read_manager_peer_http.py` (SHA256 `f093210c5dadda895a370ec1427607c3eade7e593e3133d3551881f2e8e20bcd`, accepted commit `05fe0d5c2a5dae727aa7219549843b1ce6235945`, exact selector `tests/test_p1_00b_characterization_users_read_manager_peer_http.py::UsersReadManagerPeerHttpCharacterizationTests::test_middle_manager_peer_read_returns_http_403_before_relation_loading`): describes only observed HTTP403 for actor middle-manager id 7 reading target middle-manager id 8 after the one exact target get, before relation loading/serialization; authentication is overridden, so no authentication-policy acceptance.
- Auxiliary shared-seed witness: `tests/test_p1_00b_characterization_c16_c17_shared_seed_contribution.py` at accepted commit `ea1f081ab54bee071c56f2f59050a719b2559b41`, exact selector `tests/test_p1_00b_characterization_c16_c17_shared_seed_contribution.py::C16C17SharedSeedContributionTests::test_false_double_count_rejected_and_complete_evidence_restored` (existing evidence assertion only; not newly accepted product behavior).
- Auxiliary pinned-helper restoration: `tests/test_p1_00b_characterization_c16_pinned_helper_restoration.py` at accepted commit `fcc9ba2ee11ef589dae2216918f43a6aae9bd1e4`, exact selector `tests/test_p1_00b_characterization_c16_pinned_helper_restoration.py::C16PinnedHelperRestorationTests::test_pinned_helper_source_drift_rejected_and_complete_closure_restored` (existing evidence assertion only; not newly accepted product behavior).

Reconciliation status and machine aggregate tracking:
- Current accounting provenance from `docs/refactor/two-server/section-1/inventory/recent-characterization-reconciliation-c15-from-c17.json` at commit `c66dabdfe845d6f69623986c0705c415dfcf089c` (SHA256 `b7dac667be1584c50743b24d806d2b9d1c6fe50a85ec1e9936a65ed96754ffbb`): `reconciled_behavior_units=4`, `remaining_behavior_units=1` (C16 manager-peer HTTP403 only), `total_behavior_units=5`, `pending_five=5`, `units_since_reconciliation=5`, `global_reconciliation_complete=false`.
- This linkage leaves those counters untouched and does not reconcile C16.
- Preserve original snapshots, receipts and all deferrals, explicitly retaining `defer_prior_unit=normal169-c16-receipt-custody-20261003-c001` and `defer_prior_unit=normal169-c16-machine-linkage-20261003-c001-v2`. No rejected artifacts are evidence; no sixth behavior before complete reconciliation.

Seed accounting, blockers, and Stage status:
- Distinguish prospective seeds without changing counts: C16 and C17 both reference `API-IDENTITY-GET-USERS-READ-USER-L440`; their union contributes one prospective distinct seed, and C16 adds zero after C17. C13 separately references `API-TRADE-GET-TRADES-EXPORT-TRADES-WITH-USER-L5797`, also prospective and absent from the canonical 387. The group has those two prospective distinct seeds, not two read_user contributions. Do not reinterpret the C15-from-C17 receipt scoped read_user `prospective_count=1` as a complete five-unit distinct union, change historic receipts, claim canonical promotion, or claim `FULL_SEED_ACCEPTANCE`.
- Retain canonical 387/465 (135 baseline + 252 reconciled new), 78 remaining seeds, 24 unresolved origins, `PARTIAL` and `FULL_SEED_ACCEPTANCE_NOT_ASSESSED`.
- Both canonical High blockers remain open: `P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE` and `P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN`.
- Stage status: `P1-00` `COMPLETE`; `P1-00b` `IN_PROGRESS`; `closures_authorized=false`.
- Explicit exclusions: read_user self-success, super-admin rejection, other read success/authentication-policy acceptance, mutation, persistence, retry, reload, origin cells, and seed/cell/blocker/Stage closure. Canonical accounting, those remaining read_user scenarios and Stage closure stay unmet. This documentation unit grants no behavior/seed/origin progress. Reuse existing task selectors; do not author tests or invent pass receipts.

### 2026-10-03 UTC — Continuation 16 evidence reconciliation successor checkpoint (manager-peer HTTP403)

Assignment `WA-P1-00B-GATE-FIRST-RECONCILIATION-20261002-169` (digest `5f8cbf6411079ac471fbf364568a2f54997abc60253b67ec72aae88eae3cc23e`, inspected HEAD `8e5590383b40c46c4b5c2b97c20208b0bfb650a7`, Stage `P1-00b`, parent `WA-P1-00B-FREEZE-20260904-001`) records one bounded incremental evidence-reconciliation successor for accepted continuation 16 under open canonical blocker `P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE`:
- Receipt artifact: `docs/refactor/two-server/section-1/inventory/recent-characterization-reconciliation-c16-from-c15.json` as the incremental successor binding unchanged predecessor checkpoint `docs/refactor/two-server/section-1/inventory/recent-characterization-reconciliation-c15-from-c17.json` (commit `c66dabdfe845d6f69623986c0705c415dfcf089c`, SHA256 `b7dac667be1584c50743b24d806d2b9d1c6fe50a85ec1e9936a65ed96754ffbb`).
- Source inventory artifact: `docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-16.json` (SHA256 `22e2c7c921ee8e20a2335a1ce1f32e0224f4b1403f2597ef6a11e36d4930fe3a`, commit `0eebdb41c0300ae0d92a16d5cd0aa8db75ad2b9f`).
- Unchanged accepted behavior test: `tests/test_p1_00b_characterization_users_read_manager_peer_http.py` (commit `05fe0d5c2a5dae727aa7219549843b1ce6235945`, SHA256 `f093210c5dadda895a370ec1427607c3eade7e593e3133d3551881f2e8e20bcd`, seed `API-IDENTITY-GET-USERS-READ-USER-L440`, selector `tests/test_p1_00b_characterization_users_read_manager_peer_http.py::UsersReadManagerPeerHttpCharacterizationTests::test_middle_manager_peer_read_returns_http_403_before_relation_loading`).
- Shared-seed witness: `tests/test_p1_00b_characterization_c16_c17_shared_seed_contribution.py` at accepted commit `ea1f081ab54bee071c56f2f59050a719b2559b41`.
- Pinned-helper restoration: `tests/test_p1_00b_characterization_c16_pinned_helper_restoration.py` at accepted commit `fcc9ba2ee11ef589dae2216918f43a6aae9bd1e4`.
- Live successor assertion test: `tests/test_p1_00b_characterization_c16_successor_reconciliation.py::C16SuccessorReconciliationTests::test_c16_successor_reconciles_accepted_unit_and_preserves_open_gates`.

Reconciliation status and unit tracking:
- 5/5 existing behavior evidence reconciled (`reconciled_behavior_units=5`, `remaining_behavior_units=0`).
- Retained five-unit provenance counters: `total_behavior_units=5`, `pending_five=5`, `units_since_reconciliation=5`, `delta_reconciled_behavior_units=1`, `historical_prior_reconciled_units=4`.
- Cumulative inventory paths (order preserved): continuations 13, 14, 17, 15, and 16; remaining inventory paths empty.
- Reconciliation policy: five-unit evidence group reconciled while canonical accounting remains pending (`global_reconciliation_complete=false`, `no_sixth_behavior_accepted_before_complete_reconciliation=true`).
- Deferrals preserved: continuation-12, prior rejected proposals, explicitly retaining `defer_prior_unit=normal169-c16-receipt-custody-20261003-c001` and `defer_prior_unit=normal169-c16-machine-linkage-20261003-c001-v2`. Rejected artifacts are never evidence.

Seed accounting, blockers, and Stage status:
- Scoped read_user accounting view remains `prospective_distinct_seed_count=1` (`API-IDENTITY-GET-USERS-READ-USER-L440` shared between C16 and C17; zero new distinct seed contribution from C16).
- Complete five-unit prospective distinct union is exactly two seeds (count 2: `API-IDENTITY-GET-USERS-READ-USER-L440` plus C13 `API-TRADE-GET-TRADES-EXPORT-TRADES-WITH-USER-L5797`). C14/C15 `API-IDENTITY-GET-USERS-READ-ALL-USERS-L407` is already canonical.
- Retained canonical counts: 135 baseline, 252 reconciled new, 387 evidenced, 78 remaining, 465 expected; coverage status `PARTIAL`; `FULL_SEED_ACCEPTANCE_NOT_ASSESSED`.
- Canonical High blockers remain open: `P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE` and `P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN` (24 unresolved origin cells; `closures_authorized=false`).
- Stage status: `P1-00` `COMPLETE`; `P1-00b` `IN_PROGRESS`. No mechanical receipt is Stage closure.
- Explicit exclusions: read_user self-success, super-admin rejection, authorization policy coverage, mutation, persistence, retry, reload, origin matrix cells, seed-blocker closure, and Stage closure.

### 2026-10-03 UTC — Continuation 13 canonical ledger integration checkpoint (trade export self-delegation)

Assignment `WA-P1-00B-GATE-FIRST-RECONCILIATION-20261002-169` (digest `5f8cbf6411079ac471fbf364568a2f54997abc60253b67ec72aae88eae3cc23e`, inspected HEAD `62a7c05f22b77150ad3724f47bfafc0967be4cf6`, Stage `P1-00b`, parent `WA-P1-00B-FREEZE-20260904-001`) records one bounded documentation-only C13 current accounting entry integrating accepted C13 canonical promotion evidence under open canonical blocker `P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE`:
- Accepted C13 promotion receipt: `docs/refactor/two-server/section-1/inventory/recent-characterization-c13-canonical-promotion.json`, commit `ff8a08490a0555bc7afcc56c6e189294751e8eec`, SHA256 `5ab85485719dfe5635ed9c106082b0453108336b5791f6abc51c4c5bf2f8430b`. Receipt `inspected_head` `b8f2aea4367f3f08195e32522396f980351057cf` remains historical, not a current HEAD.
- Historical 388 snapshot: `docs/refactor/two-server/section-1/inventory/recent-characterization-seed-coverage-388-snapshot.json`, commit `62a7c05f22b77150ad3724f47bfafc0967be4cf6`, SHA256 `da0fd31c0dbc4776b548a8b235f8479fe8e60687ff51e0cb2623c3c21d4c42d7`; live `seed-handler-coverage.json` currently has the same bytes/hash, while the historical snapshot stays immutable.
- Existing witness test selector: `tests/test_p1_00b_characterization_c13_only_canonical_promotion.py::C13CanonicalPromotionTests::test_c13_canonical_promotion_retains_shared_seed_gate` cited strictly in its role as existing evidence (no invented new run receipts).

Current canonical counts from accepted evidence:
- Canonical counts: 135 baseline + 253 reconciled new = 388/465 evidenced, 77 remaining, `PARTIAL`, `FULL_SEED_ACCEPTANCE_NOT_ASSESSED`.
- C13 seed `API-TRADE-GET-TRADES-EXPORT-TRADES-WITH-USER-L5797` was already promoted with receipt `delta_distinct_seed_count=1`; this ledger unit contributes zero additional distinct seeds.
- Older 387/78 ledger entries are historical and remain unchanged.
- Shared C16/C17 seed `API-IDENTITY-GET-USERS-READ-USER-L440` is still pending canonical promotion and canonical ledger accounting; its shared union is one seed, C16 contributes zero after C17.

Reconciliation status, blockers, and Stage status:
- Retain five-unit evidence reconciliation 5/5 (`reconciled_behavior_units=5`, `remaining_behavior_units=0`) and all provenance counters `pending_five=5`, `total_behavior_units=5`, `units_since_reconciliation=5`, `global_reconciliation_complete=false`; no sixth behavior before actual complete canonical reconciliation.
- Both High blockers stay open: `P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE` and `P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN`, 24 unresolved origins, `closures_authorized=false`, `P1-00` `COMPLETE` and `P1-00b` `IN_PROGRESS`.
- `read_user` self-success and super-admin rejection remain unmet.
- Auxiliary docs/tests/CONTINUE add no product behavior or Stage progress.

Deferrals and exclusions:
- Preserve all prior receipts/counters/deferrals and explicitly retain these exact strings: defer_prior_unit=normal169-c16-c17-union-promotion-with-custody-20261003-c014; defer_prior_unit=normal169-c16-c17-canonical-promotion-20261003-c012; defer_prior_unit=normal169-c16-c17-canonical-promotion-20261003-c012-v2; defer_prior_unit=normal169-canonical-manifest-successor-20261003-c010-v2; defer_prior_unit=normal169-c17-consumer-loader-integration-20261003-c007; defer_prior_unit=normal169-c17-historical-coverage-reader-isolation-20261003-c005; defer_prior_unit=normal169-historical-coverage-isolation-20261003-c004; defer_prior_unit=normal169-c16-receipt-custody-20261003-c001; defer_prior_unit=normal169-c16-machine-linkage-20261003-c001-v2. Rejected artifacts are not evidence.
- State exclusions: shared C16/C17 canonical promotion/integration; read_user self-success; super-admin rejection; authorization policy; new/sixth behavior; mutation/persistence/retry/reload; origin/seed-blocker/Stage closure. No tracked file other than the ledger changes; no test, manifest, snapshot or historical receipt edits.
