FROM python:3.13-slim

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# UID 1009 -- one above thermostat-poller
RUN useradd --system --no-create-home --uid 1009 havenpoll
USER havenpoll

ENV PYTHONUNBUFFERED=1

# Poll writes /tmp/last_poll_ok each cycle. Anything older than poll_interval + 60s is unhealthy.
HEALTHCHECK --interval=60s --timeout=5s --start-period=60s --retries=3 \
  CMD test -n "$(find /tmp/last_poll_ok -mmin -7 2>/dev/null)" || exit 1

CMD ["python", "app.py"]
