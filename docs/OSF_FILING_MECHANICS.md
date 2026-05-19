---
name: OSF_FILING_MECHANICS
date: 2026-05-18
owner: chris
status: active
role-label: ops-runbook
replaces: archive/OSF_FILING.md
extraction_pr: docs/plans/pre-osf-doc-audit-execution-2026-05-18.md PR6
related:
  - plans/sced-rebaseline-spec-2026-05-13.md
  - plans/pre-osf-doc-audit-execution-2026-05-18.md
---

# OSF filing mechanics

Operational walkthrough for depositing this study's pre-registration at OSF. The acceptance criteria / artifact list that used to live in the original `OSF_FILING.md` are now owned by the binding spec [`sced-rebaseline-spec-2026-05-13.md`](plans/sced-rebaseline-spec-2026-05-13.md) §11 (pre-OSF dependencies) + §13 (PR #109 disposition); this doc covers only the OSF-platform mechanics.

**Template choice (locked 2026-05-18):** OSF open-ended template, with the binding spec attached via Zenodo DOI. Rationale captured in [`docs/plans/pre-osf-doc-audit-execution-2026-05-18.md`](plans/pre-osf-doc-audit-execution-2026-05-18.md) §"OSF template choice".

## Pre-flight check (before clicking "submit")

Verify the freeze-day checklist from [`plans/pre-osf-doc-audit-execution-2026-05-18.md`](plans/pre-osf-doc-audit-execution-2026-05-18.md) PR9 is satisfied:

- Spec header `status: frozen` + `frozen_at_commit: <SHA>` set
- Impl plan header status updated to phases-1-6-complete
- `arm_calendar.py` hash recorded in the freeze commit
- Final shadow validation `validation_results.json` committed at the freeze commit (per D6)
- All PR1-PR8 work landed; main branch represents the OSF artifact

## Filing steps (6 steps, ~30 min wall clock)

### 1. Tag the freeze commit

```bash
# From a clean main checkout:
git checkout main && git pull --ff-only origin main
git log -1 --format='%H'  # capture the SHA; this is the OSF commit hash

# Tag (use the actual SHA from above):
git tag -a osf-prereg-2026-05-30 -m "OSF pre-registration freeze commit"
git push origin osf-prereg-2026-05-30
```

The tag name pattern is `osf-prereg-YYYY-MM-DD` so the relationship between OSF deposit date and repo state is obvious to anyone landing on the repo.

### 2. Generate Zenodo DOI

GitHub-Zenodo integration auto-archives tagged releases if Zenodo is enabled for the repo:

- Confirm Zenodo connection at <https://zenodo.org/account/settings/github/>
- Repo `FireDevPro/energy-stack` should appear in the toggle list with archiving enabled
- Pushing the tag triggers Zenodo to mint a DOI (typically within ~5 minutes)
- Capture the DOI from the Zenodo dashboard or the new GitHub release's badge

If Zenodo integration isn't pre-configured, do that first; otherwise manually create a release at <https://github.com/FireDevPro/energy-stack/releases/new> pointing at the tag, then Zenodo picks it up.

### 3. Create the OSF open-ended registration

At <https://osf.io/registries/osf/new>:

- **Template:** select **Open-Ended Registration**
- **Title:** the paper's working title (suggest matching the eventual manuscript title for searchability)
- **Description / narrative field:** ~1 page covering:
  - One paragraph: what the study is (single-household HVAC controller A/B SCED in Plainfield IL, summer 2026, 12 arm periods of 14 days each)
  - One paragraph: what's binding at the frozen commit (link to Zenodo DOI; reference [`docs/plans/sced-rebaseline-spec-2026-05-13.md`](plans/sced-rebaseline-spec-2026-05-13.md) as the source of truth for hypotheses, arm definitions, calendar, metrics, statistical analysis plan, and decision rules)
  - One paragraph: why it's a discovery study, not inference (cite spec §9.5)
  - Link block: Zenodo DOI + GitHub repo URL
  - Dates: experiment 2026-06-01 → 2026-11-16; analysis bundle deposit after data collection closes
- **Privacy:** Public (no embargo)
- **Contributors:** add yourself as admin; no other contributors

### 4. Submit and approve

Click "Submit for review". OSF emails the admin contributor (you) for confirmation; the registration auto-approves after 48 hours if no action. If you want it live faster, click the approval link in the email.

**Cannot edit after submission.** Read the narrative three times before clicking submit. Typos can only be addressed via post-deposit "Updates" which sit alongside the original (not replacing it).

### 5. Capture OSF link + add README badge

Once the registration is approved (visible at `https://osf.io/<5-char-code>/`):

- Add an OSF Preregistered badge to the repo README: `[![Pre-registered](https://img.shields.io/badge/OSF-preregistered-blue)](https://osf.io/<5-char-code>/)`
- Update the [`README.md`](../README.md) research-project headline to include the OSF link

### 6. Post-filing comms (optional)

Operator-discretion. Default scope:

- Update repo README badge + link (done in step 5)
- Anything beyond that (email collaborators, social media, blog post) is up to you

## What this doc does NOT cover

- The acceptance-criteria / artifact-list — owned by spec §11
- The validation-bundle structure — owned by spec §11 #13 and `docs/REPLAY_VALIDATION.md`
- The freeze-day checklist for repo state before filing — owned by `plans/pre-osf-doc-audit-execution-2026-05-18.md` PR9

## Reference

If the OSF platform UI has materially changed since this doc was authored (2026-05-18) and the steps above don't match the current flow, the substance is: open-ended template, narrative field referencing the Zenodo DOI of the tagged freeze commit, no embargo, public from filing. Walk the UI from <https://osf.io/registries> to find the current path.
