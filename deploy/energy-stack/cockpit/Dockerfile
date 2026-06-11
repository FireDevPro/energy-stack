# Multi-stage: build the Vite frontend, then assemble the Python runtime
# that serves /api/* and the built UI same-origin on :8765.

FROM node:22-slim AS frontend-build

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim

WORKDIR /app
ENV PYTHONPATH=/app

RUN pip install --no-cache-dir --upgrade pip

COPY backend/requirements.txt /app/backend/
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# backend/tests rides along intentionally: snapshot.py imports the canned
# summer_normal fixture from .tests.fixtures at runtime.
COPY backend/ /app/backend/
COPY --from=frontend-build /build/dist /app/frontend/dist

RUN useradd --system --no-create-home --uid 1002 cockpit && chown -R cockpit /app
USER cockpit

ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8765"]
