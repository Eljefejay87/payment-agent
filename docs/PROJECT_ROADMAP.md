# Project Roadmap

## Current verified state

This repository is currently a Payment Agent project for United Capital Management / United Account Services, with reusable platform infrastructure under `shared/` and agent-specific logic under `agents/`.

The verified scope currently includes:

- Payment Agent email scanning, parsing, duplicate protection, and reporting
- a shared data layer for normalized records and review workflows
- a voicemail tracker implementation in a limited Phase 1 form
- local and Railway deployment notes for the Payment Agent and Voicemail Tracker
- a growing shared platform architecture intended for future UCM agents

## Current roadmap themes

### 1. Production-safe payment processing

The primary verified objective is to keep the Payment Agent operating safely and predictably in Microsoft 365, with SQLite-based duplicate protection and controlled Teams reporting.

### 2. Shared platform maturity

The repo documents an intention to evolve into a reusable UCM AI platform. The shared data layer and common utilities represent this direction, but those features remain grounded in controlled and verified operational patterns.

### 3. Safe deployment readiness

The repo contains explicit deployment safeguards, including persistent `/data` volume guidance, health checks, dry-run validation, and careful Graph auth handling. This is a verified operational priority.

### 4. Operational visibility

The repository emphasizes health output, logging, runtime status, and duplicate protection. These are documented as core engineering controls rather than optional extras.

## Known unknowns

The following roadmap items remain `To be documented` because they are not verified in this repository:

- broader UCM enterprise-wide automation roadmap
- final production ownership and runbook across all agents
- formal retention, compliance, and incident escalation policy
- large-scale external integration roadmap beyond the existing docs

## Safety-first roadmap principles

The repository’s roadmap should remain aligned with these non-negotiable principles:

- production safety over speed
- small reversible changes
- no unapproved database or secret changes
- limited, verified scope
- explicit documentation for unknowns
