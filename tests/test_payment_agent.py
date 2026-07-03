from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agents.payment_agent.config import load_settings, validate_settings
from agents.payment_agent.database import PaymentDatabase
from agents.payment_agent.graph_client import is_payment_subject
from agents.payment_agent.models import PaymentRecord
from agents.payment_agent.parser import cents_to_currency, parse_payment_email
from agents.payment_agent.reports import build_daily_report, build_realtime_alert
from agents.payment_agent.service import PaymentAgent


class PaymentParserTests(unittest.TestCase):
    def test_parse_payment_email(self) -> None:
        parsed = parse_payment_email(
            "\n".join(
                [
                    "Account: B123440",
                    "Type: ACH",
                    "Note: Online payment",
                    "Payments date: 2026-06-29",
                    "Payment amount: $1,234.56",
                ]
            )
        )

        self.assertEqual(parsed.account_number, "B123440")
        self.assertEqual(parsed.payment_type, "ACH")
        self.assertEqual(parsed.payment_date, "2026-06-29")
        self.assertEqual(cents_to_currency(parsed.payment_amount_cents), "$1,234.56")

    def test_parse_real_payments_line_format(self) -> None:
        parsed = parse_payment_email(
            "\n".join(
                [
                    "Account: B123440",
                    "Type: Online",
                    "Note: Customer payment",
                    "Payments: 6/29/2026 $141.12",
                ]
            )
        )

        self.assertEqual(parsed.account_number, "B123440")
        self.assertEqual(parsed.payment_type, "Online")
        self.assertEqual(parsed.note, "Customer payment")
        self.assertEqual(parsed.payment_date, "6/29/2026")
        self.assertEqual(cents_to_currency(parsed.payment_amount_cents), "$141.12")


class PaymentSubjectTests(unittest.TestCase):
    def test_original_payment_subject_matches(self) -> None:
        self.assertTrue(is_payment_subject("Online Payment - B123440", "Online Payment -"))

    def test_approved_debit_card_subject_matches(self) -> None:
        self.assertTrue(
            is_payment_subject(
                "Online Debit Card payment was approved (115004) Reference number: B139301",
                "Online Payment -",
            )
        )

    def test_approved_credit_card_subject_matches(self) -> None:
        self.assertTrue(
            is_payment_subject(
                "Online Credit Card payment was approved (841317) Reference number: 3451079",
                "Online Payment -",
            )
        )

    def test_approved_credit_or_debit_card_subject_matches(self) -> None:
        self.assertTrue(
            is_payment_subject(
                "Online Credit or Debit Card payment was approved (040144) Reference number: C205815",
                "Online Payment -",
            )
        )

    def test_non_payment_subject_does_not_match(self) -> None:
        self.assertFalse(is_payment_subject("Email batch is ready for review", "Online Payment -"))


class PaymentDatabaseTests(unittest.TestCase):
    def test_duplicate_detection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db = PaymentDatabase(Path(temp_dir) / "payments.sqlite3")
            db.initialize()
            payment = PaymentRecord(
                message_id="message-1",
                account_number="B123440",
                payment_type="ACH",
                note="Online payment",
                payment_date="2026-06-29",
                payment_amount_cents=123456,
                email_received_at="2026-06-29T10:00:00Z",
                email_subject="Online Payment - B123440",
                sender_email="sender@example.com",
            )

            self.assertFalse(db.is_processed("message-1"))
            db.save_payment(payment, internet_message_id="<message-1>", processed_at=datetime.now(timezone.utc))
            db.save_payment(payment, internet_message_id="<message-1>", processed_at=datetime.now(timezone.utc))

            self.assertTrue(db.is_processed("message-1"))
            self.assertTrue(db.is_processed_email("message-2", "<message-1>"))
            rows = db.payments_for_local_date("2026-06-29")
            self.assertEqual(len(rows), 1)

    def test_duplicate_payment_detects_new_graph_id_same_email(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db = PaymentDatabase(Path(temp_dir) / "payments.sqlite3")
            db.initialize()
            payment = PaymentRecord(
                message_id="message-1",
                account_number="B123440",
                payment_type="Debit Card",
                note="Online payment",
                payment_date="2026-06-29",
                payment_amount_cents=14112,
                email_received_at="2026-06-29T10:00:00Z",
                email_subject="Online Payment - B123440",
                sender_email="sender@example.com",
            )
            moved_payment = PaymentRecord(
                message_id="message-2",
                account_number="B123440",
                payment_type="Debit Card",
                note="Online payment",
                payment_date="2026-06-29",
                payment_amount_cents=14112,
                email_received_at="2026-06-29T10:00:00Z",
                email_subject="Online Payment - B123440",
                sender_email="sender@example.com",
            )

            db.save_payment(payment, internet_message_id="<stable-email-id>", processed_at=datetime.now(timezone.utc))

            self.assertTrue(db.is_duplicate_payment(moved_payment, "<stable-email-id>"))
            self.assertTrue(db.is_duplicate_payment(moved_payment, None))


class PaymentConfigTests(unittest.TestCase):
    def test_config_validation_reports_missing_required_values(self) -> None:
        with patch.dict(os.environ, {"DRY_RUN": "true"}, clear=True):
            settings = load_settings(env_file="/tmp/ucm-payment-agent-test-missing.env")
            errors = validate_settings(settings)

        self.assertIn("MAILBOX_USER_ID is required.", errors)
        self.assertIn("SENDER_EMAIL is required.", errors)
        self.assertIn("MS_GRAPH_TENANT_ID is required.", errors)


class PaymentReportTests(unittest.TestCase):
    def test_daily_report_totals(self) -> None:
        rows = [
            {
                "account_number": "B123440",
                "payment_amount_cents": 10000,
                "payment_type": "ACH",
                "payment_date": "2026-06-29",
                "note": "First",
            },
            {
                "account_number": "B123441",
                "payment_amount_cents": 2500,
                "payment_type": "CARD",
                "payment_date": "2026-06-29",
                "note": "Second",
            },
        ]

        report = build_daily_report(rows, "2026-06-29")

        self.assertIn("Total payments: 2", report.text)
        self.assertIn("Total collected: $125.00", report.text)

    def test_realtime_alert_omits_unavailable_fields(self) -> None:
        payment = PaymentRecord(
            message_id="message-1",
            account_number="B123440",
            payment_type="Debit Card",
            note="One Time Payment",
            payment_date="6/29/2026",
            payment_amount_cents=14112,
            email_received_at="2026-06-29T14:39:56Z",
            email_subject="Online Payment - B123440",
            sender_email="support@unitedaccountsservice.com",
        )

        message = build_realtime_alert(payment)

        self.assertIn("💰 **Payment Received**", message.text)
        self.assertIn("💵 **Amount:** $141.12", message.text)
        self.assertIn("📄 **Account #:** B123440", message.text)
        self.assertIn("💳 **Payment Type:** Debit Card", message.text)
        self.assertIn("📝 **Note:** One Time Payment", message.text)
        self.assertIn("📅 **Received:** 6/29/2026", message.text)
        self.assertNotIn("None", message.text)
        self.assertNotIn("Debtor", message.text)
        self.assertNotIn("Creditor", message.text)


class PaymentAgentCleanupTests(unittest.TestCase):
    def test_successful_payment_cleanup_moves_to_processed_folder(self) -> None:
        agent = build_test_agent(processed=False)

        count = agent.scan_once()

        self.assertEqual(count, 1)
        self.assertEqual(agent.teams.sent_count, 1)
        self.assertEqual(agent.graph.read_messages, ["message-1"])
        self.assertEqual(agent.graph.moved_messages, [("message-1", "Processed Payments")])

    def test_duplicate_payment_cleanup_moves_to_duplicate_folder_without_teams(self) -> None:
        agent = build_test_agent(processed=True)

        count = agent.scan_once()

        self.assertEqual(count, 0)
        self.assertEqual(agent.teams.sent_count, 0)
        self.assertEqual(agent.graph.read_messages, ["message-1"])
        self.assertEqual(agent.graph.moved_messages, [("message-1", "Duplicate Payments")])

    def test_duplicate_stable_email_id_does_not_send_teams(self) -> None:
        agent = build_test_agent(processed=False)
        agent.db.processed_internet_message_ids.add("<message-1>")

        count = agent.scan_once()

        self.assertEqual(count, 0)
        self.assertEqual(agent.teams.sent_count, 0)
        self.assertEqual(agent.graph.moved_messages, [("message-1", "Duplicate Payments")])


def build_test_agent(processed: bool) -> PaymentAgent:
    agent = PaymentAgent.__new__(PaymentAgent)
    agent.settings = SimpleNamespace(
        mailbox_user_id="payments@example.com",
        save_email_html=False,
        realtime_enabled=False,
        teams_post_method="graph_chat",
    )
    agent.db = FakePaymentDatabase(processed=processed)
    agent.graph = FakePaymentGraph()
    agent.teams = FakeTeamsNotifier()
    return agent


class FakePaymentDatabase:
    def __init__(self, processed: bool) -> None:
        self.processed = processed
        self.processed_internet_message_ids: set[str] = set()

    def initialize(self) -> None:
        return None

    def is_processed(self, message_id: str) -> bool:
        return self.processed

    def is_processed_email(self, message_id: str, internet_message_id: str | None) -> bool:
        return self.processed or internet_message_id in self.processed_internet_message_ids

    def is_duplicate_payment(self, payment: PaymentRecord, internet_message_id: str | None) -> bool:
        return internet_message_id in self.processed_internet_message_ids

    def save_payment(self, payment: PaymentRecord, internet_message_id: str | None, processed_at: datetime) -> None:
        self.processed = True


class FakePaymentGraph:
    def __init__(self) -> None:
        self.read_messages: list[str] = []
        self.moved_messages: list[tuple[str, str]] = []

    def find_payment_messages(self) -> list[dict]:
        return [
            {
                "id": "message-1",
                "internetMessageId": "<message-1>",
                "subject": "Online Payment - B123440",
                "receivedDateTime": "2026-06-29T14:39:56Z",
                "from": {"emailAddress": {"address": "support@example.com"}},
                "body": {
                    "contentType": "text",
                    "content": "\n".join(
                        [
                            "Account: B123440",
                            "Type: Debit Card",
                            "Note: One Time Payment",
                            "Payments: 6/29/2026 $141.12",
                        ]
                    ),
                },
            }
        ]

    def mark_user_message_read(self, mailbox_user_id: str, message_id: str) -> None:
        self.read_messages.append(message_id)

    def move_user_message(self, mailbox_user_id: str, message_id: str, folder_name: str) -> None:
        self.moved_messages.append((message_id, folder_name))


class FakeTeamsNotifier:
    def __init__(self) -> None:
        self.sent_count = 0

    def send(self, message) -> None:
        self.sent_count += 1


if __name__ == "__main__":
    unittest.main()
