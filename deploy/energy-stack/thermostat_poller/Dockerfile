FROM python:3.13-slim

WORKDIR /app
ENV PYTHONPATH=/app

RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt /app/thermostat_poller/
RUN pip install --no-cache-dir -r /app/thermostat_poller/requirements.txt

COPY . /app/thermostat_poller/

# UID 1008 -- one above hvac-scheduler
RUN useradd --system --no-create-home --uid 1008 tstatpoll && mkdir -p /data && chown -R tstatpoll /app /data
USER tstatpoll

ENV PYTHONUNBUFFERED=1

# Polls every 600s default. Anything older than 15 min is unhealthy.
HEALTHCHECK --interval=120s --timeout=5s --start-period=60s --retries=3 \
  CMD test -n "$(find /tmp/last_poll_ok -mmin -15 2>/dev/null)" || exit 1

CMD ["python", "-m", "thermostat_poller.poller"]
