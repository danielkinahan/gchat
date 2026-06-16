from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup

from .models import ChannelSeed, MessageSeed, NameChangeSeed, PersonSeed, SourceSeed

_DISCORD_EPOCH_MS = 1420070400000
_REPLY_ID_RE = re.compile(r"scrollToMessage\(event, ['\"](?P<id>\d+)['\"]\)")
_CHANNEL_RENAME_PATTERNS = (
    re.compile(r"changed the channel name to (?P<name>.+)$", re.IGNORECASE),
    re.compile(r"changed the channel name:\s*(?P<name>.+)$", re.IGNORECASE),
)
_NICKNAME_RENAME_PATTERNS = (
    re.compile(r"changed their nickname to (?P<name>.+)$", re.IGNORECASE),
    re.compile(r"changed his nickname to (?P<name>.+)$", re.IGNORECASE),
    re.compile(r"changed her nickname to (?P<name>.+)$", re.IGNORECASE),
    re.compile(r"set their nickname to (?P<name>.+)$", re.IGNORECASE),
)

# Patterns to detect system-like changes (photo/avatar, emoji/poll)
_PHOTO_CHANGE_PATTERNS = (
    re.compile(
        r"(?P<actor>.+?) changed the (?:server|group|channel) (?:icon|photo|avatar)(?: to)?\.?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?P<actor>.+?) updated the (?:server|group|channel) (?:icon|photo|avatar)\.?$",
        re.IGNORECASE,
    ),
)

_EMOJI_SET_PATTERNS = (
    re.compile(
        r"(?P<actor>.+?) added (?:the )?(?:emoji|reaction) (?P<emoji>.+)\.?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?P<actor>.+?) set the group emoji to (?P<emoji>.+)\.?$", re.IGNORECASE
    ),
)

_POLL_PATTERNS = (
    re.compile(r"(?P<actor>.+?) created a poll(?:[:\s].+)?$", re.IGNORECASE),
)


@dataclass(frozen=True)
class DiscordExport:
    source: SourceSeed
    channel: ChannelSeed
    messages: list[MessageSeed]
    people: list[PersonSeed]
    name_changes: list[NameChangeSeed]


def _strip_surrounding_quotes(value: str) -> str:
    pairs = {
        '"': '"',
        "'": "'",
        "“": "”",
        "‘": "’",
    }
    stripped = value.strip()
    while (
        len(stripped) >= 2
        and stripped[0] in pairs
        and stripped[-1] == pairs[stripped[0]]
    ):
        stripped = stripped[1:-1].strip()
    return stripped


def _discord_snowflake_timestamp(message_id: str) -> datetime:
    snowflake = int(message_id)
    timestamp_ms = (snowflake >> 22) + _DISCORD_EPOCH_MS
    return datetime.fromtimestamp(timestamp_ms / 1000.0, tz=timezone.utc).replace(
        tzinfo=None
    )


def _replace_emoji_with_alt_text(node) -> None:
    for emoji in node.select("img[alt]"):
        alt = emoji.get("alt")
        if isinstance(alt, str) and alt.strip():
            emoji.replace_with(alt)


def _text_content(node) -> str:
    clone = BeautifulSoup(str(node), "html.parser")
    _replace_emoji_with_alt_text(clone)
    return clone.get_text("\n", strip=True)


def _preamble_names(soup: BeautifulSoup, path: Path) -> tuple[str, str]:
    entries = [
        entry.get_text(" ", strip=True) for entry in soup.select(".preamble__entry")
    ]
    guild_name = entries[0] if entries else path.parent.name
    channel_text = entries[1] if len(entries) > 1 else path.stem
    channel_name = channel_text.split(" / ")[-1].strip().lstrip("#") or path.stem
    return guild_name or path.parent.name, channel_name


def _local_asset_path(raw_value: str, html_path: Path, export_root: Path) -> str | None:
    value = raw_value.strip()
    if not value or value.startswith("data:"):
        return None
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"}:
        return value
    if parsed.scheme == "file":
        candidate = Path(unquote(parsed.path)).resolve()
    elif parsed.scheme:
        return value
    else:
        candidate = (html_path.parent / unquote(parsed.path)).resolve()
    try:
        return candidate.relative_to(export_root.resolve()).as_posix()
    except ValueError:
        return candidate.as_posix()


def _reaction_data(
    message_div, html_path: Path, export_root: Path
) -> tuple[int, str | None, str | None]:
    total_count = 0
    summary_parts: list[str] = []
    details: list[dict[str, object]] = []

    for reaction in message_div.select(".chatlog__reaction"):
        count_text = reaction.select_one(".chatlog__reaction-count")
        try:
            count = (
                int(count_text.get_text(strip=True)) if count_text is not None else 1
            )
        except ValueError:
            count = 1
        emoji_image = reaction.select_one("img.chatlog__emoji")
        emoji_name = ""
        image_url: str | None = None
        if emoji_image is not None:
            emoji_name = str(emoji_image.get("alt") or "").strip()
            src = emoji_image.get("src")
            if isinstance(src, str) and src.strip():
                image_url = _local_asset_path(src, html_path, export_root)
        if not emoji_name:
            emoji_name = str(
                reaction.get("title") or reaction.get_text(" ", strip=True)
            ).strip()
        if not emoji_name:
            continue
        total_count += count
        summary_parts.append(f"{emoji_name}×{count}")
        details.append(
            {
                "name": emoji_name,
                "count": count,
                "emoji_id": None,
                "image_url": image_url,
                "code": str(reaction.get("title") or "").strip() or None,
                "is_animated": False,
            }
        )

    return (
        total_count,
        " ".join(summary_parts) if summary_parts else None,
        json.dumps(details, ensure_ascii=False) if details else None,
    )


def _reply_to_id(message_div) -> str | None:
    reply_link = message_div.select_one(".chatlog__reply-link")
    if reply_link is None:
        return None
    onclick = reply_link.get("onclick")
    if not isinstance(onclick, str):
        return None
    match = _REPLY_ID_RE.search(onclick)
    return match.group("id") if match else None


def _attachment_data(
    message_div, html_path: Path, export_root: Path
) -> tuple[int, str | None]:
    attachments = message_div.select(".chatlog__attachment")
    preview: str | None = None
    for attachment in attachments:
        candidates: list[tuple[str, str]] = [
            ("img.chatlog__attachment-media", "src"),
            ("video.chatlog__attachment-media source", "src"),
            ("audio.chatlog__attachment-media source", "src"),
            (".chatlog__attachment-generic-name a", "href"),
            ("a", "href"),
        ]
        for selector, attribute in candidates:
            node = attachment.select_one(selector)
            if node is None:
                continue
            value = node.get(attribute)
            if not isinstance(value, str) or not value.strip():
                continue
            preview = _local_asset_path(value, html_path, export_root)
            if preview:
                return len(attachments), preview
    return len(attachments), preview


def _person_from_message(
    message_div,
    people_by_key: dict[str, PersonSeed],
    previous_person: PersonSeed | None,
) -> PersonSeed:
    author = message_div.select_one(".chatlog__author[data-user-id]")
    if author is None:
        author = message_div.select_one(
            ".chatlog__system-notification-author[data-user-id]"
        )
    if author is None:
        return previous_person or people_by_key.setdefault(
            "system",
            PersonSeed(platform="discord", raw_id="system", display_name="System"),
        )

    raw_id = str(
        author.get("data-user-id") or author.get_text(" ", strip=True) or "unknown"
    )
    display_name = author.get_text(" ", strip=True) or raw_id
    return people_by_key.setdefault(
        raw_id,
        PersonSeed(platform="discord", raw_id=raw_id, display_name=display_name),
    )


def _message_content(message_div) -> str:
    system_content = message_div.select_one(".chatlog__system-notification-content")
    if system_content is not None:
        return _text_content(system_content)

    content = message_div.select_one(".chatlog__content")
    if content is not None:
        return _text_content(content)

    markdown = message_div.select_one(".chatlog__markdown")
    if markdown is not None:
        return _text_content(markdown)

    return ""


def _extract_name_change(
    content: str,
    person: PersonSeed,
    source: SourceSeed,
    channel_seed: ChannelSeed,
    ts: datetime,
) -> list[NameChangeSeed]:
    stripped = content.strip()
    changes: list[NameChangeSeed] = []
    for pattern in _CHANNEL_RENAME_PATTERNS:
        match = pattern.search(stripped)
        if not match:
            continue
        new_name = _strip_surrounding_quotes(match.group("name"))
        if new_name:
            changes.append(
                NameChangeSeed(
                    source_name=source.name,
                    platform="discord",
                    entity_kind="channel",
                    entity_raw_id=channel_seed.raw_id,
                    previous_name=None,
                    new_name=new_name,
                    ts=ts,
                    kind="channel-name-change",
                    payload_json=json.dumps(
                        {
                            "actor_name": person.display_name,
                            "actor_raw_id": person.raw_id,
                        },
                        ensure_ascii=False,
                    ),
                )
            )
        return changes
    for pattern in _NICKNAME_RENAME_PATTERNS:
        match = pattern.search(stripped)
        if not match:
            continue
        new_name = _strip_surrounding_quotes(match.group("name"))
        if new_name:
            changes.append(
                NameChangeSeed(
                    source_name=source.name,
                    platform="discord",
                    entity_kind="person",
                    entity_raw_id=person.raw_id,
                    previous_name=None,
                    new_name=new_name,
                    ts=ts,
                    kind="nickname-change",
                    payload_json=json.dumps(
                        {
                            "actor_name": person.display_name,
                            "actor_raw_id": person.raw_id,
                        },
                        ensure_ascii=False,
                    ),
                )
            )
        return changes

    # detect photo/avatar changes
    for pattern in _PHOTO_CHANGE_PATTERNS:
        match = pattern.search(stripped)
        if match:
            actor = (
                match.group("actor").strip()
                if match.groupdict().get("actor")
                else person.display_name
            )
            changes.append(
                NameChangeSeed(
                    source_name=source.name,
                    platform="discord",
                    entity_kind="channel",
                    entity_raw_id=channel_seed.raw_id,
                    previous_name=None,
                    new_name="photo-changed",
                    ts=ts,
                    kind="channel-photo-change",
                    payload_json=json.dumps({"actor_name": actor}, ensure_ascii=False),
                )
            )
            return changes

    # detect emoji/poll changes
    for pattern in _EMOJI_SET_PATTERNS:
        match = pattern.search(stripped)
        if match:
            actor = (
                match.group("actor").strip()
                if match.groupdict().get("actor")
                else person.display_name
            )
            emoji = match.groupdict().get("emoji") or ""
            changes.append(
                NameChangeSeed(
                    source_name=source.name,
                    platform="discord",
                    entity_kind="channel",
                    entity_raw_id=channel_seed.raw_id,
                    previous_name=None,
                    new_name=(emoji or "emoji-changed"),
                    ts=ts,
                    kind="channel-emoji-change",
                    payload_json=json.dumps(
                        {"actor_name": actor, "emoji": emoji}, ensure_ascii=False
                    ),
                )
            )
            return changes

    for pattern in _POLL_PATTERNS:
        match = pattern.search(stripped)
        if match:
            actor = (
                match.group("actor").strip()
                if match.groupdict().get("actor")
                else person.display_name
            )
            changes.append(
                NameChangeSeed(
                    source_name=source.name,
                    platform="discord",
                    entity_kind="channel",
                    entity_raw_id=channel_seed.raw_id,
                    previous_name=None,
                    new_name="poll-created",
                    ts=ts,
                    kind="channel-poll",
                    payload_json=json.dumps({"actor_name": actor}, ensure_ascii=False),
                )
            )
            return changes

    return changes


def normalize_export(path: Path) -> DiscordExport:
    export_root = path.parent
    while export_root.name and export_root.name != "discord":
        export_root = export_root.parent
    soup = BeautifulSoup(
        path.read_text(encoding="utf-8", errors="ignore"), "html.parser"
    )

    guild_name, channel_name = _preamble_names(soup, path)
    source = SourceSeed(platform="discord", name=f"Discord: {guild_name}")
    channel_seed = ChannelSeed(
        source_name=source.name,
        raw_id=path.stem,
        name=channel_name,
        theme_name=channel_name,
    )

    people_by_key: dict[str, PersonSeed] = {}
    messages: list[MessageSeed] = []
    name_changes: list[NameChangeSeed] = []
    previous_person: PersonSeed | None = None

    for message_div in soup.select(".chatlog__message-container[data-message-id]"):
        message_id = str(message_div.get("data-message-id") or "").strip()
        if not message_id:
            continue
        ts = _discord_snowflake_timestamp(message_id)
        person = _person_from_message(message_div, people_by_key, previous_person)
        previous_person = person
        content = _message_content(message_div)
        attachment_count, attachment_preview = _attachment_data(
            message_div, path, export_root
        )
        reaction_count, reaction_summary, reaction_details_json = _reaction_data(
            message_div, path, export_root
        )
        is_edited = message_div.select_one(".chatlog__edited-timestamp") is not None

        name_changes.extend(
            _extract_name_change(content, person, source, channel_seed, ts)
        )

        messages.append(
            MessageSeed(
                id=message_id,
                source_name=source.name,
                channel_raw_id=channel_seed.raw_id,
                channel_name=channel_seed.name,
                theme_name=channel_seed.theme_name,
                person=person,
                ts=ts,
                content=content,
                reply_to_id=_reply_to_id(message_div),
                attachment_count=attachment_count,
                attachment_preview=attachment_preview,
                reaction_count=reaction_count,
                reaction_summary=reaction_summary,
                reaction_details_json=reaction_details_json,
                is_edited=is_edited,
            )
        )

    return DiscordExport(
        source=source,
        channel=channel_seed,
        messages=messages,
        people=list(people_by_key.values()),
        name_changes=name_changes,
    )
