from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import duckdb
from fastapi.testclient import TestClient

from gchat.api import (
    _build_signal_filename_index,
    _resolve_local_attachment_url,
    create_app,
)
from gchat.builder import build_database
from tests.test_ingest import ROOT, _write_discord_html_export


class ApiTests(unittest.TestCase):
    def test_read_endpoints(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir = tmp_path / "config"
            config_dir.mkdir()

            discord_sample = _write_discord_html_export(tmp_path / "discord-sample")
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
            import shutil

            _write_discord_html_export(discord_dir)
            facebook_dir = data_dir / "facebook"
            facebook_dir.mkdir()
            facebook_chat = next((ROOT / "data" / "facebook").iterdir())
            shutil.copytree(facebook_chat, facebook_dir / facebook_chat.name)
            db_path = tmp_path / "gchat.duckdb"
            build_database(data_dir, db_path, config_dir=config_dir)

            client = TestClient(create_app(db_path, data_dir=data_dir))
            overview = client.get("/api/overview").json()
            self.assertGreater(overview["total_messages"], 0)
            self.assertTrue(overview["people"])
            self.assertIn("message_stats", overview)
            self.assertIn("with_text", overview["message_stats"])
            self.assertIn("most_active_hour", overview["message_stats"])

            con = duckdb.connect(str(db_path))
            example_person_row = con.execute(
                "SELECT id FROM people WHERE display_name = 'Example Person'"
            ).fetchone()
            self.assertIsNotNone(example_person_row)
            assert example_person_row is not None
            example_person_id = example_person_row[0]
            con.close()

            top_people = client.get(
                f"/api/top-people?limit=10&people={example_person_id}"
            ).json()
            self.assertGreater(len(top_people["items"]), 0)
            self.assertEqual(top_people["items"][0]["display_name"], "Example Person")

            time_series = client.get("/api/messages-over-time?granularity=day").json()
            self.assertTrue(time_series["points"])

            word_series = client.get("/api/messages-by-month?metric=words").json()
            self.assertTrue(word_series["points"])

            conv_series = client.get(
                "/api/messages-by-month?metric=conversations"
            ).json()
            self.assertTrue(conv_series["points"])

            con = duckdb.connect(str(db_path))
            reacted_message_row = con.execute(
                """
                SELECT id
                FROM messages
                WHERE reaction_count > 0
                  AND id IN (
                      SELECT m.id
                      FROM messages m
                      JOIN channels c ON c.id = m.channel_id
                      JOIN sources s ON s.id = c.source_id
                      WHERE s.platform = 'discord'
                  )
                ORDER BY reaction_count DESC, ts DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
            self.assertIsNotNone(reacted_message_row)
            assert reacted_message_row is not None
            reacted_message_id = reacted_message_row[0]
            con.execute(
                """
                UPDATE messages
                SET reaction_count = 999,
                    reaction_summary = '😂×7 👍×2',
                    reaction_details_json = '[{"name":"dickbowtie","count":6,"emoji_id":"797916117854322738","image_url":"assets/797916117854322738-8a4166b64a0bcff2.png","code":"dickbowtie","is_animated":false},{"name":"😂","count":7,"emoji_id":null,"image_url":"assets/1f602.svg","code":"joy","is_animated":false}]',
                    content = '',
                    attachment_count = 2,
                    attachment_preview = 'https://cdn.example.com/demo.png'
                WHERE id = ?
                """,
                [reacted_message_id],
            )
            con.close()

            reacted_messages = client.get("/api/top-reacted-messages?limit=1").json()
            self.assertEqual(
                reacted_messages["items"][0]["attachment_preview"],
                "https://cdn.example.com/demo.png",
            )
            self.assertEqual(
                reacted_messages["items"][0]["attachment_url"],
                "https://cdn.example.com/demo.png",
            )
            self.assertEqual(reacted_messages["items"][0]["content"], "")
            self.assertEqual(
                reacted_messages["items"][0]["reaction_summary"], "😂×7 👍×2"
            )
            self.assertTrue(reacted_messages["items"][0]["reaction_details"])
            self.assertTrue(
                reacted_messages["items"][0]["reaction_details"][0][
                    "image_url"
                ].startswith("assets/")
            )
            self.assertEqual(
                reacted_messages["items"][0]["reaction_details"][1]["name"], "😂"
            )
            self.assertIsNone(
                reacted_messages["items"][0]["reaction_details"][1]["image_url"]
            )

            media_source_dir = data_dir / "facebook" / "media_test_source" / "photos"
            media_source_dir.mkdir(parents=True)
            expected_media = media_source_dir / "preview.jpg"
            expected_media.write_bytes(b"jpeg")

            media_resp = client.get(
                "/api/media",
                params={
                    "platform": "facebook",
                    "source": "media_test_source",
                    "path": "photos/preview.jpg",
                },
            )
            self.assertEqual(media_resp.status_code, 200)
            self.assertEqual(media_resp.content, b"jpeg")

            con = duckdb.connect(str(db_path))
            channel_row = con.execute(
                """
                SELECT c.id, c.source_id, c.name, c.platform_channel_id, s.platform
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
            assert channel_row is not None
            assert second_channel_row is not None
            channel_id, source_id, current_name, platform_channel_id, platform = (
                channel_row
            )
            second_channel_id, second_source_id, second_current_name = (
                second_channel_row
            )
            actor_row = con.execute(
                """
                SELECT pi.platform_user_id, p.id, p.display_name
                FROM messages m
                JOIN channels c ON c.id = m.channel_id
                JOIN sources s ON s.id = c.source_id
                JOIN people p ON p.id = m.person_id
                JOIN platform_identities pi ON pi.person_id = p.id AND pi.platform = s.platform
                WHERE c.id = ?
                LIMIT 1
                """,
                [channel_id],
            ).fetchone()
            self.assertIsNotNone(actor_row)
            assert actor_row is not None
            actor_raw_id, actor_person_id, actor_real_name = actor_row
            renamed_name = f"{current_name} renamed"
            con.execute(
                "DELETE FROM channel_name_changes WHERE channel_id = ?",
                [channel_id],
            )
            con.execute(
                "DELETE FROM channel_name_changes WHERE channel_id = ?",
                [second_channel_id],
            )
            next_id_row = con.execute(
                "SELECT COALESCE(MAX(id), 0) + 1 FROM channel_name_changes"
            ).fetchone()
            self.assertIsNotNone(next_id_row)
            assert next_id_row is not None
            next_id = next_id_row[0]
            con.execute(
                "INSERT INTO channel_name_changes VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    next_id,
                    channel_id,
                    source_id,
                    "channel_name",
                    None,
                    current_name,
                    "2024-01-01 00:00:00",
                    '{"actor_name":"Bootstrapper"}',
                ],
            )
            con.execute(
                "INSERT INTO channel_name_changes VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    next_id + 1,
                    channel_id,
                    source_id,
                    "channel_name",
                    current_name,
                    renamed_name,
                    "2023-12-31 00:00:00",
                    f'{{"actor_name":"Crystal cowboy","actor_raw_id":"{actor_raw_id}"}}',
                ],
            )
            con.execute(
                "INSERT INTO channel_name_changes VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    next_id + 2,
                    channel_id,
                    source_id,
                    "channel_name",
                    f" {renamed_name} ",
                    renamed_name.upper(),
                    "2024-01-03 00:00:00",
                    '{"actor_name":"Case Changer"}',
                ],
            )
            con.execute(
                "INSERT INTO channel_name_changes VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    next_id + 3,
                    second_channel_id,
                    second_source_id,
                    "channel_name",
                    None,
                    second_current_name,
                    "2024-01-04 00:00:00",
                    None,
                ],
            )
            next_person_change_row = con.execute(
                "SELECT COALESCE(MAX(id), 0) + 1 FROM person_name_changes"
            ).fetchone()
            self.assertIsNotNone(next_person_change_row)
            assert next_person_change_row is not None
            next_person_change_id = next_person_change_row[0]
            person_row = con.execute(
                "SELECT id FROM people WHERE id <> ? ORDER BY id LIMIT 1",
                [actor_person_id],
            ).fetchone()
            self.assertIsNotNone(person_row)
            assert person_row is not None
            person_id = person_row[0]
            con.execute(
                "INSERT INTO person_name_changes VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    next_person_change_id,
                    actor_person_id,
                    source_id,
                    "nickname",
                    "Old Actor Nick",
                    "Horton",
                    "2024-01-01 12:00:00",
                    f'{{"chatId":"{platform_channel_id}","actor_name":"You","actor_raw_id":"{actor_raw_id}"}}',
                ],
            )
            con.execute(
                "INSERT INTO person_name_changes VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    next_person_change_id + 1,
                    person_id,
                    source_id,
                    "nickname",
                    "Old Nick",
                    "New Nick",
                    "2024-01-05 00:00:00",
                    f'{{"chatId":"{platform_channel_id}","actor_name":"cokehead","actor_raw_id":"{actor_raw_id}"}}',
                ],
            )
            con.close()

            history = client.get(f"/api/name-history?platforms={platform}").json()
            chat = next(
                (item for item in history["chats"] if item["id"] == channel_id), None
            )
            self.assertIsNotNone(chat)
            assert chat is not None
            self.assertEqual(len(chat["previous_names"]), 2)
            bootstrap_entry = next(
                (
                    entry
                    for entry in chat["previous_names"]
                    if entry["new_name"] == current_name
                ),
                None,
            )
            rename_entry = next(
                (
                    entry
                    for entry in chat["previous_names"]
                    if entry["new_name"] == renamed_name
                ),
                None,
            )
            self.assertIsNotNone(bootstrap_entry)
            self.assertIsNotNone(rename_entry)
            assert bootstrap_entry is not None
            assert rename_entry is not None
            self.assertIsNone(bootstrap_entry["previous_name"])
            self.assertEqual(bootstrap_entry["author_name"], "Bootstrapper")
            self.assertEqual(rename_entry["previous_name"], current_name)
            self.assertEqual(
                rename_entry["author_name"],
                actor_real_name,
            )
            participant = next(
                (item for item in chat["participants"] if item["id"] == person_id), None
            )
            self.assertIsNotNone(participant)
            assert participant is not None
            self.assertEqual(participant["history"][0]["new_name"], "New Nick")
            self.assertEqual(
                participant["history"][0]["author_name"],
                f"Horton ({actor_real_name})",
            )
            self.assertIsNone(
                next(
                    (
                        item
                        for item in history["chats"]
                        if item["id"] == second_channel_id
                    ),
                    None,
                )
            )

    def test_local_attachment_resolution_helpers(self) -> None:
        with TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            fb_source = data_dir / "facebook" / "sample_chat" / "photos"
            fb_source.mkdir(parents=True)
            (fb_source / "preview.jpg").write_bytes(b"jpeg")

            fb_url = _resolve_local_attachment_url(
                "http://localhost:5173/messages/inbox/sample_chat/photos/preview.jpg",
                "Facebook: sample_chat",
                data_dir,
            )
            self.assertEqual(
                fb_url,
                "/api/media?platform=facebook&source=sample_chat&path=photos%2Fpreview.jpg",
            )

            discord_export = _write_discord_html_export(data_dir / "discord")
            discord_url = _resolve_local_attachment_url(
                "123456789/assets/demo.png",
                "Discord: Example Guild",
                data_dir,
            )
            self.assertEqual(
                discord_url,
                "/api/media?platform=discord&source=Example+Guild&path=123456789%2Fassets%2Fdemo.png",
            )

            discord_media_dir = data_dir / "discord" / "media"
            discord_media_dir.mkdir(parents=True, exist_ok=True)
            media_file = discord_media_dir / "clip.mp4"
            media_file.write_bytes(b"discord-media")
            discord_absolute_media_url = _resolve_local_attachment_url(
                f"file://{media_file.as_posix()}",
                "Discord: Example Guild",
                data_dir,
            )
            self.assertEqual(
                discord_absolute_media_url,
                "/api/media?platform=discord&source=Example+Guild&path=media%2Fclip.mp4",
            )
            discord_media_basename_url = _resolve_local_attachment_url(
                "clip.mp4",
                "Discord: Example Guild",
                data_dir,
            )
            self.assertEqual(
                discord_media_basename_url,
                "/api/media?platform=discord&source=Example+Guild&path=media%2Fclip.mp4",
            )

            legacy_discord_media_dir = data_dir / "discord-media"
            legacy_discord_media_dir.mkdir(parents=True, exist_ok=True)
            legacy_media_file = legacy_discord_media_dir / "legacy.wav"
            legacy_media_file.write_bytes(b"legacy-discord-media")
            legacy_media_url = _resolve_local_attachment_url(
                f"file://{legacy_media_file.as_posix()}",
                "Discord: Example Guild",
                data_dir,
            )
            self.assertEqual(
                legacy_media_url,
                "/api/media?platform=discord&source=Example+Guild&path=legacy.wav",
            )

            self.assertTrue(discord_export.exists())

            signal_source = data_dir / "signal_decrypted" / "signal-export-test"
            signal_media = signal_source / "media"
            signal_media.mkdir(parents=True)
            (signal_media / "signal-2022-08-16-105045 PM.jpeg").write_bytes(
                b"signal-jpeg"
            )

            signal_index = _build_signal_filename_index(data_dir)
            signal_url = _resolve_local_attachment_url(
                "http://localhost:5173/signal-2022-08-16-105045%20PM.jpeg",
                "Signal: signal-export-test",
                data_dir,
                signal_index,
            )
            self.assertEqual(
                signal_url,
                "/api/media?platform=signal&source=signal-export-test&path=media%2Fsignal-2022-08-16-105045+PM.jpeg",
            )

            (signal_media / "signal-hash-only.jpeg").write_bytes(b"hash-match-jpeg")
            signal_root_media = (
                data_dir
                / "signal_decrypted"
                / "Happy chat (FREE ◼️◼️◼️◼️) (_id27)"
                / "media"
            )
            signal_root_media.mkdir(parents=True, exist_ok=True)
            (signal_root_media / "Attachment_14341_-1.jpg").write_bytes(b"signal-jpeg")

            rebuilt_index = _build_signal_filename_index(data_dir)
            hash_only_url = _resolve_local_attachment_url(
                "http://localhost:5173/signal-hash-only.jpeg",
                "Signal: signal-export-test",
                data_dir,
                rebuilt_index,
            )
            self.assertEqual(
                hash_only_url,
                "/api/media?platform=signal&source=signal-export-test&path=media%2Fsignal-hash-only.jpeg",
            )
            signal_root_url = _resolve_local_attachment_url(
                "Happy chat (FREE ◼️◼️◼️◼️) (_id27)/media/Attachment_14341_-1.jpg",
                "Signal: signal_decrypted",
                data_dir,
                rebuilt_index,
            )
            self.assertEqual(
                signal_root_url,
                "/api/media?platform=signal&source=signal_decrypted&path=Happy+chat+%28FREE+%E2%97%BC%EF%B8%8F%E2%97%BC%EF%B8%8F%E2%97%BC%EF%B8%8F%E2%97%BC%EF%B8%8F%29+%28_id27%29%2Fmedia%2FAttachment_14341_-1.jpg",
            )

    def test_local_attachment_resolution_helpers_flat_signal_layout(self) -> None:
        with TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            signal_source = data_dir / "signal_decrypted" / "signal-export-test"
            signal_media = signal_source / "media"
            signal_media.mkdir(parents=True)
            (signal_media / "signal-2022-08-16-105045 PM.jpeg").write_bytes(
                b"signal-jpeg"
            )

            signal_index = _build_signal_filename_index(data_dir)
            signal_url = _resolve_local_attachment_url(
                "http://localhost:5173/signal-2022-08-16-105045%20PM.jpeg",
                "Signal: signal-export-test",
                data_dir,
                signal_index,
            )
            self.assertEqual(
                signal_url,
                "/api/media?platform=signal&source=signal-export-test&path=media%2Fsignal-2022-08-16-105045+PM.jpeg",
            )


if __name__ == "__main__":
    unittest.main()
