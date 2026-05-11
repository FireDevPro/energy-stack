"""Analyze ComEd 2025 cooling-season RTP price distributions to inform Arm B thresholds."""
import datetime
from collections import defaultdict
from statistics import mean, median, quantiles
import os
from zoneinfo import ZoneInfo

CT = ZoneInfo("America/Chicago")
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

def parse_file(path):
    """Parse ComEd text format: millis:price,millis:price,..."""
    with open(path) as f:
        data = f.read().strip()
    records = []
    for pair in data.split(','):
        if ':' not in pair:
            continue
        millis, price = pair.split(':')
        ts = datetime.datetime.fromtimestamp(int(millis) / 1000, tz=datetime.timezone.utc).astimezone(CT)
        records.append((ts, float(price)))
    return records

# Load all months
all_5min = []
for fname in ['may2025.txt', 'jun2025.txt', 'jul2025.txt', 'aug2025.txt', 'sep2025.txt']:
    path = os.path.join(DATA_DIR, fname)
    if os.path.exists(path):
        recs = parse_file(path)
        all_5min.extend(recs)
        print(f"{fname}: {len(recs)} 5-min records, {recs[-1][0].date()} to {recs[0][0].date()}")

print(f"\nTotal 5-min records: {len(all_5min)}")

# Aggregate to hourly averages.
#
# Inclusion rule: keep an hour if >=6 of 12 5-minute prints are present
# (the same threshold used by the locked analysis pipeline at runtime;
# see EXPERIMENT_DESIGN.md §4 Rule 3). NOT the strict ComEd billing
# rule (which requires the full 12 prints).
#
# Why the looser rule for threshold derivation: a partial-print hour
# is still a real hour the household experienced; tightening to 12/12
# only changes May-Sep P95 by 0.02 c/kWh (9.53 -> 9.55) and leaves
# P99, max, and the 17-scarcity-day count exact. The bundled 2025
# data has 3,556 hours with all 12 prints, 107 hours with 6-11 prints,
# and 7 hours below 6 prints (excluded). See check_partial_hours.py
# for the side-by-side comparison.
hourly = defaultdict(list)
for ts, price in all_5min:
    hour_key = ts.replace(minute=0, second=0, microsecond=0)
    hourly[hour_key].append(price)

hourly_avg = {k: mean(v) for k, v in hourly.items() if len(v) >= 6}
hourly_records = sorted(hourly_avg.items())
prices = [p for _, p in hourly_records]

print(f"Total hourly records: {len(hourly_records)}")
print(f"Date range: {hourly_records[0][0].date()} to {hourly_records[-1][0].date()}")

print("\n=== Overall hourly price distribution (May-Sep 2025) ===")
print(f"  Min:    {min(prices):.2f}")
print(f"  P5:     {quantiles(prices, n=20)[0]:.2f}")
print(f"  P25:    {quantiles(prices, n=4)[0]:.2f}")
print(f"  P50:    {median(prices):.2f}")
print(f"  Mean:   {mean(prices):.2f}")
print(f"  P75:    {quantiles(prices, n=4)[2]:.2f}")
print(f"  P90:    {quantiles(prices, n=10)[8]:.2f}")
print(f"  P95:    {quantiles(prices, n=20)[18]:.2f}")
print(f"  P99:    {quantiles(prices, n=100)[98]:.2f}")
print(f"  Max:    {max(prices):.2f}")

print("\n=== Hours above thresholds across all 5 months ===")
total = len(prices)
for threshold in [5, 8, 10, 12, 15, 20, 25, 30, 50, 100]:
    count = sum(1 for p in prices if p >= threshold)
    pct = 100 * count / total
    print(f"  >= {threshold:3d} c/kWh: {count:5d} hours ({pct:5.2f}%)")

print("\n=== Top 20 hours by hourly average price ===")
sorted_by_price = sorted(hourly_records, key=lambda x: -x[1])
for i, (ts, p) in enumerate(sorted_by_price[:20]):
    print(f"  {i+1:2d}. {ts.strftime('%Y-%m-%d %a %H:%M CT')}  {p:6.2f}c/kWh")

print("\n=== Mean hourly price by hour-of-day (Jun-Sep 2025 summer) ===")
hour_buckets = defaultdict(list)
for ts, p in hourly_records:
    if ts.month >= 6 and ts.month <= 9:
        hour_buckets[ts.hour].append(p)
for h in sorted(hour_buckets.keys()):
    vals = hour_buckets[h]
    print(f"  {h:02d}:00 CT: mean={mean(vals):5.2f}  p50={median(vals):5.2f}  p95={quantiles(vals, n=20)[18]:5.2f}  max={max(vals):6.2f}  n={len(vals)}")

print("\n=== Hours above thresholds by hour-of-day (Jun-Sep 2025 summer) ===")
print("  hour: >=10c   >=20c   >=50c")
for h in sorted(hour_buckets.keys()):
    vals = hour_buckets[h]
    above_10 = sum(1 for p in vals if p >= 10)
    above_20 = sum(1 for p in vals if p >= 20)
    above_50 = sum(1 for p in vals if p >= 50)
    pct_10 = 100 * above_10 / len(vals)
    print(f"  {h:02d}:00: {above_10:3d}h ({pct_10:5.1f}%)  {above_20:3d}h    {above_50:3d}h")

print("\n=== Day-of-week patterns (Jun-Sep 2025 summer) ===")
dow_buckets = defaultdict(list)
dow_names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
for ts, p in hourly_records:
    if ts.month >= 6 and ts.month <= 9:
        dow_buckets[ts.weekday()].append(p)
for d in sorted(dow_buckets.keys()):
    vals = dow_buckets[d]
    above_10 = sum(1 for p in vals if p >= 10)
    above_20 = sum(1 for p in vals if p >= 20)
    print(f"  {dow_names[d]}: mean={mean(vals):5.2f}  p95={quantiles(vals, n=20)[18]:5.2f}  >=10c {above_10:3d}h  >=20c {above_20:2d}h")

print("\n=== Scarcity event days (any hour >= 25 c/kWh, Jun-Sep) ===")
scarcity_days = defaultdict(list)
for ts, p in hourly_records:
    if ts.month >= 6 and ts.month <= 9 and p >= 25:
        scarcity_days[ts.date()].append((ts, p))
for date in sorted(scarcity_days.keys()):
    events = scarcity_days[date]
    max_price = max(p for _, p in events)
    duration = len(events)
    hours = sorted(set(t.hour for t, _ in events))
    print(f"  {date} ({duration}h scarcity, max {max_price:.1f}c, hours: {hours})")

print("\n=== Current 14-17 CT shutoff window analysis (Jun-Sep 2025) ===")
in_window = [p for ts, p in hourly_records if 14 <= ts.hour <= 17 and ts.month >= 6 and ts.month <= 9]
out_window = [p for ts, p in hourly_records if (ts.hour < 14 or ts.hour > 17) and ts.month >= 6 and ts.month <= 9]
print(f"  In-window (14-17 CT):  mean={mean(in_window):5.2f}  p95={quantiles(in_window, n=20)[18]:5.2f}  max={max(in_window):.2f}")
print(f"  Out-of-window:          mean={mean(out_window):5.2f}  p95={quantiles(out_window, n=20)[18]:5.2f}  max={max(out_window):.2f}")
top_out = sorted([(ts, p) for ts, p in hourly_records if (ts.hour < 14 or ts.hour > 17) and ts.month >= 6 and ts.month <= 9], key=lambda x: -x[1])[:10]
print(f"  Top 10 high-price hours OUTSIDE 14-17 CT window:")
for ts, p in top_out:
    print(f"    {ts.strftime('%Y-%m-%d %a %H:%M')}  {p:.2f}c/kWh")

print("\n=== Negative pricing hours (any hour < 0 c/kWh) ===")
neg = [(ts, p) for ts, p in hourly_records if p < 0]
print(f"  Total: {len(neg)} hours below 0 c/kWh ({100*len(neg)/len(prices):.2f}% of all hours)")
if neg:
    print(f"  Min price: {min(p for _, p in neg):.2f} c/kWh")
    by_hour = defaultdict(int)
    for ts, _ in neg:
        by_hour[ts.hour] += 1
    print(f"  Top hours-of-day with negative pricing:")
    for h, count in sorted(by_hour.items(), key=lambda x: -x[1])[:5]:
        print(f"    {h:02d}:00 CT: {count} hours")
