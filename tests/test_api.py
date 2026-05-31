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

            client = TestClient(create_app(db_path))
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


if __name__ == "__main__":
    unittest.main()
