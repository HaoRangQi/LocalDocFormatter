FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DOCFORMAT_HOST=0.0.0.0 \
    DOCFORMAT_PORT=8765 \
    DOCFORMAT_NO_BROWSER=1 \
    DOCFORMAT_CONTAINER=1 \
    DOCFORMAT_WORKSPACE_ROOTS=/workspace \
    DOCFORMAT_AI_CONFIG_PATH=/data/ai-config.json

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libreoffice \
        libreoffice-writer \
        libreoffice-calc \
        libreoffice-impress \
        fonts-noto-cjk \
        fonts-liberation \
        fontconfig \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY docformat ./docformat
COPY README.md ./README.md

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data /workspace \
    && chown -R appuser:appuser /app /data /workspace

USER appuser
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import json, urllib.request; json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=3))" || exit 1

CMD ["python", "-m", "docformat"]
