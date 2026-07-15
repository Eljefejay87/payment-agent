# UCM AI Operations Agent: Payment Agent

The Payment Agent monitors Microsoft 365 email for online payment notification emails, extracts payment details, stores each payment in SQLite, prevents duplicate processing, and posts leadership summaries to Microsoft Teams.

Dry-run mode is enabled by default, so the agent can be tested without sending Teams messages.

## What It Does

- Scans a Microsoft 365 mailbox for messages whose subject contains `Online Payment -`.
- Filters by the configured sender email address.
- Extracts:
  - Account number
  - Payment type
  - Note
  - Payment date
  - Payment amount
  - Email received time
- Saves processed message IDs and payments into SQLite.
- Prevents duplicate processing by message ID.
- Sends a daily Teams leadership report with:
  - Total number of payments
  - Total dollar amount collected
  - Account and amount list
- Optionally sends real-time alerts for each processed payment.
- Optionally stores the email HTML body as a local snapshot.

## Questions To Confirm

Before turning this on for production, please confirm:

1. Email provider: Microsoft 365 / Outlook, or another provider?
2. Sender email address for `United Account Services`.
3. Teams destination: incoming webhook URL, Teams channel, or leadership chat?
4. Preferred daily report time.
5. Timezone.
6. Reporting style: real-time alerts, daily summary, or both?

## Architecture

The project is now organized as a reusable UCM AI Platform. The Payment Agent is the first agent plugged into it.

```text
payment-agent/
agents/
  payment_agent/
    main.py
    config.py
    database.py
    db.py
    graph_client.py
    models.py
    parser.py
    reports.py
    service.py
    teams.py
shared/
  config.py
  database.py
  logging.py
  scheduler.py
  data_layer/
    models.py
    repository.py
    idempotency.py
    adapters.py
  integrations/
    microsoft_graph.py
    microsoft_teams.py
  utils/
    text.py
database/
logs/
reports/
screenshots/
tests/
README.md
requirements.txt
.env.example
.gitignore
main.py
```

`main.py` at the project root remains a compatibility wrapper, so existing commands still work.

Future agents should be added under `agents/` and should reuse `shared/` for configuration, logging, SQLite access, scheduling, Microsoft Graph, Teams posting, and common utilities.

Reserved future agent locations:

- `agents/placement_agent/`
- `agents/compliance_agent/`
- `agents/finance_agent/`
- `agents/executive_agent/`

Do not create these agents until their business requirements are defined.

## Shared UCM Data Layer

`shared/data_layer/` provides a storage-agnostic compatibility contract for normalized operational records. It includes typed enums/dataclasses, Decimal-safe serialization, stable idempotency helpers, an abstract repository interface, in-memory and durable SQLite implementations, agent-run records, and initial adapters for Cash Flow HQ and ICR Remit.

Agents can create normalized records without changing their existing writes:

```python
from shared.data_layer import normalize_cash_flow_bill

shared_record = normalize_cash_flow_bill(existing_bill_candidate)
payload = shared_record.to_dict()
```

Cash Flow HQ and ICR continue using their current Notion, SQLite, Outlook, and local-file workflows. The shared SQLite database is separate and does not automatically migrate historical source records. See `docs/shared_data_layer.md` for the inventory, status mappings, adapter metadata, repository contract, persistence rules, and idempotency rules.

## Voicemail Tracker Agent

Phase 1 has been added for the United Account Services Voicemail Tracker Agent.
This phase only reads Outlook voicemail emails and parses the voicemail details.
It does not write to Google Sheets, send Teams summaries, move emails, delete emails, or contact consumers.

Phase 1 parses:

- Date Received
- Time Received
- Phone Number
- Duration
- Transcript
- Audio attachment/reference
- Source Email ID

Run the safe sample-data test:

```bash
python main.py voicemail-test-sample
```

Run a live Outlook intake scan:

```bash
python main.py voicemail-scan-once
```

Voicemail settings:

```dotenv
VOICEMAIL_MAILBOX_USER_ID=voicemail@example.com
VOICEMAIL_SENDER_EMAIL=voicemail@vaspian.com
VOICEMAIL_SUBJECT_CONTAINS=voicemail
VOICEMAIL_LOOKBACK_HOURS=48
VOICEMAIL_STATUS_PATH=~/Library/Application Support/UCM/payment-agent/voicemail_status.json
VOICEMAIL_RUNTIME_STATE_PATH=~/Library/Application Support/UCM/payment-agent/voicemail_runtime_state.json
VOICEMAIL_HEALTH_PATH=~/Library/Application Support/UCM/payment-agent/voicemail_health.json
VOICEMAIL_SCAN_INTERVAL_MINUTES=15
VOICEMAIL_SUMMARY_TIME=08:50
```

Each normal live scan atomically replaces this small local status snapshot with
timestamps, outcome, record/callback counts, and a non-sensitive error summary.
It stores no voicemail audio, transcript, phone number, account number, message
identifier, or debtor data. Phase 1 has no callback-resolution workflow, so the
pending callback count is the number of voicemail records found by the latest
successful scan.

The voicemail agent uses Outlook message ID / internet message ID as the source identifier for duplicate protection. The runtime state file stores processed voicemail IDs, the last successful scan timestamp, pending callback IDs, callback completion status, and the last Teams-summary guard date so redeploys cannot reprocess the same Outlook voicemail. Phase 1 still does not write to Google Sheets, move emails, delete emails, contact consumers, or add SCollect integration.

Check voicemail health:

```bash
python main.py voicemail-health
```

Run the long-lived Railway-ready worker locally:

```bash
python main.py voicemail-run
```

The Railway-only start helper is `scripts/railway_voicemail_tracker_start.sh`, with matching deployment config in `railway.voicemail.json`. For Railway, mount a persistent volume at `/data` and use the voicemail variables in `.env.railway.example`. The existing weekday Teams summary time remains `08:50` via `VOICEMAIL_SUMMARY_TIME`; this readiness pass does not add or change Teams posting behavior.

## Files

- `main.py` - compatibility command-line entry point.
- `agents/payment_agent/main.py` - Payment Agent command-line entry point.
- `agents/payment_agent/config.py` - Payment Agent environment-based settings.
- `agents/payment_agent/graph_client.py` - Payment-specific email search built on shared Microsoft Graph.
- `agents/payment_agent/parser.py` - payment email parser.
- `agents/payment_agent/database.py` - Payment Agent SQLite schema and payment queries.
- `agents/payment_agent/db.py` - compatibility import wrapper for older `db` imports.
- `agents/payment_agent/reports.py` - daily report and real-time alert formatting.
- `agents/payment_agent/service.py` - Payment Agent business workflow.
- `shared/config.py` - reusable environment helpers.
- `shared/database.py` - reusable SQLite base class.
- `shared/logging.py` - reusable logging setup.
- `shared/scheduler.py` - reusable scheduler wrapper.
- `shared/integrations/microsoft_graph.py` - reusable Microsoft Graph client.
- `shared/integrations/microsoft_teams.py` - reusable Teams sender.
- `shared/utils/text.py` - reusable text, HTML, and filename helpers.
- `.env.example` - settings template.
- `requirements.txt` - Python dependencies.

## Setup

From this folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and fill in the real settings.

## Microsoft Graph Setup

Create an Azure App Registration in the email Microsoft 365 tenant for mailbox scanning.

Minimum for mailbox scanning:

- API: Microsoft Graph
- Permission type: Application
- Permission: `Mail.Read`
- Admin consent: required

Then set:

```dotenv
MS_GRAPH_TENANT_ID=
MS_GRAPH_CLIENT_ID=
MS_GRAPH_CLIENT_SECRET=
MAILBOX_USER_ID=payments@example.com
```

`MAILBOX_USER_ID` can be the mailbox email address or Graph user ID.

If the Teams chat lives in a different Microsoft 365 tenant, do not try cross-tenant Graph authentication. Use a separate Teams app registration in the Teams tenant and configure the `TEAMS_GRAPH_*` settings below.

## Teams Delivery Options

### Option A: Teams Incoming Webhook

This is usually the simplest path for channel posting.

```dotenv
TEAMS_POST_METHOD=webhook
TEAMS_WEBHOOK_URL=https://...
```

### Option B: Microsoft Graph Chat Posting

Use this for an existing leadership group chat. This is separate from the email Graph app and can point at a different Microsoft 365 tenant.

```dotenv
TEAMS_POST_METHOD=graph_chat
TEAMS_GRAPH_TENANT_ID=
TEAMS_GRAPH_CLIENT_ID=
TEAMS_GRAPH_CLIENT_SECRET=
TEAMS_CHAT_ID=
TEAMS_GRAPH_TOKEN_CACHE_PATH=.graph_teams_token_cache.bin
```

This uses Microsoft Graph delegated chat posting with the Teams tenant app registration.

Minimum Microsoft Graph delegated permissions for the Teams app:

- `ChatMessage.Send` - send payment notification messages to the configured chat.
- `Chat.ReadBasic` - list recent chats with `debug-list-teams-chats` so you can find `TEAMS_CHAT_ID`.

The existing email app-only `Mail.Read` permission still handles mailbox scanning through `MS_GRAPH_*`. Teams chat operations use `TEAMS_GRAPH_*`. Chat messages require a signed-in Teams-tenant work account token, so the first Teams command may show a Microsoft device-code login prompt. After the first successful sign-in, MSAL stores the delegated Teams token in `TEAMS_GRAPH_TOKEN_CACHE_PATH` and reuses/refreshes it.

Recommended setup:

1. In the Teams tenant, create a Microsoft Entra app registration for Teams posting.
2. Add delegated Microsoft Graph permission `ChatMessage.Send`.
3. Add delegated Microsoft Graph permission `Chat.ReadBasic` if you need to find the chat ID with the debug command.
4. Grant consent if your tenant requires admin approval.
5. Enable public client/device-code flow for the Teams app registration if your tenant requires it.
6. Set `TEAMS_POST_METHOD=graph_chat`.
7. Set `TEAMS_GRAPH_TENANT_ID`, `TEAMS_GRAPH_CLIENT_ID`, and `TEAMS_GRAPH_CLIENT_SECRET` from the Teams app registration.
8. Set `TEAMS_CHAT_ID` to the existing group chat ID.
9. Set `DRY_RUN=false` when ready to post real messages.

List recent Teams chats without sending a message:

```bash
python main.py debug-list-teams-chats
```

Send a test message without scanning email:

```bash
python main.py debug-teams-message
```

### Option C: Microsoft Graph Channel Posting

Use this for a Teams channel.

```dotenv
TEAMS_POST_METHOD=graph_channel
TEAMS_TEAM_ID=
TEAMS_CHANNEL_ID=
```

This requires additional Graph permissions for sending channel messages.

## Configure Dry Run

Dry-run mode logs the Teams message instead of sending it:

```dotenv
DRY_RUN=true
```

When ready to send:

```dotenv
DRY_RUN=false
```

## Initialize Database

```bash
python main.py init-db
```

This creates `payment_agent.sqlite3` unless `DATABASE_PATH` is changed.

## Test One Scan

```bash
python main.py scan-once
```

The agent will:

1. Search recent payment emails.
2. Skip already processed messages.
3. Parse and save new payments.
4. Send a Teams payment notification when `TEAMS_POST_METHOD=graph_chat`, or when real-time reporting is enabled.
5. Log Teams output instead of sending if `DRY_RUN=true`.

## Weekly Remit Agent

The Weekly Remit Agent sends the weekly ICR broker remit email after you export the two SCollect Excel reports and place them in the local incoming folder.

V1 expects one broker:

```dotenv
REMIT_BROKER_NAME=ICR
REMIT_BROKER_EMAIL=jprawel@icroffice.com
```

Drop the files here:

```text
remits/incoming/ICR/
```

Required filenames:

```text
United Remit*.xlsx or United Remit*.csv
United Liq*.xlsx or United Liq*.csv
```

The spreadsheet contents and formatting are not changed. The agent only validates that both files exist, sends them, records the send in SQLite, sends the owner Teams confirmation, and moves the sent files into a dated folder:

```text
remits/sent/ICR/YYYY-MM-DD/
```

Broker email template:

```text
Subject: United Capital Management Weekly Remit - ICR - Week of YYYY-MM-DD

Hi Jim,

Attached are United Capital Management's weekly ICR remit report and liquidation report for the week of YYYY-MM-DD.

Attached files:
- United Remit...
- United Liq...

Please let us know if you need anything else.

Thank you,
United Capital Management
```

### Weekly Remit Configuration

Add these settings to `.env`:

```dotenv
REMIT_BROKER_NAME=ICR
REMIT_BROKER_EMAIL=jprawel@icroffice.com
REMIT_INCOMING_FOLDER=remits/incoming/ICR
REMIT_SENT_FOLDER=remits/sent/ICR
REMIT_FAILED_FOLDER=remits/failed/ICR
REMIT_DUPLICATE_FOLDER=remits/duplicates/ICR
REMIT_REMIT_FILENAME_CONTAINS=United Remit
REMIT_LIQUIDATION_FILENAME_CONTAINS=United Liq
REMIT_ALLOWED_EXTENSIONS=.xlsx,.xls,.csv
REMIT_SEND_MODE=send
REMIT_RUN_DAY=monday
REMIT_SEND_DEADLINE=15:00
REMIT_SCAN_INTERVAL_MINUTES=15
REMIT_SEND_OWNER_TEAMS_UPDATE=true
REMIT_OWNER_TEAMS_CHAT_ID=
```

`REMIT_OWNER_TEAMS_CHAT_ID` should be your private one-on-one Teams chat ID, not the UCM Leadership group chat ID.

### Microsoft Graph Permission For Broker Email

The email Microsoft Graph app needs permission to send mail from the configured mailbox:

- API: Microsoft Graph
- Permission type: Application
- Permission: `Mail.Send`
- Admin consent: required

Existing mailbox cleanup already uses `Mail.ReadWrite`. Payment scanning uses `Mail.Read`. The Weekly Remit Agent uses `Mail.Send` for sending the broker email.

### Weekly Remit Commands

Initialize the remit database table:

```bash
python3 main.py remit-init-db
```

Check whether the two files are ready without sending email:

```bash
python3 main.py debug-remit-files
```

Run one send attempt. This respects the Monday before-3:00-PM window:

```bash
python3 main.py remit-scan-once
```

For a manual test outside the Monday window:

```bash
python3 main.py remit-scan-once --force
```

Run continuously and check every configured interval:

```bash
python3 main.py remit-run
```

Duplicate protection is by broker and week start date. If an ICR remit for that week has already been sent, the agent will not send another email.

### ICR Remit Import

Import an ICR `.xlsx` or `.csv` export that contains `AgencyFee` and `ClientFee` columns. The importer totals both columns as Due to Agency and Due to Client, prevents duplicate imports by broker, week, and filename, creates the Cash Flow HQ obligation, and creates an Outlook draft with the remit and liquidation-rate reports attached. The broker draft contains only the weekly attachment notice and filenames; internal totals are not included.

Preview and validate an export without creating a Notion row, import-history record, or Outlook draft:

```bash
python main.py icr-remit-import --file "path/to/icr-remit.xlsx" --liquidation-file "path/to/icr-liq-rate.csv" --dry-run
```

Important flags:

- `--file` is required and accepts an `.xlsx` or `.csv` export.
- `--liquidation-file` is required and identifies the liquidation-rate report attached to the broker draft.
- `--dry-run` parses and totals the file without creating Notion or Outlook records.
- `--env-file` loads an alternate environment file.

The command uses the existing Cash Flow HQ Notion settings: `NOTION_API_KEY`, `CASH_FLOW_HQ_PARENT_PAGE_ID`, `CASH_FLOW_HQ_DATABASE_NAME`, and `NOTION_VERSION`. The API key and parent page ID must be configured; the database name and Notion version have the defaults shown in the Cash Flow HQ configuration section. A live import also requires the Weekly Remit Microsoft Graph and broker settings validated by the application, including `MAILBOX_USER_ID`, `MS_GRAPH_TENANT_ID`, `MS_GRAPH_CLIENT_ID`, `MS_GRAPH_CLIENT_SECRET`, `REMIT_BROKER_NAME`, and `REMIT_BROKER_EMAIL`. If owner Teams updates remain enabled, the existing `REMIT_OWNER_TEAMS_CHAT_ID` and `TEAMS_GRAPH_*` settings are also required. The Graph application needs permission to create the mailbox draft and attach the export.

## Chargeback Tracker Phase 1

The existing shared `United Charge Back Tracker` Google Sheet remains the official source of truth. The importer reads `.csv`, `.xlsx`, `.png`, `.jpg`, and `.jpeg` reports, preserves financial values as extracted, uses the existing Account ID + Chargeback Date + Amount duplicate match, and appends new rows only.

Imports populate only Account ID, Consumer Name, Chargeback Date, Amount, Client Name, Due Client, and Notes when manual review is necessary. Collector Name, Bonus Paid, Date Recon w/ Agent, and Date Recon w/ Client remain blank. No Source column is used. Client Name defaults to `NDH`; a case-insensitive `NOT US` marker on that record maps Client Name to `ICR`, and the marker is removed from imported data. One report may contain both clients. `--source NDH` or `--source Jim` remains available as an explicit full-file override; `Jim` maps to Client Name `ICR` for compatibility.

Screenshot OCR reuses the project's Tesseract execution pattern. One screenshot may produce multiple rows, including separate chargebacks for the same consumer. Configure `CHARGEBACK_OCR_COMMAND` if Tesseract is not on `PATH`; `CHARGEBACK_OCR_MIN_CONFIDENCE` defaults to `0.80`. Any field below that threshold is left blank and Notes identifies the record for manual review. OCR never determines Collector Name or recalculates Amount or Due Client.

The production spreadsheet and worksheet are pinned to the existing artifact:

```dotenv
CHARGEBACK_SPREADSHEET_ID=1i89CMRBpbi_hEi6GCGtYne7Y6q_e8GgJbREVET0njuU
CHARGEBACK_SHEET_NAME=Sample_Chargeback_Tracker
CHARGEBACK_GOOGLE_SERVICE_ACCOUNT_FILE=/absolute/path/to/google-service-account.json
CHARGEBACK_OCR_COMMAND=tesseract
CHARGEBACK_OCR_MIN_CONFIDENCE=0.80
```

`CHARGEBACK_GOOGLE_SERVICE_ACCOUNT_FILE` must point to the existing Google service-account JSON file. Do not copy the credential into the repository. Share the existing `United Charge Back Tracker` spreadsheet with that service-account email as an editor. The integration uses the existing `google-auth` service-account flow with the narrow Sheets and Drive metadata scopes.

Verify authentication, the exact spreadsheet and worksheet, and append capability without reading row data or performing a write:

```bash
python main.py chargeback-verify-connection
```

The verification command calls Google Sheets metadata to open the spreadsheet and locate the worksheet, then checks the service account's Drive `canEdit` and `canModifyContent` capabilities. It never appends, inserts, updates, or deletes cells or rows.

After connection verification succeeds, preview is the default for report imports:

```bash
python main.py chargeback-import --file "path/to/chargebacks.csv"
```

After reviewing the counts, explicitly append new records:

```bash
python main.py chargeback-import --file "path/to/chargebacks.csv" --apply --confirm APPEND_CHARGEBACKS
```

## UCM Admin Dashboard

The local UCM Admin Dashboard gives you one browser page for the current and future UCM agents.

V1 includes:

- Payment Agent status, today's payment count, today's collected total, and recent payments.
- Weekly Remit Agent file status for `United Remit` and `United Liq`.
- Buttons to scan payments, open the ICR remit drop folder, and send the weekly remit when files are ready.
- Cash Flow Forecast from the existing Cash Flow HQ Notion data source.
- Placeholders for Placement, Compliance, Finance, and Executive Dashboard agents.

### Cash Flow Forecast

The dashboard reads the existing Cash Flow HQ data source and calculates a read-only cash horizon. It does not scan Outlook or create Notion records.

- Past Due: unpaid bills with a due date before today.
- Due Today: unpaid bills due today.
- Next 7 Days and Next 30 Days: unpaid bills due after today through the inclusive horizon.
- This Month: unpaid bills due from today through the last day of the current month.
- AutoPay and Manual totals: all dated, unpaid obligations separated by `Payment Type`; blank or non-AutoPay values count as Manual.
- Top Upcoming Payments: the next 10 unpaid obligations sorted by due date, including unresolved past-due bills.

The section displays Vendor, Due Date, Amount, Category, forecast status, and Action Required. Category, Vendor, Status, and payment-type filters run in the browser against the 10 displayed rows. No new route was added; the widget reuses the existing dashboard `/` page and `/api/status` snapshot.

Dashboard settings:

```dotenv
DASHBOARD_HOST=0.0.0.0
DASHBOARD_PORT=8080
```

Start from Terminal:

```bash
python3 main.py dashboard
```

Then open:

```text
http://127.0.0.1:8080
```

When the dashboard starts, it logs the local, LAN, and Tailscale URLs using the configured dashboard port.

Mac double-click option:

```text
Start UCM Dashboard.command
```

If macOS blocks the launcher the first time, right-click it, choose Open, then confirm.

## Run Local Validation

These checks do not require Microsoft credentials or network access:

```bash
python -m unittest discover tests
python main.py init-db
```

## Send Daily Report Manually

```bash
python main.py send-daily-report
```

## Run Continuously

```bash
python main.py run
```

This scans every `SCAN_INTERVAL_MINUTES` and sends the daily report at `DAILY_REPORT_TIME`.

Example:

```dotenv
SCAN_INTERVAL_MINUTES=15
DAILY_REPORT_TIME=17:00
TIMEZONE=America/New_York
REPORT_MODE=daily
```

`REPORT_MODE` options:

- `daily`
- `realtime`
- `both`

## Railway Cloud Deployment Preparation

The Payment Agent can be run on Railway with the existing business behavior preserved. The Railway start command is:

```bash
scripts/railway_payment_agent_start.sh
```

That script runs `python main.py init-db` and then `python main.py run`.

Railway-specific files:

- `Dockerfile`
- `railway.json`
- `.dockerignore`
- `.env.railway.example`
- `scripts/railway_payment_agent_start.sh`
- `docs/payment_agent_railway.md`

Use a Railway volume mounted at `/data` and set:

```dotenv
DATABASE_PATH=/data/payment_agent.sqlite3
PAYMENT_AGENT_HEALTH_PATH=/data/payment_agent_health.json
LOG_FORMAT=json
```

The Payment Agent health check is available with:

```bash
python main.py health
```

Outlook mailbox scanning can run unattended in Railway with the existing app-only Microsoft Graph credentials. The email app registration needs application `Mail.Read` and `Mail.ReadWrite` with admin consent.

Teams `graph_chat` posting uses delegated Microsoft Graph auth and needs a persistent token cache at `TEAMS_GRAPH_TOKEN_CACHE_PATH`, such as `/data/.graph_teams_token_cache.bin`. If delegated Teams refresh is not reliable in Railway, use `TEAMS_POST_METHOD=webhook` for unattended posting or plan a later channel/app-only notification design.

See `docs/payment_agent_railway.md` for the complete Railway variable list, auth notes, local safe-validation steps, and deployment checklist.

## Scheduler Options

### macOS launchd

The repo includes helper scripts for daily operation on macOS. The installer creates a background runtime copy under:

```text
~/Library/Application Support/UCM/payment-agent
```

That location avoids macOS privacy blocks that can prevent background services from reading apps inside `Documents`.

The LaunchAgent starts when you log into your Mac, restarts automatically if it crashes, and writes logs to the runtime folder:

- `~/Library/Application Support/UCM/payment-agent/logs/payment-agent.out.log`
- `~/Library/Application Support/UCM/payment-agent/logs/payment-agent.err.log`

Install and start:

```bash
./scripts/install_launch_agent.sh
```

Run the installer again any time you want to update the background runtime from the current project folder.

Start manually:

```bash
./scripts/start_agent.sh
```

Stop:

```bash
./scripts/stop_agent.sh
```

Status:

```bash
./scripts/status_agent.sh
```

Uninstall:

```bash
./scripts/uninstall_launch_agent.sh
```

### cron

For a scan every 15 minutes:

```cron
*/15 * * * * cd "/Users/jcollins/Documents/AI AGENT UCM/payment-agent" && .venv/bin/python main.py scan-once >> logs/payment-agent.log 2>&1
```

For a daily report at 5:00 PM:

```cron
0 17 * * * cd "/Users/jcollins/Documents/AI AGENT UCM/payment-agent" && .venv/bin/python main.py send-daily-report >> logs/payment-agent.log 2>&1
```

Create the log folder first:

```bash
mkdir -p logs
```

## Optional Email Snapshots

To save the HTML body of each processed email locally:

```dotenv
SAVE_EMAIL_HTML=true
EMAIL_SNAPSHOT_DIR=email_snapshots
```

This is not a screenshot or PDF renderer yet. It preserves the original HTML body so a later screenshot/PDF step can be added cleanly.

## Security Notes

- Do not put passwords, tokens, client secrets, webhook URLs, or mailbox credentials in code.
- Keep `.env` out of source control.
- Use the least Microsoft Graph permissions possible.
- Rotate the Azure client secret on a regular schedule.
- Consider limiting the app registration to only the payment mailbox using Exchange application access policies.

## Current Email Format Assumption

The parser expects labels like:

```text
Account: B123440
Type: ACH
Note: Online payment
Payments date: 2026-06-29
Payment amount: $123.45
```

It also handles simple HTML emails by converting HTML to text before parsing.

## Operations Intelligence Agent

The Operations Intelligence Agent watches the Microsoft Teams leadership chat for the daily SCollect dashboard screenshot, saves the image locally, extracts visible metrics with OCR, stores structured history, and posts a daily executive summary back to Teams.

### Architecture

- `agents/operations_intelligence_agent/graph_client.py` reads Teams chat messages and downloads pasted images or image attachments.
- `agents/operations_intelligence_agent/ocr.py` runs local OCR once per screenshot and extracts known SCollect labels.
- `agents/operations_intelligence_agent/database.py` stores screenshot hashes, metric JSON, OCR text, missing fields, and report status.
- `agents/operations_intelligence_agent/reports.py` builds the executive summary and compares it with the previous available report.
- `agents/operations_intelligence_agent/service.py` coordinates duplicate detection, saving, extraction, reporting, and Teams posting.

### Required Environment Variables

Add these to `.env`:

```dotenv
DRY_RUN=true
DATABASE_PATH=payment_agent.sqlite3
TIMEZONE=America/New_York

TEAMS_GRAPH_TENANT_ID=your-tenant-id
TEAMS_GRAPH_CLIENT_ID=your-app-client-id
TEAMS_GRAPH_CLIENT_SECRET=your-app-client-secret
TEAMS_GRAPH_TOKEN_CACHE_PATH=.graph_teams_token_cache.bin

OPS_LEADERSHIP_CHAT_ID=the-leadership-chat-id
OPS_DAILY_SCAN_START=17:00
OPS_DAILY_SCAN_END=18:15
OPS_SCAN_INTERVAL_MINUTES=10
OPS_LOOKBACK_HOURS=30
OPS_SCREENSHOTS_DIR=screenshots/operations-intelligence
OPS_REPORTS_DIR=reports/operations-intelligence
OPS_OCR_COMMAND=tesseract
OPS_OCR_MIN_CONFIDENCE=0.72
OPS_POST_SUMMARY_TO_TEAMS=true
OPS_LOW_QUALITY_ACTION=alert
OPS_COLLECTOR_CODES=CSOLO,VMAR,KMAD,UNITED HOUSE
```

Microsoft Graph delegated permissions needed for V1:

- `Chat.Read` to read the leadership chat and download image content.
- `ChatMessage.Send` to post the executive summary back to the chat.

Run this once to sign in and create the local Teams token cache:

```bash
python main.py ops-auth
```

### Teams Chat Setup

Use the leadership chat where the manager posts the SCollect dashboard screenshot around 5:20 PM each weekday. Set `OPS_LEADERSHIP_CHAT_ID` to that chat id. If you need to discover chat ids, use Microsoft Graph Explorer or the existing Teams chat debug command if available in your environment.

### How Screenshots Are Detected

During the configured window, the agent reads recent messages from `OPS_LEADERSHIP_CHAT_ID`. It treats pasted Teams images and image attachments as candidate SCollect screenshots. Each image is saved under:

```text
screenshots/operations-intelligence/YYYY-MM-DD/
```

The agent stores the Teams message id, image id, file path, and SHA-256 hash. If the same screenshot appears again, it is skipped and OCR is not run again.

### Extracted Data Storage

Structured history is stored in SQLite at `DATABASE_PATH`.

- `ops_screenshots` records each saved screenshot and prevents duplicate processing.
- `ops_reports` stores metric JSON, collector totals if readable, OCR text, missing fields, manual review notes, report text, and Teams-post status.

A plain-text copy of each summary is written to:

```text
reports/operations-intelligence/YYYY-MM-DD.txt
```

### Running Locally

Initialize the database tables:

```bash
python main.py ops-init-db
```

Check local setup before the first live Teams test:

```bash
python main.py ops-check-setup
```

Process a local screenshot for testing:

```bash
python main.py ops-process-image --image /path/to/scollect-screenshot.png --report-date 2026-07-02
```

Post a corrected report from a specific local screenshot:

```bash
python main.py ops-post-image --image /path/to/scollect-screenshot.png --report-date 2026-07-02
```

Create OCR debug artifacts for a screenshot without posting to Teams:

```bash
python main.py ops-debug-image --image /path/to/scollect-screenshot.png --report-date 2026-07-02
```

Reprocess saved screenshots for a date without posting to Teams:

```bash
python main.py ops-reprocess-date --date 2026-07-02 --dry-run
```

Reprocess saved screenshots for a date and post the latest quality-passing corrected report to Teams:

```bash
python main.py ops-post-report --date 2026-07-02
```

Import historical screenshots from the leadership Teams chat without posting anything back to Teams:

```bash
python main.py ops-import-history --days 30
```

Safe preview mode searches Teams and reports what would be imported, but does not write screenshot/report rows to the database:

```bash
python main.py ops-import-history --days 30 --dry-run
```

Save OCR debug files during the historical import:

```bash
python main.py ops-import-history --days 30 --debug
```

Re-run OCR for screenshots that were already imported:

```bash
python main.py ops-import-history --days 30 --force-reprocess
```

The historical importer:

- searches `OPS_LEADERSHIP_CHAT_ID` for image messages in the last N days;
- saves screenshots under `screenshots/operations-intelligence/YYYY-MM-DD/`;
- processes each screenshot through the same OCR pipeline and quality gate as the daily run;
- stores metrics in `ops_reports` and writes report files under `reports/operations-intelligence/`;
- skips duplicate Teams image ids and duplicate screenshot hashes;
- never posts to Teams during the import.

The import summary shows days searched, screenshots found, successfully imported reports, manual-review reports, duplicates skipped, missing weekdays, failed downloads/errors, and a historical summary with total collected, daily averages, best/lowest collection day, reliable top collector, and quality-gate pass count.

Debug artifacts are written to:

```text
reports/operations-intelligence/debug/YYYY-MM-DD/
```

Scan Teams once:

```bash
python main.py ops-scan-once --force
```

Run continuously:

```bash
python main.py ops-run
```

### Daily Scheduling

For V1, the simplest Mac schedule is to run `python main.py ops-run` at startup and let the agent check every `OPS_SCAN_INTERVAL_MINUTES` during `OPS_DAILY_SCAN_START` through `OPS_DAILY_SCAN_END`.

Install the Operations Intelligence Agent as a macOS background job:

```bash
./scripts/install_operations_agent.sh
```

Check status:

```bash
./scripts/status_operations_agent.sh
```

Stop:

```bash
./scripts/stop_operations_agent.sh
```

Uninstall:

```bash
./scripts/uninstall_operations_agent.sh
```

The background job runs `ops-run`, which checks Teams during the configured daily screenshot window.

For a weekday-only schedule, create a macOS Calendar alert or LaunchAgent that runs:

```bash
cd "/Users/jcollins/Documents/AI AGENT UCM/payment-agent"
python main.py ops-scan-once --force
```

Set it for 5:25 PM Monday through Friday so the 5:20 PM screenshot has time to arrive.

### Data Quality Rules

The agent does not guess. Missing fields, unreadable OCR, low-confidence fields, and unreadable collector totals are clearly listed under `Data Quality` in the Teams report.

Normal executive summaries are only posted when the minimum quality gate passes. V1 requires Accounts Worked, Attempts, Live Contacts, Contact Rate, and either Posted Cash or Future Scheduled Cash. If the gate fails, `OPS_LOW_QUALITY_ACTION=alert` posts only a short manual-review alert. Set `OPS_LOW_QUALITY_ACTION=skip` to save the report locally without posting anything.

Top Collector is strict. It is shown only when at least two collector rows are read from the whiteboard area, collector names match `OPS_COLLECTOR_CODES`, and the dollar amounts come from that whiteboard section. City/state text, addresses, phone numbers, and dashboard labels are never treated as collectors.

Historical data from `ops_reports` is available as an additive data source for dashboard trend analysis and future forecasting. The current daily Teams brief, OCR quality gate, and Performance Score format remain unchanged.

Operations screenshots are classified before OCR. Only likely SCollect/Admin Tools end-of-day dashboard screenshots are processed. Online Payment emails, attendance pages, browser pages, phone screenshots, and unrelated images are skipped.

Audit stored screenshots and optionally mark bad imports as excluded from dashboard metrics:

```bash
python main.py ops-audit-images --days 30
python main.py ops-audit-images --days 30 --mark-non-operations
```

Marked non-operations records stay in SQLite for audit history, but `/operations`, trends, and manual review queues ignore them.

Install the local OCR engine before production use:

```bash
brew install tesseract
```

### Future Improvements

- Add a curated SCollect screenshot template once several real examples are available.
- Add optional AI vision fallback for hard-to-read screenshots after OCR fails.
- Add dashboard charts from the stored `ops_reports` history.
- Add a chat-id discovery command for non-technical setup.

## Cash Flow HQ

Cash Flow HQ creates a Notion foundation for business bills, payroll, Jim remit, manual expenses, and weekly/monthly cash obligations.

Phase 1 creates the Notion database and views. Phase 2 scans Outlook for likely bill-related emails and creates Cash Flow HQ rows for review. It does not detect payment confirmations, create daily automation, mark anything paid, run advanced analytics, or connect to the main dashboard.

### Configuration

Add these values to `.env`:

```bash
NOTION_API_KEY=
CASH_FLOW_HQ_PARENT_PAGE_ID=
CASH_FLOW_HQ_DATABASE_NAME=Cash Flow HQ
NOTION_VERSION=2026-03-11
CASH_FLOW_HQ_MAILBOX_USER_ID=
```

Notion values:

| Variable | Where it comes from |
| --- | --- |
| `NOTION_API_KEY` | Notion Developer portal. Use an internal integration token or a personal access token. Keep it private. |
| `CASH_FLOW_HQ_PARENT_PAGE_ID` | The Notion page URL where Cash Flow HQ should live. Copy the page ID from the URL after sharing that page with the Notion integration. |
| `CASH_FLOW_HQ_DATABASE_NAME` | The database name this tool creates/finds. Leave it as `Cash Flow HQ` unless you intentionally renamed it. |
| `NOTION_VERSION` | The Notion API version header used by the code. Leave it as `2026-03-11`. |

Before running a scan, open the parent page in Notion, use the page menu to add/share the Notion connection, and confirm the connection can insert and query content. The Outlook scan also uses the existing `MS_GRAPH_TENANT_ID`, `MS_GRAPH_CLIENT_ID`, and `MS_GRAPH_CLIENT_SECRET` settings with Microsoft Graph `Mail.Read`.

If a scan is started without the required Notion settings, the tool stops before scanning and prints a Cash Flow HQ Notion setup error listing the missing values.

### Preview Phase 1

Preview the schema and view names without contacting Notion:

```bash
python main.py cash-flow-preview
```

### Create Phase 1

Create or reuse the `Cash Flow HQ` Notion database and add the Phase 1 views:

```bash
python main.py cash-flow-init
```

The script creates the requested properties, including `Week` and `Month` formulas based on `Due Date`, then creates the requested table views: Dashboard, This Week, This Month, Paid, Auto Pay, Manual Entries, Payroll, Jim Remit, Needs Review, and Past Due.

### Action Required Formula Tools

Inspect the live Cash Flow HQ property types, safety report, and proposed formula without changing the formula:

```bash
python main.py cash-flow-debug-action-required
```

Apply the supported `Action Required` formula patch:

```bash
python main.py cash-flow-patch-action-required
```

For step-by-step Notion API troubleshooting, `python main.py cash-flow-diagnose-action-required` attempts each diagnostic formula patch and reports whether Notion accepted it. Unlike the debug command, both `patch` and `diagnose` modify the live `Action Required` property. All three commands accept `--env-file` and require the Cash Flow HQ Notion settings listed above, plus access to the existing Cash Flow HQ data source.

### Scan Outlook Email

Preview likely bill email imports without writing Notion rows:

```bash
python main.py cashflow-scan-email --dry-run --days 7 --limit 50
```

Run the Phase 2 import:

```bash
python main.py cashflow-scan-email --days 7 --limit 50
```

Add `--debug` to log extracted vendor, amount, due date, and status for each candidate:

```bash
python main.py cashflow-scan-email --dry-run --debug --days 7 --limit 50
```

The scanner searches the configured mailbox Inbox for recent bill-related terms such as invoice, bill, statement, amount due, payment due, due date, autopay, subscription, renewal, receipt, and payment reminder.

When an email has enough detail, the scanner creates a Cash Flow HQ row with `Status = Upcoming` and `Source = Email`. If the amount or due date is missing, it creates the row as `Status = Needs Review`. It does not guess missing amounts or due dates, does not mark anything paid, and does not overwrite Manual, Payroll, or Jim Remit entries.

Duplicate protection checks the original email link first, then checks for an existing row with the same vendor/payee, amount, and due date. Matching rows are skipped rather than updated.

### Payment Scan

Preview payment confirmation matches without updating Notion:

```bash
python main.py cashflow-payment-scan --dry-run --debug --days 7 --limit 50
```

Run the payment confirmation scan:

```bash
python main.py cashflow-payment-scan --days 7 --limit 50
```

The payment scan only updates existing Cash Flow HQ rows when the match is high confidence. It never creates new bills.

### Review Workflow

Show the Cash Flow HQ review queue:

```bash
python main.py cashflow-review --days 7 --limit 50
```

Update a bill after manual review:

```bash
python main.py cashflow-update-bill --page-id <notion-page-id> --due-date 2026-07-20 --amount 99.50 --status Upcoming
```

Mark a reviewed bill paid:

```bash
python main.py cashflow-mark-paid --page-id <notion-page-id> --payment-date 2026-07-13 --payment-method Manual
```

Hide an unmatched payment confirmation from future review output:

```bash
python main.py cashflow-ignore-email --message-id <outlook-message-id>
```

Ignored review email IDs are stored locally at `CASH_FLOW_HQ_REVIEW_STATE_PATH`, defaulting to `.cash_flow_hq_review.json`.

### Twice-Daily Automation

Run one combined Cash Flow HQ pass manually:

```bash
python main.py cashflow-run --days 7 --limit 50
```

Start the long-running Cash Flow HQ scheduler:

```bash
python main.py cashflow-scheduler --days 7 --limit 50
```

By default, the scheduler runs daily at `10:00` and `17:00`. Override the times with:

```bash
CASH_FLOW_HQ_RUN_TIMES=10:00,17:00
```

Each scheduled run performs the bill email scan first, then the payment confirmation scan. Use `--dry-run` to test the scheduled path without importing bills or marking payments paid.

Install the scheduler as a macOS LaunchAgent:

```bash
scripts/install_cash_flow_hq_scheduler.sh
scripts/status_cash_flow_hq_scheduler.sh
```

Remove it with:

```bash
scripts/uninstall_cash_flow_hq_scheduler.sh
```

## Shared Dashboard Data and Needs Review

The dashboard includes a read-only normalized data service in `agents/dashboard/shared_data.py`. It reads `SharedRecordRepository` records and agent-run history to provide Decimal-safe cash summaries, upcoming and past-due bills, recent remits, agent health, and a centralized `Needs Review` section.

Records enter the review queue when normalized status is `needs_review`, review status is `pending`, action is required, confidence is below 72%, or an agent run failed. Queue filters support record type, source system, priority, review status, action requirement, and effective-date range. Only allowlisted operational metadata is returned.

Read-only routes:

```text
GET /api/shared-dashboard
GET /api/needs-review?page=1&page_size=25
GET /api/needs-review/<shared-record-id>
GET /api/agent-health
```

Open `GET /needs-review` for the read-only filtered list view.

The service supports normalized Cash Flow HQ, ICR Remit, and agent-run records. The configured dashboard uses a durable SQLite repository so shared records, review decisions, audit events, and run history survive restarts.

The Needs Review page supports controlled local Approve, Reject, and Resolve decisions for shared records. Each action requires a reviewer, explicit confirmation, a current record timestamp, a unique request ID, and a valid process-local CSRF token. Reject also requires a reason. Successful decisions append an audit event and remove the item from the open queue. Failed agent-run projections are not actionable.

```text
POST /api/needs-review/<shared-record-id>/approve
POST /api/needs-review/<shared-record-id>/reject
POST /api/needs-review/<shared-record-id>/resolve
GET  /api/needs-review/<shared-record-id>/audit
```

These actions only change the injected shared repository. They do not call Notion, Outlook, Teams, Google Sheets, payment services, or existing agent storage.

Configure the database location if the durable macOS default is not appropriate:

```bash
SHARED_DATA_DATABASE_PATH=~/Library/Application Support/UCM/payment-agent/shared_ucm_data.sqlite3
SHARED_DATA_SYNC_ENABLED=true
SHARED_DATA_SYNC_INTERVAL_MINUTES=60
SHARED_DATA_SYNC_SOURCE=all
SHARED_DATA_SYNC_LIMIT=100
SHARED_DATA_SYNC_RUN_AT_START=true
```

Initialize and verify it without importing source records:

```bash
python main.py shared-data-init
python main.py shared-data-status
```

The status command checks integrity, foreign keys, schema versions, counts, and duplicate groups. No historical source data is imported automatically.

### Preview and synchronize existing records

Preview source records without writing the shared database:

```bash
python main.py shared-data-sync --source cash-flow
python main.py shared-data-sync --source icr
python main.py shared-data-sync --source all --limit 100
```

The command reports creates, updates, skips, conflicts, and normalization errors. Cash Flow HQ is read from Notion; ICR is read from existing local import history. Neither source is modified.

After reviewing a conflict-free preview, explicitly apply the same reconciliation plan to shared SQLite:

```bash
python main.py shared-data-sync --source all --apply --confirm APPLY_SHARED_SYNC
```

Apply is all-or-nothing at the plan level: source errors or human-review conflicts block every create/update. Approved, rejected, and resolved decisions are preserved. Repeated runs skip unchanged records.

Cash Flow HQ `Action Required = No` and equivalent negative values do not enter Needs Review. `Yes` becomes a clear action, while specific formula instructions remain unchanged.

### Scheduled synchronization

Run one guarded scheduled-style synchronization and record its agent-run history:

```bash
python main.py shared-data-sync-once
```

Run the long-lived scheduler using the configured interval:

```bash
python main.py shared-data-run
```

Install it as an independent macOS LaunchAgent:

```bash
scripts/install_shared_data_agent.sh
scripts/status_shared_data_agent.sh
```

Remove only the synchronization LaunchAgent with `scripts/uninstall_shared_data_agent.sh`. The worker is separate from the Payment Agent and dashboard. Every attempt records start/completion times, status, create/update/skip counts, conflicts, errors, and external sources in shared SQLite.

The dashboard Shared Data Sync card shows the latest run, configured interval/source, recent failures, and a confirmed `Sync Now` action. Sync failures appear as read-only operational alerts in Needs Review. No sync writes back to Notion, Outlook, Teams, ICR history, or source files.

The dashboard presents Cash Flow Forecast and Needs Review as separate full-width sections so financial totals, filters, and review reasons remain easy to scan. The wider responsive canvas, clearer card spacing, accessible focus states, and alternating table rows improve readability without changing dashboard actions or data behavior. Needs Review shows at most five priority items; the full queue remains available at `/needs-review`. Missing business inputs for broader cash forecasting are documented in `docs/cash_flow_dashboard_data_requirements.md` and are never guessed.
