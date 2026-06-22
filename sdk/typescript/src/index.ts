import { LoreError } from "./error.js";

export { LoreError } from "./error.js";

export type JsonObject = Record<string, any>;
export type JsonResponse = JsonObject | any[];

export interface LoreClientOptions {
  baseUrl: string;
  authToken?: string;
  timeout?: number;
}

export type MemoryNamespace = "inbox" | "notes";
export type MemoryLane = "project" | "procedural" | "ops" | "companion" | "draft";

export interface ProvenanceRef {
  page_ids?: string[];
  capture_ids?: string[];
  trace_ids?: string[];
  policy_ids?: string[];
  candidate_ids?: string[];
  task_ids?: string[];
  sources?: string[];
  source_paths?: string[];
  source_urls?: string[];
  evidence?: string;
  source_task?: string;
  tool_calls?: JsonObject[];
  constraints?: string[];
}

export interface MemoryCaptureOptions {
  agentName?: string;
  namespace?: MemoryNamespace;
  metadata?: JsonObject;
  lane?: MemoryLane;
  actor?: string;
  taskId?: string;
  decisionId?: string;
  traceId?: string;
  toolCalls?: JsonObject[];
  constraints?: string[];
  policiesApplied?: string[];
  provenance?: ProvenanceRef;
}

export interface MemoryCaptureResponse {
  capture_id: string;
  timestamp: string;
}

export interface MemoryRecallOptions {
  subject?: string;
  lane?: MemoryLane;
  actor?: string;
  minStrength?: number;
  limit?: number;
  recordAccess?: boolean;
  /** Admin-only: explicitly recall outside the authenticated actor scope. */
  crossActor?: boolean;
}

export interface MemoryRecallClaim {
  candidate_id: string;
  subject: string;
  predicate: string;
  object: string;
  status: string;
  confidence: string | null;
  actor: string | null;
  lane: string | null;
  strength: number;
  access_count: number;
  age_days: number;
  recall_score: number;
  recall_signals: Record<string, number>;
  source_page_ids: string[];
  source_capture_ids: string[];
  valid_from: string | null;
  valid_until: string | null;
  updated_at: string | null;
}

export interface MemoryRecallResponse {
  query: string | null;
  count: number;
  latency_ms: number;
  weights: Record<string, number>;
  claims: MemoryRecallClaim[];
  pending_captures: number;
  hint: string | null;
}

export interface MemoryRecallAckOptions {
  /** Admin-only actor target; requires crossActor. */
  actor?: string;
  /** Admin-only: acknowledge claims outside the authenticated actor scope. */
  crossActor?: boolean;
}

export interface MemoryRecallAckResponse {
  acknowledged_count: number;
  timestamp: string;
}

type QueryParams = Record<string, string | number | boolean | string[] | number[] | undefined | null>;
type RequestBody = JsonObject | any[];

export class LoreClient {
  readonly baseUrl: string;
  readonly authToken?: string;
  readonly timeout: number;

  constructor(options: LoreClientOptions) {
    this.baseUrl = options.baseUrl.replace(/\/+$/, "");
    this.authToken = options.authToken;
    this.timeout = options.timeout ?? 30_000;
  }

  health(): Promise<JsonResponse> {
    return this.request("GET", "/healthz");
  }

  version(): Promise<JsonResponse> {
    return this.request("GET", "/api/version");
  }

  config(): Promise<JsonResponse> {
    return this.request("GET", "/api/config");
  }

  listPages(params: { category?: string; tag?: string; kind?: string; visibility?: string; query?: string; limit?: number } = {}): Promise<JsonResponse> {
    return this.request("GET", "/api/pages", {
      params: {
        category: params.category,
        tag: params.tag,
        kind: params.kind,
        visibility: params.visibility,
        q: params.query,
        limit: params.limit,
      },
    });
  }

  getPage(pageId: string): Promise<JsonResponse> {
    return this.request("GET", `/api/pages/${this.path(pageId)}`);
  }

  upsertPage(pageId: string, content: string, options: { commitMessage?: string } = {}): Promise<JsonResponse> {
    return this.request("PUT", `/api/pages/${this.path(pageId)}`, {
      data: this.withoutUndefined({
        content,
        commit_message: options.commitMessage,
      }),
    });
  }

  deletePage(pageId: string): Promise<JsonResponse> {
    return this.request("DELETE", `/api/pages/${this.path(pageId)}`);
  }

  getRenderedPage(pageId: string): Promise<JsonResponse> {
    return this.request("GET", `/api/pages/${this.path(pageId)}/rendered`);
  }

  getPageLinks(pageId: string): Promise<JsonResponse> {
    return this.request("GET", `/api/pages/${this.path(pageId)}/links`);
  }

  updateMetadata(pageId: string, metadata: Record<string, any>): Promise<JsonResponse> {
    return this.request("PATCH", `/api/pages/${this.path(pageId)}/metadata`, { data: metadata });
  }

  createStub(pageId: string, options: { title?: string } = {}): Promise<JsonResponse> {
    return this.request("POST", `/api/pages/${this.path(pageId)}/stub`, {
      data: this.withoutUndefined({ title: options.title }),
    });
  }

  search(query: string, options: { limit?: number } = {}): Promise<JsonResponse> {
    return this.request("GET", "/api/search", { params: { q: query, limit: options.limit ?? 20 } });
  }

  searchFts(query: string, options: { limit?: number } = {}): Promise<JsonResponse> {
    return this.request("GET", "/api/search/fts", { params: { q: query, limit: options.limit ?? 20 } });
  }

  searchBm25(query: string, options: { limit?: number } = {}): Promise<JsonResponse> {
    return this.request("GET", "/api/search/bm25", { params: { q: query, limit: options.limit ?? 20 } });
  }

  reindex(): Promise<JsonResponse> {
    return this.request("POST", "/api/search/reindex");
  }

  getLinks(): Promise<JsonResponse> {
    return this.request("GET", "/api/links");
  }

  getGraphStats(): Promise<JsonResponse> {
    return this.request("GET", "/api/graph/stats");
  }

  getEnrichedGraph(): Promise<JsonResponse> {
    return this.request("GET", "/api/graph/enriched");
  }

  getSourceEdges(): Promise<JsonResponse> {
    return this.request("GET", "/api/graph/sources");
  }

  lint(): Promise<JsonResponse> {
    return this.request("GET", "/api/lint");
  }

  lintFixable(): Promise<JsonResponse> {
    return this.request("GET", "/api/lint/fixable");
  }

  lintStale(): Promise<JsonResponse> {
    return this.request("GET", "/api/lint/stale");
  }

  lintContradictions(): Promise<JsonResponse> {
    return this.request("GET", "/api/lint/contradictions");
  }

  listCaptures(params: { status?: string; tag?: string; limit?: number; offset?: number } = {}): Promise<JsonResponse> {
    return this.request("GET", "/api/captures", {
      params: {
        status: params.status,
        tag: params.tag,
        limit: params.limit ?? 50,
        offset: params.offset ?? 0,
      },
    });
  }

  createCapture(params: { title: string; body: string; source: string; tags?: string[] }): Promise<JsonResponse> {
    return this.request("POST", "/api/capture", {
      data: this.withoutUndefined({
        title: params.title,
        body: params.body,
        source: params.source,
        tags: params.tags,
        observation: params.body,
        sources: [params.source],
      }),
    });
  }

  captureDigest(): Promise<JsonResponse> {
    return this.request("GET", "/api/captures/digest");
  }

  updateCaptureStatus(pageId: string, status: string): Promise<JsonResponse> {
    return this.request("POST", `/api/captures/${this.path(pageId)}/status`, { data: { status } });
  }

  promoteCapture(
    pageId: string,
    options: { commitMessage?: string; targetPageId?: string; content?: string } = {},
  ): Promise<JsonResponse> {
    return this.request("POST", `/api/captures/${this.path(pageId)}/promote`, {
      data: this.withoutUndefined({
        commit_message: options.commitMessage,
        target_page_id: options.targetPageId,
        content: options.content,
      }),
    });
  }

  promotionAudit(): Promise<JsonResponse> {
    return this.request("GET", "/api/promotions");
  }

  catalog(): Promise<JsonResponse> {
    return this.request("GET", "/api/catalog");
  }

  semantics(): Promise<JsonResponse> {
    return this.request("GET", "/api/semantics");
  }

  frontmatterSpec(): Promise<JsonResponse> {
    return this.request("GET", "/api/frontmatter/spec");
  }

  listDecisions(): Promise<JsonResponse> {
    return this.request("GET", "/api/decisions");
  }

  decisionTemplate(): Promise<JsonResponse> {
    return this.request("GET", "/api/decisions/template");
  }

  listProcedures(): Promise<JsonResponse> {
    return this.request("GET", "/api/procedures");
  }

  procedureTemplate(): Promise<JsonResponse> {
    return this.request("GET", "/api/procedures/template");
  }

  exportProcedure(pageId: string): Promise<JsonResponse> {
    return this.request("GET", `/api/procedures/${this.path(pageId)}/export`);
  }

  codeReferences(codePath: string): Promise<JsonResponse> {
    return this.request("GET", `/api/code-references/${this.path(codePath)}`);
  }

  ingestService(serviceId: string, sourceDir: string): Promise<JsonResponse> {
    return this.request("POST", `/api/code-ingest/${this.path(serviceId)}`, { params: { source_dir: sourceDir } });
  }

  serviceInventory(serviceId: string): Promise<JsonResponse> {
    return this.request("GET", `/api/code-ingest/${this.path(serviceId)}/inventory`);
  }

  ragRetrieve(query: string, options: { limit?: number } = {}): Promise<JsonResponse> {
    return this.request("POST", "/api/rag/retrieve", { data: { query, limit: options.limit ?? 10 } });
  }

  ragEvaluate(query: string, expectedIds: string[]): Promise<JsonResponse> {
    return this.request("POST", "/api/rag/evaluate", {
      data: { queries: [{ query, expected_ids: expectedIds }] },
    });
  }

  mcp(payload: Record<string, any>): Promise<JsonResponse> {
    return this.request("POST", "/mcp", { data: payload });
  }

  mcpInfo(): Promise<JsonResponse> {
    return this.request("GET", "/mcp");
  }

  protected async request(
    method: string,
    path: string,
    options: { params?: QueryParams; data?: RequestBody } = {},
  ): Promise<JsonResponse> {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), this.timeout);
    const headers: Record<string, string> = { Accept: "application/json" };
    const init: RequestInit = { method, headers, signal: controller.signal };

    if (this.authToken) {
      headers.Authorization = `Bearer ${this.authToken}`;
    }
    if (options.data !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(options.data);
    }

    try {
      const response = await fetch(this.url(path, options.params), init);
      if (!response.ok) {
        throw new LoreError(response.status, await this.errorMessage(response));
      }
      return this.parseJson(await response.text());
    } catch (error) {
      if (error instanceof LoreError) {
        throw error;
      }
      if (error instanceof Error) {
        throw new LoreError(0, error.message);
      }
      throw new LoreError(0, String(error));
    } finally {
      clearTimeout(timeoutId);
    }
  }

  private url(path: string, params?: QueryParams): string {
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(params ?? {})) {
      if (value === undefined || value === null) {
        continue;
      }
      const values = Array.isArray(value) ? value : [value];
      for (const item of values) {
        search.append(key, String(item));
      }
    }
    const query = search.toString();
    return query ? `${this.baseUrl}${path}?${query}` : `${this.baseUrl}${path}`;
  }

  private path(value: string): string {
    return value
      .replace(/^\/+|\/+$/g, "")
      .split("/")
      .map((part) => encodeURIComponent(part))
      .join("/");
  }

  protected withoutUndefined(values: JsonObject): JsonObject {
    return Object.fromEntries(Object.entries(values).filter(([, value]) => value !== undefined && value !== null));
  }

  private parseJson(text: string): JsonResponse {
    if (!text) {
      return {};
    }
    return JSON.parse(text);
  }

  private async errorMessage(response: Response): Promise<string> {
    const text = await response.text();
    if (!text) {
      return response.statusText;
    }
    try {
      const payload = JSON.parse(text);
      if (payload && typeof payload === "object" && !Array.isArray(payload)) {
        const detail = payload.detail ?? payload.message ?? payload.error;
        if (detail !== undefined && detail !== null) {
          return String(detail);
        }
      }
    } catch {
      return text;
    }
    return text;
  }
}

/** Typed durable-memory client using the same transport, auth, timeout, and errors as LoreClient. */
export class MemoryProvider extends LoreClient {
  async capture(memoryText: string, options: MemoryCaptureOptions = {}): Promise<string> {
    const result = (await this.request("POST", "/api/memory/capture", {
      data: this.withoutUndefined({
        text: memoryText,
        agent_name: options.agentName,
        namespace: options.namespace ?? "inbox",
        metadata: options.metadata,
        lane: options.lane,
        actor: options.actor,
        task_id: options.taskId,
        decision_id: options.decisionId,
        trace_id: options.traceId,
        tool_calls: options.toolCalls,
        constraints: options.constraints,
        policies_applied: options.policiesApplied,
        provenance: options.provenance,
      }),
    })) as MemoryCaptureResponse;
    return result.capture_id;
  }

  async recall(query?: string, options: MemoryRecallOptions = {}): Promise<MemoryRecallClaim[]> {
    return (await this.recallResponse(query, options)).claims;
  }

  recallResponse(query?: string, options: MemoryRecallOptions = {}): Promise<MemoryRecallResponse> {
    return this.request("GET", "/api/memory/recall", {
      params: {
        query,
        subject: options.subject,
        lane: options.lane,
        actor: options.actor,
        min_strength: options.minStrength || undefined,
        limit: options.limit ?? 20,
        record_access: options.recordAccess ?? false,
        cross_actor: options.crossActor || undefined,
      },
    }) as Promise<MemoryRecallResponse>;
  }

  acknowledgeRecall(
    candidateIds: string[],
    options: MemoryRecallAckOptions = {},
  ): Promise<MemoryRecallAckResponse> {
    return this.request("POST", "/api/memory/recall/ack", {
      data: this.withoutUndefined({
        candidate_ids: candidateIds,
        actor: options.actor,
        cross_actor: options.crossActor || undefined,
      }),
    }) as Promise<MemoryRecallAckResponse>;
  }
}
