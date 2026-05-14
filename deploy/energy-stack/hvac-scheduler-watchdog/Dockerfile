FROM python:3.13-slim

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY check.py .

# UID 1008 -- one above the scheduler so the watchdog runs as a
# distinct service identity.
RUN useradd --system --no-create-home --uid 1008 watchdog && chown -R watchdog /app
USER watchdog

ENV PYTHONUNBUFFERED=1

CMD ["python", "check.py"]
