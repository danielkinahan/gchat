from __future__ import annotations

import argparse
from pathlib import Path

from .builder import build_database


def main() -> None:
    parser = argparse.ArgumentParser(prog="gchat")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="Build the DuckDB database")
    build.add_argument("--data-dir", type=Path, default=Path("data"))
    build.add_argument("--output", type=Path, default=Path("gchat.duckdb"))

    args = parser.parse_args()
    if args.command == "build":
        build_database(args.data_dir, args.output)

