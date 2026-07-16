"""Overview statistics route."""

from __future__ import annotations

from datetime import date
from typing import Any

import duckdb
from fastapi import FastAPI, HTTPException, Query

from ..analytics_sql import metric_sql
from ..api_filters import QueryFilters, csv_ints, csv_strings, filters_clause


def _connect(app: FastAPI) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(app.state.db_path), read_only=True)


def _count_metric(metric: str) -> str:
    normalized = metric.strip().casefold()
    if normalized not in {"messages", "words", "conversations"}:
        raise HTTPException(status_code=400, detail="Invalid metric filter")
    return normalized


def register_overview_routes(app: FastAPI) -> None:
    @app.get("/api/overview")
    def overview(
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
        metric = _count_metric(metric)
        filters = QueryFilters(
            start=start,
            end=end,
            people=csv_ints(people, "people"),
            themes=csv_ints(themes, "themes"),
            platforms=csv_strings(platforms),
            include_bots=include_bots,
        )
        params: list[Any] = []
        where = filters_clause(
            filters, params, app.state.reconciliation, app.state.theme_id_to_name
        )
        edited_expr = (
            "COALESCE(CAST(m.is_edited AS INTEGER), 0)"
            if app.state.has_is_edited
            else "0"
        )
        ms = metric_sql(
            metric,
            has_is_system=app.state.has_is_system,
            has_word_count=app.state.has_word_count,
            excluded_ids=app.state.excluded_message_ids,
        )
        message_scope = metric_sql(
            "messages",
            has_is_system=app.state.has_is_system,
            has_word_count=app.state.has_word_count,
            excluded_ids=app.state.excluded_message_ids,
        )
        with _connect(app) as con:
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
                    WHERE {where}{message_scope.extra_where}
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
                    WHERE {where}{message_scope.extra_where}
                ),
                ordered AS (
                    SELECT ts, LAG(ts) OVER (ORDER BY ts) AS prev_ts
                    FROM filtered
                ),
                gaps AS (
                    SELECT
                        prev_ts,
                        ts,
                        date_diff('second', prev_ts, ts) AS gap_seconds
                    FROM ordered
                    WHERE prev_ts IS NOT NULL
                )
                SELECT gap_seconds, prev_ts, ts
                FROM gaps
                ORDER BY gap_seconds DESC, prev_ts ASC
                LIMIT 1
                """,
                params,
            ).fetchone()
            longest_active_row = con.execute(
                f"""
                WITH filtered AS (
                    SELECT m.id, m.ts
                    FROM messages m
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON c.source_id = s.id
                    WHERE {where}{message_scope.extra_where}
                ),
                ordered AS (
                    SELECT id, ts, LAG(ts) OVER (ORDER BY ts) AS prev_ts
                    FROM filtered
                ),
                grouped AS (
                    SELECT
                        id,
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
                        MAX(ts) AS end_ts,
                        arg_min(id, ts) AS start_message_id
                    FROM grouped
                    GROUP BY session_id
                )
                SELECT
                    date_diff('second', start_ts, end_ts) AS duration_seconds,
                    start_message_id
                FROM sessions
                ORDER BY duration_seconds DESC, start_ts ASC
                LIMIT 1
                """,
                params,
            ).fetchone()
            most_active: dict[str, tuple[Any, Any] | None] = {}
            for granularity in ("year", "month", "day", "hour"):
                most_active[granularity] = con.execute(
                    f"""
                    SELECT date_trunc('{granularity}', bucket_ts) AS bucket,
                           {ms.aggregate} AS count
                    FROM (
                        SELECT m.ts AS bucket_ts{ms.inner_select_suffix}
                        FROM messages m
                        JOIN channels c ON c.id = m.channel_id
                        JOIN sources s ON c.source_id = s.id
                        WHERE {where}{ms.extra_where}
                    )
                    GROUP BY bucket
                    ORDER BY count DESC, bucket ASC
                    LIMIT 1
                    """,
                    params,
                ).fetchone()
            most_active_year = most_active["year"]
            most_active_month = most_active["month"]
            most_active_day = most_active["day"]
            most_active_hour = most_active["hour"]
            conversations_row = (None, None, None)
            if app.state.has_conversation_id:
                conversations_row = con.execute(
                    f"""
                    WITH filtered AS (
                        SELECT m.conversation_id
                        FROM messages m
                        JOIN channels c ON c.id = m.channel_id
                        JOIN sources s ON c.source_id = s.id
                        WHERE {where}{message_scope.extra_where}
                          AND m.conversation_id IS NOT NULL
                    ),
                    per_conversation AS (
                        SELECT conversation_id, COUNT(*) AS message_count
                        FROM filtered
                        GROUP BY conversation_id
                    )
                    SELECT
                        COUNT(*) AS conversation_count,
                        AVG(message_count) AS avg_messages,
                        MAX(message_count) AS longest_conversation
                    FROM per_conversation
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
                "longest_period_without_messages_seconds": int(
                    (longest_gap_row or (0, None, None))[0] or 0
                ),
                "longest_period_without_messages_start": (
                    (longest_gap_row or (0, None, None))[1].isoformat()
                    if longest_gap_row and longest_gap_row[1]
                    else None
                ),
                "longest_period_without_messages_end": (
                    (longest_gap_row or (0, None, None))[2].isoformat()
                    if longest_gap_row and longest_gap_row[2]
                    else None
                ),
                "longest_active_conversation_seconds": int(
                    (longest_active_row or (0, None))[0] or 0
                ),
                "longest_active_conversation_message_id": (
                    str((longest_active_row or (0, None))[1])
                    if longest_active_row and longest_active_row[1]
                    else None
                ),
                "most_active_year": {
                    "bucket": (
                        most_active_year[0].isoformat() if most_active_year else None
                    ),
                    "count": int(most_active_year[1]) if most_active_year else 0,
                },
                "most_active_month": {
                    "bucket": (
                        most_active_month[0].isoformat() if most_active_month else None
                    ),
                    "count": int(most_active_month[1]) if most_active_month else 0,
                },
                "most_active_day": {
                    "bucket": (
                        most_active_day[0].isoformat() if most_active_day else None
                    ),
                    "count": int(most_active_day[1]) if most_active_day else 0,
                },
                "most_active_hour": {
                    "bucket": (
                        most_active_hour[0].isoformat() if most_active_hour else None
                    ),
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
