# Image Docker optionnelle (Render « Docker » ou build local)
FROM python:3.12-slim

WORKDIR /app

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

COPY backend /app/backend
COPY frontend /app/frontend
RUN mkdir -p /app/data

WORKDIR /app/backend
ENV PYTHONUNBUFFERED=1

# Render injecte PORT au runtime
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-10000}"]
