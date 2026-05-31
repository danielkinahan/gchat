from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .models import MessageSeed, NameChangeSeed, PersonSeed, SourceSeed, ChannelSeed
from .util import message_counts, parse_iso_datetime


@dataclass(frozen=True)
class DiscordExport:
    source: SourceSeed
    channel: ChannelSeed
    messages: list[MessageSeed]
    people: list[PersonSeed]
    name_changes: list[NameChangeSeed]


def _reaction_count(reactions: object) -> int:
    if not isinstance(reactions, list):
        return 0
    total = 0
    for reaction in reactions:
        if isinstance(reaction, dict):
            total += int(reaction.get("count", 1))
        else:
            total += 1
    return total


def _reaction_summary(reactions: object) -> str | None:
    if not isinstance(reactions, list):
        return None
    parts: list[str] = []
    for reaction in reactions:
        if not isinstance(reaction, dict):
            continue
        emoji = reaction.get("emoji")
        emoji_name = ""
        if isinstance(emoji, dict):
            emoji_name = str(emoji.get("name") or "").strip()
        if not emoji_name:
            emoji_name = str(reaction.get("emojiName") or reaction.get("name") or "").strip()
        if not emoji_name:
            continue
        count = int(reaction.get("count", 1) or 1)
        parts.append(f"{emoji_name}×{count}")
    return " ".join(parts) if parts else None


def _reply_to_id(message: dict) -> str | None:
    referenced = message.get("referencedMessage")
    if isinstance(referenced, dict) and referenced.get("id"):
        return str(referenced["id"])
    reference = message.get("messageReference")
    if isinstance(reference, dict) and reference.get("messageId"):
        return str(reference["messageId"])
    return None


def _attachment_preview(message: dict) -> str | None:
    attachments = message.get("attachments")
    if not isinstance(attachments, list):
        return None
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        url = attachment.get("url")
        if isinstance(url, str) and url.strip():
            return url.strip()
        filename = attachment.get("fileName")
        if isinstance(filename, str) and filename.strip():
            return filename.strip()
    return None


def normalize_export(path: Path) -> DiscordExport:
    payload = json.loads(path.read_text(encoding="utf-8"))
    guild = payload["guild"]
    channel = payload["channel"]
    source = SourceSeed(platform="discord", name=f"Discord: {guild['name']}")
    channel_seed = ChannelSeed(
        source_name=source.name,
        raw_id=str(channel["id"]),
        name=str(channel.get("name") or channel["id"]),
        theme_name=str(channel.get("name") or channel["id"]),
    )
    people_by_key: dict[str, PersonSeed] = {}
    messages: list[MessageSeed] = []
    name_changes: list[NameChangeSeed] = []

    for message in payload.get("messages", []):
        author = message.get("author") or {}
        raw_id = str(author.get("id") or author.get("name") or "unknown")
        display_name = str(author.get("nickname") or author.get("global_name") or author.get("name") or raw_id)
        person = people_by_key.setdefault(
            raw_id,
            PersonSeed(platform="discord", raw_id=raw_id, display_name=display_name),
        )
        content = str(message.get("content") or "")
        ts = parse_iso_datetime(str(message["timestamp"]))
        attachment_count = len(message.get("attachments") or [])
        reaction_count = _reaction_count(message.get("reactions"))
        mtype = str(message.get("type"))
        if mtype == "9":
            name_changes.append(
                NameChangeSeed(
                    source_name=source.name,
                    platform="discord",
                    entity_kind="person",
                    entity_raw_id=raw_id,
                    previous_name=None,
                    new_name=display_name,
                    ts=ts,
                    kind="nickname-change",
                )
            )
        elif mtype == "8":
            name_changes.append(
                NameChangeSeed(
                    source_name=source.name,
                    platform="discord",
                    entity_kind="channel",
                    entity_raw_id=channel_seed.raw_id,
                    previous_name=None,
                    new_name=channel_seed.name,
                    ts=ts,
                    kind="channel-name-change",
                    payload_json=json.dumps({"actor_name": display_name}, ensure_ascii=False),
                )
            )
        messages.append(
            MessageSeed(
                id=str(message["id"]),
                source_name=source.name,
                channel_raw_id=channel_seed.raw_id,
                channel_name=channel_seed.name,
                theme_name=channel_seed.theme_name,
                person=person,
                ts=ts,
                content=content,
                reply_to_id=_reply_to_id(message),
                attachment_count=attachment_count,
                attachment_preview=_attachment_preview(message),
                reaction_count=reaction_count,
                reaction_summary=_reaction_summary(message.get("reactions")),
            )
        )

    return DiscordExport(
        source=source,
        channel=channel_seed,
        messages=messages,
        people=list(people_by_key.values()),
        name_changes=name_changes,
    )
