from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASELINE = REPO_ROOT / "scripts" / "scan_secrets_baseline.txt"
SELF_PATH = "scripts/scan_secrets.py"
BASELINE_PATH = "scripts/scan_secrets_baseline.txt"
APPROVED_GIT_EMAIL = "nyx@axis.love"

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}\b")
PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d{1,2}[-. ]*)?(?:\(?\d{3}\)?[-. ]*)\d{3}[-. ]\d{4}(?!\w)")
SOCIAL_HANDLE_RE = re.compile(r"(?<![\w/])@[A-Za-z0-9_]{3,}")
INTERNAL_CODE_NAME_RE = re.compile(r"\b(?:GPUBox|ExampleProject|Example Project|gpubox)\b", re.IGNORECASE)
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b([A-Za-z0-9_]*(?:^|_)(?:API_KEY|SECRET|TOKEN|PASSWORD|BEARER)(?:$|_)[A-Za-z0-9_]*)\b\s*([=:])\s*(.+)"
)
PEM_KEY_RE = re.compile(
    r"-----BEGIN\s+(?:RSA\s+PRIVATE\s+KEY|EC\s+PRIVATE\s+KEY|OPENSSH\s+PRIVATE\s+KEY|PRIVATE\s+KEY)-----"
)
IPV4_RE = re.compile(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b")
URL_RE = re.compile(r"\b(https?://[^\s<>'\"]+)", re.IGNORECASE)

CODE_PATTERNS = (
    re.compile(r"^\s*(?:from|import)\b"),
    re.compile(r"^\s*[A-Za-z_][A-Za-z0-9_, ]*\s*:\s*[\w\[\], .|]+\s*$"),
)

GIT_GREP_PATTERN_CATEGORIES = {
    "secrets": (
        r"API_KEY|SECRET|TOKEN|PASSWORD|BEARER|BEGIN[[:space:]]+"
        r"(RSA[[:space:]]+PRIVATE[[:space:]]+KEY|EC[[:space:]]+PRIVATE[[:space:]]+KEY|"
        r"OPENSSH[[:space:]]+PRIVATE[[:space:]]+KEY|PRIVATE[[:space:]]+KEY)"
    ),
    "personal": (
        r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+\.)+[A-Za-z]{2,}|"
        r"copyright|[0-9]{3}[-. ][0-9]{3}[-. ][0-9]{4}|"
        r"GPUBox|ExampleProject|Example Project|gpubox"
    ),
    "endpoints": r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}|https?://",
    "code": r"^[[:space:]]*(from|import)[[:space:]]|@[A-Za-z0-9_]{3,}",
}

BINARY_SUFFIXES = {
    ".db",
    ".dll",
    ".dylib",
    ".eot",
    ".gif",
    ".gz",
    ".ico",
    ".jpg",
    ".jpeg",
    ".otf",
    ".pdf",
    ".png",
    ".pyc",
    ".so",
    ".svgz",
    ".ttf",
    ".woff",
    ".woff2",
    ".zip",
}

FORBIDDEN_FILE_RE = re.compile(r"(^|/)(?:\.env|auth\.json|credentials\.json|[^/]+\.(?:pem|key))$", re.IGNORECASE)
SAFE_EMAIL_DOMAINS = {"example.com", "example.org", "example.net", "localhost"}
SAFE_EMAIL_ADDRESSES = {"git@github.com"}
SAFE_IPS = {"127.0.0.1", "0.0.0.0"}
SAFE_URL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "example.com", "example.org", "example.net"}
SAFE_URL_SUFFIXES = (".test", ".example", ".example.com", ".example.org", ".invalid")
PLACEHOLDER_VALUES = {
    "",
    "changeme",
    "change-me",
    "your-secret-here",
    "todo",
    "redacted",
    "placeholder",
    "example",
    "example-token",
    "example-key",
    "example-secret",
    "your-token",
    "none",
    "null",
}


@dataclass(frozen=True)
class Finding:
    path: str
    line_number: int
    pattern: str
    match: str
    line: str
    revision: str | None = None


@dataclass(frozen=True)
class BaselineRule:
    path: str | None = None
    pattern: str | None = None

    def matches(self, finding: Finding) -> bool:
        if self.path:
            if self.path.endswith("/"):
                if not finding.path.startswith(self.path):
                    return False
            elif finding.path != self.path:
                return False
        if self.pattern:
            haystacks = (finding.pattern, finding.match, finding.line)
            return any(self.pattern in value for value in haystacks)
        return True


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan tracked files for secret and privacy leaks.")
    parser.add_argument("--quiet", action="store_true", help="Only print a summary line.")
    parser.add_argument(
        "--all-revisions",
        action="store_true",
        help="Scan all git revisions and git author/committer emails.",
    )
    parser.add_argument(
        "--baseline",
        nargs="?",
        const=str(DEFAULT_BASELINE),
        default=str(DEFAULT_BASELINE) if DEFAULT_BASELINE.exists() else None,
        help="Use a baseline allowlist file. Defaults to scripts/scan_secrets_baseline.txt when present.",
    )
    return parser.parse_args(argv)


def is_binary_path(path: str) -> bool:
    return Path(path).suffix.lower() in BINARY_SUFFIXES


def should_skip_path(path: str) -> bool:
    if path == SELF_PATH:
        return True
    if path == BASELINE_PATH:
        return True
    if FORBIDDEN_FILE_RE.search(path):
        return False
    return is_binary_path(path)


def is_allowlisted(finding: Finding, baseline: list[BaselineRule] | None) -> bool:
    return bool(baseline and any(rule.matches(finding) for rule in baseline))


def iter_tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line and not should_skip_path(line)]


def load_baseline(path: str | None) -> list[BaselineRule]:
    if not path:
        return []

    baseline_path = Path(path)
    if not baseline_path.is_absolute():
        baseline_path = REPO_ROOT / baseline_path
    if not baseline_path.exists():
        raise FileNotFoundError(f"Baseline file not found: {baseline_path}")

    rules: list[BaselineRule] = []
    for raw_line in baseline_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            candidate_path, pattern = line.split(":", 1)
            if "/" in candidate_path or candidate_path.endswith((".py", ".md", ".json", ".service", ".txt", ".sh")):
                rules.append(BaselineRule(path=candidate_path, pattern=pattern))
                continue
        if "/" in line or line.endswith("/"):
            rules.append(BaselineRule(path=line))
        else:
            rules.append(BaselineRule(pattern=line))
    return rules


def is_placeholder_value(value: str) -> bool:
    cleaned = value.strip().rstrip(",\\")
    cleaned = cleaned.split("#", 1)[0].strip()
    if cleaned.startswith(("f'", 'f"', "r'", 'r"', "b'", 'b"')):
        cleaned = cleaned[1:]
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {'"', "'"}:
        cleaned = cleaned[1:-1].strip()

    lowered = cleaned.lower()
    if lowered in PLACEHOLDER_VALUES:
        return True
    if not cleaned:
        return True
    if cleaned.startswith(("$", "${", "<")):
        return True
    if "os.environ" in cleaned or "getenv(" in cleaned or "environ.get(" in cleaned:
        return True
    return False


def extract_secret_candidate(value: str) -> str | None:
    cleaned = value.strip().rstrip(",\\")
    cleaned = cleaned.split("#", 1)[0].strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {'"', "'"}:
        return cleaned[1:-1].strip()
    if re.fullmatch(r"[A-Za-z0-9._~+=-]{8,}", cleaned):
        return cleaned
    return None


def looks_like_type_annotation(line: str) -> bool:
    return any(pattern.search(line) for pattern in CODE_PATTERNS)


def is_safe_ip(ip: str) -> bool:
    if ip in SAFE_IPS:
        return True
    try:
        parts = [int(part) for part in ip.split(".")]
    except ValueError:
        return False
    if len(parts) != 4 or any(part < 0 or part > 255 for part in parts):
        return True

    o1, o2 = parts[0], parts[1]
    if o1 == 10:
        return True
    if o1 == 172 and 16 <= o2 <= 31:
        return True
    if o1 == 192 and o2 == 168:
        return True
    if o1 == 127:
        return True
    if o1 == 169 and o2 == 254:
        return True
    return False


def is_safe_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    hostname = (parsed.hostname or "").lower()
    return (
        hostname in SAFE_URL_HOSTS
        or any(hostname.endswith(suffix) for suffix in SAFE_URL_SUFFIXES)
    )


def add_finding(
    findings: list[Finding],
    *,
    path: str,
    line_number: int,
    pattern: str,
    match: str,
    line: str,
) -> None:
    findings.append(
        Finding(
            path=path,
            line_number=line_number,
            pattern=pattern,
            match=match,
            line=line.rstrip(),
        )
    )


def scan_content(path: str, content: str, baseline: list[BaselineRule] | None = None) -> list[Finding]:
    findings: list[Finding] = []

    for line_number, line in enumerate(content.splitlines(), start=1):
        if looks_like_type_annotation(line):
            continue

        secret_match = SECRET_ASSIGNMENT_RE.search(line)
        if secret_match:
            variable_name, separator, raw_value = secret_match.groups()
            if separator == ":" and "=" not in line and re.fullmatch(r"[\w\[\], .|]+", raw_value.strip()):
                pass
            else:
                candidate = extract_secret_candidate(raw_value)
                if candidate and not is_placeholder_value(candidate):
                    add_finding(
                        findings,
                        path=path,
                        line_number=line_number,
                        pattern=f"secret assignment: {variable_name}",
                        match=candidate,
                        line=line,
                    )

        if PEM_KEY_RE.search(line):
            add_finding(
                findings,
                path=path,
                line_number=line_number,
                pattern="private key block",
                match=line.strip()[:80],
                line=line,
            )

        if "@" in line:
            for match in EMAIL_RE.finditer(line):
                domain = match.group(0).rsplit("@", 1)[-1].lower()
                if match.group(0).lower() not in SAFE_EMAIL_ADDRESSES and domain not in SAFE_EMAIL_DOMAINS:
                    add_finding(
                        findings,
                        path=path,
                        line_number=line_number,
                        pattern="email address",
                        match=match.group(0),
                        line=line,
                    )

        if "copyright" in line.lower():
            name_match = re.search(r"\b([A-Z][a-z]+ [A-Z][a-z]+)\b", line)
            if name_match:
                add_finding(
                    findings,
                    path=path,
                    line_number=line_number,
                    pattern="copyright full name",
                    match=name_match.group(1),
                    line=line,
                )

        if PHONE_RE.search(line):
            for match in PHONE_RE.finditer(line):
                add_finding(
                    findings,
                    path=path,
                    line_number=line_number,
                    pattern="phone number",
                    match=match.group(0),
                    line=line,
                )

        if "@" in line and not line.lstrip().startswith("@"):
            for match in SOCIAL_HANDLE_RE.finditer(line):
                next_char = line[match.end():match.end() + 1]
                if next_char in {".", "/", "("}:
                    continue
                if match.group(0).lower() == "@media":
                    continue
                add_finding(
                    findings,
                    path=path,
                    line_number=line_number,
                    pattern="social media handle",
                    match=match.group(0),
                    line=line,
                )

        if INTERNAL_CODE_NAME_RE.search(line):
            for match in INTERNAL_CODE_NAME_RE.finditer(line):
                add_finding(
                    findings,
                    path=path,
                    line_number=line_number,
                    pattern="internal code name",
                    match=match.group(0),
                    line=line,
                )

        for match in IPV4_RE.finditer(line):
            ip = match.group(1)
            if not is_safe_ip(ip):
                add_finding(
                    findings,
                    path=path,
                    line_number=line_number,
                    pattern="public IP address",
                    match=ip,
                    line=line,
                )

        for match in URL_RE.finditer(line):
            url = match.group(1).rstrip("`).,;]}")
            if not is_safe_url(url):
                add_finding(
                    findings,
                    path=path,
                    line_number=line_number,
                    pattern="real endpoint URL",
                    match=url,
                    line=line,
                )

    if baseline:
        findings = [finding for finding in findings if not is_allowlisted(finding, baseline)]
    return findings


def read_text_file(path: str) -> str | None:
    abs_path = REPO_ROOT / path
    try:
        raw = abs_path.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="ignore")


def scan_repo(baseline: list[BaselineRule] | None = None) -> list[Finding]:
    findings: list[Finding] = []
    for path in iter_tracked_files():
        if FORBIDDEN_FILE_RE.search(path):
            findings.append(
                Finding(
                    path=path,
                    line_number=1,
                    pattern="forbidden tracked file",
                    match=path,
                    line=path,
                )
            )
            continue
        content = read_text_file(path)
        if content is None:
            continue
        findings.extend(scan_content(path, content, baseline))

    if baseline:
        findings = [finding for finding in findings if not is_allowlisted(finding, baseline)]
    return findings


def git_lines(args: list[str], *, check: bool = True) -> list[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=check,
        capture_output=True,
        text=True,
    )
    return result.stdout.splitlines()


def all_revisions() -> list[str]:
    return git_lines(["rev-list", "--all"])


def scan_forbidden_files_in_revisions(revisions: list[str], baseline: list[BaselineRule] | None) -> list[Finding]:
    findings: list[Finding] = []
    for revision in revisions:
        for path in git_lines(["ls-tree", "-r", "--name-only", revision]):
            if not FORBIDDEN_FILE_RE.search(path):
                continue
            finding = Finding(
                path=path,
                line_number=1,
                pattern="forbidden tracked file",
                match=path,
                line=path,
                revision=revision,
            )
            if not is_allowlisted(finding, baseline):
                findings.append(finding)
    return findings


def scan_git_grep_output(output: str, baseline: list[BaselineRule] | None) -> list[Finding]:
    findings: list[Finding] = []
    seen: set[tuple[str, str, int, str, str]] = set()

    for raw_line in output.splitlines():
        try:
            revision, path, raw_line_number, content = raw_line.split(":", 3)
            line_number = int(raw_line_number)
        except ValueError:
            continue

        if should_skip_path(path):
            continue

        line_findings = scan_content(path, content, baseline=None)
        for finding in line_findings:
            finding = Finding(
                path=finding.path,
                line_number=line_number,
                pattern=finding.pattern,
                match=finding.match,
                line=finding.line,
                revision=revision,
            )
            key = (revision, finding.path, finding.line_number, finding.pattern, finding.match)
            if key in seen or is_allowlisted(finding, baseline):
                continue
            seen.add(key)
            findings.append(finding)

    return findings


def scan_revision_content(revisions: list[str], baseline: list[BaselineRule] | None) -> list[Finding]:
    findings: list[Finding] = []
    if not revisions:
        return findings

    for pattern in GIT_GREP_PATTERN_CATEGORIES.values():
        result = subprocess.run(
            ["git", "grep", "--line-number", "-I", "-E", pattern, *revisions, "--"],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode not in {0, 1}:
            raise RuntimeError(result.stderr.strip() or "git grep failed")
        if result.stdout:
            findings.extend(scan_git_grep_output(result.stdout, baseline))

    deduped: list[Finding] = []
    seen: set[tuple[str | None, str, int, str, str]] = set()
    for finding in findings:
        key = (finding.revision, finding.path, finding.line_number, finding.pattern, finding.match)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(finding)
    return deduped


def scan_git_emails() -> list[Finding]:
    findings: list[Finding] = []
    for label, format_arg in (("author", "%ae"), ("committer", "%ce")):
        for email in sorted(set(git_lines(["log", "--all", f"--format={format_arg}"]))):
            if email == APPROVED_GIT_EMAIL:
                continue
            findings.append(
                Finding(
                    path=f"git-log:{label}-email",
                    line_number=1,
                    pattern="unapproved git email",
                    match=email,
                    line=email,
                )
            )
    return findings


def scan_all_revisions(baseline: list[BaselineRule] | None = None) -> list[Finding]:
    revisions = all_revisions()
    findings = scan_forbidden_files_in_revisions(revisions, baseline)
    findings.extend(scan_revision_content(revisions, baseline))
    findings.extend(scan_git_emails())
    return findings


def print_findings(findings: list[Finding], quiet: bool) -> None:
    if not quiet:
        for finding in findings:
            revision = f"{finding.revision}:" if finding.revision else ""
            print(
                f"{revision}{finding.path}:{finding.line_number}: {finding.pattern} -> {finding.match}",
                file=sys.stderr,
            )
    if findings:
        print(f"scan_secrets: FAIL ({len(findings)} issue(s))", file=sys.stderr)
    else:
        print("scan_secrets: OK (0 issues)", file=sys.stdout)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    baseline = load_baseline(args.baseline)
    findings = scan_all_revisions(baseline) if args.all_revisions else scan_repo(baseline)
    print_findings(findings, args.quiet)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
