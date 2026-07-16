"""Message search route with trigram prefilter and legacy fallback."""

from __future__ import annotations

from datetime import date
from typing import Any

import duckdb
from fastapi import FastAPI, Query

from ..analytics_sql import excluded_ids_sql
from ..api_filters import QueryFilters, csv_ints, csv_strings, filters_clause


def register_search_routes(app: FastAPI) -> None:
    @app.get("/api/search")
    def search(
        q: str = Query(min_length=1, max_length=500),
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        start: date | None = Query(default=None, alias="from"),
        end: date | None = Query(default=None, alias="to"),
        people: str | None = None,
        themes: str | None = None,
        platforms: str | None = None,
    ) -> dict[str, Any]:
        filters = QueryFilters(
            start=start,
            end=end,
            people=csv_ints(people, "people"),
            themes=csv_ints(themes, "themes"),
            platforms=csv_strings(platforms),
            include_bots=True,
        )
        params: list[Any] = []
        where = filters_clause(
            filters,
            params,
            app.state.reconciliation,
            app.state.theme_id_to_name,
        )
        has_non_ascii = any(ord(char) > 127 for char in q)
        like_op = "LIKE" if has_non_ascii else "ILIKE"
        search_param = f"%{q}%"
        fact_filter = ""
        fact_params: list[Any] = []
        normalized_query = q.casefold()
        if (
            app.state.has_analytics_facts
            and not has_non_ascii
            and len(normalized_query) >= 3
        ):
            trigrams = sorted(
                {
                    normalized_query[index : index + 3]
                    for index in range(len(normalized_query) - 2)
                }
            )
            placeholders = ", ".join("?" for _ in trigrams)
            fact_filter = f"""
                AND m.id IN (
                    SELECT message_id
                    FROM message_search_trigrams
                    WHERE gram IN ({placeholders})
                    GROUP BY message_id
                    HAVING COUNT(DISTINCT gram) = ?
                )
            """
            fact_params = [*trigrams, len(trigrams)]
        excluded_filter = excluded_ids_sql(app.state.excluded_message_ids)
        with duckdb.connect(str(app.state.db_path), read_only=True) as con:
            total_row = con.execute(
                f"""
                SELECT COUNT(*)
                FROM messages m
                JOIN channels c ON c.id = m.channel_id
                JOIN sources s ON c.source_id = s.id
                LEFT JOIN people p ON p.id = m.person_id
                WHERE {where}
                  {excluded_filter}
                  {fact_filter}
                  AND m.content {like_op} ?
                """,
                params + fact_params + [search_param],
            ).fetchone()
            total = int(total_row[0]) if total_row else 0
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
                    s.platform
                FROM messages m
                JOIN channels c ON c.id = m.channel_id
                JOIN sources s ON c.source_id = s.id
                LEFT JOIN people p ON p.id = m.person_id
                WHERE {where}
                  {excluded_filter}
                  {fact_filter}
                  AND m.content {like_op} ?
                ORDER BY m.ts DESC
                LIMIT ? OFFSET ?
                """,
                params + fact_params + [search_param, limit, offset],
            ).fetchall()
        return {
            "total": total,
            "has_more": (offset + limit) < total,
            "items": [
                {
                    "id": str(row[0]),
                    "ts": (
                        row[1].isoformat()
                        if hasattr(row[1], "isoformat")
                        else str(row[1])
                    ),
                    "content": row[2],
                    "person_name": row[3],
                    "person_color": row[4],
                    "channel_name": row[5],
                    "source_name": row[6],
                    "platform": row[7],
                }
                for row in rows
            ],
        }
