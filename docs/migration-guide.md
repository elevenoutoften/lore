# ExampleProject to Standalone Lore Migration Guide

This plan covers extracting Lore from the ExampleProject monorepo into a standalone
project while preserving a clean release path for the current beta.

## Current State

- Lore lives at `services/lore/` in the ExampleProject repository.
- Runtime dependencies are FastAPI, SQLite, numpy, markdown-it-py, and uvicorn.
- Lore is self-contained and has no ExampleProject-specific code dependencies.
- Git history currently lives inside the ExampleProject repository history.
- Documentation, SDKs, tests, sample vault data, Docker packaging, and release
  metadata are already scoped under `services/lore/`.

## Migration Steps

### Step 1: Extract to a Standalone Repository

Create a branch that contains only the Lore directory history:

```bash
git subtree split --prefix=services/lore -b lore-standalone
```

Create the standalone remote repository, then push the split branch:

```bash
git remote add lore-origin git@github.com:<owner>/lore.git
git push lore-origin lore-standalone:main
```

Verify after extraction:

- [ ] Repository root contains the former `services/lore/` contents.
- [ ] `lore_app/`, `docs/`, `tests/`, `eval/`, `sdk/`, `sample-vault/`, and
  `Dockerfile` are present.
- [ ] Commit history includes relevant Lore changes.
- [ ] No unrelated ExampleProject service code is present.

### Step 2: Update Package Metadata

Update standalone project metadata:

- [ ] `pyproject.toml` uses standalone project URLs, repository links, issue
  tracker links, and documentation links.
- [ ] `pyproject.toml` removes stale ExampleProject references unless they are historical
  migration notes.
- [ ] `README.md` explains standalone installation from PyPI, source checkout,
  Docker, and local development.
- [ ] `Dockerfile` builds from the standalone repository root.
- [ ] Python SDK metadata points to the standalone repository.
- [ ] TypeScript SDK metadata points to the standalone repository.
- [ ] Changelog describes the beta extraction and compatibility expectations.

### Step 3: Set Up CI/CD

Create GitHub Actions workflows for the standalone repository:

- [ ] Test workflow runs Python tests with `pytest tests eval`.
- [ ] Lint workflow checks formatting, import hygiene, and static analysis rules
  chosen for the standalone project.
- [ ] Build workflow creates Python package artifacts and validates Docker image
  builds.
- [x] Publish workflow creates a GitHub Release with wheel and sdist artifacts
  on `v*` tags.
- [x] Docker publish workflow uploads the Lore image to GitHub Container
  Registry (`ghcr.io/elevenoutoften/lore`) on tagged releases.
- [ ] PyPI publishing remains deferred until package registry release policy and
  credentials are finalized.
- [x] No repository publish secrets are required for GitHub Release or GHCR;
  Actions use the built-in `GITHUB_TOKEN`.

Recommended release workflow gates:

- [ ] Pull requests must pass tests and build checks.
- [ ] Main branch must pass tests before publishing.
- [ ] Publishing requires an annotated version tag.
- [ ] Release artifacts are retained for audit and rollback.

### Step 4: Move and Publish Documentation

Keep the existing docs in the standalone repository and publish them as a docs
site.

- [ ] Move `docs/` to the standalone project unchanged during extraction.
- [ ] Set up MkDocs or a similar static documentation site.
- [ ] Add navigation for quickstart, configuration, API reference, security,
  deployment, SDKs, MCP examples, beta checklist, and migration notes.
- [ ] Update internal links that previously depended on the ExampleProject repository
  layout.
- [ ] Add standalone development commands for tests, Docker, SDKs, and sample
  vault setup.
- [ ] Confirm every documentation page renders without broken links.

### Step 5: Release

Prepare and publish the first beta:

- [ ] Confirm the beta release checklist is complete.
- [ ] Tag the release as `v1.0.0-beta.1`.
- [x] Publish a GitHub Release with generated release notes and attached Python
  artifacts.
- [x] Publish the Lore Docker image to `ghcr.io/elevenoutoften/lore`.
- [ ] Publish `lore-app` to PyPI after deferred package registry work is
  completed.
- [ ] Publish SDK packages if they are versioned independently.
- [ ] Create release notes with install commands, upgrade guidance, known
  limitations, and rollback guidance.
- [ ] Announce the beta to intended users and include support and issue-reporting
  links.

## Risk Assessment

| Risk | Level | Notes | Mitigation |
| --- | --- | --- | --- |
| Code extraction changes behavior | Low | Lore is self-contained and no code changes are required for extraction. | Run the full test and eval suites before and after extraction. |
| Lost or noisy history | Low | Subtree extraction should preserve relevant history but may include imperfect commit boundaries. | Inspect the split branch before pushing and keep the ExampleProject repository as source history. |
| CI/CD secrets missing or incorrect | Low | GitHub Release and GHCR publishing use the built-in `GITHUB_TOKEN`; only future PyPI work will need extra credentials. | Keep publish permissions minimal and add PyPI credentials only when package publishing is enabled. |
| Docker context assumptions break | Medium | Existing Dockerfile may assume the monorepo layout. | Build from a clean standalone checkout and adjust paths before release. |
| Documentation links break | Medium | Some links may reference ExampleProject-relative paths. | Run a docs link check before publishing. |
| Downstream users depend on old path | Low | Existing users may reference `services/lore/` in scripts. | Provide migration notes and keep ExampleProject integration as a subtree, submodule, or pinned dependency. |

## Rollback Plan

If the standalone migration needs to be reversed or paused:

- Keep ExampleProject `services/lore/` as the canonical source until the standalone
  repository passes release checks.
- Reattach Lore to ExampleProject as a Git submodule or Git subtree if shared
  development still needs to happen from the monorepo.
- Pin ExampleProject deployments to a known good GitHub Release artifact or GHCR
  image until PyPI publishing is enabled.
- Keep the pre-migration ExampleProject commit available as the fallback deployment
  source.
- If a beta artifact is bad, yank or mark it deprecated in the package registry,
  unpublish or retag the Docker image according to registry policy, and publish a
  corrected beta version.

## Timeline Estimate

| Work | Estimate |
| --- | ---: |
| Extraction | 1 hour |
| CI setup | 2 hours |
| Documentation | 1 hour |
| Total | ~4 hours |

Release validation and publishing should use the beta checklist before any
artifacts are announced.
