# Lore

Use Lore when you want durable project memory from a shell:
`capture` `recall` `ack` `search` `read` `write`

Bundled CLI in this skill:
- Windows: `.\bin\lore.cmd ...`
- macOS/Linux: `./bin/lore ...`
- If this skill's `bin/` is on `PATH`, the same commands are just `lore ...`

Config: `LORE_BASE_URL` and `LORE_API_KEY`.
Use a Lore API key in `LORE_API_KEY`; Flow API keys do not work.

Authenticated identity scopes capture and recall, so use the same identity for both unless you intentionally perform an authorized cross-actor operation.

Examples:
```bash
lore capture "Pixl renders text as garbage on Illustrious XL" --lane project --source-task flow_000891
lore recall "pixl text rendering" --limit 5
lore ack 7e3c1a90-2b4f-4d8a-9c1e-0f5a6b2d3e41 5a9d2c70-8e1b-4f3a-bb62-1d4e7c9a0f23
lore search "memory backend"
lore read services/lore
lore write notes/agent/demo --file note.md -m "capture follow-up"
```
