# Project Status

## Project management rule

Before starting any task, review this file together with [02_BACKLOG.md](02_BACKLOG.md), [03_MILESTONES.md](03_MILESTONES.md), [04_DECISIONS.md](04_DECISIONS.md), [05_HANDOFF.md](05_HANDOFF.md), and [06_RELEASE_PLAN.md](06_RELEASE_PLAN.md). Keep these files updated as work progresses, mark completed work, record decisions, update milestones, and preserve a current handoff trail. Use them as the planning source of truth and avoid duplicating guidance that already exists in the main repository docs.

## Current project summary

The repository currently centers on the Payment Agent for United Capital Management / United Account Services. The verified scope includes payment email processing, duplicate protection, SQLite-backed storage, shared platform code under [shared](../../shared), and documentation for local and Railway-based operation.

## Completed work

- The repository contains a documented Payment Agent workflow for scanning Microsoft 365 email, extracting payment details, and storing records.
- A shared data layer foundation is present under [docs/shared_data_layer.md](../shared_data_layer.md).
- The repo includes onboarding and engineering guidance in [00_START_HERE.md](../../00_START_HERE.md), [AGENTS.md](../../AGENTS.md), and [docs/UCM_ENGINEERING_PLAYBOOK.md](../UCM_ENGINEERING_PLAYBOOK.md).
- Local and deployment guidance is present in [README.md](../../README.md) and [docs/DEPLOYMENT_GUIDE.md](../DEPLOYMENT_GUIDE.md).

## Work currently in progress

- The repository continues to evolve around shared platform maturity and safer operational patterns.
- The roadmap identifies work around production-safe payment processing, shared platform maturity, deployment readiness, and operational visibility in [docs/PROJECT_ROADMAP.md](../PROJECT_ROADMAP.md).
- The roadmap also notes that the Weekly Remit workflow is in an early build stage.

## Known blockers

- Broader UCM enterprise integrations remain outside the verified repository boundary.
- Production ownership, incident escalation, and long-term support procedures are still documented as "To be documented" in [docs/KNOWN_LIMITATIONS.md](../KNOWN_LIMITATIONS.md).
- Deployment, secret, database, and migration changes require explicit approval.

## Immediate next priorities

1. Keep documentation aligned with the current repository state.
2. Continue safe validation around the documented Payment Agent workflow.
3. Resolve or document unknowns rather than expanding scope prematurely.

## Overall project health

The project appears healthy for controlled, documentation-first work. It is less mature for broad production rollout until ownership, external integrations, and operational procedures are verified more fully.
