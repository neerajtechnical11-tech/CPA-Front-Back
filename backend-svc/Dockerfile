# Backend container: FastAPI + all backend logic (pipeline, agents, evals, auth).
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# system deps sometimes needed by pypdf/lxml wheels
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# code only - settings.env is MOUNTED at runtime (see docker-compose / k8s Secret)
COPY api.py .
COPY backend ./backend
COPY ingestion ./ingestion

EXPOSE 8000
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
