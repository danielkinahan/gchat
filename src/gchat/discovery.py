from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DatasetPaths:
    data_dir: Path
    discord_files: list[Path]
    facebook_chats: list[Path]
    signal_dbs: list[Path]
    signal_exports: list[Path]


def discover_dataset(data_dir: Path) -> DatasetPaths:
    discord_files = sorted((data_dir / "discord").glob("*.json")) if (data_dir / "discord").exists() else []
    facebook_root = data_dir / "facebook"
    facebook_chats = sorted([path for path in facebook_root.iterdir() if path.is_dir()]) if facebook_root.exists() else []
    signal_root = data_dir / "signal"
    signal_dbs = [signal_root / "db.sqlite"] if (signal_root / "db.sqlite").exists() else []
    signal_exports = (
        sorted({path.parent for path in signal_root.glob("**/main.jsonl") if path.is_file() and path.parent != signal_root}, key=str)
        if signal_root.exists()
        else []
    )
    return DatasetPaths(
        data_dir=data_dir,
        discord_files=discord_files,
        facebook_chats=facebook_chats,
        signal_dbs=signal_dbs,
        signal_exports=signal_exports,
    )
