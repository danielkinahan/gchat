from __future__ import annotations

import hashlib
import hmac as _hmac
import ipaddress
import json
import os
import socket
import threading
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlencode, urljoin, urlparse

import duckdb
import httpx2 as httpx
import uvicorn
import yaml
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response

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

_LINK_PREVIEW_CACHE_LOCK = threading.Lock()
_LINK_PREVIEW_CACHE: dict[str, dict[str, Any]] = {}
_LINK_PREVIEW_TTL_SECONDS = 60 * 60 * 24 * 7  # cache successes for 7 days
_LINK_PREVIEW_ERROR_TTL_SECONDS = 60 * 30  # cache failures for 30 minutes
_LINK_PREVIEW_TIMEOUT_SECONDS = 6.0
_LINK_PREVIEW_MAX_BYTES = 1024 * 1024  # only parse the first 1 MiB
_LINK_PREVIEW_USER_AGENT = (
    "Mozilla/5.0 (compatible; gchat-link-preview/1.0; +https://github.com/gchat)"
)


def _is_safe_link_host(host: str) -> bool:
    """Reject obvious internal/loopback hosts to mitigate SSRF abuse."""
    if not host:
        return False
    lowered = host.split(":")[0].strip().lower()
    if lowered in {"localhost", "broadcasthost"} or lowered.endswith(".local"):
        return False
    try:
        addresses = socket.getaddrinfo(lowered, None)
    except OSError:
        return False
    for entry in addresses:
        try:
            ip = ipaddress.ip_address(entry[4][0])
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False
    return True


def _select_link_preview_meta(
    soup: BeautifulSoup, names: list[str]
) -> str | None:
    """Find the first matching <meta> tag content for the given property names."""
    for name in names:
        tag = soup.find("meta", attrs={"property": name})
        if tag is None:
            tag = soup.find("meta", attrs={"name": name})
        if tag is None:
            continue
        content = tag.get("content")
        if isinstance(content, str):
            stripped = content.strip()
            if stripped:
                return stripped
    return None


def _fetch_link_preview(url: str) -> dict[str, Any]:
    """Fetch the URL and parse OpenGraph/HTML metadata into a preview payload."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("URL must be http(s) with a host")
    if not _is_safe_link_host(parsed.netloc):
        raise ValueError("Host is not allowed")

    headers = {
        "User-Agent": _LINK_PREVIEW_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    with httpx.Client(
        timeout=_LINK_PREVIEW_TIMEOUT_SECONDS,
        follow_redirects=True,
        headers=headers,
    ) as client:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if "html" not in content_type.lower():
                raise ValueError(f"Unsupported content-type: {content_type}")
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                chunks.append(chunk)
                total += len(chunk)
                if total >= _LINK_PREVIEW_MAX_BYTES:
                    break
            raw = b"".join(chunks)[:_LINK_PREVIEW_MAX_BYTES]
            final_url = str(response.url)
            encoding = response.encoding or "utf-8"
    try:
        body = raw.decode(encoding, errors="replace")
    except LookupError:
        body = raw.decode("utf-8", errors="replace")
    soup = BeautifulSoup(body, "html.parser")

    title = _select_link_preview_meta(soup, ["og:title", "twitter:title"])
    if not title:
        title_tag = soup.find("title")
        if title_tag is not None and title_tag.string:
            title = title_tag.string.strip()
    description = _select_link_preview_meta(
        soup, ["og:description", "twitter:description", "description"]
    )
    image = _select_link_preview_meta(
        soup, ["og:image", "og:image:url", "twitter:image", "twitter:image:src"]
    )
    if image:
        image = urljoin(final_url, image)
    site_name = _select_link_preview_meta(soup, ["og:site_name", "application-name"])
    if not site_name:
        site_name = urlparse(final_url).netloc
    favicon: str | None = None
    icon_link = soup.find(
        "link", rel=lambda value: bool(value) and "icon" in value.lower()
    )
    if icon_link is not None:
        href = icon_link.get("href")
        if isinstance(href, str) and href.strip():
            favicon = urljoin(final_url, href.strip())

    return {
        "url": url,
        "resolved_url": final_url,
        "title": title,
        "description": description,
        "image": image,
        "site_name": site_name,
        "favicon": favicon,
    }


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
            WHEN lower({column}) IN ('soundcloud.com', 'www.soundcloud.com', 'm.soundcloud.com', 'on.soundcloud.com', 'snd.sc', 'api.soundcloud.com') THEN 'soundcloud.com'
            ELSE lower({column})
        END"""


def _count_metric(metric: str) -> str:
    normalized = metric.strip().casefold()
    if normalized not in {"messages", "words", "conversations"}:
        raise HTTPException(status_code=400, detail="Invalid metric filter")
    return normalized


def _count_metric_expr(metric: str) -> str:
    if metric == "words":
        return "SUM(word_count)"
    if metric == "conversations":
        return "COUNT(DISTINCT m.conversation_id)"
    return "COUNT(*)"


@dataclass(frozen=True)
class _MetricSql:
    """SQL fragments needed to render one of the messages/words/conversations metrics.

    Endpoints render their queries as `SELECT {agg} ... FROM (SELECT ... {inner_select_suffix}
    FROM messages m ... WHERE {where}{extra_where}) ...`. The inner subquery shape
    is identical across metrics; only the projected `word_count` column and the
    outer aggregate differ. This collapses the three-way if/elif/else blocks that
    used to ship in every metric-aware endpoint.
    """

    aggregate: str
    inner_select_suffix: str
    extra_where: str


def _metric_sql(metric: str) -> _MetricSql:
    if metric == "words":
        return _MetricSql(
            aggregate="SUM(word_count)",
            inner_select_suffix=(
                f", m.conversation_id, {_word_count_expr()} AS word_count"
            ),
            extra_where="",
        )
    if metric == "conversations":
        return _MetricSql(
            aggregate="COUNT(DISTINCT conversation_id)",
            inner_select_suffix=", m.conversation_id",
            extra_where=" AND m.conversation_id IS NOT NULL",
        )
    return _MetricSql(
        aggregate="COUNT(*)",
        inner_select_suffix=", m.conversation_id",
        extra_where="",
    )


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


def _load_people_extra() -> dict[str, dict[str, str]]:
    """Return {name: {avatar, color}} from people.yaml for metadata enrichment."""
    config_path = _default_config_dir() / "people.yaml"
    if not config_path.exists():
        return {}
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    result: dict[str, dict[str, str]] = {}
    for person in data.get("people", []):
        if not isinstance(person, dict) or "name" not in person:
            continue
        name = str(person["name"])
        result[name] = {
            "color": str(person.get("color") or ""),
            "avatar": str(person.get("avatar") or ""),
        }
    return result


def _load_themes_extra() -> dict[str, str]:
    """Return {theme_name: emoji} from themes.yaml."""
    config_path = _default_config_dir() / "themes.yaml"
    if not config_path.exists():
        return {}
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    result: dict[str, str] = {}
    for theme in data.get("themes", []):
        if not isinstance(theme, dict) or "name" not in theme:
            continue
        name = str(theme["name"])
        emoji = str(theme.get("emoji") or "")
        if emoji:
            result[name] = emoji
    return result


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
    app.state.has_conversation_id = _messages_has_column(
        app.state.db_path, "conversation_id"
    )
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
            app.state.has_conversation_id = _messages_has_column(
                app.state.db_path, "conversation_id"
            )
            app.state.signal_filename_index = _build_signal_filename_index(
                app.state.data_dir
            )
            app.state._runtime_signature = current_signature
            global _THEME_CHANNEL_IDS
            _THEME_CHANNEL_IDS = app.state.theme_to_channel_ids

    # ── Password auth ────────────────────────────────────────────────────────
    # Set GCHAT_PASSWORD in the environment to enable. Leave unset for open access.
    _password = os.environ.get("GCHAT_PASSWORD", "").strip()
    _COOKIE_NAME = "gchat_auth"
    _COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days

    def _auth_token() -> str:
        return hashlib.sha256(f"gchat:{_password}".encode()).hexdigest()

    def _is_authenticated(request: Request) -> bool:
        if not _password:
            return True
        token = request.cookies.get(_COOKIE_NAME, "")
        return _hmac.compare_digest(token, _auth_token())

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        if request.url.path.startswith("/api/auth/"):
            return await call_next(request)
        if not _is_authenticated(request):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return await call_next(request)

    @app.get("/api/auth/status")
    def auth_status(request: Request) -> dict[str, bool]:
        return {
            "required": bool(_password),
            "authenticated": _is_authenticated(request),
        }

    @app.post("/api/auth/login")
    async def auth_login(request: Request, response: Response) -> dict[str, bool]:
        body: dict[str, Any] = {}
        try:
            body = await request.json()
        except Exception:
            pass
        submitted = str(body.get("password", "")).strip()
        if _password and not _hmac.compare_digest(submitted, _password):
            raise HTTPException(status_code=401, detail="Incorrect password")
        response.set_cookie(
            _COOKIE_NAME,
            _auth_token(),
            httponly=True,
            samesite="strict",
            max_age=_COOKIE_MAX_AGE,
        )
        return {"ok": True}

    @app.post("/api/auth/logout")
    def auth_logout(response: Response) -> dict[str, bool]:
        response.delete_cookie(_COOKIE_NAME, samesite="strict")
        return {"ok": True}

    # ─────────────────────────────────────────────────────────────────────────

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

    @app.post("/api/restart")
    def restart_api() -> dict[str, Any]:
        """Exit the API process so the container manager restarts it.

        Used by the scheduler to pick up a freshly built database. The endpoint
        is only intended to be called from inside the docker network (the
        scheduler talks directly to `api:8000`); the nginx web gateway blocks
        external callers, so we don't bother with a token here.

        The exit is scheduled on a short delay so the response can be flushed
        before the process terminates.
        """

        def _shutdown() -> None:
            import time

            time.sleep(0.25)
            os._exit(0)

        threading.Thread(target=_shutdown, daemon=True).start()
        return {"status": "restarting"}

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

    @app.get("/api/message-context")
    def message_context(message_id: str) -> dict[str, str | None]:
        """Return a best-effort URL to view the message in its original HTML export.

        Returns an object: { "url": "/api/media?...", "fragment": "chatlog__message-container-<id>" }
        """
        with _connect(app.state.db_path) as con:
            row = con.execute(
                """
                SELECT m.id, c.platform_channel_id, s.platform, s.name
                FROM messages m
                JOIN channels c ON c.id = m.channel_id
                JOIN sources s ON s.id = c.source_id
                WHERE m.id = ?
                """,
                [message_id],
            ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Message not found")
        _msg_id, channel_raw_id, platform, source_name = row

        # Discord: look for a file named <channel_raw_id>.html under data/discord/**
        data_dir = app.state.data_dir
        if platform == "discord":
            discord_root = (data_dir / "discord").resolve()
            if discord_root.exists():
                for path in discord_root.rglob(f"{channel_raw_id}.html"):
                    # build relative path from discord root
                    rel = path.relative_to(discord_root).as_posix()
                    source_folder = source_name.removeprefix("Discord: ").strip()
                    return {
                        "url": _media_url("discord", source_folder, rel),
                        "fragment": f"chatlog__message-container-{message_id}",
                    }
            raise HTTPException(
                status_code=404, detail="HTML export not found for message"
            )

        # Signal: look under data/signal_decrypted/<source_folder>/<channel_raw_id>.html
        if platform == "signal":
            signal_root = (data_dir / "signal_decrypted").resolve()
            if signal_root.exists():
                for source_dir in signal_root.iterdir():
                    candidate = source_dir / f"{channel_raw_id}.html"
                    if candidate.exists():
                        rel = candidate.name
                        return {
                            "url": _media_url("signal", source_dir.name, rel),
                            "fragment": f"chatlog__message-container-{message_id}",
                        }
            raise HTTPException(
                status_code=404, detail="Signal HTML export not found for message"
            )

        # Facebook: try data/facebook/<channel_raw_id>/*html
        if platform == "facebook":
            fb_dir = (data_dir / "facebook" / channel_raw_id).resolve()
            if fb_dir.exists() and fb_dir.is_dir():
                for path in fb_dir.iterdir():
                    if path.suffix == ".html":
                        rel = path.name
                        return {
                            "url": _media_url("facebook", channel_raw_id, rel),
                            "fragment": None,
                        }
            raise HTTPException(
                status_code=404, detail="Facebook HTML export not found for message"
            )

        raise HTTPException(
            status_code=404, detail="Unsupported platform for message context"
        )

    @app.get("/api/media-anchored")
    def media_anchored(message_id: str) -> Response:
        """Serve an HTML export with an injected anchor id for the specified message.

        This is a best-effort helper used by the frontend to jump to a message inside
        an export that doesn't include stable element ids. The endpoint will:
        - Lookup the message in the DB to get platform/source/channel and content.
        - Find the corresponding export HTML file on disk (best-effort).
        - Insert an id attribute like `chatlog__message-container-<message_id>` on the
          element that contains a short snippet of the message content and return the modified HTML.
        """
        # Lookup message
        with _connect(app.state.db_path) as con:
            row = con.execute(
                """
                SELECT m.content, c.platform_channel_id, s.platform, s.name
                FROM messages m
                JOIN channels c ON c.id = m.channel_id
                JOIN sources s ON s.id = c.source_id
                WHERE m.id = ?
                """,
                [message_id],
            ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Message not found")
        content, channel_raw_id, platform, source_name = row
        snippet = " ".join(str(content or "").split())[:200].strip()

        data_dir = app.state.data_dir

        def find_file_and_root() -> tuple[Path, Path] | tuple[None, None]:
            # Return (file_path, source_root)
            if platform == "discord":
                discord_root = (data_dir / "discord").resolve()
                if not discord_root.exists():
                    return None, None
                for path in discord_root.rglob(f"{channel_raw_id}.html"):
                    return path, discord_root
                return None, None
            if platform == "signal":
                signal_root = (data_dir / "signal_decrypted").resolve()
                if not signal_root.exists():
                    return None, None
                # first try source dirs for an exact match
                for source_dir in signal_root.iterdir():
                    candidate = source_dir / f"{channel_raw_id}.html"
                    if candidate.exists():
                        return candidate, source_dir
                # fallback: search all html files under signal root for the snippet
                for path in signal_root.rglob("*.html"):
                    try:
                        txt = path.read_text(encoding="utf-8", errors="ignore")
                    except Exception:
                        continue
                    if snippet and snippet in " ".join(txt.split()):
                        return path, path.parent
                return None, None
            if platform == "facebook":
                fb_root = (data_dir / "facebook" / channel_raw_id).resolve()
                if fb_root.exists() and fb_root.is_dir():
                    for path in fb_root.iterdir():
                        if path.suffix == ".html":
                            txt = path.read_text(encoding="utf-8", errors="ignore")
                            if snippet and snippet in " ".join(txt.split()):
                                return path, fb_root
                    # if no snippet match, return first html
                    for path in fb_root.iterdir():
                        if path.suffix == ".html":
                            return path, fb_root
                # fallback: search entire facebook tree
                fb_root_all = (data_dir / "facebook").resolve()
                if fb_root_all.exists():
                    for path in fb_root_all.rglob("*.html"):
                        try:
                            txt = path.read_text(encoding="utf-8", errors="ignore")
                        except Exception:
                            continue
                        if snippet and snippet in " ".join(txt.split()):
                            return path, path.parent
                return None, None
            return None, None

        file_and_root = find_file_and_root()
        if not file_and_root or file_and_root[0] is None:
            raise HTTPException(
                status_code=404, detail="HTML export not found for message"
            )
        file_path, source_root = file_and_root

        # Load HTML and try to find the element containing the snippet
        try:
            html = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            raise HTTPException(status_code=404, detail="Failed to read HTML export")

        soup = BeautifulSoup(html, "html.parser")

        # Rewrite asset URLs so they point to /api/media with platform/source/path params.
        # This prevents the browser from requesting relative paths like /api/media/Avatar_36.png
        # which lack the necessary query parameters.
        try:
            # Determine source_folder: prefer the name of source_root relative to data_dir
            source_folder = None
            if platform == "signal":
                # If file_path is under data_dir/signal_decrypted/<source_folder>/..., derive source_folder
                sig_root = (data_dir / "signal_decrypted").resolve()
                try:
                    rel = file_path.relative_to(sig_root)
                    parts = rel.parts
                    if parts:
                        source_folder = parts[0]
                except Exception:
                    source_folder = (
                        source_root.name if source_root is not None else None
                    )
            elif platform == "facebook":
                # file_path parent is the chat folder; source_folder is channel_raw_id
                source_folder = channel_raw_id
            elif platform == "discord":
                source_folder = source_name.removeprefix("Discord: ").strip()

            if source_folder:
                for tag in soup.find_all(True):
                    for attr in ("src", "href"):
                        val = tag.get(attr)
                        if not val or not isinstance(val, str):
                            continue
                        v = val.strip()
                        if (
                            not v
                            or v.startswith("data:")
                            or v.startswith("http://")
                            or v.startswith("https://")
                        ):
                            continue
                        # Resolve candidate path relative to the file_path
                        candidate = None
                        try:
                            candidate_path = (file_path.parent / unquote(v)).resolve()
                            if candidate_path.exists() and candidate_path.is_file():
                                candidate = candidate_path
                        except Exception:
                            candidate = None
                        if candidate is None:
                            # Fall back to treating v as relative to the source root
                            try:
                                rel_candidate = Path(unquote(v)).as_posix().lstrip("/")
                                candidate = (
                                    (source_root / rel_candidate)
                                    if source_root is not None
                                    else None
                                )
                                if candidate is not None and not candidate.exists():
                                    candidate = None
                            except Exception:
                                candidate = None
                        if candidate is None:
                            continue
                        try:
                            rel = (
                                candidate.relative_to(source_root).as_posix()
                                if source_root is not None
                                else candidate.name
                            )
                        except Exception:
                            rel = candidate.name
                        new_url = _media_url(platform, source_folder, rel)
                        tag[attr] = new_url
        except Exception:
            # Non-fatal; proceed without rewriting assets
            pass

        target_id = f"chatlog__message-container-{message_id}"
        found = None
        if snippet:
            # find a tag whose text contains the snippet (case-insensitive)
            norm_snip = snippet.casefold()
            for tag in soup.find_all(True):
                try:
                    text = " ".join(tag.get_text(" ", strip=True).split())
                except Exception:
                    continue
                if norm_snip and norm_snip in text.casefold():
                    found = tag
                    break
        if found is None:
            # fallback: try to find element matching common message container classes
            for cls in ["chatlog__message-container", "pam", "message", "chat-message"]:
                el = soup.select_one(f".{cls}")
                if el:
                    found = el
                    break
        if found is not None:
            # if the found element is nested, prefer an enclosing div
            parent = found
            for _ in range(3):
                if parent.name and parent.name.lower() in {"div", "article", "li"}:
                    break
                if parent.parent is None:
                    break
                parent = parent.parent
            parent["id"] = target_id
        modified_html = str(soup)
        return Response(content=modified_html, media_type="text/html")

    @app.get("/api/message-snippet")
    def message_snippet(
        message_id: str, context: int = Query(default=5, ge=0, le=50)
    ) -> Response:
        """Return a small HTML page showing a window of messages around the given message id.

        The page is self-contained and uses resolved /api/media URLs for attachments so the browser
        won't attempt to load relative media paths that would otherwise 404.
        """
        with _connect(app.state.db_path) as con:
            row = con.execute(
                """
                SELECT m.id, m.ts, m.content, m.attachment_preview, m.attachment_count, m.reaction_count, m.reaction_summary, m.reaction_details_json, m.channel_id, c.platform_channel_id, s.platform, s.name, p.display_name, p.color
                FROM messages m
                JOIN channels c ON c.id = m.channel_id
                JOIN sources s ON s.id = c.source_id
                LEFT JOIN people p ON p.id = m.person_id
                WHERE m.id = ?
                """,
                [message_id],
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Message not found")
            (
                msg_id,
                msg_ts,
                msg_content,
                msg_attach_preview,
                msg_attach_count,
                msg_reaction_count,
                msg_reaction_summary,
                msg_reaction_details,
                msg_channel_id,
                platform_channel_id,
                platform,
                source_name,
                person_name,
                person_color,
            ) = row

            # Fetch surrounding messages by loading the ordered message list for the channel
            channel_rows = con.execute(
                """
                SELECT m.id, m.ts, m.content, m.attachment_preview, m.attachment_count, m.reaction_count, m.reaction_summary, m.reaction_details_json, p.display_name, p.color
                FROM messages m
                LEFT JOIN people p ON p.id = m.person_id
                WHERE m.channel_id = ?
                ORDER BY m.ts ASC, m.id ASC
                """,
                [msg_channel_id],
            ).fetchall()

            # Find index of the target message
            idx = None
            for i, r in enumerate(channel_rows):
                if str(r[0]) == str(msg_id):
                    idx = i
                    break
            if idx is None:
                # If not found in channel (shouldn't happen), return only the target
                window_rows = [
                    (
                        msg_id,
                        msg_ts,
                        msg_content,
                        msg_attach_preview,
                        msg_attach_count,
                        msg_reaction_count,
                        msg_reaction_summary,
                        msg_reaction_details,
                        person_name,
                        person_color,
                    )
                ]
            else:
                start = max(0, idx - context)
                end = min(len(channel_rows), idx + context + 1)
                selected = channel_rows[start:end]
                # selected contains tuples with same schema as prev_rows/next_rows
                window_rows = []
                for r in selected:
                    # ensure we have consistent tuple length matching later unpack
                    window_rows.append(r)

        # Build HTML outside the DB context
        from html import escape as _escape

        def _resolve_attach(preview: str | None) -> str | None:
            try:
                return _resolve_local_attachment_url(
                    preview,
                    source_name,
                    app.state.data_dir,
                    app.state.signal_filename_index,
                )
            except Exception:
                return None

        def _attachment_html(preview: str | None):
            url = _resolve_attach(preview)
            if not url:
                return ""
            lower = url.lower()
            if any(
                lower.endswith(ext)
                for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp")
            ):
                return (
                    f'<div class="attachment"><img src="{url}" alt="attachment"/></div>'
                )
            return f'<div class="attachment"><a href="{url}" target="_blank" rel="noreferrer">Attachment</a></div>'

        parts: list[str] = []
        parts.append(
            '<!doctype html><html><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>'
        )
        parts.append(
            "<style>body{font-family:Inter,system-ui,Roboto,Arial,sans-serif;padding:18px;background:#0f172a;color:#e2e8f0} .snippet{max-width:900px;margin:0 auto} .message{padding:8px;border-radius:8px;margin-bottom:8px;background:rgba(255,255,255,0.02)} .message.target{background:linear-gradient(90deg,#0ea5e9, #7dd3fc);color:#04111a} .meta{font-size:0.9rem;color:#94a3b8} .content{margin-top:6px;white-space:pre-wrap} .attachment img{max-width:100%;height:auto;border-radius:6px;margin-top:6px}</style>"
        )
        parts.append('</head><body><div class="snippet">')

        # Debug header: how many messages in window
        total_msgs = len(window_rows)
        parts.append(
            f'<div class="snippet-meta">Showing {total_msgs} messages in context</div>'
        )
        parts.append(
            '<div style="display:none">'
            + _escape(str([r[0] for r in window_rows]))
            + "</div>"
        )

        for row in window_rows:
            (
                r_id,
                r_ts,
                r_content,
                r_attach_preview,
                r_attach_count,
                r_reaction_count,
                r_reaction_summary,
                r_reaction_details,
                r_person_name,
                r_person_color,
            ) = row
            is_target = r_id == msg_id
            safe_person = _escape(str(r_person_name or "Unknown"))
            safe_time = _escape(str(r_ts) if r_ts is not None else "N/A")
            safe_content = _escape(str(r_content or ""))
            attach_html = _attachment_html(r_attach_preview)
            # reactions
            reactions_html = ""
            try:
                if r_reaction_details:
                    details = json.loads(r_reaction_details)
                    # show as pills
                    parts_reacts = []
                    for react in details:
                        name = _escape(str(react.get("name") or ""))
                        count = int(react.get("count") or 0)
                        if not name:
                            continue
                        parts_reacts.append(
                            f'<span class="react-pill">{name}×{count}</span>'
                        )
                    if parts_reacts:
                        reactions_html = (
                            f'<div class="reactions">{" ".join(parts_reacts)}</div>'
                        )
                elif r_reaction_summary:
                    reactions_html = f'<div class="reactions">{_escape(str(r_reaction_summary))}</div>'
            except Exception:
                reactions_html = ""
            cls = "message target" if is_target else "message"
            parts.append(
                f'<div id="chatlog__message-container-{r_id}" class="{cls}"><div class="meta"><strong style="color:{_escape(str(r_person_color or "#fff"))}">{safe_person}</strong> <time>{safe_time}</time></div><div class="content">{safe_content}</div>{attach_html}{reactions_html}</div>'
            )

        parts.append("</div></body></html>")
        return Response(content="\n".join(parts), media_type="text/html")

    @app.get("/api/message-window")
    def message_window(
        message_id: str, context: int = Query(default=10, ge=0, le=50)
    ) -> dict[str, object]:
        """Return a JSON window of messages around the given message id.

        This is a DB-driven window suitable for client-side rendering.
        """
        with _connect(app.state.db_path) as con:
            row = con.execute(
                """
                SELECT m.id, m.ts, m.content, m.attachment_preview, m.attachment_count,
                       m.reaction_count, m.reaction_summary, m.reaction_details_json,
                       m.channel_id, c.platform_channel_id, s.id, s.platform, s.name,
                       p.display_name, p.color, c.name
                FROM messages m
                JOIN channels c ON c.id = m.channel_id
                JOIN sources s ON s.id = c.source_id
                LEFT JOIN people p ON p.id = m.person_id
                WHERE m.id = ?
                """,
                [message_id],
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Message not found")
            (
                msg_id,
                msg_ts,
                msg_content,
                msg_attach_preview,
                msg_attach_count,
                msg_reaction_count,
                msg_reaction_summary,
                msg_reaction_details,
                msg_channel_id,
                platform_channel_id,
                source_id,
                platform,
                source_name,
                person_name,
                person_color,
                channel_initial_name,
            ) = row

            channel_rows = con.execute(
                """
                SELECT m.id, m.ts, m.content, m.attachment_preview, m.attachment_count,
                       m.reaction_count, m.reaction_summary, m.reaction_details_json,
                       p.display_name, p.color, m.person_id
                FROM messages m
                LEFT JOIN people p ON p.id = m.person_id
                WHERE m.channel_id = ?
                ORDER BY m.ts ASC, m.id ASC
                """,
                [msg_channel_id],
            ).fetchall()
            idx = None
            for i, r in enumerate(channel_rows):
                if str(r[0]) == str(msg_id):
                    idx = i
                    break
            if idx is None:
                selected = [
                    (
                        msg_id, msg_ts, msg_content, msg_attach_preview, msg_attach_count,
                        msg_reaction_count, msg_reaction_summary, msg_reaction_details,
                        person_name, person_color, None,
                    )
                ]
            else:
                start = max(0, idx - context)
                end = min(len(channel_rows), idx + context + 1)
                selected = channel_rows[start:end]

            # Channel name at the time of the target message
            channel_name_row = con.execute(
                """
                SELECT new_name FROM channel_name_changes
                WHERE channel_id = ? AND ts <= ?
                ORDER BY ts DESC LIMIT 1
                """,
                [msg_channel_id, msg_ts],
            ).fetchone()
            channel_name_at_time = _get_display_name(
                channel_name_row[0] if channel_name_row
                else (channel_initial_name or source_name),
                source_name,
                app.state.fb_chat_names,
            )

            # Nickname timeline for this channel: person_id -> [(ts, new_name), ...]
            nickname_rows = con.execute(
                """
                SELECT person_id, new_name, ts
                FROM person_name_changes
                WHERE source_id = ?
                  AND json_extract_string(payload_json, '$.chatId') = ?
                ORDER BY person_id, ts
                """,
                [source_id, platform_channel_id],
            ).fetchall()

        nickname_timeline: dict[int, list[tuple[Any, str]]] = {}
        for r_pid, r_name, r_ts in nickname_rows:
            nickname_timeline.setdefault(int(r_pid), []).append((r_ts, r_name))

        def _nickname_at(person_id: int | None, ts: Any) -> str | None:
            if person_id is None:
                return None
            timeline = nickname_timeline.get(int(person_id), [])
            result = None
            for change_ts, name in timeline:
                if change_ts <= ts:
                    result = name
                else:
                    break
            return result

        # Avatar URL map: display_name -> url (from YAML config)
        people_extra = _load_people_extra()
        avatar_by_name: dict[str, str] = {
            name: meta["avatar"]
            for name, meta in people_extra.items()
            if meta.get("avatar")
        }

        # Build JSON payload
        items: list[dict[str, object]] = []
        for r in selected:
            (
                r_id,
                r_ts,
                r_content,
                r_attach_preview,
                r_attach_count,
                r_reaction_count,
                r_reaction_summary,
                r_reaction_details,
                r_person_name,
                r_person_color,
                r_person_id,
            ) = r
            attach_url = (
                _resolve_local_attachment_url(
                    r_attach_preview,
                    source_name,
                    app.state.data_dir,
                    app.state.signal_filename_index,
                )
                if r_attach_preview
                else None
            )
            nickname = _nickname_at(r_person_id, r_ts) if r_ts else None
            items.append(
                {
                    "id": r_id,
                    "ts": r_ts.isoformat() if r_ts else None,
                    "content": r_content,
                    "attachment_preview": r_attach_preview,
                    "attachment_url": attach_url,
                    "attachment_count": r_attach_count,
                    "reaction_count": r_reaction_count,
                    "reaction_summary": r_reaction_summary,
                    "reaction_details": json.loads(r_reaction_details)
                    if r_reaction_details
                    else None,
                    "person_name": nickname or r_person_name,
                    "person_name_canonical": r_person_name,
                    "person_color": r_person_color,
                    "avatar_url": avatar_by_name.get(r_person_name or "", "") or None,
                    "channel_id": msg_channel_id,
                    "platform": platform,
                    "source_name": source_name,
                }
            )
        return {
            "channel_name": channel_name_at_time,
            "platform": platform,
            "source_name": source_name,
            "items": items,
        }

    @app.get("/api/overview")
    def overview(
        start: date | None = Query(default=None, alias="from"),
        end: date | None = Query(default=None, alias="to"),
        people: str | None = None,
        themes: str | None = None,
        platforms: str | None = None,
        metric: str = Query(default="messages", pattern="^(messages|words|conversations)$"),
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
        ms = _metric_sql(metric)
        with _connect(app.state.db_path) as con:
            total = con.execute(
                f"""
                SELECT {ms.aggregate}, MIN(ts), MAX(ts)
                FROM (
                    SELECT m.ts{ms.inner_select_suffix}
                    FROM messages m
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON c.source_id = s.id
                    WHERE {where}{ms.extra_where}
                )
                """,
                params,
            ).fetchone()
            people_rows = con.execute(
                f"""
                SELECT p.id, p.display_name, p.color,
                       {ms.aggregate} AS message_count
                FROM (
                    SELECT m.person_id{ms.inner_select_suffix}
                    FROM messages m
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON c.source_id = s.id
                    WHERE {where}{ms.extra_where}
                ) counted
                JOIN people p ON p.id = counted.person_id
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
            conversations_row = (None, None, None)
            if app.state.has_conversation_id:
                conversations_row = con.execute(
                    f"""
                    SELECT
                        COUNT(DISTINCT m.conversation_id) AS conversation_count,
                        AVG(per_conversation.message_count) AS avg_messages,
                        MAX(per_conversation.message_count) AS longest_conversation
                    FROM messages m
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON c.source_id = s.id
                    LEFT JOIN (
                        SELECT conversation_id, COUNT(*) AS message_count
                        FROM messages
                        WHERE conversation_id IS NOT NULL
                        GROUP BY conversation_id
                    ) per_conversation ON per_conversation.conversation_id = m.conversation_id
                    WHERE {where} AND m.conversation_id IS NOT NULL
                    """,
                    params,
                ).fetchone() or (None, None, None)
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
                "conversation_count": int(conversations_row[0] or 0),
                "avg_messages_per_conversation": float(conversations_row[1] or 0.0),
                "longest_conversation_message_count": int(conversations_row[2] or 0),
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
        metric: str = Query(default="messages", pattern="^(messages|words|conversations)$"),
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
        ms = _metric_sql(metric)
        with _connect(app.state.db_path) as con:
            rows = con.execute(
                f"""
                SELECT date_trunc(?, bucket_ts) AS bucket,
                       {ms.aggregate} AS message_count
                FROM (
                    SELECT m.ts AS bucket_ts{ms.inner_select_suffix}
                    FROM messages m
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON c.source_id = s.id
                    WHERE {where}{ms.extra_where}
                )
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

    @app.get("/api/platform-over-time")
    def platform_over_time(
        granularity: str = Query(default="month", pattern="^(day|week|month|year)$"),
        start: date | None = Query(default=None, alias="from"),
        end: date | None = Query(default=None, alias="to"),
        people: str | None = None,
        themes: str | None = None,
        platforms: str | None = None,
        metric: str = Query(default="messages", pattern="^(messages|words|conversations)$"),
    ) -> dict[str, Any]:
        """Per-platform message/word counts bucketed over time."""
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
        ms = _metric_sql(metric)
        with _connect(app.state.db_path) as con:
            rows = con.execute(
                f"""
                SELECT date_trunc(?, bucket_ts) AS bucket, platform,
                       {ms.aggregate} AS count
                FROM (
                    SELECT m.ts AS bucket_ts,
                           s.platform AS platform{ms.inner_select_suffix}
                    FROM messages m
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON c.source_id = s.id
                    WHERE {where}{ms.extra_where}
                )
                GROUP BY bucket, platform
                ORDER BY bucket, platform
                """,
                params,
            ).fetchall()

        buckets_index: dict[str, dict[str, int]] = {}
        platforms_seen: set[str] = set()
        for bucket_ts, platform, count in rows:
            key = bucket_ts.isoformat() if bucket_ts else ""
            bucket_entry = buckets_index.setdefault(key, {})
            bucket_entry[str(platform)] = bucket_entry.get(str(platform), 0) + int(
                count or 0
            )
            platforms_seen.add(str(platform))
        points = []
        for bucket_key in sorted(buckets_index):
            counts = buckets_index[bucket_key]
            points.append(
                {
                    "bucket": bucket_key,
                    "counts": {
                        platform: int(counts.get(platform, 0))
                        for platform in sorted(platforms_seen)
                    },
                }
            )
        return {
            "granularity": granularity,
            "platforms": sorted(platforms_seen),
            "points": points,
        }

    @app.get("/api/calendar")
    def calendar(
        start: date | None = Query(default=None, alias="from"),
        end: date | None = Query(default=None, alias="to"),
        people: str | None = None,
        themes: str | None = None,
        platforms: str | None = None,
        metric: str = Query(default="messages", pattern="^(messages|words|conversations)$"),
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
        ms = _metric_sql(metric)
        with _connect(app.state.db_path) as con:
            rows = con.execute(
                f"""
                SELECT CAST(bucket_ts AS DATE) AS day,
                       {ms.aggregate} AS message_count
                FROM (
                    SELECT m.ts AS bucket_ts{ms.inner_select_suffix}
                    FROM messages m
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON c.source_id = s.id
                    WHERE {where}{ms.extra_where}
                )
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
        metric: str = Query(default="messages", pattern="^(messages|words|conversations)$"),
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
        ms = _metric_sql(metric)
        with _connect(app.state.db_path) as con:
            rows = con.execute(
                f"""
                SELECT EXTRACT(isodow FROM bucket_ts) AS weekday,
                       EXTRACT(hour FROM bucket_ts) AS hour,
                       {ms.aggregate} AS message_count
                FROM (
                    SELECT m.ts AS bucket_ts{ms.inner_select_suffix}
                    FROM messages m
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON c.source_id = s.id
                    WHERE {where}{ms.extra_where}
                )
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
        metric: str = Query(default="messages", pattern="^(messages|words|conversations)$"),
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
        ms = _metric_sql(metric)
        with _connect(app.state.db_path) as con:
            rows = con.execute(
                f"""
                SELECT p.id, p.display_name, p.color,
                       {ms.aggregate} AS message_count
                FROM (
                    SELECT m.person_id{ms.inner_select_suffix}
                    FROM messages m
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON c.source_id = s.id
                    WHERE {where}{ms.extra_where}
                ) counted
                JOIN people p ON p.id = counted.person_id
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
                "SELECT id, display_name, color FROM people ORDER BY display_name, id"
            ).fetchall()
            platforms = con.execute(
                "SELECT DISTINCT platform FROM sources ORDER BY platform"
            ).fetchall()

        if app.state.configured_people_names:
            people = [
                row for row in people if row[1] in app.state.configured_people_names
            ]

        people_extra = _load_people_extra()
        themes_extra = _load_themes_extra()

        return {
            "people": [
                {
                    "id": int(row[0]),
                    "name": row[1],
                    "color": row[2] or people_extra.get(row[1], {}).get("color", ""),
                    "avatar": people_extra.get(row[1], {}).get("avatar", ""),
                }
                for row in people
            ],
            "themes": [
                {
                    "id": int(theme_id),
                    "name": theme_name,
                    "emoji": themes_extra.get(theme_name, ""),
                }
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

    @app.get("/api/member-events")
    def member_events(
        kind: str | None = Query(default=None, pattern="^(added|removed|left)$"),
        limit: int = Query(default=10, ge=1, le=100),
        start: date | None = Query(default=None, alias="from"),
        end: date | None = Query(default=None, alias="to"),
        people: str | None = None,
        themes: str | None = None,
        platforms: str | None = None,
    ) -> dict[str, Any]:
        """Aggregate kicks/leaves/adds by actor (kicker), target (kickee), and chat.

        Returns three rankings filtered by the same query params as the rest of
        the API. The `people` filter applies to *both* actor and target so a
        person-scoped view includes events they were involved in either way.
        """
        # Ensure table exists in this DB. Older DBs may not have it.
        with _connect(app.state.db_path) as con:
            has_table = con.execute(
                """
                SELECT 1 FROM information_schema.tables WHERE table_name = 'member_events'
                """
            ).fetchone()
        if not has_table:
            return {"kind": kind, "by_actor": [], "by_target": [], "by_chat": []}

        filters = QueryFilters(
            start=start,
            end=end,
            people=_csv_ints(people, "people"),
            themes=_csv_ints(themes, "themes"),
            platforms=_csv_strings(platforms),
        )

        clauses: list[str] = ["1 = 1"]
        params: list[Any] = []
        if start is not None:
            clauses.append("e.ts >= ?")
            params.append(datetime.combine(start, time.min))
        if end is not None:
            clauses.append("e.ts < ?")
            params.append(datetime.combine(end + timedelta(days=1), time.min))
        if kind:
            clauses.append("e.kind = ?")
            params.append(kind)
        if filters.platforms:
            placeholders = ", ".join("?" for _ in filters.platforms)
            clauses.append(f"s.platform IN ({placeholders})")
            params.extend(filters.platforms)
        if filters.themes:
            theme_names = {
                app.state.theme_id_to_name.get(theme_id)
                for theme_id in filters.themes
            }
            theme_names.discard(None)
            channel_ids = sorted(
                {
                    channel_id
                    for theme_name in theme_names
                    for channel_id in app.state.theme_to_channel_ids.get(
                        theme_name, []
                    )
                }
            )
            if not channel_ids:
                return {"kind": kind, "by_actor": [], "by_target": [], "by_chat": []}
            placeholders = ", ".join("?" for _ in channel_ids)
            clauses.append(f"e.channel_id IN ({placeholders})")
            params.extend(channel_ids)
        if filters.people:
            placeholders = ", ".join("?" for _ in filters.people)
            clauses.append(
                f"(e.actor_person_id IN ({placeholders}) OR e.target_person_id IN ({placeholders}))"
            )
            params.extend(filters.people)
            params.extend(filters.people)
        where = " AND ".join(clauses)

        with _connect(app.state.db_path) as con:
            by_actor_rows = con.execute(
                f"""
                SELECT
                    MIN(p.id) AS id,
                    ANY_VALUE(p.display_name) AS display_name,
                    ANY_VALUE(p.color) AS color,
                    COUNT(*) AS count
                FROM member_events e
                JOIN channels c ON c.id = e.channel_id
                JOIN sources s ON s.id = c.source_id
                JOIN people p ON p.id = e.actor_person_id
                WHERE {where} AND e.actor_person_id IS NOT NULL
                GROUP BY LOWER(TRIM(p.display_name))
                ORDER BY count DESC, MIN(p.display_name)
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()
            by_target_rows = con.execute(
                f"""
                SELECT
                    MIN(p.id) AS id,
                    ANY_VALUE(p.display_name) AS display_name,
                    ANY_VALUE(p.color) AS color,
                    COUNT(*) AS count
                FROM member_events e
                JOIN channels c ON c.id = e.channel_id
                JOIN sources s ON s.id = c.source_id
                JOIN people p ON p.id = e.target_person_id
                WHERE {where}
                GROUP BY LOWER(TRIM(p.display_name))
                ORDER BY count DESC, MIN(p.display_name)
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()
            by_chat_rows = con.execute(
                f"""
                SELECT
                    c.id, c.name, s.name AS source_name, s.platform, COUNT(*) AS count
                FROM member_events e
                JOIN channels c ON c.id = e.channel_id
                JOIN sources s ON s.id = c.source_id
                WHERE {where}
                GROUP BY c.id, c.name, s.name, s.platform
                ORDER BY count DESC, c.name
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()

        def _person_display(name: Any) -> str:
            text = "" if name is None else str(name)
            if (
                _normalized_history_name(text) == "you"
                and app.state.primary_person_name
            ):
                return app.state.primary_person_name
            return text

        return {
            "kind": kind,
            "by_actor": [
                {
                    "id": int(row[0]),
                    "display_name": _person_display(row[1]),
                    "color": row[2],
                    "count": int(row[3]),
                }
                for row in by_actor_rows
            ],
            "by_target": [
                {
                    "id": int(row[0]),
                    "display_name": _person_display(row[1]),
                    "color": row[2],
                    "count": int(row[3]),
                }
                for row in by_target_rows
            ],
            "by_chat": [
                {
                    "id": int(row[0]),
                    "name": _get_display_name(row[1], row[2], app.state.fb_chat_names),
                    "source_name": row[2],
                    "platform": row[3],
                    "count": int(row[4]),
                }
                for row in by_chat_rows
            ],
        }

    @app.get("/api/top-chats")
    def top_chats(
        limit: int = Query(default=10, ge=1, le=100),
        start: date | None = Query(default=None, alias="from"),
        end: date | None = Query(default=None, alias="to"),
        people: str | None = None,
        themes: str | None = None,
        platforms: str | None = None,
        metric: str = Query(default="messages", pattern="^(messages|words|conversations)$"),
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
        ms = _metric_sql(metric)
        with _connect(app.state.db_path) as con:
            rows = con.execute(
                f"""
                SELECT c.id, c.name, t.name as theme_name, s.name as source_name,
                       {ms.aggregate} AS message_count
                FROM (
                    SELECT m.channel_id{ms.inner_select_suffix}
                    FROM messages m
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON c.source_id = s.id
                    WHERE {where}{ms.extra_where}
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
        metric: str = Query(default="messages", pattern="^(messages|words|conversations)$"),
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

        ms = _metric_sql(metric)
        with _connect(app.state.db_path) as con:
            rows = con.execute(
                f"""
                SELECT s.name, c.name, {ms.aggregate} as message_count
                FROM (
                    SELECT m.channel_id{ms.inner_select_suffix}
                    FROM messages m
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON c.source_id = s.id
                    WHERE {where}{ms.extra_where}
                ) counted
                JOIN channels c ON c.id = counted.channel_id
                JOIN sources s ON s.id = c.source_id
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

    @app.get("/api/domain-examples")
    def domain_examples(
        domain: str,
        limit: int = Query(default=6, ge=1, le=25),
        offset: int = Query(default=0, ge=0, le=1000),
        start: date | None = Query(default=None, alias="from"),
        end: date | None = Query(default=None, alias="to"),
        people: str | None = None,
        themes: str | None = None,
        platforms: str | None = None,
    ) -> dict[str, Any]:
        """A few example messages containing a link to the given domain."""
        normalized = domain.strip().casefold()
        if not normalized or len(normalized) > 255:
            raise HTTPException(status_code=400, detail="Invalid domain")

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
                WITH message_links AS (
                    SELECT
                        m.id AS message_id,
                        m.ts,
                        m.content,
                        p.display_name AS person_name,
                        p.color AS person_color,
                        c.name AS channel_name,
                        s.name AS source_name,
                        {_canonical_link_domain_expr("t.link")} AS domain
                    FROM messages m
                    JOIN people p ON p.id = m.person_id
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON c.source_id = s.id
                    CROSS JOIN unnest(
                        regexp_extract_all(coalesce(m.content, ''), 'https?://([^/?#\\s]+)', 1)
                    ) AS t(link)
                    WHERE {where} AND m.content IS NOT NULL AND m.content <> ''
                )
                SELECT DISTINCT message_id, ts, content, person_name, person_color, channel_name, source_name
                FROM message_links
                WHERE domain = ?
                ORDER BY ts DESC, message_id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, normalized, limit, offset],
            ).fetchall()

        return {
            "domain": normalized,
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

    @app.get("/api/link-preview")
    def link_preview(url: str) -> dict[str, Any]:
        """Fetch OpenGraph metadata for an external URL with TTL caching.

        Used by the frontend to render rich previews for any link, replacing the
        platform-specific YouTube/SoundCloud iframe embeds. Failures are cached
        briefly so the UI never blocks on the same broken link for long.
        """
        cleaned = (url or "").strip()
        if not cleaned or len(cleaned) > 2048:
            raise HTTPException(status_code=400, detail="Invalid URL")
        now = _time.time()
        with _LINK_PREVIEW_CACHE_LOCK:
            cached = _LINK_PREVIEW_CACHE.get(cleaned)
            if cached:
                ttl = (
                    _LINK_PREVIEW_ERROR_TTL_SECONDS
                    if cached["data"].get("error")
                    else _LINK_PREVIEW_TTL_SECONDS
                )
                if (now - cached["fetched_at"]) < ttl:
                    return cached["data"]
        try:
            payload = _fetch_link_preview(cleaned)
        except (
            ValueError,
            httpx.HTTPError,
            httpx.TimeoutException,
            httpx.HTTPStatusError,
        ) as exc:
            payload = {"url": cleaned, "error": str(exc)}
        except Exception as exc:  # defensive: never raise from the cache
            payload = {"url": cleaned, "error": f"unexpected: {exc}"}
        with _LINK_PREVIEW_CACHE_LOCK:
            if len(_LINK_PREVIEW_CACHE) > 5000:
                _LINK_PREVIEW_CACHE.clear()
            _LINK_PREVIEW_CACHE[cleaned] = {"fetched_at": now, "data": payload}
        return payload

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
        offset: int = Query(default=0, ge=0),
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
                LIMIT ? OFFSET ?
                """,
                [*params, limit + 1, offset],
            ).fetchall()

        has_more = len(rows) > limit
        rows = rows[:limit]

        return {
            "has_more": has_more,
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
        metric: str = Query(default="messages", pattern="^(messages|words|conversations)$"),
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
        ms = _metric_sql(metric)
        with _connect(app.state.db_path) as con:
            rows = con.execute(
                f"""
                SELECT date_trunc('month', bucket_ts) AS month,
                       {ms.aggregate} AS message_count
                FROM (
                    SELECT m.ts AS bucket_ts{ms.inner_select_suffix}
                    FROM messages m
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON c.source_id = s.id
                    WHERE {where}{ms.extra_where}
                )
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
        metric: str = Query(default="messages", pattern="^(messages|words|conversations)$"),
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
        ms = _metric_sql(metric)
        with _connect(app.state.db_path) as con:
            rows = con.execute(
                f"""
                SELECT EXTRACT(hour FROM bucket_ts) AS hour,
                       {ms.aggregate} AS message_count
                FROM (
                    SELECT m.ts AS bucket_ts{ms.inner_select_suffix}
                    FROM messages m
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON c.source_id = s.id
                    WHERE {where}{ms.extra_where}
                )
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
