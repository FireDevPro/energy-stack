FROM python:3.13-slim

WORKDIR /app
ENV PYTHONPATH=/app

RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt /app/eagle_poller/
RUN pip install --no-cache-dir -r /app/eagle_poller/requirements.txt

COPY . /app/eagle_poller/

RUN useradd --system --no-create-home --uid 1001 poller && chown -R poller /app
USER poller

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "eagle_poller.poller"]
