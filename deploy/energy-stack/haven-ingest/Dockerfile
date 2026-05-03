FROM python:3.13-slim

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# UID 1009 -- one above thermostat-poller
RUN useradd --system --no-create-home --uid 1009 havenpoll && mkdir -p /inbox/haven && chown -R havenpoll /app /inbox
USER havenpoll

ENV PYTHONUNBUFFERED=1

# Scans every 60s default. Anything older than 5 min is unhealthy.
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
  CMD test -n "$(find /tmp/last_scan_ok -mmin -5 2>/dev/null)" || exit 1

CMD ["python", "app.py"]
