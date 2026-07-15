from __future__ import annotations

import logging
import re
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from .config import CashFlowHQSettings
from .models import BillCandidate, BillEmail, CashFlowBillRecord, PaymentConfirmation, VendorRule
from .notion_client import NotionClient
from .schema import (
    DUE_STATUS_PROPERTY_NAME,
    ACTION_REQUIRED_PROPERTY_NAME,
    ACTION_REQUIRED_FALLBACK_FORMULA,
    PAYMENT_TYPE_OPTIONS,
    VENDOR_RULE_DATABASE_NAME,
    VENDOR_RULE_SEEDS,
    action_required_formula_diagnostics,
    build_action_required_diagnostic_formulas,
    build_action_required_formula,
    build_database_payload,
    build_properties,
    due_status_property,
    build_vendor_rules_database_payload,
    build_view_payload,
    build_view_specs,
    extract_data_source_id,
)

LOGGER = logging.getLogger(__name__)


class CashFlowHQService:
    def __init__(self, settings: CashFlowHQSettings, notion: NotionClient | None = None) -> None:
        self.settings = settings
        self.notion = notion or NotionClient(settings.notion_api_key, settings.notion_version)

    def build_payload_preview(self) -> dict[str, Any]:
        return {
            "database": build_database_payload(
                self.settings.notion_parent_page_id,
                self.settings.database_name,
            ),
            "views": [spec.name for spec in build_view_specs()],
        }

    def list_data_source_metadata(self) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        seen: set[str] = set()
        for query in (self.settings.database_name, VENDOR_RULE_DATABASE_NAME):
            response = self.notion.request(
                "POST",
                "/search",
                json={
                    "query": query,
                    "filter": {"property": "object", "value": "data_source"},
                    "page_size": 25,
                },
            )
            for result in response.get("results", []):
                data_source_id = result.get("id", "")
                if not data_source_id or data_source_id in seen:
                    continue
                seen.add(data_source_id)
                parent = result.get("parent", {})
                rows.append(
                    {
                        "title": plain_title(result.get("title", [])),
                        "data_source_id": data_source_id,
                        "parent_type": parent.get("type", ""),
                        "parent_id": parent.get("database_id") or parent.get("page_id") or "",
                    }
                )
        return rows

    def ensure_foundation(self) -> dict[str, Any]:
        foundation = self.find_cash_flow_foundation()
        created = False
        if foundation is None:
            database = self.create_database()
            created = True
            foundation = self.foundation_from_database(database)
        database_id = foundation["database_id"]
        data_source_id = foundation["data_source_id"]
        self.ensure_due_status_property(data_source_id)
        self.ensure_action_required_property(data_source_id)
        created_views = self.ensure_views(database_id, data_source_id)
        vendor_rules_foundation = self.ensure_vendor_rules_foundation()
        return {
            "database_id": database_id,
            "data_source_id": data_source_id,
            "database_created": created,
            "views_created": created_views,
            "vendor_rules_foundation": vendor_rules_foundation,
        }

    def ensure_runtime_foundation(self) -> dict[str, Any]:
        foundation = self.find_cash_flow_foundation()
        if foundation is None:
            return self.ensure_foundation()
        data_source_id = foundation["data_source_id"]
        self.ensure_due_status_property(data_source_id)
        self.ensure_action_required_property(data_source_id)
        vendor_rules_foundation = self.get_existing_vendor_rules_foundation()
        if vendor_rules_foundation is None:
            vendor_rules_foundation = self.ensure_vendor_rules_foundation()
        return {
            "database_id": foundation["database_id"],
            "data_source_id": data_source_id,
            "database_created": False,
            "views_created": [],
            "vendor_rules_foundation": vendor_rules_foundation,
        }

    def get_existing_foundation(self) -> dict[str, str]:
        foundation = self.find_cash_flow_foundation()
        if foundation is None:
            raise RuntimeError(f"Notion database not found: {self.settings.database_name}")
        return foundation

    def find_cash_flow_foundation(self) -> dict[str, str] | None:
        data_source_id = getattr(self.settings, "cash_flow_data_source_id", "")
        if data_source_id:
            return self.foundation_from_data_source(self.retrieve_data_source(data_source_id))
        return self.find_foundation_by_name(self.settings.database_name)

    def find_database_by_name(self, database_name: str) -> dict[str, Any] | None:
        foundation = self.find_foundation_by_name(database_name)
        if not foundation:
            return None
        return self.retrieve_database(foundation["database_id"])

    def find_foundation_by_name(self, database_name: str) -> dict[str, str] | None:
        response = self.notion.request(
            "POST",
            "/search",
            json={
                "query": database_name,
                "filter": {"property": "object", "value": "data_source"},
                "page_size": 10,
            },
        )
        for result in response.get("results", []):
            title = plain_title(result.get("title", []))
            if title == database_name:
                LOGGER.info("Found existing Notion database: %s", database_name)
                return self.foundation_from_data_source(result)
        return None

    def foundation_from_data_source(self, data_source: dict[str, Any]) -> dict[str, str]:
        database_id = (
            data_source.get("parent", {}).get("database_id")
            or data_source.get("database_id")
            or data_source.get("database", {}).get("id")
        )
        if not database_id:
            raise RuntimeError("Notion data source search result did not include a parent database_id.")
        return {
            "database_id": database_id,
            "data_source_id": data_source["id"],
        }

    def foundation_from_database(self, database: dict[str, Any]) -> dict[str, str]:
        database_id = database["id"]
        if not database.get("data_sources"):
            database = self.retrieve_database(database_id)
        return {
            "database_id": database_id,
            "data_source_id": extract_data_source_id(database),
        }

    def create_database(self) -> dict[str, Any]:
        payload = build_database_payload(
            self.settings.notion_parent_page_id,
            self.settings.database_name,
        )
        database = self.notion.request("POST", "/databases", json=payload)
        LOGGER.info("Created Notion database: %s", self.settings.database_name)
        return database

    def ensure_due_status_property(self, data_source_id: str) -> None:
        data_source = self.notion.request("GET", f"/data_sources/{data_source_id}")
        existing = data_source.get("properties", {}).get(DUE_STATUS_PROPERTY_NAME)
        desired = due_status_property()
        if existing and existing.get("formula", {}).get("expression") == desired["formula"]["expression"]:
            return
        self.notion.request(
            "PATCH",
            f"/data_sources/{data_source_id}",
            json={"properties": {DUE_STATUS_PROPERTY_NAME: desired}},
        )
        LOGGER.info("Updated Notion property: %s", DUE_STATUS_PROPERTY_NAME)

    def ensure_action_required_property(self, data_source_id: str) -> None:
        data_source = self.notion.request("GET", f"/data_sources/{data_source_id}")
        existing = data_source.get("properties", {}).get(ACTION_REQUIRED_PROPERTY_NAME)
        desired = {"formula": {"expression": build_action_required_formula(data_source.get("properties", {}))}}
        if existing and existing.get("formula", {}).get("expression") == desired["formula"]["expression"]:
            return
        self.patch_action_required_formula(data_source_id, desired["formula"]["expression"])

    def patch_action_required_formula(self, data_source_id: str, expression: str | None = None) -> str:
        if expression is None:
            data_source = self.notion.request("GET", f"/data_sources/{data_source_id}")
            primary = build_action_required_formula(data_source.get("properties", {}))
        else:
            primary = expression
        try:
            self.notion.request(
                "PATCH",
                f"/data_sources/{data_source_id}",
                json={"properties": {ACTION_REQUIRED_PROPERTY_NAME: {"formula": {"expression": primary}}}},
            )
            LOGGER.info("Updated Notion property: %s", ACTION_REQUIRED_PROPERTY_NAME)
            return primary
        except RuntimeError as exc:
            LOGGER.warning("Notion rejected full Action Required formula: %s", exc)
            self.notion.request(
                "PATCH",
                f"/data_sources/{data_source_id}",
                json={
                    "properties": {
                        ACTION_REQUIRED_PROPERTY_NAME: {
                            "formula": {"expression": ACTION_REQUIRED_FALLBACK_FORMULA}
                        }
                    }
                },
            )
            LOGGER.info("Updated Notion property with fallback formula: %s", ACTION_REQUIRED_PROPERTY_NAME)
            return ACTION_REQUIRED_FALLBACK_FORMULA

    def ensure_payment_confirmation_properties(self, data_source_id: str) -> None:
        data_source = self.notion.request("GET", f"/data_sources/{data_source_id}")
        existing = data_source.get("properties", {})
        all_properties = build_properties()
        names = [
            "Payment Source",
            "Payment Confirmation Subject",
            "Confirmation Link",
            "Payment Method",
            "Invoice Number",
        ]
        missing = {name: all_properties[name] for name in names if name not in existing}
        if not missing:
            return
        self.notion.request("PATCH", f"/data_sources/{data_source_id}", json={"properties": missing})
        LOGGER.info("Updated Notion payment confirmation properties: %s", ", ".join(missing))

    def action_required_formula_debug(self, data_source_id: str) -> dict[str, Any]:
        data_source = self.notion.request("GET", f"/data_sources/{data_source_id}")
        return action_required_formula_diagnostics(data_source.get("properties", {}))

    def action_required_formula_diagnostic_steps(self, data_source_id: str) -> list[tuple[str, str]]:
        data_source = self.notion.request("GET", f"/data_sources/{data_source_id}")
        return build_action_required_diagnostic_formulas(data_source.get("properties", {}))

    def action_required_property_schema(self, data_source_id: str) -> dict[str, Any]:
        data_source = self.notion.request("GET", f"/data_sources/{data_source_id}")
        return data_source.get("properties", {}).get(ACTION_REQUIRED_PROPERTY_NAME, {})

    def action_required_formula_patch_body(self, expression: str) -> dict[str, Any]:
        return {
            "properties": {
                ACTION_REQUIRED_PROPERTY_NAME: {
                    "formula": {"expression": expression}
                }
            }
        }

    def patch_action_required_formula_diagnostic_step(
        self,
        data_source_id: str,
        expression: str,
    ) -> Any:
        return self.notion.request(
            "PATCH",
            f"/data_sources/{data_source_id}",
            json=self.action_required_formula_patch_body(expression),
        )

    def diagnose_action_required_formula_patches(self, data_source_id: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for index, (label, expression) in enumerate(
            self.action_required_formula_diagnostic_steps(data_source_id),
            start=1,
        ):
            try:
                response = self.patch_action_required_formula_diagnostic_step(data_source_id, expression)
            except RuntimeError as exc:
                results.append(
                    {
                        "step": index,
                        "label": label,
                        "status": "FAIL",
                        "formula": expression,
                        "patch_body": self.action_required_formula_patch_body(expression),
                        "response": str(exc),
                    }
                )
                continue
            results.append(
                {
                    "step": index,
                    "label": label,
                    "status": "PASS",
                    "formula": expression,
                    "patch_body": self.action_required_formula_patch_body(expression),
                    "response": response,
                }
            )
        return results

    def ensure_vendor_rules_foundation(self) -> dict[str, str]:
        foundation = self.get_existing_vendor_rules_foundation()
        if foundation is None:
            database = self.notion.request(
                "POST",
                "/databases",
                json=build_vendor_rules_database_payload(self.settings.notion_parent_page_id),
            )
            foundation = self.foundation_from_database(database)
            LOGGER.info("Created Notion database: %s", VENDOR_RULE_DATABASE_NAME)
        self.ensure_vendor_rule_properties(foundation["data_source_id"])
        self.ensure_vendor_rule_seeds(foundation["data_source_id"])
        return foundation

    def ensure_vendor_rule_properties(self, data_source_id: str) -> None:
        data_source = self.notion.request("GET", f"/data_sources/{data_source_id}")
        existing = data_source.get("properties", {})
        desired = build_vendor_rules_database_payload(self.settings.notion_parent_page_id)["initial_data_source"]["properties"]
        missing = {name: prop for name, prop in desired.items() if name not in existing}
        if missing:
            self.notion.request("PATCH", f"/data_sources/{data_source_id}", json={"properties": missing})
            LOGGER.info("Updated Vendor Rules properties: %s", ", ".join(sorted(missing)))
        foundation = self.foundation_from_data_source(data_source)
        self.ensure_vendor_rules_view(foundation["database_id"], data_source_id)

    def ensure_vendor_rules_view(self, database_id: str, data_source_id: str) -> None:
        view_name = "Recurring Vendors"
        existing = self.list_views(database_id)
        if any(view.get("name") == view_name for view in existing):
            return
        self.notion.request(
            "POST",
            "/views",
            json={
                "database_id": database_id,
                "data_source_id": data_source_id,
                "name": view_name,
                "type": "table",
                "filter": {"property": "Active", "checkbox": {"equals": True}},
                "sorts": [{"property": "Vendor Name", "direction": "ascending"}],
                "configuration": {"type": "table", "wrap_cells": True},
            },
        )
        LOGGER.info("Created Notion view: %s", view_name)

    def get_existing_vendor_rules_foundation(self) -> dict[str, str] | None:
        data_source_id = getattr(self.settings, "vendor_rules_data_source_id", "")
        if data_source_id:
            return self.foundation_from_data_source(self.retrieve_data_source(data_source_id))
        return self.find_foundation_by_name(VENDOR_RULE_DATABASE_NAME)

    def ensure_vendor_rule_seeds(self, data_source_id: str) -> None:
        existing = {
            rule.match_text.strip().lower(): rule
            for rule in self.list_vendor_rules(data_source_id, active_only=False)
        }
        for seed in VENDOR_RULE_SEEDS:
            match_key = seed["match_text"].strip().lower()
            if match_key in existing:
                self.update_vendor_rule(existing[match_key], VendorRule(**seed))
                continue
            self.create_vendor_rule(data_source_id, VendorRule(**seed))
            LOGGER.info("Seeded Vendor Rule: %s", seed["vendor_name"])

    def update_vendor_rule(self, existing: VendorRule, rule: VendorRule) -> None:
        page_id = getattr(existing, "page_id", "")
        if not page_id:
            return
        self.notion.request("PATCH", f"/pages/{page_id}", json={"properties": self.vendor_rule_properties(rule)})
        LOGGER.info("Updated Vendor Rule: %s", rule.vendor_name)

    def create_vendor_rule(self, data_source_id: str, rule: VendorRule) -> dict[str, Any]:
        return self.notion.request(
            "POST",
            "/pages",
            json={
                "parent": {"data_source_id": data_source_id},
                "properties": self.vendor_rule_properties(rule),
            },
        )

    def vendor_rule_properties(self, rule: VendorRule) -> dict[str, Any]:
        properties: dict[str, Any] = {
            "Vendor Name": title_property(rule.vendor_name),
            "Match Text": rich_text_property(rule.match_text),
            "Display Name": rich_text_property(rule.display_name or ""),
            "Default Status": {"select": {"name": rule.default_status}},
            "Active": {"checkbox": rule.active},
            "Notes": rich_text_property(rule.notes),
        }
        if rule.category:
            properties["Category"] = {"select": {"name": rule.category}}
        if rule.frequency:
            properties["Frequency"] = {"select": {"name": rule.frequency}}
        if rule.due_day is not None:
            properties["Due Day"] = {"number": rule.due_day}
        if rule.payment_type:
            properties["Payment Type"] = {"select": {"name": rule.payment_type}}
        if rule.service:
            properties["Service"] = rich_text_property(rule.service)
        if rule.invoice_day is not None:
            properties["Invoice Day"] = {"number": rule.invoice_day}
        if rule.pay_by_day is not None:
            properties["Pay By Day"] = {"number": rule.pay_by_day}
        if rule.grace_period_days is not None:
            properties["Grace Period Days"] = {"number": rule.grace_period_days}
        if rule.auto_pay is not None:
            properties["AutoPay"] = {"checkbox": rule.auto_pay}
        if rule.critical is not None:
            properties["Critical"] = {"checkbox": rule.critical}
        if rule.typical_amount is not None:
            properties["Typical Amount"] = {"number": float(rule.typical_amount)}
        if rule.billing_model:
            properties["Billing Model"] = rich_text_property(rule.billing_model)
        if rule.rate_per_user is not None:
            properties["Rate Per User"] = {"number": float(rule.rate_per_user)}
        if rule.current_user_count is not None:
            properties["Current User Count"] = {"number": rule.current_user_count}
        if rule.monthly_server_fee is not None:
            properties["Monthly Server Fee"] = {"number": float(rule.monthly_server_fee)}
        if rule.provider_group:
            properties["Provider Group"] = rich_text_property(rule.provider_group)
        return properties

    def list_vendor_rules(self, data_source_id: str, active_only: bool = True) -> list[VendorRule]:
        response = self.notion.request("POST", f"/data_sources/{data_source_id}/query", json={"page_size": 100})
        rules = [vendor_rule_from_page(page) for page in response.get("results", [])]
        if active_only:
            return [rule for rule in rules if rule.active]
        return rules

    def list_cash_flow_bills(self, data_source_id: str) -> list[CashFlowBillRecord]:
        response = self.notion.request("POST", f"/data_sources/{data_source_id}/query", json={"page_size": 100})
        return [cash_flow_bill_from_page(page) for page in response.get("results", [])]

    def mark_bill_paid_from_confirmation(
        self,
        bill: CashFlowBillRecord,
        confirmation: PaymentConfirmation,
        payment_method: str,
    ) -> None:
        if confirmation.received_date is None:
            raise RuntimeError("Payment confirmation is missing received date.")
        self.notion.request(
            "PATCH",
            f"/pages/{bill.page_id}",
            json={
                "properties": {
                    "Status": {"select": {"name": "Paid"}},
                    "Payment Date": {"date": {"start": confirmation.received_date.isoformat()}},
                    "Payment Source": {"select": {"name": "Email"}},
                    "Payment Confirmation Subject": rich_text_property(confirmation.subject),
                    "Confirmation Link": {"url": confirmation.email_link},
                    "Payment Method": {"select": {"name": payment_method}},
                }
            },
        )

    def mark_bill_paid_manually(
        self,
        page_id: str,
        payment_date: date,
        payment_method: str = "Manual",
        confirmation_link: str | None = None,
        confirmation_subject: str | None = None,
    ) -> None:
        properties: dict[str, Any] = {
            "Status": {"select": {"name": "Paid"}},
            "Payment Date": {"date": {"start": payment_date.isoformat()}},
            "Payment Source": {"select": {"name": "Email" if confirmation_link else "Manual"}},
            "Payment Method": {"select": {"name": payment_method}},
        }
        if confirmation_link:
            properties["Confirmation Link"] = {"url": confirmation_link}
        if confirmation_subject:
            properties["Payment Confirmation Subject"] = rich_text_property(confirmation_subject)
        self.notion.request("PATCH", f"/pages/{page_id}", json={"properties": properties})

    def update_bill_fields(
        self,
        page_id: str,
        expense_name: str | None = None,
        vendor_payee: str | None = None,
        amount: Decimal | None = None,
        due_date_value: date | None = None,
        status: str | None = None,
        category: str | None = None,
        payment_type: str | None = None,
        frequency: str | None = None,
        notes: str | None = None,
    ) -> None:
        properties: dict[str, Any] = {}
        if expense_name:
            properties["Expense Name"] = title_property(expense_name)
        if vendor_payee:
            properties["Vendor / Payee"] = rich_text_property(vendor_payee)
        if amount is not None:
            properties["Amount"] = {"number": float(amount)}
        if due_date_value is not None:
            properties["Due Date"] = {"date": {"start": due_date_value.isoformat()}}
        if status:
            properties["Status"] = {"select": {"name": status}}
        if category:
            properties["Category"] = {"select": {"name": category}}
        if payment_type:
            properties["Payment Type"] = {"select": {"name": payment_type}}
        if frequency:
            properties["Frequency"] = {"select": {"name": frequency}}
        if notes is not None:
            properties["Notes"] = rich_text_property(notes)
        if not properties:
            raise RuntimeError("No Cash Flow HQ bill fields were provided to update.")
        self.notion.request("PATCH", f"/pages/{page_id}", json={"properties": properties})

    def apply_vendor_rules(
        self,
        candidate: BillCandidate,
        message: BillEmail,
        rules: list[VendorRule],
    ) -> BillCandidate:
        rule = match_vendor_rule(candidate, message, rules)
        if not rule:
            return candidate
        LOGGER.info("Vendor Rules matched: %s", rule.vendor_name)
        updates: dict[str, Any] = {}
        field_sources = dict(candidate.field_sources)
        review_reasons = tuple(candidate.review_reasons)
        display_name = rule.display_name or rule.vendor_name
        if display_name and candidate.vendor_payee != display_name:
            updates["vendor_payee"] = display_name
            field_sources["vendor"] = "vendor rules"
        if rule.category and (not candidate.category or (candidate.status == "Needs Review" and candidate.category != rule.category)):
            updates["category"] = rule.category
            field_sources["category"] = "vendor rules"
            LOGGER.info("Filled Category from Vendor Rules")
        if not candidate.frequency and rule.frequency:
            updates["frequency"] = rule.frequency
            field_sources["frequency"] = "vendor rules"
            LOGGER.info("Filled Frequency from Vendor Rules")
        if not candidate.payment_type and rule.payment_type in PAYMENT_TYPE_OPTIONS:
            updates["payment_type"] = rule.payment_type
            field_sources["payment_type"] = "vendor rules"
            LOGGER.info("Filled Payment Type from Vendor Rules")
        payment_day = rule.pay_by_day or rule.due_day
        if candidate.due_date is None and payment_day:
            generated = due_date_from_rule(payment_day, message)
            if generated:
                updates["due_date"] = generated
                field_sources["due_date"] = "vendor rules"
                review_reasons = tuple(reason for reason in review_reasons if reason != "missing due date")
                LOGGER.info("Filled Due Date from Vendor Rules")
        review_reasons = add_expected_amount_reason(
            candidate.amount,
            expected_amount_for_rule(rule),
            review_reasons,
        )
        status = "Upcoming" if candidate.amount is not None and (updates.get("due_date") or candidate.due_date) and not review_reasons else "Needs Review"
        if status != candidate.status:
            updates["status"] = status
            updates["confidence"] = "high" if status == "Upcoming" else "low"
        if updates:
            updates["field_sources"] = field_sources
            updates["review_reasons"] = review_reasons
            updates["notes"] = format_business_notes(review_reasons)
            return replace(candidate, **updates)
        return candidate

    def retrieve_database(self, database_id: str) -> dict[str, Any]:
        return self.notion.request("GET", f"/databases/{database_id}")

    def retrieve_data_source(self, data_source_id: str) -> dict[str, Any]:
        return self.notion.request("GET", f"/data_sources/{data_source_id}")

    def ensure_views(self, database_id: str, data_source_id: str) -> list[str]:
        existing = self.list_views(database_id)
        existing_names = {view.get("name") for view in existing if view.get("name")}
        created: list[str] = []
        for spec in build_view_specs():
            payload = build_view_payload(database_id, data_source_id, spec)
            existing_view = next((view for view in existing if view.get("name") == spec.name), None)
            if spec.name == "Dashboard" and existing_view:
                self.update_view(existing_view["id"], payload)
                continue
            if spec.name in existing_names:
                continue
            default_view = next((view for view in existing if view.get("name") == "Default view"), None)
            if spec.name == "Dashboard" and default_view:
                self.update_view(default_view["id"], payload)
                default_view["name"] = spec.name
                existing_names.add(spec.name)
            else:
                self.notion.request("POST", "/views", json=payload)
                existing_names.add(spec.name)
            created.append(spec.name)
            LOGGER.info("Created Notion view: %s", spec.name)
        return created

    def update_view(self, view_id: str, payload: dict[str, Any]) -> None:
        update_payload = {
            key: value
            for key, value in payload.items()
            if key not in {"database_id", "data_source_id"}
        }
        try:
            self.notion.request("PATCH", f"/views/{view_id}", json=update_payload)
        except RuntimeError as exc:
            properties = update_payload.get("configuration", {}).pop("properties", None)
            if not properties:
                raise
            LOGGER.warning("Notion view property visibility update was not accepted; retrying layout update without hidden properties. %s", exc)
            self.notion.request("PATCH", f"/views/{view_id}", json=update_payload)

    def list_views(self, database_id: str) -> list[dict[str, Any]]:
        response = self.notion.request("GET", f"/views?database_id={database_id}")
        views: list[dict[str, Any]] = []
        for item in response.get("results", []):
            view = self.notion.request("GET", f"/views/{item['id']}")
            views.append(view)
        return views

    def email_bill_exists(self, data_source_id: str, candidate: BillCandidate) -> bool:
        if candidate.email_link and self._query_by_email_link(data_source_id, candidate.email_link):
            return True
        if not candidate.has_duplicate_key:
            return False
        response = self.notion.request(
            "POST",
            f"/data_sources/{data_source_id}/query",
            json={
                "filter": {
                    "and": [
                        {"property": "Amount", "number": {"equals": float(candidate.amount or Decimal("0"))}},
                        {"property": "Due Date", "date": {"equals": candidate.due_date.isoformat()}},
                    ]
                },
                "page_size": 25,
            },
        )
        vendor = candidate.vendor_payee.strip().lower()
        for page in response.get("results", []):
            properties = page.get("properties", {})
            existing_vendor = plain_rich_text(properties.get("Vendor / Payee", {})).strip().lower()
            if existing_vendor == vendor:
                return True
        return False

    def _query_by_email_link(self, data_source_id: str, email_link: str) -> bool:
        response = self.notion.request(
            "POST",
            f"/data_sources/{data_source_id}/query",
            json={
                "filter": {"property": "Email Link", "url": {"equals": email_link}},
                "page_size": 1,
            },
        )
        return bool(response.get("results"))

    def create_email_bill(self, data_source_id: str, candidate: BillCandidate) -> dict[str, Any]:
        return self.notion.request(
            "POST",
            "/pages",
            json={
                "parent": {"data_source_id": data_source_id},
                "properties": self.create_bill_properties(candidate),
            },
        )

    def create_bill_properties(self, candidate: BillCandidate) -> dict[str, Any]:
        properties = {
            "Expense Name": title_property(candidate.expense_name),
            "Vendor / Payee": rich_text_property(candidate.vendor_payee),
            "Status": {"select": {"name": candidate.status}},
            "Source": {"select": {"name": "Email"}},
            "Notes": rich_text_property(candidate.notes),
        }
        if candidate.amount is not None:
            properties["Amount"] = {"number": float(candidate.amount)}
        if candidate.due_date is not None:
            properties["Due Date"] = {"date": {"start": candidate.due_date.isoformat()}}
        if candidate.payment_type:
            properties["Payment Type"] = {"select": {"name": candidate.payment_type}}
        if candidate.category:
            properties["Category"] = {"select": {"name": candidate.category}}
        if candidate.frequency:
            properties["Frequency"] = {"select": {"name": candidate.frequency}}
        if candidate.email_link:
            properties["Email Link"] = {"url": candidate.email_link}
        return properties

    def create_manual_expense_payload(
        self,
        expense_name: str,
        amount: float,
        due_date: str,
        vendor_payee: str = "",
        category: str = "Miscellaneous",
        source: str = "Manual",
    ) -> dict[str, Any]:
        return {
            "Expense Name": {
                "title": [{"type": "text", "text": {"content": expense_name}}],
            },
            "Vendor / Payee": {
                "rich_text": [{"type": "text", "text": {"content": vendor_payee}}],
            },
            "Category": {"select": {"name": category}},
            "Amount": {"number": amount},
            "Due Date": {"date": {"start": due_date}},
            "Status": {"select": {"name": "Upcoming"}},
            "Payment Type": {"select": {"name": "Manual"}},
            "Source": {"select": {"name": source}},
        }


def plain_title(title: list[dict[str, Any]]) -> str:
    return "".join(part.get("plain_text", "") for part in title)


def plain_rich_text(property_value: dict[str, Any]) -> str:
    return "".join(part.get("plain_text", "") for part in property_value.get("rich_text", []))


def title_property(value: str) -> dict[str, Any]:
    return {"title": [{"type": "text", "text": {"content": value[:2000]}}]}


def rich_text_property(value: str) -> dict[str, Any]:
    if not value:
        return {"rich_text": []}
    return {"rich_text": [{"type": "text", "text": {"content": value[:2000]}}]}


def vendor_rule_from_page(page: dict[str, Any]) -> VendorRule:
    properties = page.get("properties", {})
    return VendorRule(
        vendor_name=plain_title(properties.get("Vendor Name", {}).get("title", [])),
        match_text=plain_rich_text(properties.get("Match Text", {})),
        display_name=plain_rich_text(properties.get("Display Name", {})) or None,
        category=select_name(properties.get("Category", {})),
        frequency=select_name(properties.get("Frequency", {})),
        due_day=number_value(properties.get("Due Day", {})),
        payment_type=select_name(properties.get("Payment Type", {})),
        default_status=select_name(properties.get("Default Status", {})) or "Upcoming",
        active=bool(properties.get("Active", {}).get("checkbox", False)),
        notes=plain_rich_text(properties.get("Notes", {})),
        service=plain_rich_text(properties.get("Service", {})) or None,
        invoice_day=number_value(properties.get("Invoice Day", {})),
        pay_by_day=number_value(properties.get("Pay By Day", {})),
        grace_period_days=number_value(properties.get("Grace Period Days", {})),
        auto_pay=checkbox_value(properties.get("AutoPay", {})),
        critical=checkbox_value(properties.get("Critical", {})),
        typical_amount=decimal_number_value(properties.get("Typical Amount", {})),
        billing_model=plain_rich_text(properties.get("Billing Model", {})) or None,
        rate_per_user=decimal_number_value(properties.get("Rate Per User", {})),
        current_user_count=number_value(properties.get("Current User Count", {})),
        monthly_server_fee=decimal_number_value(properties.get("Monthly Server Fee", {})),
        provider_group=plain_rich_text(properties.get("Provider Group", {})) or None,
        page_id=page.get("id"),
    )


def cash_flow_bill_from_page(page: dict[str, Any]) -> CashFlowBillRecord:
    properties = page.get("properties", {})
    return CashFlowBillRecord(
        page_id=page.get("id", ""),
        vendor_payee=plain_rich_text(properties.get("Vendor / Payee", {})),
        expense_name=plain_title(properties.get("Expense Name", {}).get("title", [])),
        amount=decimal_number_value(properties.get("Amount", {})),
        due_date=date_value(properties.get("Due Date", {})),
        status=select_name(properties.get("Status", {})),
        payment_date=date_value(properties.get("Payment Date", {})),
        payment_type=select_name(properties.get("Payment Type", {})),
        email_link=url_value(properties.get("Email Link", {})),
        invoice_number=plain_rich_text(properties.get("Invoice Number", {})) or None,
        confirmation_link=url_value(properties.get("Confirmation Link", {})),
        category=select_name(properties.get("Category", {})),
        frequency=select_name(properties.get("Frequency", {})),
        due_status=formula_string(properties.get("Due Status", {})),
        action_required=formula_string(properties.get("Action Required", {})),
        notes=plain_rich_text(properties.get("Notes", {})) or None,
    )


def select_name(property_value: dict[str, Any]) -> str | None:
    select = property_value.get("select") or {}
    return select.get("name")


def number_value(property_value: dict[str, Any]) -> int | None:
    value = property_value.get("number")
    if value is None:
        return None
    return int(value)


def decimal_number_value(property_value: dict[str, Any]) -> Decimal | None:
    value = property_value.get("number")
    if value is None:
        return None
    return Decimal(str(value))


def date_value(property_value: dict[str, Any]) -> date | None:
    value = (property_value.get("date") or {}).get("start")
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def url_value(property_value: dict[str, Any]) -> str | None:
    return property_value.get("url") or None


def formula_string(property_value: dict[str, Any]) -> str | None:
    formula = property_value.get("formula") or {}
    if formula.get("type") == "string":
        return formula.get("string")
    value = formula.get("string")
    if value is not None:
        return str(value)
    return None


def checkbox_value(property_value: dict[str, Any]) -> bool | None:
    if "checkbox" not in property_value:
        return None
    return bool(property_value.get("checkbox"))


def format_business_notes(review_reasons: tuple[str, ...]) -> str:
    if not review_reasons:
        return "✓ Ready for Payment"
    lines = ["⚠ Needs Review", ""]
    lines.extend(f"• {display_review_reason(reason)}" for reason in review_reasons)
    return "\n".join(lines)[:1800]


def display_review_reason(reason: str) -> str:
    if reason == "missing due date":
        return "Missing due date"
    if reason == "missing amount":
        return "Missing amount"
    return reason


def expected_amount_for_rule(rule: VendorRule) -> Decimal | None:
    if rule.current_user_count is not None and rule.rate_per_user is not None and rule.monthly_server_fee is not None:
        return Decimal(rule.current_user_count) * Decimal(str(rule.rate_per_user)) + Decimal(str(rule.monthly_server_fee))
    if rule.typical_amount is not None:
        return Decimal(str(rule.typical_amount))
    return None


def add_expected_amount_reason(
    actual: Decimal | None,
    expected: Decimal | None,
    review_reasons: tuple[str, ...],
) -> tuple[str, ...]:
    if actual is None or expected is None:
        return review_reasons
    delta = actual - expected
    if abs(delta) < Decimal("0.01"):
        return review_reasons
    reason = "Higher than expected" if delta > 0 else "Lower than expected"
    if reason in review_reasons:
        return review_reasons
    return review_reasons + (reason,)


def match_vendor_rule(candidate: BillCandidate, message: BillEmail, rules: list[VendorRule]) -> VendorRule | None:
    vendor = candidate.vendor_payee.strip().lower()
    haystack = " ".join(
        [
            candidate.vendor_payee,
            candidate.expense_name,
            message.subject,
            message.sender_name,
            message.sender_email,
            message.body_text,
        ]
    ).lower()
    for rule in rules:
        if rule.vendor_name.strip().lower() == vendor:
            return rule
    for rule in rules:
        match_text = rule.match_text.strip().lower()
        if match_text and match_text in haystack:
            return rule
    return None


def due_date_from_rule(due_day: int, message: BillEmail) -> date | None:
    if due_day < 1 or due_day > 31:
        return None
    month_date = invoice_month_from_message(message) or (message.received_at.date() if message.received_at else None)
    if not month_date:
        return None
    try:
        return date(month_date.year, month_date.month, due_day)
    except ValueError:
        return None


def invoice_month_from_message(message: BillEmail) -> date | None:
    text = f"{message.subject} {message.body_text}"
    patterns = [
        r"(?:invoice|billing|statement)\s*(?:month|period)?\D{0,20}(\d{1,2})[/-](\d{4})",
        r"(?:invoice|billing|statement)\s*(?:month|period)?\D{0,20}([A-Za-z]+)\s+(\d{4})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        first, year = match.group(1), match.group(2)
        try:
            month = int(first)
        except ValueError:
            try:
                month = datetime.strptime(first, "%B").month
            except Exception:
                try:
                    month = datetime.strptime(first, "%b").month
                except Exception:
                    continue
        try:
            return date(int(year), month, 1)
        except ValueError:
            continue
    return None
