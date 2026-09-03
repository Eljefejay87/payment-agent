from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from agents.cash_flow_hq.weekly_planner import (
    WeeklyCashPlannerDatabase,
    WeeklyCashPlannerService,
)
from agents.cash_flow_hq.private_bridge_service import CashFlowHqPrivateBridgeService, StaleCashFlowRecordError
from agents.icr_remit_agent.database import ICRRemitDatabase
from agents.icr_remit_agent.models import ICRRemitResult


class WeeklyCashPlannerTests(unittest.TestCase):
    def test_operating_reserved_and_spendable_cash_calculations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db = WeeklyCashPlannerDatabase(Path(temp_dir) / "planner.sqlite3")
            plan = db.save_plan_from_remit(example_remit(), Decimal("673.00"))
            db.add_reservation(plan.week_id, "Licenses", "Licensing", Decimal("900.00"), priority=1)
            db.add_reservation(plan.week_id, "Rent (Amex)", "Rent", Decimal("400.00"), priority=2)
            db.add_reservation(plan.week_id, "Website & Payment Portal", "Website", Decimal("800.00"), priority=3)
            service = WeeklyCashPlannerService(Path(temp_dir) / "planner.sqlite3", Path(temp_dir) / "remit.sqlite3")

            snapshot = service.snapshot(today=date(2026, 7, 13))

            self.assertEqual(plan.operating_cash, Decimal("2683.29"))
            self.assertEqual(snapshot.reserved_cash, Decimal("2100.00"))
            self.assertEqual(snapshot.spendable_cash, Decimal("583.29"))

    def test_only_planned_and_reserved_reduce_spendable_cash(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db = WeeklyCashPlannerDatabase(Path(temp_dir) / "planner.sqlite3")
            plan = db.save_plan_from_remit(example_remit(), Decimal("0"))
            planned = db.add_reservation(plan.week_id, "Planned item", "Custom", Decimal("100.00"), status="Planned")
            reserved = db.add_reservation(plan.week_id, "Reserved item", "Custom", Decimal("200.00"), status="Reserved")
            paid = db.add_reservation(plan.week_id, "Paid item", "Custom", Decimal("300.00"), status="Paid")

            snapshot = WeeklyCashPlannerService(db.path, Path(temp_dir) / "remit.sqlite3").snapshot(today=date(2026, 7, 13))

            self.assertTrue(planned.reduces_spendable_cash)
            self.assertTrue(reserved.reduces_spendable_cash)
            self.assertFalse(paid.reduces_spendable_cash)
            self.assertEqual(snapshot.reserved_cash, Decimal("300.00"))

    def test_reservation_status_transitions_change_reserved_cash(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db = WeeklyCashPlannerDatabase(Path(temp_dir) / "planner.sqlite3")
            plan = db.save_plan_from_remit(example_remit())
            reservation = db.add_reservation(plan.week_id, "Tax reserve", "Taxes", Decimal("500.00"))

            db.update_reservation_status(reservation.id, "Paid")
            snapshot = WeeklyCashPlannerService(db.path, Path(temp_dir) / "remit.sqlite3").snapshot(today=date(2026, 7, 13))

            self.assertEqual(snapshot.reserved_cash, Decimal("0.00"))

    def test_historical_week_preservation_and_duplicate_prevention(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db = WeeklyCashPlannerDatabase(Path(temp_dir) / "planner.sqlite3")
            original = db.save_plan_from_remit(example_remit(total=Decimal("4738.00")), Decimal("673.00"))
            duplicate = db.save_plan_from_remit(example_remit(total=Decimal("9999.00")), Decimal("0"))

            self.assertEqual(duplicate.week_id, original.week_id)
            self.assertEqual(duplicate.weekly_remit_amount, Decimal("4738.00"))
            self.assertEqual(duplicate.operating_deficit, Decimal("673.00"))

    def test_weekly_remit_import_uses_existing_import_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            remit_db_path = Path(temp_dir) / "remit.sqlite3"
            remit_db = ICRRemitDatabase(remit_db_path)
            remit_db.initialize()
            remit_db.save_import(example_remit())
            service = WeeklyCashPlannerService(Path(temp_dir) / "planner.sqlite3", remit_db_path)

            plan = service.create_plan_from_latest_remit(Decimal("673.00"), today=date(2026, 7, 13))

            self.assertEqual(plan.week_start, date(2026, 7, 13))
            self.assertEqual(plan.week_end, date(2026, 7, 19))
            self.assertEqual(plan.weekly_remit_amount, Decimal("4738.00"))
            self.assertEqual(plan.jim_remit_amount, Decimal("1381.71"))
            self.assertEqual(plan.jim_remit_status, "Open")
            self.assertIsNone(plan.jim_remit_paid_at)
            self.assertEqual(plan.remit_status, "Finalized")

    def test_payment_agent_bill_compatibility_splits_by_next_remit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db = WeeklyCashPlannerDatabase(Path(temp_dir) / "planner.sqlite3")
            db.save_plan_from_remit(example_remit())
            service = WeeklyCashPlannerService(db.path, Path(temp_dir) / "remit.sqlite3")
            bills = [
                {"vendor": "ADP", "amount": 69.90, "due_date": date(2026, 7, 23), "status": "Upcoming"},
                {"vendor": "Rent", "amount": 400.00, "due_date": date(2026, 7, 30), "status": "Upcoming"},
                {"vendor": "Zoom", "amount": 16.99, "due_date": date(2026, 7, 15), "status": "Paid"},
            ]

            snapshot = service.snapshot(bills, today=date(2026, 7, 13))

            self.assertEqual(snapshot.next_expected_remit, date(2026, 7, 26))
            self.assertEqual([row["vendor"] for row in snapshot.bills_due_before_next_remit], ["ADP"])
            self.assertEqual([row["vendor"] for row in snapshot.bills_due_after_next_remit], ["Rent"])

    def test_jason_snapshot_is_read_only_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db = WeeklyCashPlannerDatabase(Path(temp_dir) / "planner.sqlite3")
            plan = db.save_plan_from_remit(example_remit(), Decimal("673.00"))
            db.add_reservation(plan.week_id, "Licenses", "Licensing", Decimal("900.00"), priority=1)
            service = WeeklyCashPlannerService(db.path, Path(temp_dir) / "remit.sqlite3")

            snapshot = service.jason_snapshot(today=date(2026, 7, 13))

            self.assertEqual(snapshot["plan"]["weekly_remit_amount"], "$4,738.00")
            self.assertEqual(snapshot["plan"]["jim_remit_amount"], "$1,381.71")
            self.assertEqual(snapshot["plan"]["jim_remit_status"], "Open")
            self.assertEqual(snapshot["operating_cash"], "$2,683.29")
            self.assertEqual(snapshot["top_reservations"][0]["title"], "Licenses")

    def test_legacy_plan_defaults_jim_remit_status_to_open(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db = WeeklyCashPlannerDatabase(Path(temp_dir) / "planner.sqlite3")
            db.initialize()
            with db.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO weekly_cash_plans
                    (week_id, week_start, week_end, weekly_remit_amount, jim_remit_amount,
                     operating_deficit, remit_status, remit_source, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "cash-plan-2026-07-27",
                        "2026-07-27",
                        "2026-08-02",
                        "4738.00",
                        "1381.71",
                        "671.00",
                        "Finalized",
                        "manual",
                        "Open",
                        "2026-07-30T00:00:00+00:00",
                        "2026-07-30T00:00:00+00:00",
                    ),
                )
            plan = db.open_plan_for_week(date(2026, 7, 27))
            self.assertIsNotNone(plan)
            self.assertEqual(plan.jim_remit_status, "Open")
            self.assertIsNone(plan.jim_remit_paid_at)

    def test_mark_current_week_jim_remit_paid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = WeeklyCashPlannerService(Path(temp_dir) / "planner.sqlite3", Path(temp_dir) / "remit.sqlite3")
            service.record_already_sent_remit(
                week_start=date(2026, 7, 27),
                weekly_remit=Decimal("4600.00"),
                jim_remit=Decimal("1381.71"),
                operating_deficit=Decimal("671.00"),
            )
            plan = service.mark_current_week_jim_remit_paid(today=date(2026, 7, 30), paid_at="2026-07-30T12:00:00+00:00")
            self.assertEqual(plan.jim_remit_status, "Paid")
            self.assertEqual(plan.jim_remit_paid_at, "2026-07-30T12:00:00+00:00")

    def test_mark_current_week_jim_remit_cancelled_or_deferred_excludes_future_obligation_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db = WeeklyCashPlannerDatabase(Path(temp_dir) / "planner.sqlite3")
            plan = db.save_plan_from_remit(example_remit(), Decimal("0"))
            cancelled = db.update_jim_remit_status(plan.week_id, "Cancelled")
            deferred = db.update_jim_remit_status(plan.week_id, "Deferred")
            self.assertEqual(cancelled.jim_remit_status, "Cancelled")
            self.assertIsNone(cancelled.jim_remit_paid_at)
            self.assertEqual(deferred.jim_remit_status, "Deferred")
            self.assertIsNone(deferred.jim_remit_paid_at)

    def test_unpaid_jim_remit_remains_open_until_marked_paid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = WeeklyCashPlannerService(Path(temp_dir) / "planner.sqlite3", Path(temp_dir) / "remit.sqlite3")
            plan = service.record_already_sent_remit(
                week_start=date(2026, 7, 27),
                weekly_remit=Decimal("4600.00"),
                jim_remit=Decimal("1381.71"),
                operating_deficit=Decimal("671.00"),
            )
            self.assertEqual(plan.jim_remit_status, "Open")
            self.assertIsNone(plan.jim_remit_paid_at)

    def test_current_week_does_not_use_prior_week_remit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            remit_db_path = Path(temp_dir) / "remit.sqlite3"
            remit_db = ICRRemitDatabase(remit_db_path)
            remit_db.initialize()
            remit_db.save_import(example_remit(remit_week=date(2026, 7, 6), total=Decimal("1279.51"), jim=Decimal("767.68")))
            service = WeeklyCashPlannerService(Path(temp_dir) / "planner.sqlite3", remit_db_path)

            with self.assertRaisesRegex(RuntimeError, "2026-07-13"):
                service.create_plan_from_latest_remit(Decimal("673.00"), today=date(2026, 7, 13))

            self.assertIsNone(service.latest_validated_remit(date(2026, 7, 13)))

    def test_current_week_uses_matching_week_remit_not_older_import(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            remit_db_path = Path(temp_dir) / "remit.sqlite3"
            remit_db = ICRRemitDatabase(remit_db_path)
            remit_db.initialize()
            remit_db.save_import(example_remit(remit_week=date(2026, 7, 6), total=Decimal("1279.51"), jim=Decimal("767.68")))
            remit_db.save_import(example_remit(remit_week=date(2026, 7, 13), total=Decimal("4738.00"), jim=Decimal("1381.71")))
            service = WeeklyCashPlannerService(Path(temp_dir) / "planner.sqlite3", remit_db_path)

            plan = service.create_plan_from_latest_remit(Decimal("673.00"), today=date(2026, 7, 13))

            self.assertEqual(plan.week_start, date(2026, 7, 13))
            self.assertEqual(plan.weekly_remit_amount, Decimal("4738.00"))
            self.assertEqual(plan.jim_remit_amount, Decimal("1381.71"))

    def test_record_already_sent_remit_creates_local_plan_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = WeeklyCashPlannerService(Path(temp_dir) / "planner.sqlite3", Path(temp_dir) / "remit.sqlite3")

            plan = service.record_already_sent_remit(
                week_start=date(2026, 7, 27),
                weekly_remit=Decimal("4600.00"),
                jim_remit=Decimal("1381.00"),
                operating_deficit=Decimal("673.00"),
            )
            duplicate = service.record_already_sent_remit(
                week_start=date(2026, 7, 27),
                weekly_remit=Decimal("9999.00"),
                jim_remit=Decimal("9999.00"),
                operating_deficit=Decimal("0"),
            )

            self.assertEqual(duplicate.week_id, plan.week_id)
            self.assertEqual(duplicate.weekly_remit_amount, Decimal("4600.00"))
            self.assertEqual(duplicate.jim_remit_amount, Decimal("1381.00"))
            self.assertEqual(duplicate.operating_cash, Decimal("2546.00"))
            self.assertEqual(len(service.remit_db.list_imports()), 1)

    def test_private_bridge_marks_current_week_jim_remit_paid_only_after_binding_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            planner = WeeklyCashPlannerService(Path(temp_dir) / "planner.sqlite3", Path(temp_dir) / "remit.sqlite3")
            plan = planner.record_already_sent_remit(
                week_start=date(2026, 8, 31),
                weekly_remit=Decimal("5000.00"),
                jim_remit=Decimal("1330.22"),
                operating_deficit=Decimal("100.00"),
            )
            before = planner.db.get_plan_for_week(date(2026, 8, 31))
            bridge = CashFlowHqPrivateBridgeService(
                str(Path(temp_dir) / "shared.sqlite3"),
                repository=NoopRepository(),
                planner=planner,
                cash_flow=NoopCashFlow(),
                now=lambda: date(2026, 9, 3),
            )

            preview = bridge.current_week_jim_remit(today=date(2026, 9, 3))
            self.assertEqual(preview["status"], "ok")
            self.assertEqual(preview["record"]["amount"], "$1,330.22")
            self.assertEqual(preview["record"]["current_status"], "Open")
            self.assertEqual(bridge.repository.list_calls, 0)
            self.assertEqual(bridge.cash_flow.calls, [])

            updated = bridge.mark_current_week_jim_remit_paid(
                expected_week_id=plan.week_id,
                expected_week_start="2026-08-31",
                expected_amount=Decimal("1330.22"),
                expected_status="Open",
                today=date(2026, 9, 3),
            )
            after = planner.db.get_plan_for_week(date(2026, 8, 31))

            self.assertEqual(updated["status"], "ok")
            self.assertEqual(updated["record"]["current_status"], "Paid")
            self.assertIsNotNone(after.jim_remit_paid_at)
            self.assertEqual(after.week_id, before.week_id)
            self.assertEqual(after.week_start, before.week_start)
            self.assertEqual(after.week_end, before.week_end)
            self.assertEqual(after.weekly_remit_amount, before.weekly_remit_amount)
            self.assertEqual(after.jim_remit_amount, before.jim_remit_amount)
            self.assertEqual(after.operating_deficit, before.operating_deficit)
            self.assertEqual(after.remit_status, before.remit_status)
            self.assertEqual(after.remit_source, before.remit_source)
            self.assertEqual(after.status, before.status)
            self.assertEqual(after.created_at, before.created_at)
            self.assertEqual(planner.db.reservations_for_week(plan.week_id), [])
            self.assertEqual(bridge.repository.list_calls, 0)
            self.assertEqual(bridge.cash_flow.calls, [])

    def test_private_bridge_rejects_stale_jim_remit_approval_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            planner = WeeklyCashPlannerService(Path(temp_dir) / "planner.sqlite3", Path(temp_dir) / "remit.sqlite3")
            plan = planner.record_already_sent_remit(
                week_start=date(2026, 8, 31),
                weekly_remit=Decimal("5000.00"),
                jim_remit=Decimal("1330.22"),
            )
            bridge = CashFlowHqPrivateBridgeService(
                str(Path(temp_dir) / "shared.sqlite3"),
                repository=NoopRepository(),
                planner=planner,
                cash_flow=NoopCashFlow(),
            )

            with self.assertRaises(StaleCashFlowRecordError):
                bridge.mark_current_week_jim_remit_paid(
                    expected_week_id=plan.week_id,
                    expected_week_start="2026-08-31",
                    expected_amount=Decimal("999.00"),
                    expected_status="Open",
                    today=date(2026, 9, 3),
                )

            unchanged = planner.db.get_plan_for_week(date(2026, 8, 31))
            self.assertEqual(unchanged.jim_remit_status, "Open")
            self.assertIsNone(unchanged.jim_remit_paid_at)
            self.assertEqual(unchanged.jim_remit_amount, Decimal("1330.22"))


def example_remit(
    total: Decimal = Decimal("4738.00"),
    jim: Decimal = Decimal("1381.71"),
    remit_week: date = date(2026, 7, 13),
) -> ICRRemitResult:
    return ICRRemitResult(
        broker="ICR",
        contact="Jim",
        remit_week=remit_week,
        week_ending=remit_week + timedelta(days=6),
        file_path=Path("icr-remit.csv"),
        due_to_agency=total - jim,
        due_to_client=jim,
        total_collected=total,
        status="Finalized",
        notes="Validated remit import.",
    )


class NoopRepository:
    def __init__(self) -> None:
        self.list_calls = 0

    def list(self, *_args, **_kwargs):
        self.list_calls += 1
        return []


class NoopCashFlow:
    def __init__(self) -> None:
        self.calls = []

    def __getattr__(self, name):
        def _record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            raise AssertionError(f"Unexpected Cash Flow call: {name}")
        return _record


if __name__ == "__main__":
    unittest.main()
