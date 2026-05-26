from __future__ import annotations

import re
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .db_utils import retry_on_locked
from .repository import LoreRepository, MarkdownPage
from .schemas import PageDetail


class LoreSearchIndex:
    """SQLite FTS5 full-text search index for Lore pages."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._init_tables()

    def _init_tables(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS pages (
                    page_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL DEFAULT '',
                    visibility TEXT NOT NULL DEFAULT '',
                    status TEXT,
                    summary TEXT,
                    tags TEXT NOT NULL DEFAULT '',
                    body TEXT NOT NULL DEFAULT '',
                    sources TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT '',
                    actor TEXT,
                    lane TEXT
                );

                CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
                    page_id,
                    title,
                    summary,
                    tags,
                    body,
                    sources,
                    content='pages',
                    content_rowid='rowid'
                );

                CREATE TRIGGER IF NOT EXISTS pages_ai AFTER INSERT ON pages BEGIN
                    INSERT INTO pages_fts(rowid, page_id, title, summary, tags, body, sources)
                    VALUES (new.rowid, new.page_id, new.title, new.summary, new.tags, new.body, new.sources);
                END;

                CREATE TRIGGER IF NOT EXISTS pages_ad AFTER DELETE ON pages BEGIN
                    INSERT INTO pages_fts(pages_fts, rowid, page_id, title, summary, tags, body, sources)
                    VALUES ('delete', old.rowid, old.page_id, old.title, old.summary, old.tags, old.body, old.sources);
                END;

                CREATE TRIGGER IF NOT EXISTS pages_au AFTER UPDATE ON pages BEGIN
                    INSERT INTO pages_fts(pages_fts, rowid, page_id, title, summary, tags, body, sources)
                    VALUES ('delete', old.rowid, old.page_id, old.title, old.summary, old.tags, old.body, old.sources);
                    INSERT INTO pages_fts(rowid, page_id, title, summary, tags, body, sources)
                    VALUES (new.rowid, new.page_id, new.title, new.summary, new.tags, new.body, new.sources);
                END;
                """
            )
            self._conn.commit()
            self._migrate_columns()

    def _migrate_columns(self) -> None:
        """Add actor/lane columns to existing databases that lack them."""
        existing = {row[1] for row in self._conn.execute("PRAGMA table_info(pages)").fetchall()}
        if "actor" not in existing:
            self._conn.execute("ALTER TABLE pages ADD COLUMN actor TEXT")
        if "lane" not in existing:
            self._conn.execute("ALTER TABLE pages ADD COLUMN lane TEXT")
        self._conn.commit()

    @retry_on_locked()
    def rebuild(self, repo: LoreRepository) -> int:
        """Rebuild the index from scratch. Returns number of indexed pages."""
        with self._lock:
            self._conn.execute("DELETE FROM pages")
            self._conn.commit()

            count = 0
            for page in repo._scan_pages():
                self.upsert_page(page)
                count += 1
            return count

    def upsert_page(self, page: MarkdownPage) -> None:
        """Insert or update a single page in the index."""
        summary = page.summary()
        tags = " ".join(summary.tags)
        sources = " ".join(summary.sources)
        self._conn.execute(
            """INSERT INTO pages (page_id, title, kind, visibility, status, summary, tags, body, sources, updated_at, actor, lane)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(page_id) DO UPDATE SET
                   title = excluded.title,
                   kind = excluded.kind,
                   visibility = excluded.visibility,
                   status = excluded.status,
                   summary = excluded.summary,
                   tags = excluded.tags,
                   body = excluded.body,
                   sources = excluded.sources,
                   updated_at = excluded.updated_at,
                   actor = excluded.actor,
                   lane = excluded.lane""",
            (
                summary.id,
                summary.title,
                summary.kind,
                summary.visibility,
                summary.status,
                summary.summary or "",
                tags,
                searchable_body(page.body),
                sources,
                summary.updated_at,
                page.frontmatter.get("actor"),
                page.frontmatter.get("lane"),
            ),
        )
        self._conn.commit()

    def upsert_page_from_detail(self, page: PageDetail) -> None:
        """Upsert from a PageDetail API response."""
        tags = " ".join(page.tags)
        sources = " ".join(page.sources)
        self._conn.execute(
            """INSERT INTO pages (page_id, title, kind, visibility, status, summary, tags, body, sources, updated_at, actor, lane)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(page_id) DO UPDATE SET
                   title = excluded.title,
                   kind = excluded.kind,
                   visibility = excluded.visibility,
                   status = excluded.status,
                   summary = excluded.summary,
                   tags = excluded.tags,
                   body = excluded.body,
                   sources = excluded.sources,
                   updated_at = excluded.updated_at,
                   actor = excluded.actor,
                   lane = excluded.lane""",
            (
                page.id,
                page.title,
                page.kind,
                page.visibility,
                page.status,
                page.summary or "",
                tags,
                searchable_body(page.body),
                sources,
                page.updated_at,
                page.frontmatter.get("actor"),
                page.frontmatter.get("lane"),
            ),
        )
        self._conn.commit()

    def remove_page(self, page_id: str) -> None:
        """Remove a page from the index."""
        self._conn.execute("DELETE FROM pages WHERE page_id = ?", (page_id,))
        self._conn.commit()

    def search(self, query: str, *, kind: str | None = None, lane: str | None = None,
               actor: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """Search the FTS index using SQLite FTS rank ordering."""
        return self._search(query, kind=kind, lane=lane, actor=actor, limit=limit, order_expr="rank")

    def search_bm25(self, query: str, *, kind: str | None = None, lane: str | None = None,
                    actor: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """Search the FTS index using the FTS5 bm25() ranking function."""
        return self._search(query, kind=kind, lane=lane, actor=actor, limit=limit, order_expr="bm25(pages_fts)")

    def _search(
        self,
        query: str,
        *,
        kind: str | None = None,
        lane: str | None = None,
        actor: str | None = None,
        limit: int = 20,
        order_expr: str,
    ) -> list[dict[str, Any]]:
        clean = query.strip().replace('"', '""')
        if not clean:
            return []

        fts_query = f'"{clean}"'
        if "/" in clean or "." in clean or ":" in clean:
            fts_query = self._expand_path_query(clean)
        if len(clean) >= 4 and re.fullmatch(r"[A-Za-z0-9_]+", clean):
            fts_query = f'{fts_query} OR {clean}*'
        kind_filter = "AND p.kind = ?" if kind else ""
        lane_filter = "AND p.lane = ?" if lane else ""
        actor_filter = "AND p.actor = ?" if actor else ""
        params: list[Any] = [fts_query]
        if kind:
            params.append(kind)
        if lane:
            params.append(lane)
        if actor:
            params.append(actor)
        params.append(limit)

        rows = self._conn.execute(
            f"""
            SELECT p.page_id, p.title, p.kind, p.visibility, p.status, p.summary, p.tags, p.sources, p.updated_at,
                   {order_expr} AS rank_value,
                   snippet(pages_fts, 4, '**', '**', '...', 32) AS body_snippet,
                   p.body
            FROM pages_fts f
            JOIN pages p ON p.page_id = f.page_id
            WHERE pages_fts MATCH ? {kind_filter} {lane_filter} {actor_filter}
            ORDER BY rank_value
            LIMIT ?
            """,
            params,
        ).fetchall()

        hits = [
            {
                "page_id": row[0],
                "title": row[1],
                "kind": row[2],
                "visibility": row[3],
                "status": row[4],
                "summary": row[5],
                "tags": row[6].split() if row[6] else [],
                "sources": row[7].split() if row[7] else [],
                "updated_at": row[8],
                "score": -row[9],
                "snippet": row[10] or "",
                "matched_fields": self._compute_matched_fields(
                    query=query,
                    title=row[1] or "",
                    summary=row[5] or "",
                    tags=row[6] or "",
                    body=row[11] or "",
                    sources=row[7] or "",
                ),
            }
            for row in rows
        ]
        return self._boost_exact_matches(query, hits)

    def _expand_path_query(self, query: str) -> str:
        """Expand path-like queries to also match segments."""
        parts = query.replace("/", " ").replace(".", " ").replace(":", " ").split()
        if len(parts) <= 1:
            return query

        expanded = f'"{query}"'
        for part in parts:
            if len(part) >= 2:
                expanded += f" OR {part}"
        return expanded

    def _boost_exact_matches(self, query: str, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Boost hits where query matches page_id, title, or tags exactly."""
        q = query.casefold().strip()
        for hit in hits:
            bonus = 0.0
            page_id = str(hit.get("page_id", "")).casefold()
            title = str(hit.get("title", "")).casefold()
            tags = " ".join(hit.get("tags", [])).casefold()

            if page_id == q or page_id.endswith("/" + q):
                bonus += 10.0
            elif q in page_id.split("/"):
                bonus += 5.0

            if title == q:
                bonus += 8.0
            elif title.startswith(q):
                bonus += 4.0

            if q in tags.split():
                bonus += 3.0

            hit["score"] = hit.get("score", 0) + bonus
            hit["matched_fields"] = hit.get("matched_fields") or []
            if bonus > 0 and "title" not in hit["matched_fields"]:
                hit["matched_fields"].append("title")

        hits.sort(key=lambda h: h.get("score", 0), reverse=True)
        return hits

    def _compute_matched_fields(
        self,
        *,
        query: str,
        title: str,
        summary: str,
        tags: str,
        body: str,
        sources: str,
    ) -> list[str]:
        terms = re.findall(r"[A-Za-z0-9_]+", query.casefold())
        if not terms:
            terms = query.casefold().split()

        fields = {
            "title": title.casefold(),
            "summary": summary.casefold(),
            "tags": tags.casefold(),
            "body": body.casefold(),
            "sources": sources.casefold(),
        }

        return [
            field_name
            for field_name, field_value in fields.items()
            if any(term in field_value for term in terms)
        ]

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def list_lanes(self) -> list[dict[str, Any]]:
        """Return available lanes with page counts."""
        rows = self._conn.execute(
            "SELECT lane, COUNT(*) as cnt FROM pages WHERE lane IS NOT NULL GROUP BY lane ORDER BY cnt DESC"
        ).fetchall()
        return [{"lane": row[0], "count": row[1]} for row in rows]

    def list_actors(self) -> list[dict[str, Any]]:
        """Return known actors with capture/page counts."""
        rows = self._conn.execute(
            "SELECT actor, COUNT(*) as cnt FROM pages WHERE actor IS NOT NULL GROUP BY actor ORDER BY cnt DESC"
        ).fetchall()
        return [{"actor": row[0], "count": row[1]} for row in rows]


def searchable_body(body: str) -> str:
    return re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", body)
