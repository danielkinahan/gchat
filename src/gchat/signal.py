from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from bs4 import BeautifulSoup

from .models import ChannelSeed, MessageSeed, NameChangeSeed, PersonSeed, SourceSeed
from .reconciliation import ReconciliationConfig
from .util import to_utc_naive

_SIGNAL_SYSTEM_PATTERNS = (
    re.compile(
        r"^.+ (named|changed|updated) the group (name|title|description|avatar|photo|icon)(?: to .+)?[.!?]?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(you|.+?) (added|removed|invited) .+ (to|from) the (group|chat)[.!?]?$",
        re.IGNORECASE,
    ),
    re.compile(r"^.+ (left|joined) the (group|chat)[.!?]?$", re.IGNORECASE),
    re.compile(
        r"^(you )?(were|was) (removed|added) (from|to) the (group|chat)[.!?]?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(you )?changed the (group|chat) (name|description|avatar|photo|icon)(?: to .+)?[.!?]?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(you )?updated the (group|cover) (photo|avatar|icon)[.!?]?$", re.IGNORECASE
    ),
    re.compile(r"^missed (voice|video) call[.!?]?$", re.IGNORECASE),
    re.compile(r"^(voice|video) call[.!?]?$", re.IGNORECASE),
    re.compile(r"^(you )?accepted a request to join[.!?]?$", re.IGNORECASE),
    re.compile(r"^(you )?(sent|requested) a group invite[.!?]?$", re.IGNORECASE),
)


def _is_signal_system_message(content: str | None) -> bool:
    """Return True if this looks like a Signal system/action message."""
    if not content:
        return False
    text = " ".join(content.split()).strip()
    for pat in _SIGNAL_SYSTEM_PATTERNS:
        if pat.match(text):
            return True
    # Also check using the existing group-name-change extraction
    if _extract_group_name_change_from_text(text) is not None:
        return True
    return False


_GROUP_NAME_PATTERNS = (
    re.compile(
        r"^(?P<actor>.+?) named the group (?P<name>.+?)(?:[.!?])?(?:\n.*)?$",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"^(?P<actor>.+?) changed the group name to (?P<name>.+?)(?:[.!?])?(?:\n.*)?$",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"^(?P<actor>.+?) changed the group title to (?P<name>.+?)(?:[.!?])?(?:\n.*)?$",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"^(?P<actor>.+?) updated the group name to (?P<name>.+?)(?:[.!?])?(?:\n.*)?$",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"^(?P<actor>.+?) updated the group title to (?P<name>.+?)(?:[.!?])?(?:\n.*)?$",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"^(?P<actor>.+?) renamed the group to (?P<name>.+?)(?:[.!?])?(?:\n.*)?$",
        re.IGNORECASE | re.DOTALL,
    ),
)


@dataclass(frozen=True)
class SignalExport:
    source: SourceSeed
    channels: list[ChannelSeed]
    messages: list[MessageSeed]
    people: list[PersonSeed]
    name_changes: list[NameChangeSeed]


def _display_name(row: sqlite3.Row) -> str:
    for key in ("profileFullName", "profileName", "name", "e164", "serviceId", "id"):
        value = row[key]
        if value:
            return str(value)
    return str(row["id"])


def _conversation_name(row: sqlite3.Row) -> str:
    for key in ("name", "profileFullName", "profileName", "e164", "serviceId", "id"):
        value = row[key]
        if value:
            return str(value)
    return str(row["id"])


def _jsonl_records(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            yield json.loads(line)


def _join_name(*parts: object) -> str:
    values = [str(part).strip() for part in parts if part and str(part).strip()]
    return " ".join(values)


def _backup_source(path: Path) -> SourceSeed:
    return SourceSeed(platform="signal", name=f"Signal: {path.name}")


def _legacy_source(path: Path) -> SourceSeed:
    return SourceSeed(platform="signal", name=f"Signal: {path.parent.name}")


def _recipient_display_name(recipient: dict, account: dict | None = None) -> str:
    if "self" in recipient:
        if account:
            name = _join_name(account.get("givenName"), account.get("username"))
            if name:
                return name
        return f"Signal {recipient['id']}"

    contact = recipient.get("contact") or {}
    for key in (
        ("profileGivenName", "profileFamilyName"),
        ("systemGivenName", "systemFamilyName"),
    ):
        name = _join_name(contact.get(key[0]), contact.get(key[1]))
        if name:
            return name
    for key in ("systemNickname", "profileName", "name", "e164", "aci", "pni"):
        value = contact.get(key)
        if value:
            return str(value)
    return str(recipient["id"])


def _recipient_primary_id(recipient: dict, account: dict | None = None) -> str:
    if "self" in recipient:
        if account:
            username = account.get("username")
            if username:
                return str(username)
        return f"signal-self:{recipient['id']}"

    contact = recipient.get("contact") or {}
    for key in ("aci", "pni", "e164"):
        value = contact.get(key)
        if value:
            return str(value)
    return str(recipient["id"])


def _extract_message_content(chat_item: dict) -> str:
    standard = chat_item.get("standardMessage") or {}
    text = standard.get("text") or {}
    body = text.get("body")
    if body:
        return str(body)
    contact = chat_item.get("contactMessage") or {}
    text = contact.get("text") or {}
    if text.get("body"):
        return str(text["body"])
    return ""


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


def _extract_group_name_change_from_text(content: str) -> tuple[str | None, str] | None:
    text = " ".join(content.split())

    def _is_system_sentence(s: str) -> bool:
        s_clean = s.strip().lower()
        patterns = [
            r"you changed the group description",
            r"you changed the group avatar",
            r"changed the group description",
            r"changed the group avatar",
            r"updated the group photo",
            r"changed the group photo",
            r"added .* to the group",
            r"removed .* from the group",
        ]
        for pat in patterns:
            if re.search(pat, s_clean):
                return True
        return False

    # Try matching against a cleaned version with system-like sentences removed.
    parts = re.split(r"(?<=[.!?])\s+", text)
    kept = [p.strip() for p in parts if p.strip() and not _is_system_sentence(p)]
    cleaned = " ".join(kept).strip()

    candidates = [cleaned, text] if cleaned and cleaned != text else [text]

    for candidate in candidates:
        for pattern in _GROUP_NAME_PATTERNS:
            match = pattern.match(candidate)
            if not match:
                continue
            actor = match.group("actor").strip() or None
            group_name = _strip_surrounding_quotes(
                match.group("name").strip().rstrip(".!?").strip()
            )
            if group_name:
                return actor, group_name
    return None


def _extract_group_name_from_text(content: str) -> str | None:
    change = _extract_group_name_change_from_text(content)
    return change[1] if change is not None else None


def _extract_attachment_count(chat_item: dict) -> int:
    standard = chat_item.get("standardMessage") or {}
    attachments = standard.get("attachments")
    if isinstance(attachments, list):
        return len(attachments)
    if "stickerMessage" in chat_item or "viewOnceMessage" in chat_item:
        return 1
    return 0


def _extract_attachment_preview(chat_item: dict) -> str | None:
    standard = chat_item.get("standardMessage") or {}
    attachments = standard.get("attachments")
    if isinstance(attachments, list):
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            raw_pointer = attachment.get("pointer")
            pointer: dict[str, object] = (
                raw_pointer if isinstance(raw_pointer, dict) else {}
            )
            for value in (
                pointer.get("fileName"),
                attachment.get("fileName"),
                pointer.get("contentType"),
                attachment.get("contentType"),
            ):
                if isinstance(value, str) and value.strip():
                    return value.strip()
    if "stickerMessage" in chat_item:
        return "sticker"
    if "viewOnceMessage" in chat_item:
        return "view-once message"
    return None


def _extract_reaction_count(chat_item: dict) -> int:
    def count_value(value: object) -> int:
        if isinstance(value, list):
            return len(value)
        if isinstance(value, dict):
            nested = (
                value.get("reactions")
                or value.get("reactionList")
                or value.get("reactionData")
            )
            if isinstance(nested, list):
                return len(nested)
            if isinstance(nested, dict):
                return count_value(nested)
            return 1
        if isinstance(value, int):
            return value
        return 0

    standard = chat_item.get("standardMessage") or {}
    for source in (
        chat_item.get("reactions"),
        chat_item.get("reactionList"),
        chat_item.get("reactionData"),
        standard.get("reactions"),
        standard.get("reactionList"),
        standard.get("reactionData"),
    ):
        if source:
            count = count_value(source)
            if count:
                return count
    return 0


def _extract_reaction_summary(chat_item: dict) -> str | None:
    standard = chat_item.get("standardMessage") or {}
    raw_reactions: object = None
    for source in (
        chat_item.get("reactions"),
        chat_item.get("reactionList"),
        chat_item.get("reactionData"),
        standard.get("reactions"),
        standard.get("reactionList"),
        standard.get("reactionData"),
    ):
        if source:
            raw_reactions = source
            break
    if not isinstance(raw_reactions, list):
        return None

    counts: Counter[str] = Counter()
    for reaction in raw_reactions:
        if not isinstance(reaction, dict):
            continue
        emoji = str(
            reaction.get("emoji")
            or reaction.get("reaction")
            or reaction.get("reactionEmoji")
            or ""
        ).strip()
        if emoji:
            counts[emoji] += 1
    if not counts:
        return None
    return " ".join(f"{emoji}×{count}" for emoji, count in counts.most_common())


def _message_id(chat_item: dict) -> str:
    payload = json.dumps(
        chat_item, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def normalize_backup(
    path: Path, include_signal_identities: set[str] | None = None
) -> SignalExport:
    root = path / "main.jsonl" if path.is_dir() else path
    source = _backup_source(path if path.is_dir() else path.parent)

    account: dict | None = None
    recipients: dict[str, dict] = {}
    chats: dict[str, dict] = {}
    recipient_ids_by_stable_id: dict[str, str] = {}
    group_recipient_ids: set[str] = set()
    group_titles: dict[str, str] = {}

    for record in _jsonl_records(root):
        if "account" in record:
            account = record["account"]
            continue
        if "recipient" in record:
            recipient = record["recipient"]
            recipient_id = str(recipient["id"])
            recipients[recipient_id] = recipient
            contact = recipient.get("contact") or {}
            for key in ("aci", "pni", "e164"):
                value = contact.get(key)
                if value and str(value) not in recipient_ids_by_stable_id:
                    recipient_ids_by_stable_id[str(value)] = recipient_id
            continue
        if "chat" in record:
            chat = record["chat"]
            chat_id = str(chat["id"])
            chats[chat_id] = chat
            recipient_id = str(chat.get("recipientId") or "")
            recipient = recipients.get(recipient_id)
            if recipient and "group" in recipient:
                group_recipient_ids.add(recipient_id)
                snapshot = (recipient.get("group") or {}).get("snapshot") or {}
                title = snapshot.get("title") or {}
                group_titles[chat_id] = str(
                    title.get("title") or f"Signal group {chat_id}"
                )

    group_chat_ids = {
        chat_id
        for chat_id, chat in chats.items()
        if str(chat.get("recipientId") or "") in group_recipient_ids
    }
    group_chat_participants: dict[str, set[str]] = {
        chat_id: set() for chat_id in group_chat_ids
    }

    for chat_id in group_chat_ids:
        recipient_id = str(chats[chat_id].get("recipientId") or "")
        recipient = recipients.get(recipient_id)
        if recipient and "group" in recipient:
            snapshot = (recipient.get("group") or {}).get("snapshot") or {}
            for member in snapshot.get("members") or []:
                if not isinstance(member, dict):
                    continue
                user_id = member.get("userId")
                if not user_id:
                    continue
                stable_id = str(user_id)
                group_chat_participants[chat_id].add(stable_id)
                mapped = recipient_ids_by_stable_id.get(stable_id)
                if mapped:
                    mapped_recipient = recipients.get(mapped)
                    if mapped_recipient is not None:
                        group_chat_participants[chat_id].add(
                            _recipient_primary_id(mapped_recipient, account)
                        )

    for record in _jsonl_records(root):
        if "chatItem" not in record:
            continue
        item = record["chatItem"]
        chat_id = str(item.get("chatId") or "")
        if chat_id not in group_chat_ids:
            continue
        author_id = str(item.get("authorId") or "")
        author = recipients.get(author_id)
        if author is not None:
            group_chat_participants[chat_id].add(_recipient_primary_id(author, account))

    allowed_group_chat_ids = set(group_chat_ids)
    if include_signal_identities is not None:
        configured_ids = {identity.casefold() for identity in include_signal_identities}
        allowed_group_chat_ids = {
            chat_id
            for chat_id, participant_ids in group_chat_participants.items()
            if len(
                {
                    participant_id.casefold()
                    for participant_id in participant_ids
                    if participant_id.casefold() in configured_ids
                }
            )
            >= 2
        }

    relevant_recipient_ids: set[str] = set()
    relevant_stable_ids: set[str] = set()
    channels: dict[str, ChannelSeed] = {}
    name_changes: list[NameChangeSeed] = []
    group_title_updates: dict[str, list[tuple[datetime, str | None, str, dict]]] = {}

    for chat_id in allowed_group_chat_ids:
        channels[chat_id] = ChannelSeed(
            source_name=source.name,
            raw_id=chat_id,
            name=group_titles.get(chat_id, f"Signal group {chat_id}"),
            theme_name=group_titles.get(chat_id, f"Signal group {chat_id}"),
        )
        recipient_id = str(chats[chat_id].get("recipientId") or "")
        recipient = recipients.get(recipient_id)
        if recipient and "group" in recipient:
            snapshot = (recipient.get("group") or {}).get("snapshot") or {}
            for member in snapshot.get("members") or []:
                if not isinstance(member, dict):
                    continue
                user_id = member.get("userId")
                if not user_id:
                    continue
                relevant_stable_ids.add(str(user_id))
                mapped = recipient_ids_by_stable_id.get(str(user_id))
                if mapped:
                    relevant_recipient_ids.add(mapped)

    if account:
        relevant_recipient_ids.add("1")

    for record in _jsonl_records(root):
        if "chatItem" not in record:
            continue
        item = record["chatItem"]
        chat_id = str(item.get("chatId") or "")
        if chat_id not in allowed_group_chat_ids:
            continue

        author_id = str(item.get("authorId") or "")
        if author_id:
            relevant_recipient_ids.add(author_id)

        if "updateMessage" in item:
            update = item["updateMessage"] or {}
            profile_change = update.get("profileChange") or {}
            if profile_change.get("previousName") or profile_change.get("newName"):
                author = recipients.get(author_id)
                raw_id = _recipient_primary_id(author, account) if author else author_id
                previous_name = profile_change.get("previousName")
                new_name = profile_change.get("newName")
                if new_name:
                    name_changes.append(
                        NameChangeSeed(
                            source_name=source.name,
                            platform="signal",
                            entity_kind="person",
                            entity_raw_id=raw_id,
                            previous_name=str(previous_name) if previous_name else None,
                            new_name=str(new_name),
                            ts=to_utc_naive(
                                datetime.fromtimestamp(int(item["dateSent"]) / 1000.0)
                            ),
                            kind="nickname-change",
                            payload_json=json.dumps(item, ensure_ascii=False),
                        )
                    )
            group_change = update.get("groupChange") or {}
            for change in group_change.get("updates") or []:
                if not isinstance(change, dict):
                    continue
                # Handle group name updates
                if "groupNameUpdate" in change:
                    title_update = change["groupNameUpdate"] or {}
                    new_title = str(title_update.get("newGroupName") or "").strip()
                    if not new_title:
                        continue
                    old_title = (
                        str(title_update.get("oldGroupName") or "").strip() or None
                    )
                    group_title_updates.setdefault(chat_id, []).append(
                        (
                            to_utc_naive(
                                datetime.fromtimestamp(int(item["dateSent"]) / 1000.0)
                            ),
                            None,
                            new_title,
                            item,
                        )
                    )
                    continue
                # Handle group icon/photo updates
                if "groupIconUpdate" in change or "groupPhotoUpdate" in change:
                    # icon_update = (
                    #     change.get("groupIconUpdate")
                    #     or change.get("groupPhotoUpdate")
                    #     or {}
                    # )
                    group_title_updates.setdefault(chat_id, []).append(
                        (
                            to_utc_naive(
                                datetime.fromtimestamp(int(item["dateSent"]) / 1000.0)
                            ),
                            None,
                            "(photo changed)",
                            item,
                        )
                    )
                    continue
                # Handle group emoji updates
                if "groupEmojiUpdate" in change or "groupReactionUpdate" in change:
                    emoji_update = (
                        change.get("groupEmojiUpdate")
                        or change.get("groupReactionUpdate")
                        or {}
                    )
                    emoji = (
                        emoji_update.get("emoji")
                        or emoji_update.get("reaction")
                        or None
                    )
                    group_title_updates.setdefault(chat_id, []).append(
                        (
                            to_utc_naive(
                                datetime.fromtimestamp(int(item["dateSent"]) / 1000.0)
                            ),
                            None,
                            str(emoji) if emoji else "(emoji changed)",
                            item,
                        )
                    )
                    continue
                # Handle polls or other updates generically
                if "poll" in change or "pollUpdate" in change:
                    group_title_updates.setdefault(chat_id, []).append(
                        (
                            to_utc_naive(
                                datetime.fromtimestamp(int(item["dateSent"]) / 1000.0)
                            ),
                            None,
                            "(poll)",
                            item,
                        )
                    )
                    continue

        content = _extract_message_content(item)
        text_title = _extract_group_name_from_text(content)
        if text_title:
            group_title_updates.setdefault(chat_id, []).append(
                (
                    to_utc_naive(
                        datetime.fromtimestamp(int(item["dateSent"]) / 1000.0)
                    ),
                    None,
                    text_title,
                    item,
                )
            )

    for chat_id, updates in group_title_updates.items():
        updates.sort(key=lambda entry: (entry[0], entry[2].casefold()))
        last_title: str | None = None
        seeded_initial_title = False
        for ts, old_title, new_title, payload in updates:
            if (
                old_title
                and old_title != new_title
                and not seeded_initial_title
                and last_title is None
            ):
                name_changes.append(
                    NameChangeSeed(
                        source_name=source.name,
                        platform="signal",
                        entity_kind="channel",
                        entity_raw_id=chat_id,
                        previous_name=None,
                        new_name=old_title,
                        ts=ts,
                        kind="channel-title-change",
                        payload_json=json.dumps(payload, ensure_ascii=False),
                    )
                )
                last_title = old_title
                seeded_initial_title = True
            previous_name = old_title
            if previous_name is None and last_title and last_title != new_title:
                previous_name = last_title
            if last_title == new_title and previous_name in (None, new_title):
                continue
            if previous_name == new_title:
                continue
            name_changes.append(
                NameChangeSeed(
                    source_name=source.name,
                    platform="signal",
                    entity_kind="channel",
                    entity_raw_id=chat_id,
                    previous_name=previous_name,
                    new_name=new_title,
                    ts=ts,
                    kind="channel-title-change",
                    payload_json=json.dumps(payload, ensure_ascii=False),
                )
            )
            last_title = new_title
        if last_title and chat_id in channels:
            channels[chat_id] = ChannelSeed(
                source_name=source.name,
                raw_id=chat_id,
                name=last_title,
                theme_name=last_title,
            )

    people: dict[str, PersonSeed] = {}
    for recipient_id in sorted(relevant_recipient_ids):
        recipient = recipients.get(recipient_id)
        if recipient is None:
            continue
        raw_id = _recipient_primary_id(recipient, account)
        display_name = _recipient_display_name(recipient, account)
        people[raw_id] = PersonSeed(
            platform="signal", raw_id=raw_id, display_name=display_name
        )

    for stable_id in sorted(relevant_stable_ids):
        if stable_id in people:
            continue
        people[stable_id] = PersonSeed(
            platform="signal", raw_id=stable_id, display_name=stable_id
        )

    messages: list[MessageSeed] = []
    for record in _jsonl_records(root):
        if "chatItem" not in record:
            continue
        item = record["chatItem"]
        chat_id = str(item.get("chatId") or "")
        if chat_id not in allowed_group_chat_ids:
            continue
        author_id = str(item.get("authorId") or "")
        author = recipients.get(author_id)
        raw_id = _recipient_primary_id(author, account) if author else author_id
        person = people.get(raw_id)
        if person is None:
            display_name = (
                _recipient_display_name(author, account) if author else raw_id
            )
            person = PersonSeed(
                platform="signal", raw_id=raw_id, display_name=display_name
            )
            people[raw_id] = person
        timestamp = to_utc_naive(datetime.fromtimestamp(int(item["dateSent"]) / 1000.0))
        messages.append(
            MessageSeed(
                id=_message_id(item),
                source_name=source.name,
                channel_raw_id=chat_id,
                channel_name=channels[chat_id].name,
                theme_name=channels[chat_id].theme_name,
                person=person,
                ts=timestamp,
                content=_extract_message_content(item),
                attachment_count=_extract_attachment_count(item),
                attachment_preview=_extract_attachment_preview(item),
                reaction_count=_extract_reaction_count(item),
                reaction_summary=_extract_reaction_summary(item),
                is_system=_is_signal_system_message(_extract_message_content(item)),
            )
        )

    return SignalExport(
        source=source,
        channels=list(channels.values()),
        messages=messages,
        people=list(people.values()),
        name_changes=name_changes,
    )


def normalize_database(
    path: Path, include_signal_identities: set[str] | None = None
) -> SignalExport:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        con.row_factory = sqlite3.Row
        source = _legacy_source(path)

        conversation_rows = con.execute(
            "SELECT id, name, profileName, profileFullName, e164, serviceId, members FROM conversations"
        ).fetchall()
        configured_ids = (
            {identity.casefold() for identity in include_signal_identities}
            if include_signal_identities is not None
            else None
        )
        people_by_key: dict[str, PersonSeed] = {}
        channels: dict[str, ChannelSeed] = {}
        name_changes: list[NameChangeSeed] = []

        for row in conversation_rows:
            conversation_id = str(row["id"])
            member_ids: set[str] = set()
            members = row["members"]
            parsed: list[dict] = []
            if members:
                try:
                    decoded = json.loads(members)
                except json.JSONDecodeError:
                    decoded = []
                parsed = decoded if isinstance(decoded, list) else []
                for member in parsed:
                    if isinstance(member, dict):
                        for key in ("aci", "e164", "serviceId", "id", "uuid"):
                            value = member.get(key)
                            if value:
                                member_ids.add(str(value))
                                break
            is_group_chat = len(member_ids) > 1
            if configured_ids is not None and is_group_chat:
                configured_participants = {
                    member_id.casefold()
                    for member_id in member_ids
                    if member_id.casefold() in configured_ids
                }
                if len(configured_participants) < 2:
                    continue

            channel_name = _conversation_name(row)
            channels[conversation_id] = ChannelSeed(
                source_name=source.name,
                raw_id=conversation_id,
                name=channel_name,
                theme_name=channel_name,
            )
            for key in ("serviceId", "e164", "id"):
                value = row[key]
                if value:
                    raw_id = str(value)
                    people_by_key.setdefault(
                        raw_id,
                        PersonSeed(
                            platform="signal",
                            raw_id=raw_id,
                            display_name=_display_name(row),
                        ),
                    )
            for member in parsed:
                if isinstance(member, dict):
                    for key in ("aci", "e164", "serviceId", "id", "uuid"):
                        value = member.get(key)
                        if value:
                            raw_id = str(value)
                            people_by_key.setdefault(
                                raw_id,
                                PersonSeed(
                                    platform="signal",
                                    raw_id=raw_id,
                                    display_name=str(
                                        member.get("name")
                                        or member.get("profileName")
                                        or raw_id
                                    ),
                                ),
                            )
                            break

        event_rows = con.execute(
            "SELECT id, conversationId, timestamp, type, json FROM messages WHERE type IN ('profile-change', 'group-v2-change') ORDER BY timestamp, id"
        ).fetchall()
        for row in event_rows:
            payload = json.loads(row["json"] or "{}")
            ts = to_utc_naive(datetime.fromtimestamp(int(row["timestamp"]) / 1000.0))
            conversation_id = str(row["conversationId"])
            if configured_ids is not None and conversation_id not in channels:
                continue
            if row["type"] == "profile-change":
                change = payload.get("profileChange") or {}
                if change.get("type") == "name":
                    changed_id = str(payload.get("changedId") or conversation_id)
                    new_name = str(
                        change.get("newName") or change.get("oldName") or changed_id
                    )
                    previous_name = change.get("oldName")
                    people_by_key.setdefault(
                        changed_id,
                        PersonSeed(
                            platform="signal", raw_id=changed_id, display_name=new_name
                        ),
                    )
                    name_changes.append(
                        NameChangeSeed(
                            source_name=source.name,
                            platform="signal",
                            entity_kind="person",
                            entity_raw_id=changed_id,
                            previous_name=str(previous_name) if previous_name else None,
                            new_name=new_name,
                            ts=ts,
                            kind="nickname-change",
                            payload_json=row["json"],
                        )
                    )
            elif row["type"] == "group-v2-change":
                details = (payload.get("groupV2Change") or {}).get("details") or []
                channel_id = conversation_id
                channel = channels.get(channel_id)
                if channel is None:
                    channel_name = channel_id
                    channel = ChannelSeed(
                        source_name=source.name,
                        raw_id=channel_id,
                        name=channel_name,
                        theme_name=channel_name,
                    )
                    channels[channel_id] = channel
                for detail in details:
                    if detail.get("type") == "title":
                        new_title = str(detail.get("newTitle") or channel.name)
                        name_changes.append(
                            NameChangeSeed(
                                source_name=source.name,
                                platform="signal",
                                entity_kind="channel",
                                entity_raw_id=channel_id,
                                previous_name=str(detail.get("oldTitle"))
                                if detail.get("oldTitle")
                                else None,
                                new_name=new_title,
                                ts=ts,
                                kind="channel-title-change",
                                payload_json=row["json"],
                            )
                        )

        reaction_counts = dict(
            con.execute(
                "SELECT messageId, COUNT(*) FROM reactions GROUP BY messageId"
            ).fetchall()
        )
        reaction_summaries: dict[str, str] = {}
        try:
            reaction_cols = {
                str(row["name"])
                for row in con.execute("PRAGMA table_info(reactions)").fetchall()
            }
        except sqlite3.Error:
            reaction_cols = set()
        if "messageId" in reaction_cols:
            selected_cols = ["messageId"]
            for optional in ("emoji", "reaction", "emojiName", "json"):
                if optional in reaction_cols:
                    selected_cols.append(optional)
            query = f"SELECT {', '.join(selected_cols)} FROM reactions ORDER BY rowid"
            grouped: dict[str, Counter[str]] = {}
            for row in con.execute(query).fetchall():
                message_id = str(row["messageId"])
                emoji = ""
                for key in ("emoji", "reaction", "emojiName"):
                    if key in selected_cols and row[key]:
                        emoji = str(row[key]).strip()
                        if emoji:
                            break
                if not emoji and "json" in selected_cols and row["json"]:
                    try:
                        payload = json.loads(str(row["json"]))
                    except json.JSONDecodeError:
                        payload = {}
                    if isinstance(payload, dict):
                        emoji = str(
                            payload.get("emoji")
                            or payload.get("reaction")
                            or payload.get("emojiName")
                            or ""
                        ).strip()
                if not emoji:
                    continue
                grouped.setdefault(message_id, Counter())[emoji] += 1
            for message_id, counts in grouped.items():
                reaction_summaries[message_id] = " ".join(
                    f"{emoji}×{count}" for emoji, count in counts.most_common()
                )
        attachment_counts = dict(
            con.execute(
                "SELECT messageId, COUNT(*) FROM message_attachments GROUP BY messageId"
            ).fetchall()
        )
        attachment_previews: dict[str, str] = {}
        try:
            attachment_cols = {
                str(row["name"])
                for row in con.execute(
                    "PRAGMA table_info(message_attachments)"
                ).fetchall()
            }
        except sqlite3.Error:
            attachment_cols = set()
        if "messageId" in attachment_cols:
            selected_cols = ["messageId"]
            for optional in ("fileName", "path", "contentType", "json"):
                if optional in attachment_cols:
                    selected_cols.append(optional)
            query = f"SELECT {', '.join(selected_cols)} FROM message_attachments ORDER BY rowid"
            for row in con.execute(query).fetchall():
                message_id = str(row["messageId"])
                if message_id in attachment_previews:
                    continue
                preview: str | None = None
                for key in ("fileName", "path", "contentType"):
                    if key in selected_cols:
                        value = row[key]
                        if isinstance(value, str) and value.strip():
                            preview = value.strip()
                            break
                if preview is None and "json" in selected_cols and row["json"]:
                    try:
                        payload = json.loads(str(row["json"]))
                    except json.JSONDecodeError:
                        payload = {}
                    if isinstance(payload, dict):
                        for key in ("fileName", "path", "contentType", "url"):
                            value = payload.get(key)
                            if isinstance(value, str) and value.strip():
                                preview = value.strip()
                                break
                if preview:
                    attachment_previews[message_id] = preview

        messages: list[MessageSeed] = []
        rows = con.execute(
            "SELECT id, conversationId, sourceServiceId, body, timestamp, type FROM messages WHERE type IN ('incoming','outgoing') ORDER BY timestamp, id"
        ).fetchall()
        for row in rows:
            conversation_id = str(row["conversationId"])
            channel = channels.get(conversation_id)
            if channel is None:
                if configured_ids is not None:
                    continue
                channel_name = conversation_id
                channel = ChannelSeed(
                    source_name=source.name,
                    raw_id=conversation_id,
                    name=channel_name,
                    theme_name=channel_name,
                )
                channels[conversation_id] = channel
            raw_id = str(row["sourceServiceId"] or conversation_id)
            person = people_by_key.setdefault(
                raw_id,
                PersonSeed(platform="signal", raw_id=raw_id, display_name=raw_id),
            )
            body = str(row["body"] or "")
            timestamp = to_utc_naive(
                datetime.fromtimestamp(int(row["timestamp"]) / 1000.0)
            )
            messages.append(
                MessageSeed(
                    id=str(row["id"]),
                    source_name=source.name,
                    channel_raw_id=channel.raw_id,
                    channel_name=channel.name,
                    theme_name=channel.theme_name,
                    person=person,
                    ts=timestamp,
                    content=body,
                    attachment_count=int(attachment_counts.get(row["id"], 0)),
                    attachment_preview=attachment_previews.get(str(row["id"])),
                    reaction_count=int(reaction_counts.get(row["id"], 0)),
                    reaction_summary=reaction_summaries.get(str(row["id"])),
                    is_system=_is_signal_system_message(body),
                )
            )

        return SignalExport(
            source=source,
            channels=list(channels.values()),
            messages=messages,
            people=list(people_by_key.values()),
            name_changes=name_changes,
        )
    finally:
        con.close()


def _html_message_body(message_div) -> str:
    clone = BeautifulSoup(str(message_div), "html.parser")
    for quote in clone.select(".msg-quote"):
        quote.decompose()
    body_parts: list[str] = []
    for pre in clone.find_all("pre"):
        text = pre.get_text("\n", strip=True)
        if text:
            body_parts.append(text)
    return "\n\n".join(body_parts)


def _html_reply_anchor(message_div) -> str | None:
    quote_link = message_div.select_one('.quote-link[href^="#"]')
    if quote_link is None:
        return None
    href = quote_link.get("href")
    if not isinstance(href, str) or not href.startswith("#"):
        return None
    anchor = href.lstrip("#").strip()
    return anchor or None


def _looks_like_html_export(path: Path) -> bool:
    if not path.is_dir():
        return False
    if any(path.glob("*.html")) and (path / "media").is_dir():
        return True
    for child in path.iterdir():
        if child.is_dir() and any(child.glob("*.html")):
            return True
    return False


def _parse_html_timestamp(value: str) -> datetime | None:
    text = " ".join(value.split())
    for fmt in ("%b %d, %Y %H:%M:%S", "%b %d, %Y %H:%M"):
        try:
            return to_utc_naive(datetime.strptime(text, fmt))
        except ValueError:
            continue
    return None


def _channel_raw_id_from_dir(name: str) -> str:
    match = re.search(r"\(_id(?P<id>\d+)\)\s*$", name)
    if match:
        return match.group("id")
    return name


def _channel_name_from_dir(name: str) -> str:
    return re.sub(r"\s*\(_id\d+\)\s*$", "", name).strip() or name


def _message_attachment_paths(message_div, chat_dir_name: str) -> list[str]:
    attachments: list[str] = []
    for tag in message_div.find_all(["img", "source", "a"]):
        path: str | None = None
        if tag.name in {"img", "source"}:
            path = tag.get("src")
        elif tag.name == "a":
            path = tag.get("href")
        if not isinstance(path, str) or not path.startswith("media/"):
            continue
        relative = f"{chat_dir_name}/{path}"
        if relative not in attachments:
            attachments.append(relative)
    return attachments


def _message_reactions(message_div) -> tuple[int, str | None, str | None]:
    counts: Counter[str] = Counter()
    reactors: dict[str, list[dict[str, str]]] = {}
    for reaction in message_div.select("div.msg-reactions div.msg-reaction"):
        emoji = reaction.select_one("span.msg-emoji")
        value = emoji.get_text(strip=True) if emoji is not None else ""
        if not value:
            continue
        reaction_count = 1
        reaction_count_node = reaction.select_one("span.reaction-count")
        if reaction_count_node is not None:
            parsed = reaction_count_node.get_text(strip=True)
            if parsed.isdigit():
                reaction_count = max(int(parsed), 1)
        counts[value] += reaction_count
        info = reaction.select_one(".msg-reaction-info")
        if info is not None:
            match = re.search(
                r"(?:^|\n)\s*From:\s*(.+?)(?:\n|$)",
                info.get_text("\n", strip=True),
                flags=re.IGNORECASE,
            )
            if match:
                display_name = match.group(1).strip()
                reactors.setdefault(value, []).append(
                    {
                        "platform": "signal",
                        "raw_id": _html_person_raw_id(display_name),
                        "display_name": display_name,
                    }
                )
    if not counts:
        return 0, None, None
    summary = " ".join(f"{emoji}×{count}" for emoji, count in counts.most_common())
    details = json.dumps(
        [
            {
                "name": emoji,
                "count": count,
                "reactors": reactors.get(emoji, []),
            }
            for emoji, count in counts.most_common()
        ],
        ensure_ascii=False,
    )
    return sum(counts.values()), summary, details


def _notify(status: Callable[[str], None] | None, message: str) -> None:
    if status is not None:
        status(message)


def _html_person_raw_id(display_name: str) -> str:
    normalized = " ".join(display_name.strip().split())
    if normalized.casefold() == "you":
        return "self"
    return f"name:{normalized.casefold()}"


def _html_person(display_name: str, people: dict[str, PersonSeed]) -> PersonSeed:
    raw_id = _html_person_raw_id(display_name)
    canonical_display_name = "You" if raw_id == "self" else display_name
    return people.setdefault(
        raw_id,
        PersonSeed(
            platform="signal", raw_id=raw_id, display_name=canonical_display_name
        ),
    )


def _extract_html_chat_members(soup: BeautifulSoup) -> list[str]:
    details = soup.select_one(".thread-subtitle .groupdetails .columnview")
    if details is not None:
        columns = details.select("span.column-right-align, span.column-left-align")
        for index in range(0, len(columns) - 1, 2):
            label = columns[index].get_text(" ", strip=True).casefold().rstrip(":")
            if label != "members":
                continue
            raw_members = columns[index + 1].get_text(" ", strip=True)
            cleaned = re.sub(r"\s*\(admin\)\s*", "", raw_members, flags=re.IGNORECASE)
            members = [
                re.sub(r"\s+", " ", part).strip()
                for part in re.split(r"\s*,\s*", cleaned)
                if part.strip()
            ]
            if members:
                return members
    return []


def _signal_html_participant_names(soup: BeautifulSoup) -> set[str]:
    names = {name for name in _extract_html_chat_members(soup) if name}
    for member in soup.select("div.membername"):
        display_name = member.get_text(" ", strip=True)
        if display_name:
            names.add(display_name)
    return names


def _matched_configured_signal_people(
    participant_names: set[str],
    reconciliation: ReconciliationConfig | None,
    include_signal_identities: set[str] | None,
) -> set[str]:
    if not participant_names:
        return set()

    matched: set[str] = set()
    if reconciliation is not None:
        for name in participant_names:
            canonical_name, color_hint = reconciliation.people.resolve(
                "signal", _html_person_raw_id(name), name
            )
            if color_hint:
                matched.add(canonical_name)
        return matched

    if include_signal_identities is None:
        return set()

    configured = {identity.casefold() for identity in include_signal_identities}
    for name in participant_names:
        normalized_name = " ".join(name.strip().casefold().split())
        raw_id = _html_person_raw_id(name).casefold()
        if normalized_name in configured or raw_id in configured:
            matched.add(normalized_name)
    return matched


def _extract_status_message_text(message_div) -> str:
    status_pre = message_div.select_one("div.status-text pre")
    if status_pre is not None:
        return status_pre.get_text(" ", strip=True)
    for pre in message_div.find_all("pre"):
        if (
            pre.find_parent(class_="footer") is not None
            or pre.find_parent(class_="footer-status") is not None
        ):
            continue
        text = pre.get_text(" ", strip=True)
        if text:
            return text
    return ""


def normalize_html_export(
    path: Path,
    include_signal_identities: set[str] | None = None,
    status: Callable[[str], None] | None = None,
    reconciliation: ReconciliationConfig | None = None,
    filter_to_configured_people: bool = False,
) -> SignalExport:
    source = SourceSeed(platform="signal", name=f"Signal: {path.name}")
    channels: dict[str, ChannelSeed] = {}
    people: dict[str, PersonSeed] = {}
    messages: list[MessageSeed] = []
    name_changes: list[NameChangeSeed] = []

    if any(path.glob("*.html")) and (path / "media").is_dir():
        chat_dirs = [path]
    else:
        chat_dirs = sorted(
            [
                child
                for child in path.iterdir()
                if child.is_dir() and any(child.glob("*.html"))
            ],
            key=lambda child: child.name.casefold(),
        )

    total_chats = len(chat_dirs)
    _notify(status, f"Signal HTML export: found {total_chats} chats in {path.name}")
    for index, chat_dir in enumerate(chat_dirs, start=1):
        _notify(
            status,
            f"Signal HTML export: parsing chat {index}/{total_chats} ({chat_dir.name})",
        )
        html_files = sorted(chat_dir.glob("*.html"))
        if not html_files:
            continue
        html_file = html_files[0]
        soup = BeautifulSoup(
            html_file.read_text(encoding="utf-8", errors="ignore"), "html.parser"
        )

        participant_names = _signal_html_participant_names(soup)
        if filter_to_configured_people:
            matched_people = _matched_configured_signal_people(
                participant_names,
                reconciliation,
                include_signal_identities,
            )
            if len(matched_people) < 2:
                _notify(
                    status,
                    f"Signal HTML export: skipping chat {chat_dir.name} (matched {len(matched_people)} configured people)",
                )
                continue

        channel_raw_id = _channel_raw_id_from_dir(chat_dir.name)
        title = (
            soup.title.get_text(strip=True) if soup.title else ""
        ) or _channel_name_from_dir(chat_dir.name)
        channels[channel_raw_id] = ChannelSeed(
            source_name=source.name,
            raw_id=channel_raw_id,
            name=title,
            theme_name=title,
        )

        group_title_updates: list[
            tuple[datetime, str | None, str | None, str, dict[str, str]]
        ] = []

        for message_index, message_div in enumerate(soup.select("div.msg"), start=1):
            classes = set(message_div.get("class") or [])
            if "msg-date-change" in classes:
                continue

            timestamp_node = message_div.select_one(
                "div.footer span.msg-data"
            ) or message_div.select_one("div.footer-status span.msg-data")
            if timestamp_node is None:
                continue
            timestamp = _parse_html_timestamp(timestamp_node.get_text(" ", strip=True))
            if timestamp is None:
                continue

            if "msg-status" in classes:
                status_text = _extract_status_message_text(message_div)
                group_name_change = _extract_group_name_change_from_text(status_text)
                if group_name_change is not None:
                    actor_name, new_title = group_name_change
                    actor_raw_id = (
                        _html_person_raw_id(actor_name) if actor_name else None
                    )
                    if actor_name:
                        _html_person(actor_name, people)
                    group_title_updates.append(
                        (
                            timestamp,
                            actor_name,
                            actor_raw_id,
                            new_title,
                            {
                                "actor_name": actor_name or "",
                                "actor_raw_id": actor_raw_id or "",
                                "text": status_text,
                            },
                        )
                    )
                continue

            if "msg-outgoing" in classes:
                person = _html_person("You", people)
            else:
                member = message_div.select_one("div.membername")
                display_name = (
                    member.get_text(" ", strip=True)
                    if member is not None
                    else "Unknown"
                )
                person = _html_person(display_name, people)

            content = _html_message_body(message_div)
            reply_to_id = _html_reply_anchor(message_div)

            attachments = _message_attachment_paths(message_div, chat_dir.name)
            attachment_preview = attachments[0] if attachments else None
            reaction_count, reaction_summary, reaction_details_json = (
                _message_reactions(message_div)
            )

            digest_input = "|".join(
                [
                    channel_raw_id,
                    timestamp.isoformat(),
                    person.raw_id,
                    content,
                    str(message_index),
                    attachment_preview or "",
                ]
            )
            message_id = hashlib.sha1(digest_input.encode("utf-8")).hexdigest()
            messages.append(
                MessageSeed(
                    id=message_id,
                    source_name=source.name,
                    channel_raw_id=channel_raw_id,
                    channel_name=title,
                    theme_name=title,
                    person=person,
                    ts=timestamp,
                    content=content,
                    reply_to_id=reply_to_id,
                    attachment_count=len(attachments),
                    attachment_preview=attachment_preview,
                    reaction_count=reaction_count,
                    reaction_summary=reaction_summary,
                    reaction_details_json=reaction_details_json,
                    is_system=_is_signal_system_message(content),
                )
            )

        group_title_updates.sort(key=lambda entry: (entry[0], entry[3].casefold()))
        previous_title: str | None = None
        for (
            timestamp,
            actor_name,
            actor_raw_id,
            new_title,
            payload,
        ) in group_title_updates:
            if previous_title == new_title:
                continue
            name_changes.append(
                NameChangeSeed(
                    source_name=source.name,
                    platform="signal",
                    entity_kind="channel",
                    entity_raw_id=channel_raw_id,
                    previous_name=previous_title,
                    new_name=new_title,
                    ts=timestamp,
                    kind="channel-title-change",
                    payload_json=json.dumps(payload, ensure_ascii=False),
                )
            )
            previous_title = new_title

        _notify(status, f"Signal HTML export: parsed {len(messages)} messages so far")

    _notify(
        status,
        f"Signal HTML export complete: {len(channels)} chats, {len(messages)} messages",
    )
    return SignalExport(
        source=source,
        channels=list(channels.values()),
        messages=messages,
        people=list(people.values()),
        name_changes=name_changes,
    )


def normalize(
    path: Path,
    include_signal_identities: set[str] | None = None,
    status: Callable[[str], None] | None = None,
    reconciliation: ReconciliationConfig | None = None,
    filter_to_configured_people: bool = False,
) -> SignalExport:
    if not _looks_like_html_export(path):
        raise ValueError(
            "Signal input must be an HTML export directory (expected data/signal_decrypted)."
        )
    return normalize_html_export(
        path,
        include_signal_identities=include_signal_identities,
        status=status,
        reconciliation=reconciliation,
        filter_to_configured_people=filter_to_configured_people,
    )
