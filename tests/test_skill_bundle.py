from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "skill"
SKILL_MD = SKILL_DIR / "SKILL.md"
WRAPPER = SKILL_DIR / "scripts" / "lore.py"
WINDOWS_LAUNCHER = SKILL_DIR / "bin" / "lore.cmd"
POSIX_LAUNCHER = SKILL_DIR / "bin" / "lore"
SDK_ROOT = ROOT / "sdk" / "python"


def _load_wrapper_module():
    spec = importlib.util.spec_from_file_location("skill_lore_wrapper", WRAPPER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_skill_bundle_files_exist():
    assert SKILL_MD.is_file()
    assert WRAPPER.is_file()
    assert WINDOWS_LAUNCHER.is_file()
    assert POSIX_LAUNCHER.is_file()


def test_skill_markdown_leads_with_six_verbs_and_correct_key_guidance():
    text = SKILL_MD.read_text(encoding="utf-8")
    assert "`capture` `recall` `ack` `search` `read` `write`" in text
    assert "LORE_BASE_URL" in text
    assert "LORE_API_KEY" in text
    assert "Flow API keys do not work." in text
    assert "reuses Flow's API keys" not in text
    assert "Authenticated identity scopes capture and recall" in text
    assert len(text.splitlines()) <= 24


def test_skill_wrapper_targets_repo_sdk_checkout():
    module = _load_wrapper_module()
    assert module.bundled_sdk_root(WRAPPER) == SDK_ROOT
    assert (module.bundled_sdk_root(WRAPPER) / "lore_sdk" / "cli.py").is_file()


def test_skill_wrapper_help_runs_the_sdk_cli():
    env = os.environ.copy()
    env.setdefault("LORE_BASE_URL", "https://lore.axis.love")
    env.setdefault("LORE_API_KEY", "lore_test")
    result = subprocess.run(
        [sys.executable, str(WRAPPER), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "capture" in result.stdout
    assert "recall" in result.stdout
    assert "write" in result.stdout
