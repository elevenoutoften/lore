"""ASGI entry point for uvicorn. Keeps lore_app.main import-safe."""

from lore_app.main import create_app

app = create_app()
