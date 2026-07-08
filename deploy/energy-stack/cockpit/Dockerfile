# The Vigil cockpit: a FastAPI backend that serves three /api/vigil/* polls
# and the single-file frontend same-origin on :8765. No build step — the board
# is one static HTML file (no npm/Vite), which is what a kiosk wants.

FROM python:3.13-slim

WORKDIR /app
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir --upgrade pip

COPY backend/requirements.txt /app/backend/
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

COPY backend/ /app/backend/
COPY frontend/index.html /app/frontend/index.html

RUN useradd --system --no-create-home --uid 1002 cockpit && chown -R cockpit /app
USER cockpit

CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8765"]
