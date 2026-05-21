FROM python:3.13-slim

WORKDIR /app
ENV PYTHONPATH=/app

RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt /app/comed_poller/
RUN pip install --no-cache-dir -r /app/comed_poller/requirements.txt

COPY . /app/comed_poller/

RUN useradd --system --no-create-home --uid 1002 poller && chown -R poller /app
USER poller

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "comed_poller.poller"]
