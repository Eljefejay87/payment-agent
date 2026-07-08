from __future__ import annotations

import os
import sys
import unittest
from base64 import b64encode
from datetime import date, datetime, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agents.cash_flow_hq.config import load_cash_flow_settings, validate_cash_flow_settings
from agents.cash_flow_hq.email_scan import CashFlowEmailScanner
from agents.cash_flow_hq.graph_client import CashFlowGraphClient
from agents.cash_flow_hq.models import AttachmentMetadata, BillEmail, VendorRule
from agents.cash_flow_hq.parser import extract_amount, extract_due_date, is_bill_related, message_from_graph, parse_bill_candidate
from agents.cash_flow_hq.schema import (
    CATEGORY_OPTIONS,
    DUE_STATUS_PROPERTY_NAME,
    SOURCE_OPTIONS,
    STATUS_OPTIONS,
    VENDOR_RULE_DATABASE_NAME,
    VENDOR_RULE_SEEDS,
    build_database_payload,
    build_vendor_rules_database_payload,
    build_view_payload,
    build_view_specs,
    due_status_label,
)
from agents.cash_flow_hq.service import CashFlowHQService


class CashFlowHQSchemaTests(unittest.TestCase):
    def test_database_payload_contains_requested_properties(self) -> None:
        payload = build_database_payload("page-id", "Cash Flow HQ")
        properties = payload["initial_data_source"]["properties"]

        self.assertEqual(properties["Expense Name"], {"title": {}})
        self.assertEqual(properties["Vendor / Payee"], {"rich_text": {}})
        self.assertEqual(properties["Amount"], {"number": {"format": "dollar"}})
        self.assertEqual(properties["Due Date"], {"date": {}})
        self.assertIn('prop("Due Date")', properties[DUE_STATUS_PROPERTY_NAME]["formula"]["expression"])
        self.assertEqual(properties["Payment Date"], {"date": {}})
        self.assertEqual(properties["Email Link"], {"url": {}})
        self.assertEqual(properties["Notes"], {"rich_text": {}})
        self.assertIn('prop("Due Date")', properties["Week"]["formula"]["expression"])
        self.assertIn('prop("Due Date")', properties["Month"]["formula"]["expression"])

    def test_due_status_labels_are_human_friendly(self) -> None:
        today = date(2026, 7, 8)

        self.assertEqual(due_status_label(today, today), "Due Today")
        self.assertEqual(due_status_label(date(2026, 7, 9), today), "Due Tomorrow")
        self.assertEqual(due_status_label(date(2026, 7, 11), today), "Due in 3 Days")
        self.assertEqual(due_status_label(date(2026, 7, 6), today), "Past Due by 2 Days")
        self.assertEqual(due_status_label(None, today), "")

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

    def test_view_specs_include_requested_views(self) -> None:
        names = [spec.name for spec in build_view_specs()]

        self.assertEqual(
            names,
            [
                "Dashboard",
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
        spec = build_view_specs()[1]
        payload = build_view_payload("database-id", "source-id", spec)

        self.assertEqual(payload["database_id"], "database-id")
        self.assertEqual(payload["data_source_id"], "source-id")
        self.assertEqual(payload["name"], "This Week")
        self.assertEqual(payload["filter"]["property"], "Due Date")
        self.assertEqual(payload["filter"]["date"], {"this_week": {}})

    def test_month_view_uses_supported_notion_date_filter_conditions(self) -> None:
        spec = build_view_specs()[2]
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
            settings = load_cash_flow_settings()

        self.assertEqual(settings.database_name, "Cash Flow HQ")
        self.assertEqual(settings.notion_version, "2026-03-11")

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
        self.assertEqual(len(result["views_created"]), 10)
        self.assertEqual(notion.patched_views, 1)
        self.assertEqual(len(notion.views), 9)
        self.assertEqual(notion.created_pages, 0)
        self.assertEqual(len(notion.created_vendor_rules), 2)
        self.assertIn(DUE_STATUS_PROPERTY_NAME, notion.data_source_properties)

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
        rule = VendorRule("D1AL", "D1AL", "Software", "Monthly", 5, "Manual", "Upcoming", True)

        updated = service.apply_vendor_rules(candidate, email, [rule])

        self.assertEqual(updated.category, "Software")
        self.assertEqual(updated.frequency, "Monthly")
        self.assertEqual(updated.payment_type, "Manual")
        self.assertEqual(updated.due_date.isoformat(), "2026-07-05")
        self.assertEqual(updated.status, "Upcoming")
        self.assertEqual(updated.review_reasons, ())
        self.assertEqual(updated.field_sources["due_date"], "vendor rules")
        self.assertNotIn("Missing due date", updated.notes)
        self.assertEqual(updated.notes, "Imported from Outlook\n✓ Ready for payment")
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
        rule = VendorRule("Pope and Land", "Pope and Land", "Rent", "Monthly", 1, "Manual", "Upcoming", True)

        updated = service.apply_vendor_rules(candidate, email, [rule])

        self.assertEqual(updated.category, "Rent")
        self.assertEqual(updated.due_date.isoformat(), "2026-07-01")
        self.assertEqual(updated.status, "Upcoming")
        self.assertEqual(updated.frequency, "Monthly")
        self.assertNotIn("Missing due date", updated.notes)
        self.assertEqual(updated.notes, "Imported from Outlook\n✓ Ready for payment")

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
            "Imported from Outlook\n\nNeeds Review:\n• Missing due date",
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


class FakeNotion:
    def __init__(self, existing_database: bool = False, existing_vendor_rules: bool = False) -> None:
        self.existing_database = existing_database
        self.existing_vendor_rules = existing_vendor_rules
        self.created_pages = 0
        self.created_vendor_rules: list[dict] = []
        self.patched_views = 0
        self.views: list[dict] = []
        self.query_results: list[dict] = []
        self.vendor_rule_pages: list[dict] = []
        self.data_source_properties: dict = {}
        self.search_payload: dict | None = None

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
                return {
                    "results": [
                        {
                            "id": "vendor-source-id",
                            "title": [{"plain_text": VENDOR_RULE_DATABASE_NAME}],
                            "parent": {"type": "database_id", "database_id": "vendor-database-id"},
                        }
                    ]
                }
            return {"results": []}
        if method == "POST" and path == "/data_sources/source-id/query":
            return {"results": self.query_results}
        if method == "POST" and path == "/data_sources/vendor-source-id/query":
            return {"results": self.vendor_rule_pages}
        if method == "POST" and path == "/databases":
            title = kwargs["json"]["title"][0]["text"]["content"]
            if title == VENDOR_RULE_DATABASE_NAME:
                self.existing_vendor_rules = True
                return {"id": "vendor-database-id", "data_sources": [{"id": "vendor-source-id"}]}
            self.existing_database = True
            self.data_source_properties = kwargs["json"]["initial_data_source"]["properties"]
            return {"id": "database-id", "data_sources": [{"id": "source-id"}]}
        if method == "GET" and path == "/data_sources/source-id":
            return {"id": "source-id", "properties": self.data_source_properties}
        if method == "PATCH" and path == "/data_sources/source-id":
            self.data_source_properties.update(kwargs["json"]["properties"])
            return {"id": "source-id", "properties": self.data_source_properties}
        if method == "GET" and path == "/databases/database-id":
            return {"id": "database-id", "data_sources": [{"id": "source-id"}]}
        if method == "GET" and path == "/databases/vendor-database-id":
            return {"id": "vendor-database-id", "data_sources": [{"id": "vendor-source-id"}]}
        if method == "GET" and path == "/views?database_id=database-id":
            return {"results": [{"id": "default-view"}]}
        if method == "GET" and path == "/views/default-view":
            return {"id": "default-view", "name": "Default view"}
        if method == "PATCH" and path == "/views/default-view":
            self.patched_views += 1
            return {"id": "default-view", "name": kwargs["json"]["name"]}
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
    return {"properties": readable}


def build_settings() -> SimpleNamespace:
    return SimpleNamespace(
        notion_api_key="secret",
        notion_version="2026-03-11",
        notion_parent_page_id="page-id",
        database_name="Cash Flow HQ",
    )


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
