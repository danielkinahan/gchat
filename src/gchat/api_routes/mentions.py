"""Mention analytics routes."""

from __future__ import annotations

from datetime import date
from typing import Any

import duckdb
from fastapi import FastAPI, Query

from ..analytics_sql import metric_sql
from ..api_filters import QueryFilters, csv_ints, csv_strings, filters_clause


def register_mention_routes(app: FastAPI) -> None:
    @app.get("/api/most-mentioned")
    def most_mentioned(
        limit: int = Query(default=200, ge=1, le=1000),
        start: date | None = Query(default=None, alias="from"),
        end: date | None = Query(default=None, alias="to"),
        people: str | None = None,
        themes: str | None = None,
        platforms: str | None = None,
        include_bots: bool = False,
    ) -> dict[str, Any]:
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
        scope = metric_sql(
            "messages",
            has_is_system=app.state.has_is_system,
            excluded_ids=app.state.excluded_message_ids,
        )
        if app.state.has_analytics_facts:
            source = f"""
                SELECT mm.mention
                FROM message_mentions mm
                JOIN messages m ON m.id = mm.message_id
                JOIN channels c ON c.id = m.channel_id
                JOIN sources s ON c.source_id = s.id
                WHERE {where}{scope.extra_where}
            """
        else:
            source = f"""
                SELECT lower(mention) AS mention
                FROM messages m
                JOIN channels c ON c.id = m.channel_id
                JOIN sources s ON c.source_id = s.id
                CROSS JOIN unnest(
                    regexp_extract_all(
                        coalesce(m.content, ''),
                        '@([A-Za-z0-9_]+)',
                        1
                    )
                ) AS t(mention)
                WHERE {where}{scope.extra_where}
                  AND m.content IS NOT NULL AND m.content <> ''
            """
        with duckdb.connect(str(app.state.db_path), read_only=True) as con:
            rows = con.execute(
                f"""
                WITH mentions AS (
                    {source}
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
