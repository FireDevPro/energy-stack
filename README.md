---
date: 2026-06-20
owner: chris
status: active
role-label: chris
---

<p align="center">
  <img src="docs/assets/hero.jpg" alt="Stylized cutaway of a house with sensor inputs (smart meter, thermostat, AC condenser, weather, water heater, breaker panel) on the left flowing into a central 'energy-proxy' hub, which connects to five output panels on the right: optimized HVAC schedule, comfort guardrails, price-aware decisions, analytics and insights, and peak-risk avoidance." width="900">
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT"></a>
  <a href=".python-version"><img src="https://img.shields.io/badge/python-3.13-blue.svg" alt="Python 3.13"></a>
  <a href="https://github.com/FireDevPro/energy-stack/actions/workflows/typecheck.yml"><img src="https://img.shields.io/github/actions/workflow/status/FireDevPro/energy-stack/typecheck.yml?branch=main&label=type-check" alt="Type-check status"></a>
  <img src="https://img.shields.io/github/last-commit/FireDevPro/energy-stack" alt="Last commit">
</p>

# energy-proxy

Home energy monitoring and ComEd-RTP-aware HVAC cost optimization running as a
Docker Compose stack on Pi-lab. Single ComEd Hourly Pricing household; IECC climate
zone 5A; Amana CTK04AE thermostat on a 2-stage AC + modulating furnace.

## The controller

Two control approaches — retained for a 2027 cost-savings comparison:

- **Arm A** — the CTK04AE's internal programmed schedule runs autonomously.
- **Arm B** — Arm A **plus price awareness** (the rev 4.1 spike-only
  controller, **live in production since 2026-07-06**). At normal prices the
  thermostat runs its own program and the controller writes nothing. During
  ComEd RTP spikes it pushes timed warm holds anchored on a live read of the
  device's current program value (elevated = program + offset; scarcity = an
  absolute max-house temperature), and **actively releases** them once two
  consecutive price buckets confirm cheap. Warm-only; never cools below the
  device's program. Safety is device-owned: setpoint min/max limits are the
  hard cap, and the timed-hold TTL reverts the device to its own schedule if
  the controller dies — the safety net and the release path are independent
  mechanisms by design.

**2026 status:** built, validated, and running the season live. The Arm A vs
Arm B cost-savings comparison is the **2027 experiment** — deferred.

Controller design: [`docs/superpowers/specs/2026-06-20-commissioning-controller-design.md`](docs/superpowers/specs/2026-06-20-commissioning-controller-design.md) (rev 4.1, spike-only — live since 2026-07-06)
Implementation plan: [`docs/superpowers/plans/2026-07-05-spike-only-controller-plan.md`](docs/superpowers/plans/2026-07-05-spike-only-controller-plan.md)

## What's in this repo

- **Controller** — `hvac-scheduler` service. Current spec/plan linked above.
- **Arm apparatus** — CTK04AE program (Arm A) and price-overlay controller (Arm B),
  retained for the 2027 comparison.
- **Telemetry pipeline** — Docker Compose stack of pollers + InfluxDB + Grafana +
  Loki, under [`deploy/energy-stack/`](deploy/energy-stack/). Per-service detail in
  [`docs/SERVICES.md`](docs/SERVICES.md).
- **Analysis tooling** — shadow-validation ops check under
  [`tools/analysis/`](tools/analysis/) (`run_shadow_validation.py` + helpers).
- **5CP telemetry** — PJM capacity-peak data collected and retained for later
  analysis; not a live control input.

## Architecture

![Architecture: telemetry inputs (EAGLE-3, PJM Data Miner 2, NWS, Refoss EM16P, Ecowitt, thermostat-poller, ComfortNet) flow into InfluxDB 2.7. ComEd Hourly Pricing is the sole live control input, feeding the rev 4 spike-only hvac-scheduler, which leaves the CTK04AE thermostat running its own program at normal prices and pushes timed warm holds via the TCC cloud only during price spikes, actively released when prices confirm cheap. InfluxDB also feeds Grafana dashboards, Loki + Promtail logs, and the telegram-notifier (including the controller down-beacon alert written by the hvac-scheduler-watchdog). The thermostat drives a 2-stage Amana AC and modulating furnace.](docs/diagrams/architecture.png)

> Diagram source: [`docs/diagrams/architecture.mmd`](docs/diagrams/architecture.mmd) (Mermaid).
> Re-render with `npx -y @mermaid-js/mermaid-cli -i docs/diagrams/architecture.mmd -o docs/diagrams/architecture.png -t neutral -b white -w 1600 -H 1400` (SVG is also committed at the same path).

Stack runs as a single Docker Compose project. Deployment is automated: merging to
`main` triggers a GitHub Actions workflow that joins the operator's tailnet as an
ephemeral CI node and updates the stack in place. No public ingress.

## Reading path

1. [`docs/PROJECT.md`](docs/PROJECT.md) — project narrative, decisions, phase history, hardware inventory.
2. [`INDEX.md`](INDEX.md) — canonical document map (every active doc, grouped by intent).
3. [`docs/superpowers/specs/2026-06-20-commissioning-controller-design.md`](docs/superpowers/specs/2026-06-20-commissioning-controller-design.md) — current controller spec.
4. [`deploy/energy-stack/README.md`](deploy/energy-stack/README.md) — stack ops, ports, secrets, restore.

For AI agents: `AGENTS.md` (local, gitignored — not in the committed repo) is the standing behavior contract for this repo.
Legacy operator README archived at [`docs/archive/README-LEGACY.md`](docs/archive/README-LEGACY.md).

## License

[MIT](LICENSE)
