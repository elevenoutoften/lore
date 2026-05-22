# Graph Analytics Design

Lore graph analytics are advisory signals for retrieval and discovery. They summarize the existing context graph, but they are not canonical content and are not stored back into pages, captures, claims, or traces.

## Options

### SQLite-local analytics

The first slice computes analytics directly from the existing `ContextGraph` built from SQLite-backed repository and ledger data. The implementation can stay in pure Python with no new services or dependencies:

- Degree centrality highlights heavily connected pages, claims, actors, sources, and traces.
- Betweenness centrality highlights bridge nodes that sit on many shortest paths.
- Community detection groups densely related nodes using lightweight label propagation.
- Semantic entry points combine high degree with cross-community bridging.

This approach is simple to operate and works everywhere Lore already runs. It is also naturally optional: if analytics are slow or unnecessary for a request, callers can skip them without affecting canonical reads and writes.

### External vector index

An external index such as Qdrant or ChromaDB would support embedding search, semantic clustering, and richer similarity queries. That can become valuable when Lore needs natural-language nearest-neighbor retrieval across large corpora or cross-modal data.

The trade-off is operational complexity. A vector DB adds deployment, persistence, schema migration, synchronization, and failure-mode concerns. It also creates a second derived index that must be kept consistent with pages and ledger entries. Those costs are hard to justify for basic graph centrality and community detection on small local graphs.

## Recommendation

Start with SQLite-local analytics. The context graph already contains typed nodes and edges, the expected graph size is modest, and pure Python analytics provide useful retrieval hints without introducing new infrastructure. Vector embeddings should be added later as a separate module when semantic similarity is needed, not as a prerequisite for graph metrics.

## API Surface

The analytics module exposes:

- `GraphAnalytics.compute()`: computes and caches the full result for one graph instance.
- `GraphAnalytics.degree_centrality(node_id)`: returns a node's degree centrality.
- `GraphAnalytics.community_of(node_id)`: returns a node's detected community ID.
- `GraphAnalytics.top_k_by(metric, k)`: returns top node IDs by `degree_centrality` or `betweenness`.
- `GraphAnalytics.semantic_entry_points(k)`: returns discovery entry points based on degree and community bridging.

The REST API exposes `GET /api/graph/analytics`, returning node metrics, communities, top nodes, compute timestamp, and graph size. The MCP tool `lore_graph_analytics` exposes the same computed result for agent clients.

## Caching

Analytics are cached in memory per `GraphAnalytics` instance. That keeps repeated metric lookups idempotent and cheap after `compute()` runs. A broader application-level cache can be added later and should be invalidated on page upsert, page delete, capture promotion, and ledger writes that change context graph nodes or edges.

This cache is a performance optimization only. The source of truth remains the repository, ledger, and context graph builder.

## Canonicality

Analytics results are advisory. They can influence ranking, navigation, and retrieval entry points, but they never replace canonical page content, provenance, claims, decisions, traces, or policies. If an analytics result conflicts with stored content, the stored content wins.
