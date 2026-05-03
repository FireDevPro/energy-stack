FROM python:3.13-slim

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

RUN useradd --system --no-create-home --uid 1008 notifier && chown -R notifier /app
USER notifier

ENV PYTHONUNBUFFERED=1

# Notifier alert-loop runs every 5 min by default. Healthy if last tick is recent.
HEALTHCHECK --interval=120s --timeout=5s --start-period=60s --retries=3 \
  CMD test -n "$(find /tmp/last_tick_ok -mmin -10 2>/dev/null)" || exit 1

CMD ["python", "app.py"]
