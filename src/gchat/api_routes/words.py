"""Word-frequency and word-trend routes."""

from __future__ import annotations

from datetime import date
from typing import Any

import duckdb
from fastapi import FastAPI, HTTPException, Query

from ..analytics_sql import excluded_ids_sql, tokenized_message_source
from ..api_filters import QueryFilters, csv_ints, csv_strings, filters_clause
from ..stop_words import COMMON_STOP_WORDS


def _filters(
    app: FastAPI,
    *,
    start: date | None,
    end: date | None,
    people: str | None,
    themes: str | None,
    platforms: str | None,
    include_bots: bool,
) -> tuple[str, list[Any]]:
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
    return where, params


def register_word_routes(app: FastAPI) -> None:
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
        include_bots: bool = False,
    ) -> dict[str, Any]:
        where, params = _filters(
            app,
            start=start,
            end=end,
            people=people,
            themes=themes,
            platforms=platforms,
            include_bots=include_bots,
        )
        stop_words = sorted(COMMON_STOP_WORDS)
        stop_placeholders = ", ".join("?" for _ in stop_words)
        params.extend(stop_words)
        q_clause = ""
        if q and (query := q.strip().casefold()):
            q_clause = " AND word LIKE ?"
            params.append(f"%{query}%")
        limit_clause = ""
        if not all:
            limit_clause = " LIMIT ?"
            params.append(limit)
        excluded = excluded_ids_sql(app.state.excluded_message_ids)
        token_source = tokenized_message_source(
            selected_columns="",
            where=where,
            has_facts=app.state.has_analytics_facts,
            extra_where=(
                (" AND NOT m.is_system" if app.state.has_is_system else "") + excluded
            ),
            token_alias="word",
        )
        with duckdb.connect(str(app.state.db_path), read_only=True) as con:
            rows = con.execute(
                f"""
                WITH tokens AS (
                    {token_source}
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

    @app.get("/api/word-over-time")
    def word_over_time(
        word: str,
        start: date | None = Query(default=None, alias="from"),
        end: date | None = Query(default=None, alias="to"),
        people: str | None = None,
        themes: str | None = None,
        platforms: str | None = None,
        include_bots: bool = False,
    ) -> dict[str, Any]:
        normalized = "".join(char for char in word.casefold() if "a" <= char <= "z")
        if len(normalized) < 3:
            raise HTTPException(
                status_code=400, detail="Word must contain at least 3 letters"
            )
        where, params = _filters(
            app,
            start=start,
            end=end,
            people=people,
            themes=themes,
            platforms=platforms,
            include_bots=include_bots,
        )
        token_source = tokenized_message_source(
            selected_columns="date_trunc('month', m.ts) AS month",
            where=where,
            has_facts=app.state.has_analytics_facts,
            extra_where=(
                (" AND NOT m.is_system" if app.state.has_is_system else "")
                + excluded_ids_sql(app.state.excluded_message_ids)
            ),
        )
        with duckdb.connect(str(app.state.db_path), read_only=True) as con:
            rows = con.execute(
                f"""
                WITH tokenized AS (
                    {token_source}
                ),
                totals AS (
                    SELECT month, COUNT(*) AS total_words
                    FROM tokenized
                    GROUP BY month
                ),
                word_counts AS (
                    SELECT month, COUNT(*) AS usage_count
                    FROM tokenized
                    WHERE token = ?
                    GROUP BY month
                )
                SELECT
                    t.month,
                    COALESCE(wc.usage_count, 0) AS usage_count,
                    t.total_words
                FROM totals t
                LEFT JOIN word_counts wc ON wc.month = t.month
                ORDER BY t.month
                """,
                [*params, normalized],
            ).fetchall()
        return {
            "word": normalized,
            "points": [
                {
                    "month": row[0].isoformat() if row[0] else None,
                    "count": int(row[1]),
                    "total_words": int(row[2]),
                    "percent": (
                        round((int(row[1]) / int(row[2])) * 100, 4)
                        if int(row[2]) > 0
                        else 0.0
                    ),
                }
                for row in rows
            ],
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
        include_bots: bool = False,
    ) -> dict[str, Any]:
        normalized = "".join(char for char in word.casefold() if "a" <= char <= "z")
        if len(normalized) < 3:
            raise HTTPException(
                status_code=400, detail="Word must contain at least 3 letters"
            )
        where, params = _filters(
            app,
            start=start,
            end=end,
            people=people,
            themes=themes,
            platforms=platforms,
            include_bots=include_bots,
        )
        token_source = tokenized_message_source(
            selected_columns=(
                "m.person_id, c.id AS channel_id, c.name AS channel_name, "
                "s.name AS source_name"
            ),
            where=where,
            has_facts=app.state.has_analytics_facts,
            extra_where=(
                (" AND NOT m.is_system" if app.state.has_is_system else "")
                + excluded_ids_sql(app.state.excluded_message_ids)
            ),
        )
        with duckdb.connect(str(app.state.db_path), read_only=True) as con:
            people_rows = con.execute(
                f"""
                WITH tokens AS (
                    {token_source}
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
                    {token_source}
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
                    "name": _display_name(row[1], row[2], app.state.fb_chat_names),
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
        include_bots: bool = False,
    ) -> dict[str, Any]:
        normalized = "".join(char for char in word.casefold() if "a" <= char <= "z")
        if len(normalized) < 3:
            raise HTTPException(
                status_code=400, detail="Word must contain at least 3 letters"
            )
        where, params = _filters(
            app,
            start=start,
            end=end,
            people=people,
            themes=themes,
            platforms=platforms,
            include_bots=include_bots,
        )
        token_source = tokenized_message_source(
            selected_columns=(
                "m.id AS message_id, m.ts, m.content, "
                "p.display_name AS person_name, p.color AS person_color, "
                "c.name AS channel_name, s.name AS source_name"
            ),
            where=where,
            has_facts=app.state.has_analytics_facts,
            extra_joins="JOIN people p ON p.id = m.person_id",
            extra_where=(
                (" AND NOT m.is_system" if app.state.has_is_system else "")
                + excluded_ids_sql(app.state.excluded_message_ids)
            ),
        )
        word_cte = f"WITH tokens AS ({token_source})"

        def message_dict(row: Any) -> dict[str, Any]:
            return {
                "id": row[0],
                "ts": row[1].isoformat() if row[1] else None,
                "content": row[2],
                "person_name": row[3],
                "person_color": row[4],
                "channel_name": _display_name(
                    row[5],
                    row[6],
                    app.state.fb_chat_names,
                ),
                "source_name": row[6],
            }

        with duckdb.connect(str(app.state.db_path), read_only=True) as con:
            rows = con.execute(
                word_cte
                + """
                SELECT DISTINCT
                    message_id, ts, content, person_name, person_color,
                    channel_name, source_name
                FROM tokens
                WHERE token = ?
                ORDER BY ts DESC, message_id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, normalized, limit, offset],
            ).fetchall()
            first_row = con.execute(
                word_cte
                + """
                SELECT DISTINCT
                    message_id, ts, content, person_name, person_color,
                    channel_name, source_name
                FROM tokens
                WHERE token = ?
                ORDER BY ts ASC, message_id ASC
                LIMIT 1
                """,
                [*params, normalized],
            ).fetchone()
        return {
            "word": normalized,
            "has_more": len(rows) == limit,
            "first_message": message_dict(first_row) if first_row else None,
            "messages": [message_dict(row) for row in rows],
        }


def _display_name(
    channel_name: str,
    source_name: str,
    facebook_names: dict[str, str],
) -> str:
    if source_name.startswith("Facebook: "):
        return facebook_names.get(channel_name, channel_name)
    return channel_name
