FROM python:3.13-slim

WORKDIR /app
ENV PYTHONPATH=/app

RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt /app/purpleair_poller/
RUN pip install --no-cache-dir -r /app/purpleair_poller/requirements.txt

COPY . /app/purpleair_poller/

RUN useradd --system --no-create-home --uid 1012 poller && chown -R poller /app
USER poller

ENV PYTHONUNBUFFERED=1

# Polls every 15 min. Anything older than 40 min is unhealthy.
HEALTHCHECK --interval=300s --timeout=5s --start-period=120s --retries=2 \
  CMD test -n "$(find /tmp/last_poll_ok -mmin -40 2>/dev/null)" || exit 1

CMD ["python", "-m", "purpleair_poller.app"]
