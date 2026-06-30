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
United Remit*.xlsx
United Liq*.xlsx
```

The spreadsheet contents and formatting are not changed. The agent only validates that both files exist, sends them, records the send in SQLite, sends the owner Teams confirmation, and moves the sent files into a dated folder:

```text
remits/sent/ICR/YYYY-MM-DD/
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
REMIT_ALLOWED_EXTENSIONS=.xlsx,.xls
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

## UCM Admin Dashboard

The local UCM Admin Dashboard gives you one browser page for the current and future UCM agents.

V1 includes:

- Payment Agent status, today's payment count, today's collected total, and recent payments.
- Weekly Remit Agent file status for `United Remit` and `United Liq`.
- Buttons to scan payments, open the ICR remit drop folder, and send the weekly remit when files are ready.
- Placeholders for Placement, Compliance, Finance, and Executive Dashboard agents.

Dashboard settings:

```dotenv
DASHBOARD_HOST=127.0.0.1
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
