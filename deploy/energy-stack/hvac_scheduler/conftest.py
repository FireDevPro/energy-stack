"""Pytest config for hvac-scheduler tests.

app.py validates SCHEDULER_MODE at module-import time and sys.exit(2)s
on missing/invalid (per spec §3, fail-closed). Tests `import app` at
module scope, so the env var must be set BEFORE pytest collects tests.

Default = `production`: most permissive. Existing tests that pass
``dry_run=True``/``False`` directly to ``execute_action`` are unblocked
by the SCHEDULER_MODE gate (production mode lets writes through to the
existing dry_run check). Mode-specific tests override via
``monkeypatch.setenv``.
"""
import os

os.environ.setdefault("SCHEDULER_MODE", "production")
