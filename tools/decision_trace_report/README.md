<!-- tools/decision_trace_report/README.md -->
# Decision-trace commissioning report tool

Daily + on-demand markdown report rendering `decision_trace.*` events
from Loki and `hvac.*` measurements from InfluxDB. Runs on the Windows
workstation, queries Pi-lab over the LAN.

See full design spec: `docs/superpowers/specs/2026-05-15-decision-trace-report-tool-design.md`

## Quick start

1. Install dependencies (one-time):
   ```
   pip install -r tools/decision_trace_report/requirements.txt
   ```

2. Set required environment variables. Either via shell env, your
   Windows Task Scheduler entry, or `--env-file PATH`. See
   `.env.example` in this directory for the full list.

3. Render yesterday's CT day (default):
   ```
   python -m tools.decision_trace_report
   ```

4. Render a specific past day:
   ```
   python -m tools.decision_trace_report --date 2026-05-14
   ```

5. Suppress Telegram heartbeat (e.g., ad-hoc reruns):
   ```
   python -m tools.decision_trace_report --date 2026-05-14 --no-telegram
   ```

## Output

By default writes to:
`D:\Projects\energy-proxy\docs\test-reports\YYYY-MM-DD-decision-trace.md`

That directory is gitignored — reports are transient artifacts.

## Daily automation

Schedule a Windows Task Scheduler entry to run the tool at 08:00 CT
every day. Trigger: 08:00 daily. Action: `python -m tools.decision_trace_report`
with working directory `D:\Projects\energy-proxy`.

A run on 2026-05-16 at 08:00 CT renders `2026-05-15-decision-trace.md`.

## Tests

```
python -m pytest tools/decision_trace_report/tests/
```

No live HTTP. All Loki + InfluxDB + Telegram calls are mocked.

## Daily automation — Windows Task Scheduler

Goal: render yesterday's report every morning at 08:00 CT without manual
invocation.

1. Open Task Scheduler (Win+R → `taskschd.msc`).
2. Create Task → name: `decision-trace-report-daily`.
3. Trigger: Daily at 08:00. Synchronize across time zones: off.
4. Action: Start a program.
   - Program: `python.exe` (or the full path to your venv's python)
   - Arguments: `-m tools.decision_trace_report`
   - Start in: `D:\Projects\energy-proxy`
5. Conditions: uncheck "Start the task only if the computer is on AC power"
   if you want it to run on battery (desktop = AC always).
6. Settings: tick "Run task as soon as possible after a scheduled start
   is missed" so a brief reboot at 08:00 doesn't lose that day's run.
7. Environment variables: set `LOKI_URL`, `INFLUXDB_URL`, `INFLUXDB_TOKEN`,
   `INFLUXDB_ORG`, `INFLUXDB_BUCKET`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
   either as system env vars OR put them in a `.env` file and use
   `python -m tools.decision_trace_report --env-file C:\path\to\.env`
   as the action's arguments instead.

To verify: right-click the task → Run. Wait ~30s. Check
`D:\Projects\energy-proxy\docs\test-reports\` for the new file.
