from __future__ import annotations

import argparse
import json
from dataclasses import asdict, is_dataclass
from typing import Any

from app.data_loader import load_snapshot
from app.rule_engine import QuotationRuleEngine


def main() -> None:
    parser = argparse.ArgumentParser(description="Quotation bot MVP CLI")
    parser.add_argument(
        "--snapshot",
        default=None,
        help="Path to quotation_snapshot.json. Defaults to the workspace snapshot.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    search_parser = subparsers.add_parser("search", help="Search products")
    search_parser.add_argument("query")
    search_parser.add_argument("--limit", type=int, default=10)

    product_parser = subparsers.add_parser("product", help="Show product constraints")
    product_parser.add_argument("product_id")

    check_parser = subparsers.add_parser("check", help="Validate selected products")
    check_parser.add_argument("--region", required=False)
    check_parser.add_argument("--product-id", action="append", default=[])
    check_parser.add_argument("--system-family", required=False)
    check_parser.add_argument("--acquisition-type", required=False)
    check_parser.add_argument("--tube-stand-id", required=False)
    check_parser.add_argument("--wallstand-id", required=False)
    check_parser.add_argument("--table-id", required=False)
    check_parser.add_argument("--grid-id", required=False)
    check_parser.add_argument("--grid-position", required=False)
    check_parser.add_argument("--detector-type", required=False)
    check_parser.add_argument("--generator", required=False)
    check_parser.add_argument("--tube-spec", required=False)
    check_parser.add_argument("--spec-category", required=False)

    args = parser.parse_args()
    snapshot = load_snapshot(args.snapshot)
    engine = QuotationRuleEngine(snapshot)

    if args.command == "search":
        result = snapshot.find_products(args.query, limit=args.limit)
    elif args.command == "product":
        result = {
            "product": snapshot.products_by_id.get(args.product_id),
            "constraints": engine.product_constraints(args.product_id),
        }
    elif args.command == "check":
        result = engine.check_configuration(
            args.product_id,
            region=args.region,
            system_family=args.system_family,
            acquisition_type=args.acquisition_type,
            tube_stand_id=args.tube_stand_id,
            wallstand_id=args.wallstand_id,
            table_id=args.table_id,
            grid_id=args.grid_id,
            grid_position=args.grid_position,
            detector_type=args.detector_type,
            generator=args.generator,
            tube_spec=args.tube_spec,
            spec_category=args.spec_category,
        )
    else:
        parser.error(f"Unsupported command: {args.command}")

    print(json.dumps(_to_jsonable(result), ensure_ascii=False, indent=2))


def _to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {
            key: _to_jsonable(item)
            for key, item in value.items()
            if item is not None and not (key == "source" and not item)
        }
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    return value


if __name__ == "__main__":
    main()
