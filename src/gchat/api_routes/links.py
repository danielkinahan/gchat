"""Link-domain, example, and author analytics routes."""

from __future__ import annotations

from datetime import date
from typing import Any

import duckdb
from fastapi import FastAPI, HTTPException, Query

from ..analytics_sql import (
    canonical_link_domain_expr,
    metric_sql,
)
from ..api_filters import QueryFilters, csv_ints, csv_strings, filters_clause


def _filters(
    app: FastAPI,
    *,
    start: date | None,
    end: date | None,
    people: str | None,
    themes: str | None,
    platforms: str | None,
    include_bots: bool,
) -> tuple[str, list[Any], str]:
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
    return where, params, scope.extra_where


def register_link_routes(app: FastAPI) -> None:
    @app.get("/api/linked-domains")
    def linked_domains(
        limit: int = Query(default=200, ge=1, le=1000),
        start: date | None = Query(default=None, alias="from"),
        end: date | None = Query(default=None, alias="to"),
        people: str | None = None,
        themes: str | None = None,
        platforms: str | None = None,
        include_bots: bool = False,
    ) -> dict[str, Any]:
        where, params, extra_where = _filters(
            app,
            start=start,
            end=end,
            people=people,
            themes=themes,
            platforms=platforms,
            include_bots=include_bots,
        )
        if app.state.has_analytics_facts:
            link_source = f"""
                SELECT ml.domain
                FROM message_links ml
                JOIN messages m ON m.id = ml.message_id
                JOIN channels c ON c.id = m.channel_id
                JOIN sources s ON c.source_id = s.id
                WHERE {where}{extra_where}
            """
        else:
            link_source = f"""
                SELECT {canonical_link_domain_expr("link")} AS domain
                FROM messages m
                JOIN channels c ON c.id = m.channel_id
                JOIN sources s ON c.source_id = s.id
                CROSS JOIN unnest(
                    regexp_extract_all(
                        coalesce(m.content, ''),
                        'https?://([^/?#\\s]+)',
                        1
                    )
                ) AS t(link)
                WHERE {where}{extra_where}
                  AND m.content IS NOT NULL AND m.content <> ''
            """
        with duckdb.connect(str(app.state.db_path), read_only=True) as con:
            rows = con.execute(
                f"""
                WITH links AS (
                    {link_source}
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
        include_bots: bool = False,
    ) -> dict[str, Any]:
        normalized = domain.strip().casefold()
        if not normalized or len(normalized) > 255:
            raise HTTPException(status_code=400, detail="Invalid domain")
        where, params, extra_where = _filters(
            app,
            start=start,
            end=end,
            people=people,
            themes=themes,
            platforms=platforms,
            include_bots=include_bots,
        )
        selected = """
            m.id AS message_id,
            m.ts,
            m.content,
            p.display_name AS person_name,
            p.color AS person_color,
            c.name AS channel_name,
            s.name AS source_name
        """
        if app.state.has_analytics_facts:
            source = f"""
                SELECT {selected}, ml.domain
                FROM message_links ml
                JOIN messages m ON m.id = ml.message_id
                JOIN people p ON p.id = m.person_id
                JOIN channels c ON c.id = m.channel_id
                JOIN sources s ON c.source_id = s.id
                WHERE {where}{extra_where}
            """
        else:
            source = f"""
                SELECT
                    {selected},
                    {canonical_link_domain_expr("t.link")} AS domain
                FROM messages m
                JOIN people p ON p.id = m.person_id
                JOIN channels c ON c.id = m.channel_id
                JOIN sources s ON c.source_id = s.id
                CROSS JOIN unnest(
                    regexp_extract_all(
                        coalesce(m.content, ''),
                        'https?://([^/?#\\s]+)',
                        1
                    )
                ) AS t(link)
                WHERE {where}{extra_where}
                  AND m.content IS NOT NULL AND m.content <> ''
            """
        with duckdb.connect(str(app.state.db_path), read_only=True) as con:
            rows = con.execute(
                f"""
                WITH matched_links AS (
                    {source}
                )
                SELECT DISTINCT
                    message_id, ts, content, person_name, person_color,
                    channel_name, source_name
                FROM matched_links
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
                    "channel_name": _display_name(
                        row[5], row[6], app.state.fb_chat_names
                    ),
                    "source_name": row[6],
                }
                for row in rows
            ],
        }

    @app.get("/api/links-by-author")
    def links_by_author(
        limit: int = Query(default=15, ge=1, le=100),
        start: date | None = Query(default=None, alias="from"),
        end: date | None = Query(default=None, alias="to"),
        people: str | None = None,
        themes: str | None = None,
        platforms: str | None = None,
        include_bots: bool = False,
    ) -> dict[str, Any]:
        where, params, extra_where = _filters(
            app,
            start=start,
            end=end,
            people=people,
            themes=themes,
            platforms=platforms,
            include_bots=include_bots,
        )
        if app.state.has_analytics_facts:
            source = f"""
                SELECT m.person_id, ml.domain
                FROM message_links ml
                JOIN messages m ON m.id = ml.message_id
                JOIN channels c ON c.id = m.channel_id
                JOIN sources s ON c.source_id = s.id
                WHERE {where}{extra_where}
            """
        else:
            source = f"""
                SELECT
                    m.person_id,
                    {canonical_link_domain_expr("link")} AS domain
                FROM messages m
                JOIN channels c ON c.id = m.channel_id
                JOIN sources s ON c.source_id = s.id
                CROSS JOIN unnest(
                    regexp_extract_all(
                        coalesce(m.content, ''),
                        'https?://([^/?#\\s]+)',
                        1
                    )
                ) AS t(link)
                WHERE {where}{extra_where}
                  AND m.content IS NOT NULL AND m.content <> ''
            """
        with duckdb.connect(str(app.state.db_path), read_only=True) as con:
            rows = con.execute(
                f"""
                WITH links AS (
                    {source}
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


def _display_name(
    channel_name: str,
    source_name: str,
    facebook_names: dict[str, str],
) -> str:
    if source_name.startswith("Facebook: "):
        return facebook_names.get(channel_name, channel_name)
    return channel_name
