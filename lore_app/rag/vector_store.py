"""SQLite-backed sparse vector store using TF-IDF weighting."""
from __future__ import annotations

import math
import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any


class VectorStore:
    """Sparse TF-IDF vector store backed by SQLite."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_tables()
        self._idf_cache: dict[str, float] = {}

    def _init_tables(self) -> None:
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

    def upsert_chunk(self, chunk_id: str, page_id: str, chunk_index: int, content: str) -> None:
        tokens = self._tokenize(content)
        tf_counts = Counter(tokens)
        total = len(tokens) or 1

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
        self._idf_cache.clear()

    def remove_page(self, page_id: str) -> int:
        rows = self._conn.execute("SELECT chunk_id FROM chunks WHERE page_id = ?", (page_id,)).fetchall()
        chunk_ids = [row[0] for row in rows]
        if chunk_ids:
            placeholders = ",".join("?" for _ in chunk_ids)
            self._conn.execute(f"DELETE FROM chunk_tokens WHERE chunk_id IN ({placeholders})", chunk_ids)
        cursor = self._conn.execute("DELETE FROM chunks WHERE page_id = ?", (page_id,))
        self._conn.commit()
        self._idf_cache.clear()
        return cursor.rowcount

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Search chunks using TF-IDF cosine similarity."""
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []

        query_tf = Counter(query_tokens)
        query_vec = {token: count / len(query_tokens) for token, count in query_tf.items()}
        idf = self._get_idf(query_tokens)

        placeholders = ",".join("?" for _ in query_tokens)
        rows = self._conn.execute(
            f"SELECT DISTINCT chunk_id FROM chunk_tokens WHERE token IN ({placeholders})",
            query_tokens,
        ).fetchall()
        if not rows:
            return []

        scored: list[tuple[float, str]] = []
        for (chunk_id,) in rows:
            token_rows = self._conn.execute(
                "SELECT token, tf FROM chunk_tokens WHERE chunk_id = ?",
                (chunk_id,),
            ).fetchall()
            chunk_vec = {row[0]: row[1] for row in token_rows}
            score = self._cosine_similarity(query_vec, chunk_vec, idf)
            if score > 0:
                scored.append((score, chunk_id))

        scored.sort(reverse=True)
        results: list[dict[str, Any]] = []
        for score, chunk_id in scored[:limit]:
            chunk_row = self._conn.execute(
                "SELECT page_id, chunk_index, content, token_count FROM chunks WHERE chunk_id = ?",
                (chunk_id,),
            ).fetchone()
            if chunk_row:
                results.append(
                    {
                        "chunk_id": chunk_id,
                        "page_id": chunk_row[0],
                        "chunk_index": chunk_row[1],
                        "content": chunk_row[2],
                        "token_count": chunk_row[3],
                        "score": score,
                    }
                )
        return results

    def rebuild_doc_freq(self) -> None:
        """Rebuild document frequency table from chunks."""
        self._conn.execute("DELETE FROM doc_freq")
        rows = self._conn.execute(
            "SELECT token, COUNT(DISTINCT chunk_id) FROM chunk_tokens GROUP BY token"
        ).fetchall()
        self._conn.executemany("INSERT INTO doc_freq (token, df) VALUES (?, ?)", rows)
        self._conn.commit()
        self._idf_cache.clear()

    def clear(self) -> None:
        self._conn.execute("DELETE FROM chunk_tokens")
        self._conn.execute("DELETE FROM chunks")
        self._conn.execute("DELETE FROM doc_freq")
        self._conn.commit()
        self._idf_cache.clear()

    def _get_idf(self, tokens: list[str]) -> dict[str, float]:
        if not self._idf_cache:
            total_docs = self._conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            if total_docs == 0:
                return {token: 0.0 for token in tokens}
            rows = self._conn.execute("SELECT token, df FROM doc_freq").fetchall()
            self._idf_cache = {
                token: math.log((total_docs + 1) / (df + 1)) + 1
                for token, df in rows
            }
        return {token: self._idf_cache.get(token, 1.0) for token in tokens}

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
        self._conn.close()
