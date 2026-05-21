---
title: Capture Workflow
kind: guide
visibility: public
summary: How agents capture, consolidate, and promote draft knowledge.
---
# Capture Workflow

Captures let agents store evidence-backed observations without immediately
changing canonical knowledge.

## Draft

```bash
curl -sS -X POST "$LORE_URL/api/capture" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Service Dashboard deploy note",
    "observation": "Service Dashboard depends on the server-side service registry.",
    "related_pages": ["services/service-dashboard"],
    "suggested_target_page": "services/service-dashboard",
    "confidence": "medium",
    "sources": ["sample-vault"]
  }'
```

Draft captures appear in `/captures` and `/api/captures?status=draft`.

## Audit

Move a capture into the focused audit lane when confidence, conflicts, or
sensitivity require extra scrutiny:

```bash
curl -sS -X POST "$LORE_URL/api/captures/inbox/2026-05-04/service-dashboard-deploy-note/status" \
  -H "Content-Type: application/json" \
  -d '{"status":"review"}'
```

## Promote

Promote after the observation is validated by an agent, automation, or explicit
operator action:

```bash
curl -sS -X POST "$LORE_URL/api/captures/inbox/2026-05-04/service-dashboard-deploy-note/promote" \
  -H "Content-Type: application/json" \
  -d '{"target_page_id":"services/service-dashboard"}'
```

The demo policy is documented in [[Agent Memory Decision|decisions/agent-memory-policy]].
