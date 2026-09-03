#!/usr/bin/env bash
set -euo pipefail
umask 077

usage() {
  cat <<'EOF'
Usage:
  scripts/two_site_codex_final_review.sh --check-only
  scripts/two_site_codex_final_review.sh \
    --stage <STAGE_ID> --assignment <ASSIGNMENT_ID> \
    --bundle <RELATIVE_BUNDLE_PATH> --base <FULL_SHA> --head <FULL_SHA>

Runs a fresh Codex final reviewer in a read-only sandbox and writes an atomic JSON
receipt plus a Markdown audit view into the ignored review bundle.
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
schema_path="$repo_root/.cursor/skills/two-site-refactor/assets/final-review.schema.json"
source_authorization="$repo_root/.local/two-site-refactor/authorizations/CODEX_SOURCE_REVIEW.md"
codex_bin="$(command -v codex 2>/dev/null || true)"
check_only=false
stage_id=""
assignment_id=""
bundle_rel=""
base_sha=""
head_sha=""

while (($#)); do
  case "$1" in
    --check-only)
      check_only=true
      shift
      ;;
    --stage|--assignment|--bundle|--base|--head)
      (($# >= 2)) || die "missing value for $1"
      case "$1" in
        --stage) stage_id="$2" ;;
        --assignment) assignment_id="$2" ;;
        --bundle) bundle_rel="$2" ;;
        --base) base_sha="$2" ;;
        --head) head_sha="$2" ;;
      esac
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

require_command git
require_command jq
require_command realpath
require_command date
test -n "$codex_bin" && test -x "$codex_bin" || die "required command is unavailable: codex"
test -f "$schema_path" || die "review schema is missing: $schema_path"
jq -e . "$schema_path" >/dev/null || die "review schema is not valid JSON"

validate_source_authorization() {
  test -s "$source_authorization" &&
    grep -Fxq 'authorization: APPROVED' "$source_authorization" &&
    grep -Fxq 'scope: TWO_SITE_REFACTOR_SOURCE_AND_EVIDENCE_TO_OPENAI_CODEX' "$source_authorization"
}

if $check_only; then
  "$codex_bin" login status >/dev/null 2>&1 || die "Codex CLI is not authenticated"
  validate_source_authorization || die "explicit source-review authorization is missing: $source_authorization"
  printf 'READY: Codex CLI, authentication, Git, jq, and review schema are available.\n'
  exit 0
fi

validate_source_authorization || die "explicit source-review authorization is missing: $source_authorization"

[[ "$stage_id" =~ ^[A-Z][A-Z0-9]*-[0-9]+$ ]] || die "invalid Stage ID"
[[ "$assignment_id" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || die "invalid Assignment ID"
[[ "$base_sha" =~ ^[0-9a-f]{40}$ ]] || die "base must be a full lowercase SHA"
[[ "$head_sha" =~ ^[0-9a-f]{40}$ ]] || die "head must be a full lowercase SHA"
[[ "$base_sha" != "$head_sha" ]] || die "base and head must differ"

expected_bundle=".local/two-site-refactor/review-bundles/${stage_id}-${assignment_id}"
[[ "$bundle_rel" == "$expected_bundle" ]] || die "bundle must be exactly $expected_bundle"
bundle_abs="$repo_root/$bundle_rel"
test -d "$bundle_abs" || die "review bundle does not exist: $bundle_rel"
[[ "$(realpath -e "$bundle_abs")" == "$bundle_abs" ]] || die "review bundle cannot traverse symlinks"

for required_file in assignment.md STAGE_REPORT.md FINAL_REVIEW_REQUEST.md HANDOFF.md commit.txt; do
  test -s "$bundle_abs/$required_file" || die "missing or empty bundle file: $required_file"
done

[[ "$(git -C "$repo_root" branch --show-current)" == "refactor/two-site-architecture" ]] || \
  die "review must run from refactor/two-site-architecture"
[[ "$(git -C "$repo_root" rev-parse HEAD)" == "$head_sha" ]] || die "integration HEAD differs from --head"
git -C "$repo_root" cat-file -e "${base_sha}^{commit}" || die "base commit does not exist"
git -C "$repo_root" cat-file -e "${head_sha}^{commit}" || die "head commit does not exist"
git -C "$repo_root" merge-base --is-ancestor "$base_sha" "$head_sha" || die "base is not an ancestor of head"
git -C "$repo_root" diff --quiet || die "tracked worktree changes must be clean before review"
git -C "$repo_root" diff --cached --quiet || die "staged changes must be clean before review"
grep -Fq "$head_sha" "$bundle_abs/commit.txt" || die "commit.txt does not identify the reviewed head"
grep -Fq "$head_sha" "$bundle_abs/FINAL_REVIEW_REQUEST.md" || die "review request does not identify the reviewed head"
grep -Fq "$base_sha" "$bundle_abs/FINAL_REVIEW_REQUEST.md" || die "review request does not identify the integration base"

tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/two-site-codex-review.XXXXXX")"
receipt_tmp=""
markdown_tmp=""
cleanup() {
  rm -rf -- "$tmp_dir"
  if test -n "$receipt_tmp"; then rm -f -- "$receipt_tmp"; fi
  if test -n "$markdown_tmp"; then rm -f -- "$markdown_tmp"; fi
}
trap cleanup EXIT
raw_receipt="$tmp_dir/receipt.json"

read -r -d '' review_prompt <<EOF || true
Act as CODEX_FINAL_REVIEWER for exactly one Stage of this repository's approved
two-site refactor. This is an independent final review, not implementation.

Stage: $stage_id
Assignment: $assignment_id
Integration base: $base_sha
Reviewed head: $head_sha
Review bundle: $bundle_rel

Read AGENTS.md and complete the MemoryCustodian startup loading for a review task.
Then read the canonical two-site plan, execution governance, execution-order entry,
Stage card, Assignment, complete review bundle, exact diff $base_sha..$head_sha, and
referenced evidence. Verify scope, dependency receipts, locks, behavior invariants,
success/failure/rollback/integration evidence, secret handling, and absence of
unapproved external mutation. Be skeptical; green meta-tests alone are not proof.

Do not edit any file, commit, branch, ledger, Stage state, external system, or
production resource. Return only JSON conforming to the supplied schema. APPROVE is
valid only if this exact commit has no unresolved High/Critical gap and the evidence is
adequate. For APPROVE, decision.accepted_commit must equal $head_sha. For every other
verdict it must be null. Never infer approval from missing evidence.
EOF

PYTHONDONTWRITEBYTECODE=1 TMPDIR="$tmp_dir" "$codex_bin" exec \
  --cd "$repo_root" \
  --sandbox read-only \
  --ephemeral \
  --output-schema "$schema_path" \
  --output-last-message "$raw_receipt" \
  "$review_prompt"

test -s "$raw_receipt" || die "Codex did not produce a receipt"
jq -e . "$raw_receipt" >/dev/null || die "Codex receipt is not valid JSON"
jq -e \
  --arg stage "$stage_id" \
  --arg assignment "$assignment_id" \
  --arg base "$base_sha" \
  --arg head "$head_sha" '
    .schema_version == "1.0" and
    .reviewer_authority == "CODEX_FINAL_REVIEWER" and
    .receipt_type == "STAGE_FINAL" and
    .stage_id == $stage and
    .assignment_id == $assignment and
    .integration_base_sha == $base and
    .reviewed_head_sha == $head and
    ((.verdict == "APPROVE" and .decision.accepted_commit == $head) or
     (.verdict != "APPROVE" and .decision.accepted_commit == null))
  ' "$raw_receipt" >/dev/null || die "Codex receipt identity or acceptance binding is invalid"

jq -e '
  if .verdict == "APPROVE" then
    .scope.dependency_receipts_valid == true and
    .scope.changed_paths_within_assignment == true and
    .scope.locks_and_parallel_peer_valid == true and
    .scope.unauthorized_external_mutation == "NONE" and
    (all(.findings[]; .severity != "CRITICAL" and .severity != "HIGH")) and
    ((.verification | length) > 0) and
    (all(.verification[]; .result == "PASS"))
  else
    true
  end
' "$raw_receipt" >/dev/null || die "APPROVE receipt contradicts mandatory safety gates"

reviewed_at="$(jq -r .reviewed_at_utc "$raw_receipt")"
reviewed_epoch="$(date -u -d "$reviewed_at" +%s 2>/dev/null)" || die "review timestamp is invalid"
now_epoch="$(date -u +%s)"
review_age="$((now_epoch - reviewed_epoch))"
((review_age >= -300 && review_age <= 900)) || die "review timestamp is outside the validity window"

receipt_tmp="$bundle_abs/.CODEX_FINAL_REVIEW.json.tmp"
markdown_tmp="$bundle_abs/.CODEX_FINAL_REVIEW.md.tmp"
jq -S . "$raw_receipt" >"$receipt_tmp"

jq -r '
  def safe: tostring | gsub("[|\\r\\n]"; " ");
  "# Codex Final Review — `\(.stage_id)` / `\(.reviewed_head_sha)`\n",
  "- Reviewer authority: `\(.reviewer_authority)`",
  "- Assignment ID: `\(.assignment_id)`",
  "- Integration base SHA: `\(.integration_base_sha)`",
  "- Reviewed head SHA: `\(.reviewed_head_sha)`",
  "- Review date (UTC): `\(.reviewed_at_utc)`",
  "- Verdict: `\(.verdict)`",
  "- Receipt type: `\(.receipt_type)`\n",
  "## Scope\n",
  "- Dependency receipts valid: `\(.scope.dependency_receipts_valid)`",
  "- Changed paths within Assignment: `\(.scope.changed_paths_within_assignment)`",
  "- Locks and parallel peer valid: `\(.scope.locks_and_parallel_peer_valid)`",
  "- Unauthorized external mutation: `\(.scope.unauthorized_external_mutation | safe)`\n",
  "## Findings\n",
  (if (.findings | length) == 0 then "- None."
   else (.findings[] | "- **\(.severity)** — \(.finding | safe) Evidence: \(.evidence | safe) Required: \(.required_action | safe)") end),
  "\n## Verification\n",
  (if (.verification | length) == 0 then "- None recorded."
   else (.verification[] | "- `\(.result)` — \(.check | safe) Evidence: \(.evidence_uri | safe)") end),
  "\n## Decision\n",
  "- Accepted commit: `\(.decision.accepted_commit // "NONE")`",
  "- Residual risk: \(.decision.residual_risk | safe)",
  "- Conditions: \(if (.decision.conditions | length) == 0 then "NONE" else (.decision.conditions | join("; ") | safe) end)",
  "- Next dependency-ready Stages: \(if (.decision.next_dependency_ready_stages | length) == 0 then "NONE" else (.decision.next_dependency_ready_stages | join(", ") | safe) end)",
  "\nOnly `APPROVE` for this exact integration commit permits Stage completion."
' "$raw_receipt" >"$markdown_tmp"

mv -f -- "$receipt_tmp" "$bundle_abs/CODEX_FINAL_REVIEW.json"
mv -f -- "$markdown_tmp" "$bundle_abs/CODEX_FINAL_REVIEW.md"

verdict="$(jq -r .verdict "$bundle_abs/CODEX_FINAL_REVIEW.json")"
printf 'CODEX_FINAL_REVIEW=%s\n' "$verdict"
printf 'RECEIPT=%s\n' "$bundle_rel/CODEX_FINAL_REVIEW.json"
