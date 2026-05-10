# OSF pre-registration filing procedure

OSF (Open Science Framework, <https://osf.io>) hosts the binding
pre-registration for this study. Filing locks the experimental design,
threshold values, analysis plan, and assignment CSV at a frozen commit
hash. Changes after filing require an OSF amendment with explicit
justification.

Per [`EXPERIMENT_DESIGN.md§13`](EXPERIMENT_DESIGN.md#13-pre-registration-commitments)
and [`ARM_B_IMPLEMENTATION.md§10`](ARM_B_IMPLEMENTATION.md#10-acceptance-criteria-for-osf-filing).

---

## Acceptance criteria (pre-flight checklist)

ALL of the following must be true before filing. Each is verifiable
without operator intervention:

  1. ✅ All unit tests passing across hvac-scheduler, nws-poller,
     pjm-dm2-poller, scripts. See: `pytest deploy/energy-stack/`.
  2. ✅ All integration tests passing on replay data
     (`test_integration_2025_replay.py` + the §3 PJM replay).
  3. ✅ 24-hour dry-run validation completed successfully per
     [`DRY_RUN_VALIDATION.md`](DRY_RUN_VALIDATION.md).
  4. ✅ AIR toggling procedure documented and tested at least once
     per [`ARM_TRANSITIONS.md`](ARM_TRANSITIONS.md).
  5. ✅ All InfluxDB measurements writing correctly:
       - new: `hvac.price_overlay`, `hvac.5cp_state`, `hvac.arm_transitions`
       - updated: `pjm.metered_load` (zone="CE" hourly cadence per §0b),
         `nws.forecast` with new fields per §0a (`apparent_max_f`,
         `apparent_min_f`, `apparent_avg_f`, `rh_max_pct`, `rh_avg_pct`,
         `sky_cover_avg_pct`, `wind_gust_max_mph`).
  6. ✅ Two-week shakedown period completed without unresolved issues.
  7. ✅ Assignment CSV regenerated with the locked seed and committed
     ([`docs/experiment-assignments-summer-2026.csv`](experiment-assignments-summer-2026.csv)).
  8. ✅ EXPERIMENT_DESIGN.md frozen at the OSF-referenced commit hash.

If any of these is incomplete by 2026-05-30, OSF filing slips and
randomization start date moves accordingly.

---

## Step-by-step filing

### 1. Verify the locked artifacts on a clean checkout

```bash
git checkout main && git pull --ff-only
pytest deploy/energy-stack/   # everything green

# Verify the assignment CSV is the current locked output:
python deploy/energy-stack/scripts/randomize_arms.py \
    --output /tmp/regenerated.csv
diff /tmp/regenerated.csv docs/experiment-assignments-summer-2026.csv
# (no differences expected)

# Snapshot the OSF-pinned commit:
COMMIT_HASH=$(git rev-parse HEAD)
echo "OSF commit hash: $COMMIT_HASH"
```

### 2. Tag the OSF commit

```bash
git tag -a osf-prereg-2026-05-30 -m "OSF pre-registration filing"
git push origin osf-prereg-2026-05-30
```

### 3. File on OSF

Navigate to <https://osf.io/registrations/new>, select the "Open-Ended
Registration" template, and attach:

  - URL of the tagged GitHub release matching ``osf-prereg-2026-05-30``
  - Direct URL to ``docs/EXPERIMENT_DESIGN.md`` at that commit hash
  - Direct URL to ``docs/experiment-assignments-summer-2026.csv`` at
    that commit hash

The OSF page resolves to a permanent DOI on submission. Record that DOI
in `EXPERIMENT_DESIGN.md` §13 references and re-commit (this commit is
post-filing and not part of the locked snapshot).

### 4. Post-filing communication

  - Update the GitHub repo `README.md` to surface the OSF DOI.
  - Send a one-line note to any collaborators (none currently for this
    N=1 study).

### 5. Begin Arm A on Monday 2026-06-01 00:00 CT

Per [`ARM_TRANSITIONS.md`](ARM_TRANSITIONS.md), with the assignment for
2026-W23 being Arm A under the locked seed (regenerated CSV pinned at
the OSF commit hash).

---

## Algorithm change pre-OSF (May 2026)

`randomize_arms.py` was updated from a 2-week-block (1 A + 1 B per pair,
random order) to a 4-week-block (2 consecutive A + 2 consecutive B,
AABB or BBAA random) algorithm in this commit window, before OSF
filing, to match `EXPERIMENT_DESIGN.md` §5. The 2-week-arm-period unit
is the methodological choice the design committed to (12 analyzable
days per arm-period after 48h washout vs 5 under 1-week arms); the
prior implementation predated the design freeze.

The pre-OSF algorithm change is documented here rather than as an OSF
amendment, since OSF filing has not yet occurred. Once the OSF
pre-registration is filed, any further algorithm change requires an
explicit amendment with justification.

---

## Year-round vs summer-only naming

The CSV is named `experiment-assignments-summer-2026.csv` for
historical continuity but covers year-round 2026-06-01 through
2027-05-31 weeks (53 Mondays) per `EXPERIMENT_DESIGN.md` §5. Formal
analysis weeks are filtered post-hoc by realized weekly cooling-degree
days >= 5; non-cooling-relevant weeks are reported descriptively only.
