# UCM AI Agent Standards

Use these standards for every UCM AI Operations agent unless the user explicitly overrides them.

## Build Philosophy

- Build production-ready services, not throwaway scripts.
- Reuse existing code in `shared/` and prior agents before creating new modules.
- Keep agents modular so future UCM agents can share configuration, logging, database access, Microsoft Graph, Teams, scheduling, parsers, and utilities.
- Favor readable, explicit code over clever abstractions.
- Do not rebuild or replace working functionality without a clear request.

## Architecture

- Put each agent under `agents/<agent_name>/`.
- Put reusable platform code under `shared/`.
- Keep business workflows in agent services.
- Keep external APIs in integrations.
- Keep persistence in database modules.
- Keep scheduling separate from processing logic.
- Keep helper functions in utilities.

## Configuration

- Load settings from `.env`.
- Keep `.env.example` updated with placeholders.
- Never hardcode secrets, client IDs, tenant IDs, webhook URLs, mailbox names, or database paths.
- Validate required settings at startup with clear messages.

## Security

- Use least-privilege Microsoft Graph permissions.
- Never log tokens, secrets, webhook URLs, or unnecessary sensitive information.
- Validate external input, especially email bodies and API payloads.
- Sanitize filenames and generated paths.
- Prevent duplicate processing with database constraints or processed-record tables.

## Logging and Errors

- Log startup, shutdown, successful processing, skipped duplicates, warnings, retries, API failures, database failures, and unexpected errors.
- Catch per-record failures so one bad item does not stop the batch.
- Retry transient API or network failures with bounded backoff when practical.

## Project Expectations

- Include `README.md`, `requirements.txt`, `.env.example`, `.gitignore`, and tests for parsing, configuration, database behavior, duplicate detection, and mocked integrations.
- Keep changes scoped to the requested task.
- Report validation performed and any skipped checks.
