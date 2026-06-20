"""Compute and store per-person message diversity statistics."""

from __future__ import annotations

from typing import Any

import duckdb

from .stop_words import COMMON_STOP_WORDS

PersonStatsRow = tuple[
    int,
    int,
    int,
    int,
    float,
    float,
    int,
    int,
    int,
    float,
]


def _stop_word_placeholders(stop_words: frozenset[str]) -> tuple[str, list[str]]:
    words = sorted(stop_words)
    return ", ".join("?" for _ in words), words


def person_stats_sql(
    *,
    where: str,
    has_is_system: bool,
    excluded_filter: str,
    stop_words: frozenset[str] = COMMON_STOP_WORDS,
) -> tuple[str, list[str]]:
    """Return SQL and stop-word bind params for person diversity stats."""
    is_system_filter = "AND NOT m.is_system" if has_is_system else ""
    stop_placeholders, stop_params = _stop_word_placeholders(stop_words)
    sql = f"""
        WITH scoped_messages AS (
            SELECT
                m.person_id,
                m.channel_id,
                c.theme_id,
                s.platform,
                m.content
            FROM messages m
            JOIN channels c ON c.id = m.channel_id
            JOIN sources s ON s.id = c.source_id
            WHERE {where}
              {is_system_filter}
              {excluded_filter}
        ),
        message_counts AS (
            SELECT
                person_id,
                COUNT(*) AS message_count,
                COUNT(DISTINCT channel_id) AS channel_count,
                COUNT(DISTINCT theme_id) AS theme_count,
                COUNT(DISTINCT platform) AS platform_count
            FROM scoped_messages
            GROUP BY person_id
        ),
        text_messages AS (
            SELECT person_id, channel_id, content
            FROM scoped_messages
            WHERE content IS NOT NULL AND content <> ''
        ),
        tokens AS (
            SELECT
                person_id,
                unnest(
                    regexp_extract_all(
                        replace(lower(coalesce(content, '')), chr(39), ''),
                        '[a-z]{{3,}}'
                    )
                ) AS word
            FROM text_messages
        ),
        filtered_tokens AS (
            SELECT person_id, word
            FROM tokens
            WHERE word NOT IN ({stop_placeholders})
        ),
        word_freq AS (
            SELECT person_id, word, COUNT(*) AS cnt
            FROM filtered_tokens
            GROUP BY person_id, word
        ),
        word_totals AS (
            SELECT
                person_id,
                COUNT(*) AS total_words,
                COUNT(DISTINCT word) AS unique_words
            FROM filtered_tokens
            GROUP BY person_id
        ),
        word_entropy AS (
            SELECT
                wf.person_id,
                -SUM(
                    (wf.cnt * 1.0 / wt.total_words)
                    * log2(wf.cnt * 1.0 / wt.total_words)
                ) AS word_entropy
            FROM word_freq wf
            JOIN word_totals wt ON wt.person_id = wf.person_id
            WHERE wt.total_words > 0
            GROUP BY wf.person_id
        ),
        channel_shares AS (
            SELECT
                person_id,
                channel_id,
                COUNT(*) AS cnt,
                COUNT(*) * 1.0
                    / SUM(COUNT(*)) OVER (PARTITION BY person_id) AS share
            FROM scoped_messages
            GROUP BY person_id, channel_id
        ),
        channel_hhi AS (
            SELECT person_id, SUM(share * share) AS channel_hhi
            FROM channel_shares
            GROUP BY person_id
        )
        SELECT
            mc.person_id,
            mc.message_count,
            COALESCE(wt.unique_words, 0) AS unique_words,
            COALESCE(wt.total_words, 0) AS total_words,
            CASE
                WHEN COALESCE(wt.total_words, 0) > 0
                THEN COALESCE(wt.unique_words, 0) * 1.0 / wt.total_words
                ELSE 0.0
            END AS ttr,
            COALESCE(we.word_entropy, 0.0) AS word_entropy,
            mc.channel_count,
            mc.theme_count,
            mc.platform_count,
            COALESCE(ch.channel_hhi, 0.0) AS channel_hhi
        FROM message_counts mc
        LEFT JOIN word_totals wt ON wt.person_id = mc.person_id
        LEFT JOIN word_entropy we ON we.person_id = mc.person_id
        LEFT JOIN channel_hhi ch ON ch.person_id = mc.person_id
        ORDER BY mc.message_count DESC, mc.person_id
    """
    return sql, stop_params


def compute_person_stats(
    con: duckdb.DuckDBPyConnection,
    *,
    where: str = "1 = 1",
    params: list[Any] | None = None,
    has_is_system: bool = True,
    excluded_filter: str = "",
) -> list[PersonStatsRow]:
    sql, stop_params = person_stats_sql(
        where=where,
        has_is_system=has_is_system,
        excluded_filter=excluded_filter,
    )
    bind = list(params or []) + stop_params
    rows = con.execute(sql, bind).fetchall()
    return [
        (
            int(row[0]),
            int(row[1]),
            int(row[2]),
            int(row[3]),
            float(row[4]),
            float(row[5]),
            int(row[6]),
            int(row[7]),
            int(row[8]),
            float(row[9]),
        )
        for row in rows
    ]


def refresh_person_stats(
    con: duckdb.DuckDBPyConnection,
    *,
    has_is_system: bool = True,
) -> int:
    """Recompute and persist person_stats for the full database."""
    con.execute("DELETE FROM person_stats")
    rows = compute_person_stats(con, has_is_system=has_is_system)
    if rows:
        con.executemany(
            """
            INSERT INTO person_stats (
                person_id,
                message_count,
                unique_words,
                total_words,
                ttr,
                word_entropy,
                channel_count,
                theme_count,
                platform_count,
                channel_hhi
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)


def person_stats_row_to_dict(
    row: tuple[Any, ...],
    *,
    display_name: str,
    color: str,
    avatar: str = "",
) -> dict[str, Any]:
    return {
        "id": int(row[0]),
        "display_name": display_name,
        "color": color,
        "avatar": avatar,
        "message_count": int(row[1]),
        "unique_words": int(row[2]),
        "total_words": int(row[3]),
        "ttr": round(float(row[4]), 6),
        "word_entropy": round(float(row[5]), 4),
        "channel_count": int(row[6]),
        "theme_count": int(row[7]),
        "platform_count": int(row[8]),
        "channel_hhi": round(float(row[9]), 4),
    }
