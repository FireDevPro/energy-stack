FROM python:3.13-slim

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY poller.py .

RUN useradd --system --no-create-home --uid 1004 poller && chown -R poller /app
USER poller

ENV PYTHONUNBUFFERED=1

# Refoss polls every 30s. Anything older than 5 min is unhealthy.
HEALTHCHECK --interval=60s --timeout=5s --start-period=45s --retries=3 \
  CMD test -n "$(find /tmp/last_poll_ok -mmin -5 2>/dev/null)" || exit 1

CMD ["python", "poller.py"]
