#!/bin/bash
# Live unmonitored load per service leg, from the Refoss EM16P, for a breaker-by-breaker test.
# Refreshes every 30 s (the poll interval); Ctrl-C to stop. `--once` prints one reading.
#
#   Leg A unmonitored = em:1 (mains leg A) - em:2..6 - em:13, em:15..18
#   Leg B unmonitored = em:7 (mains leg B) - em:8..12 - em:14
# em:14 (Basement Micro/Fridge) is in the leg-A-referenced bank C but its breaker is on leg B.
# Same arithmetic as loads.PHYSICAL_LEG. Every reading is appended to ./breaker_test_<date>.csv
# so breaker flips can be matched to timestamps afterwards.
# Procedure and the record sheet: docs/THERMAL_MODEL_2026-11.md, "Unmonitored load".
set -euo pipefail
PI="${PI_SSH:-chris@192.168.20.10}"
LOG="breaker_test_$(date +%Y%m%d).csv"
[ -f "$LOG" ] || echo "time,mains_A_W,mains_B_W,unmon_A_W,unmon_B_W,unmon_total_W" > "$LOG"
Q='from(bucket:"energy") |> range(start: -2m) |> filter(fn:(r)=> r._measurement=="refoss.channel" and r._field=="power_w") |> last() |> keep(columns:["channel","_value"])'
echo "logging to $PWD/$LOG"
while true; do
  echo "$Q" | ssh -o BatchMode=yes "$PI" 'docker exec -i influxdb influx query --raw -f /dev/stdin' | tr -d '\r' \
    | awk -F, -v t="$(date +%H:%M:%S)" -v out="$LOG" '
        $5 ~ /^em:[0-9]+$/ { n = substr($5, 4) + 0; p[n] = $4 }
        END {
          ua = p[1]; for (i = 2; i <= 6; i++) ua -= p[i]; for (i = 13; i <= 18; i++) if (i != 14) ua -= p[i]
          ub = p[7]; for (i = 8; i <= 12; i++) ub -= p[i]; ub -= p[14]
          printf "%s  mains A %5.0f W  B %5.0f W  |  UNMONITORED  leg A %5.0f W   leg B %5.0f W   total %5.0f W\n", t, p[1], p[7], ua, ub, ua + ub
          printf "%s,%.0f,%.0f,%.0f,%.0f,%.0f\n", t, p[1], p[7], ua, ub, ua + ub >> out
        }'
  [ "${1:-}" = "--once" ] && break
  sleep 30
done
