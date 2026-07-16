"""Materialized content facts used by language, link, mention, and search APIs."""

from __future__ import annotations

import duckdb

FACT_SCHEMA_VERSION = "2"
FACT_TABLES = frozenset(
    {
        "message_tokens",
        "message_links",
        "message_mentions",
        "message_search_trigrams",
        "message_reaction_events",
        "build_metadata",
    }
)


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


def materialize_analytics_facts(con: duckdb.DuckDBPyConnection) -> None:
    """Populate all content-derived facts inside the current transaction."""
    for table in (
        "message_tokens",
        "message_links",
        "message_mentions",
        "message_search_trigrams",
        "message_reaction_events",
        "build_metadata",
    ):
        con.execute(f"DELETE FROM {table}")

    con.execute(
        """
        INSERT INTO message_tokens
        SELECT
            m.id,
            CAST(token_index AS INTEGER) - 1,
            token
        FROM messages m
        CROSS JOIN UNNEST(
            regexp_extract_all(
                replace(lower(coalesce(m.content, '')), chr(39), ''),
                '[a-z]{3,}'
            )
        ) WITH ORDINALITY AS extracted(token, token_index)
        ORDER BY token, m.id, token_index
        """
    )

    domain_expr = canonical_link_domain_expr("raw_host")
    con.execute(
        f"""
        INSERT INTO message_links
        WITH extracted AS (
            SELECT
                m.id AS message_id,
                CAST(link_index AS INTEGER) - 1 AS link_index,
                raw_url,
                regexp_extract(raw_url, 'https?://([^/?#\\s]+)', 1) AS raw_host
            FROM messages m
            CROSS JOIN UNNEST(
                regexp_extract_all(coalesce(m.content, ''), 'https?://[^\\s]+')
            ) WITH ORDINALITY AS links(raw_url, link_index)
        )
        SELECT message_id, link_index, raw_url, raw_host, {domain_expr} AS domain
        FROM extracted
        WHERE raw_host <> ''
        ORDER BY domain, message_id, link_index
        """
    )

    con.execute(
        """
        INSERT INTO message_mentions
        SELECT
            m.id,
            CAST(mention_index AS INTEGER) - 1,
            lower(mention)
        FROM messages m
        CROSS JOIN UNNEST(
            regexp_extract_all(coalesce(m.content, ''), '@([A-Za-z0-9_]+)', 1)
        ) WITH ORDINALITY AS mentions(mention, mention_index)
        WHERE mention <> ''
        ORDER BY mention, m.id, mention_index
        """
    )

    con.execute(
        """
        INSERT INTO message_search_trigrams
        SELECT DISTINCT
            m.id,
            substring(lower(m.content), positions.pos, 3) AS gram
        FROM messages m
        CROSS JOIN UNNEST(
            generate_series(1, greatest(length(lower(m.content)) - 2, 0))
        ) AS positions(pos)
        WHERE m.content IS NOT NULL
          AND length(m.content) >= 3
        ORDER BY gram, m.id
        """
    )

    metadata = {
        "fact_schema_version": FACT_SCHEMA_VERSION,
        "tokenizer_version": "ascii-apostrophe-v1",
        "link_extractor_version": "http-host-v1",
        "mention_extractor_version": "ascii-handle-v1",
        "search_index_version": "lower-trigram-v1",
    }
    con.executemany(
        "INSERT INTO build_metadata VALUES (?, ?)",
        list(metadata.items()),
    )

    for name, table, columns in (
        ("message_tokens_token_idx", "message_tokens", "token, message_id"),
        ("message_links_domain_idx", "message_links", "domain, message_id"),
        ("message_mentions_mention_idx", "message_mentions", "mention, message_id"),
        (
            "message_search_trigrams_gram_idx",
            "message_search_trigrams",
            "gram, message_id",
        ),
    ):
        con.execute(f"DROP INDEX IF EXISTS {name}")
        con.execute(f"CREATE INDEX {name} ON {table} ({columns})")


def has_analytics_facts(con: duckdb.DuckDBPyConnection) -> bool:
    rows = con.execute(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_name IN (
            'message_tokens',
            'message_links',
            'message_mentions',
            'message_search_trigrams',
            'message_reaction_events',
            'build_metadata'
        )
        """
    ).fetchall()
    if {str(row[0]) for row in rows} != FACT_TABLES:
        return False
    version = con.execute(
        "SELECT value FROM build_metadata WHERE key = 'fact_schema_version'"
    ).fetchone()
    return bool(version and str(version[0]) == FACT_SCHEMA_VERSION)
