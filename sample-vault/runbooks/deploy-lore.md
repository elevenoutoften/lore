---
title: Deploy Lore Runbook
kind: procedure
visibility: public
summary: Demo procedure for deploying and checking a Lore instance.
trigger: Lore service deployment or restore.
preconditions:
  - Content directory is backed up.
  - Environment variables are known.
postconditions:
  - Health check returns ok.
  - Search index is rebuilt.
---
# Deploy Lore Runbook

## Steps

1. Back up the content directory.
2. Deploy the application package or container.
3. Start the service with the configured content and database paths.
4. Run `curl "$LORE_URL/healthz"`.
5. Run `curl -X POST "$LORE_URL/api/search/reindex"`.
6. Check [[Search|guides/search]] and [[API Usage|guides/api-usage]] examples.

## Related

- [[Architecture Overview|architecture/overview]]
- [[API Gateway|services/api-gateway]]
- [[Service Dashboard|services/service-dashboard]]
