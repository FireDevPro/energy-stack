---
date: 2026-05-19
owner: chris
status: active
role-label: ops-checklist
name: public-flip-readiness-2026-05-19
related:
  - OSF_FILING_MECHANICS.md
  - plans/sced-rebaseline-spec-2026-05-13.md
target_flip_date: 2026-05-30
---

# Public-flip readiness checklist

Operational checklist for the GitHub repo settings that need to be in
place around the moment `FireDevPro/energy-stack` flips from private
to public visibility on 2026-05-30 for the OSF / Zenodo deposit.

## Scope and non-scope

This document covers **GitHub-platform controls** that protect the
repository AFTER it becomes publicly visible. It does NOT cover:

- Sanitization of repo contents or git history — that work was completed
  separately via the sanitization PR + the `git filter-repo` rewrite +
  the delete-and-recreate of the repo. The repo as it exists today is
  the clean post-sanitization state and contains no PII or leaked
  credentials reachable via any clone path (standard or mirror) or any
  GitHub web-UI navigation.
- Pre-registration / OSF filing mechanics — see
  [`docs/OSF_FILING_MECHANICS.md`](../OSF_FILING_MECHANICS.md).
- Operational hardening of Pi-lab itself (the runtime environment) —
  separate concern, separate doc when needed.

**Critical separator: GitHub settings do not fix leaked git history.**
The controls below assume the underlying repo contents have already
been sanitized. Enabling secret scanning on an unsanitized repo would
flag historical secrets but not remove them. Sanitization is upstream of
this checklist and must already be complete before any of these settings
become load-bearing.

## Account context

The repository lives in the `FireDevPro` GitHub organization (a Pro
plan), transferred from a personal user account on 2026-05-19. GitHub
Pro on the org enables most repository-level security controls on the
private repo BEFORE the public flip; this is a meaningful change from
the original draft of this doc, which assumed free-tier and deferred
most controls until after the flip. Two features remain gated:

- **CodeQL default setup** requires the GitHub Code Security add-on
  (separate paid product, ~$48/repo/month, NOT included in Pro). Defer
  to post-public-flip when CodeQL is free for public repos.
- **Private vulnerability reporting** is a public-repos-only feature
  per GitHub's docs ([configuring PVR for a repository](https://docs.github.com/en/code-security/security-advisories/working-with-repository-security-advisories/configuring-private-vulnerability-reporting-for-a-repository)).
  The REST API endpoint (`PUT /repos/{owner}/{repo}/private-vulnerability-reporting`)
  returns 404 on private repos, and the UI row in Settings → Advanced
  Security is hidden until visibility flips to public. **Cannot be
  enabled pre-flip.** Move to Phase B (at-flip) below.

## Status legend

- [x] **DONE** — already applied / verified on `FireDevPro/energy-stack`
- [ ] **TODO-MANUAL** — must be applied via GitHub UI (no API path)
- [⏸] **AT-FLIP** — depends on the visibility change to public
- [n/a] explained in-line

---

## 1. Self-hosted runner detached

- [x] **DONE** — Pi-lab self-hosted runner was deregistered from
  GitHub and its install directory was removed from Pi-lab on
  2026-05-19, as part of the pre-flip hardening pass.

**Why it matters.** A self-hosted runner registered to a public repo is
a privilege-escalation vector: any fork PR that triggers a workflow
runs on the operator's hardware unless §4 (fork-PR approval) is at
the strictest setting. Removing the runner entirely eliminates the
surface.

**Verified.** GitHub Settings → Actions → Runners shows no
self-hosted runners.

## 2. Actions `GITHUB_TOKEN` default permissions = read-only

- [x] **DONE** — verified live as already in the correct state.
  GitHub's default for new repos created in 2024+ is `default_workflow_
  permissions: "read"`, and this repo inherited that default.

**Why it matters.** The `GITHUB_TOKEN` is injected into every workflow
run. Read-write default means any compromised action can mutate the
repo. Read-only default forces each workflow to explicitly opt into
write permissions via its YAML `permissions:` block.

**Verify command:**
```bash
gh api repos/FireDevPro/energy-stack/actions/permissions/workflow \
  --jq '.default_workflow_permissions'
# expect: "read"
```

## 3. Actions cannot create or approve PRs

- [x] **DONE** — verified live as already in the correct state.

**Why it matters.** If a workflow can both create and approve a PR, an
attacker who lands a malicious workflow change can self-approve a PR
that bypasses every other branch protection.

**Verify command:**
```bash
gh api repos/FireDevPro/energy-stack/actions/permissions/workflow \
  --jq '.can_approve_pull_request_reviews'
# expect: false
```

## 4. Fork PR workflow approval at strictest setting

- [⏸] **AT-FLIP** — this setting only matters for public repos
  (private repos cannot be forked by outside collaborators).

**What to do at flip time.** GitHub Settings → Actions → General →
"Fork pull request workflows" → select **"Require approval for all
outside collaborators"** (the strictest option).

**Why it matters.** A public repo can receive PRs from anyone. Without
approval gating, a fork PR's workflow runs automatically — its YAML
can read secrets or execute arbitrary code in the runner environment.

## 5. Security feature bundle

GitHub Pro on the private repo enables most of this bundle pre-flip.
Two items remain gated (see "Account context" above).

### 5a. Applied pre-flip via API

- [x] **DONE: Dependabot alerts.** Enabled.
- [x] **DONE: Dependabot security updates.** Enabled. The first auto-
  generated PR (`dependabot/pip/.../aiohttp-3.13.4`) has already
  arrived, confirming the integration works end-to-end.
- [x] **DONE: Secret scanning.** Enabled.
- [x] **DONE: Push protection.** Enabled — blocks future commits
  containing secret-shaped strings at `git push` time.
- [x] **DONE: Secret scanning non-provider patterns.** Enabled —
  broader detection beyond named-provider patterns.
- [x] **DONE: Secret scanning validity checks.** Enabled — flagged
  secrets are tested against the originating provider to confirm
  they're still active.

### 5b. At-flip (deferred until public)

- [⏸] **AT-FLIP: Private vulnerability reporting.** Public-repos-only
  per GitHub docs (see "Account context" above). At flip time:
  Settings → Advanced Security → "Private vulnerability reporting" →
  Enable. One click.
- [⏸] **AT-FLIP: CodeQL default setup.** Requires the GitHub Code
  Security add-on on private repos (separate ~$48/repo/month cost).
  Free for public repos after flip. At flip time: Settings → Advanced
  Security → "Code scanning" → "Set up" → "Default" → Python +
  JavaScript/TypeScript.

**Why it matters.** Layered defense. Secret scanning catches historical
leaks the sanitization missed; push protection prevents future leaks;
Dependabot keeps the supply chain current; CodeQL catches static
code-level issues; private reporting prevents researchers from being
forced to disclose publicly.

## 6. Branch ruleset for `main`

- [x] **DONE** — ruleset `main-protection` (id 16581919) is active.

Rules applied:
- Require pull request before merging (0 required approvals — solo
  workflow, PR mandatory for audit trail, self-merge allowed)
- Dismiss stale pull request approvals when new commits are pushed
- Require conversation resolution before merging
- Allowed merge methods: squash, merge, rebase
- Block force pushes (`non_fast_forward` rule)
- Restrict deletions

**Why it matters.** Once public, anyone who somehow gains write access
could force-push or delete `main`. The ruleset blocks both at the
GitHub API level. The PR-required rule gives a PR trail for every
change to `main`.

**Verify post-flip:** `git push origin main` directly from the
workstation should be rejected with `GH013: Repository rule violations
found`.

## 7. Tag ruleset for `osf-prereg-*` tags

- [x] **DONE** — ruleset `osf-prereg-immutable` (id 16581843) is
  active. Targets `refs/tags/osf-prereg-*`. Rules: block update, block
  deletion, block non-fast-forward. No bypass actors.

**Why it matters.** Once the OSF freeze tag is pushed, the SHA it
points at is referenced by the OSF deposit metadata + the Zenodo DOI
record. If the tag is force-updated or deleted after deposit, the
academic record becomes broken: the Zenodo archive still has the
original tarball but the GitHub URL referenced in the OSF deposit
suddenly resolves to nothing or to different content. The ruleset
makes this class of mistake impossible.

**Already active BEFORE the freeze tag push** — the tag is protected
from the moment it lands.

## 8. Old Actions artifacts and logs

- [n/a] for the current state — the repo was recreated empty on
  2026-05-19 before this checklist was authored, so there are zero
  pre-existing workflow runs, artifacts, or logs.

**Going forward.** When workflows run after public flip, periodically
review Actions → Workflow runs → individual runs → artifacts + logs
for any accidentally captured sensitive output. Default retention is
90 days; can be shortened in Settings → Actions → General → "Artifact
and log retention".

## 9. Disable unused community features

- [x] **DONE: Wiki.** Disabled.
- [x] **DONE: Discussions.** Disabled.
- [x] **DONE: Issues.** Left enabled per default recommendation
  (channel for incoming bug reports; security reports go through
  private vulnerability reporting per §5b once enabled).

## 10. `SECURITY.md` at repo root

- [x] **DONE** — present on `main` from PR #1.

**Why it matters.** GitHub recognizes a top-level `SECURITY.md` as the
canonical "report a vulnerability" landing page. It surfaces in the
Security tab and in the "Report a vulnerability" link that GitHub
auto-adds to public repos.

**Verify after public flip:** Visit the repo's Security tab on
github.com; the "Policy" section should display the contents of
`SECURITY.md`.

---

## Execution sequence (revised post-Pro-org-transfer)

Pre-flip state at the moment this revision was authored is captured in
the **DONE** checkmarks above (10 of 12 items already applied via
gh-api). The remaining work is:

### Phase A — Pre-flip (any time before 2026-05-30)

1. Merge any additional content / spec / impl PRs that need to land
   before the OSF freeze tag (per `OSF_FILING_MECHANICS.md`
   pre-flight).

### Phase B — At the visibility flip (~5 min)

1. **Flip visibility:** Settings → General → "Change visibility" →
   Public. Confirm the typed-name modal.
2. **§4: Set fork-PR workflow approval to strictest** via Settings →
   Actions → General.
3. **§5b: Enable private vulnerability reporting** via Settings →
   Advanced Security → "Private vulnerability reporting" → Enable.
4. **§5b: Enable CodeQL default setup** via Settings → Advanced
   Security → "Code scanning" → "Set up" → "Default" → Python +
   JavaScript/TypeScript.

### Phase C — OSF freeze tag push (immediately after Phase B)

Per `docs/OSF_FILING_MECHANICS.md` 12-step procedure. The tag ruleset
from §7 is already active, so the tag becomes immutable the moment
it's pushed.

### Phase D — Post-flip steady-state verification (within 24 hours)

Re-walk every item in this doc confirming each enabled control is
still enabled. GitHub occasionally migrates settings between sections
during platform updates; the post-flip pass catches any drift.

---

## When this doc is "done"

After Phase D verification passes, this doc moves to
`docs/plans/archive/public-flip-readiness-2026-05-19.md` per the
plan-authoring discipline in `AGENTS.md`. Its `status:` frontmatter
flips from `active` to `complete`.
