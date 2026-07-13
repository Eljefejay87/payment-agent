# UCM AI Operations Session

## Current Project Status

The Payment Agent is stable end-to-end: it detects real payment emails, parses them, prevents duplicates, saves them to SQLite, posts professional Teams notifications to the UCM Leadership chat, and cleans up processed/duplicate emails.

The Weekly Remit Agent V1 supports ICR weekly remit delivery, file archiving, duplicate prevention, and owner notification.

The local UCM Admin Dashboard V1 has been added for browser-based agent status and simple owner actions.

The ICR remit import workflow now parses `.xlsx` and `.csv` exports, totals the `AgencyFee` and `ClientFee` columns as Due to Agency and Due to Client, blocks duplicate imports, creates the Cash Flow HQ obligation, and prepares an Outlook draft. Cash Flow HQ also has debug, diagnostic, and patch commands for the Notion `Action Required` formula.

Automated verification is passing: 63 focused dashboard/shared/review/SQLite/sync/ICR tests and all 181 repository tests pass.

## Completed Work

- Built the Payment Agent for Microsoft 365 online payment notification emails.
- Added SQLite payment tracking and duplicate prevention.
- Added dry-run Teams reporting.
- Added daily summary reporting.
- Refactored the project into:
  - `agents/payment_agent/`
  - `shared/`
- Added shared modules for configuration, logging, database access, scheduling, Microsoft Graph, Microsoft Teams, and utilities.
- Added local validation tests.
- Created repository guidance files for future short prompts.
- Completed first end-to-end payment processing for `Online Payment - B123440`.
- Removed temporary debug logging after successful validation.
- Added macOS LaunchAgent support for daily operation using the project virtual environment.
- Added Microsoft Graph group chat posting with delegated `ChatMessage.Send` and a `debug-teams-message` test command.
- Added read-only `debug-list-teams-chats` command to find Teams chat IDs using delegated `Chat.ReadBasic`.
- Split email and Teams Graph configuration so mailbox scanning uses `MS_GRAPH_*` and Teams chat operations use separate `TEAMS_GRAPH_*` tenant/app settings.
- Updated Teams payment notification formatting to a professional field-based message using extracted payment data.
- Expanded payment subject detection to include approved debit-card and credit-card payment emails.
- Expanded payment subject detection to include approved `Credit or Debit Card` payment emails.
- Removed temporary MSAL/auth debug logging after Teams notifications were confirmed working.
- Added post-processing email cleanup: successful payments move to `Processed Payments`, duplicate emails move to `Duplicate Payments`.
- Added Weekly Remit Agent V1 under `agents/weekly_remit_agent/`.
- Added ICR remit file detection for `United Remit` and `United Liq` Excel files.
- Added weekly remit SQLite duplicate tracking by broker and week.
- Added Microsoft Graph broker email sending with Excel attachments.
- Added owner-only Teams confirmation after successful broker email send.
- Added sent/duplicate folder movement by sent date.
- Added remit settings to `.env.example` and local `.env`.
- Added Weekly Remit Agent setup and command instructions to `README.md`.
- Added tests for remit file detection, duplicate blocking, send recording, and file movement.
- Added local UCM Admin Dashboard under `agents/dashboard/`.
- Added browser view for Payment Agent status, Weekly Remit status, and future-agent placeholders.
- Added dashboard actions for payment scan, weekly remit send, and opening the ICR drop folder.
- Added double-click Mac launcher `Start UCM Dashboard.command`.
- Added dashboard tests and README instructions.
- Verified dashboard responds locally at `http://127.0.0.1:8080`.
- Fixed Payment Agent duplicate protection to use stable `internetMessageId` plus payment fingerprint, because Graph message IDs can change after mail moves.
- Updated dashboard/daily totals to dedupe duplicate payment rows and understand real payment dates like `6/29/2026`.
- Removed duplicate payment rows created during dashboard scan testing, leaving the three real payment records.
- Started Payment Agent manually in the current Codex session after unloading the broken macOS LaunchAgent crash loop.
- Added `Start Payment Agent.command` as a double-click launcher for manual Payment Agent runs.
- Payment Agent processed one new payment for account `B119391` and moved duplicate candidate emails to `Duplicate Payments` without resending duplicate Teams notifications.
- Updated the macOS LaunchAgent installer to run from `~/Library/Application Support/UCM/payment-agent` instead of `Documents`, avoiding the macOS privacy error that prevented automatic startup.
- Updated Payment Agent discovery to scan only the Inbox so emails moved to `Processed Payments` or `Duplicate Payments` are not rediscovered every cycle.
- Confirmed `python main.py scan-once` now finds 0 candidate emails after cleanup folders are populated.
- Updated the UCM Admin Dashboard network binding from localhost-only to `0.0.0.0` while preserving localhost access.
- Dashboard startup now logs Local URL, LAN URL, and Tailscale URL using the configured dashboard port.
- Updated dashboard environment examples/docs and the active local `.env` dashboard host to `0.0.0.0`.
- Added Mac-friendly LAN IP fallback detection using `ifconfig` when socket/ipconfig lookup is unavailable.
- Confirmed dashboard compile checks and `tests.test_dashboard` pass after the networking update.
- Started United Account Services Voicemail Tracker Agent Phase 1 under `agents/voicemail_tracker_agent/`.
- Added safe sample-data voicemail intake mode and Outlook Inbox voicemail detection using the existing shared Microsoft Graph client.
- Added voicemail parsing for received date/time, phone number, duration, transcript, audio attachment reference, and source email ID.
- Added root commands `python main.py voicemail-test-sample` and `python main.py voicemail-scan-once`.
- Added voicemail Phase 1 tests and README/.env.example documentation.
- Configured local voicemail intake settings for Vaspian: mailbox `Jaye@unitedaccountservices.com`, sender `noreply@vaspian.com`, and subject filter `Voice message from`.
- Added macOS LaunchAgent helper scripts for the Operations Intelligence Agent so the 5:20 PM SCollect screenshot monitor can run automatically.
- Fixed Operations Intelligence OCR region mapping for the clear SCollect dashboard screenshot so the whiteboard, money summary, and portfolio table are read from the correct screen areas.
- Added an Operations Intelligence portfolio-table fallback so Accounts Worked, Attempts, RPC/Live Contacts, and Contact Rate can be recovered when the top summary cards are partially unreadable.
- Added `VMAR` to the Operations Intelligence collector allowlist.
- Added explicit `ops-post-report` command to reprocess a date and post the latest quality-passing corrected Operations report to Teams.
- Added `ops-post-image` so a corrected report can be posted from a specific clear local screenshot when Teams saved multiple or compressed images for the same day.
- Updated Weekly Remit Agent to recognize `.csv` exports in addition to `.xlsx/.xls`.
- Added a professional ICR broker email template for weekly remit delivery.
- Fixed UCM Admin Dashboard remit status so a remit sent for the current week shows `Sent` after files are archived, instead of falling back to `Waiting` because the drop folder is empty.
- Added `icr-remit-import` with `.xlsx`/`.csv` parsing, totals, SQLite duplicate prevention, Cash Flow HQ obligation creation, Outlook draft creation, and a non-destructive `--dry-run` mode.
- Added `cash-flow-debug-action-required`, `cash-flow-diagnose-action-required`, and `cash-flow-patch-action-required` for inspecting, diagnosing, and updating the Notion `Action Required` formula.
- Live Notion debug verification succeeded against the configured Cash Flow HQ data source on July 12, 2026.
- Live Notion patch verification found that Notion rejected the full formula with a `400 validation_error` (`Type error with formula`); the command then successfully installed its tested fallback formula.
- Corrected the ICR import source headers to the actual export fields: `AgencyFee` and `ClientFee`, and normalized imported totals to two-decimal currency precision.
- The corrected non-destructive dry-run parsed the archived export successfully: Due to Agency `$1,617.91`, Due to Client `$2,426.72`, and Total Collected `$4,044.63`. It created no production records.
- Independently verified `remits/sent/ICR/2026-07-06/United Remit 7-6-26.xlsx` (SHA-256 `ebf1378f4082f229a89d9b47b73042594cff56597895934961bc881c1745a954`) contains 18 nonblank data rows and totals AgencyFee `$1,617.91`, ClientFee `$2,426.72`, and Total Collected `$4,044.63`; the two fee totals reconcile exactly.
- Re-ran `.venv/bin/python main.py icr-remit-import --file 'remits/sent/ICR/2026-07-06/United Remit 7-6-26.xlsx' --dry-run`; it reproduced the expected totals and created no Notion page, import-history row, or Outlook item.
- The first live command attempt stopped before creating the intended production artifacts because the importer invoked Cash Flow HQ view provisioning and Notion rejected a view payload. Post-failure checks confirmed zero matching Notion pages, import-history rows, and Outlook items.
- Fixed the scoped import-path defect so an ICR import uses the configured Cash Flow HQ data source directly and does not run unrelated schema/view provisioning.
- The archived `United Remit 7-6-26.xlsx` was initially imported in error. Its uniquely matched Cash Flow HQ page, import-history row, and unsent Outlook draft were removed after confirming the current files had been loaded; no broker email was sent.
- Fixed the dashboard date-type mismatch exposed by the new live obligation by accepting the existing ISO date string returned by `today_in_timezone`; all 15 dashboard tests and all 138 repository tests pass.
- Updated `icr-remit-import` to require the liquidation-rate report, attach both source reports, and keep internal remit totals out of the broker-facing draft.
- Independently verified the current `remits/incoming/ICR/UNITED REMIT 7-12-26.xlsx` has 11 nonblank rows: Due to Agency `$511.83`, Due to Client `$767.68`, and Total Collected `$1,279.51`.
- Imported the current remit with `remits/incoming/ICR/UNITED LIQ RATE.csv`. Production now contains exactly one Cash Flow HQ obligation for `$767.68`, one matching import-history row, and one unsent Outlook draft addressed only to the configured ICR recipient with both current files attached. Attachment hashes and the owner-approved message text were verified exactly.
- Updated the current Cash Flow HQ obligation and future ICR imports so the Notion `Amount` remains the `$767.68` owed to Jim while `Notes` records Due to Agency `$511.83`, Due to Client (owed to Jim) `$767.68`, and Total Collected `$1,279.51`.
- Added the read-only Cash Flow Forecast section to the existing UCM Dashboard. It calculates Past Due, Due Today, Next 7 Days, Next 30 Days, This Month, AutoPay, and Manual totals from Cash Flow HQ; shows proportional horizon bars, status badges, filters, and the next 10 unpaid obligations. No Outlook scan, Notion write, or new route was added.
- Added the shared UCM data layer compatibility foundation under `shared/data_layer/`: typed shared records and enums, Decimal-safe serialization, deterministic idempotency helpers, a storage-agnostic repository contract, an in-memory test repository, agent-run records, and normalization adapters for Cash Flow HQ and ICR Remit. Existing production writes, storage, schemas, schedules, and notification behavior are unchanged.
- Connected the shared foundation to a dependency-injected, read-only dashboard data service and centralized Needs Review queue. Added Decimal-safe financial summaries, review rules/filters/pagination, safe metadata filtering, failed-agent-run review projection, read-only HTTP endpoints, and a matching dashboard section. The default repository remains empty and in-memory; no production storage or write path changed.
- Added controlled local approve, reject, and resolve actions for shared Needs Review records. Added reviewer/confirmation requirements, CSRF protection, stale-write detection, idempotent request IDs, terminal-state guards, append-only review audit events, and read-only audit history. Failed agent-run projections remain non-actionable, and no external or agent-specific write path is connected.
- Added durable `SQLiteSharedRecordRepository` storage for normalized records, agent runs, and review audits. Review decisions and audit events commit atomically; schema versioning, indexes, uniqueness constraints, foreign keys, WAL mode, busy timeout, private file permissions, and non-destructive init/status commands are included. The dashboard now uses the configured SQLite repository while tests may still inject the in-memory implementation.
- Initialized and verified the live shared database at `~/Library/Application Support/UCM/payment-agent/shared_ucm_data.sqlite3`. Integrity and foreign-key checks pass, schema version 1 is installed, permissions are `0600`, and initial normalized record/run/audit counts are zero because no historical source import was performed.
- Added explicit `shared-data-sync` dry-run/apply reconciliation for Cash Flow HQ Notion pages and existing ICR import history. Dry-run is the default; apply requires `--apply --confirm APPLY_SHARED_SYNC`, writes only shared SQLite, is idempotent, preserves terminal human decisions, and blocks the entire apply on source errors or review conflicts.
- Ran live read-only previews with zero writes: Cash Flow HQ produced 9 creates, 0 updates/conflicts/errors; ICR history produced 1 create, 0 updates/conflicts/errors. The database remained empty until explicit authorization was received.
- Applied the owner-authorized 10-record shared-data import in one transaction: 9 Cash Flow HQ Notion bills and 1 ICR remit history record. Post-import integrity and foreign-key checks pass, duplicate groups remain zero, and an immediate second dry-run returned 10 skips with 0 creates, updates, conflicts, or errors.
- Documented the current agent data flows, identifiers, duplicate controls, status mappings, dashboard dependencies, and external/not-found Attendance and Manager Monitoring systems in `docs/shared_data_layer.md`.
- Verified Python `3.9.6` is linked to `LibreSSL 2.8.3`; tests pass despite the `urllib3` compatibility warning.

## Current Task

The durable shared database contains 10 reconciled source records. The first controlled import and idempotency verification are complete.

## Next Recommended Task

Restart the dashboard, verify shared summary totals and Needs Review contents against the 10 imported records, then add scheduled read-only source synchronization with agent-run history.

## Known Issues

- Graph chat posting uses delegated `ChatMessage.Send`; the first send may require device-code sign-in and tenant consent.
- Listing Teams chats uses delegated `Chat.ReadBasic`; tenant consent may be required before chats can be listed.
- Email mailbox and Teams chat are in separate Microsoft 365 tenants, so cross-tenant Graph auth should be avoided.
- The scheduler currently uses the lightweight `schedule` package; `APScheduler` is recommended for stronger production scheduling later.
- Database migrations are not yet formalized.
- Weekly Remit Agent requires Microsoft Graph `Mail.Send` application permission before live broker email sending will work.
- Old macOS LaunchAgent failed with a permission error reading `.venv/pyvenv.cfg` under `Documents`; fixed installer now uses an Application Support runtime copy.
- Codex sandbox cannot bind the local dashboard port, so live LAN reachability must be verified from the Mac after starting `Start UCM Dashboard.command` or `python main.py dashboard`.
- Tailscale URL may show `Unavailable` if the local Tailscale CLI is not running or cannot return an IPv4 address.
- Codex sandbox cannot reach Microsoft login, so the live voicemail Outlook scan must be run from the Mac/network environment rather than inside Codex.
- Operations Intelligence corrected OCR now passes the quality gate for the clear July 2 SCollect screenshot, but values should still be reviewed because OCR may read some table totals imperfectly.
- The full Cash Flow HQ `Action Required` formula is not accepted by the live Notion API; the tested fallback formula is installed and should be reviewed in the live database for the intended business behavior.
- The current ICR remit import is complete and duplicate-protected. The corrected Outlook item remains an unsent draft pending owner review; the incorrect archived-file draft and obligation were removed.
- The project virtual environment uses Python 3.9.6 with LibreSSL 2.8.3, which triggers the `urllib3` v2 warning. Rebuild the virtual environment later with a supported Python 3.12+ distribution linked to current OpenSSL; no rebuild is required for the passing test suite.

## Session Update Rule

Update this file after every meaningful coding session with current status, completed work, current task, next recommended task, and known issues.
