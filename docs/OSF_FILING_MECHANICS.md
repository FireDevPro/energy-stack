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

- Spec header `status: frozen` + `frozen_at_tag: <chosen tag name, e.g. osf-prereg-2026-05-30>` set
- Impl plan header `status: phases-1-6-complete-phase-7-deferred` + matching `frozen_at_tag` set
- `docs/THERMOSTAT_ARM_A_SCHEDULE.md` `effective_at_osf_tag` set to match
- `CITATION.cff` `version: <FREEZE-TAG>` + `date-released: <FREEZE-DATE>` set
- `arm_calendar.py` byte-identical across analysis-side + controller-side (CI hash-sync check per binding spec M5)
- LICENSE file present at repo root (matches the license field in `CITATION.cff` + `.zenodo.json`)
- Final shadow validation `validation_results.json` committed at the freeze commit (per binding spec §11 #13 D6 artifact policy)
- All PR1-PR8 work landed; main branch represents the OSF artifact
- Grep confirms zero remaining `<FREEZE-TAG>` / `<FREEZE-DATE>` placeholders

## Placeholders to fill at the freeze commit (PR9 final pass)

The PR9 draft branch uses **tag-based identifiers, not commit SHAs**. Reason: a commit can't reference its own SHA without `--amend` chicken-and-egg (the SHA the file cites becomes wrong the moment you amend; and GitHub squash-merge changes the SHA again). The tag IS the stable, externally-referenceable identifier — tag NAME is knowable BEFORE tagging, survives rebases/amends/squash-merges, and is what Zenodo/OSF cite anyway.

PR9 only fills placeholders that are **knowable before tagging**. Everything that depends on artifacts that don't exist yet (Zenodo DOI, OSF URL) goes in a separate `osf-post-deposit-fills` PR.

**Placeholders filled in PR9 (before merging):**

| Placeholder | Files | Replace with | When knowable |
|---|---|---|---|
| `<FREEZE-TAG>` | spec frontmatter `frozen_at_tag`, impl plan frontmatter `frozen_at_tag`, `docs/THERMOSTAT_ARM_A_SCHEDULE.md` frontmatter `effective_at_osf_tag` + body text, `CITATION.cff` `version` field + references URL, `.zenodo.json` `version` field | Tag name like `osf-prereg-2026-05-30`. Pick the name BEFORE tagging — it's just a string you choose. **`.zenodo.json` is what Zenodo uses for the GitHub-archive deposit version display (it takes precedence over `CITATION.cff` per Zenodo docs), so this field MUST be filled before tagging.** | Before commit + merge (you decide the name) |
| `<FREEZE-DATE>` | `CITATION.cff` `date-released` | ISO 8601 date (`2026-05-30` or whenever freeze actually happens) | Before commit + merge |

Grep them out before merging — restrict to the freeze artifact files only (this very runbook intentionally contains the literal `<FREEZE-TAG>` and `<FREEZE-DATE>` strings as documentation examples, so a repo-wide grep would falsely fail):

```bash
grep -n '<FREEZE-TAG>\|<FREEZE-DATE>' \
    docs/plans/sced-rebaseline-spec-2026-05-13.md \
    docs/plans/sced-rebaseline-implementation-2026-05-13.md \
    docs/THERMOSTAT_ARM_A_SCHEDULE.md \
    CITATION.cff \
    .zenodo.json
# Should return zero results before PR9 merges.
```

**Placeholders deliberately NOT in PR9 — added later via the post-deposit-fills PR:**

| Field | Files | Replace with | When knowable |
|---|---|---|---|
| `zenodo_doi:` (spec frontmatter) | added in post-deposit-fills PR | Bare DOI string (e.g. `10.5281/zenodo.12345678`) | After tag push triggers Zenodo (~5 min) |
| `osf_registration_url:` (spec frontmatter) | added in post-deposit-fills PR | OSF registration URL (e.g. `https://osf.io/abc12/`) | After OSF approves (or 48h auto-approve) |
| `osf_filed_at:` (spec frontmatter) | added in post-deposit-fills PR | ISO 8601 date OSF deposit actually happened | When OSF deposit submitted |
| OSF + Zenodo badges (README.md) | added in post-deposit-fills PR | Full URLs | When both DOI + OSF URL are minted/approved |
| Zenodo `related_identifiers` (`.zenodo.json`) | **Manual Zenodo UI edit, not a PR** | OSF URL in the deposit metadata | After OSF approves |

The Zenodo `.zenodo.json` deliberately does NOT carry `related_identifiers` at tag time. Reason: the Zenodo archive snapshots whatever `.zenodo.json` contains at tag time and freezes it. A later PR on main doesn't change the already-archived release metadata. The OSF cross-link gets added via the Zenodo web UI's "Edit metadata" after OSF approves — that's a one-time manual step, not a repo PR.

## Freeze-day sequencing (corrected: tag-based, no self-referential SHA)

1. Decide tag name (e.g. `osf-prereg-2026-05-30`)
2. Decide LICENSE; create LICENSE file at repo root if missing
3. Run final shadow validation on Pi-lab; commit canonical `validation_results.json` at the freeze commit (per binding spec §11 #13 D6 artifact policy)
4. On the PR9 draft branch: `sed -i 's/<FREEZE-TAG>/osf-prereg-2026-05-30/g'` + `<FREEZE-DATE>` replacement across the listed files. Single clean commit, no `--amend`.
5. Grep confirms zero remaining `<FREEZE-*>` placeholders
6. Merge PR9 to main
7. Tag main at merged commit: `git tag -a osf-prereg-2026-05-30 -m "OSF pre-registration freeze commit"` and `git push origin osf-prereg-2026-05-30`
8. Wait ~5 min for Zenodo to mint DOI from the tagged release; capture DOI from Zenodo dashboard
9. Create OSF open-ended registration per "Filing steps" below; reference the Zenodo DOI in the narrative
10. Submit OSF; wait for 48h auto-approve (or click the email approval link to accelerate)
11. After OSF approval: open `osf-post-deposit-fills` PR on main adding `zenodo_doi:` + `osf_registration_url:` + `osf_filed_at:` to spec frontmatter + the README badges. Merge.
12. Manually edit the Zenodo deposit via the web UI's "Edit metadata" page to add `related_identifiers` pointing at the OSF URL. Save.

The two-PR sequence (PR9 + post-deposit-fills) is unavoidable. The tagged Zenodo archive intentionally contains less metadata than the eventual state of main, because Zenodo can't auto-update from a later PR. The OSF deposit cites the Zenodo DOI of the PR9-merged commit; main eventually reflects all the cross-references. Tag stays as the stable identifier across this.

## Filing steps (6 steps, ~30 min wall clock)

### 1. Tag the freeze commit

```bash
# From a clean main checkout (PR9 must be merged first; tag name must
# match the <FREEZE-TAG> placeholder already filled in spec/impl/
# THERMOSTAT_ARM_A_SCHEDULE/CITATION.cff frontmatter):
git checkout main && git pull --ff-only origin main
git log -1 --format='%H'  # capture the SHA for the OSF/Zenodo deposit
                          # metadata only — NOT cited in any in-repo file
                          # (in-repo files cite the tag, which points at
                          # this SHA)

# Tag at the merged-PR9 commit:
git tag -a osf-prereg-2026-05-30 -m "OSF pre-registration freeze commit"
git push origin osf-prereg-2026-05-30
```

The tag NAME is what spec + impl plan + THERMOSTAT_ARM_A_SCHEDULE + CITATION.cff reference (via the `<FREEZE-TAG>` placeholder filled in PR9). The literal SHA captured above goes in the OSF registration narrative + Zenodo deposit metadata so the deposit cites a specific point-in-time; in-repo references stay tag-based so they survive any future history rewrites.

The tag name pattern is `osf-prereg-YYYY-MM-DD` so the relationship between OSF deposit date and repo state is obvious to anyone landing on the repo.

### 2. Generate Zenodo DOI

GitHub-Zenodo integration auto-archives tagged releases if Zenodo is enabled for the repo:

- Confirm Zenodo connection at <https://zenodo.org/account/settings/github/>
- Repo `Promithius-DR/energy-stack` should appear in the toggle list with archiving enabled
- Pushing the tag triggers Zenodo to mint a DOI (typically within ~5 minutes)
- Capture the DOI from the Zenodo dashboard or the new GitHub release's badge

If Zenodo integration isn't pre-configured, do that first; otherwise manually create a release at <https://github.com/Promithius-DR/energy-stack/releases/new> pointing at the tag, then Zenodo picks it up.

### 3. Create the OSF open-ended registration

At <https://osf.io/registries/osf/new>:

- **Template:** select **Open-Ended Registration**
- **Title:** the paper's working title (suggest matching the eventual manuscript title for searchability)
- **Description / narrative field:** ~1 page covering:
  - One paragraph: what the study is (single-household HVAC controller A/B SCED in Plainfield IL, summer 2026, 12 arm periods of 14 days each)
  - One paragraph: what's binding at the OSF freeze tag (link to Zenodo DOI for the tagged release; reference [`docs/plans/sced-rebaseline-spec-2026-05-13.md`](plans/sced-rebaseline-spec-2026-05-13.md) as the source of truth for hypotheses, arm definitions, calendar, metrics, statistical analysis plan, and decision rules). Capture the literal commit SHA in the OSF narrative for traceability — `git rev-parse osf-prereg-2026-05-30` (or `git log -1 --format=%H` immediately after tag push) returns the 40-char SHA the tag points at; paste it into the narrative alongside the tag name and Zenodo DOI.
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
