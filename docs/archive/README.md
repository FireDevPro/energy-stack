# Archive

Historical design and planning documents for features that have shipped. Kept for context but **not maintained**. Active docs live at [`docs/`](../) and the linked external repos are the source of truth for current state.

| File | What it documents | Live reference |
|---|---|---|
| [`COMFORTNET_PIPELINE.md`](COMFORTNET_PIPELINE.md) | MQTT pipeline design (broker + telegraf + Pi-3B publisher) for ComfortNET HVAC bus telemetry | [`Promithius-DR/comfortnet`](https://github.com/Promithius-DR/comfortnet) repo (Python library + daemon, `docs/SETTING_REVIEW.md` for menu options); `docs/COMFORTNET_USE_CASES.md` for what we do with the data |
| [`phase-3.3-eagle-poller-design.md`](phase-3.3-eagle-poller-design.md) | EAGLE-3 poller design (Phase 3.3, shipped April 2026) | [`docs/SERVICES.md#eagle-poller`](../SERVICES.md#eagle-poller) for current behavior |
| [`phase-8-comed-bill-ingest-design.md`](phase-8-comed-bill-ingest-design.md) | ComEd bill ingest script design (Phase 8, shipped May 2026) | [`deploy/energy-stack/scripts/parse_comed_bill.py`](../../deploy/energy-stack/scripts/parse_comed_bill.py) and [`deploy/energy-stack/scripts/README.md`](../../deploy/energy-stack/scripts/README.md) |
| [`phase-8-comed-bill-ingest-plan.md`](phase-8-comed-bill-ingest-plan.md) | Implementation plan for the same | Same as above |
| [`session-3.1-influxdb-grafana.md`](session-3.1-influxdb-grafana.md) | Phase 3.1 planning prompt for InfluxDB + Grafana (shipped April 2026) | [`deploy/energy-stack/README.md`](../../deploy/energy-stack/README.md) |

If you find yourself updating a doc here, that's the signal it should move back to `docs/` as an active reference, not stay in archive.
