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
    "Insurance",
    "Debt Purchase",
    "Loan Payment",
    "Office Expense",
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
FREQUENCY_OPTIONS = ["Weekly", "Biweekly", "Monthly", "Quarterly", "Annual", "One-Time"]
SOURCE_OPTIONS = ["Email", "Manual", "Payroll", "Jim Remit"]
DUE_STATUS_PROPERTY_NAME = "Due Status"
DASHBOARD_VIEW_PROPERTIES = [
    "Expense Name",
    "Amount",
    "Status",
    DUE_STATUS_PROPERTY_NAME,
    "Due Date",
    "Vendor / Payee",
    "Category",
    "Payment Type",
    "Frequency",
    "Source",
    "Email Link",
    "Notes",
]
DASHBOARD_HIDDEN_PROPERTIES = {"Notes", "Month", "Week", "Payment Date"}
DUE_STATUS_FORMULA = (
    'if(empty(prop("Due Date")), "", '
    'if(dateBetween(prop("Due Date"), now(), "days") < 0, '
    '"🔴 Past Due by " + format(abs(dateBetween(prop("Due Date"), now(), "days"))) + " Days", '
    'if(dateBetween(prop("Due Date"), now(), "days") == 0, "🟡 Due Today", '
    'if(dateBetween(prop("Due Date"), now(), "days") == 1, "🟡 Due Tomorrow", '
    '"🟢 Due in " + format(dateBetween(prop("Due Date"), now(), "days")) + " Days"))))'
)
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
]
VENDOR_RULE_DEFAULT_STATUS_OPTIONS = ["Upcoming"]
VENDOR_RULE_DATABASE_NAME = "Vendor Rules"
VENDOR_RULE_SEEDS = [
    {
        "vendor_name": "D1AL",
        "match_text": "D1AL",
        "display_name": "D1AL",
        "category": "Software",
        "frequency": "Monthly",
        "due_day": 5,
        "payment_type": "Manual",
        "default_status": "Upcoming",
        "active": True,
        "notes": "Seeded Phase 2.7 rule.",
    },
    {
        "vendor_name": "Pope and Land",
        "match_text": "Pope and Land",
        "display_name": "Pope & Land",
        "category": "Rent",
        "frequency": "Monthly",
        "due_day": 1,
        "payment_type": "Manual",
        "default_status": "Upcoming",
        "active": True,
        "notes": "Seeded Phase 2.7 rule.",
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
        "Payment Date": {"date": {}},
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
        "Payment Type": select_property(PAYMENT_TYPE_OPTIONS),
        "Default Status": select_property(VENDOR_RULE_DEFAULT_STATUS_OPTIONS),
        "Active": {"checkbox": {}},
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


def due_status_label(due_date: date | None, today: date) -> str:
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
        ViewSpec("This Week", date_empty_filter("Due Date", "this_week")),
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


def build_view_payload(database_id: str, data_source_id: str, spec: ViewSpec) -> dict[str, Any]:
    configuration = {"type": "table", "wrap_cells": True}
    if spec.name == "Dashboard":
        visible_properties = [
            {"property": property_name, "visible": property_name not in DASHBOARD_HIDDEN_PROPERTIES}
            for property_name in DASHBOARD_VIEW_PROPERTIES
        ]
        hidden_properties = [
            {"property": property_name, "visible": False}
            for property_name in DASHBOARD_HIDDEN_PROPERTIES
            if property_name not in DASHBOARD_VIEW_PROPERTIES
        ]
        configuration["properties"] = visible_properties + hidden_properties
    payload: dict[str, Any] = {
        "database_id": database_id,
        "data_source_id": data_source_id,
        "name": spec.name,
        "type": "table",
        "sorts": [{"property": "Due Date", "direction": "ascending"}],
        "configuration": configuration,
    }
    if spec.filter:
        payload["filter"] = spec.filter
    return payload


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
