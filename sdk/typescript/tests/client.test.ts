import assert from "node:assert/strict";
import test from "node:test";

import { LoreClient, LoreError } from "../src/index.js";

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
