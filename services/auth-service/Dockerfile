FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        default-libmysqlclient-dev \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

COPY services/auth-service/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


FROM python:3.11-slim AS final

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local
COPY services/shared/ ./shared/
COPY services/auth-service/ .

RUN mkdir -p /app/services && \
    ln -sfn /app/shared /app/services/shared && \
    touch /app/services/__init__.py && \
    chmod +x start.sh && \
    useradd -m -u 1000 appuser && \
    chown -R appuser /app

USER appuser

EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8090/health || exit 1

CMD ["./start.sh"]
