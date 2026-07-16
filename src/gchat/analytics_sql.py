"""Shared SQL fragments for message-scoped analytics routes."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MetricSql:
    aggregate: str
    inner_select_suffix: str
    extra_where: str


def canonical_link_domain_expr(column: str) -> str:
    return f"""CASE
        WHEN lower({column}) IN (
            'youtu.be', 'youtube.com', 'www.youtube.com', 'm.youtube.com'
        ) THEN 'youtube.com'
        WHEN lower({column}) IN (
            'soundcloud.com', 'www.soundcloud.com', 'm.soundcloud.com',
            'on.soundcloud.com', 'snd.sc', 'api.soundcloud.com'
        ) THEN 'soundcloud.com'
        ELSE lower({column})
    END"""


def excluded_ids_sql(excluded_ids: frozenset[str]) -> str:
    if not excluded_ids:
        return ""
    ids_literal = ", ".join(
        f"'{message_id.replace(chr(39), chr(39) * 2)}'"
        for message_id in sorted(excluded_ids)
    )
    return f" AND m.id NOT IN ({ids_literal})"


def word_count_expr(has_word_count: bool = True) -> str:
    if has_word_count:
        return "COALESCE(m.word_count, 0)"
    return (
        "COALESCE(array_length(regexp_extract_all("
        "replace(lower(coalesce(m.content, '')), chr(39), ''), "
        "'[a-z]{3,}')), 0)"
    )


def tokenized_message_source(
    *,
    selected_columns: str,
    where: str,
    has_facts: bool,
    extra_joins: str = "",
    extra_where: str = "",
    token_alias: str = "token",
) -> str:
    prefix = f"{selected_columns}, " if selected_columns else ""
    if has_facts:
        return f"""
            SELECT {prefix}mt.token AS {token_alias}
            FROM message_tokens mt
            JOIN messages m ON m.id = mt.message_id
            {extra_joins}
            JOIN channels c ON c.id = m.channel_id
            JOIN sources s ON c.source_id = s.id
            WHERE {where}{extra_where}
        """
    return f"""
        SELECT
            {prefix}
            unnest(
                regexp_extract_all(
                    replace(lower(coalesce(m.content, '')), chr(39), ''),
                    '[a-z]{{3,}}'
                )
            ) AS {token_alias}
        FROM messages m
        {extra_joins}
        JOIN channels c ON c.id = m.channel_id
        JOIN sources s ON c.source_id = s.id
        WHERE {where}{extra_where}
          AND m.content IS NOT NULL AND m.content <> ''
    """


def metric_sql(
    metric: str,
    has_is_system: bool = False,
    has_word_count: bool = True,
    excluded_ids: frozenset[str] | None = None,
) -> MetricSql:
    is_system_filter = " AND NOT m.is_system" if has_is_system else ""
    excluded_filter = excluded_ids_sql(excluded_ids or frozenset())
    if metric == "words":
        return MetricSql(
            aggregate="SUM(word_count)",
            inner_select_suffix=(
                f", m.conversation_id, {word_count_expr(has_word_count)} AS word_count"
            ),
            extra_where=f"{is_system_filter}{excluded_filter}",
        )
    if metric == "conversations":
        return MetricSql(
            aggregate="COUNT(DISTINCT conversation_id)",
            inner_select_suffix=", m.conversation_id",
            extra_where=(
                f" AND m.conversation_id IS NOT NULL{is_system_filter}{excluded_filter}"
            ),
        )
    return MetricSql(
        aggregate="COUNT(*)",
        inner_select_suffix=", m.conversation_id",
        extra_where=f"{is_system_filter}{excluded_filter}",
    )
