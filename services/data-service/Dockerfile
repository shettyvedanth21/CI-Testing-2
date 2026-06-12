FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

COPY services/data-service/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt python-jose[cryptography]==3.3.0


FROM python:3.11-slim AS final

WORKDIR /app

COPY --from=builder /install /usr/local
COPY services/shared/ ./shared/
COPY services/data-service/ .

RUN mkdir -p /app/services && \
    ln -sfn /app/shared /app/services/shared && \
    touch /app/services/__init__.py && \
    useradd -m -u 1000 appuser && \
    chown -R appuser /app

ENV PYTHONPATH=/app

USER appuser

EXPOSE 8081

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8081/health')"

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8081"]
