from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import duckdb
from fastapi.testclient import TestClient

from gchat.api import (
    _build_signal_filename_index,
    _resolve_local_attachment_url,
    create_app,
)
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
            _make_signal_subset(SIGNAL_EXPORT, data_dir / SIGNAL_EXPORT.name)

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
            channel_id, source_id, current_name, platform_channel_id, platform = channel_row
            second_channel_id, second_source_id, second_current_name = second_channel_row
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
            next_id = con.execute(
                "SELECT COALESCE(MAX(id), 0) + 1 FROM channel_name_changes"
            ).fetchone()[0]
            con.execute(
                "INSERT INTO channel_name_changes VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [next_id, channel_id, source_id, "channel_name", None, current_name, "2024-01-01 00:00:00", '{"actor_name":"Bootstrapper"}'],
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
                [next_id + 2, channel_id, source_id, "channel_name", f" {renamed_name} ", renamed_name.upper(), "2024-01-03 00:00:00", '{"actor_name":"Case Changer"}'],
            )
            con.execute(
                "INSERT INTO channel_name_changes VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [next_id + 3, second_channel_id, second_source_id, "channel_name", None, second_current_name, "2024-01-04 00:00:00", None],
            )
            next_person_change_id = con.execute(
                "SELECT COALESCE(MAX(id), 0) + 1 FROM person_name_changes"
            ).fetchone()[0]
            person_id = con.execute(
                "SELECT id FROM people ORDER BY id LIMIT 1"
            ).fetchone()[0]
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
            chat = next((item for item in history["chats"] if item["id"] == channel_id), None)
            self.assertIsNotNone(chat)
            self.assertEqual(len(chat["previous_names"]), 2)
            bootstrap_entry = next((entry for entry in chat["previous_names"] if entry["new_name"] == current_name), None)
            rename_entry = next((entry for entry in chat["previous_names"] if entry["new_name"] == renamed_name), None)
            self.assertIsNotNone(bootstrap_entry)
            self.assertIsNotNone(rename_entry)
            self.assertIsNone(bootstrap_entry["previous_name"])
            self.assertEqual(bootstrap_entry["author_name"], "Bootstrapper")
            self.assertEqual(rename_entry["previous_name"], current_name)
            self.assertEqual(
                rename_entry["author_name"],
                actor_real_name,
            )
            participant = next((item for item in chat["participants"] if item["id"] == person_id), None)
            self.assertIsNotNone(participant)
            self.assertEqual(participant["history"][0]["new_name"], "New Nick")
            self.assertEqual(
                participant["history"][0]["author_name"],
                f"Horton ({actor_real_name})",
            )
            self.assertIsNone(next((item for item in history["chats"] if item["id"] == second_channel_id), None))

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

            signal_source = data_dir / "signal-export-test"
            signal_files = signal_source / "files" / "41"
            signal_files.mkdir(parents=True)
            media = signal_files / "413317d826d79d0246709eda6dc92ab896613c176d81d3c09e06bbc89d99fc5e.jpg"
            media.write_bytes(b"signal-jpeg")
            size = media.stat().st_size
            main_record = {
                "chatItem": {
                    "standardMessage": {
                        "attachments": [
                            {
                                "pointer": {
                                    "contentType": "image/jpeg",
                                    "fileName": "signal-2022-08-16-105045 PM.jpeg",
                                    "locatorInfo": {"size": size},
                                }
                            }
                        ]
                    }
                }
            }
            (signal_source / "main.jsonl").write_text(
                json.dumps(main_record, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            (signal_source / "metadata.json").write_text("{}", encoding="utf-8")

            signal_index = _build_signal_filename_index(data_dir)
            signal_url = _resolve_local_attachment_url(
                "http://localhost:5173/signal-2022-08-16-105045%20PM.jpeg",
                "Signal: signal-export-test",
                data_dir,
                signal_index,
            )
            self.assertEqual(
                signal_url,
                "/api/media?platform=signal&source=signal-export-test&path=files%2F41%2F413317d826d79d0246709eda6dc92ab896613c176d81d3c09e06bbc89d99fc5e.jpg",
            )

            hash_only_media = signal_source / "files" / "42" / "hash-only.jpg"
            hash_only_media.parent.mkdir(parents=True, exist_ok=True)
            hash_only_media.write_bytes(b"hash-match-jpeg")
            hash_only_digest = base64.b64encode(hashlib.sha256(hash_only_media.read_bytes()).digest()).decode("ascii")
            hash_only_record = {
                "chatItem": {
                    "standardMessage": {
                        "attachments": [
                            {
                                "pointer": {
                                    "contentType": "image/jpeg",
                                    "fileName": "signal-hash-only.jpeg",
                                    "locatorInfo": {
                                        "size": 999999,
                                        "plaintextHash": hash_only_digest,
                                    },
                                }
                            }
                        ]
                    }
                }
            }
            with (signal_source / "main.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(hash_only_record, ensure_ascii=False) + "\n")

            rebuilt_index = _build_signal_filename_index(data_dir)
            hash_only_url = _resolve_local_attachment_url(
                "http://localhost:5173/signal-hash-only.jpeg",
                "Signal: signal-export-test",
                data_dir,
                rebuilt_index,
            )
            self.assertEqual(
                hash_only_url,
                "/api/media?platform=signal&source=signal-export-test&path=files%2F42%2Fhash-only.jpg",
            )

    def test_local_attachment_resolution_helpers_flat_signal_layout(self) -> None:
        with TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            signal_source = data_dir / "signal-export-test"
            signal_files = signal_source / "files" / "41"
            signal_files.mkdir(parents=True)
            media = signal_files / "413317d826d79d0246709eda6dc92ab896613c176d81d3c09e06bbc89d99fc5e.jpg"
            media.write_bytes(b"signal-jpeg")
            (signal_source / "main.jsonl").write_text(
                json.dumps(
                    {
                        "chatItem": {
                            "standardMessage": {
                                "attachments": [
                                    {
                                        "pointer": {
                                            "contentType": "image/jpeg",
                                            "fileName": "signal-2022-08-16-105045 PM.jpeg",
                                            "locatorInfo": {"size": media.stat().st_size},
                                        }
                                    }
                                ]
                            }
                        }
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            (signal_source / "metadata.json").write_text("{}", encoding="utf-8")

            signal_index = _build_signal_filename_index(data_dir)
            signal_url = _resolve_local_attachment_url(
                "http://localhost:5173/signal-2022-08-16-105045%20PM.jpeg",
                "Signal: signal-export-test",
                data_dir,
                signal_index,
            )
            self.assertEqual(
                signal_url,
                "/api/media?platform=signal&source=signal-export-test&path=files%2F41%2F413317d826d79d0246709eda6dc92ab896613c176d81d3c09e06bbc89d99fc5e.jpg",
            )


if __name__ == "__main__":
    unittest.main()
