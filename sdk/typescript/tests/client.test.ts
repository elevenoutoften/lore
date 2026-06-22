import assert from "node:assert/strict";
import test from "node:test";

import { LoreClient, LoreError, MemoryProvider } from "../src/index.js";

type FetchCall = {
  url: string;
  init: RequestInit;
};

const calls: FetchCall[] = [];

function mockFetch(status: number, payload: unknown, statusText = "OK"): void {
  globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    calls.push({ url: String(input), init: init ?? {} });
    const body = payload === undefined ? undefined : JSON.stringify(payload);
    return new Response(body, { status, statusText, headers: { "Content-Type": "application/json" } });
  };
}

test.beforeEach(() => {
  calls.length = 0;
});

test("client initialization", () => {
  const client = new LoreClient({ baseUrl: "https://lore.example.test/", authToken: "secret", timeout: 10 });

  assert.equal(client.baseUrl, "https://lore.example.test");
  assert.equal(client.authToken, "secret");
  assert.equal(client.timeout, 10);
});

test("GET request listPages", async () => {
  mockFetch(200, [{ id: "projects/example-project" }]);
  const client = new LoreClient({ baseUrl: "https://lore.example.test" });

  const result = await client.listPages({ kind: "project", tag: "gpu" });

  assert.deepEqual(result, [{ id: "projects/example-project" }]);
  assert.equal(calls[0].url, "https://lore.example.test/api/pages?tag=gpu&kind=project");
  assert.equal(calls[0].init.method, "GET");
});

test("POST request createCapture", async () => {
  mockFetch(200, { page: { id: "inbox/capture" } });
  const client = new LoreClient({ baseUrl: "https://lore.example.test" });

  const result = await client.createCapture({
    title: "GPU note",
    body: "Observed a deployment detail.",
    source: "README.md",
    tags: ["gpu"],
  });

  assert.equal((result as any).page.id, "inbox/capture");
  assert.equal(calls[0].url, "https://lore.example.test/api/capture");
  assert.equal(calls[0].init.method, "POST");
  assert.equal((calls[0].init.headers as Record<string, string>)["Content-Type"], "application/json");
  assert.deepEqual(JSON.parse(calls[0].init.body as string), {
    title: "GPU note",
    body: "Observed a deployment detail.",
    source: "README.md",
    tags: ["gpu"],
    observation: "Observed a deployment detail.",
    sources: ["README.md"],
  });
});

test("MemoryProvider capture uses canonical endpoint and shared auth transport", async () => {
  mockFetch(201, { capture_id: "notes/nyx/2026-06-22/deployment-note", timestamp: "2026-06-22T00:00:00Z" });
  const memory = new MemoryProvider({ baseUrl: "https://lore.example.test", authToken: "nyx-token" });

  const captureId = await memory.capture("Deployment uses the blue lane.", {
    agentName: "nyx",
    namespace: "notes",
    lane: "ops",
    taskId: "flow_000885",
    metadata: { title: "Deployment note", confidence: "high" },
  });

  assert.equal(captureId, "notes/nyx/2026-06-22/deployment-note");
  assert.equal(calls[0].url, "https://lore.example.test/api/memory/capture");
  assert.equal(calls[0].init.method, "POST");
  assert.equal((calls[0].init.headers as Record<string, string>).Authorization, "Bearer nyx-token");
  assert.deepEqual(JSON.parse(calls[0].init.body as string), {
    text: "Deployment uses the blue lane.",
    agent_name: "nyx",
    namespace: "notes",
    metadata: { title: "Deployment note", confidence: "high" },
    lane: "ops",
    task_id: "flow_000885",
  });
});

test("MemoryProvider recall mirrors Python options and returns typed claims", async () => {
  mockFetch(200, {
    query: "deployment",
    count: 1,
    latency_ms: 1.2,
    weights: { strength: 0.45 },
    claims: [{ candidate_id: "c1", subject: "services/lore", recall_score: 0.9 }],
    pending_captures: 0,
    hint: null,
  });
  const memory = new MemoryProvider({ baseUrl: "https://lore.example.test" });

  const claims = await memory.recall("deployment", {
    subject: "services/lore",
    lane: "ops",
    actor: "nyx",
    minStrength: 0.25,
    limit: 7,
    recordAccess: true,
    crossActor: true,
  });

  assert.equal(claims[0].candidate_id, "c1");
  assert.equal(
    calls[0].url,
    "https://lore.example.test/api/memory/recall?query=deployment&subject=services%2Flore&lane=ops&actor=nyx&min_strength=0.25&limit=7&record_access=true&cross_actor=true",
  );
  assert.equal(calls[0].init.method, "GET");
});

test("MemoryProvider recallResponse preserves the diagnostic envelope", async () => {
  mockFetch(200, {
    query: "pending",
    count: 0,
    latency_ms: 0.5,
    weights: {},
    claims: [],
    pending_captures: 1,
    hint: "Capture is awaiting consolidation.",
  });
  const memory = new MemoryProvider({ baseUrl: "https://lore.example.test" });

  const response = await memory.recallResponse("pending");

  assert.equal(response.count, 0);
  assert.equal(response.pending_captures, 1);
  assert.match(response.hint ?? "", /consolidation/i);
  assert.equal(
    calls[0].url,
    "https://lore.example.test/api/memory/recall?query=pending&limit=20&record_access=false",
  );
});

test("MemoryProvider acknowledgeRecall supports explicit authorized cross-actor scope", async () => {
  mockFetch(200, { acknowledged_count: 2, timestamp: "2026-06-22T00:00:00Z" });
  const memory = new MemoryProvider({ baseUrl: "https://lore.example.test", authToken: "admin-token" });

  const response = await memory.acknowledgeRecall(["c1", "c2"], { actor: "nyx", crossActor: true });

  assert.equal(response.acknowledged_count, 2);
  assert.equal(calls[0].url, "https://lore.example.test/api/memory/recall/ack");
  assert.equal((calls[0].init.headers as Record<string, string>).Authorization, "Bearer admin-token");
  assert.deepEqual(JSON.parse(calls[0].init.body as string), {
    candidate_ids: ["c1", "c2"],
    actor: "nyx",
    cross_actor: true,
  });
});

test("PUT request upsertPage", async () => {
  mockFetch(200, { id: "services/lore" });
  const client = new LoreClient({ baseUrl: "https://lore.example.test" });

  const result = await client.upsertPage("services/lore", "# Lore", { commitMessage: "Update lore" });

  assert.equal((result as any).id, "services/lore");
  assert.equal(calls[0].url, "https://lore.example.test/api/pages/services/lore");
  assert.equal(calls[0].init.method, "PUT");
  assert.deepEqual(JSON.parse(calls[0].init.body as string), { content: "# Lore", commit_message: "Update lore" });
});

test("DELETE request deletePage", async () => {
  mockFetch(204, undefined, "No Content");
  const client = new LoreClient({ baseUrl: "https://lore.example.test" });

  const result = await client.deletePage("services/lore");

  assert.deepEqual(result, {});
  assert.equal(calls[0].url, "https://lore.example.test/api/pages/services/lore");
  assert.equal(calls[0].init.method, "DELETE");
});

test("404 error handling", async () => {
  mockFetch(404, { detail: "Lore page not found." }, "Not Found");
  const client = new LoreClient({ baseUrl: "https://lore.example.test" });

  await assert.rejects(() => client.getPage("missing"), (error) => {
    assert.ok(error instanceof LoreError);
    assert.equal(error.statusCode, 404);
    assert.equal(error.message, "Lore API error 404: Lore page not found.");
    return true;
  });
});

test("500 error handling", async () => {
  globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    calls.push({ url: String(input), init: init ?? {} });
    return new Response("server exploded", { status: 500, statusText: "Internal Server Error" });
  };
  const client = new LoreClient({ baseUrl: "https://lore.example.test" });

  await assert.rejects(() => client.lint(), (error) => {
    assert.ok(error instanceof LoreError);
    assert.equal(error.statusCode, 500);
    assert.equal(error.message, "Lore API error 500: server exploded");
    return true;
  });
});

test("auth header inclusion", async () => {
  mockFetch(200, { ok: true });
  const client = new LoreClient({ baseUrl: "https://lore.example.test", authToken: "token-123" });

  await client.health();

  assert.equal((calls[0].init.headers as Record<string, string>).Authorization, "Bearer token-123");
});

test("timeout via AbortController", async () => {
  globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> =>
    new Promise((_resolve, reject) => {
      calls.push({ url: String(input), init: init ?? {} });
      init?.signal?.addEventListener("abort", () => reject(new DOMException("The operation was aborted.", "AbortError")));
    });
  const client = new LoreClient({ baseUrl: "https://lore.example.test", timeout: 1 });

  await assert.rejects(() => client.health(), (error) => {
    assert.ok(error instanceof LoreError);
    assert.equal(error.statusCode, 0);
    assert.match(error.message, /aborted/i);
    return true;
  });
});
