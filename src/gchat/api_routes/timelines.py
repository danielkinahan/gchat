"""Time-bucketed activity routes."""

from __future__ import annotations

from datetime import date
from typing import Any

import duckdb
from fastapi import FastAPI, HTTPException, Query

from ..analytics_sql import metric_sql
from ..api_filters import QueryFilters, csv_ints, csv_strings, filters_clause


def _scope(
    app: FastAPI,
    *,
    start: date | None,
    end: date | None,
    people: str | None,
    themes: str | None,
    platforms: str | None,
    include_bots: bool,
    metric: str,
    params: list[Any],
) -> tuple[str, Any]:
    normalized_metric = metric.strip().casefold()
    if normalized_metric not in {"messages", "words", "conversations"}:
        raise HTTPException(status_code=400, detail="Invalid metric filter")
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
    return where, metric_sql(
        normalized_metric,
        has_is_system=app.state.has_is_system,
        has_word_count=app.state.has_word_count,
        excluded_ids=app.state.excluded_message_ids,
    )


def _connect(app: FastAPI) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(app.state.db_path), read_only=True)


def register_timeline_routes(app: FastAPI) -> None:
    @app.get("/api/messages-over-time")
    def messages_over_time(
        granularity: str = Query(default="day", pattern="^(day|week|month)$"),
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
        params: list[Any] = [granularity]
        where, parts = _scope(
            app,
            start=start,
            end=end,
            people=people,
            themes=themes,
            platforms=platforms,
            include_bots=include_bots,
            metric=metric,
            params=params,
        )
        with _connect(app) as con:
            rows = con.execute(
                f"""
                SELECT date_trunc(?, bucket_ts) AS bucket,
                       {parts.aggregate} AS message_count
                FROM (
                    SELECT m.ts AS bucket_ts{parts.inner_select_suffix}
                    FROM messages m
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON c.source_id = s.id
                    WHERE {where}{parts.extra_where}
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
        include_bots: bool = False,
        metric: str = Query(
            default="messages", pattern="^(messages|words|conversations)$"
        ),
    ) -> dict[str, Any]:
        params: list[Any] = [granularity]
        where, parts = _scope(
            app,
            start=start,
            end=end,
            people=people,
            themes=themes,
            platforms=platforms,
            include_bots=include_bots,
            metric=metric,
            params=params,
        )
        with _connect(app) as con:
            rows = con.execute(
                f"""
                SELECT date_trunc(?, bucket_ts) AS bucket, platform,
                       {parts.aggregate} AS count
                FROM (
                    SELECT m.ts AS bucket_ts,
                           s.platform AS platform{parts.inner_select_suffix}
                    FROM messages m
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON c.source_id = s.id
                    WHERE {where}{parts.extra_where}
                )
                GROUP BY bucket, platform
                ORDER BY bucket, platform
                """,
                params,
            ).fetchall()
        buckets: dict[str, dict[str, int]] = {}
        seen: set[str] = set()
        for bucket, platform, count in rows:
            key = bucket.isoformat() if bucket else ""
            name = str(platform)
            buckets.setdefault(key, {})[name] = int(count or 0)
            seen.add(name)
        return {
            "granularity": granularity,
            "platforms": sorted(seen),
            "points": [
                {
                    "bucket": bucket,
                    "counts": {
                        platform: buckets[bucket].get(platform, 0)
                        for platform in sorted(seen)
                    },
                }
                for bucket in sorted(buckets)
            ],
        }

    def simple_timeline(
        *,
        bucket_select: str,
        group_by: str,
        start: date | None,
        end: date | None,
        people: str | None,
        themes: str | None,
        platforms: str | None,
        include_bots: bool,
        metric: str,
    ) -> list[tuple[Any, ...]]:
        params: list[Any] = []
        where, parts = _scope(
            app,
            start=start,
            end=end,
            people=people,
            themes=themes,
            platforms=platforms,
            include_bots=include_bots,
            metric=metric,
            params=params,
        )
        with _connect(app) as con:
            return con.execute(
                f"""
                SELECT {bucket_select}, {parts.aggregate} AS message_count
                FROM (
                    SELECT m.ts AS bucket_ts{parts.inner_select_suffix}
                    FROM messages m
                    JOIN channels c ON c.id = m.channel_id
                    JOIN sources s ON c.source_id = s.id
                    WHERE {where}{parts.extra_where}
                )
                GROUP BY {group_by}
                ORDER BY {group_by}
                """,
                params,
            ).fetchall()

    @app.get("/api/calendar")
    def calendar(
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
        rows = simple_timeline(
            bucket_select="CAST(bucket_ts AS DATE) AS day",
            group_by="day",
            start=start,
            end=end,
            people=people,
            themes=themes,
            platforms=platforms,
            include_bots=include_bots,
            metric=metric,
        )
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
        include_bots: bool = False,
        metric: str = Query(
            default="messages", pattern="^(messages|words|conversations)$"
        ),
    ) -> dict[str, Any]:
        rows = simple_timeline(
            bucket_select=(
                "EXTRACT(isodow FROM bucket_ts) AS weekday, "
                "EXTRACT(hour FROM bucket_ts) AS hour"
            ),
            group_by="weekday, hour",
            start=start,
            end=end,
            people=people,
            themes=themes,
            platforms=platforms,
            include_bots=include_bots,
            metric=metric,
        )
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

    def aggregate_endpoint(
        *,
        bucket_select: str,
        group_by: str,
        key: str,
        start: date | None,
        end: date | None,
        people: str | None,
        themes: str | None,
        platforms: str | None,
        include_bots: bool,
        metric: str,
    ) -> dict[str, Any]:
        rows = simple_timeline(
            bucket_select=bucket_select,
            group_by=group_by,
            start=start,
            end=end,
            people=people,
            themes=themes,
            platforms=platforms,
            include_bots=include_bots,
            metric=metric,
        )
        return {
            "points": [
                {
                    key: (
                        row[0].isoformat()
                        if hasattr(row[0], "isoformat")
                        else int(row[0])
                    ),
                    "message_count": int(row[1]),
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
        include_bots: bool = False,
        metric: str = Query(
            default="messages", pattern="^(messages|words|conversations)$"
        ),
    ) -> dict[str, Any]:
        return aggregate_endpoint(
            bucket_select="date_trunc('month', bucket_ts) AS month",
            group_by="month",
            key="month",
            start=start,
            end=end,
            people=people,
            themes=themes,
            platforms=platforms,
            include_bots=include_bots,
            metric=metric,
        )

    @app.get("/api/messages-by-hour")
    def messages_by_hour(
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
        return aggregate_endpoint(
            bucket_select="EXTRACT(hour FROM bucket_ts) AS hour",
            group_by="hour",
            key="hour",
            start=start,
            end=end,
            people=people,
            themes=themes,
            platforms=platforms,
            include_bots=include_bots,
            metric=metric,
        )
