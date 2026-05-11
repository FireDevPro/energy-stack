// PJM instantaneous load by area (RTO and ComEd zone), from
// pjm-dm2-poller. Used by Stage 6 detector accuracy report
// (compare live 5CP detector decisions against PJM-published 5CP hours)
// and by Stage 2 Rule 6 logging.
from(bucket: $bucket)
  |> range(start: $start, stop: $end)
  |> filter(fn: (r) => r._measurement == "pjm.inst_load")
  |> filter(fn: (r) => r._field == "mw")
  |> filter(fn: (r) => r.area == "PJM RTO" or r.area == "COMED")
  |> yield(name: "pjm_inst_load")
