# Coding Standards

## Purpose

This document records the coding expectations that are verifiable in this repository and preserves the project’s existing engineering standards as described in the repo README and AGENTS file.

## Core standards

- Build production-ready services, not throwaway scripts.
- Reuse shared platform code before creating new modules.
- Keep agent business logic separate from shared infrastructure.
- Prefer readable, explicit code patterns over clever abstractions.
- Keep changes scoped to the requested task.
- Prefer small, reversible changes.

## Repository structure expectations

The repository is already organized with a pattern that is intentionally consistent across agents:

- `agents/<agent_name>/` for agent-specific logic
- `shared/` for reusable platform code
- `docs/` for project operational documentation
- `tests/` for validation coverage

This pattern should remain the default unless a clearly justified repo change is requested and approved.

## Configuration and environment

- Load configuration from `.env` or environment variables.
- Keep `.env.example` aligned with supported settings.
- Do not hardcode secrets, client IDs, tenant IDs, webhook URLs, mailbox names, or durable data paths.
- Validate required configuration at startup with clear error messages when appropriate.

## Security expectations

- Use least-privilege permissions for external APIs.
- Never log tokens, secrets, raw email bodies, or customer data.
- Validate external inputs before processing them.
- Prevent duplicate processing using explicit duplicate checks.
- Store only the minimum required data for the workflow.

## Logging and operational health

The repo documents logging and health-check patterns that should remain in place for all production work:

- startup and shutdown logs
- warnings and retries
- duplicate skips or idempotent outcomes
- API and database failures
- health status reporting for runtime monitoring

Operational logs and health artifacts should avoid sensitive details and should not store raw payloads.

## Testing expectations

- Prefer the smallest relevant test or validation command.
- Use repository tests before claiming a change is safe.
- Keep tests focused on behavior, not mock-only assumptions.
- Do not add test-only production hooks unless truly required by the workflow.

## AI design expectations

Repositories using AI in this project should follow the same production-safety pattern:

- minimize repeated AI calls
- prefer a minimal, necessary model capability
- use cost controls and budget guardrails where applicable
- keep request and response handling explicit and observable

## Documentation expectations

- Keep documentation factual and repository-based.
- Write `To be documented` instead of guessing when details are unknown.
- Preserve useful existing instructions when updating docs.
- Keep architecture, deployment, and project docs aligned.

## Non-negotiable requirements

- do not change secrets or deployment configuration without approval
- do not alter databases or run migrations without explicit approval
- do not merge or deploy without approval
- do not widen scope beyond the verified repo boundaries

## Unknowns and placeholders

The following items are not verified in this repository and therefore remain `To be documented`:

- full production environment ownership and escalation matrix
- final retention policy
- formal service-level objectives
- broader UCM-wide engineering governance beyond this repo
