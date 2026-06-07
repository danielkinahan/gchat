from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DatasetPaths:
    data_dir: Path
    discord_files: list[Path]
    facebook_chats: list[Path]
    signal_exports: list[Path]


def _discord_html_exports(path: Path) -> list[Path]:
    if not path.exists() or not path.is_dir():
        return []
    return sorted(child for child in path.rglob("*.html") if child.is_file())


def _is_signal_html_export_root(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    for child in path.iterdir():
        if child.is_dir() and any(child.glob("*.html")):
            return True
    return False


def discover_dataset(data_dir: Path) -> DatasetPaths:
    discord_files = _discord_html_exports(data_dir / "discord")
    facebook_root = data_dir / "facebook"
    facebook_chats = (
        sorted([path for path in facebook_root.iterdir() if path.is_dir()])
        if facebook_root.exists()
        else []
    )
    signal_exports: list[Path] = []

    decrypted_html_root = data_dir / "signal_decrypted"
    if _is_signal_html_export_root(decrypted_html_root):
        signal_exports.append(decrypted_html_root)

    return DatasetPaths(
        data_dir=data_dir,
        discord_files=discord_files,
        facebook_chats=facebook_chats,
        signal_exports=signal_exports,
    )
