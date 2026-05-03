FROM python:3.13-slim

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

RUN useradd --system --no-create-home --uid 1006 poller && chown -R poller /app
USER poller

ENV PYTHONUNBUFFERED=1

# NWS polls every 30 min. Anything older than 60 min is unhealthy.
HEALTHCHECK --interval=300s --timeout=5s --start-period=120s --retries=2 \
  CMD test -n "$(find /tmp/last_poll_ok -mmin -60 2>/dev/null)" || exit 1

CMD ["python", "app.py"]
