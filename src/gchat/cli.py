from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .api import run_server
from .builder import build_database
from .configuration import validate_configuration
from .training_data import TrainingExportConfig, export_training_data


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_path(path: Path) -> Path:
    if path.is_absolute():
        return path
    return (_project_root() / path).resolve()


def main() -> None:
    parser = argparse.ArgumentParser(prog="gchat")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="Build the DuckDB database")
    build.add_argument("--data-dir", type=Path, default=Path("data"))
    build.add_argument(
        "--output", type=Path, default=Path("data/gchat-db/gchat.duckdb")
    )
    build.add_argument("--config-dir", type=Path, default=Path("config"))

    serve = sub.add_parser("serve", help="Run the read API")
    serve.add_argument("--db", type=Path, default=Path("data/gchat-db/gchat.duckdb"))
    serve.add_argument("--data-dir", type=Path, default=Path("data"))
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")

    validate = sub.add_parser(
        "validate-config",
        help="Validate YAML configuration and print diagnostics",
    )
    validate.add_argument("--config-dir", type=Path, default=Path("config"))

    export_training = sub.add_parser(
        "export-training",
        help="Export model-agnostic conversation windows as local JSONL",
    )
    export_training.add_argument(
        "--db", type=Path, default=Path("data/gchat-db/gchat.duckdb")
    )
    export_training.add_argument(
        "--output-dir", type=Path, default=Path("data/training")
    )
    export_training.add_argument("--max-messages", type=int, default=64)
    export_training.add_argument("--overlap-messages", type=int, default=8)
    export_training.add_argument("--min-messages", type=int, default=2)
    export_training.add_argument("--train-fraction", type=float, default=0.9)
    export_training.add_argument("--validation-fraction", type=float, default=0.05)

    args = parser.parse_args()
    if args.command == "build":
        build_database(
            _resolve_path(args.data_dir),
            _resolve_path(args.output),
            status=lambda message: print(message, file=sys.stderr, flush=True),
            config_dir=_resolve_path(args.config_dir),
        )
    elif args.command == "serve":
        run_server(
            _resolve_path(args.db),
            data_dir=_resolve_path(args.data_dir),
            host=args.host,
            port=args.port,
            reload=args.reload,
        )
    elif args.command == "validate-config":
        diagnostics = validate_configuration(_resolve_path(args.config_dir))
        print(json.dumps(diagnostics, indent=2, sort_keys=True))
    elif args.command == "export-training":
        summary = export_training_data(
            _resolve_path(args.db),
            _resolve_path(args.output_dir),
            TrainingExportConfig(
                max_messages=args.max_messages,
                overlap_messages=args.overlap_messages,
                min_messages=args.min_messages,
                train_fraction=args.train_fraction,
                validation_fraction=args.validation_fraction,
            ),
        )
        print(json.dumps(asdict(summary), indent=2, sort_keys=True))
