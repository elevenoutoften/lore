from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .schemas import (
    ClaimReinforcementResult,
    ClaimSupersedeResult,
    ConsolidationRunResult,
    DecayResult,
    ExtractedClaim,
    ExtractedEdge,
    ExtractedEntity,
    ExtractedInvalidation,
    ExtractionResult,
    ExtractionStatusResponse,
    PatchPlan,
    PolicyRule,
    TraceEntry,
)
from .db_utils import retry_on_locked
from .provenance import merge_trace_provenance

CONFIDENCE_ORDER = {"unknown": 0, "low": 1, "medium": 2, "high": 3}
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
    return datetime.now(timezone.utc).isoformat()


def _normalize(text: str) -> str:
    return " ".join(text.casefold().split())


class LedgerDB:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._connection: sqlite3.Connection | None = None
        self._conn_lock = threading.Lock()
        self._lock = threading.RLock()
        self._generation: int = 0

    @property
    def generation(self) -> int:
        """Monotonic counter incremented on every write. Used for cache invalidation."""
        return self._generation

    def _bump_generation(self) -> None:
        self._generation += 1

    @property
    def connection(self) -> sqlite3.Connection:
        with self._conn_lock:
            if self._connection is None:
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
                self._connection = sqlite3.connect(self.db_path, check_same_thread=False)
                self._connection.row_factory = sqlite3.Row
                self._connection.execute("PRAGMA busy_timeout = 5000")
                self._connection.execute("PRAGMA journal_mode = WAL")
                self._connection.execute("PRAGMA foreign_keys = ON")
        return self._connection

    # Columns added by migrations beyond the original schema.
    _MIGRATION_COLUMNS = {
        "extraction_candidates": [
            ("normalized_subject", "TEXT DEFAULT NULL"),
            ("normalized_predicate", "TEXT DEFAULT NULL"),
            ("normalized_object", "TEXT DEFAULT NULL"),
            ("supersedes", "TEXT DEFAULT NULL"),
            ("superseded_by", "TEXT DEFAULT NULL"),
            ("invalidation_reason", "TEXT DEFAULT NULL"),
            ("epistemic_status", "TEXT DEFAULT NULL"),
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
                observed_at TEXT,
                valid_from TEXT,
                valid_until TEXT,
                strength REAL DEFAULT 0.5,
                access_count INTEGER DEFAULT 0,
                last_accessed_at TEXT,
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
            self._seed_policies()

    def _run_migrations(self) -> None:
        """Add columns from _MIGRATION_COLUMNS that don't yet exist."""
        with self._lock:
            for table, columns in self._MIGRATION_COLUMNS.items():
                existing = {
                    row[1]
                    for row in self.connection.execute(f"PRAGMA table_info({table})").fetchall()
                }
                for col_name, col_def in columns:
                    if col_name not in existing:
                        try:
                            self.connection.execute(
                                f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}"
                            )
                        except sqlite3.OperationalError:
                            pass  # Column already exists from concurrent migration
                self.connection.commit()

    def _seed_policies(self) -> None:
        existing = {
            str(row["policy_id"])
            for row in self.connection.execute("SELECT policy_id FROM policies").fetchall()
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
                            source_page_ids, observed_at, valid_from, valid_until,
                            strength, created_at, updated_at
                        ) VALUES (?, ?, ?, 'candidate', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                            metadata.get("observed_at"),
                            metadata.get("valid_from"),
                            metadata.get("valid_until"),
                            metadata.get("strength", 0.5),
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
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (dedupe_hash,),
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
                existing_valid_from = str(existing["valid_from"]) if existing["valid_from"] else metadata.get("valid_from")
                existing_valid_until = str(existing["valid_until"]) if existing["valid_until"] else metadata.get("valid_until")

                self.connection.execute(
                    """
                    UPDATE extraction_candidates
                    SET strength = ?, confidence = ?, epistemic_status = ?, source_capture_ids = ?,
                        source_page_ids = ?, valid_from = ?, valid_until = ?,
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
                        now,
                        existing_id,
                    ),
                )
                self.connection.commit()
                self._bump_generation()

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

            self.connection.execute(
                """
                INSERT INTO extraction_candidates (
                    candidate_id, batch_id, candidate_type, status, confidence, epistemic_status,
                    actor, lane, content_json, dedupe_hash, source_capture_ids,
                    source_page_ids, observed_at, valid_from, valid_until,
                    strength, normalized_subject, normalized_predicate, normalized_object,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'candidate', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    metadata.get("observed_at"),
                    metadata.get("valid_from"),
                    metadata.get("valid_until"),
                    metadata.get("strength", 0.5),
                    normalized_subject,
                    normalized_predicate,
                    normalized_object,
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
        self, old_candidate_id: str, new_candidate_id: str, reason: str
    ) -> ClaimSupersedeResult:
        """Mark old claim as superseded by new claim."""
        with self._lock:
            old_row = self.connection.execute(
                "SELECT status FROM extraction_candidates WHERE candidate_id = ?",
                (old_candidate_id,),
            ).fetchone()
            if old_row is None:
                raise ValueError(f"Candidate {old_candidate_id} not found")
            old_status = str(old_row["status"])

            now = utc_now()
            self.connection.execute(
                """
                UPDATE extraction_candidates
                SET status = 'superseded', superseded_by = ?, invalidation_reason = ?, updated_at = ?
                WHERE candidate_id = ?
                """,
                (new_candidate_id, reason, now, old_candidate_id),
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

    def find_contradicting_claims(self, new_claim: ExtractedClaim) -> list[dict[str, Any]]:
        """Find existing claims that contradict a new claim.

        Contradiction: same normalized subject + same normalized predicate,
        but different normalized object.
        """
        norm_subj = _normalize(new_claim.subject)
        norm_pred = _normalize(new_claim.predicate)
        norm_obj = _normalize(new_claim.object)

        rows = self.connection.execute(
            """
            SELECT * FROM extraction_candidates
            WHERE candidate_type = 'claim'
              AND status IN ('candidate', 'active')
              AND normalized_subject = ?
              AND normalized_predicate = ?
              AND normalized_object != ?
            ORDER BY strength DESC
            """,
            (norm_subj, norm_pred, norm_obj),
        ).fetchall()
        return [_decode_row(row) for row in rows]

    # ─── Status lifecycle ──────────────────────────────────────────────────

    def activate_candidate(self, candidate_id: str) -> None:
        """Transition candidate → active."""
        self._transition_status(candidate_id, "active")

    def reject_candidate(self, candidate_id: str, reason: str | None = None) -> None:
        """Transition candidate → rejected."""
        self._transition_status(candidate_id, "rejected", reason=reason)

    def archive_candidate(self, candidate_id: str) -> None:
        """Transition active → archived."""
        self._transition_status(candidate_id, "archived")

    @retry_on_locked()
    def _transition_status(
        self, candidate_id: str, new_status: str, *, reason: str | None = None
    ) -> None:
        with self._lock:
            row = self.connection.execute(
                "SELECT status FROM extraction_candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Candidate {candidate_id} not found")
            current = str(row["status"])
            if new_status not in VALID_TRANSITIONS.get(current, set()):
                raise ValueError(
                    f"Invalid status transition: {current} → {new_status}"
                )
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
    def apply_decay(self, days_since_access: int | None = None) -> DecayResult:
        """Apply time-based decay to claim strength.

        Decay formula: strength *= 0.995^days
        Floor: 0.01 (never zero — that would be deletion)
        """
        with self._lock:
            rows = self.connection.execute(
                """
                SELECT candidate_id, strength, last_accessed_at
                FROM extraction_candidates
                WHERE status IN ('candidate', 'active')
                  AND last_accessed_at IS NOT NULL
                """
            ).fetchall()

            if not rows:
                return DecayResult(decayed_count=0, min_strength=0.0, max_strength=0.0)

            now = datetime.now(timezone.utc)
            decayed_count = 0
            min_strength = 1.0
            max_strength = 0.0

            for row in rows:
                accessed = datetime.fromisoformat(str(row["last_accessed_at"]))
                if accessed.tzinfo is None:
                    accessed = accessed.replace(tzinfo=timezone.utc)
                if days_since_access is not None:
                    days = days_since_access
                else:
                    days = max(0, (now - accessed).days)

                old_strength = float(row["strength"])
                new_strength = max(0.01, old_strength * (0.995 ** days))

                self.connection.execute(
                    """
                    UPDATE extraction_candidates
                    SET strength = ?, updated_at = ?
                    WHERE candidate_id = ?
                    """,
                    (new_strength, now.isoformat(), str(row["candidate_id"])),
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
        if min_strength > 0:
            clauses.append("strength >= ?")
            params.append(min_strength)
        if valid_at:
            clauses.append("(valid_from IS NULL OR valid_from <= ?)")
            params.append(valid_at)
            clauses.append("(valid_until IS NULL OR valid_until > ?)")
            params.append(valid_at)

        where = " AND ".join(clauses)
        params.append(500)  # limit

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

    # ─── Existing methods ──────────────────────────────────────────────────

    def is_capture_extracted(self, capture_id: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM extraction_log WHERE capture_id = ? AND success = 1 LIMIT 1",
            (capture_id,),
        ).fetchone()
        return row is not None

    @retry_on_locked()
    def reset_extraction(self, capture_ids: list[str] | None = None) -> int:
        """Reset extraction state so captures can be re-processed.

        If capture_ids is None, reset all successful extraction records.
        Returns the number of extraction log rows deleted.
        """

        now = utc_now()
        reset_statuses = ("active", "rejected", "superseded", "archived")
        if capture_ids is None:
            with self._lock:
                cursor = self.connection.execute("DELETE FROM extraction_log WHERE success = 1")
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

            candidate_rows = self.connection.execute(
                """
                SELECT candidate_id, source_capture_ids
                FROM extraction_candidates
                WHERE status IN (?, ?, ?, ?)
                """,
                reset_statuses,
            ).fetchall()
            reset_ids = [
                str(row["candidate_id"])
                for row in candidate_rows
                if _candidate_has_source_capture(row["source_capture_ids"], normalized_capture_ids)
            ]
            if reset_ids:
                id_placeholders = ", ".join("?" for _ in reset_ids)
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
            self.connection.commit()
            if cursor.rowcount > 0 or reset_ids:
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

    def get_candidates(
        self,
        candidate_type: str | None = None,
        status: str | None = None,
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
                    status, created_at, applied_at, rejected_at, rejection_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    plan.status,
                    plan.created_at,
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
            params = list(updates.values()) + [plan_id]
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
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
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
                    trace_id, parent_trace_id, actor, reason_summary, status,
                    context_refs, tool_refs, constraints, policy_refs, alternatives,
                    provenance, epistemic_status, outcome, related_ids, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["trace_id"],
                    payload["parent_trace_id"],
                    payload["actor"],
                    payload["reason_summary"],
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
        if self._connection is not None:
            self._connection.close()
            self._connection = None

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
            "valid_from": candidate.valid_from,
            "valid_until": candidate.valid_until,
        }
    if isinstance(candidate, ExtractedEdge):
        return {"strength": candidate.strength}
    return {}


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
    for key in ("content_json", "source_capture_ids", "source_page_ids", "candidate_ids", "policies_applied"):
        if key not in decoded:
            continue
        try:
            decoded[key] = json.loads(str(decoded[key]))
        except (TypeError, json.JSONDecodeError):
            pass
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
        clauses.append(
            "EXISTS (SELECT 1 FROM json_each(reasoning_traces.policy_refs) WHERE value = ?)"
        )
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
