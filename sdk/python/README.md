# Lore SDK

Stdlib-only Python client for the Lore REST API.

## Installation

From this repository:

```sh
cd sdk/python
python -m pip install -e .
```

You can also copy the `lore_sdk` package into another Python 3.11+ project. The SDK has no third-party dependencies.

## Basic Usage

```python
from lore_sdk import LoreClient

client = LoreClient("http://localhost:8078")

print(client.health())
pages = client.list_pages(kind="project")
page = client.get_page("projects/example-project")
results = client.search("ComfyUI gateway", limit=10)
```

## Writes

```python
client.upsert_page(
    "services/lore",
    """---
title: Lore
kind: service
visibility: internal
---

# Lore
""",
)

client.create_capture(
    title="Deployment note",
    body="Observed a new deployment step.",
    source="runbook.md",
    tags=["deploy"],
)
```

## Auth

Pass a bearer token when your Lore server requires authentication:

```python
client = LoreClient("http://localhost:8078", auth_token="your-token")
```

## Error Handling

Non-2xx responses raise `LoreError`.

```python
from lore_sdk import LoreClient, LoreError

client = LoreClient("http://localhost:8078")

try:
    client.get_page("missing/page")
except LoreError as exc:
    print(exc.status_code)
    print(exc.message)
```
