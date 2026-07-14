# UCM AI Operations Session

## Current Project Status

The Payment Agent is stable end-to-end: it detects real payment emails, parses them, prevents duplicates, saves them to SQLite, posts professional Teams notifications to the UCM Leadership chat, and cleans up processed/duplicate emails.

The Weekly Remit Agent V1 supports ICR weekly remit delivery, file archiving, duplicate prevention, and owner notification.

The local UCM Admin Dashboard V1 has been added for browser-based agent status and simple owner actions.

The Payment Agent repository is prepared for Railway deployment, pending Railway service setup, a persistent volume, and final Microsoft auth confirmation.

The ICR remit import workflow now parses `.xlsx` and `.csv` exports, totals the `AgencyFee` and `ClientFee` columns as Due to Agency and Due to Client, blocks duplicate imports, creates the Cash Flow HQ obligation, and prepares an Outlook draft. Cash Flow HQ also has debug, diagnostic, and patch commands for the Notion `Action Required` formula.

Automated verification is passing: all 226 repository tests pass, including the Voicemail status persistence and Chief of Staff adapter tests.

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
- Corrected Cash Flow HQ Action Required normalization after dashboard verification showed `No` values entering the queue. Negative values now mean no action, `Yes` becomes `Action required`, and specific instructions are preserved. Applied 9 reconciled record updates; the queue now contains 4 genuine unresolved items, and the follow-up dry-run returned 10 skips with no conflicts or errors.
- Added scheduled shared-data synchronization with configurable interval/source/limit, run-at-start behavior, durable success/failure agent-run history, dashboard health reporting, a confirmed manual Sync Now action, and independent macOS LaunchAgent install/status/uninstall helpers. Sync continues to write only shared SQLite and remains all-or-nothing on conflicts or source errors.
- Live scheduled-style verification completed successfully: 10 records found, 10 unchanged skips, 0 creates/updates/conflicts/errors, and the completed run was persisted in shared agent-run history for dashboard health display.
- Copied the scheduled-sync runtime and valid `com.ucm.shared-data-sync` plist into the durable macOS locations. The worker runs successfully from that runtime, but launchd activation from the Codex sandbox returned `Bootstrap failed: 5: Input/output error`; run `scripts/install_shared_data_agent.sh` once from a normal Terminal session to complete registration.
- Refined the dashboard layout so Cash Flow Forecast and Needs Review share a weighted desktop row (approximately 63/37) and stack below 1020px. Needs Review now uses compact metrics and a maximum five-item preview while preserving the full queue route. Cash Flow cards were tightened without removing horizon, payment-type, summary, or upcoming-payment data.
- Documented unavailable cash-flow business inputs in `docs/cash_flow_dashboard_data_requirements.md`; no balances, payroll, collections, remit forecasts, missing dates, or missing amounts were invented.
- Configured the owner-confirmed SCollect rule in code and live Notion: invoice on the 1st, AutoPay/due on the 5th, 10 users at $50 plus a $100 server fee, totaling $600. Marked the matched July bill paid by AutoPay on July 5, reconciled the one shared SQLite update, and verified the repeat sync was idempotent. Needs Review decreased from 4 to 3.
- Set the uniquely matched Coterie receipt due date to July 8, 2026 in Notion, leaving status and payment fields unchanged. Reconciled the single shared SQLite update and verified the repeat Cash Flow sync returned 9 unchanged skips with no conflicts or errors.
- Confirmed the uniquely matched Coterie receipt was paid on July 7, 2026. Updated the live Cash Flow HQ record to `Paid` with payment date July 7 while preserving its July 8 due date, synchronized the shared SQLite record, and verified the repeat Cash Flow dry-run returned 9 unchanged skips with no conflicts or errors. The centralized queue now contains 2 unresolved items.
- Improved dashboard readability without changing data or actions: Cash Flow Forecast and Needs Review now use separate full-width sections, the main canvas is wider, cards have more breathing room and clearer hierarchy, tables are easier to scan, and keyboard focus is more visible. Dashboard-focused tests pass.
- Documented the current agent data flows, identifiers, duplicate controls, status mappings, dashboard dependencies, and external/not-found Attendance and Manager Monitoring systems in `docs/shared_data_layer.md`.
- Verified Python `3.9.6` is linked to `LibreSSL 2.8.3`; tests pass despite the `urllib3` compatibility warning.
- Added the local, read-only Chief of Staff Phase 1 scaffold under `agents/chief_of_staff/`. The static registry inventories eight local components plus the external/not-found Attendance Tracker and Manager Monitoring systems. `python main.py chief-of-staff status` performs no agent actions, configuration loading, network calls, or production writes. The audit is documented in `docs/chief_of_staff_inventory.md` with focused tests in `tests/test_chief_of_staff.py`.
- Verified the Chief of Staff command directly, ran its 2 focused tests, and ran the full 211-test repository suite successfully. The existing Python 3.9/LibreSSL `urllib3` warning remains unchanged.
- Added side-effect-free `get_status()` adapters for Cash Flow HQ and Voicemail Tracker. Both use a typed four-field status contract. Cash Flow reads existing normalized bill metrics and shared-sync history through a SQLite `mode=ro`/`query_only` source; Voicemail reports unavailable run/callback data without scanning Outlook or inventing metrics. The existing `chief-of-staff status` command now aggregates both results above the unchanged inventory.
- Verified all 7 focused Chief of Staff tests, including aggregation output and a byte-for-byte database immutability check, and the full 216-test offline repository suite. The local status command reports current persisted values without executing either agent.
- Added the smallest Voicemail Tracker status persistence layer: an atomic, private JSON snapshot written only when the existing live `voicemail-scan-once` workflow succeeds or raises a caught error. It stores attempted/success timestamps, outcome, latest-scan callback/record counts, and a generic non-sensitive error message only. Chief of Staff reads the file without side effects and reports `Not Yet Run`, `Healthy`, or `Error` without triggering a scan.
- Verified 20 focused Voicemail/Chief of Staff tests and the full 226-test offline repository suite. Coverage includes missing/corrupt state, success/failure snapshots, pending counts, atomic replacement failure, sensitive-data exclusion, read-only status access, sample-mode isolation, status-write failure containment, and proof that status does not trigger a scan.
- Prepared Payment Agent for Railway cloud deployment without changing payment parsing, scanning, database duplicate protection, Teams formatting, or cleanup business behavior.
- Added Railway packaging files: `Dockerfile`, `railway.json`, `.dockerignore`, `.env.railway.example`, and `scripts/railway_payment_agent_start.sh`.
- Added `python main.py health` and a private JSON Payment Agent health file with status, last successful run, and last error.
- Added structured JSON logging support via `LOG_FORMAT=json` while keeping local text logs as the default.
- Added graceful shutdown support to the shared scheduler and Payment Agent runner.
- Added bounded retry wrappers for long-running Payment Agent jobs and transient Microsoft Graph request failures.
- Documented Railway environment variables, persistent `/data` volume requirements, Microsoft Graph permissions, and Teams delegated-token limitations in `docs/payment_agent_railway.md`.
- Verified Railway prep with 17 focused Payment Agent tests, the full 228-test repository suite, a compile check using a sandbox-safe bytecode cache, and two safe local Railway-entrypoint restart cycles with live scanning disabled.
- Prepared the Voicemail Tracker Agent for future Railway deployment only, without deploying or changing production behavior. Added durable runtime state for processed voicemail IDs, last successful scan, pending callbacks, callback completion status, and future Teams summary guard data; added `DRY_RUN`, `voicemail-health`, `voicemail-run`, `/data` Railway placeholders, `railway.voicemail.json`, and `scripts/railway_voicemail_tracker_start.sh`.
- Added restart-safety coverage proving repeated Outlook voicemail IDs are skipped across agent instances and within a single scan. Focused Voicemail tests pass locally.

## Current Task

Voicemail Tracker Railway readiness is prepared locally. No deployment has been performed and the existing Payment Agent Railway configuration remains unchanged.

## Next Recommended Task

Review `docs/voicemail_tracker_railway.md`, create a separate Railway service when ready, attach a persistent `/data` volume, add the documented environment variables, and run with `DRY_RUN=true` before considering live behavior.

## Known Issues

- Graph chat posting uses delegated `ChatMessage.Send`; the first send may require device-code sign-in and tenant consent.
- Listing Teams chats uses delegated `Chat.ReadBasic`; tenant consent may be required before chats can be listed.
- Email mailbox and Teams chat are in separate Microsoft 365 tenants, so cross-tenant Graph auth should be avoided.
- The scheduler currently uses the lightweight `schedule` package; `APScheduler` is recommended for stronger production scheduling later.
- Database migrations are not yet formalized.
- Weekly Remit Agent requires Microsoft Graph `Mail.Send` application permission before live broker email sending will work.
- Railway deployment is not complete until a persistent `/data` volume is attached. Without it, SQLite duplicate history and delegated Teams token cache can be lost on redeploy.
- Outlook mailbox scanning can run unattended in Railway with app-only Microsoft Graph credentials, but cleanup requires application `Mail.ReadWrite` in addition to `Mail.Read`.
- Teams `graph_chat` posting remains delegated and needs a persistent `TEAMS_GRAPH_TOKEN_CACHE_PATH` plus an initial Teams tenant sign-in. Use webhook posting if unattended delegated refresh is not reliable in Railway.
- Voicemail Tracker Railway deployment is blocked until a Railway service and persistent `/data` volume are created and Microsoft Graph variables are configured.
- The current Voicemail Tracker Phase 1 does not implement actual Teams summary posting; this Railway pass preserves the configured weekday `08:50` slot and does not add Teams behavior.
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
