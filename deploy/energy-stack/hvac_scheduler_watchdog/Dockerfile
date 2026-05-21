FROM python:3.13-slim

WORKDIR /app
ENV PYTHONPATH=/app

RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt /app/hvac_scheduler_watchdog/
RUN pip install --no-cache-dir -r /app/hvac_scheduler_watchdog/requirements.txt

COPY . /app/hvac_scheduler_watchdog/

# UID 1008 -- one above the scheduler so the watchdog runs as a
# distinct service identity.
RUN useradd --system --no-create-home --uid 1008 watchdog && chown -R watchdog /app
USER watchdog

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "hvac_scheduler_watchdog.check"]
