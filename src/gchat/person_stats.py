"""Compute and store per-person message diversity statistics."""

from __future__ import annotations

import re
from typing import Any

import duckdb

from .analytics_facts import has_analytics_facts
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
    int,
    float,
]

_TOKEN_PATTERN = re.compile(r"[a-z]{3,}")
_MTLD_THRESHOLD = 0.72


def _stop_word_placeholders(stop_words: frozenset[str]) -> tuple[str, list[str]]:
    words = sorted(stop_words)
    return ", ".join("?" for _ in words), words


def tokenize_content(
    content: str,
    *,
    stop_words: frozenset[str] = COMMON_STOP_WORDS,
) -> list[str]:
    """Tokenize message content using the same rules as diversity SQL."""
    text = content.lower().replace("'", "")
    return [word for word in _TOKEN_PATTERN.findall(text) if word not in stop_words]


def _mtld_direction(tokens: list[str], threshold: float = _MTLD_THRESHOLD) -> float:
    if not tokens:
        return 0.0
    if len(tokens) == 1:
        return 1.0

    types: set[str] = set()
    token_count = 0
    factor_count = 0.0
    ttr = 1.0

    for word in tokens:
        types.add(word)
        token_count += 1
        ttr = len(types) / token_count
        if ttr <= threshold:
            factor_count += 1.0
            types = set()
            token_count = 0

    if token_count > 0:
        factor_count += (1.0 - ttr) / (1.0 - threshold)

    if factor_count == 0.0:
        return float(len(tokens))
    return len(tokens) / factor_count


def compute_mtld(
    tokens: list[str],
    *,
    threshold: float = _MTLD_THRESHOLD,
) -> float:
    """Measure of Textual Lexical Diversity (mean of forward and reverse passes)."""
    if not tokens:
        return 0.0
    forward = _mtld_direction(tokens, threshold)
    backward = _mtld_direction(list(reversed(tokens)), threshold)
    return (forward + backward) / 2.0


def _fetch_person_tokens(
    con: duckdb.DuckDBPyConnection,
    *,
    where: str,
    params: list[Any],
    has_is_system: bool,
    excluded_filter: str,
    has_message_tokens: bool,
    stop_words: frozenset[str] = COMMON_STOP_WORDS,
) -> dict[int, list[str]]:
    is_system_filter = "AND NOT m.is_system" if has_is_system else ""
    if has_message_tokens:
        rows = con.execute(
            f"""
            SELECT m.person_id, mt.token
            FROM messages m
            JOIN message_tokens mt ON mt.message_id = m.id
            JOIN channels c ON c.id = m.channel_id
            JOIN sources s ON s.id = c.source_id
            WHERE {where}
              {is_system_filter}
              {excluded_filter}
            ORDER BY m.person_id, m.ts, m.id, mt.token_index
            """,
            params,
        ).fetchall()
        tokens_by_person: dict[int, list[str]] = {}
        for person_id, token in rows:
            normalized = str(token)
            if normalized not in stop_words:
                tokens_by_person.setdefault(int(person_id), []).append(normalized)
        return tokens_by_person
    rows = con.execute(
        f"""
        SELECT m.person_id, m.content
        FROM messages m
        JOIN channels c ON c.id = m.channel_id
        JOIN sources s ON s.id = c.source_id
        WHERE {where}
          {is_system_filter}
          {excluded_filter}
          AND m.content IS NOT NULL
          AND m.content <> ''
        ORDER BY m.person_id, m.ts, m.id
        """,
        params,
    ).fetchall()
    tokens_by_person: dict[int, list[str]] = {}
    for person_id, content in rows:
        pid = int(person_id)
        tokens_by_person.setdefault(pid, []).extend(
            tokenize_content(str(content), stop_words=stop_words)
        )
    return tokens_by_person


def person_stats_sql(
    *,
    where: str,
    has_is_system: bool,
    excluded_filter: str,
    has_message_tokens: bool = False,
    stop_words: frozenset[str] = COMMON_STOP_WORDS,
) -> tuple[str, list[str]]:
    """Return SQL and stop-word bind params for person diversity stats."""
    is_system_filter = "AND NOT m.is_system" if has_is_system else ""
    stop_placeholders, stop_params = _stop_word_placeholders(stop_words)
    token_source = (
        """
            SELECT sm.person_id, mt.token AS word
            FROM scoped_messages sm
            JOIN message_tokens mt ON mt.message_id = sm.message_id
        """
        if has_message_tokens
        else """
            SELECT
                person_id,
                unnest(
                    regexp_extract_all(
                        replace(lower(coalesce(content, '')), chr(39), ''),
                        '[a-z]{3,}'
                    )
                ) AS word
            FROM text_messages
        """
    )
    sql = f"""
        WITH scoped_messages AS (
            SELECT
                m.id AS message_id,
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
            {token_source}
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
        ),
        word_by_person AS (
            SELECT DISTINCT person_id, word
            FROM filtered_tokens
        ),
        exclusive_by_person AS (
            SELECT wbp.person_id, COUNT(*) AS exclusive_word_count
            FROM word_by_person wbp
            WHERE NOT EXISTS (
                SELECT 1
                FROM word_by_person other
                WHERE other.word = wbp.word
                  AND other.person_id <> wbp.person_id
            )
            GROUP BY wbp.person_id
        )
        SELECT
            mc.person_id,
            mc.message_count,
            COALESCE(wt.unique_words, 0) AS unique_words,
            COALESCE(wt.total_words, 0) AS total_words,
            COALESCE(we.word_entropy, 0.0) AS word_entropy,
            COALESCE(ebp.exclusive_word_count, 0) AS exclusive_word_count,
            mc.channel_count,
            mc.theme_count,
            mc.platform_count,
            COALESCE(ch.channel_hhi, 0.0) AS channel_hhi
        FROM message_counts mc
        LEFT JOIN word_totals wt ON wt.person_id = mc.person_id
        LEFT JOIN word_entropy we ON we.person_id = mc.person_id
        LEFT JOIN exclusive_by_person ebp ON ebp.person_id = mc.person_id
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
    has_message_tokens: bool | None = None,
) -> list[PersonStatsRow]:
    if has_message_tokens is None:
        has_message_tokens = has_analytics_facts(con)
    sql, stop_params = person_stats_sql(
        where=where,
        has_is_system=has_is_system,
        excluded_filter=excluded_filter,
        has_message_tokens=has_message_tokens,
    )
    bind = list(params or [])
    tokens_by_person = _fetch_person_tokens(
        con,
        where=where,
        params=bind,
        has_is_system=has_is_system,
        excluded_filter=excluded_filter,
        has_message_tokens=has_message_tokens,
    )
    bind.extend(stop_params)
    rows = con.execute(sql, bind).fetchall()
    return [
        (
            int(row[0]),
            int(row[1]),
            int(row[2]),
            int(row[3]),
            compute_mtld(tokens_by_person.get(int(row[0]), [])),
            float(row[4]),
            int(row[5]),
            int(row[6]),
            int(row[7]),
            int(row[8]),
            float(row[9]),
        )
        for row in rows
    ]


def exclusive_words_sql(
    *,
    where: str,
    has_is_system: bool,
    excluded_filter: str,
    has_message_tokens: bool = False,
    stop_words: frozenset[str] = COMMON_STOP_WORDS,
) -> tuple[str, list[str]]:
    is_system_filter = "AND NOT m.is_system" if has_is_system else ""
    stop_placeholders, stop_params = _stop_word_placeholders(stop_words)
    token_source = (
        """
            SELECT sm.person_id, mt.token AS word
            FROM scoped_messages sm
            JOIN message_tokens mt ON mt.message_id = sm.message_id
        """
        if has_message_tokens
        else """
            SELECT
                person_id,
                unnest(
                    regexp_extract_all(
                        replace(lower(coalesce(content, '')), chr(39), ''),
                        '[a-z]{3,}'
                    )
                ) AS word
            FROM scoped_messages
        """
    )
    sql = f"""
        WITH scoped_messages AS (
            SELECT m.id AS message_id, m.person_id, m.content
            FROM messages m
            JOIN channels c ON c.id = m.channel_id
            JOIN sources s ON s.id = c.source_id
            WHERE {where}
              {is_system_filter}
              {excluded_filter}
              AND m.content IS NOT NULL
              AND m.content <> ''
        ),
        tokens AS (
            {token_source}
        ),
        filtered_tokens AS (
            SELECT person_id, word
            FROM tokens
            WHERE word NOT IN ({stop_placeholders})
        )
        SELECT DISTINCT ft.word
        FROM filtered_tokens ft
        WHERE ft.person_id = ?
          AND NOT EXISTS (
            SELECT 1
            FROM filtered_tokens other
            WHERE other.word = ft.word
              AND other.person_id <> ft.person_id
          )
        ORDER BY ft.word
        LIMIT ?
    """
    return sql, stop_params


def compute_exclusive_words(
    con: duckdb.DuckDBPyConnection,
    person_id: int,
    *,
    where: str = "1 = 1",
    params: list[Any] | None = None,
    has_is_system: bool = True,
    excluded_filter: str = "",
    limit: int = 500,
    has_message_tokens: bool | None = None,
) -> list[str]:
    if has_message_tokens is None:
        has_message_tokens = has_analytics_facts(con)
    sql, stop_params = exclusive_words_sql(
        where=where,
        has_is_system=has_is_system,
        excluded_filter=excluded_filter,
        has_message_tokens=has_message_tokens,
    )
    bind = list(params or []) + stop_params + [person_id, limit]
    rows = con.execute(sql, bind).fetchall()
    return [str(row[0]) for row in rows]


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
                mtld,
                word_entropy,
                exclusive_word_count,
                channel_count,
                theme_count,
                platform_count,
                channel_hhi
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        "mtld": round(float(row[4]), 2),
        "word_entropy": round(float(row[5]), 4),
        "exclusive_word_count": int(row[6]),
        "channel_count": int(row[7]),
        "theme_count": int(row[8]),
        "platform_count": int(row[9]),
        "channel_hhi": round(float(row[10]), 4),
    }
