from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from agents.icr_remit_agent.database import ICRRemitDatabase
from agents.icr_remit_agent.models import ICRRemitResult
from shared.database import SQLiteDatabase


PLAN_STATUSES = ("Open", "Closed", "Archived")
JIM_REMIT_STATUSES = ("Open", "Planned", "Paid", "Cancelled", "Deferred")
JIM_REMIT_OPEN_STATUSES = {"Open", "Planned"}
RESERVATION_CATEGORIES = (
    "Rent",
    "Licensing",
    "Payroll",
    "Taxes",
    "Website",
    "Software",
    "Insurance",
    "Office",
    "Marketing",
    "Operations",
    "Custom",
)
RESERVATION_STATUSES = ("Planned", "Reserved", "Paid", "Released", "Cancelled")
RESERVING_STATUSES = {"Planned", "Reserved"}


SCHEMA = """
CREATE TABLE IF NOT EXISTS weekly_cash_plans (
    week_id TEXT PRIMARY KEY,
    week_start TEXT NOT NULL,
    week_end TEXT NOT NULL,
    weekly_remit_amount TEXT NOT NULL,
    jim_remit_amount TEXT NOT NULL,
    operating_deficit TEXT NOT NULL,
    remit_status TEXT NOT NULL,
    remit_source TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_weekly_cash_plans_week
ON weekly_cash_plans(week_start, week_end);

CREATE TABLE IF NOT EXISTS weekly_cash_reservations (
    id TEXT PRIMARY KEY,
    week_id TEXT NOT NULL,
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    amount TEXT NOT NULL,
    due_date TEXT,
    priority INTEGER NOT NULL,
    status TEXT NOT NULL,
    funding_source TEXT NOT NULL,
    notes TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(week_id) REFERENCES weekly_cash_plans(week_id),
    UNIQUE(week_id, title, amount, due_date)
);

CREATE INDEX IF NOT EXISTS idx_weekly_cash_reservations_week
ON weekly_cash_reservations(week_id, status, due_date);
"""


@dataclass(frozen=True)
class WeeklyCashPlan:
    week_id: str
    week_start: date
    week_end: date
    weekly_remit_amount: Decimal
    jim_remit_amount: Decimal
    jim_remit_status: str
    jim_remit_paid_at: str | None
    operating_deficit: Decimal
    remit_status: str
    remit_source: str
    status: str
    created_at: str
    updated_at: str

    @property
    def operating_cash(self) -> Decimal:
        return money(self.weekly_remit_amount - self.operating_deficit - self.jim_remit_amount)


@dataclass(frozen=True)
class ReservedFund:
    id: str
    week_id: str
    title: str
    category: str
    amount: Decimal
    due_date: date | None
    priority: int
    status: str
    funding_source: str
    notes: str
    created_at: str
    updated_at: str

    @property
    def reduces_spendable_cash(self) -> bool:
        return self.status in RESERVING_STATUSES


@dataclass(frozen=True)
class WeeklyCashPlannerSnapshot:
    plan: WeeklyCashPlan | None
    reservations: tuple[ReservedFund, ...]
    operating_cash: Decimal
    reserved_cash: Decimal
    spendable_cash: Decimal
    next_expected_remit: date | None
    bills_due_before_next_remit: tuple[dict, ...]
    bills_due_after_next_remit: tuple[dict, ...]
    recommendations: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "status": "Ready" if self.plan else "No Plan",
            "plan": plan_to_dict(self.plan) if self.plan else None,
            "reservations": [reservation_to_dict(item) for item in self.reservations],
            "operating_cash": format_money(self.operating_cash),
            "reserved_cash": format_money(self.reserved_cash),
            "spendable_cash": format_money(self.spendable_cash),
            "next_expected_remit": self.next_expected_remit.isoformat() if self.next_expected_remit else "",
            "bills_due_before_next_remit": list(self.bills_due_before_next_remit),
            "bills_due_after_next_remit": list(self.bills_due_after_next_remit),
            "recommendations": list(self.recommendations),
        }


class WeeklyCashPlannerDatabase(SQLiteDatabase):
    def initialize(self) -> None:
        self.initialize_schema(SCHEMA)
        self._ensure_jim_remit_columns()

    def _ensure_jim_remit_columns(self) -> None:
        with self.connect() as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(weekly_cash_plans)").fetchall()}
            if "jim_remit_status" not in columns:
                conn.execute("ALTER TABLE weekly_cash_plans ADD COLUMN jim_remit_status TEXT")
                conn.execute(
                    """
                    UPDATE weekly_cash_plans
                    SET jim_remit_status = 'Open'
                    WHERE jim_remit_status IS NULL OR trim(jim_remit_status) = ''
                    """
                )
            if "jim_remit_paid_at" not in columns:
                conn.execute("ALTER TABLE weekly_cash_plans ADD COLUMN jim_remit_paid_at TEXT")

    def save_plan_from_remit(
        self,
        remit: ICRRemitResult,
        operating_deficit: Decimal = Decimal("0"),
    ) -> WeeklyCashPlan:
        self.initialize()
        existing = self.get_plan_for_week(remit.remit_week)
        if existing:
            return existing
        now = utc_now()
        plan = WeeklyCashPlan(
            week_id=f"cash-plan-{remit.remit_week.isoformat()}",
            week_start=remit.remit_week,
            week_end=remit.week_ending,
            weekly_remit_amount=money(remit.total_collected),
            jim_remit_amount=money(remit.due_to_client),
            jim_remit_status="Open",
            jim_remit_paid_at=None,
            operating_deficit=money(operating_deficit),
            remit_status=remit.status,
            remit_source=remit.file_path.name,
            status="Open",
            created_at=now,
            updated_at=now,
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO weekly_cash_plans
                (week_id, week_start, week_end, weekly_remit_amount, jim_remit_amount, jim_remit_status, jim_remit_paid_at,
                 operating_deficit, remit_status, remit_source, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan.week_id,
                    plan.week_start.isoformat(),
                    plan.week_end.isoformat(),
                    str(plan.weekly_remit_amount),
                    str(plan.jim_remit_amount),
                    plan.jim_remit_status,
                    plan.jim_remit_paid_at,
                    str(plan.operating_deficit),
                    plan.remit_status,
                    plan.remit_source,
                    plan.status,
                    plan.created_at,
                    plan.updated_at,
                ),
            )
        return plan

    def get_plan_for_week(self, week_start: date) -> WeeklyCashPlan | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM weekly_cash_plans WHERE week_start = ?",
                (week_start.isoformat(),),
            ).fetchone()
        return plan_from_row(row) if row else None

    def latest_open_plan(self) -> WeeklyCashPlan | None:
        self.initialize()
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM weekly_cash_plans
                WHERE status = 'Open'
                ORDER BY week_start DESC
                LIMIT 1
                """
            ).fetchone()
        return plan_from_row(row) if row else None

    def open_plan_for_week(self, week_start: date) -> WeeklyCashPlan | None:
        self.initialize()
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM weekly_cash_plans
                WHERE status = 'Open' AND week_start = ?
                LIMIT 1
                """,
                (week_start.isoformat(),),
            ).fetchone()
        return plan_from_row(row) if row else None

    def add_reservation(
        self,
        week_id: str,
        title: str,
        category: str,
        amount: Decimal,
        due_date: date | None = None,
        priority: int = 3,
        status: str = "Planned",
        funding_source: str = "Operating Cash",
        notes: str = "",
    ) -> ReservedFund:
        validate_reservation(category, status)
        now = utc_now()
        reservation = ReservedFund(
            id=str(uuid4()),
            week_id=week_id,
            title=title,
            category=category,
            amount=money(amount),
            due_date=due_date,
            priority=priority,
            status=status,
            funding_source=funding_source,
            notes=notes,
            created_at=now,
            updated_at=now,
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO weekly_cash_reservations
                (id, week_id, title, category, amount, due_date, priority, status,
                 funding_source, notes, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reservation.id,
                    reservation.week_id,
                    reservation.title,
                    reservation.category,
                    str(reservation.amount),
                    reservation.due_date.isoformat() if reservation.due_date else None,
                    reservation.priority,
                    reservation.status,
                    reservation.funding_source,
                    reservation.notes,
                    reservation.created_at,
                    reservation.updated_at,
                ),
            )
        return self.find_reservation(week_id, title, reservation.amount, due_date) or reservation

    def update_reservation_status(self, reservation_id: str, status: str) -> ReservedFund:
        if status not in RESERVATION_STATUSES:
            raise ValueError(f"Invalid reservation status: {status}")
        now = utc_now()
        with self.connect() as conn:
            cursor = conn.execute(
                "UPDATE weekly_cash_reservations SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, reservation_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"Reservation was not found: {reservation_id}")
            row = conn.execute("SELECT * FROM weekly_cash_reservations WHERE id = ?", (reservation_id,)).fetchone()
        return reservation_from_row(row)

    def reservations_for_week(self, week_id: str) -> list[ReservedFund]:
        self.initialize()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM weekly_cash_reservations
                WHERE week_id = ?
                ORDER BY priority ASC, due_date ASC, title ASC
                """,
                (week_id,),
            ).fetchall()
        return [reservation_from_row(row) for row in rows]

    def find_reservation(
        self,
        week_id: str,
        title: str,
        amount: Decimal,
        due_date: date | None,
    ) -> ReservedFund | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM weekly_cash_reservations
                WHERE week_id = ? AND lower(title) = lower(?) AND amount = ? AND due_date IS ?
                """,
                (week_id, title, str(money(amount)), due_date.isoformat() if due_date else None),
            ).fetchone()
        return reservation_from_row(row) if row else None

    def update_jim_remit_status(self, week_id: str, status: str, paid_at: str | None = None) -> WeeklyCashPlan:
        if status not in JIM_REMIT_STATUSES:
            raise ValueError(f"Invalid Jim Remit status: {status}")
        now = utc_now()
        stored_paid_at = paid_at if status == "Paid" else None
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE weekly_cash_plans
                SET jim_remit_status = ?, jim_remit_paid_at = ?, updated_at = ?
                WHERE week_id = ?
                """,
                (status, stored_paid_at, now, week_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"Weekly cash plan was not found: {week_id}")
            row = conn.execute("SELECT * FROM weekly_cash_plans WHERE week_id = ?", (week_id,)).fetchone()
        return plan_from_row(row)


class WeeklyCashPlannerService:
    def __init__(self, planner_db_path: Path, remit_db_path: Path) -> None:
        self.db = WeeklyCashPlannerDatabase(planner_db_path)
        self.remit_db = ICRRemitDatabase(remit_db_path)

    def create_plan_from_latest_remit(
        self,
        operating_deficit: Decimal = Decimal("0"),
        today: date | None = None,
    ) -> WeeklyCashPlan:
        remit = self.latest_validated_remit(active_business_week(today))
        if remit is None:
            raise RuntimeError(
                f"No finalized ICR remit import is available for week {active_business_week(today).isoformat()}."
            )
        return self.db.save_plan_from_remit(remit, operating_deficit)

    def create_plan_from_remit(
        self,
        remit: ICRRemitResult,
        operating_deficit: Decimal = Decimal("0"),
    ) -> WeeklyCashPlan:
        return self.db.save_plan_from_remit(remit, operating_deficit)

    def record_already_sent_remit(
        self,
        week_start: date,
        weekly_remit: Decimal,
        jim_remit: Decimal,
        operating_deficit: Decimal = Decimal("0"),
    ) -> WeeklyCashPlan:
        remit = ICRRemitResult(
            broker="ICR",
            contact="Jim",
            remit_week=week_start,
            week_ending=week_start + timedelta(days=6),
            file_path=Path(f"manual-already-sent-{week_start.isoformat()}"),
            due_to_agency=money(weekly_remit - jim_remit),
            due_to_client=money(jim_remit),
            total_collected=money(weekly_remit),
            status="Finalized",
            notes="Manual local record for an already-sent remit. No email was sent.",
        )
        self.remit_db.initialize()
        if not self.remit_db.import_exists(remit.broker, remit.remit_week.isoformat(), remit.file_path.name):
            self.remit_db.save_import(remit)
        return self.db.save_plan_from_remit(remit, operating_deficit)

    def latest_validated_remit(self, week_start: date) -> ICRRemitResult | None:
        imports = self.remit_db.list_imports()
        for remit in imports:
            if remit.remit_week == week_start and is_validated_remit_status(remit.status):
                return remit
        return None

    def mark_current_week_jim_remit_paid(self, today: date | None = None, paid_at: str | None = None) -> WeeklyCashPlan:
        plan = self.db.open_plan_for_week(active_business_week(today))
        if plan is None:
            raise RuntimeError(f"No open weekly cash plan exists for week {active_business_week(today).isoformat()}.")
        return self.db.update_jim_remit_status(plan.week_id, "Paid", paid_at=paid_at or utc_now())

    def snapshot(self, bills: list[dict] | None = None, today: date | None = None) -> WeeklyCashPlannerSnapshot:
        plan = self.db.open_plan_for_week(active_business_week(today))
        if plan is None:
            return WeeklyCashPlannerSnapshot(
                plan=None,
                reservations=(),
                operating_cash=Decimal("0.00"),
                reserved_cash=Decimal("0.00"),
                spendable_cash=Decimal("0.00"),
                next_expected_remit=None,
                bills_due_before_next_remit=(),
                bills_due_after_next_remit=(),
                recommendations=(f"Create a weekly plan after the validated remit for week {active_business_week(today).isoformat()}.",),
            )
        reservations = tuple(self.db.reservations_for_week(plan.week_id))
        reserved_cash = money(sum((item.amount for item in reservations if item.reduces_spendable_cash), Decimal("0")))
        spendable_cash = money(plan.operating_cash - reserved_cash)
        next_expected_remit = plan.week_end + timedelta(days=7)
        before, after = split_upcoming_bills(bills or [], next_expected_remit)
        return WeeklyCashPlannerSnapshot(
            plan=plan,
            reservations=reservations,
            operating_cash=plan.operating_cash,
            reserved_cash=reserved_cash,
            spendable_cash=spendable_cash,
            next_expected_remit=next_expected_remit,
            bills_due_before_next_remit=tuple(before),
            bills_due_after_next_remit=tuple(after),
            recommendations=recommendations(spendable_cash, before),
        )

    def jason_snapshot(self, bills: list[dict] | None = None, today: date | None = None) -> dict:
        snapshot = self.snapshot(bills, today=today)
        data = snapshot.to_dict()
        data["top_reservations"] = data["reservations"][:5]
        return data


def split_upcoming_bills(rows: list[dict], next_expected_remit: date) -> tuple[list[dict], list[dict]]:
    unpaid = [
        row for row in rows
        if str(row.get("status", "")).strip().lower() != "paid" and isinstance(row.get("due_date"), date)
    ]
    before = sorted([row for row in unpaid if row["due_date"] <= next_expected_remit], key=lambda row: row["due_date"])
    after = sorted([row for row in unpaid if row["due_date"] > next_expected_remit], key=lambda row: row["due_date"])
    return before, after


def recommendations(spendable_cash: Decimal, bills_due_before_next_remit: list[dict]) -> tuple[str, ...]:
    if spendable_cash < 0:
        return ("Reserved obligations exceed operating cash.",)
    if bills_due_before_next_remit:
        return ("Review bills due before the next remit before releasing discretionary cash.",)
    return ("No required bills are due before the next remit.",)


def active_business_week(today: date | None = None) -> date:
    value = today or date.today()
    return value - timedelta(days=value.weekday())


def is_validated_remit_status(status: str) -> bool:
    return status.strip().lower() not in {"failed", "duplicate", "cancelled", "error"}


def validate_reservation(category: str, status: str) -> None:
    if category not in RESERVATION_CATEGORIES:
        raise ValueError(f"Invalid reservation category: {category}")
    if status not in RESERVATION_STATUSES:
        raise ValueError(f"Invalid reservation status: {status}")


def plan_from_row(row) -> WeeklyCashPlan:
    return WeeklyCashPlan(
        week_id=row["week_id"],
        week_start=date.fromisoformat(row["week_start"]),
        week_end=date.fromisoformat(row["week_end"]),
        weekly_remit_amount=Decimal(row["weekly_remit_amount"]),
        jim_remit_amount=Decimal(row["jim_remit_amount"]),
        jim_remit_status=normalize_jim_remit_status(row["jim_remit_status"] if "jim_remit_status" in row.keys() else None),
        jim_remit_paid_at=row["jim_remit_paid_at"] if "jim_remit_paid_at" in row.keys() else None,
        operating_deficit=Decimal(row["operating_deficit"]),
        remit_status=row["remit_status"],
        remit_source=row["remit_source"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def reservation_from_row(row) -> ReservedFund:
    due_date = row["due_date"]
    return ReservedFund(
        id=row["id"],
        week_id=row["week_id"],
        title=row["title"],
        category=row["category"],
        amount=Decimal(row["amount"]),
        due_date=date.fromisoformat(due_date) if due_date else None,
        priority=int(row["priority"]),
        status=row["status"],
        funding_source=row["funding_source"],
        notes=row["notes"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def plan_to_dict(plan: WeeklyCashPlan) -> dict:
    return {
        "week_id": plan.week_id,
        "week_start": plan.week_start.isoformat(),
        "week_end": plan.week_end.isoformat(),
        "weekly_remit_amount": format_money(plan.weekly_remit_amount),
        "jim_remit_amount": format_money(plan.jim_remit_amount),
        "jim_remit_status": plan.jim_remit_status,
        "jim_remit_paid_at": plan.jim_remit_paid_at or "",
        "operating_deficit": format_money(plan.operating_deficit),
        "operating_cash": format_money(plan.operating_cash),
        "remit_status": plan.remit_status,
        "remit_source": plan.remit_source,
        "status": plan.status,
        "created_at": plan.created_at,
        "updated_at": plan.updated_at,
    }


def reservation_to_dict(reservation: ReservedFund) -> dict:
    return {
        "id": reservation.id,
        "week_id": reservation.week_id,
        "title": reservation.title,
        "category": reservation.category,
        "amount": format_money(reservation.amount),
        "due_date": reservation.due_date.isoformat() if reservation.due_date else "",
        "priority": reservation.priority,
        "status": reservation.status,
        "funding_source": reservation.funding_source,
        "notes": reservation.notes,
        "created_at": reservation.created_at,
        "updated_at": reservation.updated_at,
    }


def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def normalize_jim_remit_status(value: str | None) -> str:
    status = str(value or "").strip()
    if not status:
        return "Open"
    if status not in JIM_REMIT_STATUSES:
        return "Open"
    return status


def format_money(value: Decimal) -> str:
    return f"${value:,.2f}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
