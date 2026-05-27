from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture()
def content_dir(tmp_path: Path) -> Path:
    root = tmp_path / "pages"
    root.mkdir()
    (root / "projects").mkdir()
    (root / "services").mkdir()
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
    return root
