from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

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


_NAME_CHANGE_PATTERNS = (
    re.compile(r"^(?P<actor>.+?) named the group (?P<name>.+?)\.?$", re.IGNORECASE),
    re.compile(r"^(?P<actor>.+?) renamed the group(?: to)? (?P<name>.+?)\.?$", re.IGNORECASE),
    re.compile(r"^(?P<actor>.+?) changed the group name(?: to)? (?P<name>.+?)\.?$", re.IGNORECASE),
    re.compile(r"^(?P<actor>.+?) changed the chat name(?: to)? (?P<name>.+?)\.?$", re.IGNORECASE),
    re.compile(r"^(?P<actor>.+?) set the chat name(?: to)? (?P<name>.+?)\.?$", re.IGNORECASE),
    re.compile(r"^(?P<actor>.+?) changed the group name from .+? to (?P<name>.+?)\.?$", re.IGNORECASE),
    re.compile(r"^(?P<actor>.+?) changed the chat name from .+? to (?P<name>.+?)\.?$", re.IGNORECASE),
    re.compile(r"^(?P<actor>.+?) updated the group name(?: to)? (?P<name>.+?)\.?$", re.IGNORECASE),
)


def _extract_group_name_change(content: str) -> str | None:
    text = normalize_whitespace(content)
    for pattern in _NAME_CHANGE_PATTERNS:
        match = pattern.match(text)
        if match:
            return match.group("name").strip().rstrip(".!?").strip()
    return None


def _reaction_count(node) -> int:
    reactions = node.find_all(
        lambda tag: bool(
            tag.get("class")
            and any("reaction" in str(class_name).lower() for class_name in tag.get("class", []))
        )
    )
    if reactions:
        return len(reactions)

    text = _text(node).lower()
    match = re.search(r"(\d+)\s+reactions?", text)
    if match:
        return int(match.group(1))
    return 0


def _timestamp_from_children(children) -> datetime | None:
    for child in reversed(children):
        text = _text(child)
        if not text:
            continue
        try:
            return _parse_timestamp(text)
        except ValueError:
            continue
    return None


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
    rename_events: list[tuple[datetime, int, str]] = []
    event_index = 0

    for html_file in sorted(chat_dir.glob("message_*.html")):
        soup = BeautifulSoup(html_file.read_text(encoding="utf-8"), "html.parser")
        for container in soup.select("div.pam._3-95._2pi0._2lej.uiBoxWhite.noborder"):
            event_index += 1
            children = container.find_all(True, recursive=False)
            if len(children) < 2:
                continue
            author = _text(children[0])
            content, attachment_count = _content(children[1])
            ts = _timestamp_from_children(children)
            if not ts:
                continue
            raw_id = author
            person = people_by_key.setdefault(
                raw_id,
                PersonSeed(platform="facebook", raw_id=raw_id, display_name=author),
            )
            if rename := _extract_group_name_change(content or _text(container)):
                rename_events.append((ts, event_index, rename))
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
                    reaction_count=_reaction_count(container),
                )
            )

    name_changes: list[NameChangeSeed] = []
    last_name: str | None = None
    for ts, _, new_name in sorted(rename_events, key=lambda item: (item[0], item[1], item[2].casefold())):
        if new_name == last_name:
            continue
        name_changes.append(
            NameChangeSeed(
                source_name=source.name,
                platform="facebook",
                entity_kind="channel",
                entity_raw_id=channel.raw_id,
                previous_name=last_name,
                new_name=new_name,
                ts=ts,
                kind="channel-title-change",
                payload_json=None,
            )
        )
        last_name = new_name

    return FacebookThread(
        source=source,
        channel=channel,
        messages=messages,
        people=list(people_by_key.values()),
        name_changes=name_changes,
    )
