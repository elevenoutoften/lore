FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LORE_CONTENT_DIR=/data/pages \
    LORE_SEARCH_DB=/data/db/search.db \
    LORE_VECTOR_DB=/data/db/vectors.db \
    LORE_HOST=0.0.0.0 \
    LORE_PORT=8000

WORKDIR /app

COPY pyproject.toml README.md ./
COPY lore_app ./lore_app

RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "lore_app.main:app", "--host", "0.0.0.0", "--port", "8000"]
