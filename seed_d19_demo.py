#!/usr/bin/env python3
"""CLI for the isolated D19 demo dataset."""
from __future__ import annotations

import argparse
import json

from main import init_db
from database import db
from d19_demo_seed import SEED_VERSION, reset_demo_seed, seed_d19_demo


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed FlowOrder D19 Shadow/Smoke demo orders")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--reset",
        action="store_true",
        help="Delete only data tied to D19 demo orders, then recreate the demo dataset.",
    )
    mode.add_argument(
        "--clean",
        action="store_true",
        help="Delete D19 demo orders and their derived rows without recreating them.",
    )
    args = parser.parse_args()

    init_db()
    if args.clean:
        with db() as conn:
            deleted = reset_demo_seed(conn)
        print(json.dumps({"seed_version": SEED_VERSION, "status": "cleaned", "deleted": deleted}, ensure_ascii=False, indent=2))
        return 0

    result = seed_d19_demo(reset=args.reset)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
