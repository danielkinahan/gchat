from __future__ import annotations

import unittest
from datetime import datetime

import duckdb

from gchat.analytics_facts import has_analytics_facts, materialize_analytics_facts
from gchat.models import MessageSeed, PersonSeed
from gchat.person_stats import compute_exclusive_words, compute_person_stats
from gchat.reaction_facts import materialize_reaction_facts
from gchat.schema import SCHEMA_SQL


class AnalyticsFactsTests(unittest.TestCase):
    def test_person_stats_match_legacy_tokenization(self) -> None:
        con = duckdb.connect(":memory:")
        con.execute(SCHEMA_SQL)
        con.execute("INSERT INTO sources VALUES (1, 'signal', 'Signal: demo')")
        con.execute("INSERT INTO themes VALUES (1, 'Demo')")
        con.execute("INSERT INTO channels VALUES (1, 1, 'chat', 'Demo', 1)")
        con.execute(
            "INSERT INTO people VALUES (1, 'Alice', '#111'), (2, 'Bob', '#222')"
        )
        con.execute(
            """
            INSERT INTO messages (
                id, channel_id, person_id, ts, content
            ) VALUES
                ('m1', 1, 1, TIMESTAMP '2026-01-01', 'alpha shared alpha'),
                ('m2', 1, 2, TIMESTAMP '2026-01-02', 'beta shared beta')
            """
        )

        legacy_stats = compute_person_stats(con, has_message_tokens=False)
        legacy_exclusive = compute_exclusive_words(
            con,
            1,
            has_message_tokens=False,
        )
        materialize_analytics_facts(con)

        self.assertEqual(
            compute_person_stats(con, has_message_tokens=True),
            legacy_stats,
        )
        self.assertEqual(
            compute_exclusive_words(con, 1, has_message_tokens=True),
            legacy_exclusive,
        )
        con.close()

    def test_materializes_versioned_content_facts_idempotently(self) -> None:
        con = duckdb.connect(":memory:")
        con.execute(SCHEMA_SQL)
        con.execute(
            """
            INSERT INTO messages (
                id, channel_id, person_id, ts, content
            ) VALUES (
                'm1', 1, 1, TIMESTAMP '2026-01-01',
                'Hello hello@example and @Daniel: https://youtu.be/demo'
            )
            """
        )

        materialize_analytics_facts(con)
        materialize_analytics_facts(con)

        self.assertTrue(has_analytics_facts(con))
        self.assertEqual(
            con.execute(
                "SELECT token FROM message_tokens ORDER BY token_index"
            ).fetchall(),
            [
                ("hello",),
                ("hello",),
                ("example",),
                ("and",),
                ("daniel",),
                ("https",),
                ("youtu",),
                ("demo",),
            ],
        )
        self.assertEqual(
            con.execute("SELECT raw_host, domain FROM message_links").fetchall(),
            [("youtu.be", "youtube.com")],
        )
        self.assertEqual(
            con.execute(
                "SELECT mention FROM message_mentions ORDER BY mention_index"
            ).fetchall(),
            [("example",), ("daniel",)],
        )
        self.assertGreater(
            con.execute(
                """
                SELECT COUNT(*)
                FROM message_search_trigrams
                WHERE gram = 'hel'
                """
            ).fetchone()[0],
            0,
        )
        con.close()

    def test_reaction_facts_capture_supported_identity_coverage(self) -> None:
        con = duckdb.connect(":memory:")
        con.execute(SCHEMA_SQL)
        materialize_analytics_facts(con)
        message = MessageSeed(
            id="m1",
            source_name="Signal: demo",
            channel_raw_id="chat",
            channel_name="Demo",
            theme_name="Demo",
            person=PersonSeed("signal", "self", "You"),
            ts=datetime(2026, 1, 1),
            content="hello",
            reaction_count=2,
            reaction_summary="❤️×2",
            reaction_details_json=(
                '[{"name":"❤️","count":2,"reactors":['
                '{"platform":"signal","raw_id":"name:alice",'
                '"display_name":"Alice"}]}]'
            ),
        )

        count = materialize_reaction_facts(
            con,
            [message],
            {("signal", "name:alice"): 7},
        )

        self.assertEqual(count, 2)
        self.assertEqual(
            con.execute(
                """
                SELECT COUNT(*), COUNT(reactor_raw_id), COUNT(reactor_person_id)
                FROM message_reaction_events
                """
            ).fetchone(),
            (2, 1, 1),
        )
        con.close()


if __name__ == "__main__":
    unittest.main()
