// Per-hour PJM settled real-time LMP for COMED zone, written by
// pjm-dm2-poller (rt_hrl_lmps endpoint). Bill-canonical supply price
// for ComEd Rate BESH (spec docs/plans/sced-rebaseline-spec-2026-05-13.md §8):
// the bill charges PJM RT settled hourly LMP for the COMED zone with
// no markup, so this is what HVAC$ rolls up against.
//
// pnode_id=33092371 (COMED zonal aggregate).
//
// Latency: T+2 worst case (PJM posts settled data daily 11:00-12:00 ET
// on business days). Retrospective analysis runs after each arm period
// closes; latency does not affect outcome computation.
from(bucket: $bucket)
  |> range(start: $start, stop: $end)
  |> filter(fn: (r) => r._measurement == "pjm.lmp_rt_hourly")
  |> filter(fn: (r) =>
        r._field == "total_lmp_rt"
        or r._field == "system_energy_price_rt"
        or r._field == "congestion_price_rt"
        or r._field == "marginal_loss_price_rt")
  |> filter(fn: (r) => r.pnode_id == "33092371")
  |> yield(name: "pjm_lmp_rt_hourly")
