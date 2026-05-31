from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import duckdb
from fastapi.testclient import TestClient

from gchat.api import create_app
from gchat.builder import build_database

from tests.test_ingest import ROOT, SIGNAL_EXPORT, _make_signal_subset


class ApiTests(unittest.TestCase):
    def test_read_endpoints(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir = tmp_path / "config"
            config_dir.mkdir()

            discord_sample = next((ROOT / "data" / "discord").glob("*.json"))
            from gchat.discord import normalize_export

            sample_export = normalize_export(discord_sample)
            sample_person = sample_export.people[0]

            (config_dir / "people.yaml").write_text(
                f"""people:\n  - name: Example Person\n    color: '#123456'\n    identities:\n      - platform: discord\n        id: '{sample_person.raw_id}'\n""",
                encoding="utf-8",
            )
            (config_dir / "themes.yaml").write_text(
                f"""themes:\n  - name: Example Theme\n    channels:\n      - source: '{sample_export.source.name}'\n        channel: '{sample_export.channel.name}'\n""",
                encoding="utf-8",
            )

            data_dir = tmp_path / "data"
            data_dir.mkdir()
            discord_dir = data_dir / "discord"
            discord_dir.mkdir()
            discord_file = next((ROOT / "data" / "discord").glob("*.json"))
            import shutil

            shutil.copy2(discord_file, discord_dir / discord_file.name)
            facebook_dir = data_dir / "facebook"
            facebook_dir.mkdir()
            facebook_chat = next((ROOT / "data" / "facebook").iterdir())
            shutil.copytree(facebook_chat, facebook_dir / facebook_chat.name)
            _make_signal_subset(SIGNAL_EXPORT, data_dir / "signal" / SIGNAL_EXPORT.name)

            db_path = tmp_path / "gchat.duckdb"
            build_database(data_dir, db_path, config_dir=config_dir)

            client = TestClient(create_app(db_path, data_dir=data_dir))
            overview = client.get("/api/overview").json()
            self.assertGreater(overview["total_messages"], 0)
            self.assertTrue(overview["people"])

            con = duckdb.connect(str(db_path))
            example_person_id = con.execute("SELECT id FROM people WHERE display_name = 'Example Person'").fetchone()[0]
            con.close()

            top_people = client.get(f"/api/top-people?limit=10&people={example_person_id}").json()
            self.assertGreater(len(top_people["items"]), 0)
            self.assertEqual(top_people["items"][0]["display_name"], "Example Person")

            time_series = client.get("/api/messages-over-time?granularity=day").json()
            self.assertTrue(time_series["points"])

            word_series = client.get("/api/messages-by-month?metric=words").json()
            self.assertTrue(word_series["points"])

            con = duckdb.connect(str(db_path))
            reacted_message_id = con.execute(
                """
                SELECT id
                FROM messages
                WHERE reaction_count > 0
                ORDER BY reaction_count DESC, ts DESC, id DESC
                LIMIT 1
                """
            ).fetchone()[0]
            con.execute(
                "UPDATE messages SET reaction_count = 999, reaction_summary = '😂×7 👍×2', content = '', attachment_count = 2, attachment_preview = 'https://cdn.example.com/demo.png' WHERE id = ?",
                [reacted_message_id],
            )
            con.close()

            reacted_messages = client.get("/api/top-reacted-messages?limit=1").json()
            self.assertEqual(reacted_messages["items"][0]["attachment_preview"], "https://cdn.example.com/demo.png")
            self.assertEqual(reacted_messages["items"][0]["attachment_url"], "https://cdn.example.com/demo.png")
            self.assertEqual(reacted_messages["items"][0]["content"], "https://cdn.example.com/demo.png")
            self.assertEqual(reacted_messages["items"][0]["reaction_summary"], "😂×7 👍×2")

            media_source_dir = data_dir / "facebook" / "media_test_source" / "photos"
            media_source_dir.mkdir(parents=True)
            expected_media = media_source_dir / "preview.jpg"
            expected_media.write_bytes(b"jpeg")

            media_resp = client.get(
                "/api/media",
                params={"platform": "facebook", "source": "media_test_source", "path": "photos/preview.jpg"},
            )
            self.assertEqual(media_resp.status_code, 200)
            self.assertEqual(media_resp.content, b"jpeg")

            con = duckdb.connect(str(db_path))
            channel_row = con.execute(
                """
                SELECT c.id, c.source_id, c.name, s.platform
                FROM channels c
                JOIN sources s ON s.id = c.source_id
                WHERE s.platform <> 'signal'
                ORDER BY c.id
                LIMIT 1
                """
            ).fetchone()
            second_channel_row = con.execute(
                """
                SELECT c.id, c.source_id, c.name
                FROM channels c
                JOIN sources s ON s.id = c.source_id
                WHERE s.platform <> 'signal'
                ORDER BY c.id
                LIMIT 1 OFFSET 1
                """
            ).fetchone()
            self.assertIsNotNone(channel_row)
            self.assertIsNotNone(second_channel_row)
            channel_id, source_id, current_name, platform = channel_row
            second_channel_id, second_source_id, second_current_name = second_channel_row
            renamed_name = f"{current_name} renamed"
            con.execute(
                "DELETE FROM channel_name_changes WHERE channel_id = ?",
                [channel_id],
            )
            con.execute(
                "DELETE FROM channel_name_changes WHERE channel_id = ?",
                [second_channel_id],
            )
            next_id = con.execute(
                "SELECT COALESCE(MAX(id), 0) + 1 FROM channel_name_changes"
            ).fetchone()[0]
            con.execute(
                "INSERT INTO channel_name_changes VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [next_id, channel_id, source_id, "channel_name", None, current_name, "2024-01-01 00:00:00", '{"actor_name":"Bootstrapper"}'],
            )
            con.execute(
                "INSERT INTO channel_name_changes VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [next_id + 1, channel_id, source_id, "channel_name", current_name, renamed_name, "2024-01-02 00:00:00", '{"actor_name":"Rename Tester"}'],
            )
            con.execute(
                "INSERT INTO channel_name_changes VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [next_id + 2, channel_id, source_id, "channel_name", f" {renamed_name} ", renamed_name.upper(), "2024-01-03 00:00:00", '{"actor_name":"Case Changer"}'],
            )
            con.execute(
                "INSERT INTO channel_name_changes VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [next_id + 3, second_channel_id, second_source_id, "channel_name", None, second_current_name, "2024-01-04 00:00:00", None],
            )
            con.close()

            history = client.get(f"/api/name-history?platforms={platform}").json()
            chat = next((item for item in history["chats"] if item["id"] == channel_id), None)
            self.assertIsNotNone(chat)
            self.assertEqual(len(chat["previous_names"]), 2)
            self.assertEqual(chat["previous_names"][0]["new_name"], current_name)
            self.assertIsNone(chat["previous_names"][0]["previous_name"])
            self.assertEqual(chat["previous_names"][0]["author_name"], "Bootstrapper")
            self.assertEqual(chat["previous_names"][1]["new_name"], renamed_name)
            self.assertEqual(chat["previous_names"][1]["previous_name"], current_name)
            self.assertEqual(chat["previous_names"][1]["author_name"], "Rename Tester")
            self.assertIsNone(next((item for item in history["chats"] if item["id"] == second_channel_id), None))


if __name__ == "__main__":
    unittest.main()
