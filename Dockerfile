# syntax=docker/dockerfile:1

# ---- Build wheels (for lxml etc.) ----
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       build-essential \
       libxml2-dev \
       libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip wheel --wheel-dir=/wheels -r requirements.txt

# ---- Runtime image ----
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
# Runtime libs only
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       libxml2 \
       libxslt1.1 \
       ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
# Install deps from prebuilt wheels
COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-index --find-links=/wheels -r requirements.txt
# App code
COPY misp_client.py cli.py README.md ./
# Non-root user
ARG UID=10001
ARG GID=10001
RUN groupadd -g ${GID} app \
    && useradd -m -u ${UID} -g ${GID} app
USER app
# Useful defaults; override at runtime
ENV MISP_BASE_URL="" \
    MISP_CERT_VALIDATION=true
ENTRYPOINT ["python", "/app/cli.py"]
CMD ["--help"]
