from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import duckdb
import yaml
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .reconciliation import load_reconciliation

COMMON_STOP_WORDS = {
    "the", "and", "for", "that", "you", "with", "this", "have", "are", "was",
    "but", "not", "all", "can", "your", "just", "its", "its", "from", "they",
    "what", "when", "where", "will", "would", "there", "their", "about", "out",
    "get", "got", "into", "too", "very", "how", "why", "who", "him", "her",
    "his", "she", "himself", "herself", "them", "then", "than", "our", "ours",
    "were", "had", "has", "did", "does", "dont", "cant", "im", "ive", "id",
    "ill", "youre", "youve", "theyre", "weve", "isnt", "wasnt", "wont", "aint",
    "lol", "lmao", "yeah", "yep", "nah", "ok", "okay", "bro", "dude", "tho",
    "though", "like", "https", "http", "www", "com", "org", "net", "gg",
    "jpg", "jpeg", "png", "gif", "webp", "mp4", "mov", "sticker", "video",
    "image", "images", "reply", "forwarded", "message", "messages",
}
_THEME_CHANNEL_IDS: dict[str, list[int]] = {}


def _default_db_path() -> Path:
    return Path(os.environ.get("GCHAT_DB_PATH", "gchat.duckdb"))


def _load_fb_chat_names() -> dict[str, str]:
    """Load Facebook chat name mappings from config."""
    config_path = Path.cwd() / "config" / "fb_chat_names.json"
    if config_path.exists():
        try:
            return json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _get_display_name(channel_name: str, source_name: str, fb_chat_names: dict[str, str]) -> str:
    """Get display name for a channel, using Facebook original names when available."""
    if source_name.startswith("Facebook: "):
        # Try to find the original name using channel name as folder key
        display_name = fb_chat_names.get(channel_name)
        if display_name:
            return display_name
    return channel_name


def _canonical_link_domain_expr(column: str) -> str:
    return (
        f"""CASE
            WHEN lower({column}) IN ('youtu.be', 'youtube.com', 'www.youtube.com', 'm.youtube.com') THEN 'youtube.com'
            ELSE lower({column})
        END"""
    )


def _count_metric(metric: str) -> str:
    normalized = metric.strip().casefold()
    if normalized not in {"messages", "words"}:
        raise HTTPException(status_code=400, detail="Invalid metric filter")
    return normalized


def _count_metric_expr(metric: str) -> str:
    return "SUM(word_count)" if metric == "words" else "COUNT(*)"


def _word_count_expr() -> str:
    return "COALESCE(array_length(regexp_extract_all(replace(lower(coalesce(m.content, '')), chr(39), ''), '[a-z]{3,}')), 0)"


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
            raise HTTPException(status_code=400, detail=f"Invalid {field} filter: {item!r}") from exc
    return items


def _csv_strings(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _load_configured_theme_names() -> list[str]:
    config_path = Path.cwd() / "config" / "themes.yaml"
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


def _load_configured_people_names() -> set[str]:
    config_path = Path.cwd() / "config" / "people.yaml"
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
            selected_theme_names = {theme_id_to_name.get(theme_id) for theme_id in filters.themes}
            selected_theme_names.discard(None)
            channel_index = theme_to_channel_ids if theme_to_channel_ids is not None else _THEME_CHANNEL_IDS
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

                for (source_name, channel_name), theme_name in reconciliation.themes.channel_to_theme.items():
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
    if not configured_themes:
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


def create_app(db_path: Path | None = None) -> FastAPI:
    global _THEME_CHANNEL_IDS

    app = FastAPI(title="gchat API", version="0.1.0")
    app.state.db_path = db_path or _default_db_path()
    app.state.fb_chat_names = _load_fb_chat_names()
    app.state.reconciliation = load_reconciliation()
    app.state.configured_people_names = _load_configured_people_names()
    configured_theme_names = _load_configured_theme_names()
    app.state.theme_id_to_name = {i + 1: name for i, name in enumerate(configured_theme_names)}
    app.state.theme_to_channel_ids = _load_theme_channel_ids(app.state.db_path, app.state.reconciliation)
    _THEME_CHANNEL_IDS = app.state.theme_to_channel_ids

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
        filters = QueryFilters(start=start, end=end, people=_csv_ints(people, "people"), themes=_csv_ints(themes, "themes"), platforms=_csv_strings(platforms))
        params: list[Any] = []
        where = _filters_clause(filters, params, app.state.reconciliation, app.state.theme_id_to_name)
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
        return {
            "total_messages": int(total[0] or 0),
            "date_range": {
                "start": total[1].isoformat() if total[1] else None,
                "end": total[2].isoformat() if total[2] else None,
            },
            "people": [
                {"id": int(row[0]), "display_name": row[1], "color": row[2], "message_count": int(row[3])}
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
        filters = QueryFilters(start=start, end=end, people=_csv_ints(people, "people"), themes=_csv_ints(themes, "themes"), platforms=_csv_strings(platforms))
        params: list[Any] = [granularity]
        where = _filters_clause(filters, params, app.state.reconciliation, app.state.theme_id_to_name)
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
            "points": [{"bucket": row[0].isoformat(), "message_count": int(row[1])} for row in rows],
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
        filters = QueryFilters(start=start, end=end, people=_csv_ints(people, "people"), themes=_csv_ints(themes, "themes"), platforms=_csv_strings(platforms))
        params: list[Any] = []
        where = _filters_clause(filters, params, app.state.reconciliation, app.state.theme_id_to_name)
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
        return {"points": [{"day": row[0].isoformat(), "message_count": int(row[1])} for row in rows]}

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
        filters = QueryFilters(start=start, end=end, people=_csv_ints(people, "people"), themes=_csv_ints(themes, "themes"), platforms=_csv_strings(platforms))
        params: list[Any] = []
        where = _filters_clause(filters, params, app.state.reconciliation, app.state.theme_id_to_name)
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
                {"weekday": int(row[0]), "hour": int(row[1]), "message_count": int(row[2])}
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
        filters = QueryFilters(start=start, end=end, people=_csv_ints(people, "people"), themes=_csv_ints(themes, "themes"), platforms=_csv_strings(platforms))
        params: list[Any] = []
        where = _filters_clause(filters, params, app.state.reconciliation, app.state.theme_id_to_name)
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
                {"id": int(row[0]), "display_name": row[1], "color": row[2], "message_count": int(row[3])}
                for row in rows
            ]
        }


    @app.get("/api/metadata")
    def metadata() -> dict[str, Any]:
        """Get available filters: configured people, themes, and platforms."""
        # Load configured people from people.yaml
        config_dir = Path.cwd() / "config"
        people_config_path = config_dir / "people.yaml"
        configured_people_names = set()
        if people_config_path.exists():
            try:
                config_data = yaml.safe_load(people_config_path.read_text(encoding="utf-8"))
                for person in config_data.get("people", []):
                    configured_people_names.add(person["name"])
            except Exception:
                pass
        
        with _connect(app.state.db_path) as con:
            # Only return people that are in the config
            people = con.execute(
                "SELECT id, display_name FROM people ORDER BY display_name"
            ).fetchall()
            configured_people = [
                {"id": int(row[0]), "name": row[1]} 
                for row in people 
                if row[1] in configured_people_names
            ]
            
            themes_result = [
                {"id": int(theme_id), "name": theme_name}
                for theme_id, theme_name in sorted(app.state.theme_id_to_name.items())
            ]
            
            platforms = con.execute(
                "SELECT DISTINCT platform FROM sources ORDER BY platform"
            ).fetchall()
        
        return {
            "people": configured_people,
            "themes": themes_result,
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
                WHERE {' AND '.join(channel_where)}
                ORDER BY s.platform, s.name, c.name
                """,
                channel_params,
            ).fetchall()

            channel_change_params: list[Any] = []
            channel_change_where = ["1 = 1"]
            if start is not None:
                channel_change_where.append("d.ts >= ?")
                channel_change_params.append(datetime.combine(start, time.min))
            if end is not None:
                channel_change_where.append("d.ts < ?")
                channel_change_params.append(datetime.combine(end + timedelta(days=1), time.min))
            if platforms_filter:
                placeholders = ", ".join("?" for _ in platforms_filter)
                channel_change_where.append(f"s.platform IN ({placeholders})")
                channel_change_params.extend(platforms_filter)

            channel_change_rows = con.execute(
                f"""
                WITH deduped AS (
                    SELECT DISTINCT channel_id, source_id, previous_name, new_name, ts
                    FROM channel_name_changes
                )
                SELECT d.channel_id, d.previous_name, d.new_name, d.ts
                FROM deduped d
                JOIN channels c ON c.id = d.channel_id
                JOIN sources s ON s.id = d.source_id
                WHERE {' AND '.join(channel_change_where)}
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
                person_change_params.append(datetime.combine(end + timedelta(days=1), time.min))
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
                        previous_name,
                        new_name,
                        ts
                    FROM person_name_changes
                )
                SELECT d.person_id, d.source_id, d.chat_id, p.display_name, d.previous_name, d.new_name, d.ts
                FROM deduped d
                JOIN people p ON p.id = d.person_id
                JOIN sources s ON s.id = d.source_id
                WHERE {' AND '.join(person_change_where)}
                ORDER BY d.source_id, d.chat_id, p.display_name, d.ts, d.previous_name, d.new_name
                """,
                person_change_params,
            ).fetchall()

            channel_history_by_id: dict[int, list[dict[str, Any]]] = {}
            for channel_id, previous_name, new_name, ts in channel_change_rows:
                channel_history_by_id.setdefault(int(channel_id), []).append(
                    {
                        "previous_name": previous_name,
                        "new_name": new_name,
                        "ts": ts.isoformat() if ts else None,
                    }
                )

            participants_by_chat: dict[tuple[int, str], dict[int, dict[str, Any]]] = {}
            for person_id, source_id, chat_id, display_name, previous_name, new_name, ts in person_change_rows:
                if not chat_id:
                    continue
                chat_key = (int(source_id), str(chat_id))
                person_entry = participants_by_chat.setdefault(chat_key, {}).setdefault(
                    int(person_id),
                    {
                        "id": int(person_id),
                        "display_name": display_name,
                        "history": [],
                    },
                )
                person_entry["history"].append(
                    {
                        "previous_name": previous_name,
                        "new_name": new_name,
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
                    """.format(", ".join("?" for _ in app.state.configured_people_names)),
                    sorted(app.state.configured_people_names),
                ).fetchall()
                signal_chat_ids = {int(row[0]) for row in signal_chat_rows if int(row[1]) >= 2}

            for channel_id, source_id, platform, source_name, current_name, platform_channel_id in channel_rows:
                chat_key = (int(source_id), str(platform_channel_id))
                previous_names = channel_history_by_id.get(int(channel_id), [])
                if not previous_names:
                    continue
                if platform == "signal" and int(channel_id) not in signal_chat_ids:
                    continue
                participants = []
                if platform != "signal":
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
                        "current_name": _get_display_name(current_name, source_name, app.state.fb_chat_names),
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
        filters = QueryFilters(start=start, end=end, people=_csv_ints(people, "people"), themes=_csv_ints(themes, "themes"), platforms=_csv_strings(platforms))
        params: list[Any] = []
        where = _filters_clause(filters, params, app.state.reconciliation, app.state.theme_id_to_name)
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
                    "message_count": int(row[4])
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
        filters = QueryFilters(start=start, end=end, people=_csv_ints(people, "people"), themes=_csv_ints(themes, "themes"), platforms=_csv_strings(platforms))
        configured_themes = app.state.reconciliation.themes.configured_theme_names
        if not configured_themes:
            return {"items": []}

        params: list[Any] = []
        where = _filters_clause(filters, params, app.state.reconciliation, app.state.theme_id_to_name)

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
                theme_name = app.state.reconciliation.themes.resolve(source_name, channel_name)
                if theme_name in configured_themes:
                    theme_counts[theme_name] = theme_counts.get(theme_name, 0) + count

        theme_list = sorted(theme_counts.items(), key=lambda x: x[1], reverse=True)[:limit]

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
        filters = QueryFilters(start=start, end=end, people=_csv_ints(people, "people"), themes=_csv_ints(themes, "themes"), platforms=_csv_strings(platforms))
        params: list[Any] = []
        where = _filters_clause(filters, params, app.state.reconciliation, app.state.theme_id_to_name)

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
        return {
            "items": [{"word": row[0], "count": int(row[1])} for row in rows]
        }

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
            raise HTTPException(status_code=400, detail="Word must contain at least 3 letters")

        filters = QueryFilters(start=start, end=end, people=_csv_ints(people, "people"), themes=_csv_ints(themes, "themes"), platforms=_csv_strings(platforms))
        params: list[Any] = []
        where = _filters_clause(filters, params, app.state.reconciliation, app.state.theme_id_to_name)

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
            raise HTTPException(status_code=400, detail="Word must contain at least 3 letters")

        filters = QueryFilters(start=start, end=end, people=_csv_ints(people, "people"), themes=_csv_ints(themes, "themes"), platforms=_csv_strings(platforms))
        params: list[Any] = []
        where = _filters_clause(filters, params, app.state.reconciliation, app.state.theme_id_to_name)

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
                    "channel_name": _get_display_name(row[5], row[6], app.state.fb_chat_names),
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
        filters = QueryFilters(start=start, end=end, people=_csv_ints(people, "people"), themes=_csv_ints(themes, "themes"), platforms=_csv_strings(platforms))
        params: list[Any] = []
        where = _filters_clause(filters, params, app.state.reconciliation, app.state.theme_id_to_name)

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

        return {
            "items": [
                {"domain": row[0], "count": int(row[1])}
                for row in rows
            ]
        }

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
        filters = QueryFilters(start=start, end=end, people=_csv_ints(people, "people"), themes=_csv_ints(themes, "themes"), platforms=_csv_strings(platforms))
        params: list[Any] = []
        where = _filters_clause(filters, params, app.state.reconciliation, app.state.theme_id_to_name)

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
                {"id": int(row[0]), "display_name": row[1], "color": row[2], "count": int(row[3])}
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
        filters = QueryFilters(start=start, end=end, people=_csv_ints(people, "people"), themes=_csv_ints(themes, "themes"), platforms=_csv_strings(platforms))
        params: list[Any] = []
        where = _filters_clause(filters, params, app.state.reconciliation, app.state.theme_id_to_name)

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
            "items": [
                {"mention": f"@{row[0]}", "count": int(row[1])}
                for row in rows
            ]
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
        filters = QueryFilters(start=start, end=end, people=_csv_ints(people, "people"), themes=_csv_ints(themes, "themes"), platforms=_csv_strings(platforms))
        params: list[Any] = []
        where = _filters_clause(filters, params, app.state.reconciliation, app.state.theme_id_to_name)

        with _connect(app.state.db_path) as con:
            rows = con.execute(
                f"""
                SELECT
                    m.id,
                    m.ts,
                    m.content,
                    p.display_name,
                    p.color,
                    c.name,
                    s.name,
                    m.reaction_count
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
                    "content": row[2],
                    "person_name": row[3],
                    "person_color": row[4],
                    "channel_name": _get_display_name(row[5], row[6], app.state.fb_chat_names),
                    "source_name": row[6],
                    "reaction_count": int(row[7]),
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
        filters = QueryFilters(start=start, end=end, people=_csv_ints(people, "people"), themes=_csv_ints(themes, "themes"), platforms=_csv_strings(platforms))
        params: list[Any] = []
        where = _filters_clause(filters, params, app.state.reconciliation, app.state.theme_id_to_name)

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
                {"id": int(row[0]), "display_name": row[1], "color": row[2], "count": int(row[3])}
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
        filters = QueryFilters(start=start, end=end, people=_csv_ints(people, "people"), themes=_csv_ints(themes, "themes"), platforms=_csv_strings(platforms))
        params: list[Any] = []
        where = _filters_clause(filters, params, app.state.reconciliation, app.state.theme_id_to_name)
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
                {"month": row[0].isoformat() if row[0] else None, "message_count": int(row[1])}
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
        filters = QueryFilters(start=start, end=end, people=_csv_ints(people, "people"), themes=_csv_ints(themes, "themes"), platforms=_csv_strings(platforms))
        params: list[Any] = []
        where = _filters_clause(filters, params, app.state.reconciliation, app.state.theme_id_to_name)
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
                {"hour": int(row[0]), "message_count": int(row[1])}
                for row in rows
            ]
        }

    return app


def run_server(db_path: Path | None = None, host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    uvicorn.run(create_app(db_path), host=host, port=port, reload=reload)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="gchat-api")
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    run_server(args.db, host=args.host, port=args.port, reload=args.reload)
