from __future__ import annotations

import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gchat.builder import build_database
from gchat.discovery import discover_dataset
from gchat.discord import normalize_export
from gchat.facebook import normalize_chat
from gchat.signal import normalize as normalize_signal


ROOT = Path(__file__).resolve().parents[1]
SIGNAL_EXPORT = ROOT / "data" / "signal"


def _make_signal_subset(source: Path, target: Path) -> None:
    account: dict | None = None
    group_recipient_id: str | None = None
    group_chat_id: str | None = None
    group_recipient: dict | None = None
    current_group_title: str | None = None
    group_chat: dict | None = None
    standard_message: dict | None = None
    reacted_message: dict | None = None
    profile_change_update: dict | None = None
    group_name_update: dict | None = None

    with (source / "main.jsonl").open("r", encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            if "account" in record and account is None:
                account = record
                continue
            if "recipient" in record:
                recipient = record["recipient"]
                if group_recipient_id is None and "group" in recipient:
                    group_recipient_id = str(recipient["id"])
                    group_recipient = record
                    snapshot = (recipient.get("group") or {}).get("snapshot") or {}
                    title = snapshot.get("title") or {}
                    current_group_title = str(title.get("title") or "")
                continue
            if "chat" in record and group_recipient_id is not None:
                chat = record["chat"]
                if str(chat.get("recipientId") or "") == group_recipient_id and group_chat_id is None:
                    group_chat_id = str(chat["id"])
                    group_chat = record
                continue
            if "chatItem" in record and group_chat_id is not None:
                item = record["chatItem"]
                if str(item.get("chatId") or "") != group_chat_id:
                    continue
                update = item.get("updateMessage") or {}
                if standard_message is None and "standardMessage" in item:
                    standard_message = record
                    continue
                if reacted_message is None and (item.get("standardMessage") or {}).get("reactions"):
                    reacted_message = record
                    continue
                if profile_change_update is None and update.get("profileChange"):
                    profile_change_update = record
                    continue
                if update.get("groupChange"):
                    group_change = update["groupChange"] or {}
                    for change in group_change.get("updates") or []:
                        if not isinstance(change, dict) or "groupNameUpdate" not in change:
                            continue
                        new_title = str((change["groupNameUpdate"] or {}).get("newGroupName") or "").strip()
                        if new_title and new_title != current_group_title and group_name_update is None:
                            group_name_update = record
                            break
                    continue
            if account and group_recipient_id and group_chat_id and standard_message and profile_change_update and group_name_update:
                break

    if (
        account is None
        or group_recipient is None
        or group_chat is None
        or group_recipient_id is None
        or group_chat_id is None
        or standard_message is None
        or reacted_message is None
        or profile_change_update is None
        or group_name_update is None
    ):
        raise AssertionError("Could not build a Signal subset fixture")

    selected_records: list[dict] = [
        account,
        group_recipient,
        group_chat,
        standard_message,
        reacted_message,
        profile_change_update,
        group_name_update,
    ]

    target.mkdir(parents=True, exist_ok=True)
    (target / "metadata.json").write_text((source / "metadata.json").read_text(encoding="utf-8"), encoding="utf-8")
    with (target / "main.jsonl").open("w", encoding="utf-8") as handle:
        for record in selected_records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _make_subset_data_dir(base: Path) -> Path:
    data_dir = base / "data"
    data_dir.mkdir()
    discord_dir = data_dir / "discord"
    discord_dir.mkdir()
    discord_file = next((ROOT / "data" / "discord").glob("*.json"))
    shutil.copy2(discord_file, discord_dir / discord_file.name)
    facebook_dir = data_dir / "facebook"
    facebook_dir.mkdir()
    facebook_chat = next((ROOT / "data" / "facebook").iterdir())
    shutil.copytree(facebook_chat, facebook_dir / facebook_chat.name)
    _make_signal_subset(SIGNAL_EXPORT, data_dir / SIGNAL_EXPORT.name)
    return data_dir


class IngestTests(unittest.TestCase):
    def test_discord_export(self) -> None:
        path = next((ROOT / "data" / "discord").glob("*.json"))
        export = normalize_export(path)
        self.assertGreater(len(export.messages), 0)
        self.assertTrue(export.messages[0].id)

    def test_facebook_thread(self) -> None:
        chat_dir = ROOT / "data" / "facebook" / "VirgilsDisciplesR_JuKl_Syh8Q"
        export = normalize_chat(chat_dir)
        self.assertGreater(len(export.messages), 0)
        self.assertTrue(export.messages[0].content)
        self.assertGreaterEqual(max((message.reaction_count for message in export.messages), default=0), 3)
        reacted = next((message for message in export.messages if message.reaction_count >= 3), None)
        self.assertIsNotNone(reacted)
        self.assertTrue(reacted and reacted.reaction_summary and "×" in reacted.reaction_summary)
        nickname_changes = [
            change
            for change in export.name_changes
            if change.entity_kind == "person" and change.kind == "nickname-change"
        ]
        self.assertGreater(len(nickname_changes), 0)
        self.assertTrue(any(change.payload_json and '"chatId"' in change.payload_json for change in nickname_changes))

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
        self.assertTrue(any(change.entity_raw_id == "Ben" and change.new_name == "Onion Man" for change in person_changes))
        self.assertTrue(any(change.entity_raw_id == "Alex Staszak" and change.new_name == "Bubble Man" for change in person_changes))

    def test_signal_database(self) -> None:
        with TemporaryDirectory() as tmp:
            subset = Path(tmp) / "signal"
            _make_signal_subset(SIGNAL_EXPORT, subset)
            export = normalize_signal(subset)
        self.assertGreater(len(export.messages), 0)
        self.assertTrue(export.channels)
        self.assertGreater(max((message.reaction_count for message in export.messages), default=0), 0)

    def test_signal_flat_layout_discovery(self) -> None:
        with TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            data_dir.mkdir()
            (data_dir / "db.sqlite").touch()
            _make_signal_subset(SIGNAL_EXPORT, data_dir / SIGNAL_EXPORT.name)

            paths = discover_dataset(data_dir)

        self.assertEqual(paths.signal_dbs, [data_dir / "db.sqlite"])
        self.assertEqual(paths.signal_exports, [data_dir / SIGNAL_EXPORT.name])

    def test_signal_group_title_history_uses_chronology_without_old_title(self) -> None:
        with TemporaryDirectory() as tmp:
            export_dir = Path(tmp) / "signal-export-test"
            export_dir.mkdir(parents=True, exist_ok=True)
            records = [
                {"account": {"username": "me"}},
                {"recipient": {"id": "1", "group": {"snapshot": {"title": {"title": "Final Name"}, "members": []}}}},
                {"recipient": {"id": "2", "contact": {"aci": "author-2", "name": "Author Two"}}},
                {"recipient": {"id": "3", "contact": {"aci": "author-3", "name": "Author Three"}}},
                {"chat": {"id": "10", "recipientId": "1"}},
                {
                    "chatItem": {
                        "chatId": "10",
                        "authorId": "2",
                        "dateSent": "1000",
                        "updateMessage": {
                            "groupChange": {
                                "updates": [
                                    {
                                        "groupNameUpdate": {
                                            "newGroupName": "Middle Name",
                                            "updaterAci": "author-2",
                                        }
                                    }
                                ]
                            }
                        },
                    }
                },
                {
                    "chatItem": {
                        "chatId": "10",
                        "authorId": "3",
                        "dateSent": "2000",
                        "updateMessage": {
                            "groupChange": {
                                "updates": [
                                    {
                                        "groupNameUpdate": {
                                            "newGroupName": "Final Name",
                                            "updaterAci": "author-3",
                                        }
                                    }
                                ]
                            }
                        },
                    }
                },
            ]
            (export_dir / "metadata.json").write_text("{}", encoding="utf-8")
            with (export_dir / "main.jsonl").open("w", encoding="utf-8") as handle:
                for record in records:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")

            export = normalize_signal(export_dir)

        channel = next(channel for channel in export.channels if channel.raw_id == "10")
        self.assertEqual(channel.name, "Final Name")
        title_changes = [
            change
            for change in export.name_changes
            if change.entity_kind == "channel" and change.entity_raw_id == "10"
        ]
        self.assertEqual(len(title_changes), 2)
        self.assertIsNone(title_changes[0].previous_name)
        self.assertEqual(title_changes[0].new_name, "Middle Name")
        self.assertEqual(title_changes[1].previous_name, "Middle Name")
        self.assertEqual(title_changes[1].new_name, "Final Name")

    def test_signal_group_title_history_includes_old_title_when_present(self) -> None:
        with TemporaryDirectory() as tmp:
            export_dir = Path(tmp) / "signal-export-test-old-title"
            export_dir.mkdir(parents=True, exist_ok=True)
            records = [
                {"account": {"username": "me"}},
                {"recipient": {"id": "1", "group": {"snapshot": {"title": {"title": "Final Name"}, "members": []}}}},
                {"recipient": {"id": "2", "contact": {"aci": "author-2", "name": "Author Two"}}},
                {"chat": {"id": "10", "recipientId": "1"}},
                {
                    "chatItem": {
                        "chatId": "10",
                        "authorId": "2",
                        "dateSent": "1000",
                        "updateMessage": {
                            "groupChange": {
                                "updates": [
                                    {
                                        "groupNameUpdate": {
                                            "oldGroupName": "Health Chat 👨‍⚕️💉",
                                            "newGroupName": "Final Name",
                                            "updaterAci": "author-2",
                                        }
                                    }
                                ]
                            }
                        },
                    }
                },
            ]
            (export_dir / "metadata.json").write_text("{}", encoding="utf-8")
            with (export_dir / "main.jsonl").open("w", encoding="utf-8") as handle:
                for record in records:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")

            export = normalize_signal(export_dir)

        title_changes = [
            change
            for change in export.name_changes
            if change.entity_kind == "channel" and change.entity_raw_id == "10"
        ]
        self.assertEqual(len(title_changes), 2)
        self.assertIsNone(title_changes[0].previous_name)
        self.assertEqual(title_changes[0].new_name, "Health Chat 👨‍⚕️💉")
        self.assertEqual(title_changes[1].previous_name, "Health Chat 👨‍⚕️💉")
        self.assertEqual(title_changes[1].new_name, "Final Name")

    def test_signal_group_filter_uses_configured_identities(self) -> None:
        with TemporaryDirectory() as tmp:
            export_dir = Path(tmp) / "signal-export-filter-test"
            export_dir.mkdir(parents=True, exist_ok=True)
            records = [
                {"account": {"username": "me"}},
                {
                    "recipient": {
                        "id": "1",
                        "group": {
                            "snapshot": {
                                "title": {"title": "Filter Test Group"},
                                "members": [{"userId": "known-user"}, {"userId": "other-user"}],
                            }
                        },
                    }
                },
                {"recipient": {"id": "2", "contact": {"aci": "known-user", "name": "Known User"}}},
                {"recipient": {"id": "3", "contact": {"aci": "other-user", "name": "Other User"}}},
                {"chat": {"id": "10", "recipientId": "1"}},
                {
                    "chatItem": {
                        "chatId": "10",
                        "authorId": "2",
                        "dateSent": "1000",
                        "standardMessage": {"text": {"body": "hello"}},
                    }
                },
            ]
            (export_dir / "metadata.json").write_text("{}", encoding="utf-8")
            with (export_dir / "main.jsonl").open("w", encoding="utf-8") as handle:
                for record in records:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")

            included = normalize_signal(export_dir, include_signal_identities={"known-user", "other-user"})
            excluded_single = normalize_signal(export_dir, include_signal_identities={"known-user"})
            excluded = normalize_signal(export_dir, include_signal_identities={"not-in-chat"})

        self.assertEqual(len(included.channels), 1)
        self.assertEqual(len(included.messages), 1)
        self.assertEqual(included.channels[0].name, "Filter Test Group")
        self.assertEqual(len(excluded_single.channels), 0)
        self.assertEqual(len(excluded_single.messages), 0)
        self.assertEqual(len(excluded.channels), 0)
        self.assertEqual(len(excluded.messages), 0)

    def test_build_database(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            output = tmp_path / "gchat.duckdb"
            config_dir = tmp_path / "config"
            config_dir.mkdir()
            discord_sample = normalize_export(next((ROOT / "data" / "discord").glob("*.json")))
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
            build_database(_make_subset_data_dir(tmp_path), output, config_dir=config_dir)
            self.assertTrue(output.exists())
            import duckdb

            con = duckdb.connect(str(output))
            self.assertEqual(con.execute("SELECT COUNT(*) FROM people WHERE display_name = 'Example Person'").fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM themes WHERE name = 'Example Theme'").fetchone()[0], 1)
            self.assertGreater(con.execute("SELECT COUNT(*) FROM person_name_changes").fetchone()[0], 0)
            self.assertGreater(con.execute("SELECT COUNT(*) FROM channel_name_changes").fetchone()[0], 0)
            con.close()


if __name__ == "__main__":
    unittest.main()
