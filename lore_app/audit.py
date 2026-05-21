from __future__ import annotations

import gzip
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


@dataclass
class AuditEntry:
    timestamp: str
    actor: str
    operation: str
    page_id: str
    summary: str
    commit_ref: str | None = None
    diff_size: int | None = None


class AuditLog:
    def __init__(self, log_dir: Path, retention_days: int = 365):
        self.log_dir = log_dir
        self.retention_days = retention_days
        self._last_compacted_on: date | None = None
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def record(self, entry: AuditEntry) -> None:
        log_path = self.log_dir / f"{entry.timestamp[:10]}.jsonl"
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(entry), sort_keys=True) + "\n")
        self.compact_old_logs()

    def query(
        self,
        page_id: str | None = None,
        actor: str | None = None,
        since: str | None = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        entries: list[AuditEntry] = []
        since_dt = parse_timestamp(since) if since else None
        for path in sorted(self.log_dir.glob("*.jsonl*"), reverse=True):
            for line in reversed(self._read_lines(path)):
                if not line.strip():
                    continue
                entry = AuditEntry(**json.loads(line))
                if page_id and entry.page_id != page_id:
                    continue
                if actor and entry.actor != actor:
                    continue
                if since_dt and parse_timestamp(entry.timestamp) < since_dt:
                    continue
                entries.append(entry)
                if len(entries) >= limit:
                    return entries
        return entries

    def page_history(self, page_id: str) -> list[AuditEntry]:
        return self.query(page_id=page_id, limit=10_000)

    def compact_old_logs(self) -> None:
        today = datetime.now(timezone.utc).date()
        if self._last_compacted_on == today:
            return
        self._last_compacted_on = today
        cutoff = today - timedelta(days=self.retention_days)
        for path in self.log_dir.glob("*.jsonl"):
            log_date = log_date_from_path(path)
            if log_date is None or log_date >= cutoff:
                continue
            gz_path = path.with_suffix(path.suffix + ".gz")
            if gz_path.exists():
                path.unlink()
                continue
            with path.open("rb") as source, gzip.open(gz_path, "wb") as target:
                target.writelines(source)
            path.unlink()

    def _read_lines(self, path: Path) -> list[str]:
        if path.suffix == ".gz":
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                return handle.read().splitlines()
        return path.read_text(encoding="utf-8").splitlines()


def new_audit_entry(
    *,
    actor: str,
    operation: str,
    page_id: str,
    summary: str,
    commit_ref: str | None = None,
    diff_size: int | None = None,
) -> AuditEntry:
    return AuditEntry(
        timestamp=datetime.now(timezone.utc).isoformat(),
        actor=actor,
        operation=operation,
        page_id=page_id,
        summary=summary,
        commit_ref=commit_ref,
        diff_size=diff_size,
    )


def parse_timestamp(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def log_date_from_path(path: Path) -> date | None:
    name = path.name
    if name.endswith(".jsonl.gz"):
        raw_date = name[: -len(".jsonl.gz")]
    elif name.endswith(".jsonl"):
        raw_date = name[: -len(".jsonl")]
    else:
        return None
    try:
        return date.fromisoformat(raw_date)
    except ValueError:
        return None
