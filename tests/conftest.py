from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from lore_app.main import create_app


@pytest.fixture()
def content_dir(tmp_path):
    root = tmp_path / "pages"
    root.mkdir()
    (root / "projects").mkdir()
    (root / "services").mkdir()
    (root / "procedures").mkdir()
    (root / "projects" / "example-project.md").write_text(
        """---
title: ExampleProject
kind: project
visibility: internal
status: active
summary: GPU computing service for agents and applications.
tags: [gpu, cloud]
sources:
  - README.md
---

# ExampleProject

ExampleProject runs compute, workflow, and knowledge services.

## Services

See [Workflow Engine](../services/workflow-engine), [[Workflow Engine|services/workflow-engine]], and [Missing](../services/missing).

| Service | Purpose |
| --- | --- |
| Workflow Engine | Agent-first task board |
| Lore | Durable project memory |

<script>alert("nope")</script>
""",
        encoding="utf-8",
    )
    (root / "services" / "workflow-engine.md").write_text(
        """---
title: Workflow Engine
kind: service
visibility: internal
status: active
---

# Workflow Engine

Agent-first task board and API. See [ExampleProject](../projects/example-project).
""",
        encoding="utf-8",
    )
    (root / "procedures" / "deploy-lore-service.md").write_text(
        """---
title: Deploy Lore service
kind: procedure
visibility: internal
summary: Deploy Lore service changes.
trigger: Lore code changes merged to main on ExampleProject
preconditions:
  - SSH access to the server is available
  - Main branch is clean
steps:
  - SSH to the server
  - Pull the latest main branch
  - Build the Lore Docker image
  - Restart the Lore systemd service
  - Run the Lore smoke test
postconditions:
  - Lore service is running the latest main branch
error_handling: Check journalctl for errors.
sources:
  - internal-procedures.md
status: accepted
---

# Deploy Lore service

## Trigger
Lore code changes merged to main on ExampleProject.

## Preconditions
- SSH access to the server is available
- Main branch is clean

## Steps

1. SSH to the server.
2. Pull the latest main branch.
3. Build the Lore Docker image.
4. Restart the Lore systemd service.
5. Run the Lore smoke test.

## Error Handling
Check journalctl for errors.
""",
        encoding="utf-8",
    )
    (root / "procedures" / "add-lore-seed-page.md").write_text(
        """---
title: Add a new Lore seed page
kind: procedure
visibility: internal
summary: Add canonical seed documentation for a new service, project, or concept.
trigger: A new service, project, or concept needs documenting
preconditions:
  - Lore content directory is accessible
steps:
  - Create a Markdown file with frontmatter
  - Validate the page with lint
  - Commit the new seed page
postconditions:
  - New seed page exists in Lore content
error_handling: Fix lint errors before committing.
sources:
  - internal-procedures.md
status: accepted
---

# Add a new Lore seed page

## Trigger
A new service, project, or concept needs documenting.

## Preconditions
- Lore content directory is accessible

## Steps

1. Create a Markdown file with frontmatter.
2. Validate the page with lint.
3. Commit the new seed page.

## Error Handling
Fix lint errors before committing.
""",
        encoding="utf-8",
    )
    (root / "procedures" / "create-lore-capture.md").write_text(
        """---
title: Capture agent observation
kind: procedure
visibility: internal
summary: Capture a noteworthy agent observation for autonomous consolidation.
trigger: Agent observes something worth recording
preconditions:
  - Lore service is running
steps:
  - POST to the capture API
  - Inspect the capture in the capture queue
  - Promote or incorporate the capture into a canonical page when evidence is sufficient
postconditions:
  - Observation is preserved or promoted in Lore
error_handling: Retry the capture request and check Lore service logs.
sources:
  - internal-procedures.md
status: accepted
---

# Capture agent observation

## Trigger
Agent observes something worth recording.

## Preconditions
- Lore service is running

## Steps

1. POST to the capture API.
2. Review the capture in the capture dashboard.
3. Promote the capture to a canonical page.

## Error Handling
Retry the capture request and check Lore service logs.
""",
        encoding="utf-8",
    )
    return root


@pytest.fixture()
def search_db(tmp_path):
    return tmp_path / "search.db"


@pytest.fixture()
def client(content_dir, search_db):
    from lore_app.config import LoreConfig

    lore_config = LoreConfig()
    lore_config.content_dir = content_dir
    lore_config.search_db = search_db
    lore_config.vector_db = search_db.with_name("vectors.db")
    lore_config.ledger_db = search_db.with_name("ledger.db")
    lore_config.api_keys_db = search_db.with_name("api_keys.db")
    lore_config.trusted_headers = True
    app = create_app(lore_config)
    with TestClient(app) as test_client:
        yield test_client
