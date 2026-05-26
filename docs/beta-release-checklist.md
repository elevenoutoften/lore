# Beta Release Checklist

Use this checklist before cutting a Lore beta release. Each item should be
verified against the release candidate build, not only local development code.

## Functionality

- [ ] All REST API endpoints return the documented success and error status
  codes from [API Reference](api-reference.md).
- [ ] Page CRUD works end-to-end: create, read, update, delete, then confirm the
  deleted page is absent from search and navigation.
- [ ] Search returns relevant ranked results for full-text, BM25, and hybrid
  queries using the sample vault and at least one real workspace.
- [ ] RAG retrieval returns grounded chunks from the vector store and includes
  source page identifiers in responses.
- [ ] Capture to promote workflow works: create a capture, review it, promote it
  into a page, and confirm the capture status changes.
- [ ] Wikilinks resolve correctly in rendered output, including existing pages,
  missing pages, and links with aliases.
- [ ] MCP protocol handlers respond correctly for initialize, list tools, read
  page, write page, search, and capture operations.
- [ ] Code ingest processes service definitions and creates or updates service
  pages with stable source references.
- [ ] Lint detects contradictions, stale pages, frontmatter issues, broken
  wikilinks, and invalid capture metadata.
- [ ] Link graph builds and visualizes correctly for normal pages, orphaned
  pages, and missing targets.
- [ ] Multi-workspace routing isolates content, search indexes, history, audit
  logs, and write operations by workspace.

## Security

- [ ] Auth middleware blocks unauthenticated requests to protected API and UI
  routes.
- [ ] Run `python scripts/scan_secrets.py` and verify no privacy or secret leaks
  are found before tagging a release.
- [ ] Bearer auth accepts the configured token and rejects missing, malformed, or
  incorrect tokens.
- [ ] Basic auth accepts the configured credentials and rejects missing,
  malformed, or incorrect credentials.
- [ ] Rate limiting returns HTTP 429 for excess write requests and does not block
  normal read traffic.
- [ ] Security headers are present on all application responses, including HTML,
  JSON, redirects, errors, and static assets.
- [ ] Path traversal attempts return HTTP 422 and cannot read, write, delete, or
  render files outside the workspace content directory.
- [ ] Input validation rejects oversized content and returns a clear validation
  error without writing partial data.

## History Sanitization

- [ ] Run `python3 scripts/scan_secrets.py` — must report 0 issues on tracked files
- [ ] Run `python3 scripts/scan_secrets.py --all-revisions` — must report 0 issues across all git history
- [ ] Verify `git log --all --format='%ae' | sort -u` and `git log --all --format='%ce' | sort -u` contain only approved author/committer emails
- [ ] Verify `git rev-list --all | wc -l` shows only the expected commit count (1 for orphan branch)
- [ ] Verify `git ls-remote --heads origin` shows only `refs/heads/main`
- [ ] Force-push approved by repo owner before execution: `git push --force origin main`
- [ ] After force-push, verify remote is clean with a fresh clone test

## Observability

- [ ] `/healthz` returns `ok` plus runtime metrics needed by deployment health
  checks.
- [ ] Structured request logs are emitted as JSON and include method, path,
  status, latency, and request identifier.
- [ ] Audit log records page writes with actor, action, page ID, workspace, and
  timestamp.
- [ ] Page history returns chronological entries for create, update, promote, and
  delete operations.

## SDKs

- [ ] Python SDK installs into a clean virtual environment from the release
  package.

  ```bash
  cd sdk/python && python -m pip install -e ".[test]" && python -m pytest tests/ -v
  ```

- [ ] Python SDK can connect, create pages, read pages, update pages, delete
  pages, search, and capture memory.
- [ ] TypeScript SDK installs into a clean project from the release package.

  ```bash
  cd sdk/typescript && npm ci && npm run build && npm test
  ```

- [ ] TypeScript SDK can connect, create pages, read pages, update pages, delete
  pages, search, and capture memory.
- [ ] Embed widget loads in an iframe, renders the selected page or search view,
  and respects the configured base URL.

## Deployment

- [ ] Docker image builds from a clean checkout.

  ```bash
  docker build -t lore:release .
  ```

- [ ] Docker container starts with persistent content and database volumes.

  ```bash
  docker run -d -p 8100:8000 \
    -v lore-pages:/tmp/pages \
    -v lore-db:/tmp/db \
    -e LORE_AUTH_MODE=bearer \
    -e LORE_AUTH_SECRET=change-me-in-production \
    -e LORE_CONTENT_DIR=/tmp/pages \
    -e LORE_SEARCH_DB=/tmp/db/search.db \
    -e LORE_VECTOR_DB=/tmp/db/vectors.db \
    -e LORE_LEDGER_DB=/tmp/db/ledger.db \
    -e LORE_API_KEYS_DB=/tmp/db/api_keys.db \
    lore:release
  ```

- [ ] Docker container responds to `/healthz` and supports authenticated API
  requests.

  ```bash
  curl -f http://localhost:8100/healthz
  ```

- [ ] Systemd service starts, restarts on failure, and responds through the
  configured port.
- [ ] Backup CLI exports content, search database, vector database, and audit
  state.
- [ ] Restore CLI recreates a working instance from a backup on a clean data
  directory.
- [ ] Sample vault initializes correctly and exposes the expected index,
  architecture, guides, runbooks, services, and decisions pages.

## Documentation

- [ ] Quickstart guide is accurate for a clean machine and includes install,
  start, create, search, capture, and SDK examples.
- [ ] API reference matches actual endpoints, request schemas, response schemas,
  status codes, and error payloads.
- [ ] Configuration docs list all supported environment variables with defaults
  and production guidance.
- [ ] Security docs cover auth modes, security headers, input validation, path
  handling, and rate limits.
- [ ] Deployment guide covers Docker, systemd, reverse proxy setup, persistent
  storage, and health checks.

## Testing

- [ ] All release tests pass:

  ```bash
  pytest tests/ eval
  ```

- [ ] Package builds cleanly:

  ```bash
  python -m pip install --upgrade pip build
  python -m build
  python -m pip install dist/*.whl
  python -c "import lore_app; print('Package import OK')"
  ```

- [ ] Package version matches changelog:

  ```bash
  python -c "from importlib.metadata import version; print(version('lore'))"
  # Expected: 0.3.0b1 (or current release version)
  ```

- [ ] Capture-to-durable memory regression passes:

  ```bash
  rm -rf /tmp/lore-test-db /tmp/lore-test-pages && LORE_CONTENT_DIR=/tmp/lore-test-pages LORE_SEARCH_DB=/tmp/lore-test-db/search.db LORE_LEDGER_DB=/tmp/lore-test-db/ledger.db LORE_VECTOR_DB=/tmp/lore-test-db/vectors.db LORE_API_KEYS_DB=/tmp/lore-test-db/api_keys.db uv run --extra test python -m pytest tests/test_capture_to_durable.py -v --timeout=120
  ```

- [ ] Test output has no warnings, deprecations, resource leaks, or skipped tests
  that are unexpected for the release.
- [ ] Performance benchmarks are within release thresholds for page CRUD, search,
  link graph generation, RAG retrieval, and startup time.

## Publishing

- [ ] Tag the release: `git tag -a v0.3.0-beta.1 -m "Beta 1 release" && git push origin v0.3.0-beta.1`
- [ ] Verify the `publish` GitHub Action runs and completes successfully
- [ ] Verify GitHub Release is created at https://github.com/elevenoutoften/lore/releases with wheel + sdist assets
- [ ] Verify Docker image appears at `ghcr.io/elevenoutoften/lore` with the version tag
- [ ] Verify `pre-release` flag is set for beta/alpha/rc tags
- [ ] (Future: PyPI publish when configured)
