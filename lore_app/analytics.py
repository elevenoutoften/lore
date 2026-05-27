from __future__ import annotations

from collections import Counter, deque
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from .schemas import ContextGraph


class GraphMetrics(BaseModel):
    """Metrics for a single node."""

    node_id: str
    degree_centrality: float
    betweenness_centrality: float = 0.0
    community: int = -1


class GraphAnalyticsResult(BaseModel):
    """Complete analytics result for a context graph."""

    node_metrics: dict[str, GraphMetrics]
    communities: list[list[str]]
    top_nodes: dict[str, list[str]]
    computed_at: str
    node_count: int
    edge_count: int


class GraphAnalytics:
    """Compute analytics on a context graph. Results are advisory, never canonical."""

    def __init__(self, graph: ContextGraph):
        self._graph = graph
        self._adjacency: dict[str, set[str]] = {}
        self._result: GraphAnalyticsResult | None = None

    def compute(self) -> GraphAnalyticsResult:
        """Compute all analytics. Idempotent; caches result."""

        if self._result is not None:
            return self._result

        node_ids = sorted({node.id for node in self._graph.nodes})
        self._adjacency = self._build_adjacency(node_ids)
        degree = self._compute_degree_centrality(node_ids)
        community_by_node, communities = self._detect_communities(node_ids)
        betweenness = self._compute_betweenness(node_ids)

        node_metrics = {
            node_id: GraphMetrics(
                node_id=node_id,
                degree_centrality=degree.get(node_id, 0.0),
                betweenness_centrality=betweenness.get(node_id, 0.0),
                community=community_by_node.get(node_id, -1),
            )
            for node_id in node_ids
        }

        top_nodes = {
            "degree_centrality": self._top_nodes(node_metrics, "degree_centrality", 10),
            "betweenness": self._top_nodes(node_metrics, "betweenness_centrality", 10),
        }
        self._result = GraphAnalyticsResult(
            node_metrics=node_metrics,
            communities=communities,
            top_nodes=top_nodes,
            computed_at=datetime.now(UTC).isoformat(),
            node_count=len(node_ids),
            edge_count=len(self._graph.edges),
        )
        return self._result

    def degree_centrality(self, node_id: str) -> float:
        """Get degree centrality for a specific node."""

        return (
            self.compute()
            .node_metrics.get(node_id, GraphMetrics(node_id=node_id, degree_centrality=0.0))
            .degree_centrality
        )

    def community_of(self, node_id: str) -> int:
        """Get community ID for a specific node."""

        return self.compute().node_metrics.get(node_id, GraphMetrics(node_id=node_id, degree_centrality=0.0)).community

    def top_k_by(self, metric: str, k: int = 10) -> list[str]:
        """Get top-k nodes by metric name."""

        result = self.compute()
        metric_name = "betweenness_centrality" if metric == "betweenness" else metric
        if metric_name not in {"degree_centrality", "betweenness_centrality"}:
            return []
        return self._top_nodes(result.node_metrics, metric_name, max(0, k))

    def semantic_entry_points(self, k: int = 5) -> list[str]:
        """Get nodes that are good entry points for discovery."""

        result = self.compute()
        total_communities = max(1, len(result.communities))
        scores: list[tuple[float, str]] = []
        for node_id, metrics in result.node_metrics.items():
            neighbor_communities = {
                result.node_metrics[neighbor].community
                for neighbor in self._adjacency.get(node_id, set())
                if neighbor in result.node_metrics and result.node_metrics[neighbor].community >= 0
            }
            bridge_score = len(neighbor_communities) / total_communities
            scores.append((metrics.degree_centrality + bridge_score, node_id))
        return [node_id for _, node_id in sorted(scores, key=lambda item: (-item[0], item[1]))[: max(0, k)]]

    def _build_adjacency(self, node_ids: list[str]) -> dict[str, set[str]]:
        adjacency = {node_id: set() for node_id in node_ids}
        valid_nodes = set(node_ids)
        for edge in self._graph.edges:
            if edge.source not in valid_nodes or edge.target not in valid_nodes or edge.source == edge.target:
                continue
            adjacency[edge.source].add(edge.target)
            adjacency[edge.target].add(edge.source)
        return adjacency

    def _compute_degree_centrality(self, node_ids: list[str]) -> dict[str, float]:
        if len(node_ids) <= 1:
            return {node_id: 0.0 for node_id in node_ids}
        scale = len(node_ids) - 1
        return {node_id: len(self._adjacency.get(node_id, set())) / scale for node_id in node_ids}

    def _detect_communities(self, node_ids: list[str]) -> tuple[dict[str, int], list[list[str]]]:
        if not node_ids:
            return {}, []

        labels = {node_id: node_id for node_id in node_ids}
        for _ in range(50):
            changed = False
            for node_id in node_ids:
                neighbors = sorted(self._adjacency.get(node_id, set()))
                if not neighbors:
                    continue
                counts = Counter(labels[neighbor] for neighbor in neighbors)
                best_label = min(counts, key=lambda label: (-counts[label], label))
                if labels[node_id] != best_label:
                    labels[node_id] = best_label
                    changed = True
            if not changed:
                break

        grouped: dict[str, list[str]] = {}
        for node_id in node_ids:
            grouped.setdefault(labels[node_id], []).append(node_id)
        communities = sorted(
            (sorted(members) for members in grouped.values()), key=lambda members: (members[0], len(members))
        )
        community_by_node = {
            node_id: community_id for community_id, members in enumerate(communities) for node_id in members
        }
        return community_by_node, communities

    def _compute_betweenness(self, node_ids: list[str]) -> dict[str, float]:
        scores = {node_id: 0.0 for node_id in node_ids}
        if len(node_ids) < 3:
            return scores

        sources = node_ids if len(node_ids) < 50 else node_ids[:20]
        for source in sources:
            stack: list[str] = []
            predecessors = {node_id: [] for node_id in node_ids}
            shortest_path_counts = dict.fromkeys(node_ids, 0.0)
            distances = dict.fromkeys(node_ids, -1)
            shortest_path_counts[source] = 1.0
            distances[source] = 0

            queue: deque[str] = deque([source])
            while queue:
                current = queue.popleft()
                stack.append(current)
                for neighbor in sorted(self._adjacency.get(current, set())):
                    if distances[neighbor] < 0:
                        queue.append(neighbor)
                        distances[neighbor] = distances[current] + 1
                    if distances[neighbor] == distances[current] + 1:
                        shortest_path_counts[neighbor] += shortest_path_counts[current]
                        predecessors[neighbor].append(current)

            dependencies = dict.fromkeys(node_ids, 0.0)
            while stack:
                node_id = stack.pop()
                for predecessor in predecessors[node_id]:
                    if shortest_path_counts[node_id]:
                        share = shortest_path_counts[predecessor] / shortest_path_counts[node_id]
                        dependencies[predecessor] += share * (1.0 + dependencies[node_id])
                if node_id != source:
                    scores[node_id] += dependencies[node_id]

        for node_id in scores:
            scores[node_id] /= 2.0
        if len(node_ids) > 2:
            normalizer = (len(node_ids) - 1) * (len(node_ids) - 2) / 2
            if normalizer:
                for node_id in scores:
                    scores[node_id] /= normalizer
        return scores

    @staticmethod
    def _top_nodes(node_metrics: dict[str, GraphMetrics], metric_name: str, k: int) -> list[str]:
        return [
            node_id
            for node_id, _ in sorted(
                node_metrics.items(),
                key=lambda item: (-getattr(item[1], metric_name), item[0]),
            )[:k]
        ]
