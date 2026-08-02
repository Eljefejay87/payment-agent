# Deployment Guide

## Scope

This guide documents the deployment model that is actually represented in this repository. It is intentionally limited to verified artifacts: Railway configuration, the Dockerfile, local deployment helpers, and the documented runtime health and authentication patterns.

## Current verified deployment model

The repository includes:

- `Dockerfile`
- `railway.json`
- `scripts/railway_payment_agent_start.sh`
- local startup scripts under `scripts/`
- deployment notes in `docs/payment_agent_railway.md`
- voicemail-specific Railway readiness notes in `docs/voicemail_tracker_railway.md`

The Railway configuration builds from the Dockerfile and starts the Payment Agent through the repo’s start script. The docs state that the Dockerfile and `railway.json` are the current deployment shape, and the runtime persists data to a mounted `/data` directory.

## Critical deployment safeguards

The following protections are documented in the repository and must be treated as non-negotiable:

- use a persistent volume mounted at `/data`
- keep SQLite on durable storage so duplicate state survives redeploys
- do not enable live processing without confirming credentials and environment settings
- use `DRY_RUN=true` for safe validation where practical
- keep health files limited to non-sensitive status data
- never log tokens, credentials, or customer data

## Required environment and secret handling

The repository documents environment variables for the Payment Agent and voicemail agent, including Microsoft Graph and Teams variables. The exact production values must be managed through the approved deployment environment and must not be committed into the repository.

This repository does not provide a verified production secret manager or deployment automation beyond the repo-local config templates and Railway config files. The docs explicitly say that app credentials and secrets must be supplied through the approved credential process.

## Health and runtime verification

The Payment Agent docs describe a health check endpoint/command that reads a health JSON file and reports:

- service status
- Graph availability
- whether attention is required
- last successful run
- last failed job
- sanitized error category
- timestamp and process ID

This pattern is used to confirm operational status without writing sensitive content into the health report.

## Safe validation flow

The repository suggests a safe validation path with `DRY_RUN=true` and a temporary database/health path before live deployment. This is the preferred pattern for validating startup, health output, and graceful shutdown without scanning live mail or posting live Teams messages.

## Production decision gate

The deployment notes identify blockers that must be resolved before deployment:

1. attach a persistent Railway volume at `/data`
2. confirm the email app registration has required permissions
3. validate Graph authentication in a non-production environment
4. choose a Teams delivery path that is operationally supported
5. enable live processing only after configuration has been verified

## Rollback and recovery expectations

The repo documents a conservative recovery posture:

- prefer small, reversible change and deployment patterns
- keep duplicate protection stored in SQLite and durable storage
- use health state to detect runtime issues before they become silent failures
- if auth fails, record status and wait for the next scheduled run rather than blindly continuing with invalid state
- correct credentials through the approved secret process before re-enabling live operations

## Unknowns and placeholders

The following deployment details are specifically not documented as verified here and therefore remain `To be documented`:

- production deployment topology beyond the local Railway-based notes
- detailed failover plan beyond the repository docs
- final production schedule, ownership, and escalation matrix
- full production incident runbook for all UCM systems
