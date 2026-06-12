FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

COPY services/rule-engine-service/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt python-jose[cryptography]==3.3.0


FROM python:3.11-slim AS final

WORKDIR /app

COPY --from=builder /install /usr/local
COPY services/shared/ ./shared/
COPY services/rule-engine-service/ .

RUN mkdir -p /app/services && \
    ln -sfn /app/shared /app/services/shared && \
    touch /app/services/__init__.py && \
    chmod +x /app/start.sh && \
    useradd -m -u 1000 appuser && \
    chown -R appuser /app

USER appuser

EXPOSE 8002

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8002/health')"

CMD ["./start.sh"]
