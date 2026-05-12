// Per-line-item rows for each ComEd bill, written by
// deploy/energy-stack/scripts/parse_comed_bill.py + comed_influx.py.
// One row per (bill, line_item) pair. Tags: account_no, category
// (SUPPLY | DELIVERY | TAXES_FEES_CREDITS), line_item (e.g. "Capacity
// Charge", "Customer Charge"). Fields: amount, quantity, unit, rate.
//
// Stage 6 Layer 3 reads the "Capacity Charge" line_item to compute
// the May-Sep Y+1 capacity-charge total per Att. M-2. The query
// pulls every line item; the loader filters by `line_item ==
// "Capacity Charge"`.
from(bucket: $bucket)
  |> range(start: $start, stop: $end)
  |> filter(fn: (r) => r._measurement == "comed.bill_lineitems")
  |> yield(name: "comed_bill_lineitems")
