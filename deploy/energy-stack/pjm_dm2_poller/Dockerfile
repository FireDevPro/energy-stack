FROM python:3.13-slim

WORKDIR /app
ENV PYTHONPATH=/app

RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt /app/pjm_dm2_poller/
RUN pip install --no-cache-dir -r /app/pjm_dm2_poller/requirements.txt

COPY . /app/pjm_dm2_poller/

RUN useradd --system --no-create-home --uid 1010 poller && chown -R poller /app
USER poller

ENV PYTHONUNBUFFERED=1

# Loop liveness only — the marker is touched on every poll cycle (idle or
# active). 90-min staleness budget = "loop is wedged or crashed and needs
# a restart". The healthcheck deliberately does NOT track per-feed data
# freshness: restarting the container does not fix a PJM API outage,
# expired key, or schema drift. Per-feed health lives in the
# `pjm.feed_status` Influx measurement (one point per feed attempt) and
# is the right surface for telegram-notifier / Grafana deadman alerts.
# CodeX pass 2 (2026-05-07) caught an earlier attempt to gate this
# marker on feed success — the gating composed incorrectly across idle
# cycles AND mis-modeled what Docker HEALTHCHECK should mean operationally.
HEALTHCHECK --interval=600s --timeout=5s --start-period=120s --retries=2 \
  CMD test -n "$(find /tmp/last_poll_ok -mmin -90 2>/dev/null)" || exit 1

CMD ["python", "-m", "pjm_dm2_poller.app"]
