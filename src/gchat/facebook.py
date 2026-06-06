from __future__ import annotations

import json
import re
from collections import Counter
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


def _name_key(value: str) -> str:
    return " ".join(value.split()).casefold()


def _content(node) -> tuple[str, int, str | None]:
    # In Facebook HTML exports, reactions are embedded as <ul class="_tqp"><li>...,
    # nested under the message body; remove them so message text stays clean.
    for reaction_list in node.select("ul._tqp"):
        reaction_list.decompose()

    text = _text(node)
    if text:
        return text, 0, None

    attachments = node.find_all(["img", "a"], recursive=True)
    if attachments:
        labels: list[str] = []
        preview: str | None = None
        for attachment in attachments:
            if attachment.name == "img":
                labels.append(attachment.get("alt") or "sticker")
                if preview is None:
                    src = attachment.get("src")
                    if isinstance(src, str) and src.strip():
                        preview = src.strip()
            else:
                href = attachment.get("href") or ""
                labels.append(Path(href).name if href else "attachment")
                if preview is None and href:
                    preview = str(href).strip()
        return fix_facebook_mojibake(normalize_whitespace(" ".join(labels))), len(attachments), preview
    return "", 0, None


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


def _extract_group_name_change(content: str) -> tuple[str, str | None] | None:
    text = normalize_whitespace(content)
    for pattern in _NAME_CHANGE_PATTERNS:
        match = pattern.match(text)
        if match:
            return (
                match.group("name").strip().rstrip(".!?").strip(),
                match.group("actor").strip() if match.group("actor") else None,
            )
    return None


_NICKNAME_SET_PATTERNS = (
    re.compile(
        r"^(?P<actor>.+?) (?:set|changed) the nickname for (?P<target>.+?) to (?P<nickname>.+?)\.?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?P<actor>.+?) (?:set|changed) your nickname to (?P<nickname>.+?)\.?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?P<actor>.+?) (?:set|changed) (?:his|her|their) own nickname to (?P<nickname>.+?)\.?$",
        re.IGNORECASE,
    ),
)

_NICKNAME_CLEAR_PATTERNS = (
    re.compile(
        r"^(?P<actor>.+?) cleared the nickname for (?P<target>.+?)\.?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?P<actor>.+?) cleared your nickname\.?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?P<actor>.+?) cleared (?:his|her|their) own nickname\.?$",
        re.IGNORECASE,
    ),
)


def _extract_nickname_change(content: str) -> tuple[str, str, str | None, bool] | None:
    text = normalize_whitespace(content)
    for pattern in _NICKNAME_SET_PATTERNS:
        match = pattern.match(text)
        if not match:
            continue
        actor_name = match.group("actor").strip()
        target_name = match.groupdict().get("target")
        if target_name:
            target_name = target_name.strip()
        elif "your nickname" in text.casefold():
            target_name = "You"
        else:
            target_name = actor_name
        nickname = match.group("nickname").strip().rstrip(".!?").strip()
        if not nickname:
            return None
        return target_name, nickname, actor_name, False

    for pattern in _NICKNAME_CLEAR_PATTERNS:
        match = pattern.match(text)
        if not match:
            continue
        actor_name = match.group("actor").strip()
        target_name = match.groupdict().get("target")
        if target_name:
            target_name = target_name.strip()
        elif "your nickname" in text.casefold():
            target_name = "You"
        else:
            target_name = actor_name
        return target_name, "(cleared)", actor_name, True

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

    reaction_entries = [li for ul in node.select("ul._tqp") for li in ul.find_all("li", recursive=False)]
    if reaction_entries:
        return len(reaction_entries)

    return 0


def _extract_reaction_emoji(text: str) -> str:
    value = normalize_whitespace(text)
    if not value:
        return ""
    prefix: list[str] = []
    for ch in value:
        if ch.isalnum():
            break
        if ch.isspace():
            if prefix:
                break
            continue
        prefix.append(ch)
    return "".join(prefix).strip()


def _reaction_summary(node) -> str | None:
    counts: Counter[str] = Counter()
    for li in node.select("ul._tqp > li"):
        emoji = _extract_reaction_emoji(_text(li))
        if emoji:
            counts[emoji] += 1
    if not counts:
        return None
    return " ".join(f"{emoji}×{count}" for emoji, count in counts.most_common())


_REACTION_EMOJIS = {"👍", "❤️", "❤", "😂", "😮", "😢", "😡", "🥰"}


def _looks_like_reaction_message(text: str) -> bool:
    return normalize_whitespace(text) in _REACTION_EMOJIS


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
    alias_to_raw_id: dict[str, str] = {}
    messages: list[MessageSeed] = []
    seen_message_ids: set[str] = set()
    rename_events: list[tuple[datetime, int, str, str | None, str | None]] = []
    nickname_events: list[tuple[datetime, int, str, str, str | None, str | None, bool]] = []
    event_index = 0

    for html_file in sorted(chat_dir.glob("message_*.html")):
        soup = BeautifulSoup(html_file.read_text(encoding="utf-8"), "html.parser")
        for container in soup.select("div.pam._3-95._2pi0._2lej.uiBoxWhite.noborder"):
            event_index += 1
            children = container.find_all(True, recursive=False)
            if len(children) < 2:
                continue
            author = _text(children[0])
            reaction_count = _reaction_count(container)
            reaction_summary = _reaction_summary(container)
            content, attachment_count, attachment_preview = _content(children[1])
            ts = _timestamp_from_children(children)
            if not ts:
                continue
            raw_id = alias_to_raw_id.get(_name_key(author), author)
            person = people_by_key.setdefault(
                raw_id,
                PersonSeed(platform="facebook", raw_id=raw_id, display_name=author),
            )
            alias_to_raw_id.setdefault(_name_key(author), raw_id)
            if rename_data := _extract_group_name_change(content or _text(container)):
                rename, actor_name = rename_data
                actor_raw_id = (
                    alias_to_raw_id.get(_name_key(actor_name))
                    if actor_name
                    else None
                )
                rename_events.append((ts, event_index, rename, actor_name, actor_raw_id))
            if nickname_data := _extract_nickname_change(content or _text(container)):
                target_name, nickname, actor_name, is_cleared = nickname_data
                target_raw_id = alias_to_raw_id.get(_name_key(target_name), target_name)
                people_by_key.setdefault(
                    target_raw_id,
                    PersonSeed(
                        platform="facebook",
                        raw_id=target_raw_id,
                        display_name=target_name,
                    ),
                )
                alias_to_raw_id.setdefault(_name_key(target_name), target_raw_id)
                actor_raw_id = (
                    alias_to_raw_id.get(_name_key(actor_name))
                    if actor_name
                    else None
                )
                nickname_events.append(
                    (
                        ts,
                        event_index,
                        target_raw_id,
                        nickname,
                        actor_name,
                        actor_raw_id,
                        is_cleared,
                    )
                )
                if not is_cleared:
                    alias_to_raw_id.setdefault(_name_key(nickname), target_raw_id)
            message_id = hash_message([author, ts.isoformat(), content])
            if message_id in seen_message_ids:
                continue
            seen_message_ids.add(message_id)
            if not reaction_count and _looks_like_reaction_message(content):
                reaction_count = 1
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
                    attachment_preview=attachment_preview,
                    reaction_count=reaction_count,
                    reaction_summary=reaction_summary,
                )
            )

    name_changes: list[NameChangeSeed] = []
    last_name: str | None = None
    for ts, _, new_name, actor_name, actor_raw_id in sorted(rename_events, key=lambda item: (item[0], item[1], item[2].casefold())):
        if new_name == last_name:
            continue
        payload: dict[str, str] = {}
        if actor_name:
            payload["actor_name"] = actor_name
        if actor_raw_id:
            payload["actor_raw_id"] = actor_raw_id
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
                payload_json=json.dumps(payload, ensure_ascii=False) if payload else None,
            )
        )
        last_name = new_name

    nickname_state: dict[str, str | None] = {}
    for ts, _, target_raw_id, new_nickname, actor_name, actor_raw_id, is_cleared in sorted(
        nickname_events,
        key=lambda item: (item[0], item[1], item[2].casefold(), item[3].casefold()),
    ):
        previous_nickname = nickname_state.get(target_raw_id)
        if is_cleared and previous_nickname is None:
            continue
        if not is_cleared and previous_nickname == new_nickname:
            continue
        name_changes.append(
            NameChangeSeed(
                source_name=source.name,
                platform="facebook",
                entity_kind="person",
                entity_raw_id=target_raw_id,
                previous_name=previous_nickname,
                new_name=new_nickname,
                ts=ts,
                kind="nickname-change",
                payload_json=json.dumps(
                    {
                        "chatId": channel.raw_id,
                        "actor_name": actor_name,
                        "actor_raw_id": actor_raw_id,
                    },
                    ensure_ascii=False,
                ),
            )
        )
        nickname_state[target_raw_id] = None if is_cleared else new_nickname

    return FacebookThread(
        source=source,
        channel=channel,
        messages=messages,
        people=list(people_by_key.values()),
        name_changes=name_changes,
    )
