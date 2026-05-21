---
date: 2026-05-17
owner: chris
status: active
role-label: chris
---

# energy-proxy — AI Agent Context

Standing rules for every AI session. Any agent reading this (Claude, Codex, Cursor, etc.) follows these rules.

## Project

Home Energy Monitoring & HVAC Optimization: real-time and historical residential energy monitoring with dynamic-pricing-aware HVAC scheduling, running as a Docker Compose stack on Pi-lab. Built around ComEd Hourly Pricing + PJM 5CP avoidance. Owner: Chris. Phase: pre-OSF-filing for a pre-registered SCED field study starting summer 2026 (Arm A = CTK04AE programmed schedule; Arm B = active `hvac-scheduler` with RTP/DTOD/5CP-risk-aware RBC + safety supervisor). See [PROJECT.md](PROJECT.md), [README.md](README.md), [docs/plans/sced-rebaseline-spec-2026-05-13.md](docs/plans/sced-rebaseline-spec-2026-05-13.md) (binding spec), [docs/HVAC_LOGIC.md](docs/HVAC_LOGIC.md), [docs/THERMOSTAT_ARM_A_SCHEDULE.md](docs/THERMOSTAT_ARM_A_SCHEDULE.md).

**Pre-registration is binding.** Once filed to OSF, hypotheses, arm definitions, arm calendar, metric definitions, statistical analysis plan, and decision rules lock at a frozen commit hash. Anything touching scheduler or telemetry is on the critical path to June 1 experiment start. Operational features that don't serve Arm A / Arm B / observability are parked.

## Build & test

Authoring on Windows under `D:\Projects\energy-proxy\`. Runtime on Pi-lab (`192.168.20.10`) via Docker Compose.

**Deploy:**

- Canonical: merging to `main` is the deploy. GitHub Actions GitHub-hosted runner (ubuntu-latest) joins the tailnet as an ephemeral `tag:ci` node and reaches Pi-lab via Tailscale SSH; watches `deploy/**` and `.github/workflows/deploy.yml`; pushes to `main` matching those paths rsync + `docker compose build && up -d` + verify health. ~60-120s for single-service change (slower than self-hosted; network rsync over tailnet vs local cp). PRs touching only `tools/` or `docs/` do not deploy.
- Agent stops at `gh pr create` per branching policy. Your merge of a `deploy/**` PR in the GitHub UI is the deploy action. No separate ops step.
- Manual rsync (local-only testing) exists but gets overwritten on next push to main. Prefer branch + PR + merge.
- `.env` lives only on Pi-lab (`chmod 600`). Never committed. SOPS-encrypted copy at `deploy/energy-stack/secrets/env.sops.env` is the recovery path.
- Force a redeploy without code change: GitHub Actions UI -> "Deploy to Pi" -> "Run workflow".

**Tests:**

- Canonical full-stack: `bash deploy/energy-stack/run_tests.sh`. Runs each service in its own pytest process to sidestep the `sys.modules` cache collision across services with identically-named `app.py`.
- Single service: `cd deploy/energy-stack/<service>/ && python -m pytest .`
- **DO NOT** run `python -m pytest deploy/energy-stack` from the repo root. Collection silently fails or imports the wrong `app.py`. See [deploy/energy-stack/pytest.ini](deploy/energy-stack/pytest.ini) for context.
- One-time setup: `pip install -r deploy/energy-stack/requirements-dev.lock` plus each service's `requirements.txt` for imports.
- Most services have test files; depth varies. Add tests when bugs surface.

**Ops cheat sheet (on Pi-lab):** `docker compose ps`, `docker compose logs -f <service>`, `docker compose restart <service>`. Full ops table in [deploy/energy-stack/README.md](deploy/energy-stack/README.md).

**Stack guide of record:** [deploy/energy-stack/README.md](deploy/energy-stack/README.md). AGENTS.md gives the minimum an agent needs; that file has the full operational surface.

## Entry points

For the canonical doc map (all active docs grouped by intent), see
[INDEX.md](INDEX.md). That file is the WHAT (content map); this one
is the HOW (behavior contract).

Most-touched anchors if you want jump-links instead of the full index:
- Binding pre-registration spec: [docs/plans/sced-rebaseline-spec-2026-05-13.md](docs/plans/sced-rebaseline-spec-2026-05-13.md)
- HVAC scheduler logic: [docs/HVAC_LOGIC.md](docs/HVAC_LOGIC.md)
- Per-service detail: [docs/SERVICES.md](docs/SERVICES.md)
- Project narrative: [PROJECT.md](PROJECT.md)

## Tone

Be extremely concise. Sacrifice grammar for concision. Drop articles, filler, pleasantries. Fragments OK. Code and error messages quoted exact. No em dashes.

`caveman` skill default-on for stronger compression. Toggle off with "stop caveman" if full-sentence explanation needed.

## Core coding rules

1. **Think before coding.** State assumptions. If uncertain, ask. If multiple interpretations exist, present them, don't pick silently. If a simpler approach exists, say so. If something is unclear, stop and name it.

2. **Simplicity first.** Minimum code that solves the problem. No features beyond what was asked. No abstractions for single-use code. No unrequested flexibility or configurability. No error handling for impossible scenarios. If 200 lines could be 50, rewrite. Test: "Would a senior engineer say this is overcomplicated?"

3. **Surgical changes.** Touch only what you must. Don't "improve" adjacent code, comments, or formatting. Don't refactor what isn't broken. Match existing style. Spot unrelated dead code: mention, don't delete. Remove orphans YOUR changes created (imports, vars, functions). Every changed line traces to the user's request.

4. **Outside-in TDD.** First step on any feature: write a feature-level acceptance test that exercises the full vertical slice end-to-end. Drive inward from there. Reason: prevents "task done equals feature done" confusion. The project is the whole, not any given task. Vertical phasing is the default (each phase = thin end-to-end slice per the plan-authoring rule below); horizontal phasing (subsystem-organized) is allowed only with an explicit written justification in the plan and the default disposition is "no." Regardless of phasing style, the feature-level acceptance test must produce a visible signal at every PR boundary — **xfail (`strict=True`), not skip**. Skip is silent across PRs and trains the team to ignore the north star. The marker comes off the moment the test passes against the real implementation with zero scaffolding — that is the only definition of feature-complete.

For multi-step tasks, state a brief plan with verify steps. Strong success criteria let the agent loop independently. Weak criteria require constant clarification.

## Skill protocol

Follow Superpowers protocol (`using-superpowers` auto-loads). User instructions override skills. Skills override default system behavior. Project-specific defaults below override generic skill guidance:

- **New feature work:** `grill-me` or `brainstorming` first. Resolve the design tree before coding.
- **Bugs / failures / regressions:** `diagnose`. Build a fast pass/fail signal before theorizing.
- **Multi-phase features:** `writing-plans`. See plan-authoring discipline below.
- **Executing a written plan:** `executing-plans` or `subagent-driven-development`.
- **Before claiming done, fixed, or passing:** `verification-before-completion`. Evidence before assertions.
- **Architectural friction:** `improve-codebase-architecture`.
- **Throwaway exploration:** `prototype`.
- **Unfamiliar code region:** `zoom-out`.
- **Session compact / handoff:** `handoff`.

## n8n workflows

**Authoring is via the n8n SDK MCP only.** Use `mcp__n8n__*` tools. Never edit workflow JSON files with the Edit tool — that path has burned multiple agent sessions on escape-encoded strings and Postgres publish corruption.

Canonical SDK source lives in `tools/n8n/<workflow>.workflow.ts`. Workflow JSON in `docs/n8n/` is a derived export for diff visibility only; never hand-edit. Re-export from SDK after each change.

Authoring flow:

1. Read SDK reference once per session: `mcp__n8n__get_sdk_reference`.
2. Discover nodes: `search_nodes` + `get_node_types` for exact parameter shapes — never guess.
3. Edit the `.workflow.ts`. Validate: `mcp__n8n__validate_workflow`.
4. Deploy to draft: `mcp__n8n__update_workflow` (live cron stays on prior active version).
5. Test with pin data: `prepare_test_pin_data` + `test_workflow`. Pin-data fixtures live in `tools/n8n/fixtures/<workflow>.pin.json`.
6. Live-data manual run: `execute_workflow` (`executionMode="manual"`).
7. Publish: `publish_workflow`.
8. Verify post-publish: `execute_workflow` (`executionMode="production"`) per memory `project-n8n-publish-may-corrupt-jscode`.
9. Rollback path: `unpublish_workflow` if production-mode run reveals corruption.

## Session-start working-tree audit

Every session and every dispatched subagent runs `git status` and `git stash list` BEFORE any other action.

If working tree contains modifications or untracked files unrelated to the current task, triage first:

- **Commit** if it belongs on the current branch and work is complete.
- **Stash** with descriptive message if it belongs to a different feature.
- **Discard** only with explicit user approval.
- **Escalate** if you don't know what it is. Never proceed past unknown WIP.

Reason: branch checkouts (`git checkout -b`, `git switch`) carry the working tree. `git add <file>` stages the current working-tree state of that file, not just your edits. Uncommitted edits plus a subagent running `git add` equals contaminated commits with mixed concerns. Audit prevents this.

Subagent briefings must include this rule explicitly. Subagent's first action is `git status`. If anything is unexpected, escalate to controller before continuing.

## Plan-authoring discipline (multi-phase features only)

Small single-file changes skip this section.

1. **Spec is source of truth for intent.** Plans are execution artifacts. Every task cites the spec section it implements.
2. **If a task needs a decision the spec doesn't answer, update the spec first.** No local "fit for purpose" calls.
3. **Unified plan doc at `docs/plans/<feature>-plan.md`.** One file, phase headers inside. Not a directory of phase files.
4. **Each phase = vertical slice (data -> logic -> UI / output).** Demoable end-to-end. Horizontal phases (all schema first, then all services, then all UI) forbidden.
5. **Phase 1 = tracer bullet.** Smallest possible cut through every layer the feature will eventually touch.
6. **Front-load full decomposition.** Plan all phases before executing any. If phase 1 surprises, revise later phases in place. Cheaper than designing each phase from cold.
7. **Archive on merge.** Move to `docs/plans/archive/<feature>-plan.md` in the commit that closes the feature branch.

## Multi-Phase Feature Workflow

For multi-phase features, distinguish task completion from feature completion.

A feature starts with a unified plan and an outside-in acceptance test that represents the whole feature. That test may be xfail/skip/scaffolded at first, but it remains the north star until the full feature is complete.

Work may be split into task PRs. A task PR is a coherent reviewable slice of the larger feature and may merge independently if it improves the codebase. Merging a task PR does not mean the feature is complete.

Each task PR should report both:

- **Task status:** what this PR/phase completed, tests run, and what remains before this PR is ready.
- **Feature status:** what remains in the unified feature plan, whether the feature-level acceptance test passes without scaffolding, and whether replay/validation gates are complete.

Do not call a feature complete until:

1. the feature-level outside-in test passes without replacing the real implementation under test,
2. all planned phases are implemented or explicitly descoped,
3. shape/behavior/oracle audit gaps are closed or documented as non-blocking,
4. required real-shape replay or operational validation gates pass, or expected empties are reason-coded,
5. docs and plan status are updated or archived.

After each task PR is squash-merged, sync `main` and continue the same feature thread from a fresh branch/PR. Do not stack PRs.

## Branching policy

1. **Never push directly to `main`.** Always branch.
2. **Merging to `main` requires explicit per-merge approval.** Prior approval of branch work does not extend to the merge. Silence or a pending tool call is not approval.
3. **Agent stops at `gh pr create`.** Surface PR URL. User reviews and merges in the GitHub UI. After merge, agent syncs local `main` (`git pull --ff-only origin main`) and deletes the local feature branch.
4. **Never bypass safety hooks.** No `--no-verify`, no `--no-gpg-sign`, no `ALLOW_*=1` overrides unless explicitly instructed for this specific push.

Per memory `feedback_stacked_pr_retargeting`: every PR uses `--base main`, no stacking; wait for prior PR to merge before opening the next.

Honor `.git/hooks/pre-push` if present.

## Doc hygiene (Layer 1: discipline)

Universal rules:

- Every doc living beyond one session carries a YAML header (date, owner, status, role-label).
- Headerless non-session doc is a bug. Fix in the same commit.
- Dates: ISO 8601 (`YYYY-MM-DD`). No "Q2 2026", no year-only, no "April 2026".
- Convert relative dates in conversation to absolute before saving ("Thursday" -> `2026-05-14`).

Layer 2 (drift-rules, audit artifacts, Stop hook): project-specific infrastructure. Add when the project warrants. Stub:

```
.claude/drift-rules.yaml          # code-path -> doc-update triggers + severity
.claude/hooks/                    # pre-push / Stop hook scripts that enforce
docs/DOC_AUDIT.md                 # audit state, per-doc verdicts
```

## Agents

- Solo project. Chris is operator. No named subagents yet.

Role labels: `chris`, `code-team`, `unknown`. Extend per project.

## See also

- User-level rules: `~/.claude/rules/` (auto-loaded every session).
- Path-scoped rules: `.claude/rules/` in this repo (load only when Claude reads matching files).
- Memory: `~/.claude/projects/D--Projects-energy-proxy/memory/MEMORY.md`.
- Project history and roadmap: [PROJECT.md](PROJECT.md).
