# Lore SDK

Native-fetch TypeScript client for Lore pages and durable memory.

## Installation

From this repository:

```sh
cd sdk/typescript
npm install
npm run build
```

The SDK has no runtime dependencies and uses the native `fetch` API.

## Basic Usage

```typescript
import { LoreClient } from "axis-lore-sdk";

const client = new LoreClient({ baseUrl: "http://localhost:8078" });

console.log(await client.health());
const pages = await client.listPages({ kind: "project" });
const page = await client.getPage("projects/example-project");
const results = await client.search("ComfyUI gateway", { limit: 10 });
```

## Durable Memory

`MemoryProvider` shares `LoreClient`'s configuration, bearer authentication,
timeouts, and `LoreError` behavior. Capture and recall are scoped by the actor
resolved from the bearer token; an admin must opt in with `crossActor: true` to
recall outside that scope.

```typescript
import { MemoryProvider } from "axis-lore-sdk";

const memory = new MemoryProvider({
  baseUrl: "https://lore.example",
  authToken: process.env.LORE_API_KEY,
});

const captureId = await memory.capture("Staging uses the blue deployment lane.", {
  agentName: "nyx",
  namespace: "notes",
  lane: "ops",
  taskId: "flow_000885",
  provenance: {
    source_paths: ["ops/deploy/Update-Server.sh"],
    evidence: "Verified in the deploy script.",
  },
  metadata: { confidence: "high" },
});

const recalled = await memory.recallResponse("staging deployment", { limit: 5 });
const usedIds = recalled.claims.map((claim) => claim.candidate_id);
if (usedIds.length > 0) {
  await memory.acknowledgeRecall(usedIds);
}
```

`recall()` returns just the typed claims array. `recallResponse()` also returns
the count, ranking weights, pending-capture count, and diagnostic hint. Authorized
admin callers can request an explicit cross-actor read with
`recallResponse(query, { crossActor: true })`.

## Writes

`LoreClient.createCapture(...)` targets the retained page-oriented draft
inbox/review workflow. Use `MemoryProvider.capture(...)` for new durable
agent-memory integrations.

```typescript
await client.upsertPage(
  "services/lore",
  `---
title: Lore
kind: service
visibility: internal
---

# Lore
`,
);

await client.createCapture({
  title: "Deployment note",
  body: "Observed a new deployment step.",
  source: "runbook.md",
  tags: ["deploy"],
});
```

## Embed Widget

`axis-lore-sdk/embed/lore-embed` is a separate browser entry point that mounts a
Lore page, search, or capture view into another HTML page via an iframe loaded
from the Lore server's `/embed` route.

```typescript
import { mountLoreEmbed } from "axis-lore-sdk/embed/lore-embed";

mountLoreEmbed({
  baseUrl: "https://lore.example",
  mode: "page",
  pageId: "projects/example-project",
  container: "#lore-embed",
});
```

See [`src/embed/README.md`](src/embed/README.md) for the drop-in `<script>` tag,
config options, and auth strategies.

## Auth

Pass a bearer token when your Lore server requires authentication:

```typescript
const client = new LoreClient({
  baseUrl: "http://localhost:8078",
  authToken: "your-token",
});
```

## Error Handling

Non-2xx responses throw `LoreError`.

```typescript
import { LoreClient, LoreError } from "axis-lore-sdk";

const client = new LoreClient({ baseUrl: "http://localhost:8078" });

try {
  await client.getPage("missing/page");
} catch (error) {
  if (error instanceof LoreError) {
    console.log(error.statusCode);
    console.log(error.message);
  }
}
```

## Timeout

`timeout` is optional and expressed in milliseconds. The default is 30000.
