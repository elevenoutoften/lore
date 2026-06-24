# Lore — Human Surface UAT Script

**Goal:** verify the *human* experience of Lore end-to-end on the live deployment by
clicking through it yourself. A human should be able to **log in, read knowledge pages
like articles, navigate links, search, view the graph, and do the minimal setup**
(configure the model, create an agent key). Everything else in the nav is agent/
maintainer surface and only needs a "does it load" check.

- **Target:** https://lore.axis.love (fronted by Axis SSO at core.axis.love)
- **You need:** your Axis SSO login, and — for the setup steps (E, F) — your Lore
  **admin** key (`lore_…`, the one stored as `LORE_API_KEY` on this machine).
- **How to use:** do each step, compare against **Expect**, tick the box. Jot anything
  that surprises you. Steps marked _(rough edge)_ are known UX smells — confirm whether
  they bother you.
- **Verified at the API/render level on 2026-06-25** (every route below returns 200 with
  the right content live). This script is the *human-judgment* layer on top of that:
  does it look right, read well, and behave when clicked.

---

## A. Log in & orient

| # | Action | Expect | ✓ |
|---|--------|--------|---|
| A1 | Open an incognito window → `https://lore.axis.love/` | Redirect to `core.axis.love/login?next=…` (Axis SSO). | ☐ |
| A2 | Complete Axis SSO login | You land on `lore.axis.love/` showing the **reader home** (not a 401/blank). | ☐ |
| A3 | Look at the nav | It is split into two labelled groups: **Read** (Wiki/home, Search, Graph) and **Operate** (Captures, Procedures, Health, Lint, RAG, Keys, Settings). | ☐ |

> If A2 shows 401/blank, the SSO session didn't grant a browser role. Workaround: open
> **Settings** or **Keys**, paste your `lore_` admin key in the bearer-token box, click
> **Sign in to this browser**, retry. _(rough edge — a human shouldn't have to paste a
> token after SSO; note if this happens.)_

## B. Reader home (the front door)

| # | Action | Expect | ✓ |
|---|--------|--------|---|
| B1 | Read the `/` landing | A **reader** landing — a **Featured** section and a **Recently updated** list of pages. No "create a key / configure the model" onboarding CTAs as the main content. | ☐ |
| B2 | Confirm captures are excluded | The Recently-updated list shows real knowledge pages, **not** raw `inbox/…` draft captures. | ☐ |
| B3 | Click a **Featured** / recent page | It opens that article (section C). | ☐ |

## C. Read knowledge as articles (core human value)

| # | Action | Expect | ✓ |
|---|--------|--------|---|
| C1 | From the sidebar/list pick a **substantive** page (a service/architecture/concept page — **not** an `inbox/` capture) | The article renders: title, formatted Markdown body, and an **"On this page" TOC**. | ☐ |
| C2 | Scroll to the relationships area | You see **outgoing links** and **backlinks**; broken/missing internal links are styled with a **warning** look. | ☐ |
| C3 | Click a wiki link inside the body | Navigates to that page and renders it the same way. | ☐ |
| C4 | Look at the page header/footer | _(rough edge)_ **Raw / Rendered JSON / Links JSON** links are present on every article — agent-oriented. Note whether they feel out of place for a human reader. | ☐ |

## D. Search

| # | Action | Expect | ✓ |
|---|--------|--------|---|
| D1 | Open **Search**, query `memory` | A ranked results list; relevant pages near the top. | ☐ |
| D2 | Check a result row | _(rough edge)_ Confirm rows show a useful **excerpt/snippet**, not just a bare title. | ☐ |
| D3 | Click a result | Opens that article. | ☐ |
| D4 | Search nonsense `zzqqxx` | Graceful "no results", not an error. | ☐ |

## E. Graph

| # | Action | Expect | ✓ |
|---|--------|--------|---|
| E1 | Open **Graph** (`/graph`) | An interactive vis-network graph renders in the first screen, no console errors. | ☐ |
| E2 | Click/focus a node | View recentres / shows that node's neighbours. | ☐ |

## F. Minimal setup 1 — configure the model (`/settings`)

> Admin-only. If "not authorized", paste your `lore_` **admin** key in the bearer box and
> **Sign in to this browser** first.

| # | Action | Expect | ✓ |
|---|--------|--------|---|
| F1 | Open **Settings** | "LLM Provider Settings" with a status chip for the current provider. | ☐ |
| F2 | Read the **current** block | Shows provider, model, embedding_model, base_url, and a **masked** api_key (never the real secret). | ☐ |
| F3 | Change `model` (or `temperature`) → **Save** | Success message; the current block updates immediately — **hot reload**, no restart. | ☐ |
| F4 | Click **Reset to Defaults** | Fields revert to code defaults. | ☐ |

## G. Minimal setup 2 — create an agent key (`/api-keys`)

| # | Action | Expect | ✓ |
|---|--------|--------|---|
| G1 | Open **Keys** | "API Keys": a create form (name, description, role) + list of existing keys. | ☐ |
| G2 | Create: name `uat-test`, role **reader** → **Create** | A `lore_…` token shown **once** with a working **Copy** button. | ☐ |
| G3 | Reload the page | The key appears by name/role/prefix; the full token is **not** shown again. | ☐ |
| G4 | **Revoke** `uat-test` | It is marked revoked. | ☐ |

## H. Capture draft page (provenance)

| # | Action | Expect | ✓ |
|---|--------|--------|---|
| H1 | Open **Captures** (`/captures`), click any draft | The capture page shows a **"⚠ Draft Capture"** banner and its provenance (source, confidence, actor). | ☐ |

## I. Embed widget (optional / niche)

| # | Action | Expect | ✓ |
|---|--------|--------|---|
| I1 | Visit `/embed?mode=search&q=memory` | A **chrome-less** (no nav) search view renders inside the page. | ☐ |
| I2 | Visit `/embed?pageId=<a-real-page-id>` | A chrome-less rendered article. (Bare `/embed` returning a 422 is expected — page mode needs `pageId`.) | ☐ |

## J. Error & operate-surface "does it load"

| # | Action | Expect | ✓ |
|---|--------|--------|---|
| J1 | Visit `/does/not/exist` | A **themed 404** page with a search box, not a stack trace. | ☐ |
| J2 | Open each Operate page: **Procedures, Health, Lint, RAG** | Each renders its dashboard without error (these are agent-ops tools — you only need to confirm they load). | ☐ |

## K. Logout (optional)

| # | Action | Expect | ✓ |
|---|--------|--------|---|
| K1 | Log out (clears `lore_session`) and revisit `/` | Sent back through Axis SSO. | ☐ |

---

## What is NOT a human surface (expected)

The **Operate** nav group is agent/maintainer tooling — fine to exist, but not part of the
"read articles + minimal setup" human path: **Captures** (intake/draft queue),
**Procedures** (typed artifacts/skill export), **Health** (memory metrics), **Lint**
(knowledge-quality warnings), **RAG** (retrieval debug console).

## Known rough edges to judge (please confirm)

1. **Raw-JSON links on every article** (C4) leak the agent surface into the reader view.
2. **Operate-nav clutter:** 7 maintainer destinations vs 3 reader ones — heavier than a
   casual human needs.
3. **Post-SSO token paste** (A-note, F, G): if setup demands a pasted admin key even after
   SSO, that breaks the "minimum setup for humans" rule.
4. **Search snippets** (D2): confirm result rows show a useful excerpt, not title-only.

These four are the candidate inputs for the Phase 3 **UI-redesign** track, if you choose it.
