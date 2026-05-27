from __future__ import annotations

from scripts.scan_secrets import FORBIDDEN_FILE_RE, BaselineRule, scan_content, should_skip_path


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


def test_scan_secrets_detects_public_ip():
    """Scanner catches public IP addresses."""
    findings = scan_content("x.txt", "server http://203.0.113.10/api and 8.8.8.8")
    ips = [f for f in findings if f.pattern == "public IP address"]
    assert len(ips) >= 1
    assert any(f.match == "203.0.113.10" for f in ips)
    assert any(f.match == "8.8.8.8" for f in ips)


def test_scan_secrets_allows_private_ip():
    """Scanner allows private/local IPs."""
    findings = scan_content("x.txt", "connect to 10.0.0.1 or 192.168.1.1 or 127.0.0.1")
    ips = [f for f in findings if f.pattern == "public IP address"]
    assert len(ips) == 0


def test_scan_secrets_allows_0000_ip():
    """0.0.0.0 is a safe IP."""
    findings = scan_content("x.txt", "bind to 0.0.0.0")
    ips = [f for f in findings if f.pattern == "public IP address"]
    assert len(ips) == 0


def test_scan_secrets_detects_real_endpoint_url():
    """Scanner catches real endpoint URLs."""
    findings = scan_content("x.txt", "fetch('https://api.real-service.io/v1/endpoint')")
    assert any(f.pattern == "real endpoint URL" for f in findings)


def test_scan_secrets_allows_example_urls():
    """Scanner allows example/test URLs."""
    findings = scan_content("x.txt", "see http://example.com/docs and http://localhost:8000")
    urls = [f for f in findings if f.pattern == "real endpoint URL"]
    assert len(urls) == 0


def test_scan_secrets_allows_test_domain_urls():
    """Scanner allows .test and .example TLDs."""
    findings = scan_content("x.txt", "https://myapp.test and https://docs.example")
    urls = [f for f in findings if f.pattern == "real endpoint URL"]
    assert len(urls) == 0
