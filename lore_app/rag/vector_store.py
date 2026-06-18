"""SQLite-backed sparse and optional dense vector retrieval."""

from __future__ import annotations

import logging
import math
import re
import sqlite3
import threading
from collections import Counter
from contextlib import suppress
from pathlib import Path
from typing import Any, Protocol

try:
    import sqlite_vec
except ImportError:  # pragma: no cover - exercised in packaging environments
    sqlite_vec = None

from lore_app.db_utils import retry_on_locked

logger = logging.getLogger(__name__)


class EmbeddingBackend(Protocol):
    model: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...

    def close(self) -> None: ...


class VectorStore:
    """TF-IDF store with an optional sqlite-vec dense index."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._embedding_lock = threading.Lock()
        self._cache_lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._dense_available = False
        if sqlite_vec is not None:
            try:
                self._conn.enable_load_extension(True)
                sqlite_vec.load(self._conn)
                self._dense_available = True
            except Exception as exc:
                logger.warning("sqlite-vec unavailable; continuing with sparse TF-IDF retrieval: %s", exc)
            finally:
                with suppress(AttributeError, sqlite3.Error):
                    self._conn.enable_load_extension(False)
        else:
            logger.warning("sqlite-vec is not installed; continuing with sparse TF-IDF retrieval")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._init_tables()
        self._idf_cache: dict[str, float] = {}
        self._embedding_backend: EmbeddingBackend | None = None

    def _init_tables(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id TEXT PRIMARY KEY,
                    page_id TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    token_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_chunks_page ON chunks(page_id);

                CREATE TABLE IF NOT EXISTS chunk_tokens (
                    chunk_id TEXT NOT NULL,
                    token TEXT NOT NULL,
                    tf REAL NOT NULL,
                    FOREIGN KEY (chunk_id) REFERENCES chunks(chunk_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_tokens_chunk ON chunk_tokens(chunk_id);
                CREATE INDEX IF NOT EXISTS idx_tokens_token ON chunk_tokens(token);

                CREATE TABLE IF NOT EXISTS doc_freq (
                    token TEXT PRIMARY KEY,
                    df INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS dense_index_metadata (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    model TEXT NOT NULL,
                    dimensions INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS dense_vector_rows (
                    rowid INTEGER PRIMARY KEY,
                    chunk_id TEXT NOT NULL UNIQUE,
                    page_id TEXT NOT NULL
                );
                """
            )
            self._conn.commit()

    @property
    def dense_enabled(self) -> bool:
        with self._embedding_lock:
            return self._dense_available and self._embedding_backend is not None

    @property
    def dense_available(self) -> bool:
        return self._dense_available

    def configure_embedding_backend(self, backend: EmbeddingBackend | None) -> bool:
        """Swap the optional backend; return whether the dense index needs rebuilding."""
        with self._embedding_lock:
            if backend is not None and not self._dense_available:
                logger.warning("Dense embedding backend ignored because sqlite-vec is unavailable")
                backend.close()
                backend = None
            old = self._embedding_backend
            self._embedding_backend = backend
            if old is not None and old is not backend:
                old.close()
            with self._lock:
                row = self._conn.execute("SELECT model FROM dense_index_metadata WHERE singleton = 1").fetchone()
            return backend is not None and (row is None or row[0] != backend.model)

    @retry_on_locked()
    def upsert_chunk(self, chunk_id: str, page_id: str, chunk_index: int, content: str) -> None:
        """Upsert a single chunk. Prefer upsert_page_chunks() for batch operations."""
        # Deprecated: callers should prefer upsert_page_chunks() to avoid per-chunk commits.
        tokens = self._tokenize(content)
        tf_counts = Counter(tokens)
        total = len(tokens) or 1

        with self._lock:
            self._conn.execute("DELETE FROM chunk_tokens WHERE chunk_id = ?", (chunk_id,))
            self._conn.execute("DELETE FROM chunks WHERE chunk_id = ?", (chunk_id,))
            self._conn.execute(
                "INSERT INTO chunks (chunk_id, page_id, chunk_index, content, token_count) VALUES (?, ?, ?, ?, ?)",
                (chunk_id, page_id, chunk_index, content, len(tokens)),
            )
            self._conn.executemany(
                "INSERT INTO chunk_tokens (chunk_id, token, tf) VALUES (?, ?, ?)",
                [(chunk_id, token, count / total) for token, count in tf_counts.items()],
            )
            self._conn.commit()
            self._clear_idf_cache()

    @retry_on_locked()
    def upsert_page_chunks(self, page_id: str, chunks: list[dict[str, Any]]) -> None:
        """Bulk upsert all chunks for a page in a single transaction."""
        dense_vectors: list[list[float]] | None = None
        dense_backend: EmbeddingBackend | None = None
        if chunks:
            with self._embedding_lock:
                dense_backend = self._embedding_backend
                if dense_backend is not None:
                    try:
                        dense_vectors = dense_backend.embed([str(chunk["content"]) for chunk in chunks])
                    except Exception as exc:
                        logger.warning("Dense page indexing failed; sparse TF-IDF index was retained: %s", exc)
                        dense_vectors = None
        with self._lock:
            old_rows = self._conn.execute(
                "SELECT token, COUNT(DISTINCT chunk_id) "
                "FROM chunk_tokens "
                "WHERE chunk_id IN (SELECT chunk_id FROM chunks WHERE page_id = ?) "
                "GROUP BY token",
                (page_id,),
            ).fetchall()

            old_chunk_ids = [
                row[0]
                for row in self._conn.execute(
                    "SELECT chunk_id FROM chunks WHERE page_id = ?",
                    (page_id,),
                ).fetchall()
            ]
            if old_chunk_ids:
                placeholders = ",".join("?" for _ in old_chunk_ids)
                self._conn.execute(
                    f"DELETE FROM chunk_tokens WHERE chunk_id IN ({placeholders})",
                    old_chunk_ids,
                )
            self._conn.execute("DELETE FROM chunks WHERE page_id = ?", (page_id,))

            all_token_rows: list[tuple[str, str, float]] = []
            for chunk in chunks:
                tokens = self._tokenize(chunk["content"])
                tf_counts = Counter(tokens)
                total = len(tokens) or 1
                self._conn.execute(
                    "INSERT INTO chunks (chunk_id, page_id, chunk_index, content, token_count) VALUES (?, ?, ?, ?, ?)",
                    (
                        chunk["chunk_id"],
                        chunk["page_id"],
                        chunk["chunk_index"],
                        chunk["content"],
                        len(tokens),
                    ),
                )
                all_token_rows.extend((chunk["chunk_id"], token, count / total) for token, count in tf_counts.items())
            if all_token_rows:
                self._conn.executemany(
                    "INSERT INTO chunk_tokens (chunk_id, token, tf) VALUES (?, ?, ?)",
                    all_token_rows,
                )

            new_rows = self._conn.execute(
                "SELECT token, COUNT(DISTINCT chunk_id) "
                "FROM chunk_tokens "
                "WHERE chunk_id IN (SELECT chunk_id FROM chunks WHERE page_id = ?) "
                "GROUP BY token",
                (page_id,),
            ).fetchall()

            self._apply_doc_freq_decrements(old_rows)
            self._apply_doc_freq_increments(new_rows)

            self._conn.commit()
            self._clear_idf_cache()
        if dense_vectors is not None and dense_backend is not None:
            with self._embedding_lock:
                if self._embedding_backend is dense_backend:
                    with self._lock:
                        try:
                            self._upsert_dense_page(page_id, chunks, dense_vectors, dense_backend.model)
                        except Exception as exc:
                            self._conn.rollback()
                            logger.warning("Dense vector persistence failed; sparse TF-IDF index was retained: %s", exc)

    def remove_page(self, page_id: str) -> int:
        with self._lock:
            old_rows = self._conn.execute(
                "SELECT token, COUNT(DISTINCT chunk_id) "
                "FROM chunk_tokens "
                "WHERE chunk_id IN (SELECT chunk_id FROM chunks WHERE page_id = ?) "
                "GROUP BY token",
                (page_id,),
            ).fetchall()
            chunk_ids = [
                row[0]
                for row in self._conn.execute(
                    "SELECT chunk_id FROM chunks WHERE page_id = ?",
                    (page_id,),
                ).fetchall()
            ]
            if chunk_ids:
                placeholders = ",".join("?" for _ in chunk_ids)
                self._conn.execute(
                    f"DELETE FROM chunk_tokens WHERE chunk_id IN ({placeholders})",
                    chunk_ids,
                )
            cursor = self._conn.execute("DELETE FROM chunks WHERE page_id = ?", (page_id,))
            self._delete_dense_page(page_id)
            self._apply_doc_freq_decrements(old_rows)
            self._conn.commit()
            self._clear_idf_cache()
            return cursor.rowcount

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Search dense embeddings when configured, otherwise use TF-IDF."""
        if self.dense_enabled:
            try:
                dense = self._search_dense(query, limit)
                if dense:
                    return dense
            except Exception:
                # A remote embedding outage must not take local recall down with it.
                pass
        return self._search_sparse(query, limit)

    def _search_sparse(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []

        idf = self._get_idf(query_tokens)
        query_tf = Counter(query_tokens)
        query_vec = {token: count / len(query_tokens) for token, count in query_tf.items()}

        placeholders = ",".join("?" for _ in query_tokens)
        with self._lock:
            rows = self._conn.execute(
                f"""
                WITH matching_chunks AS (
                    SELECT DISTINCT chunk_id
                    FROM chunk_tokens
                    WHERE token IN ({placeholders})
                )
                SELECT ct.chunk_id, ct.token, ct.tf,
                       c.page_id, c.chunk_index, c.content, c.token_count
                FROM chunk_tokens ct
                JOIN matching_chunks mc ON mc.chunk_id = ct.chunk_id
                JOIN chunks c ON ct.chunk_id = c.chunk_id
                """,
                query_tokens,
            ).fetchall()
        if not rows:
            return []

        chunk_data: dict[str, dict[str, Any]] = {}
        for row in rows:
            chunk_id = row[0]
            if chunk_id not in chunk_data:
                chunk_data[chunk_id] = {
                    "tokens": {},
                    "page_id": row[3],
                    "chunk_index": row[4],
                    "content": row[5],
                    "token_count": row[6],
                }
            chunk_data[chunk_id]["tokens"][row[1]] = row[2]

        results: list[dict[str, Any]] = []
        for chunk_id, data in chunk_data.items():
            chunk_vec = data["tokens"]
            score = self._cosine_similarity(query_vec, chunk_vec, idf)
            if score > 0:
                results.append(
                    {
                        "chunk_id": chunk_id,
                        "page_id": data["page_id"],
                        "chunk_index": data["chunk_index"],
                        "content": data["content"],
                        "token_count": data["token_count"],
                        "score": score,
                    }
                )
        results.sort(key=lambda result: (result["score"], result["chunk_id"]), reverse=True)
        return results[:limit]

    @retry_on_locked()
    def rebuild_doc_freq(self) -> None:
        """Rebuild the entire doc_freq table from scratch."""
        # Deprecated for normal page updates; upsert_page_chunks() maintains doc_freq incrementally.
        with self._lock:
            self._conn.execute("DELETE FROM doc_freq")
            rows = self._conn.execute(
                "SELECT token, COUNT(DISTINCT chunk_id) FROM chunk_tokens GROUP BY token"
            ).fetchall()
            self._conn.executemany("INSERT INTO doc_freq (token, df) VALUES (?, ?)", rows)
            self._conn.commit()
            self._clear_idf_cache()

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM chunk_tokens")
            self._conn.execute("DELETE FROM chunks")
            self._conn.execute("DELETE FROM doc_freq")
            self._drop_dense_index()
            self._conn.commit()
            self._clear_idf_cache()

    def chunk_count(self) -> int:
        """Number of indexed chunks. 0 means the vector index has not been built."""
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])

    def _get_idf(self, tokens: list[str]) -> dict[str, float]:
        with self._cache_lock:
            cache = dict(self._idf_cache)
        if not cache:
            # Serialize the shared-connection reads under the same lock writers
            # use. search() calls this before acquiring _lock, so there is no
            # reentrancy; without it a concurrent writer on the same sqlite3
            # connection can raise or return a partially-updated cache.
            with self._lock:
                total_docs = self._conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
                if total_docs == 0:
                    return {token: 0.0 for token in tokens}
                rows = self._conn.execute("SELECT token, df FROM doc_freq").fetchall()
            cache = {token: math.log((total_docs + 1) / (df + 1)) + 1 for token, df in rows}
            with self._cache_lock:
                self._idf_cache = dict(cache)
        return {token: cache.get(token, 1.0) for token in tokens}

    def semantic_similarities(self, query: str, texts: list[str]) -> list[float] | None:
        """Return embedding cosine similarities for arbitrary texts, or None in sparse mode."""
        if not texts:
            return None
        with self._embedding_lock:
            backend = self._embedding_backend
            if backend is None:
                return None
            vectors = backend.embed([query, *texts])
        query_vector = vectors[0]
        return [self._dense_cosine(query_vector, vector) for vector in vectors[1:]]

    def _ensure_dense_index(self, dimensions: int, model: str) -> None:
        metadata = self._conn.execute(
            "SELECT model, dimensions FROM dense_index_metadata WHERE singleton = 1"
        ).fetchone()
        if metadata != (model, dimensions):
            self._drop_dense_index()
            self._conn.execute(
                f"CREATE VIRTUAL TABLE dense_vectors USING vec0(embedding float[{dimensions}] distance_metric=cosine)"
            )
            self._conn.execute(
                "INSERT INTO dense_index_metadata(singleton, model, dimensions) VALUES (1, ?, ?)",
                (model, dimensions),
            )

    def _drop_dense_index(self) -> None:
        if self._dense_available:
            self._conn.execute("DROP TABLE IF EXISTS dense_vectors")
        self._conn.execute("DELETE FROM dense_vector_rows")
        self._conn.execute("DELETE FROM dense_index_metadata")

    def _delete_dense_page(self, page_id: str) -> None:
        rows = self._conn.execute("SELECT rowid FROM dense_vector_rows WHERE page_id = ?", (page_id,)).fetchall()
        if self._dense_available and self._dense_table_exists():
            self._conn.executemany("DELETE FROM dense_vectors WHERE rowid = ?", rows)
        self._conn.execute("DELETE FROM dense_vector_rows WHERE page_id = ?", (page_id,))

    def _upsert_dense_page(
        self,
        page_id: str,
        chunks: list[dict[str, Any]],
        vectors: list[list[float]],
        model: str,
    ) -> None:
        if len(vectors) != len(chunks) or (vectors and not vectors[0]):
            raise ValueError("Embedding backend returned malformed vectors")
        self._ensure_dense_index(len(vectors[0]), model)
        self._delete_dense_page(page_id)
        for chunk, vector in zip(chunks, vectors, strict=True):
            cursor = self._conn.execute(
                "INSERT INTO dense_vector_rows(chunk_id, page_id) VALUES (?, ?)",
                (chunk["chunk_id"], page_id),
            )
            self._conn.execute(
                "INSERT INTO dense_vectors(rowid, embedding) VALUES (?, ?)",
                (cursor.lastrowid, self._serialize_vector(vector)),
            )
        self._conn.commit()

    def _search_dense(self, query: str, limit: int) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        with self._embedding_lock:
            backend = self._embedding_backend
            if backend is None:
                return []
            vector = backend.embed([query])[0]
            with self._lock:
                if not self._dense_table_exists():
                    return []
                rows = self._conn.execute(
                    """
                    SELECT c.chunk_id, c.page_id, c.chunk_index, c.content, c.token_count, v.distance
                    FROM dense_vectors v
                    JOIN dense_vector_rows d ON d.rowid = v.rowid
                    JOIN chunks c ON c.chunk_id = d.chunk_id
                    WHERE v.embedding MATCH ? AND k = ?
                    ORDER BY v.distance
                    """,
                    (self._serialize_vector(vector), limit),
                ).fetchall()
        return [
            {
                "chunk_id": row[0],
                "page_id": row[1],
                "chunk_index": row[2],
                "content": row[3],
                "token_count": row[4],
                "score": max(0.0, 1.0 - float(row[5])),
                "semantic_similarity": max(0.0, 1.0 - float(row[5])),
            }
            for row in rows
        ]

    def _dense_table_exists(self) -> bool:
        return self._conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'dense_vectors'").fetchone() is not None

    @staticmethod
    def _serialize_vector(vector: list[float]) -> bytes:
        if sqlite_vec is None:  # pragma: no cover - guarded by dense_available
            raise RuntimeError("sqlite-vec is unavailable")
        return sqlite_vec.serialize_float32(vector)

    @staticmethod
    def _dense_cosine(a: list[float], b: list[float]) -> float:
        if len(a) != len(b) or not a:
            return 0.0
        dot = sum(x * y for x, y in zip(a, b, strict=True))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        if not norm_a or not norm_b:
            return 0.0
        return max(0.0, min(1.0, dot / (norm_a * norm_b)))

    def _clear_idf_cache(self) -> None:
        with self._cache_lock:
            self._idf_cache.clear()

    def _apply_doc_freq_decrements(self, rows: list[tuple[str, int]]) -> None:
        """Decrement df for token contributions being removed."""
        for token, df_decr in rows:
            self._conn.execute(
                "UPDATE doc_freq SET df = df - ? WHERE token = ?",
                (df_decr, token),
            )
        self._conn.execute("DELETE FROM doc_freq WHERE df <= 0")

    def _apply_doc_freq_increments(self, rows: list[tuple[str, int]]) -> None:
        """Increment df for token contributions being inserted."""
        for token, df_incr in rows:
            cursor = self._conn.execute(
                "UPDATE doc_freq SET df = df + ? WHERE token = ?",
                (df_incr, token),
            )
            if cursor.rowcount == 0:
                self._conn.execute(
                    "INSERT INTO doc_freq (token, df) VALUES (?, ?)",
                    (token, df_incr),
                )

    def _cosine_similarity(
        self,
        vec_a: dict[str, float],
        vec_b: dict[str, float],
        idf: dict[str, float],
    ) -> float:
        common = set(vec_a) & set(vec_b)
        if not common:
            return 0.0
        dot = sum(vec_a[token] * idf.get(token, 1.0) * vec_b[token] * idf.get(token, 1.0) for token in common)
        norm_a = math.sqrt(sum((value * idf.get(token, 1.0)) ** 2 for token, value in vec_a.items()))
        norm_b = math.sqrt(sum((value * idf.get(token, 1.0)) ** 2 for token, value in vec_b.items()))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return re.findall(r"[a-z0-9]{2,}", text.lower())

    def close(self) -> None:
        with self._embedding_lock:
            if self._embedding_backend is not None:
                self._embedding_backend.close()
                self._embedding_backend = None
        if self._conn is not None:
            self._conn.close()
            self._conn = None
