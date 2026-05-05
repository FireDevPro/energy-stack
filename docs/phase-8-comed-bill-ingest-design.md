# Phase 8 — ComEd Bill Ingest Design

**Status**: Design approved (2026-05-05), not yet implemented
**Owner**: Chris dePaola
**Depends on**: existing `energy-stack` (InfluxDB, Grafana), existing pollers (`comed-poller`, `eagle-poller`)

---

## Problem

The `webdashboard-api` cost calc and Grafana cost panels currently estimate cost as `Σ(power × hourly_supply_price)`. This is **supply-side only**. A real ComEd bill adds delivery charges, capacity, riders, taxes, and franchise fees that typically run 40–70% on top of supply cost, so the dashboard's "today's cost" understates the actual bill by a meaningful multiple. There is no feedback loop from real bills to confirm or calibrate the dashboard's estimates, no record of capacity-charge trends (the metric the HVAC scheduler exists to suppress), and no per-cycle EAGLE-vs-billed-kWh sanity check.

Capacity charges in particular: the latest bill (3/24–4/23/26) shows a Capacity Charge of $54.64 — *larger* than the Electricity Supply Charge of $42.15. It's calculated as `peak_kW × $8.32925`, where `peak_kW` is locked annually from the prior summer's PJM 5CP coincident-peak readings. The `hvac-scheduler`'s `HOT_5CP_RISK` day type exists specifically to suppress this peak. Without bill ingest, there is no way to measure whether that strategy is working.

## Goals

- Capture every ComEd bill into InfluxDB so historical bill data can be charted, queried, and compared against in-stack telemetry
- Reconcile the dashboard's cost estimate against the actual bill total per cycle
- Sanity-check EAGLE meter readings against ComEd's billed kWh per cycle
- Track `peak_kW` and `capacity_charge_$` over time so HVAC-scheduler effectiveness becomes measurable starting with the June 2026 bill (first bill reflecting summer 2025 5CP)
- Produce a real-time forward projection of the current cycle's bill that converges to the actual number

## Non-goals

- Automated retrieval of bills from comed.com (portal uses 2FA-via-SMS; not worth automating)
- Email-based ingest (ComEd notification emails contain only a "view bill" link, no PDF attachment)
- Replacing the dashboard's real-time supply-cost calc — the bill ingest *augments* it with delivery and rider context, not replaces it
- A general-purpose utility-bill parser — this is ComEd-specific; future utility ingests can borrow the pattern but get their own scripts

## Architecture

A **single Python script**, `deploy/energy-stack/scripts/parse_comed_bill.py`, checked into the repo. No container, no service loop, no n8n workflow.

Workflow per bill (~5 minutes/month, manual):

```
1. ComEd email arrives → click "View bill" on portal → download PDF
2. scp <bill>.pdf pi-lab:~/energy-stack/inbox/comed/
3. ssh pi-lab "cd ~/energy-stack && python scripts/parse_comed_bill.py inbox/comed/<bill>.pdf"
4. Script: parse → write to InfluxDB → move PDF to inbox/comed/processed/comed-YYYY-MM-DD-<period>.pdf
```

A workstation alias (`comed-ingest <pdf>`) collapses steps 2-3 into one command.

**Failure path**: script run by hand, errors print to stderr. PDF stays in `inbox/` until re-run succeeds. No Telegram alerting needed (the operator is already in the terminal when running it).

**Idempotency**: re-running the same bill upserts the same Influx points because the `(measurement, tag set, timestamp)` tuple — `(comed.bill, account_no + rate_plan + bill_type, service_to @ 23:59:59 CDT)` — is identical between runs. No application-level dedup required; InfluxDB's natural upsert behavior handles it. The duplicate file (`1c785f6f`) in the existing backfill set collapses onto its twin (`d54d630c`) automatically. (Note: an earlier draft of this spec described a SHA-256-derived `bill_id` tag; the implementation moved to natural upsert because it's simpler and avoids a high-cardinality tag for no purpose.)

### Why a script and not a container

12 events per year. The parser is the only hard part. A service loop, Dockerfile, healthcheck, compose entry, and Telegram failure-alert wiring add ~100 LOC and zero capability over `python parse_comed_bill.py file.pdf`. The script lives in the repo so future-Claude doesn't have to recreate the regex from scratch.

### Why pypdf and not Docling

Empirically tested both on bill `a33bdd65` (2/23–3/24/26 cycle):

- **pypdf**: flat text stream where each line item appears on the same line as its values. The regex `Capacity Charge\s*([\d.]+)\s*kW.*?\$([-\d.]+)` is unambiguous and bombproof. Whitespace artifacts in older bills are handled by normalizing input with `re.sub(r"\s+", " ", text)`.
- **Docling (table extraction on)**: conflates layout-adjacent columns into single table rows — `Electricity Supply Charge $58.87` and `Charges/Credits from previous bill $254.01` ended up in the same row.
- **Docling (table extraction off)**: stacks all labels first then all values in a single mega-cell — requires positional zip with variable-length value rows, fragile to off-by-one breakage. The DELIVERY section header was lost entirely (its total `$123.65` appeared as an unlabeled H2).

Diagnosis: ComEd's page 1 is a layout-positioned multi-column print, not a real table. Docling's strength is true tables; it tries to either table-ify (mode 1) or give up (mode 2) and both fail on layout-positioned line items. pypdf reads in document order and that happens to be exactly what we want here.

Docling stays in the toolkit for future utility ingests where the source genuinely is tabular (water, gas, property tax).

## Schema

Two InfluxDB measurements in the existing `energy` bucket.

### `comed.bill` — one point per billing cycle

Timestamp: `service_to` at 23:59:59 America/Chicago.

| Tag         | Value                                          |
|-------------|------------------------------------------------|
| account_no  | `9999999991`                                   |
| rate_plan   | `Residential-Single` \| `Residential-HourlySingle` |
| bill_type   | `normal` \| `transition` (for the 7-day Aug 2025 stub) |

| Field                  | Type   | Notes                                                    |
|------------------------|--------|----------------------------------------------------------|
| total_due              | float  | $                                                        |
| kwh                    | int    | from meter info table                                    |
| peak_kw                | float  | null on fixed-rate bills (no Capacity Charge line)      |
| supply_total           | float  | $                                                        |
| delivery_total         | float  | $                                                        |
| taxes_total            | float  | $, signed (credits make it negative)                    |
| misc_total             | float  | $, signed; usually $0.00 (billing adjustments, prior-bill credits) |
| effective_rate_per_kwh | float  | derived: total_due / kwh                                |
| service_days           | int    | from header                                              |
| issued_date            | string | YYYY-MM-DD                                              |
| service_from           | string | YYYY-MM-DD                                              |
| service_to             | string | YYYY-MM-DD                                              |

### `comed.bill_lineitems` — one point per line item per cycle

Timestamp: same as parent `comed.bill` row.

| Tag         | Value                                                              |
|-------------|--------------------------------------------------------------------|
| account_no  | `9999999991`                                                       |
| category    | `SUPPLY` \| `DELIVERY` \| `TAXES_FEES_CREDITS` \| `MISC`           |
| line_item   | e.g. `Capacity Charge`, `Carbon-Free Energy Resource Adj`, `State Tax` |

| Field    | Type   | Notes                                                |
|----------|--------|------------------------------------------------------|
| amount   | float  | $, signed                                            |
| quantity | float  | optional — e.g. `1646` for kWh-based items           |
| unit     | string | optional — `kWh` \| `kW` \| empty                    |
| rate     | float  | optional — e.g. `0.06228` for distribution facility  |

The lean fields on `comed.bill` give fast top-line queries without joining; `comed.bill_lineitems` is there for "where did the money go this month" panels and historical line-item drift analysis.

## Parser Strategy

Two regex tables, dispatched on the rate plan string parsed from the document header.

```python
RATE_PLAN_HOURLY = "Residential - Hourly Single"
RATE_PLAN_FIXED  = "Residential - Single"
```

**Common extractors** (work on both formats):

| Field           | Regex                                                          |
|-----------------|----------------------------------------------------------------|
| Service period  | `SERVICE FROM (\d+/\d+/\d+) THROUGH (\d+/\d+/\d+) \((\d+) DAYS?\)` |
| Issued date     | `Issued\s*(\d+/\d+/\d+)`                                       |
| Account no      | `Account\s*#\s*(\d+)`                                          |
| Total due       | `Total Amount Due\s*\$?([\d,]+\.\d{2})` (fallback to `Service Period Total`) |
| kWh             | from meter info table row                                      |
| Rate plan       | `Residential\s*-\s*([\w ]+?)\s+ComEd`                          |

**Hourly-Single-only extractors**:

- Capacity Charge → `Capacity Charge\s*([\d.]+)\s*kW.*?\$?([-\d.]+)` (kW + amount)
- Transmission Services Charge, Misc Procurement Components Chg, Purchased Electricity Adjustment as separate line items

**Fixed-only extractors**:

- No capacity charge — `peak_kw` field written as null
- Different supply structure (single "Electricity Supply Charge" line item; no transmission/procurement breakouts)
- Exact patterns finalized after running parser against 8/25–10/23/25 bills (`dff224a6`, `56174f5a`)

**Whitespace tolerance**: input text from `pypdf.extract_text()` is normalized with `re.sub(r"\s+", " ", text)` before regex matching, hiding the older-bill stripped-whitespace rendering artifact from the regex layer.

**Validation gates** (all must pass before writing to Influx; any failure → print line numbers, exit non-zero, leave PDF in `inbox/`):

1. `abs((supply_total + delivery_total + taxes_total + misc_total) - total_due) < 0.01`
2. `(service_to - service_from).days == service_days` from header (off-by-one tolerated for ComEd's inclusive day count)
3. `kwh > 0` UNLESS the bill qualifies as a transition stub (`kwh == 0 AND service_days < TRANSITION_MAX_DAYS`, i.e. cycle-adjustment bills under 10 days). A `kwh == 0` full-cycle bill is treated as a parser miss and refused.
4. `account_no == 9999999991` (guards against accidentally ingesting someone else's bill)

## Backfill

9 unique bills in possession, covering 8/18/2025 → 4/23/2026 (the transition stub through April 2026). One duplicate (`1c785f6f` is a copy of `d54d630c`) collapses automatically via idempotency key.

| # | File hash | Period          | Plan           | kWh  | peak kW | Total   |
|---|-----------|-----------------|----------------|------|---------|---------|
| 1 | 4ace3f0e  | 8/18 → 8/25/25  | Single (transition) | 0    | —    | $64.51  |
| 2 | dff224a6  | 8/25 → 9/23/25  | Single         | 1367 | —       | $247.83 |
| 3 | 56174f5a  | 9/23 → 10/23/25 | Single         | 1504 | —       | $268.41 |
| 4 | 49aa4c59  | 10/23 → 11/23/25 | Hourly Single | 1460 | —       | $193.68 |
| 5 | b3aca6ce  | 11/23 → 12/22/25 | Hourly Single | 1662 | 6.56    | $300.05 |
| 6 | 05dcb2f9  | 12/22 → 1/25/26 | Hourly Single  | 1954 | 6.56    | $306.32 |
| 7 | 42772a11  | 1/25 → 2/23/26  | Hourly Single  | 1601 | 6.56    | $254.01 |
| 8 | a33bdd65  | 2/23 → 3/24/26  | Hourly Single  | 1646 | 6.56    | $191.22 |
| 9 | d54d630c  | 3/24 → 4/23/26  | Hourly Single  | 1715 | 6.56    | $247.67 |

Backfill executes as a sequential one-shot: parser runs locally against each PDF, writes Influx line protocol to a `.lp` file, transferred to Pi-lab, applied with `influx write`. Single pass, no production-script changes needed. The transition stub is tagged `bill_type=transition` so downstream queries can exclude it from per-cycle averages.

**Note on Capacity Charge constancy**: peak_kw of 6.56 is identical across all 5 Hourly Pricing bills because PJM 5CP is locked annually from the prior summer. The first bill reflecting summer 2025's 5CP arrives June 2026. Summer 2026 (running now, with the HVAC scheduler active) determines what gets locked for the June 2026 → May 2027 capacity charges.

## Dashboards

New file: `grafana/dashboards/comed-bill-reconciliation.json`. Four panels.

### A) Bill total vs InfluxDB-projected total

Bar chart, one bar per closed billing cycle. For each cycle:
- Actual: `comed.bill.total_due`
- Projected: `Σ(refoss_whole_home_W × comed.prices.hourly_avg)` integrated over `[service_from, service_to)`, plus a constant for the previous month's delivery+taxes
- Delta: $ and % difference labeled on each bar

Initial expectation: actual will exceed projected by 40–70% (the delivery + taxes the projected calc doesn't include). Tracks the gap shrinking as the projected calc gets refined.

### B) EAGLE kWh vs billed kWh

Bar chart per cycle:
- Billed: `comed.bill.kwh`
- EAGLE: Σ delta of `eagle.cumulative_summation_delivered` over `[service_from, service_to)`

Should match within ~1% (rounding + meter read timing). Persistent disagreement → meter or EAGLE bug worth investigating.

### D) Capacity charge tracker

Time series:
- `comed.bill.peak_kw` per cycle (currently flat 6.56 across all Hourly bills; will step on the June 2026 bill)
- `comed.bill_lineitems` filtered to `line_item="Capacity Charge"` `amount` per cycle (same flatness; jump expected June 2026)

Annotation overlay: HVAC scheduler day-type history from `hvac.actions` (the `HOT_5CP_RISK` days). Connects "we forecast and pre-cooled for 5CP risk on these days" to "the resulting peak_kW dropped (or didn't) on the next annual reset."

### F) Forward projection (current cycle)

Single big-stat panel:

```
projected_total = supply_so_far                               // Σ Refoss × ComEd prices over [cycle_start, now)
                + delivery_estimate                            // see note
                + capacity_charge                              // last bill's amount (constant within annual window)
                + taxes_estimate                               // most recent bill's taxes_total / kwh × est_total_kwh
                + days_remaining * (avg_daily_$ over last 7d) // covers supply for the not-yet-elapsed portion
```

`est_total_kwh = kwh_so_far × (cycle_days / days_elapsed)`.

`delivery_estimate` v1: `(most recent bill's delivery_total / cycle_days) × cycle_days` — a flat per-cycle estimate, since delivery has both fixed components (Customer Charge, Standard Metering — ~$19/cycle) and per-kWh components (Distribution Facility — ~$0.063/kWh). Per-kWh-only or per-day-only estimates skew the projection. Refine in Phase 2 once we have several cycles of data showing actual fixed-vs-variable split.

Updates daily as the cycle progresses. Converges to the actual bill amount as `service_to` approaches.

## Open Questions / Future Work

- **Phase-2 zero-touch ingest** (deferred): once the parser is proven, a 4-node n8n workflow can listen for a Telegram forward of the bill PDF, download the attachment to a temp file, shell out to `parse_comed_bill.py`, and post the parse result back. ~30 min implementation. Not worth doing until parser stability is confirmed across a full year of bills.
- **Pre-Hourly-Pricing supply structure**: the 8/25–10/23/25 bills used "Residential - Single" with a different supply line-item structure. Final regex patterns get pinned during implementation by running against `dff224a6` and `56174f5a`.
- **Effective $/kWh by component**: future panel could break out "supply $/kWh" vs "delivery $/kWh" vs "taxes $/kWh" trends to spot which line items are growing.
- **Future utility ingests** (water, gas, property tax): borrow the script pattern but each gets its own parser. Docling may be the better tool for water/gas bills depending on layout.
- **Annotation of rate plan switch**: 10/23/25 marked on time-series panels showing supply costs (the switch isn't apples-to-apples for supply, only delivery + total).

## Project Files Affected

| Path                                                  | Action  |
|-------------------------------------------------------|---------|
| `deploy/energy-stack/scripts/parse_comed_bill.py`     | new     |
| `deploy/energy-stack/scripts/tests/`                  | new — fixtures from the 9 backfill bills |
| `deploy/energy-stack/scripts/requirements.txt`        | new — `pypdf`, `influxdb-client` |
| `~/energy-stack/inbox/comed/` (Pi-side runtime)       | new dir |
| `~/energy-stack/inbox/comed/processed/`               | new dir |
| `grafana/dashboards/comed-bill-reconciliation.json`   | new     |
| `PROJECT.md`                                          | append Phase 8 entry |
| `docs/SERVICES.md`                                    | append script entry |
