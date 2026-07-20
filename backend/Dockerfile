# Build context is the REPO ROOT (see docker-compose.yml: context: .)
# so this Dockerfile can reach the root-level requirements.txt.
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
# The API imports the forecasting package and loads the per-region ARIMA models
# at request time (GET /forecast/*, POST /schedule), so both must be in the image.
COPY forecasting/ ./forecasting/
COPY models/arima/ ./models/arima/

EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
