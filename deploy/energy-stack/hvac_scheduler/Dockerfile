FROM python:3.13-slim

WORKDIR /app

ENV PYTHONPATH=/app

RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt /app/hvac_scheduler/
RUN pip install --no-cache-dir -r /app/hvac_scheduler/requirements.txt

COPY . /app/hvac_scheduler/

# UID 1007 -- one above the other pollers
RUN useradd --system --no-create-home --uid 1007 scheduler && mkdir -p /data && chown -R scheduler /app /data
USER scheduler

ENV PYTHONUNBUFFERED=1

# Scheduler ticks every 60s. Anything older than 5 min is unhealthy.
HEALTHCHECK --interval=60s --timeout=5s --start-period=60s --retries=3 \
  CMD test -n "$(find /tmp/last_tick_ok -mmin -5 2>/dev/null)" || exit 1

CMD ["python", "-m", "hvac_scheduler.app"]
