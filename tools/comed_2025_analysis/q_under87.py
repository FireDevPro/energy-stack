"""How many spike days had max temp under 87F."""
import datetime
import json
from collections import defaultdict
from statistics import mean
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
apps = weather['hourly']['apparent_temperature']
weather_by_hour = {}
for i, t_str in enumerate(times):
    ts = datetime.datetime.fromisoformat(t_str).replace(tzinfo=CT)
    weather_by_hour[ts] = {'temp_f': temps[i], 'app_f': apps[i]}

daily = defaultdict(lambda: {'prices': [], 'temps': [], 'apps': []})
for ts, price in hourly_avg.items():
    if ts in weather_by_hour and 6 <= ts.month <= 9:
        d = ts.date()
        daily[d]['prices'].append(price)
        daily[d]['temps'].append(weather_by_hour[ts]['temp_f'])
        daily[d]['apps'].append(weather_by_hour[ts]['app_f'])

# Total summer days
total_days = len(daily)
days_with_spike = [d for d, v in daily.items() if any(p >= 10 for p in v['prices'])]
days_with_scarcity = [d for d, v in daily.items() if any(p >= 20 for p in v['prices'])]
print(f"Total summer days (Jun-Sep 2025): {total_days}")
print(f"Days with any spike (>=10c):      {len(days_with_spike)}")
print(f"Days with scarcity event (>=20c): {len(days_with_scarcity)}")

print(f"\n=== Spike days (>=10c) by max-temp threshold ===")
for thr in [82, 85, 87, 90, 92, 95]:
    count = sum(1 for d in days_with_spike if max(daily[d]['temps']) < thr)
    pct = 100 * count / len(days_with_spike) if days_with_spike else 0
    print(f"  Max temp < {thr}F: {count:3d} of {len(days_with_spike)} spike days ({pct:.1f}%)")

print(f"\n=== Scarcity days (>=20c) by max-temp threshold ===")
for thr in [82, 85, 87, 90, 92, 95]:
    count = sum(1 for d in days_with_scarcity if max(daily[d]['temps']) < thr)
    pct = 100 * count / len(days_with_scarcity) if days_with_scarcity else 0
    print(f"  Max temp < {thr}F: {count:3d} of {len(days_with_scarcity)} scarcity days ({pct:.1f}%)")

print(f"\n=== Spike days with max temp < 87F (full list) ===")
under_87_spike = [(d, daily[d]) for d in days_with_spike if max(daily[d]['temps']) < 87]
under_87_spike.sort(key=lambda x: -max(x[1]['prices']))
for d, v in under_87_spike:
    n_spike = sum(1 for p in v['prices'] if p >= 10)
    n_scarcity = sum(1 for p in v['prices'] if p >= 20)
    print(f"  {d} {d.strftime('%a')}: max_temp={max(v['temps']):.0f}F  max_app={max(v['apps']):.0f}F  max_price={max(v['prices']):.1f}c  spikes={n_spike}h  scarcity={n_scarcity}h")

print(f"\n=== Apparent-temp threshold check (since heat index matters) ===")
print(f"=== Spike days by max apparent-temp threshold ===")
for thr in [82, 85, 87, 90, 92, 95, 100]:
    count = sum(1 for d in days_with_spike if max(daily[d]['apps']) < thr)
    pct = 100 * count / len(days_with_spike)
    print(f"  Max apparent < {thr}F: {count:3d} of {len(days_with_spike)} spike days ({pct:.1f}%)")
