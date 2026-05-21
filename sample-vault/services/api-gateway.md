---
title: API Gateway
kind: service
visibility: public
summary: Example service page for GPU model runtime routing.
tags:
  - gpu
  - runtime
source_paths:
  - src/gateway/app.py
---
# API Gateway

The model gateway is an example service page for routing requests to local GPU
model runtimes. It demonstrates how Lore pages document ownership, source
references, and operational links.

## Responsibilities

- Accept model runtime requests.
- Normalize health and inference responses.
- Provide a stable target for apps and agents.

## Related Pages

- [[Architecture Overview|architecture/overview]]
- [[API Usage|guides/api-usage]]
- [[Search|guides/search]]
- [[Deploy Lore Runbook|runbooks/deploy-lore]]
