# Cash Flow Dashboard Data Requirements

The current dashboard can calculate Past Due, Due Today, Next 7 Days, Next 30 Days, This Month, AutoPay/Manual totals, review counts, and upcoming payments from existing Cash Flow HQ records when amount, due date, status, and payment type are populated.

The following business values are not reliably available from the current normalized records. The dashboard does not display or invent them.

| Dashboard field | Why it is unavailable | Data needed | Suggested format | Recommended source |
| --- | --- | --- | --- | --- |
| Starting cash balance | No bank or ledger balance is stored in Cash Flow HQ | Balance amount and effective timestamp | USD decimal plus ISO-8601 timestamp | Manually configured initially; later imported from an approved bank/ledger report |
| Current bank balance | No bank feed or reconciled balance record exists | Account, cleared balance, available balance, timestamp | One record per account with USD decimals | Imported from an approved financial report; do not infer from bills |
| Forecasted collections | Current records describe obligations, not expected incoming collections | Expected amount, expected date, client/source, confidence/status | USD decimal, ISO date, normalized status | Imported from an existing collections/remit forecast report |
| Expected client remit | ICR data currently represents the payable obligation, not a general receivable forecast | Client, expected receipt amount/date, remit status | USD decimal and ISO date per client/remit | Imported from an existing client-remit report |
| Payroll schedule and amount | No complete structured payroll calendar is present in normalized records | Pay date, gross cash requirement, payroll type/status | USD decimal and ISO pay date | Manually configured or imported from an approved payroll report |
| Expected payment date | Due Date does not indicate when UCM actually plans to pay | Planned payment date and decision status | ISO date plus planned/approved status | Manually set in Cash Flow HQ |
| Recurring bill amount | Vendor Rules may describe recurrence, but missing bill amounts are intentionally not guessed | Vendor-specific amount or calculation rule and effective period | USD decimal or documented formula with effective dates | Manually configured Vendor Rule or imported invoice |
| AutoPay status for incomplete rows | AutoPay can be calculated only when Payment Type is populated | Explicit AutoPay/Manual selection | Controlled boolean or select value | Manually configured Vendor Rule or existing bill record |

Rent and other individual bill amounts are usable when present in Cash Flow HQ. Missing amounts or dates remain review items rather than being replaced with estimates or zero-value placeholders.
