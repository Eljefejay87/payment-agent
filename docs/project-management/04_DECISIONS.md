# Decisions

## Decision maintenance rule

Record new decisions here when they materially affect the repository direction, workflow, or documentation approach. Include the reason for the decision and the current known context, and avoid repeating the same decision in multiple places.

## Important architectural decisions

- Keep agent-specific business workflows under [agents](../../agents) and reusable platform functionality under [shared](../../shared). Reason: this is the documented repository pattern in [docs/UCM_ENGINEERING_PLAYBOOK.md](../UCM_ENGINEERING_PLAYBOOK.md) and [README.md](../../README.md).
- Use SQLite and local health/status outputs for the currently documented runtime state. Reason: this matches the repository’s verified storage and deployment guidance.
- Keep broader UCM systems and projects such as Atlas and SCollect outside the repository boundary unless explicitly verified. Reason: the repository docs explicitly warn against mixing unverified scope into this project.

## Documentation decisions

- Use a short entry-point document first, then link to the deeper playbook and architecture docs. Reason: this reduces duplication and helps both humans and AI assistants start from a consistent path.
- Keep unknowns explicit with "To be documented" rather than filling gaps with assumptions. Reason: this is a core rule in [docs/UCM_ENGINEERING_PLAYBOOK.md](../UCM_ENGINEERING_PLAYBOOK.md).

## Deployment decisions

- Favor dry-run and read-only validation before live processing or production-impacting changes. Reason: this is the documented safe path in [docs/DEPLOYMENT_GUIDE.md](../DEPLOYMENT_GUIDE.md).
- Treat deployment, secret, database, and migration actions as approval-gated operations. Reason: this is explicitly documented in [00_START_HERE.md](../../00_START_HERE.md) and [docs/UCM_ENGINEERING_PLAYBOOK.md](../UCM_ENGINEERING_PLAYBOOK.md).

## AI workflow decisions

- Keep AI cost controls and usage guardrails in the documented workflow rather than as an afterthought. Reason: the repository includes an AI budget guard described in [README.md](../../README.md).
- Prefer small, reversible changes when working in this repository. Reason: this aligns with the engineering playbook and the current repo guidance.

## When these decisions are known

These decisions are based on the current repository documentation and code structure as of the current review and should be revisited if the repository scope changes.
