FROM python:3.13-slim

WORKDIR /app
ENV PYTHONPATH=/app

RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt /app/ecowitt_ingest/
RUN pip install --no-cache-dir -r /app/ecowitt_ingest/requirements.txt

COPY . /app/ecowitt_ingest/

RUN useradd --system --no-create-home --uid 1010 ecowitt
USER ecowitt

ENV PYTHONUNBUFFERED=1

EXPOSE 8088

# Receiver touches /tmp/last_push_ok on every accepted push. The GW1200 pushes
# every 60s by default, so anything older than ~7 min means upstream is dead
# (lost LAN, gateway reboot loop, etc.).
HEALTHCHECK --interval=60s --timeout=5s --start-period=120s --retries=3 \
  CMD test -n "$(find /tmp/last_push_ok -mmin -7 2>/dev/null)" || exit 1

CMD ["python", "-m", "ecowitt_ingest.app"]
