# ---------- build stage: install dependencies into a virtualenv ----------
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt .
RUN pip install -r requirements.txt

# ---------- runtime stage: small image, non-root user ----------
FROM python:3.12-slim
LABEL org.opencontainers.image.title="EnergyWatch AI" \
      org.opencontainers.image.description="Multi-agent energy monitoring platform (LangGraph)" \
      org.opencontainers.image.version="1.2.0"
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    ENVIRONMENT=production
RUN useradd --create-home --uid 10001 energywatch
WORKDIR /srv
COPY --from=builder /opt/venv /opt/venv
COPY --chown=energywatch:energywatch app ./app
USER energywatch
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/health', timeout=4)" || exit 1
# Render injects $PORT; default 8000 locally. One worker: telemetry stores are in-memory.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
