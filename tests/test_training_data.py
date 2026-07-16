from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gchat.schema import SCHEMA_SQL
from gchat.training_data import TrainingExportConfig, export_training_data


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


class TrainingDataTests(unittest.TestCase):
    def test_exports_chronological_windows_without_system_messages(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "gchat.duckdb"
            output_dir = root / "training"
            con = duckdb.connect(str(db_path))
            con.execute(SCHEMA_SQL)
            con.execute("INSERT INTO people VALUES (1, 'Alice', '#111111')")
            con.execute("INSERT INTO people VALUES (2, 'Bob', '#222222')")
            con.execute("INSERT INTO sources VALUES (1, 'discord', 'Example')")
            con.execute("INSERT INTO themes VALUES (1, 'Friends')")
            con.execute("INSERT INTO channels VALUES (1, 1, 'general', 'General', 1)")

            start = datetime(2026, 1, 1, 10, 0)
            rows = []
            for conversation_id in range(1, 5):
                conversation_start = start + timedelta(days=conversation_id)
                for index in range(4):
                    message_id = f"c{conversation_id}-m{index}"
                    rows.append(
                        (
                            message_id,
                            1,
                            1 if index % 2 == 0 else 2,
                            conversation_start + timedelta(minutes=index),
                            f"message {conversation_id}-{index}",
                            f"c{conversation_id}-m0" if index == 3 else None,
                            conversation_id,
                            False,
                        )
                    )
            rows.extend(
                [
                    (
                        "system",
                        1,
                        1,
                        start,
                        "Alice joined",
                        None,
                        1,
                        True,
                    ),
                    (
                        "empty",
                        1,
                        1,
                        start,
                        " ",
                        None,
                        1,
                        False,
                    ),
                ]
            )
            con.executemany(
                """
                INSERT INTO messages (
                    id, channel_id, person_id, ts, content, reply_to_id,
                    conversation_id, is_system
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            con.close()

            summary = export_training_data(
                db_path,
                output_dir,
                TrainingExportConfig(
                    max_messages=3,
                    overlap_messages=1,
                    train_fraction=0.5,
                    validation_fraction=0.25,
                ),
            )

            self.assertEqual(summary.conversations, 4)
            self.assertEqual(summary.messages, 16)
            self.assertEqual(summary.windows, 8)
            self.assertEqual(
                summary.split_windows,
                {"train": 4, "validation": 2, "test": 2},
            )

            train = _read_jsonl(output_dir / "train.jsonl")
            validation = _read_jsonl(output_dir / "validation.jsonl")
            test = _read_jsonl(output_dir / "test.jsonl")
            self.assertEqual({item["conversation_id"] for item in train}, {1, 2})
            self.assertEqual({item["conversation_id"] for item in validation}, {3})
            self.assertEqual({item["conversation_id"] for item in test}, {4})
            self.assertEqual(
                [message["id"] for message in train[0]["messages"]],
                ["c1-m0", "c1-m1", "c1-m2"],
            )
            self.assertEqual(train[0]["messages"][1]["seconds_since_previous"], 60)
            self.assertNotIn("_timestamp", train[0]["messages"][0])
            self.assertEqual(train[1]["messages"][-1]["reply_to_id"], "c1-m0")
            self.assertFalse(
                any(
                    message["id"] in {"system", "empty"}
                    for record in train
                    for message in record["messages"]
                )
            )

            manifest = json.loads(
                (output_dir / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["schema_version"], 1)
            self.assertEqual(manifest["speakers"]["1"]["display_name"], "Alice")
            self.assertEqual(manifest["speakers"]["2"]["token"], "speaker_2")

    def test_rejects_invalid_window_configuration(self) -> None:
        with self.assertRaisesRegex(ValueError, "overlap_messages"):
            TrainingExportConfig(
                max_messages=8,
                overlap_messages=8,
            ).validate()


if __name__ == "__main__":
    unittest.main()
