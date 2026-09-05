#!/usr/bin/env bash
set -euo pipefail
umask 077

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
require_command() { command -v "$1" >/dev/null 2>&1 || die "required command is unavailable: $1"; }

repo_root="$(git rev-parse --show-toplevel 2>/dev/null)" || die "not inside a Git repository"
repo_root="$(realpath -e "$repo_root")"
schema_rel="docs/refactor/two-server/templates/governance-review.schema.json"
schema_path="$repo_root/$schema_rel"
authorization="$repo_root/.local/two-site-refactor/authorizations/CODEX_SOURCE_REVIEW.md"
codex_bin="$(command -v codex 2>/dev/null || true)"
codex_model="gpt-5.6-sol"
codex_reasoning_effort="xhigh"
amendment_id=""; bundle_rel=""; base_sha=""; head_sha=""

while (($#)); do
  case "$1" in
    --amendment|--bundle|--base|--head)
      (($# >= 2)) || die "missing value for $1"
      case "$1" in
        --amendment) amendment_id="$2" ;;
        --bundle) bundle_rel="$2" ;;
        --base) base_sha="$2" ;;
        --head) head_sha="$2" ;;
      esac
      shift 2 ;;
    --help|-h)
      printf 'Usage: %s --amendment ID --bundle .local/two-site-refactor/governance-bundles/ID --base FULL_SHA --head FULL_SHA\n' "$0"
      exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

require_command git; require_command jq; require_command realpath; require_command date
test -n "$codex_bin" && test -x "$codex_bin" || die "required command is unavailable: codex"
"$codex_bin" login status >/dev/null 2>&1 || die "Codex CLI is not authenticated"
[[ "$amendment_id" =~ ^[A-Z0-9][A-Z0-9._-]+$ ]] || die "invalid amendment ID"
[[ "$base_sha" =~ ^[0-9a-f]{40}$ ]] || die "base must be a full lowercase SHA"
[[ "$head_sha" =~ ^[0-9a-f]{40}$ ]] || die "head must be a full lowercase SHA"
[[ "$base_sha" != "$head_sha" ]] || die "base and head must differ"
expected_bundle=".local/two-site-refactor/governance-bundles/${amendment_id}"
[[ "$bundle_rel" == "$expected_bundle" ]] || die "bundle must be exactly $expected_bundle"
bundle_abs="$repo_root/$bundle_rel"
test -d "$bundle_abs" || die "governance bundle does not exist"
[[ "$(realpath -e "$bundle_abs")" == "$bundle_abs" ]] || die "bundle cannot traverse symlinks"
for required in SCOPE.md FINAL_REVIEW_REQUEST.md tests.txt commit.txt; do
  test -s "$bundle_abs/$required" || die "missing or empty bundle file: $required"
done
test -s "$authorization" && grep -Fxq 'authorization: APPROVED' "$authorization" &&
  grep -Fxq 'scope: TWO_SITE_REFACTOR_SOURCE_AND_EVIDENCE_TO_OPENAI_CODEX' "$authorization" ||
  die "explicit source-review authorization is missing"
test -f "$schema_path" && jq -e . "$schema_path" >/dev/null || die "governance schema is missing or invalid"
[[ "$(git -C "$repo_root" branch --show-current)" == "refactor/two-site-architecture" ]] || die "wrong integration branch"
[[ "$(git -C "$repo_root" rev-parse HEAD)" == "$head_sha" ]] || die "integration HEAD differs from --head"
git -C "$repo_root" merge-base --is-ancestor "$base_sha" "$head_sha" || die "base is not an ancestor of head"
test -z "$(git -C "$repo_root" status --porcelain=v1 --untracked-files=all)" || die "repository must be fully clean"
stage_line_pattern='^  [A-Z][A-Z0-9]*-[0-9]+[a-z]?: \{state:'
base_stage_states="$(git -C "$repo_root" show "${base_sha}:docs/refactor/two-server/execution-order.yaml" | grep -E "$stage_line_pattern")"
head_stage_states="$(git -C "$repo_root" show "${head_sha}:docs/refactor/two-server/execution-order.yaml" | grep -E "$stage_line_pattern")"
[[ "$base_stage_states" == "$head_stage_states" ]] || die "governance amendment changed authoritative Stage state or metadata"
git -C "$repo_root" cat-file -e "${head_sha}:${schema_rel}" || die "governance schema is not tracked at reviewed head"
git -C "$repo_root" cat-file -e "${head_sha}:scripts/two_site_codex_governance_review.sh" || die "governance bridge is not tracked at reviewed head"
grep -Fq "$base_sha" "$bundle_abs/commit.txt" || die "commit.txt omits base"
grep -Fq "$head_sha" "$bundle_abs/commit.txt" || die "commit.txt omits head"
grep -Fq "$base_sha" "$bundle_abs/FINAL_REVIEW_REQUEST.md" || die "request omits base"
grep -Fq "$head_sha" "$bundle_abs/FINAL_REVIEW_REQUEST.md" || die "request omits head"

tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/two-site-governance-review.XXXXXX")"
trap 'rm -rf -- "$tmp_dir"' EXIT
raw="$tmp_dir/receipt.json"
read -r -d '' prompt <<EOF || true
Act as CODEX_FINAL_REVIEWER for exactly one execution-governance amendment in the
approved two-site refactor. This is an independent exact-SHA review, not implementation
and not permission for any Stage completion, external action, production mutation,
deployment, DNS/Writer change, secret access, or remote promotion.

Amendment: $amendment_id
Integration base: $base_sha
Reviewed head: $head_sha
Bundle: $bundle_rel

Read AGENTS.md and review-relevant MemoryCustodian files, then canonical governance,
the complete bundle, exact diff $base_sha..$head_sha, tests, and repository-local agent
contracts. Verify the declared scope, internal consistency, fail-closed behavior,
automation safety, source-of-truth preservation, and that Stage states did not change.
Return only schema-valid JSON. APPROVE only with no unresolved High/Critical finding,
all verification items PASS, unauthorized_external_mutation exactly NONE, and
decision.accepted_commit exactly $head_sha. Otherwise accepted_commit must be null.
EOF
PYTHONDONTWRITEBYTECODE=1 TMPDIR="$tmp_dir" "$codex_bin" exec \
  --model "$codex_model" --config "model_reasoning_effort=\"$codex_reasoning_effort\"" \
  --cd "$repo_root" \
  --sandbox read-only --ephemeral --output-schema "$schema_path" \
  --output-last-message "$raw" "$prompt"

test -s "$raw" && jq -e . "$raw" >/dev/null || die "Codex did not produce valid JSON"
jq -e --arg amendment "$amendment_id" --arg base "$base_sha" --arg head "$head_sha" '
  .schema_version == "1.0" and .reviewer_authority == "CODEX_FINAL_REVIEWER" and
  .receipt_type == "EXECUTION_GOVERNANCE_AMENDMENT" and .amendment_id == $amendment and
  .integration_base_sha == $base and .reviewed_head_sha == $head and
  .decision.stage_completion_granted == false and
  (if .verdict == "APPROVE" then
    .decision.accepted_commit == $head and .scope.changed_paths_within_scope == true and
    .scope.stage_state_unchanged == true and .scope.unauthorized_external_mutation == "NONE" and
    (all(.findings[]; .severity != "CRITICAL" and .severity != "HIGH")) and
    ((.verification | length) > 0) and (all(.verification[]; .result == "PASS"))
  else .decision.accepted_commit == null end)
' "$raw" >/dev/null || die "Codex governance receipt contradicts mandatory gates"
reviewed_at="$(jq -r .reviewed_at_utc "$raw")"
reviewed_epoch="$(date -u -d "$reviewed_at" +%s 2>/dev/null)" || die "review timestamp is invalid"
age="$(( $(date -u +%s) - reviewed_epoch ))"
((age >= -300 && age <= 900)) || die "review timestamp is outside the validity window"
[[ "$(git -C "$repo_root" rev-parse HEAD)" == "$head_sha" ]] || die "HEAD changed during review"
test -z "$(git -C "$repo_root" status --porcelain=v1 --untracked-files=all)" || die "repository changed during review"

tmp_receipt="$bundle_abs/.CODEX_GOVERNANCE_REVIEW.json.tmp"
jq -S . "$raw" >"$tmp_receipt"
mv -f -- "$tmp_receipt" "$bundle_abs/CODEX_GOVERNANCE_REVIEW.json"
printf 'CODEX_GOVERNANCE_REVIEW=%s\n' "$(jq -r .verdict "$bundle_abs/CODEX_GOVERNANCE_REVIEW.json")"
printf 'RECEIPT=%s\n' "$bundle_rel/CODEX_GOVERNANCE_REVIEW.json"
