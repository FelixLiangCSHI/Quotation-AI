"""Operational status command line.

Usage::

    python -m app.operations.cli
    python -m app.operations.cli --component database

The output is JSON and contains no credential or connection string.
"""

from __future__ import annotations

import argparse
import json
import sys

from app.operations.status import DEGRADED, OK, status_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.operations.cli",
        description="Print the secret-free operational status report.",
    )
    parser.add_argument(
        "--component",
        default=None,
        help="Print one component only (for example: database, agents).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = status_report()
    if args.component:
        entry = report["components"].get(args.component)
        if entry is None:
            print(f"Unknown status component: {args.component}", file=sys.stderr)
            return 2
        print(json.dumps(entry, indent=2, default=str))
        return 0 if entry["status"] in {OK, DEGRADED} else 1
    print(json.dumps(report, indent=2, default=str))
    return 0 if report["status"] in {OK, DEGRADED} else 1


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
