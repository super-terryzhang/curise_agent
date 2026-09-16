FROM python:3.11-slim

WORKDIR /app

# System deps (minimal — Phase 2+ will add more for PDF/Excel processing)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Source first — `pip install -e .` needs apps/ + domains/ to be present.
# This sacrifices a bit of layer caching but is correct.
COPY . .

# Python deps
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -e .

# Run
ENV PORT=8080 ENV=production
EXPOSE 8080
# Run `alembic upgrade head` once as a controlled release step before traffic.
# Never race migrations across autoscaling workers or an old serving revision.
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
