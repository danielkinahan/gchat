from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gchat.facebook_archive import copy_matching_archives, find_matching_archives, main, scan_archives
from gchat.reconciliation import load_reconciliation


def _chat_html(title: str, participants: str) -> str:
    return f"""<html><head><title>{title}</title></head><body><div class="_2lek">Participants: {participants}</div></body></html>"""


class FacebookArchiveTests(unittest.TestCase):
    def test_find_matching_archives_filters_on_facebook_participants(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_dir = root / "source"
            config_dir = root / "config-root"
            (config_dir / "config").mkdir(parents=True)
            source_dir.mkdir()

            (config_dir / "config" / "people.yaml").write_text(
                """people:
  - name: Person A
    identities:
      - platform: facebook
        id: "Person A"
  - name: Person B
    identities:
      - platform: facebook
        id: "Person B"
  - name: Person C
    identities:
      - platform: facebook
        id: "Person C"
  - name: Person D
    identities:
      - platform: facebook
        id: "Person D"
""",
                encoding="utf-8",
            )

            match_dir = source_dir / "gargboyz_123"
            miss_dir = source_dir / "two_people_456"
            match_dir.mkdir()
            miss_dir.mkdir()
            (match_dir / "message_1.html").write_text(
                _chat_html("Group Chat", "Person A, Person B, Person C and Person D"),
                encoding="utf-8",
            )
            (miss_dir / "message_1.html").write_text(
                _chat_html("Pair Chat", "Person A and Person B"),
                encoding="utf-8",
            )

            matches = find_matching_archives(source_dir, config_dir)

            self.assertEqual([match.source_dir.name for match in matches], ["gargboyz_123"])
            self.assertEqual(matches[0].title, "Group Chat")
            self.assertEqual(
                matches[0].matched_participants,
                ("Person A", "Person B", "Person C", "Person D"),
            )

    def test_copy_matching_archives_copies_and_skips_existing_without_overwrite(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_dir = root / "source"
            dest_dir = root / "dest"
            config_dir = root / "config-root"
            (config_dir / "config").mkdir(parents=True)
            source_dir.mkdir()

            (config_dir / "config" / "people.yaml").write_text(
                """people:
  - name: Person A
    identities:
      - platform: facebook
        id: "Person A"
  - name: Person B
    identities:
      - platform: facebook
        id: "Person B"
  - name: Person C
    identities:
      - platform: facebook
        id: "Person C"
""",
                encoding="utf-8",
            )

            chat_dir = source_dir / "group_1"
            chat_dir.mkdir()
            (chat_dir / "message_1.html").write_text(
                _chat_html("Group 1", "Person A, Person B and Person C"),
                encoding="utf-8",
            )

            matches = find_matching_archives(source_dir, config_dir)
            copied, skipped = copy_matching_archives(matches, dest_dir)
            copied_again, skipped_again = copy_matching_archives(matches, dest_dir)

            self.assertEqual([path.name for path in copied], ["group_1"])
            self.assertEqual(skipped, [])
            self.assertEqual(copied_again, [])
            self.assertEqual([path.name for path in skipped_again], ["group_1"])

    def test_scan_archives_skips_unreadable_folders(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_dir = root / "source"
            config_dir = root / "config-root"
            (config_dir / "config").mkdir(parents=True)
            source_dir.mkdir()

            (config_dir / "config" / "people.yaml").write_text(
                """people:
  - name: Person A
    identities:
      - platform: facebook
        id: "Person A"
  - name: Person B
    identities:
      - platform: facebook
        id: "Person B"
  - name: Person C
    identities:
      - platform: facebook
        id: "Person C"
""",
                encoding="utf-8",
            )

            good_dir = source_dir / "group_1"
            bad_dir = source_dir / "broken_1"
            good_dir.mkdir()
            bad_dir.mkdir()
            (good_dir / "message_1.html").write_text(
                _chat_html("Group 1", "Person A, Person B and Person C"),
                encoding="utf-8",
            )
            (bad_dir / "message_1.html").write_text("<html><head><title>Broken</title></head><body></body></html>", encoding="utf-8")

            scan = scan_archives(source_dir, config_dir)

            self.assertEqual([match.source_dir.name for match in scan.matches], ["group_1"])
            self.assertEqual([path.name for path in scan.unreadable_dirs], ["broken_1"])

    def test_main_lists_folders_before_copying(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_dir = root / "source"
            dest_dir = root / "dest"
            config_dir = root / "config-root"
            (config_dir / "config").mkdir(parents=True)
            source_dir.mkdir()

            (config_dir / "config" / "people.yaml").write_text(
                """people:
  - name: Person A
    identities:
      - platform: facebook
        id: "Person A"
  - name: Person B
    identities:
      - platform: facebook
        id: "Person B"
  - name: Person C
    identities:
      - platform: facebook
        id: "Person C"
""",
                encoding="utf-8",
            )

            chat_dir = source_dir / "group_1"
            chat_dir.mkdir()
            (chat_dir / "message_1.html").write_text(
                _chat_html("Group 1", "Person A, Person B and Person C"),
                encoding="utf-8",
            )

            stdout = StringIO()
            argv = sys.argv[:]
            try:
                sys.argv = [
                    "gchat-copy-facebook-groups",
                    "--source",
                    str(source_dir),
                    "--dest",
                    str(dest_dir),
                    "--config-dir",
                    str(config_dir),
                    "--yes",
                ]
                with redirect_stdout(stdout):
                    main()
            finally:
                sys.argv = argv

            output = stdout.getvalue()
            self.assertIn("Found 1 matching Facebook group chat folders:", output)
            self.assertIn("- group_1", output)
            self.assertIn("Compared with", output)
            self.assertIn("+ group_1", output)
            self.assertIn("Copied 1 folder(s)", output)
            self.assertTrue((dest_dir / "group_1" / "message_1.html").exists())

    def test_main_shows_existing_destination_matches(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_dir = root / "source"
            dest_dir = root / "dest"
            config_dir = root / "config-root"
            (config_dir / "config").mkdir(parents=True)
            source_dir.mkdir()
            dest_dir.mkdir()

            (config_dir / "config" / "people.yaml").write_text(
                """people:
  - name: Person A
    identities:
      - platform: facebook
        id: "Person A"
  - name: Person B
    identities:
      - platform: facebook
        id: "Person B"
  - name: Person C
    identities:
      - platform: facebook
        id: "Person C"
""",
                encoding="utf-8",
            )

            chat_dir = source_dir / "group_1"
            chat_dir.mkdir()
            (chat_dir / "message_1.html").write_text(
                _chat_html("Group 1", "Person A, Person B and Person C"),
                encoding="utf-8",
            )
            (dest_dir / "group_1").mkdir()

            stdout = StringIO()
            argv = sys.argv[:]
            try:
                sys.argv = [
                    "gchat-copy-facebook-groups",
                    "--source",
                    str(source_dir),
                    "--dest",
                    str(dest_dir),
                    "--config-dir",
                    str(config_dir),
                    "--yes",
                ]
                with redirect_stdout(stdout):
                    main()
            finally:
                sys.argv = argv

            output = stdout.getvalue()
            self.assertIn("Compared with", output)
            self.assertIn("= group_1", output)

    def test_load_reconciliation_allows_empty_theme_channels(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_dir = root / "config"
            config_dir.mkdir()
            (config_dir / "people.yaml").write_text("people: []\n", encoding="utf-8")
            (config_dir / "themes.yaml").write_text(
                """themes:\n  - name: Empty theme\n    channels:\n""",
                encoding="utf-8",
            )

            config = load_reconciliation(root)

            self.assertEqual(config.themes.channel_to_theme, {})

    def test_load_reconciliation_matches_signal_name_aliases(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_dir = root / "config"
            config_dir.mkdir()
            (config_dir / "people.yaml").write_text(
                """
people:
  - name: Alex
    color: "#76f774"
    identities:
      - platform: signal
        id: "Alex"
  - name: Daniel
    color: "#D91111"
    identities:
      - platform: signal
        id: "self"
  - name: Theo
    color: "#FFFFFF"
    identities:
      - platform: facebook
        id: "Theo Mohamed"
  - name: Caelan
    color: "#C89EC8"
    identities:
      - platform: facebook
        id: "Caelan Rae-Oulette"
""",
                encoding="utf-8",
            )
            (config_dir / "themes.yaml").write_text("themes: []\n", encoding="utf-8")

            config = load_reconciliation(root)

            self.assertEqual(
                config.people.resolve("signal", "name:alex", "Alex"),
                ("Alex", "#76f774"),
            )
            self.assertEqual(
                config.people.resolve("signal", "self", "You"),
                ("Daniel", "#D91111"),
            )
            self.assertEqual(
                config.people.resolve("signal", "name:theo mohamed", "Theo Mohamed"),
                ("Theo", "#FFFFFF"),
            )
            self.assertEqual(
                config.people.resolve("signal", "name:caelan rae", "Caelan Rae"),
                ("Caelan", "#C89EC8"),
            )

    def test_build_parser_defaults_to_repo_root_paths(self) -> None:
        from gchat.facebook_archive import DEFAULT_DEST_DIR, DEFAULT_SOURCE_DIR, PROJECT_ROOT, build_parser

        parser = build_parser()
        args = parser.parse_args([])

        self.assertEqual(args.config_dir, PROJECT_ROOT)
        self.assertEqual(args.dest, DEFAULT_DEST_DIR)
        self.assertEqual(args.source, DEFAULT_SOURCE_DIR)
