from __future__ import annotations

import argparse
import logging
import sys
from typing import Any

from shared.logging import configure_logging
from shared.scheduler import AgentScheduler

from .config import Settings, load_settings, validate_settings
from .database import PaymentDatabase


def main() -> int:
    parser = argparse.ArgumentParser(description="UCM AI Operations Payment Agent")
    parser.add_argument(
        "command",
        choices=[
            "init-db",
            "scan-once",
            "send-daily-report",
            "run",
            "debug-mail-search",
            "debug-teams-message",
            "debug-list-teams-chats",
        ],
        help="Action to run.",
    )
    parser.add_argument("--env-file", default=None, help="Optional path to .env file.")
    args = parser.parse_args()

    settings = load_settings(args.env_file)
    configure_logging(settings.log_level)

    if args.command == "init-db":
        PaymentDatabase(settings.database_path).initialize()
        logging.info("Database initialized at %s", settings.database_path)
        return 0

    errors = validate_settings(settings)
    if args.command == "debug-mail-search":
        errors = [error for error in errors if not error.startswith("TEAMS_")]
    if args.command == "debug-teams-message":
        errors = [
            error
            for error in errors
            if not error.startswith("MAILBOX_USER_ID")
            and not error.startswith("SENDER_EMAIL")
        ]
    if args.command == "debug-list-teams-chats":
        errors = [
            error
            for error in errors
            if not error.startswith("MAILBOX_USER_ID")
            and not error.startswith("SENDER_EMAIL")
            and not error.startswith("TEAMS_CHAT_ID")
        ]
    if errors:
        for error in errors:
            logging.error(error)
        return 2

    if args.command == "debug-mail-search":
        from .graph_client import GraphClient

        return debug_mail_search(settings, GraphClient(settings))

    if args.command == "debug-teams-message":
        from shared.integrations.microsoft_teams import TeamsMessage, TeamsNotifier

        from shared.integrations.microsoft_graph import GraphClient

        message = TeamsMessage(
            title="UCM Payment Agent Teams Test",
            text="UCM Payment Agent test message. Microsoft Graph chat posting is configured.",
            html=(
                "<h2>UCM Payment Agent Teams Test</h2>"
                "<p>Microsoft Graph chat posting is configured.</p>"
            ),
        )
        TeamsNotifier(settings, teams_graph_client(settings)).send(message)
        logging.info("Teams test message command completed.")
        return 0

    if args.command == "debug-list-teams-chats":
        return debug_list_teams_chats(teams_graph_client(settings))

    from .service import PaymentAgent

    agent = PaymentAgent(settings)

    if args.command == "scan-once":
        count = agent.scan_once()
        logging.info("Scan complete. Processed %s new payment(s).", count)
        return 0

    if args.command == "send-daily-report":
        agent.send_daily_report()
        logging.info("Daily report complete.")
        return 0

    if args.command == "run":
        scheduler = AgentScheduler()
        agent.initialize()
        logging.info(
            "Payment Agent running. Scan every %s minute(s); daily report at %s; dry_run=%s",
            settings.scan_interval_minutes,
            settings.daily_report_time,
            settings.dry_run,
        )
        scheduler.every_minutes(settings.scan_interval_minutes, agent.scan_once)
        if settings.daily_enabled:
            scheduler.every_day_at(settings.daily_report_time, agent.send_daily_report)
        agent.scan_once()
        scheduler.run_forever()

    return 0


def debug_mail_search(settings: Settings, graph: Any) -> int:
    from .graph_client import is_payment_subject

    messages = graph.find_recent_message_headers()
    sender_filter = settings.sender_email.strip().lower()
    subject_filter = settings.subject_contains.strip().lower()

    subject_matches = 0
    sender_matches = 0
    both_matches = 0

    print("Current filters:")
    print(f"  MAILBOX_USER_ID: {settings.mailbox_user_id}")
    print(f"  SENDER_EMAIL: {settings.sender_email}")
    print(f"  SUBJECT_CONTAINS: {settings.subject_contains}")
    print(f"  LOOKBACK_HOURS: {settings.lookback_hours}")
    print()
    print("20 most recent messages in lookback window:")

    for message in messages[:20]:
        subject = message.get("subject", "")
        sender = _sender_email(message)
        received = message.get("receivedDateTime", "")
        print(f"  {received} | {sender} | {subject}")

    for message in messages:
        subject = message.get("subject") or ""
        sender = _sender_email(message).lower()
        subject_match = is_payment_subject(subject, subject_filter)
        sender_match = sender == sender_filter
        if subject_match:
            subject_matches += 1
        if sender_match:
            sender_matches += 1
        if subject_match and sender_match:
            both_matches += 1

    print()
    print("Counts:")
    print(f"  messages checked: {len(messages)}")
    print(f"  subject matches: {subject_matches}")
    print(f"  sender matches: {sender_matches}")
    print(f"  both sender and subject matches: {both_matches}")
    return 0


def _sender_email(message: dict[str, Any]) -> str:
    return (
        message.get("from", {})
        .get("emailAddress", {})
        .get("address", "")
    )


def debug_list_teams_chats(graph: Any) -> int:
    chats = graph.list_recent_chats()
    print("Recent Teams chats:")
    for chat in chats:
        participants = [
            _member_label(member)
            for member in chat.get("members", [])
        ]
        print(f"  chat id: {chat.get('id', '')}")
        print(f"  chat type: {chat.get('chatType', '')}")
        print(f"  last updated: {chat.get('lastUpdatedDateTime', '')}")
        print(f"  participants: {', '.join(participants) if participants else 'Unavailable'}")
        print()
    print(f"Total chats listed: {len(chats)}")
    return 0


def _member_label(member: dict[str, Any]) -> str:
    name = member.get("displayName") or "Unknown"
    email = member.get("email")
    if email:
        return f"{name} <{email}>"
    return name


def teams_graph_client(settings: Settings) -> Any:
    from shared.integrations.microsoft_graph import GraphClient

    return GraphClient(
        tenant_id=settings.teams_graph_tenant_id,
        client_id=settings.teams_graph_client_id,
        client_secret=settings.teams_graph_client_secret,
        delegated_token_cache_path=settings.teams_graph_token_cache_path,
    )


if __name__ == "__main__":
    sys.exit(main())
