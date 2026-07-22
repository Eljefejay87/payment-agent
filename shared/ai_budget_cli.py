"""Command-line interface for the shared AI budget guard."""

from __future__ import annotations

import argparse

from shared.ai_budget import AGENTS, AIBudgetGuard


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage the local UCM AI budget guard.")
    parser.add_argument(
        "command",
        choices=(
            "ai-budget-status",
            "ai-budget-pause",
            "ai-budget-resume",
            "ai-budget-reset",
        ),
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required to reset the current month's locally tracked usage.",
    )
    args = parser.parse_args()
    guard = AIBudgetGuard()

    if args.command == "ai-budget-status":
        print(format_status(guard))
    elif args.command == "ai-budget-pause":
        guard.pause()
        print("AI budget guard paused. OpenAI-powered work is blocked.")
    elif args.command == "ai-budget-resume":
        guard.resume()
        status = guard.status()
        if status.kill_switch_active:
            print("Manual pause removed; monthly hard stop remains active.")
        else:
            print("AI budget guard resumed.")
    elif not args.confirm:
        parser.error("ai-budget-reset requires --confirm")
    else:
        guard.reset()
        print("AI budget usage reset for the current month; audit rows were retained.")
    return 0


def format_status(guard: AIBudgetGuard) -> str:
    status = guard.status()
    lines = [
        f"AI Budget Status ({status.month})",
        f"Spend today: ${status.spend_today:.2f}",
        f"Spend this month: ${status.spend_this_month:.2f}",
        f"Monthly budget: ${status.monthly_budget:.2f}",
        f"Remaining budget: ${status.remaining_budget:.2f}",
        f"Percentage used: {status.percentage_used:.2f}%",
        f"Kill switch: {'ACTIVE' if status.kill_switch_active else 'inactive'}",
        "Usage by agent:",
    ]
    for agent in AGENTS:
        usage = status.usage_by_agent[agent]
        lines.append(
            f"  {agent}: ${usage.estimated_cost:.2f} | "
            f"{usage.request_count} allowed | {usage.blocked_count} blocked"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
