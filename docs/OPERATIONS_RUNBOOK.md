# Operations Runbook

## Purpose

This runbook summarizes the operational checks and response patterns that are explicitly documented in the repository. It is a practical guide for safe troubleshooting and should be used together with [docs/UCM_ENGINEERING_PLAYBOOK.md](UCM_ENGINEERING_PLAYBOOK.md) and [docs/DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md).

## Microsoft Graph authentication issues

The deployment docs describe Graph authentication recovery behavior for the Payment Agent. If Graph authentication fails or a token is rejected, the documented approach is conservative:

- record the service and Graph status rather than silently continuing as if the service is healthy
- avoid logging tokens or Graph response bodies
- correct the underlying credential issue through the approved process before re-enabling live work
- use health output and status checks to confirm the service state

## Teams notification issues

The repository documents Teams delivery as an optional reporting pathway. If Teams notifications fail:

- verify whether the configured delivery path is webhook-based or Graph-based
- avoid assuming a message was sent just because the workflow reached the posting step
- use dry-run or low-risk validation before enabling live posting again
- preserve non-sensitive status information rather than exposing message content or secrets

## Dry-run vs live-run checks

The README and deployment docs identify dry-run as the safe default for validation. Before any production-impacting action:

- confirm whether the current run is dry-run or live
- verify the environment and database paths
- check that health and persistence files are using the expected location
- avoid scanning live mail or posting live Teams messages without confirmation

## Railway health and persistence checks

The repo’s deployment docs emphasize that Railway should use a persistent `/data` volume for durable state. Operational checks should include:

- verifying that the runtime state and health files are stored in a persistent location
- confirming that SQLite data survives redeploys or restarts
- checking that duplicate protection state is not being lost because of non-persistent storage

## SQLite and database persistence checks

The Payment Agent and shared data layer rely on SQLite. Before changing or validating database behavior:

- confirm the configured database path
- avoid modifying production databases without approval
- use the documented health and status commands where possible
- treat database persistence as safety-critical

## Safe troubleshooting sequence

Use this order when investigating an issue:

1. Confirm the current mode: dry-run or live.
2. Review the health and runtime status output.
3. Check whether the issue is authentication, persistence, or delivery-related.
4. Avoid making production-impacting changes until the issue is understood.
5. Prefer small, reversible checks and read-only validation.
6. Escalate if the issue affects credentials, live processing, or persistence.

## Escalation points

Escalate to the appropriate owner or approver when:

- the issue affects credentials, Microsoft Graph access, or Teams delivery
- the issue affects persistence or duplicate protection state
- a production deployment or non-dry-run enablement is being considered
- the root cause is unclear and a live change could create operational risk

## Rollback guidance

Rollback should be conservative and reversible:

- stop the change or isolate the failing workflow
- preserve logs, health data, and state needed for investigation
- revert the smallest possible change when safe and approved
- verify the system returns to a known-good state before re-enabling live processing
- do not rewrite or recreate historical production data without approval

## Warnings

Warning: do not perform production-impacting actions without explicit approval. This includes deployment, secret rotation, database changes, enabling live processing, or changing Railway settings.

## To be documented

The following operational details are not fully verified in this repository and remain `To be documented`:

- production incident ownership beyond the repo-level guidance
- detailed failover and escalation procedures for all UCM systems
- full production support coverage for every external dependency
