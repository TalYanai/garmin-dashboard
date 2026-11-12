# Build context is the project root (set in docker-compose.yml).
# This lets us COPY garmin_analyzer.py and prompts/ from the root.

FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (cached layer unless requirements.txt changes)
COPY api/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY garmin_analyzer.py .
COPY prompts/ prompts/
COPY api/main.py .
COPY db/initial_load.py .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
