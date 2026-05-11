"""Hot+humid combined: does humidity on a hot day amplify price-spike risk?"""
import datetime
import json
import math
from collections import defaultdict
from statistics import mean, median
import os
from zoneinfo import ZoneInfo

CT = ZoneInfo("America/Chicago")
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def parse_price_file(path):
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


all_5min = []
for fname in ['may2025.txt', 'jun2025.txt', 'jul2025.txt', 'aug2025.txt', 'sep2025.txt']:
    all_5min.extend(parse_price_file(os.path.join(DATA_DIR, fname)))

hourly = defaultdict(list)
for ts, p in all_5min:
    hourly[ts.replace(minute=0, second=0, microsecond=0)].append(p)
hourly_avg = {k: mean(v) for k, v in hourly.items() if len(v) >= 6}

with open(os.path.join(DATA_DIR, 'weather2025.json')) as f:
    weather = json.load(f)
times = weather['hourly']['time']
temps = weather['hourly']['temperature_2m']
rh = weather['hourly']['relative_humidity_2m']
dewp = weather['hourly']['dew_point_2m']
weather_by_hour = {}
for i, t_str in enumerate(times):
    ts = datetime.datetime.fromisoformat(t_str).replace(tzinfo=CT)
    weather_by_hour[ts] = {'temp_f': temps[i], 'rh_pct': rh[i], 'dewp_f': dewp[i]}

# Daily aggregates
daily = defaultdict(lambda: {'prices': [], 'temps': [], 'dewps': [], 'rhs': []})
for ts, price in hourly_avg.items():
    if ts in weather_by_hour and 6 <= ts.month <= 9:
        d = ts.date()
        daily[d]['prices'].append(price)
        daily[d]['temps'].append(weather_by_hour[ts]['temp_f'])
        daily[d]['dewps'].append(weather_by_hour[ts]['dewp_f'])
        daily[d]['rhs'].append(weather_by_hour[ts]['rh_pct'])

# Split hot days by max dewpoint
print(f"=== Hot days (max temp >= 85F) split by max dewpoint ===")
print(f"  category               n   any_spike  multi_spike  scarcity   avg_max_price")
hot_days = [(d, v) for d, v in daily.items() if max(v['temps']) >= 85]
print(f"\n  Total hot days (>=85F max): {len(hot_days)}")

bins = [
    ("Hot + dry (max dewp <60F)",     lambda v: max(v['dewps']) < 60),
    ("Hot + moderate (60-67F dewp)",  lambda v: 60 <= max(v['dewps']) < 67),
    ("Hot + humid (67-72F dewp)",     lambda v: 67 <= max(v['dewps']) < 72),
    ("Hot + very humid (>=72F dewp)", lambda v: max(v['dewps']) >= 72),
]
for label, pred in bins:
    sub = [(d, v) for d, v in hot_days if pred(v)]
    if not sub:
        continue
    n = len(sub)
    any_s = sum(1 for _, v in sub if any(p >= 10 for p in v['prices']))
    mult_s = sum(1 for _, v in sub if sum(1 for p in v['prices'] if p >= 10) >= 3)
    scar = sum(1 for _, v in sub if any(p >= 20 for p in v['prices']))
    avg_max = mean([max(v['prices']) for _, v in sub])
    print(f"  {label:36s} {n:3d}   {any_s:3d} ({100*any_s/n:5.1f}%)  {mult_s:3d} ({100*mult_s/n:5.1f}%)   {scar:3d} ({100*scar/n:5.1f}%)  {avg_max:6.1f}c")

# Same split but using max heat index (temp + RH combined)
print(f"\n=== Hot days split by max apparent temperature (heat index) ===")
def heat_index(t_f, rh_pct):
    if t_f < 80:
        return t_f
    hi = (-42.379 + 2.04901523*t_f + 10.14333127*rh_pct
          - 0.22475541*t_f*rh_pct - 0.00683783*t_f*t_f
          - 0.05481717*rh_pct*rh_pct + 0.00122874*t_f*t_f*rh_pct
          + 0.00085282*t_f*rh_pct*rh_pct - 0.00000199*t_f*t_f*rh_pct*rh_pct)
    return hi

for d, v in hot_days:
    v['his'] = [heat_index(v['temps'][i], v['rhs'][i]) for i in range(len(v['temps']))]

print(f"  category                   n   any_spike  multi_spike  scarcity   avg_max_price")
hi_bins = [
    ("Hot, HI < 90F (no heat-index uplift)",  lambda v: max(v['his']) < 90),
    ("Hot, HI 90-95F",                         lambda v: 90 <= max(v['his']) < 95),
    ("Hot, HI 95-100F",                        lambda v: 95 <= max(v['his']) < 100),
    ("Hot, HI >=100F",                         lambda v: max(v['his']) >= 100),
]
for label, pred in hi_bins:
    sub = [(d, v) for d, v in hot_days if pred(v)]
    if not sub:
        continue
    n = len(sub)
    any_s = sum(1 for _, v in sub if any(p >= 10 for p in v['prices']))
    mult_s = sum(1 for _, v in sub if sum(1 for p in v['prices'] if p >= 10) >= 3)
    scar = sum(1 for _, v in sub if any(p >= 20 for p in v['prices']))
    avg_max = mean([max(v['prices']) for _, v in sub])
    print(f"  {label:40s} {n:3d}   {any_s:3d} ({100*any_s/n:5.1f}%)  {mult_s:3d} ({100*mult_s/n:5.1f}%)   {scar:3d} ({100*scar/n:5.1f}%)  {avg_max:6.1f}c")

# 92F with different humidity directly
print(f"\n=== Days at exactly 88-92F max temp split by humidity ===")
mid_hot = [(d, v) for d, v in daily.items() if 88 <= max(v['temps']) <= 92]
print(f"  Total 88-92F days: {len(mid_hot)}")
for label, pred in [
    ("Lower dewp (<65F)",  lambda v: max(v['dewps']) < 65),
    ("Mid dewp (65-72F)",  lambda v: 65 <= max(v['dewps']) < 72),
    ("High dewp (>=72F)",  lambda v: max(v['dewps']) >= 72),
]:
    sub = [(d, v) for d, v in mid_hot if pred(v)]
    if not sub:
        continue
    n = len(sub)
    any_s = sum(1 for _, v in sub if any(p >= 10 for p in v['prices']))
    scar = sum(1 for _, v in sub if any(p >= 20 for p in v['prices']))
    avg_max = mean([max(v['prices']) for _, v in sub])
    print(f"  {label:25s} {n:3d}   spike {any_s:2d} ({100*any_s/n:5.1f}%)   scarcity {scar:2d} ({100*scar/n:5.1f}%)   avg_max_price {avg_max:5.1f}c")

# Interaction: temp x humidity matrix
print(f"\n=== Spike rate by temp x dewpoint matrix (Jun-Sep 2025) ===")
print(f"                       low_dewp(<60F)   mid_dewp(60-67F)  high_dewp(67-72F)  vhi_dewp(>=72F)")
temp_bins = [(70, 80), (80, 85), (85, 90), (90, 100)]
dewp_bins = [(0, 60), (60, 67), (67, 72), (72, 100)]
for t_lo, t_hi in temp_bins:
    row = f"  Temp {t_lo}-{t_hi}F: "
    for d_lo, d_hi in dewp_bins:
        sub = [v for d, v in daily.items() if t_lo <= max(v['temps']) < t_hi and d_lo <= max(v['dewps']) < d_hi]
        if not sub:
            row += f"  {'n=0':>16s}"
            continue
        n = len(sub)
        any_s = sum(1 for v in sub if any(p >= 10 for p in v['prices']))
        row += f"  {any_s}/{n}={100*any_s/n:4.0f}%      "
    print(row)
