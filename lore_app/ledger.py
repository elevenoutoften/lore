from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from collections.abc import Callable

from .db_utils import retry_on_locked
from .provenance import merge_trace_provenance
from .recall import compute_recall_score
from .schemas import (
    ClaimReinforcementResult,
    ClaimSupersedeResult,
    ConsolidationRunResult,
    ContextRef,
    DecayResult,
    ExtractedClaim,
    ExtractedEdge,
    ExtractedEntity,
    ExtractedInvalidation,
    ExtractionResult,
    ExtractionStatusResponse,
    PatchPlan,
    PolicyRule,
    ToolRef,
    TraceEntry,
)

CONFIDENCE_ORDER = {"unknown": 0, "low": 1, "medium": 2, "high": 3}
MAX_SEMANTIC_CLAIMS_PER_RECALL = 50
SEED_POLICIES = [
    PolicyRule(
        policy_id="auto-apply:v1",
        name="Auto-apply safe patches",
        gate="auto-apply",
        condition_kind=[],
        condition_operation=["insert_new_fact", "append_sourced_paragraph", "create_stub_page"],
        effect_pass="auto-apply",
        effect_fail="review",
        fail_reason_template="Patch for {page_id} requires review: {gate} policy {policy_id} blocked auto-apply.",
        version=1,
        enabled=True,
    ),
    PolicyRule(
        policy_id="protected-surface:v1",
        name="Protect decision and runbook pages",
        gate="protected-surface",
        condition_kind=["decision", "runbook"],
        condition_operation=[],
        effect_pass="allow",
        effect_fail="review",
        fail_reason_template="Protected {kind} page {page_id} requires review before auto-apply.",
        version=1,
        enabled=True,
    ),
    PolicyRule(
        policy_id="contradiction-review:v1",
        name="Require review for contradictions",
        gate="contradiction-review",
        condition_kind=[],
        condition_operation=["update_existing_fact", "mark_stale"],
        effect_pass="allow",
        effect_fail="review",
        fail_reason_template="Patch for {page_id} has contradicting candidates and requires review.",
        version=1,
        enabled=True,
    ),
    PolicyRule(
        policy_id="risk-high:v1",
        name="High risk for protected surfaces and contradictions",
        gate="risk-assessment",
        condition_kind=["decision", "runbook"],
        condition_operation=["update_existing_fact", "mark_stale"],
        effect_pass="allow",
        effect_fail="review",
        fail_reason_template="High risk assessment for {page_id}: {kind} page with {operation} operation.",
        version=1,
        enabled=True,
    ),
]
VALID_TRANSITIONS = {
    "candidate": {"active", "rejected"},
    "active": {"superseded", "archived"},
    "superseded": set(),  # terminal
    "rejected": set(),  # terminal
    "archived": set(),  # terminal
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _normalize(text: str) -> str:
    return " ".join(text.casefold().split())


class LedgerDB:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._tl = threading.local()
        self._conn_lock = threading.Lock()
        self._all_conns: set[sqlite3.Connection] = set()
        self._lock = threading.RLock()
        self._generation: int = 0
        self._semantic_scorer: Callable[[str, list[str]], list[float] | None] | None = None

    @property
    def semantic_recall_enabled(self) -> bool:
        return self._semantic_scorer is not None

    def configure_semantic_scorer(self, scorer: Callable[[str, list[str]], list[float] | None] | None) -> None:
        self._semantic_scorer = scorer

    @property
    def generation(self) -> int:
        """Monotonic counter incremented on every write. Used for cache invalidation."""
        return self._generation

    def _bump_generation(self) -> None:
        self._generation += 1

    @property
    def _connection(self) -> sqlite3.Connection | None:
        """Compatibility shim for callers that probe connection initialization."""
        return getattr(self._tl, "conn", None)

    @property
    def connection(self) -> sqlite3.Connection:
        conn = getattr(self._tl, "conn", None)
        if conn is None:
            with self._conn_lock:
                conn = getattr(self._tl, "conn", None)
                if conn is None:
                    self.db_path.parent.mkdir(parents=True, exist_ok=True)
                    conn = sqlite3.connect(self.db_path, check_same_thread=True)
                    conn.row_factory = sqlite3.Row
                    conn.execute("PRAGMA busy_timeout = 5000")
                    conn.execute("PRAGMA journal_mode = WAL")
                    conn.execute("PRAGMA foreign_keys = ON")
                    self._all_conns.add(conn)
                    self._tl.conn = conn
        return conn

    # Columns added by migrations beyond the original schema.
    _MIGRATION_COLUMNS: ClassVar[dict[str, list[tuple[str, str]]]] = {
        "extraction_deadletters": [
            ("deadletter_id", "TEXT PRIMARY KEY"),
            ("capture_id", "TEXT NOT NULL DEFAULT ''"),
            ("provider", "TEXT NOT NULL DEFAULT ''"),
            ("failure_kind", "TEXT NOT NULL DEFAULT 'llm_error'"),
            ("failure_detail", "TEXT"),
            ("payload", "TEXT"),
            ("batch_id", "TEXT"),
            ("status", "TEXT NOT NULL DEFAULT 'unresolved'"),
            ("resolved_at", "TEXT"),
            ("resolved_by", "TEXT DEFAULT NULL"),
            ("created_at", "TEXT NOT NULL DEFAULT ''"),
            ("attempted_at", "TEXT DEFAULT NULL"),
            ("retry_count", "INTEGER NOT NULL DEFAULT 0"),
            ("last_retry_at", "TEXT DEFAULT NULL"),
        ],
        "extraction_candidates": [
            ("normalized_subject", "TEXT DEFAULT NULL"),
            ("normalized_predicate", "TEXT DEFAULT NULL"),
            ("normalized_object", "TEXT DEFAULT NULL"),
            ("supersedes", "TEXT DEFAULT NULL"),
            ("superseded_by", "TEXT DEFAULT NULL"),
            ("invalidation_reason", "TEXT DEFAULT NULL"),
            ("epistemic_status", "TEXT DEFAULT NULL"),
            ("target_section", "TEXT DEFAULT NULL"),
            ("model_version", "TEXT DEFAULT NULL"),
            ("prompt_hash", "TEXT DEFAULT NULL"),
            ("token_usage", "TEXT DEFAULT NULL"),
            ("last_decayed_at", "TEXT DEFAULT NULL"),
        ],
        "patch_plans": [
            ("batch_id", "TEXT DEFAULT NULL"),
            ("trace_id", "TEXT DEFAULT NULL"),
            ("candidate_ids", "TEXT NOT NULL DEFAULT '[]'"),
            ("target_page_id", "TEXT NOT NULL DEFAULT ''"),
            ("target_section", "TEXT DEFAULT NULL"),
            ("operation", "TEXT NOT NULL DEFAULT 'insert_new_fact'"),
            ("content_diff", "TEXT NOT NULL DEFAULT ''"),
            ("risk_level", "TEXT NOT NULL DEFAULT 'low'"),
            ("auto_appliable", "BOOLEAN NOT NULL DEFAULT 0"),
            ("policies_applied", "TEXT NOT NULL DEFAULT '[]'"),
            ("status", "TEXT NOT NULL DEFAULT 'pending'"),
            ("created_at", "TEXT NOT NULL DEFAULT ''"),
            ("reason", "TEXT DEFAULT NULL"),
            ("applied_at", "TEXT DEFAULT NULL"),
            ("rejected_at", "TEXT DEFAULT NULL"),
            ("rejection_reason", "TEXT DEFAULT NULL"),
        ],
        "consolidation_runs": [
            ("run_id", "TEXT PRIMARY KEY DEFAULT ''"),
            ("batch_id", "TEXT DEFAULT NULL"),
            ("started_at", "TEXT NOT NULL DEFAULT ''"),
            ("completed_at", "TEXT DEFAULT NULL"),
            ("captures_processed", "INTEGER DEFAULT 0"),
            ("candidates_extracted", "INTEGER DEFAULT 0"),
            ("plans_generated", "INTEGER DEFAULT 0"),
            ("auto_applied", "INTEGER DEFAULT 0"),
            ("review_required", "INTEGER DEFAULT 0"),
            ("errors", "TEXT DEFAULT '[]'"),
            ("dry_run", "BOOLEAN DEFAULT 0"),
            ("status", "TEXT NOT NULL DEFAULT 'running'"),
        ],
        "reasoning_traces": [
            ("trace_id", "TEXT PRIMARY KEY DEFAULT ''"),
            ("parent_trace_id", "TEXT DEFAULT NULL"),
            ("actor", "TEXT NOT NULL DEFAULT ''"),
            ("reason_summary", "TEXT NOT NULL DEFAULT ''"),
            ("lane", "TEXT DEFAULT NULL"),
            ("status", "TEXT NOT NULL DEFAULT 'active'"),
            ("context_refs", "TEXT NOT NULL DEFAULT '[]'"),
            ("tool_refs", "TEXT NOT NULL DEFAULT '[]'"),
            ("constraints", "TEXT NOT NULL DEFAULT '[]'"),
            ("policy_refs", "TEXT NOT NULL DEFAULT '[]'"),
            ("alternatives", "TEXT NOT NULL DEFAULT '[]'"),
            ("provenance", "TEXT NOT NULL DEFAULT '{}'"),
            ("epistemic_status", "TEXT DEFAULT NULL"),
            ("outcome", "TEXT NOT NULL DEFAULT ''"),
            ("related_ids", "TEXT NOT NULL DEFAULT '{}'"),
            ("created_at", "TEXT NOT NULL DEFAULT ''"),
            ("updated_at", "TEXT NOT NULL DEFAULT ''"),
        ],
        "policies": [
            ("policy_id", "TEXT PRIMARY KEY"),
            ("name", "TEXT NOT NULL DEFAULT ''"),
            ("description", "TEXT NOT NULL DEFAULT ''"),
            ("gate", "TEXT NOT NULL DEFAULT ''"),
            ("condition_kind", "TEXT NOT NULL DEFAULT '[]'"),
            ("condition_operation", "TEXT NOT NULL DEFAULT '[]'"),
            ("effect_pass", "TEXT NOT NULL DEFAULT 'allow'"),
            ("effect_fail", "TEXT NOT NULL DEFAULT 'block'"),
            ("fail_reason_template", "TEXT NOT NULL DEFAULT ''"),
            ("version", "INTEGER NOT NULL DEFAULT 1"),
            ("enabled", "INTEGER NOT NULL DEFAULT 1"),
            ("created_at", "TEXT NOT NULL DEFAULT ''"),
            ("updated_at", "TEXT NOT NULL DEFAULT ''"),
        ],
    }

    def initialize(self) -> None:
        with self._lock:
            self.connection.executescript(
                """
            CREATE TABLE IF NOT EXISTS extraction_batches (
                batch_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                total_captures INTEGER NOT NULL,
                total_entities INTEGER NOT NULL,
                total_claims INTEGER NOT NULL,
                total_edges INTEGER NOT NULL,
                total_invalidations INTEGER NOT NULL,
                dry_run BOOLEAN NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS extraction_candidates (
                candidate_id TEXT PRIMARY KEY,
                batch_id TEXT NOT NULL,
                candidate_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'candidate',
                confidence TEXT,
                epistemic_status TEXT,
                actor TEXT,
                lane TEXT,
                content_json TEXT NOT NULL,
                dedupe_hash TEXT NOT NULL,
                source_capture_ids TEXT NOT NULL,
                source_page_ids TEXT DEFAULT '[]',
                target_section TEXT,
                observed_at TEXT,
                valid_from TEXT,
                valid_until TEXT,
                strength REAL DEFAULT 0.5,
                access_count INTEGER DEFAULT 0,
                last_accessed_at TEXT,
                last_decayed_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (batch_id) REFERENCES extraction_batches(batch_id)
            );

            CREATE TABLE IF NOT EXISTS extraction_log (
                log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT NOT NULL,
                capture_id TEXT NOT NULL,
                extracted_at TEXT NOT NULL,
                success BOOLEAN NOT NULL DEFAULT 1,
                error TEXT,
                FOREIGN KEY (batch_id) REFERENCES extraction_batches(batch_id)
            );

            CREATE TABLE IF NOT EXISTS extraction_deadletters (
                deadletter_id TEXT PRIMARY KEY,
                capture_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                failure_kind TEXT NOT NULL,
                failure_detail TEXT,
                payload TEXT,
                batch_id TEXT,
                status TEXT NOT NULL DEFAULT 'unresolved',
                resolved_at TEXT,
                resolved_by TEXT DEFAULT NULL,
                created_at TEXT NOT NULL,
                attempted_at TEXT DEFAULT NULL,
                retry_count INTEGER NOT NULL DEFAULT 0,
                last_retry_at TEXT DEFAULT NULL
            );

            CREATE TABLE IF NOT EXISTS patch_plans (
                plan_id TEXT PRIMARY KEY,
                batch_id TEXT,
                trace_id TEXT DEFAULT NULL,
                candidate_ids TEXT NOT NULL DEFAULT '[]',
                target_page_id TEXT NOT NULL,
                target_section TEXT,
                operation TEXT NOT NULL,
                content_diff TEXT NOT NULL DEFAULT '',
                risk_level TEXT NOT NULL DEFAULT 'low',
                auto_appliable BOOLEAN NOT NULL DEFAULT 0,
                policies_applied TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                reason TEXT,
                applied_at TEXT,
                rejected_at TEXT,
                rejection_reason TEXT
            );

            CREATE TABLE IF NOT EXISTS consolidation_runs (
                run_id TEXT PRIMARY KEY,
                batch_id TEXT,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                captures_processed INTEGER DEFAULT 0,
                candidates_extracted INTEGER DEFAULT 0,
                plans_generated INTEGER DEFAULT 0,
                auto_applied INTEGER DEFAULT 0,
                review_required INTEGER DEFAULT 0,
                errors TEXT DEFAULT '[]',
                dry_run BOOLEAN DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'running'
            );

            CREATE TABLE IF NOT EXISTS reasoning_traces (
                trace_id TEXT PRIMARY KEY DEFAULT '',
                parent_trace_id TEXT DEFAULT NULL,
                actor TEXT NOT NULL DEFAULT '',
                reason_summary TEXT NOT NULL DEFAULT '',
                lane TEXT DEFAULT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                context_refs TEXT NOT NULL DEFAULT '[]',
                tool_refs TEXT NOT NULL DEFAULT '[]',
                constraints TEXT NOT NULL DEFAULT '[]',
                policy_refs TEXT NOT NULL DEFAULT '[]',
                alternatives TEXT NOT NULL DEFAULT '[]',
                provenance TEXT NOT NULL DEFAULT '{}',
                epistemic_status TEXT DEFAULT NULL,
                outcome TEXT NOT NULL DEFAULT '',
                related_ids TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS policies (
                policy_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                gate TEXT NOT NULL,
                condition_kind TEXT NOT NULL DEFAULT '[]',
                condition_operation TEXT NOT NULL DEFAULT '[]',
                effect_pass TEXT NOT NULL DEFAULT 'allow',
                effect_fail TEXT NOT NULL DEFAULT 'block',
                fail_reason_template TEXT NOT NULL DEFAULT '',
                version INTEGER NOT NULL DEFAULT 1,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_traces_actor ON reasoning_traces(actor);
            CREATE INDEX IF NOT EXISTS idx_traces_status ON reasoning_traces(status);
            CREATE INDEX IF NOT EXISTS idx_traces_related_task ON reasoning_traces(related_ids);
            """
            )
            self.connection.commit()
            self._run_migrations()
            self.connection.executescript(
                """
            CREATE INDEX IF NOT EXISTS idx_candidates_type_status_strength
                ON extraction_candidates(candidate_type, status, strength);
            CREATE INDEX IF NOT EXISTS idx_candidates_dedupe_type_status
                ON extraction_candidates(dedupe_hash, candidate_type, status);
            CREATE INDEX IF NOT EXISTS idx_candidates_type_status_subject
                ON extraction_candidates(candidate_type, status, normalized_subject);
            """
            )
            self.connection.commit()
            self._seed_policies()

    def _run_migrations(self) -> None:
        """Add columns from _MIGRATION_COLUMNS that don't yet exist."""
        with self._lock:
            for table, columns in self._MIGRATION_COLUMNS.items():
                existing = {row[1] for row in self.connection.execute(f"PRAGMA table_info({table})").fetchall()}
                for col_name, col_def in columns:
                    if col_name not in existing:
                        with contextlib.suppress(sqlite3.OperationalError):
                            self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}")
                self.connection.commit()

    def _seed_policies(self) -> None:
        existing = {
            str(row["policy_id"]) for row in self.connection.execute("SELECT policy_id FROM policies").fetchall()
        }
        for policy in SEED_POLICIES:
            if policy.policy_id not in existing:
                self.store_policy(policy)

    @retry_on_locked()
    def store_policy(self, policy: PolicyRule) -> PolicyRule:
        with self._lock:
            now = utc_now()
            existing = self.connection.execute(
                "SELECT created_at FROM policies WHERE policy_id = ?",
                (policy.policy_id,),
            ).fetchone()
            created_at = str(existing["created_at"]) if existing is not None and existing["created_at"] else now
            self.connection.execute(
                """
                INSERT OR REPLACE INTO policies (
                    policy_id, name, description, gate, condition_kind, condition_operation,
                    effect_pass, effect_fail, fail_reason_template, version, enabled,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    policy.policy_id,
                    policy.name,
                    policy.description,
                    policy.gate,
                    json.dumps(policy.condition_kind),
                    json.dumps(policy.condition_operation),
                    policy.effect_pass,
                    policy.effect_fail,
                    policy.fail_reason_template,
                    policy.version,
                    int(policy.enabled),
                    created_at,
                    now,
                ),
            )
            self.connection.commit()
            self._bump_generation()
        return policy

    def get_policy(self, policy_id: str) -> PolicyRule | None:
        row = self.connection.execute(
            "SELECT * FROM policies WHERE policy_id = ?",
            (policy_id,),
        ).fetchone()
        return _decode_policy_row(row) if row is not None else None

    def list_policies(self, gate: str | None = None, enabled_only: bool = True) -> list[PolicyRule]:
        clauses: list[str] = []
        params: list[Any] = []
        if gate:
            clauses.append("gate = ?")
            params.append(gate)
        if enabled_only:
            clauses.append("enabled = 1")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.connection.execute(
            f"""
            SELECT *
            FROM policies
            {where}
            ORDER BY gate, policy_id
            """,
            params,
        ).fetchall()
        return [_decode_policy_row(row) for row in rows]

    @retry_on_locked()
    def delete_policy(self, policy_id: str) -> bool:
        with self._lock:
            now = utc_now()
            cursor = self.connection.execute(
                """
                UPDATE policies
                SET enabled = 0, updated_at = ?
                WHERE policy_id = ?
                """,
                (now, policy_id),
            )
            self.connection.commit()
            if cursor.rowcount > 0:
                self._bump_generation()
            return cursor.rowcount > 0

    @retry_on_locked()
    def store_extraction_result(self, result: ExtractionResult) -> None:
        from .extraction import compute_extraction_hash

        now = utc_now()
        with self._lock:
            self.connection.execute(
                """
                INSERT OR REPLACE INTO extraction_batches (
                    batch_id, created_at, total_captures, total_entities,
                    total_claims, total_edges, total_invalidations, dry_run
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    result.batch_id,
                    result.processed_at,
                    len(result.source_capture_ids),
                    len(result.entities),
                    len(result.claims),
                    len(result.edges),
                    len(result.invalidations),
                ),
            )
            for candidate_type, candidate in self._iter_candidates(result):
                source_page_ids = _candidate_source_page_ids(candidate)
                dedupe_hash = _candidate_hash(candidate_type, candidate, source_page_ids, compute_extraction_hash)
                metadata = _candidate_metadata(candidate)

                if candidate_type == "claim" and isinstance(candidate, ExtractedClaim):
                    # Use reinforcement logic for claims
                    self.reinforce_or_insert_candidate(
                        candidate_type=candidate_type,
                        candidate=candidate,
                        dedupe_hash=dedupe_hash,
                        batch_id=result.batch_id,
                        source_capture_ids=result.source_capture_ids,
                        source_page_ids=source_page_ids,
                        metadata=metadata,
                        now=now,
                    )
                else:
                    # Direct insert for non-claim candidates
                    self.connection.execute(
                        """
                        INSERT INTO extraction_candidates (
                            candidate_id, batch_id, candidate_type, status, confidence, epistemic_status,
                            actor, lane, content_json, dedupe_hash, source_capture_ids,
                            source_page_ids, target_section, observed_at, valid_from, valid_until,
                            strength, model_version, prompt_hash, token_usage, created_at, updated_at
                        ) VALUES (?, ?, ?, 'candidate', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(uuid.uuid4()),
                            result.batch_id,
                            candidate_type,
                            metadata.get("confidence"),
                            metadata.get("epistemic_status"),
                            metadata.get("actor"),
                            metadata.get("lane"),
                            candidate.model_dump_json(),
                            dedupe_hash,
                            json.dumps(result.source_capture_ids),
                            json.dumps(source_page_ids),
                            metadata.get("target_section"),
                            metadata.get("observed_at"),
                            metadata.get("valid_from"),
                            metadata.get("valid_until"),
                            metadata.get("strength", 0.5),
                            metadata.get("model_version"),
                            metadata.get("prompt_hash"),
                            json.dumps(metadata["token_usage"]) if metadata.get("token_usage") is not None else None,
                            now,
                            now,
                        ),
                    )
            for capture_id in result.source_capture_ids:
                self.connection.execute(
                    """
                    INSERT INTO extraction_log (batch_id, capture_id, extracted_at, success, error)
                    VALUES (?, ?, ?, 1, NULL)
                    """,
                    (result.batch_id, capture_id, result.processed_at),
                )
            self.connection.commit()
            self._bump_generation()

    def _ensure_batch(self, batch_id: str, now: str) -> None:
        """Create a placeholder batch row if one does not already exist.

        Manual ledger writes (reinforcement, supersede) reference a synthetic
        batch id that has no corresponding extraction_batches row; without this
        the foreign key on extraction_candidates would reject the insert.
        """
        self.connection.execute(
            """
            INSERT OR IGNORE INTO extraction_batches (
                batch_id, created_at, total_captures, total_entities,
                total_claims, total_edges, total_invalidations, dry_run
            ) VALUES (?, ?, 0, 0, 0, 0, 0, 0)
            """,
            (batch_id, now),
        )

    # ─── Reinforcement ────────────────────────────────────────────────────

    @retry_on_locked()
    def reinforce_or_insert_candidate(
        self,
        *,
        candidate_type: str,
        candidate: ExtractedClaim,
        dedupe_hash: str,
        batch_id: str,
        source_capture_ids: list[str],
        source_page_ids: list[str],
        metadata: dict[str, Any],
        now: str | None = None,
    ) -> ClaimReinforcementResult:
        """Reinforce an existing compatible claim or insert a new one.

        Repeated claims with the same dedupe_hash strengthen the existing row
        instead of creating a duplicate.
        """
        with self._lock:
            if now is None:
                now = utc_now()

            # Check for existing compatible claim
            existing = self.connection.execute(
                """
                SELECT candidate_id, strength, confidence, epistemic_status, source_capture_ids,
                       source_page_ids, valid_from, valid_until
                FROM extraction_candidates
                WHERE dedupe_hash = ? AND candidate_type = 'claim'
                  AND status IN ('candidate', 'active')
                  AND actor IS ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (dedupe_hash, metadata.get("actor")),
            ).fetchone()

            if existing is not None:
                # Reinforce existing claim
                existing_id = str(existing["candidate_id"])
                prev_strength = float(existing["strength"])
                new_strength = min(prev_strength + 0.05, 1.0)

                # Merge source IDs
                existing_captures = json.loads(str(existing["source_capture_ids"]))
                existing_pages = json.loads(str(existing["source_page_ids"]))
                merged_captures = list(dict.fromkeys(existing_captures + source_capture_ids))
                merged_pages = list(dict.fromkeys(existing_pages + source_page_ids))

                # Keep higher confidence
                existing_confidence = str(existing["confidence"]) if existing["confidence"] else "unknown"
                new_confidence = metadata.get("confidence", "unknown")
                best_confidence = (
                    new_confidence
                    if CONFIDENCE_ORDER.get(new_confidence, 0) > CONFIDENCE_ORDER.get(existing_confidence, 0)
                    else existing_confidence
                )
                best_epistemic_status = metadata.get("epistemic_status") or (
                    str(existing["epistemic_status"]) if existing["epistemic_status"] else None
                )

                # Keep more specific temporal bounds
                existing_valid_from = (
                    str(existing["valid_from"]) if existing["valid_from"] else metadata.get("valid_from")
                )
                existing_valid_until = (
                    str(existing["valid_until"]) if existing["valid_until"] else metadata.get("valid_until")
                )

                self.connection.execute(
                    """
                    UPDATE extraction_candidates
                    SET strength = ?, confidence = ?, epistemic_status = ?, source_capture_ids = ?,
                        source_page_ids = ?, valid_from = ?, valid_until = ?,
                        target_section = COALESCE(?, target_section),
                        model_version = COALESCE(?, model_version),
                        prompt_hash = COALESCE(?, prompt_hash),
                        token_usage = COALESCE(?, token_usage),
                        updated_at = ?
                    WHERE candidate_id = ?
                    """,
                    (
                        new_strength,
                        best_confidence,
                        best_epistemic_status,
                        json.dumps(merged_captures),
                        json.dumps(merged_pages),
                        existing_valid_from,
                        existing_valid_until,
                        metadata.get("target_section"),
                        metadata.get("model_version"),
                        metadata.get("prompt_hash"),
                        json.dumps(metadata["token_usage"]) if metadata.get("token_usage") is not None else None,
                        now,
                        existing_id,
                    ),
                )
                self.connection.commit()
                self._bump_generation()

                self.store_trace(
                    TraceEntry(
                        trace_id="",
                        actor=str(metadata.get("actor") or "ledger"),
                        reason_summary=f"Corroborated claim {existing_id} from an additional source.",
                        context_refs=[ContextRef(type="candidate", id=existing_id)],
                        tool_refs=[ToolRef(tool="ledger", action="reinforce")],
                        policy_refs=["claim-lifecycle:corroborate-v1:pass"],
                        status="completed",
                        outcome=f"strength={prev_strength:.4f}->{new_strength:.4f}",
                        related_ids={
                            "candidate_id": existing_id,
                            "capture_id": source_capture_ids[0] if source_capture_ids else "",
                        },
                    )
                )

                return ClaimReinforcementResult(
                    candidate_id=existing_id,
                    action="reinforced",
                    previous_strength=prev_strength,
                    new_strength=new_strength,
                    merged_source_capture_ids=merged_captures,
                    merged_source_page_ids=merged_pages,
                )

            # No match -- insert new claim candidate
            candidate_id = str(uuid.uuid4())
            normalized_subject = _normalize(candidate.subject)
            normalized_predicate = _normalize(candidate.predicate)
            normalized_object = _normalize(candidate.object)

            # Ensure the referenced batch exists so manual reinforcement paths
            # (e.g. /api/ledger/reinforce with batch_id="__manual__") do not trip
            # the extraction_candidates -> extraction_batches foreign key.
            self._ensure_batch(batch_id, now)

            self.connection.execute(
                """
                INSERT INTO extraction_candidates (
                    candidate_id, batch_id, candidate_type, status, confidence, epistemic_status,
                    actor, lane, content_json, dedupe_hash, source_capture_ids,
                    source_page_ids, target_section, observed_at, valid_from, valid_until,
                    strength, normalized_subject, normalized_predicate, normalized_object,
                    model_version, prompt_hash, token_usage, created_at, updated_at
                ) VALUES (?, ?, ?, 'candidate', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate_id,
                    batch_id,
                    candidate_type,
                    metadata.get("confidence"),
                    metadata.get("epistemic_status"),
                    metadata.get("actor"),
                    metadata.get("lane"),
                    candidate.model_dump_json(),
                    dedupe_hash,
                    json.dumps(source_capture_ids),
                    json.dumps(source_page_ids),
                    metadata.get("target_section"),
                    metadata.get("observed_at"),
                    metadata.get("valid_from"),
                    metadata.get("valid_until"),
                    metadata.get("strength", 0.5),
                    normalized_subject,
                    normalized_predicate,
                    normalized_object,
                    metadata.get("model_version"),
                    metadata.get("prompt_hash"),
                    json.dumps(metadata["token_usage"]) if metadata.get("token_usage") is not None else None,
                    now,
                    now,
                ),
            )
            self.connection.commit()
            self._bump_generation()

            return ClaimReinforcementResult(
                candidate_id=candidate_id,
                action="inserted",
                previous_strength=None,
                new_strength=metadata.get("strength", 0.5),
                merged_source_capture_ids=source_capture_ids,
                merged_source_page_ids=source_page_ids,
            )

    # ─── Invalidation ─────────────────────────────────────────────────────

    @retry_on_locked()
    def supersede_candidate(
        self,
        old_candidate_id: str,
        new_candidate_id: str,
        reason: str,
        *,
        actor: str | None = None,
    ) -> ClaimSupersedeResult:
        """Mark old claim as superseded by new claim."""
        with self._lock:
            actor_clause = " AND actor = ?" if actor else ""
            old_params: tuple[Any, ...] = (old_candidate_id, actor) if actor else (old_candidate_id,)
            old_row = self.connection.execute(
                f"SELECT status FROM extraction_candidates WHERE candidate_id = ?{actor_clause}",
                old_params,
            ).fetchone()
            if old_row is None:
                raise ValueError(f"Candidate {old_candidate_id} not found")
            old_status = str(old_row["status"])

            # A claim cannot supersede itself. With old == new the two UPDATEs below
            # mark the row 'superseded' (dropping it from recall) and point it at
            # itself as both predecessor and successor — silent memory loss plus a
            # self-referential cycle, all reported as 200 OK.
            if old_candidate_id == new_candidate_id:
                raise ValueError("A claim cannot supersede itself.")

            # Only a live claim can be superseded. Superseding a terminal claim
            # (rejected/archived/already-superseded) would resurrect or overwrite that
            # terminal state and clobber an existing superseded_by edge.
            if old_status not in ("candidate", "active"):
                raise ValueError(f"Candidate {old_candidate_id} cannot be superseded from status {old_status}.")

            # Validate the superseding claim exists BEFORE retiring the old one.
            # Without this, superseding against a non-existent id silently marks the
            # old claim 'superseded' (removing it from recall) and points it at a
            # phantom -> durable memory loss with no error.
            new_params: tuple[Any, ...] = (new_candidate_id, actor) if actor else (new_candidate_id,)
            new_row = self.connection.execute(
                f"SELECT 1 FROM extraction_candidates WHERE candidate_id = ?{actor_clause}",
                new_params,
            ).fetchone()
            if new_row is None:
                raise ValueError(f"Candidate {new_candidate_id} not found")

            now = utc_now()
            self.connection.execute(
                """
                UPDATE extraction_candidates
                SET status = 'superseded', superseded_by = ?, invalidation_reason = ?,
                    valid_until = ?, updated_at = ?
                WHERE candidate_id = ?
                """,
                (new_candidate_id, reason, now, now, old_candidate_id),
            )
            self.connection.execute(
                """
                UPDATE extraction_candidates
                SET supersedes = ?, updated_at = ?
                WHERE candidate_id = ?
                """,
                (old_candidate_id, now, new_candidate_id),
            )
            self.connection.commit()
            self._bump_generation()
        return ClaimSupersedeResult(
            old_candidate_id=old_candidate_id,
            new_candidate_id=new_candidate_id,
            reason=reason,
            old_status=old_status,
        )

    def find_matching_claim(self, claim: ExtractedClaim) -> dict[str, Any] | None:
        """Return the live candidate row for an extracted claim's normalized triple."""
        with self._lock:
            row = self.connection.execute(
                """
                SELECT * FROM extraction_candidates
                WHERE candidate_type = 'claim'
                  AND status IN ('candidate', 'active')
                  AND normalized_subject = ?
                  AND normalized_predicate = ?
                  AND normalized_object = ?
                  AND actor IS ?
                ORDER BY updated_at DESC, candidate_id
                LIMIT 1
                """,
                (
                    _normalize(claim.subject),
                    _normalize(claim.predicate),
                    _normalize(claim.object),
                    claim.actor,
                ),
            ).fetchone()
        return _decode_row(row) if row is not None else None

    def find_contradicting_claims(self, new_claim: ExtractedClaim) -> list[dict[str, Any]]:
        """Find existing claims that contradict a new claim.

        Contradiction: same normalized subject + same normalized predicate,
        but different normalized object.
        """
        norm_subj = _normalize(new_claim.subject)
        norm_pred = _normalize(new_claim.predicate)
        norm_obj = _normalize(new_claim.object)
        new_valid_from = _canonical_utc(new_claim.valid_from or new_claim.observed_at or utc_now())
        new_valid_until = _canonical_utc(new_claim.valid_until)

        with self._lock:
            rows = self.connection.execute(
                """
                SELECT * FROM extraction_candidates
                WHERE candidate_type = 'claim'
                  AND status IN ('candidate', 'active')
                  AND normalized_subject = ?
                  AND normalized_predicate = ?
                  AND normalized_object != ?
                  AND (valid_until IS NULL OR datetime(valid_until) > datetime(?))
                  AND (? IS NULL OR valid_from IS NULL OR datetime(valid_from) < datetime(?))
                ORDER BY strength DESC
                """,
                (norm_subj, norm_pred, norm_obj, new_valid_from, new_valid_until, new_valid_until),
            ).fetchall()
        decoded = [_decode_row(row) for row in rows]
        if new_claim.actor:
            decoded = [row for row in decoded if row.get("actor") == new_claim.actor]
        return decoded

    # ─── Status lifecycle ──────────────────────────────────────────────────

    def activate_candidate(self, candidate_id: str, *, actor: str | None = None) -> None:
        """Transition candidate → active."""
        self._transition_status(candidate_id, "active", actor=actor)

    def reject_candidate(self, candidate_id: str, reason: str | None = None, *, actor: str | None = None) -> None:
        """Transition candidate → rejected."""
        self._transition_status(candidate_id, "rejected", reason=reason, actor=actor)

    def archive_candidate(self, candidate_id: str, *, actor: str | None = None) -> None:
        """Transition active → archived."""
        self._transition_status(candidate_id, "archived", actor=actor)

    @retry_on_locked()
    def archive_floored_claims(
        self,
        floor_days: int,
        *,
        now: datetime | None = None,
        actor: str | None = None,
    ) -> list[str]:
        """Archive claims that have remained at the decay floor for the configured window."""
        if floor_days <= 0:
            return []
        now_dt = now or datetime.now(UTC)
        cutoff = (now_dt - timedelta(days=floor_days)).isoformat()
        actor_clause = " AND actor = ?" if actor else ""
        params: tuple[Any, ...] = (cutoff, actor) if actor else (cutoff,)
        with self._lock:
            rows = self.connection.execute(
                f"""
                SELECT candidate_id FROM extraction_candidates
                WHERE candidate_type = 'claim'
                  AND status IN ('candidate', 'active')
                  AND strength <= 0.01
                  AND last_decayed_at IS NOT NULL
                  AND datetime(last_decayed_at) <= datetime(?)
                  {actor_clause}
                ORDER BY candidate_id
                """,
                params,
            ).fetchall()
            candidate_ids = [str(row["candidate_id"]) for row in rows]
            if not candidate_ids:
                return []
            reason = f"Archived after {floor_days} days at the claim decay floor."
            for candidate_id in candidate_ids:
                self.connection.execute(
                    """
                    UPDATE extraction_candidates
                    SET status = 'archived', invalidation_reason = ?, updated_at = ?
                    WHERE candidate_id = ?
                    """,
                    (reason, now_dt.isoformat(), candidate_id),
                )
            self.connection.commit()
            self._bump_generation()
            return candidate_ids

    @retry_on_locked()
    def _transition_status(
        self,
        candidate_id: str,
        new_status: str,
        *,
        reason: str | None = None,
        actor: str | None = None,
    ) -> None:
        with self._lock:
            actor_clause = " AND actor = ?" if actor else ""
            params: tuple[Any, ...] = (candidate_id, actor) if actor else (candidate_id,)
            row = self.connection.execute(
                f"SELECT status FROM extraction_candidates WHERE candidate_id = ?{actor_clause}",
                params,
            ).fetchone()
            if row is None:
                raise ValueError(f"Candidate {candidate_id} not found")
            current = str(row["status"])
            if new_status not in VALID_TRANSITIONS.get(current, set()):
                raise ValueError(f"Invalid status transition: {current} → {new_status}")
            now = utc_now()
            if reason:
                self.connection.execute(
                    """
                    UPDATE extraction_candidates
                    SET status = ?, invalidation_reason = ?, updated_at = ?
                    WHERE candidate_id = ?
                    """,
                    (new_status, reason, now, candidate_id),
                )
            else:
                self.connection.execute(
                    """
                    UPDATE extraction_candidates
                    SET status = ?, updated_at = ?
                    WHERE candidate_id = ?
                    """,
                    (new_status, now, candidate_id),
                )
            self.connection.commit()
            self._bump_generation()

    # ─── Decay ─────────────────────────────────────────────────────────────

    @retry_on_locked()
    def apply_decay(self, days_since_access: int | None = None, *, actor: str | None = None) -> DecayResult:
        """Apply time-based decay to claim strength.

        Decay formula: strength *= 0.995^days
        Floor: 0.01 (never zero — that would be deletion)
        """
        with self._lock:
            actor_clause = " AND actor = ?" if actor else ""
            params: tuple[Any, ...] = (actor,) if actor else ()
            rows = self.connection.execute(
                f"""
                SELECT candidate_id, strength, last_decayed_at,
                       COALESCE(last_decayed_at, created_at) AS decay_anchor
                FROM extraction_candidates
                WHERE status IN ('candidate', 'active')
                  AND candidate_type = 'claim'
                  {actor_clause}
                """,
                params,
            ).fetchall()

            if not rows:
                return DecayResult(decayed_count=0, min_strength=0.0, max_strength=0.0)

            now = datetime.now(UTC)
            decayed_count = 0
            min_strength = 1.0
            max_strength = 0.0

            for row in rows:
                accessed = datetime.fromisoformat(str(row["decay_anchor"]))
                if accessed.tzinfo is None:
                    accessed = accessed.replace(tzinfo=UTC)
                days = days_since_access if days_since_access is not None else max(0, (now - accessed).days)

                old_strength = float(row["strength"])
                new_strength = max(0.01, old_strength * (0.995**days))
                last_decayed_at = (
                    str(row["last_decayed_at"]) if old_strength <= 0.01 and row["last_decayed_at"] else now.isoformat()
                )

                self.connection.execute(
                    """
                    UPDATE extraction_candidates
                    SET strength = ?, last_decayed_at = ?
                    WHERE candidate_id = ?
                    """,
                    (new_strength, last_decayed_at, str(row["candidate_id"])),
                )
                decayed_count += 1
                min_strength = min(min_strength, new_strength)
                max_strength = max(max_strength, new_strength)

            self.connection.commit()
            self._bump_generation()
            return DecayResult(
                decayed_count=decayed_count,
                min_strength=round(min_strength, 4),
                max_strength=round(max_strength, 4),
            )

    # ─── Active claim queries ──────────────────────────────────────────────

    def get_active_claims(
        self,
        subject: str | None = None,
        lane: str | None = None,
        min_strength: float = 0.0,
        valid_at: str | None = None,
        actor: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Query active/candidate claims with filters."""
        clauses: list[str] = [
            "candidate_type = 'claim'",
            "status IN ('candidate', 'active')",
        ]
        params: list[Any] = []

        if subject:
            clauses.append("normalized_subject = ?")
            params.append(_normalize(subject))
        if lane:
            clauses.append("lane = ?")
            params.append(lane)
        if actor:
            clauses.append("actor = ?")
            params.append(actor)
        if min_strength > 0:
            clauses.append("strength >= ?")
            params.append(min_strength)
        if valid_at:
            canonical_valid_at = _canonical_utc(valid_at)
            clauses.append("(valid_from IS NULL OR datetime(valid_from) <= datetime(?))")
            params.append(canonical_valid_at)
            clauses.append("(valid_until IS NULL OR datetime(valid_until) > datetime(?))")
            params.append(canonical_valid_at)

        where = " AND ".join(clauses)
        params.append(max(1, min(limit, 2000)))

        with self._lock:
            rows = self.connection.execute(
                f"""
                SELECT * FROM extraction_candidates
                WHERE {where}
                ORDER BY strength DESC, updated_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [_decode_row(row) for row in rows]

    # ─── Recall (recency/salience-weighted) ────────────────────────────────

    def recall_claims(
        self,
        *,
        query: str | None = None,
        subject: str | None = None,
        lane: str | None = None,
        actor: str | None = None,
        min_strength: float = 0.0,
        valid_at: str | None = None,
        limit: int = 20,
        pool_limit: int = 500,
        record_access: bool = False,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Rank active/candidate claims by recency/salience-weighted recall.

        Claims are scored on strength (reinforce/decay), recency (freshness of the
        update anchor), salience (acknowledged-use frequency), and -- when a
        query is supplied -- lexical relevance plus optional semantic similarity.
        When a query is supplied, the candidate pool widens up to
        ``min(pool_limit * 3, 2000)`` before ranking; semantic scoring is still
        capped to the strongest 50 candidates so one recall cannot synchronously
        re-embed the widened pool. Each returned row carries ``recall_score``, a
        ``recall_signals`` breakdown, and ``age_days``.

        When ``record_access`` is true, the returned claims have their access
        count incremented and ``last_accessed_at`` stamped for explicit salience
        acknowledgement. It does not affect recency or the decay anchor.
        """
        now_dt = now or datetime.now(UTC)
        effective_pool = pool_limit
        if query is not None:
            effective_pool = min(pool_limit * 3, 2000)
        pool = self.get_active_claims(
            subject=subject,
            lane=lane,
            actor=actor,
            min_strength=min_strength,
            valid_at=valid_at or now_dt.isoformat(),
            limit=effective_pool,
        )

        claim_texts = [_claim_text(row) for row in pool]
        semantic_scores: list[float | None] = [None] * len(pool)
        if query and self._semantic_scorer is not None:
            try:
                semantic_limit = min(len(claim_texts), MAX_SEMANTIC_CLAIMS_PER_RECALL)
                raw_scores = self._semantic_scorer(query, claim_texts[:semantic_limit])
                if raw_scores is not None and len(raw_scores) == semantic_limit:
                    semantic_scores[:semantic_limit] = raw_scores
            except Exception:
                pass

        scored: list[dict[str, Any]] = []
        for index, row in enumerate(pool):
            anchor = row.get("updated_at") or row.get("created_at")
            age_days = _age_in_days(anchor, now_dt)
            score = compute_recall_score(
                strength=float(row.get("strength") or 0.0),
                age_days=age_days,
                access_count=int(row.get("access_count") or 0),
                query=query,
                text=claim_texts[index],
                semantic_similarity=semantic_scores[index],
            )
            entry = dict(row)
            entry["recall_score"] = round(score.total, 6)
            entry["recall_signals"] = score.as_dict()
            entry["age_days"] = round(age_days, 4)
            scored.append(entry)

        scored.sort(
            key=lambda item: (item["recall_score"], float(item.get("strength") or 0.0)),
            reverse=True,
        )
        contradiction_groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for entry in scored:
            key = (str(entry.get("normalized_subject") or ""), str(entry.get("normalized_predicate") or ""))
            contradiction_groups.setdefault(key, []).append(entry)
        for group in contradiction_groups.values():
            objects = {str(entry.get("normalized_object") or "") for entry in group}
            if len(group) < 2 or len(objects) < 2:
                continue
            winner = max(group, key=_claim_temporal_key)
            for loser in group:
                if loser is winner or loser.get("normalized_object") == winner.get("normalized_object"):
                    continue
                loser["contradicted_by"] = winner["candidate_id"]
                loser["recall_score"] = round(
                    min(float(loser["recall_score"]), max(0.0, float(winner["recall_score"]) - 0.000001)),
                    6,
                )
        scored.sort(
            key=lambda item: (item["recall_score"], _claim_temporal_key(item), float(item.get("strength") or 0.0)),
            reverse=True,
        )
        top = scored[: max(1, min(limit, 200))]

        if record_access and top:
            self.record_claim_access(
                [str(item["candidate_id"]) for item in top],
                now=now_dt.isoformat(),
                actor=actor,
            )
        return top

    @retry_on_locked()
    def record_claim_access(
        self,
        candidate_ids: list[str],
        *,
        now: str | None = None,
        actor: str | None = None,
    ) -> int:
        """Increment access count and stamp last_accessed_at for recalled claims."""
        ids = [cid for cid in dict.fromkeys(candidate_ids) if cid]
        if not ids:
            return 0
        timestamp = now or utc_now()
        placeholders = ", ".join("?" for _ in ids)
        actor_clause = " AND actor = ?" if actor else ""
        params: tuple[Any, ...] = (timestamp, *ids, actor) if actor else (timestamp, *ids)
        with self._lock:
            cursor = self.connection.execute(
                f"""
                UPDATE extraction_candidates
                SET access_count = COALESCE(access_count, 0) + 1,
                    last_accessed_at = ?
                WHERE candidate_id IN ({placeholders})
                  AND candidate_type = 'claim'
                  {actor_clause}
                """,
                params,
            )
            self.connection.commit()
            if cursor.rowcount > 0:
                self._bump_generation()
        return int(cursor.rowcount)

    # ─── Existing methods ──────────────────────────────────────────────────

    def is_capture_extracted(self, capture_id: str) -> bool:
        with self._lock:
            row = self.connection.execute(
                "SELECT 1 FROM extraction_log WHERE capture_id = ? AND success = 1 LIMIT 1",
                (capture_id,),
            ).fetchone()
        return row is not None

    @retry_on_locked()
    def store_deadletter(
        self,
        capture_id: str,
        provider: str,
        failure_kind: str,
        failure_detail: str | None,
        payload: str | None,
        batch_id: str | None,
    ) -> str:
        deadletter_id = str(uuid.uuid4())
        with self._lock:
            self.connection.execute(
                """
                INSERT INTO extraction_deadletters (
                    deadletter_id, capture_id, provider, failure_kind, failure_detail,
                    payload, batch_id, status, resolved_at, resolved_by, created_at,
                    attempted_at, retry_count, last_retry_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'unresolved', NULL, NULL, ?, NULL, 0, NULL)
                """,
                (
                    deadletter_id,
                    capture_id,
                    provider,
                    failure_kind,
                    failure_detail,
                    payload,
                    batch_id,
                    utc_now(),
                ),
            )
            self.connection.commit()
            self._bump_generation()
        return deadletter_id

    def list_deadletters(
        self,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 500)))
        rows = self.connection.execute(
            f"""
            SELECT *
            FROM extraction_deadletters
            {where}
            ORDER BY created_at ASC, deadletter_id
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def get_deadletter(self, deadletter_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT *
            FROM extraction_deadletters
            WHERE deadletter_id = ?
            """,
            (deadletter_id,),
        ).fetchone()
        return dict(row) if row else None

    @retry_on_locked()
    def resolve_deadletter(self, deadletter_id: str, *, resolved_by: str | None = None) -> bool:
        with self._lock:
            cursor = self.connection.execute(
                """
                UPDATE extraction_deadletters
                SET status = 'resolved', resolved_at = ?, resolved_by = ?
                WHERE deadletter_id = ? AND status != 'resolved'
                """,
                (utc_now(), resolved_by, deadletter_id),
            )
            self.connection.commit()
            if cursor.rowcount > 0:
                self._bump_generation()
            return cursor.rowcount > 0

    @retry_on_locked()
    def increment_retry(self, deadletter_id: str) -> bool:
        """Increment retry metadata for a dead-letter."""
        now = utc_now()
        with self._lock:
            cursor = self.connection.execute(
                """
                UPDATE extraction_deadletters
                SET retry_count = retry_count + 1,
                    last_retry_at = ?,
                    attempted_at = ?,
                    status = CASE
                        WHEN status = 'unresolved' THEN 'retried'
                        ELSE status
                    END
                WHERE deadletter_id = ?
                """,
                (now, now, deadletter_id),
            )
            self.connection.commit()
            if cursor.rowcount > 0:
                self._bump_generation()
            return cursor.rowcount > 0

    @retry_on_locked()
    def reset_extraction(self, capture_ids: list[str] | None = None, *, delete_candidates: bool = False) -> int:
        """Reset extraction state so captures can be re-processed.

        If capture_ids is None, reset all successful extraction records.
        Returns the number of extraction log rows deleted.
        """

        now = utc_now()
        reset_statuses = ("active", "rejected", "superseded", "archived")
        if capture_ids is None:
            with self._lock:
                cursor = self.connection.execute("DELETE FROM extraction_log WHERE success = 1")
                if delete_candidates:
                    self.connection.execute(
                        """
                        DELETE FROM extraction_candidates
                        """,
                    )
                else:
                    self.connection.execute(
                        """
                        UPDATE extraction_candidates
                        SET status = 'candidate',
                            supersedes = NULL,
                            superseded_by = NULL,
                            invalidation_reason = NULL,
                            updated_at = ?
                        WHERE status IN (?, ?, ?, ?)
                        """,
                        (now, *reset_statuses),
                    )
                self.connection.commit()
                if cursor.rowcount > 0:
                    self._bump_generation()
            return int(cursor.rowcount)

        normalized_capture_ids = list(dict.fromkeys(capture_id for capture_id in capture_ids if capture_id))
        if not normalized_capture_ids:
            return 0

        placeholders = ", ".join("?" for _ in normalized_capture_ids)
        with self._lock:
            cursor = self.connection.execute(
                f"DELETE FROM extraction_log WHERE success = 1 AND capture_id IN ({placeholders})",
                normalized_capture_ids,
            )

            if delete_candidates:
                candidate_rows = self.connection.execute(
                    """
                    SELECT candidate_id, source_capture_ids
                    FROM extraction_candidates
                    """
                ).fetchall()
            else:
                candidate_rows = self.connection.execute(
                    """
                    SELECT candidate_id, source_capture_ids
                    FROM extraction_candidates
                    WHERE status IN (?, ?, ?, ?)
                    """,
                    reset_statuses,
                ).fetchall()
            reset_ids: list[str] = []
            shared_candidate_updates: list[tuple[str, list[str]]] = []
            if delete_candidates:
                normalized_capture_ids_set = set(normalized_capture_ids)
                for row in candidate_rows:
                    sources = _parse_source_capture_ids(row["source_capture_ids"])
                    if sources is None:
                        continue
                    retried_sources = [source for source in sources if source in normalized_capture_ids_set]
                    if not retried_sources:
                        continue
                    remaining_sources = [source for source in sources if source not in normalized_capture_ids_set]
                    if remaining_sources:
                        shared_candidate_updates.append((str(row["candidate_id"]), remaining_sources))
                    else:
                        reset_ids.append(str(row["candidate_id"]))
            else:
                reset_ids = [
                    str(row["candidate_id"])
                    for row in candidate_rows
                    if _candidate_has_source_capture(row["source_capture_ids"], normalized_capture_ids)
                ]
            if reset_ids:
                id_placeholders = ", ".join("?" for _ in reset_ids)
                if delete_candidates:
                    self.connection.execute(
                        f"""
                        DELETE FROM extraction_candidates
                        WHERE candidate_id IN ({id_placeholders})
                        """,
                        reset_ids,
                    )
                else:
                    self.connection.execute(
                        f"""
                        UPDATE extraction_candidates
                        SET status = 'candidate',
                            supersedes = NULL,
                            superseded_by = NULL,
                            invalidation_reason = NULL,
                            updated_at = ?
                        WHERE candidate_id IN ({id_placeholders})
                        """,
                        (now, *reset_ids),
                    )
            for candidate_id, source_capture_ids in shared_candidate_updates:
                self.connection.execute(
                    """
                    UPDATE extraction_candidates
                    SET source_capture_ids = ?
                    WHERE candidate_id = ?
                    """,
                    (json.dumps(source_capture_ids), candidate_id),
                )
            self.connection.commit()
            if cursor.rowcount > 0 or reset_ids or shared_candidate_updates:
                self._bump_generation()
        return int(cursor.rowcount)

    def get_unprocessed_capture_ids(self, limit: int = 50) -> list[str]:
        rows = self.connection.execute(
            """
            SELECT DISTINCT capture_id
            FROM extraction_log
            WHERE success = 1
            ORDER BY capture_id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [str(row["capture_id"]) for row in rows]

    def candidate_statuses(self, candidate_ids: list[str]) -> dict[str, str]:
        """Return the current status for each given candidate id (missing ids omitted)."""
        if not candidate_ids:
            return {}
        placeholders = ",".join("?" for _ in candidate_ids)
        rows = self.connection.execute(
            f"SELECT candidate_id, status FROM extraction_candidates WHERE candidate_id IN ({placeholders})",
            list(candidate_ids),
        ).fetchall()
        return {str(row["candidate_id"]): str(row["status"]) for row in rows}

    def get_candidates(
        self,
        candidate_type: str | None = None,
        status: str | None = None,
        capture_id: str | None = None,
        page_id: str | None = None,
        lane: str | None = None,
        actor: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if candidate_type:
            clauses.append("candidate_type = ?")
            params.append(candidate_type)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if capture_id:
            clauses.append("source_capture_ids LIKE ?")
            params.append(f"%{capture_id}%")
        if page_id:
            clauses.append("source_page_ids LIKE ?")
            params.append(f"%{page_id}%")
        if lane:
            clauses.append("lane = ?")
            params.append(lane)
        if actor:
            clauses.append("actor = ?")
            params.append(actor)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 500)))
        rows = self.connection.execute(
            f"""
            SELECT *
            FROM extraction_candidates
            {where}
            ORDER BY created_at DESC, candidate_id
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [_decode_row(row) for row in rows]

    def get_candidate_status_counts(self, candidate_type: str | None = None) -> dict[str, int]:
        params: list[Any] = []
        where = ""
        if candidate_type:
            where = "WHERE candidate_type = ?"
            params.append(candidate_type)
        rows = self.connection.execute(
            f"""
            SELECT status, COUNT(*) AS count
            FROM extraction_candidates
            {where}
            GROUP BY status
            """,
            params,
        ).fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM extraction_batches WHERE batch_id = ?",
            (batch_id,),
        ).fetchone()
        return dict(row) if row else None

    def list_batches(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT *
            FROM extraction_batches
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (max(1, min(limit, 500)),),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_extraction_status(self) -> ExtractionStatusResponse:
        extracted_row = self.connection.execute(
            "SELECT COUNT(DISTINCT capture_id) AS count FROM extraction_log WHERE success = 1"
        ).fetchone()
        last_row = self.connection.execute(
            """
            SELECT batch_id, created_at
            FROM extraction_batches
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
        total_extracted = int(extracted_row["count"] if extracted_row else 0)
        return ExtractionStatusResponse(
            total_draft_captures=0,
            total_extracted=total_extracted,
            total_pending=0,
            last_batch_id=str(last_row["batch_id"]) if last_row else None,
            last_run_at=str(last_row["created_at"]) if last_row else None,
        )

    @retry_on_locked()
    def store_patch_plan(self, plan: PatchPlan, batch_id: str | None = None) -> None:
        with self._lock:
            self.connection.execute(
                """
                INSERT OR REPLACE INTO patch_plans (
                    plan_id, batch_id, trace_id, candidate_ids, target_page_id, target_section,
                    operation, content_diff, risk_level, auto_appliable, policies_applied,
                    status, created_at, reason, applied_at, rejected_at, rejection_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan.plan_id,
                    batch_id,
                    plan.trace_id,
                    json.dumps(plan.candidate_ids),
                    plan.target_page_id,
                    plan.target_section,
                    plan.operation.value,
                    plan.content_diff,
                    plan.risk_level.value,
                    int(plan.auto_appliable),
                    json.dumps([decision.model_dump(mode="json") for decision in plan.policies_applied]),
                    plan.status.value,
                    plan.created_at,
                    plan.reason,
                    plan.applied_at,
                    plan.rejected_at,
                    plan.rejection_reason,
                ),
            )
            self.connection.commit()
            self._bump_generation()

    def get_patch_plan(self, plan_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM patch_plans WHERE plan_id = ?",
            (plan_id,),
        ).fetchone()
        return _decode_row(row) if row is not None else None

    def list_patch_plans(
        self,
        status: str | None = None,
        target_page_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if target_page_id:
            clauses.append("target_page_id = ?")
            params.append(target_page_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 500)))
        rows = self.connection.execute(
            f"""
            SELECT *
            FROM patch_plans
            {where}
            ORDER BY created_at DESC, plan_id
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [_decode_row(row) for row in rows]

    @retry_on_locked()
    def update_plan_status(self, plan_id: str, status: str, **kwargs: Any) -> None:
        with self._lock:
            allowed_fields = {"applied_at", "rejected_at", "rejection_reason", "content_diff"}
            updates = {"status": status}
            for key, value in kwargs.items():
                if key in allowed_fields:
                    updates[key] = value
            if len(updates) == 1 and self.get_patch_plan(plan_id) is None:
                raise ValueError(f"Patch plan {plan_id} not found")
            assignments = ", ".join(f"{column} = ?" for column in updates)
            params = [*list(updates.values()), plan_id]
            cursor = self.connection.execute(
                f"UPDATE patch_plans SET {assignments} WHERE plan_id = ?",
                params,
            )
            if cursor.rowcount == 0:
                raise ValueError(f"Patch plan {plan_id} not found")
            self.connection.commit()
            self._bump_generation()

    @retry_on_locked()
    def store_consolidation_run(
        self,
        result: ConsolidationRunResult,
        status: str = "completed",
    ) -> None:
        with self._lock:
            now = utc_now()
            self.connection.execute(
                """
                INSERT OR REPLACE INTO consolidation_runs (
                    run_id, batch_id, started_at, completed_at, captures_processed,
                    candidates_extracted, plans_generated, auto_applied,
                    review_required, errors, dry_run, status
                ) VALUES (?, ?, COALESCE(
                    (SELECT started_at FROM consolidation_runs WHERE run_id = ?),
                    ?
                ), ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.batch_id,
                    result.batch_id,
                    result.batch_id,
                    now,
                    now if status != "running" else None,
                    result.captures_processed,
                    result.candidates_extracted,
                    result.plans_generated,
                    result.auto_applied,
                    result.review_required,
                    json.dumps(result.errors),
                    int(result.dry_run),
                    status,
                ),
            )
            self.connection.commit()
            self._bump_generation()

    def get_consolidation_status(self) -> dict[str, Any]:
        last_run = self.connection.execute(
            """
            SELECT *
            FROM consolidation_runs
            ORDER BY started_at DESC, run_id DESC
            LIMIT 1
            """
        ).fetchone()
        status_rows = self.connection.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM patch_plans
            GROUP BY status
            """
        ).fetchall()
        cutoff = datetime.now(UTC) - timedelta(hours=1)
        stuck_rows = self.connection.execute(
            """
            SELECT *
            FROM consolidation_runs
            WHERE status = 'running' AND started_at < ?
            ORDER BY started_at ASC
            """,
            (cutoff.isoformat(),),
        ).fetchall()
        return {
            "last_run": _decode_consolidation_run(last_run) if last_run is not None else None,
            "plans_by_status": {str(row["status"]): int(row["count"]) for row in status_rows},
            "stuck_runs": [_decode_consolidation_run(row) for row in stuck_rows],
        }

    @retry_on_locked()
    def store_trace(self, trace: TraceEntry) -> TraceEntry:
        """Insert or update a reasoning trace. Returns the stored trace."""
        with self._lock:
            now = utc_now()
            trace_id = trace.trace_id or f"trace-{uuid.uuid4().hex[:12]}"
            existing = self.connection.execute(
                "SELECT created_at FROM reasoning_traces WHERE trace_id = ?",
                (trace_id,),
            ).fetchone()
            created_at = trace.created_at or (
                str(existing["created_at"]) if existing is not None and existing["created_at"] else now
            )
            updated_at = trace.updated_at or now
            stored = trace.model_copy(
                update={
                    "trace_id": trace_id,
                    "provenance": merge_trace_provenance(trace),
                    "created_at": created_at,
                    "updated_at": updated_at,
                }
            )
            payload = stored.model_dump(mode="json")
            self.connection.execute(
                """
                INSERT OR REPLACE INTO reasoning_traces (
                    trace_id, parent_trace_id, actor, reason_summary, lane, status,
                    context_refs, tool_refs, constraints, policy_refs, alternatives,
                    provenance, epistemic_status, outcome, related_ids, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["trace_id"],
                    payload["parent_trace_id"],
                    payload["actor"],
                    payload["reason_summary"],
                    payload["lane"],
                    payload["status"],
                    json.dumps(payload["context_refs"]),
                    json.dumps(payload["tool_refs"]),
                    json.dumps(payload["constraints"]),
                    json.dumps(payload["policy_refs"]),
                    json.dumps(payload["alternatives"]),
                    json.dumps(payload["provenance"] or {}),
                    payload["epistemic_status"],
                    payload["outcome"],
                    json.dumps(payload["related_ids"]),
                    payload["created_at"],
                    payload["updated_at"],
                ),
            )
            self.connection.commit()
            self._bump_generation()
            return stored

    def get_trace(self, trace_id: str) -> TraceEntry | None:
        """Get a trace by ID."""
        row = self.connection.execute(
            "SELECT * FROM reasoning_traces WHERE trace_id = ?",
            (trace_id,),
        ).fetchone()
        return _decode_trace_row(row) if row is not None else None

    def list_traces(
        self,
        *,
        actor: str | None = None,
        status: str | None = None,
        task_id: str | None = None,
        capture_id: str | None = None,
        page_id: str | None = None,
        candidate_id: str | None = None,
        policy_ref: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TraceEntry]:
        """Query traces by various filters."""
        where, params = _trace_filter_clauses(
            actor=actor,
            status=status,
            task_id=task_id,
            capture_id=capture_id,
            page_id=page_id,
            candidate_id=candidate_id,
            policy_ref=policy_ref,
        )
        params.extend([max(1, min(limit, 500)), max(0, offset)])
        rows = self.connection.execute(
            f"""
            SELECT *
            FROM reasoning_traces
            {where}
            ORDER BY created_at DESC, trace_id
            LIMIT ? OFFSET ?
            """,
            params,
        ).fetchall()
        return [_decode_trace_row(row) for row in rows]

    def count_traces(self, **filters: Any) -> int:
        """Count traces matching filters."""
        where, params = _trace_filter_clauses(
            actor=filters.get("actor"),
            status=filters.get("status"),
            task_id=filters.get("task_id"),
            capture_id=filters.get("capture_id"),
            page_id=filters.get("page_id"),
            candidate_id=filters.get("candidate_id"),
            policy_ref=filters.get("policy_ref"),
        )
        row = self.connection.execute(
            f"SELECT COUNT(*) AS count FROM reasoning_traces {where}",
            params,
        ).fetchone()
        return int(row["count"] if row is not None else 0)

    def close(self) -> None:
        with self._conn_lock:
            for conn in list(self._all_conns):
                try:
                    conn.close()
                except sqlite3.ProgrammingError:
                    pass
            self._all_conns.clear()
            self._tl.conn = None

    def _iter_candidates(self, result: ExtractionResult):
        for entity in result.entities:
            yield "entity", entity
        for claim in result.claims:
            yield "claim", claim
        for edge in result.edges:
            yield "edge", edge
        for invalidation in result.invalidations:
            yield "invalidation", invalidation


def _candidate_hash(candidate_type: str, candidate: Any, source_page_ids: list[str], compute_hash) -> str:
    if isinstance(candidate, ExtractedClaim):
        return compute_hash(candidate.subject, candidate.predicate, candidate.object, source_page_ids)
    payload = candidate.model_dump()
    subject = candidate_type
    predicate = payload.get("relationship_type") or payload.get("entity_type") or payload.get("reason") or "is"
    obj = json.dumps(payload, sort_keys=True)
    return compute_hash(subject, str(predicate), obj, source_page_ids)


def _candidate_metadata(candidate: Any) -> dict[str, Any]:
    if isinstance(candidate, ExtractedClaim):
        return {
            "confidence": candidate.confidence,
            "epistemic_status": candidate.epistemic_status,
            "actor": candidate.actor,
            "lane": candidate.lane,
            "observed_at": candidate.observed_at,
            "valid_from": _canonical_utc(candidate.valid_from),
            "valid_until": _canonical_utc(candidate.valid_until),
            "target_section": candidate.section,
            "model_version": candidate.model_version,
            "prompt_hash": candidate.prompt_hash,
            "token_usage": candidate.token_usage,
        }
    if isinstance(candidate, ExtractedEdge):
        return {"strength": candidate.strength}
    return {}


def _age_in_days(anchor: Any, now_dt: datetime) -> float:
    """Fractional days between ``anchor`` (ISO timestamp) and ``now_dt``."""
    if not anchor:
        return 0.0
    try:
        ts = datetime.fromisoformat(str(anchor))
    except ValueError:
        return 0.0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return max(0.0, (now_dt - ts).total_seconds() / 86400.0)


def _canonical_utc(value: str | None) -> str | None:
    """Normalize ISO dates/timestamps to an offset-aware UTC timestamp."""
    if value is None or not str(value).strip():
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def _claim_temporal_key(row: dict[str, Any]) -> datetime:
    for field in ("valid_from", "observed_at"):
        canonical = _canonical_utc(str(row.get(field) or ""))
        if canonical:
            try:
                return datetime.fromisoformat(canonical)
            except ValueError:
                continue
    return datetime.min.replace(tzinfo=UTC)


def _claim_text(row: dict[str, Any]) -> str:
    """Build the lexical text for a claim row from its content or normalized fields."""
    content = row.get("content_json")
    if isinstance(content, dict):
        parts = [str(content.get(key) or "") for key in ("subject", "predicate", "object", "evidence")]
        text = " ".join(part for part in parts if part).strip()
        if text:
            return text
    parts = [str(row.get(key) or "") for key in ("normalized_subject", "normalized_predicate", "normalized_object")]
    return " ".join(part for part in parts if part)


def _candidate_source_page_ids(candidate: Any) -> list[str]:
    if isinstance(candidate, (ExtractedClaim, ExtractedEdge)):
        return list(candidate.source_page_ids)
    if isinstance(candidate, ExtractedInvalidation):
        return list(candidate.target_page_ids)
    if isinstance(candidate, ExtractedEntity) and candidate.target_page_hint:
        return [candidate.target_page_hint]
    return []


def _decode_row(row: sqlite3.Row) -> dict[str, Any]:
    decoded = dict(row)
    for key in (
        "content_json",
        "source_capture_ids",
        "source_page_ids",
        "candidate_ids",
        "policies_applied",
        "token_usage",
    ):
        if key not in decoded:
            continue
        with contextlib.suppress(TypeError, json.JSONDecodeError):
            decoded[key] = json.loads(str(decoded[key]))
    if "auto_appliable" in decoded:
        decoded["auto_appliable"] = bool(decoded["auto_appliable"])
    if "epistemic_status" in decoded and decoded["epistemic_status"] is not None:
        decoded["epistemic_status"] = str(decoded["epistemic_status"])
    return decoded


def _decode_policy_row(row: sqlite3.Row) -> PolicyRule:
    decoded = dict(row)
    for key in ("condition_kind", "condition_operation"):
        try:
            decoded[key] = json.loads(str(decoded.get(key) or "[]"))
        except (TypeError, json.JSONDecodeError):
            decoded[key] = []
    decoded["enabled"] = bool(decoded.get("enabled"))
    return PolicyRule(
        policy_id=str(decoded["policy_id"]),
        name=str(decoded["name"]),
        description=str(decoded.get("description") or ""),
        gate=str(decoded["gate"]),
        condition_kind=decoded["condition_kind"],
        condition_operation=decoded["condition_operation"],
        effect_pass=str(decoded.get("effect_pass") or "allow"),
        effect_fail=str(decoded.get("effect_fail") or "block"),
        fail_reason_template=str(decoded.get("fail_reason_template") or ""),
        version=int(decoded.get("version") or 1),
        enabled=bool(decoded.get("enabled")),
    )


def _candidate_has_source_capture(source_capture_ids: Any, capture_ids: list[str]) -> bool:
    try:
        sources = json.loads(str(source_capture_ids))
    except (TypeError, json.JSONDecodeError):
        return False
    if not isinstance(sources, list):
        return False
    requested = set(capture_ids)
    return any(isinstance(source, str) and source in requested for source in sources)


def _parse_source_capture_ids(source_capture_ids: Any) -> list[str] | None:
    try:
        sources = json.loads(str(source_capture_ids))
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(sources, list):
        return None
    return [source for source in sources if isinstance(source, str)]


def _decode_consolidation_run(row: sqlite3.Row) -> dict[str, Any]:
    decoded = dict(row)
    try:
        decoded["errors"] = json.loads(str(decoded.get("errors") or "[]"))
    except json.JSONDecodeError:
        decoded["errors"] = []
    decoded["dry_run"] = bool(decoded.get("dry_run"))
    return decoded


def _trace_filter_clauses(
    *,
    actor: str | None = None,
    status: str | None = None,
    task_id: str | None = None,
    capture_id: str | None = None,
    page_id: str | None = None,
    candidate_id: str | None = None,
    policy_ref: str | None = None,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if actor:
        clauses.append("actor = ?")
        params.append(actor)
    if status:
        clauses.append("status = ?")
        params.append(status)
    for key, value in (
        ("task_id", task_id),
        ("capture_id", capture_id),
        ("page_id", page_id),
        ("candidate_id", candidate_id),
    ):
        if value:
            clauses.append(f"json_extract(related_ids, '$.{key}') = ?")
            params.append(value)
    if policy_ref:
        clauses.append("EXISTS (SELECT 1 FROM json_each(reasoning_traces.policy_refs) WHERE value = ?)")
        params.append(policy_ref)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


def _decode_trace_row(row: sqlite3.Row) -> TraceEntry:
    decoded = dict(row)
    for key, fallback in (
        ("context_refs", []),
        ("tool_refs", []),
        ("constraints", []),
        ("policy_refs", []),
        ("alternatives", []),
        ("provenance", {}),
        ("related_ids", {}),
    ):
        try:
            decoded[key] = json.loads(str(decoded.get(key) or json.dumps(fallback)))
        except (TypeError, json.JSONDecodeError):
            decoded[key] = fallback
    return TraceEntry(**decoded)
