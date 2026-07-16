"""Reaction volume, author, and identity coverage routes."""

from __future__ import annotations

import json
from collections import Counter
from datetime import date
from typing import Any, Callable

import duckdb
from fastapi import FastAPI, Query

from ..analytics_sql import metric_sql
from ..api_filters import QueryFilters, csv_ints, csv_strings, filters_clause


def _connect(path: Any) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(path), read_only=True)


def _scope(app: FastAPI, filters: QueryFilters) -> tuple[str, list[Any], str]:
    params: list[Any] = []
    where = filters_clause(
        filters,
        params,
        app.state.reconciliation,
        app.state.theme_id_to_name,
    )
    message_scope = metric_sql(
        "messages",
        has_is_system=app.state.has_is_system,
        has_word_count=app.state.has_word_count,
        excluded_ids=app.state.excluded_message_ids,
    )
    return where, params, message_scope.extra_where


def register_reaction_routes(
    app: FastAPI,
    *,
    resolve_attachment: Callable[..., str | None],
    normalize_reactions: Callable[..., list[dict[str, Any]]],
    display_name: Callable[[str, str, dict[str, str]], str],
) -> None:
    @app.get("/api/reaction-authors")
    def reaction_authors(
        limit: int = Query(default=15, ge=1, le=100),
        start: date | None = Query(default=None, alias="from"),
        end: date | None = Query(default=None, alias="to"),
        people: str | None = None,
        themes: str | None = None,
        platforms: str | None = None,
        include_bots: bool = False,
    ) -> dict[str, Any]:
        filters = QueryFilters(
            start=start,
            end=end,
            people=csv_ints(people, "people"),
            themes=csv_ints(themes, "themes"),
            platforms=csv_strings(platforms),
            include_bots=include_bots,
        )
        where, params, extra_where = _scope(app, filters)
        with _connect(app.state.db_path) as con:
            rows = con.execute(
                f"""
                SELECT
                    p.id,
                    p.display_name,
                    p.color,
                    SUM(m.reaction_count) AS reaction_count
                FROM messages m
                JOIN people p ON p.id = m.person_id
                JOIN channels c ON c.id = m.channel_id
                JOIN sources s ON s.id = c.source_id
                WHERE {where}{extra_where}
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

    @app.get("/api/reactions-over-time")
    def reactions_over_time(
        start: date | None = Query(default=None, alias="from"),
        end: date | None = Query(default=None, alias="to"),
        people: str | None = None,
        themes: str | None = None,
        platforms: str | None = None,
        include_bots: bool = False,
    ) -> dict[str, Any]:
        filters = QueryFilters(
            start=start,
            end=end,
            people=csv_ints(people, "people"),
            themes=csv_ints(themes, "themes"),
            platforms=csv_strings(platforms),
            include_bots=include_bots,
        )
        where, params, extra_where = _scope(app, filters)
        with _connect(app.state.db_path) as con:
            rows = con.execute(
                f"""
                SELECT
                    date_trunc('month', m.ts) AS bucket,
                    SUM(COALESCE(m.reaction_count, 0)) AS reaction_count,
                    COUNT(*) AS message_count,
                    SUM(
                        CASE WHEN COALESCE(m.reaction_count, 0) > 0 THEN 1 ELSE 0 END
                    ) AS reacted_message_count
                FROM messages m
                JOIN channels c ON c.id = m.channel_id
                JOIN sources s ON c.source_id = s.id
                WHERE {where}{extra_where}
                GROUP BY bucket
                ORDER BY bucket
                """,
                params,
            ).fetchall()
        return {
            "granularity": "month",
            "points": [
                {
                    "bucket": row[0].isoformat(),
                    "reaction_count": int(row[1] or 0),
                    "message_count": int(row[2] or 0),
                    "reacted_message_count": int(row[3] or 0),
                    "reactions_per_message": (
                        float(row[1] or 0) / int(row[2]) if row[2] else 0.0
                    ),
                }
                for row in rows
            ],
        }

    @app.get("/api/reaction-identity-coverage")
    def reaction_identity_coverage(
        limit: int = Query(default=20, ge=1, le=100),
        start: date | None = Query(default=None, alias="from"),
        end: date | None = Query(default=None, alias="to"),
        people: str | None = None,
        themes: str | None = None,
        platforms: str | None = None,
        include_bots: bool = False,
    ) -> dict[str, Any]:
        filters = QueryFilters(
            start=start,
            end=end,
            people=csv_ints(people, "people"),
            themes=csv_ints(themes, "themes"),
            platforms=csv_strings(platforms),
            include_bots=include_bots,
        )
        where, params, extra_where = _scope(app, filters)
        with _connect(app.state.db_path) as con:
            if app.state.has_analytics_facts:
                scope_from = """
                    FROM message_reaction_events r
                    JOIN messages m ON m.id = r.message_id
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON s.id = c.source_id
                """
                scope_where = f"WHERE {where}{extra_where}"
                totals = con.execute(
                    f"""
                    SELECT
                        COUNT(*),
                        COUNT(r.reactor_raw_id),
                        COUNT(r.reactor_person_id)
                    {scope_from}
                    {scope_where}
                    """,
                    params,
                ).fetchone()
                people_rows = con.execute(
                    f"""
                    SELECT
                        p.id,
                        p.display_name,
                        p.color,
                        COUNT(*) AS reaction_count
                    {scope_from}
                    JOIN people p ON p.id = r.reactor_person_id
                    {scope_where}
                    GROUP BY p.id, p.display_name, p.color
                    ORDER BY reaction_count DESC, p.display_name
                    LIMIT ?
                    """,
                    [*params, limit],
                ).fetchall()
            else:
                totals = con.execute(
                    f"""
                    SELECT SUM(COALESCE(m.reaction_count, 0)), 0, 0
                    FROM messages m
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON s.id = c.source_id
                    WHERE {where}{extra_where}
                    """,
                    params,
                ).fetchone()
                people_rows = []
        total = int(totals[0] or 0) if totals else 0
        identified = int(totals[1] or 0) if totals else 0
        resolved = int(totals[2] or 0) if totals else 0
        return {
            "supported": app.state.has_analytics_facts,
            "total_reactions": total,
            "identified_reactions": identified,
            "resolved_reactions": resolved,
            "identity_coverage": identified / total if total else 0.0,
            "people": [
                {
                    "id": int(row[0]),
                    "display_name": row[1],
                    "color": row[2],
                    "count": int(row[3]),
                }
                for row in people_rows
            ],
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
        include_bots: bool = False,
    ) -> dict[str, Any]:
        filters = QueryFilters(
            start=start,
            end=end,
            people=csv_ints(people, "people"),
            themes=csv_ints(themes, "themes"),
            platforms=csv_strings(platforms),
            include_bots=include_bots,
        )
        where, params, extra_where = _scope(app, filters)
        attachment_preview = (
            "m.attachment_preview"
            if app.state.has_attachment_preview
            else "NULL::TEXT"
        )
        reaction_summary = (
            "m.reaction_summary"
            if app.state.has_reaction_summary
            else "NULL::TEXT"
        )
        reaction_details = (
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
                    {attachment_preview},
                    p.display_name,
                    p.color,
                    c.name,
                    s.name,
                    m.reaction_count,
                    {reaction_summary},
                    {reaction_details}
                FROM messages m
                JOIN people p ON p.id = m.person_id
                JOIN channels c ON c.id = m.channel_id
                JOIN sources s ON s.id = c.source_id
                WHERE {where}{extra_where}
                  AND m.reaction_count > 0
                ORDER BY m.reaction_count DESC, m.ts DESC, m.id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, limit + 1, offset],
            ).fetchall()
        has_more = len(rows) > limit
        return {
            "has_more": has_more,
            "items": [
                {
                    "id": row[0],
                    "ts": row[1].isoformat() if row[1] else None,
                    "content": str(row[2] or "").strip(),
                    "attachment_preview": row[4],
                    "attachment_url": resolve_attachment(
                        row[4],
                        row[8],
                        app.state.data_dir,
                        app.state.signal_filename_index,
                    ),
                    "person_name": row[5],
                    "person_color": row[6],
                    "channel_name": display_name(
                        row[7],
                        row[8],
                        app.state.fb_chat_names,
                    ),
                    "source_name": row[8],
                    "reaction_count": int(row[9]),
                    "reaction_summary": row[10],
                    "reaction_details": normalize_reactions(
                        row[11],
                        row[8],
                        app.state.data_dir,
                        app.state.signal_filename_index,
                    ),
                }
                for row in rows[:limit]
            ],
        }

    @app.get("/api/emoji-usage")
    def emoji_usage(
        limit: int = Query(default=30, ge=1, le=100),
        start: date | None = Query(default=None, alias="from"),
        end: date | None = Query(default=None, alias="to"),
        people: str | None = None,
        themes: str | None = None,
        platforms: str | None = None,
        include_bots: bool = False,
    ) -> dict[str, Any]:
        filters = QueryFilters(
            start=start,
            end=end,
            people=csv_ints(people, "people"),
            themes=csv_ints(themes, "themes"),
            platforms=csv_strings(platforms),
            include_bots=include_bots,
        )
        where, params, extra_where = _scope(app, filters)
        with _connect(app.state.db_path) as con:
            if app.state.has_analytics_facts:
                rows = con.execute(
                    f"""
                    SELECT
                        r.emoji,
                        COUNT(*) AS reaction_count,
                        any_value(r.image_url) FILTER (
                            WHERE r.image_url IS NOT NULL
                        ) AS image_url,
                        any_value(s.name) FILTER (
                            WHERE r.image_url IS NOT NULL
                        ) AS source_name
                    FROM message_reaction_events r
                    JOIN messages m ON m.id = r.message_id
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON c.source_id = s.id
                    WHERE {where}{extra_where}
                      AND r.emoji <> 'unknown'
                    GROUP BY r.emoji
                    ORDER BY reaction_count DESC, r.emoji
                    LIMIT ?
                    """,
                    [*params, limit],
                ).fetchall()
                return {
                    "items": [
                        {
                            "name": row[0],
                            "count": int(row[1]),
                            "image_url": (
                                resolve_attachment(
                                    row[2],
                                    row[3],
                                    app.state.data_dir,
                                    app.state.signal_filename_index,
                                )
                                if row[2] and row[3]
                                else None
                            ),
                        }
                        for row in rows
                    ]
                }
            rows = con.execute(
                f"""
                SELECT m.reaction_details_json, m.reaction_summary, s.name
                FROM messages m
                JOIN channels c ON c.id = m.channel_id
                JOIN sources s ON c.source_id = s.id
                WHERE {where}{extra_where}
                  AND m.reaction_count > 0
                """,
                params,
            ).fetchall()
        emoji_counts: Counter[str] = Counter()
        emoji_images: dict[str, tuple[str, str]] = {}
        for details_json, summary, source_name in rows:
            if details_json:
                try:
                    details = (
                        json.loads(details_json)
                        if isinstance(details_json, str)
                        else details_json
                    )
                    for item in details if isinstance(details, list) else []:
                        name = str(item.get("name") or "").strip()
                        count = int(item.get("count") or 0)
                        if name and count > 0:
                            emoji_counts[name] += count
                            image_url = str(item.get("image_url") or "").strip()
                            if image_url and name not in emoji_images:
                                emoji_images[name] = (image_url, source_name)
                except Exception:
                    pass
            elif summary:
                for token in str(summary).split():
                    if "×" not in token:
                        continue
                    emoji, _, count = token.partition("×")
                    try:
                        emoji_counts[emoji.strip()] += int(count)
                    except ValueError:
                        pass
        items: list[dict[str, Any]] = []
        for name, count in emoji_counts.most_common(limit):
            image_meta = emoji_images.get(name)
            image_url = None
            if image_meta:
                raw_url, source_name = image_meta
                image_url = resolve_attachment(
                    raw_url,
                    source_name,
                    app.state.data_dir,
                    app.state.signal_filename_index,
                )
            items.append({"name": name, "count": count, "image_url": image_url})
        return {"items": items}
