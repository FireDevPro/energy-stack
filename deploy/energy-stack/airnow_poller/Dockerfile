FROM python:3.13-slim

WORKDIR /app
ENV PYTHONPATH=/app

RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt /app/airnow_poller/
RUN pip install --no-cache-dir -r /app/airnow_poller/requirements.txt

COPY . /app/airnow_poller/

RUN useradd --system --no-create-home --uid 1013 poller && chown -R poller /app
USER poller

ENV PYTHONUNBUFFERED=1

# Polls every 30 min (AirNow updates hourly). Older than 75 min is unhealthy.
HEALTHCHECK --interval=300s --timeout=5s --start-period=120s --retries=2 \
  CMD test -n "$(find /tmp/last_poll_ok -mmin -75 2>/dev/null)" || exit 1

CMD ["python", "-m", "airnow_poller.app"]
