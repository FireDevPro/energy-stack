#!/usr/bin/env bash
# Focused Codex review wrapper.
#
# Builds a review packet at .codex/review-packet.md containing:
#   - phase name and plan excerpt
#   - base/head commit identifiers
#   - scoped diff (optionally narrowed to a file glob)
#   - reviewer instructions: bugs / regressions / missing tests only, no
#     style nits, no edits
#
# Then runs `codex exec < .codex/review-packet.md` and saves the response
# to .codex/review-result-<phase>.md.
#
# Usage:
#   tools/codex-focused-review.sh <phase-name> <base-ref> [path-scope]
#
# Examples:
#   tools/codex-focused-review.sh phase1 main
#   tools/codex-focused-review.sh phase2 HEAD~5 'tools/cockpit/frontend/**'
#   tools/codex-focused-review.sh phase3 HEAD~3 'tools/cockpit/**'

set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "usage: $0 <phase-name> <base-ref> [path-scope]" >&2
  exit 2
fi

PHASE="$1"
BASE_REF="$2"
PATH_SCOPE="${3:-}"

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

PLAN_FILE="docs/plans/cockpit-plan.md"
PACKET="$REPO_ROOT/.codex/review-packet.md"
RESULT="$REPO_ROOT/.codex/review-result-$PHASE.md"

mkdir -p "$REPO_ROOT/.codex"

# Resolve refs
BASE_COMMIT="$(git rev-parse --short "$BASE_REF")"
HEAD_COMMIT="$(git rev-parse --short HEAD)"

# Capture scoped diff
if [[ -n "$PATH_SCOPE" ]]; then
  DIFF_BLOB="$(git diff "$BASE_REF"..HEAD -- "$PATH_SCOPE")"
else
  DIFF_BLOB="$(git diff "$BASE_REF"..HEAD)"
fi

# Capture plan summary for this phase (front matter + headers + locked
# decisions table). Keeps the packet small.
PLAN_HEADER="$(sed -n '1,30p' "$PLAN_FILE")"
PHASE_HEADER="$(grep -nE "^### Phase|^#### Task" "$PLAN_FILE" | head -60)"

cat > "$PACKET" <<EOF
# Focused Codex review — $PHASE

You are reviewing one phase of an implementation against a locked plan.

## Rules

- Review ONLY this packet. Do not propose edits.
- Findings only. No style nits, no naming bikeshed, no formatting comments.
- Format: \`[Severity] Finding\` (Critical | Important | Nit).
- Report bugs, regressions, missing or wrong tests, contract drift, and
  scope creep relative to the plan.
- If you find nothing, say "No findings."

## Scope

- Phase: $PHASE
- Base commit: $BASE_COMMIT
- Head commit: $HEAD_COMMIT
- Path scope: ${PATH_SCOPE:-(full repo)}

## Plan front matter

\`\`\`
$PLAN_HEADER
\`\`\`

## Plan phases / tasks (headers only)

\`\`\`
$PHASE_HEADER
\`\`\`

## Diff under review

\`\`\`diff
$DIFF_BLOB
\`\`\`
EOF

echo "Wrote packet: $PACKET ($(wc -l <"$PACKET") lines)"
echo "Running codex exec..."

codex exec < "$PACKET" | tee "$RESULT"

echo
echo "Result saved: $RESULT"
