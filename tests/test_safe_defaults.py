from __future__ import annotations

from pathlib import Path

from lore_app.mcp.tools import TOOLS, call_tool
from lore_app.schemas import ConsolidationRunRequest, ConsolidationRunResult


class DummyRepo:
    pass


class RecordingWorker:
    def __init__(self) -> None:
        self.calls: list[dict[str, int | bool]] = []

    def run(
        self,
        *,
        dry_run: bool,
        batch_size: int,
        max_auto_apply: int,
        force_reextract: bool = False,
    ) -> ConsolidationRunResult:
        self.calls.append(
            {
                "dry_run": dry_run,
                "batch_size": batch_size,
                "max_auto_apply": max_auto_apply,
                "force_reextract": force_reextract,
            }
        )
        return ConsolidationRunResult(
            batch_id="batch-safe-defaults",
            captures_processed=0,
            candidates_extracted=0,
            plans_generated=0,
            auto_applied=0,
            review_required=0,
            errors=[],
            dry_run=dry_run,
        )


def test_consolidation_run_request_defaults_are_safe():
    payload = ConsolidationRunRequest()

    assert payload.dry_run is True
    assert payload.max_auto_apply == 0
    assert payload.force_reextract is False


def test_consolidation_run_request_allows_explicit_overrides():
    payload = ConsolidationRunRequest(dry_run=False, max_auto_apply=10)

    assert payload.dry_run is False
    assert payload.max_auto_apply == 10


def test_lore_consolidation_run_tool_uses_safe_defaults_without_arguments():
    worker = RecordingWorker()

    result = call_tool(
        DummyRepo(),
        {"name": "lore_consolidation_run", "arguments": {}},
        consolidation_worker=worker,
    )

    assert worker.calls == [{"dry_run": True, "batch_size": 10, "max_auto_apply": 0, "force_reextract": False}]
    assert result["structuredContent"]["dry_run"] is True


def test_lore_consolidation_run_tool_schema_defaults_are_safe():
    tool = next(tool for tool in TOOLS if tool["name"] == "lore_consolidation_run")
    properties = tool["inputSchema"]["properties"]

    assert properties["dry_run"]["default"] is True
    assert properties["max_auto_apply"]["default"] == 0
    assert properties["force_reextract"]["default"] is False


def test_lore_service_sets_ledger_db():
    service_file = Path(__file__).resolve().parent.parent / "deploy" / "axis-lore.service"
    content = service_file.read_text(encoding="utf-8")

    assert "LORE_LEDGER_DB=/data/db/ledger.db" in content
