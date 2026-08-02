# Cost Optimization

## Scope

This repository includes cost controls for OpenAI use and documents a local monthly AI budget guard. The project’s README states that this guard prevents OpenAI work from exceeding a local calendar-month limit and that transcription estimates are derived from audio duration.

## Verified cost controls

The README documents the following verified guardrails:

- a local SQLite-based budget guard for OpenAI traffic
- a monthly hard limit of `$20.00`
- warnings at `$10.00`, `$15.00`, and `$18.00`
- kill-switch behavior for OpenAI-powered work when the cap is reached
- non-OpenAI work continues normally
- transcription cost estimation based on audio duration and model-specific rates

## Cost principles

The repository’s operating model is intentionally conservative and cost-aware:

- prefer the least expensive capable model for the required task
- avoid unnecessary repeated calls and duplicate processing
- keep validation targeted and narrow
- treat expensive AI or transcription work as controlled operational functions, not casual automation

## Practical guardrails for this repo

The following are appropriate for this repository based on the existing docs:

- keep budget enforcement in the local budget guard rather than relying on memory or ad hoc checks
- prefer dry-run or read-only validation before live AI-heavy work
- avoid broad scans or batch re-processing unless explicitly requested and approved
- minimize retries and redundant tasks that increase cost without adding verification value

## Unknowns and placeholders

The following details should be documented as `To be documented` until they are verified in this repository:

- full production monthly AI cost baseline across all agents
- detailed provider-specific budgets beyond the local guard described in the README
- formal cost allocation across UCM systems and agent workloads
- a repository-wide optimization plan beyond the current Payment Agent budget guard

## Safety rule

Cost optimization must never override production safety, duplicate protection, credential safety, or data privacy. The repo explicitly prioritizes production safety over speed, and the cost controls should support that principle rather than bypass it.
