# Consolidation Policies

Policies gate patch planning decisions in Lore. Each policy has a stable ID, version, and machine-readable rules that the `PolicyEngine` evaluates when generating patch plans.

## Where Policies Live

Policies are stored in the Lore ledger database (`policies` SQLite table). They are managed via:

- **REST API**: `GET/POST/DELETE /api/policies`
- **MCP tools**: `lore_list_policies`, `lore_get_policy`
- **Python SDK**: Not yet exposed (future work)

## Policy ID Format

Policy IDs follow the format `name:vN` where:

- `name` is lowercase alphanumeric with hyphens (for example, `auto-apply`, `protected-surface`)
- `vN` is the version number (for example, `v1`, `v2`)

Examples: `auto-apply:v1`, `protected-surface:v2`, `contradiction-review:v1`

## Default Policies

Lore seeds four policies on first init:

| Policy ID | Gate | Applies To | Pass Effect | Fail Effect |
|---|---|---|---|---|
| `auto-apply:v1` | auto-apply | insert_new_fact, append_sourced_paragraph, create_stub_page | auto-apply | review |
| `protected-surface:v1` | protected-surface | decision, runbook pages | allow | review |
| `contradiction-review:v1` | contradiction-review | update_existing_fact, mark_stale | allow | review |
| `risk-high:v1` | risk-assessment | decision/runbook pages + update/mark operations | allow | review |

## How Agents Cite Policies

When a patch plan is generated, the `PolicyEngine` evaluates all applicable policies and records decisions in the plan's `policies_applied` field. Each decision includes:

- `policy_id` which policy was evaluated
- `gate` which decision gate was checked
- `passed` whether the policy passed or failed
- `reason` human-readable explanation that uses `fail_reason_template` on failure

Trace entries include `policy_refs` in the format `{policy_id}:pass` or `{policy_id}:fail`, linking the reasoning trace to specific policy evaluations.

## Policy Gates

| Gate | Description |
|---|---|
| `auto-apply` | Controls whether a patch can be applied automatically |
| `protected-surface` | Blocks auto-apply on protected page kinds (decision, runbook) |
| `contradiction-review` | Requires review when contradicting claims are detected |
| `risk-assessment` | Escalates risk level for dangerous page kind + operation combinations |

## Modifying Policies

Create or update a policy via `POST /api/policies`:

```json
{
  "policy_id": "custom-gate:v1",
  "name": "Custom review gate",
  "gate": "review-required",
  "condition_kind": ["procedure"],
  "effect_pass": "allow",
  "effect_fail": "review",
  "fail_reason_template": "Procedure page {page_id} requires human review.",
  "version": 1
}
```

Disable a policy via `DELETE /api/policies/{policy_id}`. Disabled policies are skipped during evaluation.

## Versioning

When updating a policy's rules, increment the `version` field. Policy IDs include the version, so `auto-apply:v1` and `auto-apply:v2` are distinct policies. The `PolicyEngine` always evaluates the latest enabled version of each policy.
