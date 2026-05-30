from __future__ import annotations

import argparse
import shutil
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup

from .reconciliation import load_reconciliation
from .util import fix_facebook_mojibake, normalize_whitespace


DEFAULT_SOURCE_DIR = Path("/home/daniel/Nextcloud/Archive/Facebook Data/messages/inbox")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DEST_DIR = PROJECT_ROOT / "data" / "facebook"


@dataclass(frozen=True)
class FacebookArchiveMatch:
    source_dir: Path
    title: str
    participants: tuple[str, ...]
    matched_participants: tuple[str, ...]


@dataclass(frozen=True)
class FacebookArchiveScan:
    matches: list[FacebookArchiveMatch]
    unreadable_dirs: list[Path]


def _clean_text(value: str) -> str:
    return fix_facebook_mojibake(normalize_whitespace(value))


def _parse_participants_text(text: str) -> tuple[str, ...]:
    prefix = "Participants:"
    if not text.startswith(prefix):
        return ()
    people = text[len(prefix) :].replace(", and ", ", ").replace(" and ", ", ")
    return tuple(participant for participant in (_clean_text(part) for part in people.split(",")) if participant)


def _read_chat_header(chat_dir: Path) -> tuple[str, tuple[str, ...]]:
    for html_file in sorted(chat_dir.glob("message_*.html")):
        soup = BeautifulSoup(html_file.read_text(encoding="utf-8"), "html.parser")
        title = _clean_text(soup.title.get_text(" ", strip=True)) if soup.title else chat_dir.name
        participants_node = soup.find(string=lambda text: isinstance(text, str) and text.strip().startswith("Participants:"))
        if participants_node is None:
            continue
        participants = _parse_participants_text(_clean_text(participants_node.strip()))
        if participants:
            return title or chat_dir.name, participants
    raise ValueError(f"Could not find participants in {chat_dir}")


def _facebook_people(config_dir: Path) -> set[str]:
    reconciliation = load_reconciliation(config_dir)
    return {
        raw_id
        for platform, raw_id in reconciliation.people.identity_to_person
        if platform == "facebook"
    }


def find_matching_archives(
    source_dir: Path,
    config_dir: Path,
    minimum_matches: int = 3,
) -> list[FacebookArchiveMatch]:
    return scan_archives(source_dir, config_dir, minimum_matches).matches


def scan_archives(
    source_dir: Path,
    config_dir: Path,
    minimum_matches: int = 3,
) -> FacebookArchiveScan:
    facebook_people = _facebook_people(config_dir)
    matches: list[FacebookArchiveMatch] = []
    unreadable_dirs: list[Path] = []
    for chat_dir in sorted(path for path in source_dir.iterdir() if path.is_dir()):
        try:
            title, participants = _read_chat_header(chat_dir)
        except ValueError:
            unreadable_dirs.append(chat_dir)
            continue
        matched_participants = tuple(participant for participant in participants if participant in facebook_people)
        if len(matched_participants) < minimum_matches:
            continue
        matches.append(
            FacebookArchiveMatch(
                source_dir=chat_dir,
                title=title,
                participants=participants,
                matched_participants=matched_participants,
            )
        )
    return FacebookArchiveScan(matches=matches, unreadable_dirs=unreadable_dirs)


def copy_matching_archives(
    matches: list[FacebookArchiveMatch],
    destination_dir: Path,
    overwrite: bool = False,
) -> tuple[list[Path], list[Path]]:
    copied: list[Path] = []
    skipped: list[Path] = []
    destination_dir.mkdir(parents=True, exist_ok=True)
    for match in matches:
        destination = destination_dir / match.source_dir.name
        if destination.exists():
            if not overwrite:
                skipped.append(destination)
                continue
            shutil.rmtree(destination)
        shutil.copytree(match.source_dir, destination)
        copied.append(destination)
    return copied, skipped


def _print_matches(matches: list[FacebookArchiveMatch]) -> None:
    print(f"Found {len(matches)} matching Facebook group chat folders:")
    for match in matches:
        matched = ", ".join(match.matched_participants)
        print(f"- {match.source_dir.name} ({match.title})")
        print(f"  matched participants: {matched}")


def _confirm_copy() -> bool:
    response = input("Copy these folders? [y/N]: ").strip().lower()
    return response in {"y", "yes"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gchat-copy-facebook-groups",
        description="Copy Facebook group chat archive folders that include configured participants.",
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST_DIR)
    parser.add_argument("--config-dir", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--min-participants", type=int, default=2)
    parser.add_argument("--yes", action="store_true", help="Copy without prompting for confirmation.")
    parser.add_argument("--overwrite", action="store_true", help="Replace destination folders that already exist.")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    scan = scan_archives(args.source, args.config_dir, minimum_matches=args.min_participants)
    _print_matches(scan.matches)
    if scan.unreadable_dirs:
        print(f"Skipped {len(scan.unreadable_dirs)} folder(s) with no readable participants header.")
    if not scan.matches:
        return

    if not args.yes and not _confirm_copy():
        print("Aborted.")
        return

    copied, skipped = copy_matching_archives(scan.matches, args.dest, overwrite=args.overwrite)
    print(f"Copied {len(copied)} folder(s) to {args.dest}.")
    if skipped:
        print(f"Skipped {len(skipped)} existing folder(s). Use --overwrite to replace them.")


if __name__ == "__main__":
    main()
