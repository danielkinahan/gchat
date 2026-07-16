"""Health, reload, restart, and runtime diagnostic routes."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI


def register_runtime_routes(
    app: FastAPI,
    *,
    refresh_runtime_state: Callable[..., None],
    path_signature: Callable[[Path], Any],
    config_signature: Callable[[Path], Any],
) -> None:
    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/restart")
    def restart_api() -> dict[str, Any]:
        """Exit after flushing the response so the container manager restarts."""

        def shutdown() -> None:
            time.sleep(0.25)
            os._exit(0)

        threading.Thread(target=shutdown, daemon=True).start()
        return {"status": "restarting"}

    @app.post("/api/reload")
    def reload_api() -> dict[str, Any]:
        refresh_runtime_state(force=True)
        current_signature = (
            path_signature(app.state.db_path),
            config_signature(app.state.config_dir),
        )
        db_mtime_ns = current_signature[0][0] if current_signature[0] else None
        return {
            "status": "reloaded",
            "db_mtime_ns": db_mtime_ns,
            "up_to_date": current_signature == app.state._runtime_signature,
        }

    @app.get("/api/runtime-state")
    def runtime_state() -> dict[str, Any]:
        current_signature = (
            path_signature(app.state.db_path),
            config_signature(app.state.config_dir),
        )
        db_mtime_ns = current_signature[0][0] if current_signature[0] else None
        return {
            "db_path": str(app.state.db_path),
            "db_exists": app.state.db_path.exists(),
            "db_mtime_ns": db_mtime_ns,
            "config_dir": str(app.state.config_dir),
            "cached_signature": app.state._runtime_signature,
            "current_signature": current_signature,
            "up_to_date": current_signature == app.state._runtime_signature,
            "analytics_facts_available": app.state.has_analytics_facts,
            "analytics_mode": (
                "materialized" if app.state.has_analytics_facts else "legacy-fallback"
            ),
            "config": app.state.config_diagnostics,
        }
