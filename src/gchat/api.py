from __future__ import annotations

import json
import os
import threading
import time as _time
from pathlib import Path
from typing import Any

import duckdb
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .analytics_facts import has_analytics_facts
from .analytics_sql import excluded_ids_sql, metric_sql
from .api_filters import (
    set_theme_channel_ids,
)
from .api_routes.overview import register_overview_routes
from .api_routes.reactions import register_reaction_routes
from .api_routes.runtime import register_runtime_routes
from .api_routes.search import register_search_routes
from .api_routes.links import register_link_routes
from .api_routes.media import register_media_routes
from .api_routes.messages import register_message_routes
from .api_routes.mentions import register_mention_routes
from .api_routes.auth import register_auth_routes
from .api_routes.chats import register_chat_routes
from .api_routes.history import register_history_routes
from .api_routes.previews import _is_safe_host, register_preview_routes
from .api_routes.timelines import register_timeline_routes
from .api_routes.people import register_people_routes
from .api_routes.words import register_word_routes
from .configuration import validate_configuration
from .display_config import (
    configured_people_names,
    configured_theme_names,
    primary_person_name,
)
from .media_utils import (
    build_signal_filename_index,
    normalize_reaction_details,
    resolve_local_attachment_url,
)
from .moderation import load_moderation_config, set_active_moderation
from .reconciliation import load_reconciliation

# Compatibility aliases for callers that imported these helpers from api.py.
_build_signal_filename_index = build_signal_filename_index
_excluded_ids_sql = excluded_ids_sql
_is_safe_link_host = _is_safe_host
_metric_sql = metric_sql
_resolve_local_attachment_url = resolve_local_attachment_url


def _default_db_path() -> Path:
    return Path(os.environ.get("GCHAT_DB_PATH", "data/gchat-db/gchat.duckdb"))


def _default_data_dir() -> Path:
    return Path(os.environ.get("GCHAT_DATA_DIR", "data"))


def _default_config_dir() -> Path:
    env_dir = os.environ.get("GCHAT_CONFIG_DIR")
    if env_dir:
        return Path(env_dir)
    if Path("/config").exists():
        return Path("/config")
    return Path.cwd() / "config"


def _load_fb_chat_names() -> dict[str, str]:
    """Load Facebook chat name mappings from config."""
    config_path = _default_config_dir() / "fb_chat_names.json"
    if config_path.exists():
        try:
            return json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _get_display_name(
    channel_name: str, source_name: str, fb_chat_names: dict[str, str]
) -> str:
    """Get display name for a channel, using Facebook original names when available."""
    if source_name.startswith("Facebook: "):
        # Try to find the original name using channel name as folder key
        display_name = fb_chat_names.get(channel_name)
        if display_name:
            return display_name
    return channel_name


def _load_moderation(config_dir: Path | None = None) -> frozenset[str]:
    config = load_moderation_config(config_dir or _default_config_dir())
    set_active_moderation(config)
    return config.excluded_message_ids


def _load_configured_theme_names() -> list[str]:
    return configured_theme_names(_default_config_dir())


def _load_db_theme_names(db_path: Path) -> list[str]:
    if not db_path.exists():
        return []
    with _connect(db_path) as con:
        rows = con.execute("SELECT name FROM themes ORDER BY id").fetchall()
    return [str(row[0]) for row in rows]


def _load_bot_person_ids(db_path: Path, reconciliation: Any) -> frozenset[int]:
    bot_names = reconciliation.people.bot_person_names
    if not db_path.exists() or not bot_names:
        return frozenset()
    placeholders = ", ".join("?" for _ in bot_names)
    with _connect(db_path) as con:
        rows = con.execute(
            f"SELECT id FROM people WHERE display_name IN ({placeholders})",
            sorted(bot_names),
        ).fetchall()
    return frozenset(int(row[0]) for row in rows)


def _load_configured_people_names() -> set[str]:
    return configured_people_names(_default_config_dir())


def _load_primary_person_name() -> str | None:
    return primary_person_name(_default_config_dir())


def _connect(db_path: Path):
    return duckdb.connect(str(db_path), read_only=True)


def _load_theme_channel_ids(db_path: Path, reconciliation: Any) -> dict[str, list[int]]:
    configured_themes = reconciliation.themes.configured_theme_names
    if not configured_themes or not db_path.exists():
        return {}

    with _connect(db_path) as con:
        rows = con.execute(
            """
            SELECT c.id, s.name, c.name
            FROM channels c
            JOIN sources s ON c.source_id = s.id
            """
        ).fetchall()

    theme_to_channel_ids: dict[str, list[int]] = {}
    for channel_id, source_name, channel_name in rows:
        resolved_theme = reconciliation.themes.resolve(source_name, channel_name)
        if resolved_theme in configured_themes:
            theme_to_channel_ids.setdefault(resolved_theme, []).append(int(channel_id))
    return theme_to_channel_ids


def _messages_has_column(db_path: Path, column_name: str) -> bool:
    if not db_path.exists():
        return False
    with _connect(db_path) as con:
        rows = con.execute("PRAGMA table_info(messages)").fetchall()
    return any(str(row[1]) == column_name for row in rows)


def _table_exists(db_path: Path, table_name: str) -> bool:
    if not db_path.exists():
        return False
    with _connect(db_path) as con:
        row = con.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_name = ?
            """,
            [table_name],
        ).fetchone()
    return bool(row and int(row[0]) > 0)


def _database_has_analytics_facts(db_path: Path) -> bool:
    if not db_path.exists():
        return False
    with _connect(db_path) as con:
        return has_analytics_facts(con)


def _path_signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def _config_signature(
    config_dir: Path,
) -> tuple[tuple[str, tuple[int, int] | None], ...]:
    return tuple(
        (name, _path_signature(config_dir / name))
        for name in (
            "people.yaml",
            "themes.yaml",
            "fb_chat_names.json",
            "moderation.yaml",
            "excluded_messages.yaml",
        )
    )


def create_app(db_path: Path | None = None, data_dir: Path | None = None) -> FastAPI:
    app = FastAPI(title="gchat API", version="0.1.0")
    configured_db_path = db_path or _default_db_path()
    if not configured_db_path.is_absolute():
        configured_db_path = (_project_root() / configured_db_path).resolve()
    app.state.db_path = configured_db_path
    configured_data_dir = data_dir or _default_data_dir()
    if not configured_data_dir.is_absolute():
        configured_data_dir = (_project_root() / configured_data_dir).resolve()
    app.state.data_dir = configured_data_dir
    app.state.config_dir = _default_config_dir()
    app.state.fb_chat_names = _load_fb_chat_names()
    app.state.reconciliation = load_reconciliation(config_dir=app.state.config_dir)
    app.state.bot_person_ids = _load_bot_person_ids(
        app.state.db_path, app.state.reconciliation
    )
    app.state.configured_people_names = _load_configured_people_names()
    app.state.primary_person_name = _load_primary_person_name()
    app.state.excluded_message_ids = _load_moderation(app.state.config_dir)
    app.state.config_diagnostics = validate_configuration(app.state.config_dir)
    configured_theme_names = _load_configured_theme_names()
    if not configured_theme_names:
        configured_theme_names = _load_db_theme_names(app.state.db_path)
    app.state.theme_id_to_name = {
        i + 1: name for i, name in enumerate(configured_theme_names)
    }
    app.state.theme_to_channel_ids = _load_theme_channel_ids(
        app.state.db_path, app.state.reconciliation
    )
    app.state.has_attachment_preview = _messages_has_column(
        app.state.db_path, "attachment_preview"
    )
    app.state.has_reaction_summary = _messages_has_column(
        app.state.db_path, "reaction_summary"
    )
    app.state.has_reaction_details_json = _messages_has_column(
        app.state.db_path, "reaction_details_json"
    )
    app.state.has_is_edited = _messages_has_column(app.state.db_path, "is_edited")
    app.state.has_is_system = _messages_has_column(app.state.db_path, "is_system")
    app.state.has_word_count = _messages_has_column(app.state.db_path, "word_count")
    app.state.has_person_stats = _table_exists(app.state.db_path, "person_stats")
    app.state.has_analytics_facts = _database_has_analytics_facts(app.state.db_path)
    app.state.has_conversation_id = _messages_has_column(
        app.state.db_path, "conversation_id"
    )
    app.state.signal_filename_index = build_signal_filename_index(app.state.data_dir)
    app.state._runtime_signature = (
        _path_signature(app.state.db_path),
        _config_signature(app.state.config_dir),
    )
    app.state._runtime_last_check = 0.0
    app.state._runtime_lock = threading.Lock()
    set_theme_channel_ids(app.state.theme_to_channel_ids)

    def _refresh_runtime_state(force: bool = False) -> None:
        now = _time.monotonic()
        if not force and now - app.state._runtime_last_check < 1.0:
            return
        app.state._runtime_last_check = now
        current_signature = (
            _path_signature(app.state.db_path),
            _config_signature(app.state.config_dir),
        )
        if current_signature == app.state._runtime_signature:
            return
        with app.state._runtime_lock:
            current_signature = (
                _path_signature(app.state.db_path),
                _config_signature(app.state.config_dir),
            )
            if current_signature == app.state._runtime_signature:
                return
            database_changed = current_signature[0] != app.state._runtime_signature[0]
            app.state.fb_chat_names = _load_fb_chat_names()
            app.state.reconciliation = load_reconciliation(
                config_dir=app.state.config_dir
            )
            app.state.bot_person_ids = _load_bot_person_ids(
                app.state.db_path, app.state.reconciliation
            )
            app.state.configured_people_names = _load_configured_people_names()
            app.state.primary_person_name = _load_primary_person_name()
            app.state.excluded_message_ids = _load_moderation(app.state.config_dir)
            app.state.config_diagnostics = validate_configuration(app.state.config_dir)
            configured_theme_names = _load_configured_theme_names()
            if not configured_theme_names:
                configured_theme_names = _load_db_theme_names(app.state.db_path)
            app.state.theme_id_to_name = {
                i + 1: name for i, name in enumerate(configured_theme_names)
            }
            app.state.theme_to_channel_ids = _load_theme_channel_ids(
                app.state.db_path, app.state.reconciliation
            )
            app.state.has_attachment_preview = _messages_has_column(
                app.state.db_path, "attachment_preview"
            )
            app.state.has_reaction_summary = _messages_has_column(
                app.state.db_path, "reaction_summary"
            )
            app.state.has_reaction_details_json = _messages_has_column(
                app.state.db_path, "reaction_details_json"
            )
            app.state.has_is_edited = _messages_has_column(
                app.state.db_path, "is_edited"
            )
            app.state.has_is_system = _messages_has_column(
                app.state.db_path, "is_system"
            )
            app.state.has_word_count = _messages_has_column(
                app.state.db_path, "word_count"
            )
            app.state.has_person_stats = _table_exists(
                app.state.db_path, "person_stats"
            )
            app.state.has_analytics_facts = _database_has_analytics_facts(
                app.state.db_path
            )
            app.state.has_conversation_id = _messages_has_column(
                app.state.db_path, "conversation_id"
            )
            if database_changed:
                app.state.signal_filename_index = build_signal_filename_index(
                    app.state.data_dir
                )
            app.state._runtime_signature = current_signature
            set_theme_channel_ids(app.state.theme_to_channel_ids)

    register_auth_routes(app)

    @app.middleware("http")
    async def refresh_runtime_state(request, call_next):  # type: ignore[no-untyped-def]
        _refresh_runtime_state()
        return await call_next(request)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_runtime_routes(
        app,
        refresh_runtime_state=_refresh_runtime_state,
        path_signature=_path_signature,
        config_signature=_config_signature,
    )
    register_reaction_routes(
        app,
        resolve_attachment=resolve_local_attachment_url,
        normalize_reactions=normalize_reaction_details,
        display_name=_get_display_name,
    )
    register_search_routes(app)
    register_word_routes(app)
    register_link_routes(app)
    register_mention_routes(app)
    register_chat_routes(app)
    register_history_routes(app)
    register_preview_routes(app)
    register_timeline_routes(app)
    register_people_routes(app)
    register_overview_routes(app)

    register_media_routes(app)
    register_message_routes(app)

    return app


def run_server(
    db_path: Path | None = None,
    data_dir: Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
    reload: bool = False,
) -> None:
    uvicorn.run(
        create_app(db_path, data_dir=data_dir), host=host, port=port, reload=reload
    )


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_cli_path(path: Path | None) -> Path | None:
    if path is None or path.is_absolute():
        return path
    return (_project_root() / path).resolve()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="gchat-api")
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    run_server(
        _resolve_cli_path(args.db),
        data_dir=_resolve_cli_path(args.data_dir),
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
