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


def _signal_root(data_dir: Path) -> Path | None:
    if not data_dir.exists():
        return None
    flat_exports = [path for path in data_dir.iterdir() if path.is_dir() and (path / "main.jsonl").is_file()]
    if (data_dir / "db.sqlite").is_file() or flat_exports:
        return data_dir
    nested_root = data_dir / "signal"
    if nested_root.exists():
        return nested_root
    return None


def discover_dataset(data_dir: Path) -> DatasetPaths:
    discord_files = sorted((data_dir / "discord").glob("*.json")) if (data_dir / "discord").exists() else []
    facebook_root = data_dir / "facebook"
    facebook_chats = sorted([path for path in facebook_root.iterdir() if path.is_dir()]) if facebook_root.exists() else []
    signal_root = _signal_root(data_dir)
    
    # Prefer decrypted Signal backup if available; fall back to encrypted db
    signal_dbs = []
    if signal_root:
        decrypted_db = data_dir / "signal_decrypted" / "db.sqlite"
        if decrypted_db.exists():
            signal_dbs = [decrypted_db]
        elif (signal_root / "db.sqlite").exists():
            signal_dbs = [signal_root / "db.sqlite"]
    
    signal_exports = (
        sorted(
            [path for path in signal_root.iterdir() if path.is_dir() and (path / "main.jsonl").is_file()],
            key=str,
        )
        if signal_root
        else []
    )
    return DatasetPaths(
        data_dir=data_dir,
        discord_files=discord_files,
        facebook_chats=facebook_chats,
        signal_dbs=signal_dbs,
        signal_exports=signal_exports,
    )
