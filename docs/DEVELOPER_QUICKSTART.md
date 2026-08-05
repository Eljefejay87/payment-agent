# Developer Quickstart

## Purpose

This guide is a short, practical entry point for local development and safe validation. It is intentionally scoped to what is verified in this repository and should be read together with [docs/UCM_ENGINEERING_PLAYBOOK.md](UCM_ENGINEERING_PLAYBOOK.md), [docs/SYSTEM_ARCHITECTURE.md](SYSTEM_ARCHITECTURE.md), [docs/CODING_STANDARDS.md](CODING_STANDARDS.md), and [docs/DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md).

## Required tools and setup

Verified repository expectations:

- Python is used for the runtime and tests.
- A local virtual environment is the documented setup path.
- The repo expects environment configuration from `.env` or environment variables.
- The project uses SQLite and Microsoft Graph / Teams integration patterns, so local validation should be performed carefully.

## Safe local setup steps

The repository README documents a basic setup flow:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

After that, review and populate the required settings in `.env` before any live or production-impacting action.

## Common commands

The repository documents these commands and workflows:

```bash
python main.py ai-budget-status
python main.py ai-budget-pause
python main.py ai-budget-resume
python main.py ai-budget-reset --confirm
python main.py voicemail-test-sample
python main.py voicemail-health
python main.py voicemail-retry-transcriptions
python main.py shared-data-init
python main.py shared-data-status
```

## Read-only or low-risk commands

These are the safest starting points for local inspection or verification:

- `python main.py ai-budget-status`
- `python main.py voicemail-health`
- `python main.py shared-data-status`
- `python main.py voicemail-test-sample` for sample-data validation

These are low risk because they are documented as read-only or non-destructive operational checks.

## Commands that require approval

The repository documentation is explicit that the following should not be done without approval:

- deploy or change Railway configuration
- change secrets, tenant IDs, client IDs, mailbox names, webhook URLs, or credentials
- modify SQLite databases or run migrations
- merge, push, or deploy changes
- enable live processing without validating the configuration

## How to run tests

The repository uses Python tests under `tests/`. Use the smallest relevant test or validation command first. The docs also reference local validation and dry-run patterns rather than broad live operations.

## How to confirm dry-run behavior

The deployment docs and README describe dry-run support as a safe validation path. Use dry-run or a temporary local environment when validating startup and behavior before any live processing or Teams posting.

## Recommended reading order for developers

1. [00_START_HERE.md](../00_START_HERE.md)
2. [AGENTS.md](../AGENTS.md)
3. [README.md](../README.md)
4. [docs/UCM_ENGINEERING_PLAYBOOK.md](UCM_ENGINEERING_PLAYBOOK.md)
5. [docs/SYSTEM_ARCHITECTURE.md](SYSTEM_ARCHITECTURE.md)
6. [docs/CODING_STANDARDS.md](CODING_STANDARDS.md)
7. [docs/DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md)

## Notes

Use `To be documented` for anything not verified in this repository. This guide does not replace the engineering playbook or deployment guide.
