"""Verify the Appendix A coverage claims:
- ~52% of spike days (>=10c) are temperature-correlated (max >=85F OR apparent >=90F)
- ~65% of scarcity days (>=20c) are temperature-correlated (same predicate)
- 8 of 17 scarcity days had max temp <87F
- 23.8% of 18:00 CT hours were >=10c
"""
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
weather_by_hour = {}
for i, t_str in enumerate(weather['hourly']['time']):
    ts = datetime.datetime.fromisoformat(t_str).replace(tzinfo=CT)
    weather_by_hour[ts] = {
        'temp_f': weather['hourly']['temperature_2m'][i],
        'app_f': weather['hourly']['apparent_temperature'][i],
    }

daily = defaultdict(lambda: {'prices': [], 'temps': [], 'apps': []})
for ts, price in hourly_avg.items():
    if ts in weather_by_hour and 6 <= ts.month <= 9:
        d = ts.date()
        daily[d]['prices'].append(price)
        daily[d]['temps'].append(weather_by_hour[ts]['temp_f'])
        daily[d]['apps'].append(weather_by_hour[ts]['app_f'])

spike_days = [(d, v) for d, v in daily.items() if any(p >= 10 for p in v['prices'])]
scarcity_days = [(d, v) for d, v in daily.items() if any(p >= 20 for p in v['prices'])]


def temp_correlated(v):
    return max(v['temps']) >= 85 or max(v['apps']) >= 90


print(f"=== Appendix A coverage claims (max temp >=85F OR max apparent >=90F) ===")
spike_correlated = sum(1 for _, v in spike_days if temp_correlated(v))
scar_correlated = sum(1 for _, v in scarcity_days if temp_correlated(v))
print(f"  Spike days (>=10c):     {spike_correlated} of {len(spike_days)} = {100*spike_correlated/len(spike_days):.1f}% are temperature-correlated")
print(f"  Scarcity days (>=20c):  {scar_correlated} of {len(scarcity_days)} = {100*scar_correlated/len(scarcity_days):.1f}% are temperature-correlated")
print(f"  Grid-event-driven spike days: {len(spike_days)-spike_correlated} of {len(spike_days)} = {100*(len(spike_days)-spike_correlated)/len(spike_days):.1f}%")
print(f"  Grid-event-driven scarcity days: {len(scarcity_days)-scar_correlated} of {len(scarcity_days)} = {100*(len(scarcity_days)-scar_correlated)/len(scarcity_days):.1f}%")

print(f"\n=== Cross-check: scarcity days max temp <87F ===")
under_87 = sum(1 for _, v in scarcity_days if max(v['temps']) < 87)
print(f"  {under_87} of {len(scarcity_days)} = {100*under_87/len(scarcity_days):.1f}%")

print(f"\n=== Cross-check: 18:00 CT hours >= 10c ===")
hr_18 = [p for ts, p in hourly_avg.items() if ts.month >= 6 and ts.month <= 9 and ts.hour == 18]
above_10 = sum(1 for p in hr_18 if p >= 10)
print(f"  {above_10} of {len(hr_18)} = {100*above_10/len(hr_18):.1f}%")
