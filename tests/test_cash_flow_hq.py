from __future__ import annotations

import os
import sys
import unittest
from base64 import b64encode
from datetime import date, datetime, timezone
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agents.cash_flow_hq.alerts import CashFlowBill, CashFlowTeamsAlerts, already_sent, build_morning_brief, mark_sent
from agents.cash_flow_hq.config import load_cash_flow_settings, validate_cash_flow_settings
from agents.cash_flow_hq.email_scan import CashFlowEmailScanner
from agents.cash_flow_hq.graph_client import CashFlowGraphClient
from agents.cash_flow_hq.models import AttachmentMetadata, BillEmail, VendorRule
from agents.cash_flow_hq.parser import extract_amount, extract_due_date, is_bill_related, message_from_graph, parse_bill_candidate
from agents.cash_flow_hq.schema import (
    ACTION_REQUIRED_PROPERTY_NAME,
    ACTION_REQUIRED_FALLBACK_FORMULA,
    CATEGORY_OPTIONS,
    DASHBOARD_HIDDEN_PROPERTIES,
    DASHBOARD_VIEW_PROPERTIES,
    DUE_STATUS_PROPERTY_NAME,
    SOURCE_OPTIONS,
    STATUS_OPTIONS,
    THIS_WEEK_VIEW_PROPERTIES,
    TODAYS_PRIORITIES_VIEW_PROPERTIES,
    VENDOR_RULE_DATABASE_NAME,
    VENDOR_RULE_SEEDS,
    action_required_formula_diagnostics,
    build_action_required_diagnostic_formulas,
    build_action_required_formula,
    build_database_payload,
    build_vendor_rules_database_payload,
    build_view_payload,
    build_view_specs,
    due_status_label,
)
from agents.cash_flow_hq.service import CashFlowHQService
from shared.integrations.microsoft_graph import GraphClient, chat_member_bind


class CashFlowHQSchemaTests(unittest.TestCase):
    def test_database_payload_contains_requested_properties(self) -> None:
        payload = build_database_payload("page-id", "Cash Flow HQ")
        properties = payload["initial_data_source"]["properties"]

        self.assertEqual(properties["Expense Name"], {"title": {}})
        self.assertEqual(properties["Vendor / Payee"], {"rich_text": {}})
        self.assertEqual(properties["Amount"], {"number": {"format": "dollar"}})
        self.assertEqual(properties["Due Date"], {"date": {}})
        self.assertIn('prop("Due Date")', properties[DUE_STATUS_PROPERTY_NAME]["formula"]["expression"])
        action_formula = properties[ACTION_REQUIRED_PROPERTY_NAME]["formula"]["expression"]
        self.assertIn('prop("Payment Type")', action_formula)
        self.assertIn('"Needs Review"', action_formula)
        self.assertIn('"Past Due"', action_formula)
        self.assertIn('"Pay Now"', action_formula)
        self.assertIn('"Upcoming AutoPay"', action_formula)
        self.assertIn('"OK"', action_formula)
        self.assertTrue(action_formula.startswith("if("))
        self.assertNotIn("ifs(", action_formula)
        self.assertNotIn("not(", action_formula)
        self.assertIn("dateBetween", action_formula)
        self.assertIn('dateBetween(now(), prop("Due Date"), "days")', action_formula)
        self.assertNotIn('dateBetween(now(), prop("Due Date"), "days") <= 0', action_formula)
        self.assertNotIn('dateBetween(prop("Due Date"), now(), "days")', action_formula)
        self.assertNotIn('dateBetween(dateStart(prop("Due Date")), now(), "days")', action_formula)
        self.assertIn('empty(prop("Vendor / Payee"))', action_formula)
        self.assertIn('empty(prop("Category"))', action_formula)
        self.assertNotIn('or(empty(prop("Vendor / Payee"))', action_formula)
        self.assertIn('format(prop("Payment Type")) != "Auto Pay"', action_formula)
        self.assertEqual(properties["Payment Date"], {"date": {}})
        self.assertEqual(properties["Email Link"], {"url": {}})
        self.assertEqual(properties["Notes"], {"rich_text": {}})
        self.assertIn('prop("Due Date")', properties["Week"]["formula"]["expression"])
        self.assertIn('prop("Due Date")', properties["Month"]["formula"]["expression"])

    def test_due_status_labels_are_human_friendly(self) -> None:
        today = date(2026, 7, 8)

        self.assertEqual(due_status_label(today, today), "🟡 Due Today")
        self.assertEqual(due_status_label(date(2026, 7, 9), today), "🟡 Due Tomorrow")
        self.assertEqual(due_status_label(date(2026, 7, 11), today), "🟢 Due in 3 Days")
        self.assertEqual(due_status_label(date(2026, 7, 6), today), "🔴 Past Due by 2 Days")
        self.assertEqual(due_status_label(None, today), "")
        self.assertEqual(due_status_label(date(2026, 7, 6), today, status="Paid"), "Paid")

    def test_database_payload_uses_current_notion_parent_and_initial_data_source_shape(self) -> None:
        payload = build_database_payload("395d2fa0e6ed80e79603f4ca876c10f1", "Cash Flow HQ")

        self.assertEqual(payload["parent"], {"type": "page_id", "page_id": "395d2fa0-e6ed-80e7-9603-f4ca876c10f1"})
        self.assertEqual(
            payload["initial_data_source"]["title"],
            [{"type": "text", "text": {"content": "Cash Flow HQ"}}],
        )
        self.assertIn("properties", payload["initial_data_source"])

    def test_select_options_match_phase_one_request(self) -> None:
        properties = build_database_payload("page-id", "Cash Flow HQ")["initial_data_source"]["properties"]

        self.assertEqual([item["name"] for item in properties["Category"]["select"]["options"]], CATEGORY_OPTIONS)
        self.assertEqual([item["name"] for item in properties["Status"]["select"]["options"]], STATUS_OPTIONS)
        self.assertEqual(
            {item["name"]: item["color"] for item in properties["Status"]["select"]["options"]},
            {"Upcoming": "green", "Needs Review": "yellow", "Past Due": "red", "Paid": "blue"},
        )
        self.assertEqual([item["name"] for item in properties["Source"]["select"]["options"]], SOURCE_OPTIONS)

    def test_action_required_fallback_formula_is_known_accepted_version(self) -> None:
        self.assertIn('"Yes"', ACTION_REQUIRED_FALLBACK_FORMULA)
        self.assertIn('"No"', ACTION_REQUIRED_FALLBACK_FORMULA)
        self.assertIn('format(prop("Status")) == "Needs Review"', ACTION_REQUIRED_FALLBACK_FORMULA)
        self.assertIn('format(prop("Status")) == "Past Due"', ACTION_REQUIRED_FALLBACK_FORMULA)

    def test_action_required_formula_uses_nested_if_and_date_between(self) -> None:
        formula = build_action_required_formula(
            {
                "AutoPay": {"type": "checkbox", "checkbox": {}},
                "Grace Period Days": {"type": "number", "number": {"format": "number"}},
            }
        )

        self.assertIn('prop("AutoPay")', formula)
        self.assertIn('prop("AutoPay") == false', formula)
        self.assertIn('dateBetween(now(), prop("Due Date"), "days") > 0', formula)
        self.assertNotIn('dateBetween(now(), prop("Due Date"), "days") <= 0', formula)
        self.assertNotIn('dateBetween(prop("Due Date"), now(), "days")', formula)
        self.assertNotIn('dateBetween(dateStart(prop("Due Date")), now(), "days")', formula)
        self.assertIn('and(format(prop("Status")) != "Paid", prop("AutoPay") == false)', formula)
        self.assertIn('and(format(prop("Status")) != "Paid", prop("AutoPay"))', formula)
        self.assertTrue(formula.startswith("if("))
        self.assertNotIn("ifs(", formula)
        self.assertNotIn("not(", formula)
        self.assertNotIn('prop("Due Date") < now()', formula)
        self.assertNotIn('prop("Due Date") >= now()', formula)
        self.assertNotIn('prop("Grace Period Days")', formula)
        self.assertIn('empty(prop("Category"))', formula)
        self.assertIn('empty(prop("Vendor / Payee"))', formula)
        self.assertNotIn('or(empty(prop("Vendor / Payee"))', formula)
        self.assertNotIn('format(prop("Category")) == ""', formula)
        self.assertNotIn('format(prop("Vendor / Payee")) == ""', formula)

    def test_action_required_formula_avoids_rollup_relation_payment_window_fields(self) -> None:
        formula = build_action_required_formula(
            {
                "AutoPay": {"type": "rollup", "rollup": {}},
                "Grace Period Days": {"type": "rollup", "rollup": {}},
                "Pay By Day": {"type": "rollup", "rollup": {}},
                "Invoice Day": {"type": "relation", "relation": {}},
            }
        )

        self.assertIn('format(prop("Payment Type")) == "Auto Pay"', formula)
        self.assertIn('format(prop("Payment Type")) != "Auto Pay"', formula)
        self.assertIn("dateBetween", formula)
        self.assertIn('dateBetween(now(), prop("Due Date"), "days")', formula)
        self.assertNotIn('dateBetween(now(), prop("Due Date"), "days") <= 0', formula)
        self.assertNotIn('dateBetween(prop("Due Date"), now(), "days")', formula)
        self.assertNotIn('dateBetween(dateStart(prop("Due Date")), now(), "days")', formula)
        self.assertNotIn("ifs(", formula)
        self.assertNotIn("not(", formula)
        self.assertNotIn('prop("Due Date") < now()', formula)
        self.assertNotIn('prop("Due Date") >= now()', formula)
        self.assertNotIn('prop("AutoPay")', formula)
        self.assertNotIn('prop("Grace Period Days")', formula)
        self.assertNotIn('prop("Pay By Day")', formula)
        self.assertNotIn('prop("Invoice Day")', formula)

    def test_action_required_diagnostics_report_formula_and_schema_types(self) -> None:
        diagnostics = action_required_formula_diagnostics(
            {
                "Vendor / Payee": {"type": "rich_text", "rich_text": {}},
                "Status": {"type": "select", "select": {}},
                "Amount": {"type": "number", "number": {"format": "dollar"}},
                "Due Date": {"type": "date", "date": {}},
                "Category": {"type": "select", "select": {}},
                "Payment Type": {"type": "select", "select": {}},
            }
        )

        self.assertEqual(
            diagnostics["property_types"],
            {
                "Vendor / Payee": "rich_text",
                "Status": "select",
                "Amount": "number",
                "Due Date": "date",
                "Category": "select",
                "Payment Type": "select",
            },
        )
        self.assertIn('"Pay Now"', diagnostics["full_formula"])
        self.assertIn("Due Date", diagnostics["safety_report"]["dateBetween()"])
        self.assertIn("Amount", diagnostics["safety_report"]["direct comparison"])
        self.assertIn("Category", diagnostics["safety_report"]["format()"])
        self.assertIn("Due Date is a date", diagnostics["notes"][0])

    def test_view_specs_include_requested_views(self) -> None:
        names = [spec.name for spec in build_view_specs()]

        self.assertEqual(
            names,
            [
                "Dashboard",
                "Today's Priorities",
                "This Week",
                "This Month",
                "Paid",
                "Auto Pay",
                "Manual Entries",
                "Payroll",
                "Jim Remit",
                "Needs Review",
                "Past Due",
            ],
        )

    def test_view_payload_targets_database_and_data_source(self) -> None:
        spec = build_view_specs()[2]
        payload = build_view_payload("database-id", "source-id", spec)

        self.assertEqual(payload["database_id"], "database-id")
        self.assertEqual(payload["data_source_id"], "source-id")
        self.assertEqual(payload["name"], "This Week")
        self.assertEqual(
            payload["filter"],
            {
                "and": [
                    {"property": "Due Date", "date": {"on_or_after": "today"}},
                    {"property": "Due Date", "date": {"on_or_before": "one_week_from_now"}},
                ]
            },
        )
        self.assertEqual([item["property"] for item in payload["configuration"]["properties"][: len(THIS_WEEK_VIEW_PROPERTIES)]], THIS_WEEK_VIEW_PROPERTIES)

    def test_dashboard_view_payload_orders_columns_and_hides_notes(self) -> None:
        spec = build_view_specs()[0]
        payload = build_view_payload("database-id", "source-id", spec)
        properties = payload["configuration"]["properties"]

        self.assertEqual([item["property"] for item in properties[: len(DASHBOARD_VIEW_PROPERTIES)]], DASHBOARD_VIEW_PROPERTIES)
        self.assertEqual(properties[2]["property"], "Status")
        self.assertEqual(properties[3]["property"], DUE_STATUS_PROPERTY_NAME)
        for property_name in DASHBOARD_HIDDEN_PROPERTIES:
            self.assertFalse(next(item for item in properties if item["property"] == property_name)["visible"])
        self.assertEqual(
            DASHBOARD_HIDDEN_PROPERTIES,
            {"Email Link", "Source", "Frequency", "Month", "Week", "Payment Date", "Notes"},
        )

    def test_todays_priorities_view_filters_sorts_and_hides_properties(self) -> None:
        spec = build_view_specs()[1]
        payload = build_view_payload("database-id", "source-id", spec)
        properties = payload["configuration"]["properties"]

        self.assertEqual(payload["name"], "Today's Priorities")
        self.assertEqual(
            payload["filter"],
            {
                "or": [
                    {"property": "Status", "select": {"equals": "Needs Review"}},
                    {"property": "Due Status", "formula": {"string": {"contains": "Today"}}},
                    {"property": "Due Status", "formula": {"string": {"contains": "Past Due"}}},
                ]
            },
        )
        self.assertEqual(
            payload["sorts"],
            [
                {"property": "Due Date", "direction": "ascending"},
                {"property": "Amount", "direction": "descending"},
            ],
        )
        self.assertEqual([item["property"] for item in properties[: len(TODAYS_PRIORITIES_VIEW_PROPERTIES)]], TODAYS_PRIORITIES_VIEW_PROPERTIES)
        self.assertFalse(next(item for item in properties if item["property"] == "Notes")["visible"])

    def test_month_view_uses_supported_notion_date_filter_conditions(self) -> None:
        spec = build_view_specs()[3]
        payload = build_view_payload("database-id", "source-id", spec)

        self.assertEqual(payload["name"], "This Month")
        self.assertEqual(
            payload["filter"],
            {
                "and": [
                    {"property": "Due Date", "date": {"on_or_after": "one_month_ago"}},
                    {"property": "Due Date", "date": {"on_or_before": "one_month_from_now"}},
                ]
            },
        )

    def test_vendor_rules_payload_contains_requested_properties(self) -> None:
        payload = build_vendor_rules_database_payload("page-id")
        properties = payload["initial_data_source"]["properties"]

        self.assertEqual(payload["title"][0]["text"]["content"], VENDOR_RULE_DATABASE_NAME)
        self.assertEqual(properties["Vendor Name"], {"title": {}})
        self.assertEqual(properties["Match Text"], {"rich_text": {}})
        self.assertEqual(properties["Display Name"], {"rich_text": {}})
        self.assertEqual(properties["Due Day"], {"number": {"format": "number"}})
        self.assertEqual(properties["Pay By Day"], {"number": {"format": "number"}})
        self.assertEqual(properties["Typical Amount"], {"number": {"format": "dollar"}})
        self.assertEqual(properties["AutoPay"], {"checkbox": {}})
        self.assertEqual(properties["Critical"], {"checkbox": {}})
        self.assertEqual(properties["Active"], {"checkbox": {}})
        self.assertEqual(
            [item["name"] for item in properties["Category"]["select"]["options"]],
            [
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
            ],
        )
        self.assertEqual(
            [item["name"] for item in properties["Frequency"]["select"]["options"]],
            ["Weekly", "Biweekly", "Monthly", "Quarterly", "Annual"],
        )


class CashFlowHQConfigTests(unittest.TestCase):
    def test_validate_requires_notion_settings(self) -> None:
        settings = SimpleNamespace(notion_api_key="", notion_parent_page_id="", database_name="Cash Flow HQ")

        errors = validate_cash_flow_settings(settings)

        self.assertIn("NOTION_API_KEY is required. Use the Notion integration or personal access token.", errors)
        self.assertIn(
            "CASH_FLOW_HQ_PARENT_PAGE_ID is required. Use the Notion parent page ID shared with the integration.",
            errors,
        )

    def test_load_settings_uses_cash_flow_defaults(self) -> None:
        with patch.dict(os.environ, {"NOTION_API_KEY": "secret", "CASH_FLOW_HQ_PARENT_PAGE_ID": "page"}, clear=True):
            settings = load_cash_flow_settings("/tmp/no-cash-flow-test.env")

        self.assertEqual(settings.database_name, "Cash Flow HQ")
        self.assertEqual(settings.notion_version, "2026-03-11")
        self.assertEqual(settings.cash_flow_teams_user, "jaye@unitedaccountservices.com")
        self.assertEqual(settings.cash_flow_notification_time, "08:00")

    def test_load_settings_reads_explicit_notion_data_source_ids(self) -> None:
        with patch.dict(
            os.environ,
            {
                "NOTION_API_KEY": "secret",
                "CASH_FLOW_HQ_PARENT_PAGE_ID": "page",
                "CASH_FLOW_HQ_DATA_SOURCE_ID": "cash-source-id",
                "VENDOR_RULES_DATA_SOURCE_ID": "vendor-source-id",
            },
            clear=True,
        ):
            settings = load_cash_flow_settings()

        self.assertEqual(settings.cash_flow_data_source_id, "cash-source-id")
        self.assertEqual(settings.vendor_rules_data_source_id, "vendor-source-id")

    def test_validate_scan_requires_graph_settings(self) -> None:
        settings = SimpleNamespace(
            notion_api_key="secret",
            notion_parent_page_id="page",
            database_name="Cash Flow HQ",
            mailbox_user_id="",
            graph_tenant_id="",
            graph_client_id="",
            graph_client_secret="",
        )

        errors = validate_cash_flow_settings(settings, include_graph=True)

        self.assertIn("CASH_FLOW_HQ_MAILBOX_USER_ID or MAILBOX_USER_ID is required.", errors)
        self.assertIn("MS_GRAPH_TENANT_ID is required.", errors)

    def test_validate_teams_requires_direct_message_settings(self) -> None:
        settings = SimpleNamespace(
            notion_api_key="secret",
            notion_parent_page_id="page",
            database_name="Cash Flow HQ",
            cash_flow_teams_user="",
            teams_graph_tenant_id="",
            teams_graph_client_id="",
            teams_graph_client_secret="",
        )

        errors = validate_cash_flow_settings(settings, include_teams=True)

        self.assertIn("CASH_FLOW_HQ_TEAMS_USER is required.", errors)
        self.assertIn("TEAMS_GRAPH_TENANT_ID or MS_GRAPH_TENANT_ID is required.", errors)


class CashFlowHQServiceTests(unittest.TestCase):
    def test_ensure_foundation_creates_database_and_views_without_rows(self) -> None:
        notion = FakeNotion()
        settings = SimpleNamespace(
            notion_api_key="secret",
            notion_version="2026-03-11",
            notion_parent_page_id="page-id",
            database_name="Cash Flow HQ",
        )
        service = CashFlowHQService(settings, notion=notion)

        result = service.ensure_foundation()

        self.assertTrue(result["database_created"])
        self.assertEqual(len(result["views_created"]), 11)
        self.assertEqual(notion.patched_views, 1)
        self.assertEqual(len(notion.views), 11)
        self.assertEqual(notion.created_pages, 0)
        self.assertEqual(len(notion.created_vendor_rules), len(VENDOR_RULE_SEEDS))
        self.assertIn(DUE_STATUS_PROPERTY_NAME, notion.data_source_properties)
        self.assertIn(ACTION_REQUIRED_PROPERTY_NAME, notion.data_source_properties)
        dashboard_properties = notion.view_patch_payloads[0]["configuration"]["properties"]
        self.assertEqual([item["property"] for item in dashboard_properties[:4]], ["Expense Name", "Amount", "Status", "Due Status"])
        self.assertFalse(next(item for item in dashboard_properties if item["property"] == "Notes")["visible"])

    def test_existing_dashboard_view_is_updated_without_creating_duplicate_views(self) -> None:
        notion = FakeNotion(existing_database=True)
        notion.existing_views = [{"id": "dashboard-view", "name": "Dashboard"}]
        service = CashFlowHQService(build_settings(), notion=notion)

        created = service.ensure_views("database-id", "source-id")

        self.assertNotIn("Dashboard", created)
        self.assertEqual(notion.patched_views, 1)
        self.assertNotIn("Dashboard", [view["name"] for view in notion.views])
        dashboard_properties = notion.view_patch_payloads[0]["configuration"]["properties"]
        self.assertEqual([item["property"] for item in dashboard_properties[:4]], ["Expense Name", "Amount", "Status", "Due Status"])

    def test_patch_action_required_formula_updates_only_formula_property(self) -> None:
        notion = FakeNotion(existing_database=True)
        service = CashFlowHQService(build_settings(), notion=notion)

        expression = service.patch_action_required_formula("source-id", "if(true, \"OK\", \"OK\")")

        self.assertEqual(expression, "if(true, \"OK\", \"OK\")")
        self.assertEqual(notion.patch_payloads[-1]["properties"]["Action Required"]["formula"]["expression"], expression)
        self.assertEqual(notion.created_pages, 0)
        self.assertEqual(notion.views, [])

    def test_patch_action_required_formula_builds_from_current_schema(self) -> None:
        notion = FakeNotion(existing_database=True)
        notion.data_source_properties = {
            "AutoPay": {"type": "checkbox", "checkbox": {}},
            "Grace Period Days": {"type": "number", "number": {"format": "number"}},
        }
        service = CashFlowHQService(build_settings(), notion=notion)

        expression = service.patch_action_required_formula("source-id")

        self.assertIn('prop("AutoPay")', expression)
        self.assertIn('prop("AutoPay") == false', expression)
        self.assertIn("dateBetween", expression)
        self.assertNotIn("ifs(", expression)
        self.assertNotIn("not(", expression)
        self.assertNotIn('prop("Due Date") < now()', expression)
        self.assertNotIn('prop("Due Date") >= now()', expression)
        self.assertNotIn('prop("Grace Period Days")', expression)
        self.assertEqual(notion.patch_payloads[-1]["properties"]["Action Required"]["formula"]["expression"], expression)

    def test_action_required_formula_debug_prints_exact_formula_and_property_types(self) -> None:
        notion = FakeNotion(existing_database=True)
        notion.data_source_properties = {
            "Vendor / Payee": {"type": "rich_text", "rich_text": {}},
            "Status": {"type": "select", "select": {}},
            "Amount": {"type": "number", "number": {"format": "dollar"}},
            "Due Date": {"type": "date", "date": {}},
            "Category": {"type": "select", "select": {}},
            "Payment Type": {"type": "select", "select": {}},
        }
        service = CashFlowHQService(build_settings(), notion=notion)

        diagnostics = service.action_required_formula_debug("source-id")

        self.assertEqual(diagnostics["property_types"]["Vendor / Payee"], "rich_text")
        self.assertEqual(diagnostics["property_types"]["Status"], "select")
        self.assertEqual(diagnostics["property_types"]["Amount"], "number")
        self.assertEqual(diagnostics["property_types"]["Due Date"], "date")
        self.assertEqual(diagnostics["property_types"]["Category"], "select")
        self.assertEqual(diagnostics["property_types"]["Payment Type"], "select")
        self.assertIn("Due Date", diagnostics["safety_report"]["dateBetween()"])
        self.assertIn('"Upcoming AutoPay"', diagnostics["full_formula"])
        self.assertIn("dateBetween", diagnostics["full_formula"])
        self.assertNotIn("ifs(", diagnostics["full_formula"])
        self.assertNotIn("not(", diagnostics["full_formula"])
        self.assertIn('empty(prop("Vendor / Payee"))', diagnostics["full_formula"])
        self.assertIn('empty(prop("Category"))', diagnostics["full_formula"])
        self.assertNotIn('or(empty(prop("Vendor / Payee"))', diagnostics["full_formula"])
        self.assertNotIn('prop("Due Date") < now()', diagnostics["full_formula"])
        self.assertNotIn('prop("Due Date") >= now()', diagnostics["full_formula"])
        self.assertEqual(diagnostics["full_formula"], build_action_required_formula(notion.data_source_properties))

    def test_patch_action_required_formula_falls_back_when_full_formula_is_rejected(self) -> None:
        notion = FakeNotion(existing_database=True)
        notion.reject_action_required_once = True
        service = CashFlowHQService(build_settings(), notion=notion)

        expression = service.patch_action_required_formula("source-id", "rejected formula")

        self.assertEqual(expression, ACTION_REQUIRED_FALLBACK_FORMULA)
        self.assertEqual(notion.patch_payloads[-1]["properties"]["Action Required"]["formula"]["expression"], ACTION_REQUIRED_FALLBACK_FORMULA)

    def test_diagnose_action_required_formula_records_all_steps_after_failure(self) -> None:
        notion = FakeNotion(existing_database=True)
        diagnostic_formulas = build_action_required_diagnostic_formulas()
        notion.reject_action_required_expression = diagnostic_formulas[3][1]
        service = CashFlowHQService(build_settings(), notion=notion)

        results = service.diagnose_action_required_formula_patches("source-id")

        self.assertEqual(
            [result["status"] for result in results],
            ["PASS", "PASS", "PASS", "FAIL"],
        )
        self.assertEqual(results[3]["formula"], diagnostic_formulas[3][1])
        self.assertIn("Notion PATCH rejected formula", results[3]["response"])
        self.assertEqual(results[3]["patch_body"]["properties"][ACTION_REQUIRED_PROPERTY_NAME]["formula"]["expression"], diagnostic_formulas[3][1])
        self.assertEqual(len(notion.patch_payloads), len(diagnostic_formulas))
        self.assertEqual(notion.created_pages, 0)
        self.assertEqual(notion.views, [])

    def test_diagnostic_formulas_isolate_date_between(self) -> None:
        formulas = build_action_required_diagnostic_formulas()
        self.assertEqual(
            [formula for _, formula in formulas],
            [
                'if(empty(prop("Vendor / Payee")), "Needs Review", if(empty(prop("Amount")), "Needs Review", if(empty(prop("Due Date")), "Needs Review", if(empty(prop("Category")), "Needs Review", "OK"))))',
                'if(empty(prop("Vendor / Payee")), "Needs Review", if(empty(prop("Amount")), "Needs Review", if(empty(prop("Due Date")), "Needs Review", if(empty(prop("Category")), "Needs Review", if(and(format(prop("Status")) != "Paid", dateBetween(now(), prop("Due Date"), "days") > 0), "Past Due", "OK")))))',
                'if(empty(prop("Vendor / Payee")), "Needs Review", if(empty(prop("Amount")), "Needs Review", if(empty(prop("Due Date")), "Needs Review", if(empty(prop("Category")), "Needs Review", if(and(format(prop("Status")) != "Paid", dateBetween(now(), prop("Due Date"), "days") > 0), "Past Due", if(and(format(prop("Status")) != "Paid", format(prop("Payment Type")) != "Auto Pay"), "Pay Now", "OK"))))))',
                build_action_required_formula(),
            ],
        )
        self.assertTrue(all("ifs(" not in formula for _, formula in formulas))

    def test_diagnose_action_required_formula_patches_all_steps_when_accepted(self) -> None:
        notion = FakeNotion(existing_database=True)
        service = CashFlowHQService(build_settings(), notion=notion)

        results = service.diagnose_action_required_formula_patches("source-id")
        diagnostic_formulas = build_action_required_diagnostic_formulas()

        self.assertTrue(all(result["status"] == "PASS" for result in results))
        self.assertEqual(len(results), len(diagnostic_formulas))
        self.assertEqual(
            notion.patch_payloads[-1]["properties"][ACTION_REQUIRED_PROPERTY_NAME]["formula"]["expression"],
            diagnostic_formulas[-1][1],
        )

    def test_manual_expense_payload_is_reusable_for_later_phases(self) -> None:
        settings = SimpleNamespace(
            notion_api_key="secret",
            notion_version="2026-03-11",
            notion_parent_page_id="page-id",
            database_name="Cash Flow HQ",
        )
        service = CashFlowHQService(settings, notion=FakeNotion())

        payload = service.create_manual_expense_payload("Rent", 1000.0, "2026-07-06")

        self.assertEqual(payload["Expense Name"]["title"][0]["text"]["content"], "Rent")
        self.assertEqual(payload["Status"]["select"]["name"], "Upcoming")
        self.assertEqual(payload["Source"]["select"]["name"], "Manual")

    def test_database_search_uses_data_source_filter_for_current_notion_api(self) -> None:
        notion = FakeNotion(existing_database=True)
        service = CashFlowHQService(build_settings(), notion=notion)

        foundation = service.find_foundation_by_name("Cash Flow HQ")

        self.assertEqual(foundation, {"database_id": "database-id", "data_source_id": "source-id"})
        self.assertEqual(notion.search_payload["filter"], {"property": "object", "value": "data_source"})

    def test_explicit_data_source_ids_are_preferred_over_title_search(self) -> None:
        notion = FakeNotion(existing_database=True, existing_vendor_rules=True)
        service = CashFlowHQService(
            build_settings(
                cash_flow_data_source_id="cash-source-id",
                vendor_rules_data_source_id="rules-source-id",
            ),
            notion=notion,
        )

        self.assertEqual(
            service.get_existing_foundation(),
            {"database_id": "cash-database-id", "data_source_id": "cash-source-id"},
        )
        self.assertEqual(
            service.get_existing_vendor_rules_foundation(),
            {"database_id": "rules-database-id", "data_source_id": "rules-source-id"},
        )
        self.assertIsNone(notion.search_payload)

    def test_create_bill_properties_marks_missing_details_needs_review(self) -> None:
        service = CashFlowHQService(build_settings(), notion=FakeNotion())
        candidate = parse_bill_candidate(
            build_email(
                subject="Invoice available",
                body="Your subscription invoice is ready for review.",
            )
        )

        properties = service.create_bill_properties(candidate)

        self.assertEqual(properties["Status"]["select"]["name"], "Needs Review")
        self.assertEqual(properties["Source"]["select"]["name"], "Email")
        self.assertNotIn("Amount", properties)
        self.assertNotIn("Due Date", properties)

    def test_duplicate_detects_same_vendor_amount_and_due_date(self) -> None:
        notion = FakeNotion(existing_database=True)
        notion.query_results = [
            {
                "properties": {
                    "Vendor / Payee": {"rich_text": [{"plain_text": "Acme Software"}]},
                }
            }
        ]
        service = CashFlowHQService(build_settings(), notion=notion)
        candidate = parse_bill_candidate(
            build_email(
                sender_name="Acme Software",
                subject="Invoice due",
                body="Amount due $42.50. Due date July 20, 2026.",
            )
        )

        self.assertTrue(service.email_bill_exists("source-id", candidate))

    def test_vendor_rule_exact_match_fills_missing_fields_and_due_date(self) -> None:
        service = CashFlowHQService(build_settings(), notion=FakeNotion())
        email = build_email(
            sender_name="D1AL",
            sender_email="receipts@d1al.com",
            subject="Your D1AL receipt",
            body="Amount Due: $315.37.",
        )
        candidate = parse_bill_candidate(email)
        rule = VendorRule("D1AL", "D1AL", "Telecommunications / Dialer", "Monthly", 1, "Auto Pay", "Upcoming", True)

        updated = service.apply_vendor_rules(candidate, email, [rule])

        self.assertEqual(updated.category, "Telecommunications / Dialer")
        self.assertEqual(updated.frequency, "Monthly")
        self.assertEqual(updated.payment_type, "Auto Pay")
        self.assertEqual(updated.due_date.isoformat(), "2026-07-01")
        self.assertEqual(updated.status, "Upcoming")
        self.assertEqual(updated.review_reasons, ())
        self.assertEqual(updated.field_sources["due_date"], "vendor rules")
        self.assertNotIn("Missing due date", updated.notes)
        self.assertEqual(updated.notes, "✓ Ready for Payment")
        self.assertIn("Category", service.create_bill_properties(updated))
        self.assertIn("Frequency", service.create_bill_properties(updated))

    def test_vendor_rule_partial_match_uses_match_text(self) -> None:
        service = CashFlowHQService(build_settings(), notion=FakeNotion())
        email = build_email(
            sender_name="Notifications",
            sender_email="yardinotifications@popeandland.com",
            subject="Payment Notification",
            body="Pope and Land statement. Amount Due: $3,958.07.",
        )
        candidate = parse_bill_candidate(email)
        rule = VendorRule("Pope and Land", "Pope and Land", "Rent", "Monthly", 1, "Manual", "Upcoming", True, pay_by_day=5)

        updated = service.apply_vendor_rules(candidate, email, [rule])

        self.assertEqual(updated.category, "Rent")
        self.assertEqual(updated.due_date.isoformat(), "2026-07-05")
        self.assertEqual(updated.status, "Upcoming")
        self.assertEqual(updated.frequency, "Monthly")
        self.assertNotIn("Missing due date", updated.notes)
        self.assertEqual(updated.notes, "✓ Ready for Payment")

    def test_vendor_rule_display_name_replaces_sender_email(self) -> None:
        service = CashFlowHQService(build_settings(), notion=FakeNotion())
        cases = [
            (
                build_email(
                    sender_name="yardinotifications@popeandland.com",
                    sender_email="yardinotifications@popeandland.com",
                    subject="Payment Notification",
                    body="Pope and Land statement. Amount Due: $126.95.",
                ),
                VendorRule(
                    "Pope and Land",
                    "Pope and Land",
                    "Rent",
                    "Monthly",
                    1,
                    "Manual",
                    "Upcoming",
                    True,
                    display_name="Pope & Land",
                ),
                "Pope & Land",
            ),
            (
                build_email(
                    sender_name="receipts@d1al.com",
                    sender_email="receipts@d1al.com",
                    subject="Your D1AL receipt",
                    body="Amount Due: $315.37.",
                ),
                VendorRule(
                    "D1AL",
                    "D1AL",
                    "Software",
                    "Monthly",
                    5,
                    "Manual",
                    "Upcoming",
                    True,
                    display_name="D1AL",
                ),
                "D1AL",
            ),
            (
                build_email(
                    sender_name="maria@smaxcollectionsoftware.com",
                    sender_email="maria@smaxcollectionsoftware.com",
                    subject="SCollect Monthly Invoice",
                    body="SCollect invoice. Due Date: July 15, 2026.",
                ),
                VendorRule(
                    "SCollect",
                    "SCollect",
                    "Software",
                    "Monthly",
                    None,
                    "Manual",
                    "Upcoming",
                    True,
                    display_name="SCollect",
                ),
                "SCollect",
            ),
        ]

        for email, rule, expected in cases:
            with self.subTest(expected=expected):
                candidate = parse_bill_candidate(email)
                updated = service.apply_vendor_rules(candidate, email, [rule])

                self.assertEqual(updated.vendor_payee, expected)
                self.assertNotIn("@", updated.vendor_payee)

    def test_vendor_rule_due_date_prefers_invoice_month(self) -> None:
        service = CashFlowHQService(build_settings(), notion=FakeNotion())
        email = build_email(
            sender_name="D1AL",
            subject="D1AL invoice 06/2026",
            body="Amount Due: $315.37.",
        )
        candidate = parse_bill_candidate(email)
        rule = VendorRule("D1AL", "D1AL", "Software", "Monthly", 5, "Manual", "Upcoming", True)

        updated = service.apply_vendor_rules(candidate, email, [rule])

        self.assertEqual(updated.due_date.isoformat(), "2026-06-05")

    def test_scollect_expected_amount_calculates_from_users_and_server_fee(self) -> None:
        service = CashFlowHQService(build_settings(), notion=FakeNotion())
        email = build_email(
            sender_name="SCollect",
            subject="SCollect Monthly Invoice",
            body="Amount Due: $600.00.",
        )
        candidate = parse_bill_candidate(email)
        rule = VendorRule(
            "SCollect",
            "SCollect",
            "Software",
            "Monthly",
            5,
            "Manual",
            "Upcoming",
            True,
            rate_per_user=Decimal("50.00"),
            current_user_count=10,
            monthly_server_fee=Decimal("100.00"),
        )

        updated = service.apply_vendor_rules(candidate, email, [rule])

        self.assertEqual(updated.status, "Upcoming")
        self.assertEqual(updated.notes, "✓ Ready for Payment")

    def test_scollect_seed_matches_confirmed_billing_rule(self) -> None:
        seed = next(item for item in VENDOR_RULE_SEEDS if item["vendor_name"] == "SCollect")

        self.assertEqual(seed["invoice_day"], 1)
        self.assertEqual(seed["due_day"], 5)
        self.assertEqual(seed["payment_type"], "Auto Pay")
        self.assertTrue(seed["auto_pay"])
        self.assertEqual(seed["rate_per_user"], 50.00)
        self.assertEqual(seed["current_user_count"], 10)
        self.assertEqual(seed["monthly_server_fee"], 100.00)
        self.assertEqual(seed["typical_amount"], 600.00)

    def test_expected_amount_variance_flags_note_without_blocking_import(self) -> None:
        service = CashFlowHQService(build_settings(), notion=FakeNotion())
        email = build_email(sender_name="D1AL", subject="D1AL invoice", body="Amount Due: $330.00.")
        candidate = parse_bill_candidate(email)
        rule = VendorRule(
            "D1AL",
            "D1AL",
            "Telecommunications / Dialer",
            "Monthly",
            1,
            "Auto Pay",
            "Upcoming",
            True,
            typical_amount=Decimal("315.37"),
        )

        updated = service.apply_vendor_rules(candidate, email, [rule])

        self.assertEqual(updated.status, "Needs Review")
        self.assertIn("Higher than expected", updated.notes)

    def test_vendor_rule_does_not_overwrite_extracted_values(self) -> None:
        service = CashFlowHQService(build_settings(), notion=FakeNotion())
        email = build_email(
            sender_name="D1AL",
            subject="D1AL internet invoice",
            body="Amount Due: $315.37. Due Date: July 20, 2026. Automatic payment is enabled.",
        )
        candidate = parse_bill_candidate(email)
        rule = VendorRule("D1AL", "D1AL", "Software", "Monthly", 5, "Manual", "Upcoming", True)

        updated = service.apply_vendor_rules(candidate, email, [rule])

        self.assertEqual(updated.category, "Utilities")
        self.assertEqual(updated.payment_type, "Auto Pay")
        self.assertEqual(updated.due_date.isoformat(), "2026-07-20")
        self.assertEqual(updated.frequency, "Monthly")

    def test_missing_due_date_example_populates_notes_and_needs_review(self) -> None:
        service = CashFlowHQService(build_settings(), notion=FakeNotion())
        candidate = parse_bill_candidate(
            build_email(
                sender_name="Unknown Vendor",
                subject="Invoice available",
                body="Amount Due: $100.00.",
            )
        )

        properties = service.create_bill_properties(candidate)

        self.assertEqual(properties["Vendor / Payee"]["rich_text"][0]["text"]["content"], "Unknown Vendor")
        self.assertEqual(properties["Status"]["select"]["name"], "Needs Review")
        self.assertEqual(properties["Source"]["select"]["name"], "Email")
        self.assertIn("Missing due date", properties["Notes"]["rich_text"][0]["text"]["content"])
        self.assertNotIn("Due Date", properties)
        self.assertEqual(
            properties["Notes"]["rich_text"][0]["text"]["content"],
            "Needs Review\n\n• Missing due date",
        )

    def test_vendor_rule_seed_creation_adds_initial_rules_once(self) -> None:
        notion = FakeNotion(existing_database=True, existing_vendor_rules=True)
        service = CashFlowHQService(build_settings(), notion=notion)

        service.ensure_vendor_rule_seeds("vendor-source-id")
        service.ensure_vendor_rule_seeds("vendor-source-id")

        self.assertEqual(
            [page["properties"]["Vendor Name"]["title"][0]["text"]["content"] for page in notion.created_vendor_rules],
            [seed["vendor_name"] for seed in VENDOR_RULE_SEEDS],
        )


class CashFlowHQParserTests(unittest.TestCase):
    def test_parses_likely_bill_without_guessing(self) -> None:
        email = build_email(
            sender_name="Acme Software",
            subject="Your subscription invoice",
            body="Invoice #INV-2026-77. Amount due $42.50. Due date July 20, 2026. Automatic payment is enabled.",
        )

        candidate = parse_bill_candidate(email)

        self.assertTrue(is_bill_related(email))
        self.assertEqual(candidate.vendor_payee, "Acme Software")
        self.assertEqual(str(candidate.amount), "42.50")
        self.assertEqual(candidate.due_date.isoformat(), "2026-07-20")
        self.assertEqual(candidate.payment_type, "Auto Pay")
        self.assertEqual(candidate.category, "Software")
        self.assertEqual(candidate.invoice_number, "INV-2026-77")
        self.assertEqual(candidate.status, "Upcoming")
        self.assertEqual(candidate.review_reasons, ())

    def test_missing_amount_or_due_date_is_needs_review(self) -> None:
        email = build_email(subject="Payment reminder", body="Your bill is ready.")

        candidate = parse_bill_candidate(email)

        self.assertIsNone(candidate.amount)
        self.assertIsNone(candidate.due_date)
        self.assertEqual(candidate.status, "Needs Review")
        self.assertEqual(candidate.review_reasons, ("missing amount", "missing due date"))
        self.assertIn("Missing amount", candidate.notes)
        self.assertIn("Missing due date", candidate.notes)
        self.assertNotIn("Confidence", candidate.notes)
        self.assertNotIn("Subject:", candidate.notes)

    def test_due_date_extraction_supports_common_phase_two_point_five_phrases(self) -> None:
        examples = {
            "Payment Due: July 10, 2026": "2026-07-10",
            "Due by: Jul 10, 2026": "2026-07-10",
            "Amount due by: 07/10/2026": "2026-07-10",
            "Pay by: 7/10/26": "2026-07-10",
            "Autopay scheduled for: 2026-07-10": "2026-07-10",
            "Scheduled payment date: 07/10/2026": "2026-07-10",
            "Invoice due: July 10, 2026": "2026-07-10",
            "Balance due: July 10, 2026": "2026-07-10",
            "Please pay by: July 10, 2026": "2026-07-10",
            "Renewal date: July 10, 2026": "2026-07-10",
        }

        for text, expected in examples.items():
            with self.subTest(text=text):
                self.assertEqual(extract_due_date(text).isoformat(), expected)

    def test_amount_extraction_prefers_labeled_bill_amounts(self) -> None:
        text = "Invoice number 555100. Previous balance $18.00. Amount Due: $142.35."

        self.assertEqual(str(extract_amount(text)), "142.35")

    def test_multiple_conflicting_amounts_needs_review(self) -> None:
        email = build_email(
            subject="Statement",
            body="Amount Due: $142.35. Total Due: $144.35. Due Date: July 10, 2026.",
        )

        candidate = parse_bill_candidate(email)

        self.assertIsNone(candidate.amount)
        self.assertEqual(candidate.status, "Needs Review")
        self.assertIn("multiple possible amounts", candidate.review_reasons)

    def test_multiple_conflicting_due_dates_needs_review(self) -> None:
        email = build_email(
            subject="Statement",
            body="Amount Due: $142.35. Due Date: July 10, 2026. Pay by: July 12, 2026.",
        )

        candidate = parse_bill_candidate(email)

        self.assertIsNone(candidate.due_date)
        self.assertEqual(candidate.status, "Needs Review")
        self.assertIn("multiple possible due dates", candidate.review_reasons)

    def test_parser_leaves_recurring_vendor_classification_for_vendor_rules(self) -> None:
        candidate = parse_bill_candidate(
            build_email(
                sender_name="Billing",
                sender_email="billing@d1al.com",
                subject="D1AL invoice",
                body="Amount Due: $100.00. Due Date: July 10, 2026.",
            )
        )

        self.assertEqual(candidate.vendor_payee, "Billing")
        self.assertIsNone(candidate.category)
        self.assertIsNone(candidate.frequency)

    def test_html_body_is_converted_to_clean_text_for_due_date_extraction(self) -> None:
        email = message_from_graph(
            {
                "id": "message-id",
                "internetMessageId": "<message-id@example.com>",
                "subject": "Invoice",
                "receivedDateTime": "2026-07-06T12:00:00Z",
                "from": {"emailAddress": {"name": "Vendor Billing", "address": "billing@example.com"}},
                "body": {
                    "contentType": "html",
                    "content": "<html><body><p>Amount Due: $88.10</p><div>Due Date: 2026-07-10</div></body></html>",
                },
                "webLink": "https://outlook.office.com/mail/id/message-id",
            }
        )

        candidate = parse_bill_candidate(email)

        self.assertEqual(str(candidate.amount), "88.10")
        self.assertEqual(candidate.due_date.isoformat(), "2026-07-10")
        self.assertEqual(candidate.status, "Upcoming")

    def test_pdf_attachment_not_parseable_is_logged_when_key_fields_are_missing(self) -> None:
        email = build_email(
            subject="Invoice attached",
            body="Please see attached invoice.",
            attachments=(AttachmentMetadata(name="invoice-2026-07.pdf", content_type="application/pdf"),),
        )

        candidate = parse_bill_candidate(email)

        self.assertEqual(candidate.status, "Needs Review")
        self.assertIn("PDF not parseable", candidate.review_reasons)
        self.assertNotIn("invoice-2026-07.pdf application/pdf", candidate.notes)

    def test_text_pdf_fills_missing_email_amount_due_date_and_invoice_number(self) -> None:
        email = build_email(
            subject="Invoice attached",
            body="Please see attached invoice.",
            attachments=(
                AttachmentMetadata(
                    name="invoice-2026-07.pdf",
                    content_type="application/pdf",
                    content_bytes=sample_invoice_pdf_bytes(),
                ),
            ),
        )

        candidate = parse_bill_candidate(email)

        self.assertEqual(candidate.status, "Upcoming")
        self.assertEqual(str(candidate.amount), "123.45")
        self.assertEqual(candidate.due_date.isoformat(), "2026-07-10")
        self.assertEqual(candidate.invoice_number, "INV-12345")
        self.assertEqual(candidate.vendor_payee, "Sample Vendor LLC")
        self.assertEqual(candidate.field_sources["amount"], "pdf")
        self.assertEqual(candidate.field_sources["due_date"], "pdf")

    def test_pdf_does_not_overwrite_valid_email_value_with_empty_value(self) -> None:
        email = build_email(
            subject="Invoice",
            body="Amount Due: $88.10. Due Date: 2026-07-10.",
            attachments=(
                AttachmentMetadata(
                    name="invoice-empty.pdf",
                    content_type="application/pdf",
                    content_bytes=sample_invoice_pdf_bytes("Invoice Number INV-99999"),
                ),
            ),
        )

        candidate = parse_bill_candidate(email)

        self.assertEqual(candidate.status, "Upcoming")
        self.assertEqual(str(candidate.amount), "88.10")
        self.assertEqual(candidate.due_date.isoformat(), "2026-07-10")
        self.assertEqual(candidate.field_sources["amount"], "subject/body/attachment metadata")
        self.assertEqual(candidate.field_sources["due_date"], "subject/body/attachment metadata")


class CashFlowHQGraphClientTests(unittest.TestCase):
    def test_bill_candidates_fetch_full_body_and_attachment_metadata(self) -> None:
        settings = SimpleNamespace(
            mailbox_user_id="billing@example.com",
            graph_tenant_id="tenant",
            graph_client_id="client",
            graph_client_secret="secret",
        )
        graph = FakeCashFlowGraph(settings)

        messages = graph.find_bill_messages(days=7, limit=50)

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].body_source, "body")
        self.assertIn("Due Date: July 20, 2026", messages[0].body_text)
        self.assertEqual(messages[0].attachments[0].name, "invoice.pdf")
        self.assertIsNotNone(messages[0].attachments[0].content_bytes)
        self.assertIn("GET_DETAIL", graph.calls)
        self.assertIn("GET_ATTACHMENTS", graph.calls)
        self.assertIn("GET_ATTACHMENT_CONTENT", graph.calls)

    def test_direct_chat_member_payload_binds_to_user(self) -> None:
        payload = chat_member_bind("jaye@unitedaccountservices.com")

        self.assertEqual(payload["@odata.type"], "#microsoft.graph.aadUserConversationMember")
        self.assertEqual(payload["roles"], ["owner"])
        self.assertEqual(
            payload["user@odata.bind"],
            "https://graph.microsoft.com/v1.0/users('jaye@unitedaccountservices.com')",
        )


class CashFlowHQTeamsAlertTests(unittest.TestCase):
    def test_morning_brief_contains_counts_totals_and_sorted_priorities(self) -> None:
        bills = [
            CashFlowBill("Due Today LLC", 100.0, date(2026, 7, 8), "🟡 Due Today", "Upcoming"),
            CashFlowBill("Due Tomorrow LLC", 200.0, date(2026, 7, 9), "🟡 Due Tomorrow", "Upcoming"),
            CashFlowBill("Past Due LLC", 50.0, date(2026, 7, 6), "🔴 Past Due by 2 Days", "Upcoming"),
            CashFlowBill("Review LLC", None, date(2026, 7, 10), "🟢 Due in 2 Days", "Needs Review", "⚠ Needs Review\n\n• Missing amount"),
            CashFlowBill("Paid LLC", 75.0, date(2026, 7, 8), "🟡 Due Today", "Paid"),
        ]

        alert = build_morning_brief(bills, date(2026, 7, 8))

        self.assertIn("💰 Cash Flow HQ Morning Brief", alert["text"])
        self.assertIn("Bills Due Today: 1 ($100.00)", alert["text"])
        self.assertIn("Bills Due Tomorrow: 1 ($200.00)", alert["text"])
        self.assertIn("Bills Due This Week: 3 ($300.00)", alert["text"])
        self.assertIn("Needs Review: 1", alert["text"])
        self.assertIn("Past Due: 1", alert["text"])
        self.assertLess(alert["text"].index("Past Due LLC"), alert["text"].index("Review LLC"))
        self.assertIn("Missing amount", alert["text"])

    def test_morning_brief_no_action_when_no_priorities(self) -> None:
        alert = build_morning_brief([], date(2026, 7, 8))

        self.assertIn("✅ No action needed today.", alert["text"])

    def test_alert_sends_direct_message_to_configured_user_only(self) -> None:
        notion = FakeNotion(existing_database=True)
        notion.query_results = [
            cash_flow_page(
                vendor="D1AL",
                amount=315.37,
                due_date="2026-07-09",
                due_status="🟡 Due Tomorrow",
                status="Upcoming",
            )
        ]
        graph = FakeTeamsGraph()
        state_path = Path("work/test-cash-flow-direct-send-state.json")
        if state_path.exists():
            state_path.unlink()
        service = CashFlowHQService(build_settings(), notion=notion)

        CashFlowTeamsAlerts(
            build_settings(cash_flow_notification_state_path=state_path),
            service,
            graph,
        ).send_morning_brief(today=date(2026, 7, 8))

        self.assertEqual(len(graph.direct_messages), 1)
        self.assertEqual(graph.direct_messages[0][0], "jaye@unitedaccountservices.com")
        self.assertIn("D1AL", graph.direct_messages[0][1])

    def test_alert_uses_configured_private_chat_id_before_creating_direct_chat(self) -> None:
        notion = FakeNotion(existing_database=True)
        notion.query_results = [
            cash_flow_page(
                vendor="D1AL",
                amount=315.37,
                due_date="2026-07-09",
                due_status="🟡 Due Tomorrow",
                status="Upcoming",
            )
        ]
        graph = FakeTeamsGraph()
        service = CashFlowHQService(build_settings(), notion=notion)

        CashFlowTeamsAlerts(
            build_settings(cash_flow_teams_chat_id="private-chat-id"),
            service,
            graph,
        ).send_morning_brief(today=date(2026, 7, 8), force=True, record_sent=False)

        self.assertEqual(len(graph.chat_messages), 1)
        self.assertEqual(graph.chat_messages[0][0], "private-chat-id")
        self.assertEqual(graph.direct_messages, [])

    def test_direct_graph_self_dm_has_clear_error(self) -> None:
        graph = FakeSelfDirectGraph()

        with self.assertRaisesRegex(RuntimeError, "cannot create a one-on-one chat from a user to themselves"):
            graph.post_direct_chat_message("jaye@unitedaccountservices.com", "<p>Brief</p>")

    def test_morning_brief_skips_duplicate_daily_summary(self) -> None:
        state_path = Path("work/test-cash-flow-notification-state.json")
        if state_path.exists():
            state_path.unlink()
        mark_sent(state_path, date(2026, 7, 8))

        self.assertTrue(already_sent(state_path, date(2026, 7, 8)))
        self.assertFalse(already_sent(state_path, date(2026, 7, 9)))

    def test_manual_test_notification_ignores_duplicate_state_without_updating_it(self) -> None:
        notion = FakeNotion(existing_database=True)
        notion.query_results = [
            cash_flow_page(
                vendor="D1AL",
                amount=315.37,
                due_date="2026-07-09",
                due_status="🟡 Due Tomorrow",
                status="Upcoming",
            )
        ]
        state_path = Path("work/test-cash-flow-manual-state.json")
        if state_path.exists():
            state_path.unlink()
        mark_sent(state_path, date(2026, 7, 8))
        graph = FakeTeamsGraph()
        service = CashFlowHQService(build_settings(), notion=notion)

        CashFlowTeamsAlerts(
            build_settings(cash_flow_notification_state_path=state_path),
            service,
            graph,
        ).send_morning_brief(today=date(2026, 7, 9), force=True, record_sent=False)

        self.assertEqual(len(graph.direct_messages), 1)
        self.assertTrue(already_sent(state_path, date(2026, 7, 8)))
        self.assertFalse(already_sent(state_path, date(2026, 7, 9)))


class CashFlowHQEmailScannerTests(unittest.TestCase):
    def test_dry_run_does_not_create_notion_pages(self) -> None:
        notion = FakeNotion(existing_database=True)
        service = CashFlowHQService(build_settings(), notion=notion)
        scanner = CashFlowEmailScanner(service, FakeGraph())

        result = scanner.scan(days=7, limit=50, dry_run=True)

        self.assertEqual(len(result.would_import), 1)
        self.assertEqual(len(result.flagged), 0)
        self.assertEqual(len(result.imported), 0)
        self.assertEqual(notion.created_pages, 0)

    def test_live_scan_creates_email_bill(self) -> None:
        notion = FakeNotion()
        service = CashFlowHQService(build_settings(), notion=notion)
        scanner = CashFlowEmailScanner(service, FakeGraph())

        result = scanner.scan(days=7, limit=50, dry_run=False)

        self.assertEqual(len(result.imported), 1)
        self.assertEqual(notion.created_pages, 1)
        self.assertEqual(notion.vendor_rules_search_count, 1)


class FakeNotion:
    def __init__(self, existing_database: bool = False, existing_vendor_rules: bool = False) -> None:
        self.existing_database = existing_database
        self.existing_vendor_rules = existing_vendor_rules
        self.created_pages = 0
        self.created_vendor_rules: list[dict] = []
        self.patched_views = 0
        self.views: list[dict] = []
        self.existing_views: list[dict] = []
        self.view_patch_payloads: list[dict] = []
        self.query_results: list[dict] = []
        self.vendor_rule_pages: list[dict] = []
        self.data_source_properties: dict = {}
        self.vendor_rule_properties: dict = {}
        self.updated_vendor_rules: list[dict] = []
        self.patch_payloads: list[dict] = []
        self.reject_action_required_once = False
        self.reject_action_required_expression: str | None = None
        self.search_payload: dict | None = None
        self.vendor_rules_search_count = 0

    def request(self, method: str, path: str, **kwargs):
        if method == "POST" and path == "/search":
            self.search_payload = kwargs["json"]
            query = kwargs["json"]["query"]
            if query == "Cash Flow HQ" and self.existing_database:
                return {
                    "results": [
                        {
                            "id": "source-id",
                            "title": [{"plain_text": "Cash Flow HQ"}],
                            "parent": {"type": "database_id", "database_id": "database-id"},
                        }
                    ]
                }
            if query == VENDOR_RULE_DATABASE_NAME and self.existing_vendor_rules:
                self.vendor_rules_search_count += 1
                return {
                    "results": [
                        {
                            "id": "vendor-source-id",
                            "title": [{"plain_text": VENDOR_RULE_DATABASE_NAME}],
                            "parent": {"type": "database_id", "database_id": "vendor-database-id"},
                        }
                    ]
                }
            if query == VENDOR_RULE_DATABASE_NAME:
                self.vendor_rules_search_count += 1
            return {"results": []}
        if method == "POST" and path == "/data_sources/source-id/query":
            return {"results": self.query_results}
        if method == "POST" and path == "/data_sources/vendor-source-id/query":
            return {"results": self.vendor_rule_pages}
        if method == "POST" and path == "/databases":
            title = kwargs["json"]["title"][0]["text"]["content"]
            if title == VENDOR_RULE_DATABASE_NAME:
                self.existing_vendor_rules = True
                self.vendor_rule_properties = kwargs["json"]["initial_data_source"]["properties"]
                return {"id": "vendor-database-id", "data_sources": [{"id": "vendor-source-id"}]}
            self.existing_database = True
            self.data_source_properties = kwargs["json"]["initial_data_source"]["properties"]
            return {"id": "database-id", "data_sources": [{"id": "source-id"}]}
        if method == "GET" and path == "/data_sources/source-id":
            return {"id": "source-id", "properties": self.data_source_properties}
        if method == "GET" and path == "/data_sources/vendor-source-id":
            return {
                "id": "vendor-source-id",
                "parent": {"type": "database_id", "database_id": "vendor-database-id"},
                "properties": self.vendor_rule_properties,
            }
        if method == "GET" and path == "/data_sources/cash-source-id":
            return {
                "id": "cash-source-id",
                "parent": {"type": "database_id", "database_id": "cash-database-id"},
                "properties": self.data_source_properties,
            }
        if method == "GET" and path == "/data_sources/rules-source-id":
            return {
                "id": "rules-source-id",
                "parent": {"type": "database_id", "database_id": "rules-database-id"},
                "properties": {},
            }
        if method == "PATCH" and path == "/data_sources/source-id":
            self.patch_payloads.append(kwargs["json"])
            if self.reject_action_required_once and ACTION_REQUIRED_PROPERTY_NAME in kwargs["json"].get("properties", {}):
                self.reject_action_required_once = False
                raise RuntimeError("Notion PATCH rejected formula: Type error with formula")
            expression = (
                kwargs["json"]
                .get("properties", {})
                .get(ACTION_REQUIRED_PROPERTY_NAME, {})
                .get("formula", {})
                .get("expression")
            )
            if expression and expression == self.reject_action_required_expression:
                raise RuntimeError("Notion PATCH rejected formula: Type error with formula")
            self.data_source_properties.update(kwargs["json"]["properties"])
            return {"id": "source-id", "properties": self.data_source_properties}
        if method == "PATCH" and path == "/data_sources/vendor-source-id":
            self.vendor_rule_properties.update(kwargs["json"]["properties"])
            return {"id": "vendor-source-id", "properties": self.vendor_rule_properties}
        if method == "GET" and path == "/databases/database-id":
            return {"id": "database-id", "data_sources": [{"id": "source-id"}]}
        if method == "GET" and path == "/databases/vendor-database-id":
            return {"id": "vendor-database-id", "data_sources": [{"id": "vendor-source-id"}]}
        if method == "GET" and path == "/views?database_id=database-id":
            if self.existing_views:
                return {"results": [{"id": view["id"]} for view in self.existing_views]}
            return {"results": [{"id": "default-view"}]}
        if method == "GET" and path == "/views?database_id=vendor-database-id":
            return {"results": []}
        if method == "GET" and path == "/views/default-view":
            return {"id": "default-view", "name": "Default view"}
        if method == "GET" and path == "/views/dashboard-view":
            return {"id": "dashboard-view", "name": "Dashboard"}
        if method == "PATCH" and path == "/views/default-view":
            self.patched_views += 1
            self.view_patch_payloads.append(kwargs["json"])
            return {"id": "default-view", "name": kwargs["json"]["name"]}
        if method == "PATCH" and path == "/views/dashboard-view":
            self.patched_views += 1
            self.view_patch_payloads.append(kwargs["json"])
            return {"id": "dashboard-view", "name": kwargs["json"]["name"]}
        if method == "POST" and path == "/views":
            self.views.append(kwargs["json"])
            return {"id": f"view-{len(self.views)}", "name": kwargs["json"]["name"]}
        if method == "POST" and path == "/pages":
            parent = kwargs["json"].get("parent", {})
            if parent.get("data_source_id") == "vendor-source-id":
                self.created_vendor_rules.append(kwargs["json"])
                self.vendor_rule_pages.append(vendor_rule_page_from_properties(kwargs["json"]["properties"]))
            else:
                self.created_pages += 1
            return {"id": "page-id"}
        if method == "PATCH" and path.startswith("/pages/"):
            self.updated_vendor_rules.append(kwargs["json"])
            return {"id": path.rsplit("/", 1)[-1]}
        raise AssertionError(f"Unexpected Notion call: {method} {path}")


class FakeGraph:
    def find_bill_messages(self, days: int, limit: int):
        return [
            build_email(
                sender_name="Acme Software",
                subject="Your subscription invoice",
                body="Amount due $42.50. Due date July 20, 2026.",
            )
        ]


def vendor_rule_page_from_properties(properties: dict) -> dict:
    readable: dict = {}
    for name, value in properties.items():
        if "title" in value:
            readable[name] = {
                "title": [
                    {"plain_text": part.get("plain_text") or part.get("text", {}).get("content", "")}
                    for part in value["title"]
                ]
            }
        elif "rich_text" in value:
            readable[name] = {
                "rich_text": [
                    {"plain_text": part.get("plain_text") or part.get("text", {}).get("content", "")}
                    for part in value["rich_text"]
                ]
            }
        else:
            readable[name] = value
    return {"id": properties.get("_page_id", f"vendor-page-{id(properties)}"), "properties": readable}


def build_settings(**overrides) -> SimpleNamespace:
    values = {
        "notion_api_key": "secret",
        "notion_version": "2026-03-11",
        "notion_parent_page_id": "page-id",
        "database_name": "Cash Flow HQ",
        "cash_flow_data_source_id": "",
        "vendor_rules_data_source_id": "",
        "cash_flow_teams_user": "jaye@unitedaccountservices.com",
        "cash_flow_teams_chat_id": "",
        "teams_graph_tenant_id": "tenant",
        "teams_graph_client_id": "client",
        "teams_graph_client_secret": "secret",
        "teams_graph_token_cache_path": Path(".graph_teams_token_cache.bin"),
        "cash_flow_notification_time": "08:00",
        "cash_flow_notification_state_path": Path("work/test-cash-flow-notification-state.json"),
    }
    values.update(overrides)
    return SimpleNamespace(
        **values,
    )


def cash_flow_page(
    vendor: str,
    amount: float | None,
    due_date: str | None,
    due_status: str,
    status: str,
) -> dict:
    properties = {
        "Vendor / Payee": {"rich_text": [{"plain_text": vendor}]},
        "Due Status": {"formula": {"type": "string", "string": due_status}},
        "Status": {"select": {"name": status}},
    }
    if amount is not None:
        properties["Amount"] = {"number": amount}
    if due_date:
        properties["Due Date"] = {"date": {"start": due_date}}
    return {"properties": properties}


class FakeTeamsGraph:
    def __init__(self) -> None:
        self.direct_messages: list[tuple[str, str]] = []
        self.chat_messages: list[tuple[str, str]] = []

    def post_direct_chat_message(self, user_email: str, html_content: str) -> None:
        self.direct_messages.append((user_email, html_content))

    def post_chat_message(self, chat_id: str, html_content: str) -> None:
        self.chat_messages.append((chat_id, html_content))


class FakeSelfDirectGraph:
    def delegated_request(self, method: str, path: str, scopes: list[str], **kwargs):
        if method == "GET" and path == "/me?$select=id,mail,userPrincipalName":
            return {
                "id": "me-id",
                "mail": "jaye@unitedaccountservices.com",
                "userPrincipalName": "jaye@unitedaccountservices.com",
            }
        raise AssertionError(f"Unexpected delegated request: {method} {path}")

    post_direct_chat_message = GraphClient.post_direct_chat_message
    delegated_user_profile = GraphClient.delegated_user_profile


def build_email(
    subject: str,
    body: str,
    sender_name: str = "Vendor Billing",
    sender_email: str = "billing@example.com",
    body_source: str = "body",
    attachments: tuple[AttachmentMetadata, ...] = (),
) -> BillEmail:
    return BillEmail(
        message_id="message-id",
        internet_message_id="<message-id@example.com>",
        subject=subject,
        sender_name=sender_name,
        sender_email=sender_email,
        received_at=datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc),
        body_text=body,
        body_source=body_source,
        web_link="https://outlook.office.com/mail/id/message-id",
        attachments=attachments,
    )


class FakeCashFlowGraph(CashFlowGraphClient):
    def __init__(self, settings) -> None:
        self.settings = settings
        self.calls: list[str] = []

    def request(self, method: str, path: str, **kwargs):
        if path.startswith("/users/billing%40example.com/mailFolders/inbox/messages?"):
            return {
                "value": [
                    {
                        "id": "message-id",
                        "internetMessageId": "<message-id@example.com>",
                        "subject": "Invoice available",
                        "receivedDateTime": "2026-07-06T12:00:00Z",
                        "from": {"emailAddress": {"name": "Vendor Billing", "address": "billing@example.com"}},
                        "bodyPreview": "Your invoice is ready.",
                        "webLink": "https://outlook.office.com/mail/id/message-id",
                        "hasAttachments": True,
                    }
                ]
            }
        if path.startswith("/users/billing%40example.com/messages/message-id?$select="):
            self.calls.append("GET_DETAIL")
            return {
                "id": "message-id",
                "internetMessageId": "<message-id@example.com>",
                "subject": "Invoice available",
                "receivedDateTime": "2026-07-06T12:00:00Z",
                "from": {"emailAddress": {"name": "Vendor Billing", "address": "billing@example.com"}},
                "body": {
                    "contentType": "html",
                    "content": "<p>Amount Due: $42.50</p><p>Due Date: July 20, 2026</p>",
                },
                "bodyPreview": "Your invoice is ready.",
                "webLink": "https://outlook.office.com/mail/id/message-id",
                "hasAttachments": True,
            }
        if path.startswith("/users/billing%40example.com/messages/message-id/attachments?"):
            self.calls.append("GET_ATTACHMENTS")
            return {
                "value": [
                    {"id": "attachment-id", "name": "invoice.pdf", "contentType": "application/pdf", "size": 12345}
                ]
            }
        if path.startswith("/users/billing%40example.com/messages/message-id/attachments/attachment-id"):
            self.calls.append("GET_ATTACHMENT_CONTENT")
            return {
                "id": "attachment-id",
                "name": "invoice.pdf",
                "contentType": "application/pdf",
                "size": 12345,
                "contentBytes": b64encode(sample_invoice_pdf_bytes()).decode("ascii"),
            }
        raise AssertionError(f"Unexpected Graph call: {method} {path}")


def sample_invoice_pdf_bytes(text: str | None = None) -> bytes:
    content = text or "Vendor: Sample Vendor LLC\\nInvoice Number INV-12345\\nAmount Due: $123.45\\nDue Date: July 10, 2026"
    site_packages = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/site-packages"
    if site_packages.exists():
        sys.path.append(str(site_packages))
    from reportlab.pdfgen import canvas

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer)
    y = 750
    for line in content.split("\\n"):
        pdf.drawString(72, y, line)
        y -= 20
    pdf.save()
    return buffer.getvalue()
