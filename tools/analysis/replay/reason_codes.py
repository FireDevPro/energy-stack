"""Reason codes for legitimately-empty stage outputs.

Per OSF_FILING.md criterion 14(c): when a downstream stage produces
empty outcome tables because its required input was absent or
insufficient (e.g., Stage 7 SCED p-values when the export window
contained no arm cycling), the stage MUST emit a machine-readable
reason code rather than silently producing an empty CSV that a
human or audit script could mistake for "no effect found."

Each stage writes its reason report to
`<out>/stage<N>/reason_report.json` listing one or more
`StageReasonReport` entries.
"""
from __future__ import annotations

import dataclasses
import enum
import json
from pathlib import Path
from typing import Any


class ReasonCode(str, enum.Enum):
    """Pre-registered enumeration of legitimately-empty-output reasons.

    Adding a new code requires updating this enum AND the consuming
    downstream code that handles it. Reviewers checking a pipeline
    output for "no result found" can grep for these strings to
    distinguish "the pipeline ran but found nothing" from "the
    pipeline ran successfully and produced findings."
    """

    # Stage 2 / qualifying weeks
    NO_QUALIFYING_WEEKS_IN_WINDOW = "no_qualifying_weeks_in_window"
    NO_ARM_ASSIGNMENTS_IN_WINDOW = "no_arm_assignments_in_window"
    NO_WEEK_INPUTS_FROM_STAGE1 = "no_week_inputs_from_stage1"

    # Stage 3 / weekly aggregates
    NO_QUALIFYING_WEEKS_FROM_STAGE2 = "no_qualifying_weeks_from_stage2"

    # Stage 4 / matched pairs
    SINGLE_ARM_IN_WINDOW = "single_arm_in_window"
    NO_MATCHED_PAIRS_POSSIBLE = "no_matched_pairs_possible"
    INSUFFICIENT_QUALIFYING_WEEKS_PER_ARM = "insufficient_qualifying_weeks_per_arm"

    # Stage 5 / effects
    NO_PRIMARY_QUALITY_PAIRS = "no_primary_quality_pairs"

    # Stage 6 / O2
    NO_PJM_5CP_HOURS_IN_WINDOW = "no_pjm_5cp_hours_in_window"
    AMBIGUOUS_SUMMER_YEAR = "ambiguous_summer_year"
    INSUFFICIENT_PEAKS_BY_ARM = "insufficient_peaks_by_arm"
    NO_COMED_BILLS_IN_WINDOW = "no_comed_bills_in_window"
    NO_REFOSS_MAINS_IN_WINDOW = "no_refoss_mains_in_window"
    NO_COMED_5CP_HOURS_IN_WINDOW = "no_comed_5cp_hours_in_window"
    INCOMPLETE_COMED_5CP_IN_WINDOW = "incomplete_comed_5cp_in_window"
    NO_5CP_STATE_IN_WINDOW = "no_5cp_state_in_window"

    # Stage 7 / SCED
    NO_PAIR_DIFFERENCES_FROM_STAGE5 = "no_pair_differences_from_stage5"

    # Stage 8 / decomposition
    NO_PRICE_DATA_FOR_CLASSIFICATION = "no_price_data_for_classification"
    NO_GRID_EVENT_DAYS_IN_WINDOW = "no_grid_event_days_in_window"

    # Stage 9 / sensitivities
    UPSTREAM_STAGES_EMPTY = "upstream_stages_empty"

    # Generic / measurement-level
    MEASUREMENT_NOT_IN_MANIFEST = "measurement_not_in_manifest"
    MEASUREMENT_EMPTY_IN_WINDOW = "measurement_empty_in_window"
    POST_2025_MEASUREMENT_NO_HISTORY = "post_2025_measurement_no_history"
    PRE_RANDOMIZATION_WINDOW = "pre_randomization_window"


@dataclasses.dataclass(frozen=True)
class StageReasonReport:
    """One reason-code entry for one stage."""
    stage: str                       # e.g., "stage7"
    output_file: str                 # e.g., "sced_pvalues.csv"
    reason_code: ReasonCode
    note: str | None = None
    related_inputs: tuple[str, ...] = ()  # e.g., ("pair_diffs.csv",)


def write_reason_report(
    stage_dir: Path,
    reports: list[StageReasonReport],
) -> Path:
    """Write a stage's reason report to JSON.

    Empty reports list produces a file with no entries — distinguishes
    "stage was reached but had no reasons to report" from "file is
    missing." Downstream audit can check whether the file exists.
    """
    stage_dir.mkdir(parents=True, exist_ok=True)
    report_path = stage_dir / "reason_report.json"
    with open(report_path, "w") as f:
        json.dump(
            {
                "entries": [
                    {
                        "stage": r.stage,
                        "output_file": r.output_file,
                        "reason_code": r.reason_code.value,
                        "note": r.note,
                        "related_inputs": list(r.related_inputs),
                    }
                    for r in reports
                ],
            },
            f,
            indent=2,
            sort_keys=True,
        )
    return report_path
