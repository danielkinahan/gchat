from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlencode, urlparse

import duckdb
import uvicorn
import yaml
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .reconciliation import load_reconciliation

COMMON_STOP_WORDS = {
    "the",
    "and",
    "for",
    "that",
    "you",
    "with",
    "this",
    "have",
    "are",
    "was",
    "but",
    "not",
    "all",
    "can",
    "your",
    "just",
    "its",
    "its",
    "from",
    "they",
    "what",
    "when",
    "where",
    "will",
    "would",
    "there",
    "their",
    "about",
    "out",
    "get",
    "got",
    "into",
    "too",
    "very",
    "how",
    "why",
    "who",
    "him",
    "her",
    "his",
    "she",
    "himself",
    "herself",
    "them",
    "then",
    "than",
    "our",
    "ours",
    "were",
    "had",
    "has",
    "did",
    "does",
    "dont",
    "cant",
    "im",
    "ive",
    "id",
    "ill",
    "youre",
    "youve",
    "theyre",
    "weve",
    "isnt",
    "wasnt",
    "wont",
    "aint",
    "lol",
    "lmao",
    "yeah",
    "yep",
    "nah",
    "ok",
    "okay",
    "bro",
    "dude",
    "tho",
    "though",
    "like",
    "https",
    "http",
    "www",
    "com",
    "org",
    "net",
    "gg",
    "jpg",
    "jpeg",
    "png",
    "gif",
    "webp",
    "mp4",
    "mov",
    "sticker",
    "video",
    "image",
    "images",
    "reply",
    "forwarded",
    "message",
    "messages",
}
_THEME_CHANNEL_IDS: dict[str, list[int]] = {}


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


def _canonical_link_domain_expr(column: str) -> str:
    return f"""CASE
            WHEN lower({column}) IN ('youtu.be', 'youtube.com', 'www.youtube.com', 'm.youtube.com') THEN 'youtube.com'
            ELSE lower({column})
        END"""


def _count_metric(metric: str) -> str:
    normalized = metric.strip().casefold()
    if normalized not in {"messages", "words"}:
        raise HTTPException(status_code=400, detail="Invalid metric filter")
    return normalized


def _count_metric_expr(metric: str) -> str:
    return "SUM(word_count)" if metric == "words" else "COUNT(*)"


def _word_count_expr() -> str:
    return "COALESCE(array_length(regexp_extract_all(replace(lower(coalesce(m.content, '')), chr(39), ''), '[a-z]{3,}')), 0)"


def _message_preview(
    content: str | None, attachment_count: int, attachment_preview: str | None = None
) -> str:
    text = (content or "").strip()
    if text:
        return text
    preview = (attachment_preview or "").strip()
    if preview:
        return preview
    if attachment_count > 0:
        unit = "attachment" if attachment_count == 1 else "attachments"
        return f"[{attachment_count} {unit}]"
    return "[no text]"


def _parse_json_array(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def _normalize_reaction_details(
    value: object,
    source_name: str,
    data_dir: Path,
    signal_filename_index: dict[str, dict[str, str]] | None,
) -> list[dict[str, Any]]:
    details = _parse_json_array(value)
    if not details:
        return []
    normalized: list[dict[str, Any]] = []
    for detail in details:
        emoji_id = str(detail.get("emoji_id") or "").strip() or None
        image_url = str(detail.get("image_url") or "").strip()
        # Discord unicode emoji should render with system glyphs, not duplicated image + glyph.
        if source_name.startswith("Discord: ") and emoji_id is None:
            image_url = ""
        resolved_image_url = (
            _resolve_local_attachment_url(
                image_url, source_name, data_dir, signal_filename_index
            )
            if image_url
            else None
        )
        normalized.append(
            {
                "name": str(detail.get("name") or "").strip(),
                "count": int(detail.get("count") or 0),
                "emoji_id": emoji_id,
                "image_url": resolved_image_url or image_url or None,
                "code": str(detail.get("code") or "").strip() or None,
                "is_animated": bool(detail.get("is_animated")),
            }
        )
    return normalized


def _normalized_history_name(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.split()).casefold()


def _format_history_actor_name(
    actor_name: str | None,
    actor_raw_id: str | None,
    platform: str,
    identity_to_display_name: dict[tuple[str, str], str],
    actor_nickname: str | None = None,
    you_fallback_name: str | None = None,
) -> str | None:
    def _replace_you_placeholder(
        value: str, canonical_name: str | None, fallback_name: str | None
    ) -> str:
        replacement_name = canonical_name
        if _normalized_history_name(replacement_name) == "you":
            replacement_name = None
        replacement_name = replacement_name or fallback_name
        normalized = _normalized_history_name(value)
        if normalized == "you" and replacement_name:
            return replacement_name
        if "(you)" in value.casefold() and replacement_name:
            replaced = value.replace("(You)", f"({replacement_name})")
            replaced = replaced.replace("(you)", f"({replacement_name})")
            return " ".join(replaced.split())
        return value

    resolved = (
        identity_to_display_name.get((platform, actor_raw_id)) if actor_raw_id else None
    )
    if _normalized_history_name(resolved) == "you":
        resolved = you_fallback_name or None
    display_actor = actor_nickname or resolved or actor_name
    if display_actor:
        display_actor = _replace_you_placeholder(
            display_actor, resolved, you_fallback_name
        )
    if display_actor and resolved:
        if f"({resolved})".casefold() in display_actor.casefold():
            return display_actor
        if _normalized_history_name(display_actor) == _normalized_history_name(
            resolved
        ):
            return display_actor
        return f"{display_actor} ({resolved})"
    if display_actor:
        return display_actor
    return resolved


@dataclass(frozen=True)
class QueryFilters:
    start: date | None
    end: date | None
    people: list[int]
    themes: list[int]
    platforms: list[str]


def _csv_ints(value: str | None, field: str) -> list[int]:
    if not value:
        return []
    items: list[int] = []
    for raw in value.split(","):
        item = raw.strip()
        if not item:
            continue
        try:
            items.append(int(item))
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail=f"Invalid {field} filter: {item!r}"
            ) from exc
    return items


def _csv_strings(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _load_configured_theme_names() -> list[str]:
    config_path = _default_config_dir() / "themes.yaml"
    if not config_path.exists():
        return []
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    names: list[str] = []
    for theme in data.get("themes", []):
        if isinstance(theme, dict) and "name" in theme:
            names.append(str(theme["name"]))
    return names


def _load_db_theme_names(db_path: Path) -> list[str]:
    if not db_path.exists():
        return []
    with _connect(db_path) as con:
        rows = con.execute("SELECT name FROM themes ORDER BY id").fetchall()
    return [str(row[0]) for row in rows]


def _load_configured_people_names() -> set[str]:
    config_path = _default_config_dir() / "people.yaml"
    if not config_path.exists():
        return set()
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return set()
    names: set[str] = set()
    for person in data.get("people", []):
        if isinstance(person, dict) and "name" in person:
            names.add(str(person["name"]))
    return names


def _load_primary_person_name() -> str | None:
    config_path = _default_config_dir() / "people.yaml"
    if not config_path.exists():
        return None
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    people = data.get("people", [])
    if not isinstance(people, list) or not people:
        return None
    first = people[0]
    if isinstance(first, dict) and first.get("name"):
        return str(first["name"])
    return None


def _filters_clause(
    filters: QueryFilters,
    params: list[Any],
    reconciliation: Any | None = None,
    theme_id_to_name: dict[int, str] | None = None,
    theme_to_channel_ids: dict[str, list[int]] | None = None,
) -> str:
    clauses = ["1 = 1"]
    if filters.start is not None:
        clauses.append("m.ts >= ?")
        params.append(datetime.combine(filters.start, time.min))
    if filters.end is not None:
        clauses.append("m.ts < ?")
        params.append(datetime.combine(filters.end + timedelta(days=1), time.min))
    if filters.people:
        placeholders = ", ".join("?" for _ in filters.people)
        clauses.append(f"m.person_id IN ({placeholders})")
        params.extend(filters.people)
    if filters.themes:
        if reconciliation is None or theme_id_to_name is None:
            placeholders = ", ".join("?" for _ in filters.themes)
            clauses.append(f"c.theme_id IN ({placeholders})")
            params.extend(filters.themes)
        else:
            selected_theme_names = {
                theme_id_to_name.get(theme_id) for theme_id in filters.themes
            }
            selected_theme_names.discard(None)
            channel_index = (
                theme_to_channel_ids
                if theme_to_channel_ids is not None
                else _THEME_CHANNEL_IDS
            )
            if not selected_theme_names:
                clauses.append("1 = 0")
            elif channel_index:
                channel_ids = sorted(
                    {
                        channel_id
                        for theme_name in selected_theme_names
                        for channel_id in channel_index.get(theme_name, [])
                    }
                )
                if not channel_ids:
                    clauses.append("1 = 0")
                else:
                    placeholders = ", ".join("?" for _ in channel_ids)
                    clauses.append(f"c.id IN ({placeholders})")
                    params.extend(channel_ids)
            else:
                exact_terms: list[str] = []
                exact_params: list[Any] = []
                fallback_terms: list[str] = []
                fallback_params: list[Any] = []

                for (
                    source_name,
                    channel_name,
                ), theme_name in reconciliation.themes.channel_to_theme.items():
                    if theme_name not in selected_theme_names:
                        continue
                    exact_terms.append("(s.name = ? AND c.name = ?)")
                    exact_params.extend([source_name, channel_name])

                    if source_name.startswith("Facebook: "):
                        fallback_terms.append(
                            "("
                            "s.platform = 'facebook' "
                            "AND starts_with(lower(s.name), lower(? || '_')) "
                            "AND starts_with(lower(c.name), lower(? || '_'))"
                            ")"
                        )
                        fallback_params.extend([source_name, channel_name])
                    elif source_name.startswith("Signal: "):
                        fallback_terms.append(
                            "("
                            "s.platform = 'signal' "
                            "AND starts_with(lower(s.name), lower(?)) "
                            "AND lower(c.name) = lower(?)"
                            ")"
                        )
                        fallback_params.extend([source_name, channel_name])

                theme_terms: list[str] = []
                if exact_terms:
                    theme_terms.append(f"({' OR '.join(exact_terms)})")
                if fallback_terms:
                    theme_terms.append(f"({' OR '.join(fallback_terms)})")

                if theme_terms:
                    clauses.append(f"({' OR '.join(theme_terms)})")
                    params.extend(exact_params)
                    params.extend(fallback_params)
                else:
                    clauses.append("1 = 0")
    if filters.platforms:
        placeholders = ", ".join("?" for _ in filters.platforms)
        clauses.append(f"s.platform IN ({placeholders})")
        params.extend(filters.platforms)
    return " AND ".join(clauses)


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


def _row_to_dict(row: tuple[Any, ...]) -> dict[str, Any]:
    return dict(row)


def _messages_has_column(db_path: Path, column_name: str) -> bool:
    if not db_path.exists():
        return False
    with _connect(db_path) as con:
        rows = con.execute("PRAGMA table_info(messages)").fetchall()
    return any(str(row[1]) == column_name for row in rows)


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
        for name in ("people.yaml", "themes.yaml", "fb_chat_names.json")
    )


def _safe_child_path(root: Path, rel_path: str) -> Path | None:
    cleaned_parts = [
        part for part in Path(rel_path).parts if part not in {"", ".", ".."}
    ]
    if not cleaned_parts:
        return None
    target = (root / Path(*cleaned_parts)).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return None
    return target


def _media_url(platform: str, source: str, rel_path: str) -> str:
    return f"/api/media?{urlencode({'platform': platform, 'source': source, 'path': rel_path})}"


def _normalize_facebook_preview_path(preview: str, source_folder: str) -> str:
    local_preview = preview
    parsed = urlparse(preview)
    if parsed.scheme in {"http", "https"} and parsed.netloc.startswith(
        ("localhost:", "127.0.0.1:")
    ):
        local_preview = parsed.path or ""
    local_preview = unquote(local_preview).strip()
    if local_preview.startswith("/"):
        local_preview = local_preview[1:]

    inbox_prefix = "messages/inbox/"
    if local_preview.startswith(inbox_prefix):
        remainder = local_preview[len(inbox_prefix) :]
        source_prefix = f"{source_folder}/"
        if remainder.startswith(source_prefix):
            return remainder[len(source_prefix) :]
    archived_prefix = "messages/archived_threads/"
    if local_preview.startswith(archived_prefix):
        remainder = local_preview[len(archived_prefix) :]
        source_prefix = f"{source_folder}/"
        if remainder.startswith(source_prefix):
            return remainder[len(source_prefix) :]
    source_prefix = f"{source_folder}/"
    if local_preview.startswith(source_prefix):
        return local_preview[len(source_prefix) :]
    return local_preview


def _normalize_local_preview_path(preview: str) -> str:
    parsed = urlparse(preview)
    local_preview = preview
    if parsed.scheme in {"http", "https"} and parsed.netloc.startswith(
        ("localhost:", "127.0.0.1:")
    ):
        local_preview = parsed.path or ""
    local_preview = unquote(local_preview).strip()
    if local_preview.startswith("/"):
        local_preview = local_preview[1:]
    return local_preview


def _build_signal_filename_index(data_dir: Path) -> dict[str, dict[str, str]]:
    decrypted_root = data_dir / "signal_decrypted"
    if not decrypted_root.exists() or not decrypted_root.is_dir():
        return {}

    index: dict[str, dict[str, str]] = {}
    for source_dir in decrypted_root.iterdir():
        if not source_dir.is_dir():
            continue
        media_root = source_dir / "media"
        if not media_root.exists() or not media_root.is_dir():
            continue
        source_map: dict[str, str] = {}
        for file_path in media_root.rglob("*"):
            if not file_path.is_file():
                continue
            normalized_file_name = file_path.name.casefold()
            source_map.setdefault(
                normalized_file_name,
                file_path.relative_to(source_dir).as_posix(),
            )
        if source_map:
            index[source_dir.name] = source_map

    return index


def _signal_source_root(data_dir: Path, source_folder: str) -> Path | None:
    if source_folder == "signal_decrypted":
        root = (data_dir / "signal_decrypted").resolve()
        if root.exists() and root.is_dir():
            return root
    decrypted_root = (data_dir / "signal_decrypted" / source_folder).resolve()
    if decrypted_root.exists() and decrypted_root.is_dir():
        return decrypted_root

    return None


def _resolve_local_attachment_url(
    attachment_preview: str | None,
    source_name: str,
    data_dir: Path,
    signal_filename_index: dict[str, dict[str, str]] | None = None,
) -> str | None:
    preview = (attachment_preview or "").strip()
    if not preview:
        return None
    lowered = preview.casefold()
    if lowered.startswith(("http://", "https://")):
        parsed = urlparse(preview)
        if parsed.netloc and not parsed.netloc.startswith(("localhost:", "127.0.0.1:")):
            return preview

    if source_name.startswith("Facebook: "):
        source_folder = source_name.removeprefix("Facebook: ").strip()
        source_root = (data_dir / "facebook" / source_folder).resolve()
        candidate = _safe_child_path(
            source_root,
            _normalize_facebook_preview_path(preview, source_folder),
        )
        if candidate and candidate.exists() and candidate.is_file():
            relative = candidate.relative_to(source_root).as_posix()
            return _media_url("facebook", source_folder, relative)
        return None

    if source_name.startswith("Signal: "):
        source_folder = source_name.removeprefix("Signal: ").strip()
        source_root = _signal_source_root(data_dir, source_folder)
        if source_root is None:
            return None
        normalized_preview = _normalize_local_preview_path(preview)
        direct = _safe_child_path(source_root, normalized_preview)
        if direct and direct.exists() and direct.is_file():
            relative = direct.relative_to(source_root).as_posix()
            return _media_url("signal", source_folder, relative)
        if signal_filename_index:
            mapped_rel_path = signal_filename_index.get(source_folder, {}).get(
                Path(normalized_preview).name.casefold()
            )
            if mapped_rel_path:
                mapped = _safe_child_path(source_root, mapped_rel_path)
                if mapped and mapped.exists() and mapped.is_file():
                    return _media_url("signal", source_folder, mapped_rel_path)
        return None

    if source_name.startswith("Discord: "):
        source_folder = source_name.removeprefix("Discord: ").strip()
        source_root = (data_dir / "discord").resolve()
        discord_media_root = (data_dir / "discord-media").resolve()

        parsed = urlparse(preview)
        normalized_preview = _normalize_local_preview_path(preview)

        # Handle absolute local file paths (e.g. file:///data/discord/media/... or /data/discord-media/...).
        absolute_candidate: Path | None = None
        if parsed.scheme == "file" and parsed.path:
            absolute_candidate = Path(unquote(parsed.path)).resolve()
        elif preview.startswith("/"):
            absolute_candidate = Path(unquote(preview)).resolve()

        if (
            absolute_candidate is not None
            and absolute_candidate.exists()
            and absolute_candidate.is_file()
        ):
            try:
                relative = absolute_candidate.relative_to(source_root).as_posix()
                return _media_url("discord", source_folder, relative)
            except ValueError:
                try:
                    relative = absolute_candidate.relative_to(
                        discord_media_root
                    ).as_posix()
                    return _media_url("discord", source_folder, relative)
                except ValueError:
                    pass

        direct = _safe_child_path(source_root, normalized_preview)
        if direct and direct.exists() and direct.is_file():
            relative = direct.relative_to(source_root).as_posix()
            return _media_url("discord", source_folder, relative)

        basename = Path(normalized_preview).name
        fallback_candidates = [
            _safe_child_path(source_root / "assets", basename),
            _safe_child_path(source_root / "media", basename),
            _safe_child_path(discord_media_root, basename),
        ]
        for fallback in fallback_candidates:
            if fallback and fallback.exists() and fallback.is_file():
                if str(fallback).startswith(str(discord_media_root)):
                    relative = fallback.relative_to(discord_media_root).as_posix()
                    return _media_url("discord", source_folder, relative)
                relative = fallback.relative_to(source_root).as_posix()
                return _media_url("discord", source_folder, relative)

        return None

    return None


def create_app(db_path: Path | None = None, data_dir: Path | None = None) -> FastAPI:
    global _THEME_CHANNEL_IDS

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
    app.state.configured_people_names = _load_configured_people_names()
    app.state.primary_person_name = _load_primary_person_name()
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
    app.state.signal_filename_index = _build_signal_filename_index(app.state.data_dir)
    app.state._runtime_signature = (
        _path_signature(app.state.db_path),
        _config_signature(app.state.config_dir),
    )
    app.state._runtime_lock = threading.Lock()
    _THEME_CHANNEL_IDS = app.state.theme_to_channel_ids

    def _refresh_runtime_state() -> None:
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
            app.state.fb_chat_names = _load_fb_chat_names()
            app.state.reconciliation = load_reconciliation(
                config_dir=app.state.config_dir
            )
            app.state.configured_people_names = _load_configured_people_names()
            app.state.primary_person_name = _load_primary_person_name()
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
            app.state.signal_filename_index = _build_signal_filename_index(
                app.state.data_dir
            )
            app.state._runtime_signature = current_signature
            global _THEME_CHANNEL_IDS
            _THEME_CHANNEL_IDS = app.state.theme_to_channel_ids

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

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/runtime-state")
    def runtime_state() -> dict[str, Any]:
        current_signature = (
            _path_signature(app.state.db_path),
            _config_signature(app.state.config_dir),
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
        }

    @app.get("/api/media")
    def media_file(platform: str, source: str, path: str) -> FileResponse:
        if platform not in {"facebook", "signal", "discord"}:
            raise HTTPException(status_code=404, detail="Unsupported media platform")

        target: Path | None = None
        if platform == "facebook":
            source_root = (app.state.data_dir / "facebook" / source).resolve()
            target = _safe_child_path(source_root, path)
        elif platform == "signal":
            source_root = _signal_source_root(app.state.data_dir, source)
            if source_root is None:
                raise HTTPException(status_code=404, detail="Media source not found")
            target = _safe_child_path(source_root, path)
        else:
            source_root = (app.state.data_dir / "discord").resolve()
            target = _safe_child_path(source_root, path)
            if target is None or not target.exists() or not target.is_file():
                discord_media_root = (app.state.data_dir / "discord-media").resolve()
                target = _safe_child_path(discord_media_root, path)

        if target is None or not target.exists() or not target.is_file():
            raise HTTPException(status_code=404, detail="Media file not found")
        return FileResponse(target)

    @app.get("/api/overview")
    def overview(
        start: date | None = Query(default=None, alias="from"),
        end: date | None = Query(default=None, alias="to"),
        people: str | None = None,
        themes: str | None = None,
        platforms: str | None = None,
        metric: str = Query(default="messages", pattern="^(messages|words)$"),
    ) -> dict[str, Any]:
        metric = _count_metric(metric)
        filters = QueryFilters(
            start=start,
            end=end,
            people=_csv_ints(people, "people"),
            themes=_csv_ints(themes, "themes"),
            platforms=_csv_strings(platforms),
        )
        params: list[Any] = []
        where = _filters_clause(
            filters, params, app.state.reconciliation, app.state.theme_id_to_name
        )
        edited_expr = (
            "COALESCE(CAST(m.is_edited AS INTEGER), 0)"
            if app.state.has_is_edited
            else "0"
        )
        with _connect(app.state.db_path) as con:
            if metric == "words":
                total = con.execute(
                    f"""
                    SELECT SUM(word_count), MIN(ts), MAX(ts)
                    FROM (
                        SELECT m.ts, {_word_count_expr()} AS word_count
                        FROM messages m
                        JOIN channels c ON c.id = m.channel_id
                        JOIN sources s ON c.source_id = s.id
                        WHERE {where}
                    )
                    """,
                    params,
                ).fetchone()
                people_rows = con.execute(
                    f"""
                    SELECT p.id, p.display_name, p.color, SUM(word_count) AS message_count
                    FROM (
                        SELECT m.person_id, {_word_count_expr()} AS word_count
                        FROM messages m
                        JOIN channels c ON c.id = m.channel_id
                        JOIN sources s ON c.source_id = s.id
                        WHERE {where}
                    ) counted
                    JOIN people p ON p.id = counted.person_id
                    GROUP BY p.id, p.display_name, p.color
                    ORDER BY message_count DESC, p.display_name
                    """,
                    params,
                ).fetchall()
            else:
                total = con.execute(
                    f"""
                    SELECT COUNT(*), MIN(m.ts), MAX(m.ts)
                    FROM messages m
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON c.source_id = s.id
                    WHERE {where}
                    """,
                    params,
                ).fetchone()
                people_rows = con.execute(
                    f"""
                    SELECT p.id, p.display_name, p.color, COUNT(*) AS message_count
                    FROM messages m
                    JOIN people p ON p.id = m.person_id
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON c.source_id = s.id
                    WHERE {where}
                    GROUP BY p.id, p.display_name, p.color
                    ORDER BY message_count DESC, p.display_name
                    """,
                    params,
                ).fetchall()
            message_stats_row = con.execute(
                f"""
                WITH filtered AS (
                    SELECT
                        m.ts,
                        TRIM(COALESCE(m.content, '')) AS content,
                        COALESCE(m.attachment_count, 0) AS attachment_count,
                        LOWER(regexp_replace(split_part(COALESCE(m.attachment_preview, ''), '?', 1), '#.*$', '')) AS preview_path,
                        {edited_expr} AS is_edited
                    FROM messages m
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON c.source_id = s.id
                    WHERE {where}
                )
                SELECT
                    COUNT(*) AS total_messages,
                    SUM(CASE WHEN content <> '' THEN 1 ELSE 0 END) AS with_text,
                    SUM(CASE WHEN regexp_matches(content, '(https?://|www\\.)') THEN 1 ELSE 0 END) AS with_links,
                    SUM(CASE WHEN attachment_count > 0 AND regexp_matches(preview_path, '\\.(jpg|jpeg|png|webp|bmp|heic|heif|avif)$') THEN 1 ELSE 0 END) AS with_images,
                    SUM(CASE WHEN attachment_count > 0 AND regexp_matches(preview_path, '\\.gif$') THEN 1 ELSE 0 END) AS with_gifs,
                    SUM(CASE WHEN attachment_count > 0 AND regexp_matches(preview_path, '\\.(mp4|mov|webm|mkv|avi|wmv|m4v)$') THEN 1 ELSE 0 END) AS with_videos,
                    SUM(CASE WHEN attachment_count > 0 AND preview_path LIKE '%sticker%' THEN 1 ELSE 0 END) AS with_stickers,
                    SUM(CASE WHEN attachment_count > 0 AND regexp_matches(preview_path, '\\.(mp3|wav|m4a|aac|ogg|opus|flac|amr|aif|aiff|mpga)$') THEN 1 ELSE 0 END) AS with_audio_files,
                    SUM(CASE WHEN attachment_count > 0 AND regexp_matches(preview_path, '\\.(pdf|doc|docx|txt|rtf|odt|xls|xlsx|ods|ppt|pptx|csv)$') THEN 1 ELSE 0 END) AS with_documents,
                    SUM(
                        CASE
                            WHEN attachment_count > 0
                                 AND NOT regexp_matches(preview_path, '\\.(jpg|jpeg|png|webp|bmp|heic|heif|avif|gif|mp4|mov|webm|mkv|avi|wmv|m4v|mp3|wav|m4a|aac|ogg|opus|flac|amr|aif|aiff|mpga|pdf|doc|docx|txt|rtf|odt|xls|xlsx|ods|ppt|pptx|csv)$')
                                 AND preview_path NOT LIKE '%sticker%'
                            THEN 1
                            ELSE 0
                        END
                    ) AS with_other_files,
                    SUM(is_edited) AS edited_messages
                FROM filtered
                """,
                params,
            ).fetchone()
            longest_gap_row = con.execute(
                f"""
                WITH filtered AS (
                    SELECT m.ts
                    FROM messages m
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON c.source_id = s.id
                    WHERE {where}
                ),
                ordered AS (
                    SELECT ts, LAG(ts) OVER (ORDER BY ts) AS prev_ts
                    FROM filtered
                )
                SELECT COALESCE(MAX(date_diff('second', prev_ts, ts)), 0)
                FROM ordered
                WHERE prev_ts IS NOT NULL
                """,
                params,
            ).fetchone()
            longest_active_row = con.execute(
                f"""
                WITH filtered AS (
                    SELECT m.ts
                    FROM messages m
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON c.source_id = s.id
                    WHERE {where}
                ),
                ordered AS (
                    SELECT ts, LAG(ts) OVER (ORDER BY ts) AS prev_ts
                    FROM filtered
                ),
                grouped AS (
                    SELECT
                        ts,
                        SUM(
                            CASE
                                WHEN prev_ts IS NULL OR date_diff('minute', prev_ts, ts) > 15 THEN 1
                                ELSE 0
                            END
                        ) OVER (ORDER BY ts ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS session_id
                    FROM ordered
                ),
                sessions AS (
                    SELECT
                        session_id,
                        MIN(ts) AS start_ts,
                        MAX(ts) AS end_ts
                    FROM grouped
                    GROUP BY session_id
                )
                SELECT COALESCE(MAX(date_diff('second', start_ts, end_ts)), 0)
                FROM sessions
                """,
                params,
            ).fetchone()
            most_active_year = con.execute(
                f"""
                SELECT date_trunc('year', m.ts) AS bucket, COUNT(*) AS count
                FROM messages m
                JOIN channels c ON c.id = m.channel_id
                JOIN sources s ON c.source_id = s.id
                WHERE {where}
                GROUP BY bucket
                ORDER BY count DESC, bucket ASC
                LIMIT 1
                """,
                params,
            ).fetchone()
            most_active_month = con.execute(
                f"""
                SELECT date_trunc('month', m.ts) AS bucket, COUNT(*) AS count
                FROM messages m
                JOIN channels c ON c.id = m.channel_id
                JOIN sources s ON c.source_id = s.id
                WHERE {where}
                GROUP BY bucket
                ORDER BY count DESC, bucket ASC
                LIMIT 1
                """,
                params,
            ).fetchone()
            most_active_day = con.execute(
                f"""
                SELECT date_trunc('day', m.ts) AS bucket, COUNT(*) AS count
                FROM messages m
                JOIN channels c ON c.id = m.channel_id
                JOIN sources s ON c.source_id = s.id
                WHERE {where}
                GROUP BY bucket
                ORDER BY count DESC, bucket ASC
                LIMIT 1
                """,
                params,
            ).fetchone()
            most_active_hour = con.execute(
                f"""
                SELECT date_trunc('hour', m.ts) AS bucket, COUNT(*) AS count
                FROM messages m
                JOIN channels c ON c.id = m.channel_id
                JOIN sources s ON c.source_id = s.id
                WHERE {where}
                GROUP BY bucket
                ORDER BY count DESC, bucket ASC
                LIMIT 1
                """,
                params,
            ).fetchone()
        total_messages = int(total[0] or 0)
        start_ts = total[1]
        end_ts = total[2]
        total_days = 1
        if start_ts and end_ts:
            total_days = max((end_ts.date() - start_ts.date()).days + 1, 1)
        average_per_day = float(total_messages / total_days) if total_messages else 0.0
        return {
            "total_messages": total_messages,
            "date_range": {
                "start": start_ts.isoformat() if start_ts else None,
                "end": end_ts.isoformat() if end_ts else None,
            },
            "message_stats": {
                "with_text": int(message_stats_row[1] or 0),
                "with_links": int(message_stats_row[2] or 0),
                "with_images": int(message_stats_row[3] or 0),
                "with_gifs": int(message_stats_row[4] or 0),
                "with_videos": int(message_stats_row[5] or 0),
                "with_stickers": int(message_stats_row[6] or 0),
                "with_audio_files": int(message_stats_row[7] or 0),
                "with_documents": int(message_stats_row[8] or 0),
                "with_other_files": int(message_stats_row[9] or 0),
                "edited_messages": int(message_stats_row[10] or 0),
                "average_per_day": average_per_day,
                "longest_period_without_messages_seconds": int(longest_gap_row[0] or 0),
                "longest_active_conversation_seconds": int(longest_active_row[0] or 0),
                "most_active_year": {
                    "bucket": most_active_year[0].isoformat()
                    if most_active_year
                    else None,
                    "count": int(most_active_year[1]) if most_active_year else 0,
                },
                "most_active_month": {
                    "bucket": most_active_month[0].isoformat()
                    if most_active_month
                    else None,
                    "count": int(most_active_month[1]) if most_active_month else 0,
                },
                "most_active_day": {
                    "bucket": most_active_day[0].isoformat()
                    if most_active_day
                    else None,
                    "count": int(most_active_day[1]) if most_active_day else 0,
                },
                "most_active_hour": {
                    "bucket": most_active_hour[0].isoformat()
                    if most_active_hour
                    else None,
                    "count": int(most_active_hour[1]) if most_active_hour else 0,
                },
            },
            "people": [
                {
                    "id": int(row[0]),
                    "display_name": row[1],
                    "color": row[2],
                    "message_count": int(row[3]),
                }
                for row in people_rows
            ],
        }

    @app.get("/api/messages-over-time")
    def messages_over_time(
        granularity: str = Query(default="day", pattern="^(day|week|month)$"),
        start: date | None = Query(default=None, alias="from"),
        end: date | None = Query(default=None, alias="to"),
        people: str | None = None,
        themes: str | None = None,
        platforms: str | None = None,
        metric: str = Query(default="messages", pattern="^(messages|words)$"),
    ) -> dict[str, Any]:
        metric = _count_metric(metric)
        filters = QueryFilters(
            start=start,
            end=end,
            people=_csv_ints(people, "people"),
            themes=_csv_ints(themes, "themes"),
            platforms=_csv_strings(platforms),
        )
        params: list[Any] = [granularity]
        where = _filters_clause(
            filters, params, app.state.reconciliation, app.state.theme_id_to_name
        )
        with _connect(app.state.db_path) as con:
            if metric == "words":
                rows = con.execute(
                    f"""
                    SELECT date_trunc(?, bucket_ts) AS bucket, SUM(word_count) AS message_count
                    FROM (
                        SELECT m.ts AS bucket_ts, {_word_count_expr()} AS word_count
                        FROM messages m
                        JOIN channels c ON c.id = m.channel_id
                        JOIN sources s ON c.source_id = s.id
                        WHERE {where}
                    )
                    GROUP BY bucket
                    ORDER BY bucket
                    """,
                    params,
                ).fetchall()
            else:
                rows = con.execute(
                    f"""
                    SELECT date_trunc(?, m.ts) AS bucket, COUNT(*) AS message_count
                    FROM messages m
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON c.source_id = s.id
                    WHERE {where}
                    GROUP BY bucket
                    ORDER BY bucket
                    """,
                    params,
                ).fetchall()
        return {
            "granularity": granularity,
            "points": [
                {"bucket": row[0].isoformat(), "message_count": int(row[1])}
                for row in rows
            ],
        }

    @app.get("/api/calendar")
    def calendar(
        start: date | None = Query(default=None, alias="from"),
        end: date | None = Query(default=None, alias="to"),
        people: str | None = None,
        themes: str | None = None,
        platforms: str | None = None,
        metric: str = Query(default="messages", pattern="^(messages|words)$"),
    ) -> dict[str, Any]:
        metric = _count_metric(metric)
        filters = QueryFilters(
            start=start,
            end=end,
            people=_csv_ints(people, "people"),
            themes=_csv_ints(themes, "themes"),
            platforms=_csv_strings(platforms),
        )
        params: list[Any] = []
        where = _filters_clause(
            filters, params, app.state.reconciliation, app.state.theme_id_to_name
        )
        with _connect(app.state.db_path) as con:
            if metric == "words":
                rows = con.execute(
                    f"""
                    SELECT CAST(bucket_ts AS DATE) AS day, SUM(word_count) AS message_count
                    FROM (
                        SELECT m.ts AS bucket_ts, {_word_count_expr()} AS word_count
                        FROM messages m
                        JOIN channels c ON c.id = m.channel_id
                        JOIN sources s ON c.source_id = s.id
                        WHERE {where}
                    )
                    GROUP BY day
                    ORDER BY day
                    """,
                    params,
                ).fetchall()
            else:
                rows = con.execute(
                    f"""
                    SELECT CAST(m.ts AS DATE) AS day, COUNT(*) AS message_count
                    FROM messages m
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON c.source_id = s.id
                    WHERE {where}
                    GROUP BY day
                    ORDER BY day
                    """,
                    params,
                ).fetchall()
        return {
            "points": [
                {"day": row[0].isoformat(), "message_count": int(row[1])}
                for row in rows
            ]
        }

    @app.get("/api/activity-heatmap")
    def activity_heatmap(
        start: date | None = Query(default=None, alias="from"),
        end: date | None = Query(default=None, alias="to"),
        people: str | None = None,
        themes: str | None = None,
        platforms: str | None = None,
        metric: str = Query(default="messages", pattern="^(messages|words)$"),
    ) -> dict[str, Any]:
        metric = _count_metric(metric)
        filters = QueryFilters(
            start=start,
            end=end,
            people=_csv_ints(people, "people"),
            themes=_csv_ints(themes, "themes"),
            platforms=_csv_strings(platforms),
        )
        params: list[Any] = []
        where = _filters_clause(
            filters, params, app.state.reconciliation, app.state.theme_id_to_name
        )
        with _connect(app.state.db_path) as con:
            if metric == "words":
                rows = con.execute(
                    f"""
                    SELECT EXTRACT(isodow FROM bucket_ts) AS weekday, EXTRACT(hour FROM bucket_ts) AS hour, SUM(word_count) AS message_count
                    FROM (
                        SELECT m.ts AS bucket_ts, {_word_count_expr()} AS word_count
                        FROM messages m
                        JOIN channels c ON c.id = m.channel_id
                        JOIN sources s ON c.source_id = s.id
                        WHERE {where}
                    )
                    GROUP BY weekday, hour
                    ORDER BY weekday, hour
                    """,
                    params,
                ).fetchall()
            else:
                rows = con.execute(
                    f"""
                    SELECT EXTRACT(isodow FROM m.ts) AS weekday, EXTRACT(hour FROM m.ts) AS hour, COUNT(*) AS message_count
                    FROM messages m
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON c.source_id = s.id
                    WHERE {where}
                    GROUP BY weekday, hour
                    ORDER BY weekday, hour
                    """,
                    params,
                ).fetchall()
        return {
            "points": [
                {
                    "weekday": int(row[0]),
                    "hour": int(row[1]),
                    "message_count": int(row[2]),
                }
                for row in rows
            ]
        }

    @app.get("/api/top-people")
    def top_people(
        limit: int = Query(default=10, ge=1, le=100),
        start: date | None = Query(default=None, alias="from"),
        end: date | None = Query(default=None, alias="to"),
        people: str | None = None,
        themes: str | None = None,
        platforms: str | None = None,
        metric: str = Query(default="messages", pattern="^(messages|words)$"),
    ) -> dict[str, Any]:
        metric = _count_metric(metric)
        filters = QueryFilters(
            start=start,
            end=end,
            people=_csv_ints(people, "people"),
            themes=_csv_ints(themes, "themes"),
            platforms=_csv_strings(platforms),
        )
        params: list[Any] = []
        where = _filters_clause(
            filters, params, app.state.reconciliation, app.state.theme_id_to_name
        )
        params.append(limit)
        with _connect(app.state.db_path) as con:
            if metric == "words":
                rows = con.execute(
                    f"""
                    SELECT p.id, p.display_name, p.color, SUM(word_count) AS message_count
                    FROM (
                        SELECT m.person_id, {_word_count_expr()} AS word_count
                        FROM messages m
                        JOIN channels c ON c.id = m.channel_id
                        JOIN sources s ON c.source_id = s.id
                        WHERE {where}
                    ) counted
                    JOIN people p ON p.id = counted.person_id
                    GROUP BY p.id, p.display_name, p.color
                    ORDER BY message_count DESC, p.display_name
                    LIMIT ?
                    """,
                    params,
                ).fetchall()
            else:
                rows = con.execute(
                    f"""
                    SELECT p.id, p.display_name, p.color, COUNT(*) AS message_count
                    FROM messages m
                    JOIN people p ON p.id = m.person_id
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON c.source_id = s.id
                    WHERE {where}
                    GROUP BY p.id, p.display_name, p.color
                    ORDER BY message_count DESC, p.display_name
                    LIMIT ?
                    """,
                    params,
                ).fetchall()
        return {
            "items": [
                {
                    "id": int(row[0]),
                    "display_name": row[1],
                    "color": row[2],
                    "message_count": int(row[3]),
                }
                for row in rows
            ]
        }

    @app.get("/api/metadata")
    def metadata() -> dict[str, Any]:
        """Get available filters from the database and reconciliation config."""
        with _connect(app.state.db_path) as con:
            people = con.execute(
                "SELECT id, display_name FROM people ORDER BY display_name, id"
            ).fetchall()
            platforms = con.execute(
                "SELECT DISTINCT platform FROM sources ORDER BY platform"
            ).fetchall()

        if app.state.configured_people_names:
            people = [
                row for row in people if row[1] in app.state.configured_people_names
            ]

        return {
            "people": [{"id": int(row[0]), "name": row[1]} for row in people],
            "themes": [
                {"id": int(theme_id), "name": theme_name}
                for theme_id, theme_name in sorted(app.state.theme_id_to_name.items())
            ],
            "platforms": [row[0] for row in platforms],
        }

    @app.get("/api/name-history")
    def name_history(
        start: date | None = Query(default=None, alias="from"),
        end: date | None = Query(default=None, alias="to"),
        people: str | None = None,
        platforms: str | None = None,
    ) -> dict[str, Any]:
        """Channel rename history and person nickname history."""
        people_filter = _csv_ints(people, "people")
        platforms_filter = _csv_strings(platforms)

        with _connect(app.state.db_path) as con:
            channel_params: list[Any] = []
            channel_where = ["1 = 1"]
            if platforms_filter:
                placeholders = ", ".join("?" for _ in platforms_filter)
                channel_where.append(f"s.platform IN ({placeholders})")
                channel_params.extend(platforms_filter)

            channel_rows = con.execute(
                f"""
                SELECT
                    c.id,
                    c.source_id,
                    s.platform,
                    s.name AS source_name,
                    c.name AS current_name,
                    c.platform_channel_id
                FROM channels c
                JOIN sources s ON s.id = c.source_id
                WHERE {" AND ".join(channel_where)}
                ORDER BY s.platform, s.name, c.name
                """,
                channel_params,
            ).fetchall()

            channel_change_params: list[Any] = []
            channel_change_where = ["trim(d.new_name) <> ''"]
            if start is not None:
                channel_change_where.append("d.ts >= ?")
                channel_change_params.append(datetime.combine(start, time.min))
            if end is not None:
                channel_change_where.append("d.ts < ?")
                channel_change_params.append(
                    datetime.combine(end + timedelta(days=1), time.min)
                )
            if platforms_filter:
                placeholders = ", ".join("?" for _ in platforms_filter)
                channel_change_where.append(f"s.platform IN ({placeholders})")
                channel_change_params.extend(platforms_filter)

            channel_change_rows = con.execute(
                f"""
                WITH deduped AS (
                    SELECT DISTINCT
                        channel_id,
                        source_id,
                        previous_name,
                        new_name,
                        ts,
                        json_extract_string(payload_json, '$.actor_name') AS actor_name,
                        coalesce(
                            json_extract_string(payload_json, '$.actor_raw_id'),
                            json_extract_string(payload_json, '$.updateMessage.groupChange.updates[0].groupNameUpdate.updaterAci')
                        ) AS actor_raw_id
                    FROM channel_name_changes
                )
                SELECT d.channel_id, d.source_id, s.platform, c.platform_channel_id, d.previous_name, d.new_name, d.ts, d.actor_name, d.actor_raw_id
                FROM deduped d
                JOIN channels c ON c.id = d.channel_id
                JOIN sources s ON s.id = d.source_id
                WHERE {" AND ".join(channel_change_where)}
                ORDER BY d.channel_id, d.ts, d.previous_name, d.new_name
                """,
                channel_change_params,
            ).fetchall()

            person_change_params: list[Any] = []
            person_change_where = ["1 = 1"]
            if start is not None:
                person_change_where.append("d.ts >= ?")
                person_change_params.append(datetime.combine(start, time.min))
            if end is not None:
                person_change_where.append("d.ts < ?")
                person_change_params.append(
                    datetime.combine(end + timedelta(days=1), time.min)
                )
            if people_filter:
                placeholders = ", ".join("?" for _ in people_filter)
                person_change_where.append(f"d.person_id IN ({placeholders})")
                person_change_params.extend(people_filter)
            if platforms_filter:
                placeholders = ", ".join("?" for _ in platforms_filter)
                person_change_where.append(f"s.platform IN ({placeholders})")
                person_change_params.extend(platforms_filter)

            person_change_rows = con.execute(
                f"""
                WITH deduped AS (
                    SELECT DISTINCT
                        person_id,
                        source_id,
                        json_extract_string(payload_json, '$.chatId') AS chat_id,
                        json_extract_string(payload_json, '$.actor_name') AS actor_name,
                        json_extract_string(payload_json, '$.actor_raw_id') AS actor_raw_id,
                        previous_name,
                        new_name,
                        ts
                    FROM person_name_changes
                )
                SELECT d.person_id, d.source_id, s.platform, d.chat_id, p.display_name, d.actor_name, d.actor_raw_id, d.previous_name, d.new_name, d.ts
                FROM deduped d
                JOIN people p ON p.id = d.person_id
                JOIN sources s ON s.id = d.source_id
                WHERE {" AND ".join(person_change_where)}
                ORDER BY d.source_id, d.chat_id, p.display_name, d.ts, d.previous_name, d.new_name
                """,
                person_change_params,
            ).fetchall()

            nickname_timeline_rows = con.execute(
                f"""
                WITH deduped AS (
                    SELECT DISTINCT
                        person_id,
                        source_id,
                        json_extract_string(payload_json, '$.chatId') AS chat_id,
                        new_name,
                        ts
                    FROM person_name_changes
                )
                SELECT d.person_id, d.source_id, s.platform, d.chat_id, d.new_name, d.ts
                FROM deduped d
                JOIN sources s ON s.id = d.source_id
                WHERE {" AND ".join(["1 = 1"] + ([f"s.platform IN ({', '.join('?' for _ in platforms_filter)})"] if platforms_filter else []))}
                ORDER BY d.source_id, d.chat_id, d.person_id, d.ts
                """,
                platforms_filter if platforms_filter else [],
            ).fetchall()

            identity_rows = con.execute(
                """
                SELECT pi.platform, pi.platform_user_id, p.id, p.display_name
                FROM platform_identities pi
                JOIN people p ON p.id = pi.person_id
                """
            ).fetchall()
            preferred_name_by_person_id: dict[int, str] = {}
            for platform, platform_user_id, person_id, display_name in identity_rows:
                key = (str(platform), str(platform_user_id))
                configured = app.state.reconciliation.people.identity_to_person.get(key)
                if configured:
                    preferred_name_by_person_id[int(person_id)] = configured[0]
                    continue
                candidate_name = str(display_name)
                if _normalized_history_name(candidate_name) not in {"", "you"}:
                    preferred_name_by_person_id.setdefault(
                        int(person_id), candidate_name
                    )
                    continue
                candidate_id = str(platform_user_id)
                if _normalized_history_name(candidate_id) not in {"", "you"}:
                    preferred_name_by_person_id.setdefault(int(person_id), candidate_id)
            identity_to_display_name = {
                (str(platform), str(platform_user_id)): str(display_name)
                for platform, platform_user_id, _, display_name in identity_rows
            }
            for (platform, raw_id), (
                configured_name,
                _color,
            ) in app.state.reconciliation.people.identity_to_person.items():
                key = (str(platform), str(raw_id))
                existing = identity_to_display_name.get(key)
                if existing is None or _normalized_history_name(existing) == "you":
                    identity_to_display_name[key] = configured_name
            for platform, platform_user_id, person_id, _display_name in identity_rows:
                key = (str(platform), str(platform_user_id))
                existing = identity_to_display_name.get(key)
                if existing and _normalized_history_name(existing) == "you":
                    preferred_name = preferred_name_by_person_id.get(int(person_id))
                    if preferred_name:
                        identity_to_display_name[key] = preferred_name
            identity_to_person_id = {
                (str(platform), str(platform_user_id)): int(person_id)
                for platform, platform_user_id, person_id, _ in identity_rows
            }

            nickname_timeline: dict[
                tuple[str, int, str, int], list[tuple[datetime | None, str]]
            ] = {}
            for (
                person_id,
                source_id,
                platform,
                chat_id,
                new_name,
                ts,
            ) in nickname_timeline_rows:
                if not chat_id:
                    continue
                key = (str(platform), int(source_id), str(chat_id), int(person_id))
                nickname_timeline.setdefault(key, []).append((ts, str(new_name or "")))
            for key in nickname_timeline:
                nickname_timeline[key].sort(key=lambda item: item[0] or datetime.min)

            def person_display_name(person_id: int, fallback_display_name: str) -> str:
                preferred = preferred_name_by_person_id.get(person_id)
                if preferred:
                    return preferred
                if (
                    _normalized_history_name(fallback_display_name) == "you"
                    and app.state.primary_person_name
                ):
                    return app.state.primary_person_name
                return fallback_display_name

            def actor_nickname_at(
                platform: str,
                source_id: int,
                chat_id: str | None,
                actor_raw_id: str | None,
                ts: datetime | None,
            ) -> str | None:
                if not chat_id or not actor_raw_id or ts is None:
                    return None
                person_id = identity_to_person_id.get((platform, actor_raw_id))
                if person_id is None:
                    return None
                events = nickname_timeline.get(
                    (platform, source_id, str(chat_id), person_id), []
                )
                nickname: str | None = None
                for event_ts, event_new_name in events:
                    if event_ts is None or event_ts > ts:
                        break
                    normalized = _normalized_history_name(event_new_name)
                    nickname = (
                        None if normalized in {"", "(cleared)"} else event_new_name
                    )
                return nickname

            channel_history_by_id: dict[int, list[dict[str, Any]]] = {}
            for (
                channel_id,
                source_id,
                platform,
                platform_chat_id,
                previous_name,
                new_name,
                ts,
                actor_name,
                actor_raw_id,
            ) in channel_change_rows:
                historical_actor_nickname = actor_nickname_at(
                    str(platform),
                    int(source_id),
                    str(platform_chat_id) if platform_chat_id is not None else None,
                    actor_raw_id,
                    ts,
                )
                channel_history_by_id.setdefault(int(channel_id), []).append(
                    {
                        "previous_name": previous_name,
                        "new_name": new_name,
                        "author_name": _format_history_actor_name(
                            actor_name,
                            actor_raw_id,
                            str(platform),
                            identity_to_display_name,
                            actor_nickname=historical_actor_nickname,
                            you_fallback_name=app.state.primary_person_name,
                        ),
                        "ts": ts.isoformat() if ts else None,
                    }
                )

            participants_by_chat: dict[tuple[int, str], dict[int, dict[str, Any]]] = {}
            for (
                person_id,
                source_id,
                platform,
                chat_id,
                display_name,
                actor_name,
                actor_raw_id,
                previous_name,
                new_name,
                ts,
            ) in person_change_rows:
                if not chat_id:
                    continue
                chat_key = (int(source_id), str(chat_id))
                person_entry = participants_by_chat.setdefault(chat_key, {}).setdefault(
                    int(person_id),
                    {
                        "id": int(person_id),
                        "display_name": person_display_name(
                            int(person_id), str(display_name)
                        ),
                        "history": [],
                    },
                )
                person_entry["history"].append(
                    {
                        "previous_name": previous_name,
                        "new_name": new_name,
                        "author_name": _format_history_actor_name(
                            actor_name,
                            actor_raw_id,
                            str(platform),
                            identity_to_display_name,
                            actor_nickname=actor_nickname_at(
                                str(platform),
                                int(source_id),
                                str(chat_id),
                                actor_raw_id,
                                ts,
                            ),
                            you_fallback_name=app.state.primary_person_name,
                        )
                        or display_name,
                        "ts": ts.isoformat() if ts else None,
                    }
                )

            chats: list[dict[str, Any]] = []
            signal_chat_ids: set[int] = set()
            if app.state.configured_people_names:
                signal_chat_rows = con.execute(
                    """
                    SELECT c.id, COUNT(DISTINCT p.id) AS configured_people_count
                    FROM messages m
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON s.id = c.source_id
                    JOIN people p ON p.id = m.person_id
                    WHERE s.platform = 'signal'
                      AND p.display_name IN ({})
                    GROUP BY c.id
                    """.format(
                        ", ".join("?" for _ in app.state.configured_people_names)
                    ),
                    sorted(app.state.configured_people_names),
                ).fetchall()
                signal_chat_ids = {
                    int(row[0]) for row in signal_chat_rows if int(row[1]) >= 2
                }

            for (
                channel_id,
                source_id,
                platform,
                source_name,
                current_name,
                platform_channel_id,
            ) in channel_rows:
                chat_key = (int(source_id), str(platform_channel_id))
                raw_previous_names = channel_history_by_id.get(int(channel_id), [])
                if not raw_previous_names:
                    continue
                previous_names: list[dict[str, Any]] = []
                has_real_rename = False
                for change in raw_previous_names:
                    previous_norm = _normalized_history_name(change["previous_name"])
                    new_norm = _normalized_history_name(change["new_name"])
                    if not new_norm:
                        continue
                    if previous_norm and previous_norm != new_norm:
                        has_real_rename = True
                    if not previous_norm or previous_norm != new_norm:
                        previous_names.append(change)
                if not has_real_rename or not previous_names:
                    continue
                if platform == "signal" and int(channel_id) not in signal_chat_ids:
                    continue
                participants = [
                    participant
                    for participant in sorted(
                        participants_by_chat.get(chat_key, {}).values(),
                        key=lambda item: item["display_name"].casefold(),
                    )
                    if participant["history"]
                ]
                chats.append(
                    {
                        "id": int(channel_id),
                        "platform": platform,
                        "source_name": source_name,
                        "current_name": _get_display_name(
                            current_name, source_name, app.state.fb_chat_names
                        ),
                        "platform_channel_id": platform_channel_id,
                        "previous_names": previous_names,
                        "participants": participants,
                    }
                )

            return {"chats": chats}

    @app.get("/api/top-chats")
    def top_chats(
        limit: int = Query(default=10, ge=1, le=100),
        start: date | None = Query(default=None, alias="from"),
        end: date | None = Query(default=None, alias="to"),
        people: str | None = None,
        themes: str | None = None,
        platforms: str | None = None,
        metric: str = Query(default="messages", pattern="^(messages|words)$"),
    ) -> dict[str, Any]:
        """Top chats (channels) by message count."""
        metric = _count_metric(metric)
        filters = QueryFilters(
            start=start,
            end=end,
            people=_csv_ints(people, "people"),
            themes=_csv_ints(themes, "themes"),
            platforms=_csv_strings(platforms),
        )
        params: list[Any] = []
        where = _filters_clause(
            filters, params, app.state.reconciliation, app.state.theme_id_to_name
        )
        params.append(limit)
        with _connect(app.state.db_path) as con:
            if metric == "words":
                rows = con.execute(
                    f"""
                    SELECT c.id, c.name, t.name as theme_name, s.name as source_name, SUM(word_count) AS message_count
                    FROM (
                        SELECT m.channel_id, {_word_count_expr()} AS word_count
                        FROM messages m
                        JOIN channels c ON c.id = m.channel_id
                        JOIN sources s ON c.source_id = s.id
                        WHERE {where}
                    ) counted
                    JOIN channels c ON c.id = counted.channel_id
                    JOIN themes t ON t.id = c.theme_id
                    JOIN sources s ON c.source_id = s.id
                    GROUP BY c.id, c.name, t.name, s.name
                    ORDER BY message_count DESC, c.name
                    LIMIT ?
                    """,
                    params,
                ).fetchall()
            else:
                rows = con.execute(
                    f"""
                    SELECT c.id, c.name, t.name as theme_name, s.name as source_name, COUNT(*) AS message_count
                    FROM messages m
                    JOIN channels c ON c.id = m.channel_id
                    JOIN themes t ON t.id = c.theme_id
                    JOIN sources s ON c.source_id = s.id
                    WHERE {where}
                    GROUP BY c.id, c.name, t.name, s.name
                    ORDER BY message_count DESC, c.name
                    LIMIT ?
                    """,
                    params,
                ).fetchall()
        return {
            "items": [
                {
                    "id": int(row[0]),
                    "name": _get_display_name(row[1], row[3], app.state.fb_chat_names),
                    "theme_name": row[2],
                    "message_count": int(row[4]),
                }
                for row in rows
            ]
        }

    @app.get("/api/top-themes")
    def top_themes(
        limit: int = Query(default=10, ge=1, le=100),
        start: date | None = Query(default=None, alias="from"),
        end: date | None = Query(default=None, alias="to"),
        people: str | None = None,
        themes: str | None = None,
        platforms: str | None = None,
        metric: str = Query(default="messages", pattern="^(messages|words)$"),
    ) -> dict[str, Any]:
        """Top configured themes by message count."""
        metric = _count_metric(metric)
        filters = QueryFilters(
            start=start,
            end=end,
            people=_csv_ints(people, "people"),
            themes=_csv_ints(themes, "themes"),
            platforms=_csv_strings(platforms),
        )
        configured_themes = app.state.reconciliation.themes.configured_theme_names
        if not configured_themes:
            return {"items": []}

        params: list[Any] = []
        where = _filters_clause(
            filters, params, app.state.reconciliation, app.state.theme_id_to_name
        )

        with _connect(app.state.db_path) as con:
            if metric == "words":
                rows = con.execute(
                    f"""
                    SELECT s.name, c.name, SUM(word_count) as message_count
                    FROM (
                        SELECT m.channel_id, {_word_count_expr()} AS word_count
                        FROM messages m
                        JOIN channels c ON c.id = m.channel_id
                        JOIN sources s ON c.source_id = s.id
                        WHERE {where}
                    ) counted
                    JOIN channels c ON c.id = counted.channel_id
                    JOIN sources s ON s.id = c.source_id
                    GROUP BY s.name, c.name
                    """,
                    params,
                ).fetchall()
            else:
                rows = con.execute(
                    f"""
                    SELECT s.name, c.name, COUNT(*) as message_count
                    FROM messages m
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON c.source_id = s.id
                    WHERE {where}
                    GROUP BY s.name, c.name
                    """,
                    params,
                ).fetchall()

            theme_counts = {}
            for source_name, channel_name, count in rows:
                theme_name = app.state.reconciliation.themes.resolve(
                    source_name, channel_name
                )
                if theme_name in configured_themes:
                    theme_counts[theme_name] = theme_counts.get(theme_name, 0) + count

        theme_list = sorted(theme_counts.items(), key=lambda x: x[1], reverse=True)[
            :limit
        ]

        return {
            "items": [
                {"id": 0, "name": name, "message_count": count}
                for name, count in theme_list
            ]
        }

    @app.get("/api/top-words")
    def top_words(
        limit: int = Query(default=200, ge=1, le=100000),
        all: bool = False,
        q: str | None = None,
        start: date | None = Query(default=None, alias="from"),
        end: date | None = Query(default=None, alias="to"),
        people: str | None = None,
        themes: str | None = None,
        platforms: str | None = None,
    ) -> dict[str, Any]:
        """Most used words under current filters."""
        filters = QueryFilters(
            start=start,
            end=end,
            people=_csv_ints(people, "people"),
            themes=_csv_ints(themes, "themes"),
            platforms=_csv_strings(platforms),
        )
        params: list[Any] = []
        where = _filters_clause(
            filters, params, app.state.reconciliation, app.state.theme_id_to_name
        )

        stop_words = sorted(COMMON_STOP_WORDS)
        stop_placeholders = ", ".join("?" for _ in stop_words)
        params.extend(stop_words)

        q_clause = ""
        if q:
            query = q.strip().casefold()
            if query:
                q_clause = " AND word LIKE ?"
                params.append(f"%{query}%")

        limit_clause = ""
        if not all:
            limit_clause = " LIMIT ?"
            params.append(limit)
        with _connect(app.state.db_path) as con:
            rows = con.execute(
                f"""
                WITH tokens AS (
                    SELECT unnest(
                        regexp_extract_all(
                            replace(lower(coalesce(m.content, '')), chr(39), ''),
                            '[a-z]{{3,}}'
                        )
                    ) AS word
                    FROM messages m
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON c.source_id = s.id
                    WHERE {where} AND m.content IS NOT NULL AND m.content <> ''
                )
                SELECT word, COUNT(*) AS usage_count
                FROM tokens
                WHERE word NOT IN ({stop_placeholders}){q_clause}
                GROUP BY word
                ORDER BY usage_count DESC, word
                {limit_clause}
                """,
                params,
            ).fetchall()
        return {"items": [{"word": row[0], "count": int(row[1])} for row in rows]}

    @app.get("/api/word-breakdown")
    def word_breakdown(
        word: str,
        limit: int = Query(default=10, ge=1, le=100),
        start: date | None = Query(default=None, alias="from"),
        end: date | None = Query(default=None, alias="to"),
        people: str | None = None,
        themes: str | None = None,
        platforms: str | None = None,
    ) -> dict[str, Any]:
        """Who and which chats used a selected word the most."""
        normalized = "".join(ch for ch in word.casefold() if "a" <= ch <= "z")
        if len(normalized) < 3:
            raise HTTPException(
                status_code=400, detail="Word must contain at least 3 letters"
            )

        filters = QueryFilters(
            start=start,
            end=end,
            people=_csv_ints(people, "people"),
            themes=_csv_ints(themes, "themes"),
            platforms=_csv_strings(platforms),
        )
        params: list[Any] = []
        where = _filters_clause(
            filters, params, app.state.reconciliation, app.state.theme_id_to_name
        )

        with _connect(app.state.db_path) as con:
            people_rows = con.execute(
                f"""
                WITH tokens AS (
                    SELECT
                        m.person_id,
                        c.id AS channel_id,
                        c.name AS channel_name,
                        s.name AS source_name,
                        unnest(
                            regexp_extract_all(
                                replace(lower(coalesce(m.content, '')), chr(39), ''),
                                '[a-z]{{3,}}'
                            )
                        ) AS token
                    FROM messages m
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON c.source_id = s.id
                    WHERE {where} AND m.content IS NOT NULL AND m.content <> ''
                )
                SELECT p.id, p.display_name, p.color, COUNT(*) AS usage_count
                FROM tokens t
                JOIN people p ON p.id = t.person_id
                WHERE t.token = ?
                GROUP BY p.id, p.display_name, p.color
                ORDER BY usage_count DESC, p.display_name
                LIMIT ?
                """,
                [*params, normalized, limit],
            ).fetchall()

            chat_rows = con.execute(
                f"""
                WITH tokens AS (
                    SELECT
                        m.person_id,
                        c.id AS channel_id,
                        c.name AS channel_name,
                        s.name AS source_name,
                        unnest(
                            regexp_extract_all(
                                replace(lower(coalesce(m.content, '')), chr(39), ''),
                                '[a-z]{{3,}}'
                            )
                        ) AS token
                    FROM messages m
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON c.source_id = s.id
                    WHERE {where} AND m.content IS NOT NULL AND m.content <> ''
                )
                SELECT channel_id, channel_name, source_name, COUNT(*) AS usage_count
                FROM tokens
                WHERE token = ?
                GROUP BY channel_id, channel_name, source_name
                ORDER BY usage_count DESC, channel_name
                LIMIT ?
                """,
                [*params, normalized, limit],
            ).fetchall()

        return {
            "word": normalized,
            "people": [
                {
                    "id": int(row[0]),
                    "display_name": row[1],
                    "color": row[2],
                    "count": int(row[3]),
                }
                for row in people_rows
            ],
            "chats": [
                {
                    "id": int(row[0]),
                    "name": _get_display_name(row[1], row[2], app.state.fb_chat_names),
                    "source_name": row[2],
                    "count": int(row[3]),
                }
                for row in chat_rows
            ],
        }

    @app.get("/api/word-examples")
    def word_examples(
        word: str,
        limit: int = Query(default=6, ge=1, le=25),
        offset: int = Query(default=0, ge=0, le=1000),
        start: date | None = Query(default=None, alias="from"),
        end: date | None = Query(default=None, alias="to"),
        people: str | None = None,
        themes: str | None = None,
        platforms: str | None = None,
    ) -> dict[str, Any]:
        """A few example messages containing a selected word."""
        normalized = "".join(ch for ch in word.casefold() if "a" <= ch <= "z")
        if len(normalized) < 3:
            raise HTTPException(
                status_code=400, detail="Word must contain at least 3 letters"
            )

        filters = QueryFilters(
            start=start,
            end=end,
            people=_csv_ints(people, "people"),
            themes=_csv_ints(themes, "themes"),
            platforms=_csv_strings(platforms),
        )
        params: list[Any] = []
        where = _filters_clause(
            filters, params, app.state.reconciliation, app.state.theme_id_to_name
        )

        with _connect(app.state.db_path) as con:
            rows = con.execute(
                f"""
                WITH tokens AS (
                    SELECT
                        m.id AS message_id,
                        m.ts,
                        m.content,
                        p.display_name AS person_name,
                        p.color AS person_color,
                        c.name AS channel_name,
                        s.name AS source_name,
                        unnest(
                            regexp_extract_all(
                                replace(lower(coalesce(m.content, '')), chr(39), ''),
                                '[a-z]{{3,}}'
                            )
                        ) AS token
                    FROM messages m
                    JOIN people p ON p.id = m.person_id
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON s.id = c.source_id
                    WHERE {where} AND m.content IS NOT NULL AND m.content <> ''
                )
                SELECT DISTINCT message_id, ts, content, person_name, person_color, channel_name, source_name
                FROM tokens
                WHERE token = ?
                ORDER BY ts DESC, message_id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, normalized, limit, offset],
            ).fetchall()

        return {
            "word": normalized,
            "has_more": len(rows) == limit,
            "messages": [
                {
                    "id": row[0],
                    "ts": row[1].isoformat() if row[1] else None,
                    "content": row[2],
                    "person_name": row[3],
                    "person_color": row[4],
                    "channel_name": _get_display_name(
                        row[5], row[6], app.state.fb_chat_names
                    ),
                    "source_name": row[6],
                }
                for row in rows
            ],
        }

    @app.get("/api/linked-domains")
    def linked_domains(
        limit: int = Query(default=200, ge=1, le=1000),
        start: date | None = Query(default=None, alias="from"),
        end: date | None = Query(default=None, alias="to"),
        people: str | None = None,
        themes: str | None = None,
        platforms: str | None = None,
    ) -> dict[str, Any]:
        """Most linked domains by total link count."""
        filters = QueryFilters(
            start=start,
            end=end,
            people=_csv_ints(people, "people"),
            themes=_csv_ints(themes, "themes"),
            platforms=_csv_strings(platforms),
        )
        params: list[Any] = []
        where = _filters_clause(
            filters, params, app.state.reconciliation, app.state.theme_id_to_name
        )

        with _connect(app.state.db_path) as con:
            rows = con.execute(
                f"""
                WITH links AS (
                    SELECT
                        {_canonical_link_domain_expr("link")} AS domain
                    FROM messages m
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON c.source_id = s.id
                    CROSS JOIN unnest(
                        regexp_extract_all(coalesce(m.content, ''), 'https?://([^/?#\\s]+)', 1)
                    ) AS t(link)
                    WHERE {where} AND m.content IS NOT NULL AND m.content <> ''
                )
                SELECT domain, COUNT(*) AS link_count
                FROM links
                WHERE domain IS NOT NULL AND domain <> ''
                GROUP BY domain
                ORDER BY link_count DESC, domain
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()

        return {"items": [{"domain": row[0], "count": int(row[1])} for row in rows]}

    @app.get("/api/links-by-author")
    def links_by_author(
        limit: int = Query(default=15, ge=1, le=100),
        start: date | None = Query(default=None, alias="from"),
        end: date | None = Query(default=None, alias="to"),
        people: str | None = None,
        themes: str | None = None,
        platforms: str | None = None,
    ) -> dict[str, Any]:
        """Authors ranked by total links sent."""
        filters = QueryFilters(
            start=start,
            end=end,
            people=_csv_ints(people, "people"),
            themes=_csv_ints(themes, "themes"),
            platforms=_csv_strings(platforms),
        )
        params: list[Any] = []
        where = _filters_clause(
            filters, params, app.state.reconciliation, app.state.theme_id_to_name
        )

        with _connect(app.state.db_path) as con:
            rows = con.execute(
                f"""
                WITH links AS (
                    SELECT
                        m.person_id,
                        {_canonical_link_domain_expr("link")} AS domain
                    FROM messages m
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON c.source_id = s.id
                    CROSS JOIN unnest(
                        regexp_extract_all(coalesce(m.content, ''), 'https?://([^/?#\\s]+)', 1)
                    ) AS t(link)
                    WHERE {where} AND m.content IS NOT NULL AND m.content <> ''
                )
                SELECT p.id, p.display_name, p.color, COUNT(*) AS link_count
                FROM links l
                JOIN people p ON p.id = l.person_id
                WHERE l.domain IS NOT NULL AND l.domain <> ''
                GROUP BY p.id, p.display_name, p.color
                ORDER BY link_count DESC, p.display_name
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()

        return {
            "items": [
                {
                    "id": int(row[0]),
                    "display_name": row[1],
                    "color": row[2],
                    "count": int(row[3]),
                }
                for row in rows
            ]
        }

    @app.get("/api/most-mentioned")
    def most_mentioned(
        limit: int = Query(default=200, ge=1, le=1000),
        start: date | None = Query(default=None, alias="from"),
        end: date | None = Query(default=None, alias="to"),
        people: str | None = None,
        themes: str | None = None,
        platforms: str | None = None,
    ) -> dict[str, Any]:
        """Most mentioned names in messages."""
        filters = QueryFilters(
            start=start,
            end=end,
            people=_csv_ints(people, "people"),
            themes=_csv_ints(themes, "themes"),
            platforms=_csv_strings(platforms),
        )
        params: list[Any] = []
        where = _filters_clause(
            filters, params, app.state.reconciliation, app.state.theme_id_to_name
        )

        with _connect(app.state.db_path) as con:
            rows = con.execute(
                f"""
                WITH mentions AS (
                    SELECT
                        lower(mention) AS mention
                    FROM messages m
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON c.source_id = s.id
                    CROSS JOIN unnest(
                        regexp_extract_all(coalesce(m.content, ''), '@([A-Za-z0-9_]+)', 1)
                    ) AS t(mention)
                    WHERE {where} AND m.content IS NOT NULL AND m.content <> ''
                )
                SELECT mention, COUNT(*) AS mention_count
                FROM mentions
                WHERE mention IS NOT NULL AND mention <> ''
                GROUP BY mention
                ORDER BY mention_count DESC, mention
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()

        return {
            "items": [{"mention": f"@{row[0]}", "count": int(row[1])} for row in rows]
        }

    @app.get("/api/top-reacted-messages")
    def top_reacted_messages(
        limit: int = Query(default=6, ge=1, le=50),
        start: date | None = Query(default=None, alias="from"),
        end: date | None = Query(default=None, alias="to"),
        people: str | None = None,
        themes: str | None = None,
        platforms: str | None = None,
    ) -> dict[str, Any]:
        """Messages with the most total reactions."""
        filters = QueryFilters(
            start=start,
            end=end,
            people=_csv_ints(people, "people"),
            themes=_csv_ints(themes, "themes"),
            platforms=_csv_strings(platforms),
        )
        params: list[Any] = []
        where = _filters_clause(
            filters, params, app.state.reconciliation, app.state.theme_id_to_name
        )
        attachment_preview_select = (
            "m.attachment_preview" if app.state.has_attachment_preview else "NULL::TEXT"
        )
        reaction_summary_select = (
            "m.reaction_summary" if app.state.has_reaction_summary else "NULL::TEXT"
        )
        reaction_details_select = (
            "m.reaction_details_json"
            if app.state.has_reaction_details_json
            else "NULL::TEXT"
        )

        with _connect(app.state.db_path) as con:
            rows = con.execute(
                f"""
                SELECT
                    m.id,
                    m.ts,
                    m.content,
                    m.attachment_count,
                    {attachment_preview_select} AS attachment_preview,
                    p.display_name,
                    p.color,
                    c.name,
                    s.name,
                    m.reaction_count,
                    {reaction_summary_select} AS reaction_summary,
                    {reaction_details_select} AS reaction_details_json
                FROM messages m
                JOIN people p ON p.id = m.person_id
                JOIN channels c ON c.id = m.channel_id
                JOIN sources s ON s.id = c.source_id
                WHERE {where} AND m.reaction_count > 0
                ORDER BY m.reaction_count DESC, m.ts DESC, m.id DESC
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()

        return {
            "items": [
                {
                    "id": row[0],
                    "ts": row[1].isoformat() if row[1] else None,
                    "content": str(row[2] or "").strip(),
                    "attachment_preview": row[4],
                    "attachment_url": _resolve_local_attachment_url(
                        row[4],
                        row[8],
                        app.state.data_dir,
                        app.state.signal_filename_index,
                    ),
                    "person_name": row[5],
                    "person_color": row[6],
                    "channel_name": _get_display_name(
                        row[7], row[8], app.state.fb_chat_names
                    ),
                    "source_name": row[8],
                    "reaction_count": int(row[9]),
                    "reaction_summary": row[10],
                    "reaction_details": _normalize_reaction_details(
                        row[11],
                        row[8],
                        app.state.data_dir,
                        app.state.signal_filename_index,
                    ),
                }
                for row in rows
            ]
        }

    @app.get("/api/reaction-authors")
    def reaction_authors(
        limit: int = Query(default=15, ge=1, le=100),
        start: date | None = Query(default=None, alias="from"),
        end: date | None = Query(default=None, alias="to"),
        people: str | None = None,
        themes: str | None = None,
        platforms: str | None = None,
    ) -> dict[str, Any]:
        """Authors ranked by reactions received on their messages."""
        filters = QueryFilters(
            start=start,
            end=end,
            people=_csv_ints(people, "people"),
            themes=_csv_ints(themes, "themes"),
            platforms=_csv_strings(platforms),
        )
        params: list[Any] = []
        where = _filters_clause(
            filters, params, app.state.reconciliation, app.state.theme_id_to_name
        )

        with _connect(app.state.db_path) as con:
            rows = con.execute(
                f"""
                SELECT p.id, p.display_name, p.color, SUM(m.reaction_count) AS reaction_count
                FROM messages m
                JOIN people p ON p.id = m.person_id
                JOIN channels c ON c.id = m.channel_id
                JOIN sources s ON s.id = c.source_id
                WHERE {where}
                GROUP BY p.id, p.display_name, p.color
                HAVING SUM(m.reaction_count) > 0
                ORDER BY reaction_count DESC, p.display_name
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()

        return {
            "items": [
                {
                    "id": int(row[0]),
                    "display_name": row[1],
                    "color": row[2],
                    "count": int(row[3]),
                }
                for row in rows
            ]
        }

    @app.get("/api/messages-by-month")
    def messages_by_month(
        start: date | None = Query(default=None, alias="from"),
        end: date | None = Query(default=None, alias="to"),
        people: str | None = None,
        themes: str | None = None,
        platforms: str | None = None,
        metric: str = Query(default="messages", pattern="^(messages|words)$"),
    ) -> dict[str, Any]:
        """Messages per month over all time."""
        metric = _count_metric(metric)
        filters = QueryFilters(
            start=start,
            end=end,
            people=_csv_ints(people, "people"),
            themes=_csv_ints(themes, "themes"),
            platforms=_csv_strings(platforms),
        )
        params: list[Any] = []
        where = _filters_clause(
            filters, params, app.state.reconciliation, app.state.theme_id_to_name
        )
        with _connect(app.state.db_path) as con:
            if metric == "words":
                rows = con.execute(
                    f"""
                    SELECT date_trunc('month', bucket_ts) AS month, SUM(word_count) AS message_count
                    FROM (
                        SELECT m.ts AS bucket_ts, {_word_count_expr()} AS word_count
                        FROM messages m
                        JOIN channels c ON c.id = m.channel_id
                        JOIN sources s ON c.source_id = s.id
                        WHERE {where}
                    )
                    GROUP BY month
                    ORDER BY month
                    """,
                    params,
                ).fetchall()
            else:
                rows = con.execute(
                    f"""
                    SELECT date_trunc('month', m.ts) AS month, COUNT(*) AS message_count
                    FROM messages m
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON c.source_id = s.id
                    WHERE {where}
                    GROUP BY month
                    ORDER BY month
                    """,
                    params,
                ).fetchall()
        return {
            "points": [
                {
                    "month": row[0].isoformat() if row[0] else None,
                    "message_count": int(row[1]),
                }
                for row in rows
            ]
        }

    @app.get("/api/messages-by-hour")
    def messages_by_hour(
        start: date | None = Query(default=None, alias="from"),
        end: date | None = Query(default=None, alias="to"),
        people: str | None = None,
        themes: str | None = None,
        platforms: str | None = None,
        metric: str = Query(default="messages", pattern="^(messages|words)$"),
    ) -> dict[str, Any]:
        """Messages by hour of day (0-23)."""
        metric = _count_metric(metric)
        filters = QueryFilters(
            start=start,
            end=end,
            people=_csv_ints(people, "people"),
            themes=_csv_ints(themes, "themes"),
            platforms=_csv_strings(platforms),
        )
        params: list[Any] = []
        where = _filters_clause(
            filters, params, app.state.reconciliation, app.state.theme_id_to_name
        )
        with _connect(app.state.db_path) as con:
            if metric == "words":
                rows = con.execute(
                    f"""
                    SELECT EXTRACT(hour FROM bucket_ts) AS hour, SUM(word_count) AS message_count
                    FROM (
                        SELECT m.ts AS bucket_ts, {_word_count_expr()} AS word_count
                        FROM messages m
                        JOIN channels c ON c.id = m.channel_id
                        JOIN sources s ON c.source_id = s.id
                        WHERE {where}
                    )
                    GROUP BY hour
                    ORDER BY hour
                    """,
                    params,
                ).fetchall()
            else:
                rows = con.execute(
                    f"""
                    SELECT EXTRACT(hour FROM m.ts) AS hour, COUNT(*) AS message_count
                    FROM messages m
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON c.source_id = s.id
                    WHERE {where}
                    GROUP BY hour
                    ORDER BY hour
                    """,
                    params,
                ).fetchall()
        return {
            "points": [
                {"hour": int(row[0]), "message_count": int(row[1])} for row in rows
            ]
        }

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
