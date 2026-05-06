FROM python:3.13-slim

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

RUN useradd --system --no-create-home --uid 1010 poller && chown -R poller /app
USER poller

ENV PYTHONUNBUFFERED=1

# Polls hourly; fetchers fire only on their scheduled hours, so the marker
# file is touched every cycle regardless of whether HTTP calls were made.
# Anything older than 90 min means the loop stalled.
HEALTHCHECK --interval=600s --timeout=5s --start-period=120s --retries=2 \
  CMD test -n "$(find /tmp/last_poll_ok -mmin -90 2>/dev/null)" || exit 1

CMD ["python", "app.py"]
