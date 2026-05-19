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
place at the moment `Promithius-DR/energy-stack` flips from private to
public visibility on 2026-05-30 for the OSF / Zenodo deposit.

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

## Status legend

- [x] **DONE** — already in place at the time this doc was written
- [ ] **TODO** — must be completed before 2026-05-30 public flip
- [n/a] explained in-line

## 1. Self-hosted runner detached

- [x] **DONE** — Pi-lab self-hosted runner was deregistered from GitHub
  and its install directory was removed from Pi-lab on 2026-05-19, as
  part of the pre-flip hardening pass.

**Why it matters.** A self-hosted runner registered to a public repo is
a privilege-escalation vector: any fork PR that triggers a workflow
runs on the operator's hardware unless `fork pull request workflow`
approval gating is at the strictest setting. Removing the runner
entirely eliminates the surface. If self-hosted is reintroduced after
the flip, **§4 (fork-PR approval) and §6 (workflow permissions) become
mandatory prerequisites**, not optional hardening.

**Verify.** GitHub Settings → Actions → Runners shows no
self-hosted runners. `ssh chris@pi-lab 'ls /home/chris/actions-runner'`
returns "No such file or directory".

## 2. Actions `GITHUB_TOKEN` default permissions = read-only

- [ ] **TODO**

**What to do.** GitHub Settings → Actions → General → "Workflow
permissions" section → select **Read repository contents and packages
permissions** (NOT "Read and write permissions"). Save.

**Why it matters.** The `GITHUB_TOKEN` is injected into every workflow
run. Read-write default means any compromised action can mutate the
repo (push commits, modify issues, alter PRs). Read-only default forces
each workflow to explicitly opt into write permissions via its YAML
`permissions:` block, making any escalation visible in code review.

**Verify after.** Open the same Settings page; the "Workflow
permissions" radio button should land on "Read repository contents and
packages permissions" after a page reload.

## 3. Actions cannot create or approve PRs

- [ ] **TODO**

**What to do.** GitHub Settings → Actions → General → "Workflow
permissions" section → ensure the checkbox **"Allow GitHub Actions to
create and approve pull requests"** is **unchecked**.

**Why it matters.** If a workflow can both create and approve a PR, an
attacker who lands a malicious workflow change can self-approve a PR
that bypasses every other branch protection. Keeping this off means PR
approval requires a human regardless of what any workflow tries to do.

**Verify after.** Checkbox shows as unchecked after page reload.

## 4. Fork PR workflow approval at strictest setting

- [ ] **TODO**

**What to do.** GitHub Settings → Actions → General → "Fork pull
request workflows from outside collaborators" → select **"Require
approval for all outside collaborators"** (the strictest of the
available options). Then in "Fork pull request workflows" section
below, select **"Require approval for all outside collaborators"** for
the corresponding sub-setting as well.

**Why it matters.** A public repo can receive PRs from anyone with a
GitHub account. Without approval gating, a fork PR's workflow runs
automatically — its YAML can read secrets, exfiltrate the repo's
`GITHUB_TOKEN`, or execute arbitrary code in the runner environment.
With this enabled, the operator must click "Approve and run" before
any fork-author workflow executes for the first time.

**Verify after.** Reload the page; both radio buttons should sit on the
strictest option.

## 5. Security feature bundle

Enable the full suite. Several of these are free for public repos and
unavailable or rate-limited on private free-tier — flipping public is
the cleanest moment to turn them all on.

- [ ] **TODO: Secret scanning.** Settings → Code security → "Secret
  scanning" → Enable. GitHub will scan all current and future blobs
  for known secret patterns and surface findings.
- [ ] **TODO: Push protection.** Settings → Code security → "Push
  protection" → Enable. Rejects future commits that contain
  secret-shaped strings at `git push` time, before they enter the
  history. Closes the "I accidentally committed a secret" failure mode
  going forward.
- [ ] **TODO: Dependabot alerts.** Settings → Code security →
  "Dependabot alerts" → Enable. Notifies on known-vulnerable
  dependencies (Python `requirements.txt`, npm `package-lock.json`).
- [ ] **TODO: Dependabot security updates.** Settings → Code security →
  "Dependabot security updates" → Enable. Auto-opens PRs to bump
  vulnerable deps to fixed versions.
- [ ] **TODO: Dependency graph.** Settings → Code security →
  "Dependency graph" → Enable. Prerequisite for Dependabot; usually
  on by default for public repos but verify.
- [ ] **TODO: CodeQL default setup.** Settings → Code security →
  "Code scanning" → "Set up" → "Default" → languages: Python +
  JavaScript/TypeScript (the cockpit frontend). Free for public
  repos. Runs on push to default branch + on PRs.
- [ ] **TODO: Private vulnerability reporting.** Settings → Code
  security → "Private vulnerability reporting" → Enable. Gives
  security researchers a private channel via the Security tab, instead
  of having to open a public issue describing a vulnerability.

**Why it matters.** Layered defense. Secret scanning catches historical
leaks the sanitization missed; push protection prevents future leaks;
Dependabot keeps the supply chain current; CodeQL catches static
code-level issues; private reporting prevents researchers from being
forced to disclose publicly.

**Verify after.** Open Settings → Code security; every section above
shows a green "Enabled" indicator.

## 6. Branch ruleset for `main`

- [ ] **TODO**

**What to do.** Settings → Rules → Rulesets → "New ruleset" → "New
branch ruleset". Configure:

- **Name:** `main-protection`
- **Enforcement status:** Active
- **Target branches:** Include "Default branch" (resolves to `main`)
- **Branch rules** (check each):
  - Require a pull request before merging
    - Required approvals: **0** (solo workflow — PR is mandatory for
      audit trail, but self-merge is allowed since there are no other
      reviewers)
    - Dismiss stale pull request approvals when new commits are pushed
    - Require conversation resolution before merging
  - Require status checks to pass before merging
    - (Add specific checks once CodeQL + any test workflows are live;
      can be edited later)
  - Block force pushes
  - Restrict deletions

**Why it matters.** Once public, anyone who somehow gains write access
(compromised credentials, leaked PAT) could force-push or delete `main`.
The ruleset blocks both at the GitHub API level even if the actor has
write permission. The PR-required rule also gives the operator a record
of every change to `main` rather than direct pushes that leave no PR
trail.

**Verify after.** Try `git push origin main` directly from the
workstation; it should be rejected with "GH013: Repository rule
violations found".

## 7. Tag ruleset for `osf-prereg-*` tags

- [ ] **TODO**

**What to do.** Settings → Rules → Rulesets → "New ruleset" → "New tag
ruleset". Configure:

- **Name:** `osf-prereg-immutable`
- **Enforcement status:** Active
- **Target tags:** Pattern `osf-prereg-*` (matches every freeze tag
  this study will create, current and future)
- **Tag rules**:
  - Restrict creations (only repo admin — i.e., just the operator)
  - Restrict updates (no one, including admin)
  - Restrict deletions (no one, including admin)

**Why it matters.** Once the OSF freeze tag is pushed, the SHA it points
at is referenced by the OSF deposit metadata + the Zenodo DOI record.
If the tag is force-updated or deleted after deposit, the academic
record becomes broken: the Zenodo archive still has the original
tarball but the GitHub URL referenced in the OSF deposit metadata
suddenly resolves to nothing or to different content. The ruleset
makes this class of mistake impossible.

**Verify after.** Try `git tag -d osf-prereg-2026-05-30 && git push
origin :osf-prereg-2026-05-30` on a test tag after the rule is active;
the push should be rejected. (Use a throwaway tag for the test, not
the real freeze tag.)

## 8. Old Actions artifacts and logs

- [n/a] for the current state — the repo was recreated empty before
  this checklist was authored, so there are zero pre-existing
  workflow runs, artifacts, or logs.

**Going forward.** When workflows run after public flip, periodically
review Actions → Workflow runs → individual runs → artifacts + logs
for any accidentally captured sensitive output. Default retention is
90 days; can be shortened in Settings → Actions → General → "Artifact
and log retention".

## 9. Disable unused community features

- [ ] **TODO: Wiki.** Settings → General → Features → uncheck "Wikis".
  No wiki content exists; disabling removes one more attack surface
  for spam and accidental leakage.
- [ ] **TODO: Discussions.** Settings → General → Features → uncheck
  "Discussions". Not in use; same rationale.
- [ ] **DECIDE: Issues.** Settings → General → Features → "Issues".
  Leaving enabled is fine if the operator intends to triage incoming
  bug reports. Disable if monitoring would be neglected. Default
  recommendation: **leave enabled** but pair with a brief issue
  template that sets expectations (response time, scope, no security
  reports via public issues — those go through private vulnerability
  reporting per §5).

## 10. `SECURITY.md` at repo root

- [ ] **TODO** — companion file added in the same PR as this
  checklist. See `SECURITY.md` at repo root.

**Why it matters.** GitHub recognizes a top-level `SECURITY.md` as the
canonical "report a vulnerability" landing page. It surfaces in the
Security tab and in the "Report a vulnerability" link that GitHub
auto-adds to public repos. Without it, security researchers default
to opening public issues, which is the wrong channel and may
inadvertently broadcast a 0day.

**Verify after.** Visit the repo's Security tab on github.com; the
"Policy" section should display the contents of `SECURITY.md`.

## Execution order

The order of items 2 through 10 doesn't matter — they are independent
settings. Recommend:

1. Land THIS PR first so the checklist and `SECURITY.md` are in main
2. Walk through items 2-10 in any order — the entire pass takes 15-30
   minutes
3. Flip visibility (Settings → General → "Change visibility" → Public)
4. Immediately re-verify each setting still shows the expected value
   (some settings reset or relocate when visibility flips)
5. Push the OSF freeze tag (per `OSF_FILING_MECHANICS.md`)
6. Confirm Zenodo mints the DOI; capture link
7. Submit OSF registration

## Post-flip verification

Within 24 hours of the public flip, re-walk this checklist confirming
each enabled control is still enabled. GitHub occasionally migrates
settings between sections during platform updates; the post-flip pass
catches any drift.

## When this doc is "done"

After the public flip is complete and verified, this doc moves to
`docs/plans/archive/public-flip-readiness-2026-05-19.md` per the
plan-authoring discipline in `AGENTS.md`. Its `status:` frontmatter
flips from `active` to `complete`.
