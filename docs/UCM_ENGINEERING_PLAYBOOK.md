# UCM Engineering Playbook

## 1. Mission and engineering principles

This repository is the Payment Agent project for United Capital Management / United Account Services. The verified scope in this repository is a Python-based Microsoft 365 email processing agent that scans for payment emails, extracts payment details, stores processed records in SQLite, prevents duplicate work, and can send leadership summaries to Microsoft Teams.

The repository also contains reusable platform code under `shared/` and several other agent code paths under `agents/`, but the current verified business runtime remains the Payment Agent workflow described in the project README and the deployment notes.

Engineering principles for this repository:

- Production safety is more important than speed.
- Small, reversible changes are preferred.
- Document only facts that are verified in this repository.
- When a detail is not proven here, write `To be documented` rather than inventing it.
- Do not expose credentials, customer data, or raw mailbox payloads in logs or documentation.
- Prefer the least expensive capable AI model and avoid unnecessary repeated calls.
- Never deploy, merge, change secrets, modify databases, or run migrations without explicit approval.

## 2. Repository and project boundaries

### Verified repository scope

The repository contains:

- root-level entry points such as `main.py`
- a Payment Agent implementation under `agents/payment_agent/`
- shared reusable platform modules under `shared/`
- a SQLite-backed data layer under `shared/data_layer/`
- local deployment scripts and Railway configuration files
- documentation under `docs/`
- tests under `tests/`

The README states that the project has grown into a reusable UCM AI platform, with the Payment Agent as the first agent plugged into it. The repository also includes other agent folders such as voicemail, chargeback, chief-of-staff, dashboard, and operations intelligence, but those should be treated as external systems unless they are directly verified in the code or docs for this repository.

### Explicit non-boundaries

The following must not be mixed into this repository unless an explicit, verified change is requested:

- Jason is not treated as a verified integration here unless code or docs prove a direct integration.
- Atlas is a separate personal trading project and must never be described as part of this repository.
- SCollect is Windows-only and has no verified API integration in this repo; it must not be described as directly integrated.
- Other UCM systems such as Voicemail Agent, Chargeback Agent, Chief of Staff, Cash Flow HQ, and Licensing HQ are treated as external systems unless the repository proves otherwise.

## 3. Git rules

- Work in small, scoped changes.
- Do not change unrelated files, code, or configuration.
- Do not commit, merge, push, or deploy without explicit approval.
- Preserve useful existing instructions and documentation context when editing.
- Prefer documentation-only updates for operational guidance unless a direct code change is explicitly requested.
- When editing docs, keep the repository’s existing tone and conventions intact.

## 4. Testing and verification

Testing at this repository level is primarily Python unit tests under `tests/`. The README and deployment docs refer to commands such as:

```bash
python main.py voicemail-test-sample
python main.py voicemail-health
python main.py voicemail-retry-transcriptions
python main.py shared-data-init
python main.py shared-data-status
```

For verification work:

- Validate the smallest relevant command or test first.
- Do not claim a fix or change is complete without fresh evidence.
- If a repository fact is uncertain, verify it in code or docs before documenting it.
- Keep validation narrow, explicit, and relevant to the documentation changes being made.

## 5. Railway and deployment safeguards

The repo contains Railway configuration and deployment notes, including `railway.json` and `docs/payment_agent_railway.md`.

Verified safeguards from the repo:

- The Payment Agent deployment expects a persistent `/data` volume for durable SQLite and health data.
- `DRY_RUN` is explicitly used for safe validation and non-production startup checks.
- Health files are used to report runtime status without writing secrets or payload data.
- The deployment docs state that deployment and credential changes must be validated before enabling non-dry-run operation.

Required safeguards:

- Do not deploy or change Railway configuration without explicit approval.
- Never change secrets, client IDs, tenant IDs, mailbox names, or webhook URLs in documentation or code without explicit approval.
- Treat database persistence, health checks, and credential rotation as safety-critical operations.
- Prefer dry-run and read-only validation before live processing.

## 6. Security and data privacy

This repository handles operational and financial email content, payment data, and message metadata. The documented safety expectations include:

- do not log tokens, secrets, or customer data
- sanitize filenames and generated paths
- avoid exposing raw email body content in diagnostics
- keep health outputs limited to non-sensitive status data
- use SQLite and file permissions in a deliberate, minimal way

This repository should never be used to:

- expose credentials in logs or documentation
- store raw sensitive payloads in public or shared files
- document customer account details beyond what a repository verification requires

## 7. AI-agent design standards

The repository’s existing standards describe reusable platform architecture under `shared/` with agent-specific services under `agents/`. The verified design pattern is:

- keep shared configuration, logging, SQLite helpers, Microsoft Graph integration, and Teams posting in `shared/`
- keep each business workflow in its own agent module under `agents/<agent_name>/`
- keep scheduling and persistence separate from business logic
- use idempotency and duplicate-protection patterns for repeated processing
- filter and validate external inputs before acting on them

AI agents in this repository should be designed to be:

- modular
- idempotent where possible
- observable via health and log output
- safe by default
- recoverable through non-destructive retry logic

## 8. Logging, retries, health checks, and monitoring

The repo’s docs describe structured logging, health checks, retry behavior, and duplicate protection. The verified pattern is:

- keep runtime status in local health files
- write bounded error summaries rather than raw payloads
- log startup, shutdown, skipped duplicates, warnings, retries, API failures, and unexpected failures
- retry transient API or network errors with bounded backoff where practical
- preserve service health status even when Graph becomes temporarily unavailable

This repository does not replace operational monitoring with informal logs alone. Health files, periodic runtime state, and explicit status checks are part of the verified operating model.

## 9. Cost optimization

The repository includes an AI budget guard with calendar-month hard limits and warnings. The README describes a local SQLite-based AI budget guard for OpenAI traffic and costs per transcription duration. This means cost controls are expected to be built into the product, not added as an afterthought.

For this repo:

- prefer the least expensive capable model for any AI task
- avoid repeated calls and redundant scans when a guarded or cached path already exists
- document cost-sensitive workflows in the relevant design and deployment docs
- treat AI spend guardrails as part of the engineering baseline

## 10. Documentation standards

This repository’s documentation should follow these rules:

- Prefer verified facts from code and existing docs.
- Use `To be documented` when a detail is still unknown.
- Keep architecture, deployment, and playbook docs aligned.
- Link to related docs using repository-relative paths.
- Preserve useful existing instructions from README and AGENTS when updating docs.
- Write in plain, operational language that supports production safety.

## 11. Incident and rollback procedures

When an incident occurs, the default approach should be conservative:

1. Stop the change or isolate the failing workflow.
2. Preserve logs and state necessary for investigation.
3. Determine whether the issue is code, configuration, credentials, or deployment-state related.
4. Revert the smallest possible change if safe and approved.
5. Confirm the system returns to a known-good state before re-enabling live processing.
6. Document the root cause and the operational fix for future runs.

Rollback expectations:

- prefer reversible edits and small deployments
- do not rewrite historical source data or production databases without explicit approval
- do not modify app state, secrets, or schedule configuration casually
- use read-only validation and dry-run checkpoints when possible

## 12. Required completion-report format

Every completed engineering task in this repository should include the following sections in the final report:

- Summary of change
- Files changed
- Verification performed
- Result and evidence
- Risks, assumptions, or unknowns
- Any follow-up required

If the change is documentation-only, include a clear statement that no application code, tests, configuration, deployment, database, or secrets were changed.

## 13. Non-negotiable safety rules

The following rules are non-negotiable:

- Never deploy, merge, change secrets, modify databases, or run migrations without explicit approval.
- Never expose credentials or customer data.
- Do not claim a repository fact without code or doc evidence.
- Do not invent integrations or features.
- Prefer small, reversible changes.
- Document unknowns explicitly as `To be documented`.
- Do not change Railway settings, environment variables, database files, or deployment definitions without explicit approval.
- Keep the repository focused on the verified Payment Agent scope unless a separate task explicitly expands it.

## 14. Helpful repo references

- [README.md](../README.md)
- [AGENTS.md](../AGENTS.md)
- [docs/payment_agent_railway.md](payment_agent_railway.md)
- [docs/shared_data_layer.md](shared_data_layer.md)
- [docs/voicemail_tracker_railway.md](voicemail_tracker_railway.md)

This playbook is intentionally conservative. It reflects the repository’s verified structure and operational safeguards, and it should be treated as the default standard for future engineering work in this project.
