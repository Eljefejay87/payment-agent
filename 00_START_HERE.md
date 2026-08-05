# 00_START_HERE

## Purpose

This is the first document to read before working in this repository. It is a short entry point that helps any AI coding assistant or developer find the mandatory repo guidance without re-reading the full project context each time.

## Repository identity

This repository is the Payment Agent project for United Capital Management / United Account Services. It contains the verified Payment Agent runtime and reusable shared platform code under `shared/` and `agents/`.

Broader UCM systems are external unless they are directly verified in this repository. Atlas is a separate project and must not be mixed with this repository. SCollect is Windows-only and has no direct API integration in this repo, so it must not be described as directly integrated.

## Required reading order

Read these in order, from the most mandatory operating rules to the project overview:

1. [AGENTS.md](AGENTS.md) — Read this first to understand the repository’s mandatory coding-agent instructions and repo-level standards.
2. [docs/UCM_ENGINEERING_PLAYBOOK.md](docs/UCM_ENGINEERING_PLAYBOOK.md) — Read this next for the repo-wide engineering principles, safety rules, and required completion-report expectations.
3. [docs/SYSTEM_ARCHITECTURE.md](docs/SYSTEM_ARCHITECTURE.md) — Read this to understand the verified architecture, storage model, and external dependencies.
4. [docs/CODING_STANDARDS.md](docs/CODING_STANDARDS.md) — Read this before making changes to confirm how code and docs should be structured and reviewed.
5. [docs/DEPLOYMENT_GUIDE.md](docs/DEPLOYMENT_GUIDE.md) — Read this before any deployment-related work so the validated Railway and runtime safeguards are clear.
6. [docs/COST_OPTIMIZATION.md](docs/COST_OPTIMIZATION.md) — Read this for the verified model-cost and efficiency rules used in this repository.
7. [docs/PROJECT_ROADMAP.md](docs/PROJECT_ROADMAP.md) — Read this to understand the current verified roadmap and priorities.
8. [README.md](README.md) — Read this after the rules and architecture docs for the product overview, setup notes, and verified runtime behavior.

## Non-negotiable rules

- Protect production.
- Do not deploy, merge, push, change secrets, modify databases, or run migrations without explicit approval.
- Verify before reporting success.
- Prefer small, reversible changes.
- Do not expose credentials or customer data.
- Keep Atlas and unrelated projects isolated.
- Do not invent integrations or architecture.
- Use the least expensive capable model and avoid repeated unnecessary work.

## Task workflow

1. Inspect — confirm the repository fact pattern and affected files before making changes.
2. Plan — keep the scope narrow and document assumptions or unknowns as `To be documented`.
3. Implement — make the smallest change that addresses the task without widening scope.
4. Test — run the smallest relevant validation or check needed to verify the change.
5. Review — confirm the patch is consistent with the repo’s rules, docs, and boundaries.
6. Report — end with a concise completion report containing the required fields.
7. Deploy only with approval — do not deploy or change runtime state without explicit approval.

## Required completion report

Every engineering task should end with:

- Summary
- Files changed
- Tests or checks run
- Risk level
- Deployment status
- Recommended next step

## Quick routing guide

- Architecture questions: [docs/SYSTEM_ARCHITECTURE.md](docs/SYSTEM_ARCHITECTURE.md)
- Coding rules: [docs/CODING_STANDARDS.md](docs/CODING_STANDARDS.md)
- Deployment work: [docs/DEPLOYMENT_GUIDE.md](docs/DEPLOYMENT_GUIDE.md)
- Cost/model usage: [docs/COST_OPTIMIZATION.md](docs/COST_OPTIMIZATION.md)
- Roadmap and priorities: [docs/PROJECT_ROADMAP.md](docs/PROJECT_ROADMAP.md)
- General repo setup: [README.md](README.md)

## Scope warning

This repository-level documentation is not a replacement for a future company-wide UCM operating system or governance layer. It reflects the verified repository boundary and documented operational safeguards in this project only. Any broader UCM-wide process should be treated as `To be documented` until it is explicitly verified here.
