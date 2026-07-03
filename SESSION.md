# UCM AI Operations Session

## Current Project Status

The Payment Agent is stable end-to-end: it detects real payment emails, parses them, prevents duplicates, saves them to SQLite, posts professional Teams notifications to the UCM Leadership chat, and cleans up processed/duplicate emails.

The Weekly Remit Agent V1 build has started for ICR weekly remit delivery.

The local UCM Admin Dashboard V1 has been added for browser-based agent status and simple owner actions.

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

## Current Task

Operations Intelligence OCR has been corrected for the July 2 SCollect dashboard screenshot, and a safe corrected-report post command is available. The screenshot is clear; the previous manual-review result came from incorrect crop regions and missing fallback extraction for the portfolio table.

## Next Recommended Task

Run `python main.py ops-post-image --image /path/to/clear-scollect-screenshot.png --report-date 2026-07-02` from the project virtual environment to post the corrected July 2 Operations report to Teams from the known clear screenshot.

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

## Session Update Rule

Update this file after every meaningful coding session with current status, completed work, current task, next recommended task, and known issues.
