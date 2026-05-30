from __future__ import annotations

import os
import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gchat.builder import build_database
from gchat.discord import normalize_export
from gchat.facebook import normalize_chat
from gchat.signal import normalize as normalize_signal


ROOT = Path(__file__).resolve().parents[1]
SIGNAL_EXPORT = ROOT / "data" / "signal" / "signal-export-2026-05-30-13-27-57"


def _make_signal_subset(source: Path, target: Path) -> None:
    account: dict | None = None
    group_recipient_id: str | None = None
    group_chat_id: str | None = None
    group_recipient: dict | None = None
    current_group_title: str | None = None
    group_chat: dict | None = None
    standard_message: dict | None = None
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
        or profile_change_update is None
        or group_name_update is None
    ):
        raise AssertionError("Could not build a Signal subset fixture")

    selected_records: list[dict] = [
        account,
        group_recipient,
        group_chat,
        standard_message,
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
    _make_signal_subset(SIGNAL_EXPORT, data_dir / "signal" / SIGNAL_EXPORT.name)
    return data_dir


class IngestTests(unittest.TestCase):
    def test_discord_export(self) -> None:
        path = next((ROOT / "data" / "discord").glob("*.json"))
        export = normalize_export(path)
        self.assertGreater(len(export.messages), 0)
        self.assertTrue(export.messages[0].id)

    def test_facebook_thread(self) -> None:
        chat_dir = next((ROOT / "data" / "facebook").iterdir())
        export = normalize_chat(chat_dir)
        self.assertGreater(len(export.messages), 0)
        self.assertTrue(export.messages[0].content)

    def test_signal_database(self) -> None:
        with TemporaryDirectory() as tmp:
            subset = Path(tmp) / "signal" / SIGNAL_EXPORT.name
            _make_signal_subset(SIGNAL_EXPORT, subset)
            export = normalize_signal(subset)
        self.assertGreater(len(export.messages), 0)
        self.assertTrue(export.channels)

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
            cwd = Path.cwd()
            try:
                os.chdir(tmp_path)
                build_database(_make_subset_data_dir(tmp_path), output)
            finally:
                os.chdir(cwd)
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
