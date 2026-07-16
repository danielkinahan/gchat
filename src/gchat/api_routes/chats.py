"""Top chat and configured-theme routes."""

from __future__ import annotations

from datetime import date
from typing import Any

import duckdb
from fastapi import FastAPI, HTTPException, Query

from ..analytics_sql import metric_sql
from ..api_filters import QueryFilters, csv_ints, csv_strings, filters_clause


def _metric(value: str) -> str:
    normalized = value.strip().casefold()
    if normalized not in {"messages", "words", "conversations"}:
        raise HTTPException(status_code=400, detail="Invalid metric filter")
    return normalized


def _display_name(
    channel_name: str,
    source_name: str,
    facebook_names: dict[str, str],
) -> str:
    if source_name.startswith("Facebook: "):
        return facebook_names.get(channel_name, channel_name)
    return channel_name


def register_chat_routes(app: FastAPI) -> None:
    @app.get("/api/top-chats")
    def top_chats(
        limit: int = Query(default=10, ge=1, le=100),
        start: date | None = Query(default=None, alias="from"),
        end: date | None = Query(default=None, alias="to"),
        people: str | None = None,
        themes: str | None = None,
        platforms: str | None = None,
        include_bots: bool = False,
        metric: str = Query(
            default="messages", pattern="^(messages|words|conversations)$"
        ),
    ) -> dict[str, Any]:
        normalized_metric = _metric(metric)
        params: list[Any] = []
        where = filters_clause(
            QueryFilters(
                start=start,
                end=end,
                people=csv_ints(people, "people"),
                themes=csv_ints(themes, "themes"),
                platforms=csv_strings(platforms),
                include_bots=include_bots,
            ),
            params,
            app.state.reconciliation,
            app.state.theme_id_to_name,
        )
        params.append(limit)
        metric_parts = metric_sql(
            normalized_metric,
            has_is_system=app.state.has_is_system,
            has_word_count=app.state.has_word_count,
            excluded_ids=app.state.excluded_message_ids,
        )
        with duckdb.connect(str(app.state.db_path), read_only=True) as con:
            rows = con.execute(
                f"""
                SELECT
                    c.id,
                    c.name,
                    t.name AS theme_name,
                    s.name AS source_name,
                    {metric_parts.aggregate} AS message_count
                FROM (
                    SELECT m.channel_id{metric_parts.inner_select_suffix}
                    FROM messages m
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON c.source_id = s.id
                    WHERE {where}{metric_parts.extra_where}
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
                    "name": _display_name(
                        row[1],
                        row[3],
                        app.state.fb_chat_names,
                    ),
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
        include_bots: bool = False,
        metric: str = Query(
            default="messages", pattern="^(messages|words|conversations)$"
        ),
    ) -> dict[str, Any]:
        normalized_metric = _metric(metric)
        configured_themes = (
            app.state.reconciliation.themes.configured_theme_names
        )
        if not configured_themes:
            return {"items": []}
        params: list[Any] = []
        where = filters_clause(
            QueryFilters(
                start=start,
                end=end,
                people=csv_ints(people, "people"),
                themes=csv_ints(themes, "themes"),
                platforms=csv_strings(platforms),
                include_bots=include_bots,
            ),
            params,
            app.state.reconciliation,
            app.state.theme_id_to_name,
        )
        metric_parts = metric_sql(
            normalized_metric,
            has_is_system=app.state.has_is_system,
            has_word_count=app.state.has_word_count,
            excluded_ids=app.state.excluded_message_ids,
        )
        with duckdb.connect(str(app.state.db_path), read_only=True) as con:
            rows = con.execute(
                f"""
                SELECT s.name, c.name, {metric_parts.aggregate} AS message_count
                FROM (
                    SELECT m.channel_id{metric_parts.inner_select_suffix}
                    FROM messages m
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON c.source_id = s.id
                    WHERE {where}{metric_parts.extra_where}
                ) counted
                JOIN channels c ON c.id = counted.channel_id
                JOIN sources s ON s.id = c.source_id
                GROUP BY s.name, c.name
                """,
                params,
            ).fetchall()
        theme_counts: dict[str, int] = {}
        for source_name, channel_name, count in rows:
            theme_name = app.state.reconciliation.themes.resolve(
                source_name,
                channel_name,
            )
            if theme_name in configured_themes:
                theme_counts[theme_name] = theme_counts.get(theme_name, 0) + int(
                    count
                )
        theme_list = sorted(
            theme_counts.items(),
            key=lambda item: item[1],
            reverse=True,
        )[:limit]
        return {
            "items": [
                {"id": 0, "name": name, "message_count": count}
                for name, count in theme_list
            ]
        }
