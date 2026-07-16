from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from gchat.configuration import validate_configuration
from gchat.moderation import load_moderation_config
from gchat.reconciliation import load_reconciliation


class ConfigValidationTests(unittest.TestCase):
    def test_returns_configuration_diagnostics(self) -> None:
        with TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            (config_dir / "people.yaml").write_text(
                """
people:
  - name: Example Bot
    is_bot: true
    identities:
      - platform: signal
        id: bot
""",
                encoding="utf-8",
            )

            diagnostics = validate_configuration(config_dir)

            self.assertTrue(diagnostics["valid"])
            self.assertEqual(diagnostics["people"], 1)
            self.assertEqual(diagnostics["bots"], 1)
            self.assertEqual(diagnostics["identities"], 1)

    def test_rejects_identity_without_identifier(self) -> None:
        with TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            (config_dir / "people.yaml").write_text(
                """
people:
  - name: Broken
    identities:
      - platform: signal
""",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "id, username, or name"):
                load_reconciliation(config_dir=config_dir)

    def test_rejects_invalid_media_hash(self) -> None:
        with TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            (config_dir / "moderation.yaml").write_text(
                """
blocked_media:
  sha256:
    - not-a-sha256
""",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "invalid SHA-256"):
                load_moderation_config(config_dir)

    def test_rejects_conflicting_channel_assignments(self) -> None:
        with TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            (config_dir / "themes.yaml").write_text(
                """
themes:
  - name: One
    channels:
      - source: "Signal: demo"
        channel: Main
  - name: Two
    channels:
      - source: "Signal: demo"
        channel: Main
""",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "assigned to both"):
                load_reconciliation(config_dir=config_dir)
