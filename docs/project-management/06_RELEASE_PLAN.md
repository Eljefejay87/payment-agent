# Release Plan

## Release planning rule

Review this file before starting work that affects scope, timing, or release readiness. Update it as the repository evolves and keep it aligned with the current roadmap and documentation rather than introducing speculative release content.

## Current version status

The repository currently documents a Payment Agent workflow and supporting platform layers, but it does not define a formal release versioning scheme in the documentation set. The safest interpretation is that work should remain release-by-release and documentation-driven until a formal operational release process is verified.

## Planned future releases

### Release 1: Documentation and safety hardening

- Improve onboarding and operational clarity.
- Keep the engineering playbook and handoff docs aligned with the repo.
- Preserve conservative deployment and approval safeguards.

### Release 2: Shared platform maturity

- Continue refining shared configuration, logging, and persistence patterns.
- Keep new work aligned with the existing repository boundaries.

### Release 3: Broader operational readiness

- Expand runtime validation, operational visibility, and support guidance only when the repository state clearly supports it.

## Features expected in each release

- Release 1: safer documentation and clearer handoff paths.
- Release 2: shared platform improvements that remain grounded in current verified patterns.
- Release 3: stronger operational readiness for deployment and support workflows.

## Release checklist

- Confirm the documented scope matches the repository state.
- Review deployment and safety guidance.
- Verify that no unapproved deployment, secret, or database changes are required.
- Confirm documentation is current before release planning is finalized.

## Documentation checklist before release

- Confirm [00_START_HERE.md](../../00_START_HERE.md), [AGENTS.md](../../AGENTS.md), [docs/UCM_ENGINEERING_PLAYBOOK.md](../UCM_ENGINEERING_PLAYBOOK.md), and [README.md](../../README.md) still match the implementation.
- Verify that the project-management docs remain consistent with [docs/PROJECT_ROADMAP.md](../PROJECT_ROADMAP.md).
- Keep any unknowns explicitly marked as "To be documented".
