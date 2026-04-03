FROM python:3.10-slim AS base

WORKDIR /app

# System deps for python-can
RUN apt-get update && apt-get install -y --no-install-recommends \
    can-utils \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY src/ src/
COPY config/ config/
COPY data/ data/

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/system/health')" || exit 1

ENTRYPOINT ["can-hmi"]
CMD ["--config", "config/system.json"]
