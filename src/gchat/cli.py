from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .builder import build_database
from .api import run_server


def main() -> None:
    parser = argparse.ArgumentParser(prog="gchat")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="Build the DuckDB database")
    build.add_argument("--data-dir", type=Path, default=Path("data"))
    build.add_argument("--output", type=Path, default=Path("gchat.duckdb"))
    build.add_argument("--config-dir", type=Path, default=Path("config"))

    serve = sub.add_parser("serve", help="Run the read API")
    serve.add_argument("--db", type=Path, default=Path("gchat.duckdb"))
    serve.add_argument("--data-dir", type=Path, default=Path("data"))
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")

    args = parser.parse_args()
    if args.command == "build":
        build_database(
            args.data_dir,
            args.output,
            status=lambda message: print(message, file=sys.stderr, flush=True),
            config_dir=args.config_dir,
        )
    elif args.command == "serve":
        run_server(args.db, data_dir=args.data_dir, host=args.host, port=args.port, reload=args.reload)
