# ICR Remit Automation

## Overview

The ICR Remit Automation automatically processes incoming ICR remit files placed in the designated folder. It uses the existing `ICRRemitImportService` to parse files, create Notion records, and generate email drafts.

## How It Works

1. Every 15 minutes, the system scans `/remits/incoming/ICR/` for `.xlsx` and `.csv` files
2. Files are processed in oldest-first order (by modification time)
3. For each file:
   - Parses the remit data using existing parser
   - Creates one Notion record in Cash Flow HQ (category: "Broker Remit", source: "Jim Remit")
   - Generates an email draft via Microsoft Graph
   - Moves successfully processed files to `/remits/sent/`
   - Moves duplicate files to `/remits/duplicates/`
   - Leaves failed files in `/remits/incoming/ICR/` for manual review
4. Duplicate detection uses broker + remit week + file-content SHA-256 for new imports, while retaining filename/identity checks for compatibility. Existing rows are migrated with deterministic synthetic legacy hashes when original file contents are unavailable.

## Files Changed

### New Files
- `agents/icr_remit_agent/incoming_processor.py` - Core automation logic
- `launchd/com.ucm.icr-remit-processor.plist` - Scheduled job configuration
- `tests/test_icr_incoming_processor.py` - Comprehensive test suite
- `docs/ICR_REMIT_AUTOMATION.md` - This documentation

### Modified Files
- `agents/icr_remit_agent/main.py` - Added `icr-remit-process-incoming` CLI command

## CLI Commands

### Process Incoming Files (Dry Run)
```bash
cd /Users/jcollins/Documents/AI\ AGENT\ UCM/payment-agent
.venv/bin/python main.py icr-remit-process-incoming --dry-run
```

### Process Incoming Files (Live)
```bash
cd /Users/jcollins/Documents/AI\ AGENT\ UCM/payment-agent
.venv/bin/python main.py icr-remit-process-incoming
```

## Installation

### 1. Verify Prerequisites
Ensure the following environment variables are configured in `.env`:
- `NOTION_API_KEY`
- `CASH_FLOW_HQ_DATA_SOURCE_ID`
- `REMIT_BROKER_EMAIL`
- `REMIT_MAILBOX_USER_ID`
- `REMIT_GRAPH_TENANT_ID`
- `REMIT_GRAPH_CLIENT_ID`
- `REMIT_GRAPH_CLIENT_SECRET`

### 2. Create Liquidation Folder
```bash
mkdir -p "/Users/jcollins/Documents/AI AGENT UCM/payment-agent/remits/incoming/ICR/liquidation"
```

### 3. Install the launchd Job
```bash
# Copy the plist to LaunchAgents
cp "/Users/jcollins/Documents/AI AGENT UCM/payment-agent/launchd/com.ucm.icr-remit-processor.plist" \
   ~/Library/LaunchAgents/

# Load the job
launchctl load ~/Library/LaunchAgents/com.ucm.icr-remit-processor.plist

# Verify it's loaded
launchctl list | grep icr-remit-processor
```

### 4. Verify Installation
```bash
# Check logs after 15 minutes
tail -f "/Users/jcollins/Documents/AI AGENT UCM/payment-agent/logs/icr-remit-processor.out.log"
```

## Usage

### Processing a Remit

1. **Prepare Files:**
   - Place the ICR remit file (`.xlsx` or `.csv`) in `/remits/incoming/ICR/`
   - Place the corresponding liquidation report in `/remits/incoming/ICR/liquidation/`

2. **Automatic Processing:**
   - The system will automatically detect and process the file within 15 minutes
   - Check logs for processing status

3. **Verify Results:**
   - Processed file moved to `/remits/sent/`
   - Notion record created in Cash Flow HQ
   - Email draft created in Outlook

### Manual Processing

If you need to process immediately:
```bash
cd /Users/jcollins/Documents/AI\ AGENT\ UCM/payment-agent
.venv/bin/python main.py icr-remit-process-incoming
```

## Monitoring

### Check Job Status
```bash
launchctl list | grep icr-remit-processor
```

### View Logs
```bash
# Standard output
tail -f "/Users/jcollins/Documents/AI AGENT UCM/payment-agent/logs/icr-remit-processor.out.log"

# Error output
tail -f "/Users/jcollins/Documents/AI AGENT UCM/payment-agent/logs/icr-remit-processor.err.log"
```

### Check Folder Status
```bash
# Incoming files
ls -la "/Users/jcollins/Documents/AI AGENT UCM/payment-agent/remits/incoming/ICR/"

# Processed files
ls -la "/Users/jcollins/Documents/AI AGENT UCM/payment-agent/remits/sent/"

# Duplicate files
ls -la "/Users/jcollins/Documents/AI AGENT UCM/payment-agent/remits/duplicates/"
```

## Troubleshooting

### File Not Processing

1. **Check file format:** Only `.xlsx` and `.csv` files are processed
2. **Check liquidation file:** Must exist in `/remits/incoming/ICR/liquidation/`
3. **Check logs:** Review error logs for specific issues
4. **Verify permissions:** Ensure the process can read/write files

### Duplicate Detection

If a file is marked as duplicate:
- Check if the same remit week was already processed
- Review `/remits/duplicates/` folder
- Check database: `SELECT * FROM icr_remit_imports ORDER BY created_at DESC LIMIT 10;`

### Failed Processing

If a file fails to process:
- File remains in `/remits/incoming/ICR/`
- Check error logs for details
- Verify Notion and Graph API credentials
- Try manual processing with `--dry-run` first

## Rollback

### 1. Stop the Automation
```bash
# Unload the launchd job
launchctl unload ~/Library/LaunchAgents/com.ucm.icr-remit-processor.plist

# Verify it's stopped
launchctl list | grep icr-remit-processor
```

### 2. Remove the launchd Job (Optional)
```bash
rm ~/Library/LaunchAgents/com.ucm.icr-remit-processor.plist
```

### 3. Revert Code Changes
```bash
cd /Users/jcollins/Documents/AI\ AGENT\ UCM/payment-agent

# Remove new files
rm agents/icr_remit_agent/incoming_processor.py
rm tests/test_icr_incoming_processor.py
rm launchd/com.ucm.icr-remit-processor.plist
rm docs/ICR_REMIT_AUTOMATION.md

# Revert main.py to previous version
git checkout agents/icr_remit_agent/main.py
```

### 4. Manual Processing Still Available
Even after rollback, the original manual command still works:
```bash
.venv/bin/python main.py icr-remit-import \
  --file /path/to/remit.xlsx \
  --liquidation-file /path/to/liquidation.xlsx
```

## Testing

### Run Test Suite
```bash
cd /Users/jcollins/Documents/AI\ AGENT\ UCM/payment-agent
.venv/bin/python -m pytest tests/test_icr_incoming_processor.py -v
```

### Test Coverage
- ✅ Empty incoming folder
- ✅ One valid file processing
- ✅ Duplicate file handling
- ✅ Unsupported file types ignored
- ✅ Hidden files ignored (.DS_Store)
- ✅ Failed processing leaves file in incoming
- ✅ Oldest-first ordering
- ✅ Idempotent rerun
- ✅ Dry run mode
- ✅ Missing liquidation file
- ✅ CSV file support

## Safety Features

1. **Dry Run Mode:** Test without creating records or moving files
2. **Duplicate Protection:** Prevents processing the same remit twice
3. **Error Isolation:** Failed files don't block other files
4. **File Preservation:** Failed files remain in incoming for review
5. **Deterministic Ordering:** Oldest files processed first
6. **Hidden File Filtering:** Ignores .DS_Store and other hidden files
7. **Type Filtering:** Only processes .xlsx and .csv files

## Schedule Details

- **Frequency:** Every 15 minutes (900 seconds)
- **Run at Load:** No (prevents immediate execution on boot)
- **Working Directory:** `/Users/jcollins/Documents/AI AGENT UCM/payment-agent`
- **Python:** Uses project virtual environment
- **Logs:** Separate stdout and stderr logs

## Notes

- The automation does NOT create separate "Incoming Weekly Remit" records
- It creates ONE record per remit: "ICR Weekly Remit - {date}"
- Jim's outgoing amount is calculated from the ClientFee column
- Due date follows the Wednesday rule for the remit cycle; Monday/Tuesday sends are due Wednesday, with the parser/service handling the applicable Wednesday for the source date.
- Payroll and weekly loan are separate manual entries (not automated)
