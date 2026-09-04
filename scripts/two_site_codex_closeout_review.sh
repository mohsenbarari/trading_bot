#!/usr/bin/env bash
set -euo pipefail
umask 077

usage() {
  cat <<'EOF'
Usage:
  scripts/two_site_codex_closeout_review.sh \
    --stage <STAGE_ID> --assignment <ASSIGNMENT_ID> \
    --bundle <RELATIVE_BUNDLE_PATH> --accepted-head <FULL_SHA>

  scripts/two_site_codex_closeout_review.sh --verify-commit \
    --stage <STAGE_ID> --assignment <ASSIGNMENT_ID> \
    --bundle <RELATIVE_BUNDLE_PATH> --accepted-head <FULL_SHA> \
    --closeout-head <FULL_SHA>

The review mode asks a fresh read-only Codex reviewer to authorize the exact staged
mechanical closeout patch. The verification mode proves that the resulting commit is
the unchanged, single-parent child authorized by that receipt.
EOF
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command is unavailable: $1"
}

repo_root="$(git rev-parse --show-toplevel 2>/dev/null)" || die "not inside a Git repository"
repo_root="$(realpath -e "$repo_root")"
schema_rel="docs/refactor/two-server/templates/closeout-review.schema.json"
schema_path="$repo_root/$schema_rel"
source_authorization="$repo_root/.local/two-site-refactor/authorizations/CODEX_SOURCE_REVIEW.md"
codex_bin="$(command -v codex 2>/dev/null || true)"
verify_commit=false
stage_id=""
assignment_id=""
bundle_rel=""
accepted_head=""
closeout_head=""

while (($#)); do
  case "$1" in
    --verify-commit) verify_commit=true; shift ;;
    --stage|--assignment|--bundle|--accepted-head|--closeout-head)
      (($# >= 2)) || die "missing value for $1"
      case "$1" in
        --stage) stage_id="$2" ;;
        --assignment) assignment_id="$2" ;;
        --bundle) bundle_rel="$2" ;;
        --accepted-head) accepted_head="$2" ;;
        --closeout-head) closeout_head="$2" ;;
      esac
      shift 2
      ;;
    --help|-h) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

require_command git
require_command jq
require_command realpath
require_command sha256sum
require_command date
require_command awk
require_command cmp
require_command grep
require_command sed
[[ "$stage_id" =~ ^[A-Z][A-Z0-9]*-[0-9]+[a-z]?$ ]] || die "invalid Stage ID"
[[ "$assignment_id" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || die "invalid Assignment ID"
[[ "$accepted_head" =~ ^[0-9a-f]{40}$ ]] || die "accepted head must be a full lowercase SHA"

expected_bundle=".local/two-site-refactor/review-bundles/${stage_id}-${assignment_id}"
[[ "$bundle_rel" == "$expected_bundle" ]] || die "bundle must be exactly $expected_bundle"
bundle_abs="$repo_root/$bundle_rel"
test -d "$bundle_abs" || die "review bundle does not exist: $bundle_rel"
[[ "$(realpath -e "$bundle_abs")" == "$bundle_abs" ]] || die "review bundle cannot traverse symlinks"

stage_receipt="$bundle_abs/CODEX_FINAL_REVIEW.json"
closeout_receipt="$bundle_abs/CODEX_CLOSEOUT_PREACTION.json"
durable_prefix="docs/refactor/two-server/reviews/${stage_id}-${assignment_id}"
allowed_paths=(
  "docs/refactor/two-server/CHANGE_LEDGER.md"
  "docs/refactor/two-server/execution-order.yaml"
  "${durable_prefix}.json"
  "${durable_prefix}.md"
)

test -s "$stage_receipt" || die "accepted Stage receipt is missing"
jq -e --arg stage "$stage_id" --arg assignment "$assignment_id" --arg head "$accepted_head" '
  .reviewer_authority == "CODEX_FINAL_REVIEWER" and
  .receipt_type == "STAGE_FINAL" and
  .stage_id == $stage and
  .assignment_id == $assignment and
  .reviewed_head_sha == $head and
  .verdict == "APPROVE" and
  .decision.accepted_commit == $head
' "$stage_receipt" >/dev/null || die "Stage receipt does not approve the accepted head"

validate_source_authorization() {
  test -s "$source_authorization" &&
    grep -Fxq 'authorization: APPROVED' "$source_authorization" &&
    grep -Fxq 'scope: TWO_SITE_REFACTOR_SOURCE_AND_EVIDENCE_TO_OPENAI_CODEX' "$source_authorization"
}

validate_exact_paths() {
  local mode="$1"
  local -a observed=()
  if [[ "$mode" == "cached" ]]; then
    mapfile -d '' observed < <(git -C "$repo_root" diff --cached --name-only -z --diff-filter=ACM)
    git -C "$repo_root" diff --cached --quiet --diff-filter=DRTUXB || \
      die "closeout cannot delete, rename, copy, or contain unresolved paths"
  else
    mapfile -d '' observed < <(git -C "$repo_root" diff --name-only -z --diff-filter=ACM "$accepted_head" "$closeout_head")
    git -C "$repo_root" diff --quiet --diff-filter=DRTUXB "$accepted_head" "$closeout_head" || \
      die "closeout commit cannot delete, rename, copy, or contain unresolved paths"
  fi
  ((${#observed[@]} == ${#allowed_paths[@]})) || die "closeout must change exactly four canonical paths"
  local expected path found
  for expected in "${allowed_paths[@]}"; do
    found=false
    for path in "${observed[@]}"; do
      [[ "$path" == "$expected" ]] && found=true
    done
    $found || die "required closeout path is missing: $expected"
  done
}

patch_hash_cached() {
  git -C "$repo_root" diff --cached --binary --full-index --no-ext-diff "$accepted_head" | sha256sum | awk '{print $1}'
}

patch_hash_commit() {
  git -C "$repo_root" diff --binary --full-index --no-ext-diff "$accepted_head" "$closeout_head" | sha256sum | awk '{print $1}'
}

validate_closeout_content() {
  local removed added normalized
  mapfile -t removed < <(
    git -C "$repo_root" diff --cached --unified=0 -- "docs/refactor/two-server/execution-order.yaml" |
      grep '^-' | grep -v '^---'
  )
  mapfile -t added < <(
    git -C "$repo_root" diff --cached --unified=0 -- "docs/refactor/two-server/execution-order.yaml" |
      grep '^+' | grep -v '^+++'
  )
  ((${#removed[@]} == 1 && ${#added[@]} == 1)) || \
    die "closeout must change exactly one execution-order Stage line"
  [[ "${removed[0]}" =~ ^-\ \ ${stage_id}:\ \{state:\ [A-Z_]+ ]] || \
    die "closeout removed line does not belong to the target Stage"
  [[ "${removed[0]}" != *"state: COMPLETE"* ]] || die "target Stage is already COMPLETE"
  [[ "${added[0]}" =~ ^\+\ \ ${stage_id}:\ \{state:\ COMPLETE ]] || \
    die "closeout added line does not set the target Stage COMPLETE"
  normalized="$(printf '%s\n' "${removed[0]#-}" | sed -E 's/state: [A-Z_]+/state: COMPLETE/')"
  [[ "$normalized" == "${added[0]#+}" ]] || \
    die "closeout cannot change dependencies or Stage metadata"

  jq -S . "$stage_receipt" >"$tmp_dir/source-stage-receipt.json"
  git -C "$repo_root" show ":${durable_prefix}.json" | jq -S . >"$tmp_dir/durable-stage-receipt.json"
  cmp -s "$tmp_dir/source-stage-receipt.json" "$tmp_dir/durable-stage-receipt.json" || \
    die "durable JSON receipt is not an exact normalized copy of the accepted receipt"
  test -s "$bundle_abs/CODEX_FINAL_REVIEW.md" || die "accepted Stage Markdown receipt is missing"
  git -C "$repo_root" show ":${durable_prefix}.md" >"$tmp_dir/durable-stage-receipt.md"
  cmp -s "$bundle_abs/CODEX_FINAL_REVIEW.md" "$tmp_dir/durable-stage-receipt.md" || \
    die "durable Markdown receipt is not an exact copy of the accepted receipt"
  git -C "$repo_root" diff --cached -- "docs/refactor/two-server/CHANGE_LEDGER.md" |
    grep '^+' | grep -v '^+++' | grep -Fq "$stage_id" || die "ledger addition omits target Stage"
  git -C "$repo_root" diff --cached -- "docs/refactor/two-server/CHANGE_LEDGER.md" |
    grep '^+' | grep -v '^+++' | grep -Fq "$accepted_head" || die "ledger addition omits accepted Stage SHA"
}

if $verify_commit; then
  [[ "$closeout_head" =~ ^[0-9a-f]{40}$ ]] || die "closeout head must be a full lowercase SHA"
  test -s "$closeout_receipt" || die "closeout pre-action receipt is missing"
  [[ "$(git -C "$repo_root" rev-parse HEAD)" == "$closeout_head" ]] || die "HEAD differs from closeout head"
  [[ "$(git -C "$repo_root" rev-parse "${closeout_head}^")" == "$accepted_head" ]] || \
    die "closeout commit is not the direct child of the accepted Stage head"
  [[ "$(git -C "$repo_root" rev-list --parents -n 1 "$closeout_head" | awk '{print NF}')" == "2" ]] || \
    die "closeout must be a single-parent commit"
  test -z "$(git -C "$repo_root" status --porcelain=v1 --untracked-files=all)" || \
    die "repository must be fully clean after closeout commit"
  validate_exact_paths committed
  actual_hash="$(patch_hash_commit)"
  jq -e --arg stage "$stage_id" --arg assignment "$assignment_id" \
    --arg head "$accepted_head" --arg patch "$actual_hash" '
      .reviewer_authority == "CODEX_FINAL_REVIEWER" and
      .receipt_type == "STAGE_CLOSEOUT_PREACTION" and
      .stage_id == $stage and .assignment_id == $assignment and
      .accepted_stage_sha == $head and .proposed_patch_sha256 == $patch and
      .verdict == "APPROVE" and .decision.commit_authorized == true
    ' "$closeout_receipt" >/dev/null || die "closeout commit does not match its approval receipt"
  reviewed_at="$(jq -r .reviewed_at_utc "$closeout_receipt")"
  reviewed_epoch="$(date -u -d "$reviewed_at" +%s 2>/dev/null)" || die "closeout receipt timestamp is invalid"
  receipt_age="$(( $(date -u +%s) - reviewed_epoch ))"
  ((receipt_age >= -300 && receipt_age <= 1800)) || die "closeout receipt expired before commit verification"
  printf 'CLOSEOUT_COMMIT=VERIFIED\n'
  printf 'CLOSEOUT_HEAD=%s\n' "$closeout_head"
  exit 0
fi

test -z "$closeout_head" || die "--closeout-head is valid only with --verify-commit"
test -n "$codex_bin" && test -x "$codex_bin" || die "required command is unavailable: codex"
"$codex_bin" login status >/dev/null 2>&1 || die "Codex CLI is not authenticated"
validate_source_authorization || die "explicit source-review authorization is missing"
test -f "$schema_path" || die "closeout schema is missing"
jq -e . "$schema_path" >/dev/null || die "closeout schema is not valid JSON"
[[ "$(git -C "$repo_root" branch --show-current)" == "refactor/two-site-architecture" ]] || \
  die "closeout review must run from the integration branch"
[[ "$(git -C "$repo_root" rev-parse HEAD)" == "$accepted_head" ]] || die "HEAD differs from accepted Stage head"
git -C "$repo_root" cat-file -e "${accepted_head}:${schema_rel}" || die "closeout schema is not tracked at accepted head"
git -C "$repo_root" cat-file -e "${accepted_head}:scripts/two_site_codex_closeout_review.sh" || \
  die "closeout bridge is not tracked at accepted head"
git -C "$repo_root" diff --quiet || die "unstaged tracked changes are forbidden during closeout review"
test -z "$(git -C "$repo_root" ls-files --others --exclude-standard)" || \
  die "untracked files are forbidden during closeout review"
validate_exact_paths cached

tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/two-site-closeout-review.XXXXXX")"
trap 'rm -rf -- "$tmp_dir"' EXIT
validate_closeout_content
patch_sha256="$(patch_hash_cached)"
raw_receipt="$tmp_dir/receipt.json"

read -r -d '' review_prompt <<EOF || true
Act as CODEX_FINAL_REVIEWER for only the mechanical closeout of one already-approved
two-site Stage. This is a pre-action review of the exact staged Git patch, not a review
of implementation and not permission for external or production mutation.

Stage: $stage_id
Assignment: $assignment_id
Accepted Stage implementation SHA: $accepted_head
Staged patch SHA-256: $patch_sha256
Review bundle: $bundle_rel
Allowed changed paths (exactly these four):
${allowed_paths[*]}

Read AGENTS.md and the review-relevant project memory, execution governance, accepted
CODEX_FINAL_REVIEW.json, the full staged diff, and the proposed durable receipt copies.
APPROVE only if the Stage receipt accepts the exact current HEAD, the staged patch only
records that acceptance and changes this Stage from its current state to COMPLETE,
dependency definitions and evidence meaning are unchanged, all four paths are present,
and no source/test/runtime/external action is included. Return only schema-valid JSON.
The proposed_patch_sha256 must equal $patch_sha256. Any uncertainty fails closed.
EOF

PYTHONDONTWRITEBYTECODE=1 TMPDIR="$tmp_dir" "$codex_bin" exec \
  --cd "$repo_root" --sandbox read-only --ephemeral \
  --output-schema "$schema_path" --output-last-message "$raw_receipt" "$review_prompt"

test -s "$raw_receipt" || die "Codex did not produce a closeout receipt"
jq -e . "$raw_receipt" >/dev/null || die "Codex closeout receipt is not valid JSON"
jq -e --arg stage "$stage_id" --arg assignment "$assignment_id" \
  --arg head "$accepted_head" --arg patch "$patch_sha256" '
    .schema_version == "1.0" and
    .reviewer_authority == "CODEX_FINAL_REVIEWER" and
    .receipt_type == "STAGE_CLOSEOUT_PREACTION" and
    .stage_id == $stage and .assignment_id == $assignment and
    .accepted_stage_sha == $head and .proposed_patch_sha256 == $patch and
    (if .verdict == "APPROVE" then
      .scope.stage_receipt_valid == true and
      .scope.changed_paths_closeout_only == true and
      .scope.state_transition_valid == true and
      .scope.unauthorized_external_mutation == "NONE" and
      .decision.commit_authorized == true and
      all(.findings[]; .severity != "CRITICAL" and .severity != "HIGH")
    else .decision.commit_authorized == false end)
  ' "$raw_receipt" >/dev/null || die "Codex closeout receipt contradicts mandatory gates"

reviewed_at="$(jq -r .reviewed_at_utc "$raw_receipt")"
reviewed_epoch="$(date -u -d "$reviewed_at" +%s 2>/dev/null)" || die "review timestamp is invalid"
now_epoch="$(date -u +%s)"
review_age="$((now_epoch - reviewed_epoch))"
((review_age >= -300 && review_age <= 900)) || die "review timestamp is outside the validity window"
[[ "$(patch_hash_cached)" == "$patch_sha256" ]] || die "staged closeout changed during review"
validate_exact_paths cached

receipt_tmp="$bundle_abs/.CODEX_CLOSEOUT_PREACTION.json.tmp"
jq -S . "$raw_receipt" >"$receipt_tmp"
mv -f -- "$receipt_tmp" "$closeout_receipt"
printf 'STAGE_CLOSEOUT_PREACTION=%s\n' "$(jq -r .verdict "$closeout_receipt")"
printf 'PATCH_SHA256=%s\n' "$patch_sha256"
