# Known Limitations

## Purpose

This document records the repository boundaries and limits that are explicitly verified or clearly unverified. It is intended to reduce accidental overclaiming by future AI agents and developers.

## Unverified integrations

The repository does not provide evidence of direct integrations with:

- Jason
- Atlas
- SCollect
- any broader UCM orchestration layer not explicitly verified here

These should be treated as external or unverified unless a future code or documentation change proves otherwise.

## Broader UCM systems are external to this repository

The repository includes references to other UCM systems such as Voicemail Agent, Chargeback Agent, Chief of Staff, Cash Flow HQ, and Licensing HQ, but they are treated as external systems unless the repository itself verifies a direct integration.

## SCollect limitations

SCollect is documented as Windows-only and has no verified API integration in this repository. It should not be described as directly integrated.

## Atlas separation

Atlas is a separate personal trading project and must not be mixed into this repository.

## Known operational gaps

The current documentation explicitly leaves several areas as `To be documented` because they are not verified in this repository:

- full production topology across all UCM systems
- formal retention and archival policy
- detailed SLA and uptime targets
- cross-agent approval workflows beyond the review patterns already present
- production ownership and escalation matrix
- broader UCM-wide governance beyond this repo

## Features that are not implemented here

The repository docs indicate that some capabilities are not part of the current verified scope, including:

- full Teams posting beyond the documented workflow patterns
- full production-grade workflow automation beyond the current Payment Agent behavior
- direct SCollect integration
- direct Atlas integration
- a company-wide UCM operating system

## Non-goals for this repository

This repository is not intended to serve as a substitute for:

- a future enterprise UCM operating system
- a full production support and incident management platform
- a universal integration layer for every UCM service
- a general-purpose project for unrelated external systems

## Recommended handling for unknowns

When a future task depends on a detail that is not verified here, write `To be documented` rather than inventing an implementation or integration.
