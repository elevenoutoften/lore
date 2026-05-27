"""Lint configuration: rule enablement, severity overrides, and suppressions."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class LintConfig:
    """Load and apply lint configuration from a JSON file."""

    def __init__(self, config_path: Path | None = None):
        self.enabled_rules: set[str] | None = None
        self.severity_overrides: dict[str, str] = {}
        self.suppressions: dict[str, dict[str, str]] = {}
        self.stale_threshold_days: int = 90
        self.allowed_missing_fields: set[str] = set()

        if config_path and config_path.exists():
            self._load(config_path)

    def _load(self, path: Path) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))

        if "enabled_rules" in data:
            self.enabled_rules = set(data["enabled_rules"])

        self.severity_overrides = data.get("severity_overrides", {})

        for page_id, rules in data.get("suppressions", {}).items():
            if isinstance(rules, dict):
                self.suppressions[page_id] = rules
            elif isinstance(rules, list):
                self.suppressions[page_id] = {rule: "suppressed" for rule in rules}

        self.stale_threshold_days = data.get("stale_threshold_days", 90)
        self.allowed_missing_fields = set(data.get("allowed_missing_fields", []))

    def is_rule_enabled(self, rule: str) -> bool:
        if self.enabled_rules is None:
            return True
        return rule in self.enabled_rules

    def get_severity(self, rule: str, default: str) -> str:
        return self.severity_overrides.get(rule, default)

    def is_suppressed(self, page_id: str, rule: str) -> bool:
        return rule in self.suppressions.get(page_id, {})

    def suppression_reason(self, page_id: str, rule: str) -> str | None:
        return self.suppressions.get(page_id, {}).get(rule)

    def is_field_allowed_missing(self, field: str) -> bool:
        return field in self.allowed_missing_fields
