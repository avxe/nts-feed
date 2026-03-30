# =============================================================================
# Multi-stage Dockerfile for NTS Feed
# =============================================================================
# Runtime model:
# - Builder stage installs Python dependencies once
# - Runtime stage keeps only app code and runtime system packages
# - No vector/ML dependencies are installed

FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir gunicorn~=23.0.0

FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY . .

COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

RUN groupadd -r appuser && useradd -r -g appuser -d /app appuser \
    && mkdir -p /app/.cache/yt-dlp \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 5555

ENTRYPOINT ["/docker-entrypoint.sh"]
