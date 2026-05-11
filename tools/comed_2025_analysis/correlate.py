"""Correlate ComEd 2025 RTP prices against Plainfield IL weather observations."""
import datetime
import json
from collections import defaultdict
from statistics import mean, median, quantiles, pstdev
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


# Load and aggregate prices to hourly
all_5min = []
for fname in ['may2025.txt', 'jun2025.txt', 'jul2025.txt', 'aug2025.txt', 'sep2025.txt']:
    path = os.path.join(DATA_DIR, fname)
    if os.path.exists(path):
        all_5min.extend(parse_price_file(path))

hourly_price = defaultdict(list)
for ts, price in all_5min:
    hour_key = ts.replace(minute=0, second=0, microsecond=0)
    hourly_price[hour_key].append(price)
hourly_price_avg = {k: mean(v) for k, v in hourly_price.items() if len(v) >= 6}

# Load weather
with open(os.path.join(DATA_DIR, 'weather2025.json')) as f:
    weather = json.load(f)

times = weather['hourly']['time']
temps = weather['hourly']['temperature_2m']
rh = weather['hourly']['relative_humidity_2m']
dewpoint = weather['hourly']['dew_point_2m']
wind = weather['hourly']['wind_speed_10m']
solar = weather['hourly']['shortwave_radiation']
apparent = weather['hourly']['apparent_temperature']

weather_by_hour = {}
for i, t_str in enumerate(times):
    ts = datetime.datetime.fromisoformat(t_str).replace(tzinfo=CT)
    weather_by_hour[ts] = {
        'temp_f': temps[i],
        'rh_pct': rh[i],
        'dewpoint_f': dewpoint[i],
        'wind_mph': wind[i],
        'solar_wm2': solar[i],
        'apparent_f': apparent[i],
    }

# Join price and weather
joined = []
for ts, price in hourly_price_avg.items():
    if ts in weather_by_hour:
        joined.append({
            'ts': ts,
            'price': price,
            **weather_by_hour[ts]
        })

print(f"Joined records: {len(joined)} hours (May-Sep 2025)")


def correlation(xs, ys):
    """Pearson correlation."""
    n = len(xs)
    mx, my = mean(xs), mean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / n
    sx, sy = pstdev(xs), pstdev(ys)
    return cov / (sx * sy) if sx and sy else 0


def spearman(xs, ys):
    """Spearman rank correlation - robust to outliers/non-linearity."""
    def rank(vals):
        sorted_indices = sorted(range(len(vals)), key=lambda i: vals[i])
        ranks = [0] * len(vals)
        for r, i in enumerate(sorted_indices):
            ranks[i] = r + 1
        return ranks
    rx = rank(xs)
    ry = rank(ys)
    return correlation(rx, ry)


# Summer-only subset (Jun-Sep)
summer = [r for r in joined if 6 <= r['ts'].month <= 9]

# Overall correlations
print(f"\n=== Pearson and Spearman correlations: hourly price vs weather variables (Jun-Sep 2025) ===")
prices = [r['price'] for r in summer]
for var in ['temp_f', 'apparent_f', 'dewpoint_f', 'rh_pct', 'wind_mph', 'solar_wm2']:
    xs = [r[var] for r in summer]
    p = correlation(xs, prices)
    s = spearman(xs, prices)
    print(f"  {var:12s}: Pearson r = {p:+.3f}   Spearman rho = {s:+.3f}")

# Subset to "spike" hours: above 10c and 20c
print(f"\n=== Weather conditions during price tiers (Jun-Sep 2025) ===")
tiers = [
    ('Normal (<10c)', lambda r: r['price'] < 10),
    ('Elevated (10-20c)', lambda r: 10 <= r['price'] < 20),
    ('Scarcity (>=20c)', lambda r: r['price'] >= 20),
]
print(f"  {'Tier':22s}  n      mean_temp  p95_temp  mean_dewp  mean_app   mean_solar")
for label, predicate in tiers:
    subset = [r for r in summer if predicate(r)]
    if not subset:
        continue
    print(f"  {label:22s}  {len(subset):5d}  "
          f"{mean(r['temp_f'] for r in subset):8.1f}  "
          f"{quantiles([r['temp_f'] for r in subset], n=20)[18]:7.1f}  "
          f"{mean(r['dewpoint_f'] for r in subset):8.1f}  "
          f"{mean(r['apparent_f'] for r in subset):7.1f}  "
          f"{mean(r['solar_wm2'] for r in subset):9.1f}")

# Daily aggregates: does daily max temp predict whether the day has any price spike?
print(f"\n=== Daily max temp vs daily price spike count (Jun-Sep 2025) ===")
daily = defaultdict(lambda: {'prices': [], 'temps': [], 'dewps': [], 'apps': []})
for r in summer:
    d = r['ts'].date()
    daily[d]['prices'].append(r['price'])
    daily[d]['temps'].append(r['temp_f'])
    daily[d]['dewps'].append(r['dewpoint_f'])
    daily[d]['apps'].append(r['apparent_f'])

print(f"  Daily max temp bin    n_days   any_spike(>=10c)  mult_spikes(>=3h>=10c)  scarcity(>=20c)  spike_count_avg")
bins = [(60, 70), (70, 80), (80, 85), (85, 90), (90, 95), (95, 105)]
for lo, hi in bins:
    days_in_bin = [d for d, v in daily.items() if lo <= max(v['temps']) < hi]
    if not days_in_bin:
        continue
    n = len(days_in_bin)
    any_10 = sum(1 for d in days_in_bin if any(p >= 10 for p in daily[d]['prices']))
    mult_10 = sum(1 for d in days_in_bin if sum(1 for p in daily[d]['prices'] if p >= 10) >= 3)
    any_20 = sum(1 for d in days_in_bin if any(p >= 20 for p in daily[d]['prices']))
    avg_spikes = mean([sum(1 for p in daily[d]['prices'] if p >= 10) for d in days_in_bin])
    print(f"  {lo}-{hi}F                 {n:3d}    {any_10:3d} ({100*any_10/n:5.1f}%)      {mult_10:3d} ({100*mult_10/n:5.1f}%)         {any_20:3d} ({100*any_20/n:5.1f}%)        {avg_spikes:.1f}")

# Day-by-day high temp vs whether it had a 5CP-grade scarcity event
print(f"\n=== Days with scarcity events (>=20c) vs daily weather (Jun-Sep) ===")
scarcity_days = []
non_scarcity_days = []
for d, v in daily.items():
    is_scarcity = any(p >= 20 for p in v['prices'])
    record = {
        'date': d,
        'max_temp': max(v['temps']),
        'min_temp': min(v['temps']),
        'mean_temp': mean(v['temps']),
        'max_dewp': max(v['dewps']),
        'mean_dewp': mean(v['dewps']),
        'max_app': max(v['apps']),
        'max_price': max(v['prices']),
        'spike_count': sum(1 for p in v['prices'] if p >= 10),
    }
    if is_scarcity:
        scarcity_days.append(record)
    else:
        non_scarcity_days.append(record)

print(f"\n  Scarcity days (n={len(scarcity_days)}):")
print(f"    max_temp: mean={mean(r['max_temp'] for r in scarcity_days):.1f}F  range {min(r['max_temp'] for r in scarcity_days):.0f}-{max(r['max_temp'] for r in scarcity_days):.0f}F")
print(f"    max_dewp: mean={mean(r['max_dewp'] for r in scarcity_days):.1f}F  range {min(r['max_dewp'] for r in scarcity_days):.0f}-{max(r['max_dewp'] for r in scarcity_days):.0f}F")
print(f"    max_app:  mean={mean(r['max_app'] for r in scarcity_days):.1f}F")
print(f"\n  Non-scarcity days (n={len(non_scarcity_days)}):")
print(f"    max_temp: mean={mean(r['max_temp'] for r in non_scarcity_days):.1f}F  range {min(r['max_temp'] for r in non_scarcity_days):.0f}-{max(r['max_temp'] for r in non_scarcity_days):.0f}F")
print(f"    max_dewp: mean={mean(r['max_dewp'] for r in non_scarcity_days):.1f}F  range {min(r['max_dewp'] for r in non_scarcity_days):.0f}-{max(r['max_dewp'] for r in non_scarcity_days):.0f}F")

print(f"\n  Individual scarcity days:")
for r in sorted(scarcity_days, key=lambda r: -r['max_price']):
    dow = r['date'].strftime('%a')
    print(f"    {r['date']} {dow}  max_temp={r['max_temp']:.0f}F  max_dewp={r['max_dewp']:.0f}F  max_app={r['max_app']:.0f}F  max_price={r['max_price']:.1f}c  spikes={r['spike_count']}h")

# False negative analysis: hot days (>=90F) that did NOT have a spike
print(f"\n=== Hot days (>=90F max) that did NOT have any price spike (>=10c) ===")
hot_no_spike = [r for r in non_scarcity_days + scarcity_days if r['max_temp'] >= 90 and r['spike_count'] == 0]
print(f"  Count: {len(hot_no_spike)}")
for r in sorted(hot_no_spike, key=lambda r: -r['max_temp'])[:10]:
    print(f"    {r['date']} {r['date'].strftime('%a')}  max_temp={r['max_temp']:.0f}F  max_dewp={r['max_dewp']:.0f}F  max_price={r['max_price']:.1f}c")

# False positive analysis: mild days (<85F) that had a spike
print(f"\n=== Mild days (<85F max) that DID have a price spike (>=10c) ===")
mild_spike = [r for r in non_scarcity_days + scarcity_days if r['max_temp'] < 85 and r['spike_count'] > 0]
print(f"  Count: {len(mild_spike)}")
for r in sorted(mild_spike, key=lambda r: -r['max_price'])[:10]:
    print(f"    {r['date']} {r['date'].strftime('%a')}  max_temp={r['max_temp']:.0f}F  max_dewp={r['max_dewp']:.0f}F  max_price={r['max_price']:.1f}c  spikes={r['spike_count']}h")
