from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb


SCHEMA_VERSION = 1
SPLITS = ("train", "validation", "test")


@dataclass(frozen=True)
class TrainingExportConfig:
    max_messages: int = 64
    overlap_messages: int = 8
    min_messages: int = 2
    train_fraction: float = 0.9
    validation_fraction: float = 0.05

    def validate(self) -> None:
        if self.max_messages < 2:
            raise ValueError("max_messages must be at least 2")
        if not 0 <= self.overlap_messages < self.max_messages:
            raise ValueError("overlap_messages must be between 0 and max_messages - 1")
        if not 1 <= self.min_messages <= self.max_messages:
            raise ValueError("min_messages must be between 1 and max_messages")
        if not 0 < self.train_fraction < 1:
            raise ValueError("train_fraction must be between 0 and 1")
        if not 0 <= self.validation_fraction < 1:
            raise ValueError("validation_fraction must be between 0 and 1")
        if self.train_fraction + self.validation_fraction >= 1:
            raise ValueError(
                "train_fraction + validation_fraction must be less than 1"
            )


@dataclass(frozen=True)
class TrainingExportSummary:
    conversations: int
    messages: int
    windows: int
    split_windows: dict[str, int]
    output_dir: str


def _isoformat(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _split_for_position(
    position: float,
    *,
    train_fraction: float,
    validation_fraction: float,
) -> str:
    if position <= train_fraction:
        return "train"
    if position <= train_fraction + validation_fraction:
        return "validation"
    return "test"


def _windows(
    messages: list[dict[str, Any]],
    *,
    max_messages: int,
    overlap_messages: int,
    min_messages: int,
) -> list[list[dict[str, Any]]]:
    result = []
    start = 0
    step = max_messages - overlap_messages
    while start < len(messages):
        window = messages[start : start + max_messages]
        if len(window) >= min_messages:
            result.append(window)
        if start + max_messages >= len(messages):
            break
        start += step
    return result


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
    temporary.replace(path)


def export_training_data(
    db_path: Path,
    output_dir: Path,
    config: TrainingExportConfig | None = None,
) -> TrainingExportSummary:
    """Export chronological, model-agnostic group-chat windows as JSONL."""
    config = config or TrainingExportConfig()
    config.validate()
    if not db_path.exists():
        raise FileNotFoundError(f"Database does not exist: {db_path}")

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        people_rows = con.execute(
            "SELECT id, display_name FROM people ORDER BY id"
        ).fetchall()
        rows = con.execute(
            """
            SELECT
                m.conversation_id,
                m.id,
                m.person_id,
                m.ts,
                m.content,
                m.reply_to_id,
                m.reaction_count,
                m.is_edited,
                c.id,
                c.name,
                s.platform,
                s.name,
                t.name
            FROM messages m
            JOIN channels c ON c.id = m.channel_id
            JOIN sources s ON s.id = c.source_id
            LEFT JOIN themes t ON t.id = c.theme_id
            WHERE m.conversation_id IS NOT NULL
              AND NOT m.is_system
              AND m.content IS NOT NULL
              AND trim(m.content) <> ''
            ORDER BY m.conversation_id, m.ts, m.id
            """
        ).fetchall()
    finally:
        con.close()

    speakers = {
        int(person_id): {
            "token": f"speaker_{person_id}",
            "display_name": display_name,
        }
        for person_id, display_name in people_rows
    }
    conversations: dict[int, dict[str, Any]] = {}
    for row in rows:
        (
            conversation_id,
            message_id,
            person_id,
            timestamp,
            content,
            reply_to_id,
            reaction_count,
            is_edited,
            channel_id,
            channel_name,
            platform,
            source_name,
            theme_name,
        ) = row
        conversation = conversations.setdefault(
            int(conversation_id),
            {
                "conversation_id": int(conversation_id),
                "channel": {
                    "id": int(channel_id),
                    "name": channel_name,
                    "platform": platform,
                    "source": source_name,
                    "theme": theme_name,
                },
                "messages": [],
            },
        )
        previous = conversation["messages"][-1] if conversation["messages"] else None
        gap_seconds = (
            max(0, int((timestamp - previous["_timestamp"]).total_seconds()))
            if previous
            else 0
        )
        conversation["messages"].append(
            {
                "id": str(message_id),
                "speaker": speakers[int(person_id)]["token"],
                "timestamp": _isoformat(timestamp),
                "seconds_since_previous": gap_seconds,
                "content": content,
                "reply_to_id": str(reply_to_id) if reply_to_id else None,
                "reaction_count": int(reaction_count or 0),
                "is_edited": bool(is_edited),
                "_timestamp": timestamp,
            }
        )

    ordered = sorted(
        conversations.values(),
        key=lambda item: (
            item["messages"][0]["_timestamp"],
            item["conversation_id"],
        ),
    )
    total_messages = sum(len(item["messages"]) for item in ordered)
    consumed_messages = 0
    records: dict[str, list[dict[str, Any]]] = {split: [] for split in SPLITS}
    exported_conversations = 0
    exported_messages = 0

    for conversation in ordered:
        messages = conversation["messages"]
        midpoint = (consumed_messages + len(messages) / 2) / max(total_messages, 1)
        split = _split_for_position(
            midpoint,
            train_fraction=config.train_fraction,
            validation_fraction=config.validation_fraction,
        )
        consumed_messages += len(messages)
        chunks = _windows(
            messages,
            max_messages=config.max_messages,
            overlap_messages=config.overlap_messages,
            min_messages=config.min_messages,
        )
        if not chunks:
            continue
        exported_conversations += 1
        exported_messages += len(messages)
        for window_index, chunk in enumerate(chunks):
            clean_messages = [
                {key: value for key, value in message.items() if key != "_timestamp"}
                for message in chunk
            ]
            records[split].append(
                {
                    "schema_version": SCHEMA_VERSION,
                    "window_id": (
                        f"{conversation['conversation_id']}:{window_index:04d}"
                    ),
                    "conversation_id": conversation["conversation_id"],
                    "channel": conversation["channel"],
                    "messages": clean_messages,
                }
            )

    output_dir.mkdir(parents=True, exist_ok=True)
    for split in SPLITS:
        _write_jsonl(output_dir / f"{split}.jsonl", records[split])

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "format": "gchat-conversation-windows",
        "config": asdict(config),
        "speakers": speakers,
        "counts": {
            "conversations": exported_conversations,
            "messages": exported_messages,
            "windows": sum(len(items) for items in records.values()),
            "split_windows": {
                split: len(records[split])
                for split in SPLITS
            },
        },
    }
    manifest_path = output_dir / "manifest.json"
    manifest_tmp = output_dir / "manifest.json.tmp"
    manifest_tmp.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest_tmp.replace(manifest_path)

    return TrainingExportSummary(
        conversations=exported_conversations,
        messages=exported_messages,
        windows=sum(len(items) for items in records.values()),
        split_windows={split: len(records[split]) for split in SPLITS},
        output_dir=str(output_dir),
    )
