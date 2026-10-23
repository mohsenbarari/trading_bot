# Coverage CI follow-up — 2026-10-23

## Status and evidence boundary

Official **Coverage Report run `39993855386`**, commit
`aa8050b67f0da3bf391842a45dcbd44b872d3d1b`, **failed and is not accepted**.
Do not describe generated coverage reports as a passing release gate.

Observed results:

| Check | Result |
| --- | --- |
| Initial backend unittest discovery | 6,635 tests; 14 failures, 82 errors, 350 skips; 821.090 seconds |
| Additional PRIVATE_PRIMARY pytest selection | Three failing cases, all permission failures in the choreography-plan builder tests |
| PostgreSQL registration/counter opt-in | Counter tests reach teardown, then the requested downgrade is deliberately rejected |
| Frontend unit coverage | Exit code 0; reported coverage 90.4% |
| Stage 9 infrastructure contract tests | 156 tests passed |
| Stage 9 mutation/evidence | Mutation statistics collection aborts; runtime/traceability evidence is not complete |
| Final covered-suite check | Backend exit code 1; frontend exit code 0 |

The reported backend 78.0% and repository-weighted 79.2% coverage are descriptive
outputs from a failed run, not quality approval. Skips are reported, not newly
authorized. No thresholds, assertions, skips, or gates were weakened.

Read-only evidence was obtained from native GitHub CLI job logs and artifact
`12520296462` (`repository-coverage-1026`, 25,733,860 compressed bytes). The archive
was read in memory with a 32 MiB limit; only bounded test/mutation summaries were
displayed. No production settings, credentials, sessions, or data were inspected.

## Priorities and exact failing groups

### P1 — resolve attribution before claiming runtime regression or safety

1. `tests.test_offers_router_expire` has two CI failures:
   - `test_expire_offer_rejects_missing_foreign_or_inactive_offers`: `409 != 404`.
   - `test_expire_offer_rejects_rate_limited_or_daily_limit_requests`: `409 != 403`.

   Both dependency baselines pass all 13 tests in this module when run alone in
   network-disabled containers. Full-suite/shared Redis or global-state
   interaction is a hypothesis, **not a confirmed cause**. This comparison does
   not recreate the complete CI execution order or shared Redis state.

2. `tests.test_market_export_historical_scan` has three failing assertions across
   two methods:
   - `test_historical_queries_use_non_xau_index_and_bucket_key_lookup`: `0 != 2`.
   - `test_same_rows_and_order_for_mixed_history_and_ledger_states`: empty results
     differ from expected rows for `limit=20` and `limit=1000`.

   All three differences reproduce with **both old and security-updated
   dependencies against the same current source**. This is a proven existing
   baseline defect, not a new-only dependency failure. It still needs an owned
   fix; no assertion should simply be removed or rebaselined.

3. Stage 9 mutation generation produces nine mutated files, then aborts during
   statistics collection with `BadTestExecutionCommandsException` in mutmut's
   pytest runner. The log does not preserve the underlying pytest error because
   debug output was disabled. **The actual cause and old/new attribution remain
   unresolved.** Missing downstream runtime/traceability evidence is a cascade,
   not an independent proof that product behavior failed.

No additional auth/upload test failure appears in the initial unittest failure
header inventory. This is not a substitute for complete passing CI or browser
acceptance.

### P2 — repair test-environment gaps without changing product safeguards

The initial 82 unittest errors group as follows:

| Module | Errors | Evidence / next investigation |
| --- | ---: | --- |
| `test_coin_intelligence_offer_condition_training` | 1 | Missing `joblib` |
| `test_coin_offer_condition_calibration` | 1 | Missing `numpy` |
| `test_coin_offer_condition_owner_review` | 1 | Missing `joblib` |
| `test_prepare_private_primary_control_release` | 37 | Fixture setup creates `/root/pp-prep-*`; hosted runner is not root |
| `test_provision_private_primary_secrets` | 14 | Fixtures assume root-owned locations / permissions |
| `test_quiesce_production_legacy_market_collectors` | 25 | Inspect fixture/ownership errors individually; do not assume identical causes |
| `test_production_coin_inference_snapshot_relay` | 1 | Fake systemd log is absent after installer refusal |
| `test_production_market_pipeline_release` | 1 | `test_control_payload_is_commit_exact_minimal_and_tamper_evident` |
| `test_telegram_queue_stage4_harness` | 1 | `test_scanner_report_is_directly_recomputed`: fixture report permission error |

Research dependencies are declared separately in
`apps/coin_rate_estimator/requirements-research.txt`, not the production/test
requirements installed by this job. Decide an explicit research-test dependency
surface; do not add heavy research frameworks to production to silence imports.

The remaining unittest assertion failures, in addition to the five P1 failures,
are:

- `test_market_pipeline_stage3_foundation.test_secret_contract_requires_root_only_parent_and_shared_runtime_group`.
- `test_production_coin_inference_snapshot_relay.test_installer_rolls_back_units_and_timer_state_after_installed_verify_failure`
  (`root_required` instead of the expected rollback evidence).
- `test_production_deploy_choreography.test_mocked_iran_runtime_env_install_uses_same_directory_atomic_promotion`
  (exit 28 instead of 0; not attributed yet).
- `test_production_private_primary_control_release_smoke.test_help_and_import_survive_hostile_cwd_path_and_git_env`
  (a selected system-Python subprocess cannot import `pydantic`).
- `test_quiesce_production_legacy_market_collectors.test_quiesce_persists_prior_state_before_mutation_and_recovers_failure`.
- `test_quiesce_production_legacy_market_collectors.test_sigkill_between_prepared_and_wal_retries_same_journal`.
- `test_telegram_delivery_callsite_inventory.test_every_runtime_callsite_is_classified_and_remaining_counts_do_not_grow`.
- `test_telegram_delivery_callsite_inventory.test_runtime_inventory_matches_exact_reviewed_baseline`.
- `test_telegram_delivery_callsite_inventory.test_source_guards_reject_mini_app_revival_and_api_otp_gateway`.

The three additional pytest failures are in
`tests/test_build_production_private_primary_choreography_plan.py`:

- `test_generated_plan_passes_controller_structural_validator`.
- `test_official_builder_receipt_is_accepted_by_controller_validate`.
- `test_handmade_receipt_without_input_inventory_is_rejected`.

All three report `PermissionError` for the fixture's
`/root/secure-envs/trading-bot/market-pipeline-cutover/web-legacy-handoff.json` path.
Do not weaken production root/ownership checks or run the entire suite as root
as a blanket workaround; isolate the test prerequisites and preserve rejection
coverage.

The opt-in failure is
`tests.test_user_counter_sync_postgres.UserCounterPostgresTests.tearDownClass`:
line 143 requests downgrade to `f7c8d9e0a1b2`, crossing migration
`fb1c2d3e4f5a`, whose downgrade deliberately refuses destructive schema reversal.
The migration safeguard must remain intact; investigate scratch-fixture cleanup.

## Controlled dependency comparisons

Existing cached images only; no builds or downloads:

| Image | Selected dependency versions |
| --- | --- |
| Old `bb639822737a9eef992b9ff14095bf3abd6d5e927c16e5fdeb0386190d1d6291` | FastAPI 0.111.0; Starlette 0.37.2; Pydantic 2.8.2; aiogram 3.10.0; aiohttp 3.9.5 |
| Security `61e83a73ebd37b8582140a4c0cc9abcd8895a8b0b4f5289dc1fd07d2bae78f34` | FastAPI 0.161.1; Starlette 1.9.0; Pydantic 2.13.4; aiogram 3.31.0; aiohttp 3.14.5 |

Both have SQLAlchemy 2.0.31. Only standard public Python image environment keys
were observed; no values were displayed. Each comparison used:

- The same current public source, exact selected test modules, and tracked
  non-deployable example configuration mounted read-only.
- Explicit `APP_ENV_FILE=/work/config/unit-test.env.example` before application
  imports, cleared process environment, `SERVER_MODE=foreign`, `ENVIRONMENT=test`.
- `--network=none`, a socket/DNS fence, read-only container root, UID/GID 65534,
  dropped capabilities, no new privileges, private `/work` and `/tmp` tmpfs,
  512 MiB memory, one CPU, 64 PIDs, and a 90-second per-container deadline.
- No published ports, production mount, inherited `.env`, database, session,
  image build/retag, or global runtime mutation. Exact owned containers were
  removed after completion.

The first historical comparison omitted the required public registry fixture
and produced identical `FileNotFoundError` results on both versions. Those are
**harness errors, not product evidence**. The final comparison added only the
read-only tracked `config/market_data_sources.v1.json` fixture and reproduced the
three actual historical assertion failures with zero errors or skips.

For the Telegram callsite fingerprint, a full scan hit its 35-second bound and
does **not** count as a completed scan. A subsequent scoped scan used the actual
audit visitor against all runtime files changed since pre-security `95283032`:
`main.py`, `core/frontend_static.py`, and
`core/services/chat_upload_session_service.py`. Old and current contents both
produce zero callsite rows. The scanner and every other scanned runtime file
are unchanged, so the security changes introduce **zero inventory-row delta**;
they did not introduce the existing fingerprint drift. This is static evidence,
not a complete runtime inventory reapproval.

## Reinspection commands and handoff

Read official metadata and failure excerpts without rerunning or changing CI:

```bash
gh run view 39993855386 --json headSha,status,conclusion,jobs
gh run view 39993855386 --job 120552173722 --log-failed
gh api 'repos/{owner}/{repo}/actions/runs/39993855386/artifacts'
```

Inspect only bounded excerpts. Never print authentication headers, signed
artifact-download URLs, live settings, or exception inputs containing secrets.

The separate required pytest selection in the original workflow is:

```bash
APP_ENV_FILE=config/unit-test.env.example PYTHONWARNINGS=error \
python3 -m coverage run --branch --parallel-mode -m pytest -q \
  tests/test_build_production_private_primary_choreography_plan.py \
  tests/test_production_private_primary_choreography.py \
  tests/test_promote_production_private_primary_product.py \
  tests/test_reconcile_estimator_snapshot_publication_outbox.py \
  tests/test_verify_production_private_primary_promotion.py
```

Run reproduction commands only in an isolated test environment. Do not execute
production bootstrap or maintenance to make these tests green. File equality
alone is not proof of dependency compatibility; old/new comparisons above have
deliberately limited scope. Mutation's original pytest error and the full-suite
expiry interaction remain open, and complete passing release evidence is still
required.

## Browser follow-up after bind-path recovery

On `8dcf27b1`, run `39998590240` passed the three initial gates and reached real
browser assertions. At diagnosis, five browser shards had passed; Chromium
shard 4 reported 18 failures, one flaky retry-pass, 26 skipped and 29 passed;
Firefox shard 4 reported 19 failures, 26 skipped and 29 passed. These are not
complete passing E2E results. The responsive 414px case was flaky, not the final
failure attributed to it.

The observed blockers were stale notification/registration selectors and absent
host-side mutation authorization. The notification login helper expected only
the read-state label although the existing UI legitimately renders the unread
label. Registration expected obsolete spelling/ARIA/status text and used an
expired fixed invitation fixture. Six trade-history cases correctly refused
mutation because the Playwright process did not receive the required target and
confirmation. A container's `.env` never supplies that process authorization.

The focused correction changes only E2E tests and both hosted-runner workflows:
require a unique visible notification control with one of the two exact labels;
use current exact registration labels, a future invitation and its exact rendered
expiry; authorize mutation explicitly for the disposable local Docker stack,
loopback backend and exact app/Redis names. `mutationRuntime` and product code
are unchanged. No assertion/negative case, skip, retry or timeout is relaxed.

Seven hermetic CI/selector contracts passed independently and in root's rerun.
Playwright collected 15 cases in two affected files with the existing toolchain;
collection is not browser execution. A new full CI run must establish actual
browser acceptance; the unrelated coverage blockers above remain open.

## Actual follow-up on `00588776`

Run `40004366645` reached browser assertions after the corrections: shard4
Chromium reported8failed/2flaky/38passed/26skipped, Firefox10failed/38passed/
26skipped, and WebKit6failed/2flaky/40passed/26skipped. These are failed shards,
not approval. Failures remain in notification login/control/toast cases and
trade-history profile/block/load-more cases. The login/realtime cause and
dependency-upgrade attribution remain unresolved.

Three trade-history test-contract mismatches are source-proven on both the
pre-security `95283032` and CI source: the profile assertion expects removed
privacy copy, blocking expects native page.dialog although the UI uses
AppConfirmDialog, and load-more waits for `/api/trades/my` although the component
requests `/api/trades/my/page`. This limited comparison does not prove every
browser failure pre-existing or authorize weaker assertions/UI changes.

Coverage run `40004366552` failed:6,666unittests still report14failures,
82errors and350skips, with the same three additional root-fixture pytest errors.
Stage9's missing INV-004 evidence is a demonstrated cascade: the scratch-suite
runner stops when the counter suite's deliberately forbidden downgrade fails,
before running Stage3 registration. Do not weaken the registry or migration guard.

Artifact `12524338036` (`repository-coverage-1029`,25,784,060bytes) was read only
in bounded RAM; the26,295byte mutation log still reports
BadTestExecutionCommandsException during baseline pytest statistics. Its
underlying pytest exception is not preserved with mutmut debug off. Nine files
were mutated but results marked not checked are not genuine survivor evidence.

A network-none/read-only/nonroot resource-bounded probe of the existing cached
security validation image found pytest absent. The container was auto-removed
and its absence verified; no dependency was installed, and the selected eight
baseline tests were **not run**. A reproduction needs the actual test toolchain;
do not repeat a production-image attempt or substitute unittest as pytest proof.
Host maintenance and boot-mount setup completed independently; none of these
remaining application acceptance gates was waived.
