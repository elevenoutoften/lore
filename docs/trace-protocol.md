# Reasoning Trace Protocol

## Purpose
Reasoning traces store audit-grade rationale summaries for agent decisions. They explain *why* a choice was made, NOT the raw chain-of-thought.

## What NOT to Store
- Raw model chain-of-thought or hidden reasoning
- Verbatim model outputs
- Internal monologue or scratchpad content

## What TO Store
- Concise rationale summaries (human-readable, <5000 chars)
- Context references (pages, captures, tasks examined)
- Tool references (what tools were called, what they returned)
- Constraints that applied
- Policy references that governed the decision
- Alternatives considered and why they were rejected
- Outcome of the decision
