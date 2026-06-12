FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        default-libmysqlclient-dev \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

COPY services/analytics-service/requirements.api.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.api.txt


FROM builder AS worker-builder

COPY services/analytics-service/requirements.worker.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.worker.txt


FROM python:3.11-slim AS api

WORKDIR /app

COPY --from=builder /install /usr/local
COPY services/shared/ ./shared/
COPY services/analytics-service/ .

RUN mkdir -p /app/services && \
    ln -sfn /app/shared /app/services/shared && \
    touch /app/services/__init__.py && \
    chmod +x /app/start.sh && \
    useradd -m -u 1000 appuser && \
    chown -R appuser /app

ENV APP_ROLE=api

USER appuser

EXPOSE 8003

CMD ["./start.sh"]


FROM python:3.11-slim AS worker

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=worker-builder /install /usr/local
COPY services/shared/ ./shared/
COPY services/analytics-service/ .

RUN mkdir -p /app/services && \
    ln -sfn /app/shared /app/services/shared && \
    touch /app/services/__init__.py && \
    chmod +x /app/start.sh && \
    useradd -m -u 1000 appuser && \
    chown -R appuser /app

ENV APP_ROLE=worker

USER appuser

EXPOSE 8003

CMD ["./start.sh"]
