from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gchat.moderation import (
    REMOVED_MEDIA_URL,
    load_moderation_config,
    media_url_if_allowed,
    set_active_moderation,
)


class ModerationTests(unittest.TestCase):
    def test_load_moderation_yaml(self) -> None:
        with TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            (config_dir / "moderation.yaml").write_text(
                """
excluded_message_ids:
  - msg-123
blocked_media:
  sha256:
    - ABCDEF0123456789ABCDEF0123456789ABCDEF0123456789ABCDEF0123456789
  filenames:
    - blocked.jpg
""",
                encoding="utf-8",
            )
            config = load_moderation_config(config_dir)
            self.assertEqual(config.excluded_message_ids, frozenset({"msg-123"}))
            self.assertIn(
                "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
                config.blocked_media_sha256,
            )
            self.assertEqual(config.blocked_media_filenames, frozenset({"blocked.jpg"}))

    def test_legacy_excluded_messages_fallback(self) -> None:
        with TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            (config_dir / "excluded_messages.yaml").write_text(
                "- legacy-msg\n",
                encoding="utf-8",
            )
            config = load_moderation_config(config_dir)
            self.assertEqual(config.excluded_message_ids, frozenset({"legacy-msg"}))
            self.assertEqual(config.blocked_media_sha256, frozenset())
            self.assertEqual(config.blocked_media_filenames, frozenset())

    def test_blocked_media_by_hash_and_filename(self) -> None:
        with TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            media_file = Path(tmp) / "photo.jpg"
            media_file.write_bytes(b"blocked-image-bytes")
            digest = hashlib.sha256(b"blocked-image-bytes").hexdigest()

            (config_dir / "moderation.yaml").write_text(
                f"""
blocked_media:
  sha256:
    - {digest}
  filenames:
    - other.jpg
""",
                encoding="utf-8",
            )
            config = load_moderation_config(config_dir)
            set_active_moderation(config)

            self.assertEqual(
                media_url_if_allowed("/api/media?platform=signal&source=x&path=y", media_file),
                REMOVED_MEDIA_URL,
            )

            other_file = Path(tmp) / "other.jpg"
            other_file.write_bytes(b"different")
            self.assertEqual(
                media_url_if_allowed("/api/media?platform=signal&source=x&path=y", other_file),
                REMOVED_MEDIA_URL,
            )

            allowed_file = Path(tmp) / "allowed.png"
            allowed_file.write_bytes(b"allowed")
            self.assertEqual(
                media_url_if_allowed("/api/media?platform=signal&source=x&path=y", allowed_file),
                "/api/media?platform=signal&source=x&path=y",
            )


if __name__ == "__main__":
    unittest.main()
