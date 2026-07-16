from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gchat.api import _resolve_local_attachment_url
from gchat.builder import build_database
from gchat.discord import normalize_export
from gchat.discovery import discover_dataset
from gchat.facebook import normalize_chat
from gchat.reconciliation import load_reconciliation
from gchat.signal import _message_reactions, normalize as normalize_signal

ROOT = Path(__file__).resolve().parents[1]


def _write_discord_html_export(discord_dir: Path) -> Path:
    guild_dir = discord_dir / "123456789"
    assets_dir = guild_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    (assets_dir / "demo.png").write_bytes(b"png")
    (assets_dir / "dickbowtie.png").write_bytes(b"emoji")

    html = """
    <html>
      <head><title>Example Guild - general</title></head>
      <body>
        <div class="preamble">
          <div class="preamble__entry">Example Guild</div>
          <div class="preamble__entry">Text Channels / general</div>
        </div>

        <div class="chatlog__message-container" data-message-id="175928847299117063" id="chatlog__message-container-175928847299117063">
          <div class="chatlog__header">
            <span class="chatlog__author" data-user-id="1001">Alice</span>
            <span class="chatlog__timestamp"><a title="07-Jan-2024 10:00">Jan 07, 2024 10:00</a></span>
          </div>
          <div class="chatlog__content chatlog__markdown">
            <div class="chatlog__markdown-preserve">hello there</div>
          </div>
          <div class="chatlog__attachment">
            <a href="assets/demo.png"><img class="chatlog__attachment-media" src="assets/demo.png" /></a>
          </div>
          <div class="chatlog__reactions">
            <div class="chatlog__reaction" title="dickbowtie">
              <img class="chatlog__emoji chatlog__emoji--small" alt="dickbowtie" src="assets/dickbowtie.png" />
              <span class="chatlog__reaction-count">2</span>
            </div>
          </div>
        </div>

        <div class="chatlog__message-container" data-message-id="175928847299117064" id="chatlog__message-container-175928847299117064">
          <div class="chatlog__reply">
            <a class="chatlog__reply-link" href="#chatlog__message-container-175928847299117063">reply</a>
          </div>
          <span class="chatlog__short-timestamp" title="07-Jan-2024 10:01">10:01</span>
          <div class="chatlog__content chatlog__markdown">
            <div class="chatlog__markdown-preserve">follow up</div>
          </div>
          <span class="chatlog__edited-timestamp" title="07-Jan-2024 10:02">(edited)</span>
        </div>

        <div class="chatlog__message-container" data-message-id="175928847299117065" id="chatlog__message-container-175928847299117065">
          <div class="chatlog__system-notification">
            <span class="chatlog__system-notification-author" data-user-id="1001">Alice</span>
            <span class="chatlog__system-notification-timestamp"><a title="07-Jan-2024 10:03">Jan 07, 2024 10:03</a></span>
            <span class="chatlog__system-notification-content">Alice changed the channel name to "general-renamed"</span>
          </div>
        </div>
      </body>
    </html>
    """
    html_path = guild_dir / "987654321.html"
    html_path.write_text(html, encoding="utf-8")
    return html_path


def _make_subset_data_dir(base: Path) -> Path:
    data_dir = base / "data"
    data_dir.mkdir()
    discord_dir = data_dir / "discord"
    discord_dir.mkdir()
    _write_discord_html_export(discord_dir)
    facebook_dir = data_dir / "facebook"
    facebook_dir.mkdir()
    facebook_chat = next((ROOT / "data" / "facebook").iterdir())
    shutil.copytree(facebook_chat, facebook_dir / facebook_chat.name)
    return data_dir


class IngestTests(unittest.TestCase):
    def test_signal_reaction_identity_is_preserved_when_exported(self) -> None:
        soup = BeautifulSoup(
            """
            <div class="message">
              <div class="msg-reactions">
                <div class="msg-reaction">
                  <span class="msg-emoji">❤️</span>
                  <div class="msg-reaction-info">
                    From: Alice
                    Sent: 2026-01-01 12:00:00
                  </div>
                </div>
              </div>
            </div>
            """,
            "html.parser",
        )

        count, _, details_json = _message_reactions(soup)

        self.assertEqual(count, 1)
        details = json.loads(details_json or "[]")
        self.assertEqual(
            details[0]["reactors"][0],
            {
                "platform": "signal",
                "raw_id": "name:alice",
                "display_name": "Alice",
            },
        )

    def test_discord_export(self) -> None:
        with TemporaryDirectory() as tmp:
            path = _write_discord_html_export(Path(tmp) / "discord")
            export = normalize_export(path)

        self.assertEqual(export.source.name, "Discord: Example Guild")
        self.assertEqual(export.channel.raw_id, "987654321")
        self.assertEqual(export.channel.name, "general")
        self.assertEqual(len(export.messages), 3)
        self.assertEqual(export.messages[0].id, "175928847299117063")
        self.assertEqual(export.messages[1].reply_to_id, "175928847299117063")
        self.assertTrue(export.messages[1].is_edited)
        self.assertEqual(
            export.messages[0].attachment_preview, "123456789/assets/demo.png"
        )
        reacted = next(
            (message for message in export.messages if message.reaction_details_json),
            None,
        )
        self.assertIsNotNone(reacted)
        assert reacted is not None
        details = json.loads(reacted.reaction_details_json or "[]")
        self.assertTrue(
            any(
                item.get("image_url") == "123456789/assets/dickbowtie.png"
                for item in details
            )
        )
        self.assertTrue(any(item.get("name") == "dickbowtie" for item in details))
        channel_changes = [
            change for change in export.name_changes if change.entity_kind == "channel"
        ]
        self.assertEqual(len(channel_changes), 1)
        self.assertEqual(channel_changes[0].new_name, "general-renamed")

    def test_facebook_thread(self) -> None:
        chat_dir = ROOT / "data" / "facebook" / "VirgilsDisciplesR_JuKl_Syh8Q"
        if not chat_dir.exists():
            self.skipTest("optional Facebook archive fixture is not available")
        export = normalize_chat(chat_dir)
        self.assertGreater(len(export.messages), 0)
        self.assertTrue(export.messages[0].content)
        self.assertGreaterEqual(
            max((message.reaction_count for message in export.messages), default=0), 3
        )
        reacted = next(
            (message for message in export.messages if message.reaction_count >= 3),
            None,
        )
        self.assertIsNotNone(reacted)
        self.assertTrue(
            reacted and reacted.reaction_summary and "×" in reacted.reaction_summary
        )
        nickname_changes = [
            change
            for change in export.name_changes
            if change.entity_kind == "person" and change.kind == "nickname-change"
        ]
        self.assertGreater(len(nickname_changes), 0)
        self.assertTrue(
            any(
                change.payload_json and '"chatId"' in change.payload_json
                for change in nickname_changes
            )
        )

    def test_facebook_nickname_aliases_reuse_person_identity(self) -> None:
        with TemporaryDirectory() as tmp:
            chat_dir = Path(tmp) / "NicknamesChat"
            chat_dir.mkdir(parents=True, exist_ok=True)
            html = """
            <html><body>
              <div class="pam _3-95 _2pi0 _2lej uiBoxWhite noborder">
                <div class="_3-96 _2pio _2lek _2lel">Alex Staszak</div>
                <div class="_3-96 _2let"><div><div>first</div></div></div>
                <div class="_3-94 _2lem">01 Jan 2020, 09:59</div>
              </div>
              <div class="pam _3-95 _2pi0 _2lej uiBoxWhite noborder">
                <div class="_3-96 _2pio _2lek _2lel">Ben</div>
                <div class="_3-96 _2let"><div><div>Ben set his own nickname to Onion Man.</div></div></div>
                <div class="_3-94 _2lem">01 Jan 2020, 10:00</div>
              </div>
              <div class="pam _3-95 _2pi0 _2lej uiBoxWhite noborder">
                <div class="_3-96 _2pio _2lek _2lel">Onion Man</div>
                <div class="_3-96 _2let"><div><div>hello</div></div></div>
                <div class="_3-94 _2lem">01 Jan 2020, 10:01</div>
              </div>
              <div class="pam _3-95 _2pi0 _2lej uiBoxWhite noborder">
                <div class="_3-96 _2pio _2lek _2lel">Ben</div>
                <div class="_3-96 _2let"><div><div>Ben set the nickname for Alex Staszak to Bubble Man.</div></div></div>
                <div class="_3-94 _2lem">01 Jan 2020, 10:02</div>
              </div>
              <div class="pam _3-95 _2pi0 _2lej uiBoxWhite noborder">
                <div class="_3-96 _2pio _2lek _2lel">Bubble Man</div>
                <div class="_3-96 _2let"><div><div>yo</div></div></div>
                <div class="_3-94 _2lem">01 Jan 2020, 10:03</div>
              </div>
            </body></html>
            """
            (chat_dir / "message_1.html").write_text(html, encoding="utf-8")
            export = normalize_chat(chat_dir)

        raw_ids = {person.raw_id for person in export.people}
        self.assertEqual(raw_ids, {"Alex Staszak", "Ben"})
        person_changes = [
            change
            for change in export.name_changes
            if change.entity_kind == "person" and change.kind == "nickname-change"
        ]
        self.assertTrue(
            any(
                change.entity_raw_id == "Ben" and change.new_name == "Onion Man"
                for change in person_changes
            )
        )
        self.assertTrue(
            any(
                change.entity_raw_id == "Alex Staszak"
                and change.new_name == "Bubble Man"
                for change in person_changes
            )
        )

    def test_facebook_video_placeholder_preserves_media_url(self) -> None:
        with TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            source_dir = data_dir / "facebook" / "GirlfriendChat_g5mGaF8S4g"
            media_dir = source_dir / "videos"
            media_dir.mkdir(parents=True)
            media_path = media_dir / "video1560358639_455678731901508.mp4"
            media_path.write_bytes(b"fake video")

            html = """
            <html><body>
              <div class="pam _3-95 _2pi0 _2lej uiBoxWhite noborder">
                <div class="_3-96 _2pio _2lek _2lel">Theo</div>
                <div class="_3-96 _2let">
                  <div><div><video src="messages/inbox/GirlfriendChat_g5mGaF8S4g/videos/video1560358639_455678731901508.mp4" controls="1">
                    <a href="messages/inbox/GirlfriendChat_g5mGaF8S4g/videos/video1560358639_455678731901508.mp4">
                      <div>Click for video:</div>
                      <img src="messages/inbox/GirlfriendChat_g5mGaF8S4g/videos/video1560358639_455678731901508.mp4" />
                    </a>
                  </video></div></div>
                </div>
                <div class="_3-94 _2lem">12 Jun 2019, 09:57</div>
              </div>
            </body></html>
            """
            (source_dir / "message_1.html").write_text(html, encoding="utf-8")
            export = normalize_chat(source_dir)

            self.assertEqual(len(export.messages), 1)
            message = export.messages[0]
            self.assertEqual(message.content, "")
            self.assertEqual(message.attachment_count, 1)
            self.assertEqual(
                message.attachment_preview,
                "messages/inbox/GirlfriendChat_g5mGaF8S4g/videos/video1560358639_455678731901508.mp4",
            )

            resolved = _resolve_local_attachment_url(
                message.attachment_preview,
                "Facebook: GirlfriendChat_g5mGaF8S4g",
                data_dir,
            )
            self.assertEqual(
                resolved,
                "/api/media?platform=facebook&source=GirlfriendChat_g5mGaF8S4g&path=videos%2Fvideo1560358639_455678731901508.mp4",
            )
            archived = _resolve_local_attachment_url(
                "messages/archived_threads/GirlfriendChat_g5mGaF8S4g/videos/video1560358639_455678731901508.mp4",
                "Facebook: GirlfriendChat_g5mGaF8S4g",
                data_dir,
            )
            self.assertEqual(
                archived,
                "/api/media?platform=facebook&source=GirlfriendChat_g5mGaF8S4g&path=videos%2Fvideo1560358639_455678731901508.mp4",
            )

    def test_signal_html_export(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "signal_decrypted"
            chat = root / "Test Group (_id77)"
            media = chat / "media"
            media.mkdir(parents=True, exist_ok=True)
            (media / "photo.jpg").write_bytes(b"jpg")
            (media / "clip.mp4").write_bytes(b"mp4")
            (media / "doc.pdf").write_bytes(b"pdf")
            html = """
            <html><head><title>Test Group</title></head><body>
              <div id="message-header">
                <div class="thread-subtitle">
                  3 members
                  <label>
                    <span class="groupdetails">
                      <span class="columnview">
                        <span class="column-right-align">Members:</span>
                        <span class="column-left-align">Alice <i>(admin)</i>, Bob, Carol</span>
                      </span>
                    </span>
                  </label>
                </div>
              </div>
              <div class="msg msg-status">
                <div class="status-text"><pre dir="auto"><span class="msg-thread-icon"></span>Alice changed the group name to "Better Group"</pre></div>
                <div class="footer-status"><span class="msg-data">Oct 09, 2020 19:45:00</span></div>
              </div>
              <div class="msg msg-incoming">
                <div class="msg-name"><div class="membername">Alice</div></div>
                <div><pre dir="auto">hello there</pre></div>
                <div class="footer"><span class="msg-data">Oct 09, 2020 19:50:50</span></div>
                <div class="msg-reactions">
                  <div class="msg-reaction"><span class="msg-emoji">❤️</span><span class="reaction-count">2</span></div>
                </div>
              </div>
              <div class="msg msg-outgoing msg-sender-1">
                <div class="attachment"><img src="media/photo.jpg" /></div>
                <div class="attachment"><video controls><source src="media/clip.mp4" type="video/mp4" /></video></div>
                <div class="attachment"><a href="media/doc.pdf">file</a></div>
                <div class="footer"><span class="msg-data">Oct 09, 2020 20:00:00</span></div>
              </div>
            </body></html>
            """
            (chat / "Test Group.html").write_text(html, encoding="utf-8")

            export = normalize_signal(root)

        self.assertEqual(len(export.channels), 1)
        self.assertEqual(export.channels[0].raw_id, "77")
        self.assertEqual(export.channels[0].name, "Test Group")
        self.assertEqual(len(export.messages), 2)
        incoming = next(
            message for message in export.messages if message.content == "hello there"
        )
        self.assertEqual(incoming.reaction_count, 2)
        self.assertEqual(incoming.reaction_summary, "❤️×2")
        outgoing = next(
            message for message in export.messages if message.attachment_count > 0
        )
        self.assertEqual(outgoing.attachment_count, 3)
        self.assertEqual(
            outgoing.attachment_preview, "Test Group (_id77)/media/photo.jpg"
        )
        channel_changes = [
            change for change in export.name_changes if change.entity_kind == "channel"
        ]
        self.assertEqual(len(channel_changes), 1)
        self.assertEqual(channel_changes[0].new_name, "Better Group")
        self.assertIn('"actor_name": "Alice"', channel_changes[0].payload_json or "")

    def test_signal_html_reply_strips_quote(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "signal_decrypted"
            chat = root / "Reply Group (_id88)"
            chat.mkdir(parents=True)
            html = """
            <html><body>
              <div class="msg msg-incoming">
                <div class="msg-name"><div class="membername">Alice</div></div>
                <div><pre dir="auto">original text here</pre></div>
                <div class="footer"><span class="msg-data">Oct 09, 2020 19:50:50</span></div>
              </div>
              <div class="msg msg-incoming">
                <div class="msg-name"><div class="membername">Bob</div></div>
                <div class="msg-quote">
                  <a class="quote-link" href="#123">
                    <div class="msg-quote-message">
                      <pre dir="auto">original text here</pre>
                    </div>
                  </a>
                </div>
                <div><pre dir="auto">reply text only</pre></div>
                <div class="footer"><span class="msg-data">Oct 09, 2020 19:51:00</span></div>
              </div>
            </body></html>
            """
            (chat / "Reply Group.html").write_text(html, encoding="utf-8")
            export = normalize_signal(root)

        reply = next(
            message
            for message in export.messages
            if message.content == "reply text only"
        )
        self.assertEqual(reply.reply_to_id, "123")
        self.assertNotIn("original text here", reply.content)

    def test_facebook_poll_vote_change_is_system(self) -> None:
        from gchat.facebook import _is_facebook_system_message

        self.assertTrue(
            _is_facebook_system_message(
                'Caelan changed their vote to "Jonah Bonell" in the poll: Wungo Division Fight C.'
            )
        )

    def test_signal_html_export_filters_to_configured_people(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "signal_decrypted"
            config_dir = tmp_path / "config"
            config_dir.mkdir(parents=True, exist_ok=True)
            (config_dir / "people.yaml").write_text(
                """
people:
  - name: Alice
    identities:
      - platform: signal
        id: "+15550000001"
  - name: Bob
    identities:
      - platform: signal
        id: "+15550000002"
""".strip(),
                encoding="utf-8",
            )
            (config_dir / "themes.yaml").write_text("themes: []\n", encoding="utf-8")

            included_chat = root / "Included Group (_id77)"
            included_media = included_chat / "media"
            included_media.mkdir(parents=True, exist_ok=True)
            (included_media / "photo.jpg").write_bytes(b"jpg")
            included_html = """
            <html><head><title>Included Group</title></head><body>
              <div id="message-header">
                <div class="thread-subtitle">
                  3 members
                  <label>
                    <span class="groupdetails">
                      <span class="columnview">
                        <span class="column-right-align">Members:</span>
                        <span class="column-left-align">Alice, Bob, Carol</span>
                      </span>
                    </span>
                  </label>
                </div>
              </div>
              <div class="msg msg-incoming">
                <div class="msg-name"><div class="membername">Alice</div></div>
                <div><pre dir="auto">hello</pre></div>
                <div class="footer"><span class="msg-data">Oct 09, 2020 19:50:50</span></div>
              </div>
            </body></html>
            """
            (included_chat / "Included Group.html").write_text(
                included_html, encoding="utf-8"
            )

            skipped_chat = root / "Skipped Group (_id88)"
            skipped_media = skipped_chat / "media"
            skipped_media.mkdir(parents=True, exist_ok=True)
            skipped_html = """
            <html><head><title>Skipped Group</title></head><body>
              <div id="message-header">
                <div class="thread-subtitle">
                  3 members
                  <label>
                    <span class="groupdetails">
                      <span class="columnview">
                        <span class="column-right-align">Members:</span>
                        <span class="column-left-align">Mallory, Trent, Eve</span>
                      </span>
                    </span>
                  </label>
                </div>
              </div>
              <div class="msg msg-incoming">
                <div class="msg-name"><div class="membername">Mallory</div></div>
                <div><pre dir="auto">secret</pre></div>
                <div class="footer"><span class="msg-data">Oct 09, 2020 19:55:50</span></div>
              </div>
            </body></html>
            """
            (skipped_chat / "Skipped Group.html").write_text(
                skipped_html, encoding="utf-8"
            )

            export = normalize_signal(
                root,
                reconciliation=load_reconciliation(config_dir=config_dir),
                filter_to_configured_people=True,
            )

        self.assertEqual([channel.raw_id for channel in export.channels], ["77"])
        self.assertEqual(len(export.messages), 1)
        self.assertEqual(export.messages[0].channel_raw_id, "77")

    def test_signal_flat_layout_discovery(self) -> None:
        with TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            data_dir.mkdir()
            db_path = data_dir / "db.sqlite"
            import sqlite3

            con = sqlite3.connect(db_path)
            con.execute("CREATE TABLE demo (id INTEGER)")
            con.close()

            paths = discover_dataset(data_dir)

        self.assertFalse(paths.signal_exports)

    def test_signal_requires_html_export_directory(self) -> None:
        with TemporaryDirectory() as tmp:
            signal_dir = Path(tmp) / "signal"
            signal_dir.mkdir(parents=True)
            (signal_dir / "main.jsonl").write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                normalize_signal(signal_dir)

    def test_signal_html_layout_discovery(self) -> None:
        with TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            html_dir = data_dir / "signal_decrypted" / "Test Group (_id77)"
            html_dir.mkdir(parents=True)
            (html_dir / "Test Group.html").write_text(
                "<html><body></body></html>", encoding="utf-8"
            )

            paths = discover_dataset(data_dir)

        self.assertEqual(paths.signal_exports, [data_dir / "signal_decrypted"])

    def test_signal_nested_sql_layout_discovery(self) -> None:
        with TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            signal_root = data_dir / "signal" / "sql"
            signal_root.mkdir(parents=True)
            import sqlite3

            db_path = signal_root / "db.sqlite"
            con = sqlite3.connect(db_path)
            con.execute("CREATE TABLE demo (id INTEGER)")
            con.close()

            paths = discover_dataset(data_dir)

        self.assertFalse(paths.signal_exports)

    def test_build_database(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            output = tmp_path / "gchat.duckdb"
            config_dir = tmp_path / "config"
            config_dir.mkdir()
            discord_sample_path = _write_discord_html_export(
                tmp_path / "discord-sample"
            )
            discord_sample = normalize_export(discord_sample_path)
            sample_person = discord_sample.people[0]
            sample_source = discord_sample.source.name
            sample_channel = discord_sample.channel.name
            (config_dir / "people.yaml").write_text(
                f"""people:\n  - name: Example Person\n    color: '#123456'\n    identities:\n      - platform: discord\n        id: '{sample_person.raw_id}'\n""",
                encoding="utf-8",
            )
            (config_dir / "themes.yaml").write_text(
                f"""themes:\n  - name: Example Theme\n    channels:\n      - source: '{sample_source}'\n        channel: '{sample_channel}'\n""",
                encoding="utf-8",
            )
            build_database(
                _make_subset_data_dir(tmp_path), output, config_dir=config_dir
            )
            self.assertTrue(output.exists())
            import duckdb

            con = duckdb.connect(str(output))
            people_count_row = con.execute(
                "SELECT COUNT(*) FROM people WHERE display_name = 'Example Person'"
            ).fetchone()
            self.assertIsNotNone(people_count_row)
            assert people_count_row is not None
            self.assertEqual(people_count_row[0], 1)

            theme_count_row = con.execute(
                "SELECT COUNT(*) FROM themes WHERE name = 'Example Theme'"
            ).fetchone()
            self.assertIsNotNone(theme_count_row)
            assert theme_count_row is not None
            self.assertEqual(theme_count_row[0], 1)

            person_name_change_row = con.execute(
                "SELECT COUNT(*) FROM person_name_changes"
            ).fetchone()
            self.assertIsNotNone(person_name_change_row)
            assert person_name_change_row is not None
            self.assertGreaterEqual(person_name_change_row[0], 0)

            channel_name_change_row = con.execute(
                "SELECT COUNT(*) FROM channel_name_changes"
            ).fetchone()
            self.assertIsNotNone(channel_name_change_row)
            assert channel_name_change_row is not None
            self.assertGreater(channel_name_change_row[0], 0)

            person_stats_row = con.execute(
                """
                SELECT ps.exclusive_word_count
                FROM person_stats ps
                JOIN people p ON p.id = ps.person_id
                WHERE p.display_name = 'Example Person'
                  AND ps.message_count > 0
                """
            ).fetchone()
            self.assertIsNotNone(person_stats_row)
            assert person_stats_row is not None
            self.assertGreaterEqual(person_stats_row[0], 0)
            fact_version_row = con.execute(
                """
                SELECT value
                FROM build_metadata
                WHERE key = 'fact_schema_version'
                """
            ).fetchone()
            self.assertEqual(fact_version_row, ("2",))
            self.assertGreater(
                con.execute("SELECT COUNT(*) FROM message_tokens").fetchone()[0],
                0,
            )
            con.close()

    def test_failed_build_preserves_existing_database_and_cleans_temp(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            output = tmp_path / "gchat.duckdb"
            output.write_bytes(b"existing database")
            data_dir = _make_subset_data_dir(tmp_path)

            with patch(
                "gchat.builder.materialize_analytics_facts",
                side_effect=RuntimeError("fact build failed"),
            ):
                with self.assertRaisesRegex(RuntimeError, "fact build failed"):
                    build_database(data_dir, output)

            self.assertEqual(output.read_bytes(), b"existing database")
            self.assertEqual(list(tmp_path.glob("*.building")), [])


if __name__ == "__main__":
    unittest.main()
