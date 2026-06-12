FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        pkg-config \
        libcairo2-dev \
        libpango1.0-dev \
        libgdk-pixbuf-2.0-dev \
        libffi-dev \
        shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

COPY services/reporting-service/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt python-jose[cryptography]==3.3.0


FROM python:3.11-slim AS final

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libcairo2 \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libgobject-2.0-0 \
        libgdk-pixbuf-2.0-0 \
        shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local
COPY services/shared/ ./shared/
COPY services/reporting-service/ .

RUN mkdir -p /app/services && \
    ln -sfn /app/shared /app/services/shared && \
    touch /app/services/__init__.py && \
    chmod +x /app/start.sh && \
    useradd -m -u 1000 appuser && \
    chown -R appuser /app

ENV PYTHONPATH=/app

USER appuser

EXPOSE 8085

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8085/health')"

CMD ["./start.sh"]
