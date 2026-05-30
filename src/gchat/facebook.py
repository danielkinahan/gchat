from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from bs4 import BeautifulSoup

from .models import MessageSeed, NameChangeSeed, PersonSeed, SourceSeed, ChannelSeed
from .util import fix_facebook_mojibake, hash_message, message_counts, normalize_whitespace


@dataclass(frozen=True)
class FacebookThread:
    source: SourceSeed
    channel: ChannelSeed
    messages: list[MessageSeed]
    people: list[PersonSeed]
    name_changes: list[NameChangeSeed]


def _parse_timestamp(text: str) -> datetime:
    return datetime.strptime(text, "%d %b %Y, %H:%M")


def _text(node) -> str:
    return fix_facebook_mojibake(normalize_whitespace(node.get_text(" ", strip=True)))


def _content(node) -> tuple[str, int]:
    text = _text(node)
    if text:
        return text, 0

    attachments = node.find_all(["img", "a"], recursive=True)
    if attachments:
        labels: list[str] = []
        for attachment in attachments:
            if attachment.name == "img":
                labels.append(attachment.get("alt") or "sticker")
            else:
                href = attachment.get("href") or ""
                labels.append(Path(href).name if href else "attachment")
        return fix_facebook_mojibake(normalize_whitespace(" ".join(labels))), len(attachments)
    return "", 0


def normalize_chat(chat_dir: Path) -> FacebookThread:
    source = SourceSeed(platform="facebook", name=f"Facebook: {chat_dir.name}")
    channel = ChannelSeed(
        source_name=source.name,
        raw_id=chat_dir.name,
        name=chat_dir.name,
        theme_name=chat_dir.name,
    )
    people_by_key: dict[str, PersonSeed] = {}
    messages: list[MessageSeed] = []
    seen_message_ids: set[str] = set()

    for html_file in sorted(chat_dir.glob("message_*.html")):
        soup = BeautifulSoup(html_file.read_text(encoding="utf-8"), "html.parser")
        for container in soup.select("div.pam._3-95._2pi0._2lej.uiBoxWhite.noborder"):
            children = container.find_all(True, recursive=False)
            if len(children) != 3:
                continue
            author = _text(children[0])
            content, attachment_count = _content(children[1])
            timestamp_text = _text(children[2])
            if not author or not timestamp_text:
                continue
            ts = _parse_timestamp(timestamp_text)
            raw_id = author
            person = people_by_key.setdefault(
                raw_id,
                PersonSeed(platform="facebook", raw_id=raw_id, display_name=author),
            )
            message_id = hash_message([author, ts.isoformat(), content])
            if message_id in seen_message_ids:
                continue
            seen_message_ids.add(message_id)
            messages.append(
                MessageSeed(
                    id=message_id,
                    source_name=source.name,
                    channel_raw_id=channel.raw_id,
                    channel_name=channel.name,
                    theme_name=channel.theme_name,
                    person=person,
                    ts=ts,
                    content=content,
                    attachment_count=attachment_count,
                    reaction_count=0,
                )
            )

    return FacebookThread(
        source=source,
        channel=channel,
        messages=messages,
        people=list(people_by_key.values()),
        name_changes=[],
    )
