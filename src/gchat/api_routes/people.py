"""People rankings, diversity, exclusive words, and metadata routes."""

from __future__ import annotations

from datetime import date
from typing import Any

import duckdb
from fastapi import FastAPI, HTTPException, Query

from ..analytics_sql import excluded_ids_sql, metric_sql
from ..api_filters import QueryFilters, csv_ints, csv_strings, filters_clause
from ..dictionary import partition_dictionary_words
from ..display_config import people_display_metadata, theme_emoji
from ..person_stats import (
    compute_exclusive_words,
    compute_person_stats,
    person_stats_row_to_dict,
)


def _connect(app: FastAPI) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(app.state.db_path), read_only=True)


def _filters(
    app: FastAPI,
    *,
    start: date | None,
    end: date | None,
    people: str | None,
    themes: str | None,
    platforms: str | None,
    include_bots: bool,
) -> QueryFilters:
    return QueryFilters(
        start=start,
        end=end,
        people=csv_ints(people, "people"),
        themes=csv_ints(themes, "themes"),
        platforms=csv_strings(platforms),
        include_bots=include_bots,
    )


def register_people_routes(app: FastAPI) -> None:
    @app.get("/api/top-people")
    def top_people(
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
        params: list[Any] = []
        where = filters_clause(
            _filters(
                app,
                start=start,
                end=end,
                people=people,
                themes=themes,
                platforms=platforms,
                include_bots=include_bots,
            ),
            params,
            app.state.reconciliation,
            app.state.theme_id_to_name,
        )
        params.append(limit)
        parts = metric_sql(
            metric,
            has_is_system=app.state.has_is_system,
            has_word_count=app.state.has_word_count,
            excluded_ids=app.state.excluded_message_ids,
        )
        with _connect(app) as con:
            rows = con.execute(
                f"""
                SELECT p.id, p.display_name, p.color,
                       {parts.aggregate} AS message_count
                FROM (
                    SELECT m.person_id{parts.inner_select_suffix}
                    FROM messages m
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON c.source_id = s.id
                    WHERE {where}{parts.extra_where}
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

    @app.get("/api/person-diversity")
    def person_diversity(
        limit: int = Query(default=50, ge=1, le=500),
        start: date | None = Query(default=None, alias="from"),
        end: date | None = Query(default=None, alias="to"),
        people: str | None = None,
        themes: str | None = None,
        platforms: str | None = None,
        include_bots: bool = False,
    ) -> dict[str, Any]:
        query_filters = _filters(
            app,
            start=start,
            end=end,
            people=people,
            themes=themes,
            platforms=platforms,
            include_bots=include_bots,
        )
        needs_live = bool(
            query_filters.start
            or query_filters.end
            or query_filters.themes
            or query_filters.platforms
            or (bool(app.state.bot_person_ids) and include_bots)
            or not app.state.has_person_stats
        )
        people_extra = people_display_metadata(app.state.config_dir)
        with _connect(app) as con:
            if needs_live:
                params: list[Any] = []
                where = filters_clause(
                    query_filters,
                    params,
                    app.state.reconciliation,
                    app.state.theme_id_to_name,
                )
                stats_rows = compute_person_stats(
                    con,
                    where=where,
                    params=params,
                    has_is_system=app.state.has_is_system,
                    excluded_filter=excluded_ids_sql(
                        app.state.excluded_message_ids
                    ),
                )
            else:
                params = []
                people_clause = ""
                if query_filters.people:
                    placeholders = ", ".join("?" for _ in query_filters.people)
                    people_clause = f"AND ps.person_id IN ({placeholders})"
                    params.extend(query_filters.people)
                stats_rows = con.execute(
                    f"""
                    SELECT
                        ps.person_id, ps.message_count, ps.unique_words,
                        ps.total_words, ps.mtld, ps.word_entropy,
                        ps.exclusive_word_count, ps.channel_count,
                        ps.theme_count, ps.platform_count, ps.channel_hhi
                    FROM person_stats ps
                    WHERE 1 = 1 {people_clause}
                    ORDER BY ps.message_count DESC, ps.person_id
                    """,
                    params,
                ).fetchall()
            people_rows = con.execute(
                "SELECT id, display_name, color FROM people"
            ).fetchall()
        people_by_id = {
            int(row[0]): (str(row[1]), str(row[2] or "")) for row in people_rows
        }
        items: list[dict[str, Any]] = []
        for row in stats_rows:
            meta = people_by_id.get(int(row[0]))
            if meta is None:
                continue
            display_name, color = meta
            if (
                not include_bots
                and display_name
                in app.state.reconciliation.people.bot_person_names
            ):
                continue
            if (
                not query_filters.people
                and app.state.configured_people_names
                and display_name not in app.state.configured_people_names
            ):
                continue
            extra = people_extra.get(display_name, {})
            items.append(
                person_stats_row_to_dict(
                    row,
                    display_name=display_name,
                    color=color or extra.get("color", ""),
                    avatar=extra.get("avatar", ""),
                )
            )
        items.sort(
            key=lambda item: (
                -int(item["message_count"]),
                str(item["display_name"]),
            )
        )
        return {
            "items": items[:limit],
            "source": "live" if needs_live else "materialized",
        }

    @app.get("/api/person-exclusive-words")
    def person_exclusive_words(
        person_id: int = Query(..., ge=1),
        limit: int = Query(default=10000, ge=1, le=10000),
        start: date | None = Query(default=None, alias="from"),
        end: date | None = Query(default=None, alias="to"),
        people: str | None = None,
        themes: str | None = None,
        platforms: str | None = None,
        include_bots: bool = False,
    ) -> dict[str, Any]:
        params: list[Any] = []
        where = filters_clause(
            _filters(
                app,
                start=start,
                end=end,
                people=people,
                themes=themes,
                platforms=platforms,
                include_bots=include_bots,
            ),
            params,
            app.state.reconciliation,
            app.state.theme_id_to_name,
        )
        with _connect(app) as con:
            person_row = con.execute(
                "SELECT display_name FROM people WHERE id = ?",
                [person_id],
            ).fetchone()
            if person_row is None:
                raise HTTPException(status_code=404, detail="Person not found")
            words = compute_exclusive_words(
                con,
                person_id,
                where=where,
                params=params,
                has_is_system=app.state.has_is_system,
                excluded_filter=excluded_ids_sql(app.state.excluded_message_ids),
                limit=limit,
            )
        dictionary_words, other_words = partition_dictionary_words(words)
        return {
            "person_id": person_id,
            "display_name": str(person_row[0]),
            "words": words,
            "dictionary_words": dictionary_words,
            "other_words": other_words,
            "count": len(words),
            "dictionary_count": len(dictionary_words),
            "other_count": len(other_words),
            "truncated": len(words) >= limit,
        }

    @app.get("/api/metadata")
    def metadata() -> dict[str, Any]:
        with _connect(app) as con:
            people = con.execute(
                "SELECT id, display_name, color FROM people ORDER BY display_name, id"
            ).fetchall()
            platforms = con.execute(
                "SELECT DISTINCT platform FROM sources ORDER BY platform"
            ).fetchall()
        if app.state.configured_people_names:
            people = [
                row
                for row in people
                if row[1] in app.state.configured_people_names
            ]
        people_extra = people_display_metadata(app.state.config_dir)
        themes_extra = theme_emoji(app.state.config_dir)
        return {
            "people": [
                {
                    "id": int(row[0]),
                    "name": row[1],
                    "color": row[2]
                    or people_extra.get(row[1], {}).get("color", ""),
                    "avatar": people_extra.get(row[1], {}).get("avatar", ""),
                    "is_bot": row[1]
                    in app.state.reconciliation.people.bot_person_names,
                }
                for row in people
            ],
            "themes": [
                {
                    "id": int(theme_id),
                    "name": theme_name,
                    "emoji": themes_extra.get(theme_name, ""),
                }
                for theme_id, theme_name in sorted(
                    app.state.theme_id_to_name.items()
                )
            ],
            "platforms": [row[0] for row in platforms],
        }
