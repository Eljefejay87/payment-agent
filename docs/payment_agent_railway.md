# Payment Agent Railway Deployment Notes

This document prepares the existing Payment Agent for Railway. It does not add new business behavior and does not connect the agent to any other platform.

## Runtime Summary

Railway should run the same production command used locally:

```bash
scripts/railway_payment_agent_start.sh
```

The script initializes the SQLite schema and then runs:

```bash
python main.py run
```

`python main.py run` still performs the same scheduled Payment Agent workflow:

- scan the configured Outlook mailbox;
- parse new payment emails;
- prevent duplicates through SQLite;
- send the configured Teams notification/report;
- mark/move processed or duplicate messages after the correct outcome is confirmed.

`railway.json` supplies this command as Railway configuration-as-code. The
Railway Settings page does not copy configuration-as-code values into its Custom
Start Command field, so that field may remain blank. After this file is pushed,
the deployment details should show the start command as coming from the
repository configuration. The Dockerfile `CMD` provides the same fallback when
Railway builds the service from the Dockerfile.

## Railway Files

- `Dockerfile` builds a Python 3.12 Linux container.
- `railway.json` tells Railway to build from the Dockerfile and restart on failure.
- `.dockerignore` keeps secrets, local databases, logs, screenshots, reports, and local runtime files out of the deployment image.
- `.env.railway.example` lists only the Payment Agent variables needed in Railway.
- `scripts/railway_payment_agent_start.sh` is the Railway start command.

## Required Railway Variables

Set these in Railway service variables:

```dotenv
DRY_RUN=false
LOG_LEVEL=INFO
LOG_FORMAT=json
DATABASE_PATH=/data/payment_agent.sqlite3
TIMEZONE=America/New_York
PAYMENT_AGENT_HEALTH_PATH=/data/payment_agent_health.json
PAYMENT_AGENT_RUN_STARTUP_SCAN=true

EMAIL_PROVIDER=microsoft365
MAILBOX_USER_ID=
SENDER_EMAIL=
SUBJECT_CONTAINS=Online Payment -
SCAN_INTERVAL_MINUTES=15
LOOKBACK_HOURS=48

MS_GRAPH_TENANT_ID=
MS_GRAPH_CLIENT_ID=
MS_GRAPH_CLIENT_SECRET=

DAILY_REPORT_TIME=17:00
REPORT_MODE=daily

TEAMS_POST_METHOD=
TEAMS_WEBHOOK_URL=

TEAMS_GRAPH_TENANT_ID=
TEAMS_GRAPH_CLIENT_ID=
TEAMS_GRAPH_CLIENT_SECRET=
TEAMS_GRAPH_TOKEN_CACHE_PATH=/data/.graph_teams_token_cache.bin
TEAMS_CHAT_ID=

SAVE_EMAIL_HTML=false
EMAIL_SNAPSHOT_DIR=/data/email_snapshots
```

Use a Railway volume mounted at `/data` so the SQLite database, health file, optional email snapshots, and delegated Teams token cache survive restarts and redeploys.

## Authentication

### Outlook Email

Outlook mailbox scanning can run unattended in Railway with the current app-only Microsoft Graph flow.

Required Microsoft Graph application permissions in the email tenant:

- `Mail.Read` - read Inbox payment notification emails.
- `Mail.ReadWrite` - mark processed/duplicate emails as read and move them to the cleanup folders.

Admin consent is required for application permissions.

### Teams Notifications

Webhook posting can run unattended if `TEAMS_POST_METHOD=webhook`.

The current `graph_chat` support uses delegated Microsoft Graph chat posting. It can post to an existing group chat, but it depends on an MSAL delegated token cache created through device-code login. In Railway this requires:

- a persistent Railway volume;
- `TEAMS_GRAPH_TOKEN_CACHE_PATH=/data/.graph_teams_token_cache.bin`;
- one successful delegated Teams sign-in before relying on unattended refresh;
- tenant consent for the Teams delegated permissions.

Minimum Teams delegated permissions for the existing `graph_chat` workflow:

- `ChatMessage.Send` - send messages to the configured chat.
- `Chat.ReadBasic` - only needed for `debug-list-teams-chats`.
- `User.Read` - standard delegated sign-in/profile permission.
- `offline_access`, `openid`, and `profile` are normally added during delegated sign-in so MSAL can refresh tokens.

If Railway cannot complete or refresh the delegated Teams token, use a Teams incoming webhook or move notifications to a channel/app-only design later. Do not attempt cross-tenant auth with the email app.

## Health Check

The runtime writes a small JSON health file at `PAYMENT_AGENT_HEALTH_PATH`.

Read it locally with:

```bash
python main.py health
```

It reports:

- service status;
- Graph availability;
- whether Microsoft Graph authentication requires attention;
- last successful run;
- last failed job;
- a sanitized error category, if any;
- update timestamp and process id.

No secrets, tokens, Graph response bodies, mailbox details, or payment payloads are stored in the health file.

### Microsoft Graph authentication recovery

The mailbox client refreshes its app-only access token before expiry. If Microsoft Graph rejects a token with `401`, it invalidates the cached token, obtains one fresh client-credential token, and retries the request once. If authentication remains unavailable, the scheduled job records `service_status=running`, `graph_status=unavailable`, and `attention_required=true`, then waits for the next scheduled run instead of terminating the worker.

This does not repair invalid or revoked application credentials. Correct the existing managed secret through the approved credential process, then verify the next scheduled scan or the read-only health command. Do not place token values or Graph response bodies in Railway logs.

## Local Safe Validation

To verify the Railway process shape without scanning live mail, use a temporary env file with:

```dotenv
DRY_RUN=true
LOG_FORMAT=json
DATABASE_PATH=/tmp/ucm-payment-agent-railway-test/payment_agent.sqlite3
PAYMENT_AGENT_HEALTH_PATH=/tmp/ucm-payment-agent-railway-test/health.json
PAYMENT_AGENT_RUN_STARTUP_SCAN=false
SCAN_INTERVAL_MINUTES=1440
REPORT_MODE=daily
TEAMS_POST_METHOD=webhook
MS_GRAPH_TENANT_ID=test
MS_GRAPH_CLIENT_ID=test
MS_GRAPH_CLIENT_SECRET=test
MAILBOX_USER_ID=test@example.com
SENDER_EMAIL=sender@example.com
```

Then run:

```bash
python main.py run --env-file /tmp/ucm-payment-agent-railway-test.env
python main.py health --env-file /tmp/ucm-payment-agent-railway-test.env
```

Stop the run process with Ctrl+C. This validates startup, structured logs, graceful shutdown, and health output without scanning mail or posting Teams messages.

## Restart Safety

Duplicate protection remains in SQLite:

- `processed_emails.message_id`
- `processed_emails.internet_message_id`
- payment fingerprint fallback in `payments`

Railway must persist `DATABASE_PATH` on a volume. If the database is stored inside the container filesystem, redeploys can erase duplicate state and old Inbox messages could be processed again.

## Cloud Blockers To Resolve Before Deployment

1. Add a Railway volume mounted at `/data`.
2. Confirm the email app registration has application `Mail.Read` and `Mail.ReadWrite` with admin consent.
3. Validate Graph authentication recovery in a non-production Railway service with non-production credentials and no startup scan.
4. Decide the Teams cloud path:
   - webhook for easiest unattended operation, or
   - delegated `graph_chat` with a persistent token cache and initial sign-in.
5. Set `DRY_RUN=false` only after Railway env variables and auth are confirmed.
