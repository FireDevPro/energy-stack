FROM python:3.13-slim

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY poller.py .

RUN useradd --system --no-create-home --uid 1002 poller && chown -R poller /app
USER poller

ENV PYTHONUNBUFFERED=1

CMD ["python", "poller.py"]
