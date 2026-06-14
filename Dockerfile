FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LORE_CONTENT_DIR=/data/pages \
    LORE_SEARCH_DB=/data/db/search.db \
    LORE_VECTOR_DB=/data/db/vectors.db \
    LORE_LEDGER_DB=/data/db/ledger.db \
    LORE_SETTINGS_DB=/data/db/settings.db \
    LORE_API_KEYS_DB=/data/db/api-keys.db \
    LORE_AUTH_MODE=api_key \
    LORE_HOST=0.0.0.0 \
    LORE_PORT=8000

WORKDIR /app

COPY pyproject.toml README.md ./
COPY lore_app ./lore_app

RUN pip install --no-cache-dir . && \
    groupadd -r lore && useradd -r -g lore -d /data -s /sbin/nologin lore && \
    mkdir -p /data/pages /data/db && chown -R lore:lore /data

VOLUME ["/data/pages", "/data/db"]

USER lore

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "lore_app.asgi:app", "--host", "0.0.0.0", "--port", "8000"]
