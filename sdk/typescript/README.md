# Lore SDK

Native-fetch TypeScript client for the Lore REST API.

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
import { LoreClient } from "lore-sdk";

const client = new LoreClient({ baseUrl: "http://localhost:8078" });

console.log(await client.health());
const pages = await client.listPages({ kind: "project" });
const page = await client.getPage("projects/example-project");
const results = await client.search("ComfyUI gateway", { limit: 10 });
```

## Writes

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
import { LoreClient, LoreError } from "lore-sdk";

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
