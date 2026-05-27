"""SQLite-backed sparse vector store using TF-IDF weighting."""

from __future__ import annotations

import math
import re
import sqlite3
import threading
from collections import Counter
from pathlib import Path
from typing import Any

from lore_app.db_utils import retry_on_locked


class VectorStore:
    """Sparse TF-IDF vector store backed by SQLite."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._cache_lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._init_tables()
        self._idf_cache: dict[str, float] = {}

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
                """
            )
            self._conn.commit()

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
            self._apply_doc_freq_decrements(old_rows)
            self._conn.commit()
            self._clear_idf_cache()
            return cursor.rowcount

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Search chunks using TF-IDF cosine similarity."""
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
            self._conn.commit()
            self._clear_idf_cache()

    def _get_idf(self, tokens: list[str]) -> dict[str, float]:
        with self._cache_lock:
            cache = dict(self._idf_cache)
        if not cache:
            total_docs = self._conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            if total_docs == 0:
                return {token: 0.0 for token in tokens}
            rows = self._conn.execute("SELECT token, df FROM doc_freq").fetchall()
            cache = {token: math.log((total_docs + 1) / (df + 1)) + 1 for token, df in rows}
            with self._cache_lock:
                self._idf_cache = dict(cache)
        return {token: cache.get(token, 1.0) for token in tokens}

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
        if self._conn is not None:
            self._conn.close()
            self._conn = None
