from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

from .models import (
    ChannelSeed,
    MemberEventSeed,
    MessageSeed,
    NameChangeSeed,
    PersonSeed,
    SourceSeed,
)
from .util import (
    fix_facebook_mojibake,
    hash_message,
    message_counts,
    normalize_whitespace,
)


@dataclass(frozen=True)
class FacebookThread:
    source: SourceSeed
    channel: ChannelSeed
    messages: list[MessageSeed]
    people: list[PersonSeed]
    name_changes: list[NameChangeSeed]
    member_events: list[MemberEventSeed]


_MEMBER_EVENT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # "X added Y, Z to the group"
    (
        re.compile(
            r"^(?P<actor>.+?) added (?P<targets>.+?) to the (?:group|chat)\.?$",
            re.IGNORECASE,
        ),
        "added",
    ),
    # "X removed Y from the group"
    (
        re.compile(
            r"^(?P<actor>.+?) removed (?P<targets>.+?) from the (?:group|chat)\.?$",
            re.IGNORECASE,
        ),
        "removed",
    ),
    # "X left the group" - the actor is also the target
    (
        re.compile(
            r"^(?P<actor>.+?) left the (?:group|chat|conversation)\.?$",
            re.IGNORECASE,
        ),
        "left",
    ),
)


def _split_target_names(targets: str) -> list[str]:
    cleaned = re.sub(r"\s+and\s+", ", ", targets, flags=re.IGNORECASE)
    return [part.strip().rstrip(".") for part in cleaned.split(",") if part.strip()]


def _extract_member_event(content: str) -> tuple[str, str, list[str]] | None:
    text = normalize_whitespace(content)
    if not text:
        return None
    for pattern, kind in _MEMBER_EVENT_PATTERNS:
        match = pattern.match(text)
        if not match:
            continue
        actor = match.group("actor").strip()
        if kind == "left":
            return kind, actor, [actor]
        targets_text = match.groupdict().get("targets", "")
        targets = _split_target_names(targets_text) if targets_text else []
        if not targets:
            continue
        return kind, actor, targets
    return None


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
    attachments = []
    for attachment in node.find_all(["video", "audio", "img", "a"], recursive=True):
        if attachment.name in {"img", "a"}:
            parent = attachment.parent
            while parent is not None and getattr(parent, "name", None):
                if parent.name in {"video", "audio"}:
                    break
                parent = parent.parent
            else:
                parent = None
            if parent is not None:
                continue
        attachments.append(attachment)

    if attachments:
        labels: list[str] = []
        preview: str | None = None
        for attachment in attachments:
            attachment_preview = _attachment_preview(attachment)
            if preview is None and attachment_preview:
                preview = attachment_preview
            labels.append(_attachment_label(attachment, attachment_preview))

        # If the text contains a common removed-attachments placeholder, strip it
        # so we can treat the message as an attachment-only message.
        if text:
            cleaned = text
            removed_phrase = "One or more media attachments were removed"
            if removed_phrase.lower() in cleaned.lower():
                # remove that phrase and any surrounding punctuation
                cleaned = re.sub(
                    r"\bOne or more media attachments were removed\b[.]*",
                    "",
                    cleaned,
                    flags=re.IGNORECASE,
                ).strip()
                text = cleaned

        if (
            not text
            or _is_attachment_only_message(text)
            or _is_attachment_label_text(text, labels)
        ):
            return "", len(attachments), preview
        return text, len(attachments), preview

    # No attachment nodes found. Some exports put a placeholder text like
    # "X sent an attachment" or "One or more media attachments were removed" or
    # "X sent a link" while the actual link may still be in text or an <a> tag.
    if text:
        normalized = normalize_whitespace(text).strip()
        # Try to extract an explicit URL in the text (http/https)
        url_match = re.search(r"(https?://[^\s]+)", text)
        # Try to extract a local file path (messages/...) often used for FB attachments
        file_match = re.search(r"(messages/[^\s]+)", text)

        # Conditions where we should treat the message as attachment-only and prefer the URL/file
        is_placeholder = (
            _is_attachment_only_message(text)
            or re.search(r"sent (an |a )?(attachment|link)", normalized, re.IGNORECASE)
            or "one or more media attachments were removed" in normalized.casefold()
            or re.search(r"^https?://", normalized, re.IGNORECASE)
        )

        if is_placeholder:
            # Prefer explicit <a> href if present
            a = node.find("a")
            if a is not None:
                href = _attachment_preview(a)
                if href:
                    return "", 1, href
            # Prefer an inline URL if present
            if url_match:
                return "", 1, url_match.group(1).strip()
            # Prefer a local messages/ path if present
            if file_match:
                return "", 1, file_match.group(1).strip()
            # otherwise return as attachment-only with no preview
            return "", 0, None
    if text:
        return text, 0, None
    return "", 0, None


_NAME_CHANGE_PATTERNS = (
    re.compile(r"^(?P<actor>.+?) named the group (?P<name>.+?)\.?$", re.IGNORECASE),
    re.compile(
        r"^(?P<actor>.+?) renamed the group(?: to)? (?P<name>.+?)\.?$", re.IGNORECASE
    ),
    re.compile(
        r"^(?P<actor>.+?) changed the group name(?: to)? (?P<name>.+?)\.?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?P<actor>.+?) changed the chat name(?: to)? (?P<name>.+?)\.?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?P<actor>.+?) set the chat name(?: to)? (?P<name>.+?)\.?$", re.IGNORECASE
    ),
    re.compile(
        r"^(?P<actor>.+?) changed the group name from .+? to (?P<name>.+?)\.?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?P<actor>.+?) changed the chat name from .+? to (?P<name>.+?)\.?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?P<actor>.+?) updated the group name(?: to)? (?P<name>.+?)\.?$",
        re.IGNORECASE,
    ),
)

# Patterns for detecting photo/avatar updates and other system changes
_PHOTO_CHANGE_PATTERNS = (
    re.compile(
        r"^(?P<actor>.+?) changed the group (?:photo|avatar|picture)(?: to)?\.?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?P<actor>.+?) updated the group (?:photo|avatar|picture)\.?$", re.IGNORECASE
    ),
    re.compile(
        r"^(?P<actor>.+?) changed the profile picture for the group\.?$", re.IGNORECASE
    ),
)


_ATTACHMENT_ONLY_PATTERNS = (
    re.compile(
        r"^.+ sent an attachment\.?(?:\s+https?://\S+)?$",
        re.IGNORECASE,
    ),
    re.compile(r"^click for (video|audio):?$", re.IGNORECASE),
)

# Patterns for Facebook system messages that aren't rename/member/nickname events.
# These match photo changes, poll events, theme changes, and other UI actions.
_FACEBOOK_EXTRA_SYSTEM_PATTERNS = (
    re.compile(r"^.+ changed the (group|chat) photo\.?$", re.IGNORECASE),
    re.compile(r"^.+ updated the (group|cover) photo\.?$", re.IGNORECASE),
    re.compile(r"^.+ changed the group's? (icon|cover|avatar)\.?$", re.IGNORECASE),
    re.compile(r"^.+ created a poll[.:]", re.IGNORECASE),
    re.compile(r"^.+ (voted in|answered) (a )?poll", re.IGNORECASE),
    re.compile(r"^.+ set the (group )?theme to .+\.?$", re.IGNORECASE),
    re.compile(r"^.+ changed the (group )?theme\.?$", re.IGNORECASE),
    re.compile(r"^.+ turned off (link previews|notifications)\.?$", re.IGNORECASE),
    re.compile(r"^.+ turned on (link previews|notifications)\.?$", re.IGNORECASE),
    re.compile(r"^.+ pinned a message\.?$", re.IGNORECASE),
    re.compile(r"^.+ (started|ended) a (live )?video(?: call)?\.?$", re.IGNORECASE),
    re.compile(
        r"^.+ (started|answered|declined|missed) (an? )?(audio|video) call\.?$",
        re.IGNORECASE,
    ),
    re.compile(r"^.+ created (the|this) group\.?$", re.IGNORECASE),
)


def _is_facebook_system_message(content: str) -> bool:
    """Return True if this is a Facebook system/action message, not user text."""
    if not content:
        return False
    text = normalize_whitespace(content)
    if _extract_group_name_change(text):
        return True
    if _extract_member_event(text):
        return True
    if _extract_nickname_change(text):
        return True
    for pat in _FACEBOOK_EXTRA_SYSTEM_PATTERNS:
        if pat.match(text):
            return True
    return False


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


def _is_attachment_only_message(text: str) -> bool:
    normalized = normalize_whitespace(text)
    return any(pattern.match(normalized) for pattern in _ATTACHMENT_ONLY_PATTERNS)


def _attachment_preview(attachment) -> str | None:
    for attr in ("src", "data-src", "href"):
        value = attachment.get(attr)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _attachment_label(attachment, preview: str | None) -> str:
    if preview:
        return Path(preview).name or preview
    if attachment.name in {"video", "audio"}:
        return attachment.name
    return attachment.get("alt") or "attachment"


def _is_attachment_label_text(text: str, labels: list[str]) -> bool:
    normalized_text = normalize_whitespace(text).casefold()
    if not normalized_text:
        return False
    normalized_labels = [
        normalize_whitespace(label).casefold() for label in labels if label
    ]
    return (
        normalized_text in normalized_labels
        or normalized_text == normalize_whitespace(" ".join(labels)).casefold()
    )


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
            and any(
                "reaction" in str(class_name).lower()
                for class_name in tag.get("class", [])
            )
        )
    )
    if reactions:
        return len(reactions)

    text = _text(node).lower()
    match = re.search(r"(\d+)\s+reactions?", text)
    if match:
        return int(match.group(1))

    reaction_entries = [
        li for ul in node.select("ul._tqp") for li in ul.find_all("li", recursive=False)
    ]
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

# Variation selectors / zero-width joiners are common in emoji sequences and
# should not be counted as visible characters when deciding if a message is
# emoji-only.
_EMOJI_MODIFIER_CODEPOINTS = {
    "\ufe0f",  # variation selector-16
    "\ufe0e",  # variation selector-15
    "\u200d",  # zero-width joiner
}


def _is_emoji_only(text: str) -> bool:
    """Return True when `text` looks like a short emoji-only message.

    Facebook group chats allow setting a custom reaction emoji that is unique
    per chat (#7 in TODO). Messages whose entire content is that emoji should be
    treated as a reaction. We approximate this by considering messages that
    consist solely of non-ASCII symbol characters (emoji / pictographs) and are
    short.
    """
    if not text:
        return False
    visible = [
        ch for ch in text if not ch.isspace() and ch not in _EMOJI_MODIFIER_CODEPOINTS
    ]
    if not visible or len(visible) > 6:
        return False
    for ch in visible:
        codepoint = ord(ch)
        if ch.isalnum():
            return False
        if codepoint < 0x80:
            # Non-emoji punctuation (single emoji is always in the supplementary planes).
            return False
    return True


def _looks_like_reaction_message(text: str) -> bool:
    normalized = normalize_whitespace(text)
    if normalized in _REACTION_EMOJIS:
        return True
    return _is_emoji_only(normalized)


def _merge_emoji_into_summary(summary: str | None, emoji: str) -> str:
    counts: Counter[str] = Counter()
    if summary:
        for token in summary.split():
            if "×" not in token:
                continue
            key, _, value = token.partition("×")
            try:
                counts[key] += int(value)
            except ValueError:
                continue
    counts[emoji] += 1
    return " ".join(f"{token}×{count}" for token, count in counts.most_common())


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
    message_event_indices: list[int] = []
    seen_message_ids: set[str] = set()
    rename_events: list[tuple[datetime, int, str, str | None, str | None]] = []
    nickname_events: list[
        tuple[datetime, int, str, str, str | None, str | None, bool]
    ] = []
    member_events: list[MemberEventSeed] = []
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
                    alias_to_raw_id.get(_name_key(actor_name)) if actor_name else None
                )
                rename_events.append(
                    (ts, event_index, rename, actor_name, actor_raw_id)
                )
            if member_event := _extract_member_event(content or _text(container)):
                event_kind, actor_name, target_names = member_event
                actor_raw_id = alias_to_raw_id.get(_name_key(actor_name))
                if actor_raw_id is None and event_kind == "left":
                    actor_raw_id = raw_id
                for target_name in target_names:
                    target_raw_id = alias_to_raw_id.get(
                        _name_key(target_name), target_name
                    )
                    people_by_key.setdefault(
                        target_raw_id,
                        PersonSeed(
                            platform="facebook",
                            raw_id=target_raw_id,
                            display_name=target_name,
                        ),
                    )
                    alias_to_raw_id.setdefault(_name_key(target_name), target_raw_id)
                    payload = {
                        "actor_name": actor_name,
                        "actor_raw_id": actor_raw_id,
                        "target_name": target_name,
                        "chatId": channel.raw_id,
                    }
                    member_events.append(
                        MemberEventSeed(
                            source_name=source.name,
                            platform="facebook",
                            channel_raw_id=channel.raw_id,
                            kind=event_kind,
                            actor_raw_id=actor_raw_id,
                            target_raw_id=target_raw_id,
                            actor_display_name=actor_name,
                            target_display_name=target_name,
                            ts=ts,
                            payload_json=json.dumps(payload, ensure_ascii=False),
                        )
                    )
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
                    alias_to_raw_id.get(_name_key(actor_name)) if actor_name else None
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
                    is_system=_is_facebook_system_message(content),
                )
            )
            message_event_indices.append(event_index)

    # Facebook DYI exports list messages newest-first within each file and event_index
    # increments in that iteration order. Sort chronologically (ts ASC, event_index DESC)
    # so we can (a) attribute "main-emoji reaction messages" to the preceding chronological
    # message, and (b) assign deterministic sub-minute microseconds for in-minute ordering,
    # since FB timestamps only have minute precision.
    indexed_messages = sorted(
        zip(message_event_indices, messages),
        key=lambda pair: (pair[1].ts, -pair[0]),
    )
    folded: list[MessageSeed] = []
    last_message_idx: int | None = None
    for _, msg in indexed_messages:
        if last_message_idx is not None and _looks_like_reaction_message(msg.content):
            target = folded[last_message_idx]
            emoji = normalize_whitespace(msg.content)
            folded[last_message_idx] = replace(
                target,
                reaction_count=target.reaction_count + 1,
                reaction_summary=_merge_emoji_into_summary(
                    target.reaction_summary, emoji
                ),
            )
            continue
        folded.append(msg)
        last_message_idx = len(folded) - 1

    processed: list[MessageSeed] = []
    prev_minute: datetime | None = None
    minute_counter = 0
    for msg in folded:
        minute_key = msg.ts.replace(second=0, microsecond=0)
        if minute_key != prev_minute:
            minute_counter = 0
            prev_minute = minute_key
        if minute_counter < 1_000_000:
            adjusted = msg.ts.replace(microsecond=minute_counter)
        else:
            adjusted = msg.ts
        processed.append(replace(msg, ts=adjusted))
        minute_counter += 1
    messages = processed

    name_changes: list[NameChangeSeed] = []
    last_name: str | None = None
    for ts, _, new_name, actor_name, actor_raw_id in sorted(
        rename_events, key=lambda item: (item[0], item[1], item[2].casefold())
    ):
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
                payload_json=json.dumps(payload, ensure_ascii=False)
                if payload
                else None,
            )
        )
        last_name = new_name

    nickname_state: dict[str, str | None] = {}
    for (
        ts,
        _,
        target_raw_id,
        new_nickname,
        actor_name,
        actor_raw_id,
        is_cleared,
    ) in sorted(
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
        member_events=member_events,
    )
