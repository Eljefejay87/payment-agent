# Chief of Staff Phase 1 Inventory

## Scope

The Chief of Staff foundation inventories existing UCM components and reports
persisted status for Cash Flow HQ and Voicemail Tracker. Its status command is
read-only: it does not call agents, trigger scans or jobs, or connect to Teams.

Run the inventory with:

```bash
python main.py chief-of-staff status
```

## Repository inventory

| Component | Repository location | Current entry points | Phase 1 boundary |
| --- | --- | --- | --- |
| Payment Agent | `agents/payment_agent/` | Root CLI commands for scans, reports, and diagnostics | Inventory diagnostics only; do not run scans or reports |
| Cash Flow HQ | `agents/cash_flow_hq/` | Root `cash-flow-*` commands | Register preview/debug commands only |
| Voicemail Tracker | `agents/voicemail_tracker_agent/` | Root `voicemail-*` commands | Register sample/intake inspection only; do not scan from status |
| Weekly Remit Agent | `agents/weekly_remit_agent/` | Root `remit-*` commands | Register local file readiness only |
| ICR Remit Import | `agents/icr_remit_agent/` | Root `icr-remit-import` command | Dry-run is the only eligible future action |
| Operations Intelligence | `agents/operations_intelligence_agent/` | Root `ops-*` commands | Register setup/local inspection only |
| Shared Data Layer | `shared/data_layer/` | Root `shared-data-*` commands | Status and default dry-run sync only |
| UCM Admin Dashboard | `agents/dashboard/` | Root `dashboard` command | Treat as an interface, not an autonomous specialist |
| Attendance Tracker | External/not found | None in this repository | Do not invent an adapter |
| Manager Monitoring | External/not found | None in this repository | Do not invent an adapter |

## CLI routing

The repository uses `main.py` as a prefix-based command router. The Chief of
Staff follows that convention: the root command removes `chief-of-staff` and
delegates the remaining `status` command to `agents/chief_of_staff/main.py`.

## Read-only status adapters

Both adapters expose only overall status, last attempted and successful runs
when persisted, last-run outcome, summary metrics, and a current error when the
latest persisted run failed.

- Cash Flow HQ reads normalized bill counts and shared-sync run history from the
  existing shared SQLite database. It reports total bills, Needs Review, past
  due, and upcoming counts.
- Voicemail Tracker reads its agent-local `voicemail_status.json` snapshot. A
  missing file produces a clear `Not Yet Run` outcome; a corrupt file produces
  `Error`. Chief of Staff never scans Outlook to fill a gap.

The SQLite source opens the existing database using `mode=ro`, enables
`PRAGMA query_only`, and uses a one-second timeout. It never initializes schemas
or calls repository write methods.

The Voicemail Tracker writes its snapshot only from the existing live
`voicemail-scan-once` execution path. Writes use a temporary file, flush and
fsync it, set private `0600` permissions, and atomically replace the prior file.
The snapshot contains only last attempted/successful timestamps, outcome,
pending callback count, records processed, and a generic error message. It
contains no audio, message content, phone numbers, account numbers, identifiers,
or debtor data. Because Phase 1 has no callback-resolution workflow, pending
callbacks currently means the number of records found in the latest successful
scan.

## Callback resolution

Callback resolution is an explicit local action against the existing private
Voicemail Tracker runtime state. It does not contact a consumer, scan Outlook,
send Teams messages, or expose voicemail contents.

```bash
python main.py chief-of-staff callbacks
python main.py chief-of-staff complete-callback --voicemail-id "ID_FROM_LIST" --confirm
```

`callbacks` lists pending callback IDs and timestamps only. `complete-callback`
requires `--confirm`, marks exactly one existing record complete, and updates
the non-sensitive pending callback count used by `chief-of-staff status`.
