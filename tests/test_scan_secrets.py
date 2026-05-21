from __future__ import annotations

from scripts.scan_secrets import BaselineRule, FORBIDDEN_FILE_RE, scan_content, should_skip_path


def test_scan_secrets_detects_bearer_token():
    """Scanner catches a hardcoded bearer token."""
    findings = scan_content("test.py", 'BEARER_TOKEN = "sk-abc123def456"')
    assert any("BEARER_TOKEN" in f.pattern or "sk-abc" in f.match for f in findings)


def test_scan_secrets_ignores_placeholder():
    """Scanner ignores placeholder secrets."""
    findings = scan_content("test.py", 'LORE_AUTH_SECRET = "changeme"')
    assert len(findings) == 0


def test_scan_secrets_ignores_env_var():
    """Scanner ignores environment variable references."""
    findings = scan_content("test.py", 'secret = os.environ.get("LORE_AUTH_SECRET", "")')
    assert len(findings) == 0


def test_scan_secrets_respects_baseline_rule():
    findings = scan_content(
        "docs/example.md",
        'LORE_AUTH_SECRET = "real-secret"',
        baseline=[BaselineRule(path="docs/example.md", pattern="real-secret")],
    )
    assert findings == []


def test_scan_secrets_detects_private_key_block():
    """Scanner catches a BEGIN PRIVATE KEY block in text."""
    findings = scan_content("config.yml", "-----BEGIN PRIVATE KEY-----\nMIIEvgIBADANBgkq")
    assert any(f.pattern == "private key block" for f in findings)


def test_scan_secrets_detects_rsa_private_key_block():
    """Scanner catches a BEGIN RSA PRIVATE KEY block."""
    findings = scan_content("config.yml", "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA")
    assert any(f.pattern == "private key block" for f in findings)


def test_scan_secrets_flags_tracked_pem_file():
    """Scanner treats .pem files as forbidden tracked files instead of skipped binaries."""
    assert FORBIDDEN_FILE_RE.search("secrets/server.pem")
    assert should_skip_path("secrets/server.pem") is False


def test_scan_secrets_detects_env_file():
    """Scanner flags .env files as forbidden tracked files."""
    assert FORBIDDEN_FILE_RE.search(".env")
    assert FORBIDDEN_FILE_RE.search("config/.env")
    assert should_skip_path(".env") is False
