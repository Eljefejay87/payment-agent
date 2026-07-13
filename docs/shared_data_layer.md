# Shared UCM Data Layer Foundation

## Scope

This package defines compatibility contracts for normalized UCM operational records. It does not migrate production data, replace agent-specific models, add a production database, change schedules, or alter any current Notion, Outlook, Teams, Google Sheets, or SQLite write path.

The implementation lives in `shared/data_layer/` because the repository already uses `shared/` for cross-agent database, configuration, logging, Microsoft Graph, and Teams components.

## Existing Data Flow Inventory

| Agent or surface | Input | Current storage or output | Primary identifier | Status field | Review method | Dashboard usage |
| --- | --- | --- | --- | --- | --- | --- |
| Cash Flow HQ | Outlook bill email plus PDF/body parsing; manual inputs | Notion Cash Flow HQ data source | Outlook `internetMessageId`/message ID and Email Link; fallback duplicate check is vendor + amount + due date | Notion `Status`: `Upcoming`, `Needs Review`, `Paid`; formula-based Due Status | `Needs Review`, review reasons, confidence, and Notion views | Dashboard reads Notion rows for forecast totals, needs attention, and upcoming bills |
| ICR Remit Agent | Local ICR remit workbook/CSV and liquidation-rate file | Cash Flow HQ Notion obligation, SQLite `icr_remit_imports`, Outlook draft | Existing SQLite uniqueness: broker + remit week + remit filename | `ICRRemitResult.status`, default `Pending`; Notion obligation is `Upcoming` | Owner reviews source totals and Outlook draft before send | Cash Flow Forecast reads the resulting Notion obligation; Weekly Remit card has separate sent-batch status |
| Operations Intelligence | SCollect dashboard screenshots received through Teams | Local screenshots/reports and SQLite `ops_screenshots`/`ops_reports`; optional Teams summary | Teams message ID + image ID; screenshot SHA-256; report uniqueness by screenshot hash | Screenshot status plus report quality/manual-review fields | Missing fields, manual-review notes, `manual_review`, `approved_at`, edited-field audit, dashboard review route | Operations card, trends, historical reports, and manual-review queue read SQLite |
| Voicemail Tracker | Vaspian voicemail emails and attachments from Outlook | Phase 1 returns/logs parsed records; no durable voicemail table found | Source Outlook email ID | No persisted status field found | No approval workflow found in this repository | No voicemail dashboard dependency found |
| Attendance Tracker | Not found in this repository | External Manager Monitoring/Google Sheets surface referenced by dashboard | Not found locally | External feed values such as attendance submitted | External checklist UI/feed | Dashboard consumes the external checklist status feed |
| Manager Monitoring | Not found in this repository | External Apps Script/Google Sheet URLs configured for the dashboard | Not found locally | External checklist status such as Ready/Complete/Needs Checklist | External checklist and sheet | Daily Checklist card reads its external status feed |
| UCM Dashboard | SQLite agent data, existing Cash Flow HQ Notion rows, external checklist feed | Local HTML/JSON responses; no new operational storage | Agent-specific source identifiers | Display-only normalized labels derived from source data | Operations review routes can save/approve/reprocess existing reports | Aggregates payment, remit, Cash Flow, checklist, and Operations data |
| Outlook/Teams notification workflows | Agent-specific messages, drafts, reports, and attachments | Microsoft Graph mail drafts/sends and Teams chat/channel posts; optional Teams webhook | Stable Graph message/chat/channel IDs where available | External HTTP/Graph result plus agent-specific sent flags | Draft-first owner review for ICR; Operations quality gate before posting | Delivery results are reflected indirectly through agent storage/status |

### Existing timestamps and duplicate controls

- Cash Flow HQ uses Outlook received timestamps in parsed email records. Notion stores page timestamps externally. Duplicate detection first checks Email Link, then vendor + amount + due date.
- ICR import history stores `created_at` and `updated_at` in UTC ISO-8601 form. SQLite enforces broker + remit week + filename uniqueness.
- Operations screenshots and reports store created/updated timestamps. Screenshots are protected by message/image and SHA-256 uniqueness; reports by screenshot hash.
- Voicemail records currently store received date/time strings derived from the Outlook message and preserve the source email ID.
- Weekly remit sent batches use broker + week start uniqueness and created/updated timestamps.

## Shared Contract

`shared.data_layer.SharedRecord` is a frozen typed dataclass with schema version `1`. It supports:

- stable record and source identifiers;
- normalized record, source, status, priority, and review enums;
- timezone-aware created/updated datetimes;
- optional effective date, owner, action, confidence, source URL, and summary;
- `Decimal` monetary values and an explicit currency;
- agent-specific metadata without expanding the universal schema;
- a deterministic idempotency key.

Serialization emits Decimal values as strings and datetimes as ISO-8601 values. Deserialization restores typed values. Optional fields remain optional.

### Currently justified record types

- `bill`: Cash Flow HQ bills and obligations.
- `remit`: ICR remit imports.
- `voicemail`: parsed Voicemail Tracker records.
- `collector_metric`: Operations Intelligence metrics.
- `notification`: Outlook/Teams delivery records.
- `review_item`: Operations and Cash Flow review work.
- `agent_run`: shared execution history.

Attendance and manager-task record types are intentionally deferred until their external repositories and contracts are inspected.

## Normalized Status Semantics

Shared statuses are `new`, `upcoming`, `due`, `past_due`, `in_progress`, `completed`, `paid`, `failed`, `cancelled`, and `needs_review`. Existing production-facing values are unchanged.

| Existing system value | Normalized status | Notes |
| --- | --- | --- |
| Cash Flow HQ `Upcoming` | `upcoming` | Direct mapping |
| Cash Flow HQ `Needs Review` | `needs_review` | Review status becomes `pending` |
| Cash Flow HQ `Paid` | `paid` | Direct mapping |
| Cash Flow HQ `Past Due` | `past_due` | Used when present as an explicit status |
| ICR `Pending` | `new` | Import exists but agent-specific status is not yet completed |
| ICR `Completed` | `completed` | Direct mapping when present |
| ICR `Failed` | `failed` | Direct mapping when present |
| Operations quality failure/manual review | `needs_review` | Reserved for a future Operations adapter |
| Weekly remit `sent` | `completed` | Documented only; no adapter added in this phase |

Unknown current status strings normalize conservatively to `new` and remain preserved in metadata as `existing_status`.

Priorities are `low`, `normal`, `high`, and `critical`. Review statuses are `not_required`, `pending`, `approved`, `rejected`, and `resolved`.

## Repository Interface

`SharedRecordRepository` is storage-agnostic and defines:

- `upsert(record)`
- `get(record_id)`
- `get_by_source(source_system, source_record_id)`
- `get_by_idempotency_key(idempotency_key)`
- `list(filters)`
- `mark_reviewed(...)`
- `update_status(...)`
- `record_agent_run(...)`
- `list_agent_runs()`

`InMemorySharedRecordRepository` is provided only for tests and local integration prototyping. It prevents duplicates using the idempotency key first and source-system/source-record identity second. The dashboard defaults to an empty instance; no production agent writes normalized records to it.

## Read-Only Dashboard Service

`agents/dashboard/shared_data.py` provides `ReadOnlyDashboardDataService`. It accepts the storage-agnostic repository interface and exposes record/status counts, action and review records, upcoming and past-due bills, recent remits, recent/failed agent runs, source-system queries, date-range queries, and Decimal-safe financial aggregates.

The current HTTP integration exposes read-only endpoints:

- `GET /api/shared-dashboard`
- `GET /api/needs-review`
- `GET /api/needs-review/<shared-record-id>`
- `GET /api/agent-health`

Review-list filters are `record_type`, `source_system`, `priority`, `review_status`, `action_required`, `date_from`, and `date_to`. `page` and `page_size` provide bounded pagination. Decimal values serialize as strings and datetimes as ISO-8601 values.

The dashboard home page includes a `Needs Review` section with unresolved, critical/high, past-due, failed-run, oldest-age, and top-item summaries. It also makes normalized upcoming bills, past-due bills, recent remits, and agent health available through the shared dashboard payload. Existing Cash Flow HQ and Operations cards are unchanged.

`GET /needs-review` renders the matching read-only list view and supports the same filters without adding approval or write controls.

### Needs Review inclusion and ordering

A normalized record is included when its status is `needs_review`, its review status is `pending`, it has a non-empty action requirement, or its numeric confidence is below the existing dashboard review threshold of 72%. Failed agent runs are projected as read-only operational review items.

Items sort by critical priority, high priority, past-due/failed state, then oldest creation time. Dashboard metadata is allowlisted; source-file paths, raw email bodies, credentials, tokens, and unknown metadata fields are omitted.

This phase supports normalized Cash Flow HQ bills, ICR remit records, and agent-run records through dependency-injected fixture/in-memory loading. It does not migrate history, add persistent shared storage, read live Notion/Outlook/Teams data, or expose approval, rejection, update, delete, send, or payment actions.

## Adapter Contracts

### Cash Flow HQ

`normalize_cash_flow_bill(BillCandidate)` creates a `bill` record and preserves vendor, invoice number, category, payment method, AutoPay/manual classification, original status/confidence, review reasons, field sources, Outlook IDs, optional Notion page ID, and the existing/fallback duplicate key in metadata.

The shared amount remains a `Decimal`. The effective date is the due date. Outlook internet message ID is preferred as the source identifier.

### ICR Remit

`normalize_icr_remit(ICRRemitResult)` creates a `remit` record. The shared amount is Due to Client because that is the current Cash Flow HQ obligation amount. Metadata preserves AgencyFee/Due to Agency, ClientFee/Due to Client, total collected, broker/contact, remit week, week ending, source file, optional Notion production URL, optional Outlook draft reference, and the existing broker/week/filename duplicate key.

These adapters do not execute existing writes or store normalized records.

## Idempotency Rules

Keys use canonical JSON plus SHA-256 and a domain namespace. Text is trimmed, case-folded, and whitespace-normalized. Decimal trailing zeros are removed. Aware datetimes are converted to UTC. Dates use ISO-8601. Paths use the case-folded filename.

- Cash Flow HQ: vendor + amount + due date + stable Outlook/source identifier.
- ICR Remit: broker/contact remit identity + remit week + total collected + source filename identity.

These helpers do not weaken or replace the current Notion or SQLite duplicate checks. Adapters preserve the existing duplicate key in metadata when supplied.

## Agent Run Contract

`AgentRunRecord` captures agent name, run ID, aware start/completion timestamps, normalized status, record counts, review count, optional error, dry-run flag, and external services used. It is not connected to existing schedulers in this phase.

## Compliance and Compatibility Notes

- Financial amounts, voicemail content, consumer/account information, and communications are sensitive operational data.
- The contract stores only normalized fields supplied by an adapter; agent-specific data stays in metadata.
- No tokens, tenant IDs, client IDs, mailbox names, recipients, or webhook URLs are added to the shared models.
- No retention policy or production authorization boundary is created here. Those must be defined before a persistent shared repository is introduced.
- A future persistent implementation must add access control, encryption/host protections, retention, audit events, and migration/reconciliation procedures.

This is an operational compliance review, not legal advice.

## Next Recommended Step

Add controlled human approval actions for the Needs Review queue with audit logging and strict write safeguards.
