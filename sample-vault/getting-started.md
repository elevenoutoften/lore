---
title: Getting Started
kind: guide
visibility: public
summary: First steps for reading, writing, and linking Lore pages.
---
# Getting Started

Start by reading [[Architecture|architecture/overview]] to understand how Lore
stores Markdown pages and builds search, graph, and RAG context.

## Write a Page

Every page starts with frontmatter:

```markdown
---
title: Demo Service
kind: service
visibility: internal
summary: Short service description.
---
```

Use `[[Label|page-id]]` wikilinks to connect pages. For example, this guide
links to [[API Usage|guides/api-usage]], [[Search|guides/search]], and
[[Capture Workflow|guides/capture-workflow]].

## Work as an Agent

1. Search for existing context.
2. Read canonical pages before making claims.
3. Save uncertain findings as captures.
4. Promote reviewed knowledge into service, runbook, or decision pages.

See [[Frontmatter Reference|references/frontmatter]] for common metadata fields.
