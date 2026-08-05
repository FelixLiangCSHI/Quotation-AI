"""Command-line entry point for the approval-reminder worker.

Examples::

    python -m worker.reminder_worker --run-once
    python -m worker.reminder_worker --interval-seconds 900

The scheduling mechanism is deliberately simple: a database-backed due-task
query executed by a separate process. A cron entry or container schedule
invoking ``--run-once`` is equivalent to the built-in loop, and neither
depends on a running Streamlit session.
"""

from __future__ import annotations

import argparse
import json
import logging
import time

from app.emailing.reminders import ApprovalReminderWorker

LOGGER = logging.getLogger("worker.reminder_worker")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m worker.reminder_worker",
        description="Send due two-day pending-approval reminders.",
    )
    parser.add_argument(
        "--run-once",
        action="store_true",
        help="Process the currently due reminders and exit.",
    )
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=900.0,
        help="Polling interval when not running once (default: 900).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level (default: INFO).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    worker = ApprovalReminderWorker()
    if args.run_once:
        report = worker.run_once()
        LOGGER.info("reminder run complete: %s", json.dumps(report.as_dict()))
        print(json.dumps(report.as_dict(), indent=2))
        return 0

    if args.interval_seconds <= 0:
        raise SystemExit("--interval-seconds must be greater than zero")
    LOGGER.info("Starting reminder loop every %ss", args.interval_seconds)
    while True:  # pragma: no cover - long-running loop
        report = worker.run_once()
        LOGGER.info("reminder run complete: %s", json.dumps(report.as_dict()))
        time.sleep(args.interval_seconds)


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
