# Build context must be pipeline/ (set in docker-compose.yml)
FROM python:3.12-slim

WORKDIR /api

# Install dependencies
COPY L7_api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy API source
COPY L7_api/app/ ./app/

# Copy pre-computed L6 events (auto-ingested on first startup)
COPY L6_emit/output/ ./L6_emit/output/

EXPOSE 8000

CMD ["uvicorn", "main:app", "--app-dir", "app", "--host", "0.0.0.0", "--port", "8000"]
