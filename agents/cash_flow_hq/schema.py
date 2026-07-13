from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any


CATEGORY_OPTIONS = [
    "Rent",
    "Payroll",
    "Broker Remit",
    "Software",
    "Utilities",
    "Utilities / Internet",
    "Telecommunications",
    "Telecommunications / Dialer",
    "Insurance",
    "Debt Purchase",
    "Loan Payment",
    "Office Expense",
    "Office Supplies",
    "Office Supplies / Utilities",
    "Marketing",
    "Professional Services",
    "Banking",
    "Licensing",
    "Travel",
    "Collections",
    "Taxes",
    "Miscellaneous",
]

STATUS_OPTIONS = ["Upcoming", "Paid", "Past Due", "Needs Review"]
STATUS_OPTION_COLORS = {
    "Upcoming": "green",
    "Needs Review": "yellow",
    "Past Due": "red",
    "Paid": "blue",
}
PAYMENT_TYPE_OPTIONS = ["Auto Pay", "Manual"]
VENDOR_RULE_PAYMENT_TYPE_OPTIONS = ["Auto Pay", "Manual", "Card / Unknown", "TBD"]
FREQUENCY_OPTIONS = ["Weekly", "Biweekly", "Monthly", "Quarterly", "Annual", "One-Time"]
SOURCE_OPTIONS = ["Email", "Manual", "Payroll", "Jim Remit"]
PAYMENT_SOURCE_OPTIONS = ["Email", "Manual"]
DUE_STATUS_PROPERTY_NAME = "Due Status"
ACTION_REQUIRED_PROPERTY_NAME = "Action Required"
DASHBOARD_VIEW_PROPERTIES = [
    "Expense Name",
    "Amount",
    "Status",
    DUE_STATUS_PROPERTY_NAME,
    ACTION_REQUIRED_PROPERTY_NAME,
    "Due Date",
    "Vendor / Payee",
    "Category",
    "Payment Type",
]
TODAYS_PRIORITIES_VIEW_PROPERTIES = [
    "Expense Name",
    "Amount",
    "Status",
    DUE_STATUS_PROPERTY_NAME,
    "Vendor / Payee",
    "Category",
]
THIS_WEEK_VIEW_PROPERTIES = [
    "Expense Name",
    "Amount",
    "Status",
    DUE_STATUS_PROPERTY_NAME,
    ACTION_REQUIRED_PROPERTY_NAME,
    "Due Date",
    "Vendor / Payee",
]
ALL_VIEW_PROPERTIES = [
    "Expense Name",
    "Amount",
    "Status",
    DUE_STATUS_PROPERTY_NAME,
    "Due Date",
    ACTION_REQUIRED_PROPERTY_NAME,
    "Vendor / Payee",
    "Category",
    "Payment Type",
    "Frequency",
    "Source",
    "Email Link",
    "Notes",
    "Month",
    "Week",
    "Payment Date",
]
DASHBOARD_HIDDEN_PROPERTIES = {"Email Link", "Source", "Frequency", "Month", "Week", "Payment Date", "Notes"}
DUE_STATUS_FORMULA = (
    'if(format(prop("Status")) == "Paid", "Paid", '
    'if(empty(prop("Due Date")), "", '
    'if(dateBetween(prop("Due Date"), now(), "days") < 0, '
    '"🔴 Past Due by " + format(abs(dateBetween(prop("Due Date"), now(), "days"))) + " Days", '
    'if(dateBetween(prop("Due Date"), now(), "days") == 0, "🟡 Due Today", '
    'if(dateBetween(prop("Due Date"), now(), "days") == 1, "🟡 Due Tomorrow", '
    '"🟢 Due in " + format(dateBetween(prop("Due Date"), now(), "days")) + " Days")))))'
)
ACTION_REQUIRED_FORMULA = (
    'if(format(prop("Status")) == "Paid", '
    '"OK", '
    'if(empty(prop("Vendor / Payee")), '
    '"Needs Review", '
    'if(empty(prop("Amount")), '
    '"Needs Review", '
    'if(empty(prop("Due Date")), '
    '"Needs Review", '
    'if(empty(prop("Category")), '
    '"Needs Review", '
    'if(and(format(prop("Status")) != "Paid", dateBetween(now(), prop("Due Date"), "days") > 0), '
    '"Past Due", '
    'if(and(format(prop("Status")) != "Paid", format(prop("Payment Type")) != "Auto Pay"), '
    '"Pay Now", '
    'if(and(format(prop("Status")) != "Paid", format(prop("Payment Type")) == "Auto Pay"), '
    '"Upcoming AutoPay", '
    '"OK"))))))))'
)
ACTION_REQUIRED_FALLBACK_FORMULA = (
    'if(or(format(prop("Status")) == "Needs Review", format(prop("Status")) == "Past Due"), "Yes", "No")'
)
ACTION_REQUIRED_DIAGNOSTIC_PROPERTIES = [
    "Vendor / Payee",
    "Status",
    "Amount",
    "Due Date",
    "Category",
    "Payment Type",
]
VENDOR_RULE_FREQUENCY_OPTIONS = ["Weekly", "Biweekly", "Monthly", "Quarterly", "Annual"]
VENDOR_RULE_CATEGORY_OPTIONS = [
    "Rent",
    "Software",
    "Insurance",
    "Payroll",
    "Utilities",
    "Office Supplies",
    "Marketing",
    "Professional Services",
    "Banking",
    "Taxes",
    "Licensing",
    "Travel",
    "Collections",
    "Telecommunications",
    "Telecommunications / Dialer",
    "Utilities / Internet",
    "Office Supplies / Utilities",
]
VENDOR_RULE_DEFAULT_STATUS_OPTIONS = ["Upcoming"]
VENDOR_RULE_DATABASE_NAME = "Vendor Rules"
VENDOR_RULE_SEEDS = [
    {
        "vendor_name": "D1AL",
        "match_text": "D1AL",
        "display_name": "D1AL",
        "service": "Voice Dialer",
        "category": "Telecommunications / Dialer",
        "frequency": "Monthly",
        "due_day": 1,
        "invoice_day": 1,
        "pay_by_day": 1,
        "auto_pay": True,
        "payment_type": "Auto Pay",
        "default_status": "Upcoming",
        "active": True,
        "critical": True,
        "typical_amount": 315.37,
        "notes": "Recurring vendor v1.1.",
    },
    {
        "vendor_name": "Pope and Land",
        "match_text": "Pope and Land",
        "display_name": "Pope & Land",
        "service": "Office Rent",
        "category": "Rent",
        "frequency": "Monthly",
        "due_day": 1,
        "invoice_day": 1,
        "pay_by_day": 5,
        "grace_period_days": 4,
        "auto_pay": False,
        "payment_type": "Manual",
        "default_status": "Upcoming",
        "active": True,
        "critical": True,
        "typical_amount": 3958.07,
        "notes": "Recurring vendor v1.1.",
    },
    {
        "vendor_name": "Vaspian",
        "match_text": "Vaspian",
        "display_name": "Vaspian",
        "service": "SMS/Text Messaging",
        "category": "Telecommunications",
        "frequency": "Monthly",
        "due_day": 1,
        "invoice_day": 1,
        "pay_by_day": 1,
        "auto_pay": True,
        "payment_type": "Auto Pay",
        "default_status": "Upcoming",
        "active": True,
        "critical": True,
        "typical_amount": 550.00,
        "notes": "Recurring vendor v1.1.",
    },
    {
        "vendor_name": "SCollect",
        "match_text": "SCollect",
        "display_name": "SCollect",
        "service": "Collection Software",
        "category": "Software",
        "frequency": "Monthly",
        "due_day": 5,
        "invoice_day": 1,
        "pay_by_day": 5,
        "auto_pay": True,
        "payment_type": "Auto Pay",
        "default_status": "Upcoming",
        "active": True,
        "critical": True,
        "billing_model": "Per User + Server Fee",
        "rate_per_user": 50.00,
        "current_user_count": 10,
        "monthly_server_fee": 100.00,
        "typical_amount": 600.00,
        "notes": "Invoice on the 1st; AutoPay on the 5th. Calculation: (10 users x $50) + $100 server fee = $600.",
    },
    {
        "vendor_name": "Comcast Business",
        "match_text": "Comcast",
        "display_name": "Comcast Business",
        "service": "Internet",
        "category": "Utilities / Internet",
        "frequency": "Monthly",
        "due_day": 10,
        "invoice_day": 10,
        "pay_by_day": 10,
        "auto_pay": False,
        "payment_type": "Manual",
        "default_status": "Upcoming",
        "active": True,
        "critical": True,
        "typical_amount": 298.00,
        "notes": "Recurring vendor v1.1.",
    },
    {
        "vendor_name": "Concepts2Code",
        "match_text": "Concepts2Code",
        "display_name": "Concepts2Code",
        "service": "Software Development",
        "category": "Professional Services",
        "frequency": "Monthly",
        "due_day": 1,
        "invoice_day": 1,
        "pay_by_day": 1,
        "auto_pay": True,
        "payment_type": "Auto Pay",
        "default_status": "Upcoming",
        "active": True,
        "critical": False,
        "typical_amount": 799.00,
        "notes": "Recurring vendor v1.1.",
    },
    {
        "vendor_name": "Blue Real Spring Water",
        "match_text": "Blue Real",
        "display_name": "Blue Real Spring Water",
        "service": "Office Water Service",
        "category": "Office Supplies / Utilities",
        "frequency": "Monthly",
        "due_day": None,
        "auto_pay": None,
        "payment_type": "Card / Unknown",
        "default_status": "Upcoming",
        "active": True,
        "critical": False,
        "typical_amount": 49.00,
        "notes": "Invoice day/pay by day TBD.",
    },
    {
        "vendor_name": "Coterie",
        "match_text": "Coterie",
        "display_name": "Coterie",
        "service": "E&O Insurance",
        "category": "Insurance",
        "frequency": "Monthly",
        "due_day": None,
        "payment_type": "TBD",
        "default_status": "Upcoming",
        "active": True,
        "critical": True,
        "provider_group": "InsureOne",
        "typical_amount": 229.00,
        "notes": "Due date/autopay/payment type TBD.",
    },
    {
        "vendor_name": "Hiscox",
        "match_text": "Hiscox",
        "display_name": "Hiscox",
        "service": "Cyber Insurance",
        "category": "Insurance",
        "frequency": "Monthly",
        "due_day": None,
        "payment_type": "TBD",
        "default_status": "Upcoming",
        "active": True,
        "critical": True,
        "provider_group": "InsureOne",
        "typical_amount": 147.00,
        "notes": "Due date/autopay/payment type TBD.",
    },
    {
        "vendor_name": "Bitwarden",
        "match_text": "Bitwarden",
        "display_name": "Bitwarden",
        "service": "Password Management",
        "category": "Software",
        "frequency": "Annual",
        "due_day": None,
        "payment_type": "Auto Pay",
        "default_status": "Upcoming",
        "active": True,
        "critical": True,
        "typical_amount": 10.00,
        "notes": "Due date TBD.",
    },
    {
        "vendor_name": "Intuit",
        "match_text": "Intuit",
        "display_name": "Intuit",
        "service": "Accounting / Payroll Software",
        "category": "Software",
        "frequency": "Monthly",
        "due_day": None,
        "payment_type": "Auto Pay",
        "default_status": "Upcoming",
        "active": True,
        "critical": True,
        "notes": "Due date and typical amount TBD.",
    },
    {
        "vendor_name": "ADP",
        "match_text": "ADP",
        "display_name": "ADP",
        "service": "Payroll",
        "category": "Payroll",
        "frequency": "Monthly",
        "due_day": None,
        "payment_type": "TBD",
        "default_status": "Upcoming",
        "active": True,
        "critical": True,
        "typical_amount": 69.90,
        "notes": "Due date/payment type TBD.",
    },
    {
        "vendor_name": "American Express",
        "match_text": "American Express",
        "display_name": "American Express",
        "service": "Business Card",
        "category": "Banking",
        "frequency": "Monthly",
        "due_day": None,
        "payment_type": "Manual",
        "default_status": "Upcoming",
        "active": True,
        "critical": True,
        "notes": "Due date and typical amount TBD.",
    },
    {
        "vendor_name": "OFR",
        "match_text": "FLOFR",
        "display_name": "Florida OFR",
        "service": "State Licensing",
        "category": "Licensing",
        "frequency": "Annual",
        "due_day": None,
        "payment_type": "Manual",
        "default_status": "Upcoming",
        "active": True,
        "critical": True,
        "notes": "Due date and typical amount TBD.",
    },
]


@dataclass(frozen=True)
class ViewSpec:
    name: str
    filter: dict[str, Any] | None = None


def build_database_payload(parent_page_id: str, database_name: str) -> dict[str, Any]:
    return {
        "parent": {"type": "page_id", "page_id": normalize_notion_uuid(parent_page_id)},
        "title": [{"type": "text", "text": {"content": database_name}}],
        "description": [
            {
                "type": "text",
                "text": {
                    "content": (
                        "Tracks business bills, payroll, Jim remit, manual expenses, "
                        "and weekly/monthly cash obligations."
                    )
                },
            }
        ],
        "is_inline": False,
        "initial_data_source": {
            "title": [{"type": "text", "text": {"content": database_name}}],
            "properties": build_properties(),
        },
    }


def build_vendor_rules_database_payload(parent_page_id: str) -> dict[str, Any]:
    return {
        "parent": {"type": "page_id", "page_id": normalize_notion_uuid(parent_page_id)},
        "title": [{"type": "text", "text": {"content": VENDOR_RULE_DATABASE_NAME}}],
        "description": [
            {
                "type": "text",
                "text": {"content": "Rules for recurring Cash Flow HQ vendor classification."},
            }
        ],
        "is_inline": False,
        "initial_data_source": {
            "title": [{"type": "text", "text": {"content": VENDOR_RULE_DATABASE_NAME}}],
            "properties": build_vendor_rule_properties(),
        },
    }


def build_properties() -> dict[str, Any]:
    return {
        "Expense Name": {"title": {}},
        "Vendor / Payee": {"rich_text": {}},
        "Category": select_property(CATEGORY_OPTIONS),
        "Amount": {"number": {"format": "dollar"}},
        "Due Date": {"date": {}},
        DUE_STATUS_PROPERTY_NAME: due_status_property(),
        ACTION_REQUIRED_PROPERTY_NAME: action_required_property(),
        "Payment Date": {"date": {}},
        "Payment Source": select_property(PAYMENT_SOURCE_OPTIONS),
        "Payment Confirmation Subject": {"rich_text": {}},
        "Confirmation Link": {"url": {}},
        "Payment Method": select_property(PAYMENT_TYPE_OPTIONS),
        "Invoice Number": {"rich_text": {}},
        "Status": status_select_property(),
        "Payment Type": select_property(PAYMENT_TYPE_OPTIONS),
        "Frequency": select_property(FREQUENCY_OPTIONS),
        "Week": {
            "formula": {
                "expression": 'if(empty(prop("Due Date")), "", formatDate(prop("Due Date"), "YYYY-[W]WW"))'
            }
        },
        "Month": {
            "formula": {
                "expression": 'if(empty(prop("Due Date")), "", formatDate(prop("Due Date"), "YYYY-MM"))'
            }
        },
        "Source": select_property(SOURCE_OPTIONS),
        "Email Link": {"url": {}},
        "Notes": {"rich_text": {}},
    }


def build_vendor_rule_properties() -> dict[str, Any]:
    return {
        "Vendor Name": {"title": {}},
        "Match Text": {"rich_text": {}},
        "Display Name": {"rich_text": {}},
        "Category": select_property(VENDOR_RULE_CATEGORY_OPTIONS),
        "Frequency": select_property(VENDOR_RULE_FREQUENCY_OPTIONS),
        "Due Day": {"number": {"format": "number"}},
        "Payment Type": select_property(VENDOR_RULE_PAYMENT_TYPE_OPTIONS),
        "Default Status": select_property(VENDOR_RULE_DEFAULT_STATUS_OPTIONS),
        "Active": {"checkbox": {}},
        "Service": {"rich_text": {}},
        "Invoice Day": {"number": {"format": "number"}},
        "Pay By Day": {"number": {"format": "number"}},
        "Grace Period Days": {"number": {"format": "number"}},
        "AutoPay": {"checkbox": {}},
        "Critical": {"checkbox": {}},
        "Typical Amount": {"number": {"format": "dollar"}},
        "Billing Model": {"rich_text": {}},
        "Rate Per User": {"number": {"format": "dollar"}},
        "Current User Count": {"number": {"format": "number"}},
        "Monthly Server Fee": {"number": {"format": "dollar"}},
        "Provider Group": {"rich_text": {}},
        "Notes": {"rich_text": {}},
    }


def select_property(options: list[str]) -> dict[str, Any]:
    return {"select": {"options": [{"name": option} for option in options]}}


def status_select_property() -> dict[str, Any]:
    return {
        "select": {
            "options": [
                {"name": option, "color": STATUS_OPTION_COLORS[option]}
                for option in STATUS_OPTIONS
            ]
        }
    }


def due_status_property() -> dict[str, Any]:
    return {"formula": {"expression": DUE_STATUS_FORMULA}}


def action_required_property() -> dict[str, Any]:
    return {"formula": {"expression": build_action_required_formula()}}


def build_action_required_formula(properties: dict[str, Any] | None = None) -> str:
    properties = properties or {}
    open_bill = 'format(prop("Status")) != "Paid"'
    due_days = 'dateBetween(now(), prop("Due Date"), "days")'
    auto_pay_condition = 'format(prop("Payment Type")) == "Auto Pay"'
    manual_payment_condition = 'format(prop("Payment Type")) != "Auto Pay"'
    if property_type(properties, "AutoPay") == "checkbox":
        auto_pay_condition = 'prop("AutoPay")'
        manual_payment_condition = 'prop("AutoPay") == false'

    return (
        'if(format(prop("Status")) == "Paid", '
        '"OK", '
        'if(empty(prop("Vendor / Payee")), '
        '"Needs Review", '
        'if(empty(prop("Amount")), '
        '"Needs Review", '
        'if(empty(prop("Due Date")), '
        '"Needs Review", '
        'if(empty(prop("Category")), '
        '"Needs Review", '
        f"if(and({open_bill}, {due_days} > 0), "
        '"Past Due", '
        f"if(and({open_bill}, {manual_payment_condition}), "
        '"Pay Now", '
        f"if(and({open_bill}, {auto_pay_condition}), "
        '"Upcoming AutoPay", '
        '"OK"))))))))'
    )


def build_action_required_diagnostic_formulas(
    properties: dict[str, Any] | None = None,
) -> list[tuple[str, str]]:
    missing_fields = (
        'if(empty(prop("Vendor / Payee")), "Needs Review", '
        'if(empty(prop("Amount")), "Needs Review", '
        'if(empty(prop("Due Date")), "Needs Review", '
        'if(empty(prop("Category")), "Needs Review", "OK"))))'
    )
    missing_plus_past_due = (
        'if(empty(prop("Vendor / Payee")), "Needs Review", '
        'if(empty(prop("Amount")), "Needs Review", '
        'if(empty(prop("Due Date")), "Needs Review", '
        'if(empty(prop("Category")), "Needs Review", '
        'if(and(format(prop("Status")) != "Paid", dateBetween(now(), prop("Due Date"), "days") > 0), '
        '"Past Due", "OK")))))'
    )
    missing_plus_pay_now = (
        'if(empty(prop("Vendor / Payee")), "Needs Review", '
        'if(empty(prop("Amount")), "Needs Review", '
        'if(empty(prop("Due Date")), "Needs Review", '
        'if(empty(prop("Category")), "Needs Review", '
        'if(and(format(prop("Status")) != "Paid", dateBetween(now(), prop("Due Date"), "days") > 0), '
        '"Past Due", '
        'if(and(format(prop("Status")) != "Paid", format(prop("Payment Type")) != "Auto Pay"), '
        '"Pay Now", "OK"))))))'
    )
    return [
        ("missing fields only", missing_fields),
        ("missing fields plus past due", missing_plus_past_due),
        ("missing fields plus pay now", missing_plus_pay_now),
        ("full nested formula", build_action_required_formula(properties)),
    ]


def property_type(properties: dict[str, Any], property_name: str) -> str:
    property_payload = properties.get(property_name) or {}
    if "type" in property_payload:
        return str(property_payload.get("type") or "")
    for key in property_payload:
        if key not in {"id", "name", "description"}:
            return key
    return "missing"


def action_required_formula_diagnostics(properties: dict[str, Any]) -> dict[str, Any]:
    checked = {name: property_type(properties, name) for name in ACTION_REQUIRED_DIAGNOSTIC_PROPERTIES}
    return {
        "property_types": checked,
        "safety_report": action_required_property_safety_report(checked),
        "full_formula": build_action_required_formula(properties),
        "fallback_formula": ACTION_REQUIRED_FALLBACK_FORMULA,
        "notes": action_required_formula_notes(checked),
    }


def action_required_formula_notes(property_types: dict[str, str]) -> list[str]:
    notes: list[str] = []
    if property_types.get("Due Date") == "date":
        notes.append("Due Date is a date and is the only property used with dateBetween.")
    else:
        notes.append("Due Date is not a plain date; dateBetween may be rejected.")
    return notes


def action_required_property_safety_report(property_types: dict[str, str]) -> dict[str, list[str]]:
    return {
        "format()": [
            name for name, prop_type in property_types.items()
            if prop_type in {"title", "rich_text", "select", "status", "number", "date", "checkbox", "formula", "rollup"}
        ],
        "empty()": [
            name for name, prop_type in property_types.items()
            if prop_type in {"title", "rich_text", "number", "date", "select", "status", "formula", "rollup"}
        ],
        "direct comparison": [
            name for name, prop_type in property_types.items()
            if prop_type in {"number", "checkbox"}
        ],
        "dateBetween()": [
            name for name, prop_type in property_types.items()
            if prop_type == "date"
        ],
    }


def due_status_label(due_date: date | None, today: date, status: str = "") -> str:
    if status == "Paid":
        return "Paid"
    if due_date is None:
        return ""
    days = (due_date - today).days
    if days < 0:
        return f"🔴 Past Due by {abs(days)} Days"
    if days == 0:
        return "🟡 Due Today"
    if days == 1:
        return "🟡 Due Tomorrow"
    return f"🟢 Due in {days} Days"


def build_view_specs() -> list[ViewSpec]:
    return [
        ViewSpec("Dashboard"),
        ViewSpec("Today's Priorities", todays_priorities_filter()),
        ViewSpec(
            "This Week",
            {
                "and": [
                    due_date_filter({"on_or_after": "today"}),
                    due_date_filter({"on_or_before": "one_week_from_now"}),
                ]
            },
        ),
        ViewSpec(
            "This Month",
            {
                "and": [
                    due_date_filter({"on_or_after": "one_month_ago"}),
                    due_date_filter({"on_or_before": "one_month_from_now"}),
                ]
            },
        ),
        ViewSpec("Paid", select_filter("Status", "Paid")),
        ViewSpec("Auto Pay", select_filter("Payment Type", "Auto Pay")),
        ViewSpec("Manual Entries", select_filter("Source", "Manual")),
        ViewSpec("Payroll", select_filter("Source", "Payroll")),
        ViewSpec("Jim Remit", select_filter("Source", "Jim Remit")),
        ViewSpec("Needs Review", select_filter("Status", "Needs Review")),
        ViewSpec("Past Due", select_filter("Status", "Past Due")),
    ]


def date_empty_filter(property_name: str, operator: str) -> dict[str, Any]:
    return {"property": property_name, "date": {operator: {}}}


def due_date_filter(date_condition: dict[str, Any]) -> dict[str, Any]:
    return {"property": "Due Date", "date": date_condition}


def select_filter(property_name: str, option_name: str) -> dict[str, Any]:
    return {"property": property_name, "select": {"equals": option_name}}


def formula_string_filter(property_name: str, condition: dict[str, str]) -> dict[str, Any]:
    return {"property": property_name, "formula": {"string": condition}}


def todays_priorities_filter() -> dict[str, Any]:
    return {
        "or": [
            select_filter("Status", "Needs Review"),
            formula_string_filter(DUE_STATUS_PROPERTY_NAME, {"contains": "Today"}),
            formula_string_filter(DUE_STATUS_PROPERTY_NAME, {"contains": "Past Due"}),
        ]
    }


def build_view_payload(database_id: str, data_source_id: str, spec: ViewSpec) -> dict[str, Any]:
    configuration = {"type": "table", "wrap_cells": True}
    visible_property_names = view_visible_properties(spec.name)
    if visible_property_names:
        configuration["properties"] = view_property_visibility(visible_property_names)
    payload: dict[str, Any] = {
        "database_id": database_id,
        "data_source_id": data_source_id,
        "name": spec.name,
        "type": "table",
        "sorts": view_sorts(spec.name),
        "configuration": configuration,
    }
    if spec.filter:
        payload["filter"] = spec.filter
    return payload


def view_visible_properties(view_name: str) -> list[str]:
    if view_name == "Dashboard":
        return DASHBOARD_VIEW_PROPERTIES
    if view_name == "Today's Priorities":
        return TODAYS_PRIORITIES_VIEW_PROPERTIES
    if view_name == "This Week":
        return THIS_WEEK_VIEW_PROPERTIES
    return []


def view_property_visibility(visible_property_names: list[str]) -> list[dict[str, Any]]:
    hidden_property_names = [name for name in ALL_VIEW_PROPERTIES if name not in visible_property_names]
    return (
        [{"property": property_name, "visible": True} for property_name in visible_property_names]
        + [{"property": property_name, "visible": False} for property_name in hidden_property_names]
    )


def view_sorts(view_name: str) -> list[dict[str, str]]:
    if view_name == "Today's Priorities":
        return [
            {"property": "Due Date", "direction": "ascending"},
            {"property": "Amount", "direction": "descending"},
        ]
    return [{"property": "Due Date", "direction": "ascending"}]


def extract_data_source_id(database: dict[str, Any]) -> str:
    data_sources = database.get("data_sources") or []
    if not data_sources:
        raise RuntimeError("Notion did not return a data source for the Cash Flow HQ database.")
    return data_sources[0]["id"]


def normalize_notion_uuid(value: str) -> str:
    compact = "".join(re.findall(r"[0-9a-fA-F]", value or ""))
    if len(compact) != 32:
        return value
    return (
        f"{compact[0:8]}-{compact[8:12]}-{compact[12:16]}-"
        f"{compact[16:20]}-{compact[20:32]}"
    ).lower()
