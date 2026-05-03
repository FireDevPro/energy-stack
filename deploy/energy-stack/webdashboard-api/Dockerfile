FROM python:3.13-slim

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

RUN useradd --system --no-create-home --uid 1005 api && chown -R api /app
USER api

ENV PYTHONUNBUFFERED=1

EXPOSE 8082

# HTTP-based healthcheck against the /api/health endpoint.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request,sys; r=urllib.request.urlopen('http://localhost:8082/api/health', timeout=3); sys.exit(0 if r.status==200 else 1)" || exit 1

CMD ["python", "app.py"]
