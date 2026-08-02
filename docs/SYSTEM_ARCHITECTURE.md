# System Architecture

## Overview

This repository is a Python-based UCM Payment Agent project with operational workflows for Microsoft 365 email intake, data storage in SQLite, duplicate protection, and Teams reporting. The current verified architecture is documented in the project README and the deployment notes. This document is intentionally conservative and limited to facts supported by the repository.

## Execution model

The root-level `main.py` remains a compatibility wrapper, while the actual Payment Agent workflow is implemented under `agents/payment_agent/`.

The project is organized around a reusable platform pattern:

- `shared/` contains reusable configuration, logging, database helpers, scheduler support, Microsoft Graph utilities, Teams integration, and text utilities.
- `agents/` contains business-facing agent implementations.
- `database/`, `logs/`, `reports/`, and `screenshots/` are operational runtime locations described in the repo.

## Verified runtime responsibilities

### Payment Agent

The Payment Agent verifies and processes Microsoft 365 email messages whose subject contains `Online Payment -` and whose sender matches the configured sender email address.

It performs the following verified actions:

- scans a configured mailbox for payment emails
- extracts account number, payment type, note, payment date, payment amount, and received time
- stores processed message IDs and payment records in SQLite
- prevents duplicate processing by message ID
- optionally sends a daily leadership Teams report
- optionally sends real-time alerts for each payment
- optionally stores email HTML snapshots locally

### Shared platform services

The shared code contains cross-cutting services that the project explicitly expects future agents to reuse. Based on the repository docs, those include:

- environment and configuration helpers
- SQLite access patterns
- logging setup
- scheduling wrappers
- Microsoft Graph client routines
- Microsoft Teams posting helpers
- reusable text and utility helpers

### Shared data layer foundation

The repository also contains a shared data layer used for normalized operational records, with a durable SQLite repository for a shared data database. According to the docs, this layer does not replace existing source data stores; it provides a compatibility layer and idempotency-friendly normalized records for dashboard and review workflows.

## Storage and persistence

The repository uses SQLite persistently for operational state. The README and docs identify:

- the Payment Agent database for processed payment records and duplicate protection
- a shared data SQLite database for normalized review/dashboard data
- local runtime state and health files for voicemail tracking and deployment monitoring
- local persistent directories used for logs, reports, and screenshots

The docs specifically warn that redeploys can erase duplicate state if the database is not kept on a mounted `/data` volume in Railway.

## External dependencies

The repository directly depends on the following verified technologies:

- Python
- SQLite
- Microsoft 365 / Outlook mailbox access via Microsoft Graph
- Microsoft Teams notifications via webhook or Graph-based flows
- Railway deployment configuration

The repository does not provide evidence of direct integrations with:

- Jason
- Atlas
- SCollect
- any unverified external orchestrator or data source

Those remain external or out-of-scope unless specific repository changes verify them.

## Safety and failure behavior

The repo documents a conservative operational posture:

- dry-run mode is used for safe testing
- duplicate protection is maintained in SQLite
- health files record runtime status without secrets or raw payloads
- Graph auth failures are treated as service health issues, not as silent success
- retries are bounded and operationally safe

## Deployment topology

The project’s deployment documentation describes a Railway service that runs the payment-agent installation and uses a persistent volume for durable runtime state. The same repository includes a voicemail Railway readiness doc that explicitly notes a persistent `/data` state model and the need to validate credentials before live operation.

## Planned boundaries for future work

This repository is explicitly designed to support future agents under `agents/`, but the current documented boundary is that each agent must reuse the shared platform rather than re-implementing common infrastructure. Future work should remain modular and should not widen the repo’s scope beyond what is verified and approved.

## Unknowns and placeholders

The following details are intentionally left as `To be documented` because they are not verified in this repo:

- full production topology across all UCM systems
- formal retention and archival policy
- detailed SLA and uptime targets
- cross-agent approval workflows beyond the detected review patterns
- exact broader UCM orchestration relationship with Jason
