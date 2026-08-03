# Handoff Guide

## Handoff maintenance rule

Review the project-management set before starting work and update this file whenever ownership, status, or next steps change. Keep this file as the practical handoff log for the repository and use it to point to the canonical docs rather than repeating them.

## Everything a new developer or AI assistant needs to continue the project

## Required reading order

1. [00_START_HERE.md](../../00_START_HERE.md)
2. [AGENTS.md](../../AGENTS.md)
3. [docs/UCM_ENGINEERING_PLAYBOOK.md](../UCM_ENGINEERING_PLAYBOOK.md)
4. [docs/SYSTEM_ARCHITECTURE.md](../SYSTEM_ARCHITECTURE.md)
5. [docs/PROJECT_ROADMAP.md](../PROJECT_ROADMAP.md)
6. [README.md](../../README.md)

## Repository overview

The repository is primarily a Payment Agent project with reusable platform code under [shared](../../shared) and agent-specific code under [agents](../../agents). The current verified work is centered on payment email processing, duplicate protection, SQLite-backed storage, and documented deployment safety.

## Safe workflow

- Start from the documented repo rules before making changes.
- Keep changes small and reversible.
- Verify the current behavior before claiming success.
- Avoid deployment, secret, database, or migration changes without approval.
- Treat unknowns as "To be documented" rather than expanding scope prematurely.

## Common mistakes to avoid

- Treating broader UCM systems as if they are implemented in this repository.
- Assuming Atlas, SCollect, or unrelated projects are part of this repo.
- Making production-impacting changes without approval.
- Inventing integrations or roadmap items that are not verified here.

## Where to start on the next task

Start by reading the current roadmap and the engineering playbook, then identify the smallest verified change that matches the repository state. If the task requires broader operational scope, document the unknowns before implementing anything new.
